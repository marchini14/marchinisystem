#!/usr/bin/env python3
"""Nova agent — Airdrop & points-program opportunity hunter.

HTTP server on :8080
  GET /health        → 200 "ok"
  GET /status        → JSON scan state
  GET /opportunities → JSON scored opportunity list

Background thread checks wallet eligibility every SCAN_INTERVAL_H hours.
Read-only: never signs transactions, never holds private keys.
"""
import json
import os
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer

ETHERSCAN_KEY    = os.environ.get("ETHERSCAN_KEY", "")
ALCHEMY_KEY      = os.environ.get("ALCHEMY_KEY", "")
WALLET_ETH       = os.environ.get("WALLET_ETH", "").lower()
WALLET_SOL       = [w.strip() for w in os.environ.get("WALLET_SOL", "").split(",") if w.strip()]
SCAN_INTERVAL_H  = float(os.environ.get("SCAN_INTERVAL_H", "4"))
PORT             = int(os.environ.get("PORT", "8080"))

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
        "id": "monad",
        "name": "Monad",
        "chain": "monad_testnet",
        "contract": None,
        "action": "use Monad testnet faucet + testnet dApps",
        "check_type": "manual",
        "reward_est": "$1000–10000",
        "effort": "medium",
        "status": "testnet",
        "notes": "High-profile L1 testnet; mainnet launch TBA",
    },
    {
        "id": "movement",
        "name": "Movement Network",
        "chain": "movement",
        "contract": None,
        "action": "bridge to Movement and use ecosystem",
        "check_type": "manual",
        "reward_est": "$500–5000",
        "effort": "medium",
        "status": "active",
        "notes": "Move VM L2; token and airdrop TBA",
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
        "contract": "0x858646372CC42E1A627fcE94aa7A7033e7CF075A",
        "action": "restake ETH or LSTs on EigenLayer",
        "check_type": "etherscan_tx",
        "reward_est": "$500–5000",
        "effort": "medium",
        "status": "active",
        "notes": "EIGEN token live; Season 2 restaking rewards",
    },
    # ── Solana protocols (checked via Alchemy Solana RPC on WALLET_SOL) ──
    {
        "id": "jupiter",
        "name": "Jupiter (Solana aggregator)",
        "chain": "solana",
        "contract": None,
        "action": "swap through Jupiter aggregator on Solana",
        "check_type": "sol_activity",
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
        "action": "stake SOL with Jito (jitoSOL) or run validator",
        "check_type": "sol_activity",
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
        "action": "stake SOL with Marinade (mSOL)",
        "check_type": "sol_activity",
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
        "action": "trade perps or provide liquidity on Drift",
        "check_type": "sol_activity",
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
        "action": "supply/borrow or LP on Kamino",
        "check_type": "sol_activity",
        "reward_est": "$100–1500",
        "effort": "medium",
        "status": "active",
        "notes": "KMNO token live; points ongoing",
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


# ── Eligibility checkers ──────────────────────────────────────────────────────
def get_eth_tx_to(contract_addr, wallet):
    """Return number of txs from wallet to contract (Etherscan API)."""
    if not ETHERSCAN_KEY or not wallet:
        return 0
    url = (
        f"https://api.etherscan.io/v2/api?chainid=1&module=account"
        f"&action=txlist&address={wallet}&startblock=0&endblock=99999999"
        f"&sort=asc&apikey={ETHERSCAN_KEY}"
    )
    try:
        r = http_get(url)
        if r.get("status") != "1":
            return 0
        txs = r.get("result", [])
        target = contract_addr.lower()
        return sum(1 for tx in txs if tx.get("to", "").lower() == target)
    except Exception:
        return 0


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
        count = get_eth_tx_to(contract, WALLET_ETH)
        if count > 0:
            return True, f"{count} tx(s) to bridge contract"
        return False, "no interactions found — action needed"

    elif ct == "api" and proto["id"] == "hyperliquid":
        val = get_hyperliquid_value(WALLET_ETH)
        if val > 0:
            return True, f"account value ${val:.2f}"
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

    elif ct == "manual":
        return None, "manual check required — see notes"

    return None, "check type not implemented"


def run_scan():
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
