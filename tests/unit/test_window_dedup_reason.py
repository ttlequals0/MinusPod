"""Window dedup keeps the reason of the more confident detection."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))

from ad_detector.boundaries import deduplicate_window_ads


def _ad(start, end, confidence, reason, sponsor):
    return {'start': start, 'end': end, 'confidence': confidence,
            'reason': reason, 'sponsor': sponsor}


def test_the_more_confident_window_supplies_reason_and_sponsor():
    partial = _ad(1846.1, 1860.0, 0.80,
                  'Sponsored segment for Acme cloud services, starts here and '
                  'continues in next window beyond the end of this one.', 'Acme Cloud')
    complete = _ad(1846.1, 1868.4, 0.99, 'Sponsored segment promoting Acme.', 'Acme')

    merged = deduplicate_window_ads([partial, complete])

    assert len(merged) == 1
    assert merged[0]['confidence'] == 0.99
    assert merged[0]['reason'] == 'Sponsored segment promoting Acme.'
    assert merged[0]['sponsor'] == 'Acme'


def test_a_less_confident_later_window_does_not_replace_the_reason():
    complete = _ad(100.0, 130.0, 0.95, 'Acme read.', 'Acme')
    echo = _ad(120.0, 140.0, 0.60, 'Possibly the tail of an advertisement for Acme Cloud.',
               'Acme Cloud')

    merged = deduplicate_window_ads([complete, echo])

    assert merged[0]['reason'] == 'Acme read.'
    assert merged[0]['sponsor'] == 'Acme'


def test_equal_confidence_keeps_the_longer_reason():
    short = _ad(100.0, 130.0, 0.9, 'Acme read.', 'Acme')
    longer = _ad(120.0, 140.0, 0.9, 'Acme read with a discount code.', 'Acme Cloud')

    merged = deduplicate_window_ads([short, longer])

    assert merged[0]['reason'] == 'Acme read with a discount code.'
    assert merged[0]['sponsor'] == 'Acme Cloud'
