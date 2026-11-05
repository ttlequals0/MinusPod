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

from ad_detector import AdDetector
from ad_reviewer import _skips_reviewer
from api.patterns import (_create_patterns_from_segments, _handle_confirm_correction,
                          _handle_reject_correction,
                          _resolve_or_create_pattern_from_text)
from main_app.pipeline import keep_rules
from pattern_service import PatternService
from text_pattern_matcher import TextMatch, TextPatternMatcher, is_show_promo_pattern
from utils.markers import note_merged_members
from utils.constants import (LEARNING_SHOW_PROMO_FLOORS, NON_SPONSOR_PATTERN_CATEGORIES,
                             show_promo_floor_problem)

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
    assert pattern['created_by'] == 'show_promo'
    # a show promo is matched on its template alone: no phrase variants whose
    # last sentence could be dialogue
    import json
    assert json.loads(pattern['intro_variants']) == []
    assert json.loads(pattern['outro_variants']) == []


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


def _row(**kw):
    row = {'created_by': 'show_promo', 'scope': 'podcast', 'sponsor_id': None,
           'category': 'interaction'}
    row.update(kw)
    return row


def test_is_show_promo_pattern_needs_the_explicit_provenance():
    assert is_show_promo_pattern(_row())
    assert is_show_promo_pattern(_row(category='cross_promo'))
    # legacy sponsorless rows, hand-made rows and rows that lost their sponsor
    assert not is_show_promo_pattern(_row(created_by='auto'))
    assert not is_show_promo_pattern(_row(created_by='user'))
    assert not is_show_promo_pattern(_row(created_by=None))
    # global / network rows, rows with a sponsor, other categories
    assert not is_show_promo_pattern(_row(scope='global'))
    assert not is_show_promo_pattern(_row(scope='network'))
    assert not is_show_promo_pattern(_row(sponsor_id=3))
    assert not is_show_promo_pattern(_row(category='sponsor'))
    assert not is_show_promo_pattern(_row(category=None))


def test_legacy_sponsorless_rows_keep_their_old_behaviour(temp_db):
    _seed(temp_db)
    legacy = temp_db.create_ad_pattern(scope='podcast', text_template=ASK, podcast_id=SLUG,
                                       category='interaction')           # created_by 'auto'
    hand_made = temp_db.create_ad_pattern(scope='podcast', text_template=ASK + ' Again.',
                                          podcast_id=SLUG, created_by='user',
                                          category='interaction')
    matcher = TextPatternMatcher(db=temp_db)
    matcher._load_patterns()
    loaded = {p.id: p for p in matcher._patterns}
    assert loaded[legacy].is_show_promo is False
    assert loaded[hand_made].is_show_promo is False
    assert loaded[hand_made].is_defined is True        # unchanged tier-1 trust
    assert loaded[legacy].is_defined is False


def test_show_promo_stays_in_content_matching_without_phrase_variants(temp_db):
    _seed(temp_db)
    pid, _ = _confirm(temp_db, ASK, 'interaction')
    matcher = TextPatternMatcher(db=temp_db)
    seen = []
    original = matcher._find_content_matches

    def spy(full_text, segments, segment_map, patterns, *a, **kw):
        seen.extend(p.id for p in patterns)
        return original(full_text, segments, segment_map, patterns, *a, **kw)

    matcher._find_content_matches = spy
    matcher.find_matches(_later_episode_segments(), podcast_id=SLUG)
    assert pid in seen


def test_a_dialogue_sentence_at_the_end_of_the_confirmed_span_is_never_a_boundary(temp_db):
    """The confirmed span ran into dialogue. That sentence must not become an
    outro phrase that cuts the same words wherever they are spoken later."""
    _seed(temp_db)
    dialogue = ("So anyway my first guest today built a company from nothing and "
                "I cannot wait to hear how she did it, let us get started.")
    pid, _ = _confirm(temp_db, ASK + ' ' + dialogue, 'interaction')
    segs = _later_episode_segments()
    segs.append({'start': 300.0, 'end': 330.0, 'text': dialogue})
    matches = [m for m in TextPatternMatcher(db=temp_db).find_matches(segs, podcast_id=SLUG)
               if m.pattern_id == pid]
    assert not [m for m in matches if m.end > 250.0]


def test_a_show_promo_is_not_merged_with_a_neighbouring_non_show_promo_match():
    matcher = TextPatternMatcher(db=None)
    promo = TextMatch(pattern_id=1, start=100.0, end=130.0, confidence=0.9,
                      category='interaction', show_promo=True)
    other = TextMatch(pattern_id=2, start=131.0, end=160.0, confidence=0.95,
                      category='interaction', show_promo=False)
    merged = matcher._merge_matches([promo, other])
    assert [(m.pattern_id, m.show_promo) for m in merged] == [(1, True), (2, False)]
    # Nor with another show read: it would cut the talk between the two.
    twin = TextMatch(pattern_id=3, start=131.0, end=160.0, confidence=0.8,
                     category='interaction', show_promo=True)
    assert len(matcher._merge_matches([promo, twin])) == 2


def test_the_flag_is_carried_from_the_match_onto_the_ad(temp_db):
    _seed(temp_db)
    pid, _ = _confirm(temp_db, ASK, 'interaction')
    match = [m for m in TextPatternMatcher(db=temp_db).find_matches(
        _later_episode_segments(), podcast_id=SLUG) if m.pattern_id == pid][0]
    assert match.show_promo is True
    ads, regions = [], []
    AdDetector._add_pattern_match(MagicMock(), match, 'text_pattern', 'content', ads, regions, 'ep')
    assert ads[0]['show_promo'] is True and ads[0]['pattern_defined'] is False


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
    legacy = dict(pattern, created_by='auto')
    assert 'sponsor_id is required' in _quality_gates(legacy, [])


# ----------------------------------------------------------------- stats

def test_stats_do_not_count_show_promos_as_unknown_sponsor(temp_db):
    from unittest.mock import patch
    from api.patterns import get_pattern_stats
    from main_app import app
    _seed(temp_db)
    _confirm(temp_db, ASK, 'interaction')
    temp_db.create_ad_pattern(scope='podcast', text_template='x' * 80, podcast_id=SLUG)
    temp_db.create_ad_pattern(scope='podcast', text_template='y' * 80, podcast_id=SLUG,
                              category='interaction')      # legacy sponsorless row
    with app.test_request_context(), patch('api.patterns.get_database', return_value=temp_db):
        stats = get_pattern_stats().get_json()
    assert stats['no_sponsor'] == 2      # both legacy rows stay unknown-sponsor defects
    assert stats['show_promo'] == 1
    assert len(stats['no_sponsor_patterns']) == 2


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
    assert pattern['scope'] == 'podcast' and pattern['created_by'] == 'show_promo'
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
    resp = _post(client, _create_payload(text=MID_TEXT, category='cross_promo',
                                         start=110.0, end=145.0))
    assert resp.status_code == 400 and '225' in resp.get_data(as_text=True)
    resp = _post(client, _create_payload(start=110.0, end=115.0))
    assert resp.status_code == 400 and '20' in resp.get_data(as_text=True)
    assert 'Interaction read not learned for this show:' in resp.get_data(as_text=True)


@pytest.mark.parametrize('category, label', [('self_promo', 'Self-promo'),
                                             ('cross_promo', 'Cross-promo')])
def test_manual_create_refusal_names_the_category_in_words(temp_db, client, category, label):
    _seed(temp_db)
    body = _post(client, _create_payload(category=category, start=110.0, end=115.0)).get_data(
        as_text=True)
    assert f'{label} read not learned for this show:' in body
    assert category not in body


# --------------------------------------------------------- one floor function

def test_floor_function_is_the_one_rule():
    assert show_promo_floor_problem('interaction', 30, 200, 15, 120) is None
    assert 'below the 20s floor' in show_promo_floor_problem('interaction', 19, 200, 15, 120)
    assert 'below the 150 character floor' in show_promo_floor_problem('interaction', 30, 149, 15, 120)
    assert 'below the 30s floor' in show_promo_floor_problem('cross_promo', 29, 300, 15, 120)
    assert 'below the 225 character floor' in show_promo_floor_problem('cross_promo', 31, 224, 15, 120)
    # the configured minimum can only raise the duration floor
    assert 'below the 30s floor' in show_promo_floor_problem('interaction', 22, 200, 30, 120)
    assert show_promo_floor_problem('interaction', 22, 200, 10, 120) is None
    assert 'exceeds the 120s maximum' in show_promo_floor_problem('interaction', 300, 200, 15, 120)


def test_confirm_and_manual_create_agree_on_a_22_second_span_with_a_30_second_minimum(temp_db, client):
    _seed(temp_db)
    temp_db.set_setting('learning_min_pattern_duration', '30')
    assert _learn(temp_db, ASK, 'interaction', 110.0, 132.0) is None
    resp = _post(client, _create_payload(start=110.0, end=132.0))
    assert resp.status_code == 400


def test_confirm_and_manual_create_agree_on_a_300_second_span(temp_db, client):
    _seed(temp_db)
    assert _learn(temp_db, ASK, 'interaction', 0.0, 300.0) is None
    resp = _post(client, _create_payload(start=0.0, end=300.0))
    assert resp.status_code == 400


# ------------------------------------------- multi-segment pieces are gated too

def test_a_sponsorless_piece_in_a_show_promo_category_is_refused_not_stored_ungated(temp_db):
    _seed(temp_db)
    segments = [
        {'text': 'Support the show on Patreon, Patreon members get bonus episodes every week.',
         'sponsor': 'Patreon'},
        {'text': 'Also hit follow on the app you are listening on and leave a short review.',
         'sponsor': None},
    ]
    primary, ids = _create_patterns_from_segments(
        temp_db, segments, scope='podcast', podcast_id_str=SLUG, network_id=None,
        episode_id=EPISODE_ID, primary_sponsor='Patreon', category='self_promo',
        log_context='test')
    assert len(ids) == 1
    assert temp_db.get_ad_pattern_by_id(primary)['sponsor'] == 'Patreon'
    assert [p for p in temp_db.get_ad_patterns(active_only=False) if p['sponsor_id'] is None] == []


# ------------------------------------------------------------ reviewer skip

def _flagged_ad(**kw):
    ad = {'start': 110.0, 'end': 140.0, 'confidence': 0.9,
          'detection_stage': 'text_pattern', 'pattern_id': 7, 'show_promo': True,
          'category': 'interaction', 'sponsor': None}
    ad.update(kw)
    return ad


def _matched_ad(temp_db, pid):
    match = [m for m in TextPatternMatcher(db=temp_db).find_matches(
        _later_episode_segments(), podcast_id=SLUG) if m.pattern_id == pid][0]
    ads, regions = [], []
    AdDetector._add_pattern_match(MagicMock(), match, 'text_pattern', 'content', ads, regions, 'ep')
    return ads[0]


def test_reviewer_skips_a_show_promo_match_and_keeps_it_cut(temp_db):
    """(a) not sent to the reviewer, and the ad stays in the cut list."""
    from tests.unit.reviewer_test_utils import _build_reviewer, _mock_episode_meta
    _seed(temp_db)
    pid, _ = _confirm(temp_db, ASK, 'interaction')
    ad = _matched_ad(temp_db, pid)
    assert _skips_reviewer(ad) is True
    reviewer = _build_reviewer({'review_prompt': 'review', 'resurrect_prompt': 'resurrect'})
    result = reviewer.review(accepted_ads=[ad], resurrection_eligible=[], segments=[],
                             episode_meta=_mock_episode_meta(), pass_num=1,
                             pass_model='claude-test')
    assert result.accepted_after_review == [ad]
    assert result.verdicts == []
    reviewer._llm_client.messages_create.assert_not_called()


def test_reviewer_does_not_query_the_database_to_decide(temp_db):
    assert _skips_reviewer(_flagged_ad()) is True        # flag only, no db argument at all


def test_reviewer_still_reviews_a_sponsor_text_pattern_match(temp_db):
    """(b) unchanged for sponsor patterns."""
    _seed(temp_db)
    text = ("This episode is brought to you by Acme, makers of fine widgets "
            "since 1999, visit acme dot com today for a special discount code.")
    pid, _ = _confirm(temp_db, text, 'sponsor',
                      original_ad={'sponsor': 'Acme', 'start': 0.0, 'end': 60.0})
    match = TextMatch(pattern_id=pid, start=0.0, end=60.0, confidence=0.9, sponsor='Acme',
                      category='sponsor')
    ads, regions = [], []
    AdDetector._add_pattern_match(MagicMock(), match, 'text_pattern', 'content', ads, regions, 'ep')
    assert ads[0]['show_promo'] is False
    assert _skips_reviewer(ads[0]) is False


def test_a_disabled_show_promo_pattern_produces_no_match_and_no_skip(temp_db):
    """(c) an inactive pattern is not loaded, so nothing carries the flag."""
    _seed(temp_db)
    pid, _ = _confirm(temp_db, ASK, 'interaction')
    temp_db.update_ad_pattern(pid, is_active=0, disabled_reason='test')
    assert TextPatternMatcher(db=temp_db).find_matches(
        _later_episode_segments(), podcast_id=SLUG) == []
    assert _skips_reviewer(_flagged_ad(show_promo=False)) is False
    assert _skips_reviewer({k: v for k, v in _flagged_ad().items() if k != 'show_promo'}) is False


def test_reviewer_skip_needs_the_text_pattern_stage_and_no_distinct_merge():
    assert _skips_reviewer(_flagged_ad(detection_stage='claude')) is False
    assert _skips_reviewer(_flagged_ad(merged_distinct_ads=True)) is False


def test_a_show_promo_folded_with_any_other_member_is_reviewed():
    promo = _flagged_ad()
    claude = {'start': 120.0, 'end': 150.0, 'confidence': 0.9, 'detection_stage': 'claude',
              'category': 'interaction'}
    note_merged_members(promo, claude)
    promo['end'] = 150.0
    assert _skips_reviewer(promo) is False


def test_two_show_promo_members_still_skip():
    promo = _flagged_ad()
    twin = _flagged_ad(start=125.0, end=150.0, pattern_id=8)
    note_merged_members(promo, twin)
    assert _skips_reviewer(promo) is True


def test_keep_action_marker_never_reaches_the_reviewer_d(temp_db):
    """(d) a feed keep action pulls the marker out before the reviewer."""
    marker = _pattern_marker(temp_db, 'self_promo')
    actions = {c: 'remove' for c in ('sponsor', 'cross_promo', 'self_promo', 'interaction',
                                     'intro', 'outro', 'recap')}
    actions['self_promo'] = 'keep'
    keep_ads, remove_ads = keep_rules._partition_keep_ads([marker], actions)
    assert remove_ads == [] and keep_ads[0]['action_applied'] == 'keep'


# ------------------------------------------------ one predicate, one rules payload

def test_one_predicate_for_sponsorless_plus_promo_category():
    from utils.constants import is_show_promo_candidate
    assert is_show_promo_candidate(None, 'interaction')
    assert is_show_promo_candidate('  ', 'cross_promo')
    assert not is_show_promo_candidate('Acme', 'interaction')
    assert not is_show_promo_candidate(None, 'sponsor')
    assert not is_show_promo_candidate(None, None)


def test_public_duration_bounds_and_rules_use_the_configured_minimum(temp_db):
    from text_pattern_matcher import get_pattern_duration_bounds, show_promo_rules
    assert get_pattern_duration_bounds(temp_db) == (15, 120)
    assert show_promo_rules(temp_db) == {
        'floors': {'interaction': {'seconds': 20, 'chars': 150},
                   'self_promo': {'seconds': 20, 'chars': 150},
                   'cross_promo': {'seconds': 30, 'chars': 225}},
        'maxSeconds': 120}
    temp_db.set_setting('learning_min_pattern_duration', '40')
    rules = show_promo_rules(temp_db)
    assert rules['floors']['interaction']['seconds'] == 40
    assert rules['floors']['cross_promo']['seconds'] == 40


def test_pattern_rows_serialize_is_show_promo(temp_db, client):
    _seed(temp_db)
    pid, _ = _confirm(temp_db, ASK, 'interaction')
    legacy = temp_db.create_ad_pattern(scope='podcast', text_template='z' * 80,
                                       podcast_id=SLUG, category='interaction')
    rows = {p['id']: p for p in client.get('/api/v1/patterns').get_json()['patterns']}
    assert rows[pid]['is_show_promo'] is True
    assert rows[legacy]['is_show_promo'] is False
    assert client.get(f'/api/v1/patterns/{pid}').get_json()['is_show_promo'] is True


def test_settings_payload_carries_the_rules_but_the_config_export_does_not(temp_db, client):
    body = client.get('/api/v1/settings').get_json()
    assert body['showPromoRules']['floors']['cross_promo'] == {'seconds': 30, 'chars': 225}
    assert body['showPromoRules']['maxSeconds'] == 120
    from api.settings.core import _build_settings_payload
    assert 'showPromoRules' not in _build_settings_payload()


# ------------------------------------------- a trimmed confirm is judged trimmed

def _timed(t):
    h, rem = divmod(t, 3600)
    m, s = divmod(rem, 60)
    return f"{int(h):02d}:{int(m):02d}:{s:06.3f}"


def _ask_transcript(start=100.0, end=140.0, step=2.0, words_per_line=9):
    """ASK's words cycled over [start, end) in `step`-second VTT lines, dense
    enough that any 10 s slice clears the 150 character floor."""
    words = ASK.split()
    lines = []
    for i in range(int((end - start) / step)):
        chunk = ' '.join(words[(i * words_per_line + j) % len(words)]
                         for j in range(words_per_line))
        a = start + i * step
        lines.append(f"[{_timed(a)} --> {_timed(a + step)}] {chunk}")
    return '\n'.join(lines)


def _trimmed_confirm(temp_db, adj_start, adj_end):
    from main_app import app
    _seed(temp_db)
    temp_db.save_episode_details(
        SLUG, EPISODE_ID,
        ad_markers=[{'start': 100.0, 'end': 140.0, 'confidence': 0.9, 'was_cut': False,
                     'held_for_review': True, 'hold_reason': 'max_duration',
                     'category': 'interaction'}],
        pending_review_count=1)
    with app.test_request_context():
        _handle_confirm_correction(
            temp_db, PatternService(temp_db), SLUG, EPISODE_ID,
            {'start': 100.0, 'end': 140.0},
            {'adjusted_start': adj_start, 'adjusted_end': adj_end},
            transcript=_ask_transcript())


def test_trimmed_confirm_below_the_floor_creates_no_pattern(temp_db):
    # 100-140 s passes the 20 s floor; the 10 s the reviewer kept does not.
    _trimmed_confirm(temp_db, 110.0, 120.0)
    assert [p for p in temp_db.get_ad_patterns(active_only=False)
            if p['sponsor_id'] is None] == []
    podcast_id = temp_db.get_podcast_by_slug(SLUG)['id']
    corrections = temp_db.get_episode_corrections(podcast_id, EPISODE_ID)
    assert [c['correction_type'] for c in corrections] == ['confirm']


def test_trimmed_confirm_above_the_floor_stores_the_trimmed_duration(temp_db):
    _trimmed_confirm(temp_db, 105.0, 130.0)
    rows = [p for p in temp_db.get_ad_patterns(active_only=False) if p['sponsor_id'] is None]
    assert len(rows) == 1
    assert rows[0]['avg_duration'] == pytest.approx(25.0)


# ------------------------------------------------- a match cuts the read only

# Interview talk before and after the read, in short lines like a real
# transcript (the read's template is longer than the 500-character window it
# is bucketed into, so several partial windows cover it).
_TALK_BEFORE = [
    "But he was right that the administration failed us on the southern border.",
    "So what would you do about that, though?",
    "And how do you reverse this change?",
    "And do you have an opinion?",
    "And what is your opinion on who you think should be",
    "president in the next cycle?",
    "We should start with the current one.",
]
_READ = [
    "Guys, I have got a favor to ask before this episode begins.",
    "The algorithm, if you follow a show, will deliver you the",
    "best episodes from that show very prominently in your feed.",
    "So when we have our best episodes on this show, the most",
    "shared episodes, the most rated episodes,",
    "I would love you to know.",
    "And the simple way for you to know that is to hit that",
    "follow button.",
    "But also, it is the simple, easy, free thing that you can do",
    "to help us make this show better.",
    "And I would be hugely grateful.",
    "If you could take a minute on the app you are listening to",
    "this on right now and hit that follow button.",
    "Thank you so, so, so much.",
]
_TALK_AFTER = [
    "Our guest today is a state representative.",
    "If the elections go your way this November, you are on the",
    "verge of potentially making history by becoming the first",
    "senator of your party in that state in a generation,",
    "which would be the first time, I think, since the eighties that",
    "someone from your side has won there.",
    "What would it mean to you, and to the people who voted for you?",
    "It would mean everything, honestly, to the families back home.",
]
READ_TEXT = ' '.join(_READ)


def _lines(texts, start, step=2.6):
    return [{'start': round(start + i * step, 2), 'end': round(start + (i + 1) * step - 0.05, 2),
             'text': text} for i, text in enumerate(texts)]


def _episode_with_read(read=_READ):
    before = _lines(_TALK_BEFORE, 70.0)
    read_segs = _lines(read, before[-1]['end'] + 0.1)
    after = _lines(_TALK_AFTER, read_segs[-1]['end'] + 4.0)
    return before + read_segs + after, read_segs[0]['start'], read_segs[-1]['end']


def _show_matches(db, segments, pid):
    return [m for m in TextPatternMatcher(db=db).find_matches(segments, podcast_id=SLUG)
            if m.pattern_id == pid]


def test_the_learned_read_is_cut_and_not_the_talk_around_it(temp_db):
    _seed(temp_db)
    segments, read_start, read_end = _episode_with_read()
    pid, _ = _confirm(temp_db, READ_TEXT, 'interaction', start=read_start, end=read_end)
    assert pid
    [hit] = _show_matches(temp_db, segments, pid)
    assert hit.start == pytest.approx(read_start, abs=0.5)
    assert hit.end == pytest.approx(read_end, abs=0.5)


def test_a_read_heard_with_a_changed_ending_is_bounded_by_its_stored_duration(temp_db):
    """Only the opening aligns: the end is the opening plus the read's stored
    length (with slack), marked estimated, never the windows' reach."""
    _seed(temp_db)
    segments, read_start, read_end = _episode_with_read()
    pid, _ = _confirm(temp_db, READ_TEXT, 'interaction', start=read_start, end=read_end)
    changed = _READ[:-3] + ["Thanks, now on with the show and our guest."] * 3
    segments, start, _ = _episode_with_read(changed)
    [hit] = _show_matches(temp_db, segments, pid)
    assert hit.start == pytest.approx(start, abs=0.5)
    assert hit.end - hit.start <= (read_end - read_start) * 1.1 + 2.0
    assert hit.span_estimated is True


def test_a_confirm_of_a_span_holding_a_learned_read_does_not_learn_a_longer_pattern(temp_db):
    """Confirming an over-long match (no pattern id) credits the read already
    learned; a bigger sibling would match further into the talk next time."""
    _seed(temp_db)
    segments, read_start, read_end = _episode_with_read()
    pid, _ = _confirm(temp_db, READ_TEXT, 'interaction', start=read_start, end=read_end)
    wider = ' '.join(_TALK_BEFORE[-2:] + _READ + _TALK_AFTER[:3])
    again, ids = _confirm(temp_db, wider, 'interaction', start=read_start - 5, end=read_end + 14)
    assert again == pid and ids == [pid]
    assert [p['id'] for p in temp_db.get_ad_patterns(active_only=False)
            if p['sponsor_id'] is None] == [pid]
    assert temp_db.get_ad_pattern_by_id(pid)['confirmation_count'] >= 1


def test_a_confirm_with_the_pattern_id_records_a_match_and_learns_nothing(temp_db):
    from main_app import app
    _seed(temp_db)
    segments, read_start, read_end = _episode_with_read()
    pid, _ = _confirm(temp_db, READ_TEXT, 'interaction', start=read_start, end=read_end)
    with app.test_request_context():
        _handle_confirm_correction(
            temp_db, PatternService(temp_db), SLUG, EPISODE_ID,
            {'start': read_start - 3, 'end': read_end + 14, 'pattern_id': pid}, {})
    assert [p['id'] for p in temp_db.get_ad_patterns(active_only=False)
            if p['sponsor_id'] is None] == [pid]


def test_two_adjacent_learned_reads_are_two_cuts_each_bounded_to_its_read(temp_db):
    _seed(temp_db)
    words = ASK.split()
    ask_lines = [' '.join(words[i:i + 8]) for i in range(0, len(words), 8)]
    before = _lines(_TALK_BEFORE, 70.0)
    read_a = _lines(_READ, before[-1]['end'] + 0.1)
    read_b = _lines(ask_lines, read_a[-1]['end'] + 0.1, step=3.0)
    after = _lines(_TALK_AFTER, read_b[-1]['end'] + 4.0)
    a, _ = _confirm(temp_db, READ_TEXT, 'interaction',
                    start=read_a[0]['start'], end=read_a[-1]['end'])
    b, _ = _confirm(temp_db, ' '.join(ask_lines), 'interaction',
                    start=read_b[0]['start'], end=read_b[-1]['end'])
    assert a and b and a != b
    hits = sorted((m for m in TextPatternMatcher(db=temp_db).find_matches(
        before + read_a + read_b + after, podcast_id=SLUG) if m.show_promo),
        key=lambda m: m.start)
    assert [m.pattern_id for m in hits] == [a, b]
    assert hits[0].start == pytest.approx(read_a[0]['start'], abs=0.5)
    assert hits[0].end == pytest.approx(read_a[-1]['end'], abs=0.5)
    assert hits[1].start == pytest.approx(read_b[0]['start'], abs=0.5)
    assert hits[1].end == pytest.approx(read_b[-1]['end'], abs=0.5)


def test_two_show_matches_a_few_seconds_apart_are_never_merged():
    first = TextMatch(pattern_id=1, start=100.0, end=130.0, confidence=0.9,
                      show_promo=True, category='interaction')
    second = TextMatch(pattern_id=2, start=133.0, end=160.0, confidence=0.9,
                       show_promo=True, category='interaction')
    merged = TextPatternMatcher()._merge_matches([first, second])
    assert [(m.start, m.end) for m in merged] == [(100.0, 130.0), (133.0, 160.0)]


def _learned_read(db):
    segments, read_start, read_end = _episode_with_read()
    pid, _ = _confirm(db, READ_TEXT, 'interaction', start=read_start, end=read_end)
    return pid, read_start, read_end


def _match_from_windows(db, pid, segments, start, end):
    """find_matches with the content windows replaced by one run start-end."""
    matcher = TextPatternMatcher(db=db)
    matcher._find_content_matches = lambda *a, **k: [TextMatch(
        pattern_id=pid, start=start, end=end, confidence=0.9, match_type='content',
        show_promo=True, category='interaction')]
    return [m for m in matcher.find_matches(segments, podcast_id=SLUG) if m.pattern_id == pid]


def test_an_opening_aligned_at_the_end_of_the_windows_never_leaves_a_sliver(temp_db):
    _seed(temp_db)
    pid, read_start, _ = _learned_read(temp_db)
    segments, _, _ = _episode_with_read()
    # The run ends inside the read's third line: its opening aligns 5.3 s
    # before the run's end.
    for hit in _match_from_windows(temp_db, pid, segments, 72.0, read_start + 5.3):
        assert hit.end - hit.start >= 7.0


def test_when_opening_and_closing_align_too_close_the_opening_is_kept(temp_db):
    _seed(temp_db)
    pid, _, _ = _learned_read(temp_db)
    talk = _lines(_TALK_BEFORE, 70.0)
    edges = _lines(_READ[:3] + _READ[-3:], 90.0, step=1.0)
    segments = talk + edges + _lines(_TALK_AFTER, 97.0)
    [hit] = _match_from_windows(temp_db, pid, segments, 72.0, 110.0)
    assert hit.start == pytest.approx(90.0, abs=0.2)


def test_an_estimated_end_keeps_only_the_aligned_opening_as_grounded_text(temp_db):
    _seed(temp_db)
    pid, read_start, read_end = _learned_read(temp_db)
    changed = _READ[:-3] + ["Thanks, now on with the show and our guest."] * 3
    segments, start, _ = _episode_with_read(changed)
    [hit] = _show_matches(temp_db, segments, pid)
    assert hit.span_estimated is True
    assert hit.text_start == pytest.approx(hit.start)
    # The opening phrase ends well inside the span: a precise detection can
    # still tighten the guessed end (boundaries.tighten needs text_end < end - 1).
    assert hit.text_end < hit.end - 1.0


def test_edges_are_word_timed_when_the_segment_carries_words(temp_db):
    """The read starts mid-segment after talk: its edge is the word, not the
    segment start (without word timestamps it is the segment edge)."""
    _seed(temp_db)
    pid, _, _ = _learned_read(temp_db)
    segments, read_start, _ = _episode_with_read()
    k = len(_TALK_BEFORE)
    talk, first = segments[k - 1], segments[k]
    text = talk['text'] + ' ' + first['text']
    tokens = text.split()
    span = first['end'] - talk['start']
    words = [{'word': w, 'start': round(talk['start'] + span * n / len(tokens), 3),
              'end': round(talk['start'] + span * (n + 1) / len(tokens) - 0.01, 3)}
             for n, w in enumerate(tokens)]
    joined = {'start': talk['start'], 'end': first['end'], 'text': text, 'words': words}
    segments = segments[:k - 1] + [joined] + segments[k + 1:]
    guys = next(w for w in words if w['word'] == 'Guys,')
    [hit] = _show_matches(temp_db, segments, pid)
    assert hit.start == pytest.approx(guys['start'], abs=0.01)
    assert hit.start > talk['start'] + 1.0


def test_an_unpunctuated_template_gives_capped_edge_phrases():
    from text_pattern_matcher import AdPattern, SHOW_PROMO_EDGE_PHRASE_WORDS
    words = READ_TEXT.replace('.', '').replace(',', '').split()
    pattern = AdPattern(id=1, text_template=' '.join(words * 2), intro_variants=[],
                        outro_variants=[], sponsor=None, scope='podcast',
                        podcast_id=SLUG, network_id=None, created_by='show_promo',
                        category='interaction')
    intro, outro = TextPatternMatcher._boundary_variants(pattern)
    assert len(intro[0].split()) == SHOW_PROMO_EDGE_PHRASE_WORDS
    assert len(outro[0].split()) == SHOW_PROMO_EDGE_PHRASE_WORDS
    assert intro[0].split() == words[:SHOW_PROMO_EDGE_PHRASE_WORDS]
    assert outro[0].split() == words[-SHOW_PROMO_EDGE_PHRASE_WORDS:]


def _show_pattern_ids(db):
    return sorted(p['id'] for p in db.get_ad_patterns(active_only=False)
                  if p['sponsor_id'] is None)


def test_a_learned_ask_inside_a_confirmed_promo_of_another_category_does_not_absorb_it(temp_db):
    _seed(temp_db)
    pid, read_start, read_end = _learned_read(temp_db)
    promo = ' '.join(_READ + _TALK_AFTER[:4])
    new, _ = _confirm(temp_db, promo, 'cross_promo', start=read_start, end=read_end + 12)
    assert new and new != pid


def test_a_short_learned_ask_inside_a_long_confirmed_read_does_not_absorb_it(temp_db):
    _seed(temp_db)
    pid, _ = _confirm(temp_db, ' '.join(_READ[:8]), 'interaction', start=90.0, end=111.0)
    longer = ' '.join(_READ[:8] + _TALK_AFTER + _TALK_BEFORE)
    new, _ = _confirm(temp_db, longer, 'interaction', start=90.0, end=150.0)
    assert pid and new and new != pid


def test_the_best_contained_read_is_credited(temp_db):
    _seed(temp_db)
    near = (READ_TEXT.replace('favor', 'favour').replace('algorithm', 'algorithms')
            .replace('hugely', 'really').replace('grateful', 'thankful'))
    worse = temp_db.create_ad_pattern(
        scope='podcast', text_template=near, intro_variants=[], outro_variants=[],
        podcast_id=SLUG, duration=36.0, created_by='show_promo', category='interaction')
    best = temp_db.create_ad_pattern(
        scope='podcast', text_template=READ_TEXT, intro_variants=[], outro_variants=[],
        podcast_id=SLUG, duration=36.0, created_by='show_promo', category='interaction')
    wider = ' '.join(_TALK_BEFORE[-1:] + _READ + _TALK_AFTER[:2])
    got, _ = _confirm(temp_db, wider, 'interaction', start=85.0, end=130.0)
    assert worse < best and got == best


def test_learning_a_show_read_reads_the_show_patterns_once(temp_db, monkeypatch):
    _seed(temp_db)
    calls = []
    real = temp_db.get_ad_patterns

    def spy(*a, **k):
        if k.get('podcast_id') == SLUG:
            calls.append(k)
        return real(*a, **k)

    monkeypatch.setattr(temp_db, 'get_ad_patterns', spy)
    TextPatternMatcher(db=temp_db).create_show_promo_pattern_from_ad(
        segments=[], start=88.0, end=124.0, category='interaction',
        podcast_id=SLUG, episode_id=EPISODE_ID, ad_text=READ_TEXT)
    assert len(calls) == 1


def test_a_failed_pattern_read_is_logged_and_the_read_still_learned(temp_db, monkeypatch, caplog):
    _seed(temp_db)
    real = temp_db.get_ad_patterns

    def failing(*a, **k):
        if k.get('podcast_id') == SLUG:
            raise RuntimeError('db locked')
        return real(*a, **k)

    monkeypatch.setattr(temp_db, 'get_ad_patterns', failing)
    with caplog.at_level(logging.WARNING):
        pid = TextPatternMatcher(db=temp_db).create_show_promo_pattern_from_ad(
            segments=[], start=88.0, end=124.0, category='interaction',
            podcast_id=SLUG, episode_id=EPISODE_ID, ad_text=READ_TEXT)
    assert pid
    assert 'db locked' in caplog.text


def test_an_over_long_span_with_neither_end_of_the_read_aligned_is_not_cut(temp_db):
    _seed(temp_db)
    pid, _, _ = _learned_read(temp_db)
    segments = (_lines(_TALK_BEFORE, 70.0) + _lines(_READ[3:11], 90.0)
                + _lines(_TALK_AFTER, 112.0))
    assert _match_from_windows(temp_db, pid, segments, 72.0, 128.0) == []
