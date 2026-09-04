"""Quick search: title LIKE across feeds and episodes, all statuses."""
import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))
os.environ.setdefault('MINUSPOD_DATA_DIR', tempfile.mkdtemp(prefix='quick-search-test-'))

from api import get_database


@pytest.fixture
def seeded(app_client):
    db = get_database()
    db.create_podcast('example-podcast', 'https://example.com/feed.xml', 'The Daily Tech Show')
    db.upsert_episode('example-podcast', 'a1b2c3d4e5f6', title='Batteries again', status='pending')
    db.upsert_episode('example-podcast', 'b2c3d4e5f6a1', title='Sodium ion', status='processed')
    yield db
    db.delete_podcast('example-podcast')


def _authed(client):
    with client.session_transaction() as sess:
        sess['authenticated'] = True
    client.get('/api/v1/auth/status')


def test_matches_feed_and_pending_episode(app_client, seeded):
    _authed(app_client)
    r = app_client.get('/api/v1/quick-search?q=tech')
    assert r.status_code == 200
    body = r.get_json()
    assert [f['slug'] for f in body['feeds']] == ['example-podcast']
    r = app_client.get('/api/v1/quick-search?q=batter')
    eps = r.get_json()['episodes']
    assert eps[0]['episodeId'] == 'a1b2c3d4e5f6' and eps[0]['status'] == 'pending'
    assert eps[0]['feedTitle'] == 'The Daily Tech Show'


def test_processed_status_uses_api_alias(app_client, seeded):
    _authed(app_client)
    eps = app_client.get('/api/v1/quick-search?q=sodium').get_json()['episodes']
    assert eps[0]['status'] == 'completed'


def test_short_query_returns_empty_groups(app_client, seeded):
    _authed(app_client)
    body = app_client.get('/api/v1/quick-search?q=t').get_json()
    assert body['feeds'] == [] and body['episodes'] == []


def test_like_wildcards_are_escaped(app_client, seeded):
    _authed(app_client)
    body = app_client.get('/api/v1/quick-search?q=%25').get_json()
    assert body['feeds'] == [] and body['episodes'] == []
