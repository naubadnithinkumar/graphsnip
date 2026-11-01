"""Self-contained HTML dashboard: token savings, benchmark, and the Graphify map.

No external requests (works offline and on a projector). Charts are inline SVG.
The interactive knowledge graph is Graphify's own graph.html, embedded by
relative path (or copied next to the dashboard with --bundle).
"""
from __future__ import annotations

import html
import json
import shutil
from datetime import datetime
from pathlib import Path

from graphsnip import __version__
from graphsnip import ledger as _ledger
from graphsnip.project import Project
from graphsnip.tokens import method

e = html.escape


def n(x) -> str:
    try:
        return f"{int(x):,}"
    except Exception:
        return "-"


def _bars(items: list[tuple[str, int, str, str]], width: int = 980) -> str:
    """items: (label, value, css_class, note). Horizontal bars, longest = full bar width."""
    mx = max((v for _l, v, _c, _n in items), default=1) or 1
    row_h, gap, left, right = 30, 14, 200, 340
    h = len(items) * (row_h + gap)
    out = [f'<svg viewBox="0 0 {width} {h}" role="img" aria-label="Tokens per approach" class="chart">']
    for i, (lab, v, cls, note) in enumerate(items):
        y = i * (row_h + gap)
        w = max(3, (width - left - right) * v / mx)
        out.append(f'<text x="0" y="{y + 20}" class="lbl">{e(lab)}</text>')
        out.append(f'<rect x="{left}" y="{y}" width="{w:.1f}" height="{row_h}" rx="5" class="{cls}"/>')
        out.append(f'<text x="{left + w + 10:.1f}" y="{y + 20}" class="val"><tspan class="num">{n(v)}</tspan>'
                   f'{(" · " + e(note)) if note else ""}</text>')
    out.append("</svg>")
    return "".join(out)


def _lines(timeline: list[dict], width: int = 720, height: int = 260) -> str:
    if len(timeline) < 2:
        return '<p class="muted">Not enough calls yet to draw a curve. Use the tools (or run <code>graphsnip bench</code>) and re-generate.</p>'
    pad_l, pad_r, pad_t, pad_b = 54, 14, 14, 34
    W, H = width - pad_l - pad_r, height - pad_t - pad_b
    mx = max(t["baseline"] for t in timeline) or 1
    k = len(timeline)

    def X(i):
        return pad_l + W * i / (k - 1)

    def Y(v):
        return pad_t + H * (1 - v / mx)

    base = " ".join(f"{X(i):.1f},{Y(t['baseline']):.1f}" for i, t in enumerate(timeline))
    ret = " ".join(f"{X(i):.1f},{Y(t['returned']):.1f}" for i, t in enumerate(timeline))
    area = base + " " + " ".join(f"{X(i):.1f},{Y(t['returned']):.1f}" for i, t in reversed(list(enumerate(timeline))))
    out = [f'<svg viewBox="0 0 {width} {height}" role="img" aria-label="Cumulative tokens with and without graphsnip" class="chart">']
    for frac in (0, 0.25, 0.5, 0.75, 1):
        y = pad_t + H * (1 - frac)
        out.append(f'<line x1="{pad_l}" x2="{width - pad_r}" y1="{y:.1f}" y2="{y:.1f}" class="grid"/>')
        out.append(f'<text x="{pad_l - 8}" y="{y + 4:.1f}" text-anchor="end" class="axis">{n(mx * frac)}</text>')
    out.append(f'<polygon points="{area}" class="area"/>')
    out.append(f'<polyline points="{base}" class="l-base" fill="none"/>')
    out.append(f'<polyline points="{ret}" class="l-ret" fill="none"/>')
    out.append(f'<text x="{pad_l}" y="{height - 8}" class="axis">call 1</text>')
    out.append(f'<text x="{width - pad_r}" y="{height - 8}" text-anchor="end" class="axis">call {k}</text>')
    out.append("</svg>")
    return "".join(out)


def _kpi(label: str, value: str, sub: str = "", accent: bool = False) -> str:
    return (f'<div class="kpi{" accent" if accent else ""}"><div class="k-l">{e(label)}</div>'
            f'<div class="k-v">{value}</div><div class="k-s">{e(sub)}</div></div>')


CSS = """
:root{--bg:#f6f7f9;--card:#fff;--ink:#121826;--mut:#5b6577;--line:#e3e6ec;--brand:#0f766e;--brand2:#14b8a6;
--grep:#94a3b8;--gfy:#60a5fa;--gfyread:#6366f1;--cg:#0f766e;--bad:#b91c1c;--ok:#15803d;--warn:#b45309}
@media (prefers-color-scheme:dark){:root{--bg:#0b0f17;--card:#121826;--ink:#e8ecf4;--mut:#93a0b6;--line:#243049;--brand:#2dd4bf;--brand2:#5eead4;
--grep:#64748b;--gfy:#60a5fa;--gfyread:#818cf8;--cg:#2dd4bf;--bad:#f87171;--ok:#4ade80;--warn:#fbbf24}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.5 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
.wrap{max-width:1180px;margin:0 auto;padding:28px 18px 60px}
header{display:flex;flex-wrap:wrap;gap:14px;align-items:center;justify-content:space-between;margin-bottom:22px}
.brand{display:flex;align-items:center;gap:12px}.logo{width:38px;height:38px;border-radius:10px;background:linear-gradient(135deg,var(--brand),var(--brand2));display:grid;place-items:center;color:#fff;font-weight:800}
h1{font-size:22px;margin:0}h2{font-size:17px;margin:0 0 4px}.sub{color:var(--mut);font-size:13px}
.chips{display:flex;flex-wrap:wrap;gap:8px}.chip{background:var(--card);border:1px solid var(--line);border-radius:999px;padding:4px 11px;font-size:12.5px;color:var(--mut)}
.chip b{color:var(--ink)}
.badge{display:inline-block;border-radius:6px;padding:2px 8px;font-size:12px;font-weight:600;background:color-mix(in srgb,var(--brand) 14%,transparent);color:var(--brand)}
.badge.warn{background:color-mix(in srgb,var(--warn) 16%,transparent);color:var(--warn)}
.grid4{display:grid;grid-template-columns:repeat(auto-fit,minmax(210px,1fr));gap:14px;margin-bottom:18px}
.kpi{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:16px 18px}
.kpi.accent{background:linear-gradient(135deg,var(--brand),var(--brand2));color:#fff;border:none}.kpi.accent .k-l,.kpi.accent .k-s{color:rgba(255,255,255,.88)}
.k-l{font-size:12.5px;color:var(--mut);text-transform:uppercase;letter-spacing:.04em}.k-v{font-size:34px;font-weight:750;line-height:1.15;margin:4px 0}.k-s{font-size:12.5px;color:var(--mut)}
.card{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:18px 20px;margin-bottom:18px}
.two{display:grid;grid-template-columns:1fr 1fr;gap:18px}@media(max-width:900px){.two{grid-template-columns:1fr}}
.chart{width:100%;height:auto;display:block}.chart .lbl{font-size:14px;fill:var(--ink)}.chart .val{font-size:13px;fill:var(--mut)}.chart .num{fill:var(--ink);font-weight:700}
.chart .axis{font-size:11px;fill:var(--mut)}.chart .grid{stroke:var(--line);stroke-width:1}
.b-grep{fill:var(--grep)}.b-gfy{fill:var(--gfy)}.b-gfyread{fill:var(--gfyread)}.b-cg{fill:var(--cg)}
.area{fill:var(--cg);opacity:.16}.l-base{stroke:var(--grep);stroke-width:2.4}.l-ret{stroke:var(--cg);stroke-width:2.8}
.legend{display:flex;gap:16px;flex-wrap:wrap;font-size:12.5px;color:var(--mut);margin-top:6px}.dot{display:inline-block;width:10px;height:10px;border-radius:3px;margin-right:6px;vertical-align:-1px}
table{width:100%;border-collapse:collapse;font-size:13.5px}th,td{padding:8px 10px;border-bottom:1px solid var(--line);text-align:left}th{color:var(--mut);font-weight:600;font-size:12px;text-transform:uppercase;letter-spacing:.03em}
td.r,th.r{text-align:right;font-variant-numeric:tabular-nums}.ok{color:var(--ok);font-weight:700}.no{color:var(--bad);font-weight:700}
.muted{color:var(--mut)}code{background:color-mix(in srgb,var(--mut) 15%,transparent);padding:1px 6px;border-radius:5px;font-size:12.5px}
iframe{width:100%;height:560px;border:1px solid var(--line);border-radius:10px;background:var(--card)}
.btn{display:inline-block;border:1px solid var(--line);border-radius:8px;padding:6px 12px;font-size:13px;color:var(--ink);text-decoration:none;background:var(--card)}
.btn:hover{border-color:var(--brand)}
details{margin-top:6px}summary{cursor:pointer;color:var(--brand);font-weight:600}
.callout{border-left:4px solid var(--brand);background:color-mix(in srgb,var(--brand) 8%,transparent);padding:10px 14px;border-radius:0 8px 8px 0;font-size:14px}
footer{color:var(--mut);font-size:12.5px;margin-top:26px}
"""


def build_html(project: Project, graph_href: str) -> str:
    cfg = project.config()
    price = float(cfg.get("price_per_mtok", 3.0))
    recs = _ledger.Ledger(project.gs_dir).read()
    live = _ledger.summarize(recs, price, sources={"agent", "cli", "shell"})
    bench_s = _ledger.summarize(recs, price, sources={"bench"})
    bench = None
    bp = project.gs_dir / "bench.json"
    if bp.exists():
        try:
            bench = json.loads(bp.read_text(encoding="utf-8"))
        except Exception:
            bench = None
    stats = project.graph_stats() if project.has_graph() else {}
    top = project.top_symbols(8) if project.has_graph() else []

    # headline: live usage if there is any, else the benchmark
    if live["calls"]:
        head, head_label, head_cls = live, "Live usage (agents + CLI)", ""
    else:
        head, head_label, head_cls = bench_s, "Benchmark run (no live agent usage recorded yet)", " warn"

    kpis = [
        _kpi("Tokens saved", n(head["saved"]), f"{n(head['returned'])} returned vs {n(head['baseline'])} baseline", accent=True),
        _kpi("Reduction", f"{head['pct']}%", f"{head['ratio']}× fewer tokens" if head["ratio"] else ""),
        _kpi("Est. input cost saved", f"${head['usd_saved']:.2f}", f"at ${price:g} per million input tokens"),
        _kpi("Tool calls", n(head["calls"]), f"{head['deduped']} duplicate reads suppressed" if head["deduped"] else "priced individually"),
    ]
    if bench:
        t = bench["totals"]
        kpis.append(_kpi("Answered correctly", f"{t['answered']}/{t['tasks']}", "benchmark tasks with expected symbol + source"))

    chips = []
    if stats:
        chips += [f'<span class="chip"><b>{n(stats["nodes"])}</b> nodes</span>', f'<span class="chip"><b>{n(stats["edges"])}</b> edges</span>',
                  f'<span class="chip"><b>{n(stats["communities"])}</b> communities</span>',
                  f'<span class="chip"><b>{n(stats["symbols"])}</b> code symbols</span>']
        if stats.get("built_at_commit"):
            chips.append(f'<span class="chip">commit <b>{e(str(stats["built_at_commit"])[:8])}</b></span>')

    sections = []

    # --- approach comparison (benchmark)
    if bench:
        t = bench["totals"]
        items = [
            ("grep + read files", t["naive_tokens"], "b-grep", "no graph"),
            ("Graphify + read files", t["graphify_read_tokens"], "b-gfyread", "map, then read whole files"),
            ("Graphify query alone", t["graphify_tokens"], "b-gfy", "map only, no source code"),
            ("graphsnip", t["graphsnip_tokens"], "b-cg", f"{t['answered']}/{t['tasks']} answered with code"),
        ]
        sections.append(
            f'<section class="card"><h2>Same questions, four approaches</h2>'
            f'<div class="sub">{t["tasks"]} tasks on this repo · tokens an agent would put in context · baseline: {e(bench["baseline_definition"])}</div>'
            f'<div style="margin-top:12px">{_bars(items)}</div>'
            f'<div class="callout" style="margin-top:12px"><b>{t["saved_pct"]}% fewer tokens</b> than grep + read, and '
            f'<b>{t["saved_vs_graphify_read_pct"]}% fewer</b> than using Graphify alone and then reading the files it points to. '
            f'Graphify\'s query is cheaper still, but it returns a map without source code, so the agent has to read files afterwards. '
            f'graphsnip returns the exact code in the same step.</div></section>')

    # --- timeline + by tool
    tl_src = head["timeline"]
    by_tool = head["by_tool"]
    rows = "".join(
        f'<tr><td><code>{e(tool)}</code></td><td class="r">{n(v["calls"])}</td><td class="r">{n(v["returned"])}</td>'
        f'<td class="r">{n(v["baseline"])}</td><td class="r">{(100 * (v["baseline"] - v["returned"]) / v["baseline"]) if v["baseline"] else 0:.0f}%</td></tr>'
        for tool, v in sorted(by_tool.items(), key=lambda kv: -(kv[1]["baseline"] - kv[1]["returned"])))
    sections.append(
        f'<div class="two"><section class="card"><h2>Cumulative tokens</h2><div class="sub">{e(head_label)}</div>'
        f'<div style="margin-top:8px">{_lines(tl_src)}</div>'
        f'<div class="legend"><span><i class="dot" style="background:var(--grep)"></i>without graphsnip (baseline)</span>'
        f'<span><i class="dot" style="background:var(--cg)"></i>with graphsnip</span></div></section>'
        f'<section class="card"><h2>By tool</h2><div class="sub">where the savings come from</div>'
        f'<table style="margin-top:8px"><thead><tr><th>Tool</th><th class="r">Calls</th><th class="r">Returned</th><th class="r">Baseline</th><th class="r">Saved</th></tr></thead>'
        f'<tbody>{rows or "<tr><td colspan=5 class=muted>No calls recorded yet.</td></tr>"}</tbody></table>'
        f'<p class="sub" style="margin-top:10px">Hook activity: {e(", ".join(f"{k} ×{v}" for k, v in head["hooks"].items()) or "none yet")}</p></section></div>')

    # --- per task table
    if bench:
        trs = ""
        for r in bench["tasks"]:
            saved = f"{100 * (1 - r['graphsnip_tokens'] / r['naive_tokens']):.0f}%" if r["naive_tokens"] and r["graphsnip_tokens"] else "-"
            trs += (f'<tr><td>{e(r["question"])}</td><td><span class="chip">{e(r["type"])}</span></td><td class="r">{n(r["naive_tokens"])}</td>'
                    f'<td class="r">{n(r["graphify_read_tokens"])}</td><td class="r">{n(r["graphsnip_tokens"])}</td><td class="r">{saved}</td>'
                    f'<td class="{"ok" if r["graphsnip_hit"] else "no"}">{"✓" if r["graphsnip_hit"] else "✗"}</td></tr>')
        sections.append(
            f'<section class="card"><h2>Benchmark tasks</h2><div class="sub">edit <code>.graphsnip/tasks.json</code> to use your own questions · '
            f're-run <code>graphsnip bench</code></div>'
            f'<div style="overflow:auto"><table style="margin-top:8px"><thead><tr><th>Question</th><th>Type</th><th class="r">grep + read</th>'
            f'<th class="r">Graphify + read</th><th class="r">graphsnip</th><th class="r">Saved</th><th>OK</th></tr></thead><tbody>{trs}</tbody></table></div></section>')

    # --- recent calls
    if live["recent"]:
        trs = "".join(
            f'<tr><td><code>{e(r.get("tool", ""))}</code></td><td>{e(str(r.get("query", ""))[:90])}</td><td class="r">{n(r.get("returned"))}</td>'
            f'<td class="r">{n(r.get("baseline"))}</td><td class="r">{(100 * (1 - r.get("returned", 0) / r["baseline"])) if r.get("baseline") else 0:.0f}%</td></tr>'
            for r in live["recent"])
        sections.append(
            f'<section class="card"><h2>Recent live calls</h2><div style="overflow:auto"><table style="margin-top:8px"><thead><tr><th>Tool</th><th>Query</th>'
            f'<th class="r">Returned</th><th class="r">Baseline</th><th class="r">Saved</th></tr></thead><tbody>{trs}</tbody></table></div></section>')

    # --- graph
    hub_rows = "".join(
        f'<tr><td><code>{e(str(t["label"]))}</code></td><td class="muted">{e(str(t["file"]))}:{t["line"]}</td><td class="r">{t["degree"]}</td></tr>' for t in top)
    langs = ", ".join(f"{e(k)} ×{n(v)}" for k, v in (stats.get("languages") or {}).items())
    graph_card = (
        f'<section class="card"><div style="display:flex;justify-content:space-between;gap:10px;flex-wrap:wrap;align-items:center">'
        f'<div><h2>Knowledge graph (Graphify)</h2><div class="sub">every node is a symbol; colours are detected communities; click and drag to explore</div></div>'
        f'<a class="btn" href="{e(graph_href)}" target="_blank" rel="noopener">Open full screen ↗</a></div>'
        f'<div style="margin-top:12px"><iframe src="{e(graph_href)}" title="Graphify knowledge graph" loading="lazy"></iframe></div>'
        f'<p class="sub">If the frame is blank, open it full screen. The graph file is <code>graphify-out/graph.html</code>.</p>'
        f'<div class="two" style="margin-top:8px"><div><h2 style="font-size:15px">Most connected symbols</h2><table><thead><tr><th>Symbol</th><th>Where</th><th class="r">Links</th></tr></thead>'
        f'<tbody>{hub_rows}</tbody></table></div>'
        f'<div><h2 style="font-size:15px">Index</h2><p class="sub">Languages: {langs or "-"}</p>'
        f'<p class="sub">Built by Graphify (tree-sitter AST, no LLM, nothing left this machine). Report: <code>graphify-out/GRAPH_REPORT.md</code></p></div></div></section>')
    sections.append(graph_card)

    sections.append(
        '<section class="card"><details><summary>How these numbers are measured</summary>'
        '<p><b>Baseline.</b> For each call, graphsnip prices what an agent without the graph would realistically do: grep the question\'s terms and read the '
        'top 8 matching files in full (symbol reads are priced against reading the whole file). This is deliberately conservative: it is far less than '
        '"read the whole repo", the number Graphify\'s own benchmark compares against.</p>'
        f'<p><b>Tokens.</b> {e(method())}. Install <code>graphsnip[accurate]</code> for exact cl100k counts; absolute numbers shift a little, ratios barely move.</p>'
        '<p><b>Not counted.</b> Savings are counted once per call. In a real session, tokens that stay in context are re-sent on every later turn, so the real saving compounds; '
        'this dashboard does not claim that. Shell-output savings (<code>graphsnip run</code>) are exact: raw vs compressed.</p>'
        '<p><b>Quality.</b> A benchmark task counts as answered only if the expected symbol appears with its source code in the output.</p>'
        '</details></section>')

    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    return (f'<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
            f'<title>graphsnip dashboard</title><style>{CSS}</style></head><body><div class="wrap">'
            f'<header><div class="brand"><div class="logo">cg</div><div><h1>graphsnip <span class="badge{head_cls}">{e(head_label)}</span></h1>'
            f'<div class="sub">{e(project.root.name)} · generated {now} · v{__version__}</div></div></div><div class="chips">{"".join(chips)}</div></header>'
            f'<div class="grid4">{"".join(kpis)}</div>{"".join(sections)}'
            f'<footer>graphsnip is a companion layer on top of Graphify. Graph, communities and AST extraction are Graphify\'s; graphsnip adds symbol-level retrieval, '
            f'context packs, hooks, MCP, and this honest accounting.</footer></div></body></html>')


_VIS_VERSION = "9.1.6"
_VIS_URLS = [f"https://unpkg.com/vis-network@{_VIS_VERSION}/standalone/umd/vis-network.min.js"]
_VIS_TGZ = f"https://registry.npmjs.org/vis-network/-/vis-network-{_VIS_VERSION}.tgz"


def ensure_vis(project: Project) -> Path | None:
    """Cache vis-network locally so graph.html works offline (Graphify's graph.html loads it from a CDN)."""
    import io
    import tarfile
    import urllib.request

    dest = project.gs_dir / "vendor" / "vis-network.min.js"
    if dest.exists() and dest.stat().st_size > 100_000:
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    for url in _VIS_URLS:
        try:
            data = urllib.request.urlopen(url, timeout=20).read()
            if len(data) > 100_000:
                dest.write_bytes(data)
                return dest
        except Exception:
            continue
    try:  # npm registry tarball (same file, different host)
        raw = urllib.request.urlopen(_VIS_TGZ, timeout=40).read()
        with tarfile.open(fileobj=io.BytesIO(raw), mode="r:gz") as tf:
            m = tf.extractfile("package/standalone/umd/vis-network.min.js")
            if m:
                dest.write_bytes(m.read())
                return dest
    except Exception:
        pass
    return None


def _offline_graph(project: Project, out_dir: Path) -> bool:
    """Write graph.html next to the dashboard with vis-network loaded locally. False if it had to keep the CDN."""
    import re

    if not project.graph_html.exists():
        return False
    src = project.graph_html.read_text(encoding="utf-8", errors="replace")
    vis = ensure_vis(project)
    if vis is None:
        (out_dir / "graph.html").write_text(src, encoding="utf-8")
        return False
    shutil.copy2(vis, out_dir / "vis-network.min.js")
    src = re.sub(r'<script src="https://unpkg\.com/vis-network[^>]*>\s*</script>',
                 '<script src="vis-network.min.js"></script>', src, count=1, flags=re.S)
    (out_dir / "graph.html").write_text(src, encoding="utf-8")
    return True


def write_dashboard(project: Project, bundle: str | None = None) -> Path:
    project.gs_dir.mkdir(parents=True, exist_ok=True)
    out_dir = Path(bundle).resolve() if bundle else project.gs_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    offline = _offline_graph(project, out_dir)
    if project.graph_html.exists() and not offline:
        import sys

        sys.stderr.write("[graphsnip] note: could not cache vis-network; graph.html will need internet to render.\n")
    out = out_dir / "dashboard.html"
    out.write_text(build_html(project, "graph.html"), encoding="utf-8")
    return out
