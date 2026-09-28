"""Tests for disabled_at stamping in _update_ad_pattern_conn and pattern merges."""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))

from pattern_service import PatternService

_EXPLICIT = '2021-05-06T07:08:09Z'


@pytest.fixture
def db(temp_db):
    return temp_db


def _pattern(db, text, sponsor_id=None):
    return db.create_ad_pattern(
        scope='podcast', text_template=text, sponsor_id=sponsor_id,
        intro_variants=[], outro_variants=[], source_language=None,
    )


def test_update_ad_pattern_explicit_disabled_at_wins(db):
    pid = _pattern(db, 'Acme has a special offer for listeners. Visit acme.com.')

    db.update_ad_pattern(pid, is_active=False, disabled_at=_EXPLICIT)

    assert db.get_ad_pattern_by_id(pid)['disabled_at'] == _EXPLICIT


def test_merge_disables_with_timestamp(db):
    sponsor_id = db.create_known_sponsor(name='Acme', aliases=[], category=None)
    p1 = _pattern(db, 'Acme has a special offer for listeners. Visit acme.com.', sponsor_id)
    p2 = _pattern(db, 'Try Acme every morning. Get a free welcome kit at acme.com.', sponsor_id)

    merged_id = PatternService(db=db).merge_similar_patterns([p1, p2])

    assert merged_id is not None
    for pid in (p1, p2):
        row = db.get_ad_pattern_by_id(pid)
        assert not row['is_active']
        assert row['disabled_at']


def test_reactivation_clears_disabled_reason_unless_supplied(db):
    pid = _pattern(db, 'Acme has a special offer for listeners. Visit acme.com.')
    db.update_ad_pattern(pid, is_active=False, disabled_reason='too broad')

    db.update_ad_pattern(pid, is_active=True)
    row = db.get_ad_pattern_by_id(pid)
    assert row['disabled_at'] is None and row['disabled_reason'] is None

    db.update_ad_pattern(pid, is_active=False, disabled_reason='too broad')
    db.update_ad_pattern(pid, is_active=True, disabled_reason='kept note')
    assert db.get_ad_pattern_by_id(pid)['disabled_reason'] == 'kept note'
