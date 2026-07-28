"""Tests for segment category constants and provenance (issue #565).

Covers:
- normalize_segment_category valid/invalid passthrough
- _merge_detection_results stamps category on every stage's ads (the single
  seam every detection stage's output passes through)
- parse_ads_from_response carries the LLM's raw category field through
  extraction unvalidated (normalization happens at the merge seam)
- get_episode's marker-to-payload mapping exposes category/actionApplied
- the merge seam gates on the feed's resolved segment action map, so a
  keep-resolving detection can never be merged into (and cut with) a
  remove-resolving one
"""
import json
import os
import sys
import tempfile

os.environ.setdefault('MINUSPOD_DATA_DIR', tempfile.mkdtemp(prefix='segment_categories_test_'))
os.environ.setdefault('SECRET_KEY', 'test-secret')

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))

from ad_detector import AdDetector, parse_ads_from_response, deduplicate_window_ads
from config import (
    SEGMENT_CATEGORIES, SEGMENT_ACTIONS, DEFAULT_SEGMENT_ACTION,
    normalize_segment_category,
)
from main_app.processing import _partition_keep_ads


class TestNormalizeSegmentCategory:

    def test_valid_categories_pass_through(self):
        for cat in SEGMENT_CATEGORIES:
            assert normalize_segment_category(cat) == cat

    def test_none_normalizes_to_sponsor(self):
        assert normalize_segment_category(None) == 'sponsor'

    def test_unknown_string_normalizes_to_sponsor(self):
        assert normalize_segment_category('advertisement') == 'sponsor'

    def test_non_str_normalizes_to_sponsor(self):
        assert normalize_segment_category(42) == 'sponsor'
        assert normalize_segment_category(['sponsor']) == 'sponsor'
        assert normalize_segment_category({'category': 'sponsor'}) == 'sponsor'

    def test_constants_shape(self):
        assert 'sponsor' in SEGMENT_CATEGORIES
        assert DEFAULT_SEGMENT_ACTION in SEGMENT_ACTIONS
        assert DEFAULT_SEGMENT_ACTION == 'remove'


def _ad(start, end, detection_stage, confidence=0.9, category=None):
    ad = {'start': start, 'end': end, 'confidence': confidence,
          'reason': 'test', 'sponsor': None,
          'detection_stage': detection_stage}
    if category is not None:
        ad['category'] = category
    return ad


class TestMergeDetectionResultsStampsCategory:
    """The merge seam (_merge_detection_results) is the single point every
    stage's ads pass through, so it is the single normalization point."""

    def _det(self):
        return AdDetector(api_key='test-key')

    def test_fingerprint_ad_without_a_category_stays_unset(self):
        det = self._det()
        out = det._merge_detection_results([_ad(10.0, 40.0, 'fingerprint')])
        assert 'category' not in out[0]

    def test_text_pattern_ad_without_a_category_stays_unset(self):
        det = self._det()
        out = det._merge_detection_results([_ad(10.0, 40.0, 'text_pattern')])
        assert 'category' not in out[0]

    def test_dai_differential_ad_without_a_category_stays_unset(self):
        det = self._det()
        out = det._merge_detection_results([_ad(10.0, 40.0, 'dai_differential')])
        assert 'category' not in out[0]

    def test_llm_ad_valid_category_survives_merge(self):
        det = self._det()
        out = det._merge_detection_results(
            [_ad(10.0, 40.0, 'claude', category='cross_promo')])
        assert out[0]['category'] == 'cross_promo'

    def test_llm_ad_unknown_category_is_left_unset(self):
        det = self._det()
        out = det._merge_detection_results(
            [_ad(10.0, 40.0, 'claude', category='advertisement')])
        # Recording it as sponsor would claim a classification nothing made.
        assert out[0].get('category') is None

    def test_mixed_stage_ads_keep_valid_categories_and_leave_the_rest_unset(self):
        det = self._det()
        ads = [
            _ad(10.0, 40.0, 'fingerprint'),
            _ad(200.0, 240.0, 'text_pattern'),
            _ad(400.0, 440.0, 'dai_differential'),
            _ad(600.0, 640.0, 'claude', category='self_promo'),
        ]
        out = det._merge_detection_results(ads)
        assert len(out) == 4
        for marker in out:
            if 'category' in marker:
                assert marker['category'] in SEGMENT_CATEGORIES
        assert [m.get('category') for m in out] == [None, None, None, 'self_promo']


class TestParseAdsFromResponseCarriesCategory:
    """parse_ads_from_response passes the raw LLM category through
    unvalidated; normalize_segment_category is applied later at the merge
    seam, not here."""

    def test_valid_category_field_survives_extraction(self):
        response = json.dumps([{
            "start": 100.0, "end": 160.0, "confidence": 0.92,
            "reason": "BetterHelp ad", "sponsor": "BetterHelp",
            "category": "cross_promo",
        }])
        ads = parse_ads_from_response(response)
        assert len(ads) == 1
        assert ads[0]['category'] == 'cross_promo'

    def test_a_value_outside_the_vocabulary_counts_as_missing(self):
        """It used to be carried through and normalized to sponsor at the merge
        seam. Treating it as absent means the repair pass gets a chance at it,
        and the merge seam still defaults to sponsor if that fails."""
        response = json.dumps([{
            "start": 100.0, "end": 160.0, "confidence": 0.92,
            "reason": "ad", "sponsor": "X", "category": "advertisement",
        }])
        ads = parse_ads_from_response(response)
        assert 'category' not in ads[0]

    def test_missing_category_field_stays_absent(self):
        response = json.dumps([{
            "start": 100.0, "end": 160.0, "confidence": 0.92,
            "reason": "ad", "sponsor": "X",
        }])
        ads = parse_ads_from_response(response)
        assert 'category' not in ads[0]


def _episode_marker_payload_fields(markers):
    """Replicates the get_episode category/actionApplied mapping (see
    src/api/episodes.py) without spinning up Flask/DB, matching the
    _split_markers idiom used by test_pending_review_bucket.py."""
    out = []
    for marker in markers:
        marker = dict(marker)
        marker['category'] = marker.get('category', 'sponsor')
        marker['actionApplied'] = marker.get('action_applied')
        out.append(marker)
    return out


class TestEpisodePayloadCategoryFields:

    def test_defaults_when_marker_has_neither_field(self):
        out = _episode_marker_payload_fields([{'start': 1.0, 'end': 2.0}])
        assert out[0]['category'] == 'sponsor'
        assert out[0]['actionApplied'] is None

    def test_existing_category_and_action_applied_preserved(self):
        out = _episode_marker_payload_fields([{
            'start': 1.0, 'end': 2.0,
            'category': 'self_promo', 'action_applied': 'beep',
        }])
        assert out[0]['category'] == 'self_promo'
        assert out[0]['actionApplied'] == 'beep'


def _all_remove_map():
    return {cat: 'remove' for cat in SEGMENT_CATEGORIES}


class TestMergeGatedOnResolvedAction:
    """The detection-merge seam must not fold a keep-resolving detection
    into a remove-resolving one within the 3s adjacency window: that
    discarded the keep side's category and cut audio no downstream check
    could see as kept."""

    def _det(self):
        return AdDetector(api_key='test-key')

    def test_different_actions_within_3s_do_not_merge(self):
        det = self._det()
        action_map = dict(_all_remove_map(), self_promo='keep')
        sponsor_ad = _ad(0.0, 20.0, 'text_pattern', category='sponsor')
        self_promo_ad = _ad(20.0, 30.0, 'claude', category='self_promo')

        out = det._merge_detection_results(
            [sponsor_ad, self_promo_ad], action_map=action_map)

        assert len(out) == 2
        categories = {m['category'] for m in out}
        assert categories == {'sponsor', 'self_promo'}

        keep_ads, remove_ads = _partition_keep_ads(out, action_map)
        assert len(keep_ads) == 1
        assert keep_ads[0]['category'] == 'self_promo'
        assert len(remove_ads) == 1
        assert remove_ads[0]['category'] == 'sponsor'

    def test_all_remove_map_merges_exactly_as_today(self):
        """Regression: an all-remove map (today's default) still merges the
        same adjacency-window pair into one marker, exactly as pre-fix."""
        det = self._det()
        action_map = _all_remove_map()
        sponsor_ad = _ad(0.0, 20.0, 'text_pattern', category='sponsor')
        self_promo_ad = _ad(20.0, 30.0, 'claude', category='self_promo')

        out = det._merge_detection_results(
            [sponsor_ad, self_promo_ad], action_map=action_map)

        assert len(out) == 1
        assert out[0]['start'] == 0.0
        assert out[0]['end'] == 30.0

    def test_none_map_regression_module_level_call_sites_unaffected(self):
        """A module-level caller with no slug/db resolves no action_map
        (None); the merge must behave exactly as before this fix regardless
        of differing categories."""
        det = self._det()
        sponsor_ad = _ad(0.0, 20.0, 'text_pattern', category='sponsor')
        self_promo_ad = _ad(20.0, 30.0, 'claude', category='self_promo')

        out = det._merge_detection_results([sponsor_ad, self_promo_ad])

        assert len(out) == 1
        assert out[0]['start'] == 0.0
        assert out[0]['end'] == 30.0

    def test_true_overlap_with_different_actions_clamps_instead_of_merging(self):
        det = self._det()
        action_map = dict(_all_remove_map(), interaction='keep')
        sponsor_ad = _ad(0.0, 25.0, 'text_pattern', category='sponsor')
        interaction_ad = _ad(20.0, 30.0, 'claude', category='interaction')

        out = det._merge_detection_results(
            [sponsor_ad, interaction_ad], action_map=action_map)

        assert len(out) == 2
        by_cat = {m['category']: m for m in out}
        assert by_cat['sponsor']['start'] == 0.0
        assert by_cat['sponsor']['end'] == 25.0
        # Clamped past the sponsor marker's end so the two spans no longer
        # double-cut the same 20.0-25.0s audio.
        assert by_cat['interaction']['start'] == 25.0
        assert by_cat['interaction']['end'] == 30.0


class TestMergeDuplicateOverlapPrefersKeepCategory:
    """A >=80%-overlap duplicate fold must take the keep-resolving side's
    category when the two sides' resolved actions differ, so contested
    audio is never cut."""

    def _det(self):
        return AdDetector(api_key='test-key')

    def test_sponsor_remove_vs_interaction_keep_combines_to_interaction(self):
        det = self._det()
        action_map = dict(_all_remove_map(), interaction='keep')
        # A=[0,100] dur 100, B=[10,100] dur 90: overlap 90/90 = 1.0 (>= 0.8)
        sponsor_ad = _ad(0.0, 100.0, 'text_pattern', confidence=0.7, category='sponsor')
        interaction_ad = _ad(10.0, 100.0, 'claude', confidence=0.9, category='interaction')

        out = det._merge_overlapping_accepted_duplicates(
            [sponsor_ad, interaction_ad], action_map=action_map)

        assert len(out) == 1
        assert out[0]['category'] == 'interaction'

    def test_same_action_prefers_higher_confidence_primary_category(self):
        det = self._det()
        action_map = _all_remove_map()
        low_conf = _ad(0.0, 100.0, 'text_pattern', confidence=0.6, category='sponsor')
        high_conf = _ad(10.0, 100.0, 'claude', confidence=0.9, category='cross_promo')

        out = det._merge_overlapping_accepted_duplicates(
            [low_conf, high_conf], action_map=action_map)

        assert len(out) == 1
        assert out[0]['category'] == 'cross_promo'

    def test_none_map_falls_back_to_higher_confidence_primary_category(self):
        """No action_map (module-level caller): the differ/same-action
        distinction cannot be resolved, so the combined category always
        comes from the higher-confidence contributor."""
        det = self._det()
        low_conf = _ad(0.0, 100.0, 'text_pattern', confidence=0.6, category='sponsor')
        high_conf = _ad(10.0, 100.0, 'claude', confidence=0.9, category='interaction')

        out = det._merge_overlapping_accepted_duplicates([low_conf, high_conf])

        assert len(out) == 1
        assert out[0]['category'] == 'interaction'


DTNS_ACTION_MAP = {
    'sponsor': 'remove', 'interaction': 'remove',
    'cross_promo': 'keep', 'self_promo': 'keep',
    'intro': 'keep', 'outro': 'keep', 'recap': 'keep',
}


def _dtns5317_raw_llm_detections():
    """The real 9 raw window detections from daily-tech-news-show episode
    3c0b827ef2c5 (reprocessed on 2.78.1, 2026-07-25 02:55 UTC). Only the
    intro and outro carried a category; the other 7 did not."""
    return [
        {'start': 0.0, 'end': 156.7, 'confidence': 0.98,
         'reason': 'Pre-roll ad block: Capital One, Olly Sleep, Cologuard, '
                   'and Morning Brew Daily sponsor reads'},
        {'start': 158.0, 'end': 166.6, 'confidence': 0.9, 'category': 'intro',
         'reason': 'Show intro marker/theme'},
        {'start': 687.5, 'end': 845.5, 'confidence': 0.98,
         'reason': 'Ad break with multiple sponsors: Capital One, Michaels, '
                   'Morning Brew Daily podcast promo, Stamps.com, Vanta'},
        {'start': 814.2, 'end': 845.5, 'confidence': 0.97,
         'reason': 'Vanta sponsor read with call to action (vanta.com), '
                   'continues from previous window'},
        {'start': 1502.5, 'end': 1562.5, 'confidence': 0.9,
         'reason': "Patreon promotion with promo code 'experiment' for 26% "
                   'off, call to action patreon.com/DTNS'},
        {'start': 1900.1, 'end': 1972.2, 'confidence': 0.98,
         'reason': 'Ad break with Capital One and Noom sponsor reads, '
                   'bracketed by ad-break boundary cues'},
        {'start': 2314.1, 'end': 2319.2, 'confidence': 0.9,
         'reason': 'Patreon promo with code experiment and URL patreon.com/DTNS'},
        {'start': 2324.5, 'end': 2381.1, 'confidence': 0.85, 'category': 'outro',
         'reason': 'Show credits and DTNS Family of Podcasts sign-off'},
        {'start': 2385.8, 'end': 2444.9, 'confidence': 0.97,
         'reason': 'Capital One and Stamps.com sponsor ads with promo code podcast'},
    ]


class TestDTNS5317IntroOutroSurviveFullPipeline:
    """End-to-end reproduction of DTNS 5317: the raw 9 LLM window
    detections, run through deduplicate_window_ads and
    _merge_detection_results with the feed's real action map, must keep
    the intro and outro as distinct 'keep' markers while every other span
    still resolves to 'remove', so no ad content is left uncut."""

    def test_intro_and_outro_survive_as_keep_markers(self):
        det = AdDetector(api_key='test-key')

        deduped = deduplicate_window_ads(
            _dtns5317_raw_llm_detections(), action_map=DTNS_ACTION_MAP)
        merged = det._merge_detection_results(deduped, action_map=DTNS_ACTION_MAP)

        keep_ads, remove_ads = _partition_keep_ads(merged, DTNS_ACTION_MAP)

        keep_by_cat = {m['category']: m for m in keep_ads}
        assert keep_by_cat['intro']['start'] == 158.0
        assert keep_by_cat['intro']['end'] == 166.6
        assert keep_by_cat['intro']['action_applied'] == 'keep'
        assert keep_by_cat['intro']['was_cut'] is False

        assert keep_by_cat['outro']['start'] == 2324.5
        assert keep_by_cat['outro']['end'] == 2381.1
        assert keep_by_cat['outro']['action_applied'] == 'keep'
        assert keep_by_cat['outro']['was_cut'] is False

        # Every remove-side marker still resolves to 'sponsor'/'remove':
        # the surrounding ad content is still cut, only the categorized
        # keep spans are protected.
        assert all(normalize_segment_category(m.get('category')) == 'sponsor'
                   for m in remove_ads)
        remove_by_start = {m['start']: m for m in remove_ads}
        assert remove_by_start[0.0]['end'] == 156.7
        assert remove_by_start[2385.8]['end'] == 2444.9

    def test_all_remove_map_still_cuts_everything(self):
        """Regression: a default (all-remove) feed's identical raw
        detections must still merge and cut exactly as before: the
        category-blind window fusion of intro/outro into their neighbours
        is only a bug when it discards a keep resolution."""
        det = AdDetector(api_key='test-key')
        all_remove = {cat: 'remove' for cat in DTNS_ACTION_MAP}

        deduped = deduplicate_window_ads(
            _dtns5317_raw_llm_detections(), action_map=all_remove)
        merged = det._merge_detection_results(deduped, action_map=all_remove)

        keep_ads, remove_ads = _partition_keep_ads(merged, all_remove)
        assert keep_ads == []
        # Every marker resolves to 'remove' regardless of its stamped
        # category label (an all-remove map cuts everything).
        assert all(all_remove[normalize_segment_category(m.get('category'))] == 'remove'
                   for m in remove_ads)
        # Intro fused into the pre-roll block, outro fused into the
        # trailing sponsor ad, matching the unfixed shape.
        by_start = {m['start']: m for m in remove_ads}
        assert by_start[0.0]['end'] == 166.6
        assert by_start[2324.5]['end'] == 2444.9


class TestMergedSpanTakesTheDominantCategory:
    """A merged span used to keep whichever member sorted first, so a break
    holding a sponsor read, a self-promo and a cross-promo was labelled by
    accident. Observed on a real episode: three distinct categories at
    detection, all four final markers reading sponsor.
    """

    def _detector(self):
        from ad_detector import AdDetector
        d = AdDetector.__new__(AdDetector)
        d.pattern_service = None
        return d

    def _ad(self, start, end, category, conf=0.95):
        return {'start': start, 'end': end, 'confidence': conf,
                'category': category, 'reason': f'{category} read',
                'detection_stage': 'claude'}

    def test_the_longest_member_names_the_span(self):
        merged = self._detector()._merge_detection_results([
            self._ad(100.0, 120.0, 'self_promo'),
            self._ad(110.0, 200.0, 'sponsor'),
        ])
        assert len(merged) == 1
        assert merged[0]['category'] == 'sponsor'

    def test_order_does_not_decide_it(self):
        """Same spans, reversed input: the answer must not change."""
        merged = self._detector()._merge_detection_results([
            self._ad(110.0, 200.0, 'sponsor'),
            self._ad(100.0, 120.0, 'self_promo'),
        ])
        assert merged[0]['category'] == 'sponsor'

    def test_an_uncategorized_extension_does_not_credit_the_incumbent(self):
        """The incumbent's span was captured lazily at the first categorized
        competitor, after an uncategorized member had already extended the
        merged end, so it was credited audio it never classified."""
        merged = self._detector()._merge_detection_results([
            self._ad(100.0, 130.0, 'sponsor'),
            self._ad(100.0, 300.0, None),
            self._ad(130.0, 300.0, 'self_promo'),
        ])
        assert len(merged) == 1
        assert merged[0]['category'] == 'self_promo'

    def test_an_invalid_category_cannot_displace_a_valid_one(self):
        merged = self._detector()._merge_detection_results([
            self._ad(100.0, 130.0, 'self_promo'),
            self._ad(100.0, 300.0, 'advertisement'),
        ])
        assert merged[0]['category'] == 'self_promo'

    def test_a_short_dominant_member_keeps_its_label(self):
        merged = self._detector()._merge_detection_results([
            self._ad(100.0, 190.0, 'cross_promo'),
            self._ad(180.0, 200.0, 'sponsor'),
        ])
        assert merged[0]['category'] == 'cross_promo'

    def test_a_member_without_a_category_never_displaces_one(self):
        uncategorized = {'start': 110.0, 'end': 300.0, 'confidence': 0.95,
                         'reason': 'no category given', 'detection_stage': 'claude'}
        merged = self._detector()._merge_detection_results([
            self._ad(100.0, 120.0, 'cross_promo'), uncategorized,
        ])
        assert merged[0]['category'] == 'cross_promo'

    def test_the_bookkeeping_key_is_stripped(self):
        merged = self._detector()._merge_detection_results([
            self._ad(100.0, 120.0, 'self_promo'),
            self._ad(110.0, 200.0, 'sponsor'),
        ])
        assert '_category_span' not in merged[0]


class TestCategoryIsNotASponsorName:
    """The sponsor scan falls back to any short string field, and 'category'
    was not excluded, so a verification-pass ad reported sponsor='self_promo'."""

    def _sponsor_for(self, ad):
        parsed = parse_ads_from_response(json.dumps([ad]))
        return parsed[0].get('sponsor') if parsed else None

    def test_category_is_not_used_as_the_sponsor_name(self):
        sponsor = self._sponsor_for({
            'start': 10.0, 'end': 60.0, 'confidence': 0.95,
            'category': 'self_promo',
            'reason': 'Patreon promo',
        })

        assert sponsor != 'self_promo'

    def test_a_real_sponsor_field_still_wins(self):
        sponsor = self._sponsor_for({
            'start': 10.0, 'end': 60.0, 'confidence': 0.95,
            'category': 'sponsor', 'sponsor': 'Acme',
            'reason': 'Acme host read',
        })

        assert sponsor == 'Acme'

    def test_segment_type_is_not_used_either(self):
        sponsor = self._sponsor_for({
            'start': 10.0, 'end': 60.0, 'confidence': 0.95,
            'segment_type': 'cross_promo',
            'reason': 'Promo for another show',
        })

        assert sponsor != 'cross_promo'


class TestUncategorizedIsNotSponsor:
    """'sponsor' used to double as 'nobody classified this', which made a real
    sponsor read indistinguishable from an unclassified marker."""

    def test_a_stage_that_never_classifies_stays_uncategorized(self):
        out = AdDetector(api_key='test-key')._merge_detection_results(
            [_ad(10.0, 60.0, 'cue_pair')])

        assert 'category' not in out[0]

    def test_a_real_category_still_survives_the_merge(self):
        out = AdDetector(api_key='test-key')._merge_detection_results(
            [_ad(10.0, 60.0, 'claude', category='self_promo')])

        assert out[0]['category'] == 'self_promo'

    def test_an_invalid_category_is_left_unset(self):
        out = AdDetector(api_key='test-key')._merge_detection_results(
            [_ad(10.0, 60.0, 'claude', category='not_a_real_category')])

        assert out[0].get('category') is None

    def test_action_resolution_still_reads_unknown_as_sponsor(self):
        """Cutting behaviour must not change: unknown stays conservative."""
        from config import normalize_segment_category

        assert normalize_segment_category(None) == 'sponsor'


class TestStagesThatClassifyAtSource:
    """A DAI insert and a foreign-language block are paid ads by definition,
    so they carry a category rather than relying on a downstream default."""

    def test_differential_ads_are_sponsors(self):
        from ad_detector import dai_differential_ads
        ads = dai_differential_ads(
            {'regions': [{'kind': 'differential', 'corr': 0.1,
                          'start_s': 100.0, 'end_s': 200.0}]}, [],
            corroborating_spans=[(100.0, 200.0)])

        assert ads and all(a['category'] == 'sponsor' for a in ads)


class TestVerificationPromptCoversEveryCategory:
    def test_every_valid_category_is_offered_to_the_verification_pass(self):
        """Three categories were unreachable from pass 2 while the settings UI
        still exposed a per-category action for each of them."""
        from config import SEGMENT_CATEGORIES
        from database import DEFAULT_VERIFICATION_PROMPT

        missing = [c for c in SEGMENT_CATEGORIES if c not in DEFAULT_VERIFICATION_PROMPT]
        assert missing == []


class TestUnsetStaysUnsetOnEverySavePath:
    """The merge seam is not the only place a marker's category is settled."""

    def test_a_pass2_marker_with_no_category_stays_unset(self):
        from main_app.processing import _stamp_pass2_marker_categories

        markers = [{'start': 1.0, 'end': 2.0},
                   {'start': 3.0, 'end': 4.0, 'category': 'not-a-category'},
                   {'start': 5.0, 'end': 6.0, 'category': 'outro'}]

        _stamp_pass2_marker_categories(markers)

        assert 'category' not in markers[0]
        assert 'category' not in markers[1]
        assert markers[2]['category'] == 'outro'

    def test_the_duplicate_fold_does_not_invent_a_category(self):
        from ad_detector import AdDetector

        detector = AdDetector.__new__(AdDetector)
        detector.pattern_service = None
        folded = detector._merge_overlapping_accepted_duplicates([
            {'start': 100.0, 'end': 200.0, 'confidence': 0.9,
             'was_cut': True, 'detection_stage': 'claude'},
            {'start': 100.0, 'end': 200.0, 'confidence': 0.8,
             'was_cut': True, 'detection_stage': 'text_pattern'},
        ])

        assert len(folded) == 1
        assert folded[0].get('category') is None


class TestSplitSpansKeepTheirOwnCategoryReach:
    """split_conflicting_action_span rewrites the accumulator, so its entries
    need the same span stamp the append sites give theirs."""

    def _detector(self):
        from ad_detector import AdDetector
        d = AdDetector.__new__(AdDetector)
        d.pattern_service = None
        return d

    def _ad(self, start, end, category):
        return {'start': start, 'end': end, 'confidence': 0.95,
                'category': category, 'reason': f'{category} read',
                'detection_stage': 'claude'}

    def test_a_short_member_cannot_relabel_a_split_span(self):
        actions = {'sponsor': 'remove', 'cross_promo': 'remove',
                   'self_promo': 'remove', 'interaction': 'remove',
                   'intro': 'keep', 'outro': 'remove', 'recap': 'remove'}

        merged = self._detector()._merge_detection_results([
            self._ad(0.0, 60.0, 'intro'),
            self._ad(30.0, 300.0, 'sponsor'),
            self._ad(295.0, 305.0, 'cross_promo'),
        ], action_map=actions)

        long_span = max(merged, key=lambda m: m['end'] - m['start'])
        assert long_span['category'] == 'sponsor'

    def test_an_unrecognized_category_is_not_recorded_as_sponsor(self):
        merged = self._detector()._merge_detection_results([
            self._ad(100.0, 200.0, 'advertisement'),
        ])

        assert merged[0].get('category') is None
