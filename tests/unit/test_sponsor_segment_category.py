"""A sponsor-level segment category reaches detection, matching, the hint, and the learner."""
import logging

from tests.app_bootstrap import bootstrap

bootstrap('sponsor_segment_category_')

from sponsor_normalize import segment_category_for  # noqa: E402
from text_pattern_matcher import TextPatternMatcher  # noqa: E402
from tests.unit.test_known_pattern_hint import _detector_with  # noqa: E402


def _detector(overrides):
    return _detector_with([], sponsor_categories=overrides)


def test_map_covers_name_and_aliases_case_insensitively(temp_db):
    temp_db.create_known_sponsor('Acme', aliases=['Acme Widgets', ' AW '],
                                 segment_category='self_promo')
    temp_db.create_known_sponsor('Zeta')
    assert temp_db.get_sponsor_segment_categories() == {
        'acme': 'self_promo', 'acme widgets': 'self_promo', 'aw': 'self_promo'}


def test_inactive_sponsor_is_not_applied(temp_db):
    sid = temp_db.create_known_sponsor('Old', segment_category='self_promo')
    temp_db.update_known_sponsor(sid, is_active=0)
    assert temp_db.get_sponsor_segment_categories() == {}


def test_update_sets_and_clears_segment_category(temp_db):
    sid = temp_db.create_known_sponsor('Acme')
    assert temp_db.update_known_sponsor(sid, segment_category='cross_promo')
    assert temp_db.get_known_sponsor_by_id(sid)['segment_category'] == 'cross_promo'
    assert temp_db.update_known_sponsor(sid, segment_category=None)
    assert temp_db.get_known_sponsor_by_id(sid)['segment_category'] is None


def test_label_resolver_matches_exact_alias_then_whole_word():
    overrides = {'acme': 'self_promo', 'acme widgets': 'self_promo', 'zeta': 'cross_promo'}
    assert segment_category_for(' Acme ', overrides) == 'self_promo'
    assert segment_category_for('Acme by Zeta', overrides) == 'self_promo'
    assert segment_category_for('Zeta and Acme Widgets', overrides) == 'self_promo'
    assert segment_category_for('Acmeco', overrides) is None
    assert segment_category_for(None, overrides) is None


def test_override_restamps_matching_ads_only(caplog):
    ads = [
        {'start': 10.0, 'end': 40.0, 'sponsor': 'Acme by Zeta', 'category': 'sponsor'},
        {'start': 50.0, 'end': 80.0, 'sponsor': 'Zeta', 'category': 'sponsor'},
        {'start': 90.0, 'end': 95.0, 'category': 'outro'},
    ]
    with caplog.at_level(logging.INFO):
        _detector({'acme': 'self_promo'})._apply_sponsor_segment_categories(
            ads, 'example-podcast', 'a1b2c3')
    assert [a.get('category') for a in ads] == ['self_promo', 'sponsor', 'outro']
    assert 'Sponsor category: Acme by Zeta sponsor -> self_promo' in caplog.text


def test_override_survives_db_failure():
    d = _detector({})
    d.db.get_sponsor_segment_categories.side_effect = RuntimeError('locked')
    ads = [{'start': 0.0, 'end': 5.0, 'sponsor': 'X', 'category': 'sponsor'}]
    assert d._apply_sponsor_segment_categories(ads, 's', 'e') is ads
    assert ads[0]['category'] == 'sponsor'


def _segments(text, start=100.0, end=160.0):
    words = text.split()
    step = (end - start) / len(words)
    return [{'start': start + i * step, 'end': start + (i + 1) * step, 'text': w}
            for i, w in enumerate(words)]


def test_learned_pattern_stores_the_sponsor_category_via_alias(temp_db):
    temp_db.create_known_sponsor('Acme', aliases=['Acme Widgets'], segment_category='self_promo')
    text = ('acme widgets saved my drive last week after the blue screen and acme widgets '
            'brought every file back so thank you for acme widgets it is worth it ') * 3
    matcher = TextPatternMatcher(db=temp_db)
    pid = matcher.create_pattern_from_ad(
        _segments(text), 100.0, 160.0, sponsor='Acme Widgets',
        podcast_id='example-podcast', episode_id='a1b2c3', category='sponsor')
    assert pid is not None
    stored = temp_db.get_connection().execute(
        "SELECT category FROM ad_patterns WHERE id = ?", (pid,)).fetchone()[0]
    assert stored == 'self_promo'


def test_stored_pattern_category_stays_while_matching_uses_the_sponsor_one(temp_db):
    sid = temp_db.create_known_sponsor('Acme', segment_category='self_promo')
    pid = temp_db.create_ad_pattern(scope='podcast', text_template='acme read ' * 12,
                                    sponsor_id=sid, podcast_id='example-podcast',
                                    category='sponsor')
    temp_db.create_audio_fingerprint(pid, b'fp', 30.0)
    row = temp_db.get_ad_pattern_by_id(pid)
    assert row['category'] == 'sponsor'
    assert row['sponsor_segment_category'] == 'self_promo'
    matcher = TextPatternMatcher(db=temp_db)
    matcher._load_patterns()
    assert [p.category for p in matcher._patterns if p.id == pid] == ['self_promo']
    assert temp_db.get_all_fingerprints_with_sponsors()[0]['category'] == 'self_promo'
    temp_db.update_known_sponsor(sid, segment_category=None)
    assert temp_db.get_all_fingerprints_with_sponsors()[0]['category'] == 'sponsor'
