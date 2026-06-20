"""Regression tests for AdDetector._detect_keep_content_ads parsing.

Guards the critical bug where the LLM response OBJECT (not response.content)
was passed to extract_json_ads_array, raising AttributeError and silently
aborting keep-content to blacklist on every episode.
"""
import json
from unittest.mock import patch

from ad_detector import AdDetector


class _StubResponse:
    """LLMResponse-shaped duck: the parse path reads .content."""

    def __init__(self, content):
        self.content = content
        self.usage = {}


def _segments(total=3600.0):
    # One short segment per minute so create_windows yields several windows.
    return [{'start': float(t), 'end': float(t) + 30.0, 'text': f'content at {t}s'}
            for t in range(0, int(total), 60)]


def _detect(detector, segments, content_json):
    """Run the content pass with every window returning content_json."""
    def fake_call(*, window_label, **_kw):
        return _StubResponse(content_json), None

    with patch.object(detector, '_call_llm_for_window', side_effect=fake_call):
        return detector._detect_keep_content_ads(
            segments, model='m', slug='s', episode_id='e',
            podcast_name='p', episode_title='t', description_section='',
            llm_timeout=30, max_retries=1,
        )


def test_full_coverage_parses_and_removes_nothing():
    # The whole episode is labeled content -> complement is empty -> []. If the
    # response object were passed instead of .content this would raise instead.
    d = AdDetector(api_key='test-key')
    segs = _segments(3600.0)
    ads = _detect(d, segs, json.dumps([{'start': 0, 'end': 3600}]))
    assert ads == []


def test_head_gap_inverts_to_a_leading_ad():
    # Content starts at 100s in every window -> the episode head (0-100s) is
    # unlabeled -> one inverted ad anchored at 0, proving the parse+invert path.
    d = AdDetector(api_key='test-key')
    segs = _segments(3600.0)
    ads = _detect(d, segs, json.dumps([{'start': 100, 'end': 3600}]))
    assert ads is not None
    assert len(ads) == 1
    assert ads[0]['start'] == 0
    assert ads[0]['detection_stage'] == 'keep_content'


def test_no_response_aborts_to_blacklist():
    # A window returning no response must abort the whole inversion (None) so
    # the caller falls back to blacklist rather than cut on partial knowledge.
    d = AdDetector(api_key='test-key')
    segs = _segments(3600.0)

    def fake_call(*, window_label, **_kw):
        return None, RuntimeError('llm down')

    with patch.object(d, '_call_llm_for_window', side_effect=fake_call):
        ads = d._detect_keep_content_ads(
            segs, model='m', slug='s', episode_id='e',
            podcast_name='p', episode_title='t', description_section='',
            llm_timeout=30, max_retries=1,
        )
    assert ads is None
