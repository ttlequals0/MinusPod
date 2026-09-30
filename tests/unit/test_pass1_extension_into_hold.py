"""A pass-1 reviewer extension into a pending hold: the render cuts it, so the hold shrinks."""
from tests.app_bootstrap import bootstrap

bootstrap('pass1_extension_into_hold_test_')

from audio_processor import AudioProcessor
from config import is_pending_review
from tests.unit.pipeline_test_utils import _run_pipeline
from tests.unit.recut_test_utils import ALL_REMOVE

HELD = 'cross_promo'


def _ads():
    cut = {'start': 376.9, 'end': 601.2, 'category': 'sponsor', 'confidence': 0.95,
           'detection_stage': 'llm', 'reason': 'Acme sponsor read'}
    hold = {'start': 609.0, 'end': 680.8, 'category': HELD, 'confidence': 0.9,
            'detection_stage': 'llm', 'reason': 'Acme read', 'hold_id': 'a1b2c3d4e5f6'}
    return cut, hold


def _extend_to(end):
    # Like the real reviewer: the cut list gets an adjusted copy, the master takes the verdict.
    def review(ads_to_remove, all_ads):
        out = []
        for ad in ads_to_remove:
            if ad['category'] == 'sponsor':
                moved = dict(reviewer_verdict='adjust', reviewer_moved=True,
                             reviewer_original_start=ad['start'],
                             reviewer_original_end=ad['end'], end=end)
                next(m for m in all_ads if m is ad).update(moved)
                ad = dict(ad, **moved)
            out.append(ad)
        return out, all_ads
    return review


def _run(end):
    cut, hold = _ads()
    run = _run_pipeline([cut, hold], dict(ALL_REMOVE), held_categories={HELD},
                        reviewer_side_effect=_extend_to(end), duration=3000.0)
    return run, run['local_ap'].process_episode.call_args


def test_render_does_not_clip_a_cut_at_a_pending_hold():
    applied = AudioProcessor().compute_applied_cuts(
        [{'start': 376.9, 'end': 661.2}], 3000.0,
        cut_barriers=[{'start': 609.0, 'end': 680.8}])
    assert [(c['start'], c['end']) for c in applied] == [(376.9, 661.2)]


def test_extension_into_a_pending_hold_shrinks_the_hold(caplog):
    with caplog.at_level('INFO', logger='podcast.audio'):
        run, call = _run(661.2)

    assert run['result'] is True
    requested = [(s['start'], s['end']) for s in call.args[1]]
    assert requested == [(376.9, 661.2)]
    assert [(b['start'], b['end']) for b in call.kwargs['cut_barriers']] == [(661.2, 680.8)]

    saved = run['storage'].save_combined_ads.call_args.args[2]
    spans = {(m['start'], m['end']): m for m in saved}
    assert set(spans) == {(376.9, 661.2), (661.2, 680.8)}
    held = spans[(661.2, 680.8)]
    assert is_pending_review(held) and held['hold_id'] == 'a1b2c3d4e5f6'
    assert spans[(376.9, 661.2)]['was_cut'] is True
    assert any('Hold 609.0s-680.8s shrinks to 661.2s-680.8s' in r.getMessage()
               for r in caplog.records)


def test_extension_over_the_whole_hold_removes_it():
    run, call = _run(685.0)
    assert [(b['start'], b['end']) for b in call.kwargs['cut_barriers']] == []
    saved = run['storage'].save_combined_ads.call_args.args[2]
    assert [(m['start'], m['end']) for m in saved] == [(376.9, 685.0)]


def test_short_remainder_is_left_uncut_without_a_hold():
    run, call = _run(676.0)
    assert [(b['start'], b['end']) for b in call.kwargs['cut_barriers']] == []
    saved = run['storage'].save_combined_ads.call_args.args[2]
    assert [(m['start'], m['end']) for m in saved] == [(376.9, 676.0)]


def test_unmoved_cut_next_to_a_hold_leaves_it_whole():
    cut, hold = _ads()
    run = _run_pipeline([cut, hold], dict(ALL_REMOVE), held_categories={HELD},
                        duration=3000.0)
    call = run['local_ap'].process_episode.call_args
    assert [(b['start'], b['end']) for b in call.kwargs['cut_barriers']] == [(609.0, 680.8)]
