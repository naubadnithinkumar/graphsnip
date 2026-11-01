"""Project: where the Graphify graph lives, how to (re)build it, how to load it."""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import threading
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
    "graphify-out", ".graphsnip", ".next", ".nuxt", "target", "vendor", ".tox",
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
        self.gs_dir = self.root / ".graphsnip"
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
        p = self.gs_dir / "config.json"
        try:
            if p.exists():
                cfg.update(json.loads(p.read_text(encoding="utf-8")))
        except Exception:
            pass
        if os.environ.get("GRAPHSNIP_MODE"):
            cfg["mode"] = os.environ["GRAPHSNIP_MODE"]
        if os.environ.get("GRAPHSNIP_PRICE_PER_MTOK"):
            try:
                cfg["price_per_mtok"] = float(os.environ["GRAPHSNIP_PRICE_PER_MTOK"])
            except ValueError:
                pass
        return cfg

    def write_default_config(self) -> Path:
        self.gs_dir.mkdir(parents=True, exist_ok=True)
        p = self.gs_dir / "config.json"
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
            sys.stderr.write(f"[graphsnip] cluster-only warning: {(r2.stderr or r2.stdout)[-400:]}\n")
        self._graph = None
        self._labels = None
        self._last_stale_check = 0.0
        sem = self._post_build()
        s = self.graph_stats()
        if sem:
            s["semantic"] = sem
        s["seconds"] = round(time.time() - t0, 1)
        s["mode"] = "full build" if first else "incremental update"
        return s

    # ---------- change detection (manifest: size, mtime, content hash per file) ----------
    @property
    def manifest_path(self) -> Path:
        return self.gs_dir / "manifest.json"

    def _load_manifest(self) -> dict | None:
        try:
            d = json.loads(self.manifest_path.read_text(encoding="utf-8"))
            return d.get("files") if isinstance(d, dict) and d.get("v") == 1 else None
        except Exception:
            return None

    @staticmethod
    def _hash_file(p: Path) -> str:
        h = hashlib.sha1()
        try:
            h.update(p.read_bytes())
        except OSError:
            return ""
        return h.hexdigest()

    def write_manifest(self) -> None:
        self._files = None
        files = {}
        for p in self.files():
            try:
                st = p.stat()
            except OSError:
                continue
            files[self.rel(p)] = [st.st_size, st.st_mtime_ns, self._hash_file(p)]
        try:
            self.gs_dir.mkdir(parents=True, exist_ok=True)
            tmp = self.manifest_path.with_suffix(".tmp")
            tmp.write_text(json.dumps({"v": 1, "built": time.time(), "files": files}), encoding="utf-8")
            os.replace(tmp, self.manifest_path)
        except OSError:
            pass

    def changes(self) -> dict | None:
        """Files added, modified or deleted since the graph was last built (None: no manifest yet).
        A touched file whose content hash is unchanged (git checkout, formatter no-op) does not count."""
        man = self._load_manifest()
        if man is None:
            return None
        self._files = None
        now: dict[str, Path] = {self.rel(p): p for p in self.files()}
        added = [r for r in now if r not in man]
        deleted = [r for r in man if r not in now]
        modified, touched = [], False
        for r, p in now.items():
            old = man.get(r)
            if not old:
                continue
            try:
                st = p.stat()
            except OSError:
                continue
            if st.st_size == old[0] and st.st_mtime_ns == old[1]:
                continue
            if self._hash_file(p) == old[2]:
                man[r] = [st.st_size, st.st_mtime_ns, old[2]]
                touched = True
            else:
                modified.append(r)
        if touched and not (added or deleted or modified):
            try:
                self.manifest_path.write_text(json.dumps({"v": 1, "built": time.time(), "files": man}), encoding="utf-8")
            except OSError:
                pass
        return {"added": added, "modified": modified, "deleted": deleted}

    def is_stale(self, max_age_s: float = 20.0) -> bool:
        """True when the code changed since the graph was built (cheap, cached for a few seconds)."""
        now = time.time()
        if now - self._last_stale_check < max_age_s:
            return self._last_stale
        self._last_stale_check = now
        if not self.has_graph():
            self._last_stale = True
            return True
        ch = self.changes()
        if ch is not None:
            self._last_stale = bool(ch["added"] or ch["modified"] or ch["deleted"])
            return self._last_stale
        # no manifest yet (graph built by an older version): fall back to modification times once, then record a manifest
        gm = self.graph_json.stat().st_mtime
        stale = False
        for p in self.files():
            try:
                if p.stat().st_mtime > gm + 1:
                    stale = True
                    break
            except OSError:
                continue
        if not stale:
            self.write_manifest()
        self._last_stale = stale
        return stale

    def freshness(self) -> dict:
        ch = self.changes() if self.has_graph() else None
        age = time.time() - self.graph_json.stat().st_mtime if self.has_graph() else None
        return {"graph_age_s": age, "changes": ch, "refreshing": self.refreshing()}

    # ---------- cross-process refresh lock ----------
    @property
    def lock_path(self) -> Path:
        return self.gs_dir / "refresh.lock"

    def _lock_is_stale(self) -> bool:
        try:
            pid_s, ts_s = self.lock_path.read_text(encoding="utf-8").split()[:2]
            if time.time() - float(ts_s) > 1800:
                return True
            if os.name == "posix":
                try:
                    os.kill(int(pid_s), 0)
                except ProcessLookupError:
                    return True
                except (PermissionError, OSError):
                    return False
            return False
        except FileNotFoundError:
            return False
        except Exception:
            return True

    def try_lock(self) -> bool:
        self.gs_dir.mkdir(parents=True, exist_ok=True)
        for _ in range(2):
            try:
                fd = os.open(str(self.lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.write(fd, f"{os.getpid()} {time.time()}".encode())
                os.close(fd)
                return True
            except FileExistsError:
                if self._lock_is_stale():
                    try:
                        os.unlink(self.lock_path)
                    except OSError:
                        return False
                    continue
                return False
            except OSError:
                return False
        return False

    def unlock(self) -> None:
        try:
            os.unlink(self.lock_path)
        except OSError:
            pass

    def refreshing(self) -> bool:
        return self.lock_path.exists() and not self._lock_is_stale()

    def wait_unlocked(self, timeout: float = 900.0) -> bool:
        t0 = time.time()
        while self.refreshing():
            if time.time() - t0 > timeout:
                return False
            time.sleep(0.5)
        return True

    def _post_build(self) -> dict | None:
        """Hooks that follow a successful graph refresh (the semantic index catches up on changed nodes)."""
        self.write_manifest()
        try:
            from graphsnip import semantic

            return semantic.refresh_if_enabled(self)
        except Exception as e:  # never let an optional layer break the graph
            sys.stderr.write(f"[graphsnip] semantic refresh skipped: {type(e).__name__}: {e}\n")
        return None

    def refresh(self, background: bool = False) -> bool:
        """Rebuild the graph under the cross-process lock. False: another refresh is already running."""
        if not self.try_lock():
            return False

        def run():
            try:
                self.build()
            except GraphError as e:
                sys.stderr.write(f"[graphsnip] refresh failed, using existing graph: {e}\n")
            finally:
                self.unlock()

        if background:
            threading.Thread(target=run, daemon=True, name="graphsnip-refresh").start()
        else:
            run()
        return True

    def ensure_graph(self, auto_refresh: bool = True, background: bool = False) -> None:
        if not self.has_graph():
            if self.try_lock():
                try:
                    self.build()
                finally:
                    self.unlock()
            else:  # another process is building the first graph: wait for it
                self.wait_unlocked(900.0)
                if not self.has_graph():
                    raise GraphError("The graph is still being built by another process. Try again shortly.")
        elif auto_refresh and self.is_stale():
            self._files = None
            self.refresh(background=background)

    def graph(self):
        if not self.has_graph():
            raise GraphError("No graph yet. Run `graphsnip build` (or `graphsnip init`).")
        m = self.graph_json.stat().st_mtime
        if self._graph is None or m != self._graph_mtime:
            from graphify.serve import _read_graph  # Graphify's own loader (size cap, edge direction)

            last = None
            for attempt in range(4):  # the file can be mid-write while a background refresh finishes
                try:
                    self._graph = _read_graph(str(self.graph_json))
                    last = None
                    break
                except Exception as e:
                    last = e
                    if self._graph is not None:
                        return self._graph  # keep serving the previous snapshot
                    time.sleep(0.5 * (attempt + 1))
            if last is not None:
                raise GraphError(f"could not read {self.graph_json}: {last}")
            self._graph_mtime = self.graph_json.stat().st_mtime
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
