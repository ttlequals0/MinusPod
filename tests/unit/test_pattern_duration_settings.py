"""Configurable pattern duration bounds and the split-instead-of-drop path.

A feed whose ad blocks run past the ceiling used to teach the matcher nothing:
26 spans between 120s and 442s were dropped in 8 hours on one instance. The
bounds are now settings, and a span over the ceiling is split at its ad
transitions so each read is learned on its own.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))

from database import Database
from text_pattern_matcher import TextPatternMatcher


@pytest.fixture
def db(tmp_path):
    Database._instance = None
    if hasattr(Database, '_initialized'):
        Database._initialized = False
    inst = Database(data_dir=str(tmp_path))
    yield inst
    Database._instance = None


ONE_AD = (
    "BetterHelp therapy can help you live a more empowered life. "
    "Visit them online to start with a licensed therapist today. "
    "BetterHelp matches you in 24 hours."
)

# Two reads joined by a transition phrase, the shape the ceiling is meant to
# catch and the shape build_split_candidates can cut.
# Each read carries its own domain: extract_sponsor_from_text keys on one, and
# a piece with no sponsor of its own is skipped rather than mislabeled.
TWO_ADS = (
    "BetterHelp therapy can help you live a more empowered life. "
    "Visit betterhelp.com/show to start with a licensed therapist today. "
    "BetterHelp matches you in 24 hours. "
    "Today's sponsor is Squarespace. "
    "Squarespace gives you everything you need to build a website. "
    "Go to squarespace.com/podcast for a free trial with Squarespace."
)


def _segments(text, start, end):
    """One segment per sentence so the splitter has timestamps to cut on."""
    sentences = [s.strip() for s in text.split('. ') if s.strip()]
    step = (end - start) / len(sentences)
    return [
        {'start': start + i * step, 'end': start + (i + 1) * step,
         'text': s if s.endswith('.') else s + '.'}
        for i, s in enumerate(sentences)
    ]


def test_ceiling_is_configurable(db):
    """A span over the default ceiling is learned once the setting allows it."""
    db.set_setting('learning_max_pattern_duration', '300', is_default=False)
    matcher = TextPatternMatcher(db=db)
    pid = matcher.create_pattern_from_ad(
        segments=_segments(ONE_AD, 0.0, 200.0), start=0.0, end=200.0,
        sponsor='BetterHelp', scope='podcast', podcast_id='some-show',
        episode_id='abc')
    assert pid is not None


def test_floor_is_configurable(db):
    db.set_setting('learning_min_pattern_duration', '5', is_default=False)
    matcher = TextPatternMatcher(db=db)
    pid = matcher.create_pattern_from_ad(
        segments=_segments(ONE_AD, 0.0, 8.0), start=0.0, end=8.0,
        sponsor='BetterHelp', scope='podcast', podcast_id='some-show',
        episode_id='abc')
    assert pid is not None


def test_defaults_match_the_shipped_constants(db):
    matcher = TextPatternMatcher(db=db)
    assert matcher._pattern_duration_bounds() == (15, 120)


def test_long_multi_ad_span_is_split_rather_than_dropped(db):
    """The regression: a span over the ceiling used to teach nothing."""
    matcher = TextPatternMatcher(db=db)
    created = matcher.create_patterns_from_ad(
        segments=_segments(TWO_ADS, 0.0, 200.0), start=0.0, end=200.0,
        sponsor='BetterHelp', scope='podcast', podcast_id='some-show',
        episode_id='abc')
    assert len(created) == 2
    # Each result carries its own piece bounds so the caller fingerprints the
    # audio the pattern actually covers, not the whole span.
    assert all(c['end'] - c['start'] < 200.0 for c in created)
    assert created[0]['start'] == 0.0
    assert created[-1]['end'] == 200.0
    sponsors = {db.get_ad_pattern_by_id(c['id'])['sponsor'] for c in created}
    assert len(sponsors) == 2, sponsors


def test_long_span_without_a_transition_is_still_dropped(db):
    """Nothing to cut on means the contamination guard still applies."""
    matcher = TextPatternMatcher(db=db)
    ids = matcher.create_patterns_from_ad(
        segments=_segments(ONE_AD, 0.0, 200.0), start=0.0, end=200.0,
        sponsor='BetterHelp', scope='podcast', podcast_id='some-show',
        episode_id='abc')
    assert ids == []


def test_ordinary_span_creates_exactly_one_pattern(db):
    matcher = TextPatternMatcher(db=db)
    ids = matcher.create_patterns_from_ad(
        segments=_segments(ONE_AD, 0.0, 60.0), start=0.0, end=60.0,
        sponsor='BetterHelp', scope='podcast', podcast_id='some-show',
        episode_id='abc')
    assert len(ids) == 1


def test_standard_opener_is_not_read_as_two_transitions(db):
    """"This episode is brought to you by" also contains "brought to you by".

    Counting phrase-list entries scored the most common opener in podcasting
    as two transitions, so every read using it was dropped as contaminated.
    """
    matcher = TextPatternMatcher(db=db)
    text = ("This episode is brought to you by BetterHelp. "
            "BetterHelp matches you with a licensed therapist in 24 hours. "
            "Visit BetterHelp online to get started today.")
    pid = matcher.create_pattern_from_ad(
        segments=_segments(text, 0.0, 60.0), start=0.0, end=60.0,
        sponsor='BetterHelp', scope='podcast', podcast_id='some-show',
        episode_id='abc')
    assert pid is not None


def test_split_piece_without_its_own_sponsor_is_skipped(db):
    """Inheriting the parent sponsor across a transition would label the next
    advertiser's read with the previous one's name."""
    text = (
        "BetterHelp therapy can help you live a more empowered life. "
        "Visit betterhelp.com/show to start with a licensed therapist today. "
        "BetterHelp matches you in 24 hours. "
        "Today's sponsor is a company you have never heard of. "
        "They make a product and they would like you to consider buying it. "
        "That is the whole pitch, with nothing to identify them by."
    )
    matcher = TextPatternMatcher(db=db)
    created = matcher.create_patterns_from_ad(
        segments=_segments(text, 0.0, 200.0), start=0.0, end=200.0,
        sponsor='BetterHelp', scope='podcast', podcast_id='some-show',
        episode_id='abc')
    assert len(created) == 1
    assert db.get_ad_pattern_by_id(created[0]['id'])['sponsor'] == 'BetterHelp'
    assert created[0]['end'] < 200.0


def test_split_piece_may_exceed_the_contamination_ceiling(db):
    """The ceiling screens for multi-ad contamination. A piece already cut at
    its transitions has been screened, so a long single read is kept."""
    matcher = TextPatternMatcher(db=db)
    created = matcher.create_patterns_from_ad(
        segments=_segments(TWO_ADS, 0.0, 442.0), start=0.0, end=442.0,
        sponsor='BetterHelp', scope='podcast', podcast_id='some-show',
        episode_id='abc')
    assert created
    assert any(c['end'] - c['start'] > 120 for c in created)


def test_first_split_piece_keeps_the_caller_s_sponsor(db):
    """No divider is placed near the span start, so the opening read rarely
    carries a detectable sponsor of its own. Dropping it lost the read the
    caller had already identified."""
    text = (
        "BetterHelp therapy can help you live a more empowered life. "
        "Start with a licensed therapist today and see the difference. "
        "BetterHelp matches you in 24 hours with someone who fits. "
        "Today's sponsor is Squarespace. "
        "Squarespace gives you everything you need to build a website. "
        "Go to squarespace.com/podcast for a free trial with Squarespace."
    )
    matcher = TextPatternMatcher(db=db)
    created = matcher.create_patterns_from_ad(
        segments=_segments(text, 0.0, 200.0), start=0.0, end=200.0,
        sponsor='BetterHelp', scope='podcast', podcast_id='some-show',
        episode_id='abc')
    sponsors = [db.get_ad_pattern_by_id(c['id'])['sponsor'] for c in created]
    assert sponsors == ['BetterHelp', 'Squarespace']


def test_split_piece_is_still_bounded(db):
    """The ceiling is relaxed for a cut piece, not removed: one undetected
    transition would otherwise store a pattern of any length."""
    matcher = TextPatternMatcher(db=db)
    created = matcher.create_patterns_from_ad(
        segments=_segments(TWO_ADS, 0.0, 900.0), start=0.0, end=900.0,
        sponsor='BetterHelp', scope='podcast', podcast_id='some-show',
        episode_id='abc')
    assert created == []


# The opening read names its advertiser. A labeled brand that first appears
# only in the back half means the span opens with someone else's read.
MISATTRIBUTED = (
    "Squarespace gives you everything you need to build a beautiful website. "
    "Go to squarespace.com/podcast today for a free trial of the platform. "
    "It really is that easy to get a professional site of your very own. "
    "Whether a portfolio or a store, the templates cover all of it for you. "
    "BetterHelp matches you with a licensed therapist within a day. "
    "Visit betterhelp.com/show for ten percent off with BetterHelp."
)

MID_SPAN_BRAND = (
    "I recently inherited responsibility for a server with a failing drive. "
    "The previous admin had given up on it and the data seemed to be gone. "
    "BetterHelp is not that kind of rescue, but the shape is the same. "
    "BetterHelp matches you with a licensed therapist within a day of asking."
)


def test_brand_only_in_back_half_is_rejected(db):
    matcher = TextPatternMatcher(db=db)
    pid = matcher.create_pattern_from_ad(
        segments=_segments(MISATTRIBUTED, 0.0, 60.0), start=0.0, end=60.0,
        sponsor='BetterHelp', scope='podcast', podcast_id='some-show',
        episode_id='abc')
    assert pid is None


def test_brand_first_named_mid_span_still_learns(db):
    """A testimonial-shaped read names the brand partway in; that must not
    re-trip the placement rejection the alias fix removed."""
    matcher = TextPatternMatcher(db=db)
    pid = matcher.create_pattern_from_ad(
        segments=_segments(MID_SPAN_BRAND, 0.0, 60.0), start=0.0, end=60.0,
        sponsor='BetterHelp', scope='podcast', podcast_id='some-show',
        episode_id='abc')
    assert pid is not None
