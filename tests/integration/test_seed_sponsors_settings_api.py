"""Integration tests for the seed-sponsors toggle settings API exposure
and PUT persistence (hushpod adoption)."""
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))
os.environ.setdefault('MINUSPOD_DATA_DIR', tempfile.mkdtemp(prefix='ss-api-test-'))


def _authed(client):
    with client.session_transaction() as sess:
        sess['authenticated'] = True
    client.get('/api/v1/auth/status')


def _csrf_headers(client):
    csrf = None
    for cookie in client._cookies.values():
        if cookie.key == 'minuspod_csrf':
            csrf = cookie.value
    return {'X-CSRF-Token': csrf} if csrf else {}


def test_seed_sponsors_defaults_true(app_client):
    _authed(app_client)
    data = app_client.get('/api/v1/settings').get_json()
    for key in ('seedSponsorsDetection', 'seedSponsorsVerification',
                'seedSponsorsReviewer', 'seedSponsorsResurrect'):
        assert data[key]['value'] is True


def test_seed_sponsors_roundtrip(app_client):
    _authed(app_client)
    resp = app_client.put('/api/v1/settings/ad-detection',
                          json={'seedSponsorsReviewer': False},
                          headers=_csrf_headers(app_client))
    assert resp.status_code == 200
    data = app_client.get('/api/v1/settings').get_json()
    assert data['seedSponsorsReviewer']['value'] is False
    assert data['seedSponsorsDetection']['value'] is True


def test_text_recurrence_hints_default_false(app_client):
    _authed(app_client)
    data = app_client.get('/api/v1/settings').get_json()
    assert data['textRecurrenceHints']['value'] is False


def test_text_recurrence_hints_roundtrip(app_client):
    _authed(app_client)
    resp = app_client.put('/api/v1/settings/ad-detection',
                          json={'textRecurrenceHints': True},
                          headers=_csrf_headers(app_client))
    assert resp.status_code == 200
    data = app_client.get('/api/v1/settings').get_json()
    assert data['textRecurrenceHints']['value'] is True
