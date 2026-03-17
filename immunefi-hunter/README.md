# immunefi-hunter

Autonomous smart contract vulnerability hunter for Immunefi bug bounties. Combines computational attack path exploration with LLM-driven reasoning to find bugs that tools and manual auditors miss.

**Philosophy:** Tools find patterns. Auditors find logic bugs. This finds what happens when you *actually execute* thousands of attack paths against live state and let the results tell you where the bugs are.

## What Makes This Different

Most audit tools are pattern matchers (Slither, Mythril) or reasoning frameworks (Nemesis, manual audit). This is neither — it's **empirical attack simulation**.

### 1. Attack Path Autoresearch
The core differentiator. Instead of asking "could this be vulnerable?", we ask "what happens when we try every possible attack sequence?"

```
For each target contract:
  1. Extract all external/public functions
  2. Generate permutations of call sequences (2-5 calls deep)
  3. For each sequence:
     - Fork mainnet state via anvil
     - Execute the sequence with adversarial parameters
     - Check invariants: did balances change unexpectedly?
       Did access controls hold? Did state become inconsistent?
  4. Score each sequence by "suspicion" — unexpected state changes,
     reverts at wrong points, profit opportunities
  5. Feed high-scoring sequences back as seeds for deeper exploration
  6. Iterate until convergence (no new suspicious paths found)
```

This is autoresearch applied to security: systematic exploration with feedback loops.

### 2. Economic Invariant Testing
Not "does this revert?" but "can someone profit from this?"

```python
# For every external function combination:
attacker_balance_before = get_balance(attacker)
execute_attack_sequence(sequence)
attacker_balance_after = get_balance(attacker)

if attacker_balance_after > attacker_balance_before:
    flag_as_exploit(sequence, profit=delta)
```

Flash loan wrappers, multi-block MEV sequences, governance manipulation — tested empirically against real state.

### 3. Cross-Contract Interaction Analysis
Most bugs aren't in one contract. They're in the *space between* contracts.

- Map all external calls and their targets
- For protocols with multiple contracts: test invariants across the full system
- Composability attacks: what happens when Contract A's callback interacts with Contract B's state?
- Permission boundaries: can contract A trick contract B into acting on its behalf?

### 4. Differential Analysis
Compare contract behavior across versions, forks, or upgrade proposals.

- Pull both versions of a contract (pre/post upgrade)
- Run identical call sequences against both
- Flag any behavioral differences — especially around edge cases
- Catches storage collision bugs, initialization gaps, and subtle logic changes

### 5. Historical Exploit Pattern Learning
Train on real exploits, not theoretical patterns.

- Maintain a database of past DeFi exploits (Rekt, DeFi Llama, Immunefi disclosures)
- Extract attack *signatures* — not code patterns, but behavioral patterns
- "This contract's oracle usage matches the pattern from the Euler exploit"
- Continuously updated as new exploits are disclosed

## Commands

```sh
# Explore a specific contract — full pipeline
python hunter.py explore 0x1234...abcd --chain ethereum

# Autonomous sweep — hunt across Immunefi programs
python hunter.py sweep --min-bounty 50000 --hours 4

# Attack path search on a local project
python hunter.py fuzz ./contracts/ --depth 4 --iterations 1000

# Differential analysis between two contract versions
python hunter.py diff 0xOLD...addr 0xNEW...addr --chain ethereum

# Economic invariant check
python hunter.py profit-check 0x1234...abcd --flash-loan --chain ethereum

# List findings
python hunter.py findings --severity critical,high

# Generate Immunefi-format report
python hunter.py report <finding-id>
```

## Architecture

```
hunter.py              — CLI entry point + orchestration
core/
  autoresearch.py      — Attack path permutation engine
  invariants.py        — Economic + state invariant definitions
  explorer.py          — Anvil fork manager + sequence executor
  scorer.py            — Suspicion scoring + feedback loop
analysis/
  cross_contract.py    — Multi-contract interaction mapping
  differential.py      — Version diff behavioral analysis
  exploit_db.py        — Historical exploit pattern database
fetchers/
  immunefi.py          — Program discovery + asset enumeration
  etherscan.py         — Multi-chain source fetcher
  sourcify.py          — Sourcify fallback
reporters/
  immunefi_fmt.py      — Immunefi report template
  findings_db.py       — SQLite findings + scan tracking
```

## How Autoresearch Applies

The same principle that powers our prediction models:

1. **Generate hypotheses** — permute function call sequences
2. **Test empirically** — execute against forked state
3. **Score results** — did invariants break? did the attacker profit?
4. **Feedback loop** — high-scoring sequences seed deeper exploration
5. **Converge** — stop when no new suspicious paths emerge

In prediction markets, this finds edges. In security, this finds exploits. The methodology is identical — exhaustive search with empirical validation.

## Requirements

- Python 3.11+
- foundry (forge/anvil/cast)
- slither-analyzer (for initial recon/AST parsing)
- Etherscan API keys (free tier works, multi-chain)

## Notes

- Never submits reports automatically — human review required
- All findings include reproducible forge test PoCs
- Respects rate limits on all APIs
- Findings database prevents duplicate work across sessions
