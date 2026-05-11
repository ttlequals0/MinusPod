import pytest

from benchmark.truth_parser import (
    Ad,
    TruthParseError,
    parse_text,
    parse_timestamp,
    validate_cross_reference,
    validate_logical,
)


def test_single_ad():
    txt = """
start: 0:45
end: 1:52
text: This episode is brought to you by BetterHelp.
"""
    truth = parse_text(txt)
    assert not truth.is_no_ad_episode
    assert len(truth.ads) == 1
    assert truth.ads[0].start == 45.0
    assert truth.ads[0].end == 112.0
    assert "BetterHelp" in truth.ads[0].text


def test_multiple_ads_separated_by_dashes():
    txt = """
start: 0:45
end: 1:52
text: Ad one.
---
start: 20:40
end: 21:45
text: Ad two.
"""
    truth = parse_text(txt)
    assert len(truth.ads) == 2
    assert truth.ads[0].start == 45.0
    assert truth.ads[1].start == 20 * 60 + 40


def test_multiline_text_field():
    txt = """
start: 0:45
end: 1:52
text: First line of ad
second line
third line
"""
    truth = parse_text(txt)
    assert truth.ads[0].text == "First line of ad\nsecond line\nthird line"


def test_no_ad_marker():
    txt = "# Verified: no ads in this episode.\n"
    truth = parse_text(txt)
    assert truth.is_no_ad_episode is True
    assert truth.ads == []


def test_no_ad_marker_case_insensitive():
    truth = parse_text("# verified:  NO ads in this one")
    assert truth.is_no_ad_episode


def test_empty_file_raises():
    with pytest.raises(TruthParseError, match="empty"):
        parse_text("")


def test_only_comments_no_marker_raises():
    with pytest.raises(TruthParseError, match="empty"):
        parse_text("# just a comment\n# another\n")


def test_marker_plus_blocks_conflicts():
    txt = """
# Verified: no ads in this episode.
start: 0:45
end: 1:52
text: contradictory
"""
    with pytest.raises(TruthParseError, match="marker.*blocks"):
        parse_text(txt)


def test_missing_required_field():
    txt = """
start: 0:45
text: missing end field
"""
    with pytest.raises(TruthParseError, match="end"):
        parse_text(txt)


def test_empty_text_field():
    txt = """
start: 0:45
end: 1:52
text:
"""
    with pytest.raises(TruthParseError, match="text"):
        parse_text(txt)


def test_unexpected_line_outside_field():
    txt = """
random line outside a field
start: 0:45
end: 1:52
text: foo
"""
    with pytest.raises(TruthParseError, match="random"):
        parse_text(txt)


@pytest.mark.parametrize("raw,expected", [
    ("45", 45.0),
    ("0:45", 45.0),
    ("1:30", 90.0),
    ("0:00:45", 45.0),
    ("1:02:03", 3723.0),
    ("0:45.5", 45.5),
])
def test_timestamp_formats(raw, expected):
    assert parse_timestamp(raw) == pytest.approx(expected)


def test_timestamp_invalid():
    with pytest.raises(TruthParseError):
        parse_timestamp("not-a-time")


def test_label_case_insensitive():
    truth = parse_text("Start: 1\nEND: 2\nText: foo\n")
    assert truth.ads[0].start == 1.0
    assert truth.ads[0].end == 2.0


def test_validate_logical_overlap():
    from benchmark.truth_parser import Truth
    truth = Truth(
        ads=[Ad(start=10, end=30, text="a"), Ad(start=20, end=40, text="b")],
        is_no_ad_episode=False,
    )
    with pytest.raises(TruthParseError, match="overlap|before"):
        validate_logical(truth)


def test_validate_logical_start_after_end():
    from benchmark.truth_parser import Truth
    truth = Truth(ads=[Ad(start=30, end=10, text="a")], is_no_ad_episode=False)
    with pytest.raises(TruthParseError, match="start.*end"):
        validate_logical(truth)


def test_validate_logical_exceeds_duration():
    from benchmark.truth_parser import Truth
    truth = Truth(ads=[Ad(start=0, end=120, text="a")], is_no_ad_episode=False)
    with pytest.raises(TruthParseError, match="duration"):
        validate_logical(truth, episode_duration=60)


def test_validate_logical_passes_for_no_ad_episode():
    from benchmark.truth_parser import Truth
    truth = Truth(ads=[], is_no_ad_episode=True)
    validate_logical(truth, episode_duration=60)


def test_cross_reference_match():
    from benchmark.truth_parser import Truth
    segments = [
        {"start": 0.0, "end": 5.0, "text": "Welcome"},
        {"start": 5.0, "end": 30.0, "text": "This episode is brought to you by BetterHelp dot com slash podcast"},
        {"start": 30.0, "end": 60.0, "text": "Now back to the show"},
    ]
    truth = Truth(
        ads=[Ad(start=5.0, end=30.0, text="This episode is brought to you by BetterHelp.com/podcast")],
        is_no_ad_episode=False,
    )
    validate_cross_reference(truth, segments)


def test_cross_reference_mismatch():
    from benchmark.truth_parser import Truth
    segments = [{"start": 0.0, "end": 30.0, "text": "completely different content"}]
    truth = Truth(
        ads=[Ad(start=0.0, end=30.0, text="An ad about BetterHelp")],
        is_no_ad_episode=False,
    )
    with pytest.raises(TruthParseError, match="fuzzy-match"):
        validate_cross_reference(truth, segments)


def test_cross_reference_no_covering_segments():
    from benchmark.truth_parser import Truth
    segments = [{"start": 0.0, "end": 5.0, "text": "x"}]
    truth = Truth(ads=[Ad(start=10.0, end=20.0, text="y")], is_no_ad_episode=False)
    with pytest.raises(TruthParseError, match="no segments"):
        validate_cross_reference(truth, segments)
