"""Integration tests for PATCH/GET of the per-feed title override (#375)."""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))


@pytest.fixture
def _auth(monkeypatch):
    monkeypatch.delenv('ADMIN_PASSWORD', raising=False)
    yield


@pytest.fixture
def seeded_feed(app_client):
    from api import get_database
    from werkzeug.security import generate_password_hash
    db = get_database()
    # Feed-mutating endpoints require an app password to be configured.
    db.set_setting('app_password', generate_password_hash('TitleTest123!', method='scrypt'))
    slug = 'title-feed'
    db.create_podcast(slug, 'https://example.com/feed.xml', title='Source Show')
    yield {'slug': slug, 'db': db}
    try:
        db.delete_podcast(slug)
    finally:
        # Restore the no-password state so this doesn't leak into other
        # integration tests that share the app_client database.
        db.set_setting('app_password', '')


def _csrf(app_client):
    """Authenticate the session and mint the double-submit CSRF token header."""
    with app_client.session_transaction() as sess:
        sess['authenticated'] = True
    app_client.get('/api/v1/auth/status')
    cookie = app_client.get_cookie('minuspod_csrf')
    return {'X-CSRF-Token': cookie.value} if cookie else {}


def test_set_get_and_clear_title_override(app_client, seeded_feed, _auth):
    slug = seeded_feed['slug']
    hdr = _csrf(app_client)

    r = app_client.patch(f'/api/v1/feeds/{slug}', json={'titleOverride': '  My Show (MP)  '}, headers=hdr)
    assert r.status_code == 200

    body = app_client.get(f'/api/v1/feeds/{slug}').get_json()
    assert body['title'] == 'Source Show'          # source preserved
    assert body['titleOverride'] == 'My Show (MP)'  # trimmed and stored

    # clearing with null falls back to the source title
    r = app_client.patch(f'/api/v1/feeds/{slug}', json={'titleOverride': None}, headers=hdr)
    assert r.status_code == 200
    assert app_client.get(f'/api/v1/feeds/{slug}').get_json()['titleOverride'] is None


def test_rejects_oversized_and_non_string(app_client, seeded_feed, _auth):
    slug = seeded_feed['slug']
    hdr = _csrf(app_client)
    assert app_client.patch(f'/api/v1/feeds/{slug}',
                            json={'titleOverride': 'x' * 501}, headers=hdr).status_code == 400
    assert app_client.patch(f'/api/v1/feeds/{slug}',
                            json={'titleOverride': 123}, headers=hdr).status_code == 400


def test_sanitizes_control_chars_and_newlines(app_client, seeded_feed, _auth):
    slug = seeded_feed['slug']
    hdr = _csrf(app_client)
    # \x01 is XML-forbidden; the newline must collapse so the served <title>
    # stays a well-formed single line.
    r = app_client.patch(f'/api/v1/feeds/{slug}',
                         json={'titleOverride': 'A\x01B\nC'}, headers=hdr)
    assert r.status_code == 200
    assert app_client.get(f'/api/v1/feeds/{slug}').get_json()['titleOverride'] == 'AB C'


def test_title_change_regenerates_served_feed(app_client, seeded_feed, _auth):
    slug, db = seeded_feed['slug'], seeded_feed['db']
    hdr = _csrf(app_client)
    # A stored validator makes the next refresh able to 304; the regen path
    # must clear it so subscribers get the new title, not a cached old one.
    db.update_podcast(slug, etag='"cached"', last_modified_header='Mon, 01 Jan 2026 00:00:00 GMT')
    r = app_client.patch(f'/api/v1/feeds/{slug}', json={'titleOverride': 'Renamed (MP)'}, headers=hdr)
    assert r.status_code == 200
    assert db.get_podcast_by_slug(slug)['etag'] is None
