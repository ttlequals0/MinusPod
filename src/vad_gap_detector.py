"""VAD gap detector.

Finds spans of audio that Whisper's VAD dropped (no transcribed segment
covers them). Emits ad markers for gaps that look like ad residue:

- Head gap before segments[0].start (sped-up legal disclaimers, ad tails)
- Mid gap between adjacent segments with signoff-then-resume context or
  when adjacent to an already-detected ad
- Tail gap after segments[-1].end when no postroll marker covers it

Runs after Claude + text-pattern detection, before validation. See the
2.0.7 plan for the full rationale.
"""
from typing import Dict, List, Optional, Tuple
from difflib import SequenceMatcher
import logging
import re

from roll_detector import (
    SIGNOFF_PATTERNS,
    SHOW_START_PATTERNS,
    AD_INDICATOR_PATTERNS,
    _region_covered,
)
from utils.text import get_transcript_text_for_range
from config import VAD_GAP_CONFIDENCE

logger = logging.getLogger(__name__)

_GAP_ADJACENCY_BUFFER = 1.0  # seconds; how close a gap must be to an ad to count as adjacent

# DAI seam check: dynamic insertion can duplicate a few seconds of show
# audio around the splice, so extending an ad across the gap swallows real
# content. Skip the extension when transcript just beyond the boundary
# verbatim-duplicates transcript inside the span. 50 normalized chars
# (~10 words) sits above stock ad boilerplate ("this episode is brought to
# you by" ~33) and below the incident line (~66); a false positive only
# skips an optional extension.
_SEAM_MIN_DUP_CHARS = 50
# Duplicated splice audio sits within seconds of the seam, so compare narrow
# windows: up to 30s of span text before/after the boundary vs 15s beyond it.
_SEAM_INSIDE_WINDOW_SECONDS = 30.0
_SEAM_BEYOND_WINDOW_SECONDS = 15.0


def _ends_with_signoff(text: str) -> bool:
    if not text:
        return False
    tail = text[-200:]
    if any(p.search(tail) for p in SIGNOFF_PATTERNS):
        return True
    return sum(1 for p in AD_INDICATOR_PATTERNS if p.search(tail)) >= 2


def _starts_with_resume(text: str) -> bool:
    if not text:
        return False
    head = text[:200]
    return any(p.search(head) for p in SHOW_START_PATTERNS)


def _adjacent_existing_ad(
    gap_start: float, gap_end: float, ads: List[Dict],
) -> Tuple[Optional[Dict], Optional[str]]:
    for ad in ads:
        ad_start = ad.get('start', 0.0)
        ad_end = ad.get('end', 0.0)
        if abs(ad_end - gap_start) <= _GAP_ADJACENCY_BUFFER:
            return ad, 'before'  # ad ends right before the gap
        if abs(gap_end - ad_start) <= _GAP_ADJACENCY_BUFFER:
            return ad, 'after'  # ad starts right after the gap
    return None, None


def _normalize_seam_text(text: str) -> str:
    """Lowercase, strip punctuation, collapse whitespace for verbatim compare."""
    s = re.sub(r'[^a-z0-9\s]+', ' ', text.lower())
    return re.sub(r'\s+', ' ', s).strip()


def _has_verbatim_duplicate(inside_text: str, beyond_text: str) -> bool:
    a = _normalize_seam_text(inside_text)
    b = _normalize_seam_text(beyond_text)
    if len(a) < _SEAM_MIN_DUP_CHARS or len(b) < _SEAM_MIN_DUP_CHARS:
        return False
    match = SequenceMatcher(None, a, b, autojunk=False).find_longest_match(
        0, len(a), 0, len(b))
    return match.size >= _SEAM_MIN_DUP_CHARS


def _dai_seam_detected(
    segments: List[Dict],
    gap_start: float,
    gap_end: float,
    adjacent: Dict,
    side: str,
) -> bool:
    """True when the proposed gap extension shows a DAI splice duplicate.

    Compares transcript just beyond the proposed extended boundary against
    span text near the seam. The extension region itself is untranscribed
    (the gap) plus at most the ~1s adjacency buffer, so a duplicate's first
    copy always sits at or before the pre-extension boundary. Stopping "at
    the first occurrence" would therefore shrink the LLM-detected boundary,
    which this check must never do -- skipping the extension entirely is the
    only shrink-only option, so the caller skips on True.
    """
    if side == 'before':
        # Extension pushes ad end forward to gap_end. Beyond-boundary text is
        # the resume after the gap; inside text is the span tail before it.
        inside = get_transcript_text_for_range(
            segments,
            max(adjacent.get('start', 0.0), gap_start - _SEAM_INSIDE_WINDOW_SECONDS),
            gap_start,
        )
        beyond = get_transcript_text_for_range(
            segments, gap_end, gap_end + _SEAM_BEYOND_WINDOW_SECONDS)
    else:
        # Extension pulls ad start backward to gap_start. Beyond-boundary text
        # is the show before the gap; inside text is the span head after it.
        inside = get_transcript_text_for_range(
            segments,
            gap_end,
            min(adjacent.get('end', 0.0), gap_end + _SEAM_INSIDE_WINDOW_SECONDS),
        )
        beyond = get_transcript_text_for_range(
            segments, gap_start - _SEAM_BEYOND_WINDOW_SECONDS, gap_start)
    return _has_verbatim_duplicate(inside, beyond)


def _new_marker(start: float, end: float, reason: str) -> Dict:
    return {
        'start': float(start),
        'end': float(end),
        'confidence': VAD_GAP_CONFIDENCE,
        'reason': reason,
        'detection_stage': 'vad_gap',
        'sponsor': None,
    }


def detect_vad_gaps(
    segments: List[Dict],
    existing_ads: List[Dict],
    episode_duration: float,
    start_min_seconds: float = 3.0,
    mid_min_seconds: float = 8.0,
    tail_min_seconds: float = 3.0,
) -> List[Dict]:
    """Return ad markers for suspicious untranscribed audio spans.

    The input `existing_ads` may be mutated: mid-gaps adjacent to a detected
    ad extend that ad's boundary in place instead of emitting a new marker
    (consistent with `ad_detector.extend_ad_boundaries_by_content`).

    Args:
        segments: Whisper segments sorted by start, each with 'start', 'end', 'text'.
        existing_ads: Already-detected ad markers. Checked for overlap; some may be
            extended in place when a gap is adjacent.
        episode_duration: Total audio duration in seconds.
        start_min_seconds: Minimum head-gap duration to emit.
        mid_min_seconds: Minimum mid-gap duration to emit (still needs both signoff-before AND resume-after context).
        tail_min_seconds: Minimum tail-gap duration to emit.

    Returns:
        List of new ad markers. May be empty.
    """
    if not segments:
        return []

    new_markers: List[Dict] = []

    # Head gap
    head_end = segments[0].get('start', 0.0)
    if head_end >= start_min_seconds and not _region_covered(0.0, head_end, existing_ads):
        new_markers.append(_new_marker(
            0.0, head_end,
            f'VAD gap at episode head ({head_end:.1f}s untranscribed)',
        ))
        logger.info(f"VAD head gap: 0.0s-{head_end:.1f}s")

    # Mid gaps
    for i in range(len(segments) - 1):
        gap_start = segments[i].get('end', 0.0)
        gap_end = segments[i + 1].get('start', 0.0)
        gap_duration = gap_end - gap_start
        if gap_duration < mid_min_seconds:
            continue
        if _region_covered(gap_start, gap_end, existing_ads):
            continue

        adjacent, side = _adjacent_existing_ad(gap_start, gap_end, existing_ads)
        if adjacent is not None:
            if _dai_seam_detected(segments, gap_start, gap_end, adjacent, side):
                logger.info(
                    f"VAD mid gap {gap_start:.1f}-{gap_end:.1f}s: DAI seam "
                    f"duplicate beyond boundary; not extending ad "
                    f"{adjacent.get('start', 0.0):.1f}-{adjacent.get('end', 0.0):.1f}s"
                )
                continue
            old_start = adjacent.get('start', 0.0)
            old_end = adjacent.get('end', 0.0)
            adjacent['start'] = min(old_start, gap_start)
            adjacent['end'] = max(old_end, gap_end)
            adjacent['vad_gap_extended'] = True
            logger.info(
                f"VAD mid gap merged into adjacent ad: "
                f"{old_start:.1f}-{old_end:.1f}s -> "
                f"{adjacent['start']:.1f}-{adjacent['end']:.1f}s"
            )
            continue

        before_text = segments[i].get('text', '')
        after_text = segments[i + 1].get('text', '')
        # Require BOTH signoff before the gap AND resume after it. Either alone
        # produces false positives on conversational shows where filler phrases
        # like "thanks for tuning in" or "welcome back" appear in normal speech.
        if _ends_with_signoff(before_text) and _starts_with_resume(after_text):
            new_markers.append(_new_marker(
                gap_start, gap_end,
                'VAD gap with signoff and resume context',
            ))
            logger.info(f"VAD mid gap: {gap_start:.1f}s-{gap_end:.1f}s")

    # Tail gap
    if episode_duration > 0:
        tail_start = segments[-1].get('end', 0.0)
        tail_span = episode_duration - tail_start
        if tail_span >= tail_min_seconds and not _region_covered(tail_start, episode_duration, existing_ads):
            new_markers.append(_new_marker(
                tail_start, episode_duration,
                f'VAD gap at episode tail ({tail_span:.1f}s untranscribed)',
            ))
            logger.info(f"VAD tail gap: {tail_start:.1f}s-{episode_duration:.1f}s")

    return new_markers
