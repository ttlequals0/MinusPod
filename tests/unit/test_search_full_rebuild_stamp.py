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
    yield
    bg._INDEX_STATE.update(previous)


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


def test_recording_a_full_rebuild_persists_the_time():
    db = database.Database()
    bg._record_full_rebuild(db, 1234567.0)
    assert db.get_system_setting(KEY) == '1234567.0'


def _cleanup(monkeypatch, stored):
    db = MagicMock()
    db.cleanup_old_episodes.return_value = (0, 0.0)
    db.get_all_podcasts.return_value = []
    db.get_system_setting.return_value = stored
    monkeypatch.setattr(bg, 'db', db)
    monkeypatch.setattr(bg, 'rebuild_recents_feed', lambda: None)
    bg._INDEX_STATE['last_refresh'] = 0
    bg._INDEX_STATE['last_full'] = 0
    queue = MagicMock()
    queue.slot_count.return_value = 0
    with patch('processing_queue.ProcessingQueue', return_value=queue):
        bg.run_cleanup()
    return db


def test_restart_with_a_recent_stamp_refreshes_instead_of_rebuilding(monkeypatch):
    db = _cleanup(monkeypatch, str(time.time() - 3600))
    db.rebuild_search_index.assert_not_called()
    db.refresh_search_index.assert_called_once()


def test_restart_with_an_old_stamp_runs_the_full_rebuild_and_persists_it(monkeypatch):
    old = time.time() - bg.INDEX_FULL_REBUILD_INTERVAL_SECONDS - 60
    db = _cleanup(monkeypatch, str(old))
    db.rebuild_search_index.assert_called_once()
    stamp = float(db.set_system_setting.call_args.args[1])
    assert db.set_system_setting.call_args.args[0] == KEY
    assert stamp == pytest.approx(time.time(), abs=5)


def test_restart_with_no_stamp_runs_the_full_rebuild(monkeypatch):
    db = _cleanup(monkeypatch, None)
    db.rebuild_search_index.assert_called_once()


def test_a_failed_stamp_write_does_not_fail_the_rebuild(monkeypatch):
    db = MagicMock()
    db.set_system_setting.side_effect = RuntimeError('database is locked')
    bg._record_full_rebuild(db, 1.0)
