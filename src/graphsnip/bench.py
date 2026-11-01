"""Offline, reproducible benchmark.

For each task it prices three approaches and checks the answer is actually there:

  naive       grep the terms, read the best-matching files in full (capped)   <- realistic baseline
  graphify    Graphify's own `query` (budgeted)                              <- graph map, no source code
  graphsnip   pack / find+symbol / impact+symbol                             <- map + exact code

Quality is checked, not assumed: a task counts as answered only when the
expected symbol shows up in the output (and, for graphsnip, with its source).
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path

from graphsnip.baseline import Baseline, query_terms
from graphsnip.engine import Engine
from graphsnip.project import Project
from graphsnip.tokens import count_tokens, method

TYPES = ("understand", "impact", "locate")


def _short(label: str) -> str:
    return re.sub(r"\([^()]*\)\s*$", "", label).strip()


def _resolvable(G, project: Project, name: str) -> bool:
    """Auto-generated tasks must be answerable by the tools; user-written tasks are never filtered."""
    try:
        from graphify.affected import resolve_seed_outcome
        from graphify.serve import _resolve_single_node

        nid, err = _resolve_single_node(G, name.split(".")[-1] if "." not in name else name)
        seed, _ = resolve_seed_outcome(G, name.split(".")[-1], project.root)
        return nid is not None and not err and seed is not None
    except Exception:
        return False


def default_tasks(project: Project, n: int = 8) -> list[dict]:
    eng = Engine(project.root, source="bench", auto_refresh=False)
    G = project.graph()
    seen_files: dict[str, int] = {}
    picks = []
    idx = project.label_index()
    from graphsnip.project import bare

    for row in project.top_symbols(80):
        lab = _short(str(row["label"]))
        if len(lab) < 4 or lab.startswith("_") or row["file"] in seen_files and seen_files[row["file"]] >= 1:
            continue
        if len(idx.get(bare(lab), [])) != 1:  # ambiguous names make impact/symbol lookups refuse; skip them
            continue
        if not _resolvable(G, project, eng._display(G, row["id"]).replace("()", "")):
            continue
        seen_files[row["file"]] = seen_files.get(row["file"], 0) + 1
        picks.append(row)
        if len(picks) >= n:
            break
    tasks = []
    for i, row in enumerate(picks):
        name = _short(eng._display(G, row["id"]))
        t = TYPES[i % 3]
        q = {"understand": f"how does {name} work and what does it depend on",
             "impact": f"what breaks if I change {name}",
             "locate": f"where is {name} defined and where is it used"}[t]
        tasks.append({"id": f"t{i + 1}", "type": t, "question": q, "expect": [name.split(".")[-1]],
                      "target_file": row["file"]})
    return tasks


def load_tasks(project: Project, path: str | None) -> list[dict]:
    p = Path(path) if path else project.gs_dir / "tasks.json"
    if p.exists():
        data = json.loads(p.read_text(encoding="utf-8"))
        tasks = data["tasks"] if isinstance(data, dict) else data
        for i, t in enumerate(tasks):
            t.setdefault("id", f"t{i + 1}")
            t.setdefault("type", "understand")
            t.setdefault("expect", [])
        return tasks
    return default_tasks(project)


def save_default_tasks(project: Project) -> Path:
    project.gs_dir.mkdir(parents=True, exist_ok=True)
    p = project.gs_dir / "tasks.json"
    p.write_text(json.dumps({"_note": "Edit freely. type: understand | impact | locate. expect: symbol names that must appear in a correct answer.",
                             "tasks": default_tasks(project)}, indent=2) + "\n", encoding="utf-8")
    return p


def run_bench(root: str = ".", tasks_path: str | None = None, quiet: bool = False) -> dict:
    eng = Engine(root, source="bench")
    p = eng.p
    p.ensure_graph(True)
    G = p.graph()
    tasks = load_tasks(p, tasks_path)
    base = Baseline(p)
    eng.ledger.clear(source="bench")
    from graphify.serve import _query_graph_text

    corpus_tokens = sum(base.file_tokens(p.rel(f)) for f in p.files()[:20000])
    rows = []
    t0 = time.time()
    for t in tasks:
        q = t["question"]
        expect = [e.lower() for e in t.get("expect", [])]
        e2 = Engine(root, source="bench", auto_refresh=False)  # fresh session per task
        naive_tokens, naive_files = base.grep_read(query_terms(q))
        naive_text = " ".join((p.read(f) or "") for f in naive_files).lower()
        naive_hit = all(x in naive_text for x in expect) if expect else bool(naive_files)

        gtext = _query_graph_text(G, q, mode="bfs", depth=3, token_budget=2000, graph_path=str(p.graph_json))
        gtokens = 0 if gtext.startswith("No matching") else count_tokens(gtext)
        ghit = all(x in gtext.lower() for x in expect) if expect else bool(gtokens)
        # what an agent using Graphify alone then does: read the files its top hits point to
        g_files: list[str] = []
        for m in re.finditer(r"src=([^\s\]]+)", gtext):
            f = m.group(1)
            if f and f not in g_files and (p.root / f).is_file():
                g_files.append(f)
            if len(g_files) >= 3:
                break
        gread_tokens = (gtokens + sum(base.file_tokens(f) for f in g_files)) if gtokens else 0

        typ = t.get("type", "understand")
        name = (t.get("expect") or [q])[0]
        parts = []
        if typ == "understand":
            parts.append(e2.pack(q, budget=3500))
        elif typ == "impact":
            parts.append(e2.impact(name))
            parts.append(e2.symbol(name))
        else:
            parts.append(e2.find(q, budget=1200))
            parts.append(e2.symbol(name))
        gs_text = "\n".join(r.text for r in parts if r.ok)
        gs_tokens = sum(r.returned for r in parts if r.ok)
        has_code = "```" in gs_text
        gs_hit = (all(x in gs_text.lower() for x in expect) if expect else bool(gs_tokens)) and has_code
        # second read of the same symbol in the same session
        again = e2.symbol(name)
        rows.append({
            "id": t["id"], "type": typ, "question": q, "expect": t.get("expect", []),
            "naive_tokens": naive_tokens, "naive_files": len(naive_files), "naive_hit": naive_hit,
            "graphify_tokens": gtokens, "graphify_hit": ghit, "graphify_read_tokens": gread_tokens,
            "graphsnip_tokens": gs_tokens, "graphsnip_hit": gs_hit, "graphsnip_has_code": has_code,
            "reread_tokens": again.returned if again.ok else None,
            "reduction_vs_naive": round(naive_tokens / gs_tokens, 1) if gs_tokens else None,
        })
    ok = [r for r in rows if r["graphsnip_tokens"]]
    tot_n = sum(r["naive_tokens"] for r in ok)
    tot_c = sum(r["graphsnip_tokens"] for r in ok)
    tot_g = sum(r["graphify_tokens"] for r in rows)
    tot_gr = sum(r["graphify_read_tokens"] for r in rows)
    result = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "root": str(p.root),
        "token_method": method(),
        "baseline_definition": "grep the question's terms, read the top 8 matching files in full",
        "graph": p.graph_stats(),
        "corpus_tokens": corpus_tokens,
        "tasks": rows,
        "totals": {
            "naive_tokens": tot_n, "graphsnip_tokens": tot_c, "graphify_tokens": tot_g, "graphify_read_tokens": tot_gr,
            "saved_vs_graphify_read_pct": round(100 * (tot_gr - tot_c) / tot_gr, 1) if tot_gr else 0.0,
            "saved_tokens": tot_n - tot_c,
            "saved_pct": round(100 * (tot_n - tot_c) / tot_n, 1) if tot_n else 0.0,
            "ratio": round(tot_n / tot_c, 1) if tot_c else 0.0,
            "vs_corpus_ratio": round(corpus_tokens / (tot_c / len(ok)), 1) if ok and tot_c else 0.0,
            "answered": sum(1 for r in rows if r["graphsnip_hit"]),
            "naive_answered": sum(1 for r in rows if r["naive_hit"]),
            "graphify_answered": sum(1 for r in rows if r["graphify_hit"]),
            "tasks": len(rows),
            "seconds": round(time.time() - t0, 1),
        },
    }
    p.gs_dir.mkdir(parents=True, exist_ok=True)
    (p.gs_dir / "bench.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    return result


def format_table(res: dict) -> str:
    rows = res["tasks"]
    t = res["totals"]
    w = max(8, min(44, max(len(r["question"]) for r in rows)))
    hdr = f"{'task':<{w}}  {'grep+read':>9}  {'graphify':>8}  {'gfy+read':>8}  {'graphsnip':>9}  {'saved':>6}  ok"
    out = [hdr, "-" * len(hdr)]
    for r in rows:
        saved = f"{100 * (1 - r['graphsnip_tokens'] / r['naive_tokens']):.0f}%" if r["naive_tokens"] and r["graphsnip_tokens"] else "-"
        out.append(f"{r['question'][:w]:<{w}}  {r['naive_tokens']:>9,}  {r['graphify_tokens']:>8,}  {r['graphify_read_tokens']:>8,}  "
                   f"{r['graphsnip_tokens']:>9,}  {saved:>6}  {'✓' if r['graphsnip_hit'] else '✗'}")
    out.append("-" * len(hdr))
    out.append(f"{'TOTAL':<{w}}  {t['naive_tokens']:>9,}  {t['graphify_tokens']:>8,}  {t['graphify_read_tokens']:>8,}  "
               f"{t['graphsnip_tokens']:>9,}  {t['saved_pct']:>5}%  {t['answered']}/{t['tasks']}")
    out.append(f"\ngrep+read  = {res['baseline_definition']} (what an agent does with no graph)")
    out.append("graphify   = Graphify `query` alone: a map with no source code")
    out.append("gfy+read   = that map, then reading the whole files its top hits point to (3 files max)")
    out.append("graphsnip  = exact code, ranked and budgeted (pack / find+symbol / impact+symbol)")
    out.append(f"Saved column is vs grep+read; vs graphify+read it is {t['saved_vs_graphify_read_pct']}%. Tokens: {res['token_method']}.")
    out.append(f"Answered (expected symbol + source present): graphsnip {t['answered']}/{t['tasks']}, "
               f"grep+read {t['naive_answered']}/{t['tasks']}.")
    return "\n".join(out)
