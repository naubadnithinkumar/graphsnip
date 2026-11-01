"""Engine: the tools an agent (or a human) calls. Every call is priced against
the honest baseline and written to the ledger."""
from __future__ import annotations

import hashlib
import math
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from code_graf import ledger as _ledger
from code_graf.baseline import Baseline, query_terms
from code_graf.project import CODE_EXTS, GraphError, Project, parse_line
from code_graf.slicer import shorten_docstring, signature, slice_symbol
from code_graf.tokens import count_tokens

_FENCE = {".py": "python", ".pyi": "python", ".js": "javascript", ".jsx": "jsx", ".mjs": "javascript",
          ".ts": "typescript", ".tsx": "tsx", ".go": "go", ".rs": "rust", ".java": "java", ".c": "c",
          ".h": "c", ".cpp": "cpp", ".cc": "cpp", ".hpp": "cpp", ".rb": "ruby", ".cs": "csharp",
          ".kt": "kotlin", ".php": "php", ".swift": "swift", ".lua": "lua", ".sh": "bash", ".vue": "vue",
          ".svelte": "svelte"}
_OUT_REL = ("calls", "indirect_call", "uses", "inherits", "extends", "implements")
_IN_REL = ("calls", "indirect_call", "references", "uses")
_REL_W = {"calls": 0.6, "indirect_call": 0.4, "method": 0.7, "inherits": 0.6, "extends": 0.6,
          "implements": 0.5, "uses": 0.4, "references": 0.3, "contains": 0.25, "imports_from": 0.15}


@dataclass
class Result:
    text: str
    returned: int
    baseline: int = 0
    files: list = field(default_factory=list)
    ok: bool = True
    deduped: bool = False

    @property
    def saved(self) -> int:
        return self.baseline - self.returned


class Engine:
    def __init__(self, root=".", session: str | None = None, source: str = "cli", auto_refresh: bool = True):
        self.p = Project(root)
        self.ledger = _ledger.Ledger(self.p.cg_dir)
        self.base = Baseline(self.p)
        self.session = session or uuid.uuid4().hex[:8]
        self.source = source
        self.auto_refresh = auto_refresh
        self._seen: dict[tuple, str] = {}

    # ------------------------------------------------------------ plumbing
    def _graph(self):
        self.p.ensure_graph(self.auto_refresh)
        return self.p.graph()

    def _finish(self, tool: str, query: str, text: str, baseline: int, files: list[str],
                ok: bool = True, deduped: bool = False) -> Result:
        ret = count_tokens(text)
        if ok:
            self.ledger.record(kind="call", tool=tool, query=query[:200], returned=ret, baseline=baseline,
                               files=files[:12], source=self.source, session=self.session, deduped=deduped)
        return Result(text=text, returned=ret, baseline=baseline, files=files, ok=ok, deduped=deduped)

    def _fail(self, text: str) -> Result:
        return Result(text=text, returned=count_tokens(text), ok=False)

    def _src(self, rel: str, cache: dict) -> str:
        if rel not in cache:
            cache[rel] = self.p.read(rel) or ""
        return cache[rel]

    def _display(self, G, nid: str) -> str:
        return self.p.display(nid)

    def _kind(self, d: dict) -> str:
        return "class" if d.get("_callable_class") else "function"

    # ------------------------------------------------------------ find
    def find(self, question: str, budget: int = 1500, depth: int = 2) -> Result:
        try:
            G = self._graph()
            from graphify.serve import _query_graph_text

            text = _query_graph_text(G, question, mode="bfs", depth=depth, token_budget=budget,
                                     graph_path=str(self.p.graph_json))
        except GraphError as e:
            return self._fail(str(e))
        except Exception as e:  # graphify internals can change between versions
            return self._fail(f"find failed: {type(e).__name__}: {e}")
        if text.startswith("No matching nodes"):
            return self._fail(text + " Try other keywords, or fall back to grep.")
        text = text.rstrip() + "\n\n→ cg_symbol(name) returns exact code; cg_pack(task) returns a ready code bundle."
        base, files = self.base.grep_read(query_terms(question))
        return self._finish("find", question, text, base, files)

    # ------------------------------------------------------------ symbol
    def _neighbors(self, G, nid: str) -> tuple[list[str], list[str], list[str]]:
        def lab(n):
            return str(G.nodes[n].get("label", n))

        outs, ins, members = [], [], []
        try:
            for _u, v, d in G.out_edges(nid, data=True):
                r = d.get("relation")
                if r in _OUT_REL:
                    outs.append(lab(v))
                elif r == "method":
                    members.append(lab(v))
            for u, _v, d in G.in_edges(nid, data=True):
                if d.get("relation") in _IN_REL:
                    ins.append(lab(u))
        except Exception:
            pass

        def uniq(xs):
            seen, out = set(), []
            for x in xs:
                if x not in seen and x:
                    seen.add(x)
                    out.append(x)
            return out

        return uniq(outs), uniq(ins), uniq(members)

    @staticmethod
    def _short(names: list[str], cap: int) -> str:
        if not names:
            return "-"
        shown = ", ".join(names[:cap])
        return shown + (f" (+{len(names) - cap})" if len(names) > cap else "")

    def symbol(self, name: str, max_lines: int = 120, docs: str = "short") -> Result:
        try:
            G = self._graph()
            from graphify.serve import _resolve_single_node

            nid, err = _resolve_single_node(G, name)
        except GraphError as e:
            return self._fail(str(e))
        except Exception as e:
            return self._fail(f"symbol lookup failed: {type(e).__name__}: {e}")
        if err:
            return self._fail(err)
        d = G.nodes[nid]
        rel = str(d.get("source_file", ""))
        if self.p.is_file_node(d):
            r = self.outline(rel)
            r.text = f"('{name}' is a file; showing its outline.)\n" + r.text
            return r
        line = parse_line(d.get("source_location")) or 1
        src = self.p.read(rel)
        if src is None:
            return self._fail(f"Could not read {rel}.")
        suffix = Path(rel).suffix
        sl = slice_symbol(src, suffix, line, max_lines=max_lines)
        outs, ins, members = self._neighbors(G, nid)
        label = self._display(G, nid)
        shown_text, omitted = (shorten_docstring(sl.text, suffix) if docs != "full" else (sl.text, 0))
        head = f"## {label} ({self._kind(d)}) — {rel}:{sl.start}-{sl.end}"
        if sl.truncated:
            head += f"  [showing {max_lines} of {sl.total_lines} lines]"
        lang = _FENCE.get(suffix.lower(), "")
        body = f"```{lang}\n{shown_text}\n```"
        nav = []
        if members:
            nav.append(f"methods: {self._short(members, 12)}")
        nav.append(f"calls/uses: {self._short(outs, 8)}")
        nav.append(f"used by: {self._short(ins, 8)}")
        if sl.truncated:
            nav.append(f"… +{sl.total_lines - max_lines} more lines — call again with max_lines={sl.total_lines}")
        full = "\n".join([head, body, *nav])

        file_tokens = self.base.file_tokens(rel)  # what Read(file) would have cost
        key = (rel, sl.start, sl.end)
        digest = hashlib.sha1(shown_text.encode("utf-8", "replace")).hexdigest()  # what the agent was actually shown
        if self._seen.get(key) == digest:
            short = (f"✓ {label} ({rel}:{sl.start}-{sl.end}) was already returned earlier in this session "
                     f"and is unchanged. Reuse that code.")
            if count_tokens(short) < 0.6 * count_tokens(full):  # a pointer must actually be cheaper than the code
                return self._finish("symbol", name, short, file_tokens, [rel], deduped=True)
        self._seen[key] = digest
        return self._finish("symbol", name, full, file_tokens, [rel])

    # ------------------------------------------------------------ pack
    _TEST_RE = None

    def _is_test_path(self, rel: str) -> bool:
        import re

        if Engine._TEST_RE is None:
            Engine._TEST_RE = re.compile(r"(^|/)(tests?|__tests__|specs?|e2e)(/|$)|(^|/)test_|_test\.|\.test\.|\.spec\.")
        return bool(Engine._TEST_RE.search(rel))

    def _class_skeleton(self, G, nid: str, src: str, suffix: str, line: int, sl, max_methods: int = 30) -> str:
        """For a big class: header + short docstring + one signature line per method."""
        head_text, _ = shorten_docstring(slice_symbol(src, suffix, line, max_lines=14).text, suffix, min_lines=5)
        head = "\n".join(head_text.split("\n")[:8])
        rows = []
        try:
            for _u, v, d in G.out_edges(nid, data=True):
                if d.get("relation") == "method" and v in G:
                    ln = parse_line(G.nodes[v].get("source_location"))
                    if ln:
                        rows.append((ln, v))
        except Exception:
            pass
        rows.sort()
        out = [head, f"    # ... class body is {sl.total_lines} lines; {len(rows)} methods. Signatures:"]
        for ln, _v in rows[:max_methods]:
            out.append(signature(src, suffix, ln, max_lines=2).split("\n")[0].rstrip())
        if len(rows) > max_methods:
            out.append(f"    # ... +{len(rows) - max_methods} more methods")
        return "\n".join(out)

    def pack(self, task: str, budget: int = 4000) -> Result:
        import re

        try:
            G = self._graph()
            from graphify.serve import _pick_seeds, _query_terms, _score_query

            terms = _query_terms(task)
            qs = _score_query(G, terms, collect_per_term_seeds=True)
            seeds = _pick_seeds(qs.ranked, max_k=4, G=G, best_seed_by_term=qs.best_seed_by_term,
                                term_order=tuple(terms))
        except GraphError as e:
            return self._fail(str(e))
        except Exception as e:
            return self._fail(f"pack failed: {type(e).__name__}: {e}")
        if not seeds and not qs.ranked:
            return self._fail("No symbols matched this task. Try different keywords, or fall back to grep.")

        def deg(n):
            try:
                return G.degree(n)
            except Exception:
                return 0

        def is_sym(n):
            d = G.nodes[n]
            return (d.get("file_type") == "code" and parse_line(d.get("source_location")) is not None
                    and d.get("source_file") and Path(str(d.get("source_file"))).suffix.lower() in CODE_EXTS
                    and not self.p.is_file_node(d))

        def resolve(n):
            """Rationale/docstring nodes point at the symbol they describe."""
            if n not in G:
                return []
            d = G.nodes[n]
            if d.get("file_type") == "rationale":
                try:
                    return [v for _u, v, dd in G.out_edges(n, data=True) if dd.get("relation") == "rationale_for" and v in G]
                except Exception:
                    return []
            return [n]

        cand: dict[str, float] = {}
        seed_syms: list[str] = []
        for i, s in enumerate(seeds):
            w = 1.0 / (1.0 + 0.25 * i)
            for n in resolve(s):
                if is_sym(n):
                    seed_syms.append(n)
                    cand[n] = max(cand.get(n, 0), w)
                elif G.nodes[n].get("file_type") == "code" and G.nodes[n].get("source_file"):
                    sf = G.nodes[n].get("source_file")
                    inside = [m for m, dd in self.p.symbol_nodes() if dd.get("source_file") == sf]
                    inside.sort(key=lambda m: -deg(m))
                    for m in inside[:6]:
                        seed_syms.append(m)
                        cand[m] = max(cand.get(m, 0), w * 0.8)
        # direct lexical hits from Graphify's ranking, so a strong name match is never lost to graph proximity
        rank_i = 0
        for _score, n in qs.ranked[:40]:
            for m in resolve(n):
                if is_sym(m):
                    cand[m] = max(cand.get(m, 0), 0.9 / (1.0 + 0.12 * rank_i))
                    rank_i += 1

        def expand(n: str, w: float, hop: int):
            edges = []
            try:
                edges += [(v, d.get("relation")) for _u, v, d in G.out_edges(n, data=True)]
                edges += [(u, d.get("relation")) for u, _v, d in G.in_edges(n, data=True)]
            except Exception:
                return
            for m, rel in edges:
                rw = _REL_W.get(rel)
                if rw is None or m not in G or not is_sym(m):
                    continue
                nw = w * rw / (1.0 + 0.35 * math.log2(1 + deg(m)))
                if nw > cand.get(m, 0):
                    cand[m] = nw
                    if hop == 1 and rel in ("calls", "method"):
                        expand(m, nw * 0.5, 2)

        for s in list(dict.fromkeys(seed_syms)):
            expand(s, cand[s], 1)

        # relevance re-ranking: boost labels that contain query words, demote tests
        def stem(w: str) -> str:
            w = w.lower()
            if w.startswith("error") or w.startswith("exception") or w == "fail" or w == "failure":
                return "exception"
            for suf in ("ing", "ed", "es", "s"):
                if w.endswith(suf) and len(w) - len(suf) >= 4:
                    return w[: -len(suf)]
            return w

        tq = [stem(t) for t in query_terms(task) if len(t) >= 3]
        wants_tests = any(t.startswith("test") for t in tq)
        final: dict[str, float] = {}
        for n, w in cand.items():
            d = G.nodes[n]
            raw = str(d.get("label", "")).replace("_", " ")
            toks = [stem(x) for x in re.findall(r"[A-Z]+(?![a-z])|[A-Z]?[a-z0-9]+", raw)]
            hit = 0.0
            for t in tq:
                if t in toks:
                    hit += 1.0
                elif any(len(t) >= 5 and (tok.startswith(t[:5]) or t.startswith(tok)) and len(tok) >= 5 for tok in toks):
                    hit += 0.5
            boost = 1.0 + 2.0 * (hit / max(len(tq), 1))
            pen = 0.35 if (not wants_tests and self._is_test_path(str(d.get("source_file")))) else 1.0
            final[n] = w * boost * pen
        ordered = sorted(final, key=lambda n: -final[n])[:60]
        n_core = 3
        core_ids = set(ordered[:n_core])

        used = 0
        full_budget = int(budget * 0.9)
        core: list[str] = []
        support: list[str] = []
        sigs: list[str] = []
        related: list[str] = []
        files: list[str] = []
        cache: dict[str, str] = {}
        n_full = 0
        for idx, n in enumerate(ordered):
            d = G.nodes[n]
            rel = str(d.get("source_file"))
            line = parse_line(d.get("source_location")) or 1
            src = self._src(rel, cache)
            if not src:
                continue
            suffix = Path(rel).suffix
            is_core = n in core_ids
            sl = slice_symbol(src, suffix, line, max_lines=110 if is_core else 60)
            label = self._display(G, n)
            lang = _FENCE.get(suffix.lower(), "")
            is_big_class = bool(d.get("_callable_class")) and sl.total_lines > 60
            if is_big_class:
                body_text = self._class_skeleton(G, n, src, suffix, line, sl)
                head = f"### {label} — {rel}:{line}-{line + sl.total_lines - 1}  [class skeleton]"
            else:
                body_text = shorten_docstring(sl.text, suffix)[0]
                head = f"### {label} — {rel}:{sl.start}-{sl.end}" + ("  [truncated]" if sl.truncated else "")
            block = f"{head}\n```{lang}\n{body_text}\n```"
            t = count_tokens(block)
            if idx == 0 and t > full_budget:  # the top hit always goes in, trimmed to fit
                keep = max(12, int(sl.total_lines * full_budget / max(t, 1)))
                sl = slice_symbol(src, suffix, line, max_lines=keep)
                block = f"### {label} — {rel}:{sl.start}-{sl.end}  [truncated to fit budget]\n```{lang}\n{shorten_docstring(sl.text, suffix)[0]}\n```"
                t = count_tokens(block)
            if used + t <= full_budget:
                (core if is_core else support).append(block)
                used += t
                n_full += 1
                if rel not in files:
                    files.append(rel)
                continue
            sig = signature(src, suffix, line)
            sig_line = f"- `{label}` — {rel}:{line}\n  ```{lang}\n  " + sig.replace("\n", "\n  ") + "\n  ```"
            st = count_tokens(sig_line)
            if used + st <= budget and len(sigs) < 12:
                sigs.append(sig_line)
                used += st
            elif len(related) < 20:
                related.append(f"- `{label}` — {rel}:{line}")

        names = [self._display(G, n) for n in ordered[:n_core]]
        parts = [f"# Context pack: {task.strip()[:140]}",
                 f"top matches: {', '.join(names)} · {n_full} symbols in full · {len(files)} files · budget {budget} tokens"]
        if core:
            parts += ["## Core", *core]
        if support:
            parts += ["## Supporting code", *support]
        if sigs:
            parts += ["## Signatures only (call cg_symbol for full code)", *sigs]
        if related:
            parts += ["## Also related (names only)", *related]
        text = "\n\n".join(parts)
        base, bfiles = self.base.grep_read(query_terms(task))
        return self._finish("pack", task, text, base, files or bfiles)

    # ------------------------------------------------------------ impact
    def impact(self, name: str, depth: int = 2, limit: int = 40) -> Result:
        try:
            G = self._graph()
            from graphify.affected import format_affected, resolve_seed_outcome

            seed, rivals = resolve_seed_outcome(G, name, self.p.root)
            if seed is None:
                if rivals:
                    names = ", ".join(str(G.nodes[r].get("label", r)) + " (" + str(G.nodes[r].get("source_file")) + ")"
                                      for r in rivals[:8] if r in G)
                    return self._fail(f"'{name}' is ambiguous: {names}. Qualify with a file path, e.g. 'path/to/file.py::{name}'.")
                return self._fail(f"No node matching '{name}'.")
            text = format_affected(G, name, depth=depth, root=self.p.root, seed=seed)
        except GraphError as e:
            return self._fail(str(e))
        except Exception as e:
            return self._fail(f"impact failed: {type(e).__name__}: {e}")
        lines = text.splitlines()
        if len(lines) > limit + 3:
            text = "\n".join(lines[: limit + 3] + [f"… +{len(lines) - limit - 3} more affected nodes (raise depth/limit or narrow the name)"])
        base, files = self.base.symbol_name_files(name)
        return self._finish("impact", name, text, base, files)

    # ------------------------------------------------------------ outline
    def outline(self, path: str, limit: int = 200) -> Result:
        try:
            G = self._graph()
        except GraphError as e:
            return self._fail(str(e))
        rel = self.p.rel(self.p.root / path) if not Path(path).is_absolute() else self.p.rel(path)
        rows = []
        for nid, d in self.p.symbol_nodes():
            if str(d.get("source_file")) == rel:
                rows.append((parse_line(d.get("source_location")) or 0, str(d.get("label")), self._kind(d)))
        if not rows:
            return self._fail(f"No indexed symbols in '{path}'. It may be unsupported, ignored, or newer than the graph.")
        rows.sort()
        lines = [f"## {rel} — {len(rows)} symbols (from the Graphify graph)"]
        for ln, lab, kind in rows[:limit]:
            lines.append(f"L{ln:<5} {lab}{' [class]' if kind == 'class' else ''}")
        if len(rows) > limit:
            lines.append(f"… +{len(rows) - limit} more")
        lines.append("→ cg_symbol(name) for the code, or Read with offset/limit around a line.")
        return self._finish("outline", path, "\n".join(lines), self.base.file_tokens(rel), [rel])

    # ------------------------------------------------------------ savings
    def savings(self) -> Result:
        recs = self.ledger.read()
        s = _ledger.summarize(recs, self.p.config().get("price_per_mtok", 3.0), sources={"agent", "cli", "shell"})
        if not s["calls"]:
            return self._fail("No calls recorded yet.")
        lines = [f"code-graf savings: {s['calls']} calls · returned {s['returned']:,} tokens vs {s['baseline']:,} "
                 f"for grep+read (baseline) → saved {s['saved']:,} tokens ({s['pct']}%), ≈ ${s['usd_saved']}."]
        for tool, t in sorted(s["by_tool"].items(), key=lambda kv: -(kv[1]["baseline"] - kv[1]["returned"])):
            lines.append(f"  {tool:<8} {t['calls']:>3} calls · {t['returned']:>7,} vs {t['baseline']:>8,} tokens")
        return Result(text="\n".join(lines), returned=count_tokens("\n".join(lines)))
