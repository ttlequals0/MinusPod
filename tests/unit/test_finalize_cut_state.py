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

    def test_partial_coverage_is_not_cut_and_records_the_covered_span(self):
        a = _marker(10.0, 50.0)
        processing._finalize_cut_state([a], [a], [applied_cut(10.0, 30.0)], 600.0)
        assert a['was_cut'] is False
        assert a['partial_cut_spans'] == [{'start': 10.0, 'end': 30.0}]
        processing._finalize_cut_state([a], [a], [applied_cut(10.0, 50.0)], 600.0)
        assert a['was_cut'] is True
        assert 'partial_cut_spans' not in a

    def test_is_idempotent(self):
        markers = [_marker(10.0, 40.0), _marker(40.5, 70.0), _marker(100.0, 104.0),
                   _marker(200.0, 260.0)]
        cuts = [applied_cut(10.0, 70.0), applied_cut(200.0, 230.0)]
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


# ---------------------------------------------------------------------------
# _recut_episode order of operations and failure safety
# ---------------------------------------------------------------------------

def _run_recut(ads_to_remove, all_ads, render, *, new_duration=600.0,
               assets_side_effect=None, finalize_side_effect=None, run_stats=None):
    """Drive _recut_episode with the cut list and the renderer stubbed."""
    snapshot = json.dumps([{'start': 1.0, 'end': 2.0, 'was_cut': True}])
    calls = MagicMock()
    progress = {}
    captured = {}
    with ExitStack() as stack:
        p = lambda *a, **k: stack.enter_context(patch.object(*a, **k))
        db = p(processing, 'db')
        storage = p(processing, 'storage')
        p(processing, 'status_service')
        p(processing, '_handle_processing_failure')
        p(processing, '_copy_retained_original_to_temp', return_value='/tmp/fcs-work.mp3')
        p(processing, '_build_recut_ad_list', return_value=(ads_to_remove, all_ads, [], [], []))

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
            if finalize_side_effect:
                raise finalize_side_effect

        p(processing, '_generate_assets', side_effect=_assets)
        p(processing, '_finalize_episode', side_effect=_finalize)
        local_ap_cls = p(processing, 'AudioProcessor')
        p(processing.os.path, 'exists', return_value=False)
        move = p(processing.shutil, 'move', side_effect=lambda *a: calls.move())

        db.get_episode.return_value = {'podcast_id': 1, 'processed_version': 2,
                                       'ad_markers_json': snapshot}
        db.get_original_segments.return_value = [{'start': 0.0, 'end': 600.0}]
        db.get_all_settings.return_value = {}
        db.resolve_segment_actions.return_value = {}
        db.get_confirmed_corrections.return_value = []
        db.get_false_positive_corrections.return_value = []
        storage.get_original_path.return_value.exists.return_value = True
        storage.get_applied_cuts.return_value = None
        storage.get_episode_path.return_value = '/tmp/fcs-final.mp3'
        storage.save_combined_ads.side_effect = (
            lambda *a: calls.save(copy.deepcopy(a[2])))

        local_ap = local_ap_cls.return_value
        durations = iter([600.0, new_duration])
        local_ap.get_audio_duration.side_effect = lambda path: next(durations)

        def _render(work_path, segs, cut_barriers=None, hard_barriers=None):
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

    def test_order_is_render_move_assets_save_finalize(self):
        a = _marker(10.0, 40.0)
        m = _run_recut([a], [a], render=lambda segs: [applied_cut(10.0, 40.0)])
        assert m['result'] is True
        order = [c[0] for c in m['calls'].method_calls]
        assert order == ['render', 'move', 'assets', 'save', 'finalize']

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
