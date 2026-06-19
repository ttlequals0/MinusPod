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

from config import AUDIO_CUE_SNAP_CONFIDENCE

logger = logging.getLogger('podcast.claude.cue_snap')


# How far back from the ad start the cue is allowed to sit. Stingers usually
# land 0-3s before the spoken copy.
DEFAULT_SNAP_LEAD_SECONDS = 4.0
# How far past the ad start the cue is allowed to sit. Detection latency
# (whisper segment alignment + first-pass window edge) puts the LLM's start
# slightly after the cue some of the time, so we allow a small overshoot.
DEFAULT_SNAP_LAG_SECONDS = 2.0
# Gap between the cue's end and the snapped ad start. Tiny lead so the cut
# does not slice into the trailing decay of the ding.
SNAP_GAP_SECONDS = 0.05
# Minimum cue confidence to consider for snapping (default; DB-settable via
# audio_cue_snap_confidence, which the caller threads in as min_confidence).
MIN_CUE_CONFIDENCE_FOR_SNAP = AUDIO_CUE_SNAP_CONFIDENCE


def _snap_record(original: float, proposed: float, cue) -> Dict:
    """Build the per-edge snap audit record shared by the start and end edges."""
    details = cue.details or {}
    return {
        'original': round(original, 3),
        'cue_start': round(cue.start, 3),
        'cue_end': round(cue.end, 3),
        'cue_confidence': round(cue.confidence, 3),
        'shift_seconds': round(proposed - original, 3),
        'template_id': details.get('template_id'),
        'label': details.get('label'),
        'source': details.get('source', 'spectral'),
    }


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
    cues = [c for c in cues if c.confidence >= min_confidence]
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
        start_cue = _pick_cue_for_start(
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
                snap_record['start'] = _snap_record(original_start, proposed_start, start_cue)
                used_cue_ids.add(id(start_cue))
                logger.info(
                    f"Cue snap (start): {original_start:.3f}s -> {new_start:.3f}s "
                    f"(delta={new_start - original_start:+.3f}s, "
                    f"cue={snap_record['start'].get('label') or 'spectral'}, "
                    f"conf={start_cue.confidence:.2f})"
                )

        # --- End edge ---------------------------------------------------
        end_cue = _pick_cue_for_end(
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
                snap_record['end'] = _snap_record(original_end, proposed_end, end_cue)
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

    Best = highest confidence within the search window whose end is not past
    the ad's end. Ties broken by proximity to ``ad_start``.
    """
    low = ad_start - snap_lead_s
    high = ad_start + snap_lag_s
    best = None
    best_key = None
    for cue in cues:
        cue_end = cue.end
        if cue_end < low or cue_end > high:
            continue
        if ad_end is not None and cue_end >= float(ad_end):
            continue
        key = (cue.confidence, -abs(cue_end - ad_start))
        if best_key is None or key > best_key:
            best = cue
            best_key = key
    return best


def _pick_cue_for_end(
    cues: List, ad_end: float, ad_start: float,
    snap_lead_s: float, snap_lag_s: float,
    exclude_ids: set,
):
    """Find the best cue to snap ``ad_end`` to.

    A cue whose START sits within ``[ad_end - lag, ad_end + lead]`` is a
    candidate -- the resume-content stinger plays at break boundary so its
    start marks where content returns. Cues already used for the start
    snap of this ad are excluded so the same cue cannot collapse the ad
    to a single instant. Cues whose start is before ``ad_start`` cannot
    bound the end edge.
    """
    # The end-side stinger can sit a beat before the LLM's end (snap_lag)
    # because the LLM tends to overshoot into post-break silence, or a beat
    # after it (snap_lead) because the LLM sometimes cuts at the last word
    # of the ad copy and the stinger plays a moment later.
    low = ad_end - snap_lag_s
    high = ad_end + snap_lead_s
    best = None
    best_key = None
    for cue in cues:
        if id(cue) in exclude_ids:
            continue
        cue_start = cue.start
        if cue_start < low or cue_start > high:
            continue
        if cue_start <= ad_start:
            continue
        key = (cue.confidence, -abs(cue_start - ad_end))
        if best_key is None or key > best_key:
            best = cue
            best_key = key
    return best
