"""Tests for the segment-category keep-action bypass in pass 1.

A marker whose resolved segment-category action is 'keep' is pulled out
before the validator and reviewer ever see it: persisted with
was_cut=False/action_applied='keep', but never entering the validator
input, the reviewer input, the cut list, or held-for-review routing.
Markers resolving to 'remove' (the default) flow through byte-identical
to before.
"""
import logging
import os
import sys
import tempfile
import types
from contextlib import ExitStack

import pytest

os.environ.setdefault('MINUSPOD_DATA_DIR', tempfile.mkdtemp(prefix='keepbypass_test_'))
os.environ.setdefault('SECRET_KEY', 'test-secret')

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))

from unittest.mock import patch

import main_app.processing as processing
from api.patterns import _matches_held_marker
from config import count_pending_review, HOLD_REASON_VERIFICATION_KEPT_CONFLICT

SEGMENTS = [{'start': 0.0, 'end': 5.0, 'text': 'hello'},
            {'start': 5.0, 'end': 10.0, 'text': 'world'}]


def _sponsor_ad():
    return {'start': 10.0, 'end': 20.0, 'category': 'sponsor',
           'confidence': 0.95, 'detection_stage': 'llm'}


def _cross_promo_ad():
    return {'start': 30.0, 'end': 40.0, 'category': 'cross_promo',
           'confidence': 0.95, 'detection_stage': 'llm'}


def _run_pipeline(first_pass_ads, segment_actions, late_synthesized_ad=None,
                  real_sweeps=False, audio_analysis_result=None, segments=None):
    """Drive process_episode's full pass-1 flow with every stage but the
    partition itself mocked out. Returns the recorded mocks for inspection.

    ``late_synthesized_ad``: a marker added inside _refine_and_validate
    after the keep partition already ran, appended to the mocked stage's
    return value, not its input. ``real_sweeps=True`` leaves
    _snap_terminal_starts/_complete_cut_tails unpatched, to prove the late
    keep-partition wiring against the real sweep functions.
    ``audio_analysis_result`` feeds the mocked _run_audio_analysis return
    value; ``segments`` overrides the module-level SEGMENTS fixture.
    """
    podcast_row = {'id': 1, 'slug': 'keep-feed', 'description': None,
                   'tags': None, 'dai_platform': None,
                   'passthrough_enabled': None, 'skip_ad_detection': None,
                   'detection_mode': None}
    segments = SEGMENTS if segments is None else segments

    def _fake_refine_and_validate(slug, episode_id, all_ads, *a, **k):
        for ad in all_ads:
            if segment_actions.get(ad.get('category')) == 'keep':
                raise AssertionError(
                    'validator was called with a keep-action marker')
        for ad in all_ads:
            ad['was_cut'] = True
        result = list(all_ads)
        if late_synthesized_ad is not None:
            late_synthesized_ad['was_cut'] = True
            result = result + [late_synthesized_ad]
        return result, result

    def _fake_run_ad_reviewer(slug, episode_id, podcast_id, ads_to_remove,
                              all_ads_with_validation, *a, **k):
        return ads_to_remove, all_ads_with_validation

    def _pass_through_ads(slug, episode_id, ads_to_remove, *a, **k):
        return ads_to_remove

    with ExitStack() as stack:
        p = lambda *a, **k: stack.enter_context(patch.object(*a, **k))
        db = p(processing, 'db')
        p(processing, 'status_service')
        storage = p(processing, 'storage')
        audio_processor = p(processing, 'audio_processor')
        p(processing.ad_detector, 'get_model', return_value='test-model')
        p(processing.ad_detector, 'get_verification_model', return_value='test-model')
        p(processing, 'start_episode_token_tracking')
        p(processing, 'get_available_memory_gb', return_value=None)
        p(processing, 'get_min_cut_confidence', return_value=0.8)
        p(processing, '_download_and_transcribe',
          return_value=('/tmp/keep.mp3', segments))
        p(processing, '_run_differential_fetch', return_value=None)
        p(processing, '_run_audio_analysis', return_value=audio_analysis_result)
        p(processing, 'load_positional_prior', return_value=None)
        detect = p(processing, '_detect_ads_first_pass',
                  return_value=(first_pass_ads, len(first_pass_ads), {}))
        refine = p(processing, '_refine_and_validate',
                  side_effect=_fake_refine_and_validate)
        reviewer = p(processing, '_run_ad_reviewer',
                    side_effect=_fake_run_ad_reviewer)
        if not real_sweeps:
            p(processing, '_snap_terminal_starts', side_effect=_pass_through_ads)
            p(processing, '_complete_cut_tails', side_effect=_pass_through_ads)
        local_ap_cls = p(processing, 'AudioProcessor')
        p(processing, '_run_verification_pass',
          return_value=(0, [], [], [], '/tmp/cut.mp3', 0, True, 0))
        generate_assets = p(processing, '_generate_assets')
        finalize = p(processing, '_finalize_episode')
        p(processing.shutil, 'move')
        p(processing.os, 'unlink')
        p(processing.os.path, 'exists', return_value=False)

        db.get_episode.return_value = {}
        db.get_podcast_by_slug.return_value = podcast_row
        db.get_setting.return_value = 'false'
        db.get_setting_float.side_effect = lambda key, default=None: default
        db.get_all_settings.return_value = {}
        db.resolve_segment_actions.return_value = segment_actions
        audio_processor.get_audio_duration.return_value = 100.0
        local_ap = local_ap_cls.return_value
        local_ap.process_episode.side_effect = (
            lambda audio_path, ads_to_remove: ('/tmp/cut.mp3', list(ads_to_remove)))
        local_ap.get_audio_duration.return_value = 100.0
        storage.get_episode_path.return_value = '/tmp/final.mp3'

        result = processing.process_episode(
            'keep-feed', 'ep1', 'https://example.com/ep1.mp3')

    return {'result': result, 'db': db, 'storage': storage,
            'detect': detect, 'refine': refine, 'reviewer': reviewer,
            'generate_assets': generate_assets, 'finalize': finalize,
            'local_ap': local_ap}


class TestKeepBypass:
    def test_keep_marker_bypasses_validator_and_reviewer(self):
        sponsor = _sponsor_ad()
        cross_promo = _cross_promo_ad()
        segment_actions = {'sponsor': 'remove', 'cross_promo': 'keep',
                           'self_promo': 'remove', 'interaction': 'remove',
                           'intro': 'remove', 'outro': 'remove', 'recap': 'remove'}

        m = _run_pipeline([sponsor, cross_promo], segment_actions)

        assert m['result'] is True
        m['db'].resolve_segment_actions.assert_called_once_with(
            'keep-feed', podcast=m['db'].get_podcast_by_slug.return_value)

        refine_all_ads = m['refine'].call_args.args[2]
        assert refine_all_ads == [sponsor]
        reviewer_all_ads = m['reviewer'].call_args.args[4]
        assert cross_promo not in reviewer_all_ads

        saved = m['storage'].save_combined_ads.call_args.args[2]
        by_span = {(a['start'], a['end']): a for a in saved}
        keep_marker = by_span[(cross_promo['start'], cross_promo['end'])]
        assert keep_marker['was_cut'] is False
        assert keep_marker['action_applied'] == 'keep'
        sponsor_marker = by_span[(sponsor['start'], sponsor['end'])]
        assert sponsor_marker['was_cut'] is True
        assert sponsor_marker['action_applied'] == 'remove'

    def test_all_remove_is_byte_identical(self):
        sponsor = _sponsor_ad()
        cross_promo = _cross_promo_ad()
        segment_actions = {'sponsor': 'remove', 'cross_promo': 'remove',
                           'self_promo': 'remove', 'interaction': 'remove',
                           'intro': 'remove', 'outro': 'remove', 'recap': 'remove'}
        first_pass_ads = [sponsor, cross_promo]

        m = _run_pipeline(first_pass_ads, segment_actions)

        assert m['result'] is True
        refine_all_ads = m['refine'].call_args.args[2]
        assert refine_all_ads == first_pass_ads
        assert [id(a) for a in refine_all_ads] == [id(a) for a in first_pass_ads]

        # No keep action in the map, so the cut partition stamps every cut
        # marker action_applied='remove' and beep stays False for all of
        # them: the audio path is byte-identical.
        saved = m['storage'].save_combined_ads.call_args.args[2]
        assert {(a['start'], a['end']): a['action_applied'] for a in saved} == {
            (sponsor['start'], sponsor['end']): 'remove',
            (cross_promo['start'], cross_promo['end']): 'remove',
        }
        audio_segments = m['local_ap'].process_episode.call_args.args[1]
        assert all(s['beep'] is False for s in audio_segments)


class TestKeepMarkersBlockTerminalSnap:
    """Regression: _snap_terminal_starts must not treat a kept marker as
    safe coverage. Runs the real (unpatched) sweep functions, not mocks."""

    def test_terminal_snap_does_not_sweep_across_kept_marker(self):
        # The digital-silence event at 72.0 sits inside the 30s scan-back
        # window: if the kept marker were misread as safe coverage, the
        # sweep would pull the ad's start back to 72.0, eating part of
        # the kept recap into the cut.
        segments = [{'start': 70.0, 'end': 85.0,
                    'text': 'and that wraps up this segment for today'}]
        kept_marker = {'start': 70.0, 'end': 85.0, 'category': 'recap',
                       'action_applied': 'keep', 'was_cut': False,
                       'confidence': 0.9, 'detection_stage': 'llm'}
        terminal_ad = {'start': 90.0, 'end': 99.0, 'confidence': 0.9,
                       'reason': 'terminal block', 'detection_stage': 'text_pattern',
                       'was_cut': True}
        all_ads_with_validation = [kept_marker, terminal_ad]
        ads_to_remove = [terminal_ad]
        splice_evidence = {'events': [
            {'time': 72.0, 'end_time': 73.4, 'type': 'digital_silence',
             'depth_dbfs': -90.0, 'duration_s': 1.4, 'loudness_step_lu': None,
             'centroid_step_hz': None, 'flatness_step': None},
        ]}
        audio_analysis_result = types.SimpleNamespace(splice_evidence=splice_evidence)

        with patch.object(processing, 'db') as db, \
                patch.object(processing, 'storage'):
            db.get_setting_float.return_value = 30.0
            result = processing._snap_terminal_starts(
                'keep-feed', 'ep1', ads_to_remove, all_ads_with_validation,
                segments, audio_analysis_result, 100.0)

        assert result[0]['start'] == 90.0
        assert 'terminal_snap' not in terminal_ad
        # The kept marker itself must never be touched by the sweep.
        assert kept_marker['start'] == 70.0
        assert kept_marker['action_applied'] == 'keep'

    def test_tail_completion_clamp_still_stops_at_kept_marker(self):
        # _complete_cut_tails' next_start clamp treats every marker in
        # all_ads_with_validation as a hard stop, kept or not. Promo-phrase
        # segments after the cut would otherwise extend its end to 45.0;
        # the kept marker's start at 35.0 must cap it there instead.
        segments = [
            {'start': 20.0, 'end': 25.0, 'text': 'use promo code SAVE10 today'},
            {'start': 25.0, 'end': 45.0,
             'text': 'use promo code SAVE10 again and again'},
        ]
        terminal_ad = {'start': 10.0, 'end': 20.0, 'reason': 'Acme sponsor read',
                       'detection_stage': 'text_pattern', 'confidence': 0.9,
                       'was_cut': True}
        kept_marker = {'start': 35.0, 'end': 48.0, 'category': 'outro',
                       'action_applied': 'keep', 'was_cut': False,
                       'confidence': 0.9, 'detection_stage': 'llm'}
        all_ads_with_validation = [terminal_ad, kept_marker]
        ads_to_remove = [terminal_ad]

        with patch.object(processing, 'db') as db, \
                patch.object(processing, 'storage'):
            db.get_setting.return_value = 'true'
            result = processing._complete_cut_tails(
                'keep-feed', 'ep1', ads_to_remove, all_ads_with_validation,
                segments)

        assert result[0]['end'] == 35.0
        assert terminal_ad['end'] == 35.0
        assert terminal_ad.get('tail_completed') is True
        # The kept marker itself must never be touched.
        assert kept_marker['start'] == 35.0
        assert kept_marker['end'] == 48.0
        assert kept_marker['action_applied'] == 'keep'


class TestLateKeepSafetyNet:
    """A marker synthesized after the keep partition already ran (heuristic
    pre/post-roll, VAD-gap) never had a chance to be pulled out even when
    its category resolves to 'keep'. _apply_late_keep_safety_net is the
    last chance, right before the cut list reaches the audio processor."""

    def test_drops_synthesized_marker_with_keep_resolved_category(self, caplog):
        actions_map = {'sponsor': 'keep', 'cross_promo': 'remove',
                       'self_promo': 'remove', 'interaction': 'remove',
                       'intro': 'remove', 'outro': 'remove', 'recap': 'remove'}
        # No 'category' key, like a marker added after _partition_keep_ads
        # already ran: normalizes to 'sponsor', which resolves to 'keep'.
        synthesized = {'start': 90.0, 'end': 99.0, 'was_cut': True,
                       'detection_stage': 'post_roll'}
        real_cut = {'start': 10.0, 'end': 20.0, 'category': 'cross_promo',
                    'was_cut': True}
        ads_to_remove = [real_cut, synthesized]
        all_ads_with_validation = [real_cut, synthesized]

        with caplog.at_level(logging.DEBUG, logger='podcast.audio'):
            result = processing._apply_late_keep_safety_net(
                ads_to_remove, all_ads_with_validation, actions_map)

        assert result == [real_cut]
        assert synthesized['was_cut'] is False
        assert synthesized['action_applied'] == 'keep'
        # The unrelated cut-list marker is untouched.
        assert real_cut['was_cut'] is True
        assert 'action_applied' not in real_cut
        assert any('Late keep safety net' in r.message for r in caplog.records)

    def test_stamps_a_different_master_object_too(self):
        # Reviewer/sweep adjustments rebuild dicts, so the master entry in
        # all_ads_with_validation is not always the same object as the
        # ads_to_remove entry; _find_master matches by (start, end) too.
        actions_map = {'sponsor': 'keep', 'cross_promo': 'remove',
                       'self_promo': 'remove', 'interaction': 'remove',
                       'intro': 'remove', 'outro': 'remove', 'recap': 'remove'}
        synthesized = {'start': 90.0, 'end': 99.0, 'was_cut': True}
        master_twin = {'start': 90.0, 'end': 99.0, 'was_cut': True}

        result = processing._apply_late_keep_safety_net(
            [synthesized], [master_twin], actions_map)

        assert result == []
        assert master_twin['was_cut'] is False
        assert master_twin['action_applied'] == 'keep'

    def test_no_op_when_no_category_resolves_to_keep(self):
        actions_map = {'sponsor': 'remove', 'cross_promo': 'remove',
                       'self_promo': 'remove', 'interaction': 'remove',
                       'intro': 'remove', 'outro': 'remove', 'recap': 'remove'}
        ads_to_remove = [{'start': 10.0, 'end': 20.0}]

        result = processing._apply_late_keep_safety_net(
            ads_to_remove, ads_to_remove, actions_map)

        assert result is ads_to_remove
        assert 'action_applied' not in ads_to_remove[0]


class TestLateKeepPartitionBeforeSweeps:
    """A marker synthesized inside _refine_and_validate, whose category
    resolves to 'keep', must be pulled out right after _refine_and_validate
    returns, before the reviewer's resurrection pool and the sweeps see it,
    not merely at the final _apply_late_keep_safety_net backstop. Drives
    the real, unpatched functions through process_episode (real_sweeps=True);
    only db/storage and the detection/reviewer/verification stages are mocked.
    """

    def test_late_partition_drops_synthesized_marker_end_to_end(self):
        """A synthesized post-roll marker (no category) is caught by the
        early re-partition right after _refine_and_validate returns and
        never reaches the audio processor, while a real cross_promo cut
        marker still cuts normally."""
        cross_promo = _cross_promo_ad()
        late_marker = {'start': 90.0, 'end': 99.0, 'confidence': 0.9,
                       'detection_stage': 'post_roll'}
        segment_actions = {'sponsor': 'keep', 'cross_promo': 'remove',
                           'self_promo': 'remove', 'interaction': 'remove',
                           'intro': 'remove', 'outro': 'remove', 'recap': 'remove'}

        m = _run_pipeline([cross_promo], segment_actions,
                          late_synthesized_ad=late_marker)

        assert m['result'] is True
        saved = m['storage'].save_combined_ads.call_args.args[2]
        by_span = {(a['start'], a['end']): a for a in saved}
        caught = by_span[(late_marker['start'], late_marker['end'])]
        assert caught['was_cut'] is False
        assert caught['action_applied'] == 'keep'
        cross_promo_marker = by_span[(cross_promo['start'], cross_promo['end'])]
        assert cross_promo_marker['was_cut'] is True
        assert cross_promo_marker['action_applied'] == 'remove'

        audio_segments = m['local_ap'].process_episode.call_args.args[1]
        assert [s['start'] for s in audio_segments] == [cross_promo['start']]

    def test_terminal_snap_does_not_swallow_late_kept_span(self):
        """Drives the real, unpatched _snap_terminal_starts inside the full
        process_episode flow. A synthesized marker at [70, 85) (no category,
        normalizes to 'sponsor' -> 'keep') sits ahead of a terminal cut at
        [90, 99), with a digital-silence splice event at 72.0 inside the 30s
        scan-back window. Without the early re-partition, the marker would
        reach the sweep unstamped, its span read as safe coverage, and the
        terminal cut's start would snap back to 72.0, swallowing part of
        the kept span."""
        terminal_ad = {'start': 90.0, 'end': 99.0, 'category': 'cross_promo',
                       'confidence': 0.9, 'detection_stage': 'text_pattern',
                       'reason': 'terminal block'}
        late_marker = {'start': 70.0, 'end': 85.0, 'confidence': 0.9,
                       'detection_stage': 'post_roll'}
        segment_actions = {'sponsor': 'keep', 'cross_promo': 'remove',
                           'self_promo': 'remove', 'interaction': 'remove',
                           'intro': 'remove', 'outro': 'remove', 'recap': 'remove'}
        segments = [{'start': 70.0, 'end': 85.0,
                    'text': 'and that wraps up this segment for today'}]
        splice_evidence = {'events': [
            {'time': 72.0, 'end_time': 73.4, 'type': 'digital_silence',
             'depth_dbfs': -90.0, 'duration_s': 1.4, 'loudness_step_lu': None,
             'centroid_step_hz': None, 'flatness_step': None},
        ]}
        audio_analysis_result = types.SimpleNamespace(
            splice_evidence=splice_evidence,
            to_dict=lambda: {},
            get_signals_by_type=lambda t: [],
        )

        m = _run_pipeline(
            [terminal_ad], segment_actions, late_synthesized_ad=late_marker,
            real_sweeps=True, audio_analysis_result=audio_analysis_result,
            segments=segments,
        )

        assert m['result'] is True
        saved = m['storage'].save_combined_ads.call_args.args[2]
        by_span = {(a['start'], a['end']): a for a in saved}
        kept = by_span[(late_marker['start'], late_marker['end'])]
        assert kept['was_cut'] is False
        assert kept['action_applied'] == 'keep'

        # Fixed expected value, not a re-read of the possibly-mutated input
        # dict: the terminal cut's start must not have moved into the kept
        # span, and the final cut list handed to ffmpeg must not overlap it.
        audio_segments = m['local_ap'].process_episode.call_args.args[1]
        assert len(audio_segments) == 1
        assert audio_segments[0]['start'] == 90.0
        assert not any(
            processing.ranges_overlap(s['start'], s['end'], 70.0, 85.0)
            for s in audio_segments
        )


class TestPartitionKeepAdsClearsHold:
    """A keep resolution is a final decision, so _partition_keep_ads must
    clear any hold on a marker it catches. Otherwise a marker already held
    for review would keep counting as pending review, and a user confirm on
    it would force-cut it on recut (_build_recut_ad_list never consults
    action_applied), overriding the feed's keep policy."""

    def _held_keep_marker(self):
        # No 'category' key, normalizes to 'sponsor'. held_for_review
        # simulates a marker already held before the late re-partition
        # catches it.
        return {'start': 70.0, 'end': 85.0, 'confidence': 0.9,
               'held_for_review': True, 'hold_reason': 'max_duration',
               'was_cut': False}

    def test_held_keep_marker_is_no_longer_pending_review(self):
        actions_map = {'sponsor': 'keep', 'cross_promo': 'remove',
                       'self_promo': 'remove', 'interaction': 'remove',
                       'intro': 'remove', 'outro': 'remove', 'recap': 'remove'}
        marker = self._held_keep_marker()

        keep_ads, remove_ads = processing._partition_keep_ads([marker], actions_map)

        assert keep_ads == [marker]
        assert remove_ads == []
        assert marker['was_cut'] is False
        assert marker['action_applied'] == 'keep'
        assert marker['held_for_review'] is False
        assert 'hold_reason' not in marker
        # Original reason kept additively, for traceability.
        assert marker['hold_cleared_reason'] == 'max_duration'
        assert processing.is_pending_review(marker) is False
        assert count_pending_review([marker]) == 0

    def test_held_keep_marker_absent_from_patterns_held_marker_match(self):
        actions_map = {'sponsor': 'keep', 'cross_promo': 'remove',
                       'self_promo': 'remove', 'interaction': 'remove',
                       'intro': 'remove', 'outro': 'remove', 'recap': 'remove'}
        marker = self._held_keep_marker()

        processing._partition_keep_ads([marker], actions_map)

        assert _matches_held_marker(
            marker, marker['start'], marker['end'], 0.5) is False

    def test_held_remove_marker_stays_held(self):
        """A held marker whose category resolves to 'remove' is untouched:
        only a keep resolution overrides a hold."""
        actions_map = {'sponsor': 'keep', 'cross_promo': 'remove',
                       'self_promo': 'remove', 'interaction': 'remove',
                       'intro': 'remove', 'outro': 'remove', 'recap': 'remove'}
        marker = {'start': 10.0, 'end': 20.0, 'category': 'cross_promo',
                  'confidence': 0.9, 'held_for_review': True,
                  'hold_reason': 'max_duration', 'was_cut': False}

        keep_ads, remove_ads = processing._partition_keep_ads([marker], actions_map)

        assert keep_ads == []
        assert remove_ads == [marker]
        assert marker['held_for_review'] is True
        assert marker['hold_reason'] == 'max_duration'
        assert 'hold_cleared_reason' not in marker
        assert processing.is_pending_review(marker) is True
        assert _matches_held_marker(
            marker, marker['start'], marker['end'], 0.5) is True

    def test_all_remove_fast_path_never_touches_held_marker(self):
        """Identity fast path: with no category resolving to 'keep', the
        loop never runs at all, so a held marker (even one shaped exactly
        like the held-keep fixture) is not written to."""
        actions_map = {'sponsor': 'remove', 'cross_promo': 'remove',
                       'self_promo': 'remove', 'interaction': 'remove',
                       'intro': 'remove', 'outro': 'remove', 'recap': 'remove'}
        marker = self._held_keep_marker()
        all_ads = [marker]

        keep_ads, remove_ads = processing._partition_keep_ads(all_ads, actions_map)

        assert keep_ads == []
        assert remove_ads is all_ads
        assert marker['held_for_review'] is True
        assert marker['hold_reason'] == 'max_duration'
        assert 'action_applied' not in marker
        assert 'hold_cleared_reason' not in marker


class TestExcludeKeptSpansFromVerification:
    """A pass-2 (verification) finding overlapping a kept pass-1 span must
    be dropped before _gate_verification_ads_by_confidence can cut, hold,
    or log it as a dropped miss."""

    # Pass-1 removed original 100.0-200.0 with a fixed 1.0s beep. A kept
    # marker at original 500.0-520.0 therefore maps onto the processed
    # timeline as 401.0-421.0 (500 - (100s removed - 1s replaced) = 401).
    PASS1_CUTS = [{'start': 100.0, 'end': 200.0, 'replacement_duration': 1.0}]
    KEPT_MARKER = {'start': 500.0, 'end': 520.0, 'action_applied': 'keep'}

    def test_overlapping_finding_dropped_before_any_routing(self, caplog):
        proc_overlap = {'start': 405.0, 'end': 415.0, 'confidence': 0.95,
                        'validation': {'decision': 'ACCEPT', 'adjusted_confidence': 0.95}}
        orig_overlap = {'start': 504.0, 'end': 514.0, 'confidence': 0.95,
                        'sponsor': 'Acme'}

        with patch.object(processing, 'get_replacement_duration', return_value=1.0), \
                caplog.at_level(logging.DEBUG, logger='podcast.audio'):
            out_proc, out_orig, conflicts = processing._exclude_kept_spans_from_verification(
                [proc_overlap], [orig_overlap], [self.KEPT_MARKER], self.PASS1_CUTS)

        assert out_proc == []
        assert out_orig == []
        assert any('contradicts kept span' in r.message for r in caplog.records)

        # The keep still stands, but the disagreement surfaces for review
        # instead of vanishing.
        assert conflicts == [orig_overlap]
        assert orig_overlap['held_for_review'] is True
        assert orig_overlap['was_cut'] is False
        assert orig_overlap['hold_reason'] == HOLD_REASON_VERIFICATION_KEPT_CONFLICT

        # Nothing routes to a cut: the kept span is never cut through.
        v_ads_to_cut, v_ads_for_ui, v_ads_held, n = processing._gate_verification_ads_by_confidence(
            out_proc, out_orig, min_cut_confidence=0.5)
        assert v_ads_to_cut == []
        assert n == 0

    def test_non_overlapping_finding_routes_per_existing_rules(self):
        proc_clear = {'start': 50.0, 'end': 60.0, 'confidence': 0.95,
                     'validation': {'decision': 'ACCEPT', 'adjusted_confidence': 0.95}}
        orig_clear = {'start': 51.0, 'end': 61.0, 'confidence': 0.95,
                     'sponsor': 'Acme'}

        with patch.object(processing, 'get_replacement_duration', return_value=1.0):
            out_proc, out_orig, conflicts = processing._exclude_kept_spans_from_verification(
                [proc_clear], [orig_clear], [self.KEPT_MARKER], self.PASS1_CUTS)

        assert out_proc == [proc_clear]
        assert out_orig == [orig_clear]
        assert conflicts == []

        # Confidence 0.95 >= min_cut_confidence 0.5: confirmed-cut path,
        # unaffected since this finding never overlapped a kept span.
        v_ads_to_cut, v_ads_for_ui, v_ads_held, _n = processing._gate_verification_ads_by_confidence(
            out_proc, out_orig, min_cut_confidence=0.5)
        assert v_ads_to_cut == [proc_clear]
        assert v_ads_for_ui == [orig_clear]
        assert v_ads_held == []

    def test_no_kept_markers_is_noop(self):
        proc = [{'start': 1.0, 'end': 2.0}]
        orig = [{'start': 1.0, 'end': 2.0}]

        out_proc, out_orig, conflicts = processing._exclude_kept_spans_from_verification(
            proc, orig, [], self.PASS1_CUTS)

        assert out_proc is proc
        assert out_orig is orig
        assert conflicts == []


class TestStampPass2MarkerCategories:
    """Pass-2-created markers never route through the detector-merge
    category-validating seam, so _stamp_pass2_marker_categories validates
    them at save time instead."""

    def test_a_missing_category_stays_missing(self):
        markers = [{'start': 1.0, 'end': 2.0}]

        out = processing._stamp_pass2_marker_categories(markers)

        assert out is markers
        assert 'category' not in markers[0]

    def test_preserves_a_valid_category_and_drops_an_unknown_one(self):
        markers = [{'start': 1.0, 'end': 2.0, 'category': 'cross_promo'},
                  {'start': 3.0, 'end': 4.0, 'category': 'not-a-real-category'}]

        processing._stamp_pass2_marker_categories(markers)

        assert markers[0]['category'] == 'cross_promo'
        assert 'category' not in markers[1]


class TestPartitionPass2CategoryActions:
    ACTIONS = {
        'sponsor': 'remove',
        'cross_promo': 'remove',
        'self_promo': 'keep',
        'interaction': 'keep',
        'intro': 'keep',
        'outro': 'beep',
        'recap': 'keep',
    }

    @staticmethod
    def _pair(category, **extra):
        processed = {'start': 10.0, 'end': 20.0, 'category': category, **extra}
        original = {'start': 110.0, 'end': 120.0, 'category': category, **extra}
        return processed, original

    def test_keep_action_removes_pair_from_cut_pipeline(self):
        processed, original = self._pair(
            'self_promo', held_for_review=True, hold_reason='max_duration')

        out_p, out_o, kept_p, kept_o = processing._partition_pass2_category_actions(
            [processed], [original], self.ACTIONS)

        assert out_p == []
        assert out_o == []
        assert kept_p == [processed]
        assert kept_o == [original]
        for marker in (processed, original):
            assert marker['action_applied'] == 'keep'
            assert marker['was_cut'] is False
            assert marker['held_for_review'] is False
            assert marker['hold_cleared_reason'] == 'max_duration'
            assert 'hold_reason' not in marker

    @pytest.mark.parametrize(
        ('category', 'expected'), [('sponsor', 'remove'), ('outro', 'beep')])
    def test_cut_actions_are_delayed_until_candidate_reaches_recut(
            self, category, expected):
        processed, original = self._pair(category)

        out_p, out_o, kept_p, kept_o = processing._partition_pass2_category_actions(
            [processed], [original], self.ACTIONS)

        assert out_p == [processed]
        assert out_o == [original]
        assert kept_p == []
        assert kept_o == []
        assert 'action_applied' not in processed
        assert 'action_applied' not in original

        processing._stamp_pass2_cut_actions(out_p, out_o, self.ACTIONS)

        assert processed['action_applied'] == expected
        assert original['action_applied'] == expected

    def test_defined_pattern_overrides_keep(self):
        processed, original = self._pair('self_promo', pattern_defined=True)

        out_p, out_o, kept_p, kept_o = processing._partition_pass2_category_actions(
            [processed], [original], self.ACTIONS)

        assert out_p == [processed]
        assert out_o == [original]
        assert kept_p == []
        assert kept_o == []
        for marker in (processed, original):
            assert 'action_applied' not in marker
            assert marker['keep_overridden_by_pattern'] is True

        processing._stamp_pass2_cut_actions(out_p, out_o, self.ACTIONS)
        assert processed['action_applied'] == 'remove'
        assert original['action_applied'] == 'remove'

    def test_missing_category_uses_conservative_sponsor_action(self):
        processed = {'start': 10.0, 'end': 20.0}
        original = {'start': 110.0, 'end': 120.0}

        out_p, out_o, kept_p, kept_o = processing._partition_pass2_category_actions(
            [processed], [original], self.ACTIONS)

        assert out_p == [processed]
        assert out_o == [original]
        assert kept_p == []
        assert kept_o == []
        assert 'action_applied' not in processed
        assert 'action_applied' not in original

        processing._stamp_pass2_cut_actions(out_p, out_o, self.ACTIONS)
        assert processed['action_applied'] == 'remove'
        assert original['action_applied'] == 'remove'

    def test_overlapping_candidate_splits_around_kept_audio(self):
        processed = {'start': 100.0, 'end': 180.0, 'reason': 'heuristic roll'}
        original = {'start': 119.0, 'end': 199.0, 'reason': 'heuristic roll'}
        kept = {'start': 130.0, 'end': 150.0, 'category': 'self_promo'}
        pass1_cuts = [
            {'start': 50.0, 'end': 70.0, 'replacement_duration': 1.0},
        ]

        out_p, out_o = processing._exclude_category_kept_spans(
            [processed], [original], [kept], pass1_cuts)

        assert [(ad['start'], ad['end']) for ad in out_p] == [
            (100.0, 130.0), (150.0, 180.0),
        ]
        assert [(ad['start'], ad['end']) for ad in out_o] == [
            (119.0, 149.0), (169.0, 199.0),
        ]
        assert all(ad['reason'] == 'heuristic roll' for ad in out_p + out_o)

    def test_kept_audio_covering_candidate_drops_it(self):
        processed, original = self._pair(None)
        kept = {'start': 5.0, 'end': 25.0, 'category': 'self_promo'}

        out_p, out_o = processing._exclude_category_kept_spans(
            [processed], [original], [kept], [])

        assert out_p == []
        assert out_o == []

    def test_run_verification_persists_keep_without_recut(self):
        ctx = types.SimpleNamespace(
            slug='pass2-actions', episode_id='ep1', podcast_id=1,
            podcast_name='Test Show', episode_title='Episode',
            episode_description=None, podcast_description=None,
        )
        processed, original = self._pair('self_promo')
        audio_processor = types.SimpleNamespace()

        with ExitStack() as stack:
            db = stack.enter_context(patch.object(processing, 'db'))
            stack.enter_context(patch.object(processing, 'storage'))
            stack.enter_context(patch.object(
                processing, '_apply_pass2_heuristic_rolls'))
            verifier_cls = stack.enter_context(
                patch('verification_pass.VerificationPass'))
            verifier_cls.return_value.verify.return_value = {
                'ads': [original],
                'ads_processed': [processed],
                'segments': SEGMENTS,
                'status': 'success',
            }
            db.get_setting_float.return_value = 0.6

            result = processing._run_verification_pass(
                ctx, '/tmp/pass2-actions.mp3', [], False, 0.8,
                audio_processor, None, segment_actions=self.ACTIONS,
            )

        assert result[0] == 0
        assert result[1] == []
        assert result[3] == [original]
        assert result[4] == '/tmp/pass2-actions.mp3'
        assert result[6] is True
        assert original['action_applied'] == 'keep'
        assert original['was_cut'] is False

    def test_pass1_keep_overlap_is_diverted_before_category_keep_partition(self):
        ctx = types.SimpleNamespace(
            slug='pass2-actions', episode_id='ep1', podcast_id=1,
            podcast_name='Test Show', episode_title='Episode',
            episode_description=None, podcast_description=None,
        )
        processed = {
            'start': 110.0, 'end': 120.0, 'confidence': 0.95,
            'category': 'self_promo',
        }
        original = dict(processed)

        with ExitStack() as stack:
            db = stack.enter_context(patch.object(processing, 'db'))
            stack.enter_context(patch.object(processing, 'storage'))
            stack.enter_context(patch.object(
                processing, '_apply_pass2_heuristic_rolls'))
            verifier_cls = stack.enter_context(
                patch('verification_pass.VerificationPass'))
            verifier_cls.return_value.verify.return_value = {
                'ads': [original],
                'ads_processed': [processed],
                'segments': SEGMENTS,
                'status': 'success',
            }
            db.get_setting_float.return_value = 0.6

            result = processing._run_verification_pass(
                ctx, '/tmp/pass2-actions.mp3', [], False, 0.8,
                types.SimpleNamespace(), None,
                pass1_kept_markers=[{
                    'start': 110.0, 'end': 120.0,
                    'action_applied': 'keep',
                }],
                segment_actions=self.ACTIONS,
            )

        assert result[1] == []
        assert result[3] == [original]
        assert original['hold_reason'] == 'verification_kept_conflict'
        assert 'action_applied' not in original


def test_dedupe_pass2_markers_collapses_repeats():
    """The merge concatenates the UI and held lists, so a marker reaching both
    would persist twice. The guard collapses it regardless of how it got there."""
    a = {'start': 1076.22, 'end': 1231.18, 'hold_reason': 'verification_kept_conflict'}
    dup = {'start': 1076.22, 'end': 1231.18, 'hold_reason': 'verification_kept_conflict'}
    other = {'start': 2032.18, 'end': 2131.08, 'hold_reason': 'verification_kept_conflict'}
    different_reason = {'start': 1076.22, 'end': 1231.18, 'hold_reason': 'verification_miss'}

    out = processing._dedupe_pass2_markers([a, dup, other, different_reason])

    assert out == [a, other, different_reason]
    assert len(out) == 3


def test_dedupe_pass2_markers_leaves_distinct_spans_alone():
    markers = [{'start': 1.0, 'end': 2.0}, {'start': 3.0, 'end': 4.0}]
    assert processing._dedupe_pass2_markers(markers) == markers


def test_kept_conflicts_are_disjoint_from_survivors():
    """A conflict routes to the held list, so it must not also remain in the
    surviving list. Overlap there saved the marker twice and rendered
    duplicate review cards in the UI."""
    kept = {'start': 500.0, 'end': 520.0}
    proc_overlap = {'start': 504.0, 'end': 514.0, 'confidence': 0.95}
    orig_overlap = {'start': 504.0, 'end': 514.0, 'confidence': 0.95}
    proc_clear = {'start': 50.0, 'end': 60.0, 'confidence': 0.95}
    orig_clear = {'start': 50.0, 'end': 60.0, 'confidence': 0.95}

    with patch.object(processing, 'get_replacement_duration', return_value=0.0):
        surv_proc, surv_orig, conflicts = processing._exclude_kept_spans_from_verification(
            [proc_overlap, proc_clear], [orig_overlap, orig_clear], [kept], [])

    assert conflicts == [orig_overlap]
    assert surv_orig == [orig_clear]
    assert surv_proc == [proc_clear]
    for c in conflicts:
        assert c not in surv_orig
