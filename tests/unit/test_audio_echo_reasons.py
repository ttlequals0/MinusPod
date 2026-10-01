"""A reason that only echoes the prompt's audio signals is neither ad language nor a sponsor (#807)."""
import pytest

from tests.app_bootstrap import bootstrap

bootstrap('audio_echo_reasons_test_')

from ad_detector.prompts import _extract_sponsor_name, _normalize_ad
from utils.constants import AUDIO_ECHO_RE, mentions_advertising, sanitize_sponsor_label

ECHO_REASONS = [
    'DAI transition pair and volume anomaly indicate ad boundary',
    'Volume anomaly at 1300.0s (volume_decrease, 12.0 dB)',
    'Splice evidence: digital silence and loudness step at both edges',
    'splice_1261.3',
    '12.0 dB',
]


@pytest.mark.parametrize('reason', ECHO_REASONS)
def test_an_audio_echo_is_not_ad_language(reason):
    assert AUDIO_ECHO_RE.search(reason)
    assert mentions_advertising(reason) is False


@pytest.mark.parametrize('reason', ECHO_REASONS)
def test_an_audio_echo_is_never_a_sponsor(reason):
    assert sanitize_sponsor_label(reason) is None
    assert _extract_sponsor_name({'reason': reason}) == 'Advertisement detected'


def test_real_ad_words_still_count_beside_an_echo():
    assert mentions_advertising('Acme sponsor read; DAI transition pair at both edges') is True
    assert mentions_advertising('Non-English language segment (likely DAI ad)') is True


def test_a_long_span_with_only_an_echoed_reason_is_rejected():
    ad = {'start': 100.0, 'end': 260.0, 'confidence': 0.9,
          'reason': 'DAI transition pair and volume anomaly indicate ad boundary'}
    assert _normalize_ad(ad, 100.0, 260.0) is None
