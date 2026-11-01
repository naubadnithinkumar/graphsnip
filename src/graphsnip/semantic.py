"""Optional semantic layer: one vector per code symbol of the Graphify graph, stored beside the ledger.

Layout (all derived data, safe to delete, git-ignore it):
  .graphsnip/semantic/vectors.npy   one row per symbol (float16, L2-normalised)
  .graphsnip/semantic/ids.json      row -> graph node id, plus a hash of the text that was embedded
  .graphsnip/semantic/meta.json     model name, dimensions, version, build time

Nothing here is required: without the `semantic` extra or an index, retrieval is keyword-only exactly as before.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import sys
import time
from pathlib import Path

VERSION = 1
DEFAULT_MODEL = "BAAI/bge-small-en-v1.5"
BODY_LINES = 14
MAX_TEXT_CHARS = 1400
RRF_K = 60


class SemanticUnavailable(RuntimeError):
    pass


# ------------------------------------------------------------------ text for one symbol
_SPLIT = re.compile(r"[A-Z]+(?![a-z])|[A-Z]?[a-z0-9]+")


def split_identifier(name: str) -> str:
    return " ".join(w.lower() for w in _SPLIT.findall(name.replace("_", " ").replace("-", " ").replace(".", " ")))


def _labels(G, nodes) -> list[str]:
    out, seen = [], set()
    for n in nodes:
        lab = re.sub(r"\(\)\s*$", "", str(G.nodes[n].get("label", ""))).strip(". ")
        if lab and lab not in seen:
            seen.add(lab)
            out.append(lab)
    return out


def node_text(project, G, nid: str, cache: dict) -> str:
    """What gets embedded for a symbol: its name, where it lives, who it talks to, and the start of its body."""
    from graphsnip.engine import _IN_REL, _OUT_REL
    from graphsnip.project import parse_line

    d = G.nodes[nid]
    label = re.sub(r"\(\)\s*$", "", str(d.get("label", nid))).strip(". ")
    rel = str(d.get("source_file", ""))
    line = parse_line(d.get("source_location")) or 1
    callees, callers = [], []
    try:
        callees = [v for _u, v, dd in G.out_edges(nid, data=True) if dd.get("relation") in _OUT_REL and v in G]
        callers = [u for u, _v, dd in G.in_edges(nid, data=True) if dd.get("relation") in _IN_REL and u in G]
    except Exception:
        pass
    if rel not in cache:
        cache[rel] = (project.read(rel) or "").splitlines()
    body = "\n".join(cache[rel][max(line - 1, 0): max(line - 1, 0) + BODY_LINES])
    parts = [
        f"{label} ({'class' if d.get('_callable_class') else 'function'}) in {rel}",
        f"words: {split_identifier(label)} {split_identifier(Path(rel).stem)}",
    ]
    if callees:
        parts.append("uses: " + ", ".join(_labels(G, callees)[:8]))
    if callers:
        parts.append("used by: " + ", ".join(_labels(G, callers)[:8]))
    parts.append(body)
    return "\n".join(parts)[:MAX_TEXT_CHARS]


def text_hash(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8", "replace")).hexdigest()[:16]


# ------------------------------------------------------------------ embedders
class Embedder:
    name = "base"
    dim = 0

    def embed(self, texts: list[str]):  # -> numpy float32 (n, dim), L2-normalised
        raise NotImplementedError

    def embed_query(self, text: str):
        return self.embed([text])[0]


class FastEmbedder(Embedder):
    """Small local ONNX model through `fastembed`: no server, no API key, code never leaves the machine."""

    def __init__(self, model: str = DEFAULT_MODEL):
        try:
            from fastembed import TextEmbedding
        except Exception as e:
            raise SemanticUnavailable("semantic search needs the optional extra: pip install 'graphsnip[semantic]'") from e
        self.name = model
        try:
            self._m = TextEmbedding(model_name=model)
        except Exception as e:
            raise SemanticUnavailable(f"could not load embedding model {model!r}: {type(e).__name__}: {e}") from e
        self.dim = len(next(iter(self._m.embed(["probe"]))))

    @staticmethod
    def _norm(a):
        import numpy as np

        a = np.asarray(a, dtype="float32")
        n = np.linalg.norm(a, axis=1, keepdims=True)
        n[n == 0] = 1.0
        return a / n

    def embed(self, texts: list[str]):
        import numpy as np

        return self._norm(np.array(list(self._m.embed(texts))))

    def embed_query(self, text: str):
        qe = getattr(self._m, "query_embed", None)
        if qe is not None:
            import numpy as np

            return self._norm(np.array(list(qe([text]))))[0]
        return super().embed_query(text)


class HashEmbedder(Embedder):
    """Dependency-free, deterministic bag-of-words hashing. Used for tests and offline checks, not for quality.
    `aliases` maps a word to a shared concept token, which lets tests express 'these words mean the same thing'."""

    def __init__(self, dim: int = 256, aliases: dict[str, str] | None = None):
        self.name = f"hash-{dim}"
        self.dim = dim
        self.aliases = {k.lower(): v.lower() for k, v in (aliases or {}).items()}

    def _vec(self, text: str):
        import numpy as np

        v = np.zeros(self.dim, dtype="float32")
        for w in re.findall(r"[a-z0-9]+", split_identifier(text) + " " + text.lower()):
            w = self.aliases.get(w, w)
            if len(w) < 3:
                continue
            h = int(hashlib.md5(w.encode()).hexdigest(), 16)
            v[h % self.dim] += 1.0 if (h >> 64) & 1 else -1.0
        n = float(np.linalg.norm(v))
        return v / n if n else v

    def embed(self, texts: list[str]):
        import numpy as np

        return np.stack([self._vec(t) for t in texts]) if texts else np.zeros((0, self.dim), dtype="float32")


_EMBEDDERS: dict[str, Embedder] = {}


def get_embedder(project=None) -> Embedder:
    name = os.environ.get("GRAPHSNIP_EMBEDDER") or (project.config().get("semantic_model") if project else None) or DEFAULT_MODEL
    if name not in _EMBEDDERS:
        _EMBEDDERS[name] = HashEmbedder() if name == "hash" else FastEmbedder(name)
    return _EMBEDDERS[name]


# ------------------------------------------------------------------ store
def sem_dir(project) -> Path:
    return project.gs_dir / "semantic"


def has_index(project) -> bool:
    d = sem_dir(project)
    return (d / "vectors.npy").exists() and (d / "ids.json").exists() and (d / "meta.json").exists()


def _load(project):
    import numpy as np

    d = sem_dir(project)
    meta = json.loads((d / "meta.json").read_text(encoding="utf-8"))
    ids = json.loads((d / "ids.json").read_text(encoding="utf-8"))
    return meta, ids["ids"], ids["hashes"], np.load(d / "vectors.npy")


def _save(project, meta: dict, ids: list[str], hashes: list[str], mat) -> None:
    import numpy as np

    d = sem_dir(project)
    d.mkdir(parents=True, exist_ok=True)
    (d / ".gitignore").write_text("*\n", encoding="utf-8")  # derived, per-machine and per-model: never commit
    tmp = d / "vectors.tmp.npy"
    np.save(tmp, mat.astype("float16"))
    os.replace(tmp, d / "vectors.npy")
    (d / "ids.json.tmp").write_text(json.dumps({"ids": ids, "hashes": hashes}), encoding="utf-8")
    os.replace(d / "ids.json.tmp", d / "ids.json")
    (d / "meta.json.tmp").write_text(json.dumps(meta), encoding="utf-8")
    os.replace(d / "meta.json.tmp", d / "meta.json")


def refresh(project, embedder: Embedder | None = None, force: bool = False, batch: int = 64, progress=None) -> dict:
    """Bring the vectors in line with the current graph, embedding only new or changed symbols."""
    import numpy as np

    t0 = time.time()
    emb = embedder or get_embedder(project)
    G = project.graph()
    cache: dict = {}
    want: list[tuple[str, str]] = []  # (node id, text)
    for nid, _d in project.symbol_nodes():
        want.append((nid, node_text(project, G, nid, cache)))
    old_vec: dict[str, tuple[str, object]] = {}
    if has_index(project) and not force:
        try:
            meta, ids, hashes, mat = _load(project)
            if meta.get("model") == emb.name and meta.get("dim") == emb.dim and meta.get("v") == VERSION:
                for i, nid in enumerate(ids):
                    old_vec[nid] = (hashes[i], mat[i])
        except Exception:
            old_vec = {}
    todo = [(nid, t) for nid, t in want if old_vec.get(nid, ("", None))[0] != text_hash(t)]
    new_vecs: dict[str, object] = {}
    for i in range(0, len(todo), batch):
        chunk = todo[i: i + batch]
        vecs = emb.embed([t for _n, t in chunk])
        for (nid, _t), v in zip(chunk, vecs):
            new_vecs[nid] = v
        if progress:
            progress(min(i + batch, len(todo)), len(todo))
    ids, hashes, rows = [], [], []
    for nid, t in want:
        h = text_hash(t)
        if nid in new_vecs:
            rows.append(new_vecs[nid])
        else:
            rows.append(old_vec[nid][1])
        ids.append(nid)
        hashes.append(h)
    mat = np.stack(rows).astype("float32") if rows else np.zeros((0, emb.dim), dtype="float32")
    sid = set(ids)
    removed = len([n for n in old_vec if n not in sid])
    meta = {"v": VERSION, "model": emb.name, "dim": emb.dim, "count": len(ids), "built": time.time()}
    _save(project, meta, ids, hashes, mat)
    _CACHE.pop(str(project.root), None)
    return {"total": len(ids), "embedded": len(todo), "reused": len(ids) - len(todo), "removed": removed,
            "seconds": round(time.time() - t0, 1), "model": emb.name}


def refresh_if_enabled(project) -> dict | None:
    """Called after a graph refresh: keep an existing index current. Never builds one on its own."""
    if os.environ.get("GRAPHSNIP_SEMANTIC", "").lower() == "off" or project.config().get("semantic") == "off":
        return None
    if not has_index(project):
        return None
    try:
        return refresh(project)
    except SemanticUnavailable as e:
        sys.stderr.write(f"[graphsnip] {e}\n")
        return None


_CACHE: dict[str, tuple[float, list[str], object]] = {}


def _matrix(project):
    key = str(project.root)
    mt = (sem_dir(project) / "vectors.npy").stat().st_mtime
    hit = _CACHE.get(key)
    if hit and hit[0] == mt:
        return hit[1], hit[2], None
    meta, ids, _h, mat = _load(project)
    mat = mat.astype("float32")
    _CACHE[key] = (mt, ids, mat)
    return ids, mat, meta


def status(project) -> dict:
    if not has_index(project):
        return {"present": False}
    try:
        meta = json.loads((sem_dir(project) / "meta.json").read_text(encoding="utf-8"))
    except Exception:
        return {"present": False}
    G = project.graph() if project.has_graph() else None
    pending = None
    if G is not None:
        try:
            _m, ids, hashes, _mat = _load(project)
            have = dict(zip(ids, hashes))
            cache: dict = {}
            pending = sum(1 for nid, _d in project.symbol_nodes() if have.get(nid) != text_hash(node_text(project, G, nid, cache)))
        except Exception:
            pending = None
    return {"present": True, "model": meta.get("model"), "dim": meta.get("dim"), "count": meta.get("count"),
            "built": meta.get("built"), "pending": pending}


# ------------------------------------------------------------------ search and fusion
def search(project, query: str, k: int = 20, embedder: Embedder | None = None) -> list[tuple[str, float]]:
    """Nearest symbols to the query by cosine similarity: [(node id, score)], best first. [] when unavailable."""
    import numpy as np

    if os.environ.get("GRAPHSNIP_SEMANTIC", "").lower() == "off" or project.config().get("semantic") == "off":
        return []
    if not has_index(project):
        return []
    try:
        emb = embedder or get_embedder(project)
        ids, mat, _meta = _matrix(project)
        if mat.shape[0] == 0 or mat.shape[1] != emb.dim:
            return []
        q = np.asarray(emb.embed_query(query), dtype="float32")
        scores = mat @ q
    except SemanticUnavailable as e:
        sys.stderr.write(f"[graphsnip] {e}\n")
        return []
    k = min(k, len(ids))
    top = np.argpartition(-scores, k - 1)[:k]
    top = top[np.argsort(-scores[top])]
    G = project.graph()
    return [(ids[i], float(scores[i])) for i in top if ids[i] in G and not math.isnan(float(scores[i]))]


def rrf(*ranked: list[str], k: int = RRF_K) -> list[tuple[str, float]]:
    """Reciprocal rank fusion: merge several best-first lists of ids without tuning weights."""
    score: dict[str, float] = {}
    for lst in ranked:
        for r, nid in enumerate(lst):
            score[nid] = score.get(nid, 0.0) + 1.0 / (k + r + 1)
    return sorted(score.items(), key=lambda kv: -kv[1])
