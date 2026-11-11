import requests

from transcriber import Transcriber
from utils.http import scrub_query_strings


def test_drops_query_and_keeps_following_text():
    out = scrub_query_strings("failed: https://cdn.example.com/a.mp3?key=secret123 next")
    assert out == "failed: https://cdn.example.com/a.mp3?<redacted> next"


def test_text_without_a_url_is_unchanged():
    assert scrub_query_strings("CDN check failed: connection reset") == "CDN check failed: connection reset"


def test_multiple_urls_each_scrubbed():
    out = scrub_query_strings("a https://h/x?t=1 b http://h2/y?z=2")
    assert out == "a https://h/x?<redacted> b http://h2/y?<redacted>"


def test_non_string_is_coerced_not_raised():
    assert scrub_query_strings(12345) == "12345"


def test_cdn_check_error_message_has_no_query_string(monkeypatch):
    def boom(*a, **k):
        raise requests.RequestException(
            "HTTPSConnectionPool: url=https://cdn.example.com/a.mp3?key=secret123")
    monkeypatch.setattr('utils.safe_http.safe_head', boom)
    available, message = Transcriber().check_audio_availability('https://cdn.example.com/a.mp3')
    assert available is False
    assert 'secret123' not in message
    assert '?<redacted>' in message
