"""Measured A/B: the same questions answered by Claude Code with and without graphsnip, using the usage and cost the
CLI itself reports. This replaces the dashboard's estimate with real numbers for the tasks you run.

Design (so the comparison is fair):
  - two throwaway copies of your repo: `control` has graphsnip uninstalled, `treat` has it installed and its graph built
  - both arms are read-only (no Edit/Write/Bash), same model, same prompt, fresh session each run, order shuffled
  - cost and tokens come from `claude -p --output-format json` (total_cost_usd and usage incl. cache), never from our estimates
  - "answered" only checks that the expected symbols appear in the answer; the answers are saved so you can read them
"""
from __future__ import annotations

import json
import random
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from graphsnip import installer
from graphsnip.bench import load_tasks
from graphsnip.project import Project

_IGNORE = shutil.ignore_patterns(".git", "node_modules", ".venv", "venv", "__pycache__", "dist", "build", ".graphsnip",
                                 "graphify-out", ".next", "target", ".tox", ".mypy_cache", ".pytest_cache")
_READONLY_DENY = "Edit,Write,NotebookEdit,Bash"
PROMPT = "{q}\n\nAnswer concisely and cite file:line for each claim. Do not modify anything."


def parse_result(stdout: str) -> dict | None:
    """The `result` object from `claude -p --output-format json` (a dict, or a list of events whose last `result` we want)."""
    try:
        data = json.loads(stdout)
    except Exception:
        for ln in reversed([x for x in stdout.splitlines() if x.strip()]):  # stream-json fallback: last line with a result
            try:
                data = json.loads(ln)
                break
            except Exception:
                continue
        else:
            return None
    if isinstance(data, list):
        data = next((x for x in reversed(data) if isinstance(x, dict) and x.get("type") == "result"), None)
    if not isinstance(data, dict) or data.get("type", "result") != "result":
        return None
    u = data.get("usage") or {}
    inp, cc, cr, out = (int(u.get(k, 0) or 0) for k in ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens", "output_tokens"))
    return {"ok": not data.get("is_error", False), "cost": float(data.get("total_cost_usd", 0) or 0), "input": inp, "cache_creation": cc,
            "cache_read": cr, "output": out, "context": inp + cc + cr, "turns": int(data.get("num_turns", 0) or 0),
            "ms": int(data.get("duration_ms", 0) or 0), "text": str(data.get("result", "")), "models": sorted((data.get("modelUsage") or {}).keys())}


def _default_runner(cmd: list[str], cwd: str, timeout: int) -> tuple[int, str, str]:
    r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout)
    return r.returncode, r.stdout, r.stderr


def _prepare(src: Path, tmp: Path) -> tuple[Path, Path, Path]:
    control, treat = tmp / "control", tmp / "treat"
    for d in (control, treat):
        shutil.copytree(src, d, ignore=_IGNORE, symlinks=True)
    installer.uninstall(str(control), ["claude"])           # keep the user's own content, drop graphsnip
    treat_p = Project(str(treat))
    treat_p.build(force=True)
    installer.install(str(treat), ["claude"], allow_tools=True)
    empty = tmp / "empty-mcp.json"
    empty.write_text('{"mcpServers": {}}', encoding="utf-8")
    return control, treat, empty


def command(arm: str, prompt: str, treat: Path, empty: Path, model: str | None, run_budget: float, claude_bin: str = "claude") -> list[str]:
    cmd = [claude_bin, "-p", prompt, "--output-format", "json", "--no-session-persistence", "--max-budget-usd", f"{run_budget:g}",
           "--strict-mcp-config", "--disallowedTools", _READONLY_DENY]
    if arm == "treat":
        cmd += ["--mcp-config", str(treat / ".mcp.json"), "--allowedTools", "Read,Grep,Glob,mcp__graphsnip"]
    else:
        cmd += ["--mcp-config", str(empty), "--allowedTools", "Read,Grep,Glob"]
    if model:
        cmd += ["--model", model]
    return cmd


def _med(xs: list[float]) -> float:
    return float(statistics.median(xs)) if xs else 0.0


def run_ab(root: str = ".", tasks_path: str | None = None, repeats: int = 2, limit: int | None = None, model: str | None = None,
           max_cost: float = 5.0, run_budget: float = 1.0, dry_run: bool = False, keep: bool = False, claude_bin: str = "claude",
           runner=None, log=lambda s: print(s, file=sys.stderr), timeout: int = 900) -> dict:
    src = Project(root)
    src.ensure_graph(True)
    tasks = load_tasks(src, tasks_path)[: limit or None]
    if not tasks:
        raise RuntimeError("No tasks. Run `graphsnip bench --init-tasks` or pass --tasks.")
    plan = [(t, rep, arm) for t in tasks for rep in range(repeats) for arm in ("control", "treat")]
    random.Random(7).shuffle(plan)
    if dry_run:
        return {"dry_run": True, "runs": len(plan), "tasks": len(tasks), "repeats": repeats, "max_cost": max_cost,
                "example": command("treat", PROMPT.format(q=tasks[0]["question"]), Path("<treat>"), Path("<empty>"), model, run_budget, claude_bin)}
    if runner is None and not shutil.which(claude_bin):
        raise RuntimeError(f"`{claude_bin}` not found on PATH. Install Claude Code, or pass --claude-bin.")
    runner = runner or _default_runner
    tmp = Path(tempfile.mkdtemp(prefix="graphsnip-ab-"))
    ts = time.strftime("%Y%m%d-%H%M%S")
    ans_dir = src.gs_dir / "ab" / ts
    ans_dir.mkdir(parents=True, exist_ok=True)
    runs, spent, aborted = [], 0.0, None
    try:
        log(f"[graphsnip ab] preparing copies in {tmp} ...")
        control, treat, empty = _prepare(src.root, tmp)
        for i, (t, rep, arm) in enumerate(plan, 1):
            if spent >= max_cost:
                aborted = f"stopped after ${spent:.2f} (cost cap ${max_cost:g}); {len(plan) - i + 1} runs not done"
                log(f"[graphsnip ab] {aborted}")
                break
            cwd = control if arm == "control" else treat
            cmd = command(arm, PROMPT.format(q=t["question"]), treat, empty, model, run_budget, claude_bin)
            log(f"[graphsnip ab] {i}/{len(plan)} {arm:<7} {t['id']} (rep {rep + 1}) spent ${spent:.2f}")
            try:
                rc, out, err = runner(cmd, str(cwd), timeout)
            except Exception as e:  # timeout etc.
                runs.append({"task": t["id"], "arm": arm, "rep": rep, "ok": False, "error": str(e)[:200]})
                continue
            res = parse_result(out)
            if not res:
                runs.append({"task": t["id"], "arm": arm, "rep": rep, "ok": False, "error": (err or out)[-200:] or f"exit {rc}"})
                continue
            spent += res["cost"]
            expect = [e.lower() for e in t.get("expect", [])]
            res["hit"] = all(x in res["text"].lower() for x in expect) if expect else None
            (ans_dir / f"{t['id']}-{arm}-{rep + 1}.md").write_text(f"# {t['question']}\n\n{res['text']}\n", encoding="utf-8")
            runs.append({"task": t["id"], "arm": arm, "rep": rep, **{k: v for k, v in res.items() if k != "text"}})
    finally:
        if keep:
            log(f"[graphsnip ab] kept {tmp}")
        else:
            shutil.rmtree(tmp, ignore_errors=True)
    result = summarize(runs, tasks)
    result.update({"ts": ts, "root": str(src.root), "repeats": repeats, "model": model or "default", "aborted": aborted,
                   "answers_dir": str(ans_dir), "spent": round(spent, 4), "prompt": PROMPT})
    (src.gs_dir / "ab.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    return result


def summarize(runs: list[dict], tasks: list[dict]) -> dict:
    per_task = []
    for t in tasks:
        row = {"id": t["id"], "question": t["question"]}
        for arm in ("control", "treat"):
            rs = [r for r in runs if r.get("task") == t["id"] and r.get("arm") == arm and r.get("ok")]
            row[arm] = {"runs": len(rs), "cost": _med([r["cost"] for r in rs]), "context": _med([r["context"] for r in rs]),
                        "output": _med([r["output"] for r in rs]), "turns": _med([r["turns"] for r in rs]),
                        "hits": sum(1 for r in rs if r.get("hit")), "hit_known": sum(1 for r in rs if r.get("hit") is not None)}
        per_task.append(row)
    both = [r for r in per_task if r["control"]["runs"] and r["treat"]["runs"]]

    def tot(arm, k):
        return sum(r[arm][k] for r in both)

    cc, tc = tot("control", "cost"), tot("treat", "cost")
    cx, tx = tot("control", "context"), tot("treat", "context")
    return {
        "tasks": per_task,
        "paired_tasks": len(both),
        "failed_runs": sum(1 for r in runs if not r.get("ok")),
        "totals": {"control_cost": round(cc, 4), "treat_cost": round(tc, 4), "cost_saved": round(cc - tc, 4),
                   "cost_saved_pct": round(100 * (cc - tc) / cc, 1) if cc else 0.0,
                   "control_context": int(cx), "treat_context": int(tx), "context_saved": int(cx - tx),
                   "context_saved_pct": round(100 * (cx - tx) / cx, 1) if cx else 0.0,
                   "control_output": int(tot("control", "output")), "treat_output": int(tot("treat", "output")),
                   "control_hits": tot("control", "hits"), "treat_hits": tot("treat", "hits"),
                   "hit_known": tot("treat", "hit_known")},
    }


def format_table(res: dict) -> str:
    t = res["totals"]
    lines = [f"{'task':<9} {'control $':>10} {'graphsnip $':>12} {'saved':>7}  {'ctx tokens (ctl → gs)':>26}  answered"]
    for r in res["tasks"]:
        c, g = r["control"], r["treat"]
        if not (c["runs"] and g["runs"]):
            lines.append(f"{r['id']:<9} (no complete pair)")
            continue
        sv = f"{100 * (c['cost'] - g['cost']) / c['cost']:.0f}%" if c["cost"] else "-"
        lines.append(f"{r['id']:<9} {c['cost']:>10.4f} {g['cost']:>12.4f} {sv:>7}  {int(c['context']):>11,} → {int(g['context']):>10,}  "
                     f"{c['hits']}/{c['hit_known']} vs {g['hits']}/{g['hit_known']}")
    lines.append("-" * 80)
    lines.append(f"MEASURED over {res['paired_tasks']} tasks × {res['repeats']} runs per arm (model: {res['model']}):")
    lines.append(f"  cost      ${t['control_cost']:.4f} → ${t['treat_cost']:.4f}   saved ${t['cost_saved']:.4f} ({t['cost_saved_pct']}%)")
    lines.append(f"  context   {t['control_context']:,} → {t['treat_context']:,} tokens   saved {t['context_saved']:,} ({t['context_saved_pct']}%)")
    lines.append(f"  answered  without {t['control_hits']}/{t['hit_known']}   with graphsnip {t['treat_hits']}/{t['hit_known']}  (expected symbols present; read the saved answers to judge quality)")
    if res.get("aborted"):
        lines.append(f"  NOTE: {res['aborted']}")
    if res.get("failed_runs"):
        lines.append(f"  NOTE: {res['failed_runs']} run(s) failed and are excluded")
    lines.append(f"  answers saved in {res['answers_dir']}")
    return "\n".join(lines)
