"""Node-level + end-to-end tests for the retrieval graph. Embeddings use the
real `LocalHashEmbeddingProvider` default (no OPENAI_API_KEY needed) — since it's
deterministic, identical query/content text always yields similarity 1.0,
which is exactly what these tests lean on.
"""

from repositories import MemoryRepository

from memory.embeddings import embed_texts
from memory.retrieval_graph import assemble_context, run_retrieval, vector_retrieve


def _config(session):
    return {"configurable": {"session": session}}


# --- vector_retrieve (node) --------------------------------------------------

def test_vector_retrieve_returns_no_candidates_when_user_has_no_memories(db_session, test_user_id):
    state = {"user_id": test_user_id, "query": "anything", "candidate_pool": 30}
    out = vector_retrieve(state, _config(db_session))
    assert out == {"candidates": []}


def test_vector_retrieve_finds_matching_memories(db_session, test_user_id):
    repo = MemoryRepository(db_session)
    content = "user has a cat named Mochi"
    repo.create(test_user_id, "semantic", content, embedding=embed_texts([content])[0])

    state = {"user_id": test_user_id, "query": content, "candidate_pool": 30}
    out = vector_retrieve(state, _config(db_session))
    assert len(out["candidates"]) == 1
    assert out["candidates"][0]["content"] == "user has a cat named Mochi"
    assert out["candidates"][0]["similarity"] > 0.999


def test_vector_retrieve_on_empty_query_short_circuits(db_session, test_user_id):
    state = {"user_id": test_user_id, "query": "   ", "candidate_pool": 30}
    out = vector_retrieve(state, _config(db_session))
    assert out == {"candidates": []}


# --- assemble_context (node) -------------------------------------------------

def test_assemble_context_bumps_access_count_and_formats_output(db_session, test_user_id):
    repo = MemoryRepository(db_session)
    m = repo.create(test_user_id, "semantic", "user likes tea", embedding=[1.0] + [0.0] * 1535)
    assert m.access_count == 0

    state = {
        "ranked": [
            {
                "memory_id": m.id,
                "content": m.content,
                "memory_type": m.memory_type,
                "similarity": 0.9,
                "importance": m.importance,
                "score": 0.85,
                "created_at": m.created_at,
            }
        ]
    }
    out = assemble_context(state, _config(db_session))
    assert len(out["assembled_context"]) == 1
    result = out["assembled_context"][0]
    assert result["memory_id"] == m.id
    assert result["content"] == "user likes tea"
    assert isinstance(result["created_at"], str)  # serialized, not a raw datetime

    db_session.refresh(m)
    assert m.access_count == 1


def test_assemble_context_on_no_ranked_candidates_yields_empty_list(db_session):
    out = assemble_context({"ranked": []}, _config(db_session))
    assert out == {"assembled_context": []}


# --- run_retrieval (whole graph, against a real Postgres transaction) ------

def test_run_retrieval_end_to_end(db_session, test_user_id):
    repo = MemoryRepository(db_session)
    color_fact = "user's favorite color is teal"
    trip_fact = "user went to Tokyo last spring"
    repo.create(test_user_id, "semantic", color_fact, embedding=embed_texts([color_fact])[0])
    repo.create(test_user_id, "episodic", trip_fact, embedding=embed_texts([trip_fact])[0])

    results = run_retrieval(db_session, test_user_id, color_fact, top_k=6)
    assert len(results) >= 1
    assert results[0]["content"] == "user's favorite color is teal"
    assert results[0]["similarity"] > 0.999


def test_run_retrieval_respects_top_k(db_session, test_user_id):
    repo = MemoryRepository(db_session)
    for i in range(5):
        repo.create(test_user_id, "semantic", f"fact number {i}", embedding=[1.0] + [0.0] * 1535)

    results = run_retrieval(db_session, test_user_id, "fact number 0", top_k=2)
    assert len(results) == 2
