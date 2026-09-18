"""LangGraph extraction/write graph — the background
fallback path that periodically distills recent dialogue into memories,
independent of whether the agent called `memory_remember` mid-conversation
(that's `memory.remember`, which reuses `dedup.decide_action` from here so
the two write paths never disagree about what counts as a duplicate).

Graph shape, matching the doc's node list:

    fetch_recent_dialogue -> extract_candidates -> embed_candidates
        -> retrieve_similar --[conditional edge: route_decisions]--> update_memory
                                                                    | insert_memory
                                                                    | discard

`retrieve_similar` is a plain node: for every candidate it looks up the
nearest existing memory of the same tier and records the match (or lack of
one). `route_decisions` is the graph's one real conditional edge — it runs
`dedup.decide_action` per candidate and fans out one `Send` per candidate to
whichever node handles that action, so a single batch with mixed outcomes
(some updates, some inserts, some discards) resolves in one graph run rather
than the whole batch being forced down one branch.
"""

import operator
from typing import Annotated, TypedDict

from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, StateGraph
from langgraph.types import Send
from sqlalchemy.orm import Session

from memory.dedup import decide_action
from memory.embeddings import embed_texts
from memory.llm import extract_memory_candidates
from repositories import MemoryRepository, MessageRepository

DEFAULT_DIALOGUE_LIMIT = 40  # recent messages fed to the extractor per run

_ACTION_NODE = {"update": "update_memory", "insert": "insert_memory", "discard": "discard"}


class ExtractionState(TypedDict):
    user_id: str
    limit: int
    dialogue_text: str
    source_message_ids: list[int]
    candidates: list[dict]                       # [{content, memory_type, importance, source_message_ids}]
    embedded: list[dict]                          # [{candidate, embedding}]
    similar_matches: dict[int, dict]              # candidate idx -> {memory_id, content, similarity} | {}
    results: Annotated[list[dict], operator.add]  # outcomes from whichever action node(s) ran


class CandidateState(TypedDict):
    """Per-candidate payload routed via `Send`; also `memory.remember`'s state
    for the single-candidate `memory_remember` path.
    """

    user_id: str
    candidate: dict
    embedding: list[float]
    similar_id: int | None
    similar_content: str | None
    similarity: float | None
    results: Annotated[list[dict], operator.add]


# --- nodes -------------------------------------------------------------

def fetch_recent_dialogue(state: ExtractionState, config: RunnableConfig) -> dict:
    session: Session = config["configurable"]["session"]
    messages = MessageRepository(session).recent(state["limit"])
    lines = [f"{'user' if m.from_role == 'human' else 'companion'}: {m.text}" for m in messages if m.text]
    return {
        "dialogue_text": "\n".join(lines),
        "source_message_ids": [m.id for m in messages],
    }


def extract_candidates(state: ExtractionState, config: RunnableConfig) -> dict:
    if not state["dialogue_text"]:
        return {"candidates": []}
    candidates = extract_memory_candidates(state["dialogue_text"])
    for c in candidates:
        c["source_message_ids"] = state["source_message_ids"]
    return {"candidates": candidates}


def embed_candidates(state: ExtractionState, config: RunnableConfig) -> dict:
    candidates = state["candidates"]
    if not candidates:
        return {"embedded": []}
    vectors = embed_texts([c["content"] for c in candidates])
    return {"embedded": [{"candidate": c, "embedding": v} for c, v in zip(candidates, vectors)]}


def retrieve_similar(state: ExtractionState, config: RunnableConfig) -> dict:
    session: Session = config["configurable"]["session"]
    repo = MemoryRepository(session)
    similar_matches: dict[int, dict] = {}
    for idx, item in enumerate(state["embedded"]):
        candidate = item["candidate"]
        match = repo.find_most_similar(state["user_id"], candidate["memory_type"], item["embedding"])
        if match:
            memory, similarity = match
            similar_matches[idx] = {"memory_id": memory.id, "content": memory.content, "similarity": similarity}
        else:
            similar_matches[idx] = {}
    return {"similar_matches": similar_matches}


def route_decisions(state: ExtractionState, config: RunnableConfig) -> list[Send]:
    sends = []
    for idx, item in enumerate(state["embedded"]):
        candidate, embedding = item["candidate"], item["embedding"]
        match = state["similar_matches"].get(idx) or {}
        action = decide_action(match.get("similarity"), candidate["content"], match.get("content"))
        sends.append(
            Send(
                _ACTION_NODE[action],
                CandidateState(
                    user_id=state["user_id"],
                    candidate=candidate,
                    embedding=embedding,
                    similar_id=match.get("memory_id"),
                    similar_content=match.get("content"),
                    similarity=match.get("similarity"),
                    results=[],
                ),
            )
        )
    return sends


def insert_memory(state: CandidateState, config: RunnableConfig) -> dict:
    session: Session = config["configurable"]["session"]
    c = state["candidate"]
    m = MemoryRepository(session).create(
        user_id=state["user_id"],
        memory_type=c["memory_type"],
        content=c["content"],
        embedding=state["embedding"],
        importance=c.get("importance", 0.5),
        source="auto",
        source_message_ids=c.get("source_message_ids"),
    )
    return {"results": [{"action": "insert", "memory_id": m.id, "content": c["content"]}]}


def update_memory(state: CandidateState, config: RunnableConfig) -> dict:
    session: Session = config["configurable"]["session"]
    repo = MemoryRepository(session)
    c = state["candidate"]
    new_memory = repo.create(
        user_id=state["user_id"],
        memory_type=c["memory_type"],
        content=c["content"],
        embedding=state["embedding"],
        importance=c.get("importance", 0.5),
        source="auto",
        source_message_ids=c.get("source_message_ids"),
    )
    repo.supersede(state["similar_id"], new_memory)
    return {
        "results": [
            {"action": "update", "memory_id": new_memory.id, "superseded_id": state["similar_id"], "content": c["content"]}
        ]
    }


def discard(state: CandidateState, config: RunnableConfig) -> dict:
    c = state.get("candidate") or {}
    return {"results": [{"action": "discard", "similar_id": state.get("similar_id"), "content": c.get("content", "")}]}


# --- graph ---------------------------------------------------------------

def _build_extraction_graph():
    graph = StateGraph(ExtractionState)
    graph.add_node("fetch_recent_dialogue", fetch_recent_dialogue)
    graph.add_node("extract_candidates", extract_candidates)
    graph.add_node("embed_candidates", embed_candidates)
    graph.add_node("retrieve_similar", retrieve_similar)
    graph.add_node("update_memory", update_memory)
    graph.add_node("insert_memory", insert_memory)
    graph.add_node("discard", discard)

    graph.set_entry_point("fetch_recent_dialogue")
    graph.add_edge("fetch_recent_dialogue", "extract_candidates")
    graph.add_edge("extract_candidates", "embed_candidates")
    graph.add_edge("embed_candidates", "retrieve_similar")
    graph.add_conditional_edges(
        "retrieve_similar", route_decisions, ["update_memory", "insert_memory", "discard"]
    )
    graph.add_edge("update_memory", END)
    graph.add_edge("insert_memory", END)
    graph.add_edge("discard", END)
    return graph.compile()


extraction_graph = _build_extraction_graph()


def run_extraction(session: Session, user_id: str, limit: int = DEFAULT_DIALOGUE_LIMIT) -> list[dict]:
    """Entry point the Celery task calls (doc: "when the Celery task fires, all
    it does is call extraction_graph.invoke(...)"). Runs inside the caller's session/transaction
    — wrap this in `session_scope()` to commit or roll back the whole run atomically.
    """
    initial_state: ExtractionState = {
        "user_id": user_id,
        "limit": limit,
        "dialogue_text": "",
        "source_message_ids": [],
        "candidates": [],
        "embedded": [],
        "similar_matches": {},
        "results": [],
    }
    final_state = extraction_graph.invoke(initial_state, config={"configurable": {"session": session}})
    return final_state["results"]
