"""Integration tests for PATCH/GET of the per-feed description override.."""
import os
import sys

from tests.integration.override_helpers import _csrf

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))


def test_set_get_and_clear_description_override(app_client, seeded_feed, _auth):
    slug = seeded_feed['slug']
    hdr = _csrf(app_client)

    r = app_client.patch(f'/api/v1/feeds/{slug}',
                         json={'descriptionOverride': '  Line one\n\nLine   two  '}, headers=hdr)
    assert r.status_code == 200
    body = app_client.get(f'/api/v1/feeds/{slug}').get_json()
    assert body['descriptionOverride'] == 'Line one\n\nLine two'

    r = app_client.patch(f'/api/v1/feeds/{slug}', json={'descriptionOverride': None}, headers=hdr)
    assert r.status_code == 200
    assert app_client.get(f'/api/v1/feeds/{slug}').get_json()['descriptionOverride'] is None


def test_blank_description_override_clears(app_client, seeded_feed, _auth):
    slug = seeded_feed['slug']
    hdr = _csrf(app_client)
    app_client.patch(f'/api/v1/feeds/{slug}', json={'descriptionOverride': 'x'}, headers=hdr)
    r = app_client.patch(f'/api/v1/feeds/{slug}', json={'descriptionOverride': '  \n '}, headers=hdr)
    assert r.status_code == 200
    assert app_client.get(f'/api/v1/feeds/{slug}').get_json()['descriptionOverride'] is None


def test_rejects_oversized_and_non_string(app_client, seeded_feed, _auth):
    slug = seeded_feed['slug']
    hdr = _csrf(app_client)
    assert app_client.patch(f'/api/v1/feeds/{slug}',
                            json={'descriptionOverride': 'x' * 10001}, headers=hdr).status_code == 400
    assert app_client.patch(f'/api/v1/feeds/{slug}',
                            json={'descriptionOverride': 'x' * 10000}, headers=hdr).status_code == 200
    assert app_client.patch(f'/api/v1/feeds/{slug}',
                            json={'descriptionOverride': 123}, headers=hdr).status_code == 400


def test_strips_control_chars_and_keeps_newlines(app_client, seeded_feed, _auth):
    slug = seeded_feed['slug']
    hdr = _csrf(app_client)
    r = app_client.patch(f'/api/v1/feeds/{slug}',
                         json={'descriptionOverride': 'A\x01B\nC'}, headers=hdr)
    assert r.status_code == 200
    assert app_client.get(f'/api/v1/feeds/{slug}').get_json()['descriptionOverride'] == 'AB\nC'


def test_description_change_regenerates_served_feed(app_client, seeded_feed, _auth):
    slug, db = seeded_feed['slug'], seeded_feed['db']
    hdr = _csrf(app_client)
    db.update_podcast(slug, etag='"cached"', last_modified_header='Mon, 01 Jan 2026 00:00:00 GMT')
    r = app_client.patch(f'/api/v1/feeds/{slug}', json={'descriptionOverride': 'New text'}, headers=hdr)
    assert r.status_code == 200
    assert db.get_podcast_by_slug(slug)['etag'] is None
