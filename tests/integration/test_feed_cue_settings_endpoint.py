"""Integration tests for per-feed cue settings overrides (Task A2).

Covers all 7 knobs:
- PATCH accepts valid values and null (clears).
- PATCH rejects out-of-range values with 400.
- GET round-trip: set then read back.
- GET /feeds list surfaces the fields.
"""
import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))
os.environ.setdefault('MINUSPOD_DATA_DIR', tempfile.mkdtemp(prefix='feed-cue-test-'))


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
    slug = 'cue-settings-feed'
    db.create_podcast(slug, 'https://example.com/feed.xml', title='Cue Settings Test')
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


# -- cueCreateFromPairsOverride --

def test_patch_create_from_pairs_true(app_client, seeded_feed, _auth):
    slug = seeded_feed['slug']
    hdr = _csrf(app_client)
    r = app_client.patch(f'/api/v1/feeds/{slug}',
                         json={'cueCreateFromPairsOverride': True}, headers=hdr)
    assert r.status_code == 200
    assert r.get_json()['cueCreateFromPairsOverride'] is True


def test_patch_create_from_pairs_false(app_client, seeded_feed, _auth):
    slug = seeded_feed['slug']
    hdr = _csrf(app_client)
    r = app_client.patch(f'/api/v1/feeds/{slug}',
                         json={'cueCreateFromPairsOverride': False}, headers=hdr)
    assert r.status_code == 200
    assert r.get_json()['cueCreateFromPairsOverride'] is False


def test_patch_create_from_pairs_null_clears(app_client, seeded_feed, _auth):
    slug = seeded_feed['slug']
    hdr = _csrf(app_client)
    app_client.patch(f'/api/v1/feeds/{slug}',
                     json={'cueCreateFromPairsOverride': True}, headers=hdr)
    r = app_client.patch(f'/api/v1/feeds/{slug}',
                         json={'cueCreateFromPairsOverride': None}, headers=hdr)
    assert r.status_code == 200
    assert r.get_json()['cueCreateFromPairsOverride'] is None


def test_patch_create_from_pairs_invalid_string(app_client, seeded_feed, _auth):
    slug = seeded_feed['slug']
    hdr = _csrf(app_client)
    r = app_client.patch(f'/api/v1/feeds/{slug}',
                         json={'cueCreateFromPairsOverride': 'yes'}, headers=hdr)
    assert r.status_code == 400


# -- cuePairMinBreakOverride --

def test_patch_pair_min_break_valid(app_client, seeded_feed, _auth):
    slug = seeded_feed['slug']
    hdr = _csrf(app_client)
    r = app_client.patch(f'/api/v1/feeds/{slug}',
                         json={'cuePairMinBreakOverride': 20.0}, headers=hdr)
    assert r.status_code == 200
    assert r.get_json()['cuePairMinBreakOverride'] == pytest.approx(20.0)


def test_patch_pair_min_break_rejects_below_floor(app_client, seeded_feed, _auth):
    slug = seeded_feed['slug']
    hdr = _csrf(app_client)
    r = app_client.patch(f'/api/v1/feeds/{slug}',
                         json={'cuePairMinBreakOverride': 0.5}, headers=hdr)
    assert r.status_code == 400


def test_patch_pair_min_break_null_clears(app_client, seeded_feed, _auth):
    slug = seeded_feed['slug']
    hdr = _csrf(app_client)
    app_client.patch(f'/api/v1/feeds/{slug}',
                     json={'cuePairMinBreakOverride': 20.0}, headers=hdr)
    r = app_client.patch(f'/api/v1/feeds/{slug}',
                         json={'cuePairMinBreakOverride': None}, headers=hdr)
    assert r.status_code == 200
    assert r.get_json()['cuePairMinBreakOverride'] is None


# -- cuePairMaxBreakOverride --

def test_patch_pair_max_break_valid(app_client, seeded_feed, _auth):
    slug = seeded_feed['slug']
    hdr = _csrf(app_client)
    r = app_client.patch(f'/api/v1/feeds/{slug}',
                         json={'cuePairMaxBreakOverride': 300.0}, headers=hdr)
    assert r.status_code == 200
    assert r.get_json()['cuePairMaxBreakOverride'] == pytest.approx(300.0)


def test_patch_pair_max_break_rejects_above_ceiling(app_client, seeded_feed, _auth):
    slug = seeded_feed['slug']
    hdr = _csrf(app_client)
    r = app_client.patch(f'/api/v1/feeds/{slug}',
                         json={'cuePairMaxBreakOverride': 9999.0}, headers=hdr)
    assert r.status_code == 400


# -- cuePairMaxBreakFractionOverride --

def test_patch_pair_max_break_fraction_valid(app_client, seeded_feed, _auth):
    slug = seeded_feed['slug']
    hdr = _csrf(app_client)
    r = app_client.patch(f'/api/v1/feeds/{slug}',
                         json={'cuePairMaxBreakFractionOverride': 0.3}, headers=hdr)
    assert r.status_code == 200
    assert r.get_json()['cuePairMaxBreakFractionOverride'] == pytest.approx(0.3)


def test_patch_pair_max_break_fraction_rejects_above_1(app_client, seeded_feed, _auth):
    slug = seeded_feed['slug']
    hdr = _csrf(app_client)
    r = app_client.patch(f'/api/v1/feeds/{slug}',
                         json={'cuePairMaxBreakFractionOverride': 1.5}, headers=hdr)
    assert r.status_code == 400


# -- cueSnapConfidenceOverride --

def test_patch_snap_confidence_valid(app_client, seeded_feed, _auth):
    slug = seeded_feed['slug']
    hdr = _csrf(app_client)
    r = app_client.patch(f'/api/v1/feeds/{slug}',
                         json={'cueSnapConfidenceOverride': 0.75}, headers=hdr)
    assert r.status_code == 200
    assert r.get_json()['cueSnapConfidenceOverride'] == pytest.approx(0.75)


def test_patch_snap_confidence_rejects_above_1(app_client, seeded_feed, _auth):
    slug = seeded_feed['slug']
    hdr = _csrf(app_client)
    r = app_client.patch(f'/api/v1/feeds/{slug}',
                         json={'cueSnapConfidenceOverride': 1.5}, headers=hdr)
    assert r.status_code == 400


# -- cueSnapLeadOverride --

def test_patch_snap_lead_valid(app_client, seeded_feed, _auth):
    slug = seeded_feed['slug']
    hdr = _csrf(app_client)
    r = app_client.patch(f'/api/v1/feeds/{slug}',
                         json={'cueSnapLeadOverride': 5.0}, headers=hdr)
    assert r.status_code == 200
    assert r.get_json()['cueSnapLeadOverride'] == pytest.approx(5.0)


def test_patch_snap_lead_rejects_below_floor(app_client, seeded_feed, _auth):
    slug = seeded_feed['slug']
    hdr = _csrf(app_client)
    r = app_client.patch(f'/api/v1/feeds/{slug}',
                         json={'cueSnapLeadOverride': 0.1}, headers=hdr)
    assert r.status_code == 400


def test_patch_snap_lead_rejects_above_ceiling(app_client, seeded_feed, _auth):
    slug = seeded_feed['slug']
    hdr = _csrf(app_client)
    r = app_client.patch(f'/api/v1/feeds/{slug}',
                         json={'cueSnapLeadOverride': 99.0}, headers=hdr)
    assert r.status_code == 400


# -- cueSnapLagOverride --

def test_patch_snap_lag_valid(app_client, seeded_feed, _auth):
    slug = seeded_feed['slug']
    hdr = _csrf(app_client)
    r = app_client.patch(f'/api/v1/feeds/{slug}',
                         json={'cueSnapLagOverride': 3.0}, headers=hdr)
    assert r.status_code == 200
    assert r.get_json()['cueSnapLagOverride'] == pytest.approx(3.0)


def test_patch_snap_lag_null_clears(app_client, seeded_feed, _auth):
    slug = seeded_feed['slug']
    hdr = _csrf(app_client)
    app_client.patch(f'/api/v1/feeds/{slug}',
                     json={'cueSnapLagOverride': 3.0}, headers=hdr)
    r = app_client.patch(f'/api/v1/feeds/{slug}',
                         json={'cueSnapLagOverride': None}, headers=hdr)
    assert r.status_code == 200
    assert r.get_json()['cueSnapLagOverride'] is None


# -- GET round-trip --

def test_get_round_trip_all_fields(app_client, seeded_feed, _auth):
    slug = seeded_feed['slug']
    hdr = _csrf(app_client)
    app_client.patch(f'/api/v1/feeds/{slug}', json={
        'cueCreateFromPairsOverride': True,
        'cuePairMinBreakOverride': 25.0,
        'cuePairMaxBreakOverride': 400.0,
        'cuePairMaxBreakFractionOverride': 0.4,
        'cueSnapConfidenceOverride': 0.72,
        'cueSnapLeadOverride': 8.0,
        'cueSnapLagOverride': 3.5,
    }, headers=hdr)
    body = app_client.get(f'/api/v1/feeds/{slug}').get_json()
    assert body['cueCreateFromPairsOverride'] is True
    assert body['cuePairMinBreakOverride'] == pytest.approx(25.0)
    assert body['cuePairMaxBreakOverride'] == pytest.approx(400.0)
    assert body['cuePairMaxBreakFractionOverride'] == pytest.approx(0.4)
    assert body['cueSnapConfidenceOverride'] == pytest.approx(0.72)
    assert body['cueSnapLeadOverride'] == pytest.approx(8.0)
    assert body['cueSnapLagOverride'] == pytest.approx(3.5)


# -- GET /feeds list --

def test_list_feeds_surfaces_overrides(app_client, seeded_feed, _auth):
    slug = seeded_feed['slug']
    hdr = _csrf(app_client)
    app_client.patch(f'/api/v1/feeds/{slug}',
                     json={'cueSnapConfidenceOverride': 0.65}, headers=hdr)
    feeds = app_client.get('/api/v1/feeds').get_json()['feeds']
    match = next((f for f in feeds if f['slug'] == slug), None)
    assert match is not None
    assert match['cueSnapConfidenceOverride'] == pytest.approx(0.65)
