"""Shell-output compression. Test, build and git logs often cost more tokens than
the code they are about. `graphsnip run -- <cmd>` runs a command, keeps the
exit code, and prints a compressed version of its output. The saving is
measured exactly (raw tokens vs compressed tokens), not estimated."""
from __future__ import annotations

import re
import subprocess
import sys

from graphsnip import ledger as _ledger
from graphsnip.project import Project
from graphsnip.tokens import count_tokens

_ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")
_NOISE = re.compile(r"^\s*(\d{1,3}%|[#=>\-\.\s\|/\\█▏▎▍▌▋▊▉]{12,})\s*$")
_IMPORTANT = re.compile(r"(error|fail|failed|traceback|exception|panic|assert|warning|fatal|✗|✘|FAILED|ERROR|cannot|denied|not found)", re.I)
_SUMMARY = re.compile(r"(passed|failed|errors?|tests? ran|test suites?|total|ok\b|finished|compiled|built in|exit code)", re.I)


_NUM = re.compile(r"\d+|0x[0-9a-fA-F]+")


def _collapse_similar(lines: list[str], min_run: int = 5) -> list[str]:
    """Runs of lines identical up to numbers (progress, 'item 17 ... ok') become first, count, last."""
    out: list[str] = []
    i = 0
    while i < len(lines):
        key = _NUM.sub("#", lines[i])
        j = i + 1
        while j < len(lines) and lines[j] != "" and _NUM.sub("#", lines[j]) == key and not _IMPORTANT.search(lines[j]):
            j += 1
        run = j - i
        if run >= min_run and lines[i] != "" and not _IMPORTANT.search(lines[i]):
            out += [lines[i], f"… {run - 2} similar lines …", lines[j - 1]]
        else:
            out += lines[i:j]
        i = j
    return out


def compress(text: str, max_lines: int = 120, head: int = 20, tail: int = 30) -> str:
    text = _ANSI.sub("", text).replace("\r\n", "\n")
    text = re.sub(r"[^\n]*\r(?!\n)", "", text)  # carriage-return progress redraws
    lines = [ln.rstrip() for ln in text.split("\n")]
    # drop progress noise
    lines = [ln for ln in lines if not _NOISE.match(ln)]
    # collapse consecutive duplicates
    out: list[str] = []
    prev, n = None, 0
    for ln in lines + [None]:
        if ln == prev:
            n += 1
            continue
        if prev is not None:
            out.append(prev if n == 1 else f"{prev}  (×{n})")
        prev, n = ln, 1
    lines = _collapse_similar(out)
    # trim leading/trailing blank runs and collapse blank runs
    cleaned: list[str] = []
    for ln in lines:
        if ln == "" and cleaned and cleaned[-1] == "":
            continue
        cleaned.append(ln)
    lines = cleaned
    if len(lines) <= max_lines:
        return "\n".join(lines).strip("\n")
    keep = set(range(min(head, len(lines))))
    keep |= set(range(max(0, len(lines) - tail), len(lines)))
    budget = max_lines - len(keep)
    for i, ln in enumerate(lines):
        if budget <= 0:
            break
        if i in keep:
            continue
        if _IMPORTANT.search(ln) or _SUMMARY.search(ln):
            for j in (i - 1, i, i + 1):
                if 0 <= j < len(lines) and j not in keep and budget > 0:
                    keep.add(j)
                    budget -= 1
    res: list[str] = []
    last = -1
    for i in sorted(keep):
        if i != last + 1:
            res.append(f"… {i - last - 1} lines omitted …")
        res.append(lines[i])
        last = i
    if last != len(lines) - 1:
        res.append(f"… {len(lines) - 1 - last} lines omitted …")
    return "\n".join(res).strip("\n")


def run(argv: list[str], root: str = ".") -> int:
    if not argv:
        sys.stderr.write("usage: graphsnip run -- <command> [args...]\n")
        return 2
    try:
        r = subprocess.run(argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, errors="replace")
    except FileNotFoundError:
        sys.stderr.write(f"graphsnip run: command not found: {argv[0]}\n")
        return 127
    raw = r.stdout or ""
    small = compress(raw)
    raw_t, small_t = count_tokens(raw), count_tokens(small)
    if small_t >= raw_t:
        small, small_t = raw, raw_t
    sys.stdout.write(small + ("\n" if small and not small.endswith("\n") else ""))
    if small_t < raw_t:
        sys.stdout.write(f"[graphsnip: output compressed {raw_t} → {small_t} tokens]\n")
    try:
        _ledger.Ledger(Project(root).gs_dir).record(
            kind="call", tool="shell", query=" ".join(argv)[:160], returned=small_t, baseline=raw_t,
            files=[], source="shell", session="shell", deduped=False)
    except Exception:
        pass
    return r.returncode
