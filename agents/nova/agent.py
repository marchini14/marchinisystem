#!/usr/bin/env python3
"""Nova agent — Airdrop & points-program opportunity hunter.

HTTP server on :8080
  GET /health        → 200 "ok"
  GET /status        → JSON scan state
  GET /opportunities → JSON scored opportunity list

Background thread checks wallet eligibility every SCAN_INTERVAL_H hours.
Read-only: never signs transactions, never holds private keys.
"""
import hashlib
import hmac
import json
import os
import threading
import time
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer

ETHERSCAN_KEY    = os.environ.get("ETHERSCAN_KEY", "")
ALCHEMY_KEY      = os.environ.get("ALCHEMY_KEY", "")
WALLET_ETH       = [w.strip().lower() for w in os.environ.get("WALLET_ETH", "").split(",") if w.strip()]
WALLET_SOL       = [w.strip() for w in os.environ.get("WALLET_SOL", "").split(",") if w.strip()]
SCAN_INTERVAL_H  = float(os.environ.get("SCAN_INTERVAL_H", "4"))
PORT             = int(os.environ.get("PORT", "8080"))
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID   = os.environ.get("TELEGRAM_CHAT_ID", "")
# Read-only: only ever calls GET /api/v3/account (balances). Never places
# orders, never withdraws — the key must be scoped to "Read" only on MEXC's
# side, per the no-withdraw / MEXC-only rule.
MEXC_API_KEY     = os.environ.get("MEXC_API_KEY", "")
MEXC_API_SECRET  = os.environ.get("MEXC_API_SECRET", "")
# How many recent signatures per wallet to pull when checking for protocol
# interaction (Jupiter/Drift/Kamino have no persistent token to check, so we
# have to look at tx history instead). Higher = more coverage but more RPC
# calls per 4h cycle.
SOL_TX_LOOKBACK  = int(os.environ.get("SOL_TX_LOOKBACK", "100"))

# ── Curated protocol list ────────────────────────────────────────────────────
# Update NOVA_PROTOCOLS env var with JSON to override at runtime without redeploy.
DEFAULT_PROTOCOLS = [
    {
        "id": "hyperliquid",
        "name": "Hyperliquid",
        "chain": "arb",
        "contract": None,
        "action": "trade on Hyperliquid DEX (perps or spot)",
        "check_type": "api",
        "api_url": "https://api.hyperliquid.xyz/info",
        "api_method": "POST",
        "api_body": {"type": "clearinghouseState", "user": "{wallet}"},
        "api_eligible_key": "marginSummary.accountValue",
        "reward_est": "$500–3000",
        "effort": "low",
        "status": "active",
        "notes": "Points program ongoing; snapshot timing unknown",
    },
    {
        "id": "scroll",
        "name": "Scroll",
        "chain": "ethereum",
        "contract": "0x5300000000000000000000000000000000000004",
        "action": "bridge ETH to Scroll L2 and use dApps",
        "check_type": "etherscan_tx",
        "reward_est": "$300–2000",
        "effort": "low",
        "status": "active",
        "notes": "L2 with confirmed upcoming token launch",
    },
    {
        "id": "zksync",
        "name": "zkSync Era",
        "chain": "ethereum",
        "contract": "0x32400084C286CF3E17e7B677ea9583e60a000324",
        "action": "bridge and use zkSync Era ecosystem",
        "check_type": "etherscan_tx",
        "reward_est": "$200–1500",
        "effort": "low",
        "status": "active",
        "notes": "ZK token allocated; additional seasons possible",
    },
    {
        "id": "linea",
        "name": "Linea (Consensys)",
        "chain": "ethereum",
        "contract": "0xd19d4B5d358258f05D7B411E21A1460D11B0876F",
        "action": "bridge to Linea and use dApps",
        "check_type": "etherscan_tx",
        "reward_est": "$300–2000",
        "effort": "low",
        "status": "active",
        "notes": "Linea Surge points active; token TBA",
    },
    {
        "id": "base",
        "name": "Base (Coinbase L2)",
        "chain": "ethereum",
        "contract": "0x3154Cf16ccdb4C6d922629664174b904d80F2C35",
        "action": "bridge to Base and use protocols (Aerodrome, etc.)",
        "check_type": "etherscan_tx",
        "reward_est": "$200–1000",
        "effort": "low",
        "status": "active",
        "notes": "No official token yet; ecosystem activity tracked",
    },
    {
        "id": "berachain",
        "name": "Berachain",
        "chain": "bera",
        "contract": None,
        "action": "stake BGT, provide liquidity in Bex",
        "check_type": "manual",
        "reward_est": "$200–2000",
        "effort": "low",
        "status": "mainnet",
        "notes": "BERA token live; ongoing BGT rewards",
    },
    {
        "id": "starknet",
        "name": "StarkNet",
        "chain": "ethereum",
        "contract": "0xae0Ee0A63A2cE6BaeEFFE56e7714FB4EFE48D419",
        "action": "bridge to StarkNet and use dApps",
        "check_type": "etherscan_tx",
        "reward_est": "$200–1000",
        "effort": "low",
        "status": "active",
        "notes": "STRK token live; additional distribution possible",
    },
    {
        "id": "eigenlayer",
        "name": "EigenLayer",
        "chain": "ethereum",
        "contract": None,
        "action": "restake ETH or LSTs on EigenLayer, watch for a new season announcement",
        "check_type": "manual",
        "reward_est": "unknown — Season 2 stakedrop claim already closed",
        "effort": "medium",
        "status": "uncertain",
        "notes": "Season 2 claim window closed; no confirmed new season as of 2026-07-20 — check eigenlayer.xyz before spending effort here",
    },
    # ── Solana protocols (checked via Alchemy Solana RPC on WALLET_SOL) ──
    {
        "id": "jupiter",
        "name": "Jupiter (Solana aggregator)",
        "chain": "solana",
        "contract": None,
        "program_id": "JUP6LkbZbjS1jKKwapdHNy74zcZ3tLUZoi5QNyVTaV4",
        "action": "swap through Jupiter aggregator on Solana",
        "check_type": "sol_program_interaction",
        "reward_est": "$100–2000",
        "effort": "low",
        "status": "active",
        "notes": "Largest Solana DEX aggregator; JUP distribution ongoing",
    },
    {
        "id": "jito",
        "name": "Jito (Solana MEV/staking)",
        "chain": "solana",
        "contract": None,
        "mint": "J1toso1uCk3RLmjorhTtrVwY9HJ7X8V9yYac6Y7kGCPn",  # jitoSOL
        "action": "stake SOL with Jito (jitoSOL) or run validator",
        "check_type": "sol_mint_balance",
        "reward_est": "$200–3000",
        "effort": "low",
        "status": "active",
        "notes": "jitoSOL holders + points program",
    },
    {
        "id": "marinade",
        "name": "Marinade (Solana liquid staking)",
        "chain": "solana",
        "contract": None,
        "mint": "mSoLzYCxHdYgdzU16g5QSh3i5K3z3KZK7ytfqcJm7So",  # mSOL
        "action": "stake SOL with Marinade (mSOL)",
        "check_type": "sol_mint_balance",
        "reward_est": "$100–1000",
        "effort": "low",
        "status": "active",
        "notes": "mSOL holders tracked for potential distributions",
    },
    {
        "id": "drift",
        "name": "Drift (Solana perps)",
        "chain": "solana",
        "contract": None,
        "program_id": "dRiftyHA39MWEi3m9aunc5MzRF1JYuBsbn6VPcn33UH",
        "action": "trade perps or provide liquidity on Drift",
        "check_type": "sol_program_interaction",
        "reward_est": "$100–1500",
        "effort": "medium",
        "status": "active",
        "notes": "Drift points program; DRIFT token live",
    },
    {
        "id": "kamino",
        "name": "Kamino (Solana DeFi)",
        "chain": "solana",
        "contract": None,
        "program_id": "KLend2g3cP87fffoy8q1mQqGKjrxjC8boSyAYavgmjD",
        "action": "supply/borrow or LP on Kamino",
        "check_type": "sol_program_interaction",
        "reward_est": "$100–1500",
        "effort": "medium",
        "status": "active",
        "notes": "KMNO token live; confirmed active Season 3 with milestone rewards",
    },
    # ── New candidates (2026-07 research) — no confirmed single trackable
    # contract address, so check_type=manual: Nova lists them as a reminder
    # but can't auto-verify eligibility yet. Check the site directly.
    {
        "id": "polymarket",
        "name": "Polymarket",
        "chain": "polygon",
        "contract": None,
        "action": "trade on Polymarket prediction markets",
        "check_type": "manual",
        "reward_est": "unknown — POLY token confirmed, active points program",
        "effort": "low",
        "status": "active",
        "notes": "Runs on Polygon, not Ethereum mainnet; check polymarket.com/portfolio manually",
    },
    {
        "id": "metamask",
        "name": "MetaMask",
        "chain": "ethereum",
        "contract": None,
        "action": "use MetaMask swap/portfolio features",
        "check_type": "manual",
        "reward_est": "unknown — MASK token confirmed upcoming by Consensys CEO",
        "effort": "low",
        "status": "upcoming",
        "notes": "No single trackable contract; activity is through the wallet UI itself",
    },
    {
        "id": "mexc",
        "name": "MEXC Exchange",
        "chain": "cex",
        "contract": None,
        "action": "deposit/trade on MEXC to qualify for exchange rewards (Launchpool, trading competitions)",
        "check_type": "mexc_balance",
        "reward_est": "unknown — MEXC runs recurring Launchpool/trading-competition rewards",
        "effort": "low",
        "status": "active",
        "notes": "Checked via read-only MEXC API key (spot account balances only) — key has no trade/withdraw permission",
    },
    {
        "id": "backpack",
        "name": "Backpack",
        "chain": "cex",
        "contract": None,
        "action": "deposit/trade on Backpack exchange",
        "check_type": "manual",
        "reward_est": "unknown — Season 4 running, TGE announced Feb 2026",
        "effort": "low",
        "status": "active",
        "notes": "Centralized exchange activity, not on-chain — check backpack.exchange account",
    },
    {
        "id": "axiom",
        "name": "Axiom Trade (Solana)",
        "chain": "solana",
        "contract": None,
        "action": "trade on Axiom Trade terminal",
        "check_type": "manual",
        "reward_est": "unknown — active points program",
        "effort": "medium",
        "status": "active",
        "notes": "Needs program-specific activity check, not just generic SOL balance — check axiom.trade/portfolio",
    },
]


# ── Shared state ─────────────────────────────────────────────────────────────
STATE = {
    "status":        "starting",
    "last_run":      None,
    "next_run":      None,
    "opportunities": [],
    "wallet_eth":    WALLET_ETH,
    "wallet_sol":    WALLET_SOL,
    "error":         None,
}
STATE_LOCK = threading.Lock()


# ── HTTP helpers ──────────────────────────────────────────────────────────────
def http_get(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read())


def http_post_json(url, payload):
    body = json.dumps(payload).encode()
    req = urllib.request.Request(
        url, data=body,
        headers={"Content-Type": "application/json", "User-Agent": "Mozilla/5.0"},
    )
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read())


def notify_telegram(text):
    """Best-effort push to Telegram. Never raises — a notification failure
    should not interrupt or crash the scan loop."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return
    try:
        http_post_json(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            {"chat_id": TELEGRAM_CHAT_ID, "text": text},
        )
    except Exception as e:
        print(f"[nova] telegram notify failed: {e}")


# ── Alchemy Solana RPC ─────────────────────────────────────────────────────────
# Read-only: checks SOL balance + SPL token accounts for WALLET_SOL.
# Uses your Alchemy Solana RPC key. Never signs, never sends funds.
ALCHEMY_SOL_RPC = "https://solana-mainnet.g.alchemy.com/v2/"


def get_sol_balance(wallet):
    """Return SOL balance (float) of wallet via Alchemy, 0.0 on error."""
    if not ALCHEMY_KEY or not wallet:
        return 0.0
    try:
        r = http_post_json(
            ALCHEMY_SOL_RPC + ALCHEMY_KEY,
            {"jsonrpc": "2.0", "id": 1, "method": "getBalance", "params": [wallet]},
        )
        val = r.get("result", {}).get("value", 0)
        return float(val) / 1e9
    except Exception:
        return 0.0


def get_sol_token_count(wallet):
    """Return number of SPL token accounts held by wallet via Alchemy."""
    if not ALCHEMY_KEY or not wallet:
        return 0
    try:
        r = http_post_json(
            ALCHEMY_SOL_RPC + ALCHEMY_KEY,
            {"jsonrpc": "2.0", "id": 1, "method": "getTokenAccountsByOwner",
             "params": [wallet, {"programId": "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"},
                        {"encoding": "jsonParsed"}]},
        )
        return len(r.get("result", {}).get("value", []))
    except Exception:
        return 0


def get_sol_mint_balance(wallet, mint):
    """Return uiAmount balance of a specific SPL mint for wallet via Alchemy, 0.0 on error.

    Unlike get_sol_token_count/get_sol_balance (which only prove the wallet
    holds *some* SOL/SPL token), this proves the wallet actually holds the
    protocol's own liquid-staking token — real evidence of using that
    specific protocol, not just having a funded wallet.
    """
    if not ALCHEMY_KEY or not wallet:
        return 0.0
    try:
        r = http_post_json(
            ALCHEMY_SOL_RPC + ALCHEMY_KEY,
            {"jsonrpc": "2.0", "id": 1, "method": "getTokenAccountsByOwner",
             "params": [wallet, {"mint": mint}, {"encoding": "jsonParsed"}]},
        )
        total = 0.0
        for acc in r.get("result", {}).get("value", []):
            info = acc.get("account", {}).get("data", {}).get("parsed", {}).get("info", {})
            amt = info.get("tokenAmount", {}).get("uiAmount")
            if amt:
                total += float(amt)
        return total
    except Exception:
        return 0.0


def get_sol_program_ids(wallet, limit=SOL_TX_LOOKBACK):
    """Return the set of program IDs invoked (top-level + inner/CPI) across
    a wallet's most recent `limit` tx signatures via Alchemy.

    Protocols like Jupiter/Drift/Kamino leave no persistent token in the
    wallet to check (unlike Jito/Marinade's jitoSOL/mSOL) — a swap or a
    perps trade doesn't leave anything behind. Real evidence of use is a
    past transaction that invoked that protocol's program, so we pull
    recent tx history and check which programs actually got called.
    """
    if not ALCHEMY_KEY or not wallet:
        return set()
    try:
        sigs_resp = http_post_json(
            ALCHEMY_SOL_RPC + ALCHEMY_KEY,
            {"jsonrpc": "2.0", "id": 1, "method": "getSignaturesForAddress",
             "params": [wallet, {"limit": limit}]},
        )
        sigs = [s["signature"] for s in sigs_resp.get("result", []) if s.get("signature")]
        if not sigs:
            return set()

        program_ids = set()
        for i in range(0, len(sigs), 25):
            chunk = sigs[i:i + 25]
            batch = [
                {"jsonrpc": "2.0", "id": j, "method": "getTransaction",
                 "params": [sig, {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0}]}
                for j, sig in enumerate(chunk)
            ]
            resp = http_post_json(ALCHEMY_SOL_RPC + ALCHEMY_KEY, batch)
            for item in resp if isinstance(resp, list) else []:
                tx = (item or {}).get("result")
                if not tx:
                    continue
                for ix in tx.get("transaction", {}).get("message", {}).get("instructions", []):
                    pid = ix.get("programId")
                    if pid:
                        program_ids.add(pid)
                for inner in tx.get("meta", {}).get("innerInstructions", []) or []:
                    for ix in inner.get("instructions", []):
                        pid = ix.get("programId")
                        if pid:
                            program_ids.add(pid)
        return program_ids
    except Exception:
        return set()


_PROGRAM_ID_CACHE = {}
_PREV_ELIGIBLE = set()


def get_wallet_program_ids(wallet):
    """Cache get_sol_program_ids per wallet for the duration of one scan
    cycle — Jupiter/Drift/Kamino would otherwise each re-fetch + re-parse
    the same tx history for the same wallet. Cleared at the start of
    run_scan() so each cycle sees fresh history.
    """
    if wallet not in _PROGRAM_ID_CACHE:
        _PROGRAM_ID_CACHE[wallet] = get_sol_program_ids(wallet)
    return _PROGRAM_ID_CACHE[wallet]


# ── Eligibility checkers ──────────────────────────────────────────────────────
def get_eth_activity(contract_addr, wallet):
    """Stats for wallet's txs to contract_addr, from one Etherscan txlist call:
      count      — number of txs to the contract
      days       — distinct calendar days with a tx (activity streak signal)
      selectors  — distinct function selectors called (breadth of usage, not
                   just a single bridge-and-done tx)
    """
    empty = {"count": 0, "days": 0, "selectors": 0}
    if not ETHERSCAN_KEY or not wallet:
        return empty
    url = (
        f"https://api.etherscan.io/v2/api?chainid=1&module=account"
        f"&action=txlist&address={wallet}&startblock=0&endblock=99999999"
        f"&sort=asc&apikey={ETHERSCAN_KEY}"
    )
    try:
        r = http_get(url)
        if r.get("status") != "1":
            return empty
        target = contract_addr.lower()
        matched = [tx for tx in r.get("result", []) if tx.get("to", "").lower() == target]
        days = {time.strftime("%Y-%m-%d", time.gmtime(int(tx["timeStamp"]))) for tx in matched}
        selectors = {tx["input"][:10] for tx in matched if tx.get("input", "0x") != "0x"}
        return {"count": len(matched), "days": len(days), "selectors": len(selectors)}
    except Exception:
        return empty


MEXC_BASE_URL = "https://api.mexc.com"


def get_mexc_account():
    """Return (balances: list[{asset, free, locked}], error: str|None) for
    the configured MEXC account via a signed, read-only GET /api/v3/account
    call. Never places orders or withdraws — used purely as an eligibility
    signal (balance/activity) for airdrop programs that require proof of
    exchange usage.
    """
    if not MEXC_API_KEY or not MEXC_API_SECRET:
        return [], "MEXC_API_KEY/MEXC_API_SECRET not set"
    try:
        params = {"timestamp": int(time.time() * 1000), "recvWindow": 5000}
        query = urllib.parse.urlencode(params)
        signature = hmac.new(
            MEXC_API_SECRET.encode(), query.encode(), hashlib.sha256
        ).hexdigest()
        url = f"{MEXC_BASE_URL}/api/v3/account?{query}&signature={signature}"
        req = urllib.request.Request(url, headers={"X-MEXC-APIKEY": MEXC_API_KEY})
        with urllib.request.urlopen(req, timeout=20) as r:
            data = json.loads(r.read())
        balances = [
            b for b in data.get("balances", [])
            if float(b.get("free", 0)) > 0 or float(b.get("locked", 0)) > 0
        ]
        return balances, None
    except Exception as e:
        return [], str(e)


def get_hyperliquid_value(wallet):
    """Return account value on Hyperliquid (0 if none)."""
    if not wallet:
        return 0.0
    try:
        r = http_post_json(
            "https://api.hyperliquid.xyz/info",
            {"type": "clearinghouseState", "user": wallet},
        )
        val = r.get("marginSummary", {}).get("accountValue", "0")
        return float(val)
    except Exception:
        return 0.0


# ── Main scan ─────────────────────────────────────────────────────────────────
def check_eligibility(proto):
    """Return (eligible: bool, detail: str) for one protocol."""
    ct = proto.get("check_type")

    if ct == "etherscan_tx":
        contract = proto.get("contract")
        if not contract or not WALLET_ETH:
            return False, "wallet or contract unknown"
        count = days = selectors = 0
        for w in WALLET_ETH:
            stats = get_eth_activity(contract, w)
            count += stats["count"]
            days = max(days, stats["days"])
            selectors = max(selectors, stats["selectors"])
        if count > 0:
            return True, (
                f"{count} tx(s) across {days} distinct day(s), "
                f"{selectors} distinct function(s) called, across {len(WALLET_ETH)} wallet(s)"
            )
        return False, "no interactions found — action needed"

    elif ct == "api" and proto["id"] == "hyperliquid":
        val = sum(get_hyperliquid_value(w) for w in WALLET_ETH)
        if val > 0:
            return True, f"account value ${val:.2f} across {len(WALLET_ETH)} wallet(s)"
        return False, "no Hyperliquid account — action needed"

    elif ct == "sol_activity":
        if not WALLET_SOL:
            return False, "WALLET_SOL not set — action needed"
        total_sol = 0.0
        total_tokens = 0
        for w in WALLET_SOL:
            total_sol += get_sol_balance(w)
            total_tokens += get_sol_token_count(w)
        if total_sol > 0 or total_tokens > 0:
            return True, f"{total_sol:.4f} SOL total, {total_tokens} SPL accounts across {len(WALLET_SOL)} wallet(s)"
        return False, "no SOL activity found — action needed (swap/stake on Solana)"

    elif ct == "sol_mint_balance":
        if not WALLET_SOL:
            return False, "WALLET_SOL not set — action needed"
        mint = proto.get("mint")
        total = sum(get_sol_mint_balance(w, mint) for w in WALLET_SOL)
        if total > 0:
            return True, f"{total:.4f} {proto['name'].split('(')[0].strip()} token held across {len(WALLET_SOL)} wallet(s)"
        return False, f"no {proto['name'].split('(')[0].strip()} token held — action needed (stake to get it)"

    elif ct == "sol_program_interaction":
        if not WALLET_SOL:
            return False, "WALLET_SOL not set — action needed"
        program_id = proto.get("program_id")
        seen = set()
        for w in WALLET_SOL:
            seen |= get_wallet_program_ids(w)
        name = proto["name"].split("(")[0].strip()
        if program_id in seen:
            return True, f"found a tx invoking the {name} program in the last {SOL_TX_LOOKBACK} signatures"
        return False, f"no {name} program interaction in the last {SOL_TX_LOOKBACK} signatures — action needed"

    elif ct == "mexc_balance":
        balances, err = get_mexc_account()
        if err:
            return False, f"MEXC check failed — {err}"
        if balances:
            summary = ", ".join(
                f"{b['asset']} {float(b['free']) + float(b['locked']):.4f}" for b in balances[:5]
            )
            return True, f"{len(balances)} non-zero asset(s): {summary}"
        return False, "no non-zero MEXC balances found — action needed (deposit/trade)"

    elif ct == "manual":
        return None, "manual check required — see notes"

    return None, "check type not implemented"


def run_scan():
    _PROGRAM_ID_CACHE.clear()

    protocols = DEFAULT_PROTOCOLS
    env_override = os.environ.get("NOVA_PROTOCOLS")
    if env_override:
        try:
            protocols = json.loads(env_override)
        except Exception:
            pass

    results = []
    for p in protocols:
        print(f"[nova] Checking {p['name']}…")
        try:
            eligible, detail = check_eligibility(p)
        except Exception as e:
            eligible, detail = None, f"error: {e}"

        if eligible is True:
            badge = "ELIGIBLE"
        elif eligible is False:
            badge = "ACTION_NEEDED"
        else:
            badge = "MANUAL"

        results.append({
            "id":         p["id"],
            "name":       p["name"],
            "chain":      p["chain"],
            "badge":      badge,
            "detail":     detail,
            "action":     p["action"],
            "reward_est": p["reward_est"],
            "effort":     p["effort"],
            "status":     p["status"],
            "notes":      p["notes"],
            "ts":         time.time(),
        })
        print(f"[nova]   {badge}: {detail}")

    # Sort: ELIGIBLE first, then ACTION_NEEDED, then MANUAL
    order = {"ELIGIBLE": 0, "ACTION_NEEDED": 1, "MANUAL": 2}
    results.sort(key=lambda r: order.get(r["badge"], 9))

    # Only notify about protocols that just BECAME eligible — otherwise a
    # static "still eligible" state would re-notify every SCAN_INTERVAL_H
    # forever. _PREV_ELIGIBLE resets on redeploy, which just risks one
    # duplicate notification rather than silently missing new ones.
    global _PREV_ELIGIBLE
    current_eligible = {r["id"] for r in results if r["badge"] == "ELIGIBLE"}
    newly_eligible = current_eligible - _PREV_ELIGIBLE
    for r in results:
        if r["id"] in newly_eligible:
            notify_telegram(
                f"🪂 Nova: newly eligible — {r['name']}\n"
                f"{r['detail']}\n"
                f"reward est: {r['reward_est']}\n"
                f"action: {r['action']}"
            )
    _PREV_ELIGIBLE = current_eligible

    return results, None


# ── Scan loop ─────────────────────────────────────────────────────────────────
def scan_loop():
    while True:
        with STATE_LOCK:
            STATE["status"] = "scanning"
            STATE["error"]  = None

        opportunities, err = run_scan()

        with STATE_LOCK:
            STATE.update({
                "status":        "idle",
                "opportunities": opportunities,
                "last_run":      time.time(),
                "next_run":      time.time() + SCAN_INTERVAL_H * 3600,
                "error":         err,
            })

        eligible = sum(1 for o in opportunities if o["badge"] == "ELIGIBLE")
        action   = sum(1 for o in opportunities if o["badge"] == "ACTION_NEEDED")
        print(f"[nova] Done — {eligible} eligible, {action} need action. Next in {SCAN_INTERVAL_H}h.")
        time.sleep(SCAN_INTERVAL_H * 3600)


# ── HTTP server ───────────────────────────────────────────────────────────────
class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/health":
            self._send(200, b"ok", "text/plain")

        elif self.path.startswith("/opportunities"):
            with STATE_LOCK:
                body = json.dumps({
                    "status":        STATE["status"],
                    "last_run":      STATE["last_run"],
                    "next_run":      STATE["next_run"],
                    "wallet_eth":    STATE["wallet_eth"],
                    "wallet_sol":    STATE["wallet_sol"],
                    "opportunities": STATE["opportunities"],
                }, default=str).encode()
            self._send(200, body, "application/json")

        elif self.path.startswith("/status"):
            with STATE_LOCK:
                ops = STATE["opportunities"]
                body = json.dumps({
                    "agent":    "nova",
                    "version":  "1.0.0",
                    "status":   STATE["status"],
                    "last_run": STATE["last_run"],
                    "next_run": STATE["next_run"],
                    "eligible": sum(1 for o in ops if o["badge"] == "ELIGIBLE"),
                    "action":   sum(1 for o in ops if o["badge"] == "ACTION_NEEDED"),
                    "manual":   sum(1 for o in ops if o["badge"] == "MANUAL"),
                    "error":    STATE["error"],
                }, default=str).encode()
            self._send(200, body, "application/json")

        else:
            self._send(404, b"not found", "text/plain")

    def _send(self, code, body, ct):
        self.send_response(code)
        self.send_header("Content-Type", ct)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_):
        pass


if __name__ == "__main__":
    if not WALLET_ETH:
        print("[nova] WARNING: WALLET_ETH not set — eligibility checks will be limited")
    threading.Thread(target=scan_loop, daemon=True).start()
    print(f"[nova] HTTP server on :{PORT}")
    HTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
