"""Keyword-only vs hybrid (keyword + semantic) retrieval on the same questions.

Run it on questions that do NOT use the code's own words (a `tasks.json` with `question` and `expect`):
a task counts as found when every expected string appears in what `gs_pack` returns.
"""
from __future__ import annotations

import json
import os
import time

from graphsnip import semantic
from graphsnip.bench import load_tasks
from graphsnip.engine import Engine
from graphsnip.project import Project


def _arm(project: Project, question: str, expect: list[str], hybrid: bool) -> dict:
    prev = os.environ.get("GRAPHSNIP_SEMANTIC")
    if hybrid:
        os.environ.pop("GRAPHSNIP_SEMANTIC", None)
    else:
        os.environ["GRAPHSNIP_SEMANTIC"] = "off"
    try:
        eng = Engine(project.root, source="bench", auto_refresh=False)  # "bench" calls are kept out of the savings totals
        r = eng.pack(question)
    finally:
        if prev is None:
            os.environ.pop("GRAPHSNIP_SEMANTIC", None)
        else:
            os.environ["GRAPHSNIP_SEMANTIC"] = prev
    text = r.text.lower() if r.ok else ""
    found = [e for e in expect if e.lower() in text]
    first = min((text.find(e.lower()) for e in found), default=-1)
    return {"ok": r.ok, "found": len(found), "expected": len(expect), "hit": bool(expect) and len(found) == len(expect),
            "tokens": r.returned if r.ok else 0, "first_at_pct": round(100 * first / max(len(text), 1)) if first >= 0 else None,
            "retrieval": eng.last_retrieval}


def run_eval(root: str = ".", tasks_path: str | None = None, write: bool = True) -> dict:
    p = Project(root)
    if not semantic.has_index(p):
        raise semantic.SemanticUnavailable("No semantic index yet. Run `graphsnip embed` first.")
    p.ensure_graph(False)
    rows = []
    for t in load_tasks(p, tasks_path):
        exp = [str(e) for e in (t.get("expect") or [])]
        rows.append({"id": t.get("id"), "question": t["question"], "kind": t.get("kind", ""),
                     "keyword": _arm(p, t["question"], exp, False), "hybrid": _arm(p, t["question"], exp, True)})
    n = len(rows)
    kh = sum(r["keyword"]["hit"] for r in rows)
    hh = sum(r["hybrid"]["hit"] for r in rows)
    res = {
        "ran_at": time.strftime("%Y-%m-%dT%H:%M:%S"), "model": semantic.status(p).get("model"), "tasks": rows,
        "totals": {"n": n, "keyword_hits": kh, "hybrid_hits": hh, "gained": sum(1 for r in rows if r["hybrid"]["hit"] and not r["keyword"]["hit"]),
                   "lost": sum(1 for r in rows if r["keyword"]["hit"] and not r["hybrid"]["hit"]),
                   "keyword_tokens": sum(r["keyword"]["tokens"] for r in rows), "hybrid_tokens": sum(r["hybrid"]["tokens"] for r in rows)},
    }
    if write:
        p.gs_dir.mkdir(parents=True, exist_ok=True)
        (p.gs_dir / "retrieval.json").write_text(json.dumps(res, indent=2), encoding="utf-8")
    return res


def format_table(res: dict) -> str:
    t = res["totals"]
    out = [f"Retrieval: keyword vs hybrid ({res.get('model')}), {t['n']} questions", ""]
    out.append(f"  {'question':<56} {'keyword':>8} {'hybrid':>8}")
    for r in res["tasks"]:
        out.append(f"  {r['question'][:55]:<56} {('found' if r['keyword']['hit'] else 'MISSED'):>8} {('found' if r['hybrid']['hit'] else 'MISSED'):>8}")
    out.append("")
    out.append(f"  found: keyword {t['keyword_hits']}/{t['n']}, hybrid {t['hybrid_hits']}/{t['n']}  "
               f"(gained {t['gained']}, lost {t['lost']})")
    out.append(f"  tokens returned: keyword {t['keyword_tokens']:,}, hybrid {t['hybrid_tokens']:,}")
    if t["lost"]:
        out.append("  ! hybrid lost questions that keyword search found; do not ship this model or setting for this repo.")
    return "\n".join(out)
