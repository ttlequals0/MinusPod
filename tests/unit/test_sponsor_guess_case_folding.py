"""Sponsor guessing folds case before rejecting filler words."""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))

from text_pattern_matcher import _guess_sponsor_from_segment, split_template_text  # noqa: E402


@pytest.mark.parametrize('word', ['The', 'THE', 'Today', 'Our', 'This', 'An'])
def test_capitalized_filler_is_not_a_sponsor(word):
    # The transcript capitalizes sentence-initial words, so comparing the raw
    # candidate against a lowercase skip list let these through as brands.
    segment = f'This episode is brought to you by {word} folks at the store.'
    assert _guess_sponsor_from_segment(segment) is None


@pytest.mark.parametrize('word', ['Produced', 'Hosted', 'Edited'])
def test_credit_verbs_are_not_sponsors(word):
    segment = f'This episode is brought to you by {word} by someone at the studio.'
    assert _guess_sponsor_from_segment(segment) is None


@pytest.mark.parametrize('word', ['Sponsor', 'Advertisement', 'Unknown', 'None'])
def test_extraction_failure_markers_are_not_sponsors(word):
    # Shared with the detector and pattern creation via INVALID_SPONSOR_VALUES.
    segment = f'This episode is brought to you by {word} and more content here.'
    assert _guess_sponsor_from_segment(segment) is None


@pytest.mark.parametrize('brand', ['Acme', 'Widgetco', 'Squarespace'])
def test_real_brands_still_resolve(brand):
    segment = f'This episode is brought to you by {brand}, the best of its kind.'
    assert _guess_sponsor_from_segment(segment) == brand


def test_lowercase_brand_is_title_cased():
    segment = 'This episode is brought to you by acme, the best of its kind.'
    assert _guess_sponsor_from_segment(segment) == 'Acme'


def test_split_segments_do_not_carry_filler_sponsors():
    # split_template_text feeds get_or_create_known_sponsor, so a filler word
    # here would create a sponsor row named "The".
    text = ('This episode is brought to you by The folks at one place. '
            'This episode is sponsored by Widgetco, which makes widgets.')
    sponsors = {seg['sponsor'] for seg in split_template_text(text)}
    assert 'The' not in sponsors
    assert 'Widgetco' in sponsors
