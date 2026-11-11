import random
import re
import time

import pytest
import requests

from transcriber import Transcriber
from utils.http import (
    MAX_REDACT_CHARS, redact_feed_credentials, redact_log_text, scrub_query_strings, scrub_request_paths)


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


def test_cdn_check_ssrf_message_is_redacted(monkeypatch):
    from utils.url import SSRFError

    def blocked(*a, **k):
        raise SSRFError("blocked https://u:pw@10.0.0.1/a.mp3?key=secret123 /" + "b" * 64)
    monkeypatch.setattr('utils.safe_http.safe_head', blocked)
    available, message = Transcriber().check_audio_availability('https://cdn.example.com/a.mp3')
    assert available is False
    assert message == "URL blocked: blocked https://<redacted>@10.0.0.1/a.mp3?<redacted> /[redacted-feed-key]"


_HEX_RUN = re.compile(r'(?<![0-9a-fA-F])[0-9a-fA-F]{8,}')


def _assert_tokens_whole(out):
    for token, whole in (('[redacted', '[redacted-feed-key]'), ('<redac', '<redacted>')):
        assert out.count(token) == out.count(whole), out


@pytest.mark.parametrize("text,secret", [
    ('z' * 16344 + ' ' + 'a' * 64 + ' ' + 'w ' * 20000, 'aaaaaaaa'),
    ('w ' * 8190 + '/f?' + 's' * 200 + ' ' + 'w ' * 20000, 'sss'),
    ('w ' * 8180 + 'https://h/f?' + 's' * 300 + ' ' + 'w ' * 20000, 'sss'),
    ('w ' * 20000 + 'https://h/f?' + 's' * 300 + ' ' + 'w ' * 8180, 'sss'),
    ('w ' * 20000 + 'D' * 64 + ' ' + 'w ' * 8185, 'DDDDDDDD'),
    ('w ' * 8190 + 'https://user:' + 'p' * 100 + '@h/x ' + 'w ' * 20000, 'ppp'),
], ids=['key-at-head-cut', 'path-query-at-head-cut', 'url-query-at-head-cut', 'url-query-at-tail-cut',
        'upper-key-at-tail-cut', 'password-at-head-cut'])
def test_cap_never_splits_a_secret_or_a_token(text, secret):
    out = redact_log_text(text)
    assert secret not in out
    assert not _HEX_RUN.search(out.replace('w', ''))
    assert len(out) <= MAX_REDACT_CHARS
    _assert_tokens_whole(out)


_IDEMPOTENT_CASES = [
    'word ' * 20000,
    'z' * 16344 + ' ' + 'a' * 64 + ' ' + 'w ' * 20000,
    'x' * (MAX_REDACT_CHARS * 3),
    ('https://h/p?k=' + 'v' * 30 + ' ') * 3000,
    ('/f?k=v ' + 'A' * 64 + ' ') * 3000,
    '\n'.join(['Traceback line /a/b?k=v https://u:p@h/x?y=1'] * 2000),
    'x' + 'a' * 64 + '/f?k=v',
    'a' * 64 + '://h/p?k=v',
    "'https://h/x?y=1'",
    '<https://h/p?x=1>',
    '/x?,,,',
    '?<redacted>',
    '/f?<redacted>&k=v',
    '',
]


@pytest.mark.parametrize("text", _IDEMPOTENT_CASES, ids=[f"case{i}" for i in range(len(_IDEMPOTENT_CASES))])
def test_redaction_is_idempotent_and_capped(text):
    once = redact_log_text(text)
    assert len(once) <= MAX_REDACT_CHARS
    assert redact_log_text(once) == once


def _random_text(rng, pieces):
    return ''.join(rng.choice(pieces) for _ in range(rng.randint(1, 40)))


_PIECES = ['a', 'A', 'f', '0', 'g', '/', '//', '?', ':', '.', '-', '@', ' ', '\n', '"', "'", ')', ']', '>',
           '[', '<', '}', ',', '=', '#', 'https://', 'WSS://', 'u:p@', '?k=v', 'redacted', '<redacted>',
           '[redacted-feed-key]', 'a' * 64, 'B' * 64, '0123456789abcdef.', 'é', '\ud800']


def test_redaction_properties_on_random_text():
    rng = random.Random(1234)
    for _ in range(4000):
        text = _random_text(rng, _PIECES)
        once = redact_log_text(text)
        assert redact_log_text(once) == once, repr(text)
        canaried = f"{text} https://h/p?q=CANARY1 /p?q=CANARY2 https://u:CANARY3@h/p {'c' * 64} {text}"
        out = redact_log_text(canaried)
        assert 'CANARY' not in out and 'c' * 64 not in out, repr(text)


def test_redaction_properties_on_random_elided_text():
    rng = random.Random(99)
    for _ in range(40):
        text = ' '.join(_random_text(rng, _PIECES) for _ in range(3000))
        once = redact_log_text(text)
        assert len(once) <= MAX_REDACT_CHARS
        assert redact_log_text(once) == once
        # The kept head and tail are exact slices of the full redaction, cut at whitespace.
        head, _, tail = re.split(r' (\[\d+ chars elided\])', once)
        full = redact_feed_credentials(scrub_request_paths(scrub_query_strings(text)))
        assert full.startswith(head) and full[len(head)].isspace()
        assert full.endswith(tail) and tail[:1].isspace()


@pytest.mark.parametrize("text,expected", [
    ("HTTPS://h/f?key=secret", "HTTPS://h/f?<redacted>"),
    ("Https://h/f?key=secret", "Https://h/f?<redacted>"),
    ("wss://h/f?token=secret", "wss://h/f?<redacted>"),
    ("ws://h/f?token=secret", "ws://h/f?<redacted>"),
    ("ftp://h/f?k=s", "ftp://h/f?<redacted>"),
    ("svn+ssh://h/f?k=s", "svn+ssh://h/f?<redacted>"),
    ("url:/feed?key=abc", "url:/feed?<redacted>"),
    ("x//feed?key=abc", "x//feed?<redacted>"),
    ("://h/feed?key=abc", "://h/feed?<redacted>"),
    ("Is this ok? yes", "Is this ok? yes"),
    ("/var/log/app.log done", "/var/log/app.log done"),
    ("path /a/b?", "path /a/b?"),
    ("path /a/b? done", "path /a/b? done"),
    ("foo/bar?k=v", "foo/bar?k=v"),
    ('File "/app/src/x.py", line 3, in f', 'File "/app/src/x.py", line 3, in f'),
    ("key=secret", "key=secret"),
    ('{"key": "abc"}', '{"key": "abc"}'),
    ("ratio 10:30/45? done", "ratio 10:30/45? done"),
    ("a:b/c?d=1", "a:b/c?d=1"),
])
def test_schemes_and_path_prefixes(text, expected):
    assert redact_log_text(text) == expected


def test_absolute_url_is_handled_once():
    assert scrub_request_paths("HTTPS://h/x?y=1") == "HTTPS://h/x?y=1"
    assert scrub_query_strings("wss://h/x?y=1") == "wss://h/x?<redacted>"
    assert scrub_query_strings("/feed?key=abc") == "/feed?key=abc"


@pytest.mark.parametrize("text,expected", [
    ("key " + "A" * 64, "key [redacted-feed-key]"),
    ("key " + "aB" * 32, "key [redacted-feed-key]"),
    ("sub " + "ABCDEF0123456789." + "c" * 64, "sub [redacted-feed-key]"),
    ("/feed/" + "F" * 64 + ".xml", "/feed/[redacted-feed-key].xml"),
    ("too long " + "A" * 65, "too long " + "A" * 65),
])
def test_feed_keys_are_redacted_in_any_case(text, expected):
    assert redact_log_text(text) == expected
    assert redact_feed_credentials(text) == expected


@pytest.mark.parametrize("text,expected", [
    ("'https://h/x?y=1'", "'https://h/x?<redacted>'"),
    ('{"u": "https://h/x?y=1"}', '{"u": "https://h/x?<redacted>"}'),
    ("see https://h/x?y=1, then", "see https://h/x?<redacted>, then"),
    ("<https://h/p?x=1>", "<https://h/p?<redacted>>"),
    ("Go to https://h/p?x=1.", "Go to https://h/p?<redacted>."),
    ("{'u': 'https://h/x?y=1'}", "{'u': 'https://h/x?<redacted>'}"),
    ("(https://h/x?y=1)", "(https://h/x?<redacted>)"),
    ("https://h/it's/a.mp3?key=s", "https://h/it's/a.mp3?<redacted>"),
])
def test_absolute_urls_keep_trailing_punctuation(text, expected):
    assert redact_log_text(text) == expected


@pytest.mark.parametrize("text,expected", [
    ("https://user:pass@h/p?x=1", "https://<redacted>@h/p?<redacted>"),
    ("https://token@h/p", "https://<redacted>@h/p"),
    ("https://user:p@ss@h/p", "https://<redacted>@h/p"),
    ("HTTP://u:p@h:8080", "HTTP://<redacted>@h:8080"),
    ("//u:p@h/p", "//<redacted>@h/p"),
    ("https://h/p?email=a@b.c", "https://h/p?<redacted>"),
    ("https://h?email=a@b.c", "https://h?<redacted>"),
    ("https://h/p@x", "https://h/p@x"),
    ("https://h#a@b", "https://h#a@b"),
    ("user@example.com", "user@example.com"),
    ("mailto:user@example.com", "mailto:user@example.com"),
    ("// note @alice", "// note @alice"),
])
def test_url_userinfo_is_redacted(text, expected):
    assert redact_log_text(text) == expected


class _BadStr:
    def __str__(self):
        raise RuntimeError("no str")


@pytest.mark.parametrize("value,expected", [
    (None, "None"),
    (b"https://h/x?k=v", "b'https://h/x?<redacted>'"),
    (42, "42"),
    (_BadStr(), "[log text withheld: redaction failed]"),
    ("lone \ud800 https://h/x?k=v", "lone \ud800 https://h/x?<redacted>"),
])
def test_redaction_never_raises(value, expected):
    assert redact_log_text(value) == expected


def test_one_megabyte_input_is_bounded_and_fast():
    text = ("w " * 300000) + "https://h/x?k=SECRET " + ("w " * 300000)
    start = time.perf_counter()
    out = redact_log_text(text)
    assert time.perf_counter() - start < 0.5
    assert 'SECRET' not in out and len(out) <= MAX_REDACT_CHARS
    huge = ("https://u:PASS@h/x?k=SECRET " * 400000)
    start = time.perf_counter()
    out = redact_log_text(huge)
    assert time.perf_counter() - start < 0.5
    assert 'SECRET' not in out and 'PASS' not in out and len(out) <= MAX_REDACT_CHARS
    assert redact_log_text(out) == out


_DOUBLING_SHAPES = {
    'comma-run': lambda n: '/x?' + ',' * n + 'x',
    'a-comma-pairs': lambda n: '/x?' + 'a,' * n,
    'http-repeat': lambda n: 'http://' * n,
    'mixed-case-scheme-repeat': lambda n: 'HtTp://' * n,
    'host-dots': lambda n: 'https://' + 'a.' * n,
    'path-paren-repeat': lambda n: '/a(' * n,
    'nested-brackets': lambda n: '[(' * n + '/f?k=v' + ')]' * n,
    'colon-pairs': lambda n: 'a:' * n,
    'scheme-repeat': lambda n: 'a://' * n,
    'at-run': lambda n: '@' * n,
    'userinfo-repeat': lambda n: 'u:p@' * n,
    'url-userinfo-repeat': lambda n: 'https://' + 'u:p@' * n,
    'mixed-case-userinfo': lambda n: 'HTTPS://' + 'U:P@' * n,
    'hex-run': lambda n: 'a' * n,
    'mixed-case-hex-run': lambda n: 'aB' * n,
    'subscriber-prefix-repeat': lambda n: ('0123456789abcdef.' * n),
    'scheme-chars-no-colon': lambda n: 'a+.-' * n,
    'slash-run': lambda n: '/' * n,
    'double-slash-at': lambda n: '//@' * n,
    'query-marks': lambda n: '/?' * n,
}


def _best_time(text, repeats=5):
    best = float('inf')
    for _ in range(repeats):
        start = time.perf_counter()
        redact_log_text(text)
        best = min(best, time.perf_counter() - start)
    return best


@pytest.mark.parametrize("name", sorted(_DOUBLING_SHAPES))
def test_redaction_time_doubles_linearly(name):
    build = _DOUBLING_SHAPES[name]
    n = 200000 // (len(build(2)) - len(build(1)))
    small, large = _best_time(build(n)), _best_time(build(2 * n))
    assert large < 0.5 and small < 0.5
    assert large / max(small, 1e-3) < 3, (name, small, large)
