"""Startup processing-state reconciliation clears stale status file entries.

Covers:
- current_job present -> cleared + episode DB status reset to pending
- queued_episodes present -> dropped
- corrupt status file -> treated as empty + warning logged
- missing status file -> no-op (no crash)

Tests import reconcile_startup_state directly from status_service so they
do not trigger Flask app initialisation (main_app/__init__.py runs _startup()
at import time and requires /app/data).
"""
import json
import os
import time

import pytest

from status_service import reconcile_startup_state


@pytest.fixture
def reconcile_env(temp_dir, temp_db, monkeypatch):
    """Fresh StatusService bound to temp_dir."""
    import status_service as ss_mod
    ss_mod.StatusService._instance = None
    monkeypatch.setattr(ss_mod, 'STATUS_FILE', os.path.join(temp_dir, 'processing_status.json'))
    monkeypatch.setattr(ss_mod, '_get_soft_timeout', lambda: 3600)

    ss = ss_mod.StatusService()

    yield ss, temp_db, os.path.join(temp_dir, 'processing_status.json')

    ss_mod.StatusService._instance = None


def _seed(db, slug, episode_id, status='processing'):
    db.create_podcast(slug, f'https://example.com/{slug}.xml', slug)
    db.upsert_episode(slug, episode_id, original_url='https://example.com/ep.mp3',
                      title='Test', status=status)


class TestReconcileStartupState:
    def test_stale_current_job_is_cleared_from_status_file(self, reconcile_env):
        ss, db, status_file = reconcile_env
        _seed(db, 'pod', 'ep1')
        ss.start_job('pod', 'ep1', 'Title', 'Pod')
        assert ss.get_status().current_job is not None

        reconcile_startup_state(db)

        assert ss.get_status().current_job is None

    def test_stale_current_job_resets_episode_db_status_to_pending(self, reconcile_env):
        ss, db, status_file = reconcile_env
        _seed(db, 'pod', 'ep1')
        ss.start_job('pod', 'ep1', 'Title', 'Pod')

        reconcile_startup_state(db)

        assert db.get_episode('pod', 'ep1')['status'] == 'pending'

    def test_stale_current_job_sets_error_message(self, reconcile_env):
        ss, db, status_file = reconcile_env
        _seed(db, 'pod', 'ep1')
        ss.start_job('pod', 'ep1', 'Title', 'Pod')

        reconcile_startup_state(db)

        ep = db.get_episode('pod', 'ep1')
        assert 'restart' in (ep.get('error_message') or '').lower()

    def test_stale_queued_episodes_are_dropped(self, reconcile_env):
        ss, db, status_file = reconcile_env
        ss.queue_episode('pod', 'ep1', 'Title', 'Pod')
        ss.queue_episode('pod', 'ep2', 'Title2', 'Pod')
        assert ss.get_status().queue_length == 2

        reconcile_startup_state(db)

        assert ss.get_status().queue_length == 0

    def test_no_episode_in_db_does_not_crash(self, reconcile_env):
        """current_job referencing an unknown episode must not raise."""
        ss, db, status_file = reconcile_env
        with ss._status_transaction():
            raw = ss._read_status_file()
            raw['current_job'] = {
                'slug': 'ghost', 'episode_id': 'ep-x',
                'title': 'Gone', 'podcast_name': 'Ghost',
                'started_at': time.time(), 'stage': 'downloading', 'progress': 0.0,
            }
            ss._write_status_file(raw)

        reconcile_startup_state(db)  # must not raise

        assert ss.get_status().current_job is None

    def test_missing_status_file_is_noop(self, reconcile_env):
        ss, db, status_file = reconcile_env
        assert not os.path.exists(status_file)

        reconcile_startup_state(db)  # must not raise

        assert ss.get_status().current_job is None

    def test_no_stale_state_writes_nothing_extra(self, reconcile_env):
        """When status file is absent reconcile must not create it with dirty state."""
        ss, db, status_file = reconcile_env

        reconcile_startup_state(db)

        if os.path.exists(status_file):
            with open(status_file) as f:
                data = json.load(f)
            assert data.get('current_job') is None

    def test_does_not_reset_non_processing_episode(self, reconcile_env):
        """UPDATE WHERE status='processing' must be a no-op for a processed episode."""
        ss, db, status_file = reconcile_env
        _seed(db, 'pod', 'ep1', status='processed')
        with ss._status_transaction():
            raw = ss._read_status_file()
            raw['current_job'] = {
                'slug': 'pod', 'episode_id': 'ep1',
                'title': 'T', 'podcast_name': 'P',
                'started_at': time.time(), 'stage': 'transcribing', 'progress': 50.0,
            }
            ss._write_status_file(raw)

        reconcile_startup_state(db)

        assert db.get_episode('pod', 'ep1')['status'] == 'processed'


class TestCorruptStatusFile:
    def test_corrupt_json_returns_empty_and_logs_warning(self, reconcile_env, caplog):
        ss, db, status_file = reconcile_env
        with open(status_file, 'w') as f:
            f.write('{not valid json}}')

        import logging
        with caplog.at_level(logging.WARNING, logger='podcast.status'):
            result = ss.get_status()

        assert result.current_job is None
        assert result.queue_length == 0
        assert any('corrupt' in r.message.lower() for r in caplog.records)

    def test_corrupt_json_is_rewritten_clean(self, reconcile_env):
        ss, db, status_file = reconcile_env
        with open(status_file, 'w') as f:
            f.write('{{{{bad}}}')

        ss.get_status()

        with open(status_file) as f:
            data = json.load(f)  # must be valid JSON now
        assert data.get('current_job') is None
