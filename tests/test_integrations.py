import json
import subprocess
import sys

from graphsnip import hooks, installer
from graphsnip.dashboard import build_html, write_dashboard
from graphsnip.project import Project


def _project_with_cfg(repo, **cfg):
    p = Project(repo)
    p.gs_dir.mkdir(exist_ok=True)
    (p.gs_dir / "config.json").write_text(json.dumps(cfg))
    return Project(repo)


# ------------------------------------------------------------------ hooks
def test_hook_nudges_on_large_read_and_stays_quiet_with_limit(repo):
    p = _project_with_cfg(repo, mode="nudge", read_threshold_tokens=20)
    f = str(repo / "app" / "services.py")
    out = hooks.handle({"tool_name": "Read", "tool_input": {"file_path": f}}, p)
    ctx = out["hookSpecificOutput"]["additionalContext"]
    assert "UserService" in ctx and "permissionDecision" not in out["hookSpecificOutput"]
    assert hooks.handle({"tool_name": "Read", "tool_input": {"file_path": f, "limit": 20}}, p) is None


def test_hook_strict_denies_identifier_grep_but_not_literals(repo):
    p = _project_with_cfg(repo, mode="strict")
    out = hooks.handle({"tool_name": "Grep", "tool_input": {"pattern": "validate_email"}}, p)
    assert out["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert "gs_symbol" in out["hookSpecificOutput"]["permissionDecisionReason"]
    assert hooks.handle({"tool_name": "Grep", "tool_input": {"pattern": "bad email"}}, p) is None
    assert hooks.handle({"tool_name": "Grep", "tool_input": {"pattern": "nope_not_a_symbol"}}, p) is None


def test_hook_bash_grep_and_pipelines(repo):
    p = _project_with_cfg(repo, mode="nudge")
    assert hooks.handle({"tool_name": "Bash", "tool_input": {"command": "grep -rn validate_name app/"}}, p)
    assert hooks.handle({"tool_name": "Bash", "tool_input": {"command": "grep validate_name app/ | head"}}, p) is None


def test_hook_off_and_shell_filter_is_opt_in(repo):
    p = _project_with_cfg(repo, mode="off")
    assert hooks.handle({"tool_name": "Grep", "tool_input": {"pattern": "validate_email"}}, p) is None
    p = _project_with_cfg(repo, mode="nudge", shell_filter=False)
    assert hooks.handle({"tool_name": "Bash", "tool_input": {"command": "pytest -q"}}, p) is None
    p = _project_with_cfg(repo, mode="nudge", shell_filter=True)
    out = hooks.handle({"tool_name": "Bash", "tool_input": {"command": "pytest -q"}}, p)["hookSpecificOutput"]
    assert "graphsnip run" in out["updatedInput"]["command"] or "graphsnip" in out["updatedInput"]["command"]
    # never rewrite pipelines or chained commands
    assert hooks.handle({"tool_name": "Bash", "tool_input": {"command": "pytest -q && rm -rf x"}}, p) is None


def test_hook_cli_fails_open_on_garbage():
    r = subprocess.run([sys.executable, "-m", "graphsnip", "hook", "pretooluse"], input="not json", capture_output=True, text=True)
    assert r.returncode == 0 and r.stdout == ""


# ------------------------------------------------------------------ installer
def test_dry_run_writes_nothing(tmp_path):
    lines = installer.install(str(tmp_path), ["all"], dry_run=True)
    assert lines and all(ln.startswith("[dry-run]") for ln in lines)
    assert list(tmp_path.iterdir()) == []


def test_install_all_is_idempotent_and_preserves_existing(tmp_path):
    (tmp_path / ".mcp.json").write_text(json.dumps({"mcpServers": {"other": {"command": "x"}}}))
    (tmp_path / "AGENTS.md").write_text("# My rules\n\nBe nice.\n")
    installer.install(str(tmp_path), ["all"], allow_tools=True)
    snap = {p.relative_to(tmp_path).as_posix(): p.read_text() for p in tmp_path.rglob("*") if p.is_file()}
    installer.install(str(tmp_path), ["all"], allow_tools=True)
    snap2 = {p.relative_to(tmp_path).as_posix(): p.read_text() for p in tmp_path.rglob("*") if p.is_file()}
    assert snap == snap2  # second run changes nothing
    mcp = json.loads(snap[".mcp.json"])
    assert "other" in mcp["mcpServers"] and "graphsnip" in mcp["mcpServers"]
    assert json.loads(snap[".vscode/mcp.json"])["servers"]["graphsnip"]["type"] == "stdio"
    s = json.loads(snap[".claude/settings.json"])
    assert any("hook pretooluse" in h["command"] for g in s["hooks"]["PreToolUse"] for h in g["hooks"])
    assert "mcp__graphsnip" in s["permissions"]["allow"]
    assert "[mcp_servers.graphsnip]" in snap[".codex/config.toml"]
    assert snap["AGENTS.md"].startswith("# My rules") and installer.BEGIN in snap["AGENTS.md"]
    assert snap["AGENTS.md"].count(installer.BEGIN) == 1
    assert "model: haiku" in snap[".claude/agents/graphsnip-explorer.md"]
    assert ".graphsnip/" in snap[".gitignore"]


def test_uninstall_restores_user_content(tmp_path):
    (tmp_path / "CLAUDE.md").write_text("# Mine\n\nKeep this.\n")
    (tmp_path / ".mcp.json").write_text(json.dumps({"mcpServers": {"other": {"command": "x"}}}))
    installer.install(str(tmp_path), ["claude"])
    installer.uninstall(str(tmp_path), ["claude"])
    assert (tmp_path / "CLAUDE.md").read_text().strip() == "# Mine\n\nKeep this."
    assert json.loads((tmp_path / ".mcp.json").read_text()) == {"mcpServers": {"other": {"command": "x"}}}
    assert not (tmp_path / ".claude" / "agents" / "graphsnip-explorer.md").exists()
    assert "graphsnip" not in (tmp_path / ".claude" / "settings.json").read_text()


def test_install_rejects_unknown_client_and_bad_json(tmp_path):
    import pytest

    with pytest.raises(ValueError):
        installer.install(str(tmp_path), ["vim"])
    (tmp_path / ".mcp.json").write_text("{not json")
    with pytest.raises(RuntimeError):
        installer.install(str(tmp_path), ["claude"])


# ------------------------------------------------------------------ MCP server (raw JSON-RPC)
def test_mcp_server_speaks_protocol(repo):
    msgs = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-03-26", "capabilities": {},
                                                                       "clientInfo": {"name": "t", "version": "0"}}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "gs_symbol", "arguments": {"name": "create_user"}}},
        {"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "gs_symbol", "arguments": {"name": "create_user"}}},
        {"jsonrpc": "2.0", "id": 5, "method": "tools/call", "params": {"name": "gs_symbol", "arguments": {"name": "zzz_missing"}}},
        {"jsonrpc": "2.0", "id": 6, "method": "nope"},
    ]
    r = subprocess.run([sys.executable, "-m", "graphsnip", "serve", "--root", str(repo)],
                       input="\n".join(json.dumps(m) for m in msgs) + "\n", capture_output=True, text=True, timeout=120)
    out = {d["id"]: d for d in (json.loads(x) for x in r.stdout.splitlines() if x.strip())}
    assert out[1]["result"]["protocolVersion"] == "2025-03-26" and out[1]["result"]["serverInfo"]["name"] == "graphsnip"
    assert {t["name"] for t in out[2]["result"]["tools"]} >= {"gs_pack", "gs_find", "gs_symbol", "gs_impact", "gs_outline"}
    assert "def create_user" in out[3]["result"]["content"][0]["text"]
    assert "already returned" in out[4]["result"]["content"][0]["text"]
    assert out[5]["result"]["isError"] is True
    assert out[6]["error"]["code"] == -32601
    assert 7 not in out  # the notification got no reply


# ------------------------------------------------------------------ dashboard
def test_dashboard_renders_and_escapes(repo):
    from graphsnip.bench import run_bench

    p = Project(repo)
    (repo / "t2.json").write_text(json.dumps([{"type": "understand", "question": "how does <script>alert(1)</script> work", "expect": ["create_user"]}]))
    run_bench(str(repo), str(repo / "t2.json"))
    h = build_html(p, "graph.html")
    assert "Tokens saved" in h and "four approaches" in h and "<script>alert(1)" not in h and "&lt;script&gt;" in h
    out = write_dashboard(p, bundle=str(repo / "bundle"))
    assert out.exists() and (repo / "bundle" / "graph.html").exists()
