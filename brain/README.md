# brain

Long-term agent memory with TF-IDF semantic search and entity graph. Requires `numpy` and `scikit-learn`.

## Commands

```sh
# store
python brain.py store "BTC halving historically leads to 6-12mo bull run" \
  --type insight --source research --tags '["crypto","btc"]' --confidence 0.8

# semantic search
python brain.py search "bitcoin price cycles" --limit 3

# recall by id
python brain.py recall abc123

# forget (soft-expire or --hard delete)
python brain.py forget abc123
python brain.py forget abc123 --hard

# evolve (supersede old memory with updated version)
python brain.py evolve abc123 "BTC halving leads to 3-18mo bull run based on 2024 data"

# link memory to entity
python brain.py link abc123 --entity "Bitcoin" --entity-type asset

# stats / dump
python brain.py stats
python brain.py dump
```

## Config

Set `BRAIN_DB_DIR` env var to control where `brain.db` and `vectorizer.pkl` are stored (default: cwd).

## How search works

1. TF-IDF vectorizer refits every 50 stores (bigrams, sublinear TF, 5k features)
2. Cosine similarity ranking against stored embeddings
3. Falls back to keyword matching when <5 memories or no vectorizer yet

## Schema

4 tables: `memories` (core), `entities`, `relationships`, `memory_entities`. Designed for future Graphiti/Neo4j migration.

## When to use memo instead

If you don't need semantic search, entity graphs, confidence scoring, or memory evolution — use [memo](../memo/) instead. Zero deps, ~100 lines.
