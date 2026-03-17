---
name: polymarket-trade
description: Place trades on Polymarket prediction markets via polymarket-cli through an EU proxy. Use when placing bets, checking positions, managing orders, or querying market prices on Polymarket. Handles the full pipeline: wallet setup, USDC funding via bankr, token approvals, and order execution. NOT for market research or prediction modeling — only for trade execution and position management.
---

# Polymarket Trading

## Prerequisites

- `polymarket-cli` installed at `~/.cargo/bin/polymarket` (built from source via cargo)
- EU proxy running (required — CLOB API geoblocked from US datacenter IPs)
- Wallet with USDC.e on Polygon (NOT native USDC — Polymarket uses bridged USDC.e)
- Foundry `cast` for on-chain approvals

## Critical Knowledge

**Polymarket uses USDC.e (bridged), not native USDC.**
- USDC.e contract: `0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174`
- Native USDC (`0x3c499c542cEF5E3811e1192ce70d8cC03d5c3359`) will NOT work for trading
- If bankr sends native USDC, swap to USDC.e first

**EU proxy is mandatory for all CLOB operations.**
Load proxy env before every command:
```bash
. "$HOME/.cargo/env"
export HTTPS_PROXY="$(grep HTTPS_PROXY /data/.secrets/proxy_env | cut -d'"' -f2)"
```
Proxy credentials: `/data/.secrets/proxy_env`

**Signature type must be `eoa`** (not proxy). Config at `~/.config/polymarket/config.json`.

## Trading Wallet

- EOA: `0xD98a56631dded0E9f0A46ED1C3176A70A6ACfbA5`
- Private key: `/data/.secrets/polymarket_pk` (chmod 600)
- Config: `~/.config/polymarket/config.json`

## Trade Execution Flow

### 1. Check Balance
```bash
polymarket clob balance --asset-type collateral -o json
```

### 2. Find Market
```bash
polymarket -o json markets search "BLAST Rotterdam"
```
Extract the `conditionId` and token IDs from market data.

### 3. Check Orderbook
```bash
polymarket clob book "<TOKEN_ID>"
```
Token ID is the numeric string (not hex). Get from market detail or use known IDs.

### 4. Place Order

**Limit order:**
```bash
polymarket clob create-order --token "<TOKEN_ID>" --side buy --price 0.35 --size 10
```

**Market order:**
```bash
polymarket clob market-order --token "<TOKEN_ID>" --side buy --amount 5
```
Amount is in USDC for buys, shares for sells.

### 5. Manage Orders
```bash
polymarket clob orders              # List open orders
polymarket clob cancel --id <ID>    # Cancel specific order
polymarket clob cancel-all          # Cancel all orders
polymarket clob trades              # List filled trades
```

## Funding the Wallet

### Via bankr CLI
```bash
export PATH="$HOME/.bun/bin:$PATH"
bankr "Send X USDC.e to 0xD98a56631dded0E9f0A46ED1C3176A70A6ACfbA5 on Polygon"
```
- bankr wallet: `0x632a62123bcd7d9ee847d25224cf410f5e6e9edf`
- Specify **USDC.e** not just USDC (bankr defaults to USDC.e on Polygon but be explicit)
- Also send 0.5 POL for gas if wallet is fresh

### If bankr sends native USDC
```bash
bankr "Swap X USDC to USDC.e on Polygon for wallet 0xD98a..."
```

## First-Time Setup (already done, reference only)

### Token Approvals
USDC.e must be approved for three Polymarket exchange contracts:
```bash
PK=$(python3 -c "import json; print(json.load(open('/home/openclaw/.config/polymarket/config.json'))['private_key'])")
USDC_E="0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"
MAX="0xffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff"

for contract in \
  "0x4bFb41d5B3570DeFd03C39a9A4D8dE6Bd8B8982E" \
  "0xC5d563A36AE78145C45a50134d48A1215220f80a" \
  "0xd91E80cF2E7be2e162c6513ceD06f1dD0dA35296"; do
  cast send "$USDC_E" "approve(address,uint256)" "$contract" "$MAX" \
    --rpc-url "https://1rpc.io/matic" --private-key "$PK"
done
```

Then refresh CLOB state:
```bash
polymarket clob update-balance --asset-type collateral
```

### Wallet Creation
```bash
polymarket wallet create                    # Creates EOA + proxy
polymarket wallet import <PK> --signature-type eoa  # Or import existing
polymarket clob create-api-key              # CLOB API key (auto-saved)
```

## Known Token IDs (BLAST Rotterdam)

See `references/blast_tokens.md` for current market token IDs.

## Troubleshooting

| Issue | Fix |
|-------|-----|
| `not enough balance / allowance` | Run approvals for USDC.e (not native USDC). Then `update-balance` |
| `Blocked: true` from geoblock | Ensure HTTPS_PROXY is set. Proxy must be EU |
| bankr DNS fails | Use bankr CLI (`~/.bun/bin/bankr`), not curl to API |
| RPC 401/403 | Use `https://1rpc.io/matic` — other public RPCs block us |
| Allowances show 0 but approved | Wrong USDC contract. Must approve USDC.e not native |
| `No wallet configured` | Run `polymarket wallet show` to verify config exists |
