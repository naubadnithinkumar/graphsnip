"""Model routing: pick the cheapest model tier that can handle a task, and account for it honestly.

graphsnip cannot switch a host agent's model by itself (MCP servers have no such power). What it can do:
  1. decide  - score a task from its wording and from graph signals (files and tokens a context pack needs)
  2. steer   - ship a /graphsnip command pinned to the light model and a /graphsnip-deep command for the rest
  3. account - log every decision, whether the running model matched it, and the estimated dollars

Dollars are an estimate from the price table in .graphsnip/config.json ("routing.prices"); the defaults are
placeholders you should edit. Savings are credited only when the running tier matched the decision.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

TIERS = ("light", "standard", "heavy")
_RANK = {t: i for i, t in enumerate(TIERS)}

DEFAULTS = {
    "baseline_tier": "standard",  # what you would run without routing
    "prices": {"light": [1.0, 5.0], "standard": [3.0, 15.0], "heavy": [5.0, 25.0]},  # USD per million tokens [input, output]
    "models": {"claude": {"light": "haiku", "standard": "sonnet", "heavy": "opus"}, "copilot": {}, "codex": {}},
    "answer_tokens": 700,    # assumed output tokens per routed task
    "overhead_tokens": 500,  # assumed prompt/system tokens per routed task
}

_HEAVY = re.compile(r"\b(refactor\w*|migrat\w*|redesign\w*|rewrit\w*|re-?architect\w*|implement\w*|debug\w*|race condition|"
                    r"deadlock|security|vulnerab\w*|performance|optimi[sz]\w*|memory leak|concurren\w*|fix (the |this )?bug|"
                    r"add (a |an )?(new )?(feature|endpoint|module))\b", re.I)
_LIGHT = re.compile(r"\b(where|which file|find|locate|list|show|who calls|what calls|outline|summari[sz]e|explain|what is|"
                    r"what does|how does|defined|used)\b", re.I)
_MULTI = re.compile(r"(\band then\b|;|\n\s*\d+[.)])", re.I)


def config(project) -> dict:
    user = (project.config().get("routing") or {})
    cfg = {k: (dict(v) if isinstance(v, dict) else v) for k, v in DEFAULTS.items()}
    for k, v in user.items():
        if isinstance(v, dict) and isinstance(cfg.get(k), dict):
            cfg[k].update(v)
        else:
            cfg[k] = v
    return cfg


def cost(cfg: dict, tier: str, tokens_in: int, tokens_out: int) -> float:
    pin, pout = cfg["prices"].get(tier, cfg["prices"][cfg["baseline_tier"]])
    return (tokens_in * pin + tokens_out * pout) / 1_000_000


@dataclass
class Route:
    tier: str
    score: int
    reasons: list = field(default_factory=list)
    tokens_in: int = 0
    tokens_out: int = 0
    files: int = 0


def decide(task: str, pack_files: int = 0, pack_tokens: int = 0, pack_budget: int = 4000, cfg: dict | None = None) -> Route:
    score, why = 0, []
    lookup_only = not _HEAVY.search(task) and bool(_LIGHT.search(task))
    if _HEAVY.search(task):
        score += 3
        why.append(f"change/diagnosis wording ('{_HEAVY.search(task).group(0).lower()}') +3")
    elif _LIGHT.search(task):
        score -= 2
        why.append(f"lookup/explain wording ('{_LIGHT.search(task).group(0).lower()}') -2")
    if _MULTI.search(task):
        score += 1
        why.append("several steps in one request +1")
    if len(task.split()) > 40:
        score += 1
        why.append("long request +1")
    gpts, gwhy = 0, []
    if pack_files >= 6:
        gpts += 2
        gwhy.append(f"graph: {pack_files} files involved +2")
    elif pack_files >= 3:
        gpts += 1
        gwhy.append(f"graph: {pack_files} files involved +1")
    if pack_tokens >= 0.8 * pack_budget:
        gpts += 1
        gwhy.append(f"graph: large context needed ({pack_tokens:,} tokens) +1")
    if lookup_only and gpts > 1:
        # a "where/find/explain" question naturally touches many files; that alone does not make it hard
        gwhy.append(f"graph size capped at +1 for a lookup question (was +{gpts})")
        gpts = 1
    score += gpts
    why += gwhy
    tier = "light" if score <= 0 else "heavy" if score >= 4 else "standard"
    cfg = cfg or DEFAULTS
    return Route(tier=tier, score=score, reasons=why or ["no strong signal"], files=pack_files,
                 tokens_in=pack_tokens + cfg["overhead_tokens"], tokens_out=cfg["answer_tokens"])


def status_of(decided: str, running: str | None) -> str:
    if running is None:
        return "advisory"          # decision made, nobody verified which model ran
    if running == "session":
        return "session"           # ran on whatever the session uses; no routing claimed
    if running not in _RANK:
        return "advisory"
    if running == decided:
        return "routed"
    return "escalate" if _RANK[running] < _RANK[decided] else "overpowered"


def account(cfg: dict, route: Route, status: str) -> dict:
    base = cost(cfg, cfg["baseline_tier"], route.tokens_in, route.tokens_out)
    chosen = cost(cfg, route.tier, route.tokens_in, route.tokens_out)
    delta = round(base - chosen, 6)  # negative when the task needed a pricier model than the baseline
    return {"usd_baseline": round(base, 6), "usd_routed": round(chosen, 6),
            "usd_saved": delta if status == "routed" else 0.0,
            "usd_potential": delta if status in ("advisory", "session") else 0.0}


def summarize(records: list[dict], cfg: dict) -> dict:
    rows = [r for r in records if r.get("kind") == "route"]
    by_tier = {t: 0 for t in TIERS}
    by_status: dict[str, int] = {}
    for r in rows:
        by_tier[r.get("tier", "standard")] = by_tier.get(r.get("tier", "standard"), 0) + 1
        by_status[r.get("status", "advisory")] = by_status.get(r.get("status", "advisory"), 0) + 1
    routed = [r for r in rows if r.get("status") == "routed"]
    cheaper = [r for r in routed if _RANK.get(r["tier"], 1) < _RANK[cfg["baseline_tier"]]]
    return {
        "decisions": len(rows),
        "by_tier": by_tier,
        "by_status": by_status,
        "routed": len(routed),
        "routed_cheaper": len(cheaper),
        "tokens_on_cheaper": sum(int(r.get("tokens_in", 0)) + int(r.get("tokens_out", 0)) for r in cheaper),
        "usd_saved": round(sum(float(r.get("usd_saved", 0)) for r in routed), 4),
        "usd_potential": round(sum(float(r.get("usd_potential", 0)) for r in rows if r.get("status") in ("advisory", "session")), 4),
        "escalations": by_status.get("escalate", 0),
        "recent": list(reversed(rows[-8:])),
    }
