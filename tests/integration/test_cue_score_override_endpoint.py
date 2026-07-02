"""Integration tests for per-feed cue template score override (#350 Phase 5).

Covers:
- PATCH accepts a valid value in [0.30, 0.99].
- PATCH rejects a value below 0.30.
- PATCH rejects a non-numeric string.
- PATCH with null clears the override.
- GET round-trip: set then read back.
- GET /feeds list surfaces cueTemplateScoreOverride.
"""
import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))
os.environ.setdefault('MINUSPOD_DATA_DIR', tempfile.mkdtemp(prefix='cue-score-test-'))


@pytest.fixture
def _auth(monkeypatch):
    monkeypatch.delenv('ADMIN_PASSWORD', raising=False)
    yield


@pytest.fixture
def seeded_feed(app_client):
    from api import get_database
    from werkzeug.security import generate_password_hash
    db = get_database()
    db.set_setting('app_password', generate_password_hash('CueTest123!', method='scrypt'))
    slug = 'cue-score-feed'
    db.create_podcast(slug, 'https://example.com/feed.xml', title='Cue Score Test')
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


def test_patch_accepts_valid_value(app_client, seeded_feed, _auth):
    slug = seeded_feed['slug']
    hdr = _csrf(app_client)
    r = app_client.patch(f'/api/v1/feeds/{slug}',
                         json={'cueTemplateScoreOverride': 0.5}, headers=hdr)
    assert r.status_code == 200
    assert r.get_json()['cueTemplateScoreOverride'] == pytest.approx(0.5)


def test_patch_rejects_below_floor(app_client, seeded_feed, _auth):
    slug = seeded_feed['slug']
    hdr = _csrf(app_client)
    r = app_client.patch(f'/api/v1/feeds/{slug}',
                         json={'cueTemplateScoreOverride': 0.2}, headers=hdr)
    assert r.status_code == 400


def test_patch_rejects_non_numeric(app_client, seeded_feed, _auth):
    slug = seeded_feed['slug']
    hdr = _csrf(app_client)
    r = app_client.patch(f'/api/v1/feeds/{slug}',
                         json={'cueTemplateScoreOverride': 'abc'}, headers=hdr)
    assert r.status_code == 400


def test_patch_null_clears_override(app_client, seeded_feed, _auth):
    slug = seeded_feed['slug']
    hdr = _csrf(app_client)
    # First set a value.
    app_client.patch(f'/api/v1/feeds/{slug}',
                     json={'cueTemplateScoreOverride': 0.7}, headers=hdr)
    # Then clear it.
    r = app_client.patch(f'/api/v1/feeds/{slug}',
                         json={'cueTemplateScoreOverride': None}, headers=hdr)
    assert r.status_code == 200
    assert r.get_json()['cueTemplateScoreOverride'] is None


def test_get_round_trip(app_client, seeded_feed, _auth):
    slug = seeded_feed['slug']
    hdr = _csrf(app_client)
    app_client.patch(f'/api/v1/feeds/{slug}',
                     json={'cueTemplateScoreOverride': 0.68}, headers=hdr)
    body = app_client.get(f'/api/v1/feeds/{slug}').get_json()
    assert body['cueTemplateScoreOverride'] == pytest.approx(0.68)


def test_list_feeds_surfaces_override(app_client, seeded_feed, _auth):
    slug = seeded_feed['slug']
    hdr = _csrf(app_client)
    app_client.patch(f'/api/v1/feeds/{slug}',
                     json={'cueTemplateScoreOverride': 0.55}, headers=hdr)
    feeds = app_client.get('/api/v1/feeds').get_json()['feeds']
    match = next((f for f in feeds if f['slug'] == slug), None)
    assert match is not None
    assert match['cueTemplateScoreOverride'] == pytest.approx(0.55)
