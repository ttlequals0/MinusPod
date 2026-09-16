"""Splitting a confirmed miss the transcript gives no handoff phrase for."""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))

from text_pattern_matcher import TextPatternMatcher


@pytest.fixture
def db(temp_db):
    """The shared temp database with the two brands these reads name."""
    temp_db.create_known_sponsor('Acme Tools', ['AcmeTools'])
    temp_db.create_known_sponsor('Beta Corp', ['BetaCorp'])
    return temp_db


ACME_READ = (
    "Acme Tools keeps a workshop running without the usual hassle. "
    "Every Acme Tools order ships free and arrives inside two days. "
    "Listeners get a month of Acme Tools on the house right now. "
    "Acme Tools stands behind every single thing it sells to you."
)
BETA_READ = (
    "Beta Corp files your small business taxes in a single afternoon. "
    "Beta Corp reads the forms so you never have to open one. "
    "Try Beta Corp free for a month and see the difference today. "
    "Beta Corp has helped thousands of owners already this year."
)


def _segments(text, start, end):
    """One segment per sentence so the splitter has timestamps to cut on."""
    sentences = [s.strip() for s in text.split('. ') if s.strip()]
    step = (end - start) / len(sentences)
    return [
        {'start': start + i * step, 'end': start + (i + 1) * step,
         'text': s if s.endswith('.') else s + '.'}
        for i, s in enumerate(sentences)
    ]


def _two_brand_segments(start=0.0, mid=95.0, end=191.0):
    return (_segments(ACME_READ, start, mid)
            + _segments(BETA_READ, mid, end))


def test_a_merged_span_splits_at_its_member_boundary(db):
    matcher = TextPatternMatcher(db=db)
    created = matcher.create_patterns_from_ad(
        segments=_two_brand_segments(), start=0.0, end=191.0,
        sponsor='Acme Tools', podcast_id='example-podcast',
        episode_id='a1b2c3d4e5f6',
        ad={'merged_member_spans': [
            {'start': 0.0, 'end': 95.0, 'stage': 'claude', 'sponsor': 'Acme Tools'},
            {'start': 95.0, 'end': 191.0, 'stage': 'claude', 'sponsor': 'Beta Corp'},
        ]})
    assert [(round(c['start']), round(c['end'])) for c in created] == [
        (0, 95), (95, 191)]


def test_each_member_piece_is_learned_under_its_own_sponsor(db):
    matcher = TextPatternMatcher(db=db)
    created = matcher.create_patterns_from_ad(
        segments=_two_brand_segments(), start=0.0, end=191.0,
        sponsor='Acme Tools', podcast_id='example-podcast',
        ad={'merged_member_spans': [
            {'start': 0.0, 'end': 95.0, 'sponsor': 'Acme Tools'},
            {'start': 95.0, 'end': 191.0, 'sponsor': 'Beta Corp'},
        ]})
    rows = {p['id']: p for p in db.get_ad_patterns(podcast_id='example-podcast')}
    assert {rows[c['id']]['sponsor'] for c in created} == {'Acme Tools', 'Beta Corp'}


def test_a_brand_change_splits_a_span_with_no_merge_record(db):
    """The registry is the only evidence here: no phrase, no members."""
    matcher = TextPatternMatcher(db=db)
    created = matcher.create_patterns_from_ad(
        segments=_two_brand_segments(), start=0.0, end=191.0,
        sponsor='Acme Tools', podcast_id='example-podcast')
    assert [(round(c['start']), round(c['end'])) for c in created] == [
        (0, 95), (95, 191)]


def test_a_measured_dai_cut_splits_the_span(db):
    matcher = TextPatternMatcher(db=db)
    created = matcher.create_patterns_from_ad(
        segments=_two_brand_segments(), start=0.0, end=191.0,
        sponsor='Acme Tools', podcast_id='example-podcast',
        ad={'dai_core_spans': [{'start': 0.0, 'end': 94.0},
                               {'start': 95.0, 'end': 191.0}]})
    assert len(created) == 2


def test_a_single_brand_span_over_the_ceiling_is_still_declined(db):
    """Nothing to cut on means the contamination screen still applies."""
    matcher = TextPatternMatcher(db=db)
    long_read = ' '.join([ACME_READ] * 3)
    created = matcher.create_patterns_from_ad(
        segments=_segments(long_read, 0.0, 191.0), start=0.0, end=191.0,
        sponsor='Acme Tools', podcast_id='example-podcast')
    assert created == []


def test_a_two_brand_span_under_the_ceiling_is_split_not_declined(db):
    """110s and no handoff phrase, so the second registry brand is the only
    evidence of two reads; without it the whole span goes to the contamination
    screen and nothing is learned."""
    matcher = TextPatternMatcher(db=db)
    created = matcher.create_patterns_from_ad(
        segments=_two_brand_segments(0.0, 55.0, 110.0), start=0.0, end=110.0,
        sponsor='Acme Tools', podcast_id='example-podcast')
    assert [(round(c['start']), round(c['end'])) for c in created] == [
        (0, 55), (55, 110)]


def test_a_span_reading_a_second_brand_is_refused_under_the_ceiling(db):
    """102s of self-promo plus two brands was learned as one template."""
    matcher = TextPatternMatcher(db=db)
    pattern_id = matcher.create_pattern_from_ad(
        segments=_two_brand_segments(0.0, 50.0, 102.0), start=0.0, end=102.0,
        sponsor='Acme Tools', podcast_id='example-podcast')
    assert pattern_id is None


def test_a_single_passing_mention_does_not_refuse_the_span(db):
    """A read that name-drops a competitor once is still that read."""
    matcher = TextPatternMatcher(db=db)
    text = ACME_READ + ' We looked at Beta Corp before choosing this one.'
    pattern_id = matcher.create_pattern_from_ad(
        segments=_segments(text, 0.0, 95.0), start=0.0, end=95.0,
        sponsor='Acme Tools', podcast_id='example-podcast')
    assert pattern_id is not None


def test_a_clean_single_brand_read_is_learned_whole(db):
    matcher = TextPatternMatcher(db=db)
    created = matcher.create_patterns_from_ad(
        segments=_segments(ACME_READ, 0.0, 95.0), start=0.0, end=95.0,
        sponsor='Acme Tools', podcast_id='example-podcast')
    assert [(round(c['start']), round(c['end'])) for c in created] == [(0, 95)]
