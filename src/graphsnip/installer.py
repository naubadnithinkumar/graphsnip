"""Client installers: Claude Code, GitHub Copilot (VS Code / CLI), Codex.

Everything is idempotent and reversible:
- text files get a marked, replaceable block (<!-- graphsnip:begin --> ... <!-- graphsnip:end -->)
- JSON files are merged, touching only the `graphsnip` entry / our own hook
- `--dry-run` prints what would change and writes nothing
"""
from __future__ import annotations

import json
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path

from graphsnip.project import Project

BEGIN = "<!-- graphsnip:begin -->"
END = "<!-- graphsnip:end -->"
TOML_BEGIN = "# graphsnip:begin"
TOML_END = "# graphsnip:end"
HOOK_MARK = "graphsnip hook"
SERVER = "graphsnip"
CLIENTS = ("claude", "copilot", "codex")

GUIDE = f"""{BEGIN}
## Code navigation (graphsnip)

This repo is indexed as a code knowledge graph (Graphify). Use the `graphsnip` MCP tools before grep or whole-file reads:

1. `gs_pack(task)` first for "how does X work" or before a change. It returns the exact relevant code, ranked and token-budgeted.
2. `gs_find(question)` to locate things; `gs_outline(path)` before opening a big file.
3. `gs_symbol(name)` to read one function/class instead of the whole file.
4. `gs_impact(name)` before changing a signature or behaviour.
5. Fall back to grep/Read for literal strings, config, docs, or when the graph has no match.

Prefer `Read` with `offset`/`limit` over whole-file reads. If you need long test/build output, run it through `graphsnip run -- <command>` to compress it.
Interactive map: `graphify-out/graph.html`. Savings dashboard: `.graphsnip/dashboard.html` (`graphsnip report`).
{END}
"""

EXPLORER_AGENT = """---
name: graphsnip-explorer
description: Read-only code exploration over the graphsnip knowledge graph. Use for "where is X", "how does Y work", or "what depends on Z" so the main context only receives a short answer.
tools: mcp__graphsnip__cg_pack, mcp__graphsnip__cg_find, mcp__graphsnip__cg_symbol, mcp__graphsnip__cg_impact, mcp__graphsnip__cg_outline, Read
model: haiku
---
You explore a codebase through the graphsnip tools and report back briefly.

1. Start with `gs_pack` (understanding) or `gs_find` (locating). Use `gs_symbol` for single definitions and `gs_impact` for dependents.
2. Use Read only with offset/limit, and only when the graph tools cannot answer.
3. Answer in at most 250 words: the direct answer first, then the key `file:line` references. Do not paste code unless asked; point to it.
4. If the graph has no match, say so and suggest the grep pattern to try.
"""


@dataclass
class Plan:
    actions: list = field(default_factory=list)  # (path, description, callable)

    def add(self, path: Path, desc: str, fn):
        self.actions.append((path, desc, fn))


_PORTABLE = False  # True: write bare `graphsnip` (commit-friendly) instead of this machine's absolute path


def _command(project_root: Path | None) -> tuple[str, list[str]]:
    exe = shutil.which("graphsnip")
    if _PORTABLE:
        return "graphsnip", ["serve", "--root", "."]
    if exe:
        return exe, ["serve", "--root", "."]
    return sys.executable, ["-m", "graphsnip", "serve", "--root", "."]


def _hook_command() -> str:
    exe = shutil.which("graphsnip")
    if _PORTABLE:
        return "graphsnip hook pretooluse"
    return f"{exe} hook pretooluse" if exe else f"{sys.executable} -m graphsnip hook pretooluse"


# ---------------------------------------------------------------- text helpers
def _upsert_block(path: Path, block: str, begin: str, end: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    cur = path.read_text(encoding="utf-8") if path.exists() else ""
    if begin in cur and end in cur:
        a = cur.index(begin)
        b = cur.index(end, a) + len(end)
        new = cur[:a] + block.strip("\n") + cur[b:]
    else:
        new = (cur.rstrip("\n") + "\n\n" if cur.strip() else "") + block
    if not new.endswith("\n"):
        new += "\n"
    path.write_text(new, encoding="utf-8")


def _remove_block(path: Path, begin: str, end: str) -> bool:
    if not path.exists():
        return False
    cur = path.read_text(encoding="utf-8")
    if begin not in cur or end not in cur:
        return False
    a = cur.index(begin)
    b = cur.index(end, a) + len(end)
    new = (cur[:a].rstrip("\n") + "\n" + cur[b:].lstrip("\n")).strip("\n")
    if new.strip():
        path.write_text(new + "\n", encoding="utf-8")
    else:
        path.unlink()
    return True


def _load_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8") or "{}")
    except Exception as e:
        raise RuntimeError(f"{path} is not valid JSON ({e}); fix it or move it aside, then re-run") from e


def _save_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


# ---------------------------------------------------------------- per client
def _claude(root: Path, plan: Plan, allow_tools: bool, strict: bool) -> None:
    cmd, args = _command(root)

    def mcp():
        p = root / ".mcp.json"
        d = _load_json(p)
        d.setdefault("mcpServers", {})[SERVER] = {"command": cmd, "args": args}
        _save_json(p, d)

    def settings():
        p = root / ".claude" / "settings.json"
        d = _load_json(p)
        pre = d.setdefault("hooks", {}).setdefault("PreToolUse", [])
        pre[:] = [g for g in pre if not any(HOOK_MARK in (h.get("command", "")) for h in g.get("hooks", []))]
        pre.append({"matcher": "Read|Grep|Bash", "hooks": [{"type": "command", "command": _hook_command()}]})
        if allow_tools:
            allow = d.setdefault("permissions", {}).setdefault("allow", [])
            if f"mcp__{SERVER}" not in allow:
                allow.append(f"mcp__{SERVER}")
        _save_json(p, d)

    plan.add(root / ".mcp.json", f"register MCP server '{SERVER}'", mcp)
    plan.add(root / ".claude" / "settings.json",
             "add PreToolUse hook (Read|Grep|Bash)" + (" + allow graphsnip tools" if allow_tools else ""), settings)
    plan.add(root / "CLAUDE.md", "add graphsnip guidance block", lambda: _upsert_block(root / "CLAUDE.md", GUIDE, BEGIN, END))
    plan.add(root / ".claude" / "agents" / "graphsnip-explorer.md", "add cheap read-only explorer subagent (Haiku)",
             lambda: (root / ".claude" / "agents").mkdir(parents=True, exist_ok=True) or
             (root / ".claude" / "agents" / "graphsnip-explorer.md").write_text(EXPLORER_AGENT, encoding="utf-8"))


def _copilot(root: Path, plan: Plan) -> None:
    cmd, args = _command(root)

    def mcp():
        p = root / ".vscode" / "mcp.json"
        d = _load_json(p)
        d.setdefault("servers", {})[SERVER] = {"type": "stdio", "command": cmd, "args": args}
        _save_json(p, d)

    plan.add(root / ".vscode" / "mcp.json", f"register MCP server '{SERVER}' for VS Code Copilot", mcp)
    plan.add(root / ".github" / "copilot-instructions.md", "add graphsnip guidance block",
             lambda: _upsert_block(root / ".github" / "copilot-instructions.md", GUIDE, BEGIN, END))


def _toml_block(cmd: str, args: list[str]) -> str:
    a = ", ".join(json.dumps(x) for x in args)
    return f'{TOML_BEGIN}\n[mcp_servers.{SERVER}]\ncommand = {json.dumps(cmd)}\nargs = [{a}]\n{TOML_END}\n'


def _codex(root: Path, plan: Plan, scope: str) -> None:
    cmd, args = _command(root)
    if scope == "user":
        cfg = Path.home() / ".codex" / "config.toml"
        args = ["serve", "--root", str(root)] if cmd.endswith("graphsnip") else ["-m", "graphsnip", "serve", "--root", str(root)]
    else:
        cfg = root / ".codex" / "config.toml"
    plan.add(cfg, f"register MCP server '{SERVER}' ({scope} config)",
             lambda: _upsert_block(cfg, _toml_block(cmd, args), TOML_BEGIN, TOML_END))
    plan.add(root / "AGENTS.md", "add graphsnip guidance block",
             lambda: _upsert_block(root / "AGENTS.md", GUIDE, BEGIN, END))


def _gitignore(root: Path, plan: Plan) -> None:
    def fn():
        p = root / ".gitignore"
        cur = p.read_text(encoding="utf-8") if p.exists() else ""
        if ".graphsnip/" not in cur.split():
            p.write_text(cur.rstrip("\n") + ("\n" if cur else "") + "# graphsnip local data (ledger, dashboard)\n.graphsnip/\n", encoding="utf-8")

    plan.add(root / ".gitignore", "ignore .graphsnip/ (ledger + dashboard are local)", fn)


def install(root: str, clients: list[str], dry_run: bool = False, allow_tools: bool = False,
            codex_scope: str = "project", strict: bool = False, portable: bool = False) -> list[str]:
    global _PORTABLE
    _PORTABLE = portable
    project = Project(root)
    r = project.root
    plan = Plan()
    chosen = list(CLIENTS) if "all" in clients else [c for c in clients if c in CLIENTS]
    unknown = [c for c in clients if c != "all" and c not in CLIENTS]
    if unknown:
        raise ValueError(f"unknown client(s): {', '.join(unknown)} (choose from {', '.join(CLIENTS)} or all)")
    if "claude" in chosen:
        _claude(r, plan, allow_tools, strict)
    if "copilot" in chosen:
        _copilot(r, plan)
    if "codex" in chosen:
        _codex(r, plan, codex_scope)
    _gitignore(r, plan)
    lines = []
    for path, desc, fn in plan.actions:
        try:
            shown = path.relative_to(r)
        except ValueError:
            shown = path
        if dry_run:
            lines.append(f"[dry-run] would update {shown}: {desc}")
        else:
            fn()
            lines.append(f"updated {shown}: {desc}")
    if strict and not dry_run:
        cfgp = project.write_default_config()
        d = json.loads(cfgp.read_text(encoding="utf-8"))
        d["mode"] = "strict"
        cfgp.write_text(json.dumps(d, indent=2) + "\n", encoding="utf-8")
        lines.append("set hook mode to strict in .graphsnip/config.json")
    elif not dry_run:
        project.write_default_config()
    return lines


def uninstall(root: str, clients: list[str], codex_scope: str = "project") -> list[str]:
    r = Project(root).root
    chosen = list(CLIENTS) if "all" in clients else [c for c in clients if c in CLIENTS]
    out = []
    if "claude" in chosen:
        p = r / ".mcp.json"
        if p.exists():
            d = _load_json(p)
            if d.get("mcpServers", {}).pop(SERVER, None) is not None:
                _save_json(p, d)
                out.append("removed MCP server from .mcp.json")
        p = r / ".claude" / "settings.json"
        if p.exists():
            d = _load_json(p)
            pre = d.get("hooks", {}).get("PreToolUse", [])
            keep = [g for g in pre if not any(HOOK_MARK in h.get("command", "") for h in g.get("hooks", []))]
            if len(keep) != len(pre):
                d["hooks"]["PreToolUse"] = keep
                if not keep:
                    d["hooks"].pop("PreToolUse")
                if not d["hooks"]:
                    d.pop("hooks")
                out.append("removed PreToolUse hook from .claude/settings.json")
            al = d.get("permissions", {}).get("allow", [])
            if f"mcp__{SERVER}" in al:
                al.remove(f"mcp__{SERVER}")
                out.append("removed tool allow-rule")
            _save_json(p, d)
        if _remove_block(r / "CLAUDE.md", BEGIN, END):
            out.append("removed block from CLAUDE.md")
        a = r / ".claude" / "agents" / "graphsnip-explorer.md"
        if a.exists():
            a.unlink()
            out.append("removed explorer subagent")
    if "copilot" in chosen:
        p = r / ".vscode" / "mcp.json"
        if p.exists():
            d = _load_json(p)
            if d.get("servers", {}).pop(SERVER, None) is not None:
                _save_json(p, d)
                out.append("removed MCP server from .vscode/mcp.json")
        if _remove_block(r / ".github" / "copilot-instructions.md", BEGIN, END):
            out.append("removed block from .github/copilot-instructions.md")
    if "codex" in chosen:
        cfg = (Path.home() / ".codex" / "config.toml") if codex_scope == "user" else (r / ".codex" / "config.toml")
        if _remove_block(cfg, TOML_BEGIN, TOML_END):
            out.append(f"removed MCP block from {cfg}")
        if _remove_block(r / "AGENTS.md", BEGIN, END):
            out.append("removed block from AGENTS.md")
    return out or ["nothing to remove"]
