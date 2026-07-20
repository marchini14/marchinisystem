#!/usr/bin/env python3
"""Zero agent — smart contract static analysis scanner.

HTTP server on :8080
  GET /health  → 200 "ok"
  GET /status  → JSON scan state
  GET /report  → JSON latest findings

Background thread runs Slither scan every SCAN_INTERVAL_H hours.
Research only — never exploits, never touches funds, never auto-submits.
"""
import json
import os
import subprocess
import tempfile
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

ALCHEMY_KEY     = os.environ.get("ALCHEMY_KEY", "")
ETHERSCAN_KEY   = os.environ.get("ETHERSCAN_KEY", "")
SCAN_LIMIT      = int(os.environ.get("SCAN_LIMIT", "20"))
SCAN_INTERVAL_H = float(os.environ.get("SCAN_INTERVAL_H", "6"))
PORT            = int(os.environ.get("PORT", "8080"))

TVL_MIN = 100_000
TVL_MAX = 5_000_000
DETECTORS = (
    "reentrancy-eth,reentrancy-no-eth,reentrancy-unlimited-gas,"
    "protected-vars,unprotected-upgrade,events-access"
)
WHITELIST = {
    "uniswap", "aave", "lido", "1inch", "compound", "makerdao", "curve",
    "balancer", "sushiswap", "yearn", "convex", "frax", "rocket pool",
    "notional", "gmx", "synthetix",
}

STATE = {
    "status": "starting",
    "last_run": None,
    "next_run": None,
    "findings": [],
    "scanned": 0,
    "error": None,
}
STATE_LOCK = threading.Lock()


def http_get(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def http_post_json(url, payload):
    body = json.dumps(payload).encode()
    req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read())


def fetch_targets():
    protocols = http_get("https://api.llama.fi/protocols")
    targets = []
    for p in protocols:
        name = (p.get("name") or "").strip()
        tvl  = p.get("tvl") or 0
        if not (TVL_MIN <= tvl <= TVL_MAX):
            continue
        if any(w in name.lower() for w in WHITELIST):
            continue
        addr_field = p.get("address")
        addr = None
        if isinstance(addr_field, str) and addr_field.startswith("0x") and len(addr_field) == 42:
            addr = addr_field
        elif isinstance(addr_field, dict):
            for key in ("Ethereum", "ethereum"):
                v = addr_field.get(key)
                if isinstance(v, str) and v.startswith("0x") and len(v) == 42:
                    addr = v
                    break
        if not addr:
            continue
        targets.append({"name": name, "addr": addr, "tvl": tvl})
    targets.sort(key=lambda t: t["tvl"])
    return targets


def get_code(addr):
    alchemy_url = f"https://eth-mainnet.g.alchemy.com/v2/{ALCHEMY_KEY}"
    try:
        r = http_post_json(alchemy_url, {
            "jsonrpc": "2.0", "method": "eth_getCode",
            "params": [addr, "latest"], "id": 1,
        })
        return r.get("result", "0x")
    except Exception:
        return "0x"


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
        return None, (proc.stderr or "")[-600:]
    except Exception as e:
        return None, str(e)
    finally:
        Path(out_path).unlink(missing_ok=True)


def run_scan():
    if not ALCHEMY_KEY or not ETHERSCAN_KEY:
        return [], 0, "ALCHEMY_KEY or ETHERSCAN_KEY not set"

    print("[zero] Fetching DeFiLlama targets…")
    try:
        targets = fetch_targets()
    except Exception as e:
        return [], 0, f"DeFiLlama fetch failed: {e}"

    findings, scanned = [], 0
    for t in targets:
        if scanned >= SCAN_LIMIT:
            break
        addr, name = t["addr"], t["name"]
        if get_code(addr) in ("0x", "0x0", None):
            continue
        src = etherscan_source(addr)
        if not src.get("SourceCode", "").strip():
            continue
        real_addr, src = resolve_impl(addr, src)
        scanned += 1
        print(f"[zero] [{scanned}/{SCAN_LIMIT}] {name} ({real_addr})")
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
                "protocol":    name,
                "address":     real_addr,
                "check":       d.get("check"),
                "impact":      d.get("impact"),
                "confidence":  d.get("confidence"),
                "lines":       lines[:20],
                "description": (d.get("description") or "")[:400],
                "ts":          time.time(),
            })
            print(f"[zero]   [!] {d.get('check')} impact={d.get('impact')}")

    return findings, scanned, None


def scan_loop():
    while True:
        with STATE_LOCK:
            STATE["status"] = "scanning"
            STATE["error"]  = None

        findings, scanned, err = run_scan()

        with STATE_LOCK:
            STATE.update({
                "status":   "idle",
                "findings": findings,
                "scanned":  scanned,
                "last_run": time.time(),
                "next_run": time.time() + SCAN_INTERVAL_H * 3600,
                "error":    err,
            })

        print(f"[zero] Done — {len(findings)} findings from {scanned} contracts. Next in {SCAN_INTERVAL_H}h.")
        time.sleep(SCAN_INTERVAL_H * 3600)


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
                    "count":    len(STATE["findings"]),
                    "findings": STATE["findings"],
                }, default=str).encode()
            self._send(200, body, "application/json")
        elif self.path.startswith("/status"):
            with STATE_LOCK:
                body = json.dumps({
                    "agent":    "zero",
                    "version":  "1.0.0",
                    "status":   STATE["status"],
                    "last_run": STATE["last_run"],
                    "next_run": STATE["next_run"],
                    "scanned":  STATE["scanned"],
                    "count":    len(STATE["findings"]),
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
    threading.Thread(target=scan_loop, daemon=True).start()
    print(f"[zero] HTTP server on :{PORT}")
    HTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
