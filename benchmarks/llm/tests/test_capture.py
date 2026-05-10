import json
from pathlib import Path

import pytest

from benchmark.capture import (
    CaptureError,
    _build_truth_template,
    _classify_marker,
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
    ad_text = (
        "This episode is brought to you by Acme. Visit acme.com slash twit "
        "today to start a free trial. Customers love Acme. It just works."
    )
    segments = [
        {"start": 0.0, "end": 30.0, "text": ad_text},
        {"start": 30.0, "end": 60.0, "text": "content"},
    ]
    episode = {"adMarkers": [{"start": 0.0, "end": 30.0}]}
    out = _build_truth_template(episode, segments)
    assert "start: 0:00.00" in out
    assert "end:   0:30.00" in out
    assert "brought to you by Acme" in out


def test_truth_template_with_rejected():
    segments = [{"start": 100.0, "end": 130.0, "text": "rejected text"}]
    episode = {"adMarkers": [], "rejectedAdMarkers": [{"start": 100, "end": 130}]}
    out = _build_truth_template(episode, segments)
    assert "rejected by production" in out
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


def test_classify_real_ad_with_brought_to_you_by():
    # Real ads have ~12-16 chars/sec speech density; build a realistic example.
    text = (
        "This episode is brought to you by Acme, the leading platform for "
        "anything you can imagine. Acme makes it easy to ship reliable "
        "products fast. Customers love Acme because it just works. Visit "
        "acme.com slash twit to get started today and learn more."
    )
    segments = [{"start": 0.0, "end": 30.0, "text": text}]
    ok, reason = _classify_marker({"start": 0, "end": 30}, segments)
    assert ok and reason is None


def test_classify_rejects_pure_whisper_hallucination():
    segments = [{"start": 0.0, "end": 26.0, "text": "Thank you for watching."}]
    ok, reason = _classify_marker({"start": 0, "end": 26}, segments)
    assert not ok
    assert "hallucination" in reason.lower()


def test_classify_rejects_low_density():
    segments = [{"start": 0.0, "end": 30.0, "text": "uh."}]
    ok, reason = _classify_marker({"start": 0, "end": 30}, segments)
    assert not ok
    assert "density" in reason.lower()


def test_classify_rejects_main_content_lacking_ad_signals():
    segments = [{"start": 0.0, "end": 28.0, "text":
        "NCSC, their national cybersecurity center, issued a warning. "
        "The posting was titled, Preparing for a Vulnerability Patch Wave."}]
    ok, reason = _classify_marker({"start": 0, "end": 28}, segments)
    assert not ok
    assert "ad signal" in reason.lower()


def test_classify_rejects_empty_range():
    ok, reason = _classify_marker({"start": 0, "end": 10}, [])
    assert not ok and "no transcript" in reason.lower()


def test_truth_template_routes_suspect_marker_to_rejected_section():
    real_ad = (
        "This episode of Show is brought to you by Acme. Acme makes everything "
        "better, faster, and more reliable. Customers across the world choose "
        "Acme to power their workflows. Visit acme.com slash twit today to "
        "start a free trial and see the difference for yourself."
    )
    segments = [
        {"start": 0.0, "end": 30.0, "text": real_ad},
        {"start": 100.0, "end": 126.0, "text": "Thank you for watching."},
    ]
    episode = {"adMarkers": [
        {"start": 0.0, "end": 30.0},
        {"start": 100.0, "end": 126.0},
    ]}
    out = _build_truth_template(episode, segments)
    # Real ad stays accepted
    assert "start: 0:00.00" in out
    assert "brought to you by Acme" in out
    # Suspect marker is auto-rejected with a reason
    assert "auto-rejected: Whisper hallucination" in out
    assert "# start: 1:40.00" in out


def test_truth_template_interleaves_rejected_in_chronological_order():
    real_ad = (
        "This episode is brought to you by Acme, the leading platform out there. "
        "Visit acme.com slash twit today and use code TWIT for a free trial."
    )
    segments = [
        {"start": 0.0, "end": 30.0, "text": real_ad},
        {"start": 1000.0, "end": 1026.0, "text": "Thank you for watching."},
        {"start": 2000.0, "end": 2030.0, "text": real_ad.replace("Acme", "Beta Corp")},
    ]
    episode = {"adMarkers": [
        {"start": 0.0, "end": 30.0},
        {"start": 1000.0, "end": 1026.0},
        {"start": 2000.0, "end": 2030.0},
    ]}
    out = _build_truth_template(episode, segments)
    # The hallucination block is rejected and lives between the two real ads,
    # so uncommenting it would still leave the file ordered by start time.
    pos_acme = out.index("brought to you by Acme")
    pos_rej = out.index("auto-rejected: Whisper hallucination")
    pos_beta = out.index("Beta Corp")
    assert pos_acme < pos_rej < pos_beta


def test_truth_template_keeps_production_rejected_section_too():
    real_ad = (
        "This episode is brought to you by Acme, the leading platform out there. "
        "Acme works for everyone, every workflow, every team size. Visit "
        "acme.com slash twit and use code TWIT for a discount today."
    )
    segments = [{"start": 0.0, "end": 30.0, "text": real_ad}]
    episode = {
        "adMarkers": [{"start": 0.0, "end": 30.0}],
        "rejectedAdMarkers": [{"start": 200.0, "end": 230.0}],
    }
    out = _build_truth_template(episode, segments)
    assert "rejected by production" in out
