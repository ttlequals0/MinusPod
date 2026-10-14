"""A per-feed max ad duration lifts the longest single LLM detection."""
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from tests.app_bootstrap import bootstrap

bootstrap('window_cap_test_')

from ad_detector import AdDetector
from config import MAX_AD_DURATION_WINDOW
from llm_capabilities import PASS_AD_DETECTION_1
from main_app import processing
from main_app.pipeline import detect


class _StubResponse:
    def __init__(self, content):
        self.content = content
        self.usage = {}


def _run(detector, start, end):
    body = (f'[{{"start": {start}, "end": {end}, "confidence": 0.9, '
            '"sponsor": "Acme", "reason": "Acme promo code read"}]')
    window = {'start': 0.0, 'end': 900.0,
              'segments': [{'start': 0.0, 'end': 900.0, 'text': 'hello'}]}
    with patch.object(detector, '_call_llm_for_window',
                      return_value=(_StubResponse(body), None)):
        return detector._process_single_window(
            window_idx=0, window=window, total_windows=1,
            model='m', system_prompt='sys', description_section='',
            podcast_name='p', episode_title='t',
            audio_enforcer=None, audio_analysis=None,
            llm_timeout=30, max_retries=1,
            slug='example-podcast', episode_id='a1b2c3d4e5f6',
            pass_name=PASS_AD_DETECTION_1,
            window_label_prefix='Window', validate_timestamps=False)


def test_default_cap_drops_a_540_second_ad():
    detector = AdDetector(api_key='test-key')
    assert detector.max_window_ad_seconds == MAX_AD_DURATION_WINDOW
    result = _run(detector, 100.0, 640.0)
    assert result.ads == []
    assert result.dropped_too_long == 1


def test_raised_cap_keeps_a_540_second_ad():
    detector = AdDetector(api_key='test-key')
    detector.max_window_ad_seconds = 600.0
    result = _run(detector, 100.0, 640.0)
    assert len(result.ads) == 1
    assert result.dropped_too_long == 0


def test_first_pass_stage_lifts_cap_for_the_run_and_restores_it():
    seen = []
    fake = MagicMock()
    fake.max_window_ad_seconds = MAX_AD_DURATION_WINDOW

    def capture(*_a, **_kw):
        seen.append(fake.max_window_ad_seconds)
        raise RuntimeError('stop after capture')

    fake.process_transcript.side_effect = capture
    ctx = SimpleNamespace(slug='example-podcast', episode_id='a1b2c3d4e5f6', podcast_id=1)
    with patch.object(processing, 'ad_detector', fake), \
         patch.object(detect.common, '_publish_status'), \
         patch.object(detect, 'clear_fallback'), \
         patch.object(detect, 'resolve_max_ad_duration', return_value=600.0):
        try:
            detect._detect_ads_first_pass(ctx, [], '/tmp/a.mp3', False, None, None)
        except RuntimeError:
            pass
    assert seen == [600.0]
    assert fake.max_window_ad_seconds == MAX_AD_DURATION_WINDOW
