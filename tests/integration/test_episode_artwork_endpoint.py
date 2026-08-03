"""The episode artwork proxy, end to end (#617).

Publishers reject a browser's cross-site Referer, so the episode cover is
fetched server-side and served from MinusPod's own origin instead of being
hot-linked. The URL is read from the episode row, never from the caller.
"""
from unittest.mock import patch

import pytest

from tests.app_bootstrap import bootstrap

_test_data_dir = bootstrap('epart_test_', secret_key='epart-test-secret')

import database  # noqa: E402
import storage as storage_module  # noqa: E402
from main_app import app  # noqa: E402


SLUG = 'artwork-feed'
EP = 'a1b2c3d4e5f6'
JPEG = b'\xff\xd8\xff\xe0' + b'\x00' * 64


def _clear_cached_covers():
    """Each test decides whether a cover is on disk, so start from empty."""
    art_dir = storage_module.Storage()._episode_artwork_dir(SLUG)
    if art_dir and art_dir.is_dir():
        for path in art_dir.iterdir():
            if path.is_file():
                path.unlink()


@pytest.fixture
def client():
    db = database.Database()
    db.set_setting('app_password', '')
    app.config['TESTING'] = True
    _clear_cached_covers()

    conn = db.get_connection()
    conn.execute("DELETE FROM podcasts WHERE slug = ?", (SLUG,))
    conn.commit()
    podcast_id = db.create_podcast(slug=SLUG, source_url='https://example.com/rss.xml',
                                   title='Artwork Feed')
    conn.execute(
        "INSERT INTO episodes (podcast_id, episode_id, title, original_url, artwork_url) "
        "VALUES (?, ?, 'E1', 'https://e/1.mp3', 'https://cdn.example/ep1.jpg')",
        (podcast_id, EP))
    conn.commit()

    with app.test_client() as c:
        yield c

    conn.execute("DELETE FROM podcasts WHERE slug = ?", (SLUG,))
    conn.commit()


def _url(episode_id=EP, slug=SLUG):
    return f'/api/v1/feeds/{slug}/episodes/{episode_id}/artwork'


def test_a_cached_cover_is_served_from_our_own_origin(client):
    storage_module.Storage()._save_episode_artwork(SLUG, EP, JPEG, 'image/jpeg')

    response = client.get(_url())

    assert response.status_code == 200
    assert response.mimetype == 'image/jpeg'
    assert response.data == JPEG


def test_a_cached_cover_cannot_be_sniffed_into_something_executable(client):
    storage_module.Storage()._save_episode_artwork(SLUG, EP, JPEG, 'image/jpeg')

    response = client.get(_url())

    assert response.headers['X-Content-Type-Options'] == 'nosniff'
    assert response.headers['Content-Security-Policy'] == "default-src 'none'"


def test_a_cold_cover_is_fetched_once_and_then_served(client):
    storage = storage_module.Storage()

    def fake_download(slug, episode_id, url):
        storage._save_episode_artwork(slug, episode_id, JPEG, 'image/jpeg')
        return True

    with patch.object(storage, 'download_episode_artwork',
                      side_effect=fake_download) as download:
        assert client.get(_url()).status_code == 200
        assert client.get(_url()).status_code == 200

    # Second request came off disk.
    assert download.call_count == 1


def test_the_fetch_uses_the_stored_url_not_anything_the_caller_supplies(client):
    storage = storage_module.Storage()
    with patch.object(storage, 'download_episode_artwork',
                      return_value=False) as download:
        client.get(_url() + '?url=https://evil.example/pwn.jpg')

    _, called_url = download.call_args[0][0::2]
    assert called_url == 'https://cdn.example/ep1.jpg'


def test_a_blocked_cover_falls_back_to_the_show_cover(client):
    # Better a show cover than the grey placeholder the hotlink block produced.
    storage = storage_module.Storage()
    with patch.object(storage, 'download_episode_artwork', return_value=False):
        response = client.get(_url())

    assert response.status_code == 302
    assert response.headers['Location'].endswith(f'/api/v1/feeds/{SLUG}/artwork')


def test_an_episode_with_no_cover_falls_back_to_the_show_cover(client):
    response = client.get(_url(episode_id='ffffffffffff'))

    assert response.status_code == 302
    assert response.headers['Location'].endswith(f'/api/v1/feeds/{SLUG}/artwork')


@pytest.mark.parametrize('episode_id', [
    '..', '%2e%2e', 'a%2f..%2fb', 'NOTHEXAT_ALL', 'a' * 64,
])
def test_a_traversal_payload_cannot_reach_the_filesystem(client, episode_id):
    # The blueprint-wide episode_id guard in api/__init__.py refuses anything
    # off the 12-hex shape before a handler runs, so this route inherits it.
    # Asserted here anyway: the route puts the value into a filename.
    storage_module.Storage()._save_episode_artwork(SLUG, EP, JPEG, 'image/jpeg')

    response = client.get(_url(episode_id=episode_id))

    assert response.status_code in (400, 404)
    assert response.data != JPEG
