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


def test_ad_addressing_mode_default_timestamps(app_client):
    _authed(app_client)
    data = app_client.get('/api/v1/settings').get_json()
    assert data['adAddressingMode']['value'] == 'timestamps'


def test_ad_addressing_mode_roundtrip(app_client):
    _authed(app_client)
    resp = app_client.put('/api/v1/settings/ad-detection',
                          json={'adAddressingMode': 'segment_ids'},
                          headers=_csrf_headers(app_client))
    assert resp.status_code == 200
    data = app_client.get('/api/v1/settings').get_json()
    assert data['adAddressingMode']['value'] == 'segment_ids'


def test_ad_addressing_mode_rejects_bogus_value(app_client):
    _authed(app_client)
    before = app_client.get('/api/v1/settings').get_json()['adAddressingMode']['value']
    resp = app_client.put('/api/v1/settings/ad-detection',
                          json={'adAddressingMode': 'bogus'},
                          headers=_csrf_headers(app_client))
    assert resp.status_code == 400
    data = app_client.get('/api/v1/settings').get_json()
    assert data['adAddressingMode']['value'] == before


def test_ad_addressing_mode_persists_via_apply_processing_flags():
    # adAddressingMode's 400 validation stays in the route, ahead of every
    # phase, but persistence itself now happens inside _apply_processing_flags
    # alongside its sibling flags (item 8). There is no second route-level
    # validated field to combine with adAddressingMode in one payload for an
    # end-to-end "reject leaves it unpersisted" check, so this unit-tests the
    # phase helper directly against a fake db.
    from api.settings import _apply_processing_flags

    calls = []

    class FakeDB:
        def set_setting(self, key, value, is_default=False):
            calls.append((key, value, is_default))

    err = _apply_processing_flags(FakeDB(), {'adAddressingMode': 'segment_ids'})
    assert err is None
    assert ('ad_addressing_mode', 'segment_ids', False) in calls
    assert calls.count(('ad_addressing_mode', 'segment_ids', False)) == 1
