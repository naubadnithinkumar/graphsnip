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


def _fmt_tok(v: float) -> str:
    return f"{v / 1e6:g}M" if v >= 1e6 else f"{v / 1e3:g}k" if v >= 1e3 else f"{v:g}"


def _percall(series: list[dict], width: int = 760, height: int = 330) -> str:
    """Paired bars per call on a log axis, so a 2% result is as visible as a 100% one, with the multiplier on top."""
    import math

    pts = [x for x in series if x["baseline"] > 0 and x["returned"] > 0]
    if not pts:
        return '<p class="muted">No calls recorded yet. Use the tools (or run <code>graphsnip bench</code>) and re-generate.</p>'
    pl, pr, pt, pb = 52, 10, 22, 40
    W, H = width - pl - pr, height - pt - pb
    vals = [x["returned"] for x in pts] + [x["baseline"] for x in pts]
    lo, hi = 10 ** math.floor(math.log10(max(1, min(vals)))), 10 ** math.ceil(math.log10(max(vals)))
    if lo == hi:
        hi = lo * 10

    def Y(v):
        return pt + H * (1 - (math.log10(v) - math.log10(lo)) / (math.log10(hi) - math.log10(lo)))

    k = len(pts)
    slot = W / k
    bw = max(3.0, min(26.0, slot * 0.38))
    out = [f'<svg viewBox="0 0 {width} {height}" role="img" aria-label="Tokens per call with and without graphsnip, log scale" class="chart">']
    e10 = int(math.log10(lo))
    while 10 ** e10 <= hi:
        y = Y(10 ** e10)
        out.append(f'<line x1="{pl}" x2="{width - pr}" y1="{y:.1f}" y2="{y:.1f}" class="grid"/>'
                   f'<text x="{pl - 7}" y="{y + 4:.1f}" text-anchor="end" class="axis">{_fmt_tok(10 ** e10)}</text>')
        e10 += 1
    for i, x in enumerate(pts):
        cx = pl + slot * (i + 0.5)
        yb, yr = Y(x["baseline"]), Y(x["returned"])
        mult = x["baseline"] / x["returned"]
        tip = f'{x["tool"]}: {x["query"]} | baseline {x["baseline"]:,} vs graphsnip {x["returned"]:,} tokens ({mult:.0f}x fewer)'
        out.append(f'<g><title>{e(tip)}</title><rect x="{cx - bw - 1:.1f}" y="{yb:.1f}" width="{bw:.1f}" height="{pt + H - yb:.1f}" class="b-grep" rx="2"/>'
                   f'<rect x="{cx + 1:.1f}" y="{yr:.1f}" width="{bw:.1f}" height="{pt + H - yr:.1f}" class="b-cg" rx="2"/>')
        if slot >= 34:
            out.append(f'<text x="{cx:.1f}" y="{yb - 5:.1f}" text-anchor="middle" class="num" style="font-size:12px">{mult:.0f}×</text>')
        out.append("</g>")
        if k <= 12 or i % max(1, k // 10) == 0:
            out.append(f'<text x="{cx:.1f}" y="{height - 22}" text-anchor="middle" class="axis">{e(x["tool"].replace("gs_", ""))}</text>'
                       f'<text x="{cx:.1f}" y="{height - 9}" text-anchor="middle" class="axis">#{i + 1}</text>')
    out.append("</svg>")
    return "".join(out)


def _saved_line(timeline: list[dict], width: int = 760, height: int = 300) -> str:
    """Cumulative tokens SAVED: one line that visibly climbs, instead of two lines where one hugs zero."""
    if len(timeline) < 2:
        return '<p class="muted">Not enough calls yet to draw a curve.</p>'
    pl, pr, pt, pb = 58, 70, 18, 34
    W, H = width - pl - pr, height - pt - pb
    sv = [t["baseline"] - t["returned"] for t in timeline]
    mx = max(sv) or 1
    k = len(sv)
    X = lambda i: pl + W * i / (k - 1)  # noqa: E731
    Y = lambda v: pt + H * (1 - v / mx)  # noqa: E731
    line = " ".join(f"{X(i):.1f},{Y(v):.1f}" for i, v in enumerate(sv))
    out = [f'<svg viewBox="0 0 {width} {height}" role="img" aria-label="Cumulative tokens saved" class="chart">']
    for frac in (0, 0.25, 0.5, 0.75, 1):
        y = pt + H * (1 - frac)
        out.append(f'<line x1="{pl}" x2="{width - pr}" y1="{y:.1f}" y2="{y:.1f}" class="grid"/><text x="{pl - 7}" y="{y + 4:.1f}" text-anchor="end" class="axis">{n(mx * frac)}</text>')
    out.append(f'<polygon points="{pl},{pt + H} {line} {X(k - 1):.1f},{pt + H}" class="area" style="opacity:.28"/><polyline points="{line}" class="l-ret" fill="none"/>')
    out.append(f'<circle cx="{X(k - 1):.1f}" cy="{Y(sv[-1]):.1f}" r="4.5" fill="var(--cg)"/><text x="{X(k - 1) + 8:.1f}" y="{Y(sv[-1]) + 4:.1f}" class="num" style="font-size:13px">{n(sv[-1])}</text>')
    out.append(f'<text x="{pl}" y="{height - 8}" class="axis">call 1</text><text x="{X(k - 1):.1f}" y="{height - 8}" text-anchor="end" class="axis">call {k}</text></svg>')
    return "".join(out)


def _tradeoff(bench: dict, width: int = 760, height: int = 320) -> str:
    """Efficiency vs accuracy: tokens (log, left = cheaper) against tasks answered. Up and to the left wins."""
    import math

    t = bench["totals"]
    nt = max(1, t["tasks"])
    pts = [("graphsnip", t["graphsnip_tokens"], t["answered"] / nt, "var(--cg)", "answered with exact code"),
           ("grep + read", t["naive_tokens"], t["naive_answered"] / nt, "var(--grep)", "whole files"),
           ("Graphify + read", t["graphify_read_tokens"], t["graphify_answered"] / nt, "var(--gfyread)", "map, then whole files"),
           ("Graphify alone", t["graphify_tokens"], t["graphify_answered"] / nt, "var(--gfy)", "map only, no source code")]
    pts = [x for x in pts if x[1] > 0]
    if not pts:
        return '<p class="muted">Run <code>graphsnip bench</code> to see this view.</p>'
    pl, pr, pt, pb = 52, 24, 26, 44
    W, H = width - pl - pr, height - pt - pb
    ymin = max(0.0, math.floor(min(x[2] for x in pts) * 10 - 2) / 10)  # zoom the accuracy axis; labelled on the chart
    lo = 10 ** math.floor(math.log10(min(x[1] for x in pts)))
    hi = 10 ** math.ceil(math.log10(max(x[1] for x in pts)))
    X = lambda v: pl + W * (math.log10(v) - math.log10(lo)) / max(1e-9, math.log10(hi) - math.log10(lo))  # noqa: E731
    Y = lambda a: pt + H * (1 - (a - ymin) / (1 - ymin))  # noqa: E731
    out = [f'<svg viewBox="0 0 {width} {height}" role="img" aria-label="Tokens versus tasks answered" class="chart">']
    for a in [ymin + (1 - ymin) * f for f in (0, 0.25, 0.5, 0.75, 1)]:
        out.append(f'<line x1="{pl}" x2="{width - pr}" y1="{Y(a):.1f}" y2="{Y(a):.1f}" class="grid"/><text x="{pl - 7}" y="{Y(a) + 4:.1f}" text-anchor="end" class="axis">{a * 100:.0f}%</text>')
    if ymin > 0:
        out.append(f'<text x="{pl + 8}" y="{pt + H - 8}" class="axis">y axis: tasks answered, zoomed (starts at {ymin * 100:.0f}%, not 0)</text>')
    e10 = int(math.log10(lo))
    while 10 ** e10 <= hi:
        out.append(f'<text x="{X(10 ** e10):.1f}" y="{height - 24}" text-anchor="middle" class="axis">{_fmt_tok(10 ** e10)}</text>')
        e10 += 1
    out.append(f'<text x="{pl + W / 2:.0f}" y="{height - 6}" text-anchor="middle" class="axis">tokens put in context for the benchmark tasks (log scale, fewer is better →)</text>')
    pts.sort(key=lambda x: x[1])
    for i, (name, tok, acc, col, note) in enumerate(pts):
        x, y = X(tok), Y(acc)
        lvl = i % 2                       # stagger labels on two rows below the points so ties never overlap
        ly = y + 34 + lvl * 40
        anchor = "end" if x > pl + W * 0.72 else "start"
        dx = -6 if anchor == "end" else 6
        out.append(f'<g><title>{e(name)}: {tok:,} tokens, {acc * 100:.0f}% answered ({note})</title>'
                   f'<line x1="{x:.1f}" x2="{x:.1f}" y1="{y + 8:.1f}" y2="{ly - 14:.1f}" stroke="{col}" stroke-width="1.5" stroke-dasharray="3 3"/>'
                   f'<circle cx="{x:.1f}" cy="{y:.1f}" r="8" fill="{col}"/>'
                   f'<text x="{x + dx:.1f}" y="{ly:.1f}" text-anchor="{anchor}" class="lbl" style="font-size:13px;font-weight:600">{e(name)}</text>'
                   f'<text x="{x + dx:.1f}" y="{ly + 15:.1f}" text-anchor="{anchor}" class="axis">{tok:,} tokens · {acc * 100:.0f}% answered</text></g>')
    out.append("</svg>")
    return "".join(out)


def _chart_card(head: dict, head_label: str, bench: dict | None) -> str:
    ser = head.get("series") or []
    ratios = sorted(x["baseline"] / x["returned"] for x in ser if x["baseline"] > 0 and x["returned"] > 0)
    med = ratios[len(ratios) // 2] if ratios else 0
    tabs = [("pc", "Per call", _percall(ser),
             f'Each pair is one tool call: grey = what grep + read would have cost, teal = what graphsnip returned (log scale). '
             + (f'<b>Typical call: {med:.0f}× fewer tokens.</b>' if med else "")),
            ("sv", "Tokens saved", _saved_line(head["timeline"]), "Running total of tokens kept out of the context window.")]
    if bench:
        t = bench["totals"]
        tabs.append(("ea", "Efficiency vs accuracy", _tradeoff(bench),
                     f'Same {t["tasks"]} questions, four approaches. <b>graphsnip answered {t["answered"]}/{t["tasks"]} with exact source code using '
                     f'{100 - t["saved_pct"]:.0f}% of the tokens</b> of grep + read ({t["naive_answered"]}/{t["tasks"]}). "Answered" = expected symbol present; for graphsnip its source code must be too. '
                     f'Graphify alone finds the symbol but returns no code.'))
    tabs.append(("cu", "Cumulative", _lines(head["timeline"]), "Both running totals on one linear axis; the graphsnip line hugs zero because it is so small."))
    btns = "".join(f'<button type="button" class="tab{" on" if i == 0 else ""}" data-t="{k}">{e(lbl)}</button>' for i, (k, lbl, _, _) in enumerate(tabs))
    panels = "".join(f'<div class="tp" data-p="{k}"{"" if i == 0 else " hidden"}>{svg}<p class="sub" style="margin-top:6px">{cap}</p></div>'
                     for i, (k, _, svg, cap) in enumerate(tabs))
    legend = ('<div class="legend"><span><i class="dot" style="background:var(--grep)"></i>without graphsnip (baseline)</span>'
              '<span><i class="dot" style="background:var(--cg)"></i>with graphsnip</span></div>')
    return (f'<section class="card"><h2>Efficiency and accuracy</h2><div class="sub">{e(head_label)}</div>'
            f'<div class="tabs">{btns}</div>{panels}{legend}</section>')


def _kpi(label: str, value: str, sub: str = "", accent: bool = False) -> str:
    return (f'<div class="kpi{" accent" if accent else ""}"><div class="k-l">{e(label)}</div>'
            f'<div class="k-v">{value}</div><div class="k-s">{e(sub)}</div></div>')


TAB_JS = '''<script>document.querySelectorAll(".card").forEach(function(c){var b=c.querySelectorAll(".tab");b.forEach(function(x){x.onclick=function(){b.forEach(function(y){y.classList.toggle("on",y===x)});c.querySelectorAll(".tp").forEach(function(p){p.hidden=p.dataset.p!==x.dataset.t})}})})</script>'''


def _fold(fid: str, title: str, summary: str, body: str, open_: bool = False) -> str:
    return (f'<section class="card fold" id="{fid}"><details{" open" if open_ else ""}><summary><span class="ft">{e(title)}</span>'
            f'<span class="fs">{e(summary)}</span></summary><div class="fb">{body}</div></details></section>')


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
details>summary{list-style:none}details>summary::-webkit-details-marker{display:none}
.fold{padding:0}.fold>details{margin:0}.fold>details>summary{display:flex;justify-content:space-between;align-items:center;gap:12px;flex-wrap:wrap;padding:14px 20px;color:var(--ink)}
.fold>details>summary::after{content:"Show ▾";color:var(--brand);font-size:13px;font-weight:600;margin-left:auto}.fold>details[open]>summary::after{content:"Hide ▴"}
.ft{font-weight:700;font-size:16px}.fs{color:var(--mut);font-size:13px;font-weight:400}.fb{padding:0 20px 18px}
.nav{position:sticky;top:0;z-index:5;display:flex;gap:6px;flex-wrap:wrap;padding:8px 0;margin:-8px 0 14px;background:color-mix(in srgb,var(--bg) 92%,transparent);backdrop-filter:blur(6px)}
.nav a{border:1px solid var(--line);background:var(--card);border-radius:999px;padding:4px 12px;font-size:13px;color:var(--mut);text-decoration:none}.nav a:hover{color:var(--brand);border-color:var(--brand)}
html{scroll-behavior:smooth}.flow{display:flex;flex-wrap:wrap;align-items:center;gap:8px;margin:10px 0}.flow .st{border:1px solid var(--line);border-radius:10px;padding:6px 10px;font-size:13px;background:var(--card)}
.flow .st.g{border-color:var(--brand);background:color-mix(in srgb,var(--brand) 9%,transparent)}.flow .st.w{border-color:var(--warn);background:color-mix(in srgb,var(--warn) 10%,transparent)}.flow .ar{color:var(--mut)}
.tabs{display:flex;gap:6px;flex-wrap:wrap;margin:10px 0 8px}.tab{border:1px solid var(--line);background:var(--card);color:var(--mut);border-radius:8px;padding:5px 12px;font:inherit;font-size:13px;cursor:pointer}
.tab.on{background:var(--brand);border-color:var(--brand);color:#fff}.tp[hidden]{display:none}@media(max-width:900px){.two{grid-template-columns:1fr!important}}
footer{color:var(--mut);font-size:12.5px;margin-top:26px}
"""


def _usd(v: float) -> str:
    return f"-${abs(v):.4f}" if v < 0 else f"${v:.4f}"


def _load_ab(project: Project) -> dict | None:
    p = project.gs_dir / "ab.json"
    try:
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None
    except Exception:
        return None


def _signed(v: float, digits: int = 4) -> str:
    return f"-${abs(v):.{digits}f}" if v < 0 else f"${v:.{digits}f}"


def _ab_card(project: Project) -> str:
    ab = _load_ab(project)
    if not ab:
        return ('<section class="card" id="ab"><h2>Measured A/B</h2><p class="sub">Not run yet. <code>graphsnip ab --yes</code> answers your benchmark questions in '
                'headless Claude Code with and without graphsnip and records the <b>real</b> tokens and cost the CLI reports. It spends real usage '
                '(capped; default $5), so it is never run automatically.</p></section>')
    t = ab["totals"]
    kp = "".join([
        _kpi("Measured cost saved" if t["cost_saved"] >= 0 else "Measured cost INCREASE", _signed(abs(t["cost_saved"])),
             f"${t['control_cost']:.4f} → ${t['treat_cost']:.4f} ({abs(t['cost_saved_pct'])}% {'less' if t['cost_saved'] >= 0 else 'more'})", accent=t["cost_saved"] >= 0),
        _kpi("Context tokens saved" if t["context_saved"] >= 0 else "Context tokens increase", n(abs(t["context_saved"])),
             f"{n(t['control_context'])} → {n(t['treat_context'])} ({abs(t['context_saved_pct'])}%)"),
        _kpi("Expected symbols found", f"{t['treat_hits']}/{t['hit_known']}", f"without graphsnip {t['control_hits']}/{t['hit_known']}"),
        _kpi("Runs", n(ab["paired_tasks"] * ab["repeats"] * 2), f"{ab['repeats']} per task per arm · model {ab['model']}"),
    ])
    rows = ""
    for r in ab["tasks"]:
        c, g = r["control"], r["treat"]
        if not (c["runs"] and g["runs"]):
            continue
        sv = f"{100 * (c['cost'] - g['cost']) / c['cost']:.0f}%" if c["cost"] else "-"
        tv = lambda a, b: f"{a:g} → {b:g}" if (a or b) else "-"  # noqa: E731
        rows += (f"<tr><td>{e(r['question'][:60])}</td><td class='r'>${c['cost']:.4f}</td><td class='r'>${g['cost']:.4f}</td><td class='r'>{sv}</td>"
                 f"<td class='r'>{n(c['context'])} → {n(g['context'])}</td><td class='r'>{tv(c.get('turns', 0), g.get('turns', 0))}</td>"
                 f"<td class='r'>{n(c.get('cache_creation', 0))} → {n(g.get('cache_creation', 0))}</td><td class='r'>{n(c['output'])} → {n(g['output'])}</td>"
                 f"<td class='r'>{c['hits']}/{c['hit_known']} · {g['hits']}/{g['hit_known']}</td></tr>")
    note = (f"<p class='sub'>Real usage and cost reported by <code>claude -p --output-format json</code>, including caching and every turn. Medians over repeats. "
            f"Both arms read-only, same model and prompt, fresh session each run. “Found” only checks the expected symbols appear; the answers are saved in "
            f"<code>{e(str(ab['answers_dir']))}</code> so you can judge quality yourself. Run {e(ab['ts'])}"
            + (f"; <b>{e(ab['aborted'])}</b>" if ab.get("aborted") else "") + (f"; {ab['failed_runs']} failed run(s) excluded" if ab.get("failed_runs") else "") + ".</p>")
    return (f'<section class="card fold" id="ab"><details open><summary><span class="ft">Measured A/B (real runs)</span>'
            f'<span class="fs">cost ${t["control_cost"]:.2f} → ${t["treat_cost"]:.2f} · {abs(t["cost_saved_pct"])}% {"saved" if t["cost_saved"] >= 0 else "more"}</span></summary><div class="fb">'
            f'<div class="grid4" style="margin-top:6px">{kp}</div>'
            f'<div style="overflow:auto"><table><thead><tr><th>Question</th><th class="r">Without $</th><th class="r">With $</th><th class="r">Saved</th>'
            f'<th class="r">Context tokens</th><th class="r">Turns</th><th class="r">Cache-write tokens</th><th class="r">Output tokens</th><th class="r">Found</th></tr></thead><tbody>{rows}</tbody></table></div>'
            f'<p class="sub">Arrows read without → with graphsnip. If cost is up while context is flat, look at turns, cache-write (billed at a premium for each fresh session) and output.</p>{note}</div></details></section>')


def _routing_card(project: Project, recs: list[dict], price: float) -> str:
    from graphsnip import router, usage

    cfg = router.config(project)
    rs = router.summarize(recs, cfg)
    firsts = [r.get("ts") for r in recs if r.get("source") in ("agent", "cli") and r.get("ts")]
    u = usage.scan(project.root, cfg, since=min(firsts) if firsts else None)
    mod = lambda c, t: (cfg["models"].get(c) or {}).get(t) or "-"  # noqa: E731

    # ---- explainer (collapsed)
    flow = ('<div class="flow"><span class="st">Task typed in /graphsnip</span><span class="ar">→</span><span class="st">gs_route scores it<br>(wording + graph size)</span>'
            '<span class="ar">→</span><span class="st g">light enough: the cheap model (e.g. Haiku) answers</span></div>'
            '<div class="flow"><span class="st">gs_route says it is too hard</span><span class="ar">→</span>'
            '<span class="st w">prints "Escalating to &lt;model&gt;…"</span><span class="ar">→</span>'
            '<span class="st w">a stronger-model subagent (Sonnet / Opus) does the work</span><span class="ar">→</span><span class="st g">same answer, no re-typing</span></div>')
    status_rows = "".join(f"<tr><td><code>{k}</code></td><td>{v}</td></tr>" for k, v in [
        ("routed", "the running tier matched the decision; savings are credited"),
        ("escalate", "the pinned light model was too weak, so the task was handed to a stronger one (or stopped, on clients that cannot hand off)"),
        ("session", "ran on the session's own model on purpose (/graphsnip-deep); counted as a missed downgrade if a cheaper tier would do"),
        ("overpowered", "ran on a stronger tier than needed"),
        ("advisory", "decision only; nothing verified which model ran, so no savings are claimed")])
    client_rows = "".join(f"<tr><td>{c}</td><td>{h}</td><td>{v}</td></tr>" for c, h, v in [
        ("Claude Code", "<code>/graphsnip</code> pinned to the light model; hard tasks hand off to a Sonnet/Opus subagent automatically", "transcripts (<code>graphsnip usage</code>)"),
        ("Copilot (VS Code)", "<code>/graphsnip</code> pinned only if <code>routing.models.copilot.light</code> is set; hard tasks need <code>/graphsnip-deep</code>", "not available"),
        ("Codex", "<code>graphsnip codex \"task\"</code> picks the tier and runs <code>codex exec -m &lt;model&gt;</code>; the prompt file alone is advisory", "launcher knows the model it passed")])
    tier_rows = "".join(f"<tr><td>{t}</td><td>{e(mod('claude', t))}</td><td>{e(mod('codex', t))}</td><td class='r'>${cfg['prices'][t][0]:g} / ${cfg['prices'][t][1]:g}</td></tr>" for t in router.TIERS)
    explain = _fold_inner("How routing works, statuses and client support",
        flow + '<h2 style="font-size:15px;margin-top:12px">Statuses</h2><table><tbody>' + status_rows + '</tbody></table>'
        '<h2 style="font-size:15px;margin-top:14px">Per client</h2><table><thead><tr><th>Client</th><th>How the model is switched</th><th>Verified by</th></tr></thead><tbody>' + client_rows + '</tbody></table>'
        '<h2 style="font-size:15px;margin-top:14px">Tiers, models and prices used for estimates</h2><table><thead><tr><th>Tier</th><th>Claude model</th><th>Codex model</th><th class="r">$ per M tokens (in / out)</th></tr></thead><tbody>'
        + tier_rows + '</tbody></table><p class="sub">Prices are editable placeholders (<code>routing.prices</code> in <code>.graphsnip/config.json</code>). '
        'Routing saves dollars per token, not tokens; the token savings above come from the graph alone.</p>')

    if not rs["decisions"]:
        return ('<section class="card" id="routing"><h2>Model routing</h2><p class="sub">No routing decisions yet. Use <code>/graphsnip</code> in your agent '
                '(or <code>graphsnip route "your task"</code>) and this section fills in.</p>' + explain + '</section>')

    kp = "".join([
        _kpi("Routing decisions", n(rs["decisions"]), " · ".join(f"{k} {v}" for k, v in rs["by_tier"].items() if v) or "-"),
        _kpi("Ran on a cheaper model", n(rs["routed_cheaper"]), f"{n(rs['tokens_on_cheaper'])} tokens handled by the light tier"),
        _kpi("Est. dollars saved by routing", f"${rs['usd_saved']:.4f}", f"vs running everything on the {cfg['baseline_tier']} tier", accent=True),
        _kpi("Escalations", n(rs["escalations"]), f"{rs['handoffs']} handed to a stronger model automatically"),
    ])
    ev = router.events(recs, cfg, 8)
    erows = "".join(
        f"<tr><td class='muted'>{e(x['ts'][5:16].replace('T', ' '))}</td><td>{e(x['client'])}</td><td>{e(str(x['query'])[:55])}</td>"
        f"<td><span class='chip'>{e(x['tier'])}</span></td><td>{e(x['path'])}</td><td class='r'>{_usd(x['saved'])}</td></tr>" for x in ev)
    events = ('<h2 style="font-size:15px;margin-top:4px">What happened, task by task</h2><div style="overflow:auto"><table><thead><tr><th>Time</th><th>Client</th><th>Task</th>'
              f'<th>Router said</th><th>What actually happened</th><th class="r">Est. saved</th></tr></thead><tbody>{erows}</tbody></table></div>'
              f'<p class="sub">Not yet counted as saved: ${rs["usd_potential"]:.4f} from decisions nobody verified or that ran on the session model.</p>')
    meas = ""
    if u:
        mrows = "".join(f"<tr><td>{e('other (synthetic/unknown)' if k == 'other' else k)}</td><td class='r'>{n(v['messages'])}</td><td class='r'>{n(v['input'])}</td>"
                        f"<td class='r'>{n(v['output'])}</td><td class='r'>{n(v['cache_read'])}</td><td class='r'>${v['usd']:.2f}</td></tr>"
                        for k, v in sorted(u["families"].items(), key=lambda kv: -kv[1]["usd"]))
        span = f"since {e(str(u['since'])[:10])} (first graphsnip call)" if u.get("since") else "all history in this project"
        meas = _fold_inner("Measured: which model actually answered (Claude Code transcripts)",
            f'<p class="sub">{span}. Cost = list-price equivalent for these tokens, <b>not your bill</b> (plans and caching change what you pay). '
            f'Cache-read tokens are billed at 10% of the input price in this estimate.</p>'
            f'<table><thead><tr><th>Model</th><th class="r">Replies</th><th class="r">Input</th><th class="r">Output</th><th class="r">Cache read</th>'
            f'<th class="r">≈ List-price cost</th></tr></thead><tbody>{mrows}</tbody></table>')
    return (f'<section class="card" id="routing"><h2>Model routing</h2><div class="sub">graphsnip scores each task and steers it to the cheapest model tier that can do it. '
            f'Dollars are estimates; savings are counted only when the running model matched the decision.</div>'
            f'<div class="grid4" style="margin-top:12px">{kp}</div>{events}{explain}{meas}</section>')


def _fold_inner(title: str, body: str) -> str:
    return f'<details style="margin-top:12px"><summary>{e(title)}</summary><div style="margin-top:8px">{body}</div></details>'


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
        _kpi("Est. input cost saved", f"${head['usd_saved']:.2f}", f"list-price estimate at ${price:g}/M, one-off, input only"),
        _kpi("Tool calls", n(head["calls"]), f"{head['deduped']} duplicate reads suppressed" if head["deduped"] else "priced individually"),
    ]
    ab = _load_ab(project)
    if ab:
        at = ab["totals"]
        pos = at["cost_saved"] >= 0
        kpis.append(_kpi("Measured cost saved (A/B)" if pos else "Measured cost INCREASE (A/B)", _signed(abs(at["cost_saved"]), 2),
                         f"{abs(at['cost_saved_pct'])}% {'less' if pos else 'more'} · real runs, {ab['paired_tasks']} tasks × {ab['repeats']}", accent=pos))
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
    bench_parts: list[str] = []

    # --- approach comparison (benchmark)
    if bench:
        t = bench["totals"]
        items = [
            ("grep + read files", t["naive_tokens"], "b-grep", "no graph"),
            ("Graphify + read files", t["graphify_read_tokens"], "b-gfyread", "map, then read whole files"),
            ("Graphify query alone", t["graphify_tokens"], "b-gfy", "map only, no source code"),
            ("graphsnip", t["graphsnip_tokens"], "b-cg", f"{t['answered']}/{t['tasks']} answered with code"),
        ]
        bench_parts.append(
            f'<h2>Same questions, four approaches</h2>'
            f'<div class="sub">{t["tasks"]} tasks on this repo · tokens an agent would put in context · baseline: {e(bench["baseline_definition"])}</div>'
            f'<div style="margin-top:12px">{_bars(items)}</div>'
            f'<div class="callout" style="margin-top:12px"><b>{t["saved_pct"]}% fewer tokens</b> than grep + read, and '
            f'<b>{t["saved_vs_graphify_read_pct"]}% fewer</b> than using Graphify alone and then reading the files it points to. '
            f'Graphify\'s query is cheaper still, but it returns a map without source code, so the agent has to read files afterwards. '
            f'graphsnip returns the exact code in the same step.</div>')

    # --- timeline + by tool
    tl_src = head["timeline"]
    by_tool = head["by_tool"]
    rows = "".join(
        f'<tr><td><code>{e(tool)}</code></td><td class="r">{n(v["calls"])}</td><td class="r">{n(v["returned"])}</td>'
        f'<td class="r">{n(v["baseline"])}</td><td class="r">{(100 * (v["baseline"] - v["returned"]) / v["baseline"]) if v["baseline"] else 0:.0f}%</td></tr>'
        for tool, v in sorted(by_tool.items(), key=lambda kv: -(kv[1]["baseline"] - kv[1]["returned"])))
    sections.append(
        f'<div class="two" style="grid-template-columns:1.5fr 1fr">{_chart_card(head, head_label, bench)}'
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
        bench_parts.append(
            f'<h2 style="margin-top:18px">Benchmark tasks</h2><div class="sub">edit <code>.graphsnip/tasks.json</code> to use your own questions · '
            f're-run <code>graphsnip bench</code></div>'
            f'<div style="overflow:auto"><table style="margin-top:8px"><thead><tr><th>Question</th><th>Type</th><th class="r">grep + read</th>'
            f'<th class="r">Graphify + read</th><th class="r">graphsnip</th><th class="r">Saved</th><th>OK</th></tr></thead><tbody>{trs}</tbody></table></div>')

    # --- recent calls
    if live["recent"]:
        trs = "".join(
            f'<tr><td><code>{e(r.get("tool", ""))}</code></td><td>{e(str(r.get("query", ""))[:90])}</td><td class="r">{n(r.get("returned"))}</td>'
            f'<td class="r">{n(r.get("baseline"))}</td><td class="r">{(100 * (1 - r.get("returned", 0) / r["baseline"])) if r.get("baseline") else 0:.0f}%</td></tr>'
            for r in live["recent"])
        sections.append(_fold("recent", "Recent live calls", f"last {len(live['recent'])} tool calls",
            f'<div style="overflow:auto"><table style="margin-top:8px"><thead><tr><th>Tool</th><th>Query</th>'
            f'<th class="r">Returned</th><th class="r">Baseline</th><th class="r">Saved</th></tr></thead><tbody>{trs}</tbody></table></div>'))

    # --- graph
    hub_rows = "".join(
        f'<tr><td><code>{e(str(t["label"]))}</code></td><td class="muted">{e(str(t["file"]))}:{t["line"]}</td><td class="r">{t["degree"]}</td></tr>' for t in top)
    langs = ", ".join(f"{e(k)} ×{n(v)}" for k, v in (stats.get("languages") or {}).items())
    graph_card = (
        f'<section class="card" id="graph"><div style="display:flex;justify-content:space-between;gap:10px;flex-wrap:wrap;align-items:center">'
        f'<div><h2>Knowledge graph (Graphify)</h2><div class="sub">every node is a symbol; colours are detected communities; click and drag to explore</div></div>'
        f'<a class="btn" href="{e(graph_href)}" target="_blank" rel="noopener">Open full screen ↗</a></div>'
        f'<div style="margin-top:12px"><iframe src="{e(graph_href)}" title="Graphify knowledge graph" loading="lazy"></iframe></div>'
        f'<p class="sub">If the frame is blank, open it full screen. The graph file is <code>graphify-out/graph.html</code>.</p>'
        f'<div class="two" style="margin-top:8px"><div><h2 style="font-size:15px">Most connected symbols</h2><table><thead><tr><th>Symbol</th><th>Where</th><th class="r">Links</th></tr></thead>'
        f'<tbody>{hub_rows}</tbody></table></div>'
        f'<div><h2 style="font-size:15px">Index</h2><p class="sub">Languages: {langs or "-"}</p>'
        f'<p class="sub">Built by Graphify (tree-sitter AST, no LLM, nothing left this machine). Report: <code>graphify-out/GRAPH_REPORT.md</code></p></div></div></section>')
    if bench_parts:
        bt = bench["totals"]
        sections.append(_fold("benchmark", "Benchmark (offline, same questions on this repo)",
                              f"{bt['tasks']} tasks · {bt['saved_pct']}% fewer tokens than grep + read · {bt['answered']}/{bt['tasks']} answered with code",
                              "".join(bench_parts)))
    sections.insert(1, _routing_card(project, recs, price))  # right after the tokens/by-tool row
    sections.insert(2, _ab_card(project))
    sections.append(graph_card)

    sections.append(
        '<section class="card fold" id="method"><details><summary>How these numbers are measured</summary>'
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
            f'<nav class="nav"><a href="#top">Savings</a><a href="#routing">Model routing</a><a href="#ab">Measured A/B</a><a href="#recent">Recent calls</a><a href="#benchmark">Benchmark</a><a href="#graph">Graph</a><a href="#method">Method</a></nav>'
            f'<div class="grid4" id="top">{"".join(kpis)}</div>{"".join(sections)}'
            f'<footer>graphsnip is a companion layer on top of Graphify. Graph, communities and AST extraction are Graphify\'s; graphsnip adds symbol-level retrieval, '
            f'context packs, hooks, MCP, and this honest accounting.</footer></div>{TAB_JS}</body></html>')


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
