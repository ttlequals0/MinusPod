"""Splitting a confirmed miss the transcript gives no handoff phrase for."""
import logging
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


def test_distinct_same_sponsor_members_do_not_learn_one_bundle(db):
    second_read = (
        "Acme Tools builds garden sheds that arrive ready to assemble. "
        "A kit from Acme Tools includes every panel and fitting you need. "
        "Choose a size online and Acme Tools delivers it to your door."
    )
    matcher = TextPatternMatcher(db=db)
    created = matcher.create_patterns_from_ad(
        segments=_segments(ACME_READ, 0.0, 55.0)
                 + _segments(second_read, 55.0, 110.0),
        start=0.0, end=110.0, sponsor='Acme Tools',
        podcast_id='example-podcast',
        ad={'merged_distinct_ads': True, 'merged_member_spans': [
            {'start': 0.0, 'end': 55.0, 'sponsor': 'Acme Tools'},
            {'start': 55.0, 'end': 110.0, 'sponsor': 'Acme Tools'},
        ]},
    )
    assert [(round(c['start']), round(c['end'])) for c in created] == [
        (0, 55), (55, 110)]
    assert all(len(db.get_ad_pattern_by_id(c['id'])['text_template']) <
               len(ACME_READ + second_read) for c in created)


def test_distinct_merge_without_a_reliable_divider_learns_nothing(db):
    matcher = TextPatternMatcher(db=db)
    created = matcher.create_patterns_from_ad(
        segments=_segments(ACME_READ, 0.0, 95.0), start=0.0, end=95.0,
        sponsor='Acme Tools', podcast_id='example-podcast',
        ad={'merged_distinct_ads': True},
    )
    assert created == []
    assert db.get_ad_patterns(podcast_id='example-podcast') == []


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


PROMO_READ = (
    "Thanks for listening to the show this week. "
    "Leave us a rating wherever you listen to podcasts. "
    "Tell a friend who would enjoy the show too."
)


@pytest.mark.parametrize('intro_sponsor', ['example-podcast', None])
def test_self_promo_intro_and_sponsor_outro_are_not_learned_as_one(db, caplog,
                                                                    intro_sponsor):
    matcher = TextPatternMatcher(db=db)
    with caplog.at_level(logging.INFO, logger='podcast.textmatch'):
        created = matcher.create_patterns_from_ad(
            segments=_segments(PROMO_READ, 0.0, 25.0)
                     + _segments(ACME_READ, 25.0, 100.0),
            start=0.0, end=100.0, sponsor='Acme Tools',
            podcast_id='example-podcast',
            ad={'merged_member_spans': [
                {'start': 0.0, 'end': 25.0, 'stage': 'claude',
                 'sponsor': intro_sponsor, 'category': 'self_promo'},
                {'start': 25.0, 'end': 100.0, 'stage': 'claude',
                 'sponsor': 'Acme Tools', 'category': 'sponsor'},
            ]})
    rows = db.get_ad_patterns(podcast_id='example-podcast')
    assert [(round(c['start']), round(c['end'])) for c in created] == [(25, 100)]
    assert [p['sponsor'] for p in rows] == ['Acme Tools']
    assert "Splitting bundled span 0-100s: intro from self_promo/" in caplog.text


def test_bundled_span_without_a_divider_is_skipped(db, caplog):
    matcher = TextPatternMatcher(db=db)
    with caplog.at_level(logging.INFO, logger='podcast.textmatch'):
        created = matcher.create_patterns_from_ad(
            segments=_segments(ACME_READ, 0.0, 90.0), start=0.0, end=90.0,
            sponsor='Acme Tools', podcast_id='example-podcast',
            ad={'merged_member_spans': [
                {'start': 0.0, 'end': 90.0, 'stage': 'claude',
                 'sponsor': 'Acme Tools', 'category': 'self_promo'},
                {'start': 0.0, 'end': 90.0, 'stage': 'fingerprint',
                 'sponsor': 'Acme Tools', 'category': 'sponsor'},
            ]})
    assert created == []
    assert "intro and outro come from different reads" in caplog.text


def test_split_piece_takes_the_category_of_its_covering_member(db):
    matcher = TextPatternMatcher(db=db)
    created = matcher.create_patterns_from_ad(
        segments=_two_brand_segments(), start=0.0, end=191.0,
        sponsor='Acme Tools', podcast_id='example-podcast', category='sponsor',
        ad={'merged_member_spans': [
            {'start': 0.0, 'end': 95.0, 'stage': 'claude',
             'sponsor': 'Acme Tools', 'category': 'cross_promo'},
            {'start': 95.0, 'end': 191.0, 'stage': 'claude',
             'sponsor': 'Beta Corp', 'category': 'sponsor'},
        ]})
    cats = {db.get_ad_pattern_by_id(c['id'])['sponsor']:
            db.get_ad_pattern_by_id(c['id']).get('category') for c in created}
    assert cats == {'Acme Tools': 'cross_promo', 'Beta Corp': 'sponsor'}


def test_same_label_members_still_learn_one_pattern(db):
    matcher = TextPatternMatcher(db=db)
    created = matcher.create_patterns_from_ad(
        segments=_segments(ACME_READ, 0.0, 90.0), start=0.0, end=90.0,
        sponsor='Acme Tools', podcast_id='example-podcast',
        ad={'merged_member_spans': [
            {'start': 0.0, 'end': 50.0, 'stage': 'claude',
             'sponsor': 'Acme Tools', 'category': 'sponsor'},
            {'start': 40.0, 'end': 90.0, 'stage': 'fingerprint',
             'sponsor': 'acme tools', 'category': 'sponsor'},
        ]})
    assert [(round(c['start']), round(c['end'])) for c in created] == [(0, 90)]


def test_a_claude_member_of_a_dai_marker_is_split_and_learned(db):
    from ad_detector import AdDetector
    detector = AdDetector(api_key='test-key')
    detector.db = db
    detector.text_pattern_matcher = TextPatternMatcher(db=db)
    detector.sponsor_service = None
    detector.audio_fingerprinter = None
    marker = {
        'start': 0.0, 'end': 191.0, 'was_cut': True, 'confidence': 0.9,
        'detection_stage': 'dai_differential', 'category': 'sponsor',
        'merged_distinct_ads': True,
        'merged_member_spans': [
            {'start': 0.0, 'end': 191.0, 'stage': 'claude', 'confidence': 0.97,
             'sponsor': 'Acme Tools'},
            {'start': 0.0, 'end': 191.0, 'stage': 'dai_differential'},
        ],
    }
    learned = detector.learn_from_detections(
        [marker], _two_brand_segments(), podcast_id='example-podcast',
        episode_id='a1b2c3d4e5f6')
    assert learned == 1
    sponsors = {p['sponsor'] for p in db.get_ad_patterns(podcast_id='example-podcast')}
    assert sponsors == {'Acme Tools', 'Beta Corp'}


def test_a_claude_member_over_two_dai_cores_splits_at_the_inner_core(db):
    """No registered brand or handoff phrase separates the reads; only the measured cores do."""
    from ad_detector import AdDetector
    first = (
        "Zorbly Goods keeps a workshop running without the usual hassle. "
        "Every Zorbly Goods order ships free and arrives inside two days. "
        "Listeners get a month of Zorbly Goods on the house right now. "
        "Zorbly Goods stands behind every single thing it sells to you."
    )
    second = (
        "Zorbly Goods also files small business taxes in a single afternoon. "
        "Zorbly Goods reads the forms so you never have to open one. "
        "Try Zorbly Goods free for a month and see the difference today. "
        "Zorbly Goods has helped thousands of owners already this year."
    )
    detector = AdDetector(api_key='test-key')
    detector.db = db
    detector.text_pattern_matcher = TextPatternMatcher(db=db)
    detector.sponsor_service = None
    detector.audio_fingerprinter = None
    marker = {
        'start': 0.0, 'end': 191.0, 'was_cut': True, 'confidence': 0.9,
        'detection_stage': 'dai_differential', 'category': 'sponsor',
        'dai_core_spans': [{'start': 0.0, 'end': 94.0}, {'start': 95.0, 'end': 191.0}],
        'merged_distinct_ads': True,
        'merged_member_spans': [
            {'start': 0.0, 'end': 191.0, 'stage': 'claude', 'confidence': 0.97,
             'sponsor': 'Zorbly Goods'},
        ],
    }
    real_create = detector.text_pattern_matcher.create_patterns_from_ad
    created = []
    detector.text_pattern_matcher.create_patterns_from_ad = (
        lambda **kwargs: created.extend(real_create(**kwargs)) or created)
    detector.learn_from_detections(
        [marker], _segments(first, 0.0, 95.0) + _segments(second, 95.0, 191.0),
        podcast_id='example-podcast', episode_id='a1b2c3d4e5f6')
    assert [(c['start'], c['end']) for c in created] == [(0.0, 95.0), (95.0, 191.0)]


def test_the_unknown_stretch_of_a_bundled_member_splits_into_its_reads(db):
    """A known read inside the member is removed; the rest learns as one pattern per brand."""
    from ad_detector import AdDetector
    from sponsor_service import SponsorService
    known = (
        "Gamma Shoes fits every foot in the family with one easy order. "
        "Gamma Shoes ships free both ways so returns cost you nothing at all. "
        "Gamma Shoes has a sale this week on every running shoe they sell."
    )
    detector = AdDetector(api_key='test-key')
    detector.db = db
    detector.text_pattern_matcher = TextPatternMatcher(db=db)
    detector.sponsor_service = SponsorService(db)
    detector.audio_fingerprinter = None
    marker = {
        'start': 0.0, 'end': 290.0, 'was_cut': True, 'confidence': 0.9,
        'detection_stage': 'dai_differential', 'category': 'sponsor',
        'merged_distinct_ads': True,
        'merged_member_spans': [
            {'start': 0.0, 'end': 290.0, 'stage': 'claude', 'confidence': 0.97,
             'sponsor': 'Gamma Shoes'},
            {'start': 0.0, 'end': 100.0, 'stage': 'text_pattern', 'pattern_id': 99},
        ],
    }
    real_create = detector.text_pattern_matcher.create_patterns_from_ad
    created, sponsors_passed = [], []

    def create(**kwargs):
        sponsors_passed.append(kwargs['sponsor'])
        created.extend(real_create(**kwargs))
        return created

    detector.text_pattern_matcher.create_patterns_from_ad = create
    detector.learn_from_detections(
        [marker], _segments(known, 0.0, 100.0) + _two_brand_segments(100.0, 195.0, 290.0),
        podcast_id='example-podcast', episode_id='a1b2c3d4e5f6')
    assert [(round(c['start']), round(c['end'])) for c in created] == [(100, 195), (195, 290)]
    assert 'Gamma Shoes' not in sponsors_passed
    sponsors = {p['sponsor'] for p in db.get_ad_patterns(podcast_id='example-podcast')}
    assert sponsors == {'Acme Tools', 'Beta Corp'}
