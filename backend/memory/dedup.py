"""Shared similarity/dedup decision — the one place that
decides "update vs. discard vs. insert", used by both the background
`extraction_graph` and the explicit `memory_remember` write path, so the two
write paths can never disagree about what counts as a duplicate.
"""

from typing import Literal

# Cosine similarity against the nearest existing memory of the same tier.
# Below this, treat the candidate as unrelated to anything on file -> insert.
# At/above it, the candidate is "the same thing" as that memory — insert
# instead becomes update-or-discard depending on whether the wording changed.
DEDUP_SIMILARITY_THRESHOLD = 0.90

Action = Literal["insert", "update", "discard"]


def decide_action(
    similarity: float | None,
    candidate_content: str,
    existing_content: str | None,
) -> Action:
    """No match, or below threshold -> insert (doc: "no conflict -> write the new memory directly").
    Above threshold: identical content (whitespace-insensitive) is a duplicate
    re-extraction of the same fact -> discard; anything else means the old
    fact changed -> update (the caller supersedes the old row, doesn't delete it).
    """
    if similarity is None or similarity < DEDUP_SIMILARITY_THRESHOLD:
        return "insert"
    if existing_content is not None and candidate_content.strip() == existing_content.strip():
        return "discard"
    return "update"
