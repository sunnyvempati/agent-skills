#!/usr/bin/env python3
"""Trading experiment tracker. SQLite, zero external deps. Structured storage for
autoresearch experiments, trades, insights, and market regimes."""

import argparse
import json
import os
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone, timedelta
from pathlib import Path

DB_PATH = Path(os.environ.get("TRADECRAFT_DB", "tradecraft.db"))

SCHEMA = """
CREATE TABLE IF NOT EXISTS experiments (
    id           TEXT PRIMARY KEY,
    name         TEXT NOT NULL,
    strategy     TEXT NOT NULL DEFAULT 'fox',
    hypothesis   TEXT,
    params       TEXT NOT NULL,
    parent_id    TEXT REFERENCES experiments(id),
    status       TEXT NOT NULL DEFAULT 'running',
    created_at   TEXT NOT NULL,
    completed_at TEXT
);

CREATE TABLE IF NOT EXISTS metrics (
    id            TEXT PRIMARY KEY,
    experiment_id TEXT NOT NULL REFERENCES experiments(id),
    name          TEXT NOT NULL,
    value         REAL NOT NULL,
    created_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS trades (
    id            TEXT PRIMARY KEY,
    experiment_id TEXT REFERENCES experiments(id),
    asset         TEXT NOT NULL,
    direction     TEXT NOT NULL,
    leverage      REAL,
    entry_price   REAL,
    exit_price    REAL,
    entry_time    TEXT,
    exit_time     TEXT,
    pnl           REAL,
    pnl_pct       REAL,
    score         REAL,
    regime_id     TEXT REFERENCES regimes(id),
    dsl_config    TEXT,
    notes         TEXT,
    created_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS insights (
    id            TEXT PRIMARY KEY,
    content       TEXT NOT NULL,
    type          TEXT NOT NULL DEFAULT 'lesson',
    tags          TEXT,
    experiment_id TEXT REFERENCES experiments(id),
    trade_id      TEXT REFERENCES trades(id),
    superseded_by TEXT REFERENCES insights(id),
    created_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS regimes (
    id          TEXT PRIMARY KEY,
    btc_trend   TEXT NOT NULL,
    volatility  TEXT NOT NULL DEFAULT 'medium',
    market_data TEXT,
    captured_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS reflect_reports (
    id             TEXT PRIMARY KEY,
    report_date    TEXT NOT NULL,
    fdr            REAL,
    metrics        TEXT,
    experiment_ids TEXT,
    notes          TEXT,
    created_at     TEXT NOT NULL
);

CREATE VIRTUAL TABLE IF NOT EXISTS insights_fts USING fts5(
    content, type, tags, content='insights', content_rowid='rowid'
);

CREATE TRIGGER IF NOT EXISTS insights_ai AFTER INSERT ON insights BEGIN
    INSERT INTO insights_fts(rowid, content, type, tags)
    VALUES (new.rowid, new.content, new.type, new.tags);
END;

CREATE TRIGGER IF NOT EXISTS insights_ad AFTER DELETE ON insights BEGIN
    INSERT INTO insights_fts(insights_fts, rowid, content, type, tags)
    VALUES ('delete', old.rowid, old.content, old.type, old.tags);
END;

CREATE INDEX IF NOT EXISTS idx_metrics_exp ON metrics(experiment_id);
CREATE INDEX IF NOT EXISTS idx_metrics_name ON metrics(name);
CREATE INDEX IF NOT EXISTS idx_trades_asset ON trades(asset);
CREATE INDEX IF NOT EXISTS idx_trades_exp ON trades(experiment_id);
CREATE INDEX IF NOT EXISTS idx_trades_regime ON trades(regime_id);
CREATE INDEX IF NOT EXISTS idx_experiments_status ON experiments(status);
CREATE INDEX IF NOT EXISTS idx_experiments_strategy ON experiments(strategy);
"""


def uid():
    return uuid.uuid4().hex[:12]


def now():
    return datetime.now(timezone.utc).isoformat()


def since_to_date(since_str):
    n = int(since_str[:-1])
    unit = since_str[-1]
    if unit == "d":
        dt = datetime.now(timezone.utc) - timedelta(days=n)
    elif unit == "h":
        dt = datetime.now(timezone.utc) - timedelta(hours=n)
    else:
        raise ValueError(f"Unknown unit: {unit}. Use 'd' or 'h'.")
    return dt.isoformat()


@contextmanager
def db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(SCHEMA)
    yield conn
    conn.close()


def out(data):
    print(json.dumps(data, indent=2, default=str))


def cmd_experiment(args):
    sub = args.exp_cmd
    if sub == "start":
        params = json.loads(args.params)
        eid = uid()
        with db() as conn:
            conn.execute(
                "INSERT INTO experiments (id, name, strategy, hypothesis, params, parent_id, status, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, 'running', ?)",
                (eid, args.name, args.strategy, args.hypothesis, json.dumps(params), args.parent, now()),
            )
            conn.commit()
        out({"id": eid, "name": args.name, "status": "running"})

    elif sub in ("complete", "fail"):
        status = "completed" if sub == "complete" else "failed"
        with db() as conn:
            conn.execute(
                "UPDATE experiments SET status=?, completed_at=? WHERE id=?",
                (status, now(), args.id),
            )
            conn.commit()
        out({"id": args.id, "status": status})

    elif sub == "list":
        with db() as conn:
            q = "SELECT * FROM experiments"
            filters, vals = [], []
            if args.strategy:
                filters.append("strategy=?")
                vals.append(args.strategy)
            if args.status:
                filters.append("status=?")
                vals.append(args.status)
            if filters:
                q += " WHERE " + " AND ".join(filters)
            q += " ORDER BY created_at DESC LIMIT ?"
            vals.append(args.limit)
            rows = conn.execute(q, vals).fetchall()
        out([dict(r) for r in rows])

    elif sub == "show":
        with db() as conn:
            exp = conn.execute("SELECT * FROM experiments WHERE id=?", (args.id,)).fetchone()
            if not exp:
                out({"error": f"experiment {args.id} not found"})
                return
            mets = conn.execute(
                "SELECT name, value, created_at FROM metrics WHERE experiment_id=? ORDER BY name",
                (args.id,),
            ).fetchall()
            trades = conn.execute(
                "SELECT id, asset, direction, pnl, pnl_pct, score FROM trades WHERE experiment_id=?",
                (args.id,),
            ).fetchall()
        out({
            "experiment": dict(exp),
            "metrics": [dict(m) for m in mets],
            "trades": [dict(t) for t in trades],
        })


def cmd_metric(args):
    mid = uid()
    with db() as conn:
        conn.execute(
            "INSERT INTO metrics (id, experiment_id, name, value, created_at) VALUES (?, ?, ?, ?, ?)",
            (mid, args.experiment, args.name, args.value, now()),
        )
        conn.commit()
    out({"id": mid, "experiment_id": args.experiment, "name": args.name, "value": args.value})


def cmd_trade(args):
    sub = args.trade_cmd
    if sub == "log":
        tid = uid()
        dsl = json.dumps(json.loads(args.dsl_config)) if args.dsl_config else None
        with db() as conn:
            conn.execute(
                "INSERT INTO trades (id, experiment_id, asset, direction, leverage, entry_price, exit_price, "
                "entry_time, exit_time, pnl, pnl_pct, score, regime_id, dsl_config, notes, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (tid, args.experiment, args.asset, args.direction, args.leverage,
                 args.entry_price, args.exit_price, args.entry_time, args.exit_time,
                 args.pnl, args.pnl_pct, args.score, args.regime, dsl, args.notes, now()),
            )
            conn.commit()
        out({"id": tid, "asset": args.asset, "direction": args.direction, "pnl": args.pnl})

    elif sub == "query":
        with db() as conn:
            q = "SELECT t.*, r.btc_trend, r.volatility FROM trades t LEFT JOIN regimes r ON t.regime_id=r.id"
            filters, vals = [], []
            if args.asset:
                filters.append("t.asset=?")
                vals.append(args.asset.upper())
            if args.direction:
                filters.append("t.direction=?")
                vals.append(args.direction)
            if args.experiment:
                filters.append("t.experiment_id=?")
                vals.append(args.experiment)
            if args.since:
                filters.append("t.created_at>=?")
                vals.append(since_to_date(args.since))
            if args.regime:
                filters.append("r.btc_trend=?")
                vals.append(args.regime)
            if filters:
                q += " WHERE " + " AND ".join(filters)
            q += " ORDER BY t.created_at DESC LIMIT ?"
            vals.append(args.limit)
            rows = conn.execute(q, vals).fetchall()
        out([dict(r) for r in rows])


def cmd_best(args):
    with db() as conn:
        q = """
            SELECT e.*, m.value as metric_value, m.name as metric_name
            FROM experiments e
            JOIN metrics m ON m.experiment_id = e.id AND m.name = ?
            WHERE e.status = 'completed'
        """
        vals = [args.metric]

        if args.strategy:
            q += " AND e.strategy = ?"
            vals.append(args.strategy)

        if args.since:
            q += " AND e.created_at >= ?"
            vals.append(since_to_date(args.since))

        if args.where:
            for clause in args.where:
                k, v = clause.split("=", 1)
                if k == "regime":
                    q += " AND e.id IN (SELECT DISTINCT t.experiment_id FROM trades t JOIN regimes r ON t.regime_id=r.id WHERE r.btc_trend=?)"
                    vals.append(v)
                elif k == "asset":
                    q += " AND e.id IN (SELECT DISTINCT experiment_id FROM trades WHERE asset=?)"
                    vals.append(v.upper())
                elif k == "strategy":
                    q += " AND e.strategy=?"
                    vals.append(v)

        order = "DESC" if not args.minimize else "ASC"
        q += f" ORDER BY m.value {order} LIMIT ?"
        vals.append(args.limit)

        rows = conn.execute(q, vals).fetchall()
    out([dict(r) for r in rows])


def cmd_compare(args):
    with db() as conn:
        results = {}
        for eid in [args.exp1, args.exp2]:
            exp = conn.execute("SELECT * FROM experiments WHERE id=?", (eid,)).fetchone()
            if not exp:
                out({"error": f"experiment {eid} not found"})
                return
            mets = conn.execute(
                "SELECT name, value FROM metrics WHERE experiment_id=?", (eid,)
            ).fetchall()
            trade_stats = conn.execute(
                "SELECT COUNT(*) as count, SUM(pnl) as total_pnl, "
                "AVG(pnl_pct) as avg_pnl_pct, "
                "SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) * 100.0 / MAX(COUNT(*), 1) as win_rate "
                "FROM trades WHERE experiment_id=?", (eid,)
            ).fetchone()
            results[eid] = {
                "experiment": dict(exp),
                "metrics": {m["name"]: m["value"] for m in mets},
                "trade_stats": dict(trade_stats) if trade_stats else {},
            }

        p1 = json.loads(results[args.exp1]["experiment"]["params"])
        p2 = json.loads(results[args.exp2]["experiment"]["params"])
        all_keys = sorted(p1.keys() | p2.keys())
        param_diff = {}
        for k in all_keys:
            v1, v2 = p1.get(k), p2.get(k)
            if v1 != v2:
                param_diff[k] = {"exp1": v1, "exp2": v2}

        out({
            "exp1": results[args.exp1],
            "exp2": results[args.exp2],
            "param_diff": param_diff,
        })


def cmd_lineage(args):
    with db() as conn:
        chain = []
        eid = args.id
        while eid:
            exp = conn.execute("SELECT * FROM experiments WHERE id=?", (eid,)).fetchone()
            if not exp:
                break
            mets = conn.execute(
                "SELECT name, value FROM metrics WHERE experiment_id=?", (eid,)
            ).fetchall()
            entry = dict(exp)
            entry["metrics"] = {m["name"]: m["value"] for m in mets}
            chain.append(entry)
            eid = exp["parent_id"]

        chain.reverse()

        if args.param:
            evolution = []
            for e in chain:
                params = json.loads(e["params"])
                evolution.append({
                    "id": e["id"],
                    "name": e["name"],
                    "param_value": params.get(args.param),
                    "metrics": e["metrics"],
                    "created_at": e["created_at"],
                })
            out({"param": args.param, "evolution": evolution})
        else:
            out({"chain": chain})


def cmd_snapshot(args):
    with db() as conn:
        q = """
            SELECT e.params, e.id, e.name, m.value as metric_value
            FROM experiments e
            JOIN metrics m ON m.experiment_id = e.id AND m.name = ?
            WHERE e.status = 'completed'
        """
        vals = [args.metric]

        if args.strategy:
            q += " AND e.strategy = ?"
            vals.append(args.strategy)
        if args.since:
            q += " AND e.created_at >= ?"
            vals.append(since_to_date(args.since))

        q += " ORDER BY m.value DESC LIMIT 1"
        row = conn.execute(q, vals).fetchone()

    if not row:
        out({"error": "no completed experiments with that metric"})
        return

    config = json.loads(row["params"])
    result = {
        "config": config,
        "source_experiment": row["id"],
        "experiment_name": row["name"],
        "metric": args.metric,
        "metric_value": row["metric_value"],
        "exported_at": now(),
    }

    if args.output:
        Path(args.output).write_text(json.dumps(config, indent=2))
        result["written_to"] = args.output

    out(result)


def cmd_insight(args):
    sub = args.insight_cmd
    if sub == "store":
        iid = uid()
        tags = json.dumps(args.tags.split(",")) if args.tags else None
        with db() as conn:
            conn.execute(
                "INSERT INTO insights (id, content, type, tags, experiment_id, trade_id, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (iid, args.content, args.type, tags, args.experiment, args.trade, now()),
            )
            conn.commit()
        out({"id": iid, "type": args.type})

    elif sub == "evolve":
        new_id = uid()
        with db() as conn:
            old = conn.execute("SELECT * FROM insights WHERE id=?", (args.id,)).fetchone()
            if not old:
                out({"error": f"insight {args.id} not found"})
                return
            tags = json.dumps(args.tags.split(",")) if args.tags else old["tags"]
            conn.execute(
                "INSERT INTO insights (id, content, type, tags, experiment_id, trade_id, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (new_id, args.content, old["type"], tags, old["experiment_id"], old["trade_id"], now()),
            )
            conn.execute("UPDATE insights SET superseded_by=? WHERE id=?", (new_id, args.id))
            conn.commit()
        out({"id": new_id, "supersedes": args.id})

    elif sub == "search":
        with db() as conn:
            rows = conn.execute(
                "SELECT i.* FROM insights i JOIN insights_fts f ON i.rowid=f.rowid "
                "WHERE insights_fts MATCH ? AND i.superseded_by IS NULL "
                "ORDER BY rank LIMIT ?",
                (args.query, args.limit),
            ).fetchall()
        out([dict(r) for r in rows])


def cmd_regime(args):
    sub = args.regime_cmd
    if sub == "store":
        rid = uid()
        data = json.dumps(json.loads(args.data)) if args.data else None
        with db() as conn:
            conn.execute(
                "INSERT INTO regimes (id, btc_trend, volatility, market_data, captured_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (rid, args.btc_trend, args.volatility, data, now()),
            )
            conn.commit()
        out({"id": rid, "btc_trend": args.btc_trend, "volatility": args.volatility})

    elif sub == "current":
        with db() as conn:
            row = conn.execute("SELECT * FROM regimes ORDER BY captured_at DESC LIMIT 1").fetchone()
        out(dict(row) if row else {"error": "no regime recorded"})


def cmd_reflect(args):
    sub = args.reflect_cmd
    if sub == "store":
        rid = uid()
        exp_ids = json.dumps(args.experiments.split(",")) if args.experiments else None
        mets = json.dumps(json.loads(args.metrics)) if args.metrics else None
        with db() as conn:
            conn.execute(
                "INSERT INTO reflect_reports (id, report_date, fdr, metrics, experiment_ids, notes, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (rid, args.date, args.fdr, mets, exp_ids, args.notes, now()),
            )
            conn.commit()
        out({"id": rid, "report_date": args.date, "fdr": args.fdr})

    elif sub == "query":
        with db() as conn:
            q = "SELECT * FROM reflect_reports"
            filters, vals = [], []
            if args.fdr_below:
                filters.append("fdr < ?")
                vals.append(args.fdr_below)
            if args.fdr_above:
                filters.append("fdr > ?")
                vals.append(args.fdr_above)
            if args.since:
                filters.append("report_date >= ?")
                vals.append(since_to_date(args.since))
            if filters:
                q += " WHERE " + " AND ".join(filters)
            q += " ORDER BY report_date DESC LIMIT ?"
            vals.append(args.limit)
            rows = conn.execute(q, vals).fetchall()

            results = []
            for r in rows:
                d = dict(r)
                if d.get("experiment_ids"):
                    exp_ids = json.loads(d["experiment_ids"])
                    exps = conn.execute(
                        f"SELECT id, name, strategy, params FROM experiments WHERE id IN ({','.join('?' * len(exp_ids))})",
                        exp_ids,
                    ).fetchall()
                    d["experiments"] = [dict(e) for e in exps]
                results.append(d)
        out(results)


def cmd_stats(args):
    with db() as conn:
        exp_count = conn.execute("SELECT COUNT(*) c FROM experiments").fetchone()["c"]
        exp_by_status = conn.execute(
            "SELECT status, COUNT(*) c FROM experiments GROUP BY status"
        ).fetchall()
        trade_count = conn.execute("SELECT COUNT(*) c FROM trades").fetchone()["c"]
        trade_pnl = conn.execute(
            "SELECT SUM(pnl) total, AVG(pnl) avg, "
            "SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) * 100.0 / MAX(COUNT(*), 1) win_rate "
            "FROM trades WHERE pnl IS NOT NULL"
        ).fetchone()
        insight_count = conn.execute(
            "SELECT COUNT(*) c FROM insights WHERE superseded_by IS NULL"
        ).fetchone()["c"]
        regime = conn.execute("SELECT * FROM regimes ORDER BY captured_at DESC LIMIT 1").fetchone()
        recent_exps = conn.execute(
            "SELECT id, name, strategy, status, created_at FROM experiments ORDER BY created_at DESC LIMIT 5"
        ).fetchall()

    out({
        "experiments": {"total": exp_count, "by_status": {r["status"]: r["c"] for r in exp_by_status}},
        "trades": {
            "total": trade_count,
            "total_pnl": trade_pnl["total"],
            "avg_pnl": trade_pnl["avg"],
            "win_rate": trade_pnl["win_rate"],
        },
        "active_insights": insight_count,
        "current_regime": dict(regime) if regime else None,
        "recent_experiments": [dict(r) for r in recent_exps],
    })


def build_parser():
    p = argparse.ArgumentParser(prog="tradecraft", description="Trading experiment tracker")
    sub = p.add_subparsers(dest="command", required=True)

    # experiment
    exp = sub.add_parser("experiment")
    exp_sub = exp.add_subparsers(dest="exp_cmd", required=True)

    exp_start = exp_sub.add_parser("start")
    exp_start.add_argument("--name", required=True)
    exp_start.add_argument("--strategy", default="fox")
    exp_start.add_argument("--hypothesis")
    exp_start.add_argument("--params", required=True, help="JSON string")
    exp_start.add_argument("--parent", help="Parent experiment ID for lineage")

    exp_complete = exp_sub.add_parser("complete")
    exp_complete.add_argument("id")

    exp_fail = exp_sub.add_parser("fail")
    exp_fail.add_argument("id")

    exp_list = exp_sub.add_parser("list")
    exp_list.add_argument("--strategy")
    exp_list.add_argument("--status")
    exp_list.add_argument("--limit", type=int, default=20)

    exp_show = exp_sub.add_parser("show")
    exp_show.add_argument("id")

    # metric
    met = sub.add_parser("metric")
    met.add_argument("--experiment", required=True)
    met.add_argument("--name", required=True)
    met.add_argument("--value", type=float, required=True)

    # trade
    trd = sub.add_parser("trade")
    trd_sub = trd.add_subparsers(dest="trade_cmd", required=True)

    trd_log = trd_sub.add_parser("log")
    trd_log.add_argument("--asset", required=True)
    trd_log.add_argument("--direction", required=True, choices=["long", "short"])
    trd_log.add_argument("--leverage", type=float)
    trd_log.add_argument("--entry-price", type=float)
    trd_log.add_argument("--exit-price", type=float)
    trd_log.add_argument("--entry-time")
    trd_log.add_argument("--exit-time")
    trd_log.add_argument("--pnl", type=float)
    trd_log.add_argument("--pnl-pct", type=float)
    trd_log.add_argument("--score", type=float)
    trd_log.add_argument("--experiment")
    trd_log.add_argument("--regime")
    trd_log.add_argument("--dsl-config", help="JSON string")
    trd_log.add_argument("--notes")

    trd_query = trd_sub.add_parser("query")
    trd_query.add_argument("--asset")
    trd_query.add_argument("--direction")
    trd_query.add_argument("--experiment")
    trd_query.add_argument("--regime")
    trd_query.add_argument("--since")
    trd_query.add_argument("--limit", type=int, default=20)

    # best
    bst = sub.add_parser("best")
    bst.add_argument("--metric", required=True)
    bst.add_argument("--where", nargs="*", help="Filters: regime=bearish asset=ETH")
    bst.add_argument("--since", help="Time window: 7d, 30d, 24h")
    bst.add_argument("--strategy")
    bst.add_argument("--minimize", action="store_true", help="Sort ascending (for drawdown, etc)")
    bst.add_argument("--limit", type=int, default=5)

    # compare
    cmp = sub.add_parser("compare")
    cmp.add_argument("exp1")
    cmp.add_argument("exp2")

    # lineage
    lin = sub.add_parser("lineage")
    lin.add_argument("id", help="Experiment ID (traces back through parent chain)")
    lin.add_argument("--param", help="Track a specific parameter's evolution")

    # snapshot
    snap = sub.add_parser("snapshot")
    snap.add_argument("--metric", required=True)
    snap.add_argument("--strategy")
    snap.add_argument("--since")
    snap.add_argument("--output", help="Write config JSON to file path")

    # insight
    ins = sub.add_parser("insight")
    ins_sub = ins.add_subparsers(dest="insight_cmd", required=True)

    ins_store = ins_sub.add_parser("store")
    ins_store.add_argument("content")
    ins_store.add_argument("--type", default="lesson", choices=["lesson", "observation", "hypothesis", "rule"])
    ins_store.add_argument("--tags")
    ins_store.add_argument("--experiment")
    ins_store.add_argument("--trade")

    ins_evolve = ins_sub.add_parser("evolve")
    ins_evolve.add_argument("id")
    ins_evolve.add_argument("content")
    ins_evolve.add_argument("--tags")

    ins_search = ins_sub.add_parser("search")
    ins_search.add_argument("query")
    ins_search.add_argument("--limit", type=int, default=10)

    # regime
    reg = sub.add_parser("regime")
    reg_sub = reg.add_subparsers(dest="regime_cmd", required=True)

    reg_store = reg_sub.add_parser("store")
    reg_store.add_argument("--btc-trend", required=True, choices=["bullish", "bearish", "neutral"])
    reg_store.add_argument("--volatility", default="medium", choices=["low", "medium", "high"])
    reg_store.add_argument("--data", help="JSON string of additional market data")

    reg_sub.add_parser("current")

    # reflect
    ref = sub.add_parser("reflect")
    ref_sub = ref.add_subparsers(dest="reflect_cmd", required=True)

    ref_store = ref_sub.add_parser("store")
    ref_store.add_argument("--date", required=True)
    ref_store.add_argument("--fdr", type=float)
    ref_store.add_argument("--metrics", help="JSON string")
    ref_store.add_argument("--experiments", help="Comma-separated experiment IDs")
    ref_store.add_argument("--notes")

    ref_query = ref_sub.add_parser("query")
    ref_query.add_argument("--fdr-below", type=float)
    ref_query.add_argument("--fdr-above", type=float)
    ref_query.add_argument("--since")
    ref_query.add_argument("--limit", type=int, default=10)

    # stats
    sub.add_parser("stats")

    return p


DISPATCH = {
    "experiment": cmd_experiment,
    "metric": cmd_metric,
    "trade": cmd_trade,
    "best": cmd_best,
    "compare": cmd_compare,
    "lineage": cmd_lineage,
    "snapshot": cmd_snapshot,
    "insight": cmd_insight,
    "regime": cmd_regime,
    "reflect": cmd_reflect,
    "stats": cmd_stats,
}


def main():
    parser = build_parser()
    args = parser.parse_args()
    DISPATCH[args.command](args)


if __name__ == "__main__":
    main()
