"""A bare visit to the server root lands on the web UI instead of a 404."""

from pathlib import Path

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


def test_ui_index_html_by_explicit_path_revalidates(client):
    """A direct request for index.html must not be cached, same as the '/ui/' fallback."""
    response = client.get('/ui/index.html')
    assert response.headers['Cache-Control'] == 'no-cache, must-revalidate'


def test_ui_root_still_revalidates(client):
    response = client.get('/ui/')
    assert response.headers['Cache-Control'] == 'no-cache, must-revalidate'


def test_ui_asset_stays_immutable(client):
    assets_dir = Path(__file__).parents[2] / 'static' / 'ui' / 'assets'
    asset_name = next(assets_dir.iterdir()).name
    response = client.get(f'/ui/assets/{asset_name}')
    assert response.headers['Cache-Control'] == 'public, max-age=31536000, immutable'


def test_ui_manifest_revalidates(client):
    response = client.get('/ui/manifest.webmanifest')
    assert response.headers['Cache-Control'] == 'no-cache, must-revalidate'


def test_ui_other_file_keeps_hourly_cache(client):
    response = client.get('/ui/logo.svg')
    assert response.headers['Cache-Control'] == 'public, max-age=3600'
