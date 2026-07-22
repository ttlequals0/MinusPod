"""Tests for text_pattern_matcher.split_template_text (issue #563).

Pure helper: segments ad template text at AD_TRANSITION_PHRASES boundaries,
returning [{'text': str, 'sponsor': Optional[str]}, ...]. Shared by
split_pattern (existing patterns) and the manual correction paths
(_resolve_or_create_pattern_from_text, _submit_correction_create) so
multi-sponsor text gets split at creation time instead of forming one
oversized pattern.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))

from text_pattern_matcher import split_template_text, MIN_TEXT_LENGTH


def test_no_transition_phrase_returns_single_segment():
    text = (
        "Just some ordinary podcast content with no sponsor phrases in it "
        "at all, just two hosts chatting about their week and upcoming plans."
    )
    segments = split_template_text(text)
    assert segments == [{'text': text, 'sponsor': None}]


def test_single_transition_phrase_returns_single_segment_no_sponsor():
    text = (
        "This episode is brought to you by Acme. Acme makes great things "
        "and you should buy Acme products today from Acme dot com right now."
    )
    segments = split_template_text(text)
    assert len(segments) == 1
    assert segments[0]['text'] == text
    assert segments[0]['sponsor'] is None


def test_two_sponsors_split_with_heuristic_names():
    text = (
        "This episode is brought to you by Acme. Acme provides the best "
        "widgets around, visit acme dot com for twenty percent off today. "
        "This episode is sponsored by Widgetco. Widgetco has amazing "
        "deals this week, check out widgetco dot com right now for savings."
    )
    segments = split_template_text(text)
    assert len(segments) == 2
    assert segments[0]['sponsor'] == 'Acme'
    assert segments[1]['sponsor'] == 'Widgetco'
    assert 'Acme' in segments[0]['text']
    assert 'Widgetco' in segments[1]['text']
    assert 'Acme' not in segments[1]['text']


def test_three_sponsors_split_into_three_segments():
    text = (
        "This episode is brought to you by Acme. Acme provides the best "
        "widgets around, visit acme dot com for twenty percent off today. "
        "This episode is sponsored by Widgetco. Widgetco has amazing "
        "deals this week, check out widgetco dot com right now for savings. "
        "Thanks to Spanso for supporting the show, go check out spanso dot "
        "com slash podcast for a free trial of their new gadget service."
    )
    segments = split_template_text(text)
    assert len(segments) == 3
    assert [s['sponsor'] for s in segments] == ['Acme', 'Widgetco', 'Spanso']


def test_overlapping_phrase_is_deduped_not_double_split():
    # "brought to you by" nests inside "this episode is brought to you by";
    # a naive scan registers two adjacent offsets there and would spin off
    # a spurious tiny segment between them (the known defect being fixed).
    filler = "word " * 20
    text = (
        f"{filler}this episode is brought to you by Acme, your favorite "
        f"widget maker with the best prices around town this month. "
        f"Thanks to Widgetco for also supporting the show this week with "
        f"their new gadget line available everywhere online right now."
    )
    segments = split_template_text(text)
    assert len(segments) == 2


def test_segments_below_min_length_are_dropped():
    text = (
        "This episode is brought to you by Acme, a wonderful sponsor with "
        "long-form ad copy describing all of their excellent products in "
        "great detail for at least fifty characters worth of content here. "
        "Thanks to X."
    )
    segments = split_template_text(text)
    assert all(len(s['text']) >= MIN_TEXT_LENGTH for s in segments)
    assert len(segments) == 1


def test_prefix_before_first_offset_joins_first_segment():
    prefix = "Hey everyone welcome back to the show before we get started,"
    text = (
        f"{prefix} this episode is brought to you by Acme who make great "
        f"widgets you can buy today at acme dot com for twenty off now. "
        f"Thanks to Widgetco for sponsoring too, check widgetco dot com out "
        f"today for their amazing new gadget deals available everywhere."
    )
    segments = split_template_text(text)
    assert segments[0]['text'].startswith(prefix)


def test_empty_text_returns_single_empty_segment():
    segments = split_template_text("")
    assert segments == [{'text': '', 'sponsor': None}]
