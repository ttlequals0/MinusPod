"""snip_short_asks learn floors: a short confirmed ask becomes a near-exact show pattern while the feed is on."""
import pytest
from rapidfuzz import fuzz

from tests.app_bootstrap import bootstrap

bootstrap('snip_short_asks_learn_test_')

from ad_detector import AdDetector
from text_pattern_matcher import TextMatch, TextPatternMatcher
from utils.short_asks import SHORT_ASK_PATTERN_MIN_SCORE, learn_floor_problem

SLUG = 'short-ask-learn'
EPISODE_ID = 'abcdef012345'
ASK = 'Please follow the show and leave a review.'
LONG_ASK = (
    "Before we get into it, if you are enjoying the show please hit that follow "
    "button right now. Take a minute on the app you are listening to this on to "
    "leave us a rating and a review. It really helps more people find the show "
    "and it takes only a few seconds of your time. Thank you so much for "
    "listening and for supporting us every single week."
)
_TALK = (
    "Today we look at how coastal towns planned their harbours across the last century. ",
    "The council archive holds maps that show every pier, crane and warehouse. ",
    "A retired surveyor explains why the channel was dredged twice before nineteen thirty. ",
    "Fishing boats gave way to ferries and later to container traffic on the quay. ",
)


@pytest.fixture
def feed(temp_db):
    temp_db.create_podcast(SLUG, 'https://example.com/feed.xml', 'Short Ask Learn')
    temp_db.upsert_episode(slug=SLUG, episode_id=EPISODE_ID, original_url='https://example.com/ep.mp3',
                           title='Test Episode', original_duration=3600.0)
    return temp_db


def _learn(db, text=ASK, seconds=4.0, category='interaction'):
    return TextPatternMatcher(db=db).create_show_promo_pattern_from_ad(
        segments=[], start=100.0, end=100.0 + seconds, category=category,
        podcast_id=SLUG, episode_id=EPISODE_ID, ad_text=text)


def _mutate(text, count):
    chars = list(text)
    for i in (3, 10, 17, 24, 31)[:count]:
        chars[i] = '#'
    return ''.join(chars)


def _episode(ask):
    segs, t = [], 0.0
    for talk in _TALK * 2:
        segs.append({'start': t, 'end': t + 20.0, 'text': talk * 2})
        t += 20.0
    segs.insert(4, {'start': 80.0, 'end': 84.0, 'text': ask})
    for seg in segs[5:]:
        seg['start'] += 4.0
        seg['end'] += 4.0
    return segs


def _hits(db, ask, pid):
    found = TextPatternMatcher(db=db).find_matches(_episode(ask), podcast_id=SLUG)
    return [m for m in found if m.pattern_id == pid]


def test_a_four_second_ask_is_learned_when_the_feed_is_on(feed):
    feed.update_podcast(SLUG, snip_short_asks=1)
    assert _learn(feed) is not None


def test_a_four_second_ask_is_refused_when_the_feed_is_off(feed):
    assert _learn(feed) is None
    feed.update_podcast(SLUG, snip_short_asks=0)
    assert _learn(feed) is None


def test_three_and_a_half_seconds_is_refused_when_on(feed):
    feed.update_podcast(SLUG, snip_short_asks=1)
    assert _learn(feed, seconds=3.5) is None


def test_a_configured_minimum_does_not_raise_the_short_ask_floor(feed):
    feed.update_podcast(SLUG, snip_short_asks=1)
    feed.set_setting('learning_min_pattern_duration', '15', is_default=False)
    assert _learn(feed) is not None


def test_text_below_thirty_characters_is_refused_when_on(feed):
    feed.update_podcast(SLUG, snip_short_asks=1)
    assert _learn(feed, text='Please follow us.') is None


def test_cross_promo_keeps_the_pr_floor_when_on(feed):
    feed.update_podcast(SLUG, snip_short_asks=1)
    assert _learn(feed, category='cross_promo') is None


def test_the_global_setting_turns_learning_on(feed):
    feed.set_setting('snip_short_asks', 'true', is_default=False)
    assert _learn(feed) is not None


def test_learn_floor_problem_names_the_reason():
    assert learn_floor_problem('interaction', 3.5, 42, 120) is not None
    assert learn_floor_problem('interaction', 4.0, 42, 120) is None
    assert learn_floor_problem('interaction', 4.0, 42, 3) is not None


def test_a_near_exact_read_matches_and_carries_the_short_ask_flag(feed):
    feed.update_podcast(SLUG, snip_short_asks=1)
    pid = _learn(feed)
    near = _mutate(ASK, 3)
    assert fuzz.partial_ratio(ASK, near) >= SHORT_ASK_PATTERN_MIN_SCORE
    hits = _hits(feed, near, pid)
    assert hits and hits[0].short_ask is True
    assert hits[0].category == 'interaction'


def test_a_looser_read_does_not_match(feed):
    feed.update_podcast(SLUG, snip_short_asks=1)
    pid = _learn(feed)
    loose = _mutate(ASK, 5)
    assert fuzz.partial_ratio(ASK, loose) < SHORT_ASK_PATTERN_MIN_SCORE
    assert _hits(feed, loose, pid) == []


def test_the_pattern_stops_matching_when_the_feed_goes_off(feed):
    feed.update_podcast(SLUG, snip_short_asks=1)
    pid = _learn(feed)
    assert _hits(feed, ASK, pid)
    feed.update_podcast(SLUG, snip_short_asks=0)
    assert _hits(feed, ASK, pid) == []


def test_a_pattern_of_150_characters_or_more_is_unchanged(feed):
    pid = _learn(feed, text=LONG_ASK, seconds=30.0)
    assert pid is not None
    hits = _hits(feed, LONG_ASK, pid)
    assert hits and hits[0].short_ask is False
    feed.update_podcast(SLUG, snip_short_asks=1)
    assert _hits(feed, LONG_ASK, pid)


def _stamped(short_ask, start=80.0, end=84.0):
    ads = []
    AdDetector(api_key='k')._add_pattern_match(
        TextMatch(pattern_id=7, start=start, end=end, confidence=0.95, category='interaction',
                  show_promo=True, short_ask=short_ask),
        'text_pattern', 'content', ads, [], EPISODE_ID)
    return ads[0]


def test_a_short_ask_match_becomes_a_flagged_marker():
    ad = _stamped(True)
    assert ad['short_ask'] is True and ad['short_ask_origin'] == [80.0, 84.0]


def test_a_plain_match_carries_no_flag():
    assert 'short_ask' not in _stamped(False)


def test_a_short_match_inside_a_longer_sentence_is_not_cut():
    from ad_validator import AdValidator, Decision
    from utils.short_asks import clamp_short_ask
    segs = [
        {'start': 70.0, 'end': 80.0, 'text': 'We also tell you every week that'},
        {'start': 80.0, 'end': 84.0, 'text': ASK.rstrip('.')},
        {'start': 84.0, 'end': 95.0, 'text': 'matters a great deal to us and to the town.'},
    ]
    ad = _stamped(True)
    ad.update(confidence=0.95, reason='Show pattern', detection_stage='text_pattern')
    clamp_short_ask(ad, segs)
    assert 'short_ask' not in ad
    out = AdValidator(episode_duration=3600.0, segments=segs).validate([ad]).ads[0]
    assert out['validation']['decision'] == Decision.REJECT.value
    assert any('Very short' in f for f in out['validation']['flags'])


class _OnlyResolver:
    """A db exposing nothing but the generic per-feed resolver."""

    def __init__(self, on):
        self.calls = []
        self._on = on

    def resolve_feed_bool(self, slug, column, setting_key):
        self.calls.append((slug, column, setting_key))
        return self._on


def test_the_feed_check_goes_through_the_generic_resolver():
    from utils.short_asks import feed_snips_short_asks
    db = _OnlyResolver(True)
    assert feed_snips_short_asks(db, SLUG) is True
    assert feed_snips_short_asks(_OnlyResolver(False), SLUG) is False
    assert feed_snips_short_asks(db, '') is False


def test_a_learned_short_ask_carries_what_a_community_export_needs(feed):
    feed.update_podcast(SLUG, snip_short_asks=1)
    pid = _learn(feed, category='self_promo')
    row = feed.get_ad_pattern_by_id(pid)
    assert row['created_by'] == 'show_promo'
    assert row['scope'] == 'podcast' and row['podcast_id'] == SLUG
    assert row['category'] == 'self_promo'
    assert row['sponsor_id'] is None
    assert row['text_template'] == ASK
    assert row['source'] == 'local'
    from utils.short_asks import is_short_show_template
    assert is_short_show_template(row['category'], row['text_template']) is True


def test_learning_writes_nothing_when_the_feed_is_off(feed):
    before = len(feed.get_ad_patterns())
    assert _learn(feed) is None
    assert len(feed.get_ad_patterns()) == before
