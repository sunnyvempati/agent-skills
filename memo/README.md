# memo

Household world model. SQLite + FTS5, zero external deps. **Append-only — never forgets.**

## Commands

```sh
# store a memory
python memo.py store "kitchen light is a Hue bulb on bridge 192.168.1.50" --type fact --tags "hue,kitchen"

# evolve (supersede, old version kept as history)
python memo.py evolve abc123 "kitchen light moved to Hue bridge 192.168.1.60"

# search (FTS5 full-text, only returns active memories)
python memo.py search "kitchen light"

# recall by id (includes version history + linked entities)
python memo.py recall abc123

# link memory to an entity
python memo.py link abc123 --entity "kitchen" --entity-type room

# relate two entities
python memo.py relate --source "kitchen light" --target "kitchen" --relation "located_in"

# query everything known about an entity
python memo.py about "kitchen"

# list entities
python memo.py entities
python memo.py entities --type person

# stats / full dump
python memo.py stats
python memo.py dump
```

## Config

Set `MEMO_DB` env var to control DB location (default: `./memo.db`).

## Design

- **No forget, no expiration.** Memories are permanent. Use `evolve` to update — old versions stay as history.
- **Entity graph.** Link memories to entities (people, rooms, devices, routines), relate entities to each other.
- **`about`** pulls all active memories + relationships for any entity — the agent's full knowledge of that thing.
