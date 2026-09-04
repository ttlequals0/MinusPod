"""Settings PUT: served-RSS-affecting flags must clear feed etags on change."""
import os
import sys
import tempfile
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))
os.environ.setdefault('MINUSPOD_DATA_DIR', tempfile.mkdtemp(prefix='settings-flags-api-test-'))


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
    from api import get_database
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
