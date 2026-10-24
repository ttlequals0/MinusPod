"""Auto-process window: resolver, settings validation and feed override round trip."""
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))
os.environ.setdefault('MINUSPOD_DATA_DIR', tempfile.mkdtemp(prefix='auto-process-age-test-'))

import pytest

from api import get_database


def _authed(client):
    with client.session_transaction() as sess:
        sess['authenticated'] = True
    client.get('/api/v1/auth/status')


def _csrf_headers(client):
    for cookie in client._cookies.values():
        if cookie.key == 'minuspod_csrf':
            return {'X-CSRF-Token': cookie.value}
    return {}


@pytest.fixture
def slug():
    db = get_database()
    name = 'example-podcast-age'
    if not db.get_podcast_by_slug(name):
        db.create_podcast(name, 'https://example.com/feed.xml')
    return name


def test_resolver_default_global_and_override(app_client, slug):
    db = get_database()
    db.update_podcast(slug, auto_process_max_age_hours_override=None)
    db.set_setting('auto_process_max_age_hours', '48', is_default=True)
    assert db.get_auto_process_max_age_hours(slug) == 48

    db.set_setting('auto_process_max_age_hours', '240', is_default=False)
    assert db.get_auto_process_max_age_hours(slug) == 240

    db.update_podcast(slug, auto_process_max_age_hours_override=72)
    assert db.get_auto_process_max_age_hours(slug) == 72
    podcast = db.get_podcast_by_slug(slug)
    assert db.get_auto_process_max_age_hours(slug, podcast=podcast) == 72

    db.update_podcast(slug, auto_process_max_age_hours_override=None)
    assert db.get_auto_process_max_age_hours(slug) == 240


def test_resolver_falls_back_when_setting_missing(app_client, slug):
    db = get_database()
    db.update_podcast(slug, auto_process_max_age_hours_override=None)
    conn = db.get_connection()
    conn.execute("DELETE FROM settings WHERE key = 'auto_process_max_age_hours'")
    conn.commit()
    assert db.get_auto_process_max_age_hours(slug) == 48


@pytest.mark.parametrize('value', [0, 721, -1, 1.5, '48', True, None])
def test_settings_put_rejects_out_of_range(app_client, value):
    _authed(app_client)
    resp = app_client.put('/api/v1/settings/ad-detection',
                          json={'autoProcessMaxAgeHours': value},
                          headers=_csrf_headers(app_client))
    assert resp.status_code == 400
    assert 'autoProcessMaxAgeHours' in resp.get_json()['error']


@pytest.mark.parametrize('value', [1, 168, 720])
def test_settings_put_round_trip(app_client, value):
    _authed(app_client)
    resp = app_client.put('/api/v1/settings/ad-detection',
                          json={'autoProcessMaxAgeHours': value},
                          headers=_csrf_headers(app_client))
    assert resp.status_code == 200
    body = app_client.get('/api/v1/settings').get_json()
    assert body['autoProcessMaxAgeHours']['value'] == value
    assert body['defaults']['autoProcessMaxAgeHours'] == 48


def test_feed_override_round_trip_and_validation(app_client, slug):
    _authed(app_client)
    headers = _csrf_headers(app_client)
    url = f'/api/v1/feeds/{slug}'

    resp = app_client.patch(url, json={'autoProcessMaxAgeHoursOverride': 168}, headers=headers)
    assert resp.status_code == 200
    assert app_client.get(url).get_json()['autoProcessMaxAgeHoursOverride'] == 168

    for bad in (0, 721, 'x', 2.5, True):
        resp = app_client.patch(url, json={'autoProcessMaxAgeHoursOverride': bad}, headers=headers)
        assert resp.status_code == 400
    assert app_client.get(url).get_json()['autoProcessMaxAgeHoursOverride'] == 168

    resp = app_client.patch(url, json={'autoProcessMaxAgeHoursOverride': None}, headers=headers)
    assert resp.status_code == 200
    assert app_client.get(url).get_json()['autoProcessMaxAgeHoursOverride'] is None
