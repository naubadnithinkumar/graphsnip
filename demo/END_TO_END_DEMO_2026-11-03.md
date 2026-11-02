# Graphsnip end-to-end demo — 3 November 2026

## Goal and boundaries
Demonstrate Graphsnip as an agent-agnostic code-context and usage-optimization layer built on Graphify. **Do not conflate estimated retrieved-context savings with measured agent usage/cost.** No fabricated results. Use a clean public sample repository with meaningful auth/session or request-routing code, fixed commit, same prompts and model for comparisons.

## 20-minute live flow
| Time | Stage | Presenter action | Evidence | MCP tools |
|---|---|---|---|---|
| 0–2m | Setup | Show Graphify graph and Graphsnip installation; run `graphsnip doctor` | Index freshness, connected client | MCP registration (not itself a tool) |
| 2–5m | Keyword | Ask for an exact known symbol; run `graphsnip find "SYMBOL"` | Ranked symbols, lines | `gs_find`, `gs_symbol` |
| 5–8m | Semantic/hybrid | Ask same concept without symbol names; run `graphsnip embed`, `graphsnip eval --tasks demo/semantic-tasks.json` after replacing placeholders | Keyword vs hybrid hit quality | `gs_find`, `gs_pack` (hybrid is internal, not separate MCP tool) |
| 8–11m | Context | Run `graphsnip pack "TASK"`, inspect budget, repeat a related request in same agent session | Packed tokens, duplicate pointer if observed | `gs_pack`, `gs_outline`, `gs_symbol` |
| 11–14m | Change impact | Ask what breaks if a function changes; trace dependencies | Graph-grounded scope, not automatic planning | `gs_impact`, `gs_pack` |
| 14–16m | Routing | Run `graphsnip route "find a function"` and `graphsnip route "refactor auth across services"`; inspect `graphsnip usage` | Tier recommendation vs verified execution | `gs_route` |
| 16–19m | Usage/cost | Show `graphsnip bench`, `graphsnip ab --dry-run`, then pre-run measured A/B results if available | Baseline vs estimated context vs measured agent tokens/cost | `gs_savings` |
| 19–20m | Wrap | Show `graphsnip report --open`; state limits and next milestones | Dashboard and evidence | `gs_savings` |

## Preparation (run tonight)
1. Use a personal/unmanaged MCP-capable client if corporate VS Code has registry-only MCP policy. A slash command is **not** proof of MCP server availability. In Claude Code confirm server/tool discovery; `graphsnip doctor`.
2. Install project dependencies and optional semantic extra in an isolated environment; run `graphsnip init`, `graphsnip embed`, `graphsnip embed --status`.
3. Choose and pin a sample repo commit and identify exact expected symbols for at least three natural-language tasks. Replace placeholders in `demo/semantic-tasks.json` with the repository's actual evaluation schema and verified symbols. Consult `docs/semantic-tasks.example.json`.
4. Run `graphsnip eval --tasks <validated tasks file>`, `graphsnip bench`, `graphsnip report`; capture outputs. Run `graphsnip ab --dry-run` before any paid run. `graphsnip ab --yes --repeats 3` spends usage; confirm budget and permissions first.
5. For a fair comparison use fresh sessions, same model/prompt, fixed repo commit, randomized order and report medians; separate Graphify map-only baseline from a task-complete Graphify workflow that reads source.
6. Capture screenshots/terminal output as fallback. If hybrid retrieval underperforms, show it honestly and explain the next iteration.

## Seven MCP tools and their roles
- `gs_find(question,budget)`: ranked code map for keyword/semantic/hybrid lookup.
- `gs_symbol(name,max_lines,docs)`: exact function/class source.
- `gs_pack(task,budget)`: context pack under token budget; session-aware deduplication.
- `gs_impact(name,depth)`: callers/dependencies and blast radius.
- `gs_outline(path)`: symbol map for a large file.
- `gs_route(task,running_tier)`: route recommendation; **does not itself switch an agent model**.
- `gs_savings()`: savings ledger and estimates.

## Important distinctions
- Graphify supplies parsing/graph capabilities and may have its own MCP/agent features; avoid claims of exclusivity.
- Semantic search embeds symbol metadata plus the start of its body; query embedding and node vectors are matched, merged with keyword ranking via reciprocal rank fusion, then graph-expanded. Hybrid search does **not** add a new MCP tool.
- Model routing: Claude Code can delegate through configured subagents; Codex launcher can choose a model; Copilot routing is limited. Verify actual model from execution evidence; only count verified savings.
- `graphsnip bench` uses an estimated grep/read baseline; `graphsnip ab` measures headless Claude Code real reported usage for read-only Q&A, not code-change success. Never equate 'expected symbol found' with correct completed code.
- Graph-grounded multi-step **planning** is a roadmap idea unless a working implementation is separately demonstrated.
- Existing README has stale codegraf URLs and package repository metadata; fix separately before publication.

## Presenter script
“Graphify maps the code. Graphsnip chooses which code to send, packs it into a token budget, reduces repeat retrieval, and reports the effect. We test exact-symbol lookup, natural-language retrieval, dependency context, routing decisions, and measured agent usage. The most important outcome is not the biggest savings percentage: it is comparable task quality with fewer consumed resources.”

## Demo pass criteria
- MCP server listed and callable in selected client.
- Keyword and hybrid retrieval return inspectable evidence; eval task expectations documented.
- Context pack respects configured budget and returns valid code.
- Routing output is distinguishable from actual model execution.
- Token/cost numbers labeled measured or estimated; no unsupported percentages.
- A fallback recorded demo exists if live client policy, network, or model usage limits block execution.
