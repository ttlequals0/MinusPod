"""Unit tests for the cue threshold auto-suggest helper (#350)."""
from audio_analysis.cue_threshold_suggest import suggest_cue_threshold


def test_bimodal_scores_suggest_gap_midpoint():
    # Noise cluster ~0.45-0.52, signal cluster ~0.90-0.97: wide clean gap.
    scores = [0.45, 0.48, 0.50, 0.52, 0.90, 0.93, 0.95, 0.97]
    out = suggest_cue_threshold(scores)
    assert out['confidence'] == 'high'
    assert 0.52 < out['suggested'] < 0.90
    assert out['effectFloorWarning'] is None


def test_unimodal_scores_are_low_confidence():
    scores = [0.48, 0.49, 0.50, 0.51, 0.52, 0.53]
    out = suggest_cue_threshold(scores)
    assert out['confidence'] == 'low'
    assert 'suggested' not in out or out.get('suggested') is None


def test_thin_sample_is_low_confidence():
    out = suggest_cue_threshold([0.95])
    assert out['confidence'] == 'low'


def test_signal_below_effect_floor_warns():
    # Real cluster sits at 0.67-0.73, below the 0.80 effect floor.
    scores = [0.45, 0.48, 0.50, 0.67, 0.70, 0.73]
    out = suggest_cue_threshold(scores, effect_floor=0.80)
    assert out['effectFloorWarning'] == 'signal-below-floor'


def test_live_effect_floor_below_signal_is_clean():
    # Same cluster, but the feed lowered its snap floor to 0.60 -> no warning.
    scores = [0.45, 0.48, 0.50, 0.67, 0.70, 0.73]
    out = suggest_cue_threshold(scores, effect_floor=0.60)
    assert out['effectFloorWarning'] is None


def test_labeled_clean_separation_wins():
    scores = [0.4, 0.45, 0.5, 0.86, 0.9, 0.93]
    labeled = [(0.70, 'rejected'), (0.72, 'rejected'),
               (0.86, 'confirmed'), (0.90, 'confirmed')]
    r = suggest_cue_threshold(scores, labeled_scores=labeled)
    assert r['confidence'] == 'high'
    assert 0.72 < r['suggested'] < 0.86
    assert r['labeledCounts'] == {'confirmed': 2, 'rejected': 2}
    assert r['labeledOverlap'] is False
    assert 'rejected' in r['reason']


def test_labeled_overlap_falls_back_to_unsupervised():
    scores = [0.4, 0.45, 0.5, 0.86, 0.9, 0.93]
    labeled = [(0.88, 'rejected'), (0.85, 'confirmed'), (0.9, 'confirmed')]
    r = suggest_cue_threshold(scores, labeled_scores=labeled)
    assert r['labeledOverlap'] is True
    # unsupervised gap-find still ran; its fields are present
    assert 'scoresN' in r


def test_labeled_below_minimum_ignored():
    scores = [0.4, 0.45, 0.5, 0.86, 0.9, 0.93]
    r_no = suggest_cue_threshold(scores)
    r_few = suggest_cue_threshold(scores, labeled_scores=[(0.7, 'rejected')])
    assert r_few.get('suggested') == r_no.get('suggested')
    assert r_few['labeledCounts'] == {'confirmed': 0, 'rejected': 1}
    assert 'labeledOverlap' not in r_few


def test_rejected_only_raises_floor():
    scores = [0.4, 0.45, 0.5, 0.86, 0.9, 0.93]
    base = suggest_cue_threshold(scores)
    assert base['suggested'] is not None
    labeled = [(base['suggested'] + 0.05, 'rejected')] * 3
    r = suggest_cue_threshold(scores, labeled_scores=labeled)
    assert r['suggested'] > base['suggested']


def test_confirmed_only_caps_suggestion():
    scores = [0.4, 0.45, 0.5, 0.86, 0.9, 0.93]
    base = suggest_cue_threshold(scores)
    labeled = [(base['suggested'] - 0.05, 'confirmed')] * 3
    r = suggest_cue_threshold(scores, labeled_scores=labeled)
    assert r['suggested'] < base['suggested']


def test_labeled_tight_gap_uses_midpoint():
    # MARGIN=0.02; gap 0.865-0.850=0.015 < 2*MARGIN=0.04 triggers midpoint path.
    # midpoint = (0.850+0.865)/2 = 0.8575 -> rounds to 0.86, strictly inside gap.
    scores = [0.4, 0.45, 0.5, 0.86, 0.9, 0.93]
    labeled = (
        [(0.85, 'rejected')] * 3
        + [(0.865, 'confirmed')] * 3
    )
    r = suggest_cue_threshold(scores, labeled_scores=labeled)
    assert r['labeledOverlap'] is False
    assert 0.850 < r['suggested'] < 0.865
