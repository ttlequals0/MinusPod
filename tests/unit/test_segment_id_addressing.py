"""Segment-ID addressing: rendering side."""
from unittest.mock import MagicMock

from ad_detector import AdDetector
from ad_detector.prompts import (
    SEGMENT_ID_SYSTEM_SECTION, format_window_prompt,
    parse_id_ads_from_response, resolve_segment_id_ads,
)


def _detector(mode):
    d = AdDetector.__new__(AdDetector)
    d.db = MagicMock()
    d.db.get_setting.side_effect = (
        lambda key: mode if key == 'ad_addressing_mode' else None)
    return d


def test_mode_resolution():
    assert _detector('segment_ids')._resolve_addressing_mode() == 'segment_ids'
    assert _detector('timestamps')._resolve_addressing_mode() == 'timestamps'
    assert _detector('SEGMENT_IDS ')._resolve_addressing_mode() == 'segment_ids'
    assert _detector('bogus')._resolve_addressing_mode() == 'timestamps'
    assert _detector(None)._resolve_addressing_mode() == 'timestamps'


def test_id_transcript_lines():
    segs = [{'sid': 0, 'start': 0.0, 'end': 5.0, 'text': 'hello'},
            {'sid': 1, 'start': 5.0, 'end': 9.0, 'text': 'world'}]
    lines = AdDetector._format_transcript_lines(segs, 'segment_ids')
    assert lines == ['[0] hello', '[1] world']
    ts_lines = AdDetector._format_transcript_lines(segs, 'timestamps')
    assert ts_lines == ['[0.0s - 5.0s] hello', '[5.0s - 9.0s] world']


def test_system_section_mentions_ids_not_timestamps():
    assert 'start_id' in SEGMENT_ID_SYSTEM_SECTION
    assert 'end_id' in SEGMENT_ID_SYSTEM_SECTION


def _window_kwargs():
    return dict(
        podcast_name='Test', episode_title='Ep1',
        description_section='', transcript_lines=['[0] hello'],
        window_index=0, total_windows=1,
        window_start=0.0, window_end=600.0,
    )


def test_window_prompt_rules_are_mode_exclusive():
    id_out = format_window_prompt(**_window_kwargs(), addressing_mode='segment_ids')
    assert 'start_id' in id_out
    assert 'Use absolute timestamps' not in id_out

    ts_out = format_window_prompt(**_window_kwargs(), addressing_mode='timestamps')
    assert 'Use absolute timestamps' in ts_out
    assert 'start_id' not in ts_out


SEGS = [{'sid': i, 'start': i * 10.0, 'end': i * 10.0 + 10.0,
         'text': f'seg {i}'} for i in range(10)]


def test_ids_resolve_to_exact_segment_times():
    ads, used = parse_id_ads_from_response(
        '[{"start_id": 2, "end_id": 4, "confidence": 0.9, '
        '"category": "sponsor", "reason": "promo code read"}]')
    assert used is True
    resolved = resolve_segment_id_ads(ads, SEGS)
    assert resolved[0]['start'] == 20.0
    assert resolved[0]['end'] == 50.0
    assert 'start_id' not in resolved[0]


def test_reversed_ids_normalized():
    ads, _ = parse_id_ads_from_response(
        '[{"start_id": 4, "end_id": 2, "confidence": 0.9, '
        '"category": "sponsor", "reason": "x"}]')
    resolved = resolve_segment_id_ads(ads, SEGS)
    assert resolved[0]['start'] == 20.0 and resolved[0]['end'] == 50.0


def test_out_of_range_id_dropped():
    ads, _ = parse_id_ads_from_response(
        '[{"start_id": 2, "end_id": 99, "confidence": 0.9, '
        '"category": "sponsor", "reason": "x"}]')
    assert resolve_segment_id_ads(ads, SEGS) == []


def test_timestamp_response_falls_back():
    ads, used = parse_id_ads_from_response(
        '[{"start": 45.0, "end": 82.0, "confidence": 0.9, '
        '"category": "sponsor", "reason": "x"}]')
    assert used is False


def test_start_id_never_misread_as_seconds():
    # The generic fuzzy start/end matcher must not turn start_id=2 into
    # start=2.0 seconds; the ID branch owns any object carrying id fields.
    ads, used = parse_id_ads_from_response(
        '[{"start_id": 2, "end_id": 4, "confidence": 0.9, '
        '"category": "sponsor", "reason": "x"}]')
    assert used is True
    assert all('start' not in ad for ad in ads)
    resolved = resolve_segment_id_ads(ads, SEGS)
    assert resolved[0]['start'] == 20.0  # not 2.0
