# Live operator run sheet — 3 Nov 2026

Run from a pinned sample repo with Graphsnip installed; replace `<...>` placeholders with verified names. These are **instructions**, not recorded results.

```bash
graphsnip doctor
graphsnip init
graphsnip embed
graphsnip embed --status
graphsnip find "<exact symbol name>"
graphsnip symbol "<exact symbol name>"
graphsnip find "<plain English behavior question>"
graphsnip pack "<same question phrased as task>"
graphsnip impact "<exact symbol name>"
graphsnip outline "<relative/path/to/file>"
graphsnip route "find where authentication is checked"
graphsnip route "refactor authentication across modules"
graphsnip usage
graphsnip eval --tasks docs/semantic-tasks.example.json
graphsnip bench
graphsnip ab --dry-run
graphsnip stats
graphsnip report --open
```

The example evaluation file may need task-specific expected symbols before it is meaningful. For actual measurements, prepare a validated evaluation dataset and check paid A/B budget before `graphsnip ab --yes --repeats 3`.

### MCP call order for a multi-module question
`gs_route` → `gs_find` → `gs_pack` → `gs_impact` → `gs_symbol` (only if necessary) → `gs_savings`. `gs_outline` is optional for large files. Tool selection is a suggested flow, **not** a claim that all clients invoke it automatically.

### Metrics slide template (fill from actual runs)
| Metric | Claude alone | Graphify alone | Graphsnip | Evidence |
|---|---|---|---|---|
| Retrieval context tokens (estimate) | TBD | TBD | TBD | bench |
| Total agent input tokens (measured) | TBD | TBD | TBD | CLI usage |
| Cache input/output tokens (measured) | TBD | TBD | TBD | CLI usage |
| Total USD (measured) | TBD | TBD | TBD | CLI usage |
| Wall time | TBD | TBD | TBD | stopwatch/logs |
| Expected symbol recall | TBD | TBD | TBD | eval |
| Tests passing on actual code change | TBD | TBD | TBD | test suite |

Note: current `graphsnip ab` supports a with/without Graphsnip comparison for read-only Claude runs; a full three-way task-complete comparison and code-change test outcome must be orchestrated separately.
