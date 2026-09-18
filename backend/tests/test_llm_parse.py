"""Unit tests for `memory.llm._parse_candidates` — the part of
`extract_candidates` that's pure and worth pinning down
without a live Anthropic API key: turning the model's raw text reply into
validated candidate dicts.
"""

from memory.llm import _parse_candidates


def test_parses_well_formed_json_array():
    text = '[{"content": "user has a cat", "type": "semantic", "importance": 0.7}]'
    assert _parse_candidates(text) == [
        {"content": "user has a cat", "memory_type": "semantic", "importance": 0.7}
    ]


def test_strips_surrounding_prose_and_code_fences():
    text = 'Here you go:\n```json\n[{"content": "trip to Tokyo", "type": "episodic", "importance": 0.6}]\n```\nHope that helps!'
    result = _parse_candidates(text)
    assert result == [{"content": "trip to Tokyo", "memory_type": "episodic", "importance": 0.6}]


def test_empty_array_yields_no_candidates():
    assert _parse_candidates("[]") == []


def test_no_json_found_yields_no_candidates():
    assert _parse_candidates("there's nothing worth remembering here") == []


def test_malformed_json_yields_no_candidates():
    assert _parse_candidates("[{not valid json}]") == []


def test_drops_items_with_invalid_memory_type():
    text = '[{"content": "x", "type": "not_a_real_type", "importance": 0.5}]'
    assert _parse_candidates(text) == []


def test_drops_items_with_empty_content():
    text = '[{"content": "  ", "type": "semantic", "importance": 0.5}]'
    assert _parse_candidates(text) == []


def test_missing_importance_defaults_to_half():
    text = '[{"content": "x", "type": "semantic"}]'
    assert _parse_candidates(text) == [{"content": "x", "memory_type": "semantic", "importance": 0.5}]


def test_importance_is_clamped_to_unit_range():
    text = '[{"content": "x", "type": "semantic", "importance": 5}]'
    assert _parse_candidates(text)[0]["importance"] == 1.0
    text = '[{"content": "y", "type": "semantic", "importance": -3}]'
    assert _parse_candidates(text)[0]["importance"] == 0.0


def test_non_numeric_importance_defaults_to_half():
    text = '[{"content": "x", "type": "semantic", "importance": "high"}]'
    assert _parse_candidates(text)[0]["importance"] == 0.5
