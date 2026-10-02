"""A bare visit to the server root lands on the web UI instead of a 404."""

import pytest

from tests.app_bootstrap import bootstrap

_test_data_dir = bootstrap('rootredirect_test_', secret_key='rootredirect-test-secret')

from main_app import app  # noqa: E402


@pytest.fixture
def client():
    app.config['TESTING'] = True
    with app.test_client() as c:
        yield c


def test_root_redirects_to_ui(client):
    response = client.get('/')
    assert response.status_code == 302
    assert response.headers['Location'].endswith('/ui/')


def test_feed_slugs_still_route_past_the_root(client):
    response = client.get('/no-such-feed-here')
    assert response.status_code == 404
