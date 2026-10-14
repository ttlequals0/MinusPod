"""A per-feed max ad duration lifts the longest single LLM detection."""
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from tests.app_bootstrap import bootstrap

bootstrap('window_cap_test_')

from ad_detector import AdDetector
from config import (
    MAX_AD_DURATION_WINDOW, resolve_max_llm_window_ad_seconds, resolve_window_ad_cap,
)
from llm_capabilities import PASS_AD_DETECTION_1
from main_app import processing
from main_app.pipeline import detect


class _StubResponse:
    def __init__(self, content):
        self.content = content
        self.usage = {}


def _run(detector, start, end, **kwargs):
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
            window_label_prefix='Window', validate_timestamps=False, **kwargs)


def test_default_cap_drops_a_540_second_ad():
    detector = AdDetector(api_key='test-key')
    result = _run(detector, 100.0, 640.0)
    assert result.ads == []
    assert result.dropped_too_long == 1


def test_raised_cap_keeps_a_540_second_ad():
    detector = AdDetector(api_key='test-key')
    result = _run(detector, 100.0, 640.0, max_window_ad_seconds=600.0)
    assert len(result.ads) == 1
    assert result.dropped_too_long == 0


class _FakeDb:
    def __init__(self, window=None, reject=None, hold=None, global_reject=None):
        self.settings = {'max_llm_window_ad_seconds': window,
                         'max_ad_duration_seconds': global_reject}
        self.overrides = {'max_ad_duration_reject_override': reject,
                          'max_ad_duration_override': hold}

    def get_setting_float(self, key, default):
        value = self.settings.get(key)
        return default if value is None else value

    def get_podcast_cue_settings_overrides(self, _podcast_id):
        return self.overrides


def test_cap_with_nothing_set_is_the_default():
    assert resolve_window_ad_cap(_FakeDb(), 1) == 420.0


def test_cap_raised_by_the_hold_cap_alone():
    assert resolve_window_ad_cap(_FakeDb(hold=600.0), 1) == 600.0


def test_cap_raised_by_the_reject_override_alone():
    assert resolve_window_ad_cap(_FakeDb(reject=540.0), 1) == 540.0


def test_cap_uses_the_global_window_setting_as_the_floor():
    assert resolve_window_ad_cap(_FakeDb(window=700.0, hold=600.0), 1) == 700.0


def test_cap_falls_back_to_the_constant_when_the_db_errors():
    assert resolve_max_llm_window_ad_seconds(MagicMock(get_setting_float=MagicMock(
        side_effect=RuntimeError('boom')))) == MAX_AD_DURATION_WINDOW


def _first_pass_value(db_cap):
    fake = MagicMock()
    fake.process_transcript.side_effect = RuntimeError('stop after capture')
    ctx = SimpleNamespace(slug='example-podcast', episode_id='a1b2c3d4e5f6', podcast_id=1)
    with patch.object(processing, 'ad_detector', fake), \
         patch.object(detect.common, '_publish_status'), \
         patch.object(detect, 'clear_fallback'), \
         patch.object(detect, 'resolve_window_ad_cap', return_value=db_cap):
        with pytest.raises(RuntimeError):
            detect._detect_ads_first_pass(ctx, [], '/tmp/a.mp3', False, None, None)
    return fake.process_transcript.call_args.kwargs['max_window_ad_seconds']


def test_first_pass_stage_passes_the_feed_cap_as_an_argument():
    assert _first_pass_value(600.0) == 600.0
