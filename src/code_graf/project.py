"""Project: where the Graphify graph lives, how to (re)build it, how to load it."""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path

CODE_EXTS = {
    ".py", ".pyi", ".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx", ".go", ".rs", ".java",
    ".c", ".h", ".cc", ".cpp", ".hpp", ".rb", ".cs", ".kt", ".kts", ".scala", ".php",
    ".swift", ".lua", ".sh", ".bash", ".zig", ".ex", ".exs", ".vue", ".svelte",
}
IGNORE_DIRS = {
    ".git", "node_modules", "venv", ".venv", "env", "__pycache__", "dist", "build",
    "graphify-out", ".code-graf", ".next", ".nuxt", "target", "vendor", ".tox",
    ".mypy_cache", ".pytest_cache", ".idea", ".vscode", "coverage", ".gradle", "Pods",
}
MAX_FILE_BYTES = 400_000
_LOC_RE = re.compile(r"L(\d+)")


class GraphError(RuntimeError):
    pass


def parse_line(loc) -> int | None:
    m = _LOC_RE.match(str(loc or ""))
    return int(m.group(1)) if m else None


class Project:
    def __init__(self, root: str | os.PathLike = "."):
        self.root = Path(root).resolve()
        self.graph_dir = self.root / "graphify-out"
        self.graph_json = self.graph_dir / "graph.json"
        self.graph_html = self.graph_dir / "graph.html"
        self.report_md = self.graph_dir / "GRAPH_REPORT.md"
        self.cg_dir = self.root / ".code-graf"
        self._graph = None
        self._graph_mtime = 0.0
        self._files: list[Path] | None = None
        self._labels: dict[str, list[str]] | None = None
        self._last_stale_check = 0.0
        self._last_stale = False

    # ---------- config ----------
    def config(self) -> dict:
        cfg = {
            "mode": "nudge",                 # nudge | strict | off
            "read_threshold_tokens": 1500,   # hook: files bigger than this get redirected
            "shell_filter": False,           # hook: opt-in rewrite of noisy shell commands
            "price_per_mtok": 3.0,           # USD per million input tokens, for the cost estimate
        }
        p = self.cg_dir / "config.json"
        try:
            if p.exists():
                cfg.update(json.loads(p.read_text(encoding="utf-8")))
        except Exception:
            pass
        if os.environ.get("CODEGRAF_MODE"):
            cfg["mode"] = os.environ["CODEGRAF_MODE"]
        if os.environ.get("CODEGRAF_PRICE_PER_MTOK"):
            try:
                cfg["price_per_mtok"] = float(os.environ["CODEGRAF_PRICE_PER_MTOK"])
            except ValueError:
                pass
        return cfg

    def write_default_config(self) -> Path:
        self.cg_dir.mkdir(parents=True, exist_ok=True)
        p = self.cg_dir / "config.json"
        if not p.exists():
            default = {
                "mode": "nudge",
                "read_threshold_tokens": 1500,
                "shell_filter": False,
                "price_per_mtok": 3.0,
            }
            p.write_text(json.dumps(default, indent=2) + "\n", encoding="utf-8")
        return p

    # ---------- files ----------
    def files(self) -> list[Path]:
        if self._files is not None:
            return self._files
        out: list[Path] = []
        try:
            r = subprocess.run(
                ["git", "-C", str(self.root), "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
                capture_output=True, timeout=30,
            )
            if r.returncode == 0 and r.stdout:
                for rel in r.stdout.decode("utf-8", "replace").split("\0"):
                    if not rel:
                        continue
                    p = self.root / rel
                    if p.suffix.lower() in CODE_EXTS and not (set(p.relative_to(self.root).parts) & IGNORE_DIRS):
                        out.append(p)
        except Exception:
            pass
        if not out:
            for dp, dns, fns in os.walk(self.root):
                dns[:] = [d for d in dns if d not in IGNORE_DIRS]
                for fn in fns:
                    p = Path(dp) / fn
                    if p.suffix.lower() in CODE_EXTS:
                        out.append(p)
        out = [p for p in out if p.is_file()]
        self._files = sorted(out)
        return self._files

    def rel(self, p: Path | str) -> str:
        p = Path(p)
        try:
            return p.resolve().relative_to(self.root).as_posix()
        except Exception:
            return str(p).replace("\\", "/")

    def read(self, rel: str) -> str | None:
        p = (self.root / rel)
        try:
            if p.stat().st_size > MAX_FILE_BYTES * 3:
                return None
            return p.read_text(encoding="utf-8", errors="replace")
        except Exception:
            return None

    # ---------- graph build / load ----------
    def has_graph(self) -> bool:
        return self.graph_json.exists()

    def _run_graphify(self, *args: str, timeout: int = 900) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, "-m", "graphify", *args],
            capture_output=True, text=True, timeout=timeout, cwd=str(self.root),
        )

    def build(self, force: bool = False) -> dict:
        """Build or refresh the Graphify graph (code only, no LLM) and regenerate
        graph.html + GRAPH_REPORT.md. Returns a small summary dict."""
        t0 = time.time()
        first = not self.has_graph() or force
        args = ["extract", str(self.root), "--code-only"]
        if force:
            args.append("--force")
        if first:
            r = self._run_graphify(*args)
        else:
            r = self._run_graphify("update", str(self.root))
            if r.returncode != 0:  # fall back to a full extract
                r = self._run_graphify(*args)
        if r.returncode != 0 or not self.has_graph():
            raise GraphError(f"graphify failed (exit {r.returncode}):\n{(r.stderr or r.stdout)[-1500:]}")
        r2 = self._run_graphify("cluster-only", str(self.root), "--no-label")
        if r2.returncode != 0:
            # graph.json exists; html/report generation is best-effort
            sys.stderr.write(f"[code-graf] cluster-only warning: {(r2.stderr or r2.stdout)[-400:]}\n")
        self._graph = None
        self._labels = None
        self._last_stale_check = 0.0
        s = self.graph_stats()
        s["seconds"] = round(time.time() - t0, 1)
        s["mode"] = "full build" if first else "incremental update"
        return s

    def is_stale(self, max_age_s: float = 20.0) -> bool:
        """True when any indexed code file is newer than graph.json (cheap, cached for a few seconds)."""
        now = time.time()
        if now - self._last_stale_check < max_age_s:
            return self._last_stale
        self._last_stale_check = now
        if not self.has_graph():
            self._last_stale = True
            return True
        gm = self.graph_json.stat().st_mtime
        stale = False
        for p in self.files():
            try:
                if p.stat().st_mtime > gm + 1:
                    stale = True
                    break
            except OSError:
                continue
        self._last_stale = stale
        return stale

    def ensure_graph(self, auto_refresh: bool = True) -> None:
        if not self.has_graph():
            self.build()
        elif auto_refresh and self.is_stale():
            self._files = None
            try:
                self.build()
            except GraphError as e:
                sys.stderr.write(f"[code-graf] refresh failed, using existing graph: {e}\n")

    def graph(self):
        if not self.has_graph():
            raise GraphError("No graph yet. Run `code-graf build` (or `code-graf init`).")
        m = self.graph_json.stat().st_mtime
        if self._graph is None or m != self._graph_mtime:
            from graphify.serve import _read_graph  # Graphify's own loader (size cap, edge direction)

            self._graph = _read_graph(str(self.graph_json))
            self._graph_mtime = m
            self._labels = None
        return self._graph

    # ---------- graph helpers ----------
    @staticmethod
    def is_file_node(d: dict) -> bool:
        lab = str(d.get("label", ""))
        sf = str(d.get("source_file", ""))
        return bool(sf) and (lab.endswith(tuple(CODE_EXTS)) or lab == sf or sf.endswith("/" + lab) or sf.endswith(lab))

    def symbol_nodes(self):
        """Yield (nid, data) for real code symbols (not file/concept/rationale nodes)."""
        G = self.graph()
        for nid, d in G.nodes(data=True):
            if d.get("file_type") != "code":
                continue
            if parse_line(d.get("source_location")) is None or not d.get("source_file"):
                continue
            if Path(str(d.get("source_file"))).suffix.lower() not in CODE_EXTS:
                continue  # toml / yaml / json config nodes are not code symbols
            if self.is_file_node(d):
                continue
            yield nid, d

    def display(self, nid: str) -> str:
        """'.make_response()' -> 'Flask.make_response()' using the owning class when known."""
        G = self.graph()
        lab = str(G.nodes[nid].get("label", nid))
        if lab.startswith("."):
            try:
                for u, _v, d in G.in_edges(nid, data=True):
                    if d.get("relation") == "method":
                        owner = re.sub(r"\(\)\s*$", "", str(G.nodes[u].get("label", "")))
                        return f"{owner}{lab}"
            except Exception:
                pass
        return lab

    def label_index(self) -> dict[str, list[str]]:
        """lower-case bare label -> node ids (for hook lookups and quick resolution)."""
        self.graph()
        if self._labels is None:
            idx: dict[str, list[str]] = {}
            for nid, d in self.symbol_nodes():
                key = bare(str(d.get("label", "")))
                idx.setdefault(key, []).append(nid)
            self._labels = idx
        return self._labels

    def outline_rows(self, rel: str) -> list[tuple[int, str, bool]]:
        """[(line, label, is_class)] for the symbols Graphify found in one file."""
        rows = []
        for _nid, d in self.symbol_nodes():
            if str(d.get("source_file")) == rel:
                rows.append((parse_line(d.get("source_location")) or 0, str(d.get("label")), bool(d.get("_callable_class"))))
        rows.sort()
        return rows

    def graph_stats(self) -> dict:
        G = self.graph()
        langs: Counter = Counter()
        n_sym = 0
        for _nid, d in self.symbol_nodes():
            n_sym += 1
            langs[Path(str(d.get("source_file"))).suffix.lower() or "?"] += 1
        comms = {d.get("community") for _n, d in G.nodes(data=True) if d.get("community") is not None}
        return {
            "nodes": G.number_of_nodes(),
            "edges": G.number_of_edges(),
            "symbols": n_sym,
            "communities": len(comms),
            "languages": dict(langs.most_common(8)),
            "built_at_commit": G.graph.get("_built_at_commit"),
            "graph_json": str(self.graph_json),
        }

    def top_symbols(self, n: int = 10, exclude_tests: bool = True) -> list[dict]:
        G = self.graph()
        rows = []
        for nid, d in self.symbol_nodes():
            sf = str(d.get("source_file"))
            if exclude_tests and re.search(r"(^|/)(tests?|__tests__|spec|specs)(/|$)|test_|_test\.|\.test\.|\.spec\.", sf):
                continue
            try:
                deg = G.degree(nid)
            except Exception:
                deg = 0
            rows.append({"id": nid, "label": d.get("label"), "file": sf, "line": parse_line(d.get("source_location")),
                         "degree": deg, "community": d.get("community")})
        rows.sort(key=lambda r: -r["degree"])
        return rows[:n]


def bare(label: str) -> str:
    """'Foo.bar()' -> 'foo.bar'; '.run()' -> 'run'; 'app_ctx()' -> 'app_ctx'."""
    s = label.strip()
    s = re.sub(r"\(.*\)\s*$", "", s)
    return s.lstrip(".").lower()
