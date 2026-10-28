"""Configurable local upload size limit (#920)."""
import io
import os
import sys
import tempfile

import pytest

from tests.app_bootstrap import authenticate_test_client

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))
os.environ.setdefault('MINUSPOD_DATA_DIR', tempfile.mkdtemp(prefix='upload-limit-test-'))

import api as api_pkg  # noqa: E402
from api import get_database  # noqa: E402

MB = 1024 * 1024
SLUG = 'upload-limit-show'


@pytest.fixture
def hdr(app_client):
    token = authenticate_test_client(app_client)
    yield token
    get_database().clear_setting('local_upload_max_mb')


@pytest.mark.parametrize('value', [1, 4096])
def test_put_accepts_bounds(app_client, hdr, value):
    r = app_client.put('/api/v1/settings/ad-detection',
                       json={'localUploadMaxMb': value}, headers=hdr)
    assert r.status_code == 200, r.get_json()
    assert app_client.get('/api/v1/settings').get_json()['localUploadMaxMb']['value'] == value


@pytest.mark.parametrize('value', [0, 4097, 'abc'])
def test_put_rejects_invalid(app_client, hdr, value):
    r = app_client.put('/api/v1/settings/ad-detection',
                       json={'localUploadMaxMb': value}, headers=hdr)
    assert r.status_code == 400


def test_default_is_1024(app_client, hdr):
    body = app_client.get('/api/v1/settings').get_json()
    assert body['localUploadMaxMb']['value'] == 1024
    assert body['localUploadMaxMb']['isDefault'] is True


def test_widen_upload_cap_uses_setting(app_client, hdr):
    get_database().set_setting('local_upload_max_mb', '2', is_default=False)
    with app_client.application.test_request_context(
            '/api/v1/feeds/some-show/episodes', method='POST'):
        api_pkg._widen_upload_cap()
        assert api_pkg.request.max_content_length == 2 * MB


@pytest.fixture
def local_feed(app_client, hdr):
    r = app_client.post('/api/v1/feeds', json={
        'feedType': 'local', 'title': 'Upload Limit Show', 'slug': SLUG}, headers=hdr)
    assert r.status_code == 201, r.get_json()
    yield
    app_client.delete(f'/api/v1/feeds/{SLUG}', headers=hdr)


def test_oversize_upload_gets_named_413(app_client, hdr, local_feed):
    get_database().set_setting('local_upload_max_mb', '1', is_default=False)
    r = app_client.post(
        f'/api/v1/feeds/{SLUG}/episodes',
        data={'audio': (io.BytesIO(b'0' * (2 * MB)), 'a.mp3')},
        content_type='multipart/form-data')
    assert r.status_code == 413
    body = r.get_json()
    assert body['error'] == 'File is too large. This server accepts up to 1 MB per upload.'
    assert body['status'] == 413
    assert body['maxUploadMb'] == 1


def test_413_on_other_route_keeps_generic_body(app_client, hdr):
    r = app_client.put('/api/v1/settings/ad-detection', headers=hdr,
                       data=b'0' * (11 * MB), content_type='application/json')
    assert r.status_code == 413
    body = r.get_json()
    assert body['status'] == 413
    assert 'maxUploadMb' not in body
    assert 'File is too large' not in body['error']
