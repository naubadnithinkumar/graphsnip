"""Symbol-level code slicing: given a file and the line Graphify recorded for a
node, return exactly that definition (tree-sitter when available, indentation /
brace matching otherwise)."""
from __future__ import annotations

import importlib
import re
from dataclasses import dataclass
from functools import lru_cache

_LANGS: dict[str, tuple[str, str]] = {
    ".py": ("python", "language"), ".pyi": ("python", "language"),
    ".js": ("javascript", "language"), ".jsx": ("javascript", "language"),
    ".mjs": ("javascript", "language"), ".cjs": ("javascript", "language"),
    ".ts": ("typescript", "language_typescript"), ".tsx": ("typescript", "language_tsx"),
    ".go": ("go", "language"), ".rs": ("rust", "language"), ".java": ("java", "language"),
    ".c": ("c", "language"), ".h": ("c", "language"),
    ".cc": ("cpp", "language"), ".cpp": ("cpp", "language"), ".hpp": ("cpp", "language"),
    ".rb": ("ruby", "language"), ".cs": ("c_sharp", "language"), ".kt": ("kotlin", "language"),
    ".php": ("php", "language_php"), ".swift": ("swift", "language"), ".lua": ("lua", "language"),
    ".sh": ("bash", "language"), ".bash": ("bash", "language"),
}
_DEF_HINTS = ("function", "method", "class", "struct", "interface", "enum", "trait", "impl",
              "module", "constructor", "definition", "declaration", "declarator")
_DEF_SKIP = ("import", "param", "argument", "call", "decorator", "export_clause", "comment", "identifier")
_WRAPPERS = {"decorated_definition", "export_statement", "ambient_declaration", "decorated_declaration"}


@dataclass
class Slice:
    start: int          # 1-based, inclusive
    end: int            # 1-based, inclusive
    text: str
    exact: bool         # True when tree-sitter located the definition
    truncated: bool = False
    total_lines: int = 0


@lru_cache(maxsize=32)
def _parser(suffix: str):
    spec = _LANGS.get(suffix)
    if not spec:
        return None
    try:
        import tree_sitter

        mod = importlib.import_module(f"tree_sitter_{spec[0]}")
        fn = getattr(mod, spec[1], None) or getattr(mod, "language")
        return tree_sitter.Parser(tree_sitter.Language(fn()))
    except Exception:
        return None


def _is_def(node) -> bool:
    t = node.type
    return any(h in t for h in _DEF_HINTS) and not any(s in t for s in _DEF_SKIP)


def _ts_range(source: str, suffix: str, line: int) -> tuple[int, int] | None:
    parser = _parser(suffix)
    if parser is None:
        return None
    try:
        tree = parser.parse(source.encode("utf-8", "replace"))
    except Exception:
        return None
    row = line - 1
    best = None
    stack = [tree.root_node]
    while stack:
        n = stack.pop()
        if n.start_point[0] > row or n.end_point[0] < row:
            continue
        if n.start_point[0] == row and _is_def(n):
            span = n.end_point[0] - n.start_point[0]
            if best is None or span > best[0]:
                best = (span, n)
        stack.extend(n.children)
    if best is None:
        return None
    node = best[1]
    parent = node.parent
    start = node.start_point[0]
    if parent is not None and parent.type in _WRAPPERS and parent.start_point[0] < start:
        start = parent.start_point[0]
    end = node.end_point[0]
    return start + 1, end + 1


def _python_block(lines: list[str], i: int) -> int:
    def indent(s: str) -> int:
        return len(s) - len(s.lstrip(" \t"))

    base = indent(lines[i])
    end = i
    j = i + 1
    while j < len(lines):
        s = lines[j]
        if s.strip() == "":
            j += 1
            continue
        if indent(s) <= base and not s.lstrip().startswith((")", "]", "}")):
            break
        end = j
        j += 1
    return end


def _brace_block(lines: list[str], i: int, cap: int = 400) -> int | None:
    depth = 0
    seen = False
    for j in range(i, min(len(lines), i + cap)):
        for ch in lines[j]:
            if ch == "{":
                depth += 1
                seen = True
            elif ch == "}":
                depth -= 1
        if seen and depth <= 0:
            return j
        if not seen and j - i > 6:
            return None
    return None


def slice_symbol(source: str, suffix: str, line: int, max_lines: int | None = 120) -> Slice:
    lines = source.splitlines()
    n = len(lines)
    line = max(1, min(line, max(n, 1)))
    suffix = suffix.lower()
    exact = False
    rng = _ts_range(source, suffix, line)
    if rng:
        start, end = rng
        exact = True
    else:
        start = line
        i = line - 1
        end_i: int | None = None
        if suffix in (".py", ".pyi"):
            end_i = _python_block(lines, i)
            # include decorators sitting directly above
            while start > 1 and lines[start - 2].lstrip().startswith("@"):
                start -= 1
        else:
            end_i = _brace_block(lines, i)
        end = (end_i + 1) if end_i is not None else min(n, line + 39)
    end = max(start, min(end, n))
    total = end - start + 1
    truncated = False
    if max_lines and total > max_lines:
        end = start + max_lines - 1
        truncated = True
    text = "\n".join(lines[start - 1:end])
    return Slice(start=start, end=end, text=text, exact=exact, truncated=truncated, total_lines=total)


def signature(source: str, suffix: str, line: int, max_lines: int = 3) -> str:
    """First few lines of a definition: enough to know what it is and how to call it."""
    lines = source.splitlines()
    i = max(0, line - 1)
    out: list[str] = []
    for j in range(i, min(len(lines), i + max_lines)):
        out.append(lines[j].rstrip())
        s = lines[j].rstrip()
        if s.endswith((":", "{", ";")) or s.endswith(") =>") or "=>" in s:
            break
    return "\n".join(out)


def shorten_docstring(text: str, suffix: str, min_lines: int = 7) -> tuple[str, int]:
    """Collapse a long Python docstring to its first paragraph. Returns (text, omitted_line_count).

    Only touches the first string statement right after a def/class line, and only
    when it is long, so short docstrings and all code are left exactly as written."""
    if suffix.lower() not in (".py", ".pyi"):
        return text, 0
    lines = text.split("\n")
    # find end of the signature: first line ending with ':' (outside of continuation)
    i = 0
    while i < len(lines) and not lines[i].rstrip().endswith(":"):
        i += 1
        if i > 12:
            return text, 0
    j = i + 1
    while j < len(lines) and lines[j].strip() == "":
        j += 1
    if j >= len(lines):
        return text, 0
    first = lines[j].lstrip()
    m = re.match(r'^[rRbBuU]{0,2}("""|\'\'\')', first)
    if not m:
        return text, 0
    q = m.group(1)
    indent = lines[j][: len(lines[j]) - len(first)]
    # single-line docstring
    if first.count(q) >= 2:
        return text, 0
    k = j + 1
    while k < len(lines) and q not in lines[k]:
        k += 1
    if k >= len(lines):
        return text, 0
    n_doc = k - j + 1
    if n_doc < min_lines:
        return text, 0
    keep = [lines[j]]
    t = j + 1
    while t < k and lines[t].strip() != "" and len(keep) < 3:
        keep.append(lines[t])
        t += 1
    omitted = k - j + 1 - len(keep) - 1
    if omitted <= 0:
        return text, 0
    note = f'{indent}... [docstring shortened: {omitted} lines omitted; docs="full" shows them]'
    new = lines[: j] + keep + [note, lines[k]] + lines[k + 1:]
    return "\n".join(new), omitted
