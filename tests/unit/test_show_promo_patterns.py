"""Podcast-scoped patterns for interaction / self_promo / cross_promo reads.

A human confirm (or manual create) of such a marker teaches the show: a
sponsorless, podcast-scoped text pattern that carries the category. Sponsor
patterns, floors and prompts are unchanged.
"""
import logging
from unittest.mock import MagicMock

import pytest

from tests.app_bootstrap import bootstrap

bootstrap('show_promo_patterns_test_')

from ad_reviewer import _skips_reviewer
from api.patterns import _handle_reject_correction, _resolve_or_create_pattern_from_text
from main_app.pipeline import keep_rules
from pattern_service import PatternService
from text_pattern_matcher import TextPatternMatcher, is_show_promo_pattern
from utils.constants import LEARNING_SHOW_PROMO_FLOORS, NON_SPONSOR_PATTERN_CATEGORIES

SLUG = 'show-promo-test'
EPISODE_ID = 'abcdef012345'
CATEGORIES = ('interaction', 'self_promo', 'cross_promo')

# ~30 s read, well over the cross_promo character floor.
ASK = (
    "Before we get into it, if you are enjoying the show please hit that follow "
    "button right now. Take a minute on the app you are listening to this on to "
    "leave us a rating and a review. It really helps more people find the show "
    "and it takes only a few seconds of your time. Thank you so much for "
    "listening and for supporting us every single week."
)
# Above the interaction floor (150 chars) but under the cross_promo floor (225).
MID_TEXT = (
    "If you are enjoying the show please hit that follow button right now and "
    "take a minute on the app you are listening to this on to leave us a review."
    " Thank you."
)
SHORT_TEXT = "Please subscribe to the show and leave us a quick review, thank you."


def _seed(db):
    db.create_podcast(SLUG, 'https://example.com/feed.xml', 'Show Promo Test')
    db.upsert_episode(slug=SLUG, episode_id=EPISODE_ID,
                      original_url='https://example.com/ep.mp3',
                      title='Test Episode', original_duration=3600.0)


def _confirm(db, text, category, start=110.0, end=140.0, original_ad=None):
    return _resolve_or_create_pattern_from_text(
        db, PatternService(db), SLUG, EPISODE_ID, text,
        original_ad if original_ad is not None else {'start': start, 'end': end},
        label='confirmed', category=category)


def _learn(db, text, category, start=110.0, end=140.0, **kw):
    return TextPatternMatcher(db=db).create_pattern_from_ad(
        segments=[{'start': start, 'end': end, 'text': text}], start=start, end=end,
        sponsor=None, scope='podcast', podcast_id=SLUG, episode_id=EPISODE_ID,
        category=category, **kw)


# ---------------------------------------------------------------- creation

@pytest.mark.parametrize('category', CATEGORIES)
def test_confirm_creates_sponsorless_podcast_pattern_with_category(temp_db, category):
    _seed(temp_db)
    pid, ids = _confirm(temp_db, ASK, category)
    assert pid is not None and ids == [pid]
    pattern = temp_db.get_ad_pattern_by_id(pid)
    assert pattern['sponsor'] is None and pattern['sponsor_id'] is None
    assert pattern['category'] == category
    assert pattern['scope'] == 'podcast' and pattern['podcast_id'] == SLUG
    assert pattern['text_template'] == ASK


@pytest.mark.parametrize('category', [None, 'sponsor'])
def test_confirm_without_sponsor_and_non_promo_category_still_skipped(temp_db, category, caplog):
    _seed(temp_db)
    with caplog.at_level(logging.INFO):
        pid, ids = _confirm(temp_db, ASK, category)
    assert (pid, ids) == (None, [])
    assert 'Skipped pattern creation (no sponsor detected)' in caplog.text


def test_sponsor_marker_is_unchanged(temp_db):
    _seed(temp_db)
    text = ("This episode is brought to you by Acme, makers of fine widgets "
            "since 1999, visit acme dot com today for a special discount code.")
    pid, _ = _confirm(temp_db, text, 'sponsor',
                      original_ad={'sponsor': 'Acme', 'start': 0.0, 'end': 60.0})
    assert temp_db.get_ad_pattern_by_id(pid)['sponsor'] == 'Acme'


def test_show_name_in_sponsor_slot_does_not_block_a_show_promo(temp_db):
    _seed(temp_db)
    pid, _ = _confirm(temp_db, ASK, 'self_promo',
                      original_ad={'sponsor': 'Show Promo Test', 'start': 110.0, 'end': 140.0})
    assert temp_db.get_ad_pattern_by_id(pid)['sponsor'] is None


def test_a_real_sponsor_on_a_self_promo_marker_keeps_the_sponsor_path(temp_db):
    _seed(temp_db)
    text = ("Support the show on Patreon at patreon dot com slash example, "
            "Patreon members get bonus episodes every single week, join Patreon "
            "today and help us keep making the show for you the listener.")
    pid, _ = _confirm(temp_db, text, 'self_promo',
                      original_ad={'sponsor': 'Patreon', 'start': 0.0, 'end': 30.0})
    assert temp_db.get_ad_pattern_by_id(pid)['sponsor'] == 'Patreon'


def test_multi_transition_text_is_one_sponsorless_pattern_not_split(temp_db):
    _seed(temp_db)
    text = ASK + " Thanks to everyone who already did. " + ASK[:120]
    pid, ids = _confirm(temp_db, text, 'interaction')
    assert pid is not None and len(ids) == 1


# ------------------------------------------------------------------ floors

def test_floor_values_are_ordered_and_documented():
    assert set(LEARNING_SHOW_PROMO_FLOORS) == set(NON_SPONSOR_PATTERN_CATEGORIES) == set(CATEGORIES)
    assert LEARNING_SHOW_PROMO_FLOORS['interaction'] == (20, 150)
    assert LEARNING_SHOW_PROMO_FLOORS['self_promo'] == (20, 150)
    assert LEARNING_SHOW_PROMO_FLOORS['cross_promo'] == (30, 225)


def test_short_text_is_refused_with_a_logged_reason(temp_db, caplog):
    _seed(temp_db)
    with caplog.at_level(logging.WARNING):
        pid = _learn(temp_db, SHORT_TEXT, 'interaction', 110.0, 140.0)
    assert pid is None
    assert 'below the 150 character floor' in caplog.text


def test_short_duration_is_refused_with_a_logged_reason(temp_db, caplog):
    _seed(temp_db)
    with caplog.at_level(logging.WARNING):
        pid = _learn(temp_db, ASK, 'interaction', 110.0, 125.0)
    assert pid is None
    assert 'below the 20s floor' in caplog.text


def test_interaction_floor_is_inclusive(temp_db):
    _seed(temp_db)
    assert _learn(temp_db, ASK, 'interaction', 110.0, 130.0) is not None


def test_cross_promo_needs_more_text_and_more_time_than_interaction(temp_db, caplog):
    _seed(temp_db)
    assert len(MID_TEXT) >= 150 and len(MID_TEXT) < 225
    with caplog.at_level(logging.WARNING):
        assert _learn(temp_db, MID_TEXT, 'cross_promo', 110.0, 145.0) is None
        assert _learn(temp_db, ASK, 'cross_promo', 110.0, 135.0) is None   # 25 s < 30 s
    assert 'below the 225 character floor' in caplog.text
    assert 'below the 30s floor' in caplog.text
    assert _learn(temp_db, MID_TEXT, 'interaction', 110.0, 140.0) is not None
    assert _learn(temp_db, ASK, 'cross_promo', 110.0, 140.0) is not None


def test_existing_maximum_duration_still_applies(temp_db):
    _seed(temp_db)
    assert _learn(temp_db, ASK, 'interaction', 0.0, 200.0) is None


def test_configured_minimum_duration_can_only_raise_the_floor(temp_db):
    _seed(temp_db)
    temp_db.set_setting('learning_min_pattern_duration', '60')
    assert _learn(temp_db, ASK, 'interaction', 110.0, 140.0) is None


@pytest.mark.parametrize('scope,podcast_id', [('global', None), ('network', None), ('podcast', None)])
def test_sponsorless_is_podcast_scoped_only(temp_db, scope, podcast_id):
    _seed(temp_db)
    pid = TextPatternMatcher(db=temp_db).create_pattern_from_ad(
        segments=[{'start': 110.0, 'end': 140.0, 'text': ASK}], start=110.0, end=140.0,
        sponsor=None, scope=scope, podcast_id=podcast_id, network_id='n1',
        episode_id=EPISODE_ID, category='interaction')
    assert pid is None


def test_sponsorless_without_a_promo_category_is_still_rejected(temp_db):
    _seed(temp_db)
    assert _learn(temp_db, ASK, None) is None
    assert _learn(temp_db, ASK, 'sponsor') is None
    assert _learn(temp_db, ASK, 'intro') is None


def test_second_confirm_of_the_same_read_reuses_the_pattern(temp_db):
    _seed(temp_db)
    first, _ = _confirm(temp_db, ASK, 'interaction')
    second, _ = _confirm(temp_db, ASK, 'interaction')
    assert first == second


# --------------------------------------------------------------- never global

def test_never_promoted_beyond_podcast_scope(temp_db):
    _seed(temp_db)
    pid, _ = _confirm(temp_db, ASK, 'interaction')
    svc = PatternService(temp_db)
    assert svc.check_sponsor_global_promotion('') is False
    assert temp_db.get_ad_pattern_by_id(pid)['scope'] == 'podcast'
    assert svc.auto_promote_sponsor_patterns('') == 0
    assert temp_db.get_ad_pattern_by_id(pid)['scope'] == 'podcast'


# ---------------------------------------------------------------- matching

_TOPICS = [
    'the founder explains how she raised her first round of funding while still working nights',
    'we talk about hiring slowly, firing quickly and why culture is a decision you make every day',
    'his father ran a small hardware store and taught him that margins matter more than slogans',
    'the guest remembers moving cities with two suitcases and a borrowed laptop at nineteen',
    'she describes the week the product nearly collapsed and what the team did to save it',
    'they disagree about whether discipline or curiosity matters more over a long career',
    'a long digression about training for a marathon, sleep schedules and recovery routines',
    'the conversation turns to money, risk, and the stories we tell ourselves about failure',
]


def _later_episode_segments():
    """A realistic episode: distinct talk around the one repeated ask."""
    segs, t = [], 0.0
    for i in range(4):
        segs.append({'start': t, 'end': t + 27.5, 'text': _TOPICS[i] * 2})
        t += 27.5
    segs.append({'start': 110.0, 'end': 140.0, 'text': ASK})
    t = 140.0
    for i in range(4, 8):
        segs.append({'start': t, 'end': t + 27.5, 'text': _TOPICS[i] * 2})
        t += 27.5
    return segs


@pytest.mark.parametrize('category', CATEGORIES)
def test_a_later_episode_is_matched_with_the_pattern_category(temp_db, category):
    _seed(temp_db)
    pid, _ = _confirm(temp_db, ASK, category)
    matches = TextPatternMatcher(db=temp_db).find_matches(_later_episode_segments(), podcast_id=SLUG)
    hit = [m for m in matches if m.pattern_id == pid]
    assert hit, 'the learned read was not matched'
    assert hit[0].category == category
    assert hit[0].sponsor is None
    assert hit[0].defined is False


def test_another_show_does_not_match(temp_db):
    _seed(temp_db)
    _confirm(temp_db, ASK, 'interaction')
    assert TextPatternMatcher(db=temp_db).find_matches(
        _later_episode_segments(), podcast_id='some-other-show') == []


def test_sponsorless_pattern_with_an_outro_stays_in_content_matching(temp_db):
    """An auto pattern with outro variants normally needs sponsor evidence in
    the outro to stay in content matching; a show promo has no sponsor."""
    _seed(temp_db)
    pid, _ = _confirm(temp_db, ASK, 'interaction')
    pattern = temp_db.get_ad_pattern_by_id(pid)
    assert pattern['outro_variants']
    matcher = TextPatternMatcher(db=temp_db)
    matcher._load_patterns()
    loaded = [p for p in matcher._patterns if p.id == pid][0]
    assert loaded.is_show_promo is True and loaded.is_defined is False
    seen = []
    original = matcher._find_content_matches

    def spy(full_text, segments, segment_map, patterns, *a, **kw):
        seen.extend(p.id for p in patterns)
        return original(full_text, segments, segment_map, patterns, *a, **kw)

    matcher._find_content_matches = spy
    matcher.find_matches(_later_episode_segments(), podcast_id=SLUG)
    assert pid in seen


def test_is_show_promo_pattern_helper():
    assert is_show_promo_pattern({'sponsor_id': None, 'category': 'cross_promo'})
    assert not is_show_promo_pattern({'sponsor_id': 3, 'category': 'cross_promo'})
    assert not is_show_promo_pattern({'sponsor_id': None, 'category': 'sponsor'})
    assert not is_show_promo_pattern({'sponsor_id': None, 'category': None})


# ------------------------------------------------- feed keep action honoured

def _pattern_marker(temp_db, category):
    _seed(temp_db)
    pid, _ = _confirm(temp_db, ASK, category)
    match = [m for m in TextPatternMatcher(db=temp_db).find_matches(
        _later_episode_segments(), podcast_id=SLUG) if m.pattern_id == pid][0]
    return {'start': match.start, 'end': match.end, 'confidence': match.confidence,
            'detection_stage': 'text_pattern', 'pattern_id': pid,
            'category': match.category, 'pattern_defined': match.defined}


@pytest.mark.parametrize('action', ['keep', 'mark'])
def test_feed_keep_action_keeps_a_matched_show_promo(temp_db, action):
    marker = _pattern_marker(temp_db, 'interaction')
    actions = {c: 'remove' for c in ('sponsor', 'cross_promo', 'self_promo', 'interaction',
                                     'intro', 'outro', 'recap')}
    actions['interaction'] = action
    keep_ads, remove_ads = keep_rules._partition_keep_ads([marker], actions)
    assert len(keep_ads) == 1 and remove_ads == []
    assert keep_ads[0]['was_cut'] is False and keep_ads[0]['action_applied'] == action


def test_default_action_removes_a_matched_show_promo(temp_db):
    marker = _pattern_marker(temp_db, 'interaction')
    actions = {c: 'remove' for c in ('sponsor', 'cross_promo', 'self_promo', 'interaction',
                                     'intro', 'outro', 'recap')}
    keep_ads, remove_ads = keep_rules._partition_keep_ads([marker], actions)
    assert keep_ads == [] and remove_ads == [marker]


def test_user_created_sponsorless_pattern_is_not_defined_either(temp_db):
    _seed(temp_db)
    pid = temp_db.create_ad_pattern(scope='podcast', text_template=ASK, podcast_id=SLUG,
                                    created_by='user', category='interaction')
    matcher = TextPatternMatcher(db=temp_db)
    matcher._load_patterns()
    loaded = [p for p in matcher._patterns if p.id == pid][0]
    assert loaded.is_defined is False


# ------------------------------------------------------- reject escape hatch

def test_reject_correction_counts_a_false_positive_and_disable_works(temp_db):
    _seed(temp_db)
    pid, _ = _confirm(temp_db, ASK, 'interaction')
    from main_app import app
    with app.app_context():
        _handle_reject_correction(temp_db, SLUG, EPISODE_ID,
                                  {'start': 110.0, 'end': 140.0, 'pattern_id': pid})
    assert temp_db.get_ad_pattern_by_id(pid)['false_positive_count'] == 1
    temp_db.update_ad_pattern(pid, is_active=0, disabled_reason='test')
    assert TextPatternMatcher(db=temp_db).find_matches(
        _later_episode_segments(), podcast_id=SLUG) == []


# ----------------------------------------------------------- community export

def test_community_export_refuses_sponsorless_with_a_clear_reason(temp_db):
    from community_export import _quality_gates
    _seed(temp_db)
    pid, _ = _confirm(temp_db, ASK, 'interaction')
    pattern = temp_db.get_ad_pattern_by_id(pid)
    pattern['confirmation_count'] = 3
    reasons = _quality_gates(pattern, [])
    assert any('show-specific' in r and 'not exported' in r for r in reasons)
    assert 'sponsor_id is required' not in reasons


# ----------------------------------------------------------------- stats

def test_stats_do_not_count_show_promos_as_unknown_sponsor(temp_db):
    from unittest.mock import patch
    from api.patterns import get_pattern_stats
    from main_app import app
    _seed(temp_db)
    _confirm(temp_db, ASK, 'interaction')
    temp_db.create_ad_pattern(scope='podcast', text_template='x' * 80, podcast_id=SLUG)
    with app.test_request_context(), patch('api.patterns.get_database', return_value=temp_db):
        stats = get_pattern_stats().get_json()
    assert stats['no_sponsor'] == 1
    assert stats['show_promo'] == 1
    assert len(stats['no_sponsor_patterns']) == 1


# -------------------------------------------------------- manual create path

def _create_payload(text=ASK, category='interaction', **kw):
    payload = {'type': 'create', 'start': 110.0, 'end': 140.0, 'text_template': text,
               'category': category, 'reason': ''}
    payload.update(kw)
    return payload


def _post(client, payload):
    import json
    return client.post(f'/api/v1/episodes/{SLUG}/{EPISODE_ID}/corrections',
                       data=json.dumps(payload), content_type='application/json')


@pytest.fixture
def client(temp_db):
    from unittest.mock import patch
    from main_app import app
    app.config['TESTING'] = True
    with patch('api.patterns.get_database', return_value=temp_db), app.test_client() as c:
        yield c


def test_manual_create_without_sponsor_for_a_promo_category(temp_db, client):
    _seed(temp_db)
    resp = _post(client, _create_payload())
    assert resp.status_code == 200, resp.get_json()
    pattern = temp_db.get_ad_pattern_by_id(resp.get_json()['pattern_id'])
    assert pattern['sponsor'] is None and pattern['category'] == 'interaction'
    assert pattern['scope'] == 'podcast'
    markers = __import__('json').loads(temp_db.get_episode(SLUG, EPISODE_ID)['ad_markers_json'])
    assert markers[0]['sponsor'] is None and markers[0]['category'] == 'interaction'


def test_manual_create_still_requires_a_sponsor_without_a_promo_category(temp_db, client):
    _seed(temp_db)
    resp = _post(client, _create_payload(category='sponsor'))
    assert resp.status_code == 400 and 'Sponsor is required' in resp.get_data(as_text=True)
    assert _post(client, _create_payload(category=None)).status_code == 400


def test_manual_create_sponsorless_is_podcast_scope_only(temp_db, client):
    _seed(temp_db)
    resp = _post(client, _create_payload(scope='global'))
    assert resp.status_code == 400


def test_manual_create_applies_the_floors(temp_db, client):
    _seed(temp_db)
    resp = _post(client, _create_payload(text=MID_TEXT, category='cross_promo'))
    assert resp.status_code == 400 and '225' in resp.get_data(as_text=True)
    resp = _post(client, _create_payload(start=110.0, end=115.0))
    assert resp.status_code == 400 and '20' in resp.get_data(as_text=True)


# ------------------------------------------------------------ reviewer skip

def _text_pattern_ad(pattern_id, **kw):
    ad = {'start': 110.0, 'end': 140.0, 'confidence': 0.9,
          'detection_stage': 'text_pattern', 'pattern_id': pattern_id,
          'category': 'interaction', 'sponsor': None}
    ad.update(kw)
    return ad


def test_reviewer_skips_a_sponsorless_promo_pattern_match(temp_db):
    """(a) not sent to the reviewer, and the ad stays in the cut list."""
    from tests.unit.reviewer_test_utils import _build_reviewer, _mock_episode_meta
    _seed(temp_db)
    pid, _ = _confirm(temp_db, ASK, 'interaction')
    ad = _text_pattern_ad(pid)
    assert _skips_reviewer(ad, temp_db) is True

    reviewer = _build_reviewer({'review_prompt': 'review', 'resurrect_prompt': 'resurrect'})
    reviewer.db = temp_db
    result = reviewer.review(accepted_ads=[ad], resurrection_eligible=[], segments=[],
                             episode_meta=_mock_episode_meta(), pass_num=1,
                             pass_model='claude-test')
    assert result.accepted_after_review == [ad]
    assert result.verdicts == []
    reviewer._llm_client.messages_create.assert_not_called()


def test_reviewer_still_reviews_a_sponsor_text_pattern_match(temp_db):
    """(b) unchanged for sponsor patterns."""
    _seed(temp_db)
    text = ("This episode is brought to you by Acme, makers of fine widgets "
            "since 1999, visit acme dot com today for a special discount code.")
    pid, _ = _confirm(temp_db, text, 'sponsor',
                      original_ad={'sponsor': 'Acme', 'start': 0.0, 'end': 60.0})
    assert _skips_reviewer(_text_pattern_ad(pid, sponsor='Acme', category='sponsor'), temp_db) is False
    # a sponsor pattern that merely carries a promo category is still a sponsor pattern
    promo_with_sponsor, _ = _confirm(
        temp_db, text + ' Acme again.', 'self_promo',
        original_ad={'sponsor': 'Acme', 'start': 0.0, 'end': 60.0})
    assert _skips_reviewer(_text_pattern_ad(promo_with_sponsor, sponsor='Acme',
                                            category='self_promo'), temp_db) is False


def test_reviewer_does_not_skip_an_inactive_sponsorless_pattern(temp_db):
    """(c) disabled pattern, no skip."""
    _seed(temp_db)
    pid, _ = _confirm(temp_db, ASK, 'interaction')
    temp_db.update_ad_pattern(pid, is_active=0, disabled_reason='test')
    assert _skips_reviewer(_text_pattern_ad(pid), temp_db) is False


def test_reviewer_skip_needs_the_text_pattern_stage_and_a_db(temp_db):
    _seed(temp_db)
    pid, _ = _confirm(temp_db, ASK, 'interaction')
    assert _skips_reviewer(_text_pattern_ad(pid, detection_stage='claude'), temp_db) is False
    assert _skips_reviewer(_text_pattern_ad(pid, merged_distinct_ads=[{}]), temp_db) is False
    assert _skips_reviewer(_text_pattern_ad(pid), None) is False
    assert _skips_reviewer(_text_pattern_ad(None), temp_db) is False
    assert _skips_reviewer(_text_pattern_ad(pid), MagicMock()) is False


def test_reviewer_skip_does_not_apply_to_a_sponsorless_pattern_without_a_promo_category(temp_db):
    _seed(temp_db)
    pid = temp_db.create_ad_pattern(scope='podcast', text_template=ASK, podcast_id=SLUG,
                                    category='intro')
    assert _skips_reviewer(_text_pattern_ad(pid, category='intro'), temp_db) is False


def test_keep_action_marker_never_reaches_the_reviewer_d(temp_db):
    """(d) a feed keep action pulls the marker out before the reviewer."""
    marker = _pattern_marker(temp_db, 'self_promo')
    actions = {c: 'remove' for c in ('sponsor', 'cross_promo', 'self_promo', 'interaction',
                                     'intro', 'outro', 'recap')}
    actions['self_promo'] = 'keep'
    keep_ads, remove_ads = keep_rules._partition_keep_ads([marker], actions)
    assert remove_ads == [] and keep_ads[0]['action_applied'] == 'keep'
