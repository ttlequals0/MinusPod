"""Learned pieces split at the read boundaries the transcript shows and keep only the sponsor's copy."""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))

from text_pattern_matcher import TextPatternMatcher
from tests.unit.bundled_read_fixture import BRANDS, SEGMENTS, SEGMENTS_OWN_TAIL, _segment

PIECE = {'start': 829.4, 'end': 937.8, '_cut_down': True, '_member_of': (731.1, 939.9, 'dai_differential'),
         'dai_core_spans': [{'start': 855.88, 'end': 874.03}, {'start': 874.77, 'end': 937.8}]}


@pytest.fixture
def matcher(temp_db):
    for brand in BRANDS:
        if not temp_db.get_known_sponsor_by_name(brand):
            temp_db.create_known_sponsor(brand, [])
    return TextPatternMatcher(db=temp_db)


def _learn(matcher, segments, start, end, sponsor, ad):
    return matcher.create_patterns_from_ad(
        segments=segments, start=start, end=end, sponsor=sponsor,
        podcast_id='example-podcast', episode_id='a1b2c3d4e5f6', ad=ad)


def test_each_bundled_read_is_learned_on_its_own_copy(matcher, temp_db, caplog):
    with caplog.at_level('INFO'):
        created = _learn(matcher, SEGMENTS, 829.4, 937.8, 'Ledgerly', PIECE)
    assert [(c['start'], c['end']) for c in created] == [
        (835.1, 860.0), (860.8, 908.4), (909.5, 937.8)]
    sponsors = {p['sponsor'] for p in temp_db.get_ad_patterns(podcast_id='example-podcast')}
    assert sponsors == {'Ledgerly', 'Acme Wash', 'Globex Foods'}
    assert 'Trimmed learned piece 829.4-860.8s to 835.1-860.0s' in caplog.text


def test_a_single_read_with_no_handoff_is_untouched(matcher):
    segments = [_segment((909.5, 918.0, 'Globex Foods brings fresh dinners to your door every week.'),
                         (918.0, 928.0, 'Globex Foods recipes take twenty minutes or less to make.'),
                         (928.0, 937.8, 'Order Globex Foods tonight and skip the grocery run.'))]
    ad = {'start': 909.5, 'end': 937.8, '_member_of': (900.0, 940.0, 'dai_differential')}
    created = _learn(matcher, segments, 909.5, 937.8, 'Globex Foods', ad)
    assert [(c['start'], c['end']) for c in created] == [(909.5, 937.8)]


def test_a_piece_that_never_names_its_sponsor_learns_nothing(matcher, caplog):
    segments = [_segment((909.5, 925.0, 'Fresh dinners arrive at your door every single week.'),
                         (925.0, 937.8, 'Recipes take twenty minutes or less to make at home.'))]
    ad = {'start': 909.5, 'end': 937.8, '_member_of': (900.0, 940.0, 'dai_differential')}
    with caplog.at_level('INFO'):
        assert _learn(matcher, segments, 909.5, 937.8, 'Globex Foods', ad) == []
    assert 'never names Globex Foods' in caplog.text


def test_a_piece_starting_on_a_segment_boundary_keeps_its_opening_sentence(matcher):
    segments = [_segment((835.1, 840.0, 'Running a small team means juggling a lot of paperwork.')),
                _segment((840.0, 848.0, 'Ledgerly keeps every invoice and receipt in one tidy place.')),
                _segment((848.0, 856.0, 'Ledgerly sends the reminders so you never chase a payment.'))]
    ad = {'start': 835.1, 'end': 856.0, '_cut_down': True,
          '_member_of': (731.1, 939.9, 'dai_differential')}
    created = _learn(matcher, segments, 835.1, 856.0, 'Ledgerly', ad)
    assert [(c['start'], c['end']) for c in created] == [(835.1, 856.0)]


def test_a_bundled_block_with_the_tail_as_its_own_segment_still_learns_each_read(matcher):
    created = _learn(matcher, SEGMENTS_OWN_TAIL, 829.4, 937.8, 'Ledgerly', PIECE)
    assert [(c['start'], c['end']) for c in created] == [
        (835.1, 860.0), (860.8, 908.4), (909.5, 937.8)]


def test_a_paused_tail_segment_before_a_single_read_is_trimmed(matcher):
    segments = [_segment((830.4, 832.3, 'Available on Plus and Pro plans.')),
                _segment((835.1, 840.0, 'Running a small team means juggling a lot of paperwork.'),
                         (840.0, 848.0, 'Ledgerly keeps every invoice and receipt in one tidy place.')),
                _segment((848.0, 856.0, 'Ledgerly sends the reminders so you never chase a payment.'))]
    ad = {'start': 829.4, 'end': 856.0, '_cut_down': True,
          '_member_of': (731.1, 939.9, 'dai_differential')}
    created = _learn(matcher, segments, 829.4, 856.0, 'Ledgerly', ad)
    assert [(c['start'], c['end']) for c in created] == [(835.1, 856.0)]


def _ledgerly_read(opener):
    return [opener,
            _segment((841.5, 848.0, 'Ledgerly keeps every invoice and receipt in one tidy place.')),
            _segment((848.0, 856.0, 'Ledgerly sends the reminders so you never chase a payment.'))]


AD_835 = {'start': 835.1, 'end': 856.0, '_cut_down': True,
          '_member_of': (731.1, 939.9, 'dai_differential')}


def test_an_unbranded_opener_followed_by_a_pause_is_kept(matcher):
    """The read's own 4.9 s opener, then a 1.5 s pause, is not a previous read's tail."""
    segments = _ledgerly_read(
        _segment((835.1, 840.0, 'Running a small team means juggling a lot of paperwork.')))
    created = _learn(matcher, segments, 835.1, 856.0, 'Ledgerly', AD_835)
    assert [(c['start'], c['end']) for c in created] == [(835.1, 856.0)]


def test_a_two_segment_lead_is_not_trimmed(matcher):
    segments = [_segment((829.4, 832.4, 'Plans start small and grow.')),
                _segment((832.4, 835.4, 'Cancel any time you like.')),
                _segment((836.5, 848.0, 'Ledgerly keeps every invoice and receipt in one tidy place.')),
                _segment((848.0, 856.0, 'Ledgerly sends the reminders so you never chase a payment.'))]
    ad = dict(AD_835, start=829.4)
    created = _learn(matcher, segments, 829.4, 856.0, 'Ledgerly', ad)
    assert [(c['start'], c['end']) for c in created] == [(829.4, 856.0)]


def test_a_trim_that_would_leave_the_read_too_short_is_not_applied(matcher, caplog):
    # Without the floor, dropping the 1.9 s tail would leave a 14.9 s read under the 15 s minimum.
    segments = [_segment((828.0, 829.9, 'Available on Plus and Pro plans.')),
                _segment((831.1, 838.0, 'Ledgerly keeps every invoice in one place.')),
                _segment((838.0, 846.0, 'Ledgerly sends the reminders so you never chase a payment.'))]
    ad = dict(AD_835, start=828.0, end=846.0)
    with caplog.at_level('INFO'):
        created = _learn(matcher, segments, 828.0, 846.0, 'Ledgerly', ad)
    assert [(c['start'], c['end']) for c in created] == [(828.0, 846.0)]
    assert 'Keeping learned piece 828.0-846.0s untrimmed' in caplog.text
