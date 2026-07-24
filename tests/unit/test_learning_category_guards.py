"""Task 7: learning guards on keep-action markers, and ad_patterns.category.

Covers:
- Corrections guard: a marker resolved to action_applied == 'keep' can
  never create a pattern_correction row (which is also the row that would
  seed cross-episode false-positive text -- both flow through the same
  create_pattern_correction call, so guarding correction creation guards
  FP-text creation too).
- ad_patterns.category: additive NULL column; NULL reads back as 'sponsor';
  the pattern learner stores a marker's category on newly created patterns.
- Community sync: export includes category; import without one defaults to
  'sponsor' via the same NULL read default.
- Kept markers still reach pattern learning -- only correction/FP-text
  creation is guarded, not learning.
"""
import json
import os
import sys

from tests.app_bootstrap import bootstrap

_test_data_dir = bootstrap('learn_cat_test_')

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))

from unittest.mock import MagicMock, patch  # noqa: E402

import pytest  # noqa: E402

import main_app.processing as processing  # noqa: E402
from ad_detector import AdDetector  # noqa: E402
from audio_fingerprinter import AudioFingerprinter, FingerprintMatch  # noqa: E402
from community_export import build_export_payload  # noqa: E402
from community_sync import apply_manifest  # noqa: E402
from main_app import app  # noqa: E402
from pattern_service import PatternService  # noqa: E402
from sponsor_normalize import get_or_create_known_sponsor  # noqa: E402
from text_pattern_matcher import AdPattern, TextMatch, TextPatternMatcher  # noqa: E402


@pytest.fixture
def client():
    app.config['TESTING'] = True
    with app.test_client() as c:
        yield c


SLUG = 'learn-cat-test'
EPISODE_ID = 'abc123def009'

# Real sponsor read: brand appears twice, no double ad-transition phrase,
# passes create_pattern_from_ad's internal quality gates.
BETTERHELP_AD_TEXT = (
    "BetterHelp therapy can help you live a more empowered life. "
    "Visit them online to start with a licensed therapist today. "
    "BetterHelp matches you in 24 hours."
)


def _seed_episode(temp_db, slug=SLUG, episode_id=EPISODE_ID, markers=None):
    temp_db.create_podcast(slug, 'https://example.com/feed.xml', 'Learn Cat Test')
    temp_db.upsert_episode(
        slug=slug, episode_id=episode_id,
        original_url='https://example.com/ep.mp3',
        title='Test Episode', original_duration=600.0,
    )
    if markers is not None:
        temp_db.save_episode_details(slug, episode_id, ad_markers=markers)


def _keep_marker(start=100.0, end=130.0, category='cross_promo'):
    return {
        'start': start, 'end': end, 'sponsor': 'OurOwnShow',
        'reason': 'cross-promo for our other show', 'confidence': 0.9,
        'detection_stage': 'claude', 'pattern_id': None,
        'category': category, 'action_applied': 'keep', 'was_cut': False,
    }


def _remove_marker(start=200.0, end=230.0):
    return {
        'start': start, 'end': end, 'sponsor': 'SpansCo',
        'reason': 'sponsor read', 'confidence': 0.9,
        'detection_stage': 'claude', 'pattern_id': None,
        'category': 'sponsor', 'was_cut': True,
    }


def _correction_payload(correction_type, start, end, *, adjusted=None):
    payload = {
        'type': correction_type,
        'original_ad': {'start': start, 'end': end, 'sponsor': 'X', 'reason': 'r'},
    }
    if adjusted:
        payload['adjusted_start'], payload['adjusted_end'] = adjusted
    return payload


# ========== 1. Corrections guard ==========

class TestKeepMarkerCorrectionGuard:

    def test_reject_on_keep_marker_is_non_actionable(self, client, temp_db):
        _seed_episode(temp_db, markers=[_keep_marker()])
        with patch('api.patterns.get_database', return_value=temp_db):
            resp = client.post(
                f'/api/v1/episodes/{SLUG}/{EPISODE_ID}/corrections',
                data=json.dumps(_correction_payload('reject', 100.0, 130.0)),
                content_type='application/json',
            )
        assert resp.status_code == 409, resp.data
        assert temp_db.get_episode_corrections(EPISODE_ID) == []
        assert temp_db.get_podcast_false_positive_texts(SLUG) == []

    def test_confirm_on_keep_marker_is_non_actionable(self, client, temp_db):
        _seed_episode(temp_db, markers=[_keep_marker()])
        with patch('api.patterns.get_database', return_value=temp_db):
            resp = client.post(
                f'/api/v1/episodes/{SLUG}/{EPISODE_ID}/corrections',
                data=json.dumps(_correction_payload('confirm', 100.0, 130.0)),
                content_type='application/json',
            )
        assert resp.status_code == 409, resp.data
        assert temp_db.get_episode_corrections(EPISODE_ID) == []

    def test_adjust_on_keep_marker_is_non_actionable(self, client, temp_db):
        _seed_episode(temp_db, markers=[_keep_marker()])
        with patch('api.patterns.get_database', return_value=temp_db):
            resp = client.post(
                f'/api/v1/episodes/{SLUG}/{EPISODE_ID}/corrections',
                data=json.dumps(_correction_payload(
                    'adjust', 100.0, 130.0, adjusted=(105.0, 125.0))),
                content_type='application/json',
            )
        assert resp.status_code == 409, resp.data
        assert temp_db.get_episode_corrections(EPISODE_ID) == []

    def test_reject_on_non_keep_marker_is_unaffected(self, client, temp_db):
        """Regression guard: a marker whose action_applied is not 'keep'
        (the all-remove default) must still be correctable normally."""
        _seed_episode(temp_db, markers=[_remove_marker()])
        with patch('api.patterns.get_database', return_value=temp_db):
            resp = client.post(
                f'/api/v1/episodes/{SLUG}/{EPISODE_ID}/corrections',
                data=json.dumps(_correction_payload('reject', 200.0, 230.0)),
                content_type='application/json',
            )
        assert resp.status_code == 200, resp.data
        assert len(temp_db.get_episode_corrections(EPISODE_ID)) == 1

    def test_reject_with_no_matching_marker_is_unaffected(self, client, temp_db):
        """No persisted marker at all (e.g. a stale client payload) must not
        be treated as a keep marker -- the guard only fires on an actual
        action_applied == 'keep' match."""
        _seed_episode(temp_db, markers=[])
        with patch('api.patterns.get_database', return_value=temp_db):
            resp = client.post(
                f'/api/v1/episodes/{SLUG}/{EPISODE_ID}/corrections',
                data=json.dumps(_correction_payload('reject', 200.0, 230.0)),
                content_type='application/json',
            )
        assert resp.status_code == 200, resp.data


# ========== 2 & 3. ad_patterns.category storage and read default ==========

class TestPatternCategoryColumn:

    def test_null_category_reads_as_sponsor(self, temp_db):
        pid = temp_db.create_ad_pattern(
            scope='podcast', text_template='x' * 60, podcast_id=SLUG,
        )
        pattern = temp_db.get_ad_pattern_by_id(pid)
        assert pattern['category'] == 'sponsor'

    def test_explicit_category_round_trips(self, temp_db):
        pid = temp_db.create_ad_pattern(
            scope='podcast', text_template='x' * 60, podcast_id=SLUG,
            category='cross_promo',
        )
        pattern = temp_db.get_ad_pattern_by_id(pid)
        assert pattern['category'] == 'cross_promo'

    def test_unrecognized_category_normalizes_to_sponsor(self, temp_db):
        pid = temp_db.create_ad_pattern(
            scope='podcast', text_template='x' * 60, podcast_id=SLUG,
            category='not-a-real-category',
        )
        pattern = temp_db.get_ad_pattern_by_id(pid)
        assert pattern['category'] == 'sponsor'

    def test_list_patterns_also_defaults_null_category(self, temp_db):
        temp_db.create_ad_pattern(
            scope='podcast', text_template='x' * 60, podcast_id=SLUG,
        )
        rows = temp_db.get_ad_patterns(podcast_id=SLUG, active_only=False)
        assert len(rows) == 1
        assert rows[0]['category'] == 'sponsor'

    def test_learner_stores_marker_category_on_new_pattern(self, temp_db):
        matcher = TextPatternMatcher(db=temp_db)
        pattern_id = matcher.create_pattern_from_ad(
            segments=[{'start': 0.0, 'end': 60.0, 'text': BETTERHELP_AD_TEXT}],
            start=0.0, end=60.0, sponsor='BetterHelp',
            scope='podcast', podcast_id=SLUG, episode_id=EPISODE_ID,
            category='cross_promo',
        )
        assert pattern_id is not None
        pattern = temp_db.get_ad_pattern_by_id(pattern_id)
        assert pattern['category'] == 'cross_promo'

    def test_learner_with_no_category_defaults_sponsor(self, temp_db):
        matcher = TextPatternMatcher(db=temp_db)
        pattern_id = matcher.create_pattern_from_ad(
            segments=[{'start': 0.0, 'end': 60.0, 'text': BETTERHELP_AD_TEXT}],
            start=0.0, end=60.0, sponsor='BetterHelp',
            scope='podcast', podcast_id=SLUG, episode_id=EPISODE_ID,
        )
        assert pattern_id is not None
        pattern = temp_db.get_ad_pattern_by_id(pattern_id)
        assert pattern['category'] == 'sponsor'


# ========== 4. Community sync ==========

class TestCommunitySyncCategory:

    def test_export_includes_category(self, temp_db):
        sponsor_id = get_or_create_known_sponsor(temp_db, 'SpansCo')
        pid = temp_db.create_ad_pattern(
            scope='global', text_template=(
                'This episode is brought to you by SpansCo, our favorite '
                'sponsor. SpansCo helps you learn something new every day.'
            ),
            sponsor_id=sponsor_id, category='cross_promo',
        )
        temp_db.update_ad_pattern(pid, confirmation_count=2)
        pattern = temp_db.get_ad_pattern_by_id(pid)
        sponsors = temp_db.get_known_sponsors(active_only=False)
        payload = build_export_payload(pattern, sponsors)
        assert payload['category'] == 'cross_promo'

    def test_import_without_category_defaults_sponsor(self, temp_db):
        pattern_service = PatternService(temp_db)
        data = {
            'community_id': 'cid-no-category',
            'version': 1,
            'scope': 'global',
            'sponsor': 'NoCategoryCo',
            'text_template': (
                'This episode is brought to you by NoCategoryCo. '
                'NoCategoryCo makes everything better and faster.'
            ),
        }
        pattern_id = pattern_service.import_community_pattern(data)
        pattern = temp_db.get_ad_pattern_by_id(pattern_id)
        assert pattern['category'] == 'sponsor'

    def test_import_with_category_round_trips(self, temp_db):
        pattern_service = PatternService(temp_db)
        data = {
            'community_id': 'cid-with-category',
            'version': 1,
            'scope': 'global',
            'sponsor': 'WithCategoryCo',
            'text_template': (
                'This episode is brought to you by WithCategoryCo. '
                'WithCategoryCo makes everything better and faster.'
            ),
            'category': 'self_promo',
        }
        pattern_id = pattern_service.import_community_pattern(data)
        pattern = temp_db.get_ad_pattern_by_id(pattern_id)
        assert pattern['category'] == 'self_promo'

    def test_apply_manifest_import_without_category_defaults_sponsor(self, temp_db):
        """End-to-end through the actual community_sync manifest applier;
        format/version keys are untouched by the category addition."""
        summary = apply_manifest(temp_db, {
            'manifest_version': 1,
            'patterns': [{
                'community_id': 'cid-manifest',
                'version': 1,
                'data': {
                    'community_id': 'cid-manifest',
                    'version': 1,
                    'scope': 'global',
                    'sponsor': 'ManifestCo',
                    'text_template': (
                        'This episode is brought to you by ManifestCo. '
                        'ManifestCo has the best deals around town today.'
                    ),
                    'intro_variants': [],
                    'outro_variants': [],
                },
            }],
        })
        assert summary['inserted'] == 1
        rows = temp_db.get_patterns_by_source('community', active_only=False)
        assert len(rows) == 1
        assert rows[0]['category'] == 'sponsor'


# ========== 5. Kept markers still reach pattern learning ==========

class TestKeptMarkersStillLearn:

    def test_learning_filter_allows_keep_action_marker(self):
        det = AdDetector(api_key='test-key')
        keep_ad = {
            'start': 0.0, 'end': 60.0, 'was_cut': False,
            'action_applied': 'keep', 'detection_stage': 'claude',
            'confidence': 0.95,
        }
        assert det._ad_passes_learning_filters(keep_ad, min_confidence=0.5) is True

    def test_learning_filter_still_rejects_plain_uncut_marker(self):
        """Sanity: an ordinary uncut marker (no action_applied at all --
        e.g. a rejected correction) must not slip through the relaxed check."""
        det = AdDetector(api_key='test-key')
        uncut_ad = {
            'start': 0.0, 'end': 60.0, 'was_cut': False,
            'detection_stage': 'claude', 'confidence': 0.95,
        }
        assert det._ad_passes_learning_filters(uncut_ad, min_confidence=0.5) is False

    def test_learn_from_detections_reaches_matcher_for_keep_marker(self):
        det = AdDetector(api_key='test-key')
        det.db = MagicMock()
        det.db.get_active_pattern_sponsors = MagicMock(return_value=set())
        det.db.get_setting_float = MagicMock(side_effect=lambda key, default: default)
        det.text_pattern_matcher = MagicMock()
        det.text_pattern_matcher.create_pattern_from_ad = MagicMock(return_value=1)
        det.sponsor_service = MagicMock()
        det.sponsor_service.get_sponsors = MagicMock(return_value=[])
        det.sponsor_service.find_sponsor_in_text = MagicMock(return_value='OurOwnShow')
        det.audio_fingerprinter = None

        keep_ad = {
            'sponsor': 'OurOwnShow', 'start': 0.0, 'end': 60.0,
            'was_cut': False, 'action_applied': 'keep',
            'detection_stage': 'claude', 'confidence': 0.95,
            'category': 'cross_promo',
        }
        learned = det.learn_from_detections(
            [keep_ad], [{'start': 0, 'end': 60, 'text': 'x'}],
            podcast_id='podA', episode_id='ep1',
        )
        assert learned == 1
        det.text_pattern_matcher.create_pattern_from_ad.assert_called_once()
        assert (det.text_pattern_matcher.create_pattern_from_ad
                .call_args.kwargs['category']) == 'cross_promo'

    def test_learn_from_kept_ads_calls_learn_from_detections(self):
        keep_ads = [{'start': 0.0, 'end': 60.0, 'was_cut': False,
                     'action_applied': 'keep', 'category': 'cross_promo'}]
        segments = [{'start': 0, 'end': 60, 'text': 'x'}]
        with patch.object(processing, 'ad_detector') as mock_detector:
            mock_detector.learn_from_detections.return_value = 1
            result = processing._learn_from_kept_ads(
                SLUG, EPISODE_ID, keep_ads, segments, '/tmp/fake.mp3'
            )
        assert result == 1
        mock_detector.learn_from_detections.assert_called_once_with(
            keep_ads, segments, SLUG, EPISODE_ID, audio_path='/tmp/fake.mp3'
        )

    def test_learn_from_kept_ads_is_noop_without_keep_ads(self):
        with patch.object(processing, 'ad_detector') as mock_detector:
            result = processing._learn_from_kept_ads(
                SLUG, EPISODE_ID, [], [], '/tmp/fake.mp3'
            )
        assert result == 0
        mock_detector.learn_from_detections.assert_not_called()

    def test_learn_from_kept_ads_is_noop_without_slug(self):
        with patch.object(processing, 'ad_detector') as mock_detector:
            result = processing._learn_from_kept_ads(
                None, EPISODE_ID, [{'start': 0.0, 'end': 60.0}], [], '/tmp/fake.mp3'
            )
        assert result == 0
        mock_detector.learn_from_detections.assert_not_called()


# ========== 6. Pattern matches inherit stored segment category ==========
#
# Reviewer High (post-Task-7 review): fingerprint/text-pattern RE-MATCHES of
# an already-learned pattern dropped the pattern's stored category on the
# floor -- TextMatch/FingerprintMatch never carried it, so a pattern learned
# from a kept cross_promo marker re-matched on a later episode with no
# category, fell through _merge_detection_results' normalize_segment_category
# default to 'sponsor', resolved to 'remove' on a feed that keeps
# cross_promo, and cut content the feed's settings say to keep. Fixed by
# threading category end to end: AdPattern/TextMatch (text_pattern_matcher.py),
# FingerprintMatch + the fingerprint DB join (audio_fingerprinter.py,
# database/fingerprints.py), and _add_pattern_match (ad_detector/__init__.py).

class TestPatternMatchCategoryInheritance:

    def test_load_patterns_carries_category_from_db(self, temp_db):
        pid = temp_db.create_ad_pattern(
            scope='global', text_template='x' * 60, category='cross_promo',
        )
        matcher = TextPatternMatcher(db=temp_db)
        matcher._load_patterns()
        pattern = next(p for p in matcher._patterns if p.id == pid)
        assert pattern.category == 'cross_promo'

    def test_load_patterns_null_category_defaults_sponsor(self, temp_db):
        """Legacy/uncategorized pattern: NULL in the DB, normalized to
        'sponsor' by get_ad_patterns' _row_with_category before AdPattern
        ever sees it."""
        pid = temp_db.create_ad_pattern(scope='global', text_template='x' * 60)
        matcher = TextPatternMatcher(db=temp_db)
        matcher._load_patterns()
        pattern = next(p for p in matcher._patterns if p.id == pid)
        assert pattern.category == 'sponsor'

    def test_find_phrase_matches_carries_pattern_category(self):
        """Pure unit test of the fuzzy phrase-match path (deterministic,
        no TF-IDF threshold sensitivity): the returned TextMatch carries the
        matched AdPattern's category."""
        matcher = TextPatternMatcher(db=None)
        pattern = AdPattern(
            id=1, text_template='x',
            intro_variants=['this is a sponsor read for spanshoe today'],
            outro_variants=[], sponsor='SpanShoe', scope='global',
            category='cross_promo',
        )
        text = ('this is a sponsor read for spanshoe today and more '
                'content follows right after this point')
        segments = [{'start': 0.0, 'end': 10.0, 'text': text}]
        full_text = text + ' '
        segment_map = [(0, len(full_text), 0)]
        matches = matcher._find_phrase_matches(full_text, segments, segment_map, [pattern])
        assert len(matches) >= 1
        assert all(m.category == 'cross_promo' for m in matches)

    def test_add_pattern_match_and_merge_seam_carry_text_match_category(self):
        """End-to-end through the exact seam the reviewer flagged: a
        TextMatch's category survives _add_pattern_match (the detection
        dict) and _merge_detection_results (the merge seam that otherwise
        stamps 'sponsor')."""
        det = AdDetector(api_key='test-key')
        all_ads, regions = [], []
        match = TextMatch(
            pattern_id=1, start=10.0, end=40.0, confidence=0.9,
            sponsor='SpanShoe', match_type='content', category='cross_promo',
        )
        det._add_pattern_match(match, 'text_pattern', 'content', all_ads, regions, episode_id='ep1')
        merged = det._merge_detection_results(all_ads, segments=[])
        assert merged[0]['category'] == 'cross_promo'

    def test_add_pattern_match_carries_fingerprint_match_category(self):
        det = AdDetector(api_key='test-key')
        all_ads, regions = [], []
        match = FingerprintMatch(
            pattern_id=2, start=5.0, end=20.0, confidence=0.95,
            sponsor='FPCo', category='self_promo',
        )
        det._add_pattern_match(match, 'fingerprint', 'fingerprint', all_ads, regions, episode_id='ep1')
        merged = det._merge_detection_results(all_ads, segments=[])
        assert merged[0]['category'] == 'self_promo'

    def test_legacy_none_category_still_falls_back_to_sponsor_at_merge_seam(self):
        """A match whose pattern genuinely has no category (category=None,
        e.g. a hand-built match bypassing the normalized DB read) still
        gets the pre-existing 'sponsor' default at the merge seam -- the
        fix does not change behavior for a pattern with no category."""
        det = AdDetector(api_key='test-key')
        all_ads, regions = [], []
        match = TextMatch(
            pattern_id=1, start=10.0, end=40.0, confidence=0.9,
            sponsor='OldCo', match_type='content', category=None,
        )
        det._add_pattern_match(match, 'text_pattern', 'content', all_ads, regions, episode_id='ep1')
        merged = det._merge_detection_results(all_ads, segments=[])
        assert merged[0]['category'] == 'sponsor'

    def test_fingerprint_pattern_linkage_carries_category(self, temp_db):
        """Fingerprints DO have pattern linkage: audio_fingerprints.pattern_id
        references ad_patterns.id, and get_all_fingerprints_with_sponsors
        already LEFT JOINs ad_patterns for the sponsor name -- confirmed by
        reading src/database/fingerprints.py before writing this test. Not a
        NEEDS_CONTEXT case: category rides the same existing join."""
        pid = temp_db.create_ad_pattern(
            scope='global', text_template='x' * 60, category='cross_promo',
        )
        temp_db.create_audio_fingerprint(pattern_id=pid, fingerprint=b'AQAA', duration=12.0)

        rows = temp_db.get_all_fingerprints_with_sponsors()
        assert len(rows) == 1
        assert rows[0]['category'] == 'cross_promo'

        fp = AudioFingerprinter.__new__(AudioFingerprinter)
        fp.db = temp_db
        loaded = fp._load_fingerprints_from_db()
        assert loaded == [(pid, 'AQAA', 12.0, None, 'cross_promo')]

    def test_fingerprint_pattern_with_no_category_defaults_sponsor(self, temp_db):
        pid = temp_db.create_ad_pattern(scope='global', text_template='x' * 60)
        temp_db.create_audio_fingerprint(pattern_id=pid, fingerprint=b'AQAA', duration=12.0)

        fp = AudioFingerprinter.__new__(AudioFingerprinter)
        fp.db = temp_db
        loaded = fp._load_fingerprints_from_db()
        assert loaded == [(pid, 'AQAA', 12.0, None, 'sponsor')]


class TestKeepPartitionFromCategorizedDetection:
    """End-to-end assertion the coordinator asked for: a detection carrying
    the stored category resolves through the feed's segment-category
    settings to keep_ads via _partition_keep_ads directly (Task 4's
    partition, reused rather than reimplemented here)."""

    def test_cross_promo_detection_on_keep_feed_partitions_to_keep(self):
        ad = {'start': 0.0, 'end': 30.0, 'category': 'cross_promo',
              'confidence': 0.9, 'detection_stage': 'text_pattern'}
        actions_map = {'cross_promo': 'keep'}
        keep_ads, remove_ads = processing._partition_keep_ads([ad], actions_map)
        assert keep_ads == [ad]
        assert remove_ads == []
        assert ad['action_applied'] == 'keep'
        assert ad['was_cut'] is False

    def test_sponsor_category_detection_on_same_feed_is_unaffected(self):
        """Regression guard: a plain sponsor-category detection on the same
        keep-cross_promo feed still cuts normally -- the keep resolution is
        per-category, not global."""
        ad = {'start': 0.0, 'end': 30.0, 'category': 'sponsor',
              'confidence': 0.9, 'detection_stage': 'text_pattern'}
        actions_map = {'cross_promo': 'keep'}
        keep_ads, remove_ads = processing._partition_keep_ads([ad], actions_map)
        assert keep_ads == []
        assert remove_ads == [ad]
