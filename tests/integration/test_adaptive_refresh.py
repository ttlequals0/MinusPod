"""Adaptive refresh wiring: scheduling after an attempt, due selection, client holds,
problem notifications, the problems endpoint and the two settings."""
import json
import logging
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))
os.environ.setdefault('MINUSPOD_DATA_DIR', tempfile.mkdtemp(prefix='adaptive-refresh-test-'))

from config import FEED_REFRESH_FAILURE_ALERT_THRESHOLD as THRESHOLD
from main_app import feeds as feeds_mod

ISO = '%Y-%m-%dT%H:%M:%SZ'


def _iso(delta_minutes=0):
    return (datetime.now(timezone.utc) + timedelta(minutes=delta_minutes)).strftime(ISO)


@pytest.fixture
def feed(app_client, monkeypatch):
    from api import get_database
    db = get_database()
    monkeypatch.setattr('main_app.feeds.db', db)
    slug = 'adaptive-feed'
    db.create_podcast(slug, 'https://feed.example.com/secret/TOKEN123/rss.xml?k=abc', 'Adaptive Feed')
    db.set_setting('rss_refresh_interval_minutes', '15', is_default=False)
    db.set_setting('adaptive_refresh_enabled', 'true', is_default=False)
    db.set_setting('adaptive_refresh_max_minutes', '60', is_default=False)
    yield {'slug': slug, 'db': db}
    db.delete_podcast(slug)


def _row(feed):
    return feed['db'].get_podcast_row(feed['slug'])


def _authed(client):
    with client.session_transaction() as sess:
        sess['authenticated'] = True


def _minutes_until(row):
    return (datetime.strptime(row['next_refresh_at'], ISO).replace(tzinfo=timezone.utc)
            - datetime.now(timezone.utc)).total_seconds() / 60


class TestScheduling:
    def test_success_schedules_at_the_floor_without_cadence(self, feed):
        feeds_mod._schedule_after_attempt(feed['slug'], feeds_mod.RefreshOutcome(True, 'refreshed'), {})
        row = _row(feed)
        assert row['refresh_interval_reason'] == 'floor'
        assert row['refresh_interval_seconds'] == 900
        assert 14 < _minutes_until(row) <= 15
        assert row['last_refresh_ok_at']

    def test_coalesced_and_local_attempts_do_not_schedule(self, feed):
        feeds_mod._schedule_after_attempt(feed['slug'], feeds_mod.RefreshOutcome(True, 'coalesced'), {})
        assert _row(feed)['next_refresh_at'] is None

    def test_failure_backs_off_from_the_floor(self, feed):
        feed['db'].update_podcast(feed['slug'], refresh_failure_count=3)
        feeds_mod._schedule_after_attempt(
            feed['slug'], feeds_mod.RefreshOutcome(False, 'fetch_failed', error='x'), {})
        row = _row(feed)
        assert row['refresh_interval_reason'] == 'backoff'
        assert 3600 <= row['refresh_interval_seconds'] <= 3960

    def test_uncounted_first_failure_retries_at_the_floor_not_the_healthy_interval(self, feed):
        feed['db'].update_podcast(feed['slug'], update_hint_seconds=30 * 86400)
        feeds_mod._schedule_after_attempt(
            feed['slug'], feeds_mod.RefreshOutcome(False, 'fetch_failed', error='x'), {})
        row = _row(feed)
        assert row['refresh_interval_reason'] == 'backoff'
        assert row['refresh_interval_seconds'] < 1100

    def test_retry_after_from_the_fetch_is_stored_and_honoured(self, feed):
        meta = {'status': 429, 'retry_after': '7200', 'kind': 'rate_limited'}
        feeds_mod._schedule_after_attempt(
            feed['slug'], feeds_mod.RefreshOutcome(False, 'fetch_failed', error='x'), meta)
        row = _row(feed)
        assert row['refresh_interval_reason'] == 'retry_after'
        assert row['refresh_interval_seconds'] == 7200
        assert row['retry_after_until']
        assert row['last_refresh_error_kind'] == 'rate_limited'
        assert feed['db'].get_refresh_hold_until(feed['slug']) is not None

    def test_retry_after_is_ignored_on_other_statuses(self, feed):
        meta = {'status': 500, 'retry_after': '7200', 'kind': 'http_error'}
        feeds_mod._schedule_after_attempt(
            feed['slug'], feeds_mod.RefreshOutcome(False, 'fetch_failed', error='x'), meta)
        assert _row(feed)['retry_after_until'] is None

    def test_success_clears_retry_after_and_stores_max_age(self, feed):
        feed['db'].update_podcast(feed['slug'], retry_after_until=_iso(-5))
        feeds_mod._schedule_after_attempt(
            feed['slug'], feeds_mod.RefreshOutcome(True, 'refreshed'), {'status': 200, 'max_age': 2400})
        row = _row(feed)
        assert row['retry_after_until'] is None
        assert row['refresh_interval_seconds'] == 2400 and row['refresh_interval_reason'] == 'hint'

    def test_disabled_mode_keeps_the_fixed_interval(self, feed):
        feed['db'].set_setting('adaptive_refresh_enabled', 'false', is_default=False)
        feed['db'].update_podcast(feed['slug'], update_hint_seconds=30 * 86400, feed_complete=1)
        feeds_mod._schedule_after_attempt(feed['slug'], feeds_mod.RefreshOutcome(True, 'refreshed'), {})
        row = _row(feed)
        assert (row['refresh_interval_seconds'], row['refresh_interval_reason']) == (900, 'fixed')

    def test_local_feed_is_never_scheduled(self, feed):
        feed['db'].update_podcast(feed['slug'], source_url='')
        feeds_mod._schedule_after_attempt(feed['slug'], feeds_mod.RefreshOutcome(True, 'refreshed'), {})
        assert _row(feed)['next_refresh_at'] is None

    def test_cadence_from_stored_episodes(self, feed):
        db = feed['db']
        now = datetime.now(timezone.utc)
        for i, hours in enumerate((9, 33, 57, 81)):
            db.upsert_episode(feed['slug'], f'ep{i}', original_url=f'https://x.example.com/{i}.mp3',
                              title=f'e{i}', published_at=(now - timedelta(hours=hours)).strftime(ISO))
        db.set_setting('adaptive_refresh_max_minutes', '1440', is_default=False)
        feeds_mod._schedule_after_attempt(feed['slug'], feeds_mod.RefreshOutcome(True, 'refreshed'), {})
        row = _row(feed)
        assert (row['refresh_interval_seconds'], row['refresh_interval_reason']) == (7200, 'cadence')

    def test_first_reason_logs_at_debug_and_repeat_is_silent(self, feed, caplog):
        caplog.set_level('DEBUG', logger='podcast.refresh')
        outcome = feeds_mod.RefreshOutcome(True, 'refreshed')
        feeds_mod._schedule_after_attempt(feed['slug'], outcome, {})
        feeds_mod._schedule_after_attempt(feed['slug'], outcome, {})
        records = [r for r in caplog.records if 'Next refresh in' in r.getMessage()]
        assert [r.levelname for r in records] == ['DEBUG']
        line = records[0].getMessage()
        assert 'TOKEN123' not in line
        assert urlparse(line.rsplit(' ', 1)[1]).hostname == 'feed.example.com'

    def test_real_change_logs_at_info(self, feed, caplog):
        caplog.set_level('DEBUG', logger='podcast.refresh')
        feed['db'].update_podcast(feed['slug'], refresh_interval_reason='max')
        feeds_mod._schedule_after_attempt(feed['slug'], feeds_mod.RefreshOutcome(True, 'refreshed'), {})
        records = [r for r in caplog.records if 'Next refresh in' in r.getMessage()]
        assert [r.levelname for r in records] == ['INFO']

    def test_first_backoff_stays_at_info(self, feed, caplog):
        caplog.set_level('DEBUG', logger='podcast.refresh')
        outcome = feeds_mod.RefreshOutcome(False, 'fetch_failed')
        feeds_mod._schedule_after_attempt(feed['slug'], outcome, {})
        records = [r for r in caplog.records if 'Next refresh in' in r.getMessage()]
        assert [r.levelname for r in records] == ['INFO'] and '(backoff)' in records[0].getMessage()


@pytest.mark.parametrize('old,new,expected', [
    (None, 'max', logging.DEBUG),
    (None, 'floor', logging.DEBUG),
    (None, 'backoff', logging.INFO),
    (None, 'retry_after', logging.INFO),
    ('max', 'max', None),
    ('max', 'backoff', logging.INFO),
    ('backoff', 'backoff', logging.INFO),
    ('retry_after', 'retry_after', logging.INFO),
    ('cadence', 'max', logging.INFO),
    ('cadence', 'due', logging.DEBUG),
    ('due', 'cadence', logging.DEBUG),
    ('max', 'due', logging.DEBUG),
    ('due', 'max', logging.DEBUG),
    ('dormant', 'due', logging.INFO),
    ('due', 'dormant', logging.INFO),
    ('backoff', 'due', logging.INFO),
    ('due', 'backoff', logging.INFO),
    ('due', 'retry_after', logging.INFO),
])
def test_reason_log_level(old, new, expected):
    from feed_schedule import reason_log_level
    assert reason_log_level(old, new) == expected


class TestDueSelection:
    def test_null_future_and_past(self, feed):
        db, slug = feed['db'], feed['slug']
        assert slug in db.get_due_feed_slugs(500)
        db.update_podcast(slug, next_refresh_at=_iso(30), last_refresh_attempt_at=_iso(-60))
        assert slug not in db.get_due_feed_slugs(500)
        db.update_podcast(slug, next_refresh_at=_iso(-1), last_refresh_attempt_at=_iso(-60))
        assert slug in db.get_due_feed_slugs(500)

    def test_null_schedule_is_due_unless_just_attempted(self, feed):
        db, slug = feed['db'], feed['slug']
        db.update_podcast(slug, last_refresh_attempt_at=_iso(-2))
        assert slug in db.get_due_feed_slugs(500)
        db.update_podcast(slug, last_refresh_attempt_at=_iso(0))
        assert slug not in db.get_due_feed_slugs(500)

    def test_a_just_attempted_feed_is_not_reselected(self, feed):
        db, slug = feed['db'], feed['slug']
        db.update_podcast(slug, next_refresh_at=_iso(-5), last_refresh_attempt_at=_iso(0))
        assert slug not in db.get_due_feed_slugs(500)

    def test_limit_applies_most_overdue_first(self, feed):
        db = feed['db']
        db.create_podcast('adaptive-two', 'https://two.example.com/rss', 'Two')
        try:
            db.update_podcast(feed['slug'], next_refresh_at=_iso(-5), last_refresh_attempt_at=_iso(-60))
            db.update_podcast('adaptive-two', next_refresh_at=_iso(-50), last_refresh_attempt_at=_iso(-90))
            due = db.get_due_feed_slugs(500)
            assert due.index('adaptive-two') < due.index(feed['slug'])
        finally:
            db.delete_podcast('adaptive-two')


class TestClientHold:
    def test_failing_feed_with_a_future_retry_is_held(self, feed):
        db, slug = feed['db'], feed['slug']
        assert db.get_refresh_hold_until(slug) is None
        db.update_podcast(slug, refresh_failure_count=2, next_refresh_at=_iso(90))
        assert db.get_refresh_hold_until(slug) is not None

    def test_healthy_feed_is_never_held_by_its_schedule(self, feed):
        db, slug = feed['db'], feed['slug']
        db.update_podcast(slug, refresh_failure_count=0, next_refresh_at=_iso(90))
        assert db.get_refresh_hold_until(slug) is None

    def test_elapsed_retry_after_does_not_hold(self, feed):
        db, slug = feed['db'], feed['slug']
        db.update_podcast(slug, retry_after_until=_iso(-1))
        assert db.get_refresh_hold_until(slug) is None

    def test_podping_waits_out_a_retry_after(self, feed, monkeypatch):
        db, slug = feed['db'], feed['slug']
        db.update_podcast(slug, retry_after_until=_iso(30))
        calls = []
        monkeypatch.setattr('main_app.feeds.refresh_rss_feed', lambda *a, **k: calls.append(a))
        assert feeds_mod.refresh_single_feed(slug) is True
        assert calls == []


class TestNotifications:
    @pytest.fixture
    def events(self, monkeypatch):
        failed, recovered = [], []
        monkeypatch.setattr('webhook_service.fire_feed_refresh_failed_event',
                            lambda **kw: failed.append(kw) or True)
        monkeypatch.setattr('webhook_service.fire_feed_refresh_recovered_event',
                            lambda **kw: recovered.append(kw) or True)
        return failed, recovered

    def _fail(self, feed, n):
        for _ in range(n):
            feeds_mod._record_refresh_failure(feed['slug'], 'connection refused')
            feed['db'].update_podcast(feed['slug'], last_refresh_failure_at=_iso(-30))

    def test_one_failure_event_and_one_recovery_event(self, feed, events):
        failed, recovered = events
        self._fail(feed, THRESHOLD + 2)
        assert len(failed) == 1 and recovered == []
        feeds_mod._record_refresh_success(feed['slug'])
        feeds_mod._record_refresh_success(feed['slug'])
        assert len(recovered) == 1
        assert recovered[0]['feed_url'] == 'https://feed.example.com'
        assert recovered[0]['failure_count'] == THRESHOLD + 2

    def test_no_recovery_event_for_a_feed_below_the_threshold(self, feed, events):
        _failed, recovered = events
        self._fail(feed, THRESHOLD - 1)
        feeds_mod._record_refresh_success(feed['slug'])
        assert recovered == []

    def test_a_second_outage_alerts_again(self, feed, events):
        failed, recovered = events
        self._fail(feed, THRESHOLD)
        feeds_mod._record_refresh_success(feed['slug'])
        self._fail(feed, THRESHOLD)
        assert len(failed) == 2 and len(recovered) == 1

    def test_recovery_event_reaches_webhook_and_email_paths(self):
        import email_service
        import webhook_service
        assert 'Feed Refresh Recovered' in webhook_service.VALID_EVENTS
        assert 'Feed Refresh Recovered' in email_service.FORMATTERS
        subject, rows, _hint = email_service.FORMATTERS['Feed Refresh Recovered']({
            'podcast_name': 'My Podcast', 'slug': 'my-podcast',
            'feed_url': 'https://feed.example.com', 'failure_count': 3})
        assert 'Recovered' in subject and rows


class TestProblemsEndpoint:
    def test_lists_only_feeds_at_the_threshold_with_host_only(self, app_client, feed):
        db, slug = feed['db'], feed['slug']
        db.update_podcast(slug, refresh_failure_count=THRESHOLD, last_refresh_error_kind='gone',
                          last_refresh_error='HTTP 404 from https://feed.example.com/secret/TOKEN123/rss.xml?k=abc',
                          last_refresh_error_at='2026-07-14T00:00:00Z',
                          last_refresh_ok_at='2026-07-13T00:00:00Z', next_refresh_at='2026-07-14T06:00:00Z')
        _authed(app_client)
        data = app_client.get('/api/v1/feeds/problems').get_json()
        entry = next(p for p in data['problems'] if p['slug'] == slug)
        assert entry['host'] == 'https://feed.example.com'
        assert entry['errorKind'] == 'gone' and 'not found' in entry['message']
        assert entry['consecutiveFailures'] == THRESHOLD
        assert entry['lastSuccessAt'] == '2026-07-13T00:00:00Z'
        assert entry['nextRetryAt'] == '2026-07-14T06:00:00Z'
        assert 'TOKEN123' not in json.dumps(data) and 'k=abc' not in json.dumps(data)
        assert data['count'] == len(data['problems']) and data['threshold'] == THRESHOLD

    def test_below_threshold_is_not_listed(self, app_client, feed):
        feed['db'].update_podcast(feed['slug'], refresh_failure_count=THRESHOLD - 1)
        _authed(app_client)
        assert all(p['slug'] != feed['slug']
                   for p in app_client.get('/api/v1/feeds/problems').get_json()['problems'])

    def test_legacy_error_text_is_classified(self, app_client, feed):
        feed['db'].update_podcast(feed['slug'], refresh_failure_count=THRESHOLD,
                                  last_refresh_error='Fetched feed could not be parsed as RSS')
        _authed(app_client)
        entry = next(p for p in app_client.get('/api/v1/feeds/problems').get_json()['problems']
                     if p['slug'] == feed['slug'])
        assert entry['errorKind'] == 'invalid_xml'

    def test_a_stored_unknown_kind_does_not_hide_a_better_classification(self, app_client, feed):
        feed['db'].update_podcast(feed['slug'], refresh_failure_count=THRESHOLD,
                                  last_refresh_error_kind='unknown',
                                  last_refresh_error='HTTP 404 from https://feed.example.com/x')
        _authed(app_client)
        entry = next(p for p in app_client.get('/api/v1/feeds/problems').get_json()['problems']
                     if p['slug'] == feed['slug'])
        assert entry['errorKind'] == 'gone'

    def test_feed_object_exposes_the_schedule(self, app_client, feed):
        feeds_mod._schedule_after_attempt(feed['slug'], feeds_mod.RefreshOutcome(True, 'refreshed'), {})
        _authed(app_client)
        data = app_client.get(f"/api/v1/feeds/{feed['slug']}").get_json()
        assert data['refreshIntervalSeconds'] == 900 and data['refreshIntervalReason'] == 'floor'
        assert data['nextRefreshAt'] and data['consecutiveRefreshFailures'] == 0
        assert data['lastRefreshOkAt']


class TestSettings:
    def _put(self, client, body):
        return client.put('/api/v1/settings/ad-detection', json=body)

    def test_defaults_and_round_trip(self, app_client, feed):
        _authed(app_client)
        data = app_client.get('/api/v1/settings').get_json()
        assert data['defaults']['adaptiveRefreshEnabled'] is True
        assert data['defaults']['adaptiveRefreshMaxMinutes'] == 60
        resp = self._put(app_client, {'adaptiveRefreshEnabled': False, 'adaptiveRefreshMaxMinutes': 120})
        assert resp.status_code == 200
        data = app_client.get('/api/v1/settings').get_json()
        assert data['adaptiveRefreshEnabled']['value'] is False
        assert data['adaptiveRefreshMaxMinutes']['value'] == 120

    @pytest.mark.parametrize('body', [
        {'adaptiveRefreshMaxMinutes': 14}, {'adaptiveRefreshMaxMinutes': 1441},
        {'adaptiveRefreshMaxMinutes': 'x'}, {'adaptiveRefreshMaxMinutes': True},
        {'adaptiveRefreshEnabled': 'yes'},
        {'rssRefreshIntervalMinutes': 90, 'adaptiveRefreshMaxMinutes': 60},
    ])
    def test_validation(self, app_client, feed, body):
        _authed(app_client)
        assert self._put(app_client, body).status_code == 400

    def test_max_never_drops_below_a_raised_floor(self, feed):
        from config import resolve_adaptive_refresh
        feed['db'].set_setting('rss_refresh_interval_minutes', '120', is_default=False)
        try:
            assert resolve_adaptive_refresh(feed['db']) == (True, 120 * 60, 120 * 60)
        finally:
            feed['db'].set_setting('rss_refresh_interval_minutes', '15', is_default=False)


class TestSettingsClampSchedule:
    def _put(self, client, body):
        _authed(client)
        return client.put('/api/v1/settings/ad-detection', json=body)

    def test_a_lowered_ceiling_pulls_healthy_feeds_in(self, app_client, feed):
        db, slug = feed['db'], feed['slug']
        db.update_podcast(slug, next_refresh_at=_iso(24 * 60))
        assert self._put(app_client, {'adaptiveRefreshMaxMinutes': 30}).status_code == 200
        assert _minutes_until(_row(feed)) <= 30

    def test_a_lowered_ceiling_updates_the_interval_and_reason_with_the_time(self, app_client, feed):
        db, slug = feed['db'], feed['slug']
        db.update_podcast(slug, next_refresh_at=_iso(24 * 60), refresh_interval_seconds=24 * 3600,
                          refresh_interval_reason='max')
        assert self._put(app_client, {'adaptiveRefreshMaxMinutes': 30}).status_code == 200
        row = _row(feed)
        assert row['refresh_interval_seconds'] == 30 * 60
        assert row['refresh_interval_reason'] == 'max'
        assert _minutes_until(row) <= 30

    def test_a_lowered_floor_pulls_floor_bound_feeds_in_but_not_cadence_feeds(self, app_client, feed):
        db, slug = feed['db'], feed['slug']
        db.create_podcast('adaptive-cadence', 'https://cadence.example.com/rss', 'Cadence')
        try:
            db.update_podcast(slug, next_refresh_at=_iso(15), refresh_interval_seconds=15 * 60,
                              refresh_interval_reason='due')
            far = _iso(40)
            db.update_podcast('adaptive-cadence', next_refresh_at=far, refresh_interval_seconds=40 * 60,
                              refresh_interval_reason='cadence')
            assert self._put(app_client, {'rssRefreshIntervalMinutes': 5}).status_code == 200
            row = _row(feed)
            assert row['refresh_interval_seconds'] == 5 * 60
            assert _minutes_until(row) <= 5
            assert db.get_podcast_row('adaptive-cadence')['next_refresh_at'] == far
        finally:
            db.set_setting('rss_refresh_interval_minutes', '15', is_default=False)
            db.delete_podcast('adaptive-cadence')

    def test_feeds_in_backoff_or_held_by_retry_after_keep_their_time(self, app_client, feed):
        db = feed['db']
        db.create_podcast('adaptive-held', 'https://held.example.com/rss', 'Held')
        far = _iso(24 * 60)
        try:
            db.update_podcast(feed['slug'], next_refresh_at=far, refresh_failure_count=2)
            db.update_podcast('adaptive-held', next_refresh_at=far, retry_after_until=_iso(23 * 60))
            assert self._put(app_client, {'adaptiveRefreshEnabled': False}).status_code == 200
            assert _row(feed)['next_refresh_at'] == far
            assert db.get_podcast_row('adaptive-held')['next_refresh_at'] == far
        finally:
            db.delete_podcast('adaptive-held')

    def test_an_unrelated_setting_leaves_the_schedule_alone(self, app_client, feed):
        far = _iso(24 * 60)
        feed['db'].update_podcast(feed['slug'], next_refresh_at=far)
        assert self._put(app_client, {'podpingEnabled': True}).status_code == 200
        assert _row(feed)['next_refresh_at'] == far


class TestFetchMetaIsolation:
    def test_one_feeds_retry_after_never_reaches_the_next_feed(self, feed, monkeypatch):
        db = feed['db']
        db.create_podcast('adaptive-other', 'https://feed.example.com/other.xml', 'Other')
        calls = []

        def fake_fetch(url, meta=None, **kwargs):
            calls.append(url)
            if len(calls) == 1:
                meta.update(status=429, retry_after='900', kind='rate_limited')
            return None, None, None

        monkeypatch.setattr('main_app.feeds.rss_parser.fetch_feed_conditional', fake_fetch)
        first, second = {}, {}
        try:
            feeds_mod.refresh_rss_feed(feed['slug'], 'https://feed.example.com/a.xml', force=True, meta=first)
            assert _row(feed)['retry_after_until']
            feeds_mod.refresh_rss_feed('adaptive-other', 'https://feed.example.com/b.xml', force=True, meta=second)
            assert len(calls) == 2
            assert first['retry_after'] == '900' and second == {}
            assert not db.get_podcast_row('adaptive-other')['retry_after_until']
        finally:
            db.delete_podcast('adaptive-other')

    def test_a_path_that_never_fetches_schedules_from_an_empty_meta(self, feed):
        outcome = feeds_mod.RefreshOutcome(False, 'fetch_failed', error='x')
        feeds_mod._schedule_after_attempt(feed['slug'], outcome, {})
        row = _row(feed)
        assert row['retry_after_until'] is None and row['last_refresh_error_kind'] == 'unknown'


class TestManualRefreshBypassesTheBreaker:
    def test_only_the_explicit_refresh_probes_through_an_open_host_breaker(self, app_client, feed, monkeypatch):
        seen = []

        def fake_fetch(url, meta=None, **kwargs):
            seen.append(kwargs.get('bypass_breaker', False))
            return None, None, None

        monkeypatch.setattr('main_app.feeds.rss_parser.fetch_feed_conditional', fake_fetch)
        feeds_mod.refresh_rss_feed(feed['slug'], 'https://feed.example.com/a.xml', force=True)
        _authed(app_client)
        csrf = {'X-CSRF-Token': app_client.get_cookie('minuspod_csrf').value} \
            if app_client.get_cookie('minuspod_csrf') else {}
        app_client.post(f"/api/v1/feeds/{feed['slug']}/refresh", headers=csrf)
        assert seen == [False, True]


class TestOneScheduleWritePerAttempt:
    @staticmethod
    def _fail_fetch(monkeypatch):
        monkeypatch.setattr('main_app.feeds.rss_parser.fetch_feed_conditional',
                            lambda url, meta=None, **kw: (None, None, None))

    @staticmethod
    def _count_schedule_writes(feed, monkeypatch):
        writes = []
        real = feed['db'].update_podcast

        def spy(slug, **fields):
            if 'next_refresh_at' in fields:
                writes.append(fields)
            return real(slug, **fields)

        monkeypatch.setattr(feed['db'], 'update_podcast', spy)
        return writes

    def test_a_failed_single_attempt_schedules_once_from_the_final_count(self, feed, monkeypatch):
        self._fail_fetch(monkeypatch)
        writes = self._count_schedule_writes(feed, monkeypatch)
        feed['db'].update_podcast(feed['slug'], refresh_failure_count=3)
        feeds_mod.refresh_rss_feed(feed['slug'], 'https://feed.example.com/a.xml', force=True)
        row = _row(feed)
        assert len(writes) == 1
        assert row['refresh_failure_count'] == 4
        assert 7200 <= row['refresh_interval_seconds'] <= 7920

    def test_a_successful_attempt_schedules_once(self, feed, monkeypatch):
        writes = self._count_schedule_writes(feed, monkeypatch)
        feeds_mod._schedule_after_attempt(
            feed['slug'], feeds_mod.RefreshOutcome(True, 'refreshed'), {'max_age': 600})
        assert len(writes) == 1
        assert {'last_refresh_ok_at', 'retry_after_until', 'http_hint_seconds'} <= set(writes[0])

    def test_batch_schedules_once_after_the_failure_is_counted(self, feed, monkeypatch):
        self._fail_fetch(monkeypatch)
        writes = self._count_schedule_writes(feed, monkeypatch)
        feed['db'].update_podcast(feed['slug'], refresh_failure_count=3)
        feeds_mod._run_refresh_batch(True, slugs=[feed['slug']])
        row = _row(feed)
        assert len(writes) == 1
        assert row['refresh_failure_count'] == 4
        assert 7200 <= row['refresh_interval_seconds'] <= 7920

    def test_suppressed_outage_retries_at_the_floor_without_backoff(self, feed, monkeypatch):
        db = feed['db']
        slugs = [feed['slug'], 'adaptive-b', 'adaptive-c']
        for extra in slugs[1:]:
            db.create_podcast(extra, f'https://{extra}.example.com/rss', extra)
        for slug in slugs:
            db.update_podcast(slug, refresh_failure_count=3)
        self._fail_fetch(monkeypatch)
        try:
            result = feeds_mod._run_refresh_batch(True, slugs=slugs)
            assert result['outage']['detected'] is True
            for slug in slugs:
                row = db.get_podcast_row(slug)
                assert row['refresh_failure_count'] == 3
                assert (row['refresh_interval_seconds'], row['refresh_interval_reason']) == (900, 'fixed')
        finally:
            for extra in slugs[1:]:
                db.delete_podcast(extra)
            db.set_setting('feeds_refresh_outage_active', '0')
            db.set_setting('feeds_next_refresh_retry_at', '')
