"""Unit tests for heuristic pre/post-roll detection."""
from roll_detector import detect_preroll, detect_postroll


def _seg(start, end, text):
    return {"start": start, "end": end, "text": text}


PREROLL_SEGS = [
    _seg(0.0, 10.0, "This episode is brought to you by Acme."),
    _seg(10.0, 20.0, "Visit acme.com slash podcast and use code SAVE."),
    _seg(20.0, 30.0, "Sign up today for a free trial."),
    _seg(30.0, 40.0, "Welcome back to the show, everyone."),
    _seg(40.0, 50.0, "Today we are talking about gardening."),
]

POSTROLL_SEGS = [
    _seg(0.0, 60.0, "Main content about gardening."),
    _seg(60.0, 70.0, "Thanks for listening, see you next week."),
    _seg(70.0, 80.0, "This show is sponsored by Acme."),
    _seg(80.0, 90.0, "Go to acme.com and use promo code ROLL."),
]


def test_preroll_detected():
    ad = detect_preroll(PREROLL_SEGS, [])
    assert ad is not None
    assert ad["start"] == 0.0
    assert ad["end"] == 30.0
    assert ad["detection_stage"] == "heuristic_preroll"


def test_preroll_confidence_formula():
    ad = detect_preroll(PREROLL_SEGS, [])
    # brought-to-you-by, .com, promo-code, sign-up, free-trial style
    # indicators all hit; formula caps at 0.95
    assert 0.7 < ad["confidence"] <= 0.95


def test_preroll_none_when_show_starts_immediately():
    segs = [_seg(0.0, 4.0, "Welcome back to the show."),
            _seg(4.0, 20.0, "Content.")]
    assert detect_preroll(segs, []) is None


def test_preroll_none_without_show_start_pattern():
    segs = [_seg(0.0, 10.0, "Visit acme.com today."),
            _seg(10.0, 20.0, "Just talking.")]
    assert detect_preroll(segs, []) is None


def test_preroll_suppressed_when_region_covered():
    existing = [{"start": 0.0, "end": 28.0}]
    assert detect_preroll(PREROLL_SEGS, existing) is None


def test_preroll_threshold_requires_two_indicators():
    segs = [_seg(0.0, 10.0, "Sign up now for a discount."),
            _seg(10.0, 20.0, "Some content here."),
            _seg(20.0, 30.0, "Welcome back to the show.")]
    assert detect_preroll(segs, []) is None
    assert detect_preroll(segs, [], skip_patterns=True) is not None


def test_preroll_empty_segments():
    assert detect_preroll([], []) is None


PRODUCED_PREROLL_SEGS = [
    _seg(0.2, 3.1, "Observe the North American Dad."),
    _seg(5.7, 8.4, "He enters his vehicle information on Acme."),
    _seg(26.8, 29.0, "Sell your car today on Acme.com."),
    _seg(29.0, 30.6, "Pickup fees may apply."),
    _seg(30.6, 32.0, "Yep, Charlemagne, the guy."),
    _seg(32.6, 37.3, "We are the Daily Tech Show Podcast, back for another week."),
    _seg(48.2, 54.3, "This is um, yeah, this is your first big one."),
]
TAGLINE_HIT = [{"start": 26.8, "end": 32.0}]


def test_preroll_detected_ad_inside_zone_counts_as_indicator():
    assert detect_preroll(PRODUCED_PREROLL_SEGS, [], podcast_name="The Daily Tech Show") is None
    ad = detect_preroll(PRODUCED_PREROLL_SEGS, TAGLINE_HIT, podcast_name="The Daily Tech Show")
    assert (ad["start"], ad["end"]) == (0.2, 32.6)


def test_preroll_show_name_line_marks_intro():
    named = detect_preroll(PRODUCED_PREROLL_SEGS, TAGLINE_HIT, podcast_name="daily tech show")
    unnamed = detect_preroll(PRODUCED_PREROLL_SEGS, TAGLINE_HIT)
    assert named["end"] == 32.6
    assert unnamed["end"] == 48.2


def test_preroll_short_show_name_is_ignored():
    segs = [_seg(0.0, 5.0, "Go to acme.com today."),
            _seg(5.0, 10.0, "Also the host of The Daily."),
            _seg(20.0, 25.0, "Welcome to the show.")]
    ad = detect_preroll(segs, TAGLINE_HIT, podcast_name="The Daily")
    assert ad["end"] == 20.0


def test_preroll_ad_outside_zone_is_not_an_indicator():
    segs = [_seg(0.0, 10.0, "Sign up now for a discount."),
            _seg(10.0, 20.0, "Welcome back to the show."),
            _seg(20.0, 30.0, "Visit acme.com for more.")]
    assert detect_preroll(segs, [{"start": 20.0, "end": 30.0}]) is None


def test_postroll_detected():
    ad = detect_postroll(POSTROLL_SEGS, [], episode_duration=90.0)
    assert ad is not None
    assert ad["start"] == 70.0
    assert ad["end"] == 90.0
    assert ad["detection_stage"] == "heuristic_postroll"


def test_postroll_none_without_signoff():
    segs = [_seg(0.0, 60.0, "Content."),
            _seg(60.0, 90.0, "Visit acme.com with promo code X.")]
    assert detect_postroll(segs, [], episode_duration=90.0) is None


def test_postroll_none_when_signoff_at_very_end():
    segs = [_seg(0.0, 86.0, "Content."),
            _seg(86.0, 90.0, "Thanks for listening.")]
    assert detect_postroll(segs, [], episode_duration=90.0) is None


def test_postroll_suppressed_when_region_covered():
    existing = [{"start": 68.0, "end": 90.0}]
    assert detect_postroll(POSTROLL_SEGS, existing, episode_duration=90.0) is None


def test_postroll_uses_last_segment_end_when_no_duration():
    ad = detect_postroll(POSTROLL_SEGS, [])
    assert ad is not None
    assert ad["end"] == 90.0
