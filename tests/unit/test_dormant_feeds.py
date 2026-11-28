"""Tests for dormant feeds: served-RSS request stamping, the dormant_feed_days
setting, and the auto-process gate that skips feeds no client fetches."""
import json
from datetime import timedelta
from unittest.mock import patch

import pytest

from tests.app_bootstrap import bootstrap

_test_data_dir = bootstrap(
    'dormant_feeds_test_', passphrase='dormant-feeds-test-passphrase')

from main_app import app, background, db as main_db
import main_app.routes as routes_module
from utils.time import ISO_FORMAT, utc_now


def _days_ago(days):
    return (utc_now() - timedelta(days=days)).strftime(ISO_FORMAT)


def _set_columns(slug, **columns):
    assignments = ', '.join(f'{name} = ?' for name in columns)
    with main_db.transaction(immediate=True) as conn:
        # Column names come from test code only.
        conn.execute(f"UPDATE podcasts SET {assignments} WHERE slug = ?",  # noqa: S608
                     (*columns.values(), slug))


@pytest.fixture
def client():
    app.config['TESTING'] = True
    with app.test_client() as c:
        yield c


@pytest.fixture
def dormancy(preserve_setting):
    """Turn dormancy on at 30 days for one test."""
    preserve_setting('dormant_feed_days')
    preserve_setting('auto_process_enabled')
    main_db.set_setting('dormant_feed_days', '30', is_default=False)
    main_db.set_setting('auto_process_enabled', 'true', is_default=False)


@pytest.fixture
def feed(request):
    slug = f"dormant-{request.node.name.replace('_', '-')[:40]}"
    main_db.create_podcast(slug, f'https://example.com/{slug}.xml', 'Dormant Test')
    yield slug
    main_db.delete_podcast(slug)


class TestIsFeedDormant:
    def test_off_by_default(self, feed, preserve_setting):
        preserve_setting('dormant_feed_days')
        main_db.clear_setting('dormant_feed_days')
        _set_columns(feed, last_feed_request_at=_days_ago(400))
        assert main_db.is_feed_dormant(main_db.get_podcast_row(feed)) is False

    def test_recent_request_is_active(self, feed, dormancy):
        _set_columns(feed, last_feed_request_at=_days_ago(29))
        assert main_db.is_feed_dormant(main_db.get_podcast_row(feed)) is False

    def test_old_request_is_dormant(self, feed, dormancy):
        _set_columns(feed, last_feed_request_at=_days_ago(31))
        assert main_db.is_feed_dormant(main_db.get_podcast_row(feed)) is True

    def test_never_requested_counts_from_creation(self, feed, dormancy):
        _set_columns(feed, last_feed_request_at=None, created_at=_days_ago(1))
        assert main_db.is_feed_dormant(main_db.get_podcast_row(feed)) is False
        _set_columns(feed, created_at=_days_ago(31))
        assert main_db.is_feed_dormant(main_db.get_podcast_row(feed)) is True

    def test_local_feed_never_dormant(self, dormancy):
        slug = 'dormant-local-feed'
        main_db.create_podcast(slug, '', 'Local', feed_type='local')
        try:
            _set_columns(slug, last_feed_request_at=_days_ago(400))
            assert main_db.is_feed_dormant(main_db.get_podcast_row(slug)) is False
        finally:
            main_db.delete_podcast(slug)

    def test_missing_podcast(self, dormancy):
        assert main_db.is_feed_dormant(None) is False


class TestAutoProcessGate:
    def test_dormant_feed_skips_auto_process(self, feed, dormancy):
        _set_columns(feed, last_feed_request_at=_days_ago(31))
        assert main_db.is_auto_process_enabled_for_podcast(feed) is False

    def test_active_feed_auto_processes(self, feed, dormancy):
        _set_columns(feed, last_feed_request_at=_days_ago(1))
        assert main_db.is_auto_process_enabled_for_podcast(feed) is True

    def test_explicit_override_beats_dormancy(self, feed, dormancy):
        _set_columns(feed, last_feed_request_at=_days_ago(31),
                     auto_process_override='true')
        assert main_db.is_auto_process_enabled_for_podcast(feed) is True


class TestFeedRequestStamp:
    def test_touch_stamps_then_throttles(self, feed):
        _set_columns(feed, last_feed_request_at=None)
        assert main_db.touch_feed_request(feed, 600) is True
        first = main_db.get_podcast_row(feed)['last_feed_request_at']
        assert first is not None
        assert main_db.touch_feed_request(feed, 600) is False
        assert main_db.get_podcast_row(feed)['last_feed_request_at'] == first

    def test_touch_restamps_after_interval(self, feed):
        _set_columns(feed, last_feed_request_at=_days_ago(1))
        assert main_db.touch_feed_request(feed, 600) is True

    def test_note_feed_request_wakes_dormant_feed(self, feed, dormancy):
        _set_columns(feed, last_feed_request_at=_days_ago(31))
        routes_module._feed_request_stamped_at.pop(feed, None)
        with app.test_request_context('/'):
            routes_module._note_feed_request(feed)
        assert main_db.is_feed_dormant(main_db.get_podcast_row(feed)) is False

    def test_note_feed_request_memo_skips_database(self, feed, monkeypatch):
        routes_module._feed_request_stamped_at.pop(feed, None)
        calls = []
        monkeypatch.setattr(main_db, 'touch_feed_request',
                            lambda *args: calls.append(args) or True)
        with app.test_request_context('/'):
            routes_module._note_feed_request(feed)
            routes_module._note_feed_request(feed)
        assert len(calls) == 1


class TestServeRssStamps:
    def test_rss_fetch_stamps_feed(self, client, feed):
        _set_columns(feed, last_feed_request_at=None)
        routes_module._feed_request_stamped_at.pop(feed, None)
        feed_map = {feed: {'in': f'https://example.com/{feed}.xml', 'out': feed}}
        with patch('main_app.routes.get_feed_map', return_value=feed_map), \
                patch('main_app.routes.storage.get_rss', return_value='<rss/>'), \
                patch('main_app.feeds.refresh_rss_feed'), \
                patch('main_app.routes._kick_background_refresh'):
            resp = client.get(f'/{feed}')
        assert resp.status_code == 200
        assert main_db.get_podcast_row(feed)['last_feed_request_at'] is not None

    def test_unknown_slug_not_stamped(self, client, monkeypatch):
        calls = []
        monkeypatch.setattr(routes_module, '_note_feed_request', calls.append)
        with patch('main_app.routes.get_feed_map', return_value={}):
            assert client.get('/no-such-feed').status_code == 404
        assert calls == []


class TestDispatcherGate:
    def _claim(self, feed):
        return {'id': 1, 'podcast_slug': feed, 'episode_id': 'ep1',
                'original_url': 'https://example.com/ep1.mp3', 'title': 'Ep 1'}

    def test_dormant_feed_claim_is_released_not_consumed(self, feed, dormancy, monkeypatch):
        _set_columns(feed, last_feed_request_at=_days_ago(31))
        closed = []
        monkeypatch.setattr(background.db, 'close_claimed_queue_row',
                            lambda queue_id, status, reason=None: closed.append((status, reason)))
        assert background._run_claimed_episode(self._claim(feed), set()) == 'bounced'
        assert closed == [('pending', None)]

    def test_disabled_feed_keeps_disabled_reason(self, feed, dormancy, monkeypatch):
        _set_columns(feed, last_feed_request_at=_days_ago(31),
                     auto_process_override='false')
        closed = []
        monkeypatch.setattr(background.db, 'close_claimed_queue_row',
                            lambda queue_id, status, reason=None: closed.append((status, reason)))
        assert background._run_claimed_episode(self._claim(feed), set()) == 'skipped'
        assert closed == [('completed', 'Auto-process disabled for this feed')]


class TestSettingsApi:
    @pytest.fixture(autouse=True)
    def _restore(self, preserve_setting):
        preserve_setting('dormant_feed_days')

    def _put(self, client, value):
        return client.put(
            '/api/v1/settings/ad-detection',
            data=json.dumps({'dormantFeedDays': value}),
            content_type='application/json',
        )

    def test_default_is_off(self, client):
        data = json.loads(client.get('/api/v1/settings').data)
        assert data['defaults']['dormantFeedDays'] == 0

    @pytest.mark.parametrize('value', [0, 30, 3650])
    def test_put_accepts_range(self, client, value):
        resp = self._put(client, value)
        assert resp.status_code == 200, resp.data
        assert main_db.get_setting('dormant_feed_days') == str(value)
        data = json.loads(client.get('/api/v1/settings').data)
        assert data['dormantFeedDays']['value'] == value

    @pytest.mark.parametrize('value', [-1, 3651, 'abc', True, 1.5])
    def test_put_rejects_invalid(self, client, value):
        resp = self._put(client, value)
        assert resp.status_code == 400
        assert 'dormantFeedDays' in json.loads(resp.data)['error']


class TestFeedApi:
    def test_feed_detail_reports_dormancy(self, client, feed, dormancy):
        stamp = _days_ago(31)
        _set_columns(feed, last_feed_request_at=stamp)
        data = json.loads(client.get(f'/api/v1/feeds/{feed}').data)
        assert data['dormant'] is True
        assert data['lastFeedRequestAt'] == stamp


class TestSharedRules:
    def test_days_argument_overrides_the_setting(self, feed):
        _set_columns(feed, last_feed_request_at=_days_ago(10))
        row = main_db.get_podcast_row(feed)
        assert main_db.is_feed_dormant(row, 5) is True
        assert main_db.is_feed_dormant(row, 0) is False

    def test_block_reason_comes_from_the_decision(self, feed, dormancy):
        _set_columns(feed, last_feed_request_at=_days_ago(31))
        assert main_db.auto_process_block(feed) == 'dormant'
        _set_columns(feed, auto_process_override='false')
        assert main_db.auto_process_block(feed) == 'disabled'
        _set_columns(feed, auto_process_override='true')
        assert main_db.auto_process_block(feed) is None

    def test_web_player_counts_for_history_but_not_for_fetch_tracking(self):
        with app.test_request_context('/', headers={'Sec-Fetch-Site': 'same-origin'}):
            assert routes_module._is_client_fetch() is False
            assert routes_module._is_client_fetch(include_web_player=True) is True
        with app.test_request_context('/', method='HEAD'):
            assert routes_module._is_client_fetch(include_web_player=True) is False

    def test_memo_is_pruned_once_it_grows(self, monkeypatch):
        monkeypatch.setattr(main_db, 'touch_feed_request', lambda *a: True)
        memo = routes_module._feed_request_stamped_at
        memo.clear()
        for i in range(routes_module._FEED_MEMO_PRUNE_AT):
            memo[f'old-{i}'] = -10_000_000.0
        with app.test_request_context('/'):
            routes_module._note_feed_request('fresh')
        assert list(memo) == ['fresh']


class TestQueuedBeforeDormancy:
    def _enqueue(self, slug, episode_id='ep-wait'):
        podcast_id = main_db.get_podcast_row(slug)['id']
        with main_db.transaction(immediate=True) as conn:
            conn.execute(
                """INSERT INTO auto_process_queue
                   (podcast_id, episode_id, original_url, title, status, priority, created_at)
                   VALUES (?, ?, 'https://example.com/e.mp3', 'Ep', 'pending', 0, datetime('now'))""",
                (podcast_id, episode_id))

    def _pending(self, slug):
        return main_db.get_connection().execute(
            """SELECT status FROM auto_process_queue q JOIN podcasts p ON p.id = q.podcast_id
               WHERE p.slug = ?""", (slug,)).fetchall()

    def test_row_waits_while_dormant_and_is_claimed_after_the_feed_wakes(self, feed, dormancy):
        self._enqueue(feed)
        _set_columns(feed, last_feed_request_at=_days_ago(31))
        assert main_db.claim_next_queued_episode() is None
        assert [r['status'] for r in self._pending(feed)] == ['pending']

        assert main_db.touch_feed_request(feed, 0) is True
        claimed = main_db.claim_next_queued_episode()
        assert claimed['podcast_slug'] == feed and claimed['episode_id'] == 'ep-wait'

    def test_override_and_user_reprocess_still_claim_while_dormant(self, feed, dormancy):
        self._enqueue(feed)
        _set_columns(feed, last_feed_request_at=_days_ago(31), auto_process_override='true')
        assert main_db.claim_next_queued_episode()['episode_id'] == 'ep-wait'
