"""Measured model mix from Claude Code session transcripts (~/.claude/projects/<project>/*.jsonl).

This is the check on routing: it reads which model actually produced each reply and its token usage.
Only Claude Code is supported; Copilot does not expose usage and Codex support is not implemented.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

_FAMILY_TIER = {"haiku": "light", "sonnet": "standard", "opus": "heavy"}


def transcripts_dir(root: Path) -> Path:
    base = Path(os.environ.get("CLAUDE_CONFIG_DIR") or (Path.home() / ".claude"))
    return base / "projects" / re.sub(r"[^A-Za-z0-9]", "-", str(root))


def family(model: str) -> str:
    m = (model or "").lower()
    for f in _FAMILY_TIER:
        if f in m:
            return f
    return "other"


def scan(root: Path, cfg: dict, directory: Path | None = None) -> dict | None:
    d = directory or transcripts_dir(root)
    if not d.is_dir():
        return None
    seen: set = set()
    fam: dict[str, dict] = {}
    for f in d.glob("*.jsonl"):
        try:
            lines = f.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        for ln in lines:
            try:
                rec = json.loads(ln)
            except Exception:
                continue
            msg = rec.get("message") if isinstance(rec, dict) else None
            if not isinstance(msg, dict) or not msg.get("model") or not isinstance(msg.get("usage"), dict):
                continue
            key = (msg.get("id"), rec.get("requestId"))
            if key != (None, None):
                if key in seen:
                    continue  # streamed replies are logged more than once
                seen.add(key)
            u = msg["usage"]
            row = fam.setdefault(family(msg["model"]), {"messages": 0, "input": 0, "output": 0, "cache_read": 0})
            row["messages"] += 1
            row["input"] += int(u.get("input_tokens", 0)) + int(u.get("cache_creation_input_tokens", 0))
            row["output"] += int(u.get("output_tokens", 0))
            row["cache_read"] += int(u.get("cache_read_input_tokens", 0))
    if not fam:
        return None
    total = 0.0
    for name, row in fam.items():
        tier = _FAMILY_TIER.get(name, cfg["baseline_tier"])
        pin, pout = cfg["prices"][tier]
        row["usd"] = round((row["input"] * pin + row["output"] * pout + row["cache_read"] * pin * 0.1) / 1_000_000, 4)
        total += row["usd"]
    return {"families": fam, "usd_total": round(total, 4), "dir": str(d)}
