"""Tests for the 2.5.13 lower duration bound in create_pattern_from_ad.

Pre-2.5.13 the function only enforced an upper bound (<= 120s). Pattern #356
(Patreon, 8s, from first-pass Claude detection) shipped because no minimum
existed. Real sponsor reads almost never fit in under 15 seconds.
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


def _real_ad_segments(text, start, end):
    return [{'start': start, 'end': end, 'text': text}]


REAL_AD_TEXT = (
    "BetterHelp therapy can help you live a more empowered life. "
    "Visit them online to start with a licensed therapist today. "
    "BetterHelp matches you in 24 hours."
)


def test_short_detection_below_15s_is_rejected(db):
    matcher = TextPatternMatcher(db=db)
    pid = matcher.create_pattern_from_ad(
        segments=_real_ad_segments(REAL_AD_TEXT, 0.0, 8.0),
        start=0.0, end=8.0,
        sponsor='BetterHelp',
        scope='podcast', podcast_id='some-show', episode_id='abc',
    )
    assert pid is None


def test_short_detection_at_14s_is_rejected(db):
    matcher = TextPatternMatcher(db=db)
    pid = matcher.create_pattern_from_ad(
        segments=_real_ad_segments(REAL_AD_TEXT, 0.0, 14.0),
        start=0.0, end=14.0,
        sponsor='BetterHelp',
        scope='podcast', podcast_id='some-show', episode_id='abc',
    )
    assert pid is None


def test_15s_floor_is_inclusive(db):
    matcher = TextPatternMatcher(db=db)
    pid = matcher.create_pattern_from_ad(
        segments=_real_ad_segments(REAL_AD_TEXT, 0.0, 15.0),
        start=0.0, end=15.0,
        sponsor='BetterHelp',
        scope='podcast', podcast_id='some-show', episode_id='abc',
    )
    assert pid is not None


def test_upper_120s_bound_still_enforced(db):
    matcher = TextPatternMatcher(db=db)
    pid = matcher.create_pattern_from_ad(
        segments=_real_ad_segments(REAL_AD_TEXT, 0.0, 121.0),
        start=0.0, end=121.0,
        sponsor='BetterHelp',
        scope='podcast', podcast_id='some-show', episode_id='abc',
    )
    assert pid is None
