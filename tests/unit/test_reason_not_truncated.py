"""The detector's reason text is stored whole (#591).

It used to be cut to 300 characters, and to 150 when combined with a sponsor,
which put a literal "..." in the UI with no fuller text to expand to.
"""
import json
import os
import sys
import tempfile

os.environ.setdefault('MINUSPOD_DATA_DIR', tempfile.mkdtemp(prefix='reason_len_test_'))
os.environ.setdefault('SECRET_KEY', 'test-secret')

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))

from ad_detector.prompts import parse_ads_from_response
from utils.constants import REASON_DESCRIPTION_MAX

LONG = (
    'The segment begins with a clear sponsorship announcement, then proceeds '
    'to describe the product at length, including a call to action with a '
    'vanity URL and a discount code, followed by host banter about the '
    'product that continues well past the usual read. '
) * 4  # ~700 chars, comfortably past both old caps


def _parse(ad):
    return parse_ads_from_response(json.dumps([ad]))


def test_long_description_survives_whole():
    ads = _parse({
        'start': 10.0, 'end': 40.0, 'confidence': 0.9,
        'description': LONG,
    })
    assert len(ads) == 1
    assert ads[0]['reason'] == LONG.strip() or ads[0]['reason'] == LONG
    assert not ads[0]['reason'].endswith('...')


def test_long_description_survives_when_combined_with_a_sponsor():
    # The 150-char cut only applied on this path, so it needs its own guard.
    ads = _parse({
        'start': 10.0, 'end': 40.0, 'confidence': 0.9,
        'sponsor': 'Acme Corp',
        'description': LONG,
    })
    assert len(ads) == 1
    reason = ads[0]['reason']
    assert len(reason) > 400, f'reason was cut to {len(reason)} chars'
    assert not reason.endswith('...')


def test_pathological_length_still_has_a_backstop():
    huge = 'x' * (REASON_DESCRIPTION_MAX + 500)
    ads = _parse({
        'start': 10.0, 'end': 40.0, 'confidence': 0.9,
        'description': huge,
    })
    assert len(ads) == 1
    assert len(ads[0]['reason']) <= REASON_DESCRIPTION_MAX
    assert ads[0]['reason'].endswith('...')


def test_backstop_is_far_above_the_old_caps():
    assert REASON_DESCRIPTION_MAX > 300
