#!/usr/bin/env python3
"""Long-term memory with TF-IDF semantic search and entity graph."""

import argparse
import json
import os
import pickle
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np

DB_DIR = Path(os.environ.get("BRAIN_DB_DIR", "."))
DB_PATH = DB_DIR / "brain.db"
VECTORIZER_PATH = DB_DIR / "vectorizer.pkl"
REFIT_INTERVAL = 50

MEMORY_TYPES = ("fact", "episode", "insight", "prediction")
SOURCE_TYPES = ("conversation", "research", "observation")

SCHEMA = """
CREATE TABLE IF NOT EXISTS memories (
    id            TEXT PRIMARY KEY,
    content       TEXT NOT NULL,
    summary       TEXT,
    memory_type   TEXT NOT NULL,
    source        TEXT NOT NULL,
    tags          TEXT,
    confidence    REAL,
    created_at    TEXT NOT NULL,
    last_accessed TEXT NOT NULL,
    access_count  INTEGER DEFAULT 0,
    expires_at    TEXT,
    superseded_by TEXT,
    embedding     BLOB
);

CREATE TABLE IF NOT EXISTS entities (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL UNIQUE,
    entity_type TEXT,
    properties  TEXT,
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS relationships (
    id            TEXT PRIMARY KEY,
    source_entity TEXT NOT NULL REFERENCES entities(id),
    target_entity TEXT NOT NULL REFERENCES entities(id),
    relation_type TEXT NOT NULL,
    properties    TEXT,
    memory_id     TEXT REFERENCES memories(id),
    created_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS memory_entities (
    memory_id TEXT NOT NULL REFERENCES memories(id),
    entity_id TEXT NOT NULL REFERENCES entities(id),
    PRIMARY KEY (memory_id, entity_id)
);

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);

CREATE INDEX IF NOT EXISTS idx_memories_type ON memories(memory_type);
CREATE INDEX IF NOT EXISTS idx_memories_created ON memories(created_at);
"""


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def get_db():
    DB_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    row = conn.execute("SELECT value FROM meta WHERE key='memories_since_refit'").fetchone()
    if row is None:
        conn.execute("INSERT INTO meta VALUES ('memories_since_refit', '0')")
        conn.commit()
    return conn


def load_vectorizer():
    if VECTORIZER_PATH.exists():
        with open(VECTORIZER_PATH, "rb") as f:
            return pickle.load(f)
    return None


def save_vectorizer(vec):
    with open(VECTORIZER_PATH, "wb") as f:
        pickle.dump(vec, f)


def refit_vectorizer(conn):
    from sklearn.feature_extraction.text import TfidfVectorizer

    rows = conn.execute("SELECT id, content FROM memories WHERE superseded_by IS NULL").fetchall()
    if len(rows) < 5:
        return

    vec = TfidfVectorizer(max_features=5000, ngram_range=(1, 2), sublinear_tf=True)
    corpus = [r["content"] for r in rows]
    vectors = vec.fit_transform(corpus)

    for i, row in enumerate(rows):
        emb = pickle.dumps(vectors[i].toarray()[0].astype(np.float32))
        conn.execute("UPDATE memories SET embedding=? WHERE id=?", (emb, row["id"]))

    conn.execute("UPDATE meta SET value='0' WHERE key='memories_since_refit'")
    conn.commit()
    save_vectorizer(vec)


def vectorize(vec, text):
    return vec.transform([text]).toarray()[0].astype(np.float32)


def cosine_sim(a, b):
    dot = np.dot(a, b)
    na, nb = np.linalg.norm(a), np.linalg.norm(b)
    if na == 0 or nb == 0:
        return 0.0
    return float(dot / (na * nb))


def keyword_score(query, content):
    words = query.lower().split()
    content_lower = content.lower()
    hits = sum(1 for w in words if w in content_lower)
    return hits / len(words) if words else 0.0


def row_to_dict(row):
    d = dict(row)
    d.pop("embedding", None)
    if d.get("tags"):
        d["tags"] = json.loads(d["tags"])
    return d


def out(data):
    print(json.dumps(data, indent=2))


def cmd_store(args):
    conn = get_db()
    vec = load_vectorizer()

    mid = uuid.uuid4().hex[:12]
    ts = now_iso()
    expires = args.expires
    if args.type == "prediction" and not expires:
        expires = (datetime.now(timezone.utc) + timedelta(days=30)).isoformat()

    emb = pickle.dumps(vectorize(vec, args.content)) if vec else None

    conn.execute(
        """INSERT INTO memories (id, content, summary, memory_type, source, tags,
           confidence, created_at, last_accessed, access_count, expires_at, embedding)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?)""",
        (mid, args.content, args.summary, args.type, args.source,
         args.tags, args.confidence, ts, ts, expires, emb),
    )

    conn.execute("UPDATE meta SET value = CAST(CAST(value AS INTEGER) + 1 AS TEXT) WHERE key='memories_since_refit'")
    conn.commit()

    since = int(conn.execute("SELECT value FROM meta WHERE key='memories_since_refit'").fetchone()["value"])
    if since >= REFIT_INTERVAL:
        refit_vectorizer(conn)

    conn.close()
    out({"ok": True, "id": mid, "type": args.type, "expires_at": expires})


def cmd_search(args):
    conn = get_db()
    vec = load_vectorizer()

    filters = ["superseded_by IS NULL", "(expires_at IS NULL OR expires_at > ?)"]
    params = [now_iso()]

    if args.type:
        filters.append("memory_type=?")
        params.append(args.type)
    if args.source:
        filters.append("source=?")
        params.append(args.source)
    if args.after:
        filters.append("created_at >= ?")
        params.append(args.after)
    if args.before:
        filters.append("created_at <= ?")
        params.append(args.before)

    where = " AND ".join(filters)
    rows = conn.execute(f"SELECT * FROM memories WHERE {where}", params).fetchall()

    query_vec = vectorize(vec, args.query) if vec else None

    results = []
    for row in rows:
        d = row_to_dict(row)
        if query_vec is not None and row["embedding"]:
            d["score"] = round(cosine_sim(query_vec, pickle.loads(row["embedding"])), 4)
        else:
            d["score"] = round(keyword_score(args.query, row["content"]), 4)
        results.append(d)

    results.sort(key=lambda x: x["score"], reverse=True)
    results = results[: args.limit]

    for r in results:
        if r["score"] > 0:
            conn.execute(
                "UPDATE memories SET last_accessed=?, access_count=access_count+1 WHERE id=?",
                (now_iso(), r["id"]),
            )
    conn.commit()
    conn.close()
    out({"ok": True, "count": len(results), "results": results})


def cmd_recall(args):
    conn = get_db()
    row = conn.execute("SELECT * FROM memories WHERE id=?", (args.id,)).fetchone()
    if not row:
        conn.close()
        out({"ok": False, "error": "not found"})
        return

    conn.execute(
        "UPDATE memories SET last_accessed=?, access_count=access_count+1 WHERE id=?",
        (now_iso(), args.id),
    )
    conn.commit()
    conn.close()
    out({"ok": True, "memory": row_to_dict(row)})


def cmd_forget(args):
    conn = get_db()
    row = conn.execute("SELECT id FROM memories WHERE id=?", (args.id,)).fetchone()
    if not row:
        conn.close()
        out({"ok": False, "error": "not found"})
        return

    if args.hard:
        conn.execute("DELETE FROM memory_entities WHERE memory_id=?", (args.id,))
        conn.execute("DELETE FROM memories WHERE id=?", (args.id,))
    else:
        conn.execute("UPDATE memories SET expires_at=? WHERE id=?", (now_iso(), args.id))

    conn.commit()
    conn.close()
    out({"ok": True, "id": args.id, "hard": args.hard})


def cmd_evolve(args):
    conn = get_db()
    old = conn.execute("SELECT * FROM memories WHERE id=?", (args.old_id,)).fetchone()
    if not old:
        conn.close()
        out({"ok": False, "error": "original not found"})
        return

    vec = load_vectorizer()
    mid = uuid.uuid4().hex[:12]
    ts = now_iso()
    emb = pickle.dumps(vectorize(vec, args.content)) if vec else None

    conn.execute(
        """INSERT INTO memories (id, content, summary, memory_type, source, tags,
           confidence, created_at, last_accessed, access_count, expires_at, embedding)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?)""",
        (mid, args.content, old["summary"], old["memory_type"], old["source"],
         old["tags"], old["confidence"], ts, ts, old["expires_at"], emb),
    )
    conn.execute("UPDATE memories SET superseded_by=? WHERE id=?", (mid, args.old_id))
    conn.commit()
    conn.close()
    out({"ok": True, "old_id": args.old_id, "new_id": mid})


def cmd_stats(args):
    conn = get_db()
    total = conn.execute("SELECT COUNT(*) c FROM memories WHERE superseded_by IS NULL").fetchone()["c"]
    by_type = conn.execute(
        "SELECT memory_type, COUNT(*) c FROM memories WHERE superseded_by IS NULL GROUP BY memory_type"
    ).fetchall()
    by_source = conn.execute(
        "SELECT source, COUNT(*) c FROM memories WHERE superseded_by IS NULL GROUP BY source"
    ).fetchall()
    most_accessed = conn.execute(
        "SELECT id, content, access_count FROM memories WHERE superseded_by IS NULL ORDER BY access_count DESC LIMIT 5"
    ).fetchall()
    recent = conn.execute(
        "SELECT id, content, memory_type, created_at FROM memories WHERE superseded_by IS NULL ORDER BY created_at DESC LIMIT 5"
    ).fetchall()
    conn.close()
    out({
        "ok": True, "total": total,
        "by_type": {r["memory_type"]: r["c"] for r in by_type},
        "by_source": {r["source"]: r["c"] for r in by_source},
        "most_accessed": [dict(r) for r in most_accessed],
        "recent": [dict(r) for r in recent],
    })


def cmd_dump(args):
    conn = get_db()
    rows = conn.execute("SELECT * FROM memories ORDER BY created_at").fetchall()
    conn.close()
    out({"ok": True, "count": len(rows), "memories": [row_to_dict(r) for r in rows]})


def cmd_link(args):
    conn = get_db()
    mem = conn.execute("SELECT id FROM memories WHERE id=?", (args.memory_id,)).fetchone()
    if not mem:
        conn.close()
        out({"ok": False, "error": "memory not found"})
        return

    ent = conn.execute("SELECT id FROM entities WHERE name=?", (args.entity,)).fetchone()
    if not ent:
        eid = uuid.uuid4().hex[:12]
        conn.execute(
            "INSERT INTO entities (id, name, entity_type, created_at) VALUES (?, ?, ?, ?)",
            (eid, args.entity, args.entity_type, now_iso()),
        )
    else:
        eid = ent["id"]

    conn.execute(
        "INSERT OR IGNORE INTO memory_entities (memory_id, entity_id) VALUES (?, ?)",
        (args.memory_id, eid),
    )
    conn.commit()
    conn.close()
    out({"ok": True, "memory_id": args.memory_id, "entity_id": eid, "entity": args.entity})


def main():
    p = argparse.ArgumentParser(prog="brain", description="Long-term memory with semantic search")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("store")
    s.add_argument("content")
    s.add_argument("--type", required=True, choices=MEMORY_TYPES)
    s.add_argument("--source", required=True, choices=SOURCE_TYPES)
    s.add_argument("--tags")
    s.add_argument("--confidence", type=float)
    s.add_argument("--summary")
    s.add_argument("--expires")

    s = sub.add_parser("search")
    s.add_argument("query")
    s.add_argument("--type", choices=MEMORY_TYPES)
    s.add_argument("--source", choices=SOURCE_TYPES)
    s.add_argument("--limit", type=int, default=5)
    s.add_argument("--after")
    s.add_argument("--before")

    s = sub.add_parser("recall")
    s.add_argument("id")

    s = sub.add_parser("forget")
    s.add_argument("id")
    s.add_argument("--hard", action="store_true")

    s = sub.add_parser("evolve")
    s.add_argument("old_id")
    s.add_argument("content")

    sub.add_parser("stats")
    sub.add_parser("dump")

    s = sub.add_parser("link")
    s.add_argument("memory_id")
    s.add_argument("--entity", required=True)
    s.add_argument("--entity-type", default="concept")

    args = p.parse_args()
    {"store": cmd_store, "search": cmd_search, "recall": cmd_recall,
     "forget": cmd_forget, "evolve": cmd_evolve, "stats": cmd_stats,
     "dump": cmd_dump, "link": cmd_link}[args.cmd](args)


if __name__ == "__main__":
    main()
