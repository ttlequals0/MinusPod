"""Pending holds are carved against the applied cuts, so marker state matches the audio."""
from tests.app_bootstrap import bootstrap

bootstrap('pass1_extension_into_hold_test_')

from audio_processor import AudioProcessor
from config import is_pending_review
from main_app import processing
from tests.unit.marker_test_utils import applied_cut
from tests.unit.pipeline_test_utils import _run_pipeline
from tests.unit.recut_test_utils import ALL_REMOVE

HELD = 'cross_promo'


def _cut(start, end, confidence=0.95):
    return {'start': start, 'end': end, 'category': 'sponsor', 'confidence': confidence,
            'detection_stage': 'llm', 'reason': 'Acme sponsor read'}


def _hold():
    return {'start': 609.0, 'end': 680.8, 'category': HELD, 'confidence': 0.9,
            'detection_stage': 'llm', 'reason': 'Acme read', 'hold_id': 'a1b2c3d4e5f6'}


def _move(start, end):
    # Like the real reviewer: the cut list gets an adjusted copy, the master takes the verdict.
    def review(ads_to_remove, all_ads):
        out = []
        for ad in ads_to_remove:
            moved = dict(reviewer_verdict='adjust', reviewer_moved=True,
                         reviewer_original_start=ad['start'],
                         reviewer_original_end=ad['end'], start=start, end=end)
            next(m for m in all_ads if m is ad).update(moved)
            out.append(dict(ad, **moved))
        return out, all_ads
    return review


def _render(segs):
    return AudioProcessor().compute_applied_cuts(segs, 3000.0)


def _run(ads, review=None):
    run = _run_pipeline(ads, dict(ALL_REMOVE), held_categories={HELD},
                        reviewer_side_effect=review, duration=3000.0, render=_render)
    saved = run['storage'].save_combined_ads.call_args.args[2]
    return run, {(m['start'], m['end']): m for m in saved}


def test_render_does_not_clip_a_cut_at_a_pending_hold():
    applied = AudioProcessor().compute_applied_cuts(
        [{'start': 376.9, 'end': 661.2}], 3000.0,
        cut_barriers=[{'start': 609.0, 'end': 680.8}])
    assert [(c['start'], c['end']) for c in applied] == [(376.9, 661.2)]


def test_extension_into_a_pending_hold_shrinks_the_hold(caplog):
    with caplog.at_level('INFO', logger='podcast.audio'):
        run, spans = _run([_cut(376.9, 601.2), _hold()], _move(376.9, 661.2))

    assert run['result'] is True
    assert set(spans) == {(376.9, 661.2), (661.2, 680.8)}
    held = spans[(661.2, 680.8)]
    assert is_pending_review(held) and held['hold_id'] == 'a1b2c3d4e5f6'
    assert spans[(376.9, 661.2)]['was_cut'] is True
    assert any('Hold 609.0s-680.8s shrinks to 661.2s-680.8s' in r.getMessage()
               for r in caplog.records)


def test_cut_over_the_whole_hold_removes_it():
    _run_result, spans = _run([_cut(376.9, 601.2), _hold()], _move(376.9, 685.0))
    assert set(spans) == {(376.9, 685.0)}


def test_short_remainder_is_dropped_with_a_log_line(caplog):
    with caplog.at_level('INFO', logger='podcast.audio'):
        _run_result, spans = _run([_cut(376.9, 601.2), _hold()], _move(376.9, 676.0))
    assert set(spans) == {(376.9, 676.0)}
    assert any('Hold 609.0s-680.8s is removed' in r.getMessage() and '676.0s-680.8s' in
               r.getMessage() for r in caplog.records)


def test_short_moved_cut_the_render_drops_leaves_the_hold_whole():
    _run_result, spans = _run([_cut(590.0, 600.0, 0.85), _hold()], _move(603.0, 611.0))
    held = spans[(609.0, 680.8)]
    assert is_pending_review(held)
    assert spans[(603.0, 611.0)]['was_cut'] is False


def test_short_cut_merged_with_a_neighbour_shrinks_the_hold():
    _run_result, spans = _run([_cut(580.0, 603.0), _cut(603.5, 612.0, 0.85), _hold()])
    assert is_pending_review(spans[(612.0, 680.8)])
    assert (609.0, 680.8) not in spans


def test_plain_cut_overlapping_a_hold_shrinks_it():
    _run_result, spans = _run([_cut(376.9, 620.0), _hold()])
    assert is_pending_review(spans[(620.0, 680.8)])
    assert spans[(376.9, 620.0)]['was_cut'] is True


def test_carved_hold_keeps_its_identity_and_diagnostics():
    cut = {'start': 10.0, 'end': 40.0, 'was_cut': True}
    hold = {'start': 30.0, 'end': 60.0, 'held_for_review': True, 'was_cut': False,
            'hold_reason': 'no_splice_evidence', 'hold_id': 'a1b2c3d4e5f6',
            'pass2_hold_review': {'span': [45.0, 55.0], 'verdict': 'reject', 'reason': 'x'}}
    all_ads = [cut, hold]
    processing._finalize_cut_state(all_ads, [cut], [applied_cut(10.0, 40.0)], 600.0)
    [_cut_marker, rest] = all_ads
    assert (rest['start'], rest['end']) == (40.0, 60.0)
    assert rest['hold_id'] == 'a1b2c3d4e5f6'
    assert rest['pass2_hold_review'] == hold['pass2_hold_review']
    assert is_pending_review(rest)
