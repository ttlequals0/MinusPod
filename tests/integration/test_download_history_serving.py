"""serve_episode records downloads; the history API lists them."""
import json
from unittest.mock import patch

import pytest

from tests.app_bootstrap import bootstrap

bootstrap('download_history_serving_test_', secret_key='download-history-secret')

import database
from main_app import app, db as app_db
import main_app.routes as routes_mod
from utils.constants import EpisodeStatus

SLUG = 'example-podcast'
EPISODE = 'a1b2c3d4e5f6'
KEY = 'e' * 64


@pytest.fixture(autouse=True)
def _bind_db_singleton():
    prev = database.Database._instance
    database.Database._instance = app_db
    yield
    app_db.set_setting('feed_auth_enabled', 'false', is_default=False)
    app_db.set_setting('download_history_enabled', 'true', is_default=False)
    database.Database._instance = prev


@pytest.fixture
def client():
    app_db.set_setting('app_password', '')
    app.config['TESTING'] = True
    with app.test_client() as c:
        yield c


@pytest.fixture
def audio(tmp_path):
    if not app_db.get_podcast_by_slug(SLUG):
        app_db.create_podcast(SLUG, 'https://example.com/feed.xml', 'Example Podcast')
    app_db.upsert_episode(SLUG, EPISODE, original_url='https://example.com/e.mp3',
                          title='Episode One', status=EpisodeStatus.PROCESSED.value)
    path = tmp_path / 'cut.mp3'
    path.write_bytes(b'\x00' * 256)
    with patch.object(routes_mod, '_published_processed_path', return_value=path):
        yield path
    with app_db.transaction(immediate=True) as conn:
        conn.execute('DELETE FROM episode_downloads')
    app_db.delete_podcast(SLUG)


def _downloads():
    return app_db.list_downloads(page=1, limit=10, sort_by='last_seen_at',
                                 sort_dir='desc')['downloads']


def test_cache_hit_records_download(client, audio):
    response = client.get(f'/episodes/{SLUG}/{EPISODE}.mp3',
                          headers={'User-Agent': 'Pocket Casts/7.0'},
                          environ_base={'REMOTE_ADDR': '203.0.113.5'})
    assert response.status_code == 200
    rows = _downloads()
    assert len(rows) == 1
    assert rows[0]['clientIp'] == '203.0.113.5'
    assert rows[0]['userAgent'] == 'Pocket Casts/7.0'
    assert rows[0]['subscriberLabel'] is None


def test_head_does_not_record(client, audio):
    assert client.head(f'/episodes/{SLUG}/{EPISODE}.mp3').status_code == 200
    assert _downloads() == []


def test_subscriber_key_label_recorded(client, audio):
    app_db.set_setting('feed_auth_enabled', 'true', is_default=False)
    app_db.set_setting('feed_auth_key', KEY, is_default=False)
    token = app_db.create_feed_subscriber_key(SLUG, 'Phone')['token']
    assert client.get(f'/episodes/{SLUG}/{EPISODE}.mp3?key={token}').status_code == 200
    assert _downloads()[0]['subscriberLabel'] == 'Phone'


def test_global_key_records_without_label(client, audio):
    app_db.set_setting('feed_auth_enabled', 'true', is_default=False)
    app_db.set_setting('feed_auth_key', KEY, is_default=False)
    assert client.get(f'/episodes/{SLUG}/{EPISODE}.mp3?key={KEY}').status_code == 200
    assert _downloads()[0]['subscriberLabel'] is None


def test_disabled_setting_skips_recording(client, audio):
    app_db.set_setting('download_history_enabled', 'false', is_default=False)
    assert client.get(f'/episodes/{SLUG}/{EPISODE}.mp3').status_code == 200
    assert _downloads() == []


def test_range_request_records_once(client, audio):
    for _ in range(2):
        response = client.get(f'/episodes/{SLUG}/{EPISODE}.mp3',
                              headers={'Range': 'bytes=0-9', 'User-Agent': 'AntennaPod/3.0'})
        assert response.status_code == 206
    rows = _downloads()
    assert len(rows) == 1 and rows[0]['requestCount'] == 1


def test_missing_and_gone_responses_record_nothing(client, audio):
    gone = 'b1b2c3d4e5f6'
    app_db.upsert_episode(SLUG, gone, original_url='https://example.com/g.mp3',
                          status=EpisodeStatus.PERMANENTLY_FAILED.value)
    assert client.get(f'/episodes/{SLUG}/{gone}.mp3').status_code == 410
    assert client.get('/episodes/no-such-feed/a1b2c3d4e5f6.mp3').status_code == 404
    assert _downloads() == []


def test_label_only_for_the_subscriber_key_request(client, audio):
    app_db.set_setting('feed_auth_enabled', 'true', is_default=False)
    app_db.set_setting('feed_auth_key', KEY, is_default=False)
    token = app_db.create_feed_subscriber_key(SLUG, 'Phone')['token']
    client.get(f'/episodes/{SLUG}/{EPISODE}.mp3?key={KEY}', headers={'User-Agent': 'global'})
    client.get(f'/episodes/{SLUG}/{EPISODE}.mp3?key={token}', headers={'User-Agent': 'scoped'})
    labels = {r['userAgent']: r['subscriberLabel'] for r in _downloads()}
    assert labels == {'global': None, 'scoped': 'Phone'}


def test_recording_failure_never_breaks_serving(client, audio):
    with patch.object(app_db, 'record_download', side_effect=RuntimeError('boom')):
        assert client.get(f'/episodes/{SLUG}/{EPISODE}.mp3').status_code == 200


def test_history_api_lists_downloads(client, audio):
    client.get(f'/episodes/{SLUG}/{EPISODE}.mp3',
               headers={'User-Agent': 'AntennaPod/3.0'})
    body = client.get('/api/v1/history/downloads?limit=5&sortBy=client_ip'
                      f'&podcastSlug={SLUG}').get_json()
    assert body['total'] == 1 and body['page'] == 1 and body['limit'] == 5
    assert body['downloads'][0]['podcastSlug'] == SLUG
    assert body['downloads'][0]['episodeTitle'] == 'Episode One'
    assert client.get('/api/v1/history/downloads?podcastSlug=other').get_json()['total'] == 0


def test_history_api_flags_blocked_user_agent(client, audio):
    client.get(f'/episodes/{SLUG}/{EPISODE}.mp3', headers={'User-Agent': 'Pocket Casts/7.0'})
    url = f'/api/v1/history/downloads?podcastSlug={SLUG}'
    try:
        assert client.get(url).get_json()['downloads'][0]['userAgentBlocked'] is False
        app_db.set_setting('jit_blocked_user_agents', json.dumps(['^pocket']), is_default=False)
        assert client.get(url).get_json()['downloads'][0]['userAgentBlocked'] is True
        app_db.set_setting('jit_blocked_user_agents', json.dumps(['^casts']), is_default=False)
        assert client.get(url).get_json()['downloads'][0]['userAgentBlocked'] is False
    finally:
        app_db.set_setting('jit_blocked_user_agents', '[]', is_default=True)


def test_serving_stores_redacted_request(client, audio):
    client.get(f'/episodes/{SLUG}/{EPISODE}.mp3?key=sekret',
               headers={'User-Agent': 'AntennaPod/3.0', 'Range': 'bytes=0-'})
    row = _downloads()[0]
    assert row['lastRequestLine'] == f'GET /episodes/{SLUG}/{EPISODE}.mp3?key=[redacted]'
    assert row['lastRequestHeaders'] == {'Range': 'bytes=0-'}
