"""Auto-suggest a cue match threshold from a distribution of occurrence scores.

The matcher's ZNCC score is the stored cue confidence. Real occurrences of a
clean cue cluster high (~0.85-0.99); the noise ceiling sits ~0.50. When the two
clusters are cleanly separated this proposes a value in the gap. Pure function;
no IO, unit-testable in isolation.
"""
from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from config import (
    AUDIO_CUE_SUGGEST_MIN_GAP,
    AUDIO_CUE_SUGGEST_MIN_LABELED,
    AUDIO_CUE_SUGGEST_MIN_SIGNAL,
    AUDIO_CUE_SUGGEST_BAND,
    AUDIO_CUE_SUGGEST_MARGIN,
    AUDIO_CUE_EFFECT_FLOOR,
)


def _unsupervised_suggest(
    occurrence_scores: List[float],
    effect_floor: float,
) -> Dict:
    """Propose a global match threshold from a list of per-occurrence scores.

    Returns a dict; on a clean bimodal distribution it carries a numeric
    ``suggested`` plus cluster stats and an ``effectFloorWarning``; otherwise a
    low-confidence result with a ``reason``.
    """
    scores = sorted(float(s) for s in occurrence_scores)
    if len(scores) < AUDIO_CUE_SUGGEST_MIN_SIGNAL:
        return {
            'confidence': 'low',
            'suggested': None,
            'reason': 'not enough cue occurrences across the sampled episodes; '
                      'mark the cue on more episodes or scan more',
            'scoresN': len(scores),
        }

    lo_band, hi_band = AUDIO_CUE_SUGGEST_BAND
    # Widest consecutive gap whose lower edge sits in the plausible band.
    best_gap = 0.0
    best_i = -1
    for i in range(len(scores) - 1):
        lower = scores[i]
        if lower < lo_band or lower > hi_band:
            continue
        gap = scores[i + 1] - lower
        if gap > best_gap:
            best_gap = gap
            best_i = i

    if best_i < 0 or best_gap < AUDIO_CUE_SUGGEST_MIN_GAP:
        return {
            'confidence': 'low',
            'suggested': None,
            'reason': 'no clear separation between noise and signal; keep the '
                      'default or re-capture the cue',
            'scoresN': len(scores),
        }

    noise_ceiling = scores[best_i]
    signal_floor = scores[best_i + 1]
    signal_count = sum(1 for s in scores if s >= signal_floor)
    if signal_count < AUDIO_CUE_SUGGEST_MIN_SIGNAL:
        return {
            'confidence': 'low',
            'suggested': None,
            'reason': 'the high-scoring cluster is too small to trust',
            'noiseCeiling': round(noise_ceiling, 3),
            'signalFloor': round(signal_floor, 3),
            'gapWidth': round(best_gap, 3),
            'scoresN': len(scores),
        }

    midpoint = (noise_ceiling + signal_floor) / 2
    suggested = min(
        max(midpoint, noise_ceiling + AUDIO_CUE_SUGGEST_MARGIN),
        signal_floor - AUDIO_CUE_SUGGEST_MARGIN,
    )
    suggested = round(min(max(suggested, 0.0), 0.99), 2)

    if signal_floor < effect_floor:
        warning = 'signal-below-floor'
        confidence = 'partial'
    else:
        warning = None
        confidence = 'high'

    return {
        'confidence': confidence,
        'suggested': suggested,
        'noiseCeiling': round(noise_ceiling, 3),
        'signalFloor': round(signal_floor, 3),
        'gapWidth': round(best_gap, 3),
        'signalCount': signal_count,
        'effectFloor': round(effect_floor, 3),
        'effectFloorWarning': warning,
        'scoresN': len(scores),
    }


def suggest_cue_threshold(
    occurrence_scores: List[float],
    effect_floor: float = AUDIO_CUE_EFFECT_FLOOR,
    labeled_scores: Optional[List[Tuple[float, str]]] = None,
) -> Dict:
    """Propose a global match threshold; verdict labels sharpen it when present.

    Rejected verdicts are known false positives above the current threshold,
    confirmed ones are known true matches; with enough of both and a clean
    gap between them the labeled placement replaces the unsupervised
    gap-find. One-sided labels only nudge the unsupervised result.
    """
    result = _unsupervised_suggest(occurrence_scores, effect_floor)
    rejected = sorted(
        float(s) for s, v in (labeled_scores or []) if v == 'rejected')
    confirmed = sorted(
        float(s) for s, v in (labeled_scores or []) if v == 'confirmed')
    n_labeled = len(rejected) + len(confirmed)
    if n_labeled:
        result['labeledCounts'] = {
            'confirmed': len(confirmed), 'rejected': len(rejected)}
    if n_labeled < AUDIO_CUE_SUGGEST_MIN_LABELED:
        return result

    if rejected and confirmed:
        if rejected[-1] >= confirmed[0]:
            result['labeledOverlap'] = True
            return result
        midpoint = (rejected[-1] + confirmed[0]) / 2
        # When the labeled gap is narrower than 2x MARGIN the clamp pair can
        # place the suggestion at or below rejected[-1]; use the plain midpoint.
        if confirmed[0] - rejected[-1] < 2 * AUDIO_CUE_SUGGEST_MARGIN:
            suggested = midpoint
        else:
            suggested = min(
                max(midpoint, rejected[-1] + AUDIO_CUE_SUGGEST_MARGIN),
                confirmed[0] - AUDIO_CUE_SUGGEST_MARGIN,
            )
        result.update({
            'confidence': 'partial' if confirmed[0] < effect_floor else 'high',
            'suggested': round(min(max(suggested, 0.0), 0.99), 2),
            'labeledOverlap': False,
            'noiseCeiling': round(rejected[-1], 3),
            'signalFloor': round(confirmed[0], 3),
            'effectFloor': round(effect_floor, 3),
            'effectFloorWarning': (
                'signal-below-floor' if confirmed[0] < effect_floor else None),
            'reason': (
                f"{len(rejected)} rejected match(es) score at or below "
                f"{rejected[-1]:.2f}; {len(confirmed)} confirmed at or above "
                f"{confirmed[0]:.2f}"),
        })
        return result

    # One-sided labels: nudge, never invent, a suggestion.
    if result['suggested'] is None:
        return result
    if rejected and result['suggested'] <= rejected[-1]:
        result['suggested'] = round(
            min(rejected[-1] + AUDIO_CUE_SUGGEST_MARGIN, 0.99), 2)
        result['reason'] = (
            f"raised above {len(rejected)} rejected match(es) "
            f"(max rejected score {rejected[-1]:.2f})")
    elif confirmed and result['suggested'] >= confirmed[0]:
        result['suggested'] = round(
            max(confirmed[0] - AUDIO_CUE_SUGGEST_MARGIN, 0.0), 2)
        result['reason'] = (
            f"capped below the lowest confirmed match ({confirmed[0]:.2f}) "
            f"so confirmed cues keep matching")
    return result
