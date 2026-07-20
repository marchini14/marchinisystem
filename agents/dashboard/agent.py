#!/usr/bin/env python3
"""Dashboard agent — server-side live status page for Zero + Nova.

HTTP server on :8080
  GET /        → HTML dashboard (HTTP Basic Auth required)
  GET /health  → 200 "ok" (no auth, for Northflank healthcheck)

Background thread refreshes state every REFRESH_INTERVAL_MIN minutes by
querying the Northflank API (containers + logs for zero-agent/nova-agent)
and the public GitHub API. Read-only — never touches funds, never signs
anything, never modifies any other service.
"""
import base64
import json
import os
import re
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer

NORTHFLANK_TOKEN    = os.environ.get("NORTHFLANK_TOKEN", "")
DASHBOARD_USER      = os.environ.get("DASHBOARD_USER", "")
DASHBOARD_PASS      = os.environ.get("DASHBOARD_PASS", "")
REFRESH_INTERVAL_MIN = float(os.environ.get("REFRESH_INTERVAL_MIN", "20"))
PORT                = int(os.environ.get("PORT", "8080"))
PROJECT             = "agency-v3"

STATE = {"html": "<p>warming up…</p>", "last_run": None}
STATE_LOCK = threading.Lock()


# ── HTTP helpers ──────────────────────────────────────────────────────────────
def nf_get(path):
    req = urllib.request.Request(
        f"https://api.northflank.com/v1{path}",
        headers={"Authorization": f"Bearer {NORTHFLANK_TOKEN}"},
    )
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read())


def http_get(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read())


def nf_logs(service, seconds=3600):
    now_ms = int(time.time() * 1000)
    start_ms = now_ms - seconds * 1000
    path = f"/projects/{PROJECT}/services/{service}/logs?startTime={start_ms}&duration={seconds}&direction=forward"
    data = nf_get(path)
    lines = data.get("logs") or data.get("data") or []
    if isinstance(lines, list) and lines and isinstance(lines[0], dict):
        lines = [l.get("log") or l.get("message") or "" for l in lines]
    return "\n".join(lines) if isinstance(lines, list) else str(lines)


# ── Zero status ───────────────────────────────────────────────────────────────
def get_zero_status():
    out = {"container": "UNREACHABLE", "created": "", "progress": "?/105",
           "immunefi": "?", "hackerone": "?", "combined": "?", "findings": [], "done_line": None}
    try:
        c = nf_get(f"/projects/{PROJECT}/services/zero-agent/containers")
        running = [x for x in c.get("data", {}).get("containers", []) if x.get("status") == "TASK_RUNNING"]
        if running:
            out["container"] = running[0]["name"]
            out["created"] = time.strftime("%H:%M:%S UTC", time.gmtime(running[0]["createdAt"]))
    except Exception:
        pass
    try:
        log = nf_logs("zero-agent")
        m = re.findall(r"\[(\d+)/105\]", log)
        if m:
            out["progress"] = f"{m[0]}/105"
        m = re.search(r"immunefi: (\d+) funded[^\n]*", log)
        if m:
            out["immunefi"] = m.group(0).replace("immunefi: ", "")
        m = re.search(r"hackerone: (\d+ of \d+[^\n]*|\d+ programs[^\n]*)", log)
        if m:
            out["hackerone"] = m.group(0)
        m = re.search(r"(\d+) programs? → (\d+) unique[^\n]*", log)
        if m:
            out["combined"] = f"{m.group(1)} programs → {m.group(2)} addresses"
        out["findings"] = re.findall(r"\[zero\]\s+\[!\][^\n]*", log)
        m = re.search(r"Done — .*?\. Next in [\d.]+h\.", log)
        if m:
            out["done_line"] = m.group(0)
    except Exception:
        pass
    return out


# ── Nova status ───────────────────────────────────────────────────────────────
def get_nova_status():
    out = {"container": "UNREACHABLE", "created": "", "done_line": None,
           "eligible": [], "action": [], "manual": []}
    try:
        c = nf_get(f"/projects/{PROJECT}/services/nova-agent/containers")
        running = [x for x in c.get("data", {}).get("containers", []) if x.get("status") == "TASK_RUNNING"]
        if running:
            out["container"] = running[0]["name"]
            out["created"] = time.strftime("%H:%M:%S UTC", time.gmtime(running[0]["createdAt"]))
    except Exception:
        pass
    try:
        log = nf_logs("nova-agent")
        m = re.search(r"\[nova\] Done — .*", log)
        if m:
            out["done_line"] = m.group(0).replace("[nova] ", "")
        # nf_logs returns newest-first: a badge line's real "Checking NAME"
        # line is the very NEXT entry in this array (immediately preceding
        # it in real time). Pair by adjacency, not accumulated state, so a
        # missing/errored line can't bleed a stale name onto the wrong badge.
        lines = log.splitlines()
        for i, line in enumerate(lines):
            bm = re.search(r"\[nova\]\s+(ELIGIBLE|ACTION_NEEDED|MANUAL):", line)
            if not bm or i + 1 >= len(lines):
                continue
            cm = re.search(r"\[nova\] Checking (.+?)(?:…|\.\.\.)", lines[i + 1])
            if not cm:
                continue
            bucket = {"ELIGIBLE": "eligible", "ACTION_NEEDED": "action", "MANUAL": "manual"}[bm.group(1)]
            name = cm.group(1)
            if name not in out[bucket]:
                out[bucket].append(name)
    except Exception:
        pass
    return out


# ── GitHub ────────────────────────────────────────────────────────────────────
def get_github_status():
    commits, runs = [], []
    try:
        commits = http_get(
            "https://api.github.com/repos/marchini14/marchinisystem/commits"
            "?sha=claude/todo-implementation-o9w30h&per_page=10"
        )
    except Exception:
        commits = None
    try:
        d = http_get(
            "https://api.github.com/repos/marchini14/marchinisystem/actions/runs"
            "?branch=claude/todo-implementation-o9w30h&per_page=8"
        )
        runs = d.get("workflow_runs", [])
    except Exception:
        runs = None
    return commits, runs


# ── HTML render ───────────────────────────────────────────────────────────────
CSS = """
:root {
  --bg: #0a0d12; --surface: #12161f; --surface-2: #171c27; --border: #232a38;
  --text: #e7eaf0; --text-dim: #8a93a6; --text-faint: #5b6272;
  --accent: #35d0b8; --accent-dim: #1c6e63;
  --good: #6fdb8f; --good-dim: #234a34; --warn: #f0b94a; --warn-dim: #4a3a1c;
  --crit: #f2617a; --crit-dim: #4a1f2a; --info: #7c9cf0; --info-dim: #23294a;
  --mono: ui-monospace, "SF Mono", "Cascadia Code", "JetBrains Mono", Consolas, monospace;
  --sans: ui-sans-serif, -apple-system, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
}
:root[data-theme="light"] {
  --bg: #f3f4f7; --surface: #fff; --surface-2: #eef0f4; --border: #dde1e8;
  --text: #171b24; --text-dim: #565f72; --text-faint: #8891a3;
  --accent: #0d9488; --accent-dim: #d4f3ee; --good: #16a34a; --good-dim: #dcf5e3;
  --warn: #b3760a; --warn-dim: #fbedd2; --crit: #d5304a; --crit-dim: #fbdde2;
  --info: #3457c7; --info-dim: #dfe6fb;
}
@media (prefers-color-scheme: light) {
  :root:not([data-theme="dark"]) {
    --bg: #f3f4f7; --surface: #fff; --surface-2: #eef0f4; --border: #dde1e8;
    --text: #171b24; --text-dim: #565f72; --text-faint: #8891a3;
    --accent: #0d9488; --accent-dim: #d4f3ee; --good: #16a34a; --good-dim: #dcf5e3;
    --warn: #b3760a; --warn-dim: #fbedd2; --crit: #d5304a; --crit-dim: #fbdde2;
    --info: #3457c7; --info-dim: #dfe6fb;
  }
}
* { box-sizing: border-box; }
body { margin: 0; background: var(--bg); color: var(--text); font-family: var(--sans);
  font-size: 14px; line-height: 1.5; padding: 28px 20px 60px; }
.wrap { max-width: 1180px; margin: 0 auto; }
header { display: flex; align-items: baseline; justify-content: space-between; flex-wrap: wrap;
  gap: 10px 24px; border-bottom: 1px solid var(--border); padding-bottom: 18px; margin-bottom: 24px; }
h1 { font-family: var(--mono); font-size: 20px; font-weight: 700; letter-spacing: 0.02em; margin: 0; text-wrap: balance; }
h1 .dim { color: var(--text-faint); font-weight: 500; }
.meta { font-family: var(--mono); font-size: 12px; color: var(--text-dim); display: flex; align-items: center; gap: 8px; }
.pulse { width: 7px; height: 7px; border-radius: 50%; background: var(--accent);
  animation: pulse 2.2s infinite; flex-shrink: 0; }
@media (prefers-reduced-motion: reduce) { .pulse { animation: none; } }
@keyframes pulse { 0% { box-shadow: 0 0 0 0 color-mix(in srgb, var(--accent) 55%, transparent); }
  70% { box-shadow: 0 0 0 7px transparent; } 100% { box-shadow: 0 0 0 0 transparent; } }
.stats { display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px; margin-bottom: 22px; }
.stat { background: var(--surface); border: 1px solid var(--border); border-radius: 10px; padding: 14px 16px; }
.stat .label { font-size: 11px; text-transform: uppercase; letter-spacing: 0.06em; color: var(--text-faint); margin-bottom: 6px; }
.stat .value { font-family: var(--mono); font-size: 24px; font-weight: 700; font-variant-numeric: tabular-nums; }
.stat .sub { font-size: 12px; color: var(--text-dim); margin-top: 2px; }
.grid2 { display: grid; grid-template-columns: 1fr 1fr; gap: 14px; margin-bottom: 22px; }
.card { background: var(--surface); border: 1px solid var(--border); border-radius: 12px; padding: 18px 20px 20px; }
.agent-name { font-family: var(--mono); font-size: 15px; font-weight: 700; letter-spacing: 0.03em; display: flex; align-items: center; gap: 8px; }
.pill { font-family: var(--mono); font-size: 10.5px; font-weight: 700; letter-spacing: 0.05em; padding: 2px 8px; border-radius: 20px; text-transform: uppercase; }
.pill.running { background: var(--good-dim); color: var(--good); }
.pill.down { background: var(--crit-dim); color: var(--crit); }
.agent-sub { font-size: 12px; color: var(--text-faint); font-family: var(--mono); margin-bottom: 16px; }
.progress-row { display: flex; justify-content: space-between; align-items: baseline; font-family: var(--mono); font-size: 12px; color: var(--text-dim); margin-bottom: 6px; }
.progress-row b { color: var(--text); font-size: 13px; }
.progress-track { height: 8px; border-radius: 5px; background: var(--surface-2); border: 1px solid var(--border); overflow: hidden; margin-bottom: 18px; }
.progress-fill { height: 100%; border-radius: 5px 0 0 5px; background: linear-gradient(90deg, var(--accent-dim), var(--accent)); }
.kv-list { display: flex; flex-direction: column; gap: 9px; margin-bottom: 16px; }
.kv { display: flex; justify-content: space-between; gap: 12px; font-size: 12.5px; border-bottom: 1px dashed var(--border); padding-bottom: 9px; }
.kv:last-child { border-bottom: none; padding-bottom: 0; }
.kv .k { color: var(--text-dim); }
.kv .v { font-family: var(--mono); text-align: right; color: var(--text); font-variant-numeric: tabular-nums; }
.note { font-size: 12px; color: var(--text-faint); border-left: 2px solid var(--border); padding-left: 10px; margin-top: 4px; }
.breakdown-bar { display: flex; height: 20px; border-radius: 6px; overflow: hidden; margin-bottom: 12px; border: 1px solid var(--border); }
.breakdown-bar span { display: block; height: 100%; }
.bd-eligible { background: var(--good); } .bd-action { background: var(--warn); } .bd-manual { background: var(--surface-2); }
.legend { display: flex; gap: 16px; flex-wrap: wrap; margin-bottom: 16px; font-size: 12px; }
.legend-item { display: flex; align-items: center; gap: 6px; color: var(--text-dim); }
.swatch { width: 9px; height: 9px; border-radius: 2px; flex-shrink: 0; }
.tag-group { margin-bottom: 12px; }
.tag-group .tg-label { font-size: 10.5px; text-transform: uppercase; letter-spacing: 0.05em; color: var(--text-faint); margin-bottom: 6px; }
.tags { display: flex; flex-wrap: wrap; gap: 6px; }
.tag { font-family: var(--mono); font-size: 11px; padding: 3px 8px; border-radius: 5px; background: var(--surface-2); border: 1px solid var(--border); color: var(--text-dim); }
.section-title { font-family: var(--mono); font-size: 12px; text-transform: uppercase; letter-spacing: 0.06em; color: var(--text-faint); margin: 0 0 10px 2px; }
.panel { background: var(--surface); border: 1px solid var(--border); border-radius: 12px; overflow: hidden; margin-bottom: 22px; }
.log-row { display: grid; grid-template-columns: 92px 1fr auto; gap: 14px; align-items: center; padding: 10px 18px; border-bottom: 1px solid var(--border); font-size: 12.5px; }
.log-row:last-child { border-bottom: none; }
.log-sha { font-family: var(--mono); color: var(--text-faint); font-size: 11.5px; }
.log-msg { color: var(--text); }
.chip { font-family: var(--mono); font-size: 10.5px; font-weight: 700; letter-spacing: 0.04em; padding: 2px 7px; border-radius: 5px; text-transform: uppercase; white-space: nowrap; }
.chip.success { background: var(--good-dim); color: var(--good); }
.chip.failure { background: var(--crit-dim); color: var(--crit); }
.chip.running { background: var(--info-dim); color: var(--info); }
.svc-table { width: 100%; overflow-x: auto; }
table { width: 100%; border-collapse: collapse; font-size: 12.5px; }
th { text-align: left; font-size: 10.5px; text-transform: uppercase; letter-spacing: 0.05em; color: var(--text-faint); padding: 10px 18px; border-bottom: 1px solid var(--border); font-weight: 600; }
td { padding: 10px 18px; border-bottom: 1px solid var(--border); font-family: var(--mono); }
tr:last-child td { border-bottom: none; }
footer { font-family: var(--mono); font-size: 11.5px; color: var(--text-faint); text-align: center; padding-top: 10px; }
@media (max-width: 720px) { .stats { grid-template-columns: repeat(2, 1fr); } .grid2 { grid-template-columns: 1fr; } .log-row { grid-template-columns: 1fr; row-gap: 4px; } }
"""


def esc(s):
    return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def render_html(zero, nova, commits, runs):
    now = time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime())
    cur, total = (zero["progress"].split("/") + ["105"])[:2]
    try:
        pct = max(2, min(100, round(int(cur) / int(total) * 100)))
    except Exception:
        pct = 2

    findings_html = "".join(f'<div class="kv"><span class="v">{esc(f)}</span></div>' for f in zero["findings"]) \
        or '<div class="kv"><span class="k">Findings this cycle</span><span class="v">0 so far</span></div>'

    def tag_row(label, names):
        tags = "".join(f'<span class="tag">{esc(n)}</span>' for n in names) or '<span class="tag">—</span>'
        return f'<div class="tag-group"><div class="tg-label">{esc(label)} ({len(names)})</div><div class="tags">{tags}</div></div>'

    n_e, n_a, n_m = len(nova["eligible"]), len(nova["action"]), len(nova["manual"])
    n_total = max(1, n_e + n_a + n_m)

    zero_pill = 'running' if zero["container"] != "UNREACHABLE" else 'down'
    nova_pill = 'running' if nova["container"] != "UNREACHABLE" else 'down'

    log_rows = ""
    if commits:
        for c in commits[:8]:
            sha = c.get("sha", "")[:7]
            msg = (c.get("commit", {}).get("message", "") or "").splitlines()[0][:80]
            log_rows += f'<div class="log-row"><span class="log-sha">{sha}</span><span class="log-msg">{esc(msg)}</span><span></span></div>'
    else:
        log_rows = '<div class="log-row"><span class="log-sha">—</span><span class="log-msg">UNREACHABLE</span><span class="chip failure">error</span></div>'

    run_rows = ""
    if runs:
        for r in runs[:8]:
            chip = "success" if r.get("conclusion") == "success" else ("running" if r.get("status") == "in_progress" else "failure")
            label = r.get("conclusion") or r.get("status") or "?"
            run_rows += f'<div class="log-row"><span class="log-sha">{r.get("head_sha","")[:7]}</span><span class="log-msg">{esc(r.get("name",""))}</span><span class="chip {chip}">{esc(label)}</span></div>'

    return f"""<!doctype html><html><head><meta charset="utf-8"><title>Agency V3 — Live Status</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>{CSS}</style></head><body><div class="wrap">
<header>
  <h1>AGENCY V3 <span class="dim">/ live status</span></h1>
  <div class="meta"><span class="pulse"></span> auto-refreshing every {REFRESH_INTERVAL_MIN:g} min — server-side, independent of any Claude session · last generated <strong>{now}</strong></div>
</header>
<div class="stats">
  <div class="stat"><div class="label">Agents live</div><div class="value">{(1 if zero_pill=='running' else 0)+(1 if nova_pill=='running' else 0)} / 2</div><div class="sub">zero-agent · nova-agent</div></div>
  <div class="stat"><div class="label">Zero — this cycle</div><div class="value">{esc(zero["progress"])}</div><div class="sub">contracts scanned</div></div>
  <div class="stat"><div class="label">Nova — last cycle</div><div class="value">{n_e} eligible</div><div class="sub">of {n_total} tracked protocols</div></div>
  <div class="stat"><div class="label">Zero findings</div><div class="value">{len(zero["findings"])}</div><div class="sub">this cycle</div></div>
</div>
<div class="grid2">
  <div class="card">
    <div class="card-head"><div class="agent-name">ZERO <span class="pill {zero_pill}">{zero_pill}</span></div></div>
    <div class="agent-sub">smart-contract bounty scanner · {esc(zero["container"])} · up since {esc(zero["created"])}</div>
    <div class="progress-row"><span>scan cycle</span><b>{esc(zero["progress"])}</b></div>
    <div class="progress-track"><div class="progress-fill" style="width:{pct}%"></div></div>
    <div class="kv-list">
      <div class="kv"><span class="k">Immunefi</span><span class="v">{esc(str(zero["immunefi"]))}</span></div>
      <div class="kv"><span class="k">HackerOne</span><span class="v">{esc(str(zero["hackerone"]))}</span></div>
      <div class="kv"><span class="k">Combined targets</span><span class="v">{esc(str(zero["combined"]))}</span></div>
      {findings_html}
    </div>
    <div class="note">{esc(zero["done_line"]) if zero["done_line"] else "Cycle in progress."}</div>
  </div>
  <div class="card">
    <div class="card-head"><div class="agent-name">NOVA <span class="pill {nova_pill}">{nova_pill}</span></div></div>
    <div class="agent-sub">airdrop / points eligibility tracker · {esc(nova["container"])} · up since {esc(nova["created"])}</div>
    <div class="progress-row"><span>last completed cycle</span><b>{n_total} protocols</b></div>
    <div class="breakdown-bar"><span class="bd-eligible" style="width:{n_e/n_total*100:.0f}%"></span><span class="bd-action" style="width:{n_a/n_total*100:.0f}%"></span><span class="bd-manual" style="width:{n_m/n_total*100:.0f}%"></span></div>
    <div class="legend">
      <div class="legend-item"><span class="swatch" style="background:var(--good)"></span>{n_e} eligible</div>
      <div class="legend-item"><span class="swatch" style="background:var(--warn)"></span>{n_a} action needed</div>
      <div class="legend-item"><span class="swatch" style="background:var(--surface-2);border:1px solid var(--border)"></span>{n_m} manual</div>
    </div>
    {tag_row("Eligible", nova["eligible"])}
    {tag_row("Action needed", nova["action"])}
    <div class="note">{esc(nova["done_line"]) if nova["done_line"] else "No completed cycle yet."}</div>
  </div>
</div>
<div class="section-title">Recent commits</div>
<div class="panel">{log_rows}</div>
<div class="section-title">Recent Actions runs</div>
<div class="panel">{run_rows or '<div class="log-row"><span class="log-sha">—</span><span class="log-msg">UNREACHABLE</span><span class="chip failure">error</span></div>'}</div>
<footer>generated server-side by dashboard-agent · Northflank + GitHub APIs polled directly</footer>
</div></body></html>"""


def refresh_loop():
    while True:
        try:
            zero = get_zero_status()
            nova = get_nova_status()
            commits, runs = get_github_status()
            html = render_html(zero, nova, commits, runs)
            with STATE_LOCK:
                STATE["html"] = html
                STATE["last_run"] = time.time()
            print(f"[dashboard] refreshed — zero={zero['progress']} nova_eligible={len(nova['eligible'])}")
        except Exception as e:
            print(f"[dashboard] refresh error: {e}")
        time.sleep(REFRESH_INTERVAL_MIN * 60)


class Handler(BaseHTTPRequestHandler):
    def _check_auth(self):
        if not DASHBOARD_USER or not DASHBOARD_PASS:
            self._send(500, b"DASHBOARD_USER/DASHBOARD_PASS not configured", "text/plain")
            return False
        hdr = self.headers.get("Authorization", "")
        expected = "Basic " + base64.b64encode(f"{DASHBOARD_USER}:{DASHBOARD_PASS}".encode()).decode()
        if hdr != expected:
            self.send_response(401)
            self.send_header("WWW-Authenticate", 'Basic realm="dashboard"')
            self.send_header("Content-Length", "0")
            self.end_headers()
            return False
        return True

    def do_GET(self):
        if self.path == "/health":
            self._send(200, b"ok", "text/plain")
            return
        if not self._check_auth():
            return
        with STATE_LOCK:
            body = STATE["html"].encode()
        self._send(200, body, "text/html; charset=utf-8")

    def _send(self, code, body, ct):
        self.send_response(code)
        self.send_header("Content-Type", ct)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_):
        pass


if __name__ == "__main__":
    threading.Thread(target=refresh_loop, daemon=True).start()
    print(f"[dashboard] HTTP server on :{PORT}")
    HTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
