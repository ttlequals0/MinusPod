import pytest
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


def test_scrub_request_paths_redacts_bare_path_only():
    from utils.http import scrub_request_paths
    assert scrub_request_paths("GET /feed/x?key=s3 HTTP/1.1") == "GET /feed/x?<redacted> HTTP/1.1"
    assert scrub_request_paths("https://h/x?y=1") == "https://h/x?y=1"
    assert scrub_request_paths("no path here") == "no path here"


@pytest.mark.parametrize("text,expected", [
    ("[/feed?key=abc]", "[/feed?<redacted>]"),
    ("(/feed?key=abc)", "(/feed?<redacted>)"),
    ('"/feed?key=abc"', '"/feed?<redacted>"'),
    ("see /f?k=v, then", "see /f?<redacted>, then"),
    ("/feed?key=abc&x=1", "/feed?<redacted>"),
    ("GET /feed?key=abc&x=1 HTTP/1.1", "GET /feed?<redacted> HTTP/1.1"),
    ("'GET /feed?key=abc HTTP/1.1'", "'GET /feed?<redacted> HTTP/1.1'"),
    ("https://h/x?y=1", "https://h/x?y=1"),
    ("Is this ok? yes", "Is this ok? yes"),
    ("/var/log/app.log done", "/var/log/app.log done"),
    ("path /a/b? done", "path /a/b? done"),
    ("foo/bar?k=v", "foo/bar?k=v"),
    ("[/f?k=v]. next", "[/f?<redacted>]. next"),
])
def test_scrub_request_paths_cases(text, expected):
    from utils.http import scrub_request_paths
    assert scrub_request_paths(text) == expected


def test_scrub_request_paths_long_input_is_fast():
    import time
    from utils.http import scrub_request_paths
    text = "/a" * 100_000 + " tail"
    start = time.perf_counter()
    assert scrub_request_paths(text) == text
    assert time.perf_counter() - start < 1.0


def test_cdn_check_error_message_redacts_feed_key_and_query(monkeypatch):
    def boom(*a, **k):
        raise requests.RequestException("down: https://feed.example.com/" + "a" * 64 + "?t=1")
    monkeypatch.setattr('utils.safe_http.safe_head', boom)
    _, message = Transcriber().check_audio_availability('https://cdn.example.com/a.mp3')
    assert message.endswith("https://feed.example.com/[redacted-feed-key]?<redacted>")


@pytest.mark.parametrize("text,needle", [
    ('/x?' + ',' * 50000 + 'x', '/x?<redacted>'),
    ('/x?' + 'a,' * 50000, '/x?<redacted>'),
    ('/' + 'a' * 20000 + '?k=v', '?<redacted>'),
    ('/' + 'a' * 50000 + '?k=v', None),
    ('https://h/x?' + 'a' * 100000, 'https://h/x?<redacted>'),
    ('http://' * 50000, None),
    ('/a(' * 100000, None),
    ('word ' * 200000, None),
], ids=['comma-run', 'a-comma-pairs', 'long-segment', 'over-cap-segment', 'long-url-query', 'http-repeat',
        'path-paren-repeat', '1mb-clean'])
def test_redaction_is_linear_on_hostile_input(text, needle):
    import time
    from utils.http import redact_log_text
    start = time.perf_counter()
    out = redact_log_text(text)
    assert time.perf_counter() - start < 0.5
    assert 'k=v' not in out
    if needle:
        assert needle in out


def test_redact_log_text_keeps_a_normal_traceback_whole_and_elides_huge_input():
    from utils.http import MAX_REDACT_CHARS, redact_log_text
    normal = "line\n" * 2000
    assert redact_log_text(normal) == normal
    huge = "x" * (MAX_REDACT_CHARS * 3)
    out = redact_log_text(huge)
    assert "chars elided" in out and len(out) < MAX_REDACT_CHARS + 100
