import json
from pathlib import Path

import pytest

from benchmark.capture import (
    CaptureError,
    _build_truth_template,
    _format_ad_block,
    _format_time,
    parse_episode_url,
)


def test_parse_episode_url():
    slug, ep = parse_episode_url("https://podsrv.example.com/ui/feeds/daily-tech-news-show/episodes/7735cdbbb405")
    assert slug == "daily-tech-news-show"
    assert ep == "7735cdbbb405"


def test_parse_episode_url_with_query():
    slug, ep = parse_episode_url("https://x/ui/feeds/foo/episodes/abc?q=1#frag")
    assert slug == "foo"
    assert ep == "abc"


def test_parse_episode_url_invalid():
    with pytest.raises(CaptureError, match="parse"):
        parse_episode_url("https://example.com/wrong/path")


@pytest.mark.parametrize("seconds,expected", [
    (45.0, "0:45.00"),
    (90.5, "1:30.50"),
    (3661.5, "1:01:01.50"),
])
def test_format_time(seconds, expected):
    assert _format_time(seconds) == expected


def test_truth_template_with_markers():
    segments = [
        {"start": 0.0, "end": 30.0, "text": "ad text here"},
        {"start": 30.0, "end": 60.0, "text": "content"},
    ]
    episode = {"adMarkers": [{"start": 0.0, "end": 30.0}]}
    out = _build_truth_template(episode, segments)
    assert "start: 0:00.00" in out
    assert "end:   0:30.00" in out
    assert "ad text here" in out


def test_truth_template_with_rejected():
    segments = [{"start": 100.0, "end": 130.0, "text": "rejected text"}]
    episode = {"adMarkers": [], "rejectedAdMarkers": [{"start": 100, "end": 130}]}
    out = _build_truth_template(episode, segments)
    assert "Rejected" in out
    assert "# start: 1:40.00" in out
    assert "# text:  rejected text" in out


def test_truth_template_no_markers_includes_marker_hint():
    out = _build_truth_template({}, [])
    assert "no-ads marker" in out.lower() or "no ads" in out.lower()


def test_format_ad_block_commented_prefix():
    segments = [{"start": 0.0, "end": 10.0, "text": "x"}]
    lines = _format_ad_block({"start": 0, "end": 10}, segments, commented=True)
    assert all(ln.startswith("# ") for ln in lines)


def test_format_ad_block_no_covering_segments_fallback():
    lines = _format_ad_block({"start": 0, "end": 10}, [], commented=False)
    assert any("transcript unavailable" in ln for ln in lines)
