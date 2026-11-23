"""VAD gap detector.

Finds spans of audio that Whisper's VAD dropped (no transcribed segment
covers them). Emits ad markers for gaps that look like ad residue:

- Head gap before segments[0].start (sped-up legal disclaimers, ad tails)
- Mid gap between adjacent segments with signoff-then-resume context or
  when adjacent to an already-detected ad
- Tail gap after segments[-1].end when no postroll marker covers it

A head or tail gap that the feed's own show intro/outro template cue covers is show
audio, not ad residue: that part of the marker carries the intro/outro category, so the
feed's segment action decides it instead of the uncorroborated-tail review hold.

Runs after LLM + text-pattern detection, before validation.
"""
from bisect import bisect_left
from difflib import SequenceMatcher
import logging
import re

from roll_detector import (
    SIGNOFF_PATTERNS,
    SHOW_START_PATTERNS,
    AD_INDICATOR_PATTERNS,
    _region_covered,
)
from utils.text import get_transcript_text_for_range, transcript_gaps
from config import (
    AUDIO_CUE_TYPE_SHOW_INTRO, AUDIO_CUE_TYPE_SHOW_OUTRO, CUE_CUT_CATEGORY,
    HOLD_REASON_LARGE_VAD_GAP, MAX_ADJACENT_AUTO_EXTENSION_SECONDS, VAD_GAP_CONFIDENCE,
    is_template_cue,
)

logger = logging.getLogger(__name__)

_GAP_ADJACENCY_BUFFER = 1.0  # seconds; how close a gap must be to an ad to count as adjacent

# DAI seam check: dynamic insertion can duplicate a few seconds of show audio around the
# splice, so extending across the gap swallows real content. Skip the extension when
# transcript just beyond the boundary verbatim-duplicates transcript inside the span.
# 50 normalized chars (~10 words) is above stock boilerplate ("this episode is brought
# to you by" ~33); a false positive only skips an optional extension.
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
    gap_start: float, gap_end: float, ads: list[dict],
) -> tuple[dict | None, str | None]:
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
    segments: list[dict],
    gap_start: float,
    gap_end: float,
    adjacent: dict,
    side: str,
) -> bool:
    """True when the proposed gap extension shows a DAI splice duplicate.

    Compares transcript beyond the extended boundary with span text near the seam. The
    extension region is untranscribed (plus at most the ~1s buffer), so a duplicate's first
    copy sits at or before the old boundary; stopping at it would shrink the LLM boundary,
    which this check must never do. Skipping the extension is the only shrink-free option.
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


def _new_marker(start: float, end: float, reason: str) -> dict:
    return {
        'start': float(start),
        'end': float(end),
        'confidence': VAD_GAP_CONFIDENCE,
        'reason': reason,
        'detection_stage': 'vad_gap',
        'sponsor': None,
    }


_SHOW_CUE_TYPES = (AUDIO_CUE_TYPE_SHOW_INTRO, AUDIO_CUE_TYPE_SHOW_OUTRO)


def show_cue_spans(audio_analysis: dict | None) -> dict[str, list[tuple[float, float]]]:
    """Template show intro/outro cue spans by cue type; spectral cues carry no reliable type."""
    spans: dict[str, list[tuple[float, float]]] = {t: [] for t in _SHOW_CUE_TYPES}
    for sig in (audio_analysis or {}).get('signals') or []:
        details = sig.get('details') or {}
        cue_type = details.get('cue_type')
        if (sig.get('signal_type') == 'audio_cue' and cue_type in spans
                and is_template_cue(details)):
            spans[cue_type].append((float(sig['start']), float(sig['end'])))
    return spans


def _split_by_show_cue(
    gap_start: float, gap_end: float, cues: list[tuple[float, float]], min_seconds: float,
) -> list[tuple[float, float, bool]]:
    """Split a gap into (start, end, is_show_audio) pieces around the cues that overlap it.

    A remainder of at least ``min_seconds`` could be an inserted ad and stays its own piece.
    """
    overlapping = [(s, e) for s, e in cues if s < gap_end and e > gap_start]
    if not overlapping:
        return [(gap_start, gap_end, False)]
    show_start = max(gap_start, min(s for s, _ in overlapping))
    show_end = min(gap_end, max(e for _, e in overlapping))
    lead = show_start - gap_start >= min_seconds
    trail = gap_end - show_end >= min_seconds
    pieces = []
    if lead:
        pieces.append((gap_start, show_start, False))
    pieces.append((show_start if lead else gap_start, show_end if trail else gap_end, True))
    if trail:
        pieces.append((show_end, gap_end, False))
    return pieces


def _edge_gap_markers(
    gap_start: float, gap_end: float, cue_type: str,
    show_cues: dict | None, min_seconds: float,
) -> list[dict]:
    """Markers for a head or tail gap, with the show-cue-covered part categorised."""
    edge = 'head' if cue_type == AUDIO_CUE_TYPE_SHOW_INTRO else 'tail'
    category = CUE_CUT_CATEGORY[cue_type]
    markers = []
    for start, end, is_show in _split_by_show_cue(
            gap_start, gap_end, (show_cues or {}).get(cue_type, []), min_seconds):
        covered = f' covered by show {category} cue' if is_show else ''
        marker = _new_marker(
            start, end,
            f'VAD gap at episode {edge}{covered} ({end - start:.1f}s untranscribed)')
        if is_show:
            marker['category'] = category
        logger.info(f"VAD {edge} gap: {start:.1f}s-{end:.1f}s{covered}")
        markers.append(marker)
    return markers


def detect_vad_gaps(
    segments: list[dict],
    existing_ads: list[dict],
    episode_duration: float,
    start_min_seconds: float = 3.0,
    mid_min_seconds: float = 8.0,
    tail_min_seconds: float = 3.0,
    adjacent_auto_extend_max_seconds: float = MAX_ADJACENT_AUTO_EXTENSION_SECONDS,
    show_cues: dict[str, list[tuple[float, float]]] | None = None,
) -> list[dict]:
    """Return new ad markers (possibly empty) for suspicious untranscribed audio spans.

    `existing_ads` may be mutated: mid-gaps adjacent to a detected ad extend its boundary in
    place instead of emitting a marker (like `ad_detector.extend_ad_boundaries_by_content`).

    segments: Whisper segments sorted by start. *_min_seconds: minimum gap to emit; a mid gap
    also needs signoff-before and resume-after context. adjacent_auto_extend_max_seconds: largest
    adjacent gap that can extend an ad on adjacency alone; larger ones need context or become
    held review candidates. show_cues: template show intro/outro cue spans by cue type (see
    `show_cue_spans`); the part of a head/tail gap they cover gets the intro/outro category.
    """
    if not segments:
        return []

    new_markers: list[dict] = []

    # Head gap
    head_end = segments[0].get('start', 0.0)
    if head_end >= start_min_seconds and not _region_covered(0.0, head_end, existing_ads):
        new_markers.extend(_edge_gap_markers(
            0.0, head_end, AUDIO_CUE_TYPE_SHOW_INTRO, show_cues, start_min_seconds))

    # Mid gaps
    starts = [seg.get('start', 0.0) for seg in segments]
    for gap_start, gap_end in transcript_gaps(segments, mid_min_seconds):
        gap_duration = gap_end - gap_start
        i_after = bisect_left(starts, gap_end)
        before_seg = max(segments[:i_after], key=lambda seg: seg.get('end', 0.0))
        after_seg = segments[i_after]
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
            before_text = before_seg.get('text', '')
            after_text = after_seg.get('text', '')
            has_break_context = (
                _ends_with_signoff(before_text)
                and _starts_with_resume(after_text)
            )
            old_start = adjacent.get('start', 0.0)
            old_end = adjacent.get('end', 0.0)
            proposed_start = min(old_start, gap_start)
            proposed_end = max(old_end, gap_end)
            added_extension = (
                max(0.0, old_start - proposed_start)
                + max(0.0, proposed_end - old_end)
            )
            prior_auto_extension = adjacent.get(
                'vad_gap_adjacency_extension_seconds', 0.0)
            if not isinstance(prior_auto_extension, (int, float)):
                prior_auto_extension = 0.0
            cumulative_auto_extension = prior_auto_extension + added_extension
            if (cumulative_auto_extension > adjacent_auto_extend_max_seconds
                    and not has_break_context):
                marker = _new_marker(
                    gap_start,
                    gap_end,
                    f'VAD gap exceeds adjacency-only extension limit '
                    f'({gap_duration:.1f}s gap, '
                    f'{cumulative_auto_extension:.1f}s cumulative)',
                )
                marker['vad_gap_requires_review'] = True
                marker['held_for_review'] = True
                marker['was_cut'] = False
                marker['hold_reason'] = HOLD_REASON_LARGE_VAD_GAP
                new_markers.append(marker)
                logger.warning(
                    f"VAD mid gap {gap_start:.1f}-{gap_end:.1f}s is too "
                    f"large for cumulative adjacency-only extension; "
                    f"holding separately"
                )
                continue
            adjacent['start'] = proposed_start
            adjacent['end'] = proposed_end
            adjacent['vad_gap_extended'] = True
            if not has_break_context:
                adjacent['vad_gap_adjacency_extension_seconds'] = (
                    cumulative_auto_extension)
            logger.info(
                f"VAD mid gap merged into adjacent ad: "
                f"{old_start:.1f}-{old_end:.1f}s -> "
                f"{adjacent['start']:.1f}-{adjacent['end']:.1f}s"
            )
            continue

        before_text = before_seg.get('text', '')
        after_text = after_seg.get('text', '')
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
            new_markers.extend(_edge_gap_markers(
                tail_start, episode_duration, AUDIO_CUE_TYPE_SHOW_OUTRO, show_cues, tail_min_seconds))

    return new_markers
