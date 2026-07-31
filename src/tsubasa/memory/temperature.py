"""Temperature scoring: hot / warm / cold (DESIGN.md §3.4).

    temp(k) = w_r·recency + w_i·impact + w_d·domain_weight + w_a·access

Recency decays exponentially (configurable half-life) from the item's
last_touched timestamp — so an old ADR cited by yesterday's incident is
hot again (re-heating comes free from assemble.touch()).
"""

from __future__ import annotations

import math
import re
from datetime import datetime, timezone

from ..config import CaptainConfig
from ..models import Entity, parse_ts

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


def tier_of(score: float) -> str:
    if score >= HOT_THRESHOLD:
        return "hot"
    if score >= WARM_THRESHOLD:
        return "warm"
    return "cold"


# Calibrated against measured Claude tokenization of two real hot.md files
# (60,511 B -> 26,300 tok; 9,583 B -> 5,110 tok). The old len//4 rule reported
# 47-58% of that: memory tiers are id-dense (evt-design-tablespaces-6bf927b0),
# and hyphenated ids with hex suffixes split far harder than prose. A budget
# guard must never under-count, so the piece count carries a margin and errs high.
_PIECE = re.compile(r"[A-Za-z]+|\d+|[^\sA-Za-z0-9]")
_TOKEN_MARGIN = 1.2


def estimate_tokens(text: str) -> int:
    """Approximate token count: alphanumeric runs split roughly every 3
    characters, each punctuation character stands alone."""
    n = 0
    for m in _PIECE.finditer(text):
        run = m.group(0)
        n += -(-len(run) // 3) if run[0].isalnum() else 1
    return max(1, int(n * _TOKEN_MARGIN))
