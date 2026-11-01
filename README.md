# code-graf

**A token-saving layer on top of [Graphify](https://github.com/Graphify-Labs/graphify).**

Graphify turns a codebase into a knowledge graph (tree-sitter, no LLM, runs locally). code-graf uses that graph to give coding agents *exactly the code they need* instead of whole files, and then **proves the savings** with an honest baseline and a dashboard you can put on a projector.

Works with **Claude Code**, **GitHub Copilot** (VS Code / CLI) and **Codex**.

```
        your repo ──► Graphify (AST → graph.json, graph.html, communities)
                          │
                          ▼
   ┌───────────────────── code-graf ─────────────────────┐
   │  symbol slicer  ·  context packs  ·  session dedupe  │
   │  budgeted tools ·  hooks          ·  shell compressor│
   │  savings ledger (honest baseline) ·  dashboard       │
   └──────────────┬───────────────────────────┬──────────┘
                  ▼                           ▼
     MCP server (cg_pack, cg_symbol, …)    .code-graf/dashboard.html
     Claude Code · Copilot · Codex         (+ Graphify's graph.html embedded)
```

## Demo in three commands

```bash
pipx install code-graf          # or: uvx code-graf …   (see "Install")
cd your-repo
code-graf demo --open           # graph → benchmark → dashboard
```

Then wire it into your agents and watch live savings land in the same dashboard:

```bash
code-graf init                  # graph + MCP + hooks + guidance for claude, copilot, codex
code-graf report --open         # refresh the dashboard from real agent usage
```

## What Graphify gives you vs. what code-graf adds

| | Graphify | code-graf |
|---|---|---|
| Parse code → knowledge graph, communities, `graph.html` | ✅ | uses it, embeds it |
| `query` / `path` / `explain` / `affected` | ✅ (maps, no source code) | wraps them, adds the code |
| Returns **exact source of one symbol** (not the file) | – | `cg_symbol` (tree-sitter ranges, docstring trimming) |
| One-call **context pack** for a task, under a token budget | – | `cg_pack` (graph-ranked, class skeletons, signatures) |
| Suppresses re-sending code the agent already has | – | per-session dedupe |
| Redirects broad `Read` / `grep` to the graph when it can answer | partial | PreToolUse hook with nudge / strict modes |
| Compresses noisy test/build output | – | `code-graf run -- <cmd>` (exact savings) |
| **Measures** savings against a realistic baseline | corpus-vs-query only | per-call ledger, benchmark, dashboard |
| Installs for Claude Code, Copilot, Codex | skills / rules | MCP + hooks + guidance + subagent, idempotent and reversible |

## Measured results

Same eight questions per repo, priced as tokens an agent would put in context.
Baseline = *grep the question's terms, read the top 8 matching files in full* (what an agent does with no graph).
"Graphify + read" = Graphify `query`, then reading the whole files its top hits point to.
Token counts use the chars/4 estimate (install `code-graf[accurate]` for tiktoken).

| Repo | grep + read | Graphify + read | code-graf | vs grep + read | vs Graphify + read | answered with code |
|---|---:|---:|---:|---:|---:|---:|
| Flask (Python, 1,500 symbols) | 366,565 | 164,491 | 17,869 | **95.1%** | 89.1% | 8/8 |
| Express (JavaScript) | 165,832 | 58,553 | 8,955 | **94.6%** | 84.7% | 8/8 |

Graphify's bare `query` is cheaper still (about 13k and 18k tokens across the eight tasks) but returns a map with **no source code**, so the agent still has to read files afterwards. code-graf returns the map *and* the code in the same step. A task counts as answered only if the expected symbol appears with its source.

Reproduce: `code-graf bench` (tasks live in `.code-graf/tasks.json`; replace them with your own questions).

## Install

```bash
pipx install code-graf            # recommended
# or
uvx code-graf init                # run without installing
# or
npx code-graf init                # thin wrapper, needs Python 3.10+ and pipx/uv
```

Requires Python 3.10+. Graphify is installed as a dependency (`graphifyy`).

## Commands

| Command | What it does |
|---|---|
| `code-graf init` | build graph, install integrations, benchmark, write dashboard (`--dry-run` previews, writes nothing) |
| `code-graf demo [--open] [--bundle DIR]` | graph + benchmark + dashboard in one go; `--bundle` makes a shareable folder |
| `code-graf build [--force]` | build or refresh the Graphify graph (code only, no LLM) |
| `code-graf pack "task"` | token-budgeted bundle of the code relevant to a task |
| `code-graf find "question"` | budgeted map: where things live, how they connect |
| `code-graf symbol Name` | one function/class: exact lines + callers + callees |
| `code-graf impact Name` | what depends on a symbol |
| `code-graf outline path` | symbols in a file with line numbers |
| `code-graf run -- <cmd>` | run a command, compress its output, keep the exit code |
| `code-graf stats` / `report` | savings in the terminal / the HTML dashboard |
| `code-graf bench` | offline benchmark: grep+read vs Graphify vs code-graf |
| `code-graf install` / `uninstall` | per-client config (`--clients claude,copilot,codex`) |
| `code-graf doctor` | check Python, Graphify, tree-sitter, graph freshness, client configs |

Every query command prints `~N tokens returned vs ~M for grep+read → saved P%` to stderr. That is the number to show live.

## MCP tools

| Tool | Use it for |
|---|---|
| `cg_pack(task, budget)` | first call for "how does X work" or before a change |
| `cg_find(question, budget)` | locate things (map only) |
| `cg_symbol(name, max_lines, docs)` | read one definition instead of the file |
| `cg_impact(name, depth)` | blast radius before changing a signature |
| `cg_outline(path)` | symbols of a big file before opening it |
| `cg_savings()` | tokens saved so far |

The server is a small dependency-free stdio JSON-RPC implementation (it does not depend on the MCP SDK's changing API). It auto-refreshes a stale graph and records every call in `.code-graf/ledger.jsonl`.

## What `install` writes

Everything is idempotent (run it twice, nothing changes) and reversible (`code-graf uninstall`). Your existing content is preserved: text files get a marked block, JSON is merged.

| Client | Files |
|---|---|
| Claude Code | `.mcp.json`, `.claude/settings.json` (PreToolUse hook), `CLAUDE.md` block, `.claude/agents/code-graf-explorer.md` (read-only Haiku subagent) |
| Copilot (VS Code) | `.vscode/mcp.json`, `.github/copilot-instructions.md` block |
| Codex | `.codex/config.toml` (or `~/.codex/config.toml` with `--codex-scope user`), `AGENTS.md` block |

Flags: `--allow-tools` pre-approves the read-only tools in Claude Code, `--portable` writes bare `code-graf` instead of this machine's absolute path (use it before committing the configs), `--strict` sets hook mode to strict.

## Hooks (Claude Code)

Fail-open: any error means "do nothing". Configure in `.code-graf/config.json` (or `CODEGRAF_MODE`).

| Mode | Behaviour |
|---|---|
| `nudge` (default) | adds context: the file's outline for big reads, `cg_symbol("X")` when a grep pattern is a known symbol. Blocks nothing |
| `strict` | denies those same calls with a reason that tells the agent what to call. Literal-string greps and reads with `offset`/`limit` are never blocked |
| `off` | nothing |

`shell_filter: true` additionally rewrites noisy test/build commands to run through `code-graf run`. It is **off by default** because the rewrite returns an `allow` decision, which skips the permission prompt; it is limited to an allow-list and never touches pipelines or chained commands. POSIX shells only.

## How the numbers are measured (and what they are not)

- **Baseline per call.** `find`/`pack`: grep the question's terms, read the top 8 matching files in full. `symbol`: read the whole file. `impact`: grep the name, read matching files (cap 10). Deliberately far below "read the whole repo", the figure Graphify's own `benchmark` compares against.
- **Counted once.** In a real session, tokens that stay in context are re-sent every turn, so true savings compound. The dashboard does not claim that.
- **Shell savings are exact** (raw vs compressed output), not estimated.
- **Quality is checked**, not assumed (see `answered` above).
- **Cost** is `tokens saved × price per million input tokens` (default $3, set `price_per_mtok` in the config). It ignores caching discounts and output tokens.

## Known limits

- Hooks and the three client configs follow each tool's documented formats, but only the MCP server was verified against a real client in development (the official MCP Python client). Run `code-graf doctor` and a short session in each client before relying on them.
- Symbol slicing is exact for the ~25 languages with tree-sitter grammars installed; elsewhere it falls back to indentation / brace matching.
- The graph only covers what Graphify extracts from code. Docs, PDFs and images need Graphify's semantic pass (needs an LLM key) and are not part of code-graf's tools.
- Graphify's `graph.html` loads vis-network from a CDN; code-graf caches it locally so the dashboard works offline (needs internet once).

## Development

```bash
pip install -e ".[dev]"
pytest -q
```

## Publishing

```bash
python -m build && twine upload dist/*          # PyPI (name `code-graf`)
cd npm && npm publish                           # npm wrapper (name `code-graf`)
```

## License

Apache-2.0. code-graf is a separate project that calls Graphify (also Apache-2.0) as a library; see `NOTICE`.
