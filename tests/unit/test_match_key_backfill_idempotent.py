"""Regression test: model_pricing match_key backfill must be collision-safe.

A NULL-match_key row whose normalized key already belongs to another row used to
abort the whole migration with 'UNIQUE constraint failed: model_pricing.match_key'
(the per-row UPDATE hit the existing UNIQUE index before the dedup could run),
leaving the row NULL so the warning re-fired on every restart. The backfill now
skips such redundant duplicates instead of failing.
"""
import logging
import shutil
import tempfile

import pytest

from config import normalize_model_key
from database import Database


@pytest.fixture
def fresh_db_dir():
    d = tempfile.mkdtemp(prefix="minuspod_matchkey_test_")
    Database._instance = None
    yield d
    Database._instance = None
    shutil.rmtree(d, ignore_errors=True)


def _insert_pricing(conn, model_id, match_key, source='live'):
    conn.execute(
        """INSERT INTO model_pricing
               (model_id, match_key, raw_model_id, display_name,
                input_cost_per_mtok, output_cost_per_mtok, source)
           VALUES (?, ?, ?, ?, 1.0, 2.0, ?)""",
        (model_id, match_key, model_id, model_id, source),
    )


def test_backfill_skips_colliding_null_row(fresh_db_dir, caplog):
    db = Database(fresh_db_dir)
    conn = db.get_connection()

    keyed_id = 'vendor/test-collide-x'
    key = normalize_model_key(keyed_id)            # 'testcollidex'
    null_id = 'test-collide-x'
    assert normalize_model_key(null_id) == key     # guaranteed collision

    _insert_pricing(conn, keyed_id, key)           # owns the match_key
    _insert_pricing(conn, null_id, None, source='legacy')  # redundant NULL dup
    conn.commit()

    with caplog.at_level(logging.WARNING):
        db._run_schema_migrations()                # must not raise, must not warn

    assert "Migration failed for match_key backfill" not in caplog.text

    # keyed row keeps its key; the colliding NULL row is left NULL (not deleted)
    assert conn.execute(
        "SELECT match_key FROM model_pricing WHERE model_id = ?", (keyed_id,)
    ).fetchone()['match_key'] == key
    null_row = conn.execute(
        "SELECT match_key FROM model_pricing WHERE model_id = ?", (null_id,)
    ).fetchone()
    assert null_row is not None and null_row['match_key'] is None

    # idempotent: a second run is also clean
    caplog.clear()
    with caplog.at_level(logging.WARNING):
        db._run_schema_migrations()
    assert "Migration failed for match_key backfill" not in caplog.text


def test_backfill_intrabatch_collision_keeps_one(fresh_db_dir, caplog):
    # Two NULL rows in the same batch that normalize to the same NEW key: the
    # first UPDATE must be visible to the second row's collision check (same
    # connection, read-your-own-writes) so the second is skipped, not thrown.
    db = Database(fresh_db_dir)
    conn = db.get_connection()

    a, b = 'intra/dup-model', 'dup-model'
    key = normalize_model_key(a)
    assert normalize_model_key(b) == key
    assert conn.execute(
        "SELECT 1 FROM model_pricing WHERE match_key = ?", (key,)
    ).fetchone() is None                           # key starts free

    _insert_pricing(conn, a, None, source='legacy')
    _insert_pricing(conn, b, None, source='legacy')
    conn.commit()

    with caplog.at_level(logging.WARNING):
        db._run_schema_migrations()
    assert "Migration failed for match_key backfill" not in caplog.text

    keyed = conn.execute(
        "SELECT COUNT(*) c FROM model_pricing WHERE match_key = ?", (key,)
    ).fetchone()['c']
    assert keyed == 1                              # exactly one row owns the key
    null_left = conn.execute(
        "SELECT COUNT(*) c FROM model_pricing WHERE model_id IN (?, ?) AND match_key IS NULL",
        (a, b),
    ).fetchone()['c']
    assert null_left == 1                          # the other stays NULL, not deleted


def test_backfill_fills_noncolliding_null_row(fresh_db_dir):
    db = Database(fresh_db_dir)
    conn = db.get_connection()

    null_id = 'test-unique-model-z'
    expected = normalize_model_key(null_id)
    assert conn.execute(
        "SELECT 1 FROM model_pricing WHERE match_key = ?", (expected,)
    ).fetchone() is None                           # key is free

    _insert_pricing(conn, null_id, None, source='legacy')
    conn.commit()

    db._run_schema_migrations()

    assert conn.execute(
        "SELECT match_key FROM model_pricing WHERE model_id = ?", (null_id,)
    ).fetchone()['match_key'] == expected
