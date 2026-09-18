"""Pure-logic tests for the shared dedup decision — no DB, no
network. This is the piece both the extraction graph and `memory_remember`
rely on to never disagree, so it's worth pinning down exactly.
"""

from memory.dedup import DEDUP_SIMILARITY_THRESHOLD, decide_action


def test_no_existing_memory_inserts():
    assert decide_action(None, "user has a cat", None) == "insert"


def test_low_similarity_inserts():
    assert decide_action(0.2, "user has a cat", "user likes hiking") == "insert"


def test_high_similarity_identical_content_discards():
    text = "user has a cat named Mochi"
    assert decide_action(0.99, text, text) == "discard"


def test_high_similarity_identical_content_ignores_surrounding_whitespace():
    assert decide_action(0.99, "  same fact  ", "same fact") == "discard"


def test_high_similarity_changed_content_updates():
    assert decide_action(0.95, "user's cat is named Biscuit", "user's cat is named Mochi") == "update"


def test_threshold_boundary_is_inclusive():
    # exactly at the threshold counts as "matched" (>= not >)
    assert decide_action(DEDUP_SIMILARITY_THRESHOLD, "new wording", "old wording") == "update"


def test_just_below_threshold_inserts():
    below = DEDUP_SIMILARITY_THRESHOLD - 0.01
    assert decide_action(below, "new wording", "old wording") == "insert"
