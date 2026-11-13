import gc
import itertools
import json
import logging
import random
import re
import sys
import time

import pytest
import requests

import utils.http as http
from run_log import _MESSAGE_FORMATTER
from transcriber import Transcriber
from utils.http import (
    _HSPACE, _LINE_BREAKS, _MAX_INPUT_CHARS, MAX_REDACT_CHARS, cap_log_text, redact_feed_credentials,
    redact_log_text,
    redact_stored_text, redact_url_userinfo, scrub_query_strings)
from utils.log_format import QueryStringRedactingFilter, _flatten, make_formatter
from utils.url import SSRFError


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


def test_scrub_query_strings_redacts_bare_paths_too():
    assert scrub_query_strings("GET /feed/x?key=s3 HTTP/1.1") == "GET /feed/x?<redacted> HTTP/1.1"
    assert scrub_query_strings("https://h/x?y=1") == "https://h/x?<redacted>"
    assert scrub_query_strings("no path here") == "no path here"


@pytest.mark.parametrize("text,expected", [
    ("[/feed?key=abc]", "[/feed?<redacted>]"),
    ("(/feed?key=abc)", "(/feed?<redacted>)"),
    ('"/feed?key=abc"', '"/feed?<redacted>"'),
    ("see /f?k=v, then", "see /f?<redacted>, then"),
    ("/feed?key=abc&x=1", "/feed?<redacted>"),
    ("GET /feed?key=abc&x=1 HTTP/1.1", "GET /feed?<redacted> HTTP/1.1"),
    ("'GET /feed?key=abc HTTP/1.1'", "'GET /feed?<redacted> HTTP/1.1'"),
    ("https://h/x?y=1", "https://h/x?<redacted>"),
    ("Is this ok? yes", "Is this ok? yes"),
    ("/var/log/app.log done", "/var/log/app.log done"),
    ("path /a/b? done", "path /a/b? done"),
    ("foo/bar?k=v", "foo/bar?<redacted>"),
    ("[/f?k=v]. next", "[/f?<redacted>]. next"),
])
def test_scrub_request_paths_cases(text, expected):
    assert scrub_query_strings(text) == expected


def test_scrub_request_paths_long_input_is_fast():
    text = "/a" * 100_000 + " tail"
    assert scrub_query_strings(text) == text
    assert _min_time(scrub_query_strings, text) < 1.0


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
    out = redact_log_text(text)
    assert _min_time(redact_log_text, text) < 1.0
    assert 'k=v' not in out
    if needle:
        assert needle in out


def test_redact_log_text_keeps_a_normal_traceback_whole_and_elides_huge_input():
    normal = "line\n" * 2000
    assert redact_log_text(normal) == normal
    huge = "x" * (MAX_REDACT_CHARS * 3)
    out = redact_log_text(huge)
    assert "chars elided" in out and len(out) < MAX_REDACT_CHARS + 100


def test_space_free_multiline_text_keeps_head_and_tail():
    text = '\n'.join('https://cdn.example/ep%d.mp3' % i for i in range(3000))
    out = redact_log_text(text)
    assert out.startswith('https://cdn.example/ep0.mp3')
    assert out.endswith('ep2999.mp3')
    assert 'chars elided' in out and len(out) <= MAX_REDACT_CHARS
    assert redact_log_text(out) == out
    assert cap_log_text(text).startswith('https://cdn.example/ep0.mp3')


@pytest.mark.parametrize("brk", list(_LINE_BREAKS), ids=[f"u{ord(b):04x}" for b in _LINE_BREAKS])
@pytest.mark.parametrize("secret_line", [
    'https://h/f?k=SECRETQ',
    'https://u:SECRETU@h/x',
    'D' * 64,
], ids=['query', 'userinfo', 'hex64'])
def test_multiline_cut_never_leaks_a_secret(brk, secret_line):
    fill = brk.join('https://cdn.example/ep%d.mp3' % i for i in range(700))
    for pad in range(0, 40, 7):
        text = fill + brk + 'p' * pad + secret_line + brk + 'q?k=SECRETQ' + brk + fill
        out = redact_log_text(text)
        assert 'SECRET' not in out and 'D' * 64 not in out
        assert len(out) <= MAX_REDACT_CHARS
        assert redact_log_text(out) == out
        _assert_tokens_whole(out)


def test_cdn_check_ssrf_message_is_redacted(monkeypatch):

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
           '[redacted-feed-key]', 'a' * 64, 'B' * 64, '0123456789abcdef.', 'é', '\ud800', '%2F', '%3D', 'c']


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
        full = scrub_query_strings(redact_feed_credentials(redact_url_userinfo(text)))
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
    ("foo/bar?k=v", "foo/bar?<redacted>"),
    ('File "/app/src/x.py", line 3, in f', 'File "/app/src/x.py", line 3, in f'),
    ("key=secret", "key=secret"),
    ('{"key": "abc"}', '{"key": "abc"}'),
    ("ratio 10:30/45? done", "ratio 10:30/45? done"),
    ("a:b/c?d=1", "a:b/c?<redacted>"),
    ("ok?/x", "ok?<redacted>"),
    ("and/or?", "and/or?"),
    ('"/a/b?"', '"/a/b?"'),
])
def test_schemes_and_path_prefixes(text, expected):
    assert redact_log_text(text) == expected


def test_each_pass_handles_only_its_own_shape():
    assert scrub_query_strings("https://u:p@h/x?y=1") == "https://u:p@h/x?<redacted>"
    assert redact_url_userinfo("https://u:p@h/x?y=1") == "https://<redacted>@h/x?y=1"
    assert redact_url_userinfo("https://h/@handle https://h/p?email=a@b.c") == (
        "https://h/@handle https://h/p?email=a@b.c")


@pytest.mark.parametrize("text,expected", [
    ("key " + "A" * 64, "key [redacted-feed-key]"),
    ("key " + "aB" * 32, "key [redacted-feed-key]"),
    ("sub " + "ABCDEF0123456789." + "c" * 64, "sub [redacted-feed-key]"),
    ("/feed/" + "F" * 64 + ".xml", "/feed/[redacted-feed-key].xml"),
    ("too long " + "A" * 65, "too long [redacted-feed-key]"),
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
    out = redact_log_text(text)
    assert _min_time(redact_log_text, text) < 1.0
    assert 'SECRET' not in out and len(out) <= MAX_REDACT_CHARS
    huge = ("https://u:PASS@h/x?k=SECRET " * 400000)
    out = redact_log_text(huge)
    assert _min_time(redact_log_text, huge) < 1.0
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
    'quote-run': lambda n: '"' * n,
    'path-query-quote-repeat': lambda n: '/f?k="' * n,
    'url-query-quote-repeat': lambda n: 'https://h/x?"' * n,
    'scheme-userinfo-repeat': lambda n: 'https://u:p@' * n,
    'quoted-userinfo-url-repeat': lambda n: '"https://u:p@h/"' * n,
    'path-quote-repeat': lambda n: '/a"' * n,
    'question-run': lambda n: '?' * n,
    'spaced-query-tokens': lambda n: '/? ' * n,
    'slashless-name-eq': lambda n: 'a?b=' * n,
    'question-eq': lambda n: '?=' * n,
    'slashless-question': lambda n: 'a?' * n,
    'alternating-name-eq': lambda n: '?a=?b=' * n,
    'percent-escape-hex': lambda n: '%2F' * n,
    'hex-dot-repeat': lambda n: ('a' * 16 + '.') * n,
    'query-lf-continuation': lambda n: '/x?a' + '\n' * n + 'b',
    'query-crlf-continuation': lambda n: '/x?a' + '\r\n' * n + 'b',
    'query-u2028-continuation': lambda n: '/x?a' + '\u2028' * n + 'b',
    'query-vt-continuation': lambda n: '/x?a' + '\x0b' * n + 'b',
    'line-breaks-only': lambda n: '\n\r\u2028\x0b' * n,
    'query-line-pairs': lambda n: '/x?a\n' * n,
}


def _min_times(texts, fn=redact_log_text, runs=7):
    """Fastest CPU time of each text, interleaved with the collector off so load and GC pauses hit all alike."""
    best = [float('inf')] * len(texts)
    enabled = gc.isenabled()
    gc.disable()
    try:
        for _ in range(runs):
            for i, text in enumerate(texts):
                # CPU time of this thread, so other processes saturating the machine do not count.
                start = time.thread_time()
                fn(text)
                best[i] = min(best[i], time.thread_time() - start)
    finally:
        if enabled:
            gc.enable()
    return best


def _min_time(fn, text):
    return _min_times([text], fn)[0]


@pytest.mark.parametrize("name", sorted(_DOUBLING_SHAPES))
def test_redaction_time_doubles_linearly(name):
    build = _DOUBLING_SHAPES[name]
    n = 500000 // (len(build(2)) - len(build(1)))
    # 4x the input: linear time is 4x and quadratic 16x, so 8 leaves a 2x margin for timer noise.
    small_text, large_text = build(n // 2), build(2 * n)
    assert len(large_text) < _MAX_INPUT_CHARS
    small, large = _min_times([small_text, large_text])
    assert large < 1.0 and small < 1.0
    assert large / max(small, 1e-3) < 8, (name, small, large)


@pytest.mark.parametrize("text,expected", [
    ('https://h/a"b?k=SECRET', 'https://h/a"b?<redacted>'),
    ('https://h/x?k=v"SECRET', 'https://h/x?<redacted>'),
    ('/f?k=v"SECRET', '/f?<redacted>'),
    ("/f?k=v'SECRET", '/f?<redacted>'),
    ('/a"b?k=SECRET', '/a"b?<redacted>'),
    ('{"u": "https://h/x?y=1"}', '{"u": "https://h/x?<redacted>"}'),
    ("'https://h/x?y=1'", "'https://h/x?<redacted>'"),
    ('"https://h/x?y=SECRET","next":"value"', '"https://h/x?<redacted>"'),
    ('https://h/x?k=v")]}SECRET', 'https://h/x?<redacted>'),
    ('https://u"x:SECRET@h/p', 'https://<redacted>@h/p'),
])
def test_an_embedded_quote_does_not_end_the_url(text, expected):
    out = redact_log_text(text)
    assert out == expected
    assert 'SECRET' not in out


@pytest.mark.parametrize("text,expected", [
    ('/a"https://u:SECRET@h/p', '/a"https://<redacted>@h/p'),
    ('{"a":"https://h/x","b":"https://u:SECRET@h2/"}', '{"a":"https://h/x","b":"https://<redacted>@h2/"}'),
    ('["https://a/x","https://u:SECRET@h/"]', '["https://a/x","https://<redacted>@h/"]'),
    ('"/f","https://u:SECRET@h/"', '"/f","https://<redacted>@h/"'),
])
def test_userinfo_of_a_url_later_in_the_token_is_redacted(text, expected):
    out = redact_log_text(text)
    assert out == expected
    assert 'SECRET' not in out


_PLANTED_KEY = '5eC0' * 16
_PLANTED_KEY2 = 'd00d' * 16
_PLANTED = ['SECRETQ1', 'SECRETU2', 'SECRETQ3', 'SECRETQ4', 'SECRETU5', 'SECRETQ6', 'SECRETU7', 'SECRETQ8',
            'SECRETU9', 'SECRETQ10', 'SECRETQ11', 'SECRETQ12', 'SECRETQ13', 'SECRETU14', _PLANTED_KEY, _PLANTED_KEY2]
_SECRET_PIECES = [
    'https://h/x?k=SECRETQ1', 'https://u:SECRETU2@h/p', '/f?k=SECRETQ3', 'https://h/a"b?k=SECRETQ4',
    '{"a":"https://h/x","b":"https://u:SECRETU5@h2/"}', '"https://h/x?y=SECRETQ6","next":"value"',
    'HTTPS://user:p@SECRETU7@h/p', "/f?k=v'SECRETQ8", 'wss://SECRETU9@h', 'key=' + _PLANTED_KEY + ';',
    'b?c=SECRETQ10', 'word?x=SECRETQ11.', 'x?a=1&b=SECRETQ12', 'GET /f?k=a b?c=SECRETQ13',
    'p%2F' + _PLANTED_KEY2 + 'e', _PLANTED_KEY + _PLANTED_KEY2, 'https://alice:pass%20SECRETU14@h/f%20x.xml',
]
_CORPUS_PIECES = _SECRET_PIECES + [
    'Is this ok? yes', 'path /a/b?', 'path /a/b? done', '/var/log/app.log done', 'File "/app/src/x.py", line 3, in f',
    'key=secret', '{"key": "abc"}', 'foo/bar?k=v', 'x//feed?key=abc', 'url:/feed?key=abc', 'https://h/x?y=1',
    '<https://h/p?x=1>', "'https://h/x?y=1'", '/a"https://h/p', '?<redacted>', '[redacted-feed-key]', '//', '@', '?',
    'https://h/@handle', 'user@example.com', 'https://h/p?email=a@b.c', ')]}', '\n', ' ', 'why?because', 'a?b',
    'ok?', 'status=403 retry=2',
]
_JOINERS = ['"', ',', "'", '[', '{', '/', '', ' ']


def _corpus():
    rng = random.Random(20261111)
    out = list(_CORPUS_PIECES)
    for _ in range(3000):
        pieces = [rng.choice(_CORPUS_PIECES) for _ in range(rng.randint(2, 8))]
        out.append(''.join(p + rng.choice(_JOINERS) for p in pieces))
    return out


def test_no_planted_secret_survives_in_any_pass_order():
    passes = (redact_url_userinfo, redact_feed_credentials, scrub_query_strings)
    orders = list(itertools.permutations(passes))
    for text in _corpus():
        once = redact_log_text(text)
        assert redact_log_text(once) == once, repr(text)
        for order in orders:
            out = text
            for step in order:
                out = step(out)
            leaked = [s for s in _PLANTED if s in out]
            assert not leaked, (repr(text), [f.__name__ for f in order], out)


_BREAK_CHARS = ['\r\n'] + list(_LINE_BREAKS)
# A query continued on the next line is only caught when the break is read as part of the token.
_CONTINUED_PIECES = [f'https://h/x?a=1{b}&tok=SECRETC{i}' for i, b in enumerate(_BREAK_CHARS)] + [
    f'x?a=1{b}SECRETS{i}' for i, b in enumerate(_BREAK_CHARS)] + ['/f?k=1\n\nb&c=SECRETQ15']
_CONTINUED_SECRETS = re.compile(r'SECRET[CS]\d+|SECRETQ15')
_LINE_JOINERS = _BREAK_CHARS + [' ' + b for b in _BREAK_CHARS] + [b + ' ' for b in _BREAK_CHARS]


def test_horizontal_space_is_every_space_that_is_not_a_line_break():
    spaces = {chr(c) for c in range(sys.maxunicode + 1) if chr(c).isspace()}
    breaks = {c for c in spaces if len(f'a{c}b'.splitlines()) == 2}
    assert breaks == set(_LINE_BREAKS)
    assert {c for c in spaces if re.fullmatch(f'[{_HSPACE}]', c)} == spaces - breaks


def test_a_query_continued_after_a_crlf_is_redacted():
    assert redact_log_text('GET https://h.example/x?a=1\r\n&tok=SECRETG') == 'GET https://h.example/x?<redacted>'
    assert redact_stored_text('GET https://h.example/x?a=1\r\n&tok=SECRETG') == 'GET https://h.example/x?<redacted>'


def _message_corpus():
    rng = random.Random(20261112)
    pieces = _CORPUS_PIECES + _CONTINUED_PIECES
    out = list(_CONTINUED_PIECES)
    for _ in range(3000):
        chosen = [rng.choice(pieces) for _ in range(rng.randint(2, 8))]
        out.append(''.join(p + rng.choice(_JOINERS + _LINE_JOINERS) for p in chosen))
    return out


def _record(text, exc_info=None):
    return logging.LogRecord('podcast.audio', logging.ERROR, __file__, 1, text, (), exc_info)


def _exc_info(text):
    try:
        raise ValueError(text)
    except ValueError:
        return sys.exc_info()


def _every_path(text):
    """The redacted message (or exception text) as each output path emits it."""
    filtered = _record(text)
    QueryStringRedactingFilter().filter(filtered)
    exc_rec = _record('failed', _exc_info(text))
    return {
        'log': redact_log_text(text),
        'stored': redact_stored_text(text),
        'json': json.loads(make_formatter('json').format(_record(text)))['message'],
        'run_log': _MESSAGE_FORMATTER.format(_record(text)),
        'filter': filtered.getMessage(),
        'text': make_formatter('text').format(_record(text)).split('] ', 3)[3],
        'json_exception': json.loads(make_formatter('json').format(exc_rec))['exception'],
        'text_exception': make_formatter('text').format(exc_rec),
    }


def test_no_planted_secret_survives_multi_line_redaction_in_any_path():
    planted = _PLANTED + ['SECRETQ15']
    for text in _message_corpus():
        once = redact_log_text(text)
        outs = _every_path(text)
        for path, out in outs.items():
            leaked = [s for s in planted if s in out] + _CONTINUED_SECRETS.findall(out)
            assert not leaked, (path, repr(text), out)
        assert outs['stored'] == outs['json'] == outs['run_log'] == outs['filter'] == once, repr(text)
        assert outs['text'] == redact_log_text(_flatten(text)) == redact_log_text(outs['text']), repr(text)
        assert redact_log_text(once) == once and redact_stored_text(once) == once, repr(text)


def test_a_line_kept_whole_by_redaction_keeps_its_break_in_every_path():
    text = 'get https://h/x?a=1\u2028  next\x85line'
    for path, out in _every_path(text).items():
        if path not in ('text', 'json_exception', 'text_exception'):
            assert out == 'get https://h/x?<redacted>\u2028  next\x85line', path


@pytest.mark.parametrize("text,expected", [
    ('GET /f?k=a b?c=SECRET', 'GET /f?<redacted> b?<redacted>'),
    ('b?c=SECRET', 'b?<redacted>'),
    ('word?x=1.', 'word?<redacted>.'),
    ('x?a=1&b=SECRET', 'x?<redacted>'),
    ('why?not a?b c?d=SECRET")', 'why?not a?b c?<redacted>")'),
    ('Is this ok? yes', 'Is this ok? yes'),
    ('ok?', 'ok?'),
    ('why?because', 'why?because'),
    ('a?b', 'a?b'),
    ('a?=b', 'a?=b'),
    ('{"key": "abc"}', '{"key": "abc"}'),
    ('status=403 retry=2', 'status=403 retry=2'),
])
def test_a_slashless_name_equals_query_is_redacted(text, expected):
    out = redact_log_text(text)
    assert out == expected
    assert 'SECRET' not in out


@pytest.mark.parametrize("text,expected", [
    ('/p%2F' + 'ab' * 32, '/p%[redacted-feed-key]'),
    ('key%3D' + 'ab' * 32, 'key%[redacted-feed-key]'),
    ('k ' + 'ab' * 32 + 'c', 'k [redacted-feed-key]'),
    ('k ' + 'ab' * 64, 'k [redacted-feed-key]'),
    ('k ' + '0123456789abcdef.' + 'ab' * 40, 'k [redacted-feed-key]'),
    ('k ' + 'ab' * 31 + 'a', 'k ' + 'ab' * 31 + 'a'),
])
def test_any_run_of_64_or_more_hex_is_redacted(text, expected):
    assert redact_feed_credentials(text) == expected
    assert redact_log_text(text) == expected
    assert redact_log_text(expected) == expected


def test_redaction_failure_report_names_only_the_exception_type(monkeypatch, capsys):
    def boom(_):
        raise RuntimeError("https://h/x?k=SECRET")

    monkeypatch.setattr(http, "_redaction_failure_reported", False)
    monkeypatch.setattr(http, "scrub_query_strings", boom)
    assert http.redact_log_text("anything") == "[log text withheld: redaction failed]"
    err = capsys.readouterr().err
    assert "RuntimeError" in err and "SECRET" not in err


def test_a_query_in_spaced_json_is_redacted_only_to_its_token():
    text = json.dumps({'reason': 'see https://h/x?k=SECRET', 'start': 100.0, 'end': 120.0})
    out = redact_log_text(text)
    assert 'SECRET' not in out and '"start": 100.0, "end": 120.0' in out
