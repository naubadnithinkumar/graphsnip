"""Savings ledger: an append-only JSONL log of every tool call with the tokens
returned and the tokens a grep-and-read agent would have spent instead."""
from __future__ import annotations

import json
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path


class Ledger:
    def __init__(self, gs_dir: Path):
        self.dir = Path(gs_dir)
        self.path = self.dir / "ledger.jsonl"

    def record(self, **fields) -> None:
        try:
            self.dir.mkdir(parents=True, exist_ok=True)
            rec = {"ts": datetime.now(timezone.utc).isoformat(timespec="seconds"), **fields}
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
        except Exception:
            pass  # never break a tool call because the ledger could not be written

    def read(self) -> list[dict]:
        if not self.path.exists():
            return []
        out = []
        for ln in self.path.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                out.append(json.loads(ln))
            except Exception:
                continue
        return out

    def clear(self, source: str | None = None) -> None:
        if source is None:
            if self.path.exists():
                self.path.unlink()
            return
        keep = [r for r in self.read() if r.get("source") != source]
        self.dir.mkdir(parents=True, exist_ok=True)
        self.path.write_text("".join(json.dumps(r) + "\n" for r in keep), encoding="utf-8")


def summarize(records: list[dict], price_per_mtok: float = 3.0, sources: set[str] | None = None) -> dict:
    """Aggregate ledger records. `sources` filters by record['source'] (agent|cli|bench|shell)."""
    rows = [r for r in records if r.get("kind", "call") == "call" and (sources is None or r.get("source") in sources)]
    hooks = [r for r in records if r.get("kind") == "hook"]
    returned = sum(int(r.get("returned", 0)) for r in rows)
    baseline = sum(int(r.get("baseline", 0)) for r in rows)
    saved = baseline - returned
    by_tool: dict[str, dict] = defaultdict(lambda: {"calls": 0, "returned": 0, "baseline": 0})
    for r in rows:
        t = by_tool[r.get("tool", "?")]
        t["calls"] += 1
        t["returned"] += int(r.get("returned", 0))
        t["baseline"] += int(r.get("baseline", 0))
    cum_b = cum_r = 0
    timeline = []
    for r in rows:
        cum_b += int(r.get("baseline", 0))
        cum_r += int(r.get("returned", 0))
        timeline.append({"ts": r.get("ts"), "baseline": cum_b, "returned": cum_r})
    hook_counts: dict[str, int] = defaultdict(int)
    for h in hooks:
        hook_counts[f"{h.get('tool', '?')}:{h.get('action', '?')}"] += 1
    return {
        "calls": len(rows),
        "returned": returned,
        "baseline": baseline,
        "saved": saved,
        "pct": round(100 * saved / baseline, 1) if baseline > 0 else 0.0,
        "ratio": round(baseline / returned, 1) if returned > 0 else 0.0,
        "usd_saved": round(saved * price_per_mtok / 1_000_000, 4),
        "deduped": sum(1 for r in rows if r.get("deduped")),
        "by_tool": dict(by_tool),
        "timeline": timeline,
        "recent": list(reversed(rows[-12:])),
        "hooks": dict(hook_counts),
        "first_ts": rows[0].get("ts") if rows else None,
        "last_ts": rows[-1].get("ts") if rows else None,
    }


def now() -> float:
    return time.time()
