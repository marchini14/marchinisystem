#!/usr/bin/env python3
"""Zero agent — smart contract bug bounty scanner.

Source: Immunefi public bounty list (immunefi.com/public-api/bounties.json)
Filter: no "Pay to Submit", no KYC, not invite-only, on-chain reward pool
        already funded (not just an advertised max), Ethereum mainnet assets

HTTP server on :8080
  GET  /health   → 200 "ok"
  GET  /status   → JSON scan state
  GET  /report   → JSON latest findings with platform + bounty info
  POST /simulate → run a candidate PoC call through Alchemy's
                    alchemy_simulateAssetChanges (dry-run, never broadcast)
                    body: {"to": "0x...", "data": "0x...", "value": "0x0"}

Research only — never exploits, never touches funds, never auto-submits.
Every finding needs manual review before submission. /simulate never signs
or broadcasts a real transaction — it only asks Alchemy what a transaction
*would* do, so it is safe to try PoC calldata here before touching a fork.
"""
import json
import os
import re
import subprocess
import tempfile
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

ETHERSCAN_KEY   = os.environ.get("ETHERSCAN_KEY", "")
ALCHEMY_KEY     = os.environ.get("ALCHEMY_KEY", "")
ALCHEMY_URL     = f"https://eth-mainnet.g.alchemy.com/v2/{ALCHEMY_KEY}"
SCAN_LIMIT      = int(os.environ.get("SCAN_LIMIT", "30"))
SCAN_INTERVAL_H = float(os.environ.get("SCAN_INTERVAL_H", "6"))
PORT            = int(os.environ.get("PORT", "8080"))

DETECTORS = (
    "reentrancy-eth,reentrancy-no-eth,reentrancy-unlimited-gas,"
    "protected-vars,unprotected-upgrade,events-access"
)

# ── Shared state ─────────────────────────────────────────────────────────────
STATE = {
    "status":   "starting",
    "last_run": None,
    "next_run": None,
    "findings": [],
    "scanned":  0,
    "programs": 0,
    "error":    None,
}
STATE_LOCK = threading.Lock()


# ── HTTP helpers ──────────────────────────────────────────────────────────────
def http_get(url, headers=None):
    h = {"User-Agent": "Mozilla/5.0"}
    if headers:
        h.update(headers)
    req = urllib.request.Request(url, headers=h)
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read())


# ── Alchemy helpers ───────────────────────────────────────────────────────────
def alchemy_rpc(method, params):
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode()
    req = urllib.request.Request(
        ALCHEMY_URL, data=body, headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read())


def get_code(addr):
    """eth_getCode — cheap pre-check so we don't waste an Etherscan+Slither
    call on a selfdestructed or otherwise codeless address."""
    try:
        r = alchemy_rpc("eth_getCode", [addr, "latest"])
        return r.get("result", "0x")
    except Exception:
        return "0x"


def simulate_asset_changes(to, data="0x", value="0x0", from_addr=None):
    """alchemy_simulateAssetChanges — dry-run a candidate PoC call. Never
    signs or broadcasts; Alchemy just reports what the call *would* move."""
    tx = {"to": to, "data": data or "0x", "value": value or "0x0"}
    if from_addr:
        tx["from"] = from_addr
    return alchemy_rpc("alchemy_simulateAssetChanges", [tx])


# ── Platform fetcher ──────────────────────────────────────────────────────────
IMMUNEFI_URL = "https://immunefi.com/public-api/bounties.json"


def fetch_immunefi():
    """Immunefi public bounty list.

    Filter: no "Pay to Submit" fee, no KYC, not invite-only, on-chain reward
    pool already funded (rewardsPool/primaryPool > 0, money is actually there
    rather than just an advertised max), Ethereum mainnet contracts only
    (matches etherscan_source/run_slither, which are mainnet-only).
    """
    targets = []
    try:
        programs = http_get(IMMUNEFI_URL)
    except Exception as e:
        print(f"[zero] immunefi fetch error: {e}")
        return targets

    for p in programs:
        if "Pay to Submit" in (p.get("features") or []):
            continue
        if p.get("kyc") or p.get("inviteOnly"):
            continue
        pool = (p.get("rewardsPool") or 0) + (p.get("primaryPool") or 0)
        if pool <= 0:
            continue

        contracts = []
        for a in p.get("assets", []):
            if a.get("type") != "smart_contract":
                continue
            url = a.get("url", "")
            if "etherscan.io" not in url:
                continue
            m = re.search(r"0x[a-fA-F0-9]{40}", url)
            if m:
                contracts.append(m.group(0))
        if not contracts:
            continue

        targets.append({
            "platform":    "immunefi",
            "name":        p.get("project", ""),
            "program_url": f"https://immunefi.com/bug-bounty/{p.get('slug', '')}",
            "contracts":   contracts,
            "max_bounty":  str(p.get("maxBounty", "?")),
            "pool":        pool,
        })

    print(f"[zero] immunefi: {len(targets)} funded, free-to-submit, no-KYC programs")
    return targets


def etherscan_source(addr):
    url = (
        f"https://api.etherscan.io/v2/api?chainid=1&module=contract"
        f"&action=getsourcecode&address={addr}&apikey={ETHERSCAN_KEY}"
    )
    try:
        r = http_get(url)
        if r.get("status") == "1" and r.get("result"):
            return r["result"][0]
    except Exception:
        pass
    return {}


def resolve_impl(addr, src, depth=0):
    if depth > 2:
        return addr, src
    if src.get("IsProxy") == "1" and src.get("Implementation"):
        impl_src = etherscan_source(src["Implementation"])
        if impl_src.get("SourceCode", "").strip():
            return resolve_impl(src["Implementation"], impl_src, depth + 1)
    return addr, src


def run_slither(addr):
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        out_path = f.name
    try:
        cmd = [
            "slither", addr,
            "--etherscan-apikey", ETHERSCAN_KEY,
            "--detect", DETECTORS,
            "--json", out_path,
            "--disable-color",
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
        p = Path(out_path)
        if p.exists() and p.stat().st_size > 0:
            return json.loads(p.read_text()), None
        return None, (proc.stderr or "")[-400:]
    except Exception as e:
        return None, str(e)
    finally:
        Path(out_path).unlink(missing_ok=True)


# ── Main scan ─────────────────────────────────────────────────────────────────
def run_scan():
    if not ETHERSCAN_KEY:
        return [], 0, 0, "ETHERSCAN_KEY not set"

    all_programs = fetch_immunefi()
    if not all_programs:
        return [], 0, 0, "immunefi returned no eligible programs"

    # Build flat target list: (addr, program_meta)
    targets = []
    seen = set()
    for prog in all_programs:
        for addr in prog.get("contracts", []):
            addr_lc = addr.lower()
            if addr_lc not in seen:
                seen.add(addr_lc)
                targets.append((addr, prog))

    print(f"[zero] {len(all_programs)} programs → {len(targets)} unique contract addresses")

    findings, scanned = [], 0
    for addr, prog in targets:
        if scanned >= SCAN_LIMIT:
            break
        if get_code(addr) in ("0x", "0x0", None):
            print(f"[zero] {addr[:10]}… no code (selfdestructed?), skip")
            continue
        src = etherscan_source(addr)
        if not src.get("SourceCode", "").strip():
            print(f"[zero] {addr[:10]}… not verified, skip")
            continue
        real_addr, src = resolve_impl(addr, src)
        scanned += 1
        print(f"[zero] [{scanned}/{SCAN_LIMIT}] {prog['name']} ({prog['platform']}) {real_addr[:10]}…")

        data, err = run_slither(real_addr)
        if not data or not data.get("success"):
            continue

        for d in data.get("results", {}).get("detectors", []):
            lines = sorted({
                ln
                for el in d.get("elements", [])
                for ln in el.get("source_mapping", {}).get("lines", [])
            })
            findings.append({
                "platform":    prog["platform"],
                "program":     prog["name"],
                "program_url": prog["program_url"],
                "max_bounty":  prog.get("max_bounty", "?"),
                "address":     real_addr,
                "check":       d.get("check"),
                "impact":      d.get("impact"),
                "confidence":  d.get("confidence"),
                "lines":       lines[:20],
                "description": (d.get("description") or "")[:400],
                "ts":          time.time(),
                "needs_poc_funds": False,
            })
            print(f"[zero]   [!] {d.get('check')} {d.get('impact')} — {prog['platform']} / {prog['name']}")

    return findings, scanned, len(all_programs), None


# ── Scan loop ─────────────────────────────────────────────────────────────────
def scan_loop():
    while True:
        with STATE_LOCK:
            STATE["status"] = "scanning"
            STATE["error"]  = None

        findings, scanned, programs, err = run_scan()

        with STATE_LOCK:
            STATE.update({
                "status":   "idle",
                "findings": findings,
                "scanned":  scanned,
                "programs": programs,
                "last_run": time.time(),
                "next_run": time.time() + SCAN_INTERVAL_H * 3600,
                "error":    err,
            })

        print(f"[zero] Done — {len(findings)} findings from {scanned} contracts "
              f"({programs} programs). Next in {SCAN_INTERVAL_H}h.")
        time.sleep(SCAN_INTERVAL_H * 3600)


# ── HTTP server ───────────────────────────────────────────────────────────────
class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/health":
            self._send(200, b"ok", "text/plain")
        elif self.path.startswith("/report"):
            with STATE_LOCK:
                body = json.dumps({
                    "status":   STATE["status"],
                    "last_run": STATE["last_run"],
                    "next_run": STATE["next_run"],
                    "scanned":  STATE["scanned"],
                    "programs": STATE["programs"],
                    "count":    len(STATE["findings"]),
                    "findings": STATE["findings"],
                }, default=str).encode()
            self._send(200, body, "application/json")
        elif self.path.startswith("/status"):
            with STATE_LOCK:
                body = json.dumps({
                    "agent":    "zero",
                    "version":  "2.0.0",
                    "status":   STATE["status"],
                    "last_run": STATE["last_run"],
                    "next_run": STATE["next_run"],
                    "scanned":  STATE["scanned"],
                    "programs": STATE["programs"],
                    "count":    len(STATE["findings"]),
                    "error":    STATE["error"],
                }, default=str).encode()
            self._send(200, body, "application/json")
        else:
            self._send(404, b"not found", "text/plain")

    def do_POST(self):
        if not self.path.startswith("/simulate"):
            self._send(404, b"not found", "text/plain")
            return
        try:
            length = int(self.headers.get("Content-Length", 0))
            req = json.loads(self.rfile.read(length) or b"{}")
            to = req.get("to")
            if not to:
                self._send(400, json.dumps({"error": "\"to\" is required"}).encode(), "application/json")
                return
            result = simulate_asset_changes(
                to=to,
                data=req.get("data", "0x"),
                value=req.get("value", "0x0"),
                from_addr=req.get("from"),
            )
            self._send(200, json.dumps(result, default=str).encode(), "application/json")
        except Exception as e:
            self._send(500, json.dumps({"error": str(e)}).encode(), "application/json")

    def _send(self, code, body, ct):
        self.send_response(code)
        self.send_header("Content-Type", ct)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_):
        pass


if __name__ == "__main__":
    threading.Thread(target=scan_loop, daemon=True).start()
    print(f"[zero] HTTP server on :{PORT} — source: Immunefi (funded, free-to-submit, no-KYC)")
    HTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
