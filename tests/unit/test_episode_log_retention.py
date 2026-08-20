"""Retention sweep for episode run logs (#660)."""
import os
import time

import pytest

from tests.app_bootstrap import bootstrap

_data_dir = bootstrap('episode_log_retention_test_')

from database import Database  # noqa: E402
from run_log import episode_log_root, run_log_temp_dir, sweep_expired_logs  # noqa: E402

DAY = 86400


@pytest.fixture
def env(tmp_path):
    Database._instance = None
    db = Database(data_dir=str(tmp_path))
    db.create_podcast('log-feed', 'https://example.com/feed.xml', 'Log Feed')
    podcast = db.get_podcast_by_slug('log-feed')
    yield {'db': db, 'data_dir': tmp_path, 'podcast': podcast}
    Database._instance = None


def _write_log(data_dir, slug, episode_id, history_id, age_days=0):
    path = (episode_log_root(data_dir) / slug / episode_id
            / f"run-{history_id}.jsonl")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('{"ts": "2026-08-20T00:00:00.000Z", "level": "INFO", '
                    '"logger": "podcast.audio", "msg": "hi"}\n')
    if age_days:
        old = time.time() - age_days * DAY
        os.utime(path, (old, old))
    return path


def _seed_run(env, episode_id='ep1', age_days=0):
    db, data_dir = env['db'], env['data_dir']
    history_id = db.record_processing_history(
        podcast_id=env['podcast']['id'], podcast_slug='log-feed',
        podcast_title='Log Feed', episode_id=episode_id,
        episode_title='One', status='completed')
    path = _write_log(data_dir, 'log-feed', episode_id, history_id, age_days)
    db.set_history_log_pointer(
        history_id, f"logs/episodes/log-feed/{episode_id}/run-{history_id}.jsonl")
    return history_id, path


def _row(env, history_id):
    return env['db'].get_connection().execute(
        'SELECT log_file FROM processing_history WHERE id = ?',
        (history_id,)).fetchone()


class TestSweep:
    def test_an_expired_log_is_deleted_and_its_pointer_cleared(self, env):
        history_id, path = _seed_run(env, age_days=40)

        assert sweep_expired_logs(env['db'], env['data_dir'], 30) == (1, 0)

        assert not path.exists()
        row = _row(env, history_id)
        assert row['log_file'] is None

    def test_a_fresh_log_is_kept(self, env):
        history_id, path = _seed_run(env, age_days=2)

        assert sweep_expired_logs(env['db'], env['data_dir'], 30) == (0, 0)

        assert path.exists()
        assert _row(env, history_id)['log_file']

    def test_an_orphan_file_is_removed(self, env):
        orphan = _write_log(env['data_dir'], 'log-feed', 'gone', 99, age_days=45)

        assert sweep_expired_logs(env['db'], env['data_dir'], 30) == (0, 1)

        assert not orphan.exists()

    def test_a_fresh_orphan_is_kept(self, env):
        orphan = _write_log(env['data_dir'], 'log-feed', 'gone', 99)

        assert sweep_expired_logs(env['db'], env['data_dir'], 30) == (0, 0)

        assert orphan.exists()

    def test_retention_zero_removes_everything(self, env):
        history_id, path = _seed_run(env)
        orphan = _write_log(env['data_dir'], 'log-feed', 'gone', 99)

        assert sweep_expired_logs(env['db'], env['data_dir'], 0) == (1, 1)

        assert not path.exists()
        assert not orphan.exists()
        assert _row(env, history_id)['log_file'] is None

    def test_stale_temp_files_are_removed(self, env):
        temp_dir = run_log_temp_dir(env['data_dir'])
        temp_dir.mkdir(parents=True, exist_ok=True)
        stale = temp_dir / 'run-log-feed-ep1-1.jsonl.tmp'
        stale.write_text('{}\n')
        old = time.time() - 40 * DAY
        os.utime(stale, (old, old))
        fresh = temp_dir / 'run-log-feed-ep2-2.jsonl.tmp'
        fresh.write_text('{}\n')

        assert sweep_expired_logs(env['db'], env['data_dir'], 30) == (0, 1)

        assert not stale.exists()
        assert fresh.exists()

    def test_empty_episode_directories_are_cleaned_up(self, env):
        _, path = _seed_run(env, age_days=40)

        sweep_expired_logs(env['db'], env['data_dir'], 30)

        assert not path.parent.exists()
        assert not path.parent.parent.exists()

    def test_a_missing_log_root_is_a_no_op(self, tmp_path):
        Database._instance = None
        db = Database(data_dir=str(tmp_path))
        try:
            assert sweep_expired_logs(db, tmp_path / 'nowhere', 30) == (0, 0)
        finally:
            Database._instance = None

    def test_a_failing_delete_never_raises(self, env, monkeypatch):
        _seed_run(env, age_days=40)

        def boom(self, **kwargs):
            raise OSError('read-only file system')

        monkeypatch.setattr('pathlib.Path.unlink', boom)

        assert sweep_expired_logs(env['db'], env['data_dir'], 30) == (0, 0)

    def test_a_failing_pointer_query_deletes_nothing(self, env, monkeypatch):
        _, path = _seed_run(env, age_days=40)
        monkeypatch.setattr(type(env['db']), 'get_history_log_pointers',
                            lambda self: (_ for _ in ()).throw(RuntimeError('db down')))

        assert sweep_expired_logs(env['db'], env['data_dir'], 30) == (0, 0)

        # Without the pointer map every file looks like an orphan; deleting on
        # that guess would take live logs with it.
        assert path.exists()

    def test_a_pointer_whose_file_vanished_is_cleared(self, env):
        history_id, path = _seed_run(env)
        path.unlink()

        assert sweep_expired_logs(env['db'], env['data_dir'], 30) == (1, 0)

        assert _row(env, history_id)['log_file'] is None

    def test_a_fresh_temp_file_survives_retention_zero(self, env):
        temp_dir = run_log_temp_dir(env['data_dir'])
        temp_dir.mkdir(parents=True, exist_ok=True)
        live = temp_dir / 'run-log-feed-ep1-live.jsonl.tmp'
        live.write_text('{}\n')

        assert sweep_expired_logs(env['db'], env['data_dir'], 0) == (0, 0)

        # A run in flight owns this file; retention 0 must not unlink it.
        assert live.exists()


class TestCleanupWiring:
    @pytest.fixture
    def cleanup(self, monkeypatch):
        import main_app.background as background

        calls = []
        monkeypatch.setattr(background.run_log, 'sweep_expired_logs',
                            lambda db, data_dir, days: calls.append(days) or (0, 0))
        monkeypatch.setattr(background.db, 'cleanup_old_episodes',
                            lambda storage=None: (0, 0.0))
        monkeypatch.setattr(background.db, 'get_all_podcasts', lambda: [])
        if hasattr(background.run_cleanup, '_last_run_log_sweep'):
            del background.run_cleanup._last_run_log_sweep
        yield background, calls
        if hasattr(background.run_cleanup, '_last_run_log_sweep'):
            del background.run_cleanup._last_run_log_sweep

    def test_run_cleanup_sweeps_run_logs(self, cleanup):
        background, calls = cleanup
        background.run_cleanup()

        assert calls == [30]

    def test_the_sweep_is_rate_limited_to_hourly(self, cleanup):
        background, calls = cleanup
        background.run_cleanup()
        background.run_cleanup()

        # The cleanup tick runs every refresh interval; walking the log tree
        # that often buys nothing.
        assert calls == [30]

    def test_an_hour_later_it_sweeps_again(self, cleanup):
        background, calls = cleanup
        background.run_cleanup()
        background.run_cleanup._last_run_log_sweep -= 3601
        background.run_cleanup()

        assert calls == [30, 30]
