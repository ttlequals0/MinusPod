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


@pytest.mark.parametrize('reason', [
    'Acme sponsor read; DAI transition pair at both edges',
    'Non-English language segment (likely DAI ad)',
    'Ad break with three ads, loudness step at both edges',
    'Mid-roll ad break; DAI transition pair at both edges',
])
def test_real_ad_words_still_count_beside_an_echo(reason):
    assert mentions_advertising(reason) is True


@pytest.mark.parametrize('reason', [
    'A summary of the discussion so far',
    'Non-English language segment (likely DAI ad)',
    'Acme Tools drill sale',
])
def test_a_short_prose_reason_is_not_a_sponsor(reason):
    assert _extract_sponsor_name({'reason': reason}) == 'Advertisement detected'


def test_a_long_span_with_only_an_echoed_reason_is_rejected():
    ad = {'start': 100.0, 'end': 260.0, 'confidence': 0.9,
          'reason': 'DAI transition pair and volume anomaly indicate ad boundary'}
    assert _normalize_ad(ad, 100.0, 260.0) is None


def test_a_short_reason_keeps_a_framed_sponsor_name():
    assert _extract_sponsor_name({'reason': 'Brought to you by Acme Tools online therapy'}) == 'Acme Tools'
