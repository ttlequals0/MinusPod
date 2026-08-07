"""Tests for near-duplicate dedupe in create_pattern_from_ad."""
from tests.app_bootstrap import bootstrap
bootstrap('pattern_learning_dedupe_test_')

from unittest.mock import MagicMock

from text_pattern_matcher import TextPatternMatcher

TEXT = ("theres a reason millions of people start their day with business "
        "news from our friends because it makes you smarter in fifteen minutes")
SEGMENTS = [{'start': 100.0, 'end': 130.0, 'text': TEXT}]


def _matcher_with_existing(existing):
    m = TextPatternMatcher(db=MagicMock())
    m.db.get_ad_patterns.return_value = [existing]
    return m


def test_near_identical_text_updates_instead_of_inserting():
    existing = {'id': 622, 'created_by': 'user', 'source': 'local',
                'category': None, 'intro_text': TEXT[:80],
                'text_template': TEXT, 'active': 1}
    m = _matcher_with_existing(existing)
    pid = m.create_pattern_from_ad(SEGMENTS, 100.0, 130.0, sponsor='Acme',
                                    podcast_id='example-podcast',
                                    category='cross_promo')
    assert pid == 622
    m.db.create_ad_pattern.assert_not_called()
    m.db.increment_pattern_match.assert_called_once_with(622)


def test_dedupe_path_never_writes_category():
    existing = {'id': 622, 'created_by': 'user', 'source': 'local',
                'category': None, 'intro_text': TEXT[:80],
                'text_template': TEXT, 'active': 1}
    m = _matcher_with_existing(existing)
    m.create_pattern_from_ad(SEGMENTS, 100.0, 130.0, sponsor='Acme',
                              podcast_id='example-podcast', category='cross_promo')
    m.db.create_ad_pattern.assert_not_called()
    assert not any('category' in (c.kwargs or {}) for c in m.db.method_calls), \
        "dedupe must not touch the existing pattern's category"
