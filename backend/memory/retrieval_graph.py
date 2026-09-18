"""LangGraph retrieval graph — the RAG pipeline behind the
`memory_search` MCP tool. Called on-demand, by the agent's own choice, not on
a fixed schedule (that's the extraction_graph's job, see extraction_graph.py).

Graph shape (`rewrite_query` is explicitly skipped for now — limited benefit
for the current single-user scenario, revisit post-MVP):

    vector_retrieve -> rerank -> assemble_context

1. vector_retrieve: embed the query, pull the top ~30 candidates by cosine
   similarity.
2. rerank: score = w1*similarity + w2*recency + w3*importance, keep top_k
   (memory.rerank — its own module since that's the piece worth unit-testing).
3. assemble_context: bump access_count/last_accessed_at on whatever was
   actually returned, then format the result.
"""

from typing import TypedDict

from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, StateGraph
from sqlalchemy.orm import Session

from memory.embeddings import embed_texts
from memory.rerank import rerank as rerank_candidates
from repositories import MemoryRepository

DEFAULT_CANDIDATE_POOL = 30


class RetrievalState(TypedDict):
    user_id: str
    query: str
    top_k: int
    candidate_pool: int
    candidates: list[dict]
    ranked: list[dict]
    assembled_context: list[dict]


def vector_retrieve(state: RetrievalState, config: RunnableConfig) -> dict:
    session: Session = config["configurable"]["session"]
    query = (state["query"] or "").strip()
    if not query:
        return {"candidates": []}
    embedding = embed_texts([query])[0]
    matches = MemoryRepository(session).search_candidates(
        state["user_id"], embedding, limit=state["candidate_pool"]
    )
    candidates = [
        {
            "memory_id": m.id,
            "content": m.content,
            "memory_type": m.memory_type,
            "similarity": similarity,
            "importance": m.importance,
            "created_at": m.created_at,
        }
        for m, similarity in matches
    ]
    return {"candidates": candidates}


def rerank(state: RetrievalState, config: RunnableConfig) -> dict:
    if not state["candidates"]:
        return {"ranked": []}
    return {"ranked": rerank_candidates(state["candidates"], state["top_k"])}


def assemble_context(state: RetrievalState, config: RunnableConfig) -> dict:
    session: Session = config["configurable"]["session"]
    repo = MemoryRepository(session)
    assembled = []
    for item in state["ranked"]:
        repo.touch_access(item["memory_id"])
        assembled.append(
            {
                "memory_id": item["memory_id"],
                "content": item["content"],
                "memory_type": item["memory_type"],
                "score": item["score"],
                "similarity": item["similarity"],
                "importance": item["importance"],
                "created_at": item["created_at"].isoformat(),
            }
        )
    return {"assembled_context": assembled}


def _build_retrieval_graph():
    graph = StateGraph(RetrievalState)
    graph.add_node("vector_retrieve", vector_retrieve)
    graph.add_node("rerank", rerank)
    graph.add_node("assemble_context", assemble_context)

    graph.set_entry_point("vector_retrieve")
    graph.add_edge("vector_retrieve", "rerank")
    graph.add_edge("rerank", "assemble_context")
    graph.add_edge("assemble_context", END)
    return graph.compile()


retrieval_graph = _build_retrieval_graph()


def run_retrieval(
    session: Session, user_id: str, query: str, top_k: int = 6, candidate_pool: int = DEFAULT_CANDIDATE_POOL
) -> list[dict]:
    """Entry point the `memory_search` MCP tool (and the `/memory/retrieve`
    debug REST endpoint) call.
    """
    initial_state: RetrievalState = {
        "user_id": user_id,
        "query": query,
        "top_k": top_k,
        "candidate_pool": candidate_pool,
        "candidates": [],
        "ranked": [],
        "assembled_context": [],
    }
    final_state = retrieval_graph.invoke(initial_state, config={"configurable": {"session": session}})
    return final_state["assembled_context"]
