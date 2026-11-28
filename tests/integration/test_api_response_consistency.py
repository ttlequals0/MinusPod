"""Small response-shape consistency checks: error bodies carry status, OPML export is not cached."""

import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))
os.environ.setdefault('MINUSPOD_DATA_DIR', tempfile.mkdtemp(prefix='api-consistency-test-'))


def _authed(client):
    with client.session_transaction() as sess:
        sess['authenticated'] = True


def test_non_boolean_raw_api_chunks_is_a_flat_error_with_status(app_client):
    _authed(app_client)
    resp = app_client.put('/api/v1/settings/ad-detection', json={'transcribeRawApiChunks': 'yes'})
    assert resp.status_code == 400
    assert resp.get_json() == {'error': 'transcribeRawApiChunks must be a boolean', 'status': 400}


def test_opml_export_is_private_no_cache(app_client):
    _authed(app_client)
    resp = app_client.get('/api/v1/feeds/export-opml')
    assert resp.status_code == 200
    assert resp.headers['Cache-Control'] == 'private, no-cache'
