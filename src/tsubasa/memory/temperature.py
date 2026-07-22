"""Temperature scoring: hot / warm / cold (DESIGN.md §3.4).

    temp(k) = w_r·recency + w_i·impact + w_d·domain_weight + w_a·access

Recency decays exponentially (configurable half-life) from the item's
last_touched timestamp — so an old ADR cited by yesterday's incident is
hot again (re-heating comes free from assemble.touch()).
"""

from __future__ import annotations

import math
from datetime import datetime, timezone

from ..config import CaptainConfig
from ..models import Entity, Task, parse_ts

IMPACT_SCORE = {"high": 1.0, "medium": 0.6, "low": 0.3}
HOT_THRESHOLD = 0.55
WARM_THRESHOLD = 0.30


def recency(last_touched: str, half_life_days: float, now: datetime | None = None) -> float:
    if not last_touched:
        return 0.0
    now = now or datetime.now(timezone.utc)
    age_days = max(0.0, (now - parse_ts(last_touched)).total_seconds() / 86400)
    return math.pow(0.5, age_days / half_life_days)


def domain_weight(domains: list[str], cfg: CaptainConfig) -> float:
    if not cfg.domains:
        return 0.5  # no configured domains: everything is mid-weight
    weights = [cfg.domains.get(d, 0.0) for d in domains]
    return max(weights) if weights else 0.0


def score_entity(e: Entity, cfg: CaptainConfig, now: datetime | None = None) -> float:
    w = cfg.weights
    s = (
        w["recency"] * recency(e.last_touched, cfg.half_life_days, now)
        + w["impact"] * IMPACT_SCORE.get(e.impact, 0.3)
        + w["domain"] * domain_weight(e.domains, cfg)
        + w["access"] * 0.0  # access tracking lands post-v0.1
    )
    if e.status in ("superseded", "achieved", "dropped"):
        s *= 0.3  # resolved knowledge cools hard but stays traversable
    elif e.type == "goal" or (e.type == "doc" and e.impact == "high"):
        s = max(s, HOT_THRESHOLD)  # open goals and high-impact docs
        # (principles) don't lose relevance with age, only with resolution
    return s


def score_task(t: Task, cfg: CaptainConfig, now: datetime | None = None) -> float:
    if t.state in ("done", "abandoned"):
        base = 0.15
    else:
        base = 1.0  # in-flight work is always top of mind
    return base * (0.5 + 0.5 * recency(t.updated or t.created, cfg.half_life_days, now)) \
        + cfg.weights["domain"] * domain_weight(t.domains, cfg)


def tier_of(score: float) -> str:
    if score >= HOT_THRESHOLD:
        return "hot"
    if score >= WARM_THRESHOLD:
        return "warm"
    return "cold"


def estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4)
