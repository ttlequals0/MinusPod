"""Pass-2 markers must not persist a second dict for a span pass 1 already stored.

api/episodes.py buckets markers into four mutually exclusive lists, so a span
showing up as both pending review and kept can only come from two stored
dicts. Three pass-2 producers could hand the merge seam a marker for a span
already in the pass-1 list: the kept-span conflict hold, the category-keep
partition, and a validator hold over a pass-1 held span. Each is walked here
through its real producer, then through the seam helper.
"""
import os
import sys
import tempfile

os.environ.setdefault('MINUSPOD_DATA_DIR', tempfile.mkdtemp(prefix='pass2merge_test_'))
os.environ.setdefault('SECRET_KEY', 'test-secret')

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))

from unittest.mock import patch  # noqa: E402

import main_app.processing as processing  # noqa: E402
from utils.markers import fold_marker_pair  # noqa: E402
from config import (  # noqa: E402
    HOLD_REASON_VERIFICATION_KEPT_CONFLICT, HOLD_REASON_VERIFICATION_MISS,
    count_pending_review, is_pending_review,
)

ACTIONS = {'sponsor': 'remove', 'cross_promo': 'remove', 'self_promo': 'remove',
           'interaction': 'remove', 'intro': 'remove', 'outro': 'keep',
           'recap': 'keep'}

# Pass 1 removed original 100.0-200.0 with a 1.0s beep, so original
# 500.0-520.0 sits at 401.0-421.0 on the processed timeline.
PASS1_CUTS = [{'start': 100.0, 'end': 200.0, 'replacement_duration': 1.0}]


def _seam(all_ads, v_ads_for_ui, v_ads_held):
    """The pass-2 merge seam in process_episode (the 'Merge pass 2 ads into
    combined list for UI' block), on the same inputs."""
    merge_v = processing._dedupe_pass2_markers(
        processing._stamp_pass2_marker_categories(v_ads_for_ui + v_ads_held))
    to_append, folded = processing._merge_pass2_markers(all_ads, merge_v)
    saved = list(all_ads) + to_append
    saved.sort(key=lambda x: x['start'])
    return saved, folded


def test_kept_span_redetected_by_pass2_persists_once():
    keep_marker = {'start': 500.0, 'end': 520.0, 'category': 'recap',
                   'confidence': 0.9, 'detection_stage': 'llm'}
    keep_ads, remove_ads = processing._partition_keep_ads([keep_marker], ACTIONS)
    assert (keep_ads, remove_ads) == ([keep_marker], [])

    proc = {'start': 401.2, 'end': 420.8, 'confidence': 0.95,
            'validation': {'decision': 'ACCEPT', 'adjusted_confidence': 0.95}}
    orig = {'start': 500.2, 'end': 519.8, 'confidence': 0.95, 'sponsor': 'Acme'}
    with patch.object(processing, 'get_replacement_duration', return_value=1.0):
        _p, _o, conflicts = processing._exclude_kept_spans_from_verification(
            [proc], [orig], keep_ads, PASS1_CUTS)
    assert len(conflicts) == 1

    saved, folded = _seam(keep_ads, [], conflicts)

    assert folded == 1
    assert len(saved) == 1
    marker = saved[0]
    assert marker['action_applied'] == 'keep'
    assert is_pending_review(marker) is False
    assert count_pending_review(saved) == 0
    # The keep verdict wins, but the pass-2 evidence it lacked survives.
    assert marker['hold_cleared_reason'] == HOLD_REASON_VERIFICATION_KEPT_CONFLICT
    assert marker['sponsor'] == 'Acme'
    assert marker['category'] == 'recap'
    assert 'hold_reason' not in marker


def test_pass2_category_keep_folds_into_the_pass1_hold():
    held = {'start': 500.0, 'end': 520.0, 'category': 'recap', 'was_cut': False,
            'held_for_review': True, 'hold_reason': 'max_duration',
            'validation': {'decision': 'HOLD', 'flags': ['HOLD: too long']}}
    proc = {'start': 401.2, 'end': 420.8, 'category': 'recap', 'confidence': 0.95}
    orig = {'start': 500.2, 'end': 519.8, 'category': 'recap', 'confidence': 0.95,
            'sponsor': 'Acme'}
    _rp, _ro, _kp, category_kept = processing._partition_pass2_category_actions(
        [proc], [orig], ACTIONS)
    assert len(category_kept) == 1

    saved, folded = _seam([held], [], category_kept)

    assert folded == 1
    assert len(saved) == 1
    marker = saved[0]
    assert marker['action_applied'] == 'keep'
    assert count_pending_review(saved) == 0
    assert marker['hold_cleared_reason'] == 'max_duration'
    assert marker['sponsor'] == 'Acme'


def test_second_hold_for_the_same_span_is_not_counted_twice():
    held = {'start': 500.0, 'end': 520.0, 'category': 'sponsor', 'was_cut': False,
            'held_for_review': True, 'hold_reason': 'max_duration'}
    proc = {'start': 401.2, 'end': 420.8, 'confidence': 0.95,
            'held_for_review': True, 'hold_reason': 'cue_unproven'}
    orig = {'start': 500.2, 'end': 519.8, 'confidence': 0.95, 'sponsor': 'Acme'}
    _cut, ui, gated_held, _n = processing._gate_verification_ads_by_confidence(
        [proc], [orig], 0.7, pass1_held_markers=[held])

    saved, folded = _seam([held], ui, gated_held)

    assert folded == 1
    assert len(saved) == 1
    marker = saved[0]
    assert count_pending_review(saved) == 1
    assert marker['hold_reason'] == 'max_duration'
    assert marker['sponsor'] == 'Acme'
    # Still held, so the second reason is a flag, not a cleared reason.
    assert 'hold_cleared_reason' not in marker
    assert ('INFO: Pass 2 also held this span (cue_unproven)'
            in marker['validation']['flags'])


def test_adjacent_span_outside_tolerance_stays_its_own_marker():
    held = {'start': 500.0, 'end': 520.0, 'category': 'sponsor', 'was_cut': False,
            'held_for_review': True, 'hold_reason': 'max_duration'}
    # 0.6s past the 0.5s both-edges tolerance on the end edge.
    pass2 = {'start': 500.2, 'end': 520.6, 'was_cut': False,
             'held_for_review': True, 'hold_reason': 'cue_unproven'}

    saved, folded = _seam([held], [], [pass2])

    assert folded == 0
    assert len(saved) == 2
    assert count_pending_review(saved) == 2


def test_marker_for_a_new_span_is_appended_untouched():
    held = {'start': 500.0, 'end': 520.0, 'was_cut': False,
            'held_for_review': True, 'hold_reason': 'max_duration'}
    pass2 = {'start': 900.0, 'end': 930.0, 'was_cut': False,
             'held_for_review': True, 'hold_reason': 'cue_unproven'}

    saved, folded = _seam([held], [], [pass2])

    assert folded == 0
    assert [id(m) for m in saved] == [id(held), id(pass2)]


def test_a_cut_pass2_marker_is_never_folded():
    """A cut marker names audio the recut removed; it keeps its own record."""
    rejected = {'start': 500.0, 'end': 520.0, 'was_cut': False,
                'validation': {'decision': 'REJECT'}}
    pass2_cut = {'start': 500.2, 'end': 519.8, 'was_cut': True,
                 'detection_stage': 'verification'}

    saved, folded = _seam([rejected], [pass2_cut], [])

    assert folded == 0
    assert len(saved) == 2


def test_a_cut_pass1_marker_is_never_a_fold_target():
    cut = {'start': 500.0, 'end': 520.0, 'was_cut': True,
           'action_applied': 'remove'}
    pass2_held = {'start': 500.2, 'end': 519.8, 'was_cut': False,
                  'held_for_review': True, 'hold_reason': 'cue_unproven'}

    saved, folded = _seam([cut], [], [pass2_held])

    assert folded == 0
    assert len(saved) == 2


def test_fold_merges_validation_instead_of_replacing_it():
    keep = {'start': 500.0, 'end': 520.0, 'was_cut': False,
            'action_applied': 'keep',
            'validation': {'decision': 'ACCEPT', 'flags': ['INFO: kept']}}
    held = {'start': 500.2, 'end': 519.8, 'was_cut': False,
            'held_for_review': True, 'hold_reason': 'cue_unproven',
            'validation': {'decision': 'REVIEW', 'adjusted_confidence': 0.81,
                           'flags': ['HOLD: cue unproven']}}

    fold_marker_pair(keep, held)

    assert keep['validation']['decision'] == 'ACCEPT'
    # The loser fills what the winner lacked, flags included.
    assert keep['validation']['adjusted_confidence'] == 0.81
    assert keep['validation']['flags'] == ['HOLD: cue unproven', 'INFO: kept']


def test_fold_does_not_shadow_a_hold_reason_already_cleared():
    keep = {'start': 500.0, 'end': 520.0, 'was_cut': False,
            'action_applied': 'keep', 'held_for_review': False,
            'hold_cleared_reason': 'max_duration'}
    held = {'start': 500.2, 'end': 519.8, 'was_cut': False,
            'held_for_review': True,
            'hold_reason': HOLD_REASON_VERIFICATION_KEPT_CONFLICT}

    fold_marker_pair(keep, held)

    assert keep['hold_cleared_reason'] == 'max_duration'
    assert (f'INFO: A second hold was cleared on this span '
            f'({HOLD_REASON_VERIFICATION_KEPT_CONFLICT})'
            in keep['validation']['flags'])


def test_a_marker_without_was_cut_is_never_folded():
    """No was_cut key means a pre-was_cut release wrote it and the span was cut."""
    legacy = {'start': 500.0, 'end': 520.0, 'sponsor': 'Acme'}
    pass2_held = {'start': 500.2, 'end': 519.8, 'was_cut': False,
                  'held_for_review': True, 'hold_reason': 'cue_unproven'}

    saved, folded = _seam([legacy], [], [pass2_held])
    assert (folded, len(saved)) == (0, 2)

    uncut = {'start': 500.0, 'end': 520.0, 'was_cut': False,
             'held_for_review': True, 'hold_reason': 'max_duration'}
    saved, folded = _seam([uncut], [], [{'start': 500.2, 'end': 519.8}])
    assert (folded, len(saved)) == (0, 2)


def test_fold_does_not_invent_was_cut():
    target = {'start': 500.0, 'end': 520.0, 'held_for_review': True,
              'hold_reason': 'max_duration'}
    fold_marker_pair(target, {'start': 500.2, 'end': 519.8})

    assert 'was_cut' not in target


def test_a_pass2_hold_survives_a_fold_into_a_rejected_pass1_marker():
    """Pass 1 rejected the span; pass 2 re-detected it above the hold floor. The
    hold is the open question, so it must reach the review queue."""
    rejected = {'start': 500.0, 'end': 520.0, 'was_cut': False,
                'validation': {'decision': 'REJECT', 'flags': ['REJECT: no evidence']}}
    proc = {'start': 401.2, 'end': 420.8, 'confidence': 0.62}
    orig = {'start': 500.2, 'end': 519.8, 'confidence': 0.62, 'sponsor': 'Acme'}
    _cut, ui, gated_held, _n = processing._gate_verification_ads_by_confidence(
        [proc], [orig], 0.7, verification_miss_hold_min_confidence=0.5,
        verification_miss_autocut_min_confidence=0.0)
    assert gated_held == [orig]

    saved, folded = _seam([rejected], ui, gated_held)

    assert folded == 1
    assert len(saved) == 1
    marker = saved[0]
    assert marker['held_for_review'] is True
    assert marker['hold_reason'] == HOLD_REASON_VERIFICATION_MISS
    assert 'hold_cleared_reason' not in marker
    assert count_pending_review(saved) == 1


def test_fold_verdict_precedence_is_keep_then_hold_then_plain():
    def _record(kind, start=500.0, end=520.0):
        marker = {'start': start, 'end': end, 'was_cut': False}
        if kind == 'keep':
            marker['action_applied'] = 'keep'
        elif kind == 'hold':
            marker.update(held_for_review=True, hold_reason=f'{kind}_reason')
        else:
            marker['validation'] = {'decision': 'REJECT'}
        return marker

    expected = {
        ('keep', 'keep'): 'keep', ('keep', 'hold'): 'keep', ('keep', 'reject'): 'keep',
        ('hold', 'keep'): 'keep', ('hold', 'hold'): 'hold', ('hold', 'reject'): 'hold',
        ('reject', 'keep'): 'keep', ('reject', 'hold'): 'hold',
        ('reject', 'reject'): 'reject',
    }
    for (target_kind, other_kind), winner in expected.items():
        target = _record(target_kind)
        fold_marker_pair(target, _record(other_kind, 500.2, 519.8))
        assert (target.get('action_applied') == 'keep') is (winner == 'keep')
        assert bool(target.get('held_for_review')) is (winner == 'hold')
        assert ('hold_reason' in target) is (winner == 'hold')
