"""Closing holds when hold_for_review is off: the pure rule, validator rule 1b, recut and the detections view."""
import json

import pytest

from tests.app_bootstrap import bootstrap

_test_data_dir = bootstrap('hold_for_review_close_test_')

from ad_validator import AdValidator, Decision  # noqa: E402
from config import (  # noqa: E402
    HOLD_REASON_CUE_LOW_CONFIDENCE, HOLD_REASON_CUE_TEMPLATE_UNPROVEN, HOLD_REASON_MAX_DURATION,
    HOLD_REASON_NO_CUE, HOLD_REASON_REVIEWER_CONTRADICTION, HOLD_REASON_UNCORROBORATED_TAIL,
    count_pending_review, is_pending_review, resolve_hold_for_review,
)
from detection_review import flatten_detections  # noqa: E402
from main_app import processing  # noqa: E402
from main_app.pipeline import recut  # noqa: E402
from utils.markers import close_holds, finalize_holds, reviewer_hold_stands  # noqa: E402


def _held(start=100.0, end=160.0, reason=HOLD_REASON_UNCORROBORATED_TAIL, **extra):
    return {'start': start, 'end': end, 'confidence': 0.9, 'detection_stage': 'claude',
            'held_for_review': True, 'hold_reason': reason, 'was_cut': False,
            'validation': {'decision': 'REVIEW'}, **extra}


class TestCloseHolds:
    def test_a_normal_hold_closes_with_the_contract_fields(self):
        marker = _held()
        assert close_holds([marker]) == 1
        assert marker['held_for_review'] is False
        assert 'hold_reason' not in marker
        assert marker['was_cut'] is False
        assert marker['closed_hold_reason'] == HOLD_REASON_UNCORROBORATED_TAIL
        assert marker['closed_hold_reason'] == HOLD_REASON_UNCORROBORATED_TAIL
        assert not is_pending_review(marker)

    def test_pending_count_drops(self):
        markers = [_held(), _held(200.0, 260.0), {'start': 0.0, 'end': 30.0, 'was_cut': True}]
        assert count_pending_review(markers) == 2
        assert close_holds(markers) == 2
        assert count_pending_review(markers) == 0

    def test_cut_and_unheld_markers_are_untouched(self):
        cut = {'start': 0.0, 'end': 30.0, 'was_cut': True}
        rejected = {'start': 50.0, 'end': 80.0, 'was_cut': False}
        assert close_holds([cut, rejected]) == 0
        assert 'closed_hold_reason' not in cut and 'closed_hold_reason' not in rejected

    def test_an_approved_hold_stays_pending(self):
        marker = _held(approved=True)
        assert close_holds([marker]) == 0
        assert is_pending_review(marker)

    @pytest.mark.parametrize('reason', [
        HOLD_REASON_NO_CUE, HOLD_REASON_CUE_TEMPLATE_UNPROVEN, HOLD_REASON_CUE_LOW_CONFIDENCE])
    def test_cue_gating_holds_stay_pending(self, reason):
        marker = _held(reason=reason)
        assert close_holds([marker]) == 0
        assert is_pending_review(marker)

    def test_a_feed_cap_hold_stays_pending_but_an_over_length_hold_closes(self):
        capped = _held(reason=HOLD_REASON_MAX_DURATION, feed_cap_hold=True)
        over_length = _held(200.0, 1200.0, reason=HOLD_REASON_MAX_DURATION)
        assert close_holds([capped, over_length]) == 1
        assert is_pending_review(capped)
        assert over_length['closed_hold_reason'] == HOLD_REASON_MAX_DURATION

    def test_a_confirm_on_file_keeps_the_hold(self):
        marker = _held()
        confirms = [{'start': 100.0, 'end': 160.0, 'auto_filed': True}]
        assert close_holds([marker], ([], confirms)) == 0
        assert is_pending_review(marker)

    def test_an_unrelated_confirm_does_not_keep_it(self):
        marker = _held()
        assert close_holds([marker], ([], [{'start': 500.0, 'end': 560.0}])) == 1

    def test_an_overlapping_user_rejection_keeps_the_hold(self):
        marker = _held()
        assert close_holds([marker], ([{'start': 95.0, 'end': 165.0}], [])) == 0

    def test_closing_twice_is_a_no_op(self):
        markers = [_held()]
        assert close_holds(markers) == 1
        assert close_holds(markers) == 0


class TestValidatorOverLength:
    """Rule 1b: with holds off, a confident LLM over-length ad is cut instead of held."""

    @staticmethod
    def _validator(monkeypatch, hold_for_review, **kwargs):
        # Lower the adjusted confidence so the existing 0.90 override does not already accept it.
        monkeypatch.setattr(AdValidator, '_verify_in_transcript', lambda self, ad, conf, flags, hits: 0.5)
        return AdValidator(3600.0, [], hold_for_review=hold_for_review, **kwargs)

    @staticmethod
    def _ad(confidence=0.96, start=100.0, end=450.0, stage='claude'):
        return {'start': start, 'end': end, 'confidence': confidence, 'detection_stage': stage,
                'reason': 'Extended sponsor read'}

    def _validate(self, monkeypatch, ad, hold_for_review, **kwargs):
        return self._validator(monkeypatch, hold_for_review, **kwargs).validate([ad]).ads[0]

    def test_holds_on_keeps_todays_hold(self, monkeypatch):
        ad = self._validate(monkeypatch, self._ad(), True)
        assert ad['validation']['decision'] == Decision.REVIEW.value
        assert ad['hold_reason'] == HOLD_REASON_MAX_DURATION

    def test_default_constructor_argument_holds(self, monkeypatch):
        monkeypatch.setattr(AdValidator, '_verify_in_transcript', lambda self, ad, conf, flags, hits: 0.5)
        ad = AdValidator(3600.0, []).validate([self._ad()]).ads[0]
        assert ad['hold_reason'] == HOLD_REASON_MAX_DURATION

    def test_holds_off_accepts_a_confident_llm_ad(self, monkeypatch):
        ad = self._validate(monkeypatch, self._ad(), False)
        assert ad['validation']['decision'] == Decision.ACCEPT.value
        assert not ad.get('held_for_review')

    def test_verification_stage_counts_as_llm(self, monkeypatch):
        ad = self._validate(monkeypatch, self._ad(stage='verification'), False)
        assert ad['validation']['decision'] == Decision.ACCEPT.value

    def test_exactly_0_95_is_not_enough(self, monkeypatch):
        ad = self._validate(monkeypatch, self._ad(confidence=0.95), False)
        assert ad['validation']['decision'] == Decision.REVIEW.value
        assert ad['hold_reason'] == HOLD_REASON_MAX_DURATION

    def test_a_non_llm_stage_is_held(self, monkeypatch):
        ad = self._validate(monkeypatch, self._ad(stage='fingerprint'), False)
        assert ad['validation']['decision'] == Decision.REVIEW.value

    def test_over_the_confirmed_cap_is_held(self, monkeypatch):
        ad = self._validate(monkeypatch, self._ad(end=1100.0), False)
        assert ad['validation']['decision'] == Decision.REVIEW.value
        assert ad['hold_reason'] == HOLD_REASON_MAX_DURATION
        assert not ad.get('feed_cap_hold')

    def test_a_per_feed_confirmed_cap_is_honoured(self, monkeypatch):
        ad = self._validate(monkeypatch, self._ad(), False, max_ad_duration_confirmed=320.0)
        assert ad['validation']['decision'] == Decision.REVIEW.value

    def test_a_feed_cap_hold_is_flagged_and_the_flag_is_rederived(self, monkeypatch):
        validator = AdValidator(3600.0, [], max_ad_duration_override=240.0, hold_for_review=False)
        ad = validator.validate([{'start': 100.0, 'end': 400.0, 'confidence': 0.9,
                                  'reason': 'Sponsor read'}]).ads[0]
        assert ad['hold_reason'] == HOLD_REASON_MAX_DURATION
        assert ad['feed_cap_hold'] is True
        assert close_holds([ad]) == 0
        ad['closed_hold_reason'] = 'x'
        rerun = AdValidator(3600.0, []).validate([ad]).ads[0]
        assert 'closed_hold_reason' not in rerun


STORED_SEGMENTS = [{'start': float(t), 'end': float(t + 5), 'text': 'this episode is sponsored by acme'}
                   for t in range(0, 400, 5)]


def _stored_episode(monkeypatch, markers, confirmed=(), hold_for_review=False):
    monkeypatch.setattr(processing.db, 'get_episode',
                        lambda s, e: {'ad_markers_json': json.dumps(markers), 'podcast_title': 'Example'})
    monkeypatch.setattr(processing.db, 'get_podcast_row', lambda slug: {'id': 42, 'slug': slug, 'hold_for_review': int(hold_for_review)})
    monkeypatch.setattr(processing.db, 'get_podcast_by_slug', lambda slug: {'id': 42, 'slug': slug})
    monkeypatch.setattr(processing.db, 'get_podcast_cue_settings_overrides', lambda pid: {})
    monkeypatch.setattr(processing.db, 'get_episode_corrections', lambda pid, eid: [])
    monkeypatch.setattr(processing.db, 'get_confirmed_corrections', lambda pid, eid: list(confirmed))
    monkeypatch.setattr(processing.db, 'get_false_positive_corrections', lambda pid, eid: [])
    monkeypatch.setattr(processing.db, 'get_episode_audio_analysis', lambda s, e: None)
    monkeypatch.setattr(processing.db, 'get_episode_upstream_transcript', lambda s, e: {})
    monkeypatch.setattr(recut.keep_rules, '_load_stored_dai_differential', lambda s, e: None)


def _build(corrections_confirmed=()):
    return recut._build_recut_ad_list(
        'example-podcast', 'a1b2c3d4e5f6', STORED_SEGMENTS, 600.0, '', 0.80,
        corrections=([], list(corrections_confirmed)))


@pytest.fixture(autouse=True)
def _isolate_db(monkeypatch):
    import database
    monkeypatch.setattr(database.Database, '_instance', None)
    monkeypatch.setenv('DATA_DIR', _test_data_dir)
    yield


def _closed_marker(**extra):
    return {'start': 100.0, 'end': 160.0, 'confidence': 0.9, 'detection_stage': 'claude',
            'sponsor': 'Acme', 'reason': 'Acme sponsor read', 'was_cut': False,
            'closed_hold_reason': HOLD_REASON_UNCORROBORATED_TAIL,
            'validation': {'decision': 'REVIEW', 'flags': []}, **extra}


class TestRecut:
    def test_a_confirm_on_a_closed_marker_cuts_it(self, monkeypatch):
        confirm = {'start': 100.0, 'end': 160.0, 'correction_type': 'confirm'}
        _stored_episode(monkeypatch, [_closed_marker()], confirmed=[confirm])
        ads_to_remove, all_ads, *_ = _build([confirm])
        assert [(a['start'], a['end']) for a in ads_to_remove] == [(100.0, 160.0)]
        assert 'closed_hold_reason' not in all_ads[0]
        assert all_ads[0]['validation']['decision'] == Decision.ACCEPT.value

    def test_a_confirm_cuts_a_closed_reviewer_hold(self, monkeypatch):
        marker = _closed_marker(closed_hold_reason=HOLD_REASON_REVIEWER_CONTRADICTION)
        confirm = {'start': 100.0, 'end': 160.0, 'correction_type': 'confirm'}
        _stored_episode(monkeypatch, [marker], confirmed=[confirm])
        ads_to_remove, *_ = _build([confirm])
        assert [(a['start'], a['end']) for a in ads_to_remove] == [(100.0, 160.0)]

    def test_a_closed_reviewer_hold_is_not_cut_by_a_plain_recut(self, monkeypatch):
        marker = _closed_marker(closed_hold_reason=HOLD_REASON_REVIEWER_CONTRADICTION)
        assert reviewer_hold_stands(marker, [])
        _stored_episode(monkeypatch, [marker])
        ads_to_remove, all_ads, *_ = _build()
        assert ads_to_remove == []
        assert all_ads[0]['held_for_review'] is True
        assert all_ads[0]['hold_reason'] == HOLD_REASON_REVIEWER_CONTRADICTION
        assert close_holds(all_ads) == 1
        assert all_ads[0]['closed_hold_reason'] == HOLD_REASON_REVIEWER_CONTRADICTION

    def test_a_user_confirm_releases_the_stored_closed_hold_check(self):
        marker = _closed_marker(closed_hold_reason=HOLD_REASON_REVIEWER_CONTRADICTION)
        assert not reviewer_hold_stands(marker, [{'start': 100.0, 'end': 160.0}])


PERSISTED_REASONS = [
    ('verification_miss', 'verification_miss'),
    ('differential_uncorroborated', 'dai_differential'),
    ('transcript_differential_unreviewed', 'transcript_differential'),
    ('verification_kept_conflict', 'verification'),
    ('uncorroborated_tail', 'claude'),
    ('estimated_pattern_bounds', 'claude'),
    ('no_splice_evidence', 'claude'),
    ('max_duration', 'claude'),
    ('reviewer_reject_conflict', 'claude'),
]


class TestClosedHoldSurvivesRecut:
    """A closed hold is never cut by a recut unless a person confirmed it."""

    @staticmethod
    def _closed(reason, stage):
        marker = _held(reason=reason, detection_stage=stage, confidence=0.99)
        assert close_holds([marker]) == 1
        return marker

    @pytest.mark.parametrize('reason,stage', PERSISTED_REASONS)
    def test_holds_off_stays_uncut_and_closed_with_the_same_reason(self, monkeypatch, reason, stage):
        _stored_episode(monkeypatch, [self._closed(reason, stage)])
        ads_to_remove, all_ads, *_ = _build()
        assert ads_to_remove == []
        assert finalize_holds(all_ads, ([], []), False) == 1
        assert all_ads[0]['closed_hold_reason'] == reason
        assert not all_ads[0].get('was_cut')

    @pytest.mark.parametrize('reason,stage', PERSISTED_REASONS)
    def test_holds_on_comes_back_pending_with_the_reason(self, monkeypatch, reason, stage):
        _stored_episode(monkeypatch, [self._closed(reason, stage)], hold_for_review=True)
        ads_to_remove, all_ads, *_ = _build()
        assert ads_to_remove == []
        assert is_pending_review(all_ads[0])
        assert all_ads[0]['hold_reason'] == reason
        assert finalize_holds(all_ads, ([], []), True) == 0

    @pytest.mark.parametrize('reason,stage', PERSISTED_REASONS)
    def test_a_confirm_still_cuts_it(self, monkeypatch, reason, stage):
        confirm = {'start': 100.0, 'end': 160.0, 'correction_type': 'confirm'}
        _stored_episode(monkeypatch, [self._closed(reason, stage)], confirmed=[confirm])
        ads_to_remove, *_ = _build([confirm])
        assert [(a['start'], a['end']) for a in ads_to_remove] == [(100.0, 160.0)]


class TestDetectionsView:
    ROW = {'feed_slug': 'example-podcast', 'feed_title': 'Example', 'episode_id': 'a1b2c3d4e5f6',
           'episode_title': 'Ep', 'podcast_id': 42}

    def _items(self, markers, corrections=()):
        row = {**self.ROW, 'ad_markers_json': json.dumps(markers)}
        return flatten_detections([row], list(corrections))

    def test_a_closed_marker_is_rejected_unresolved_and_flagged(self):
        item = self._items([_closed_marker()])[0]
        assert item['status'] == 'rejected'
        assert item['resolution'] == 'unresolved'
        assert item['autoClosed'] is True
        assert item['closedHoldReason'] == HOLD_REASON_UNCORROBORATED_TAIL
        assert item['holdReason'] is None

    def test_an_ordinary_rejected_marker_is_not_flagged(self):
        item = self._items([{'start': 0.0, 'end': 30.0, 'was_cut': False}])[0]
        assert item['autoClosed'] is False
        assert item['closedHoldReason'] is None

    def test_a_closed_marker_is_not_counted_as_dismissed(self):
        from detection_review import summarize_detections
        counts = summarize_detections(self._items([_closed_marker()]))
        assert counts['dismissed'] == 0
        assert counts['rejected'] == 1


class TestResolver:
    class _Db:
        def __init__(self, glob):
            self.glob = glob

        def get_podcast_row(self, slug):
            return {'slug': slug}

        def hold_for_review_upgrade_done(self):
            return True

        def get_setting(self, key):
            return 'true' if self.glob else 'false'

    @pytest.mark.parametrize('glob,override,expected', [
        (False, None, False), (True, None, True), (False, 1, True), (True, 0, False)])
    def test_row_override_beats_global(self, glob, override, expected):
        row = {'slug': 'example-podcast', 'hold_for_review': override}
        assert resolve_hold_for_review(self._Db(glob), row) is expected

    def test_a_slug_is_passed_through(self):
        db = self._Db(True)
        assert resolve_hold_for_review(db, 'example-podcast') is True


class TestRecutFinalSave:
    """_recut_episode closes holds before the final save when the feed holds nothing."""

    @staticmethod
    def _run(hold_for_review, markers, confirmed=()):
        import time
        from contextlib import ExitStack
        from unittest.mock import patch
        from main_app.pipeline import finalize, publish
        from tests.unit.recut_test_utils import stub_recut

        with ExitStack() as stack:
            db, storage, local_ap = stub_recut(
                stack, [], markers, episode_row={'podcast_id': 1, 'processed_version': 0},
                duration=60.0, confirmed=confirmed)
            db.get_podcast_row.return_value = {'id': 1, 'slug': 'example-podcast', 'hold_for_review': int(hold_for_review)}
            stack.enter_context(patch.object(publish, '_generate_assets'))
            finalize_mock = stack.enter_context(patch.object(finalize, '_finalize_episode'))
            stack.enter_context(patch.object(processing.shutil, 'move'))
            local_ap.get_audio_duration.return_value = 60.0
            local_ap.process_episode.return_value = ('/tmp/recut-cut.mp3', [])
            assert recut._recut_episode('example-podcast', 'ep1', 'Episode', 'Podcast', 'desc',
                                        time.time(), cancel_event=None)
            return storage.save_combined_ads.call_args.args[2], finalize_mock.call_args.kwargs

    def test_holds_off_closes_and_the_counts_drop(self):
        saved, finalize_kwargs = self._run(False, [_held(10.0, 40.0)])
        assert saved[0]['closed_hold_reason'] == HOLD_REASON_UNCORROBORATED_TAIL
        assert count_pending_review(saved) == 0
        assert finalize_kwargs['ads_held'] == 0

    def test_holds_on_leaves_the_hold_pending(self):
        saved, finalize_kwargs = self._run(True, [_held(10.0, 40.0)])
        assert is_pending_review(saved[0])
        assert finalize_kwargs['ads_held'] == 1

    def test_holds_off_keeps_a_cue_gated_hold(self):
        saved, finalize_kwargs = self._run(False, [_held(10.0, 40.0, reason=HOLD_REASON_NO_CUE)])
        assert is_pending_review(saved[0])
        assert finalize_kwargs['ads_held'] == 1


class TestRunEnd:
    """process_episode closes holds after pass 2 and the approval step, before it finalizes."""

    @staticmethod
    def _run(hold_for_review, markers, approvals=0, recut_ok=True):
        from contextlib import ExitStack
        from unittest.mock import patch
        from main_app.pipeline import common, cuts, detect, finalize, publish, review, signals, transcribe, verify

        podcast_row = {'id': 1, 'slug': 'example-podcast', 'description': None, 'tags': None,
                       'dai_platform': None, 'passthrough_enabled': None, 'skip_ad_detection': None,
                       'hold_for_review': int(hold_for_review)}
        with ExitStack() as stack:
            p = lambda *a, **k: stack.enter_context(patch.object(*a, **k))  # noqa: E731
            db = p(processing, 'db')
            p(processing, 'status_service')
            storage = p(processing, 'storage')
            audio_processor = p(processing, 'audio_processor')
            p(processing.ad_detector, 'get_model', return_value='test-model')
            p(processing.ad_detector, 'get_verification_model', return_value='test-model')
            p(processing, 'start_episode_token_tracking')
            p(processing, 'get_available_memory_gb', return_value=None)
            p(finalize, 'get_available_memory_gb', return_value=None)
            p(common, 'get_min_cut_confidence', return_value=0.8)
            p(transcribe, '_download_and_transcribe',
              return_value=('/tmp/hold.mp3', [{'start': 0.0, 'end': 5.0, 'text': 'hello'}]))
            p(signals, '_run_differential_fetch', return_value=None)
            p(signals, '_run_audio_analysis', return_value=None)
            p(processing, 'load_positional_prior', return_value=None)
            p(detect, '_detect_ads_first_pass', return_value=([], 0, None))
            p(detect, '_refine_and_validate', return_value=([], markers))
            p(review, '_run_ad_reviewer', side_effect=lambda *a, **k: (a[3], a[4]))
            p(cuts, '_snap_terminal_starts', return_value=[])
            p(cuts, '_complete_cut_tails', return_value=[])
            local_ap_cls = p(processing, 'AudioProcessor')
            p(recut, 'AudioProcessor', new=local_ap_cls)
            p(verify, '_run_verification_pass', return_value=(0, [], [], [], '/tmp/cut.mp3', 0, True, 0))
            p(publish, '_generate_assets')
            finalize_mock = p(finalize, '_finalize_episode')
            p(verify, '_file_corroborated_hold_approvals', return_value=approvals)
            recut_mock = p(recut, '_recut_episode', return_value=recut_ok)
            p(processing.shutil, 'move')
            p(processing.os, 'unlink')
            p(processing.os.path, 'exists', return_value=False)
            db.get_episode.return_value = {'reprocess_requested_at': None}
            db.get_podcast_by_slug.return_value = podcast_row
            db.get_podcast_row.return_value = podcast_row
            db.get_setting.return_value = 'false'
            db.get_all_settings.return_value = {}
            db.get_false_positive_corrections.return_value = []
            db.get_confirmed_corrections.return_value = []
            audio_processor.get_audio_duration.return_value = 100.0
            local_ap = local_ap_cls.return_value
            local_ap.process_episode.return_value = ('/tmp/cut.mp3', [])
            local_ap.get_audio_duration.return_value = 100.0
            storage.get_episode_path.return_value = '/tmp/final.mp3'
            assert processing.process_episode('example-podcast', 'ep1', 'https://example.com/ep1.mp3')
            return storage, finalize_mock, recut_mock

    def test_holds_off_closes_after_the_last_save_and_the_counts_drop(self):
        storage, finalize_mock, _ = self._run(False, [_held(10.0, 40.0)])
        saved = storage.save_combined_ads.call_args.args[2]
        assert saved[0]['closed_hold_reason'] == HOLD_REASON_UNCORROBORATED_TAIL
        assert count_pending_review(saved) == 0
        assert finalize_mock.call_args.kwargs['ads_held'] == 0

    def test_holds_on_is_unchanged(self):
        storage, finalize_mock, _ = self._run(True, [_held(10.0, 40.0)])
        saved = storage.save_combined_ads.call_args.args[2]
        assert is_pending_review(saved[0])
        assert finalize_mock.call_args.kwargs['ads_held'] == 1

    def test_an_approval_recut_owns_the_close(self):
        storage, finalize_mock, recut_mock = self._run(False, [_held(10.0, 40.0)], approvals=1)
        recut_mock.assert_called_once()
        finalize_mock.assert_not_called()
        saved = storage.save_combined_ads.call_args.args[2]
        assert is_pending_review(saved[0])


class TestOverLengthVsFeedCap:
    """The feed's own max ad duration cap still holds a confident over-length ad."""

    @pytest.mark.parametrize('end,lowered', [(350.0, False), (550.0, True)])
    def test_holds_off_still_holds_over_the_feed_cap(self, monkeypatch, end, lowered):
        if lowered:
            monkeypatch.setattr(AdValidator, '_verify_in_transcript', lambda self, ad, conf, flags, hits: 0.5)
        validator = AdValidator(3600.0, [], max_ad_duration_override=200.0, hold_for_review=False)
        ad = validator.validate([{'start': 100.0, 'end': end, 'confidence': 0.97,
                                  'detection_stage': 'claude', 'reason': 'Sponsor read'}]).ads[0]
        assert ad['validation']['decision'] == Decision.REVIEW.value
        assert ad['hold_reason'] == HOLD_REASON_MAX_DURATION
        assert ad['feed_cap_hold'] is True
        assert close_holds([ad]) == 0


class TestFailedInRunRecut:
    def test_a_failed_recut_that_wrote_nothing_still_closes_the_holds(self):
        markers = [_held(10.0, 40.0), _held(50.0, 80.0, reason=HOLD_REASON_NO_CUE)]
        storage, finalize_mock, recut_mock = TestRunEnd._run(False, markers, approvals=1, recut_ok=False)
        recut_mock.assert_called_once()
        saved = storage.save_combined_ads.call_args.args[2]
        assert saved[0]['closed_hold_reason'] == HOLD_REASON_UNCORROBORATED_TAIL
        assert is_pending_review(saved[1])
        assert finalize_mock.call_args.kwargs['ads_held'] == 1

    def test_holds_on_leaves_them_pending(self):
        storage, finalize_mock, _ = TestRunEnd._run(True, [_held(10.0, 40.0)], approvals=1, recut_ok=False)
        assert finalize_mock.call_args.kwargs['ads_held'] == 1
