#!/usr/bin/env python3
"""Household world model. SQLite + FTS5, zero external deps. Append-only — never forgets."""

import argparse
import json
import os
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

DB_PATH = Path(os.environ.get("MEMO_DB", "memo.db"))

SCHEMA = """
CREATE TABLE IF NOT EXISTS memories (
    id            TEXT PRIMARY KEY,
    content       TEXT NOT NULL,
    type          TEXT NOT NULL DEFAULT 'fact',
    tags          TEXT,
    created_at    TEXT NOT NULL,
    superseded_by TEXT
);

CREATE TABLE IF NOT EXISTS entities (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL UNIQUE,
    entity_type TEXT NOT NULL DEFAULT 'thing',
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS relationships (
    id            TEXT PRIMARY KEY,
    source_entity TEXT NOT NULL REFERENCES entities(id),
    target_entity TEXT NOT NULL REFERENCES entities(id),
    relation      TEXT NOT NULL,
    memory_id     TEXT REFERENCES memories(id),
    created_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS memory_entities (
    memory_id TEXT NOT NULL REFERENCES memories(id),
    entity_id TEXT NOT NULL REFERENCES entities(id),
    PRIMARY KEY (memory_id, entity_id)
);

CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5(
    content, type, tags, content='memories', content_rowid='rowid'
);

CREATE TRIGGER IF NOT EXISTS memories_ai AFTER INSERT ON memories BEGIN
    INSERT INTO memories_fts(rowid, content, type, tags)
    VALUES (new.rowid, new.content, new.type, COALESCE(new.tags, ''));
END;

CREATE TRIGGER IF NOT EXISTS memories_ad AFTER DELETE ON memories BEGIN
    INSERT INTO memories_fts(memories_fts, rowid, content, type, tags)
    VALUES ('delete', old.rowid, old.content, old.type, COALESCE(old.tags, ''));
END;

CREATE TRIGGER IF NOT EXISTS memories_au AFTER UPDATE ON memories BEGIN
    INSERT INTO memories_fts(memories_fts, rowid, content, type, tags)
    VALUES ('delete', old.rowid, old.content, old.type, COALESCE(old.tags, ''));
    INSERT INTO memories_fts(rowid, content, type, tags)
    VALUES (new.rowid, new.content, new.type, COALESCE(new.tags, ''));
END;
"""


def now():
    return datetime.now(timezone.utc).isoformat()


@contextmanager
def get_db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    yield conn
    conn.close()


def out(data):
    print(json.dumps(data, indent=2))


def cmd_store(args):
    mid = uuid.uuid4().hex[:12]
    with get_db() as conn:
        conn.execute(
            "INSERT INTO memories (id, content, type, tags, created_at) VALUES (?,?,?,?,?)",
            (mid, args.content, args.type, args.tags, now()),
        )
        conn.commit()
    out({"ok": True, "id": mid})


def cmd_evolve(args):
    with get_db() as conn:
        old = conn.execute("SELECT * FROM memories WHERE id=?", (args.old_id,)).fetchone()
        if not old:
            out({"ok": False, "error": "original not found"})
            return

        mid = uuid.uuid4().hex[:12]
        conn.execute(
            "INSERT INTO memories (id, content, type, tags, created_at) VALUES (?,?,?,?,?)",
            (mid, args.content, old["type"], old["tags"], now()),
        )
        conn.execute("UPDATE memories SET superseded_by=? WHERE id=?", (mid, args.old_id))
        conn.commit()
    out({"ok": True, "old_id": args.old_id, "new_id": mid})


def cmd_search(args):
    query = " OR ".join(args.query.split())
    with get_db() as conn:
        rows = conn.execute(
            """SELECT m.*, rank FROM memories m
               JOIN memories_fts f ON m.rowid = f.rowid
               WHERE memories_fts MATCH ?
               AND m.superseded_by IS NULL
               ORDER BY rank LIMIT ?""",
            (query, args.limit),
        ).fetchall()
    out({"ok": True, "count": len(rows), "results": [dict(r) for r in rows]})


def cmd_recall(args):
    with get_db() as conn:
        row = conn.execute("SELECT * FROM memories WHERE id=?", (args.id,)).fetchone()
        if not row:
            out({"ok": False, "error": "not found"})
            return

        result = dict(row)
        history = conn.execute(
            "SELECT id, content, created_at FROM memories WHERE superseded_by=? ORDER BY created_at",
            (args.id,),
        ).fetchall()
        if history:
            result["previous_versions"] = [dict(r) for r in history]

        entities = conn.execute(
            """SELECT e.id, e.name, e.entity_type FROM entities e
               JOIN memory_entities me ON e.id = me.entity_id
               WHERE me.memory_id=?""",
            (args.id,),
        ).fetchall()
        if entities:
            result["entities"] = [dict(e) for e in entities]

    out({"ok": True, "memory": result})


def cmd_link(args):
    with get_db() as conn:
        mem = conn.execute("SELECT id FROM memories WHERE id=?", (args.memory_id,)).fetchone()
        if not mem:
            out({"ok": False, "error": "memory not found"})
            return

        ent = conn.execute("SELECT id FROM entities WHERE name=?", (args.entity,)).fetchone()
        if ent:
            eid = ent["id"]
        else:
            eid = uuid.uuid4().hex[:12]
            conn.execute(
                "INSERT INTO entities (id, name, entity_type, created_at) VALUES (?,?,?,?)",
                (eid, args.entity, args.entity_type, now()),
            )

        conn.execute(
            "INSERT OR IGNORE INTO memory_entities (memory_id, entity_id) VALUES (?,?)",
            (args.memory_id, eid),
        )
        conn.commit()
    out({"ok": True, "memory_id": args.memory_id, "entity_id": eid})


def cmd_relate(args):
    with get_db() as conn:
        src = conn.execute("SELECT id FROM entities WHERE name=?", (args.source,)).fetchone()
        tgt = conn.execute("SELECT id FROM entities WHERE name=?", (args.target,)).fetchone()
        if not src or not tgt:
            out({"ok": False, "error": "entity not found"})
            return

        rid = uuid.uuid4().hex[:12]
        conn.execute(
            "INSERT INTO relationships (id, source_entity, target_entity, relation, memory_id, created_at) VALUES (?,?,?,?,?,?)",
            (rid, src["id"], tgt["id"], args.relation, args.memory_id, now()),
        )
        conn.commit()
    out({"ok": True, "id": rid})


def cmd_about(args):
    with get_db() as conn:
        ent = conn.execute("SELECT * FROM entities WHERE name=?", (args.entity,)).fetchone()
        if not ent:
            out({"ok": False, "error": "entity not found"})
            return

        memories = conn.execute(
            """SELECT m.id, m.content, m.type, m.created_at FROM memories m
               JOIN memory_entities me ON m.id = me.memory_id
               WHERE me.entity_id=? AND m.superseded_by IS NULL
               ORDER BY m.created_at DESC""",
            (ent["id"],),
        ).fetchall()

        rels = conn.execute(
            """SELECT e.name as entity, r.relation, 'outgoing' as direction
               FROM relationships r JOIN entities e ON r.target_entity = e.id
               WHERE r.source_entity=?
               UNION ALL
               SELECT e.name as entity, r.relation, 'incoming' as direction
               FROM relationships r JOIN entities e ON r.source_entity = e.id
               WHERE r.target_entity=?""",
            (ent["id"], ent["id"]),
        ).fetchall()

    out({
        "ok": True,
        "entity": dict(ent),
        "memories": [dict(m) for m in memories],
        "relationships": [dict(r) for r in rels],
    })


def cmd_entities(args):
    with get_db() as conn:
        if args.type:
            rows = conn.execute(
                "SELECT * FROM entities WHERE entity_type=? ORDER BY name", (args.type,)
            ).fetchall()
        else:
            rows = conn.execute("SELECT * FROM entities ORDER BY name").fetchall()
    out({"ok": True, "count": len(rows), "entities": [dict(r) for r in rows]})


def cmd_stats(args):
    with get_db() as conn:
        total = conn.execute("SELECT COUNT(*) c FROM memories WHERE superseded_by IS NULL").fetchone()["c"]
        superseded = conn.execute("SELECT COUNT(*) c FROM memories WHERE superseded_by IS NOT NULL").fetchone()["c"]
        by_type = conn.execute("SELECT type, COUNT(*) c FROM memories WHERE superseded_by IS NULL GROUP BY type").fetchall()
        entity_count = conn.execute("SELECT COUNT(*) c FROM entities").fetchone()["c"]
        rel_count = conn.execute("SELECT COUNT(*) c FROM relationships").fetchone()["c"]
        recent = conn.execute(
            "SELECT id, content, type, created_at FROM memories WHERE superseded_by IS NULL ORDER BY created_at DESC LIMIT 5"
        ).fetchall()
    out({
        "ok": True, "active_memories": total, "superseded": superseded,
        "entities": entity_count, "relationships": rel_count,
        "by_type": {r["type"]: r["c"] for r in by_type},
        "recent": [dict(r) for r in recent],
    })


def cmd_dump(args):
    with get_db() as conn:
        memories = conn.execute("SELECT * FROM memories ORDER BY created_at").fetchall()
        entities = conn.execute("SELECT * FROM entities ORDER BY name").fetchall()
        relationships = conn.execute("SELECT * FROM relationships ORDER BY created_at").fetchall()
    out({
        "ok": True,
        "memories": [dict(r) for r in memories],
        "entities": [dict(r) for r in entities],
        "relationships": [dict(r) for r in relationships],
    })


def main():
    p = argparse.ArgumentParser(prog="memo", description="Household world model — never forgets")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("store")
    s.add_argument("content")
    s.add_argument("--type", default="fact")
    s.add_argument("--tags")

    s = sub.add_parser("evolve")
    s.add_argument("old_id")
    s.add_argument("content")

    s = sub.add_parser("search")
    s.add_argument("query")
    s.add_argument("--limit", type=int, default=5)

    s = sub.add_parser("recall")
    s.add_argument("id")

    s = sub.add_parser("link")
    s.add_argument("memory_id")
    s.add_argument("--entity", required=True)
    s.add_argument("--entity-type", default="thing")

    s = sub.add_parser("relate")
    s.add_argument("--source", required=True)
    s.add_argument("--target", required=True)
    s.add_argument("--relation", required=True)
    s.add_argument("--memory-id")

    s = sub.add_parser("about")
    s.add_argument("entity")

    s = sub.add_parser("entities")
    s.add_argument("--type")

    sub.add_parser("stats")
    sub.add_parser("dump")

    args = p.parse_args()
    dispatch = {
        "store": cmd_store, "evolve": cmd_evolve, "search": cmd_search,
        "recall": cmd_recall, "link": cmd_link, "relate": cmd_relate,
        "about": cmd_about, "entities": cmd_entities, "stats": cmd_stats,
        "dump": cmd_dump,
    }
    dispatch[args.cmd](args)


if __name__ == "__main__":
    main()
