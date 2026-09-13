"""Tests for the per-run route snapshot on RunContext and its persisted column."""
from tests.app_bootstrap import bootstrap

bootstrap('run_route_snapshot_test_')

import run_context
from database import Database


def test_route_snapshot_defaults_none_and_sets():
    ctx = run_context.RunContext('example-podcast', 'a1b2c3d4e5f6', run_id='r1')
    assert ctx.route_snapshot is None
    snap = {'detection': {'provider_key': 'anthropic', 'configured_model': 'claude-x'},
            'review': {'provider_key': 'ollama', 'configured_model': 'local-y'}}
    ctx.set_route_snapshot(snap)
    assert ctx.route_snapshot == snap


def test_route_snapshot_rejects_secret_keys():
    ctx = run_context.RunContext('example-podcast', 'a1b2c3d4e5f6', run_id='r1')
    import pytest
    with pytest.raises(ValueError):
        ctx.set_route_snapshot({'detection': {'api_key': 'sk-secret'}})


def test_processing_runs_route_snapshot_column_on_fresh_db(tmp_path):
    Database._instance = None
    db = Database(data_dir=str(tmp_path))
    try:
        cols = {r['name'] for r in
                db.get_connection().execute("PRAGMA table_info(processing_runs)")}
        assert 'route_snapshot_json' in cols
    finally:
        Database._instance = None


def test_processing_runs_route_snapshot_column_on_existing_db(tmp_path):
    Database._instance = None
    db = Database(data_dir=str(tmp_path))
    try:
        conn = db.get_connection()
        conn.execute("ALTER TABLE processing_runs RENAME TO processing_runs_old")
        conn.execute("""CREATE TABLE processing_runs (
            run_id TEXT PRIMARY KEY,
            podcast_id INTEGER NOT NULL REFERENCES podcasts(id) ON DELETE CASCADE,
            episode_id TEXT NOT NULL,
            owner_pid INTEGER NOT NULL,
            owner_pid_start REAL,
            state TEXT NOT NULL CHECK(state IN ('running', 'cancel_requested', 'finished', 'interrupted')),
            cancel_requested_at TEXT,
            started_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
            heartbeat_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
            finished_at TEXT
        )""")
        conn.execute("DROP TABLE processing_runs_old")
        conn.commit()
        cols = {r['name'] for r in conn.execute("PRAGMA table_info(processing_runs)")}
        assert 'route_snapshot_json' not in cols

        db._run_schema_migrations()

        cols = {r['name'] for r in conn.execute("PRAGMA table_info(processing_runs)")}
        assert 'route_snapshot_json' in cols
    finally:
        Database._instance = None
