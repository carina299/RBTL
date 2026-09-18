"""Tests for the `memory_remember` explicit write path —
against a real Postgres transaction, embeddings via the deterministic local
provider (no API key needed).
"""

import pytest

from memory.remember import remember
from repositories import MemoryRepository


def test_remember_inserts_a_new_memory(db_session, test_user_id):
    result = remember(db_session, test_user_id, "user's favorite color is teal")
    assert result["action"] == "insert"
    memory = MemoryRepository(db_session).get(result["memory_id"])
    assert memory.content == "user's favorite color is teal"
    assert memory.source == "explicit"
    assert memory.status == "active"


def test_remember_discards_an_exact_repeat(db_session, test_user_id):
    first = remember(db_session, test_user_id, "user's favorite color is teal")
    second = remember(db_session, test_user_id, "user's favorite color is teal")
    assert second["action"] == "discard"
    assert second["memory_id"] == first["memory_id"]

    active = MemoryRepository(db_session).list_active(test_user_id)
    assert len(active) == 1


def test_remember_updates_when_embedding_collides_but_content_changed(db_session, test_user_id, monkeypatch):
    # A real embedder maps different text to different (if nearby) vectors;
    # forcing an identical vector here isolates the "same slot, new content"
    # branch of decide_action deterministically, without depending on any
    # particular embedding model's notion of "similar enough".
    fixed_vector = [1.0] + [0.0] * 1535
    monkeypatch.setattr("memory.remember.embed_texts", lambda texts: [fixed_vector for _ in texts])

    first = remember(db_session, test_user_id, "user's cat is named Mochi")
    second = remember(db_session, test_user_id, "user's cat is named Biscuit (corrected)")

    assert second["action"] == "update"
    assert second["superseded_id"] == first["memory_id"]

    repo = MemoryRepository(db_session)
    assert repo.get(first["memory_id"]).status == "superseded"
    active = repo.list_active(test_user_id)
    assert [m.content for m in active] == ["user's cat is named Biscuit (corrected)"]


def test_remember_rejects_empty_content(db_session, test_user_id):
    with pytest.raises(ValueError):
        remember(db_session, test_user_id, "   ")


def test_remember_rejects_invalid_memory_type(db_session, test_user_id):
    with pytest.raises(ValueError):
        remember(db_session, test_user_id, "some fact", memory_type="not_a_real_type")
