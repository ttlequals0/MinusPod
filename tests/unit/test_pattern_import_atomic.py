"""Regression test for atomic replace-mode pattern import (api-settings-patterns-1).

The bug: in replace mode the import deleted every existing pattern first, and the
delete/create DB helpers committed internally, so the route's outer rollback had
nothing to undo. A failure mid-import permanently wiped the whole ad_patterns
table. The fix routes every write through non-committing primitives inside one
transaction, so a mid-import failure rolls back and pre-existing patterns survive.
"""
import pytest

from api.patterns import _apply_pattern_imports


def _seed(db, n):
    for i in range(n):
        db.create_ad_pattern(scope='global', text_template=f'existing pattern {i}')


def _run_import_like_route(db, valid_patterns, mode):
    """Mirror import_patterns()'s transaction handling: one BEGIN IMMEDIATE,
    rollback on any exception."""
    conn = db.get_connection()
    try:
        conn.execute('BEGIN IMMEDIATE')
        result = _apply_pattern_imports(db, conn, valid_patterns, mode)
        conn.commit()
        return result
    except Exception:
        conn.rollback()
        raise


def test_replace_mode_rolls_back_on_midimport_failure(temp_db):
    db = temp_db
    _seed(db, 3)
    assert len(db.get_ad_patterns(active_only=False)) == 3

    payload = [
        {'scope': 'global', 'text_template': 'new one', '_sponsor_id': None},
        {'scope': 'global', 'text_template': 'boom', '_sponsor_id': None},
    ]

    real_create = db._create_ad_pattern_conn
    calls = {'n': 0}

    def failing_create(conn, *args, **kwargs):
        calls['n'] += 1
        if calls['n'] == 2:
            raise RuntimeError('simulated mid-import failure')
        return real_create(conn, *args, **kwargs)

    db._create_ad_pattern_conn = failing_create

    with pytest.raises(RuntimeError):
        _run_import_like_route(db, payload, 'replace')

    survivors = db.get_ad_patterns(active_only=False)
    assert len(survivors) == 3
    assert {p['text_template'] for p in survivors} == {
        'existing pattern 0', 'existing pattern 1', 'existing pattern 2'
    }


def test_replace_mode_commits_on_success(temp_db):
    db = temp_db
    _seed(db, 3)
    payload = [{'scope': 'global', 'text_template': 'kept', '_sponsor_id': None}]

    imported, updated, skipped = _run_import_like_route(db, payload, 'replace')

    assert imported == 1
    remaining = db.get_ad_patterns(active_only=False)
    assert len(remaining) == 1
    assert remaining[0]['text_template'] == 'kept'
