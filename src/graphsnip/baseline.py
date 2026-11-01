"""The honest baseline.

Graphify's own benchmark compares a query against reading the *entire corpus*,
which no agent actually does. graphsnip instead prices what an agent without
the graph would realistically do for the same question: grep for the terms,
then read the best-matching files in full (capped at `max_files`).

Both numbers are reported; the headline uses this one because it is the
defensible one.
"""
from __future__ import annotations

import re

from graphsnip.project import MAX_FILE_BYTES, Project
from graphsnip.tokens import count_tokens

_STOP = set("""
a an and are as at be by can do does for from how i in is it its of on or that the this to was what when where which who why with
use used using work works get gets make makes find show tell explain code file files function functions class classes method
methods please about into over under all any not our your their there then than also
""".split())
_WORD = re.compile(r"[A-Za-z][A-Za-z0-9_]{2,}")
_CAMEL = re.compile(r"[A-Z]+(?=[A-Z][a-z])|[A-Z]?[a-z]+|[A-Z]+|\d+")


def query_terms(text: str, limit: int = 8) -> list[str]:
    terms: list[str] = []
    for w in _WORD.findall(text or ""):
        parts = [w.lower()]
        if "_" in w or re.search(r"[a-z][A-Z]", w):
            parts.append(w.lower())
            parts += [p.lower() for p in _CAMEL.findall(w.replace("_", " ")) if len(p) >= 3]
        for p in parts:
            if p not in _STOP and len(p) >= 3 and p not in terms:
                terms.append(p)
    return terms[:limit]


class Baseline:
    def __init__(self, project: Project):
        self.p = project
        self._text: dict[str, str] | None = None
        self._tok: dict[str, int] = {}

    def _index(self) -> dict[str, str]:
        if self._text is None:
            idx: dict[str, str] = {}
            for f in self.p.files()[:20000]:
                try:
                    if f.stat().st_size > MAX_FILE_BYTES:
                        continue
                    idx[self.p.rel(f)] = f.read_text(encoding="utf-8", errors="replace")
                except Exception:
                    continue
            self._text = idx
        return self._text

    def file_tokens(self, rel: str) -> int:
        if rel not in self._tok:
            t = self._index().get(rel)
            if t is None:
                t = self.p.read(rel) or ""
            self._tok[rel] = count_tokens(t)
        return self._tok[rel]

    def grep_read(self, terms: list[str], max_files: int = 8) -> tuple[int, list[str]]:
        """Tokens for: grep each term, read the top `max_files` matching files in full."""
        if not terms:
            return 0, []
        scored: list[tuple[int, str]] = []
        for rel, text in self._index().items():
            low = text.lower()
            s = 0
            for t in terms:
                c = low.count(t)
                if c:
                    s += min(c, 10)
            if s:
                scored.append((s, rel))
        scored.sort(key=lambda x: (-x[0], x[1]))
        chosen = [rel for _s, rel in scored[:max_files]]
        tokens = sum(self.file_tokens(r) for r in chosen) + 150  # + the grep listing itself
        return tokens, chosen

    def symbol_name_files(self, name: str, max_files: int = 10) -> tuple[int, list[str]]:
        """Tokens for: grep a symbol name, read every matching file (capped)."""
        n = re.sub(r"\(.*\)\s*$", "", name).lstrip(".").split(".")[-1].lower()
        return self.grep_read([n], max_files=max_files) if len(n) >= 2 else (0, [])
