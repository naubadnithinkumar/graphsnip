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


def test_slash_commands_installed_and_removed(tmp_path, monkeypatch):
    codex_home = tmp_path / "chome"
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    repo = tmp_path / "repo"
    repo.mkdir()
    installer.install(str(repo), ["all"])
    claude = (repo / ".claude" / "commands" / "graphsnip.md").read_text()
    copilot = (repo / ".github" / "prompts" / "graphsnip.prompt.md").read_text()
    codex = (codex_home / "prompts" / "graphsnip.md").read_text()
    assert "$ARGUMENTS" in claude and "mcp__graphsnip__gs_pack" in claude and "cg_" not in claude
    assert "model: haiku" in claude and "ESCALATE" in claude
    assert "subagent_type `graphsnip-<tier>`" in claude and "Escalating to" in claude
    assert "model: sonnet" in (repo / ".claude" / "agents" / "graphsnip-standard.md").read_text()
    assert "model: opus" in (repo / ".claude" / "agents" / "graphsnip-heavy.md").read_text()
    assert "/graphsnip-deep" in copilot  # copilot cannot delegate, so it keeps the manual hand-off
    deep = (repo / ".claude" / "commands" / "graphsnip-deep.md").read_text()
    assert "model:" not in deep and 'running_tier = "session"' in deep
    assert "name: graphsnip" in copilot and "graphsnip/*" in copilot
    assert "$ARGUMENTS" in codex and codex.startswith("---")
    assert "mcp__graphsnip__gs_pack" in (repo / ".claude" / "agents" / "graphsnip-explorer.md").read_text()
    installer.uninstall(str(repo), ["all"])
    assert not (repo / ".claude" / "commands" / "graphsnip.md").exists()
    assert not (repo / ".claude" / "agents" / "graphsnip-standard.md").exists()
    assert not (repo / ".github" / "prompts" / "graphsnip.prompt.md").exists()
    assert not (codex_home / "prompts" / "graphsnip.md").exists()


def test_slash_command_never_clobbers_foreign_file(tmp_path):
    f = tmp_path / ".claude" / "commands" / "graphsnip.md"
    f.parent.mkdir(parents=True)
    f.write_text("my own command")
    try:
        installer.install(str(tmp_path), ["claude"])
        raised = False
    except RuntimeError:
        raised = True
    assert raised and f.read_text() == "my own command"


def test_router_decisions_and_accounting():
    from graphsnip import router

    cfg = router.DEFAULTS
    assert router.decide("where is dispatch_request defined", cfg=cfg).tier == "light"
    assert router.decide("refactor the auth module and migrate sessions", pack_files=7, pack_tokens=3500, cfg=cfg).tier == "heavy"
    assert router.status_of("light", "light") == "routed"
    assert router.status_of("heavy", "light") == "escalate"
    assert router.status_of("light", None) == "advisory"
    r = router.decide("where is x defined", cfg=cfg)
    routed = router.account(cfg, r, "routed")
    assert routed["usd_saved"] > 0 and router.account(cfg, r, "advisory")["usd_saved"] == 0
    assert router.account(cfg, r, "escalate")["usd_saved"] == 0  # no credit for work that needed a stronger model


def test_usage_scan_reads_transcripts(tmp_path):
    import json as _j
    from graphsnip import router, usage

    d = tmp_path / "t"
    d.mkdir()
    rows = [{"requestId": "r1", "message": {"id": "m1", "model": "claude-haiku-x", "usage": {"input_tokens": 1000, "output_tokens": 200}}},
            {"requestId": "r1", "message": {"id": "m1", "model": "claude-haiku-x", "usage": {"input_tokens": 1000, "output_tokens": 200}}},  # duplicate
            {"requestId": "r2", "message": {"id": "m2", "model": "claude-opus-x", "usage": {"input_tokens": 500, "output_tokens": 100, "cache_read_input_tokens": 9000}}}]
    (d / "s.jsonl").write_text("\n".join(_j.dumps(x) for x in rows))
    u = usage.scan(tmp_path, router.DEFAULTS, directory=d)
    assert u["families"]["haiku"]["messages"] == 1 and u["families"]["opus"]["cache_read"] == 9000
    assert u["usd_total"] > 0


def test_codex_launcher_switches_model_and_logs(tmp_path, monkeypatch, capsys):
    import json as _j, stat, sys as _s
    from graphsnip import cli, ledger, router
    from graphsnip.project import Project

    (tmp_path / "a.py").write_text("def hello():\n    return 1\n")
    (tmp_path / ".graphsnip").mkdir()
    (tmp_path / ".graphsnip" / "config.json").write_text(_j.dumps({"routing": {"models": {"codex": {"light": "mini-x", "heavy": "big-x"}}}}))
    bindir = tmp_path / "bin"
    bindir.mkdir()
    fake = bindir / "codex"
    fake.write_text("#!/bin/sh\necho \"ARGS: $@\" > \"$FAKE_OUT\"\n")
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("PATH", f"{bindir}:" + __import__("os").environ["PATH"])
    monkeypatch.setenv("FAKE_OUT", str(tmp_path / "out.txt"))
    rc = cli.main(["codex", "--root", str(tmp_path), "where is hello defined"])
    assert rc == 0
    assert "-m mini-x" in (tmp_path / "out.txt").read_text()
    recs = ledger.Ledger(Project(str(tmp_path)).gs_dir).read()
    r = [x for x in recs if x.get("kind") == "route"][-1]
    assert r["client"] == "codex" and r["status"] == "routed" and r["tier"] == "light"
    cfg = router.config(Project(str(tmp_path)))
    ev = router.events(recs, cfg)
    assert "graphsnip launched it" in ev[0]["path"]


def test_events_describe_escalation_and_handoff():
    from graphsnip import router

    cfg = router.DEFAULTS
    recs = [
        {"kind": "route", "ts": "2026-11-01T10:00:00", "client": "claude", "query": "refactor x", "tier": "heavy", "status": "escalate", "running": "light", "usd_saved": 0},
        {"kind": "route", "ts": "2026-11-01T10:00:05", "client": "claude", "query": "refactor x", "tier": "heavy", "status": "routed", "running": "heavy", "handoff": True, "usd_saved": -0.002},
    ]
    ev = router.events(recs, cfg)
    assert len(ev) == 1 and "handed off, finished on heavy (opus)" in ev[0]["path"] and ev[0]["saved"] < 0
    assert router.summarize(recs, cfg)["decisions"] == 1


def _fake_claude(cost_ctl=0.30, cost_gs=0.06):
    import json as _j

    def runner(cmd, cwd, timeout):
        treat = any("mcp__graphsnip" in c for c in cmd)
        u = {"input_tokens": 1000, "cache_creation_input_tokens": 2000, "cache_read_input_tokens": 3000 if treat else 90000, "output_tokens": 400}
        obj = {"type": "result", "subtype": "success", "is_error": False, "total_cost_usd": cost_gs if treat else cost_ctl,
               "num_turns": 3 if treat else 9, "duration_ms": 1000, "usage": u, "result": "It lives in hello() at a.py:1", "modelUsage": {"m": {}}}
        return 0, _j.dumps(obj if not treat else [{"type": "system"}, obj]), ""   # both json shapes

    return runner


def test_ab_runs_both_arms_and_measures(tmp_path):
    from graphsnip import ab

    (tmp_path / "a.py").write_text("def hello():\n    return 1\n\ndef main():\n    return hello()\n")
    (tmp_path / "CLAUDE.md").write_text("# mine\n")
    (tmp_path / ".graphsnip").mkdir()
    (tmp_path / ".graphsnip" / "tasks.json").write_text('{"tasks":[{"id":"t1","type":"locate","question":"where is hello defined","expect":["hello"]}]}')
    res = ab.run_ab(str(tmp_path), repeats=2, runner=_fake_claude(), log=lambda s: None)
    t = res["totals"]
    assert res["paired_tasks"] == 1 and t["cost_saved"] > 0 and abs(t["cost_saved_pct"] - 80.0) < 0.1
    assert t["context_saved"] > 0 and t["treat_hits"] == 2 and t["control_hits"] == 2
    assert (tmp_path / ".graphsnip" / "ab.json").exists()
    assert len(list((tmp_path / ".graphsnip" / "ab").glob("*/*.md"))) == 4
    assert (tmp_path / "CLAUDE.md").read_text() == "# mine\n"   # the user's repo is never touched
    assert "MEASURED" in ab.format_table(res)


def test_ab_cost_cap_and_dry_run_and_parse(tmp_path):
    from graphsnip import ab

    (tmp_path / "a.py").write_text("def hello():\n    return 1\n")
    (tmp_path / ".graphsnip").mkdir()
    (tmp_path / ".graphsnip" / "tasks.json").write_text('{"tasks":[{"id":"t1","question":"where is hello","expect":["hello"]}]}')
    res = ab.run_ab(str(tmp_path), repeats=5, max_cost=0.5, runner=_fake_claude(cost_ctl=0.4, cost_gs=0.4), log=lambda s: None)
    assert res["aborted"] and "cost cap" in res["aborted"]
    d = ab.run_ab(str(tmp_path), repeats=3, dry_run=True)
    assert d["runs"] == 6 and "--strict-mcp-config" in d["example"] and "--max-budget-usd" in d["example"]
    assert ab.parse_result("not json") is None and ab.parse_result('{"type":"result","usage":{"output_tokens":5},"result":"x"}')["output"] == 5


def test_dashboard_shows_measured_ab(tmp_path):
    from graphsnip import ab, dashboard
    from graphsnip.project import Project

    (tmp_path / "a.py").write_text("def hello():\n    return 1\n")
    (tmp_path / ".graphsnip").mkdir()
    (tmp_path / ".graphsnip" / "tasks.json").write_text('{"tasks":[{"id":"t1","question":"where is hello","expect":["hello"]}]}')
    ab.run_ab(str(tmp_path), repeats=1, runner=_fake_claude(), log=lambda s: None)
    html = dashboard.build_html(Project(str(tmp_path)), "x.html")
    assert "Measured A/B (real runs)" in html and "Measured cost saved" in html
