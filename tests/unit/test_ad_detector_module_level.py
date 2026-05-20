"""Tests for the module-level functions lifted out of AdDetector in 2.0.25.

These functions back the offline LLM benchmark, which can't construct a
full AdDetector (no DB, no LLM client). The tests pin the module-level
surface so future refactors don't break the benchmark contract.
"""
import json

import pytest

from ad_detector import (
    extract_json_ads_array,
    parse_ads_from_response,
    format_window_prompt,
    get_static_system_prompt,
)


# ===== Item 1: extract_json_ads_array, parse_ads_from_response =====

def test_extract_json_ads_array_module_level_basic():
    response = '[{"start": 10.0, "end": 70.0, "confidence": 0.95, "reason": "ad"}]'
    ads, method = extract_json_ads_array(response)
    assert isinstance(ads, list) and len(ads) == 1
    assert ads[0]['start'] == 10.0
    assert method == 'json_array_direct'


def test_extract_json_ads_array_handles_markdown_code_block():
    response = 'Here are the ads:\n```json\n[{"start": 5.0, "end": 65.0}]\n```'
    ads, method = extract_json_ads_array(response)
    assert ads == [{"start": 5.0, "end": 65.0}]
    assert method == 'markdown_code_block'


def test_parse_ads_from_response_module_level_basic():
    response = json.dumps([{
        "start": 100.0, "end": 160.0,
        "confidence": 0.92, "reason": "BetterHelp ad",
        "sponsor": "BetterHelp",
    }])
    ads = parse_ads_from_response(response)
    assert len(ads) == 1
    assert ads[0]['start'] == 100.0
    assert ads[0]['end'] == 160.0


@pytest.mark.parametrize('bad_sponsor', [
    # The exact line we caught in prod 2026-05-20:
    'Inferred from ~26 second gap in transcript with no spoken content provided',
    # Variations of the same reasoning-as-sponsor pattern, no extractable brand:
    'inferred from audio signal anomaly',
    'Based on the volume anomaly observed in this segment',
    'Detected as a network-inserted spot in this window',
])
def test_parse_ads_from_response_drops_reasoning_sentence_sponsor(bad_sponsor):
    """Claude occasionally puts a reasoning sentence in the sponsor field.

    Real sponsor names never start with "Inferred from" / "Based on" /
    "Detected as", never run past 60 characters, and never contain
    meta-substrings like "in transcript", "audio signal", "volume anomaly".
    The ad survives; the bogus sponsor is dropped.

    Note: if the reasoning sentence happens to embed a real brand name
    (e.g. "...looks like a Capital One ad"), the downstream fuzzy
    extractor will still surface that brand. That is intentional and is
    not what this gate targets.
    """
    response = json.dumps([{
        "start": 100.0, "end": 160.0,
        "confidence": 0.92, "reason": "ad",
        "sponsor": bad_sponsor,
    }])
    ads = parse_ads_from_response(response)
    assert len(ads) == 1, "ad should still be kept; only the sponsor field is rejected"
    assert 'sponsor' not in ads[0], (
        f"reasoning-sentence sponsor {bad_sponsor!r} should be dropped, "
        f"got {ads[0].get('sponsor')!r}"
    )


@pytest.mark.parametrize('good_sponsor', [
    'BetterHelp',
    'Athletic Greens',
    '1Password',
    'Capital One',
    'T-Mobile',
    'AT&T',
    'forhers.com',
    'Capital One Quicksilver',  # multi-word real product name
])
def test_parse_ads_from_response_keeps_normal_sponsor_names(good_sponsor):
    response = json.dumps([{
        "start": 100.0, "end": 160.0,
        "confidence": 0.92, "reason": "sponsor read",
        "sponsor": good_sponsor,
    }])
    ads = parse_ads_from_response(response)
    assert len(ads) == 1
    assert ads[0].get('sponsor') == good_sponsor


# ===== Item 2: format_window_prompt =====

def test_format_window_prompt_includes_window_header():
    out = format_window_prompt(
        podcast_name='Test', episode_title='Ep1',
        description_section='', transcript_lines=['[0.0s - 5.0s] hello'],
        window_index=2, total_windows=4,
        window_start=600.0, window_end=1200.0,
    )
    # window_index is 0-based; header is 1-based.
    assert '=== WINDOW 3/4: 10.0-20.0 minutes ===' in out


def test_format_window_prompt_appends_audio_context_between_template_and_header():
    out = format_window_prompt(
        podcast_name='Test', episode_title='Ep1',
        description_section='', transcript_lines=['[0.0s - 5.0s] hi'],
        window_index=0, total_windows=1,
        window_start=0.0, window_end=600.0,
        audio_context='\n=== AUDIO ===\nvolume_drop at 5.0s\n',
    )
    audio_pos = out.find('=== AUDIO ===')
    window_pos = out.find('=== WINDOW 1/1')
    assert audio_pos > 0 and window_pos > 0
    assert audio_pos < window_pos, "audio_context should appear before window header"


# ===== Item 3: module-level get_static_system_prompt =====

def test_get_static_system_prompt_includes_a_known_seed_sponsor():
    out = get_static_system_prompt()
    assert 'DYNAMIC SPONSOR DATABASE' in out
    # BetterHelp is a stable seed entry; assertion survives any list reordering.
    assert 'BetterHelp' in out


def test_get_static_system_prompt_is_deterministic():
    """Two calls must return identical output (no DB, env, or wallclock dependency)."""
    assert get_static_system_prompt() == get_static_system_prompt()
