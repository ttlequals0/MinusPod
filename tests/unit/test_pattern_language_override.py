"""Regression: create_pattern_from_ad must stamp the per-feed language override.

The detector and verification pass call create_pattern_from_ad with the feed
SLUG as `podcast_id` (see ad_detector __init__ "podcast_id = ctx.slug" and
pattern_service "podcast_id=slug"). The stamped source_language must therefore
resolve the per-feed language_override via the slug, not via an integer-PK
lookup that silently misses and falls back to the global setting (#376).
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


def _source_language(db, pattern_id):
    row = db.get_connection().execute(
        'SELECT source_language FROM ad_patterns WHERE id = ?', (pattern_id,)
    ).fetchone()
    return row['source_language'] if row else None


_REAL_AD = (
    "BetterHelp therapy can help you live a more empowered life. "
    "Visit them online to start with a licensed therapist today. "
    "BetterHelp matches you in 24 hours."
)


def test_pattern_stamps_per_feed_language_override(db):
    db.set_setting('whisper_language', 'en', is_default=False)
    db.create_podcast('lang-show', 'https://example.com/feed.xml', title='Lang Show')
    db.update_podcast('lang-show', language_override='de')
    matcher = TextPatternMatcher(db=db)

    pattern_id = matcher.create_pattern_from_ad(
        segments=[{'start': 0.0, 'end': 60.0, 'text': _REAL_AD}],
        start=0.0, end=60.0, sponsor='BetterHelp', scope='podcast',
        podcast_id='lang-show', episode_id='ep1',
    )

    assert pattern_id is not None
    assert _source_language(db, pattern_id) == 'de'


def test_pattern_falls_back_to_global_without_override(db):
    db.set_setting('whisper_language', 'en', is_default=False)
    db.create_podcast('plain-show', 'https://example.com/feed.xml', title='Plain Show')
    matcher = TextPatternMatcher(db=db)

    pattern_id = matcher.create_pattern_from_ad(
        segments=[{'start': 0.0, 'end': 60.0, 'text': _REAL_AD}],
        start=0.0, end=60.0, sponsor='BetterHelp', scope='podcast',
        podcast_id='plain-show', episode_id='ep1',
    )

    assert pattern_id is not None
    assert _source_language(db, pattern_id) == 'en'
