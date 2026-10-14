"""Background loop pacing: a shared RSS outage backs off across passes instead
of re-arming the same short retry, and a failed search-index rebuild waits out
a bounded cooldown rather than retrying every cleanup pass.
"""
import time
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

from tests.app_bootstrap import bootstrap

_test_data_dir = bootstrap('background_maintenance_test_')
import main_app
from main_app import background
from utils.time import parse_iso_utc


class _FakeEvent:
    """shutdown_event stand-in that ends the loop after `passes` waits."""

    def __init__(self, passes):
        self.passes = passes
        self.waits: list[float] = []

    def is_set(self):
        return len(self.waits) >= self.passes

    def wait(self, timeout=None):
        self.waits.append(timeout)
        return False


def _outage_result(detected, seconds=200):
    retry_at = datetime.now(timezone.utc) + timedelta(seconds=seconds)
    return {'success': not detected, 'succeeded': 0, 'failed': 3,
            'outcomes': {},
            'outage': {'detected': detected, 'affectedCount': 3,
                       'nextRetryAt': retry_at.isoformat() if detected else None}}


def _run_refresh_loop(monkeypatch, results, passes):
    """Run background_rss_refresh for `passes` iterations and return the waits."""
    event = _FakeEvent(passes)
    monkeypatch.setattr(background, 'shutdown_event', event)
    monkeypatch.setattr(background, 'run_cleanup', lambda: None)
    monkeypatch.setattr(background, '_run_tick', lambda fn, name: None)
    db = MagicMock()
    db.get_setting.return_value = '15'
    db.count_subscribed_feeds.return_value = 3
    monkeypatch.setattr(background, 'db', db)
    calls = {'n': 0}

    def _refresh_due_feeds(*_args, **_kwargs):
        result = results[min(calls['n'], len(results) - 1)]
        calls['n'] += 1
        return result

    monkeypatch.setattr('main_app.feeds.refresh_due_feeds', _refresh_due_feeds)
    monkeypatch.setattr('pricing_fetcher.refresh_pricing_if_stale', lambda: None)
    background.background_rss_refresh()
    return event.waits, db


class TestSharedOutageBackoff:
    def test_consecutive_outage_passes_do_not_reset_the_retry_window(self, monkeypatch):
        waits, _ = _run_refresh_loop(monkeypatch, [_outage_result(True)], 3)
        assert waits[0] < waits[1] < waits[2]

    def test_backoff_is_capped_at_the_normal_refresh_interval(self, monkeypatch):
        waits, _ = _run_refresh_loop(monkeypatch, [_outage_result(True)], 6)
        assert waits[-1] == 900

    def test_recovery_resets_the_attempt_counter(self, monkeypatch):
        results = [_outage_result(True), _outage_result(True),
                   _outage_result(False), _outage_result(True)]
        waits, _ = _run_refresh_loop(monkeypatch, results, 4)
        # Recovery drops back to the normal tick, and the next outage backs off
        # from the base delay again rather than continuing the doubling.
        assert waits[2] == background.REFRESH_TICK_SECONDS
        assert waits[3] == pytest.approx(waits[0], abs=5)


    def test_advertised_retry_time_tracks_the_backed_off_wait(self, monkeypatch):
        """The health panel renders feeds_next_refresh_retry_at, so it must name
        the attempt the loop will actually make, not the base delay."""
        waits, db = _run_refresh_loop(monkeypatch, [_outage_result(True)], 3)
        stamps = [call.args[1] for call in db.set_setting.call_args_list
                  if call.args[0] == 'feeds_next_refresh_retry_at']
        assert len(stamps) == len(waits)
        for stamp, wait in zip(stamps, waits, strict=True):
            advertised = (parse_iso_utc(stamp)
                          - datetime.now(timezone.utc)).total_seconds()
            assert advertised == pytest.approx(wait, abs=5)


class TestSearchIndexRebuildBackoff:
    @pytest.fixture(autouse=True)
    def _restore_rebuild_stamp(self):
        """Leaving a cadence stamp behind would silently suppress a rebuild in another suite."""
        previous = dict(background._INDEX_STATE)
        yield
        background._INDEX_STATE.update(previous)

    def _run_cleanup(self, monkeypatch, *, rebuild, slots=0, last=0.0, full_last=0.0, refresh=None):
        db = MagicMock()
        db.cleanup_old_episodes.return_value = (0, 0.0)
        db.get_all_podcasts.return_value = []
        db.rebuild_search_index.side_effect = rebuild
        db.refresh_search_index.side_effect = refresh
        monkeypatch.setattr(background, 'db', db)
        monkeypatch.setattr(background, 'rebuild_recents_feed', lambda: None)
        background._INDEX_STATE['last_refresh'] = last
        background._INDEX_STATE['last_full'] = full_last
        queue = MagicMock()
        queue.slot_count.return_value = slots
        with patch('processing_queue.ProcessingQueue', return_value=queue):
            background.run_cleanup()
        return db

    def test_failed_rebuild_is_not_retried_on_the_next_pass(self, monkeypatch):
        boom = lambda: (_ for _ in ()).throw(Exception('database is locked'))
        self._run_cleanup(monkeypatch, rebuild=boom)
        db = self._run_cleanup(
            monkeypatch, rebuild=boom,
            last=background._INDEX_STATE['last_refresh'])
        db.rebuild_search_index.assert_not_called()

    def test_six_hour_tick_refreshes_when_full_rebuild_not_due(self, monkeypatch):
        db = self._run_cleanup(monkeypatch, rebuild=lambda: None, full_last=time.time())
        db.refresh_search_index.assert_called_once()
        db.rebuild_search_index.assert_not_called()

    def test_weekly_tick_runs_the_full_rebuild(self, monkeypatch):
        full_last = time.time() - background.INDEX_FULL_REBUILD_INTERVAL_SECONDS - 60
        db = self._run_cleanup(monkeypatch, rebuild=lambda: None, full_last=full_last)
        db.rebuild_search_index.assert_called_once()
        db.refresh_search_index.assert_not_called()

    def test_refresh_lock_conflict_backs_off_without_retrying(self, monkeypatch):
        busy = RuntimeError('Search index rebuild already in progress')
        fail = MagicMock(side_effect=busy)
        self._run_cleanup(monkeypatch, rebuild=None, full_last=time.time(), refresh=fail)
        stamp = background._INDEX_STATE['last_refresh']
        assert stamp > time.time() - background.INDEX_REBUILD_INTERVAL_SECONDS
        db = self._run_cleanup(monkeypatch, rebuild=None, full_last=time.time(),
                               refresh=fail, last=stamp)
        db.refresh_search_index.assert_not_called()

    def test_active_run_defers_the_rebuild(self, monkeypatch):
        due = time.time() - background.INDEX_REBUILD_INTERVAL_SECONDS - 60
        db = self._run_cleanup(monkeypatch, rebuild=lambda: None, slots=1, last=due)
        db.rebuild_search_index.assert_not_called()

    def test_a_never_idle_instance_rebuilds_past_the_deferral_ceiling(self, monkeypatch):
        """Deferring to active runs forever means an always-busy instance never
        rebuilds its index."""
        overdue = time.time() - background.INDEX_REBUILD_MAX_DEFERRAL_SECONDS - 60
        db = self._run_cleanup(monkeypatch, rebuild=lambda: None, slots=1,
                               last=overdue)
        db.rebuild_search_index.assert_called_once()

    def test_a_failed_slot_lookup_keeps_the_cadence(self, monkeypatch):
        """Only a failed rebuild earns the cooldown; a failed queue lookup left
        the index untouched."""
        db = MagicMock()
        db.cleanup_old_episodes.return_value = (0, 0.0)
        db.get_all_podcasts.return_value = []
        monkeypatch.setattr(background, 'db', db)
        monkeypatch.setattr(background, 'rebuild_recents_feed', lambda: None)
        last = time.time() - background.INDEX_REBUILD_INTERVAL_SECONDS - 60
        background._INDEX_STATE['last_refresh'] = last
        with patch('processing_queue.ProcessingQueue',
                   side_effect=Exception('database is locked')):
            background.run_cleanup()
        assert background._INDEX_STATE['last_refresh'] == last
        db.rebuild_search_index.assert_not_called()

    def test_success_keeps_the_six_hour_cadence(self, monkeypatch):
        self._run_cleanup(monkeypatch, rebuild=lambda: None)
        stamp = background._INDEX_STATE['last_refresh']
        assert stamp == pytest.approx(time.time(), abs=5)
        db = self._run_cleanup(monkeypatch, rebuild=lambda: None, last=stamp)
        db.rebuild_search_index.assert_not_called()


class TestDownloadPruneCadence:
    @pytest.fixture(autouse=True)
    def _restore_prune_stamp(self):
        previous = dict(background._DOWNLOAD_STATE)
        yield
        background._DOWNLOAD_STATE.update(previous)

    def _run(self, monkeypatch):
        db = MagicMock()
        db.cleanup_old_episodes.return_value = (0, 0.0)
        db.get_all_podcasts.return_value = []
        db.get_setting_int.return_value = 90
        monkeypatch.setattr(background, 'db', db)
        monkeypatch.setattr(background, 'rebuild_recents_feed', lambda: None)
        queue = MagicMock()
        queue.slot_count.return_value = 1
        with patch('processing_queue.ProcessingQueue', return_value=queue):
            background.run_cleanup()
        return db

    def test_prune_runs_once_a_day(self, monkeypatch):
        background._DOWNLOAD_STATE['last_prune'] = 0
        assert self._run(monkeypatch).prune_downloads.call_count == 1
        assert self._run(monkeypatch).prune_downloads.call_count == 0


@pytest.mark.parametrize('value, expected', [('0', False), ('1', True), (None, False)])
def test_background_thread_flag_honors_explicit_disable(monkeypatch, value, expected):
    if value is None:
        monkeypatch.delenv('MINUSPOD_BACKGROUND_THREADS', raising=False)
    else:
        monkeypatch.setenv('MINUSPOD_BACKGROUND_THREADS', value)
    assert main_app._background_threads_enabled() is expected


class TestGeoLookupThread:
    @pytest.fixture(autouse=True)
    def _reset(self):
        previous = background._GEO_STATE['thread']
        background._GEO_STATE['thread'] = None
        yield
        background._GEO_STATE['thread'] = previous

    def test_starts_one_thread_and_skips_while_running(self, monkeypatch):
        import threading
        release = threading.Event()
        started = []
        monkeypatch.setattr(background.geo_lookup, 'run_geo_lookup',
                            lambda db: (started.append(1), release.wait(5)))
        background._start_geo_lookup()
        first = background._GEO_STATE['thread']
        while not started:
            time.sleep(0.01)
        background._start_geo_lookup()
        assert background._GEO_STATE['thread'] is first
        assert started == [1]
        release.set()
        first.join(5)
        background._start_geo_lookup()
        assert background._GEO_STATE['thread'] is not first
        background._GEO_STATE['thread'].join(5)

    def test_runs_on_the_five_minute_tick_not_the_cleanup_cadence(self, monkeypatch):
        fake_db = MagicMock()
        fake_db.reset_orphaned_queue_items.return_value = (0, 0)
        fake_db.reset_failed_queue_items.return_value = 0
        monkeypatch.setattr(background, 'db', fake_db)
        monkeypatch.setattr(background, 'reset_stuck_processing_episodes', lambda: None)
        monkeypatch.setattr(background, '_run_tick', lambda fn, name: None)
        started = []
        monkeypatch.setattr(background, '_start_geo_lookup', lambda: started.append(1))
        background._run_maintenance_tick(MagicMock())
        assert started == [1]

    def test_failure_is_logged_not_raised(self, monkeypatch):
        monkeypatch.setattr(background.geo_lookup, 'run_geo_lookup',
                            lambda db: (_ for _ in ()).throw(RuntimeError('boom')))
        background._run_geo_lookup_safely()
