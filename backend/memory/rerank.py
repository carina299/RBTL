"""Memory reranking: `score = w1*similarity + w2*recency + w3*importance`.
Pulled out as its own pure function — no DB, no network — because it's the
piece of the retrieval graph most worth pinning down with unit tests.
"""

import math
from datetime import datetime, timezone
from typing import TypedDict

# similarity dominates (it's the actual relevance signal); recency and
# importance are tie-breakers/boosts on top of it.
WEIGHT_SIMILARITY = 0.6
WEIGHT_RECENCY = 0.2
WEIGHT_IMPORTANCE = 0.2

# Recency uses exponential decay off the memory's `created_at` (when the fact
# was formed, not when it was last retrieved) — a two-week-old memory is
# "recent", a six-month-old one much less so, but never zero.
RECENCY_HALF_LIFE_DAYS = 14.0


class Candidate(TypedDict):
    memory_id: int
    content: str
    memory_type: str
    similarity: float
    importance: float
    created_at: datetime


def recency_score(created_at: datetime, now: datetime | None = None) -> float:
    now = now or datetime.now(timezone.utc)
    age_days = max((now - created_at).total_seconds() / 86400.0, 0.0)
    return math.pow(0.5, age_days / RECENCY_HALF_LIFE_DAYS)


def rerank(
    candidates: list[Candidate],
    top_k: int,
    now: datetime | None = None,
    weights: tuple[float, float, float] = (WEIGHT_SIMILARITY, WEIGHT_RECENCY, WEIGHT_IMPORTANCE),
) -> list[dict]:
    """Score and sort `candidates` (highest first), returning the top `top_k`
    with `score` and `recency` added to each. Empty input yields [].
    """
    now = now or datetime.now(timezone.utc)
    w_sim, w_recency, w_importance = weights
    scored = []
    for c in candidates:
        recency = recency_score(c["created_at"], now)
        score = w_sim * c["similarity"] + w_recency * recency + w_importance * c["importance"]
        scored.append({**c, "recency": recency, "score": score})
    scored.sort(key=lambda c: c["score"], reverse=True)
    return scored[:top_k]
