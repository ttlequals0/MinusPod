"""Settings PUT: served-RSS-affecting flags must clear feed etags on change."""
import os
import sys
import tempfile
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))
os.environ.setdefault('MINUSPOD_DATA_DIR', tempfile.mkdtemp(prefix='settings-flags-api-test-'))

from api import get_database


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


def test_only_expose_processed_default_change_clears_etags(app_client):
    db = get_database()
    _authed(app_client)
    db.set_setting('only_expose_processed_default', 'false', is_default=False)
    with patch.object(db, 'clear_all_podcast_etags') as clear:
        resp = app_client.put('/api/v1/settings/ad-detection', json={'onlyExposeProcessedDefault': True},
                              headers=_csrf_headers(app_client))
        assert resp.status_code == 200
        assert clear.call_count == 1
        resp = app_client.put('/api/v1/settings/ad-detection', json={'onlyExposeProcessedDefault': True},
                              headers=_csrf_headers(app_client))
        assert resp.status_code == 200
        assert clear.call_count == 1
    assert db.get_setting('only_expose_processed_default') == 'true'


def test_processing_defaults_round_trip_through_settings_api(app_client):
    """The three global defaults use their stored values after a PUT."""
    db = get_database()
    _authed(app_client)
    headers = _csrf_headers(app_client)
    response = app_client.put(
        '/api/v1/settings/ad-detection',
        json={'skipSecondPass': True, 'differentialFetchMode': 'off', 'chaptersMode': 'off'},
        headers=headers,
    )
    assert response.status_code == 200
    settings = app_client.get('/api/v1/settings').get_json()
    assert settings['skipSecondPass']['value'] is True
    assert settings['differentialFetchMode']['value'] == 'off'
    assert settings['chaptersMode']['value'] == 'off'

    db.set_setting('skip_second_pass', 'false', is_default=False)
    db.set_setting('differential_fetch_mode', 'auto', is_default=False)
    db.set_setting('chapters_mode', 'auto', is_default=False)


def test_skip_second_pass_rejects_non_boolean_without_changing_setting(app_client):
    db = get_database()
    db.set_setting('skip_second_pass', 'false', is_default=False)
    _authed(app_client)

    response = app_client.put(
        '/api/v1/settings/ad-detection',
        json={'skipSecondPass': 'true'},
        headers=_csrf_headers(app_client),
    )

    assert response.status_code == 400
    assert db.get_setting('skip_second_pass') == 'false'


def test_processing_defaults_reject_mixed_payload_before_any_write(app_client):
    db = get_database()
    db.set_setting('chapters_mode', 'generate', is_default=False)
    _authed(app_client)

    response = app_client.put(
        '/api/v1/settings/ad-detection',
        json={'chaptersMode': 'off', 'skipSecondPass': 'true'},
        headers=_csrf_headers(app_client),
    )

    assert response.status_code == 400
    assert db.get_setting('chapters_mode') == 'generate'
    db.set_setting('chapters_mode', 'auto', is_default=False)
