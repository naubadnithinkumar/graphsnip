"""graphsnip command line."""
from __future__ import annotations

import argparse
import importlib.metadata as md
import json
import shutil
import sys
import webbrowser

from graphsnip import __version__
from graphsnip import ledger as _ledger
from graphsnip.engine import Engine, Result
from graphsnip.project import GraphError, Project


def _common() -> argparse.ArgumentParser:
    c = argparse.ArgumentParser(add_help=False)
    c.add_argument("--root", default=".", help="project root (default: current directory)")
    return c


def _emit(r: Result, quiet: bool = False) -> int:
    print(r.text)
    if r.ok and not quiet:
        pct = f"{100 * (1 - r.returned / r.baseline):.0f}%" if r.baseline else "n/a"
        note = " (duplicate, suppressed)" if r.deduped else ""
        sys.stderr.write(f"\n[graphsnip] ~{r.returned:,} tokens returned vs ~{r.baseline:,} for grep+read → saved {pct}{note}\n")
    return 0 if r.ok else 1


def cmd_build(a) -> int:
    p = Project(a.root)
    s = p.build(force=a.force)
    print(f"Graph ready ({s['mode']}, {s['seconds']}s): {s['nodes']:,} nodes, {s['edges']:,} edges, "
          f"{s['communities']:,} communities, {s['symbols']:,} code symbols.")
    print(f"  map:    {p.graph_html}")
    print(f"  report: {p.report_md}")
    return 0


def cmd_status(a) -> int:
    p = Project(a.root)
    if not p.has_graph():
        print("No graph yet. Run `graphsnip build` or `graphsnip init`.")
        return 1
    s = p.graph_stats()
    print(f"Project: {p.root}")
    print(f"Graph:   {s['nodes']:,} nodes · {s['edges']:,} edges · {s['communities']:,} communities · {s['symbols']:,} code symbols")
    print(f"Stale:   {'yes, run `graphsnip build` (the server refreshes automatically)' if p.is_stale(0) else 'no'}")
    print(f"Mode:    {p.config()['mode']}")
    return 0


def cmd_find(a) -> int:
    return _emit(Engine(a.root, source="cli").find(a.question, budget=a.budget, depth=a.depth), a.quiet)


def cmd_symbol(a) -> int:
    return _emit(Engine(a.root, source="cli").symbol(a.name, max_lines=a.max_lines, docs="full" if a.full_docs else "short"), a.quiet)


def cmd_pack(a) -> int:
    return _emit(Engine(a.root, source="cli").pack(a.task, budget=a.budget), a.quiet)


def cmd_impact(a) -> int:
    return _emit(Engine(a.root, source="cli").impact(a.name, depth=a.depth), a.quiet)


def cmd_outline(a) -> int:
    return _emit(Engine(a.root, source="cli").outline(a.path), a.quiet)


def cmd_stats(a) -> int:
    p = Project(a.root)
    recs = _ledger.Ledger(p.gs_dir).read()
    price = float(p.config().get("price_per_mtok", 3.0))
    if a.json:
        print(json.dumps({"live": _ledger.summarize(recs, price, {"agent", "cli", "shell"}),
                          "bench": _ledger.summarize(recs, price, {"bench"})}, indent=2, default=str))
        return 0
    for title, src in (("Live usage (agents + CLI + shell)", {"agent", "cli", "shell"}), ("Benchmark", {"bench"})):
        s = _ledger.summarize(recs, price, src)
        print(f"\n{title}")
        if not s["calls"]:
            print("  nothing recorded yet")
            continue
        print(f"  {s['calls']} calls · {s['returned']:,} tokens returned vs {s['baseline']:,} baseline")
        print(f"  saved {s['saved']:,} tokens ({s['pct']}%, {s['ratio']}x) ≈ ${s['usd_saved']} at ${price:g}/M input tokens")
        for tool, t in sorted(s["by_tool"].items(), key=lambda kv: -(kv[1]["baseline"] - kv[1]["returned"])):
            print(f"    {tool:<8} {t['calls']:>4} calls  {t['returned']:>9,} vs {t['baseline']:>10,}")
        if s["hooks"]:
            print("  hooks: " + ", ".join(f"{k} ×{v}" for k, v in s["hooks"].items()))
    from graphsnip import router

    rs = router.summarize(recs, router.config(p))
    if rs["decisions"]:
        print(f"\nRouting\n  {rs['decisions']} decisions · tiers {rs['by_tier']} · status {rs['by_status']}")
        print(f"  routed (verified match): {rs['routed']} · on a cheaper tier: {rs['routed_cheaper']} ({rs['tokens_on_cheaper']:,} tokens)")
        print(f"  est. dollars saved by routing: ${rs['usd_saved']} (+ ${rs['usd_potential']} potential from advisory-only decisions)")
    return 0


def cmd_route(a) -> int:
    r = Engine(a.root, source="cli").route(a.task, client=a.client, running_tier=a.running)
    print(r.text)
    return 0


def cmd_usage(a) -> int:
    from graphsnip import router, usage

    p = Project(a.root)
    u = usage.scan(p.root, router.config(p))
    if not u:
        print(f"No Claude Code transcripts found for this project ({usage.transcripts_dir(p.root)}).")
        return 1
    print(f"Measured model mix from Claude Code transcripts ({u['dir']})")
    for name, row in sorted(u["families"].items(), key=lambda kv: -kv[1]["usd"]):
        print(f"  {name:<7} {row['messages']:>5} replies  in {row['input']:>10,}  out {row['output']:>9,}  cache-read {row['cache_read']:>11,}  ≈ ${row['usd']}")
    print(f"  total ≈ ${u['usd_total']} (estimate from routing.prices in .graphsnip/config.json)")
    return 0


def cmd_report(a) -> int:
    from graphsnip.dashboard import write_dashboard

    p = Project(a.root)
    if not p.has_graph():
        print("No graph yet. Run `graphsnip build` first.")
        return 1
    out = write_dashboard(p, bundle=a.bundle)
    print(f"Dashboard written: {out}")
    if a.bundle:
        print("The folder is self-contained (dashboard.html + graph.html): zip or share it as is.")
    if a.open:
        webbrowser.open(out.as_uri())
    return 0


def cmd_bench(a) -> int:
    from graphsnip.bench import format_table, run_bench, save_default_tasks

    p = Project(a.root)
    p.ensure_graph(True)
    if a.init_tasks or (not (p.gs_dir / "tasks.json").exists() and not a.tasks):
        path = save_default_tasks(p)
        print(f"Wrote starter tasks to {path} (edit them to use your own questions).\n")
    res = run_bench(a.root, a.tasks)
    if a.json:
        print(json.dumps(res, indent=2))
    else:
        print(format_table(res))
    return 0


def cmd_demo(a) -> int:
    from graphsnip.bench import format_table, run_bench, save_default_tasks
    from graphsnip.dashboard import write_dashboard

    p = Project(a.root)
    if not p.has_graph():
        print("Building the Graphify graph (code only, local, no LLM)…")
        s = p.build()
        print(f"  {s['nodes']:,} nodes, {s['edges']:,} edges in {s['seconds']}s\n")
    if not (p.gs_dir / "tasks.json").exists():
        save_default_tasks(p)
    print("Running benchmark…\n")
    res = run_bench(a.root)
    print(format_table(res))
    out = write_dashboard(p, bundle=a.bundle)
    print(f"\nDashboard: {out}")
    if a.open:
        webbrowser.open(out.as_uri())
    return 0


def cmd_serve(a) -> int:
    from graphsnip.mcp_server import serve

    serve(a.root)
    return 0


def cmd_hook(a) -> int:
    from graphsnip.hooks import main_stdin

    return main_stdin(a.root)


def cmd_run(a) -> int:
    from graphsnip.shellfilter import run

    argv = list(a.cmd)
    if argv and argv[0] == "--":
        argv = argv[1:]
    return run(argv, a.root)


def _clients(s: str) -> list[str]:
    return [x.strip().lower() for x in s.split(",") if x.strip()]


def cmd_install(a) -> int:
    from graphsnip.installer import install

    try:
        for ln in install(a.root, _clients(a.clients), dry_run=a.dry_run, allow_tools=a.allow_tools,
                          codex_scope=a.codex_scope, strict=a.strict, portable=a.portable):
            print(ln)
    except (ValueError, RuntimeError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    if not a.dry_run:
        print("\nRestart your agent/IDE so it picks up the MCP server. `graphsnip doctor` checks the setup.")
    return 0


def cmd_uninstall(a) -> int:
    from graphsnip.installer import uninstall

    for ln in uninstall(a.root, _clients(a.clients), a.codex_scope):
        print(ln)
    return 0


def cmd_init(a) -> int:
    from graphsnip.bench import format_table, run_bench, save_default_tasks
    from graphsnip.dashboard import write_dashboard
    from graphsnip.installer import install

    p = Project(a.root)
    if a.dry_run:
        print("1/4 [dry-run] skipping graph build")
    elif not a.no_build:
        print("1/4 Building the Graphify graph (code only, local, no LLM)…")
        s = p.build()
        print(f"    {s['nodes']:,} nodes, {s['edges']:,} edges, {s['communities']:,} communities in {s['seconds']}s")
    print("2/4 Installing agent integrations…")
    try:
        for ln in install(a.root, _clients(a.clients), dry_run=a.dry_run, allow_tools=a.allow_tools,
                          codex_scope=a.codex_scope, strict=a.strict, portable=a.portable):
            print("    " + ln)
    except (ValueError, RuntimeError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    if a.dry_run or not p.has_graph():
        return 0
    if not a.no_bench:
        print("3/4 Benchmarking on this repo…")
        save_default_tasks(p) if not (p.gs_dir / "tasks.json").exists() else None
        res = run_bench(a.root)
        print("\n" + format_table(res) + "\n")
    print("4/4 Writing the dashboard…")
    out = write_dashboard(p)
    print(f"    {out}")
    print("\nDone. Restart your agent/IDE, then ask it a code question. `graphsnip report --open` shows the savings.")
    return 0


def cmd_doctor(a) -> int:
    from graphsnip.installer import CLIENTS, HOOK_MARK

    p = Project(a.root)
    ok = True

    def line(good: bool, msg: str, fix: str = ""):
        nonlocal ok
        ok &= good
        print(f"{'✓' if good else '✗'} {msg}" + (f"  → {fix}" if (fix and not good) else ""))

    line(sys.version_info >= (3, 10), f"Python {sys.version.split()[0]}", "needs Python 3.10+")
    try:
        gv = md.version("graphifyy")
        line(True, f"Graphify {gv}")
    except Exception:
        line(False, "Graphify not installed", "pip install graphifyy")
    try:
        import tree_sitter  # noqa: F401
        line(True, "tree-sitter available (exact symbol slicing)")
    except Exception:
        line(False, "tree-sitter missing; slicing falls back to heuristics", "pip install graphifyy")
    exe = shutil.which("graphsnip")
    line(bool(exe), f"`graphsnip` on PATH ({exe})" if exe else "`graphsnip` not on PATH (configs will use `python -m graphsnip`)", "pipx install graphsnip")
    line(p.has_graph(), f"graph present ({p.graph_json})" if p.has_graph() else "no graph yet", "graphsnip build")
    if p.has_graph():
        line(not p.is_stale(0), "graph is up to date" if not p.is_stale(0) else "graph is older than some source files", "graphsnip build (the server also refreshes itself)")
        s = p.graph_stats()
        line(s["symbols"] > 0, f"{s['symbols']:,} code symbols indexed")
    mcp_claude = (p.root / ".mcp.json")
    st = p.root / ".claude" / "settings.json"
    found = {
        "claude": mcp_claude.exists() and "graphsnip" in mcp_claude.read_text(encoding="utf-8", errors="ignore"),
        "copilot": (p.root / ".vscode" / "mcp.json").exists() and "graphsnip" in (p.root / ".vscode" / "mcp.json").read_text(encoding="utf-8", errors="ignore"),
        "codex": (p.root / ".codex" / "config.toml").exists() and "graphsnip" in (p.root / ".codex" / "config.toml").read_text(encoding="utf-8", errors="ignore"),
    }
    for c in CLIENTS:
        print(f"{'✓' if found[c] else '·'} {c}: {'MCP server registered' if found[c] else 'not installed here (graphsnip install --clients ' + c + ')'}")
    hook_on = st.exists() and HOOK_MARK in st.read_text(encoding="utf-8", errors="ignore")
    print(f"{'✓' if hook_on else '·'} claude hook: {'installed' if hook_on else 'not installed'}  (mode: {p.config()['mode']})")
    print("\nAll good." if ok else "\nSome checks failed; see arrows above.")
    return 0 if ok else 1


def main(argv=None) -> int:
    common = _common()
    ap = argparse.ArgumentParser(prog="graphsnip", description="Token-saving layer on top of Graphify.")
    ap.add_argument("--version", action="version", version=f"graphsnip {__version__}")
    sub = ap.add_subparsers(dest="cmd")

    def add(name, fn, help_, **kw):
        sp = sub.add_parser(name, parents=[common], help=help_, **kw)
        sp.set_defaults(fn=fn)
        return sp

    sp = add("init", cmd_init, "build graph + install integrations + benchmark + dashboard")
    sp.add_argument("--clients", default="all", help="claude,copilot,codex or all (default: all)")
    sp.add_argument("--dry-run", action="store_true")
    sp.add_argument("--allow-tools", action="store_true", help="Claude Code: pre-approve the read-only graphsnip tools")
    sp.add_argument("--strict", action="store_true", help="hooks deny redirectable calls instead of nudging")
    sp.add_argument("--portable", action="store_true", help="write bare `graphsnip` into configs (safe to commit) instead of this machine's path")
    sp.add_argument("--codex-scope", choices=["project", "user"], default="project")
    sp.add_argument("--no-build", action="store_true")
    sp.add_argument("--no-bench", action="store_true")

    sp = add("build", cmd_build, "build or refresh the Graphify graph (code only, no LLM)")
    sp.add_argument("--force", action="store_true")
    add("status", cmd_status, "show graph status")
    sp = add("find", cmd_find, "locate symbols and connections (budgeted map)")
    sp.add_argument("question")
    sp.add_argument("--budget", type=int, default=1500)
    sp.add_argument("--depth", type=int, default=2)
    sp.add_argument("--quiet", action="store_true")
    sp = add("symbol", cmd_symbol, "read one function/class with callers and callees")
    sp.add_argument("name")
    sp.add_argument("--max-lines", type=int, default=120)
    sp.add_argument("--full-docs", action="store_true")
    sp.add_argument("--quiet", action="store_true")
    sp = add("pack", cmd_pack, "token-budgeted context pack for a task")
    sp.add_argument("task")
    sp.add_argument("--budget", type=int, default=4000)
    sp.add_argument("--quiet", action="store_true")
    sp = add("impact", cmd_impact, "what depends on a symbol")
    sp.add_argument("name")
    sp.add_argument("--depth", type=int, default=2)
    sp.add_argument("--quiet", action="store_true")
    sp = add("outline", cmd_outline, "symbols in a file with line numbers")
    sp.add_argument("path")
    sp.add_argument("--quiet", action="store_true")
    sp = add("stats", cmd_stats, "token savings so far")
    sp.add_argument("--json", action="store_true")
    sp = add("route", cmd_route, "which model tier does this task need? (logs the decision)")
    sp.add_argument("task")
    sp.add_argument("--client", default="claude", choices=["claude", "copilot", "codex"])
    sp.add_argument("--running", choices=["light", "standard", "heavy", "session"], help="tier you are actually running on, if known")
    sp = add("usage", cmd_usage, "measured model mix from Claude Code transcripts")
    sp = add("report", cmd_report, "write the HTML dashboard")
    sp.add_argument("--open", action="store_true")
    sp.add_argument("--bundle", metavar="DIR", help="write a self-contained folder (dashboard + graph.html) for sharing")
    sp = add("bench", cmd_bench, "offline benchmark: grep+read vs Graphify vs graphsnip")
    sp.add_argument("--tasks", help="tasks JSON (default .graphsnip/tasks.json, auto-generated)")
    sp.add_argument("--init-tasks", action="store_true", help="(re)write starter tasks")
    sp.add_argument("--json", action="store_true")
    sp = add("demo", cmd_demo, "one command for a live demo: graph + benchmark + dashboard")
    sp.add_argument("--open", action="store_true")
    sp.add_argument("--bundle", metavar="DIR")
    add("serve", cmd_serve, "run the MCP server over stdio")
    sp = add("hook", cmd_hook, "agent hook entry point (reads JSON on stdin)")
    sp.add_argument("event", choices=["pretooluse"])
    sp = add("run", cmd_run, "run a command and compress its output (exact token savings)")
    sp.add_argument("cmd", nargs=argparse.REMAINDER)
    sp = add("install", cmd_install, "write MCP + hooks + guidance for Claude Code / Copilot / Codex")
    sp.add_argument("--clients", default="all")
    sp.add_argument("--dry-run", action="store_true")
    sp.add_argument("--allow-tools", action="store_true")
    sp.add_argument("--strict", action="store_true")
    sp.add_argument("--portable", action="store_true")
    sp.add_argument("--codex-scope", choices=["project", "user"], default="project")
    sp = add("uninstall", cmd_uninstall, "remove everything install added")
    sp.add_argument("--clients", default="all")
    sp.add_argument("--codex-scope", choices=["project", "user"], default="project")
    add("doctor", cmd_doctor, "check the setup")

    args = ap.parse_args(argv)
    if not getattr(args, "fn", None):
        ap.print_help()
        return 0
    try:
        return int(args.fn(args) or 0)
    except GraphError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    except BrokenPipeError:
        return 0
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
