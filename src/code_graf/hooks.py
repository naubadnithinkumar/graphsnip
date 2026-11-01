"""Claude Code PreToolUse hook.

Principles
- Fail open: any error means "do nothing".
- Only redirect when the graph can actually answer (a big file it has an outline
  for, an identifier it knows). Never block a literal-string grep.
- Default mode is "nudge" (extra context, nothing blocked). "strict" denies the
  redirectable calls with a reason that tells the agent exactly what to call.
- Rewriting shell commands is opt-in (config shell_filter) and limited to an
  allow-list, because an `allow` decision skips the permission prompt.
"""
from __future__ import annotations

import json
import re
import shlex
import shutil
import sys
from pathlib import Path

from code_graf import ledger as _ledger
from code_graf.project import CODE_EXTS, Project, bare
from code_graf.tokens import count_tokens

_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_.]*$")
_GREPS = {"grep", "egrep", "fgrep", "rg", "ag", "ack"}
_NOISY = re.compile(
    r"^\s*(pytest|python3? -m pytest|npm (run )?test|npm run (build|lint)|yarn (test|build|lint)|pnpm (test|build|lint)|"
    r"npx (jest|vitest|tsc|eslint)|jest|vitest|tsc|eslint|ruff|mypy|go (test|build|vet)|cargo (test|build|check|clippy)|"
    r"mvn|gradle|\./gradlew|make( \w+)?|dotnet (test|build)|git log)\b")
_UNSAFE_SHELL = re.compile(r"[|;&<>`$()]|\n")


def _out(kind: str, text: str, deny: bool) -> dict:
    if deny:
        return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                       "permissionDecisionReason": text}}
    return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "additionalContext": text}}


def _log(project: Project, tool: str, action: str, detail: str) -> None:
    _ledger.Ledger(project.cg_dir).record(kind="hook", tool=tool, action=action, detail=detail[:160], source="hook")


def _symbol_hint(project: Project, pattern: str) -> str | None:
    pat = pattern.strip().strip("'\"")
    if not _IDENT.match(pat) or len(pat) < 3:
        return None
    ids = project.label_index().get(bare(pat))
    if not ids or len(ids) > 3:
        return None
    G = project.graph()
    parts = []
    for nid in ids:
        d = G.nodes[nid]
        parts.append(f'{project.display(nid)} ({d.get("source_file")}:{d.get("source_location")})')
    first = project.display(ids[0]).replace("()", "")
    return (f'"{pat}" is a symbol in the code graph: ' + "; ".join(parts) +
            f'. Use cg_symbol("{first}") for its code and callers, or cg_impact("{first}") for usages, instead of grep.')


def _read_hint(project: Project, ti: dict, strict: bool) -> tuple[str, str] | None:
    if ti.get("offset") or ti.get("limit"):
        return None
    fp = ti.get("file_path") or ti.get("path")
    if not fp:
        return None
    p = Path(fp)
    if p.suffix.lower() not in CODE_EXTS:
        return None
    try:
        p = p.resolve()
        rel = p.relative_to(project.root).as_posix()
        size = p.stat().st_size
    except Exception:
        return None
    threshold = int(project.config().get("read_threshold_tokens", 1500))
    if size / 4 < threshold * 0.6:
        return None
    toks = count_tokens(p.read_text(encoding="utf-8", errors="replace"))
    if toks <= threshold:
        return None
    rows = project.outline_rows(rel)
    if not rows:
        return None
    cap = 40 if strict else 18
    lines = [f"L{ln} {lab}{' [class]' if c else ''}" for ln, lab, c in rows[:cap]]
    if len(rows) > cap:
        lines.append(f"… +{len(rows) - cap} more (cg_outline('{rel}') lists all)")
    msg = (f"{rel} is ~{toks:,} tokens. Its symbols per the code graph:\n" + "\n".join(lines) +
           f"\nRead only what you need: cg_symbol(name) for one definition, or Read with offset/limit around a line.")
    return msg, rel


def _bash_pattern(cmd: str) -> str | None:
    """Pattern of a plain `grep|rg|ag|ack PATTERN ...` command; None for pipelines and anything else."""
    try:
        lex = shlex.shlex(cmd, posix=True, punctuation_chars=True)
        lex.whitespace_split = True
        parts = list(lex)
    except ValueError:
        return None
    if any(p and all(c in "|;&<>()" for c in p) for p in parts):
        return None
    if not parts or Path(parts[0]).name not in _GREPS:
        return None
    for a in parts[1:]:
        if not a.startswith("-"):
            return a
    return None


def _wrap_command(cmd: str) -> str:
    exe = shutil.which("code-graf")
    base = shlex.quote(exe) if exe else f"{shlex.quote(sys.executable)} -m code_graf"
    return f"{base} run -- bash -c {shlex.quote(cmd)}"


def handle(payload: dict, project: Project) -> dict | None:
    cfg = project.config()
    mode = str(cfg.get("mode", "nudge")).lower()
    if mode == "off" or not project.has_graph():
        return None
    strict = mode == "strict"
    tool = payload.get("tool_name")
    ti = payload.get("tool_input") or {}

    if tool == "Read":
        hit = _read_hint(project, ti, strict)
        if hit:
            msg, rel = hit
            _log(project, "Read", "deny" if strict else "nudge", rel)
            return _out("read", msg, deny=strict)
        return None

    if tool == "Grep":
        hint = _symbol_hint(project, str(ti.get("pattern", "")))
        if hint:
            _log(project, "Grep", "deny" if strict else "nudge", str(ti.get("pattern")))
            return _out("grep", hint, deny=strict)
        return None

    if tool == "Bash":
        cmd = str(ti.get("command", ""))
        pat = _bash_pattern(cmd)
        if pat:
            hint = _symbol_hint(project, pat)
            if hint:
                _log(project, "Bash", "deny" if strict else "nudge", cmd)
                return _out("grep", hint, deny=strict)
        if cfg.get("shell_filter") and _NOISY.match(cmd) and not _UNSAFE_SHELL.search(cmd) and "code-graf" not in cmd:
            _log(project, "Bash", "compress", cmd)
            new = dict(ti)
            new["command"] = _wrap_command(cmd)
            return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "allow",
                                           "permissionDecisionReason": "code-graf: compressing noisy command output",
                                           "updatedInput": new}}
    return None


def main_stdin(root: str = ".") -> int:
    try:
        raw = sys.stdin.read()
        payload = json.loads(raw) if raw.strip() else {}
        cwd = payload.get("cwd") or root
        project = Project(cwd)
        # walk up to find the project that owns a graph
        p = project.root
        for cand in [p, *p.parents]:
            if (cand / "graphify-out" / "graph.json").exists():
                project = Project(cand)
                break
        res = handle(payload, project)
        if res:
            sys.stdout.write(json.dumps(res))
    except Exception as e:  # fail open
        sys.stderr.write(f"[code-graf hook] ignored error: {type(e).__name__}: {e}\n")
    return 0
