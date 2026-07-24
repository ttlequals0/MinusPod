"""Tests for rss_refresh_interval_minutes (Task 2): registry default, PUT
validation, and the background refresh loop honoring the configured interval."""
import json
from unittest.mock import MagicMock

import pytest

from tests.app_bootstrap import bootstrap

_test_data_dir = bootstrap(
    'refresh_interval_test_', passphrase='refresh-interval-test-passphrase')

import database
from main_app import app, db as main_db
import main_app.background as background_module


class _FakeShutdownEvent:
    """Stand-in for the real, process-wide shutdown_event.

    main_app._startup() starts a real daemon thread running
    background_rss_refresh() against the actual shutdown_event singleton, so
    monkeypatching that shared object's wait()/is_set() races with it.
    background_rss_refresh() resolves `shutdown_event` as a module global on
    every call, so swapping main_app.background.shutdown_event isolates the
    synchronous, in-test call from the ambient thread (which keeps its own
    reference to the real object for any call already in flight).
    """

    def __init__(self):
        self._flag = False
        self.wait_calls = []

    def is_set(self):
        return self._flag

    def set(self):
        self._flag = True

    def wait(self, timeout=None):
        self.wait_calls.append(timeout)
        self._flag = True
        return True


@pytest.fixture
def client():
    """Flask test client."""
    app.config['TESTING'] = True
    with app.test_client() as c:
        yield c


def _get_settings(client):
    resp = client.get('/api/v1/settings')
    assert resp.status_code == 200
    return json.loads(resp.data)


class TestRegistryDefault:
    def test_get_exposes_default_of_15(self, client):
        data = _get_settings(client)
        assert data['defaults']['rssRefreshIntervalMinutes'] == 15


class TestPutValidation:
    def test_put_accepts_boundary_5(self, client):
        resp = client.put(
            '/api/v1/settings/ad-detection',
            data=json.dumps({'rssRefreshIntervalMinutes': 5}),
            content_type='application/json',
        )
        assert resp.status_code == 200, resp.data
        db = database.Database()
        assert db.get_setting('rss_refresh_interval_minutes') == '5'

    def test_put_accepts_boundary_1440(self, client):
        resp = client.put(
            '/api/v1/settings/ad-detection',
            data=json.dumps({'rssRefreshIntervalMinutes': 1440}),
            content_type='application/json',
        )
        assert resp.status_code == 200, resp.data
        db = database.Database()
        assert db.get_setting('rss_refresh_interval_minutes') == '1440'

    def test_put_rejects_below_floor(self, client):
        resp = client.put(
            '/api/v1/settings/ad-detection',
            data=json.dumps({'rssRefreshIntervalMinutes': 4}),
            content_type='application/json',
        )
        assert resp.status_code == 400
        assert 'rssRefreshIntervalMinutes' in json.loads(resp.data)['error']

    def test_put_rejects_above_ceiling(self, client):
        resp = client.put(
            '/api/v1/settings/ad-detection',
            data=json.dumps({'rssRefreshIntervalMinutes': 1441}),
            content_type='application/json',
        )
        assert resp.status_code == 400
        assert 'rssRefreshIntervalMinutes' in json.loads(resp.data)['error']

    def test_put_rejects_non_integer(self, client):
        resp = client.put(
            '/api/v1/settings/ad-detection',
            data=json.dumps({'rssRefreshIntervalMinutes': 'abc'}),
            content_type='application/json',
        )
        assert resp.status_code == 400
        assert 'rssRefreshIntervalMinutes' in json.loads(resp.data)['error']


class TestBackgroundRefreshLoop:
    def test_refresh_loop_reads_interval(self, monkeypatch):
        main_db.set_setting('rss_refresh_interval_minutes', '30', is_default=False)

        import main_app.feeds as feeds_mod
        import pricing_fetcher
        import update_checker

        monkeypatch.setattr(feeds_mod, 'refresh_all_feeds', MagicMock())
        monkeypatch.setattr(background_module, 'run_cleanup', MagicMock())
        monkeypatch.setattr(pricing_fetcher, 'refresh_pricing_if_stale', MagicMock())
        monkeypatch.setattr(update_checker, 'update_check_tick', MagicMock())
        # community_pattern_sync_tick / db_backup_tick default to disabled
        # (community_sync_enabled / db_backup_enabled both default false), so
        # they no-op for real without needing a patch here.

        fake_event = _FakeShutdownEvent()
        monkeypatch.setattr(background_module, 'shutdown_event', fake_event)

        background_module.background_rss_refresh()

        assert fake_event.wait_calls == [1800]

    def test_refresh_loop_clamps_out_of_range_db_value(self, monkeypatch):
        # A stored value outside 5-1440 (e.g. left over from a prior schema
        # or edited directly in the DB) must clamp, not blow up the loop.
        main_db.set_setting('rss_refresh_interval_minutes', '99999', is_default=False)

        import main_app.feeds as feeds_mod
        import pricing_fetcher
        import update_checker

        monkeypatch.setattr(feeds_mod, 'refresh_all_feeds', MagicMock())
        monkeypatch.setattr(background_module, 'run_cleanup', MagicMock())
        monkeypatch.setattr(pricing_fetcher, 'refresh_pricing_if_stale', MagicMock())
        monkeypatch.setattr(update_checker, 'update_check_tick', MagicMock())

        fake_event = _FakeShutdownEvent()
        monkeypatch.setattr(background_module, 'shutdown_event', fake_event)

        try:
            background_module.background_rss_refresh()
        finally:
            main_db.set_setting('rss_refresh_interval_minutes', '15', is_default=False)

        assert fake_event.wait_calls == [1440 * 60]
