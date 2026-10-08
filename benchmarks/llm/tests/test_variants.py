"""Segmentation prompt variant: frozen system prompt, user prompt build, response parse.

Mirrors tests/test_addressing_mode.py's fixture style. No benchmark calls here.
"""
from __future__ import annotations

import pytest

from benchmark import variants


ID_SEGMENTS = [
    {"sid": 0, "start": 0.0, "end": 5.0, "text": "This episode is brought to you by BetterHelp"},
    {"sid": 1, "start": 5.0, "end": 30.0, "text": "BetterHelp is online therapy that fits"},
    {"sid": 2, "start": 30.0, "end": 60.0, "text": "Now back to the show"},
    {"sid": 3, "start": 60.0, "end": 100.0, "text": "Welcome back everyone"},
]


# --- validate_variant --------------------------------------------------------

def test_validate_variant_rejects_unknown_name():
    with pytest.raises(ValueError, match="detection"):
        variants.validate_variant("bogus")


def test_validate_variant_accepts_known_names():
    for name in variants.PROMPT_VARIANTS:
        variants.validate_variant(name)  # must not raise


# --- segmentation_system_prompt ----------------------------------------------

def test_segmentation_system_prompt_loads_and_is_well_formed():
    text = variants.segmentation_system_prompt()
    assert text
    assert text.startswith("You are a JSON API")
    for line in text.split("\n"):
        assert line == line.rstrip(), f"trailing whitespace on line: {line!r}"


# --- format_segmentation_prompt: window context -----------------------------

def _prompt(window_index, total_windows, addressing_mode="timestamps"):
    return variants.format_segmentation_prompt(
        podcast_name="Test Show",
        episode_title="Test Episode",
        description_section="",
        transcript_lines=["[0.0s - 5.0s] line"],
        window_index=window_index,
        total_windows=total_windows,
        window_start=0.0,
        window_end=100.0,
        addressing_mode=addressing_mode,
    )


def test_single_window_has_no_window_context():
    prompt = _prompt(window_index=0, total_windows=1)
    assert "partial transcript" not in prompt
    assert "continues" not in prompt


def test_first_of_three_mentions_only_continues_in_next():
    prompt = _prompt(window_index=0, total_windows=3)
    assert "continues in next" in prompt
    assert "continues from previous" not in prompt


def test_middle_window_has_combined_continuation_sentence():
    prompt = _prompt(window_index=1, total_windows=3)
    assert '"continues from previous" or "continues in next"' in prompt


def test_last_of_three_mentions_only_continues_from_previous():
    prompt = _prompt(window_index=2, total_windows=3)
    assert "continues from previous" in prompt
    assert "continues in next" not in prompt


def test_segment_id_mode_header_says_indexes():
    prompt = _prompt(window_index=0, total_windows=1, addressing_mode="segment_ids")
    assert "Transcript with indexes:" in prompt
    assert "Transcript with timestamps:" not in prompt


def test_timestamps_mode_header_says_timestamps():
    prompt = _prompt(window_index=0, total_windows=1, addressing_mode="timestamps")
    assert "Transcript with timestamps:" in prompt


# --- _normalize_direct_method: object vs. bare-array wrapper ----------------

def test_normalize_direct_method_object_wrapper_is_full_compliance():
    assert variants._normalize_direct_method("json_object_segments_key") == "segmentation_object_direct"


def test_normalize_direct_method_bare_array_is_mismatched_wrapper():
    assert variants._normalize_direct_method("json_array_direct") == "segmentation_array_wrapper"


def test_bare_array_response_with_promo_scores_segmentation_array_wrapper():
    response = '[{"start": 10.0, "end": 40.0, "category": "sponsor"}]'
    ads, method, id_contract_miss = variants.parse_segmentation_response(
        response, "timestamps", None,
    )
    assert method == "segmentation_array_wrapper"
    assert len(ads) == 1
    assert id_contract_miss is False


# --- parse_segmentation_response: segment-id mode ----------------------------

def test_id_mode_resolves_only_the_sponsor_segment_to_exact_seconds():
    response = (
        '{"segments": ['
        '{"start": 0, "end": 1, "category": "sponsor", "confidence": "high", "sponsor_name": "BetterHelp"},'
        '{"start": 2, "end": 2, "category": "main_content"},'
        '{"start": 3, "end": 3, "category": "intro"}'
        ']}'
    )
    ads, method, id_contract_miss = variants.parse_segmentation_response(
        response, "segment_ids", ID_SEGMENTS,
    )
    assert id_contract_miss is False
    assert method == "segment_id_direct"
    assert len(ads) == 1
    assert ads[0]["start"] == 0.0
    assert ads[0]["end"] == 30.0


# --- parse_segmentation_response: timestamps mode ----------------------------

def test_timestamps_mode_keeps_sponsor_with_normalized_confidence():
    response = (
        '{"segments": ['
        '{"start": 10.0, "end": 40.0, "category": "sponsor", "confidence": "high", "sponsor_name": "BetterHelp"},'
        '{"start": 40.0, "end": 90.0, "category": "main_content"}'
        ']}'
    )
    ads, method, id_contract_miss = variants.parse_segmentation_response(
        response, "timestamps", None,
    )
    assert id_contract_miss is False
    assert method == "segmentation_object_direct"
    assert len(ads) == 1
    assert ads[0]["start"] == 10.0
    assert ads[0]["end"] == 40.0
    assert ads[0]["confidence"] == 0.95


# --- parse_segmentation_response: no-ads and id-contract-miss cases ----------

def test_main_content_only_response_yields_empty_list_with_no_miss():
    response = '{"segments": [{"start": 0.0, "end": 100.0, "category": "main_content"}]}'
    ads, method, id_contract_miss = variants.parse_segmentation_response(
        response, "timestamps", None,
    )
    assert ads == []
    assert method == "segmentation_object_direct"
    assert id_contract_miss is False


def test_main_content_only_response_in_id_mode_scores_segment_id_direct():
    response = '{"segments": [{"start": 0.0, "end": 100.0, "category": "main_content"}]}'
    ads, method, id_contract_miss = variants.parse_segmentation_response(
        response, "segment_ids", ID_SEGMENTS,
    )
    assert ads == []
    assert method == "segment_id_direct"
    assert id_contract_miss is False


def test_unparseable_response_returns_none_method_not_the_string_none():
    ads, method, id_contract_miss = variants.parse_segmentation_response(
        "not json at all", "timestamps", None,
    )
    assert ads == []
    assert method is None
    assert id_contract_miss is False


def test_unparseable_response_in_id_mode_also_returns_none_method():
    ads, method, id_contract_miss = variants.parse_segmentation_response(
        "not json at all", "segment_ids", ID_SEGMENTS,
    )
    assert ads == []
    assert method is None
    assert id_contract_miss is False


def test_id_mode_accepts_string_and_infinite_values_without_raising():
    response = (
        '{"segments": ['
        '{"start": "1.0", "end": 2, "category": "sponsor", "confidence": "high", "sponsor_name": "BetterHelp"},'
        '{"start": "inf", "end": 3, "category": "sponsor"}'
        ']}'
    )
    ads, method, id_contract_miss = variants.parse_segmentation_response(
        response, "segment_ids", ID_SEGMENTS,
    )
    assert id_contract_miss is False
    assert method == "segment_id_direct"
    # The "inf" entry has no integer-valued end achievable, so only the first resolves.
    assert len(ads) == 1


def test_id_mode_mixed_int_and_float_entries_logs_and_drops_float(caplog):
    response = (
        '{"segments": ['
        '{"start": 0, "end": 1, "category": "sponsor", "confidence": "high", "sponsor_name": "BetterHelp"},'
        '{"start": 2.5, "end": 3.5, "category": "sponsor"}'
        ']}'
    )
    ads, method, id_contract_miss = variants.parse_segmentation_response(
        response, "segment_ids", ID_SEGMENTS,
    )
    assert id_contract_miss is False
    assert method == "segment_id_direct"
    assert len(ads) == 1
    assert "mixed formats" in caplog.text
    assert "skipped 1" in caplog.text


# --- no-promo responses: real extraction route is kept, not a fixed label --

def test_no_promo_response_via_markdown_block_keeps_markdown_code_block_method_timestamps():
    response = '```json\n[{"start": 0.0, "end": 100.0, "category": "main_content"}]\n```'
    ads, method, id_contract_miss = variants.parse_segmentation_response(
        response, "timestamps", None,
    )
    assert ads == []
    assert method == "markdown_code_block"
    assert id_contract_miss is False


def test_no_promo_response_via_markdown_block_keeps_markdown_code_block_method_id_mode():
    response = '```json\n[{"start": 0.0, "end": 100.0, "category": "main_content"}]\n```'
    ads, method, id_contract_miss = variants.parse_segmentation_response(
        response, "segment_ids", ID_SEGMENTS,
    )
    assert ads == []
    assert method == "markdown_code_block"
    assert id_contract_miss is False


def test_id_mode_with_float_timestamps_sets_id_contract_miss():
    response = (
        '{"segments": ['
        '{"start": 10.5, "end": 40.2, "category": "sponsor", "confidence": "high", "sponsor_name": "BetterHelp"}'
        ']}'
    )
    ads, method, id_contract_miss = variants.parse_segmentation_response(
        response, "segment_ids", ID_SEGMENTS,
    )
    assert id_contract_miss is True
    assert method == "segmentation_object_direct"
    assert len(ads) == 1
    assert ads[0]["start"] == 10.5
    assert ads[0]["end"] == 40.2
