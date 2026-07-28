"""Learning guards on keep-action markers, and ad_patterns.category.

Covers:
- Corrections guard: a marker resolved to action_applied == 'keep' can
  never create a pattern_correction row (which also seeds cross-episode
  false-positive text, since both flow through the same
  create_pattern_correction call).
- ad_patterns.category: additive NULL column; NULL reads back as 'sponsor';
  the pattern learner stores a marker's category on newly created patterns.
- Community sync: export includes category; import without one defaults to
  'sponsor' via the same NULL read default.
- Kept markers still reach pattern learning: only correction/FP-text
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
from ad_detector import AdDetector
from config import normalize_segment_category  # noqa: E402
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
        be treated as a keep marker: the guard only fires on an actual
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


class TestCommunitySyncReimportPreservesCategory:
    """An old-format re-import payload (no 'category' key) must not NULL
    out a stored category on an existing pattern:
    import_community_pattern's update path only includes 'category' in the
    update kwargs when the payload actually carries the key."""

    def _seed_pattern(self, temp_db, community_id, category='cross_promo'):
        pattern_service = PatternService(temp_db)
        pattern_id = pattern_service.import_community_pattern({
            'community_id': community_id,
            'version': 1,
            'scope': 'global',
            'sponsor': 'ReimportCo',
            'text_template': (
                'This episode is brought to you by ReimportCo. '
                'ReimportCo makes everything better and faster.'
            ),
            'category': category,
        })
        return pattern_service, pattern_id

    def test_reimport_without_category_preserves_stored_category(self, temp_db):
        pattern_service, pattern_id = self._seed_pattern(
            temp_db, 'cid-reimport-no-category')
        assert temp_db.get_ad_pattern_by_id(pattern_id)['category'] == 'cross_promo'

        # Old-format payload: higher version forces the update path, but
        # carries no 'category' key at all.
        returned_id = pattern_service.import_community_pattern({
            'community_id': 'cid-reimport-no-category',
            'version': 2,
            'scope': 'global',
            'sponsor': 'ReimportCo',
            'text_template': (
                'This episode is brought to you by ReimportCo. '
                'ReimportCo makes everything better and faster today.'
            ),
        })

        assert returned_id == pattern_id
        pattern = temp_db.get_ad_pattern_by_id(pattern_id)
        assert pattern['category'] == 'cross_promo'
        assert pattern['version'] == 2

    def test_reimport_with_category_still_updates_it(self, temp_db):
        pattern_service, pattern_id = self._seed_pattern(
            temp_db, 'cid-reimport-with-category')
        assert temp_db.get_ad_pattern_by_id(pattern_id)['category'] == 'cross_promo'

        returned_id = pattern_service.import_community_pattern({
            'community_id': 'cid-reimport-with-category',
            'version': 2,
            'scope': 'global',
            'sponsor': 'ReimportCo',
            'text_template': (
                'This episode is brought to you by ReimportCo. '
                'ReimportCo makes everything better and faster today.'
            ),
            'category': 'self_promo',
        })

        assert returned_id == pattern_id
        pattern = temp_db.get_ad_pattern_by_id(pattern_id)
        assert pattern['category'] == 'self_promo'
        assert pattern['version'] == 2


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
        """Sanity: an ordinary uncut marker (no action_applied at all, e.g.
        a rejected correction) must not slip through the relaxed check."""
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
# Fingerprint/text-pattern re-matches of an already-learned pattern dropped
# the pattern's stored category: a pattern learned from a kept cross_promo
# marker re-matched with no category, fell through to the 'sponsor'
# default, and got cut on a feed that keeps cross_promo. Fixed by threading
# category through AdPattern/TextMatch, FingerprintMatch, and
# _add_pattern_match.

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
        """A TextMatch's category survives _add_pattern_match (the
        detection dict) and _merge_detection_results (the merge seam that
        otherwise stamps 'sponsor')."""
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

    def test_legacy_none_category_stays_uncategorized_at_merge_seam(self):
        """A match whose pattern genuinely has no category (category=None)
        stays uncategorized at the merge seam rather than being relabelled
        'sponsor', which would make it indistinguishable from a pattern that
        really is a sponsor read. Action resolution still treats it as
        sponsor, so cutting is unchanged."""
        det = AdDetector(api_key='test-key')
        all_ads, regions = [], []
        match = TextMatch(
            pattern_id=1, start=10.0, end=40.0, confidence=0.9,
            sponsor='OldCo', match_type='content', category=None,
        )
        det._add_pattern_match(match, 'text_pattern', 'content', all_ads, regions, episode_id='ep1')
        merged = det._merge_detection_results(all_ads, segments=[])
        assert 'category' not in merged[0]
        assert normalize_segment_category(merged[0].get('category')) == 'sponsor'

    def test_fingerprint_pattern_linkage_carries_category(self, temp_db):
        """audio_fingerprints.pattern_id references ad_patterns.id, and
        get_all_fingerprints_with_sponsors already joins ad_patterns for
        the sponsor name, so category rides the same existing join."""
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
    """A detection carrying the stored category resolves through the
    feed's segment-category settings to keep_ads via _partition_keep_ads
    directly (reused rather than reimplemented here)."""

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
        keep-cross_promo feed still cuts normally: the keep resolution is
        per-category, not global."""
        ad = {'start': 0.0, 'end': 30.0, 'category': 'sponsor',
              'confidence': 0.9, 'detection_stage': 'text_pattern'}
        actions_map = {'cross_promo': 'keep'}
        keep_ads, remove_ads = processing._partition_keep_ads([ad], actions_map)
        assert keep_ads == []
        assert remove_ads == [ad]


# ========== 7. category exposed on every pattern-returning endpoint ==========

class TestPatternCategoryOnEndpoints:
    """The API layer never re-derives category; it just has to read it
    through on every endpoint that returns a pattern (or a slice of one)."""

    def test_list_patterns_includes_category(self, client, temp_db):
        temp_db.create_ad_pattern(
            scope='global', text_template='x' * 60, category='cross_promo',
        )
        with patch('api.patterns.get_database', return_value=temp_db):
            resp = client.get('/api/v1/patterns')
        assert resp.status_code == 200, resp.data
        patterns = json.loads(resp.data)['patterns']
        assert len(patterns) == 1
        assert patterns[0]['category'] == 'cross_promo'

    def test_get_pattern_detail_includes_category(self, client, temp_db):
        pid = temp_db.create_ad_pattern(
            scope='global', text_template='x' * 60, category='self_promo',
        )
        with patch('api.patterns.get_database', return_value=temp_db):
            resp = client.get(f'/api/v1/patterns/{pid}')
        assert resp.status_code == 200, resp.data
        assert json.loads(resp.data)['category'] == 'self_promo'

    def test_get_pattern_detail_null_category_defaults_sponsor(self, client, temp_db):
        pid = temp_db.create_ad_pattern(scope='global', text_template='x' * 60)
        with patch('api.patterns.get_database', return_value=temp_db):
            resp = client.get(f'/api/v1/patterns/{pid}')
        assert resp.status_code == 200, resp.data
        assert json.loads(resp.data)['category'] == 'sponsor'

    def test_export_patterns_includes_category(self, client, temp_db):
        temp_db.create_ad_pattern(
            scope='global', text_template='x' * 60, category='interaction',
        )
        with patch('api.patterns.get_database', return_value=temp_db):
            resp = client.get('/api/v1/patterns/export')
        assert resp.status_code == 200, resp.data
        patterns = json.loads(resp.data)['patterns']
        assert len(patterns) == 1
        assert patterns[0]['category'] == 'interaction'

    def test_export_import_round_trip_preserves_category(self, client, temp_db):
        """A full-DB backup export/import round trip must not lose category
        (repo principle: never lose data during DB changes)."""
        temp_db.create_ad_pattern(
            scope='global', text_template='y' * 60, category='outro',
        )
        with patch('api.patterns.get_database', return_value=temp_db):
            exported = json.loads(client.get('/api/v1/patterns/export').data)
            resp = client.post(
                '/api/v1/patterns/import',
                data=json.dumps({'patterns': exported['patterns'], 'mode': 'replace'}),
                content_type='application/json',
            )
        assert resp.status_code == 200, resp.data
        rows = temp_db.get_ad_patterns(active_only=False)
        assert len(rows) == 1
        assert rows[0]['category'] == 'outro'

    def test_merge_suggestions_members_include_category(self, client, temp_db):
        import pattern_clusters
        pattern_clusters._CACHE.clear()
        sponsor_id = get_or_create_known_sponsor(temp_db, 'ClusterCo')
        read_a = ('ClusterCo makes great widgets for busy people. Visit '
                  'clusterco dot com slash deal for a discount.')
        read_b = ('ClusterCo makes great widgets for busy people. Visit '
                  'clusterco dot com slash deal for a discount today.')
        temp_db.create_ad_pattern(
            scope='global', text_template=read_a, sponsor_id=sponsor_id,
            category='recap',
        )
        temp_db.create_ad_pattern(
            scope='global', text_template=read_b, sponsor_id=sponsor_id,
            category='recap',
        )
        with patch('api.patterns.get_database', return_value=temp_db):
            resp = client.get('/api/v1/patterns/merge-suggestions')
        assert resp.status_code == 200, resp.data
        suggestions = json.loads(resp.data)['suggestions']
        assert len(suggestions) == 1
        members = suggestions[0]['members']
        assert len(members) == 2
        assert all(m['category'] == 'recap' for m in members)
