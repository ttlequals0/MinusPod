"""End-to-end path-traversal tests through the HTTP surface.

Unit tests in test_path_containment cover Storage directly; these
exercise the HTTP routes to confirm a traversal payload cannot leak
through to the filesystem via a request.
"""
import io
import os
import sys
import tempfile

import pytest
from PIL import Image

_test_data_dir = tempfile.mkdtemp(prefix='pathtrav_test_')
os.environ.setdefault('SECRET_KEY', 'pathtrav-test-secret')
os.environ.setdefault('DATA_DIR', _test_data_dir)

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))

import database
import storage as storage_mod
database.Database._instance = None
database.Database.__init__.__defaults__ = (_test_data_dir,)
storage_mod.Storage.__init__.__defaults__ = (_test_data_dir,)

from main_app import app
import main_app.routes as routes_mod


@pytest.fixture
def client():
    db = database.Database()
    db.set_setting('app_password', '')
    app.config['TESTING'] = True
    with app.test_client() as c:
        yield c


def test_no_password_allows_feed_mutation(client):
    """v2.26.0: with no app password set, the API is fully open -- a mutating
    request is not blocked by an auth gate. DELETE on an unknown feed reaches the
    handler and 404s; the old pre-bootstrap guard returned 403 here. Reads still
    work."""
    resp = client.delete('/api/v1/feeds/no-such-feed')
    assert resp.status_code == 404
    assert client.get('/api/v1/feeds').status_code == 200


@pytest.mark.parametrize("slug", [
    "..",
    "../etc",
    "..%2Fetc",
    "foo/bar",
    "foo\\bar",
    ".hidden",
])
def test_traversal_slug_never_returns_200(client, slug):
    """Any traversal payload must not return 200 from an artwork or RSS
    route; the storage layer raises PathContainmentError which the
    handler must translate into a 4xx."""
    for path in (
        f"/api/v1/feeds/{slug}/artwork",
        f"/episodes/{slug}/cover-minuspod.jpg",
        f"/{slug}/cover-minuspod.jpg",
    ):
        response = client.get(path)
        assert response.status_code < 200 or response.status_code >= 300


@pytest.mark.parametrize("episode_id", [
    "..",
    "../escape",
    "ZZZZZZZZZZZZ",  # correct length, wrong alphabet
    "short",
    "0123456789abc",  # one char too long
])
def test_traversal_episode_id_never_returns_200(client, episode_id):
    """Episode-id traversal payloads must not return the served file."""
    paths = [
        f"/episodes/some-slug/{episode_id}.mp3",
        f"/episodes/some-slug/{episode_id}.vtt",
        f"/episodes/some-slug/{episode_id}/chapters.json",
    ]
    for path in paths:
        response = client.get(path)
        assert response.status_code < 200 or response.status_code >= 300, (
            f"{path} returned {response.status_code}"
        )


# Unicode lookalikes and dangerous slugs must be rejected at
# route entry; .isalnum() would have accepted several of these.
@pytest.mark.parametrize("bad_id", [
    "abcdef\u0435f123",       # Cyrillic e (U+0435)
    "\uff10\uff11\uff12abcdef\uff13\uff14\uff15",  # full-width digits
    "abcdef123\u0000",        # null byte
    "AbCdEf012345",           # uppercase (hex allows a-f only)
])
def test_episode_id_unicode_rejected(client, bad_id):
    for path in (
        f"/episodes/some-slug/{bad_id}.mp3",
        f"/episodes/some-slug/{bad_id}.vtt",
        f"/episodes/some-slug/{bad_id}/chapters.json",
    ):
        response = client.get(path)
        assert response.status_code == 404, f"{path} returned {response.status_code}"


@pytest.mark.parametrize("slug", [
    "../etc/passwd",
    "foo/bar",
    "foo\\bar",
    ".hidden",
    "null\x00byte",
])
def test_public_slug_routes_reject_traversal(client, slug):
    """serve_rss, serve_episode, serve_transcript_vtt, serve_chapters_json
    all run through validate_slug_param / validate_slug_and_episode_params
    and must 404 before hitting storage."""
    response = client.get(f"/{slug}")
    assert response.status_code in (404, 301, 308), f"/{slug} returned {response.status_code}"
    for suffix in ('.mp3', '.vtt', '/chapters.json'):
        response = client.get(f"/episodes/{slug}/abc123def456{suffix}")
        assert response.status_code in (404, 301, 308)


def _png_bytes():
    buf = io.BytesIO()
    Image.new('RGB', (200, 200), (255, 255, 255)).save(buf, 'PNG')
    return buf.getvalue()


def test_minuspod_cover_serves_badged_jpeg(client):
    """The public /episodes/<slug>/cover-minuspod.jpg route (issue #420) serves
    the badged cover so podcast apps can fetch it from the feed host. Seed via
    the exact storage the handler reads (routes_mod.storage) so the test is
    immune to per-test singleton swaps."""
    slug = 'cover-ok'
    st = routes_mod.storage
    st.db.create_podcast(slug, f'https://example.com/{slug}.xml', slug)
    st.save_artwork(slug, _png_bytes(), 'image/png', 'https://example.com/a.png')

    response = client.get(f'/episodes/{slug}/cover-minuspod.jpg')
    assert response.status_code == 200
    assert response.mimetype == 'image/jpeg'
    assert response.headers.get('Access-Control-Allow-Origin') == '*'


def test_minuspod_cover_podcast_level_path_serves_badged_jpeg(client):
    """The podcast-level /<slug>/cover-minuspod.jpg route (2.25.2) serves the same
    badged cover; the feed points its channel image here, and the /episodes/ path
    stays as a back-compat alias."""
    slug = 'cover-podcast-path'
    st = routes_mod.storage
    st.db.create_podcast(slug, f'https://example.com/{slug}.xml', slug)
    st.save_artwork(slug, _png_bytes(), 'image/png', 'https://example.com/a.png')

    response = client.get(f'/{slug}/cover-minuspod.jpg')
    assert response.status_code == 200
    assert response.mimetype == 'image/jpeg'
    assert response.headers.get('Access-Control-Allow-Origin') == '*'


def test_minuspod_cover_404_without_art(client):
    slug = 'cover-none'
    st = routes_mod.storage
    st.db.create_podcast(slug, f'https://example.com/{slug}.xml', slug)
    response = client.get(f'/episodes/{slug}/cover-minuspod.jpg')
    assert response.status_code == 404
