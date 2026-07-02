"""Snap detected ad edges to nearby audio cues (#350).

Many shows play a short ding or stinger right before an ad break starts and
another right when content resumes. When the cue detector flags one within a
small window of an LLM-detected ad's ``start`` or ``end``, we snap the edge
to the matching side of the cue so the cut lands on the chime boundary
rather than a beat into / out of the spoken copy.

Both edges are bounded by ``max_boundary_shift_seconds`` (the same setting
the reviewer pass honors) so a misfiring cue cannot warp the boundary by
more than the user-permitted amount.

Pure function over ad dicts and audio signals; no DB, no LLM, no IO.
"""
from __future__ import annotations

import logging
from typing import Dict, List, Optional

from config import (
    AUDIO_CUE_SNAP_CONFIDENCE,
    AUDIO_CUE_ROLE_DEFAULT,
    AUDIO_CUE_SOURCE_SPECTRAL,
    AUDIO_CUE_START_EDGE_ROLES,
    AUDIO_CUE_END_EDGE_ROLES,
    is_template_cue,
)

logger = logging.getLogger('podcast.claude.cue_snap')


# Fallback; live value from audio_cue_snap_lead_seconds.
DEFAULT_SNAP_LEAD_SECONDS = 10.0
# Fallback; live value from audio_cue_snap_lag_seconds.
DEFAULT_SNAP_LAG_SECONDS = 4.0
# Gap between the cue's end and the snapped ad start. Tiny lead so the cut
# does not slice into the trailing decay of the ding.
SNAP_GAP_SECONDS = 0.05
# Minimum cue confidence to consider for snapping (default; DB-settable via
# audio_cue_snap_confidence, which the caller threads in as min_confidence).
MIN_CUE_CONFIDENCE_FOR_SNAP = AUDIO_CUE_SNAP_CONFIDENCE


def _cue_role(cue) -> str:
    """Matching role carried in a cue's details.

    Template cues carry the role of their type ('start' / 'end' / 'boundary' /
    'non_ad'); spectral fallback cues and any legacy signal default to
    'boundary' so their existing both-edges behavior is preserved.
    """
    return (cue.details or {}).get('role', AUDIO_CUE_ROLE_DEFAULT)


def _snap_record(original: float, proposed: float, cue, n_candidates: int = 1) -> Dict:
    """Build snap audit; sets ambiguous/candidates when 2+ eligible cues."""
    details = cue.details or {}
    rec = {
        'original': round(original, 3),
        'cue_start': round(cue.start, 3),
        'cue_end': round(cue.end, 3),
        'cue_confidence': round(cue.confidence, 3),
        'shift_seconds': round(proposed - original, 3),
        'template_id': details.get('template_id'),
        'label': details.get('label'),
        'source': details.get('source', AUDIO_CUE_SOURCE_SPECTRAL),
    }
    if n_candidates >= 2:
        rec['ambiguous'] = True
        rec['candidates'] = n_candidates
    return rec


def snap_ad_boundaries_to_cues(
    ads: List[Dict],
    audio_analysis_result,
    max_boundary_shift_s: float,
    snap_lead_s: float = DEFAULT_SNAP_LEAD_SECONDS,
    snap_lag_s: float = DEFAULT_SNAP_LAG_SECONDS,
    min_confidence: float = MIN_CUE_CONFIDENCE_FOR_SNAP,
) -> List[Dict]:
    """Return ``ads`` with each ``start`` and ``end`` snapped to a nearby cue.

    Start snap: ad start moves to the cue's *end* + a tiny lead so the cut
    lands just after the stinger finishes.

    End snap: ad end moves to the cue's *start* so the cut lands at the
    moment the resume-content stinger begins (its decay belongs to the
    content side of the break).

    Each shifted ad records the snap in ``ad['cue_snap']`` so the UI / logs
    can show why the boundary moved. A cue used for the start snap of an
    ad is excluded from the end snap of the same ad so the same cue can't
    drag both edges to itself.
    """
    if not ads or not audio_analysis_result:
        return ads
    cues = audio_analysis_result.get_signals_by_type('audio_cue') if audio_analysis_result else []
    if not cues:
        return ads
    # Only template cues may move an ad edge. Spectral-fallback cues (no
    # 'source' key) are too coarse to trust for boundary placement; they stay
    # LLM-prompt evidence only, consistent with cue-pair synthesis.
    cues = [
        c for c in cues
        if c.confidence >= min_confidence and is_template_cue(c.details)
    ]
    if not cues:
        return ads

    for ad in ads:
        try:
            original_start = float(ad['start'])
            original_end = float(ad['end'])
        except (KeyError, TypeError, ValueError):
            continue

        used_cue_ids: set = set()
        snap_record: Dict = {}

        # --- Start edge -------------------------------------------------
        start_cue, start_n = _pick_cue_for_start(
            cues, original_start, original_end, snap_lead_s, snap_lag_s,
        )
        new_start = original_start
        if start_cue is not None:
            proposed_start = start_cue.end + SNAP_GAP_SECONDS
            shift = abs(proposed_start - original_start)
            if (
                proposed_start < original_end
                and shift <= max_boundary_shift_s
                and shift >= 0.01
            ):
                new_start = round(proposed_start, 3)
                snap_record['start'] = _snap_record(
                    original_start, proposed_start, start_cue, n_candidates=start_n)
                used_cue_ids.add(id(start_cue))
                logger.info(
                    f"Cue snap (start): {original_start:.3f}s -> {new_start:.3f}s "
                    f"(delta={new_start - original_start:+.3f}s, "
                    f"cue={snap_record['start'].get('label') or 'spectral'}, "
                    f"conf={start_cue.confidence:.2f})"
                )

        # --- End edge ---------------------------------------------------
        end_cue, end_n = _pick_cue_for_end(
            cues, original_end, new_start, snap_lead_s, snap_lag_s,
            exclude_ids=used_cue_ids,
        )
        new_end = original_end
        if end_cue is not None:
            # End snap lands on the resume-content stinger's START so the
            # break ends just before the stinger plays. Its decay stays
            # with the content side.
            proposed_end = end_cue.start - SNAP_GAP_SECONDS
            shift = abs(proposed_end - original_end)
            if (
                proposed_end > new_start
                and shift <= max_boundary_shift_s
                and shift >= 0.01
            ):
                new_end = round(proposed_end, 3)
                snap_record['end'] = _snap_record(
                    original_end, proposed_end, end_cue, n_candidates=end_n)
                logger.info(
                    f"Cue snap (end): {original_end:.3f}s -> {new_end:.3f}s "
                    f"(delta={new_end - original_end:+.3f}s, "
                    f"cue={snap_record['end'].get('label') or 'spectral'}, "
                    f"conf={end_cue.confidence:.2f})"
                )

        if snap_record:
            ad['start'] = new_start
            ad['end'] = new_end
            ad['cue_snap'] = snap_record

    return ads


def _pick_cue_for_start(
    cues: List, ad_start: float, ad_end: Optional[float],
    snap_lead_s: float, snap_lag_s: float,
):
    """Find the best cue to snap ``ad_start`` to.

    Returns (best_cue, n_eligible) where n_eligible is the count of cues that
    passed the window filter. When n_eligible >= 2 the caller records an
    ambiguity flag.

    Selection key: (-round(abs(distance), 1), confidence) -- nearest-first,
    ties (within 0.1s) broken by confidence. Within the old +/-4s window
    confidence-first was fine; at 10s a farther higher-confidence cue must not
    beat a nearer one (all candidates cleared the 0.80 floor).
    """
    low = ad_start - snap_lead_s
    high = ad_start + snap_lag_s
    eligible = []
    for cue in cues:
        if _cue_role(cue) not in AUDIO_CUE_START_EDGE_ROLES:
            continue
        cue_end = cue.end
        if cue_end < low or cue_end > high:
            continue
        if ad_end is not None and cue_end >= float(ad_end):
            continue
        eligible.append(cue)
    if not eligible:
        return None, 0
    best = max(eligible, key=lambda c: (-round(abs(c.end - ad_start), 1), c.confidence))
    return best, len(eligible)


def _pick_cue_for_end(
    cues: List, ad_end: float, ad_start: float,
    snap_lead_s: float, snap_lag_s: float,
    exclude_ids: set,
):
    """Find the best cue to snap ``ad_end`` to.

    Returns (best_cue, n_eligible) where n_eligible is the count of cues that
    passed the window filter. When n_eligible >= 2 the caller records an
    ambiguity flag.

    A cue whose START sits within ``[ad_end - lag, ad_end + lead]`` is a
    candidate -- the resume-content stinger plays at break boundary so its
    start marks where content returns. Cues already used for the start
    snap of this ad are excluded so the same cue cannot collapse the ad
    to a single instant. Cues whose start is before ``ad_start`` cannot
    bound the end edge.

    Selection key: (-round(abs(distance), 1), confidence) -- nearest-first,
    ties (within 0.1s) broken by confidence. Within the old +/-4s window
    confidence-first was fine; at 10s a farther higher-confidence cue must not
    beat a nearer one (all candidates cleared the 0.80 floor).
    """
    # The end-side stinger can sit a beat before the LLM's end (snap_lag)
    # because the LLM tends to overshoot into post-break silence, or a beat
    # after it (snap_lead) because the LLM sometimes cuts at the last word
    # of the ad copy and the stinger plays a moment later.
    low = ad_end - snap_lag_s
    high = ad_end + snap_lead_s
    eligible = []
    for cue in cues:
        if id(cue) in exclude_ids:
            continue
        if _cue_role(cue) not in AUDIO_CUE_END_EDGE_ROLES:
            continue
        cue_start = cue.start
        if cue_start < low or cue_start > high:
            continue
        if cue_start <= ad_start:
            continue
        eligible.append(cue)
    if not eligible:
        return None, 0
    best = max(eligible, key=lambda c: (-round(abs(c.start - ad_end), 1), c.confidence))
    return best, len(eligible)
