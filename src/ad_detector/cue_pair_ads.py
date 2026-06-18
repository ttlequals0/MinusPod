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

import logging
from dataclasses import dataclass
from typing import Dict, List, Optional

logger = logging.getLogger('podcast.claude.cue_pair')


# Minimum confidence a cue must carry to participate in pair-based ad
# synthesis. Tighter than the snap threshold (0.80) because synthesis
# *creates* ads rather than just refining them.
DEFAULT_MIN_PAIR_CONFIDENCE = 0.85
# Plausible break-duration band: a cue pair separated by less than the
# minimum is more likely an intro flourish or a double-tap stinger, and a
# pair separated by more than the maximum is more likely two unrelated ad
# breaks back-to-back rather than one bracketing pair.
DEFAULT_MIN_BREAK_S = 30.0
DEFAULT_MAX_BREAK_S = 480.0
# An LLM-detected ad that overlaps a cue pair by this many seconds (on
# either side) is treated as "already covers it" and the pair is skipped.
OVERLAP_TOLERANCE_S = 5.0


@dataclass
class _Cue:
    start: float
    end: float
    confidence: float
    label: Optional[str]
    template_id: Optional[int]


def synthesize_ads_from_cue_pairs(
    ads: List[Dict],
    audio_analysis_result,
    min_confidence: float = DEFAULT_MIN_PAIR_CONFIDENCE,
    min_break_s: float = DEFAULT_MIN_BREAK_S,
    max_break_s: float = DEFAULT_MAX_BREAK_S,
) -> List[Dict]:
    """Return ``ads`` with cue-pair-derived synthetic entries appended.

    Args:
        ads: First-pass ad list (may be empty). Not mutated; the returned
            list is the input + any synthesised ads in chronological order.
        audio_analysis_result: ``AudioAnalysisResult`` from the analyzer,
            or ``None``.
        min_confidence: Drop any cue weaker than this before pairing.
        min_break_s: Minimum gap (cue1.end -> cue2.start) for a pair to
            qualify.
        max_break_s: Maximum gap; pairs beyond this are skipped because the
            two cues are more likely two separate boundaries.
    """
    if not audio_analysis_result:
        return list(ads)
    raw_cues = audio_analysis_result.get_signals_by_type('audio_cue')
    cues = sorted(
        (_Cue(
            start=float(c.start),
            end=float(c.end),
            confidence=float(c.confidence),
            label=(c.details or {}).get('label'),
            template_id=(c.details or {}).get('template_id'),
        ) for c in raw_cues if c.confidence >= min_confidence),
        key=lambda x: x.start,
    )
    if len(cues) < 2:
        return list(ads)

    # Greedy left-to-right pairing: each cue starts a candidate break with
    # the next cue inside the duration band. Once a pair is formed, both
    # cues are consumed so we do not chain a third cue onto the same break.
    new_ads = list(ads)
    consumed = [False] * len(cues)
    for i in range(len(cues) - 1):
        if consumed[i]:
            continue
        cue_a = cues[i]
        for j in range(i + 1, len(cues)):
            if consumed[j]:
                continue
            cue_b = cues[j]
            gap = cue_b.start - cue_a.end
            if gap < min_break_s:
                # Too close: probably the same boundary's stinger reflected
                # back; skip cue_b for *this* cue_a and try the next one.
                continue
            if gap > max_break_s:
                # No further cue within range; stop pairing for cue_a.
                break
            synth_start = round(cue_a.end + 0.05, 3)
            synth_end = round(cue_b.start - 0.05, 3)
            if _covered_by_existing_ad(ads, synth_start, synth_end):
                consumed[i] = True
                consumed[j] = True
                break
            duration = synth_end - synth_start
            if duration < min_break_s - 1.0:
                # Defensive: belt-and-suspenders against round-off after
                # the lead/lag gaps trim the span below the floor.
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
            consumed[i] = True
            consumed[j] = True
            break

    new_ads.sort(key=lambda a: a.get('start', 0.0))
    return new_ads


def _covered_by_existing_ad(ads: List[Dict], start: float, end: float) -> bool:
    """True iff an existing LLM ad overlaps ``[start, end]`` within the tolerance."""
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
