"""DB-layer tests for the sponsors management feature (issue #304):
pattern-stats enrichment and hard delete with pattern unlink.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))

from database import Database


@pytest.fixture
def db(tmp_path):
    Database._instance = None
    if hasattr(Database, '_initialized'):
        Database._initialized = False
    instance = Database(data_dir=str(tmp_path))
    yield instance
    Database._instance = None


def _add_pattern(db, sponsor_id, last_matched_at=None, is_active=True):
    pid = db.create_ad_pattern(scope='podcast', text_template='buy now', sponsor_id=sponsor_id)
    conn = db.get_connection()
    conn.execute(
        "UPDATE ad_patterns SET last_matched_at = ?, is_active = ? WHERE id = ?",
        (last_matched_at, 1 if is_active else 0, pid),
    )
    conn.commit()
    return pid


def test_pattern_stats_counts_and_max_timestamp(db):
    a = db.create_known_sponsor('Acme')
    b = db.create_known_sponsor('Brand')
    _add_pattern(db, a, '2026-01-01T00:00:00Z')
    _add_pattern(db, a, '2026-03-01T00:00:00Z')
    _add_pattern(db, b, '2026-02-01T00:00:00Z')

    stats = db.get_sponsor_pattern_stats()
    assert stats[a]['pattern_count'] == 2
    assert stats[a]['last_matched_at'] == '2026-03-01T00:00:00Z'
    assert stats[b]['pattern_count'] == 1


def test_pattern_stats_excludes_inactive_patterns(db):
    a = db.create_known_sponsor('Acme')
    _add_pattern(db, a, '2026-01-01T00:00:00Z', is_active=False)

    stats = db.get_sponsor_pattern_stats()
    assert a not in stats  # no active patterns -> absent (API defaults to 0/null)


def test_pattern_stats_sponsor_without_patterns_absent(db):
    a = db.create_known_sponsor('Acme')
    assert a not in db.get_sponsor_pattern_stats()


def test_pattern_stats_by_id(db):
    a = db.create_known_sponsor('Acme')
    _add_pattern(db, a, '2026-01-01T00:00:00Z')
    _add_pattern(db, a, '2026-03-01T00:00:00Z')
    _add_pattern(db, a, '2026-02-01T00:00:00Z', is_active=False)

    stats = db.get_sponsor_pattern_stats_by_id(a)
    assert stats['pattern_count'] == 2  # inactive excluded
    assert stats['last_matched_at'] == '2026-03-01T00:00:00Z'


def test_pattern_stats_by_id_no_patterns(db):
    a = db.create_known_sponsor('Acme')
    stats = db.get_sponsor_pattern_stats_by_id(a)
    assert stats == {'pattern_count': 0, 'last_matched_at': None}


def test_hard_delete_removes_row_and_unlinks_patterns(db):
    a = db.create_known_sponsor('Typo Sponsor')
    p1 = _add_pattern(db, a, '2026-01-01T00:00:00Z')
    p2 = _add_pattern(db, a, '2026-02-01T00:00:00Z')

    deleted, unlinked = db.hard_delete_known_sponsor(a)
    assert deleted is True
    assert unlinked == 2

    # Sponsor row is actually gone, not just inactive.
    assert db.get_known_sponsor_by_id(a) is None

    # Pattern rows survive, just unlinked.
    conn = db.get_connection()
    rows = conn.execute(
        "SELECT id, sponsor_id FROM ad_patterns WHERE id IN (?, ?)", (p1, p2)
    ).fetchall()
    assert len(rows) == 2
    assert all(r['sponsor_id'] is None for r in rows)


def test_hard_delete_unknown_id_is_noop(db):
    deleted, unlinked = db.hard_delete_known_sponsor(99999)
    assert deleted is False
    assert unlinked == 0


def test_hard_delete_sponsor_without_patterns(db):
    a = db.create_known_sponsor('Lonely')
    deleted, unlinked = db.hard_delete_known_sponsor(a)
    assert deleted is True
    assert unlinked == 0
    assert db.get_known_sponsor_by_id(a) is None
