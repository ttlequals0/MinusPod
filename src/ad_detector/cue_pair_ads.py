"""Synthesize ad spans from unmatched cue pairs (#350).

Some shows bracket every ad break with a stinger sound. When the LLM misses
a break entirely (no spoken sponsor copy is detected, or the model bails on
that window), the audio cue matcher still flags both stingers. This pass
turns *consecutive* high-confidence cues that no LLM ad covers into a
synthetic ad span, provided the gap between them matches a plausible break
duration.

This breaks the original "cue is supporting evidence only" contract; that is
why it is an opt-in setting (``audio_cue_create_from_pairs``). The contract
when the setting is off is unchanged. When on, the synthesised ads carry a
distinct ``reason`` and ``detection_stage`` so they show up in the ad
editor as cue-only detections that the user can confirm or reject before
they harden into patterns.
"""
from __future__ import annotations

import bisect
import logging
from dataclasses import dataclass
from typing import Dict, List, Optional

from config import (
    AUDIO_CUE_PAIR_CONFIDENCE,
    AUDIO_CUE_PAIR_MIN_BREAK_SECONDS,
    AUDIO_CUE_PAIR_MAX_BREAK_SECONDS,
    AUDIO_CUE_PAIR_MAX_BREAK_FRACTION,
    AUDIO_CUE_PAIR_ORIENT_WINDOW_SECONDS,
    AUDIO_CUE_ROLE_DEFAULT,
    AUDIO_CUE_ROLE_END,
    AUDIO_CUE_START_EDGE_ROLES,
    AUDIO_CUE_END_EDGE_ROLES,
    is_template_cue,
)
from ad_detector.cue_telemetry import cue_key as _diag_key

# Skip-diagnostics reasons (#350 Phase 6). Keyed by (template_id, round(start,3))
# so an eligible-but-unpaired cue's telemetry can explain why no ad formed.
SKIP_BELOW_CONFIDENCE = 'below_pair_confidence'
SKIP_COVERED = 'covered_by_existing_ad'
SKIP_NO_PARTNER = 'no_partner_in_band'
SKIP_PHASE_MISMATCH = 'phase_mismatch'


logger = logging.getLogger('podcast.claude.cue_pair')


# Defaults (DB-settable; the caller threads the live values in). Minimum
# confidence is tighter than the snap threshold because synthesis *creates*
# ads rather than just refining them. The break-duration band rejects pairs
# too close (intro flourish / double-tap stinger) or too far apart (two
# unrelated breaks rather than one bracketing pair).
DEFAULT_MIN_PAIR_CONFIDENCE = AUDIO_CUE_PAIR_CONFIDENCE
DEFAULT_MIN_BREAK_S = AUDIO_CUE_PAIR_MIN_BREAK_SECONDS
DEFAULT_MAX_BREAK_S = AUDIO_CUE_PAIR_MAX_BREAK_SECONDS
DEFAULT_MAX_BREAK_FRACTION = AUDIO_CUE_PAIR_MAX_BREAK_FRACTION
DEFAULT_ORIENT_WINDOW_S = AUDIO_CUE_PAIR_ORIENT_WINDOW_SECONDS
# An LLM-detected ad that overlaps a cue pair by this many seconds (on
# either side) is treated as "already covers it" and the pair is skipped.
OVERLAP_TOLERANCE_S = 5.0

# A pair spans an opener cue -> a later closer cue. A 'boundary' cue (and the
# role-less spectral fallback) can play either part, so the all-boundary case
# behaves exactly as before; a 'start'-typed cue can only open and an 'end'
# can only close, which stops two break-entry stingers from pairing into a
# span that covers the show content between two separate breaks. 'non_ad'
# (intro/outro) cues match neither set, so they are never opener or closer.


@dataclass
class _Cue:
    start: float
    end: float
    confidence: float
    label: Optional[str]
    template_id: Optional[int]
    role: str
    effective_role: str = ''

    def __post_init__(self):
        if not self.effective_role:
            self.effective_role = self.role


def _ad_edges_ok(a):
    try:
        float(a['start'])
        float(a['end'])
        return True
    except (KeyError, TypeError, ValueError):
        return False


def _is_exit_like(cue, idx, elig, ad_ends, window_s):
    """True if `cue` is the first edge-eligible cue after an LLM ad END, within
    the window -- i.e. it plays where content resumes after a detected break."""
    if not ad_ends:
        return False
    i = bisect.bisect_right(ad_ends, cue.start) - 1
    if i < 0:
        return False
    e = ad_ends[i]
    if cue.start - e > window_s:
        return False
    if idx > 0 and elig[idx - 1].start > e:
        return False  # an earlier eligible cue already followed this ad end
    return True


def _is_entry_like(cue, idx, elig, ad_starts, window_s):
    """True if `cue` is the last edge-eligible cue before an LLM ad START, within
    the window -- i.e. it plays where a detected break begins."""
    if not ad_starts:
        return False
    i = bisect.bisect_left(ad_starts, cue.end)
    if i >= len(ad_starts):
        return False
    s = ad_starts[i]
    if s - cue.end > window_s:
        return False
    if idx + 1 < len(elig) and elig[idx + 1].start < s:
        return False  # a later eligible cue precedes this ad start
    return True


def _orient_cues(cues, ads, window_s):
    """Pin the opening phase of a both-ends boundary cue so a leading unpaired
    exit cue cannot open a pair that spans show content.

    A feed whose single cue brackets both ends of every ad break, but whose
    opening ad has no entry cue, yields an ordered sequence exit, entry, exit,
    entry ...; greedy pairing would pair the leading exit with the first entry
    over CONTENT. Using the first-pass LLM ad edges, the leading exit cues
    (edge-eligible cues before the first ad-entry cue, sitting just after an LLM
    ad end) are marked closer-only, so pairing starts at the first real entry.

    Only the leading run is touched: a mid-episode cue is never demoted, so a
    genuinely missed break bracketed by two boundary cues still synthesizes. A
    missed break in the very first (pre-entry) position is indistinguishable from
    a content span there, so orientation favors leaving that ad over cutting into
    content -- the safer direction. Demotes only on positive LLM evidence; feeds
    with no LLM ads, or window_s == 0, behave exactly as before."""
    if window_s <= 0 or not ads:
        return
    ad_starts = sorted(float(a['start']) for a in ads if _ad_edges_ok(a))
    ad_ends = sorted(float(a['end']) for a in ads if _ad_edges_ok(a))
    # Both lists come from the same _ad_edges_ok filter, so they are empty
    # together; checking one is enough.
    if not ad_starts:
        return
    # Edge-eligible cues only: non_ad (intro/outro/content_transition) cues never
    # open or close and must not pollute the first-after / last-before adjacency.
    elig = [c for c in cues
            if c.role in AUDIO_CUE_START_EDGE_ROLES
            or c.role in AUDIO_CUE_END_EDGE_ROLES]
    # The first ad-entry cue: greedy pairing is already correctly phased from
    # there on, so only cues before it can be a leading unpaired exit. Limiting
    # orientation to that leading run keeps it from demoting a mid-episode cue
    # and stranding a genuinely missed break bracketed by two boundary cues.
    first_entry = next(
        (i for i, c in enumerate(elig)
         if _is_entry_like(c, i, elig, ad_starts, window_s)),
        None,
    )
    if first_entry is None:
        return
    for i in range(first_entry):
        c = elig[i]
        if c.role == AUDIO_CUE_ROLE_DEFAULT and _is_exit_like(c, i, elig, ad_ends, window_s):
            c.effective_role = AUDIO_CUE_ROLE_END


def synthesize_ads_from_cue_pairs(
    ads: List[Dict],
    audio_analysis_result,
    min_confidence: float = DEFAULT_MIN_PAIR_CONFIDENCE,
    min_break_s: float = DEFAULT_MIN_BREAK_S,
    max_break_s: float = DEFAULT_MAX_BREAK_S,
    total_duration: float = 0.0,
    max_break_fraction: float = DEFAULT_MAX_BREAK_FRACTION,
    orient_window_s: float = DEFAULT_ORIENT_WINDOW_S,
):
    """Return ``(ads, skip_diagnostics)``.

    ``ads`` is the input list plus any synthesised cue-pair ads in chronological
    order (input not mutated). ``skip_diagnostics`` maps
    ``(template_id, round(start, 3))`` -> reason for every eligible template cue
    that did NOT become part of a synthesised pair, so per-cue telemetry can
    explain why (#350 Phase 6).

    Args:
        ads: First-pass ad list (may be empty).
        audio_analysis_result: ``AudioAnalysisResult`` from the analyzer,
            or ``None``.
        min_confidence: Drop any cue weaker than this before pairing.
        min_break_s: Minimum gap (cue1.end -> cue2.start) for a pair to
            qualify.
        max_break_s: Maximum gap; pairs beyond this are skipped because the
            two cues are more likely two separate boundaries.
        total_duration: Episode duration (s); 0 disables the fraction guard.
        max_break_fraction: Reject a pair spanning more than this fraction of
            ``total_duration`` -- a short-episode phantom-ad backstop.
    """
    skip_diagnostics: Dict = {}
    if not audio_analysis_result:
        return list(ads), skip_diagnostics
    # On a short episode the absolute max_break_s cap can pass a pair that
    # brackets most of the show. Tighten the cap to a fraction of the episode.
    effective_max_break = max_break_s
    if total_duration > 0 and max_break_fraction > 0:
        effective_max_break = min(max_break_s, max_break_fraction * total_duration)
    raw_cues = audio_analysis_result.get_signals_by_type('audio_cue')
    # Template cues below the pair-confidence floor are excluded from pairing but
    # still recorded so telemetry can attribute an unused cue to a low score.
    for c in raw_cues:
        if is_template_cue(c.details) and c.confidence < min_confidence:
            skip_diagnostics[_diag_key((c.details or {}).get('template_id'), c.start)] = \
                SKIP_BELOW_CONFIDENCE
    # Only precise template cues may *create* ads. Spectral-fallback cues (no
    # 'source' key) are too coarse to synthesize from: on a no-template feed a
    # dense burst section pairs them into dozens of overlapping false ads. They
    # still inform the LLM prompt as supporting evidence; they just cannot mint
    # an ad on their own. The opener/closer role checks below additionally
    # exclude 'non_ad' (intro/outro) cues.
    cues = sorted(
        (_Cue(
            start=float(c.start),
            end=float(c.end),
            confidence=float(c.confidence),
            label=(c.details or {}).get('label'),
            template_id=(c.details or {}).get('template_id'),
            role=(c.details or {}).get('role', AUDIO_CUE_ROLE_DEFAULT),
        ) for c in raw_cues
        if c.confidence >= min_confidence and is_template_cue(c.details)),
        key=lambda x: x.start,
    )
    if len(cues) < 2:
        # A lone eligible cue can never pair: no partner exists at all.
        for c in cues:
            skip_diagnostics[_diag_key(c.template_id, c.start)] = SKIP_NO_PARTNER
        return list(ads), skip_diagnostics

    _orient_cues(cues, ads, orient_window_s)

    # Greedy left-to-right pairing: each cue starts a candidate break with
    # the next cue inside the duration band. Once a pair is formed, both
    # cues are consumed so we do not chain a third cue onto the same break.
    # Per-cue reasons accumulate for every cue that does not end up in a pair.
    new_ads = list(ads)
    consumed = [False] * len(cues)
    reasons: List = [None] * len(cues)
    for i in range(len(cues) - 1):
        if consumed[i]:
            continue
        cue_a = cues[i]
        if cue_a.effective_role not in AUDIO_CUE_START_EDGE_ROLES:
            # Cannot open a pair (non_ad, or demoted closer-only): phase issue.
            reasons[i] = SKIP_PHASE_MISMATCH
            continue
        found_partner_in_band = False
        for j in range(i + 1, len(cues)):
            if consumed[j]:
                continue
            cue_b = cues[j]
            if cue_b.effective_role not in AUDIO_CUE_END_EDGE_ROLES:
                continue
            gap = cue_b.start - cue_a.end
            if gap < min_break_s:
                # Too close: probably the same boundary's stinger reflected
                # back; skip cue_b for *this* cue_a and try the next one.
                continue
            if gap > effective_max_break:
                # No further cue within range (or the span would cover too much
                # of a short episode); stop pairing for cue_a.
                break
            found_partner_in_band = True
            synth_start = round(cue_a.end + 0.05, 3)
            synth_end = round(cue_b.start - 0.05, 3)
            if _covered_by_existing_ad(new_ads, synth_start, synth_end):
                reasons[i] = SKIP_COVERED
                reasons[j] = SKIP_COVERED
                consumed[i] = True
                consumed[j] = True
                break
            duration = synth_end - synth_start
            if duration < min_break_s - 1.0:
                # Defensive: belt-and-suspenders against round-off after
                # the lead/lag gaps trim the span below the floor.
                reasons[i] = SKIP_NO_PARTNER
                reasons[j] = SKIP_NO_PARTNER
                consumed[i] = True
                consumed[j] = True
                break
            confidence = round(min(cue_a.confidence, cue_b.confidence), 3)
            ad = {
                'start': synth_start,
                'end': synth_end,
                'confidence': confidence,
                'reason': 'audio_cue_pair',
                'detection_stage': 'cue_pair',
                'cue_pair': {
                    'start': {
                        'cue_start': round(cue_a.start, 3),
                        'cue_end': round(cue_a.end, 3),
                        'confidence': round(cue_a.confidence, 3),
                        'template_id': cue_a.template_id,
                        'label': cue_a.label,
                    },
                    'end': {
                        'cue_start': round(cue_b.start, 3),
                        'cue_end': round(cue_b.end, 3),
                        'confidence': round(cue_b.confidence, 3),
                        'template_id': cue_b.template_id,
                        'label': cue_b.label,
                    },
                },
            }
            new_ads.append(ad)
            logger.info(
                f"Cue pair ad synthesised: {synth_start:.1f}s-{synth_end:.1f}s "
                f"({duration:.1f}s) from cues {cue_a.label!r}@{cue_a.start:.1f} "
                f"+ {cue_b.label!r}@{cue_b.start:.1f} (conf={confidence:.2f})"
            )
            # Paired cleanly: no skip reason for either cue.
            reasons[i] = None
            reasons[j] = None
            consumed[i] = True
            consumed[j] = True
            break
        # cue_a opened but no in-band partner paired with it (every closer was
        # too near, too far, or already consumed). A covered / undersized pair
        # took the break path above and set its own reason, so this only fires
        # on a true band miss.
        if not consumed[i] and not found_partner_in_band and reasons[i] is None:
            reasons[i] = SKIP_NO_PARTNER

    # A trailing eligible cue (index len-1) is never an opener in the loop above,
    # and any opener that fell through without a partner is already handled. Mark
    # the remaining unconsumed, unclassified cues by role: any cue that is start-
    # or end-capable in context simply found no partner (no_partner); only a cue
    # that can play neither part is a phase mismatch.
    for idx, c in enumerate(cues):
        if consumed[idx] or reasons[idx] is not None:
            continue
        if (c.effective_role in AUDIO_CUE_START_EDGE_ROLES
                or c.effective_role in AUDIO_CUE_END_EDGE_ROLES):
            reasons[idx] = SKIP_NO_PARTNER
        else:
            reasons[idx] = SKIP_PHASE_MISMATCH

    for idx, c in enumerate(cues):
        if reasons[idx] is not None:
            skip_diagnostics[_diag_key(c.template_id, c.start)] = reasons[idx]

    new_ads.sort(key=lambda a: a.get('start', 0.0))
    return new_ads, skip_diagnostics


def _covered_by_existing_ad(ads: List[Dict], start: float, end: float) -> bool:
    """True iff an existing ad overlaps ``[start, end]`` within the tolerance.

    ``ads`` is the growing list (input LLM ads plus already-synthesized spans),
    so this both skips pairs an LLM ad already covers and prevents a clustered
    or duplicated cue list from minting overlapping synthetic ads.
    """
    for ad in ads:
        try:
            a_start = float(ad['start'])
            a_end = float(ad['end'])
        except (KeyError, TypeError, ValueError):
            continue
        if a_end + OVERLAP_TOLERANCE_S < start:
            continue
        if a_start - OVERLAP_TOLERANCE_S > end:
            continue
        return True
    return False
