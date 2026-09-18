"""Node-level + end-to-end tests for the extraction graph. LLM extraction is
monkeypatched (no
ANTHROPIC_API_KEY needed); embeddings use the real `LocalHashEmbeddingProvider`
default (no OPENAI_API_KEY needed either) — deterministic, so identical
candidate text always dedups, which is exactly what these tests exercise.
"""

from repositories import MemoryRepository, MessageRepository

from memory.extraction_graph import (
    extract_candidates,
    retrieve_similar,
    route_decisions,
    run_extraction,
)


def _config(session):
    return {"configurable": {"session": session}}


# --- retrieve_similar (node) -----------------------------------------------

def test_retrieve_similar_reports_no_match_when_user_has_no_memories(db_session, test_user_id):
    state = {
        "user_id": test_user_id,
        "embedded": [{"candidate": {"memory_type": "semantic"}, "embedding": [0.1] * 1536}],
    }
    out = retrieve_similar(state, _config(db_session))
    assert out == {"similar_matches": {0: {}}}


def test_retrieve_similar_finds_the_nearest_active_memory_of_the_same_tier(db_session, test_user_id):
    repo = MemoryRepository(db_session)
    vec = [1.0] + [0.0] * 1535
    existing = repo.create(test_user_id, "semantic", "user has a cat", embedding=vec)

    state = {
        "user_id": test_user_id,
        "embedded": [{"candidate": {"memory_type": "semantic"}, "embedding": vec}],
    }
    out = retrieve_similar(state, _config(db_session))
    match = out["similar_matches"][0]
    assert match["memory_id"] == existing.id
    assert match["content"] == "user has a cat"
    assert match["similarity"] > 0.999


def test_retrieve_similar_ignores_a_different_memory_type(db_session, test_user_id):
    repo = MemoryRepository(db_session)
    vec = [1.0] + [0.0] * 1535
    repo.create(test_user_id, "episodic", "user went to Tokyo", embedding=vec)

    state = {
        "user_id": test_user_id,
        "embedded": [{"candidate": {"memory_type": "semantic"}, "embedding": vec}],
    }
    out = retrieve_similar(state, _config(db_session))
    assert out["similar_matches"][0] == {}


# --- route_decisions (the graph's conditional edge) -------------------------

def test_route_decisions_fans_out_one_send_per_candidate_to_the_right_node():
    state = {
        "user_id": "u1",
        "embedded": [
            {"candidate": {"content": "new fact", "memory_type": "semantic"}, "embedding": [0.0]},
            {"candidate": {"content": "same fact", "memory_type": "semantic"}, "embedding": [0.0]},
            {"candidate": {"content": "changed fact", "memory_type": "semantic"}, "embedding": [0.0]},
        ],
        "similar_matches": {
            0: {},  # no match -> insert
            1: {"memory_id": 11, "content": "same fact", "similarity": 0.99},  # dup -> discard
            2: {"memory_id": 22, "content": "old fact", "similarity": 0.95},  # changed -> update
        },
    }
    sends = route_decisions(state, {"configurable": {}})
    nodes = {s.arg["candidate"]["content"]: s.node for s in sends}
    assert nodes == {
        "new fact": "insert_memory",
        "same fact": "discard",
        "changed fact": "update_memory",
    }


def test_route_decisions_on_no_candidates_returns_no_sends():
    assert route_decisions({"embedded": [], "similar_matches": {}, "user_id": "u1"}, {"configurable": {}}) == []


# --- run_extraction (whole graph, against a real Postgres transaction) -----

def test_run_extraction_inserts_then_discards_a_repeated_candidate(db_session, test_user_id, monkeypatch):
    repo = MessageRepository(db_session)
    repo.create("in", "user", "I have a cat named Mochi", {})
    repo.create("out", "reply", "That's a cute name!", {})

    candidate = {"content": "user has a cat named Mochi", "memory_type": "semantic", "importance": 0.6}
    monkeypatch.setattr("memory.extraction_graph.extract_memory_candidates", lambda text: [dict(candidate)])

    first = run_extraction(db_session, test_user_id, limit=10)
    assert len(first) == 1
    assert first[0]["action"] == "insert"

    second = run_extraction(db_session, test_user_id, limit=10)
    assert len(second) == 1
    assert second[0]["action"] == "discard"

    active = MemoryRepository(db_session).list_active(test_user_id, memory_type="semantic")
    assert [m.content for m in active] == ["user has a cat named Mochi"]


def test_extract_candidates_short_circuits_on_empty_dialogue(monkeypatch):
    # Note: this is a direct node-level test, not a run_extraction() one — the
    # live `messages` table always has real history in it, so there's no way
    # to make fetch_recent_dialogue actually see "no dialogue" end-to-end.
    monkeypatch.setattr(
        "memory.extraction_graph.extract_memory_candidates",
        lambda text: (_ for _ in ()).throw(AssertionError("should not be called on empty dialogue")),
    )
    out = extract_candidates({"dialogue_text": "", "source_message_ids": []}, {"configurable": {}})
    assert out == {"candidates": []}
