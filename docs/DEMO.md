# Demo script (about 8 minutes)

## Before you present (10 minutes, on your own laptop)

1. `pipx install graphsnip` (or `pip install -e .` from this repo).
2. In **agentlyra** (and again in **agentichr**): `graphsnip doctor`, then `graphsnip demo --bundle ~/demo/agentlyra`.
   - Open `~/demo/agentlyra/dashboard.html`. Check the numbers look plausible and the graph renders.
   - Edit `.graphsnip/tasks.json` to 8 questions you would really ask, then `graphsnip bench` and `graphsnip report --bundle ~/demo/agentlyra` again. Real questions make the demo credible.
3. Wire one agent: `graphsnip init --clients claude --allow-tools`, restart Claude Code, ask one code question, confirm `gs_pack` shows up in the tool calls. Then `graphsnip report --open`.
4. Keep `~/demo/<repo>/` open in a browser tab as a fallback if the live agent misbehaves.

## Talk track

1. **The problem (1 min).** Agents burn tokens finding code: grep, then read whole files. Graphify already fixes the *finding* by building a knowledge graph.
2. **What Graphify alone leaves on the table (1 min).** Run `graphify query "<a real question>"`: it returns a map, no code. The agent still reads files afterwards.
3. **graphsnip (3 min).** Run, with `2>&1` visible so the stderr line shows:
   - `graphsnip pack "how does <X> work"` → exact code, ranked, under a budget. Point at the last line: `~3,500 tokens returned vs ~50,000 for grep+read`.
   - `graphsnip symbol <Name>`, then the same command again → second call is a one-line pointer.
   - `graphsnip impact <Name>` → blast radius.
   - `graphsnip run -- <your test command>` → exact before/after token numbers.
4. **Proof (2 min).** Open the dashboard: the four-approach bar chart (grep+read / Graphify+read / Graphify alone / graphsnip), the per-task table with the ✓ column, then scroll to the embedded Graphify graph and click a community.
5. **Ship story (1 min).** One command per client (`graphsnip init`), idempotent and reversible (`uninstall`), nothing committed unless you choose `--portable`.

## Say this, not that

- Say "**about 95% fewer tokens than grep-and-read, 85–89% fewer than Graphify plus reading the files**, with the expected code present in every benchmark task." (Use your own repos' numbers once you have run them.)
- Don't say "95% cheaper bills." Savings are counted once per call; real sessions compound, but the dashboard doesn't claim that.
- Say "Graphify's query is cheaper but returns no code." Don't say Graphify is wasteful: graphsnip is built on its graph.
- If asked how the baseline is defined: "grep the terms, read the top 8 matching files in full," and show the methodology section on the dashboard.

## If something breaks live

- Graph blank in the dashboard → open `graph.html` from the same folder (needs the sibling `vis-network.min.js`).
- Agent doesn't call the tools → `graphsnip doctor`; restart the agent; the CLI commands above work without any agent.
- Hook too aggressive → `GRAPHSNIP_MODE=off` or `"mode": "nudge"` in `.graphsnip/config.json`.


## Add-ons for the demo

- **Interactive explorer** (`docs/explorer.html`): open it in a browser, toggle the layers (no graph, Graphify alone, Graphify + read, graphsnip) and move the sliders to show monthly cost for the audience's own volume. The "re-sent" slider is the compounding effect; leave it at 1 unless you say so out loud.
- **Dashboard tabs**: *Per call* (log scale, multiplier on every pair) is the best opening chart; *Efficiency vs accuracy* answers "does it get worse answers?" (same symbols found, a fraction of the tokens).
- **Routing**: say "routing saves dollars per token, not tokens". Show the task-by-task log; do not quote a routing dollar figure unless it comes from real `/graphsnip` runs.
