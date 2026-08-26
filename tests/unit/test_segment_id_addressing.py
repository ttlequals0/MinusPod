"""Segment-ID addressing: rendering side."""
from unittest.mock import MagicMock

from ad_detector import AdDetector
from ad_detector.prompts import SEGMENT_ID_SYSTEM_SECTION


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
