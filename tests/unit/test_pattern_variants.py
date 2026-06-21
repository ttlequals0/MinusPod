"""Unit tests for the shared intro/outro variant helper (#399)."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))

from pattern_variants import (
    VARIANT_CAP,
    derive_intro_outro,
    dedupe_and_cap,
    merge_variants,
    variants_for_pattern,
)

_AD_A = (
    'This episode is brought to you by Acme. Acme makes great widgets for '
    'busy people everywhere. Visit acme dot com slash deal for twenty percent '
    'off your very first order today.'
)
_AD_B = (
    'Today is sponsored by Acme. Acme builds reliable tools for home and work. '
    'Head over to acme dot com slash promo to save money on your next order now.'
)


def test_derive_from_full_template_yields_both_sides():
    intro, outro = derive_intro_outro(_AD_A)
    assert intro and outro
    assert isinstance(intro, list) and isinstance(outro, list)


def test_derive_from_empty_is_empty():
    assert derive_intro_outro('') == ([], [])
    assert derive_intro_outro(None) == ([], [])


def test_dedupe_drops_near_identical_and_caps():
    phrases = [
        'visit acme dot com slash deal today',
        'visit acme dot com slash deal today',   # exact dup -> dropped
        'go to acme today for a discount now',
        'one', 'two', 'three', 'four', 'five', 'six',
    ]
    kept = dedupe_and_cap(phrases)
    assert len(kept) == VARIANT_CAP
    # The exact duplicate is folded out, so the first two entries collapse.
    assert kept[0] == 'visit acme dot com slash deal today'
    assert kept[1] == 'go to acme today for a discount now'


def test_dedupe_skips_blank_and_non_strings():
    assert dedupe_and_cap(['', '  ', None, 5, 'real phrase here']) == ['real phrase here']


def test_merge_two_manual_variantless_patterns_yields_variants():
    # Both rows have empty variant arrays (manual patterns); the helper derives
    # intro/outro from each template and unions them.
    p1 = {'text_template': _AD_A, 'intro_variants': '[]', 'outro_variants': '[]'}
    p2 = {'text_template': _AD_B, 'intro_variants': [], 'outro_variants': []}
    intros, outros = merge_variants([p1, p2])
    assert intros and outros
    assert len(intros) <= VARIANT_CAP
    assert len(outros) <= VARIANT_CAP


def test_merge_respects_cap():
    # Genuinely distinct phrases (no two are >=95% similar) so the cap, not the
    # dedupe, is what bounds the arrays.
    distinct_intros = [
        'welcome back everyone to another great show',
        'before we begin a quick word from todays partner',
        'hey folks thanks so much for tuning in again',
        'lets talk about something that has helped me lately',
        'you have heard us mention this company before',
        'here is a brand we genuinely trust and recommend',
        'one more thing we wanted to share with you',
    ]
    distinct_outros = [
        'go check them out and tell them we sent you',
        'that link gives you a special listener discount',
        'use the code at checkout to save on your order',
        'support the show by visiting our sponsor today',
        'now back to the rest of the episode everyone',
        'do not miss this limited time offer this week',
        'head there now while the deal is still running',
    ]
    patterns = [
        {'text_template': f'Sponsor read number {i}.',
         'intro_variants': [distinct_intros[i]],
         'outro_variants': [distinct_outros[i]]}
        for i in range(7)
    ]
    intros, outros = merge_variants(patterns)
    assert len(intros) == VARIANT_CAP
    assert len(outros) == VARIANT_CAP


def test_variants_for_pattern_uses_stored_then_derives_missing_side():
    # Has intros but no outros -> outro derived from template, intros kept.
    p = {'text_template': _AD_A, 'intro_variants': ['kept intro'], 'outro_variants': []}
    intros, outros = variants_for_pattern(p)
    assert intros == ['kept intro']
    assert outros  # derived
