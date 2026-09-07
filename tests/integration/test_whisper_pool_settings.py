"""GET /settings and PUT /settings/ad-detection carry the whisper pool keys (#parallel-whisper)."""
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))
os.environ.setdefault('MINUSPOD_DATA_DIR', tempfile.mkdtemp(prefix='whisper-pool-settings-'))


def _csrf(app_client):
    with app_client.session_transaction() as sess:
        sess['authenticated'] = True
    app_client.get('/api/v1/auth/status')
    cookie = app_client.get_cookie('minuspod_csrf')
    return {'X-CSRF-Token': cookie.value} if cookie else {}


def test_get_defaults(app_client):
    _csrf(app_client)
    body = app_client.get('/api/v1/settings').get_json()
    assert body['whisperPoolEnabled']['value'] is False
    assert body['whisperPoolMaxRequests']['value'] == 4
    assert body['whisperPoolMaxEpisodes']['value'] == 1


def test_patch_roundtrip(app_client):
    hdr = _csrf(app_client)
    r = app_client.put('/api/v1/settings/ad-detection', json={
        'whisperPoolEnabled': True, 'whisperPoolMaxRequests': 6, 'whisperPoolMaxEpisodes': 2,
    }, headers=hdr)
    assert r.status_code == 200
    body = app_client.get('/api/v1/settings').get_json()
    assert body['whisperPoolEnabled']['value'] is True
    assert body['whisperPoolMaxRequests']['value'] == 6
    assert body['whisperPoolMaxEpisodes']['value'] == 2


def test_patch_rejects_out_of_range(app_client):
    hdr = _csrf(app_client)
    assert app_client.put('/api/v1/settings/ad-detection', json={'whisperPoolMaxRequests': 0}, headers=hdr).status_code == 400
    assert app_client.put('/api/v1/settings/ad-detection', json={'whisperPoolMaxRequests': 65}, headers=hdr).status_code == 400
    assert app_client.put('/api/v1/settings/ad-detection', json={'whisperPoolMaxEpisodes': 17}, headers=hdr).status_code == 400
    assert app_client.put('/api/v1/settings/ad-detection', json={'whisperPoolEnabled': 'yes'}, headers=hdr).status_code == 400
