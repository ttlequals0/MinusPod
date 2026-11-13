import time
from unittest.mock import MagicMock, patch

import pytest

from tests.app_bootstrap import bootstrap

bootstrap('search_full_stamp_')

import database  # noqa: E402
import main_app.background as bg  # noqa: E402

KEY = 'search_index_last_full_rebuild'


@pytest.fixture(autouse=True)
def _restore_state():
    previous = dict(bg._INDEX_STATE)
    stored = database.Database().get_system_setting(KEY)
    yield
    bg._INDEX_STATE.update(previous)
    conn = database.Database().get_connection()
    if stored is None:
        conn.execute("DELETE FROM system_settings WHERE key = ?", (KEY,))
        conn.commit()
    else:
        database.Database().set_system_setting(KEY, stored)


def test_full_rebuild_stamp_round_trips():
    db = database.Database()
    now = time.time()
    db.set_system_setting(KEY, str(now))
    assert bg._load_last_full_rebuild(db) == pytest.approx(now, abs=1)


@pytest.mark.parametrize('stored', ['', 'not-a-number'])
def test_missing_or_garbled_stamp_reads_as_zero(stored):
    db = database.Database()
    db.set_system_setting(KEY, stored)
    assert bg._load_last_full_rebuild(db) == 0.0


def test_any_full_rebuild_persists_the_time():
    db = database.Database()
    db.set_system_setting(KEY, '')
    before = time.time()
    db.rebuild_search_index()
    assert float(db.get_system_setting(KEY)) >= before - 1


def test_a_failed_stamp_write_does_not_fail_the_rebuild():
    db = database.Database()
    with patch.object(db, 'set_system_setting', side_effect=RuntimeError('database is locked')):
        assert db.rebuild_search_index() >= 0


def _cleanup(monkeypatch, stored, cached=0, read_error=None):
    db = MagicMock()
    db.cleanup_old_episodes.return_value = (0, 0.0)
    db.get_all_podcast_rows.return_value = []
    db.get_system_setting.return_value = stored
    db.get_system_setting.side_effect = read_error
    monkeypatch.setattr(bg, 'db', db)
    monkeypatch.setattr(bg.recents_feed, 'rebuild_recents_feed', lambda: None)
    bg._INDEX_STATE['last_refresh'] = 0
    bg._INDEX_STATE['last_full'] = cached
    queue = MagicMock()
    queue.slot_count.return_value = 0
    with patch('processing_queue.ProcessingQueue', return_value=queue):
        bg.run_cleanup()
    return db


def test_restart_with_a_recent_stamp_refreshes_instead_of_rebuilding(monkeypatch):
    db = _cleanup(monkeypatch, str(time.time() - 3600))
    db.rebuild_search_index.assert_not_called()
    db.refresh_search_index.assert_called_once()


def test_restart_with_an_old_stamp_runs_the_full_rebuild(monkeypatch):
    old = time.time() - bg.INDEX_FULL_REBUILD_INTERVAL_SECONDS - 60
    db = _cleanup(monkeypatch, str(old))
    db.rebuild_search_index.assert_called_once()
    assert bg._INDEX_STATE['last_full'] == pytest.approx(time.time(), abs=5)


def test_restart_with_no_stamp_runs_the_full_rebuild(monkeypatch):
    db = _cleanup(monkeypatch, None)
    db.rebuild_search_index.assert_called_once()


def test_a_stamp_written_after_the_cache_was_set_defers_the_full_rebuild(monkeypatch):
    stale = time.time() - bg.INDEX_FULL_REBUILD_INTERVAL_SECONDS - 60
    db = _cleanup(monkeypatch, str(time.time() - 60), cached=stale)
    db.rebuild_search_index.assert_not_called()
    db.refresh_search_index.assert_called_once()


def test_a_failed_stamp_read_falls_back_to_the_cached_value(monkeypatch):
    db = _cleanup(monkeypatch, None, cached=time.time() - 60,
                  read_error=RuntimeError('database is locked'))
    db.rebuild_search_index.assert_not_called()


def test_a_future_stored_stamp_does_not_suppress_the_full_rebuild(monkeypatch):
    db = _cleanup(monkeypatch, str(time.time() + 10 * bg.INDEX_FULL_REBUILD_INTERVAL_SECONDS))
    db.rebuild_search_index.assert_called_once()


def test_a_future_cached_stamp_does_not_suppress_the_full_rebuild(monkeypatch):
    db = _cleanup(monkeypatch, None, cached=time.time() + 10 * bg.INDEX_FULL_REBUILD_INTERVAL_SECONDS)
    db.rebuild_search_index.assert_called_once()
