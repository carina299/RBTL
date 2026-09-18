"""`memory_remember` explicit write path — the synchronous
counterpart to the background `extraction_graph`, for when the agent decides
mid-conversation that something is worth recording right away.

Reuses the exact same embed -> retrieve_similar -> decide_action logic
(`memory.dedup.decide_action`, `MemoryRepository.find_most_similar`) as the
extraction graph, so the two write paths can never disagree about what counts
as a duplicate or an update — only the trigger differs, and this path skips
straight to a single synchronous write instead of a batch.

The MCP tool wrapper that exposes this to the agent as `memory_remember(...)`
is built alongside the retrieval graph, separately; this module is
just the write logic that tool calls into.
"""

from sqlalchemy.orm import Session

from memory.dedup import decide_action
from memory.embeddings import embed_texts
from repositories import MemoryRepository


def remember(
    session: Session,
    user_id: str,
    content: str,
    memory_type: str = "semantic",
    importance: float = 0.5,
) -> dict:
    content = (content or "").strip()
    if not content:
        raise ValueError("content is required")
    if memory_type not in ("episodic", "semantic"):
        raise ValueError("memory_type must be 'episodic' or 'semantic'")

    repo = MemoryRepository(session)
    embedding = embed_texts([content])[0]
    match = repo.find_most_similar(user_id, memory_type, embedding)
    similar_content = match[0].content if match else None
    similarity = match[1] if match else None
    action = decide_action(similarity, content, similar_content)

    if action == "discard":
        return {"action": "discard", "memory_id": match[0].id, "similarity": similarity}

    new_memory = repo.create(
        user_id=user_id,
        memory_type=memory_type,
        content=content,
        embedding=embedding,
        importance=importance,
        source="explicit",
    )
    if action == "update":
        repo.supersede(match[0].id, new_memory)
        return {
            "action": "update",
            "memory_id": new_memory.id,
            "superseded_id": match[0].id,
            "similarity": similarity,
        }
    return {"action": "insert", "memory_id": new_memory.id, "similarity": similarity}
