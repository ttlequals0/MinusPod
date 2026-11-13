"""serve_minuspod_cover fallback behavior.

It is the only public podcast-level artwork route, so it must serve the plain cover when badging is off and never 404 a cached cover because compositing failed."""
import io
from unittest.mock import patch

import pytest
from PIL import Image

from tests.app_bootstrap import bootstrap

_test_data_dir = bootstrap('cover_fallback_test_', reset_storage=True)

import main_app.routes as routes_mod  # noqa: E402
from main_app import app  # noqa: E402


def _png() -> bytes:
    buf = io.BytesIO()
    Image.new('RGB', (300, 300), (255, 255, 255)).save(buf, 'PNG')
    return buf.getvalue()


@pytest.fixture
def client():
    app.config['TESTING'] = True
    with app.test_client() as c:
        yield c


def _seed(slug):
    routes_mod.db.create_podcast(slug, f'https://example.com/{slug}.xml', slug)
    routes_mod.storage.save_artwork(slug, _png(), 'image/png',
                                    'https://example.com/art.png')


def test_watermark_off_serves_plain_cover_not_badged(client):
    slug = 'cover-off'
    _seed(slug)
    routes_mod.db.set_setting('artwork_watermark_enabled', 'false')

    resp = client.get(f'/{slug}/cover-minuspod.jpg')

    assert resp.status_code == 200
    assert resp.data == routes_mod.storage.get_artwork(slug)[0]
    # Badging darkens the corner; the plain cover stays pure white there.
    badged = routes_mod.storage.get_watermarked_artwork(slug)[0]
    assert resp.data != badged


def test_watermark_on_serves_badged_cover(client):
    slug = 'cover-on'
    _seed(slug)
    routes_mod.db.set_setting('artwork_watermark_enabled', 'true')
    try:
        resp = client.get(f'/{slug}/cover-minuspod.jpg')
        assert resp.status_code == 200
        assert resp.data == routes_mod.storage.get_watermarked_artwork(slug)[0]
    finally:
        routes_mod.db.set_setting('artwork_watermark_enabled', 'false')


def test_compositing_failure_falls_back_to_plain_cover_not_404(client):
    slug = 'cover-composite-fail'
    _seed(slug)
    routes_mod.db.set_setting('artwork_watermark_enabled', 'true')
    try:
        with patch.object(routes_mod.storage, 'get_watermarked_artwork',
                          return_value=None):
            resp = client.get(f'/{slug}/cover-minuspod.jpg')
        assert resp.status_code == 200
        assert resp.data == routes_mod.storage.get_artwork(slug)[0]
    finally:
        routes_mod.db.set_setting('artwork_watermark_enabled', 'false')


def test_no_cached_cover_at_all_still_404s(client):
    slug = 'cover-missing'
    routes_mod.db.create_podcast(slug, f'https://example.com/{slug}.xml', slug)

    resp = client.get(f'/{slug}/cover-minuspod.jpg')

    assert resp.status_code == 404
