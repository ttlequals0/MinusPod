"""Regression tests for TextPatternMatcher.split_pattern (issue #563).

split_pattern was refactored to use the shared split_template_text helper.
These tests pin: no-op on a single-sponsor pattern, multi-sponsor split with
inheritance + parent disable, and the overlap-dedupe fix (the known defect
where "brought to you by" nested inside "this episode is brought to you by"
used to register a spurious extra split point).
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


TWO_SPONSOR_TEXT = (
    "This episode is brought to you by Acme. Acme provides the best "
    "widgets around, visit acme dot com for twenty percent off today. "
    "This episode is sponsored by Widgetco. Widgetco has amazing "
    "deals this week, check out widgetco dot com right now for savings."
)


def _make_pattern(db, text, scope='podcast', podcast_id='show-a', network_id=None):
    return db.create_ad_pattern(
        scope=scope,
        text_template=text,
        podcast_id=podcast_id,
        network_id=network_id,
        created_from_episode_id='abc123',
        source_language='en',
    )


def test_single_sponsor_pattern_is_not_split(db):
    matcher = TextPatternMatcher(db=db)
    pattern_id = _make_pattern(db, "This episode is brought to you by Acme, "
                                    "makers of fine widgets since 1999 today.")
    result = matcher.split_pattern(pattern_id)
    assert result == []
    pattern = db.get_ad_pattern_by_id(pattern_id)
    assert pattern['is_active'] == 1


def test_two_sponsor_pattern_splits_and_disables_parent(db):
    matcher = TextPatternMatcher(db=db)
    pattern_id = _make_pattern(db, TWO_SPONSOR_TEXT, scope='podcast',
                                podcast_id='show-a', network_id='net-1')
    new_ids = matcher.split_pattern(pattern_id)

    assert len(new_ids) == 2
    parent = db.get_ad_pattern_by_id(pattern_id)
    assert parent['is_active'] == 0
    assert parent['disabled_reason'].startswith('Split into patterns:')

    children = [db.get_ad_pattern_by_id(i) for i in new_ids]
    sponsors = {c['sponsor'] for c in children}
    assert sponsors == {'Acme', 'Widgetco'}
    for c in children:
        # Inherits scope/podcast/network/source_language from parent.
        assert c['scope'] == 'podcast'
        assert c['podcast_id'] == 'show-a'
        assert c['network_id'] == 'net-1'
        assert c['source_language'] == 'en'


def test_overlap_dedupe_fix_preserves_full_leading_phrase(db):
    # "brought to you by" nests inside "this episode is brought to you by":
    # pre-fix, these registered as two adjacent split points 17 chars apart,
    # slicing the Acme segment's text to start mid-phrase at "brought to you
    # by Acme..." (losing "this episode is") while dropping a spurious
    # 17-char fragment. The fix dedupes them to one split point so the Acme
    # segment keeps the phrase (and the preceding filler) intact.
    filler = "word " * 20
    text = (
        f"{filler}this episode is brought to you by Acme, your favorite "
        f"widget maker with the best prices around town this month. "
        f"Thanks to Widgetco for also supporting the show this week with "
        f"their new gadget line available everywhere online right now."
    )
    matcher = TextPatternMatcher(db=db)
    pattern_id = _make_pattern(db, text)
    new_ids = matcher.split_pattern(pattern_id)

    assert len(new_ids) == 2
    children = {db.get_ad_pattern_by_id(i)['sponsor']: db.get_ad_pattern_by_id(i)
                for i in new_ids}
    assert 'this episode is brought to you by Acme' in children['Acme']['text_template']


def test_no_db_returns_empty(db):
    matcher = TextPatternMatcher(db=None)
    assert matcher.split_pattern(1) == []


def test_missing_pattern_returns_empty(db):
    matcher = TextPatternMatcher(db=db)
    assert matcher.split_pattern(999999) == []
