"""The vague-reason penalty applies only to reasons that name nothing."""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))

from ad_validator import AdValidator


def _check(reason, confidence=0.8):
    flags = []
    adjusted = AdValidator(1200.0, [])._check_reason_quality(
        {'reason': reason}, confidence, flags)
    return adjusted, flags


@pytest.mark.parametrize('reason', [
    'advertisement',
    'Sponsor read',
    'possible ad',
    'Ad detected',
    'Likely ad segment',
    'Promotional content',
    'Sponsor read with typical ad language and a call to action',
])
def test_a_reason_that_names_nothing_is_still_penalised(reason):
    adjusted, flags = _check(reason)
    assert adjusted == pytest.approx(0.7)
    assert 'WARN: Vague reason' in flags


def test_a_sponsored_read_that_names_the_advertiser_is_not_penalised():
    reason = ("Partial sponsored weather segment promoting Acmeledger's "
              "cloud-based business system, ending with acmeledger.se")
    adjusted, flags = _check(reason)
    assert adjusted == pytest.approx(0.8)
    assert 'WARN: Vague reason' not in flags


def test_a_known_sponsor_beside_a_vague_word_gets_the_sponsor_boost():
    adjusted, flags = _check('Sponsor read for Squarespace')
    assert adjusted == pytest.approx(0.9)
    assert 'WARN: Vague reason' not in flags


def test_an_unknown_named_sponsor_gets_no_boost():
    adjusted, flags = _check('Sponsor: Acme Tools')
    assert adjusted == pytest.approx(0.8)
    assert flags == []


@pytest.mark.parametrize('reason', [
    'Possible ad for Acme Tools, uncertain',
    'Possible ad but may be show content about Acme',
    'Likely ad for Squarespace',
])
def test_a_hedged_reason_is_penalised_even_when_it_names_a_brand(reason):
    adjusted, flags = _check(reason)
    assert adjusted == pytest.approx(0.7)
    assert 'WARN: Vague reason' in flags
