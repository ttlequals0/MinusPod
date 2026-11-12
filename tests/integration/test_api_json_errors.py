"""Unmatched paths and wrong methods under /api/ answer in JSON; everything else is unchanged."""
import pytest

from tests.integration.fingerprint_helpers import csrf

OVER_INT64 = '9223372036854775808'


def _call(client, method, path):
    return getattr(client, method)(path, json={}, headers=csrf(client))


@pytest.mark.parametrize('method', ['get', 'put', 'delete', 'post', 'patch'])
@pytest.mark.parametrize('path', [f'/api/v1/patterns/{OVER_INT64}', '/api/v1/nope', '/api/v1/patterns/1/nope',
                                  '/api/v2/anything', '/api/nope'])
def test_an_unmatched_api_path_is_a_json_404(app_client, method, path):
    r = _call(app_client, method, path)
    assert r.status_code == 404 and r.is_json
    assert r.get_json() == {'error': 'Not found', 'status': 404}


@pytest.mark.parametrize('method,path', [
    ('delete', '/api/v1/health'), ('post', '/api/v1/patterns/1/audio/preview'),
    ('put', '/api/v1/patterns/1/audio/matches'), ('patch', '/api/v1/patterns/1')])
def test_a_wrong_method_on_a_real_route_is_a_json_405_with_allow(app_client, method, path):
    r = _call(app_client, method, path)
    assert r.status_code == 405 and r.is_json
    assert r.get_json() == {'error': 'Method not allowed', 'status': 405}
    allowed = {m.strip() for m in r.headers['Allow'].split(',')}
    assert 'GET' in allowed or 'PUT' in allowed or 'POST' in allowed
    assert method.upper() not in allowed


def test_a_route_that_raises_its_own_404_keeps_its_message(app_client):
    r = _call(app_client, 'get', '/api/v1/patterns/9223372036854775807')
    assert r.status_code == 404 and r.get_json()['error'] == 'Pattern not found'


def test_options_on_a_real_api_route_still_answers_with_allow(app_client):
    r = app_client.options('/api/v1/patterns/1/audio')
    assert r.status_code == 200 and 'GET' in r.headers['Allow']


def test_head_on_a_real_api_route_is_unchanged(app_client):
    assert app_client.head('/api/v1/patterns/9223372036854775807', headers=csrf(app_client)).status_code == 404


@pytest.mark.parametrize('path', ['/nope/deeper/still', '/episodes/x', '/ui/anything/here'])
def test_paths_outside_api_keep_their_pages(app_client, path):
    r = app_client.get(path)
    if path.startswith('/ui/'):
        assert r.status_code in (200, 404) and not r.is_json
    else:
        assert r.status_code == 404 and not r.is_json


def test_a_non_api_wrong_method_stays_html(app_client):
    r = app_client.delete('/health')
    assert r.status_code == 405 and not r.is_json


def test_a_path_that_only_starts_with_api_is_not_an_api_path(app_client):
    r = app_client.get('/apiary/x/y')
    assert r.status_code == 404 and not r.is_json
