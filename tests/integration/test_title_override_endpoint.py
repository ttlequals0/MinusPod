"""Integration tests for PATCH/GET of the per-feed title override (#375)."""
import os
import sys

from tests.integration.override_helpers import _csrf

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))


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
