"""Tests for `MemoryRepository.reweight` (the daily maintenance job) —
against a real Postgres transaction.
"""

from datetime import datetime, timedelta, timezone

from models import Memory
from repositories import MemoryRepository


def test_reweight_boosts_frequently_accessed_memories(db_session, test_user_id):
    repo = MemoryRepository(db_session)
    m = repo.create(test_user_id, "semantic", "well-used fact", importance=0.5)
    for _ in range(5):
        repo.touch_access(m.id)

    result = repo.reweight(test_user_id, boost_access_count=5, boost_amount=0.05)
    assert result["boosted"] == 1

    db_session.refresh(m)
    assert abs(m.importance - 0.55) < 1e-9


def test_reweight_caps_boost_at_one(db_session, test_user_id):
    repo = MemoryRepository(db_session)
    m = repo.create(test_user_id, "semantic", "near-max importance", importance=0.98)
    for _ in range(5):
        repo.touch_access(m.id)

    repo.reweight(test_user_id, boost_access_count=5, boost_amount=0.05)
    db_session.refresh(m)
    assert m.importance == 1.0


def test_reweight_ignores_memories_below_the_access_threshold(db_session, test_user_id):
    repo = MemoryRepository(db_session)
    m = repo.create(test_user_id, "semantic", "rarely used fact", importance=0.5)
    repo.touch_access(m.id)  # only 1, below threshold of 5

    result = repo.reweight(test_user_id, boost_access_count=5, boost_amount=0.05)
    assert result["boosted"] == 0
    db_session.refresh(m)
    assert m.importance == 0.5


def test_reweight_decays_long_unaccessed_memories(db_session, test_user_id):
    repo = MemoryRepository(db_session)
    m = repo.create(test_user_id, "semantic", "stale fact", importance=0.5)
    stale_ts = datetime.now(timezone.utc) - timedelta(days=60)
    db_session.execute(Memory.__table__.update().where(Memory.id == m.id).values(last_accessed_at=stale_ts))

    result = repo.reweight(test_user_id, decay_after_days=30, decay_amount=0.05)
    assert result["decayed"] == 1

    db_session.refresh(m)
    assert abs(m.importance - 0.45) < 1e-9


def test_reweight_floors_decay_at_zero(db_session, test_user_id):
    repo = MemoryRepository(db_session)
    m = repo.create(test_user_id, "semantic", "already-low fact", importance=0.02)
    stale_ts = datetime.now(timezone.utc) - timedelta(days=60)
    db_session.execute(Memory.__table__.update().where(Memory.id == m.id).values(last_accessed_at=stale_ts))

    repo.reweight(test_user_id, decay_after_days=30, decay_amount=0.05)
    db_session.refresh(m)
    assert m.importance == 0.0


def test_reweight_leaves_recently_accessed_memories_alone(db_session, test_user_id):
    repo = MemoryRepository(db_session)
    m = repo.create(test_user_id, "semantic", "fresh fact", importance=0.5)
    # last_accessed_at defaults to now() on creation — well within the window.

    result = repo.reweight(test_user_id, decay_after_days=30, decay_amount=0.05)
    assert result["decayed"] == 0
    db_session.refresh(m)
    assert m.importance == 0.5


def test_reweight_only_touches_the_given_user(db_session, test_user_id):
    other_user_id = "11111111-1111-1111-1111-111111111111"
    repo = MemoryRepository(db_session)
    mine = repo.create(test_user_id, "semantic", "mine", importance=0.5)
    theirs = repo.create(other_user_id, "semantic", "theirs", importance=0.5)
    for _ in range(5):
        repo.touch_access(mine.id)
        repo.touch_access(theirs.id)

    result = repo.reweight(test_user_id, boost_access_count=5, boost_amount=0.05)
    assert result["boosted"] == 1

    db_session.refresh(theirs)
    assert theirs.importance == 0.5
