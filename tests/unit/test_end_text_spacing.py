"""end_text verification ignores spacing differences around punctuation."""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))

from ad_validator import AdValidator

_SEGMENTS = [
    {'start': 100.0, 'end': 104.0, 'text': 'Och nu en annons om molnet.'},
    {'start': 104.0, 'end': 108.0, 'text': 'Läs mer på acmeledger.se'},
]


def _verify(end_text):
    flags = []
    ad = {'start': 100.0, 'end': 108.0, 'end_text': end_text}
    adjusted = AdValidator(1200.0, _SEGMENTS)._verify_in_transcript(ad, 0.9, flags)
    return adjusted, flags


@pytest.mark.parametrize('end_text', [
    'Läs mer på acmeledger .se',
    'läs  mer på acmeledger. se',
    'Läs mer på acmeledger.se',
])
def test_end_text_matches_despite_spacing(end_text):
    adjusted, flags = _verify(end_text)
    assert 'WARN: end_text not found in transcript' not in flags
    assert adjusted == pytest.approx(0.9)


def test_end_text_that_is_not_spoken_is_still_penalised():
    adjusted, flags = _verify('Besök annat.se idag')
    assert 'WARN: end_text not found in transcript' in flags
    assert adjusted == pytest.approx(0.85)
