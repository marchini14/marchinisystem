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
HISTORY = []          # rolling [{t, zero_pct, nova_eligible}], newest last
HISTORY_MAX = 60      # ~20h at a 20min refresh — real samples, not synthetic


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
           "immunefi": "?", "hackerone": "?", "combined": "?", "findings": [], "done_line": None,
           "raw": []}
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
        out["raw"] = [l for l in log.splitlines() if l.strip()][:80]
    except Exception:
        pass
    return out


# ── Nova status ───────────────────────────────────────────────────────────────
def get_nova_status():
    out = {"container": "UNREACHABLE", "created": "", "done_line": None,
           "eligible": [], "action": [], "manual": [], "raw": []}
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
        out["raw"] = [l for l in lines if l.strip()][:80]
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
  --bg: #000000; --surface: #0d0d0d; --surface-2: #161616; --border: #272727;
  --text: #c9cdd6; --text-dim: #8a93a6; --text-faint: #5b6272;
  --accent: #00d4ff; --accent-dim: #0a3a4a;
  --good: #00d4ff; --good-dim: #0a3a4a; --warn: #f0b94a; --warn-dim: #4a3a1c;
  --crit: #ff2149; --crit-dim: #4a1520; --info: #00d4ff; --info-dim: #0a3a4a;
  --neon-red: #ff2149; --neon-blue: #00d4ff; --gold: #b89a4a;
  --mono: ui-monospace, "SF Mono", "Cascadia Code", "JetBrains Mono", Consolas, monospace;
  --sans: ui-sans-serif, -apple-system, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
}
:root[data-theme="light"] {
  --bg: #f3f4f7; --surface: #fff; --surface-2: #eef0f4; --border: #dde1e8;
  --text: #171b24; --text-dim: #565f72; --text-faint: #8891a3;
  --accent: #0090c7; --accent-dim: #d8f0fa; --good: #0090c7; --good-dim: #d8f0fa;
  --warn: #b3760a; --warn-dim: #fbedd2; --crit: #e2003f; --crit-dim: #fbdde2;
  --info: #0090c7; --info-dim: #d8f0fa;
  --neon-red: #e2003f; --neon-blue: #0090c7; --gold: #8a6000;
}
@media (prefers-color-scheme: light) {
  :root:not([data-theme="dark"]) {
    --bg: #f3f4f7; --surface: #fff; --surface-2: #eef0f4; --border: #dde1e8;
    --text: #171b24; --text-dim: #565f72; --text-faint: #8891a3;
    --accent: #0090c7; --accent-dim: #d8f0fa; --good: #0090c7; --good-dim: #d8f0fa;
    --warn: #b3760a; --warn-dim: #fbedd2; --crit: #e2003f; --crit-dim: #fbdde2;
    --info: #0090c7; --info-dim: #d8f0fa;
    --neon-red: #e2003f; --neon-blue: #0090c7; --gold: #8a6000;
  }
}
* { box-sizing: border-box; }
html { background: var(--bg); }
body { margin: 0; background: var(--bg); color: var(--text); font-family: var(--sans);
  font-size: 17px; line-height: 1.55; padding: 28px 20px 60px; position: relative; }

/* decorative background — real is-it-a-graph texture, pure CSS, no data behind it */
.bg-graph { position: fixed; inset: 0; z-index: 0; pointer-events: none; opacity: 0.5; }
:root[data-theme="light"] .bg-graph { opacity: 0.28; }
@media (prefers-color-scheme: light) { :root:not([data-theme="dark"]) .bg-graph { opacity: 0.28; } }
.bg-graph path { fill: none; stroke-width: 1.6; stroke-linecap: round; }
.bg-graph .l1 { stroke: var(--neon-red); stroke-dasharray: 6 10; animation: drift 60s linear infinite; }
.bg-graph .l2 { stroke: var(--neon-blue); stroke-dasharray: 4 14; animation: drift 90s linear infinite reverse; }
.bg-graph .l3 { stroke: var(--neon-blue); stroke-dasharray: 3 9; animation: drift 75s linear infinite; opacity: 0.6; }
@keyframes drift { to { stroke-dashoffset: -1000; } }
@media (prefers-reduced-motion: reduce) { .bg-graph path { animation: none; } }
.wrap { max-width: 1220px; margin: 0 auto; position: relative; z-index: 1; }

header { display: flex; align-items: baseline; justify-content: space-between; flex-wrap: wrap;
  gap: 10px 24px; border-bottom: 1px solid var(--border); padding-bottom: 18px; margin-bottom: 26px; }
h1 { font-family: var(--mono); font-size: 26px; font-weight: 700; letter-spacing: 0.02em; margin: 0; text-wrap: balance; color: var(--gold); }
h1 .dim { color: var(--text-faint); font-weight: 500; }
.meta { font-family: var(--mono); font-size: 14px; color: var(--text-dim); display: flex; align-items: center; gap: 8px; }
.pulse { width: 8px; height: 8px; border-radius: 50%; background: var(--accent);
  animation: pulse 2.2s infinite; flex-shrink: 0; }
@media (prefers-reduced-motion: reduce) { .pulse { animation: none; } }
@keyframes pulse { 0% { box-shadow: 0 0 0 0 color-mix(in srgb, var(--accent) 55%, transparent); }
  70% { box-shadow: 0 0 0 8px transparent; } 100% { box-shadow: 0 0 0 0 transparent; } }
.stats { display: grid; grid-template-columns: repeat(4, 1fr); gap: 14px; margin-bottom: 24px; }
.stat { background: var(--surface); border: 1px solid var(--border); border-radius: 12px; padding: 16px 18px; }
.stat .label { font-size: 13px; text-transform: uppercase; letter-spacing: 0.06em; color: var(--text-faint); margin-bottom: 7px; }
.stat .value { font-family: var(--mono); font-size: 36px; font-weight: 700; font-variant-numeric: tabular-nums; }
.stat .sub { font-size: 14px; color: var(--text-dim); margin-top: 3px; }
.stat svg { display: block; margin-top: 8px; width: 100%; height: 28px; }
.stat svg polyline { fill: none; stroke-width: 2; stroke-linecap: round; stroke-linejoin: round; }
.stat svg.spark-blue polyline { stroke: var(--neon-blue); }
.stat svg.spark-red polyline { stroke: var(--neon-red); }
.grid2 { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; margin-bottom: 24px; }
.card { background: var(--surface); border: 1px solid var(--border); border-radius: 14px; padding: 20px 22px 22px; }
.agent-name { font-family: var(--mono); font-size: 20px; font-weight: 700; letter-spacing: 0.03em; display: flex; align-items: center; gap: 10px; color: var(--gold); }
.pill { font-family: var(--mono); font-size: 12.5px; font-weight: 700; letter-spacing: 0.05em; padding: 3px 10px; border-radius: 20px; text-transform: uppercase; }
.pill.running { background: var(--good-dim); color: var(--good); }
.pill.down { background: var(--crit-dim); color: var(--crit); }
.agent-sub { font-size: 14px; color: var(--text-faint); font-family: var(--mono); margin-bottom: 18px; }
.progress-row { display: flex; justify-content: space-between; align-items: baseline; font-family: var(--mono); font-size: 14px; color: var(--text-dim); margin-bottom: 7px; }
.progress-row b { color: var(--text); font-size: 16px; }
.progress-track { height: 10px; border-radius: 6px; background: var(--surface-2); border: 1px solid var(--border); overflow: hidden; margin-bottom: 20px; }
.progress-fill { height: 100%; border-radius: 6px 0 0 6px; background: linear-gradient(90deg, var(--accent-dim), var(--accent)); }
.kv-list { display: flex; flex-direction: column; gap: 10px; margin-bottom: 18px; }
.kv { display: flex; justify-content: space-between; gap: 12px; font-size: 15px; border-bottom: 1px dashed var(--border); padding-bottom: 10px; }
.kv:last-child { border-bottom: none; padding-bottom: 0; }
.kv .k { color: var(--text-dim); }
.kv .v { font-family: var(--mono); text-align: right; color: var(--text); font-variant-numeric: tabular-nums; }
.note { font-size: 14px; color: var(--text-faint); border-left: 2px solid var(--border); padding-left: 11px; margin-top: 4px; }
.breakdown-bar { display: flex; height: 22px; border-radius: 7px; overflow: hidden; margin-bottom: 14px; border: 1px solid var(--border); }
.breakdown-bar span { display: block; height: 100%; }
.bd-eligible { background: var(--good); } .bd-action { background: var(--warn); } .bd-manual { background: var(--surface-2); }
.legend { display: flex; gap: 18px; flex-wrap: wrap; margin-bottom: 18px; font-size: 14px; }
.legend-item { display: flex; align-items: center; gap: 7px; color: var(--text-dim); }
.swatch { width: 10px; height: 10px; border-radius: 2px; flex-shrink: 0; }
.tag-group { margin-bottom: 14px; }
.tag-group .tg-label { font-size: 12px; text-transform: uppercase; letter-spacing: 0.05em; color: var(--text-faint); margin-bottom: 7px; }
.tags { display: flex; flex-wrap: wrap; gap: 7px; }
.tag { font-family: var(--mono); font-size: 13.5px; padding: 4px 10px; border-radius: 6px; background: var(--surface-2); border: 1px solid var(--border); color: var(--text-dim); }
.section-title { font-family: var(--mono); font-size: 14px; text-transform: uppercase; letter-spacing: 0.06em; color: var(--gold); margin: 0 0 11px 2px; }
.panel { background: var(--surface); border: 1px solid var(--border); border-radius: 14px; overflow: hidden; margin-bottom: 24px; }
.log-row { display: grid; grid-template-columns: 100px 1fr auto; gap: 14px; align-items: center; padding: 11px 20px; border-bottom: 1px solid var(--border); font-size: 14.5px; }
.log-row:last-child { border-bottom: none; }
.log-sha { font-family: var(--mono); color: var(--text-faint); font-size: 13px; }
.log-msg { color: var(--text); }
.chip { font-family: var(--mono); font-size: 12px; font-weight: 700; letter-spacing: 0.04em; padding: 3px 8px; border-radius: 5px; text-transform: uppercase; white-space: nowrap; }
.chip.success { background: var(--good-dim); color: var(--good); }
.chip.failure { background: var(--crit-dim); color: var(--crit); }
.chip.running { background: var(--info-dim); color: var(--info); }
.svc-table { width: 100%; overflow-x: auto; }
table { width: 100%; border-collapse: collapse; font-size: 14.5px; }
th { text-align: left; font-size: 12px; text-transform: uppercase; letter-spacing: 0.05em; color: var(--text-faint); padding: 11px 20px; border-bottom: 1px solid var(--border); font-weight: 600; }
td { padding: 11px 20px; border-bottom: 1px solid var(--border); font-family: var(--mono); }
tr:last-child td { border-bottom: none; }
footer { font-family: var(--mono); font-size: 13px; color: var(--text-faint); text-align: center; padding-top: 12px; }

/* live log search */
.search-box { width: 100%; font-family: var(--mono); font-size: 15px; padding: 12px 16px; margin-bottom: 14px;
  background: var(--surface-2); border: 1px solid var(--border); border-radius: 10px; color: var(--text); }
.search-box:focus { outline: 2px solid var(--accent); outline-offset: -1px; }
.search-box::placeholder { color: var(--text-faint); }
.live-log { max-height: 420px; overflow-y: auto; font-family: var(--mono); font-size: 13.5px; }
.ll-row { display: flex; gap: 10px; padding: 7px 18px; border-bottom: 1px solid var(--border); align-items: baseline; }
.ll-row:last-child { border-bottom: none; }
.ll-src { flex-shrink: 0; font-weight: 700; letter-spacing: 0.03em; }
.ll-src.zero { color: var(--info); } .ll-src.nova { color: var(--good); }
.ll-txt { color: var(--text-dim); overflow-wrap: anywhere; }
.ll-count { font-family: var(--mono); font-size: 13px; color: var(--text-faint); margin: -6px 0 12px 2px; }

@media (max-width: 720px) {
  body { font-size: 16px; }
  .stats { grid-template-columns: repeat(2, 1fr); }
  .grid2 { grid-template-columns: 1fr; }
  .log-row { grid-template-columns: 1fr; row-gap: 4px; }
  .stat .value { font-size: 28px; }
}
"""

JS = """
function llFilter(input) {
  var q = input.value.toLowerCase();
  var rows = document.querySelectorAll('.ll-row');
  var shown = 0;
  rows.forEach(function(r) {
    var hit = r.textContent.toLowerCase().indexOf(q) !== -1;
    r.style.display = hit ? '' : 'none';
    if (hit) shown++;
  });
  var countEl = document.getElementById('ll-count');
  if (countEl) countEl.textContent = shown + ' / ' + rows.length + ' linija';
}
"""


def esc(s):
    return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def sparkline(values, width=100, height=28, pad=2):
    """SVG polyline points from real sampled history — empty string if <2 samples (no faked data)."""
    if len(values) < 2:
        return ""
    vmin, vmax = min(values), max(values)
    if vmax == vmin:
        vmax = vmin + 1
    step = (width - 2 * pad) / (len(values) - 1)
    pts = []
    for i, v in enumerate(values):
        x = pad + i * step
        y = height - pad - (v - vmin) / (vmax - vmin) * (height - 2 * pad)
        pts.append(f"{x:.1f},{y:.1f}")
    return " ".join(pts)


BG_GRAPH_SVG = """<svg class="bg-graph" preserveAspectRatio="none" viewBox="0 0 400 300">
<path class="l1" d="M0,210 L40,190 L80,225 L120,160 L160,200 L200,130 L240,175 L280,110 L320,150 L360,95 L400,140"/>
<path class="l2" d="M0,90 L35,120 L75,80 L115,135 L155,100 L195,150 L235,105 L275,145 L315,90 L360,125 L400,85"/>
<path class="l3" d="M0,260 L45,240 L85,270 L125,235 L165,255 L205,220 L245,250 L285,215 L325,245 L365,205 L400,235"/>
</svg>"""


def build_live_log(zero_raw, nova_raw):
    rows = [("zero", l) for l in zero_raw] + [("nova", l) for l in nova_raw]
    if not rows:
        return '<div class="ll-row"><span class="ll-txt">nema logova jos — cekam prvi refresh ciklus</span></div>', 0
    html = "".join(
        f'<div class="ll-row"><span class="ll-src {src}">[{src.upper()}]</span><span class="ll-txt">{esc(line)}</span></div>'
        for src, line in rows
    )
    return html, len(rows)


def render_html(zero, nova, commits, runs, history):
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

    zero_spark = sparkline([h["zero_pct"] for h in history])
    nova_spark = sparkline([h["nova_eligible"] for h in history])
    zero_spark_svg = f'<svg class="spark-blue" viewBox="0 0 100 28" preserveAspectRatio="none"><polyline points="{zero_spark}"/></svg>' if zero_spark else ""
    nova_spark_svg = f'<svg class="spark-red" viewBox="0 0 100 28" preserveAspectRatio="none"><polyline points="{nova_spark}"/></svg>' if nova_spark else ""

    live_log_html, live_log_count = build_live_log(zero.get("raw", []), nova.get("raw", []))

    return f"""<!doctype html><html><head><meta charset="utf-8"><title>Agency V3 — Live Status</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>{CSS}</style></head><body>
{BG_GRAPH_SVG}
<div class="wrap">
<header>
  <h1>AGENCY V3 <span class="dim">/ live status</span></h1>
  <div class="meta"><span class="pulse"></span> auto-refreshing every {REFRESH_INTERVAL_MIN:g} min — server-side, independent of any Claude session · last generated <strong>{now}</strong></div>
</header>
<div class="stats">
  <div class="stat"><div class="label">Agents live</div><div class="value">{(1 if zero_pill=='running' else 0)+(1 if nova_pill=='running' else 0)} / 2</div><div class="sub">zero-agent · nova-agent</div></div>
  <div class="stat"><div class="label">Zero — this cycle</div><div class="value">{esc(zero["progress"])}</div><div class="sub">contracts scanned</div>{zero_spark_svg}</div>
  <div class="stat"><div class="label">Nova — last cycle</div><div class="value">{n_e} eligible</div><div class="sub">of {n_total} tracked protocols</div>{nova_spark_svg}</div>
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
<div class="section-title">Live logs — zero-agent + nova-agent (zadnjih {live_log_count} linija)</div>
<input class="search-box" type="text" placeholder="pretrazi live logove... (npr. contract adresa, ELIGIBLE, error)" oninput="llFilter(this)">
<div class="ll-count" id="ll-count">{live_log_count} / {live_log_count} linija</div>
<div class="panel live-log">{live_log_html}</div>
<footer>generated server-side by dashboard-agent · Northflank + GitHub APIs polled directly</footer>
</div>
<script>{JS}</script>
</body></html>"""


def refresh_loop():
    while True:
        try:
            zero = get_zero_status()
            nova = get_nova_status()
            commits, runs = get_github_status()

            cur, total = (zero["progress"].split("/") + ["105"])[:2]
            try:
                zero_pct = max(0, min(100, round(int(cur) / int(total) * 100)))
            except Exception:
                zero_pct = 0
            HISTORY.append({"t": time.time(), "zero_pct": zero_pct, "nova_eligible": len(nova["eligible"])})
            del HISTORY[:-HISTORY_MAX]

            html = render_html(zero, nova, commits, runs, HISTORY)
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
