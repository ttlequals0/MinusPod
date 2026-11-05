"""normalize_url_prefix accepts clean path prefixes and rejects route collisions."""
import pytest

from config import normalize_url_prefix


@pytest.mark.parametrize('raw,expected', [
    ('', ''),
    ('/', ''),
    ('   ', ''),
    ('minuspod', '/minuspod'),
    ('/minuspod/', '/minuspod'),
    ('/a/b', '/a/b'),
])
def test_normalizes(raw, expected):
    assert normalize_url_prefix(raw) == expected


@pytest.mark.parametrize('raw', ['/ui', '/api', '/episodes/x', '/health', '/opml', '/a/../b', '/a b', '/a?x', '/a#f', '/./a'])
def test_rejects(raw):
    with pytest.raises(ValueError, match='MINUSPOD_URL_PREFIX'):
        normalize_url_prefix(raw)
