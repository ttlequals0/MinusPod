"""Tests for the 2.5.13 sponsor-occurrence guard in TextPatternMatcher.create_pattern_from_ad.

Real ads repeat the brand name; a single mention is a host name-drop the
verification pass mis-classified. The guard rejects patterns whose sponsor
name appears fewer than 2 times (case-insensitive substring count) in the
extracted ad_text.

The canonical false positive was Pattern #354 (drink-champs, Modelo):
~870 chars of host conversation about meeting Nas where "Modelo" was
mentioned exactly once. The verification pass returned this as a
"missed Modelo ad" and the pattern was written without any occurrence
check.
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
    instance = Database(data_dir=str(tmp_path))
    yield instance
    Database._instance = None


def _segments_for_text(text: str, start: float = 0.0, end: float = 60.0):
    return [{'start': start, 'end': end, 'text': text}]


def test_create_pattern_rejected_when_sponsor_appears_once(db):
    """The Modelo false positive: 870 chars of host conversation, brand mentioned 1x."""
    matcher = TextPatternMatcher(db=db)
    host_chatter = (
        "This is a fucking performance. Yo, how you get the big, Modelo? "
        "No, no, no, later, later, later. I'm good. I'm good. We shootin'. "
        "Let's go. Next question. You ever meet Pac, though? No. Never met Pac. "
        "Biggie. Met Biggie. I got a Nas story. I met Nas in New York, we was "
        "exchanging numbers, and you know my motherfucking email is qbkiller "
        "at T-Mobile. Oh my God! QB Killer? Yeah. Oh my God. Quarterback. "
        "Yeah, quarterback. Quarterback, bitch."
    )
    pattern_id = matcher.create_pattern_from_ad(
        segments=_segments_for_text(host_chatter),
        start=0.0, end=37.0,
        sponsor='Modelo',
        scope='podcast',
        podcast_id='drink-champs',
        episode_id='30c9a2d49f13',
    )
    assert pattern_id is None, (
        "Pattern with sponsor mentioned only once must be rejected"
    )


def test_create_pattern_allowed_when_sponsor_appears_twice(db):
    """Genuine sponsor read mentions the brand at least twice."""
    matcher = TextPatternMatcher(db=db)
    # Avoid two ad-transition phrases - those trip a separate guard.
    real_ad = (
        "BetterHelp therapy can help you live a more empowered life. "
        "Visit them online to start with a licensed therapist today. "
        "BetterHelp matches you in 24 hours."
    )
    pattern_id = matcher.create_pattern_from_ad(
        segments=_segments_for_text(real_ad),
        start=0.0, end=60.0,
        sponsor='BetterHelp',
        scope='podcast',
        podcast_id='some-show',
        episode_id='abc123',
    )
    assert pattern_id is not None


def test_create_pattern_allowed_when_brand_lives_inside_url(db):
    """DeleteMe-style: brand never appears standalone, only inside joindeleteme.com.

    The guard uses substring count (not word-boundary) so this passes.
    """
    matcher = TextPatternMatcher(db=db)
    deleteme_ad = (
        "Use the promo code TWIT at checkout. The only way to get 20% off "
        "is to go to joindeleteme.com slash TWIT, joindeleteme, one word, "
        "dot com slash TWIT, and you gotta use the code TWIT at checkout. "
        "That's joindeleteme.com slash TWIT, offer code TWIT. Joindeleteme."
    )
    pattern_id = matcher.create_pattern_from_ad(
        segments=_segments_for_text(deleteme_ad),
        start=0.0, end=60.0,
        sponsor='DeleteMe',
        scope='podcast',
        podcast_id='security-now-audio',
        episode_id='xyz',
    )
    assert pattern_id is not None, (
        "Pattern whose sponsor lives inside a compound URL must still be accepted"
    )


def test_create_pattern_rejected_when_sponsor_absent_from_text(db):
    """Sponsor name doesn't appear at all in the supposed ad - reject."""
    matcher = TextPatternMatcher(db=db)
    unrelated = (
        "And we're back, talking about the new product launch. The team has "
        "been heads-down for weeks. Honestly, the feedback so far has been "
        "incredible. We'll have more to share next week. Anyway, let's get "
        "to today's first topic."
    )
    pattern_id = matcher.create_pattern_from_ad(
        segments=_segments_for_text(unrelated),
        start=0.0, end=30.0,
        sponsor='Acme',
        scope='podcast',
        podcast_id='some-show',
        episode_id='abc',
    )
    assert pattern_id is None
