"""Per-feed differential fetch setting + DAI-likelihood hint (Layer 3).

Feeds API:
- PATCH differentialFetchEnabled true/false/null round-trips.
- Non-bool non-null values rejected with 400.
- GET detail surfaces the flag and the daiLikely hint.
Resolver:
- resolve_differential_fetch_enabled reads the column (1/0/NULL -> True/False/False).
"""
import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))
os.environ.setdefault('MINUSPOD_DATA_DIR', tempfile.mkdtemp(prefix='diff-fetch-test-'))


@pytest.fixture
def _auth(monkeypatch):
    monkeypatch.delenv('ADMIN_PASSWORD', raising=False)
    yield


@pytest.fixture
def seeded_feed(app_client):
    from api import get_database
    from werkzeug.security import generate_password_hash
    db = get_database()
    db.set_setting('app_password', generate_password_hash('DiffTest123!', method='scrypt'))
    slug = 'diff-fetch-feed'
    db.create_podcast(slug, 'https://example.com/feed.xml', title='Diff Fetch Test')
    yield {'slug': slug, 'db': db}
    try:
        db.delete_podcast(slug)
    finally:
        db.set_setting('app_password', '')


def _csrf(app_client):
    with app_client.session_transaction() as sess:
        sess['authenticated'] = True
    app_client.get('/api/v1/auth/status')
    cookie = app_client.get_cookie('minuspod_csrf')
    return {'X-CSRF-Token': cookie.value} if cookie else {}


def test_patch_flag_true(app_client, seeded_feed, _auth):
    slug = seeded_feed['slug']
    r = app_client.patch(f'/api/v1/feeds/{slug}',
                         json={'differentialFetchEnabled': True},
                         headers=_csrf(app_client))
    assert r.status_code == 200
    assert r.get_json()['differentialFetchEnabled'] is True


def test_patch_flag_null_clears(app_client, seeded_feed, _auth):
    slug = seeded_feed['slug']
    hdr = _csrf(app_client)
    app_client.patch(f'/api/v1/feeds/{slug}',
                     json={'differentialFetchEnabled': True}, headers=hdr)
    r = app_client.patch(f'/api/v1/feeds/{slug}',
                         json={'differentialFetchEnabled': None}, headers=hdr)
    assert r.status_code == 200
    assert r.get_json()['differentialFetchEnabled'] is None


def test_patch_flag_non_bool_rejected(app_client, seeded_feed, _auth):
    slug = seeded_feed['slug']
    r = app_client.patch(f'/api/v1/feeds/{slug}',
                         json={'differentialFetchEnabled': 'yes'},
                         headers=_csrf(app_client))
    assert r.status_code == 400


def test_get_detail_surfaces_flag_and_dai_hint(app_client, seeded_feed, _auth):
    slug = seeded_feed['slug']
    db = seeded_feed['db']
    db.upsert_episode(slug, 'ep-dai',
                      original_url='https://traffic.megaphone.fm/EP1.mp3',
                      title='DAI episode')
    _csrf(app_client)
    r = app_client.get(f'/api/v1/feeds/{slug}')
    assert r.status_code == 200
    body = r.get_json()
    assert body['differentialFetchEnabled'] is None
    assert body['daiLikely'] is True


def test_dai_hint_false_for_plain_cdn(app_client, seeded_feed, _auth):
    slug = seeded_feed['slug']
    db = seeded_feed['db']
    db.upsert_episode(slug, 'ep-plain',
                      original_url='https://cdn.example.com/EP1.mp3',
                      title='Plain episode')
    _csrf(app_client)
    r = app_client.get(f'/api/v1/feeds/{slug}')
    assert r.get_json()['daiLikely'] is False


def test_resolver_reads_column_tristate(seeded_feed):
    """NULL means unset (auto), 1/0 are explicit -- the pipeline gate needs
    all three states (#519)."""
    from config import resolve_differential_fetch_setting
    db = seeded_feed['db']
    slug = seeded_feed['slug']
    podcast_id = db.get_podcast_by_slug(slug)['id']
    assert resolve_differential_fetch_setting(db, podcast_id) is None
    db.update_podcast(slug, differential_fetch_enabled=1)
    assert resolve_differential_fetch_setting(db, podcast_id) is True
    db.update_podcast(slug, differential_fetch_enabled=0)
    assert resolve_differential_fetch_setting(db, podcast_id) is False
