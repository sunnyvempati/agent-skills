#!/usr/bin/env python3
"""
immunefi-hunter — Autonomous smart contract vulnerability hunter.

Core idea: autoresearch applied to security. Instead of pattern matching,
empirically explore attack paths against forked mainnet state and let
the results tell you where the bugs are.
"""

import argparse
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import time
import hashlib
import re
import random
from datetime import datetime
from pathlib import Path
from itertools import permutations, combinations
from typing import List, Dict, Optional, Tuple

# --- Config ---
DATA_DIR = Path(__file__).parent / "data"
DB_PATH = DATA_DIR / "findings.db"
PROGRAMS_CACHE = DATA_DIR / "programs.json"
EXPLOIT_DB = DATA_DIR / "exploits.json"

ETHERSCAN_KEYS = {
    "ethereum": os.environ.get("ETHERSCAN_API_KEY", ""),
    "arbitrum": os.environ.get("ARBISCAN_API_KEY", ""),
    "polygon": os.environ.get("POLYGONSCAN_API_KEY", ""),
    "bsc": os.environ.get("BSCSCAN_API_KEY", ""),
    "optimism": os.environ.get("OPTIMISM_API_KEY", ""),
    "base": os.environ.get("BASESCAN_API_KEY", ""),
}

EXPLORER_APIS = {
    "ethereum": "https://api.etherscan.io/api",
    "arbitrum": "https://api.arbiscan.io/api",
    "polygon": "https://api.polygonscan.com/api",
    "bsc": "https://api.bscscan.com/api",
    "optimism": "https://api-optimistic.etherscan.io/api",
    "base": "https://api.basescan.org/api",
}

RPC_URLS = {
    "ethereum": os.environ.get("ETH_RPC_URL", "https://eth.llamarpc.com"),
    "arbitrum": os.environ.get("ARB_RPC_URL", "https://arb1.arbitrum.io/rpc"),
    "polygon": os.environ.get("POLYGON_RPC_URL", "https://polygon-rpc.com"),
    "bsc": os.environ.get("BSC_RPC_URL", "https://bsc-dataseed.binance.org"),
    "optimism": os.environ.get("OP_RPC_URL", "https://mainnet.optimism.io"),
    "base": os.environ.get("BASE_RPC_URL", "https://mainnet.base.org"),
}


def ensure_dirs():
    DATA_DIR.mkdir(parents=True, exist_ok=True)


# ============================================================
# DATABASE
# ============================================================

def init_db():
    ensure_dirs()
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("""
        CREATE TABLE IF NOT EXISTS findings (
            id TEXT PRIMARY KEY,
            timestamp TEXT NOT NULL,
            program TEXT,
            contract_address TEXT,
            chain TEXT,
            severity TEXT,
            title TEXT NOT NULL,
            description TEXT,
            attack_sequence TEXT,
            profit_wei TEXT DEFAULT '0',
            detector TEXT,
            poc_test TEXT,
            status TEXT DEFAULT 'new',
            bounty_max INTEGER DEFAULT 0
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS scans (
            id TEXT PRIMARY KEY,
            timestamp TEXT NOT NULL,
            contract_address TEXT,
            chain TEXT,
            program TEXT,
            depth TEXT,
            sequences_tested INTEGER DEFAULT 0,
            findings_count INTEGER DEFAULT 0,
            duration_seconds REAL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS attack_paths (
            id TEXT PRIMARY KEY,
            contract_address TEXT,
            chain TEXT,
            sequence TEXT,
            suspicion_score REAL,
            result TEXT,
            profit_wei TEXT DEFAULT '0',
            timestamp TEXT
        )
    """)
    conn.commit()
    return conn


# ============================================================
# CONTRACT SOURCE FETCHING
# ============================================================

def fetch_source(address: str, chain: str = "ethereum") -> Optional[Dict]:
    """Fetch verified contract source + ABI from block explorer."""
    import urllib.request

    base_url = EXPLORER_APIS.get(chain, EXPLORER_APIS["ethereum"])
    api_key = ETHERSCAN_KEYS.get(chain, "")

    params = f"?module=contract&action=getsourcecode&address={address}"
    if api_key:
        params += f"&apikey={api_key}"

    try:
        req = urllib.request.Request(
            base_url + params,
            headers={"User-Agent": "immunefi-hunter/2.0"}
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read())
    except Exception as e:
        print(f"[!] Source fetch failed for {address}: {e}")
        return None

    if data.get("status") != "1" or not data.get("result"):
        print(f"[!] No verified source for {address} on {chain}")
        return None

    r = data["result"][0]
    return {
        "source": r.get("SourceCode", ""),
        "abi": r.get("ABI", ""),
        "name": r.get("ContractName", "Unknown"),
        "compiler": r.get("CompilerVersion", ""),
        "address": address,
        "chain": chain,
    }


def parse_abi_functions(abi_str: str) -> List[Dict]:
    """Extract external/public functions from ABI."""
    try:
        abi = json.loads(abi_str)
    except (json.JSONDecodeError, TypeError):
        return []

    functions = []
    for item in abi:
        if item.get("type") != "function":
            continue
        if item.get("stateMutability") in ("view", "pure"):
            continue  # Skip read-only for attack paths

        inputs = []
        for inp in item.get("inputs", []):
            inputs.append({
                "name": inp.get("name", ""),
                "type": inp.get("type", ""),
            })

        functions.append({
            "name": item["name"],
            "inputs": inputs,
            "outputs": item.get("outputs", []),
            "stateMutability": item.get("stateMutability", ""),
            "selector": None,  # Compute if needed
        })

    return functions


# ============================================================
# ATTACK PATH GENERATION (AUTORESEARCH CORE)
# ============================================================

def generate_adversarial_params(input_type: str) -> list:
    """Generate adversarial parameter values for a given Solidity type."""
    if "uint" in input_type:
        return [
            0,
            1,
            2**256 - 1,  # max uint256
            2**255,       # half max
            10**18,       # 1 ether
            10**6,        # 1 USDC
            random.randint(1, 10**18),
        ]
    elif input_type == "address":
        return [
            "0x0000000000000000000000000000000000000000",  # zero address
            "0x0000000000000000000000000000000000000001",  # precompile
            "0xdead000000000000000000000000000000000000",  # dead
            # Attacker address will be substituted at runtime
            "ATTACKER",
            "TARGET",
        ]
    elif input_type == "bool":
        return [True, False]
    elif "bytes" in input_type:
        return [b"", b"\x00" * 32, b"\xff" * 32]
    elif "string" in input_type:
        return ["", "A" * 256]
    else:
        return [0]


def generate_attack_sequences(
    functions: List[Dict],
    depth: int = 3,
    max_sequences: int = 500,
) -> List[List[Dict]]:
    """
    Generate attack call sequences using autoresearch-style permutation.
    
    Each sequence is a list of (function, params) tuples representing
    a series of calls an attacker might make.
    """
    sequences = []

    # Filter to interesting functions (state-changing)
    interesting = [f for f in functions if f["stateMutability"] not in ("view", "pure")]

    if not interesting:
        return []

    # Phase 1: Single-call edge cases
    for func in interesting:
        for params in _param_combos(func["inputs"], limit=3):
            sequences.append([{"function": func["name"], "params": params}])

    # Phase 2: Multi-call permutations (the autoresearch part)
    for d in range(2, min(depth + 1, 5)):
        # Don't enumerate all permutations — sample intelligently
        for _ in range(min(max_sequences // depth, 200)):
            seq = []
            for _ in range(d):
                func = random.choice(interesting)
                params = _param_combos(func["inputs"], limit=1)[0] if func["inputs"] else []
                seq.append({"function": func["name"], "params": params})
            sequences.append(seq)

    # Phase 3: Known attack patterns
    # Reentrancy: call → callback → call again
    # Flash loan: borrow → manipulate → repay
    # Sandwich: frontrun → victim tx → backrun
    # Governance: delegate → vote → undelegate
    pattern_names = {
        "deposit", "withdraw", "transfer", "transferFrom", "approve",
        "mint", "burn", "swap", "flashLoan", "borrow", "repay",
        "stake", "unstake", "claim", "delegate", "vote",
    }
    
    pattern_funcs = [f for f in interesting if f["name"] in pattern_names]
    for f1 in pattern_funcs:
        for f2 in pattern_funcs:
            if f1["name"] != f2["name"]:
                params1 = _param_combos(f1["inputs"], limit=1)[0] if f1["inputs"] else []
                params2 = _param_combos(f2["inputs"], limit=1)[0] if f2["inputs"] else []
                sequences.append([
                    {"function": f1["name"], "params": params1},
                    {"function": f2["name"], "params": params2},
                ])

    # Deduplicate and limit
    seen = set()
    unique = []
    for seq in sequences:
        key = json.dumps(seq, sort_keys=True, default=str)
        if key not in seen:
            seen.add(key)
            unique.append(seq)

    return unique[:max_sequences]


def _param_combos(inputs: List[Dict], limit: int = 3) -> List[List]:
    """Generate parameter combinations for a function's inputs."""
    if not inputs:
        return [[]]

    all_values = []
    for inp in inputs:
        vals = generate_adversarial_params(inp["type"])
        all_values.append(vals[:limit])

    # Cartesian product, limited
    combos = [[]]
    for values in all_values:
        new_combos = []
        for combo in combos:
            for val in values:
                new_combos.append(combo + [val])
        combos = new_combos[:limit * 10]

    return combos[:limit]


# ============================================================
# INVARIANT CHECKING
# ============================================================

class InvariantChecker:
    """
    Defines and checks invariants against contract state.
    The key insight: we don't look for specific bugs,
    we look for ANY unexpected state change.
    """

    @staticmethod
    def check_balance_invariant(pre_state: Dict, post_state: Dict) -> List[Dict]:
        """Did any token balance change in a way that benefits the attacker?"""
        violations = []
        for token, balance_before in pre_state.get("balances", {}).items():
            balance_after = post_state.get("balances", {}).get(token, balance_before)
            if balance_after > balance_before:
                profit = balance_after - balance_before
                violations.append({
                    "type": "balance_increase",
                    "token": token,
                    "profit": profit,
                    "severity": "critical" if profit > 10**18 else "high",
                })
        return violations

    @staticmethod
    def check_access_invariant(pre_state: Dict, post_state: Dict) -> List[Dict]:
        """Did the attacker gain unexpected permissions?"""
        violations = []
        pre_roles = set(pre_state.get("roles", []))
        post_roles = set(post_state.get("roles", []))
        new_roles = post_roles - pre_roles
        if new_roles:
            violations.append({
                "type": "permission_escalation",
                "new_roles": list(new_roles),
                "severity": "critical",
            })
        return violations

    @staticmethod
    def check_supply_invariant(pre_state: Dict, post_state: Dict) -> List[Dict]:
        """Did total supply change without corresponding mint/burn?"""
        violations = []
        pre_supply = pre_state.get("total_supply", 0)
        post_supply = post_state.get("total_supply", 0)
        if pre_supply != post_supply:
            violations.append({
                "type": "supply_mismatch",
                "delta": post_supply - pre_supply,
                "severity": "high",
            })
        return violations

    @staticmethod
    def check_reentrancy_invariant(execution_trace: List) -> List[Dict]:
        """Did a callback re-enter a function that should be locked?"""
        violations = []
        call_stack = []
        for event in execution_trace:
            if event.get("type") == "call":
                target = event.get("to", "")
                func = event.get("function", "")
                key = f"{target}:{func}"
                if key in call_stack:
                    violations.append({
                        "type": "reentrancy",
                        "function": func,
                        "depth": len(call_stack),
                        "severity": "critical",
                    })
                call_stack.append(key)
            elif event.get("type") == "return":
                if call_stack:
                    call_stack.pop()
        return violations


# ============================================================
# FORGE/ANVIL EXECUTION ENGINE
# ============================================================

def create_forge_test(
    contract_address: str,
    abi: str,
    sequence: List[Dict],
    chain: str = "ethereum",
) -> str:
    """Generate a Forge test that executes an attack sequence against forked state."""
    
    # Parse ABI for function signatures
    try:
        abi_list = json.loads(abi)
    except:
        abi_list = []

    # Build interface from ABI
    interface_funcs = []
    for item in abi_list:
        if item.get("type") != "function":
            continue
        inputs = ", ".join(
            f"{inp['type']} {inp.get('name', f'arg{i}')}"
            for i, inp in enumerate(item.get("inputs", []))
        )
        outputs = ", ".join(
            f"{out['type']}"
            for out in item.get("outputs", [])
        )
        returns_clause = f" returns ({outputs})" if outputs else ""
        mutability = ""
        if item.get("stateMutability") in ("view", "pure"):
            mutability = f" {item['stateMutability']}"
        interface_funcs.append(
            f"    function {item['name']}({inputs}) external{mutability}{returns_clause};"
        )

    interface_code = "interface ITarget {\n" + "\n".join(interface_funcs) + "\n}"

    # Build attack sequence calls
    attack_calls = []
    for i, step in enumerate(sequence):
        func_name = step["function"]
        params = step.get("params", [])
        param_str = ", ".join(str(p) for p in params)
        attack_calls.append(f"        target.{func_name}({param_str});")

    rpc_url = RPC_URLS.get(chain, RPC_URLS["ethereum"])

    test_code = f"""
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.0;

import "forge-std/Test.sol";

{interface_code}

contract AttackTest is Test {{
    ITarget target;
    address attacker = address(0xBEEF);
    
    function setUp() public {{
        // Fork mainnet at latest block
        vm.createSelectFork("{rpc_url}");
        target = ITarget({contract_address});
        vm.deal(attacker, 100 ether);
    }}
    
    function test_attack_sequence_{hashlib.sha256(json.dumps(sequence).encode()).hexdigest()[:8]}() public {{
        vm.startPrank(attacker);
        
        uint256 balanceBefore = attacker.balance;
        
        // Execute attack sequence
{chr(10).join(attack_calls)}
        
        uint256 balanceAfter = attacker.balance;
        
        // Check if attacker profited
        if (balanceAfter > balanceBefore) {{
            emit log_named_uint("PROFIT", balanceAfter - balanceBefore);
        }}
        
        vm.stopPrank();
    }}
}}
"""
    return test_code


def run_forge_test(test_code: str, timeout: int = 60) -> Dict:
    """Execute a forge test and return results."""
    with tempfile.TemporaryDirectory() as tmpdir:
        # Set up minimal forge project
        (Path(tmpdir) / "foundry.toml").write_text(
            '[profile.default]\nsrc = "src"\ntest = "test"\nout = "out"\n'
        )
        (Path(tmpdir) / "src").mkdir()
        (Path(tmpdir) / "test").mkdir()
        
        # Install forge-std
        subprocess.run(
            ["forge", "install", "foundry-rs/forge-std", "--no-git", "--no-commit"],
            cwd=tmpdir, capture_output=True, timeout=30
        )
        
        # Write test
        test_path = Path(tmpdir) / "test" / "Attack.t.sol"
        test_path.write_text(test_code)
        
        # Run test
        try:
            result = subprocess.run(
                ["forge", "test", "--match-contract", "AttackTest", "-vvv"],
                cwd=tmpdir, capture_output=True, text=True, timeout=timeout
            )
            
            passed = "PASS" in result.stdout
            failed = "FAIL" in result.stdout
            reverted = "revert" in result.stdout.lower() or "revert" in result.stderr.lower()
            profit_match = re.search(r"PROFIT:\s*(\d+)", result.stdout)
            profit = int(profit_match.group(1)) if profit_match else 0
            
            return {
                "passed": passed,
                "failed": failed,
                "reverted": reverted,
                "profit_wei": profit,
                "stdout": result.stdout[-2000:],  # Last 2KB
                "stderr": result.stderr[-1000:],
                "returncode": result.returncode,
            }
        except subprocess.TimeoutExpired:
            return {"passed": False, "failed": True, "reverted": False, "profit_wei": 0, "error": "timeout"}
        except Exception as e:
            return {"passed": False, "failed": True, "reverted": False, "profit_wei": 0, "error": str(e)}


# ============================================================
# SUSPICION SCORING (AUTORESEARCH FEEDBACK)
# ============================================================

def score_sequence(result: Dict, sequence: List[Dict]) -> float:
    """
    Score an attack sequence by suspicion level.
    Higher = more likely to be a real vulnerability.
    Used as feedback for the autoresearch loop.
    """
    score = 0.0

    # Profit = highest signal
    if result.get("profit_wei", 0) > 0:
        score += 10.0
        if result["profit_wei"] > 10**18:  # > 1 ETH
            score += 20.0

    # Passed without revert on a destructive sequence = suspicious
    if result.get("passed") and len(sequence) > 1:
        score += 2.0

    # Unexpected revert patterns
    if result.get("reverted"):
        # Reverted on what should be a simple call = access control working
        score -= 1.0
    
    # Longer sequences that pass are more interesting
    score += len(sequence) * 0.5

    # Known dangerous function combinations
    func_names = [s["function"] for s in sequence]
    dangerous_combos = [
        {"withdraw", "deposit"},  # Deposit-withdraw cycle
        {"flashLoan", "swap"},    # Flash loan manipulation
        {"approve", "transferFrom"},  # Approval exploit
        {"delegate", "vote"},     # Governance attack
        {"mint", "burn"},         # Supply manipulation
    ]
    for combo in dangerous_combos:
        if combo.issubset(set(func_names)):
            score += 3.0

    return max(0, score)


# ============================================================
# AUTORESEARCH EXPLORATION LOOP
# ============================================================

def explore_contract(
    address: str,
    chain: str = "ethereum",
    depth: int = 3,
    max_iterations: int = 500,
    convergence_threshold: int = 50,
    program: str = "",
):
    """
    Main autoresearch loop: generate attack paths, test them,
    score results, feed high-scoring paths back as seeds.
    """
    conn = init_db()
    start_time = time.time()

    print(f"\n{'='*60}")
    print(f"EXPLORING: {address} on {chain}")
    print(f"{'='*60}")

    # Step 1: Fetch source + ABI
    print("[*] Fetching contract source...")
    source = fetch_source(address, chain)
    if not source:
        return []

    print(f"[+] {source['name']} (compiler: {source['compiler']})")

    # Step 2: Parse ABI for attack surface
    functions = parse_abi_functions(source["abi"])
    state_changing = [f for f in functions if f["stateMutability"] not in ("view", "pure")]
    print(f"[+] {len(functions)} functions, {len(state_changing)} state-changing")

    if not state_changing:
        print("[!] No state-changing functions — nothing to attack")
        return []

    # Step 3: Initial sequence generation
    print(f"[*] Generating attack sequences (depth={depth})...")
    sequences = generate_attack_sequences(state_changing, depth=depth, max_sequences=max_iterations)
    print(f"[+] {len(sequences)} initial sequences")

    # Step 4: Autoresearch loop
    all_findings = []
    tested = 0
    high_scoring = []
    no_improvement_count = 0
    best_score = 0

    for iteration, sequence in enumerate(sequences):
        if no_improvement_count >= convergence_threshold:
            print(f"[*] Converged after {iteration} iterations (no new findings in {convergence_threshold})")
            break

        # Execute against forked state
        test_code = create_forge_test(address, source["abi"], sequence, chain)
        result = run_forge_test(test_code, timeout=30)
        tested += 1

        # Score
        suspicion = score_sequence(result, sequence)

        # Store attack path
        path_id = hashlib.sha256(f"{address}:{json.dumps(sequence)}".encode()).hexdigest()[:12]
        try:
            conn.execute("""
                INSERT OR REPLACE INTO attack_paths
                (id, contract_address, chain, sequence, suspicion_score, result, profit_wei, timestamp)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                path_id, address, chain,
                json.dumps(sequence, default=str),
                suspicion,
                json.dumps({"passed": result.get("passed"), "reverted": result.get("reverted")}, default=str),
                str(result.get("profit_wei", 0)),
                datetime.utcnow().isoformat(),
            ))
        except:
            pass

        # Track progress
        if suspicion > best_score:
            best_score = suspicion
            no_improvement_count = 0
            print(f"  [{iteration}] NEW HIGH: score={suspicion:.1f} | {' → '.join(s['function'] for s in sequence)}")
        else:
            no_improvement_count += 1

        # High-scoring = potential finding
        if suspicion >= 5.0:
            high_scoring.append((sequence, result, suspicion))

        # Profit = definite finding
        if result.get("profit_wei", 0) > 0:
            finding_id = hashlib.sha256(
                f"{address}:{chain}:profit:{json.dumps(sequence)}".encode()
            ).hexdigest()[:12]

            finding = {
                "id": finding_id,
                "severity": "critical",
                "title": f"Profit extraction via {' → '.join(s['function'] for s in sequence)}",
                "description": f"Attack sequence extracts {result['profit_wei']} wei from contract",
                "sequence": sequence,
                "profit_wei": result["profit_wei"],
                "detector": "autoresearch:profit",
            }
            all_findings.append(finding)

            conn.execute("""
                INSERT OR IGNORE INTO findings
                (id, timestamp, program, contract_address, chain, severity, title, description,
                 attack_sequence, profit_wei, detector, status, bounty_max)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'new', 0)
            """, (
                finding_id, datetime.utcnow().isoformat(), program,
                address, chain, "critical", finding["title"], finding["description"],
                json.dumps(sequence, default=str), str(result["profit_wei"]),
                "autoresearch:profit",
            ))

        # Feedback: mutate high-scoring sequences for deeper exploration
        if suspicion >= 3.0 and iteration < len(sequences) - 10:
            # Generate variations of this promising sequence
            for _ in range(3):
                mutated = list(sequence)
                # Add a random function call
                extra_func = random.choice(state_changing)
                extra_params = _param_combos(extra_func["inputs"], limit=1)[0] if extra_func["inputs"] else []
                insert_pos = random.randint(0, len(mutated))
                mutated.insert(insert_pos, {"function": extra_func["name"], "params": extra_params})
                sequences.append(mutated)

        # Progress
        if (iteration + 1) % 50 == 0:
            elapsed = time.time() - start_time
            print(f"  [{iteration+1}/{len(sequences)}] tested={tested} findings={len(all_findings)} "
                  f"best_score={best_score:.1f} elapsed={elapsed:.0f}s")

    conn.commit()
    duration = time.time() - start_time

    # Log scan
    scan_id = hashlib.sha256(f"{address}:{chain}:{datetime.utcnow().isoformat()}".encode()).hexdigest()[:12]
    conn.execute("""
        INSERT INTO scans (id, timestamp, contract_address, chain, program, depth,
                          sequences_tested, findings_count, duration_seconds)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (scan_id, datetime.utcnow().isoformat(), address, chain, program,
          str(depth), tested, len(all_findings), duration))
    conn.commit()
    conn.close()

    print(f"\n{'='*60}")
    print(f"EXPLORATION COMPLETE")
    print(f"  Sequences tested: {tested}")
    print(f"  High-scoring paths: {len(high_scoring)}")
    print(f"  Confirmed findings: {len(all_findings)}")
    print(f"  Duration: {duration:.0f}s")
    print(f"  Best suspicion score: {best_score:.1f}")
    print(f"{'='*60}")

    return all_findings


# ============================================================
# DIFFERENTIAL ANALYSIS
# ============================================================

def diff_contracts(addr_old: str, addr_new: str, chain: str = "ethereum"):
    """
    Compare two contract versions by running identical sequences
    against both and flagging behavioral differences.
    """
    print(f"\n[*] Differential analysis: {addr_old} vs {addr_new}")

    src_old = fetch_source(addr_old, chain)
    src_new = fetch_source(addr_new, chain)

    if not src_old or not src_new:
        print("[!] Could not fetch both contracts")
        return

    funcs_old = set(f["name"] for f in parse_abi_functions(src_old["abi"]))
    funcs_new = set(f["name"] for f in parse_abi_functions(src_new["abi"]))

    added = funcs_new - funcs_old
    removed = funcs_old - funcs_new
    common = funcs_old & funcs_new

    print(f"[+] Old: {len(funcs_old)} functions | New: {len(funcs_new)} functions")
    if added:
        print(f"[+] Added: {', '.join(added)}")
    if removed:
        print(f"[!] Removed: {', '.join(removed)}")
    print(f"[+] Common: {len(common)} functions")

    # Focus exploration on new/changed functions
    new_funcs = parse_abi_functions(src_new["abi"])
    interesting = [f for f in new_funcs if f["name"] in added or f["stateMutability"] not in ("view", "pure")]

    if interesting:
        print(f"\n[*] Exploring new contract with focus on changes...")
        explore_contract(addr_new, chain=chain, depth=3, max_iterations=200)


# ============================================================
# IMMUNEFI PROGRAM DISCOVERY
# ============================================================

def fetch_programs(min_bounty: int = 0) -> List[Dict]:
    """Fetch active Immunefi programs via web scraping."""
    import urllib.request

    # Immunefi moved to SSR — use jina reader
    url = "https://r.jina.ai/https://immunefi.com/bug-bounty/"
    try:
        req = urllib.request.Request(url, headers={
            "User-Agent": "immunefi-hunter/2.0",
            "Accept": "text/plain",
        })
        with urllib.request.urlopen(req, timeout=30) as resp:
            text = resp.read().decode()
    except Exception as e:
        if PROGRAMS_CACHE.exists():
            return json.loads(PROGRAMS_CACHE.read_text())
        print(f"[!] Failed to fetch programs: {e}")
        return []

    # Parse bounty listings from markdown
    programs = []
    lines = text.split("\n")
    current = {}
    for line in lines:
        # Look for bounty amount patterns
        bounty_match = re.search(r'\$[\d,]+(?:\.\d+)?(?:\s*[KkMm])?', line)
        name_match = re.search(r'\[([^\]]+)\]\(https://immunefi\.com/bounty/([^)]+)\)', line)

        if name_match:
            if current:
                programs.append(current)
            current = {
                "name": name_match.group(1),
                "slug": name_match.group(2).rstrip("/"),
                "max_bounty": 0,
                "assets": [],
            }
        if bounty_match and current:
            amount_str = bounty_match.group(0).replace("$", "").replace(",", "").strip()
            try:
                if amount_str.lower().endswith("k"):
                    amount = int(float(amount_str[:-1]) * 1000)
                elif amount_str.lower().endswith("m"):
                    amount = int(float(amount_str[:-1]) * 1000000)
                else:
                    amount = int(float(amount_str))
                current["max_bounty"] = max(current.get("max_bounty", 0), amount)
            except ValueError:
                pass

    if current:
        programs.append(current)

    # Filter and sort
    programs = [p for p in programs if p.get("max_bounty", 0) >= min_bounty]
    programs.sort(key=lambda x: x["max_bounty"], reverse=True)

    # Cache
    ensure_dirs()
    PROGRAMS_CACHE.write_text(json.dumps(programs, indent=2))

    return programs


# ============================================================
# FINDINGS & REPORTS
# ============================================================

def show_findings(severity: str = None, status: str = None):
    conn = init_db()
    query = "SELECT * FROM findings"
    conditions, params = [], []

    if severity:
        sevs = severity.split(",")
        conditions.append(f"severity IN ({','.join('?' * len(sevs))})")
        params.extend(sevs)
    if status:
        conditions.append("status = ?")
        params.append(status)

    if conditions:
        query += " WHERE " + " AND ".join(conditions)
    query += " ORDER BY CASE severity WHEN 'critical' THEN 0 WHEN 'high' THEN 1 WHEN 'medium' THEN 2 ELSE 3 END"

    cursor = conn.execute(query, params)
    rows = cursor.fetchall()
    cols = [d[0] for d in cursor.description]

    if not rows:
        print("No findings.")
        return

    icons = {"critical": "🔴", "high": "🟠", "medium": "🟡"}
    for row in rows:
        f = dict(zip(cols, row))
        icon = icons.get(f["severity"], "⚪")
        profit = int(f.get("profit_wei", 0) or 0)
        profit_str = f" | profit={profit/10**18:.4f} ETH" if profit > 0 else ""
        print(f"\n{icon} [{f['severity'].upper()}] {f['title']}")
        print(f"   Contract: {f['contract_address']} ({f['chain']})")
        print(f"   Detector: {f['detector']}{profit_str}")
        print(f"   Status: {f['status']}")

    conn.close()


def generate_report(finding_id: str):
    conn = init_db()
    row = conn.execute("SELECT * FROM findings WHERE id = ?", (finding_id,)).fetchone()
    if not row:
        print(f"Finding {finding_id} not found")
        return

    cols = [d[0] for d in conn.execute("SELECT * FROM findings LIMIT 0").description]
    f = dict(zip(cols, row))

    sequence = json.loads(f.get("attack_sequence", "[]")) if f.get("attack_sequence") else []
    seq_str = "\n".join(f"   {i+1}. Call `{s['function']}({s.get('params', [])})`" for i, s in enumerate(sequence))

    report = f"""# Bug Report — {f['title']}

## Bug Description
{f.get('description', 'Requires manual analysis.')}

## Impact
Severity: **{f['severity'].upper()}**
Contract: `{f['contract_address']}` ({f['chain']})
{f'Estimated profit: {int(f.get("profit_wei", 0) or 0) / 10**18:.4f} ETH' if int(f.get('profit_wei', 0) or 0) > 0 else ''}

## Attack Sequence
{seq_str if seq_str else 'See proof of concept.'}

## Proof of Concept
```solidity
// TODO: Forge test generated during exploration — refine and verify
// Run: forge test --match-test test_attack -vvv --fork-url <RPC>
```

## Recommendation
Address the invariant violation that allows this attack sequence to extract value.

---
*Found by immunefi-hunter autoresearch. Human review required before submission.*
"""
    print(report)
    conn.close()


# ============================================================
# CLI
# ============================================================

def main():
    parser = argparse.ArgumentParser(description="immunefi-hunter — autonomous smart contract vuln hunter")
    sub = parser.add_subparsers(dest="command")

    # explore
    p = sub.add_parser("explore", help="Full autoresearch exploration of a contract")
    p.add_argument("address", help="Contract address")
    p.add_argument("--chain", default="ethereum")
    p.add_argument("--depth", type=int, default=3, help="Max call sequence depth")
    p.add_argument("--iterations", type=int, default=500, help="Max sequences to test")
    p.add_argument("--program", default="", help="Immunefi program name")

    # sweep
    p = sub.add_parser("sweep", help="Autonomous hunt across Immunefi programs")
    p.add_argument("--min-bounty", type=int, default=50000)
    p.add_argument("--hours", type=float, default=4)
    p.add_argument("--chains", default="ethereum,arbitrum,polygon,optimism,base")

    # diff
    p = sub.add_parser("diff", help="Differential analysis between contract versions")
    p.add_argument("addr_old", help="Old contract address")
    p.add_argument("addr_new", help="New contract address")
    p.add_argument("--chain", default="ethereum")

    # findings
    p = sub.add_parser("findings", help="View findings")
    p.add_argument("--severity", default=None)
    p.add_argument("--status", default=None)

    # report
    p = sub.add_parser("report", help="Generate Immunefi report")
    p.add_argument("finding_id")

    # programs
    p = sub.add_parser("programs", help="List Immunefi programs")
    p.add_argument("--min-bounty", type=int, default=0)
    p.add_argument("--limit", type=int, default=20)

    args = parser.parse_args()

    if args.command == "explore":
        explore_contract(args.address, chain=args.chain, depth=args.depth,
                        max_iterations=args.iterations, program=args.program)
    elif args.command == "sweep":
        programs = fetch_programs(min_bounty=args.min_bounty)
        deadline = time.time() + args.hours * 3600
        print(f"[*] Sweeping {len(programs)} programs (budget: {args.hours}h)")
        for prog in programs:
            if time.time() > deadline:
                break
            for asset in prog.get("assets", []):
                addr = asset if isinstance(asset, str) else asset.get("address", "")
                if addr.startswith("0x"):
                    explore_contract(addr, program=prog.get("slug", ""))
    elif args.command == "diff":
        diff_contracts(args.addr_old, args.addr_new, chain=args.chain)
    elif args.command == "findings":
        show_findings(severity=args.severity, status=args.status)
    elif args.command == "report":
        generate_report(args.finding_id)
    elif args.command == "programs":
        programs = fetch_programs(min_bounty=args.min_bounty)
        print(f"\n{'Name':40s} {'Max Bounty':>12s}")
        print("-" * 55)
        for p in programs[:args.limit]:
            print(f"{p['name'][:40]:40s} ${p['max_bounty']:>11,}")
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
