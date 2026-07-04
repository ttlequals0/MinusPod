"""Integration tests for boundary-snap settings plumbing (Phase B, task B1).

Feeds API:
- PATCH silenceSnapEnabled / transitionSnapEnabled with true/false/null.
- Non-bool non-null values are rejected with 400.
- GET detail and GET list surface both flags.

Settings API:
- The three silence-snap tunables round-trip through PUT + GET.
- Out-of-range values (incl. positive noise dB) are rejected with 400.
- Defaults are present in GET (values and defaults block).
"""
import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))
os.environ.setdefault('MINUSPOD_DATA_DIR', tempfile.mkdtemp(prefix='snap-settings-test-'))


@pytest.fixture
def _auth(monkeypatch):
    monkeypatch.delenv('ADMIN_PASSWORD', raising=False)
    yield


@pytest.fixture
def seeded_feed(app_client):
    from api import get_database
    from werkzeug.security import generate_password_hash
    db = get_database()
    db.set_setting('app_password', generate_password_hash('SnapTest123!', method='scrypt'))
    slug = 'snap-flags-feed'
    db.create_podcast(slug, 'https://example.com/feed.xml', title='Snap Flags Test')
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


# -- Feed flags: PATCH --

@pytest.mark.parametrize('field', ['silenceSnapEnabled', 'transitionSnapEnabled'])
def test_patch_flag_true(app_client, seeded_feed, _auth, field):
    slug = seeded_feed['slug']
    hdr = _csrf(app_client)
    r = app_client.patch(f'/api/v1/feeds/{slug}', json={field: True}, headers=hdr)
    assert r.status_code == 200
    assert r.get_json()[field] is True


@pytest.mark.parametrize('field', ['silenceSnapEnabled', 'transitionSnapEnabled'])
def test_patch_flag_false(app_client, seeded_feed, _auth, field):
    slug = seeded_feed['slug']
    hdr = _csrf(app_client)
    r = app_client.patch(f'/api/v1/feeds/{slug}', json={field: False}, headers=hdr)
    assert r.status_code == 200
    assert r.get_json()[field] is False


@pytest.mark.parametrize('field', ['silenceSnapEnabled', 'transitionSnapEnabled'])
def test_patch_flag_null_clears(app_client, seeded_feed, _auth, field):
    slug = seeded_feed['slug']
    hdr = _csrf(app_client)
    app_client.patch(f'/api/v1/feeds/{slug}', json={field: True}, headers=hdr)
    r = app_client.patch(f'/api/v1/feeds/{slug}', json={field: None}, headers=hdr)
    assert r.status_code == 200
    assert r.get_json()[field] is None


@pytest.mark.parametrize('field', ['silenceSnapEnabled', 'transitionSnapEnabled'])
@pytest.mark.parametrize('bad', ['yes', 1, 0, 1.0, [True], {'on': True}])
def test_patch_flag_rejects_non_bool(app_client, seeded_feed, _auth, field, bad):
    slug = seeded_feed['slug']
    hdr = _csrf(app_client)
    r = app_client.patch(f'/api/v1/feeds/{slug}', json={field: bad}, headers=hdr)
    assert r.status_code == 400


# -- Feed flags: GET --

def test_fresh_feed_flags_are_null(app_client, seeded_feed, _auth):
    slug = seeded_feed['slug']
    _csrf(app_client)
    body = app_client.get(f'/api/v1/feeds/{slug}').get_json()
    assert body['silenceSnapEnabled'] is None
    assert body['transitionSnapEnabled'] is None


def test_get_round_trip_both_flags(app_client, seeded_feed, _auth):
    slug = seeded_feed['slug']
    hdr = _csrf(app_client)
    app_client.patch(f'/api/v1/feeds/{slug}',
                     json={'silenceSnapEnabled': True, 'transitionSnapEnabled': False},
                     headers=hdr)
    body = app_client.get(f'/api/v1/feeds/{slug}').get_json()
    assert body['silenceSnapEnabled'] is True
    assert body['transitionSnapEnabled'] is False


def test_list_feeds_surfaces_flags(app_client, seeded_feed, _auth):
    slug = seeded_feed['slug']
    hdr = _csrf(app_client)
    app_client.patch(f'/api/v1/feeds/{slug}',
                     json={'silenceSnapEnabled': True}, headers=hdr)
    feeds = app_client.get('/api/v1/feeds').get_json()['feeds']
    match = next((f for f in feeds if f['slug'] == slug), None)
    assert match is not None
    assert match['silenceSnapEnabled'] is True
    assert match['transitionSnapEnabled'] is None


# -- Silence-snap global tunables --

def _reset_snap_tunables(app_client):
    from api import get_database
    db = get_database()
    for key in ('silence_snap_noise_db', 'silence_snap_min_duration_seconds',
                'silence_snap_max_distance_seconds'):
        db.reset_setting(key)


def test_snap_tunable_defaults_in_get(app_client, _auth):
    _csrf(app_client)
    g = app_client.get('/api/v1/settings').get_json()
    assert g['silenceSnapNoiseDb']['value'] == pytest.approx(-50.0)
    assert g['silenceSnapMinDurationSeconds']['value'] == pytest.approx(0.3)
    assert g['silenceSnapMaxDistanceSeconds']['value'] == pytest.approx(2.0)
    assert g['defaults']['silenceSnapNoiseDb'] == pytest.approx(-50.0)
    assert g['defaults']['silenceSnapMinDurationSeconds'] == pytest.approx(0.3)
    assert g['defaults']['silenceSnapMaxDistanceSeconds'] == pytest.approx(2.0)


def test_snap_tunables_round_trip(app_client, _auth):
    hdr = _csrf(app_client)
    r = app_client.put('/api/v1/settings/ad-detection', json={
        'silenceSnapNoiseDb': -40.0,
        'silenceSnapMinDurationSeconds': 0.5,
        'silenceSnapMaxDistanceSeconds': 3.0,
    }, headers=hdr)
    assert r.status_code == 200
    g = app_client.get('/api/v1/settings').get_json()
    assert g['silenceSnapNoiseDb']['value'] == pytest.approx(-40.0)
    assert g['silenceSnapMinDurationSeconds']['value'] == pytest.approx(0.5)
    assert g['silenceSnapMaxDistanceSeconds']['value'] == pytest.approx(3.0)
    _reset_snap_tunables(app_client)


@pytest.mark.parametrize('payload', [
    {'silenceSnapNoiseDb': 10.0},     # positive dBFS is invalid
    {'silenceSnapNoiseDb': -10.0},    # above the -20 ceiling
    {'silenceSnapNoiseDb': -95.0},    # below the -90 floor
    {'silenceSnapMinDurationSeconds': 0.05},
    {'silenceSnapMinDurationSeconds': 6.0},
    {'silenceSnapMaxDistanceSeconds': 0.1},
    {'silenceSnapMaxDistanceSeconds': 11.0},
    {'silenceSnapNoiseDb': 'loud'},
])
def test_snap_tunables_reject_out_of_range(app_client, _auth, payload):
    hdr = _csrf(app_client)
    r = app_client.put('/api/v1/settings/ad-detection', json=payload, headers=hdr)
    assert r.status_code == 400


def test_snap_tunable_negative_range_boundaries_accepted(app_client, _auth):
    """The tuple validator must handle the negative-only noise_db range."""
    hdr = _csrf(app_client)
    for val in (-90.0, -20.0):
        r = app_client.put('/api/v1/settings/ad-detection',
                           json={'silenceSnapNoiseDb': val}, headers=hdr)
        assert r.status_code == 200, f'noise_db={val} should be accepted'
    _reset_snap_tunables(app_client)


def test_snap_tunable_bad_value_writes_nothing(app_client, _auth):
    """Validate-then-write: one bad field must not persist the valid ones."""
    hdr = _csrf(app_client)
    r = app_client.put('/api/v1/settings/ad-detection', json={
        'silenceSnapMinDurationSeconds': 0.5,
        'silenceSnapNoiseDb': 5.0,
    }, headers=hdr)
    assert r.status_code == 400
    g = app_client.get('/api/v1/settings').get_json()
    assert g['silenceSnapMinDurationSeconds']['value'] == pytest.approx(0.3)
