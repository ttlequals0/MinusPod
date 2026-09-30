"""Applied cuts as the final authority for marker state, counts and durations."""
import copy
import json
import time
from contextlib import ExitStack
from unittest.mock import MagicMock, patch

from tests.app_bootstrap import bootstrap

_test_data_dir = bootstrap('finalize_cut_state_test_')

import main_app.processing as processing  # noqa: E402
from tests.unit.marker_test_utils import applied_cut  # noqa: E402
from tests.unit.recut_test_utils import stub_recut  # noqa: E402


def _marker(start, end, **extra):
    m = {'start': start, 'end': end, 'confidence': 0.95, 'category': 'sponsor',
         'sponsor': 'Acme', 'detection_stage': 'claude', 'was_cut': True,
         'action_applied': 'remove'}
    m.update(extra)
    return m


class TestFinalizeCutState:
    def test_two_markers_under_one_rendered_cut_count_once(self):
        a, b = _marker(10.0, 40.0), _marker(40.5, 70.0)
        groups = processing._finalize_cut_state([a, b], [a, b], [applied_cut(10.0, 70.0)], 600.0)
        assert groups == [[10.0, 70.0]]
        assert a['was_cut'] is True and b['was_cut'] is True

    def test_marker_outside_the_applied_cuts_is_not_cut(self):
        a, b = _marker(10.0, 40.0), _marker(100.0, 104.0)
        processing._finalize_cut_state([a, b], [a, b], [applied_cut(10.0, 40.0)], 600.0)
        assert a['was_cut'] is True
        assert b['was_cut'] is False

    def test_marker_not_requested_is_not_cut_even_when_covered(self):
        cut = _marker(10.0, 60.0)
        stale = _marker(20.0, 30.0, validation={'decision': 'REJECT'})
        held = _marker(30.0, 40.0, was_cut=False, held_for_review=True,
                       hold_reason='max_duration')
        processing._finalize_cut_state([cut, stale, held], [cut], [applied_cut(10.0, 60.0)], 600.0)
        assert stale['was_cut'] is False
        assert held['was_cut'] is False

    def test_rebuilt_request_dict_resolves_its_master_by_span(self):
        master = _marker(10.0, 40.0, was_cut=False)
        rebuilt = dict(master)
        processing._finalize_cut_state([master], [rebuilt], [applied_cut(10.0, 40.0)], 600.0)
        assert master['was_cut'] is True
        assert rebuilt['was_cut'] is True

    def test_identity_wins_over_an_earlier_span_twin(self):
        twin = _marker(10.0, 40.0, was_cut=False, validation={'decision': 'REJECT'})
        master = _marker(10.0, 40.0)
        processing._finalize_cut_state([twin, master], [master], [applied_cut(10.0, 40.0)], 600.0)
        assert master['was_cut'] is True
        assert twin['was_cut'] is False

    def test_edges_within_tolerance_are_covered(self):
        a = _marker(10.0, 70.0)
        processing._finalize_cut_state([a], [a], [applied_cut(10.04, 69.96)], 600.0)
        assert a['was_cut'] is True
        b = _marker(10.0, 70.0)
        processing._finalize_cut_state([b], [b], [applied_cut(10.2, 70.0)], 600.0)
        assert b['was_cut'] is False

    def test_touching_rendered_cuts_cover_a_marker_together(self):
        a = _marker(10.0, 70.0)
        processing._finalize_cut_state(
            [a], [a], [applied_cut(10.0, 40.0), applied_cut(40.0, 70.0)], 600.0)
        assert a['was_cut'] is True

    def test_overrunning_marker_is_clamped_to_the_audio(self):
        a = _marker(580.0, 605.0)
        processing._finalize_cut_state([a], [a], [applied_cut(580.0, 600.0)], 600.0)
        assert a['was_cut'] is True

    def test_partial_coverage_carves_cut_and_uncut_fragments(self):
        a = _marker(10.0, 50.0, validation={'decision': 'ACCEPT', 'flags': []})
        all_ads, ads_to_remove = [a], [a]
        processing._finalize_cut_state(all_ads, ads_to_remove, [applied_cut(10.0, 30.0)], 600.0)
        assert [(m['start'], m['end'], m['was_cut']) for m in all_ads] == [
            (10.0, 30.0, True), (30.0, 50.0, False)]
        assert all(m['carved_from'] == {'start': 10.0, 'end': 50.0} for m in all_ads)
        assert ads_to_remove == [all_ads[0]] and ads_to_remove[0] is all_ads[0]
        assert [m['validation']['flags'] for m in all_ads] == [[], []]
        assert a['validation']['flags'] == []
        assert not any(m.get('held_for_review') for m in all_ads)

    def test_middle_cut_leaves_two_remainders(self):
        a = _marker(10.0, 70.0)
        all_ads = [_marker(0.0, 5.0, was_cut=False), a, _marker(100.0, 110.0, was_cut=False)]
        processing._finalize_cut_state(all_ads, [a], [applied_cut(30.0, 50.0)], 600.0)
        assert [(m['start'], m['end'], m['was_cut']) for m in all_ads] == [
            (0.0, 5.0, False), (10.0, 30.0, False), (30.0, 50.0, True),
            (50.0, 70.0, False), (100.0, 110.0, False)]

    def test_rebuilt_request_and_master_are_both_carved(self):
        master = _marker(10.0, 50.0)
        rebuilt = dict(master)
        all_ads, ads_to_remove = [master], [rebuilt]
        processing._finalize_cut_state(all_ads, ads_to_remove, [applied_cut(10.0, 30.0)], 600.0)
        assert [(m['start'], m['end'], m['was_cut']) for m in all_ads] == [
            (10.0, 30.0, True), (30.0, 50.0, False)]
        assert [(m['start'], m['end'], m['was_cut']) for m in ads_to_remove] == [
            (10.0, 30.0, True)]

    def test_sliver_of_coverage_does_not_carve(self):
        a = _marker(10.0, 50.0)
        all_ads = [a]
        processing._finalize_cut_state(all_ads, [a], [applied_cut(0.0, 10.03)], 600.0)
        assert all_ads == [a] and a['was_cut'] is False and 'carved_from' not in a

    def test_carve_is_idempotent_across_two_finalize_calls(self):
        a, b = _marker(10.0, 50.0), _marker(100.0, 120.0)
        all_ads, ads_to_remove = [a, b], [a, b]
        cuts = [applied_cut(10.0, 30.0), applied_cut(100.0, 120.0)]
        processing._finalize_cut_state(all_ads, ads_to_remove, cuts, 600.0)
        first = copy.deepcopy(all_ads)
        processing._finalize_cut_state(all_ads, [*ads_to_remove], cuts, 600.0)
        assert all_ads == first
        assert len(all_ads) == 3

    def test_pass2_cut_re_carves_a_pass1_remainder(self):
        a = _marker(10.0, 50.0, validation={'decision': 'ACCEPT', 'flags': []})
        all_ads, ads_to_remove = [a], [a]
        pass1 = [applied_cut(10.0, 30.0)]
        processing._finalize_cut_state(all_ads, ads_to_remove, pass1, 600.0)
        final = [*pass1, applied_cut(40.0, 50.0)]
        processing._finalize_cut_state(all_ads, [*ads_to_remove], final, 600.0)
        assert [(m['start'], m['end'], m['was_cut']) for m in all_ads] == [
            (10.0, 30.0, True), (30.0, 40.0, False), (40.0, 50.0, True)]
        assert all(m['carved_from'] == {'start': 10.0, 'end': 50.0} for m in all_ads)
        first = copy.deepcopy(all_ads)
        processing._finalize_cut_state(all_ads, [*ads_to_remove], final, 600.0)
        assert all_ads == first

    def test_remainder_fully_cut_later_is_marked_cut(self):
        a = _marker(10.0, 50.0, validation={'decision': 'ACCEPT', 'flags': ['WARN: x']})
        all_ads, ads_to_remove = [a], [a]
        pass1 = [applied_cut(10.0, 30.0)]
        processing._finalize_cut_state(all_ads, ads_to_remove, pass1, 600.0)
        processing._finalize_cut_state(
            all_ads, [*ads_to_remove], [*pass1, applied_cut(30.0, 50.0)], 600.0)
        assert [(m['start'], m['end'], m['was_cut'], m['validation']['flags'])
                for m in all_ads] == [(10.0, 30.0, True, ['WARN: x']),
                                      (30.0, 50.0, True, ['WARN: x'])]

    def test_carved_fragment_is_not_learned(self, monkeypatch):
        a = _marker(10.0, 50.0)
        all_ads, ads_to_remove = [a], [a]
        cuts = [applied_cut(10.0, 30.0)]
        processing._finalize_cut_state(all_ads, ads_to_remove, cuts, 600.0)
        learn = MagicMock(return_value=1)
        monkeypatch.setattr(processing.ad_detector, 'learn_from_detections', learn)
        assert processing._learn_from_applied_cut_ads(
            'example-podcast', 'a1b2c3d4e5f6', ads_to_remove, all_ads,
            cuts, 600.0, [], 'unused.wav') == 0
        learn.assert_not_called()

    def test_is_idempotent(self):
        markers = [_marker(10.0, 40.0), _marker(40.5, 70.0), _marker(100.0, 104.0),
                   _marker(200.0, 260.0), _marker(300.0, 340.0)]
        cuts = [applied_cut(10.0, 70.0), applied_cut(200.0, 230.0), applied_cut(320.0, 340.0)]
        processing._finalize_cut_state(markers, list(markers), cuts, 600.0)
        first = copy.deepcopy(markers)
        processing._finalize_cut_state(markers, list(markers), cuts, 600.0)
        assert markers == first


class TestCutSeconds:
    def test_source_and_replacement_seconds(self):
        cuts = [applied_cut(10.0, 70.0, 1.0), applied_cut(100.0, 110.0, 10.0)]
        assert processing._cut_seconds(cuts) == (70.0, 11.0)

    def test_overlapping_cuts_count_source_audio_once(self):
        cuts = [applied_cut(10.0, 70.0, 1.0), applied_cut(60.0, 80.0, 1.0)]
        assert processing._cut_seconds(cuts) == (70.0, 2.0)


class TestRecordCutSeconds:
    def test_failed_duration_probe_estimates_seconds_removed(self):
        run_stats = {}
        cuts = [applied_cut(10.0, 70.0, 1.0)]
        processing._record_cut_seconds(run_stats, cuts, 600.0, None)
        assert run_stats['source_seconds_removed'] == 60.0
        assert run_stats['replacement_seconds_added'] == 1.0
        assert run_stats['seconds_removed'] == 59.0

    def test_measured_duration_uses_the_probe(self):
        run_stats = {}
        cuts = [applied_cut(10.0, 70.0, 1.0)]
        processing._record_cut_seconds(run_stats, cuts, 600.0, 541.0)
        assert run_stats['seconds_removed'] == 59.0


# ---------------------------------------------------------------------------
# _recut_episode order of operations and failure safety
# ---------------------------------------------------------------------------

def _run_recut(ads_to_remove, all_ads, render, *, new_duration=600.0,
               assets_side_effect=None, finalize_side_effect=None, run_stats=None,
               publish_before_failure=False):
    """Drive _recut_episode with the cut list and the renderer stubbed."""
    snapshot = json.dumps([{'start': 1.0, 'end': 2.0, 'was_cut': True}])
    calls = MagicMock()
    progress = {}
    captured = {}
    with ExitStack() as stack:
        p = lambda *a, **k: stack.enter_context(patch.object(*a, **k))
        _db, storage, local_ap = stub_recut(
            stack, ads_to_remove, all_ads, duration=600.0,
            episode_row={'podcast_id': 1, 'processed_version': 2, 'ad_markers_json': snapshot})
        p(processing, '_handle_processing_failure')

        def _assets(*args, **kwargs):
            captured['assets_cuts'] = args[3]
            captured['assets_markers'] = kwargs['markers']
            captured['assets_was_cut'] = [m.get('was_cut') for m in kwargs['markers']]
            calls.assets()
            if assets_side_effect:
                raise assets_side_effect

        def _finalize(*args, **kwargs):
            captured['finalize_args'] = args
            captured['finalize_kwargs'] = kwargs
            calls.finalize()
            if publish_before_failure:
                kwargs['on_persisted']()
            if finalize_side_effect:
                raise finalize_side_effect

        p(processing, '_generate_assets', side_effect=_assets)
        p(processing, '_finalize_episode', side_effect=_finalize)
        move = p(processing.shutil, 'move', side_effect=lambda *a: calls.move())
        storage.save_combined_ads.side_effect = (
            lambda *a: calls.save(copy.deepcopy(a[2])))
        durations = iter([600.0, new_duration])
        local_ap.get_audio_duration.side_effect = lambda path: next(durations)

        def _render(work_path, segs, cut_barriers=None, hard_barriers=None):
            captured['cut_barriers'] = cut_barriers
            calls.render()
            applied = render(segs)
            return None if applied is None else ('/tmp/fcs-cut.mp3', applied)

        local_ap.process_episode.side_effect = _render

        result = processing._recut_episode(
            'example-podcast', 'a1b2c3d4e5f6', 'Episode', 'Podcast', 'desc',
            time.time(), cancel_event=None, run_stats=run_stats,
            progress=progress)

    return {'result': result, 'storage': storage, 'calls': calls, 'move': move,
            'progress': progress, 'captured': captured, 'snapshot': snapshot}


def _saves(m):
    return [c.args[0] for c in m['calls'].method_calls if c[0] == 'save']


class TestRecutOrderAndFailure:
    def test_render_failure_publishes_nothing(self):
        a, b = _marker(10.0, 40.0), _marker(100.0, 110.0, was_cut=False)
        before = copy.deepcopy([a, b])
        m = _run_recut([a], [a, b], render=lambda segs: None)
        assert m['result'] is False
        m['storage'].save_combined_ads.assert_not_called()
        m['storage'].save_chapters_and_applied_cuts.assert_not_called()
        assert 'finalize_args' not in m['captured']
        assert m['progress'] == {}
        assert [a, b] == before

    def test_assets_failure_leaves_markers_unsaved(self):
        a = _marker(10.0, 40.0)
        m = _run_recut([a], [a], render=lambda segs: [applied_cut(10.0, 40.0)],
                       assets_side_effect=RuntimeError('vtt write failed'))
        assert m['result'] is False
        m['storage'].save_combined_ads.assert_not_called()
        assert 'finalize_args' not in m['captured']

    def test_failure_after_the_save_restores_the_entry_snapshot(self):
        a = _marker(10.0, 40.0)
        m = _run_recut([a], [a], render=lambda segs: [applied_cut(10.0, 40.0)],
                       finalize_side_effect=RuntimeError('publication fence'))
        assert m['result'] is False
        saves = _saves(m)
        assert len(saves) == 2
        assert saves[-1] == json.loads(m['snapshot'])
        assert m['progress'] == {'mutated': True}

    def test_failure_after_publish_keeps_the_new_markers(self):
        a = _marker(10.0, 40.0)
        m = _run_recut([a], [a], render=lambda segs: [applied_cut(10.0, 40.0)],
                       finalize_side_effect=RuntimeError('history write failed'),
                       publish_before_failure=True)
        assert m['result'] is False
        saves = _saves(m)
        assert len(saves) == 1
        assert saves[0] != json.loads(m['snapshot'])
        assert m['progress'] == {'mutated': True, 'published': True}

    def test_order_is_render_move_assets_save_finalize(self):
        a = _marker(10.0, 40.0)
        m = _run_recut([a], [a], render=lambda segs: [applied_cut(10.0, 40.0)])
        assert m['result'] is True
        order = [c[0] for c in m['calls'].method_calls]
        assert order == ['render', 'move', 'assets', 'save', 'finalize']

    def test_every_pending_hold_is_a_render_cut_barrier(self):
        a = _marker(10.0, 40.0)
        hold = _marker(560.0, 590.0, was_cut=False, held_for_review=True,
                       hold_reason='no_splice_evidence')
        m = _run_recut([a], [a, hold], render=lambda segs: [applied_cut(10.0, 40.0)])
        assert m['result'] is True
        assert [(b['start'], b['end']) for b in m['captured']['cut_barriers']] == [
            (560.0, 590.0)]

    def test_an_approved_hold_is_not_a_render_cut_barrier(self):
        a = _marker(10.0, 40.0)
        approved = _marker(5.0, 60.0, held_for_review=True,
                           hold_reason='no_splice_evidence')
        fresh = {'start': 560.0, 'end': 590.0, 'held_for_review': True,
                 'hold_reason': 'no_splice_evidence'}
        m = _run_recut([a], [a, approved, fresh],
                       render=lambda segs: [applied_cut(10.0, 40.0)])
        assert [(b['start'], b['end']) for b in m['captured']['cut_barriers']] == [
            (560.0, 590.0)]

    def test_assets_receive_exactly_the_saved_markers_and_cuts(self):
        a, b = _marker(10.0, 40.0), _marker(100.0, 104.0)
        applied = [applied_cut(10.0, 40.0)]
        m = _run_recut([a, b], [a, b], render=lambda segs: applied)
        saved = _saves(m)[-1]
        assert m['captured']['assets_cuts'] == applied
        assert m['captured']['assets_markers'] == [a, b]
        assert m['captured']['assets_was_cut'] == [s['was_cut'] for s in saved]
        assert [s['was_cut'] for s in saved] == [True, False]

    def test_counts_and_durations_follow_the_rendered_cuts(self):
        a, b = _marker(10.0, 40.0), _marker(40.5, 70.0)
        beep = _marker(200.0, 210.0, action_applied='beep')
        applied = [applied_cut(10.0, 70.0, 1.0), applied_cut(200.0, 210.0, 10.0)]
        run_stats = {'markers': {'cut': 0, 'held': 0, 'not_cut': 0},
                     'verification_ads_cut': 0}
        # new = original - source (70 s) + replacement (11 s)
        m = _run_recut([a, b, beep], [a, b, beep], render=lambda segs: applied,
                       new_duration=541.0, run_stats=run_stats)
        args = m['captured']['finalize_args']
        assert args[4] == 2  # ads_removed: rendered cuts, not markers
        assert run_stats['markers']['cut'] == 3
        assert run_stats['seconds_removed'] == 59.0
        assert run_stats['source_seconds_removed'] == 70.0
        assert run_stats['replacement_seconds_added'] == 11.0

    def test_recut_counts_the_cut_fragment_of_a_partly_rendered_marker(self):
        a, b = _marker(10.0, 40.0), _marker(100.0, 160.0)
        all_ads = [a, b]
        run_stats = {'markers': {'cut': 0, 'held': 0, 'not_cut': 0}}
        m = _run_recut([a, b], all_ads, render=lambda segs: [
            applied_cut(10.0, 40.0), applied_cut(100.0, 130.0)], run_stats=run_stats)
        saved = _saves(m)[-1]
        assert [(s['start'], s['end'], s['was_cut']) for s in saved] == [
            (10.0, 40.0, True), (100.0, 130.0, True), (130.0, 160.0, False)]
        assert run_stats['markers']['cut'] == 2
        assert run_stats['markers']['not_cut'] == 1

    def test_recut_carves_a_pending_hold_against_the_applied_cuts(self):
        a = _marker(10.0, 40.0)
        hold = _marker(30.0, 60.0, was_cut=False, held_for_review=True,
                       hold_reason='no_splice_evidence', hold_id='a1b2c3d4e5f6')
        m = _run_recut([a], [a, hold], render=lambda segs: [applied_cut(10.0, 40.0)])
        assert m['result'] is True
        [saved] = _saves(m)
        assert [(x['start'], x['end'], bool(x.get('held_for_review'))) for x in saved] == [
            (10.0, 40.0, False), (40.0, 60.0, True)]
        assert saved[1]['hold_id'] == 'a1b2c3d4e5f6'
