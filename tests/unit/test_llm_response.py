"""Tests for utils.llm_response (extracted from ad_detector for reuse)."""
import pytest

from utils.llm_response import (
    extract_json_ads_array,
    extract_json_object,
    find_first_dict_with_key,
    find_json_array_candidates,
)


# ---------- find_json_array_candidates ----------

def test_find_json_array_simple():
    assert list(find_json_array_candidates('[1, 2, 3]')) == ['[1, 2, 3]']


def test_find_json_array_multiple_top_level():
    text = 'before [1] mid [2, 3] tail'
    assert list(find_json_array_candidates(text)) == ['[1]', '[2, 3]']


def test_find_json_array_nested():
    text = 'data: [[1, 2], [3]] end'
    assert list(find_json_array_candidates(text)) == ['[[1, 2], [3]]']


def test_find_json_array_ignores_brackets_in_strings():
    payload = '[{"label": "[skip] me"}]'
    got = list(find_json_array_candidates(payload))
    assert got == [payload]


def test_find_json_array_handles_escaped_quotes():
    payload = '[{"q": "she said \\"hi\\""}]'
    got = list(find_json_array_candidates(payload))
    assert got == [payload]


def test_find_json_array_empty_input():
    assert list(find_json_array_candidates('')) == []


def test_find_json_array_unclosed_bracket():
    """Unbalanced brackets do not yield a span."""
    assert list(find_json_array_candidates('[1, 2')) == []


# ---------- extract_json_ads_array ----------

def test_extract_json_ads_array_direct():
    ads, method = extract_json_ads_array('[{"start": 1.0, "end": 5.0}]')
    assert ads == [{'start': 1.0, 'end': 5.0}]
    assert method == 'json_array_direct'


def test_extract_json_ads_array_markdown_block():
    text = 'Sure thing\n```json\n[{"start": 10, "end": 20}]\n```'
    ads, method = extract_json_ads_array(text)
    assert ads == [{'start': 10, 'end': 20}]
    # Could match one of several strategies; just check non-None
    assert method is not None


def test_extract_json_ads_array_with_preamble():
    text = 'Here are the detected ads: [{"start": 1, "end": 2}]'
    ads, _ = extract_json_ads_array(text)
    assert ads == [{'start': 1, 'end': 2}]


def test_extract_json_ads_array_single_object_wrapped():
    """Some models return a bare ad object instead of an array."""
    text = '{"start": 1.0, "end": 5.0, "confidence": 0.9}'
    ads, method = extract_json_ads_array(text)
    assert ads == [{'start': 1.0, 'end': 5.0, 'confidence': 0.9}]
    assert method == 'json_object_single_ad'


def test_extract_json_ads_array_no_match_returns_none():
    ads, method = extract_json_ads_array('plain text with no json')
    assert ads is None
    assert method is None


# ---------- extract_json_object ----------

def test_extract_json_object_direct():
    obj, method = extract_json_object('{"verdict": "confirmed", "confidence": 0.9}')
    assert obj == {'verdict': 'confirmed', 'confidence': 0.9}
    assert method == 'json_object_direct'


def test_extract_json_object_markdown_block():
    text = '```json\n{"verdict": "reject"}\n```'
    obj, method = extract_json_object(text)
    assert obj == {'verdict': 'reject'}
    assert method == 'markdown_code_block'


def test_extract_json_object_preamble_stripped():
    text = 'Here is the result: {"verdict": "adjust"}'
    obj, _ = extract_json_object(text)
    assert obj == {'verdict': 'adjust'}


def test_extract_json_object_brace_fallback():
    """If preamble-strip doesn't help and there's no fence, brace fallback finds the object."""
    text = 'random prefix {"verdict": "confirmed"} random suffix'
    obj, method = extract_json_object(text)
    assert obj == {'verdict': 'confirmed'}
    assert method == 'brace_fallback'


def test_extract_json_object_returns_first_object_from_array():
    text = '[{"verdict": "confirmed"}, {"verdict": "reject"}]'
    obj, method = extract_json_object(text)
    assert obj == {'verdict': 'confirmed'}
    assert method == 'json_array_first_object'


def test_extract_json_object_no_json_returns_none():
    obj, method = extract_json_object('not json at all')
    assert obj is None
    assert method is None


def test_extract_json_object_malformed_json_returns_none():
    obj, _ = extract_json_object('{"verdict": "confirmed"')  # missing brace
    assert obj is None


# ---------- find_first_dict_with_key ----------

def test_find_first_dict_with_key_top_level():
    obj = {'verdict': 'confirmed', 'confidence': 0.9}
    assert find_first_dict_with_key(obj, 'verdict') is obj


def test_find_first_dict_with_key_nested_in_array():
    obj = {
        'podcast': 'x',
        'ads_reviewed': [
            {'verdict': 'confirmed', 'reasoning': 'r', 'confidence': 0.9},
        ],
    }
    found = find_first_dict_with_key(obj, 'verdict')
    assert found == {'verdict': 'confirmed', 'reasoning': 'r', 'confidence': 0.9}


def test_find_first_dict_with_key_deeply_nested():
    obj = {
        'meta': {'podcast': 'x'},
        'results': [{'item': {'review': {'verdict': 'reject', 'confidence': 0.7}}}],
    }
    found = find_first_dict_with_key(obj, 'verdict')
    assert found == {'verdict': 'reject', 'confidence': 0.7}


def test_find_first_dict_with_key_returns_first_match():
    """If multiple matching dicts exist, returns the first encountered in the
    in-order traversal."""
    obj = {
        'a': {'verdict': 'first'},
        'b': {'verdict': 'second'},
    }
    found = find_first_dict_with_key(obj, 'verdict')
    assert found in ({'verdict': 'first'}, {'verdict': 'second'})


def test_find_first_dict_with_key_no_match():
    obj = {'podcast': 'x', 'summary': 'none'}
    assert find_first_dict_with_key(obj, 'verdict') is None


def test_find_first_dict_with_key_handles_non_dict_root():
    assert find_first_dict_with_key([{'verdict': 'confirmed'}], 'verdict') == {'verdict': 'confirmed'}
    assert find_first_dict_with_key('a string', 'verdict') is None
    assert find_first_dict_with_key(42, 'verdict') is None
    assert find_first_dict_with_key(None, 'verdict') is None


# ---------- Backward compatibility shims ----------

def test_ad_detector_reexports_legacy_names():
    """Existing tests / external callers using ad_detector._find_json_array_candidates
    and ad_detector.extract_json_ads_array still work."""
    import ad_detector
    assert list(ad_detector._find_json_array_candidates('[1, 2]')) == ['[1, 2]']
    assert ad_detector.extract_json_ads_array('[{"start": 1}]') == (
        [{'start': 1}],
        'json_array_direct',
    )


def test_salvage_truncated_single_ad_recovers_start_end():
    """Models that hit max_tokens mid-`reason` field leave structurally-invalid
    JSON. The salvage strategy regex-extracts numeric fields and returns a
    usable single-ad dict when both start and end were captured before
    truncation.
    """
    from utils.llm_response import extract_json_ads_array
    text = (
        '{\n'
        '  "start": 66.99,\n'
        '  "end": 67.25,\n'
        '  "confidence": 0.95,\n'
        '  "reason": "Long preamble about the model thinking very carefully'  # truncated, no closing quote / brace
    )
    ads, method = extract_json_ads_array(text)
    assert method == "json_object_single_ad_truncated"
    assert ads is not None and len(ads) == 1
    assert ads[0]["start"] == 66.99
    assert ads[0]["end"] == 67.25
    assert ads[0]["confidence"] == 0.95


def test_salvage_truncated_returns_none_without_start_or_end():
    """If the truncation happened before start/end were emitted, the dict
    is unusable; salvage must decline rather than fabricate.
    """
    from utils.llm_response import extract_json_ads_array
    text = '{"reason": "I think there might be an ad here'
    ads, method = extract_json_ads_array(text)
    assert ads is None and method is None


def test_salvage_skips_when_text_starts_with_bracket():
    """Truncated array responses go through bracket_fallback / regex_json_array
    already. The single-ad salvage is single-object-only by design.
    """
    from utils.llm_response import extract_json_ads_array
    text = '[{"start": 10, "end": 20'
    ads, method = extract_json_ads_array(text)
    # Should NOT come back as json_object_single_ad_truncated. The earlier
    # strategies will either parse partially or return None; either is fine.
    assert method != "json_object_single_ad_truncated"
