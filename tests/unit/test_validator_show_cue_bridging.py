"""The leading and trailing extensions and the gap merge do not run across a matched show intro/outro cue."""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))

from ad_validator import AdValidator, ValidationResult

# (id, cue overrides or None for no cue, feed settings, whether the cue is partly outside the range, blocks)
VARIANTS = [
    ('show_cue', {}, None, False, True),
    ('no_cue', None, None, False, False),
    ('below_confidence', {'confidence': 0.2}, None, False, False),
    ('below_feed_min', {}, {'min_confidence': 0.95}, False, False),
    ('boundary_role', {'role': 'boundary'}, None, False, False),
    ('partly_inside', {}, None, True, True),
]


def _cue(start, end, confidence=0.9, role='non_ad', source='template'):
    return {'signal_type': 'audio_cue', 'start': start, 'end': end, 'confidence': confidence,
            'details': {'source': source, 'role': role}}


def _word(start, end=None):
    end = end or start + 1.0
    return {'start': start, 'end': end, 'text': 'Welcome to the show.',
            'words': [{'start': start, 'end': end}]}


def _validator(segments, variant, inside, partial):
    _, overrides, settings, is_partial, _ = variant
    validator = AdValidator(episode_duration=3654.27, segments=segments, cue_snap_settings=settings)
    span = partial if is_partial else inside
    validator._audio_analysis = {'signals': [] if overrides is None else [_cue(*span, **overrides)]}
    return validator


def _ids(variant):
    return variant[0]


@pytest.mark.parametrize('variant', VARIANTS, ids=_ids)
def test_leading_extension(variant):
    validator = _validator([_word(4.0), _word(23.86, 26.9)], variant, (0.5, 3.0), (2.0, 6.0))
    head = {'start': 4.0, 'end': 22.18}
    validator._extend_leading_ad([head], ValidationResult(ads=[]))
    assert head['start'] == (4.0 if variant[4] else 0.0)


@pytest.mark.parametrize('variant', VARIANTS, ids=_ids)
def test_trailing_extension(variant):
    validator = _validator([_word(10.0)], variant, (3651.5, 3653.5), (3651.5, 3660.0))
    tail = {'start': 3599.6, 'end': 3650.92}
    validator._extend_trailing_ad([tail], ValidationResult(ads=[]))
    assert tail['end'] == (3650.92 if variant[4] else 3654.27)


@pytest.mark.parametrize('variant', VARIANTS, ids=_ids)
def test_gap_merge(variant):
    validator = _validator([_word(412.0, 416.3), _word(421.0, 425.0)], variant,
                           (416.8, 417.8), (416.8, 419.5))
    merged = validator._merge_close_ads(
        [{'start': 394.19, 'end': 416.3}, {'start': 418.2, 'end': 448.11}], ValidationResult(ads=[]))
    expected = [(394.19, 416.3), (418.2, 448.11)] if variant[4] else [(394.19, 448.11)]
    assert [(m['start'], m['end']) for m in merged] == expected
