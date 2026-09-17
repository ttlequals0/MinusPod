"""Background loop pacing: a shared RSS outage backs off across passes instead
of re-arming the same short retry, and a failed search-index rebuild waits out
a bounded cooldown rather than retrying every cleanup pass.
"""
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

from tests.app_bootstrap import bootstrap

_test_data_dir = bootstrap('background_maintenance_test_')
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
        """run_cleanup keeps its cadence on the function object; leaving a stamp
        behind would silently suppress a rebuild in another suite."""
        previous = getattr(background.run_cleanup, '_last_index_rebuild', None)
        yield
        if previous is None:
            background.run_cleanup.__dict__.pop('_last_index_rebuild', None)
        else:
            background.run_cleanup._last_index_rebuild = previous

    def _run_cleanup(self, monkeypatch, *, rebuild, slots=0, last=0.0):
        db = MagicMock()
        db.cleanup_old_episodes.return_value = (0, 0.0)
        db.get_all_podcasts.return_value = []
        db.rebuild_search_index.side_effect = rebuild
        monkeypatch.setattr(background, 'db', db)
        monkeypatch.setattr(background, 'rebuild_recents_feed', lambda: None)
        background.run_cleanup._last_index_rebuild = last
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
            last=background.run_cleanup._last_index_rebuild)
        db.rebuild_search_index.assert_not_called()

    def test_active_run_defers_the_rebuild(self, monkeypatch):
        import time
        due = time.time() - background.INDEX_REBUILD_INTERVAL_SECONDS - 60
        db = self._run_cleanup(monkeypatch, rebuild=lambda: None, slots=1, last=due)
        db.rebuild_search_index.assert_not_called()

    def test_a_never_idle_instance_rebuilds_past_the_deferral_ceiling(self, monkeypatch):
        """Deferring to active runs forever means an always-busy instance never
        rebuilds its index."""
        import time
        overdue = time.time() - background.INDEX_REBUILD_MAX_DEFERRAL_SECONDS - 60
        db = self._run_cleanup(monkeypatch, rebuild=lambda: None, slots=1,
                               last=overdue)
        db.rebuild_search_index.assert_called_once()

    def test_a_failed_slot_lookup_keeps_the_cadence(self, monkeypatch):
        """Only a failed rebuild earns the cooldown; a failed queue lookup left
        the index untouched."""
        import time
        db = MagicMock()
        db.cleanup_old_episodes.return_value = (0, 0.0)
        db.get_all_podcasts.return_value = []
        monkeypatch.setattr(background, 'db', db)
        monkeypatch.setattr(background, 'rebuild_recents_feed', lambda: None)
        last = time.time() - background.INDEX_REBUILD_INTERVAL_SECONDS - 60
        background.run_cleanup._last_index_rebuild = last
        with patch('processing_queue.ProcessingQueue',
                   side_effect=Exception('database is locked')):
            background.run_cleanup()
        assert background.run_cleanup._last_index_rebuild == last
        db.rebuild_search_index.assert_not_called()

    def test_success_keeps_the_six_hour_cadence(self, monkeypatch):
        import time
        self._run_cleanup(monkeypatch, rebuild=lambda: None)
        stamp = background.run_cleanup._last_index_rebuild
        assert stamp == pytest.approx(time.time(), abs=5)
        db = self._run_cleanup(monkeypatch, rebuild=lambda: None, last=stamp)
        db.rebuild_search_index.assert_not_called()
