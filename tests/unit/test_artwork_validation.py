"""Tests for artwork download magic-number validation + size cap."""
from unittest.mock import MagicMock, patch

import pytest

from storage import (
    _ALLOWED_IMAGE_TYPES,
    _detect_image_mime,
    _max_artwork_bytes,
)


JPEG = b'\xff\xd8\xff\xe0' + b'\x00' * 20
PNG = b'\x89PNG\r\n\x1a\n' + b'\x00' * 20
GIF = b'GIF89a' + b'\x00' * 20
WEBP = b'RIFF\x24\x00\x00\x00WEBP' + b'\x00' * 20


def test_detect_image_mime_jpeg():
    assert _detect_image_mime(JPEG) == 'image/jpeg'


def test_detect_image_mime_png():
    assert _detect_image_mime(PNG) == 'image/png'


def test_detect_image_mime_gif():
    assert _detect_image_mime(GIF) == 'image/gif'


def test_detect_image_mime_webp():
    assert _detect_image_mime(WEBP) == 'image/webp'


@pytest.mark.parametrize("payload", [
    b'<svg><script>alert(1)</script></svg>',
    b'<html>',
    b'%PDF-1.4',
    b'',
    b'not-enough',
])
def test_detect_image_mime_rejects_non_images(payload):
    assert _detect_image_mime(payload) is None


def test_allowed_types_excludes_svg():
    assert 'image/svg+xml' not in _ALLOWED_IMAGE_TYPES


def test_max_artwork_bytes_default():
    assert _max_artwork_bytes() == 5 * 1024 * 1024


def test_max_artwork_bytes_env_override(monkeypatch):
    monkeypatch.setenv('MINUSPOD_MAX_ARTWORK_BYTES', str(10 * 1024 * 1024))
    assert _max_artwork_bytes() == 10 * 1024 * 1024


def test_max_artwork_bytes_floor(monkeypatch):
    monkeypatch.setenv('MINUSPOD_MAX_ARTWORK_BYTES', '1000')
    assert _max_artwork_bytes() == 64 * 1024


def test_max_artwork_bytes_ceiling(monkeypatch):
    monkeypatch.setenv('MINUSPOD_MAX_ARTWORK_BYTES', str(500 * 1024 * 1024))
    assert _max_artwork_bytes() == 50 * 1024 * 1024


def test_max_artwork_bytes_invalid_env_falls_back(monkeypatch):
    monkeypatch.setenv('MINUSPOD_MAX_ARTWORK_BYTES', 'not-a-number')
    assert _max_artwork_bytes() == 5 * 1024 * 1024


def _mock_response(content_type: str, body: bytes) -> MagicMock:
    response = MagicMock()
    response.headers = {'Content-Type': content_type}
    response.iter_content = lambda chunk_size: (body[i:i+chunk_size] for i in range(0, len(body), chunk_size))
    response.raise_for_status = lambda: None
    return response


def test_download_rejects_html_declared_as_jpeg(temp_db, tmp_path):
    from storage import Storage
    storage = Storage(data_dir=str(tmp_path))
    storage.db.create_podcast('mock-pod', 'https://example.com/feed.xml')

    with patch('storage.safe_get') as mock_get:
        mock_get.return_value = _mock_response('image/jpeg', b'<html>fake</html>')
        result = storage.download_artwork('mock-pod', 'https://cdn.example.com/x.jpg')

    assert result is False
    assert storage.get_artwork('mock-pod') is None


def test_download_rejects_oversize(temp_db, tmp_path, monkeypatch):
    from storage import Storage
    monkeypatch.setenv('MINUSPOD_MAX_ARTWORK_BYTES', str(64 * 1024))
    storage = Storage(data_dir=str(tmp_path))
    storage.db.create_podcast('mock-pod-oversize', 'https://example.com/feed.xml')

    huge = JPEG + b'\x00' * (1024 * 1024)
    with patch('storage.safe_get') as mock_get:
        mock_get.return_value = _mock_response('image/jpeg', huge)
        result = storage.download_artwork(
            'mock-pod-oversize', 'https://cdn.example.com/big.jpg'
        )

    assert result is False
    assert storage.get_artwork('mock-pod-oversize') is None


def test_download_saves_valid_jpeg(temp_db, tmp_path):
    from storage import Storage
    storage = Storage(data_dir=str(tmp_path))
    storage.db.create_podcast('ok-pod', 'https://example.com/feed.xml')

    with patch('storage.safe_get') as mock_get:
        mock_get.return_value = _mock_response('image/jpeg', JPEG)
        result = storage.download_artwork('ok-pod', 'https://cdn.example.com/ok.jpg')

    assert result is True
    cached = storage.get_artwork('ok-pod')
    assert cached is not None
    data, mime = cached
    assert mime == 'image/jpeg'
    assert data[:3] == b'\xff\xd8\xff'


def test_download_rejects_disallowed_content_type(temp_db, tmp_path):
    from storage import Storage
    storage = Storage(data_dir=str(tmp_path))
    storage.db.create_podcast('svg-pod', 'https://example.com/feed.xml')

    with patch('storage.safe_get') as mock_get:
        mock_get.return_value = _mock_response('image/svg+xml', b'<svg></svg>')
        result = storage.download_artwork('svg-pod', 'https://cdn.example.com/x.svg')

    assert result is False
