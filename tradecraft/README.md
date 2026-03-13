# tradecraft

Trading experiment tracker. SQLite, zero external deps. Structured storage for autoresearch experiments, trades, insights, and market regimes.

Turns "run experiments → stare at TSV" into "run experiments → query for answers → deploy best config → REFLECT monitors → feed back."

## Setup

```bash
export TRADECRAFT_DB=/path/to/tradecraft.db  # default: ./tradecraft.db
python3 tradecraft.py <command>
```

## Commands

### Experiments

```bash
# Start an experiment (returns id)
tradecraft experiment start --name "score_10_test" --strategy fox \
  --hypothesis "score 10 vs 9" \
  --params '{"minScore": 10, "minReasons": 4, "minVelocity": 0.10}'

# Chain experiments (lineage tracking)
tradecraft experiment start --name "score_11_test" --parent <parent_id> \
  --params '{"minScore": 11, ...}'

# Complete / fail
tradecraft experiment complete <id>
tradecraft experiment fail <id>

# List / show
tradecraft experiment list --strategy fox --status completed --limit 20
tradecraft experiment show <id>
```

### Metrics

```bash
tradecraft metric --experiment <id> --name sharpe --value 1.8
tradecraft metric --experiment <id> --name max_dd --value -0.05
tradecraft metric --experiment <id> --name win_rate --value 90.0
```

### Trades

```bash
# Log a trade
tradecraft trade log --asset ETH --direction long --leverage 10 \
  --entry-price 3200 --exit-price 3280 --pnl 80 --pnl-pct 2.5 \
  --score 11 --experiment <id> --regime <regime_id> \
  --dsl-config '{"phase1Floor": -25, "hwTier1": 7}'

# Query trades
tradecraft trade query --asset ETH --since 7d --regime bearish
tradecraft trade query --experiment <id>
tradecraft trade query --direction short --since 30d
```

### Best (time-windowed queries)

```bash
tradecraft best --metric sharpe
tradecraft best --metric sharpe --where regime=bearish --since 7d
tradecraft best --metric sharpe --where asset=ETH regime=bullish
tradecraft best --metric max_dd --minimize --since 30d
tradecraft best --metric win_rate --strategy fox --limit 3
```

### Compare (side-by-side)

```bash
tradecraft compare <exp1> <exp2>
# Shows: param diff, metrics diff, trade stats for each
```

### Lineage (parameter evolution)

```bash
# Full chain
tradecraft lineage <id>

# Track specific param across experiment chain
tradecraft lineage <id> --param minScore
```

### Snapshot (deployable config)

```bash
# Dump best config as JSON
tradecraft snapshot --metric sharpe
tradecraft snapshot --metric sharpe --strategy fox --since 7d

# Write directly to file (feeds into FOX)
tradecraft snapshot --metric sharpe --output /path/to/fox-strategies.json
```

### Insights

```bash
# Store a learning
tradecraft insight store "Score 10 reduces volume but sharpe jumps" \
  --type lesson --tags score,filter --experiment <id>

# Evolve (supersede, never delete)
tradecraft insight evolve <id> "Updated: score 10 optimal in bearish, 9 OK in bullish"

# Search
tradecraft insight search "score threshold"
```

### Regimes

```bash
tradecraft regime store --btc-trend bearish --volatility high \
  --data '{"btc_price": 95000, "fear_greed": 25}'
tradecraft regime current
```

### REFLECT reports

```bash
# Store nightly report
tradecraft reflect store --date 2026-03-13 --fdr 0.12 \
  --experiments <id1>,<id2> --notes "Both within tolerance"

# Query — "REFLECT flagged FDR > 30%, which configs have FDR < 15%?"
tradecraft reflect query --fdr-below 0.15
tradecraft reflect query --fdr-above 0.30 --since 7d
```

### Stats

```bash
tradecraft stats
# Returns: experiment counts, trade P&L/win rate, insight count, current regime
```

## Schema

6 tables: `experiments`, `metrics`, `trades`, `insights`, `regimes`, `reflect_reports`

Key relationships:
- experiments → metrics (1:many)
- experiments → trades (1:many)
- experiments → parent experiment (lineage chain)
- trades → regimes (many:1)
- insights → experiments/trades (optional links)
- reflect_reports → experiments (many:many via JSON array)
- insights have FTS5 full-text search
- insights use supersession (evolve, never delete)
