"""Tests for the upstream podcast:chapters JSON source (issue #560 follow-up).

Covers RSS capture of the podcast:chapters URL (rss_parser.extract_episodes)
and the fetch module (upstream_chapters.fetch_upstream_chapters).
"""
import json

import requests

from rss_parser import RSSParser
from upstream_chapters import MAX_UPSTREAM_CHAPTERS_BYTES, fetch_upstream_chapters
from utils.safe_http import ResponseTooLargeError


# ---------- RSS capture ----------

def _feed(item_extra: str = '') -> str:
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"
     xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd"
     xmlns:podcast="https://podcastindex.org/namespace/1.0">
  <channel>
    <title>Chapters Show</title>
    <item>
      <title>Ep One</title>
      <guid>ep-one</guid>
      <enclosure url="https://example.com/one.mp3" type="audio/mpeg"/>
      {item_extra}
    </item>
  </channel>
</rss>"""


class TestExtractEpisodesCapturesUpstreamChaptersUrl:
    def test_carries_url_when_tag_present(self):
        feed = _feed(
            '<podcast:chapters url="https://upstream.example.com/ep1.json" '
            'type="application/json+chapters"/>'
        )
        episodes = RSSParser().extract_episodes(feed)
        assert episodes[0]['upstream_chapters_url'] == \
            'https://upstream.example.com/ep1.json'

    def test_none_when_tag_absent(self):
        episodes = RSSParser().extract_episodes(_feed())
        assert episodes[0]['upstream_chapters_url'] is None

    def test_none_for_non_http_scheme(self):
        feed = _feed(
            '<podcast:chapters url="javascript:alert(1)" '
            'type="application/json+chapters"/>'
        )
        episodes = RSSParser().extract_episodes(feed)
        assert episodes[0]['upstream_chapters_url'] is None


# ---------- fetch_upstream_chapters ----------

def _response(body: bytes):
    """A minimal fake requests.Response usable with read_response_capped."""
    response = requests.Response()
    response.status_code = 200
    response._content = body
    response.iter_content = lambda chunk_size=65536: iter(
        [body[i:i + chunk_size] for i in range(0, len(body), chunk_size)] or [b'']
    )
    # A bare requests.Response() has raw=None; the real .close() dereferences
    # it, which would otherwise turn every fixture response into a spurious
    # fetch failure.
    response.close = lambda: None
    return response


class TestFetchUpstreamChapters:
    def test_happy_path_carries_title_img_url(self, monkeypatch):
        body = json.dumps({
            'version': '1.2.0',
            'chapters': [
                {'startTime': 0, 'title': 'Intro', 'img': 'https://x.com/a.jpg',
                 'url': 'https://x.com/a'},
                {'startTime': 90.5, 'title': 'Segment 2'},
            ],
        }).encode('utf-8')
        monkeypatch.setattr(
            'upstream_chapters.safe_get', lambda *a, **k: _response(body))

        result = fetch_upstream_chapters('https://feed.example.com/ch.json')

        assert result == [
            {'startTime': 0, 'title': 'Intro', 'img': 'https://x.com/a.jpg',
             'url': 'https://x.com/a'},
            {'startTime': 90.5, 'title': 'Segment 2'},
        ]

    def test_oversized_body_returns_none(self, monkeypatch):
        def _boom(*a, **k):
            raise ResponseTooLargeError('too big')
        monkeypatch.setattr('upstream_chapters.safe_get', lambda *a, **k: _response(b'{}'))
        monkeypatch.setattr('upstream_chapters.read_response_capped', _boom)

        assert fetch_upstream_chapters('https://feed.example.com/ch.json') is None

    def test_non_dict_json_returns_none(self, monkeypatch):
        body = json.dumps([1, 2, 3]).encode('utf-8')
        monkeypatch.setattr(
            'upstream_chapters.safe_get', lambda *a, **k: _response(body))

        assert fetch_upstream_chapters('https://feed.example.com/ch.json') is None

    def test_missing_chapters_key_returns_none(self, monkeypatch):
        body = json.dumps({'version': '1.2.0'}).encode('utf-8')
        monkeypatch.setattr(
            'upstream_chapters.safe_get', lambda *a, **k: _response(body))

        assert fetch_upstream_chapters('https://feed.example.com/ch.json') is None

    def test_timeout_returns_none(self, monkeypatch):
        def _boom(*a, **k):
            raise requests.exceptions.Timeout('timed out')
        monkeypatch.setattr('upstream_chapters.safe_get', _boom)

        assert fetch_upstream_chapters('https://feed.example.com/ch.json') is None

    def test_empty_chapters_list_returns_empty_list(self, monkeypatch):
        body = json.dumps({'version': '1.2.0', 'chapters': []}).encode('utf-8')
        monkeypatch.setattr(
            'upstream_chapters.safe_get', lambda *a, **k: _response(body))

        assert fetch_upstream_chapters('https://feed.example.com/ch.json') == []

    def test_entries_without_numeric_starttime_are_dropped(self, monkeypatch):
        body = json.dumps({'chapters': [
            {'title': 'No start'},
            {'startTime': -5, 'title': 'Negative'},
            {'startTime': 10, 'title': 'Kept'},
        ]}).encode('utf-8')
        monkeypatch.setattr(
            'upstream_chapters.safe_get', lambda *a, **k: _response(body))

        assert fetch_upstream_chapters('https://feed.example.com/ch.json') == \
            [{'startTime': 10, 'title': 'Kept'}]

    def test_size_cap_constant_is_one_megabyte(self):
        assert MAX_UPSTREAM_CHAPTERS_BYTES == 1024 * 1024


def test_bool_start_time_and_non_http_asset_urls_are_dropped(monkeypatch):
    body = json.dumps({'version': '1.2.0', 'chapters': [
        {'startTime': True, 'title': 'bool start'},
        {'startTime': 10, 'title': 'ok',
         'img': 'javascript:alert(1)', 'url': 'https://ok.example/x'},
    ]}).encode()
    monkeypatch.setattr('upstream_chapters.safe_get', lambda *a, **k: _response(body))
    out = fetch_upstream_chapters('https://example.com/ch.json')
    assert out == [{'startTime': 10, 'title': 'ok', 'url': 'https://ok.example/x'}]
