"""Integration tests for per-feed held-for-review settings (Phase C, C1).

Covers maxAdDurationOverride and cueGatedApproval:
- PATCH accepts valid values and null (clears).
- PATCH rejects out-of-range / wrong-type values with 400.
- GET /feeds and GET /feeds/<slug> both surface the fields.
- Round-trip: set then read back.

Mirrors test_feed_cue_settings_endpoint.py (Phase A) in structure.
"""
import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))
os.environ.setdefault('MINUSPOD_DATA_DIR', tempfile.mkdtemp(prefix='held-review-test-'))


@pytest.fixture
def _auth(monkeypatch):
    monkeypatch.delenv('ADMIN_PASSWORD', raising=False)
    yield


@pytest.fixture
def seeded_feed(app_client):
    from api import get_database
    from werkzeug.security import generate_password_hash
    db = get_database()
    db.set_setting('app_password', generate_password_hash('HeldTest123!', method='scrypt'))
    slug = 'held-review-test-feed'
    db.create_podcast(slug, 'https://example.com/held.xml', title='Held Review Test')
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


# ---- maxAdDurationOverride ----

def test_patch_max_ad_duration_sets_value(app_client, seeded_feed, _auth):
    slug = seeded_feed['slug']
    hdr = _csrf(app_client)
    r = app_client.patch(f'/api/v1/feeds/{slug}',
                         json={'maxAdDurationOverride': 240.0}, headers=hdr)
    assert r.status_code == 200
    assert r.get_json()['maxAdDurationOverride'] == pytest.approx(240.0)


def test_patch_max_ad_duration_null_clears(app_client, seeded_feed, _auth):
    slug = seeded_feed['slug']
    hdr = _csrf(app_client)
    app_client.patch(f'/api/v1/feeds/{slug}',
                     json={'maxAdDurationOverride': 180.0}, headers=hdr)
    r = app_client.patch(f'/api/v1/feeds/{slug}',
                         json={'maxAdDurationOverride': None}, headers=hdr)
    assert r.status_code == 200
    assert r.get_json()['maxAdDurationOverride'] is None


def test_patch_max_ad_duration_rejects_below_minimum(app_client, seeded_feed, _auth):
    slug = seeded_feed['slug']
    hdr = _csrf(app_client)
    r = app_client.patch(f'/api/v1/feeds/{slug}',
                         json={'maxAdDurationOverride': 0.5}, headers=hdr)
    assert r.status_code == 400


def test_patch_max_ad_duration_rejects_above_maximum(app_client, seeded_feed, _auth):
    slug = seeded_feed['slug']
    hdr = _csrf(app_client)
    r = app_client.patch(f'/api/v1/feeds/{slug}',
                         json={'maxAdDurationOverride': 3601.0}, headers=hdr)
    assert r.status_code == 400


def test_patch_max_ad_duration_rejects_bool(app_client, seeded_feed, _auth):
    slug = seeded_feed['slug']
    hdr = _csrf(app_client)
    r = app_client.patch(f'/api/v1/feeds/{slug}',
                         json={'maxAdDurationOverride': True}, headers=hdr)
    assert r.status_code == 400


def test_patch_max_ad_duration_empty_string_clears(app_client, seeded_feed, _auth):
    slug = seeded_feed['slug']
    hdr = _csrf(app_client)
    app_client.patch(f'/api/v1/feeds/{slug}',
                     json={'maxAdDurationOverride': 300.0}, headers=hdr)
    r = app_client.patch(f'/api/v1/feeds/{slug}',
                         json={'maxAdDurationOverride': ''}, headers=hdr)
    assert r.status_code == 200
    assert r.get_json()['maxAdDurationOverride'] is None


def test_patch_max_ad_duration_accepts_minimum_boundary(app_client, seeded_feed, _auth):
    slug = seeded_feed['slug']
    hdr = _csrf(app_client)
    r = app_client.patch(f'/api/v1/feeds/{slug}',
                         json={'maxAdDurationOverride': 1.0}, headers=hdr)
    assert r.status_code == 200
    assert r.get_json()['maxAdDurationOverride'] == pytest.approx(1.0)


def test_patch_max_ad_duration_accepts_maximum_boundary(app_client, seeded_feed, _auth):
    slug = seeded_feed['slug']
    hdr = _csrf(app_client)
    r = app_client.patch(f'/api/v1/feeds/{slug}',
                         json={'maxAdDurationOverride': 3600.0}, headers=hdr)
    assert r.status_code == 200
    assert r.get_json()['maxAdDurationOverride'] == pytest.approx(3600.0)


# ---- cueGatedApproval ----

def test_patch_cue_gated_approval_true(app_client, seeded_feed, _auth):
    slug = seeded_feed['slug']
    hdr = _csrf(app_client)
    r = app_client.patch(f'/api/v1/feeds/{slug}',
                         json={'cueGatedApproval': True}, headers=hdr)
    assert r.status_code == 200
    assert r.get_json()['cueGatedApproval'] is True


def test_patch_cue_gated_approval_false(app_client, seeded_feed, _auth):
    slug = seeded_feed['slug']
    hdr = _csrf(app_client)
    r = app_client.patch(f'/api/v1/feeds/{slug}',
                         json={'cueGatedApproval': False}, headers=hdr)
    assert r.status_code == 200
    assert r.get_json()['cueGatedApproval'] is False


def test_patch_cue_gated_approval_null_clears(app_client, seeded_feed, _auth):
    slug = seeded_feed['slug']
    hdr = _csrf(app_client)
    app_client.patch(f'/api/v1/feeds/{slug}',
                     json={'cueGatedApproval': True}, headers=hdr)
    r = app_client.patch(f'/api/v1/feeds/{slug}',
                         json={'cueGatedApproval': None}, headers=hdr)
    assert r.status_code == 200
    assert r.get_json()['cueGatedApproval'] is None


def test_patch_cue_gated_approval_rejects_non_bool(app_client, seeded_feed, _auth):
    slug = seeded_feed['slug']
    hdr = _csrf(app_client)
    r = app_client.patch(f'/api/v1/feeds/{slug}',
                         json={'cueGatedApproval': 'yes'}, headers=hdr)
    assert r.status_code == 400


# ---- GET round-trips ----

def test_get_feed_surfaces_both_fields(app_client, seeded_feed, _auth):
    slug = seeded_feed['slug']
    hdr = _csrf(app_client)
    app_client.patch(f'/api/v1/feeds/{slug}',
                     json={'maxAdDurationOverride': 300.0, 'cueGatedApproval': True},
                     headers=hdr)
    r = app_client.get(f'/api/v1/feeds/{slug}')
    body = r.get_json()
    assert body['maxAdDurationOverride'] == pytest.approx(300.0)
    assert body['cueGatedApproval'] is True


def test_list_feeds_surfaces_both_fields(app_client, seeded_feed, _auth):
    slug = seeded_feed['slug']
    hdr = _csrf(app_client)
    app_client.patch(f'/api/v1/feeds/{slug}',
                     json={'maxAdDurationOverride': 180.0, 'cueGatedApproval': True},
                     headers=hdr)
    feeds = app_client.get('/api/v1/feeds').get_json()['feeds']
    match = next((f for f in feeds if f['slug'] == slug), None)
    assert match is not None
    assert match['maxAdDurationOverride'] == pytest.approx(180.0)
    assert match['cueGatedApproval'] is True


def test_new_feed_defaults_both_fields_null(app_client, seeded_feed, _auth):
    _csrf(app_client)  # sets session authenticated
    r = app_client.get(f'/api/v1/feeds/{seeded_feed["slug"]}')
    body = r.get_json()
    assert 'maxAdDurationOverride' in body
    assert body['maxAdDurationOverride'] is None
    assert 'cueGatedApproval' in body
    # cue_gated_approval DEFAULT 0 -> deserializes to False (not None); both read as off
    assert body['cueGatedApproval'] is False or body['cueGatedApproval'] is None
