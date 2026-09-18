"""Unit tests for the rerank scoring function. Pure function, no DB, no network.
"""

from datetime import datetime, timedelta, timezone

from memory.rerank import RECENCY_HALF_LIFE_DAYS, recency_score, rerank

NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _candidate(memory_id, similarity, importance, age_days):
    return {
        "memory_id": memory_id,
        "content": f"memory {memory_id}",
        "memory_type": "semantic",
        "similarity": similarity,
        "importance": importance,
        "created_at": NOW - timedelta(days=age_days),
    }


def test_recency_score_is_one_at_zero_age():
    assert recency_score(NOW, now=NOW) == 1.0


def test_recency_score_decays_with_age():
    fresh = recency_score(NOW - timedelta(days=1), now=NOW)
    old = recency_score(NOW - timedelta(days=90), now=NOW)
    assert fresh > old
    assert 0.0 < old < 1.0


def test_recency_score_halves_at_the_half_life():
    half = recency_score(NOW - timedelta(days=RECENCY_HALF_LIFE_DAYS), now=NOW)
    assert abs(half - 0.5) < 1e-9


def test_recency_score_never_negative_for_future_created_at():
    # clock skew / same-transaction edge case: created_at slightly "after" now
    future = recency_score(NOW + timedelta(seconds=5), now=NOW)
    assert future == 1.0


def test_rerank_orders_by_score_not_raw_similarity():
    # candidate A: highest similarity but old and unimportant
    a = _candidate(1, similarity=0.95, importance=0.1, age_days=365)
    # candidate B: lower similarity but very recent and important
    b = _candidate(2, similarity=0.70, importance=0.9, age_days=0)
    ranked = rerank([a, b], top_k=2, now=NOW)
    assert [c["memory_id"] for c in ranked] == [2, 1]


def test_rerank_respects_top_k():
    candidates = [_candidate(i, similarity=0.9, importance=0.5, age_days=1) for i in range(5)]
    ranked = rerank(candidates, top_k=2, now=NOW)
    assert len(ranked) == 2


def test_rerank_adds_score_and_recency_fields():
    a = _candidate(1, similarity=0.8, importance=0.5, age_days=0)
    ranked = rerank([a], top_k=1, now=NOW)
    assert "score" in ranked[0] and "recency" in ranked[0]
    assert ranked[0]["recency"] == 1.0


def test_rerank_empty_input_yields_empty_output():
    assert rerank([], top_k=6, now=NOW) == []


def test_rerank_custom_weights_change_ordering():
    # same setup as the "score not raw similarity" test, but weight similarity
    # so heavily that the raw-similarity leader wins instead.
    a = _candidate(1, similarity=0.95, importance=0.1, age_days=365)
    b = _candidate(2, similarity=0.70, importance=0.9, age_days=0)
    ranked = rerank([a, b], top_k=2, now=NOW, weights=(0.98, 0.01, 0.01))
    assert [c["memory_id"] for c in ranked] == [1, 2]
