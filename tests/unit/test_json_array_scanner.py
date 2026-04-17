"""Tests for the O(n) bracket-depth JSON array scanner."""
import json
import time

from ad_detector import _find_json_array_candidates


def test_yields_single_array():
    assert list(_find_json_array_candidates('[1, 2, 3]')) == ['[1, 2, 3]']


def test_yields_multiple_top_level_arrays():
    text = 'prefix [1] middle [2, 3] suffix'
    assert list(_find_json_array_candidates(text)) == ['[1]', '[2, 3]']


def test_handles_nested_arrays():
    text = 'wrap [[1, 2], [3]] end'
    assert list(_find_json_array_candidates(text)) == ['[[1, 2], [3]]']


def test_handles_mixed_nested_and_sibling():
    text = '[1, [2, 3]] and [[4], 5]'
    got = list(_find_json_array_candidates(text))
    assert got == ['[1, [2, 3]]', '[[4], 5]']


def test_ignores_brackets_inside_strings():
    payload = '[{"reason": "[sponsor] message"}]'
    got = list(_find_json_array_candidates(payload))
    assert got == [payload]
    parsed = json.loads(got[0])
    assert parsed[0]['reason'] == '[sponsor] message'


def test_handles_escaped_quote_in_string():
    payload = r'[{"text": "he said \"hi\""}]'
    got = list(_find_json_array_candidates(payload))
    assert got == [payload]
    assert json.loads(got[0])[0]['text'] == 'he said "hi"'


def test_handles_unmatched_close_bracket_safely():
    text = ']]] noise [1, 2]'
    assert list(_find_json_array_candidates(text)) == ['[1, 2]']


def test_handles_unmatched_open_bracket_safely():
    text = '[1, 2, incomplete'
    assert list(_find_json_array_candidates(text)) == []


def test_empty_string_yields_nothing():
    assert list(_find_json_array_candidates('')) == []


def test_adversarial_input_runs_linear():
    """Nested-alternation regex could regress to seconds on this; the
    bracket scanner must stay O(n)."""
    payload = '[' * 5000
    start = time.monotonic()
    result = list(_find_json_array_candidates(payload))
    elapsed = time.monotonic() - start
    assert result == []
    assert elapsed < 0.5, f"scanner took {elapsed:.2f}s; expected linear"
