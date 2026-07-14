"""Integration tests for per-feed refresh failure tracking (#516).

Failures spaced at least FEED_REFRESH_FAILURE_COUNT_INTERVAL apart
increment a per-feed counter; the Feed Refresh Failed alert fires exactly
once, when the count reaches the threshold. Success clears the state. The
feeds API exposes the failure fields only at or past the threshold, plus
the global all-feeds refresh timestamp.
"""
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))
os.environ.setdefault('MINUSPOD_DATA_DIR', tempfile.mkdtemp(prefix='feed-fail-test-'))

from config import FEED_REFRESH_FAILURE_ALERT_THRESHOLD


def _backdate_failure_stamp(db, slug, minutes=15):
    """Age the last counted failure so the next one is counted too."""
    stamp = (datetime.now(timezone.utc) - timedelta(minutes=minutes)
             ).strftime('%Y-%m-%dT%H:%M:%SZ')
    db.update_podcast(slug, last_refresh_failure_at=stamp)


def _fail_n_times(feeds_mod, db, slug, n, error='connection refused'):
    for _ in range(n):
        feeds_mod._record_refresh_failure(slug, error)
        _backdate_failure_stamp(db, slug)


@pytest.fixture
def seeded_feed(app_client, monkeypatch):
    from api import get_database
    db = get_database()
    # main_app.feeds captures the Database singleton at import time; other
    # tests reset that singleton, so point the module at the live instance.
    monkeypatch.setattr('main_app.feeds.db', db)
    slug = 'refresh-fail-feed'
    db.create_podcast(slug, 'https://example.com/feed.xml', 'Refresh Fail Test')
    yield {'slug': slug, 'db': db}
    db.delete_podcast(slug)


@pytest.fixture
def alert_recorder(monkeypatch):
    calls = []

    def _record(**kw):
        calls.append(kw)
        return True  # dispatched (not suppressed)

    monkeypatch.setattr('webhook_service.fire_feed_refresh_failed_event', _record)
    return calls


def _authed(client):
    with client.session_transaction() as sess:
        sess['authenticated'] = True


class TestRecordHelpers:
    def test_alert_fires_once_at_threshold(self, seeded_feed, alert_recorder):
        from main_app import feeds as feeds_mod
        slug, db = seeded_feed['slug'], seeded_feed['db']

        _fail_n_times(feeds_mod, db, slug, FEED_REFRESH_FAILURE_ALERT_THRESHOLD + 1)

        assert len(alert_recorder) == 1
        assert alert_recorder[0]['slug'] == slug
        assert (alert_recorder[0]['failure_count']
                == FEED_REFRESH_FAILURE_ALERT_THRESHOLD)
        p = db.get_podcast_by_slug(slug)
        assert p['refresh_failure_count'] == FEED_REFRESH_FAILURE_ALERT_THRESHOLD + 1
        assert p['last_refresh_error'] == 'connection refused'

    def test_rapid_retries_count_once(self, seeded_feed, alert_recorder):
        """Back-to-back failures (client-poll-driven refreshes during a
        blip) must not walk the counter to the alert threshold."""
        from main_app import feeds as feeds_mod
        slug, db = seeded_feed['slug'], seeded_feed['db']

        for _ in range(FEED_REFRESH_FAILURE_ALERT_THRESHOLD + 2):
            feeds_mod._record_refresh_failure(slug, 'connection refused')

        assert db.get_podcast_by_slug(slug)['refresh_failure_count'] == 1
        assert alert_recorder == []

    def test_suppressed_alert_retries_on_next_counted_failure(
            self, seeded_feed, monkeypatch):
        """When the dedup/burst caps swallow the threshold-transition alert,
        the count steps back so a later counted failure re-fires it --
        otherwise the outage's only alert is lost."""
        from main_app import feeds as feeds_mod
        slug, db = seeded_feed['slug'], seeded_feed['db']

        outcomes = iter([False, True])
        calls = []

        def _flaky(**kw):
            calls.append(kw)
            return next(outcomes)

        monkeypatch.setattr('webhook_service.fire_feed_refresh_failed_event', _flaky)

        _fail_n_times(feeds_mod, db, slug, FEED_REFRESH_FAILURE_ALERT_THRESHOLD)
        assert len(calls) == 1  # suppressed
        assert (db.get_podcast_by_slug(slug)['refresh_failure_count']
                == FEED_REFRESH_FAILURE_ALERT_THRESHOLD - 1)

        _fail_n_times(feeds_mod, db, slug, 1)
        assert len(calls) == 2  # re-fired and dispatched

    def test_error_at_keeps_first_failure_time(self, seeded_feed, alert_recorder):
        from main_app import feeds as feeds_mod
        slug, db = seeded_feed['slug'], seeded_feed['db']

        feeds_mod._record_refresh_failure(slug, 'first failure')
        first_at = db.get_podcast_by_slug(slug)['last_refresh_error_at']
        _backdate_failure_stamp(db, slug)
        feeds_mod._record_refresh_failure(slug, 'second failure')

        p = db.get_podcast_by_slug(slug)
        assert p['last_refresh_error_at'] == first_at
        assert p['last_refresh_error'] == 'second failure'

    def test_error_message_query_strings_scrubbed(self, seeded_feed, alert_recorder):
        """Private-feed tokens live in query strings; they must not reach
        the persisted error, the API, or webhook/email payloads."""
        from main_app import feeds as feeds_mod
        slug, db = seeded_feed['slug'], seeded_feed['db']

        feeds_mod._record_refresh_failure(
            slug, 'fetch of https://example.com/feed.xml?key=SECRET failed')

        err = db.get_podcast_by_slug(slug)['last_refresh_error']
        assert 'SECRET' not in err
        assert 'https://example.com/feed.xml?<redacted>' in err

    def test_success_clears_failure_state(self, seeded_feed, alert_recorder):
        from main_app import feeds as feeds_mod
        slug, db = seeded_feed['slug'], seeded_feed['db']

        feeds_mod._record_refresh_failure(slug, 'connection refused')
        feeds_mod._record_refresh_success(slug)

        p = db.get_podcast_by_slug(slug)
        assert p['refresh_failure_count'] == 0
        assert p['last_refresh_error'] is None
        assert p['last_refresh_error_at'] is None
        assert p['last_refresh_failure_at'] is None

        # A later failure run starts a fresh count and can alert again.
        _fail_n_times(feeds_mod, db, slug, FEED_REFRESH_FAILURE_ALERT_THRESHOLD)
        assert len(alert_recorder) == 1

    def test_success_clear_ignores_stale_caller_snapshot(self, seeded_feed,
                                                         alert_recorder):
        """The clear must act on current DB state, not on whatever row the
        refresh loaded before a concurrent attempt recorded a failure."""
        from main_app import feeds as feeds_mod
        slug, db = seeded_feed['slug'], seeded_feed['db']

        # Failure lands while a slow successful refresh is mid-flight.
        feeds_mod._record_refresh_failure(slug, 'transient blip')
        feeds_mod._record_refresh_success(slug)

        assert db.get_podcast_by_slug(slug)['refresh_failure_count'] == 0


class TestApiExposure:
    def test_feeds_list_exposes_failure_fields_and_global_stamp(
            self, app_client, seeded_feed):
        db = seeded_feed['db']
        db.update_podcast(seeded_feed['slug'],
                          refresh_failure_count=FEED_REFRESH_FAILURE_ALERT_THRESHOLD,
                          last_refresh_error='connection refused',
                          last_refresh_error_at='2026-07-14T00:00:00Z')
        db.set_setting('feeds_last_refresh_completed_at', '2026-07-14T00:15:00Z')

        _authed(app_client)
        resp = app_client.get('/api/v1/feeds')
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['lastRefreshCompletedAt'] == '2026-07-14T00:15:00Z'
        feed = next(f for f in data['feeds'] if f['slug'] == seeded_feed['slug'])
        assert feed['lastRefreshError'] == 'connection refused'
        assert feed['lastRefreshErrorAt'] == '2026-07-14T00:00:00Z'

    def test_error_fields_hidden_below_threshold(self, app_client, seeded_feed):
        """A single blip must not paint the UI's failing marker; the API
        withholds the error fields until the alert threshold is reached."""
        db = seeded_feed['db']
        db.update_podcast(seeded_feed['slug'],
                          refresh_failure_count=1,
                          last_refresh_error='transient blip',
                          last_refresh_error_at='2026-07-14T00:00:00Z')

        _authed(app_client)
        resp = app_client.get(f"/api/v1/feeds/{seeded_feed['slug']}")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['lastRefreshError'] is None
        assert data['lastRefreshErrorAt'] is None

    def test_get_feed_exposes_failure_fields_at_threshold(self, app_client,
                                                          seeded_feed):
        db = seeded_feed['db']
        db.update_podcast(seeded_feed['slug'],
                          refresh_failure_count=FEED_REFRESH_FAILURE_ALERT_THRESHOLD,
                          last_refresh_error='timed out',
                          last_refresh_error_at='2026-07-14T01:00:00Z')

        _authed(app_client)
        resp = app_client.get(f"/api/v1/feeds/{seeded_feed['slug']}")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['lastRefreshError'] == 'timed out'
        assert data['lastRefreshErrorAt'] == '2026-07-14T01:00:00Z'
