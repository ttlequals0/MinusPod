"""Snap detected ad edges to nearby silence spans.

DAI ads are bracketed by short silences inserted by the ad-injection platform.
When the LLM places an edge a second or two from the real silence, snapping to
the silence midpoint lands the cut in dead air and the retained half
reconstitutes one natural pause.

Cue snap runs first and is stronger evidence; silence snap skips any edge
already committed by cue snap. Pure function; no DB, no LLM, no IO.

Word-gap mode (``word_segments=``, precise_cut_edges): an edge moves into the pause beside the nearest
sentence or word boundary, from the analyzer's spans or ``energy_reader`` frames.
"""
from __future__ import annotations

import bisect
import logging
import math
import statistics

from config import CUE_MATCH_STAGE, MIN_AD_DURATION_FOR_REMOVAL, MERGE_GAP_SECONDS
from utils.markers import EDGE_TOLERANCE, finite_number, recorded_member_spans
from utils.short_asks import (SHORT_ASK_EDGE_CAP_S, SHORT_ASK_MIN_S, SHORT_ASK_TIGHT_BELOW_S,
                               SHORT_ASK_TIGHT_EDGE_CAP_S)

logger = logging.getLogger('podcast.claude.silence_snap')


def _midpoint(span: dict) -> float:
    return (span['start'] + span['end']) / 2.0


def _pick_span(
    spans: list[dict],
    edge: float,
    max_distance_s: float,
    min_silence_s: float,
    exclude_ids: set,
    span_ends: list[float],
    proposed_must_be_less_than: float | None = None,
    proposed_must_be_greater_than: float | None = None,
) -> tuple[dict, float] | None:
    """Return (best_span, midpoint) to snap ``edge`` to, or None.

    Selection key: nearest midpoint first; ties within 0.1s go to the longer
    silence. span_ends must be the precomputed span['end'] values in span order
    (sorted by start; non-overlapping spans make ends non-decreasing too).
    """
    # Lower bound: midpoint <= end, so end < edge - max_distance_s guarantees
    # mid < edge - max_distance_s, failing |mid - edge| <= max_distance_s.
    lo = bisect.bisect_left(span_ends, edge - max_distance_s)

    candidates = []
    for span in spans[lo:]:
        # Upper bound: midpoint >= start, so start > edge + max_distance_s
        # guarantees mid > edge + max_distance_s, failing |mid - edge| <= max_distance_s.
        if span['start'] > edge + max_distance_s:
            break
        if id(span) in exclude_ids:
            continue
        dur = span['duration']
        if dur < min_silence_s:
            continue
        mid = _midpoint(span)
        dist = abs(mid - edge)
        if dist > max_distance_s:
            continue
        if dist < 0.01:
            continue
        if proposed_must_be_less_than is not None and mid >= proposed_must_be_less_than:
            continue
        if proposed_must_be_greater_than is not None and mid <= proposed_must_be_greater_than:
            continue
        candidates.append((span, mid, dur))

    if not candidates:
        return None

    # Nearest first; within 0.1s bucket -> longer silence wins.
    best, mid, _ = max(candidates, key=lambda t: (-round(abs(t[1] - edge), 1), t[2]))
    return best, mid


def snap_ad_boundaries_to_silence(
    ads: list[dict],
    silence_spans: list[dict],
    max_distance_s: float,
    min_silence_s: float,
    *,
    word_segments: list[dict] | None = None,
    energy_reader=None,
    neighbours: list[dict] | None = None,
    duration: float | None = None,
) -> None:
    """Mutate ads in place, snapping each start/end to a nearby silence midpoint.

    With ``word_segments`` the word-gap mode runs instead (see the module note and
    ``_snap_to_word_gaps``); without it the behaviour below is unchanged.

    Contract mirrors snap_ad_boundaries_to_cues: same guards, same audit record
    layout (silence_snap instead of cue_snap). Edges already in ad['cue_snap']
    are skipped. Also sorts ads by start in place (needed for neighbour gaps).

    Guards (mandatory):
      a. If pre-snap duration >= MIN_AD_DURATION_FOR_REMOVAL and the snap would
         reduce the ad below that threshold, revert the ad's silence snap.
      b. Reject an edge snap that would leave < MERGE_GAP_SECONDS between this
         ad and its neighbour.
    """
    if word_segments is not None:
        _snap_to_word_gaps(ads, silence_spans, max_distance_s, min_silence_s, word_segments,
                           energy_reader, neighbours, duration)
        return
    if not ads or not silence_spans:
        return

    # Sort ads by start so neighbour lookup is deterministic.
    ads.sort(key=lambda a: a.get('start', 0.0))

    # Precompute ends list for bisect lower-bound in _pick_span.
    # silence_detector sorts spans by start; non-overlapping spans also have
    # monotonically non-decreasing ends, so bisect_left on this list is safe.
    span_ends = [s['end'] for s in silence_spans]

    for idx, ad in enumerate(ads):
        try:
            original_start = float(ad['start'])
            original_end = float(ad['end'])
        except (KeyError, TypeError, ValueError):
            continue

        pre_snap_duration = original_end - original_start
        cue_snap = ad.get('cue_snap') or {}
        # Tracks spans already committed to one edge so they cannot snap the other.
        used_span_ids: set = set()
        snap_record: dict = {}
        new_start = original_start
        new_end = original_end

        # --- Start edge ------------------------------------------------
        if 'start' not in cue_snap:
            prev_ad = ads[idx - 1] if idx > 0 else None
            # Guard B (start): reads prev_ad['end'] after any prior snap (ads mutated in order).
            prev_end = prev_ad.get('end') if prev_ad else None

            result = _pick_span(
                silence_spans, original_start,
                max_distance_s=max_distance_s,
                min_silence_s=min_silence_s,
                exclude_ids=used_span_ids,
                span_ends=span_ends,
                proposed_must_be_less_than=original_end,
            )
            if result is not None:
                span, mid = result
                mid = round(mid, 3)
                # Merge-gap guard: reject if gap to preceding ad is too small.
                gap_ok = (prev_end is None) or (mid - prev_end >= MERGE_GAP_SECONDS)
                if gap_ok:
                    new_start = mid
                    snap_record['start'] = _build_record(original_start, mid, span)
                    used_span_ids.add(id(span))
                    logger.info(
                        'Silence snap (start): %.3fs -> %.3fs '
                        '(delta=%+.3fs, silence=%.3f-%.3f)',
                        original_start, new_start, new_start - original_start,
                        span['start'], span['end'],
                    )
                else:
                    logger.info(
                        'Silence snap (start) skipped: merge-gap guard '
                        '(prev_end=%.3f proposed_start=%.3f gap=%.3fs < %.1fs)',
                        prev_end, mid, mid - prev_end, MERGE_GAP_SECONDS,
                    )

        # --- End edge --------------------------------------------------
        if 'end' not in cue_snap:
            next_ad = ads[idx + 1] if idx < len(ads) - 1 else None
            # Guard B (end): reads next_ad['start'] pre-snap (not yet processed),
            # so the two sides together guarantee at least one sees the committed gap.
            next_start = next_ad.get('start') if next_ad else None

            result = _pick_span(
                silence_spans, original_end,
                max_distance_s=max_distance_s,
                min_silence_s=min_silence_s,
                exclude_ids=used_span_ids,
                span_ends=span_ends,
                proposed_must_be_greater_than=new_start,
            )
            if result is not None:
                span, mid = result
                mid = round(mid, 3)
                # Merge-gap guard: reject if gap to following ad is too small.
                gap_ok = (next_start is None) or (next_start - mid >= MERGE_GAP_SECONDS)
                if gap_ok:
                    new_end = mid
                    snap_record['end'] = _build_record(original_end, mid, span)
                    used_span_ids.add(id(span))
                    logger.info(
                        'Silence snap (end): %.3fs -> %.3fs '
                        '(delta=%+.3fs, silence=%.3f-%.3f)',
                        original_end, new_end, new_end - original_end,
                        span['start'], span['end'],
                    )
                else:
                    logger.info(
                        'Silence snap (end) skipped: merge-gap guard '
                        '(next_start=%.3f proposed_end=%.3f gap=%.3fs < %.1fs)',
                        next_start, mid, next_start - mid, MERGE_GAP_SECONDS,
                    )

        if not snap_record:
            continue

        # Guard A: if the pre-snap ad was removable and the snap shrinks it below
        # the removal threshold, revert entirely (compute_applied_cuts silently
        # drops sub-threshold cuts). Duration-only on purpose: it also keeps
        # sub-10s cuts in the fingerprint stage and at confidence >= 0.9, and
        # ignoring those is conservative.
        snapped_duration = new_end - new_start
        if (
            pre_snap_duration >= MIN_AD_DURATION_FOR_REMOVAL
            and snapped_duration < MIN_AD_DURATION_FOR_REMOVAL
        ):
            logger.info(
                'Silence snap reverted for %.3f-%.3f: snapped duration %.3fs '
                'would fall below removal threshold %.1fs',
                original_start, original_end, snapped_duration, MIN_AD_DURATION_FOR_REMOVAL,
            )
            continue

        ad['start'] = new_start
        ad['end'] = new_end
        ad['silence_snap'] = snap_record


def _build_record(original: float, snap_point: float, span: dict) -> dict:
    """Build the per-edge audit record (mirrors _snap_record in cue_boundary_snap)."""
    return {
        'original': round(original, 3),
        'silence_start': round(span['start'], 3),
        'silence_end': round(span['end'], 3),
        'snap_point': round(snap_point, 3),
        'shift_seconds': round(snap_point - original, 3),
        'silence_duration': round(span['duration'], 3),
    }


# --- Word-gap mode: word times can sit most of a second off near a splice, so the real pause decides.

FRAME_S = 0.01
SENTENCE_REACH_S = 1.5
WORD_GAP_REACH_S = 0.6
PAUSE_REACH_S = 0.8
READ_MARGIN_S = 0.6
MIN_PAUSE_S = 0.08
PAUSE_FLOOR_DBFS = -45.0
SPEECH_DROP_DB = 20.0
SPEECH_FLOOR_DBFS = -50.0
PAUSE_MARGIN_S = 0.03
MAX_MOVE_S = 1.5
_TERMINALS = ('.', '?', '!')


def _flatten_words(segments: list[dict]) -> list[dict]:
    """Timed words of all segments, in time order."""
    words = []
    for seg in segments or []:
        for word in seg.get('words') or []:
            try:
                start, end = float(word['start']), float(word.get('end', word['start']))
            except (KeyError, TypeError, ValueError):
                continue
            if math.isfinite(start) and math.isfinite(end) and end >= start:
                words.append({'word': str(word.get('word', '')), 'start': start, 'end': end})
    words.sort(key=lambda w: (w['start'], w['end']))
    return words


def _transcript_candidate(edge: float, side: str, words: list[dict]) -> float | None:
    """Nearest sentence boundary within 1.5 s, else nearest word boundary within 0.6 s, else None."""
    sentence, gap = [], []
    for i, word in enumerate(words):
        if side == 'start':
            if i == 0:
                continue
            point, previous = word['start'], words[i - 1]
        else:
            if i + 1 >= len(words):
                continue
            point, previous = word['end'], word
        closed = previous['word'].strip().endswith(_TERMINALS)
        distance = abs(point - edge)
        if closed and distance <= SENTENCE_REACH_S:
            sentence.append((distance, point))
        if distance <= WORD_GAP_REACH_S:
            gap.append((distance, point))
    for found in (sentence, gap):
        if found:
            return min(found)[1]
    return None


def _find_pauses(frames: list[float], t0: float) -> list[tuple[float, float]]:
    """(start, end) of every run of quiet frames at least MIN_PAUSE_S long."""
    speech = [f for f in frames if f > SPEECH_FLOOR_DBFS]
    threshold = PAUSE_FLOOR_DBFS
    if speech:
        threshold = max(PAUSE_FLOOR_DBFS, statistics.median(speech) - SPEECH_DROP_DB)
    pauses, run_start = [], None
    for i, level in enumerate([*frames, math.inf]):
        quiet = level <= threshold
        if quiet and run_start is None:
            run_start = i
        elif not quiet and run_start is not None:
            if (i - run_start) * FRAME_S >= MIN_PAUSE_S - 1e-9:
                pauses.append((t0 + run_start * FRAME_S, t0 + i * FRAME_S))
            run_start = None
    return pauses


def _word_gap_point(edge: float, side: str, words: list[dict]) -> float | None:
    """Nearest word boundary within 0.6 s for an edge inside a word; the edge itself when in a gap."""
    near = []
    for i, word in enumerate(words):
        if i + 1 < len(words) and word['end'] <= edge <= words[i + 1]['start']:
            return edge
        point = word['start'] if side == 'start' else word['end']
        if (i > 0 if side == 'start' else i + 1 < len(words)) and abs(point - edge) <= WORD_GAP_REACH_S:
            near.append((abs(point - edge), point))
    return min(near)[1] if near else None


def _distance(pause: tuple[float, float], point: float) -> float:
    return 0.0 if pause[0] <= point <= pause[1] else min(abs(pause[0] - point), abs(pause[1] - point))


def _span_pauses(lo: float, hi: float, spans, min_silence_s: float):
    """The analyzer's silence spans overlapping [lo, hi] that are long enough to be pauses."""
    return [(span['start'], span['end']) for span in spans or ()
            if span['end'] >= lo and span['start'] <= hi
            and span['end'] - span['start'] >= min_silence_s]


def _energy_pauses(lo: float, hi: float, energy_reader):
    frames = energy_reader(max(0.0, lo), hi) if energy_reader else None
    return _find_pauses(frames, max(0.0, lo)) if frames else []


def _pause_edge(edge: float, side: str, point: float, pauses, max_move_s: float):
    """(new edge, pause length) in the pause holding the edge or nearest the point, else None."""
    inside = next((p for p in pauses if p[0] <= edge <= p[1]), None)
    if inside is not None:
        # Already in a real pause: nothing to improve, and a second run lands here again.
        return edge, inside[1] - inside[0]
    near = [p for p in pauses if _distance(p, point) <= PAUSE_REACH_S]
    if near:
        pause = min(near, key=lambda p: (_distance(p, point), p[0] - p[1]))
        placed = pause[1] - PAUSE_MARGIN_S if side == 'start' else pause[0] + PAUSE_MARGIN_S
        if abs(placed - edge) <= max_move_s:
            return placed, pause[1] - pause[0]
    return None


def _snap_word_edge(edge: float, side: str, words: list[dict], *, spans=None,
                    energy_reader=None, min_silence_s: float = 0.0, max_move_s: float = MAX_MOVE_S):
    """(new edge, pause length or None, acoustic) for one cut edge; the audio is read only when no span fits."""
    candidate = _transcript_candidate(edge, side, words)
    point = edge if candidate is None else candidate
    lo = min(edge, point) - PAUSE_REACH_S - READ_MARGIN_S
    hi = max(edge, point) + PAUSE_REACH_S + READ_MARGIN_S
    placed = (_pause_edge(edge, side, point, _span_pauses(lo, hi, spans, min_silence_s), max_move_s)
              or _pause_edge(edge, side, point, _energy_pauses(lo, hi, energy_reader), max_move_s))
    if placed:
        return placed[0], placed[1], True
    # No pause in the audio: only a word gap may move the edge, never a sentence start.
    gap = _word_gap_point(edge, side, words)
    return (edge if gap is None else gap), None, False


def _word_snap_skip(ad: dict, side: str, edge: float, duration: float | None) -> str | None:
    """Why an edge keeps its place, or None. Cue and silence snap already placed theirs."""
    if (ad.get('validation') or {}).get('user_confirmed'):
        return 'user_confirmed'
    # Measured edges stay put: a cue-cut marker (as in boundaries._edge_locked) and a fingerprint match bound.
    if ad.get('detection_stage') == CUE_MATCH_STAGE:
        return 'cue_match'
    if any(abs(bound - edge) <= EDGE_TOLERANCE for bound in (
            finite_number(record.get(f'fingerprint_match_{side}')) for record in (ad, *recorded_member_spans(ad)))
           if bound is not None):
        return 'fingerprint'
    if edge <= EDGE_TOLERANCE or (duration and edge >= duration - EDGE_TOLERANCE):
        return 'episode_edge'
    for core in ad.get('dai_core_spans') or []:
        if abs(core.get('start' if side == 'start' else 'end', -1e9) - edge) <= EDGE_TOLERANCE:
            return 'dai_core'
    span = ad.get('transcript_span') or {}
    aligned = span.get(side)
    if (span.get('offset_confirmed') and isinstance(aligned, (int, float))
            and abs(aligned - edge) <= EDGE_TOLERANCE):
        return 'transcript_aligned'
    if side in (ad.get('edge_splice_anchor') or ()):
        return 'splice'
    if (ad.get('terminal_snap') and side == 'start') or (
            ad.get('tail_splice_snap') and side == 'end'):
        return 'splice'
    for key in ('cue_snap', 'silence_snap'):
        record = ad.get(key)
        if isinstance(record, dict) and isinstance(record.get(side), dict):
            return key
    return None


def _short_ask_cap(ad: dict) -> tuple[float, float, float] | None:
    origin = ad.get('short_ask_origin')
    if not (isinstance(origin, (list, tuple)) and len(origin) == 2):
        return None
    cap = (SHORT_ASK_TIGHT_EDGE_CAP_S if origin[1] - origin[0] < SHORT_ASK_TIGHT_BELOW_S
           else SHORT_ASK_EDGE_CAP_S)
    return float(origin[0]), float(origin[1]), cap


def _neighbour_limits(ad: dict, others: list[dict]) -> tuple[float | None, float | None]:
    """End of the nearest marker before the ad and start of the nearest one after it."""
    spans = [(m['start'], m['end']) for m in others
             if m is not ad and m.get('start') is not None and m.get('end') is not None
             and (m['start'], m['end']) != (ad['start'], ad['end'])]
    prior = [end for _start, end in spans if end <= ad['start'] + EDGE_TOLERANCE]
    later = [start for start, _end in spans if start >= ad['end'] - EDGE_TOLERANCE]
    return (max(prior) if prior else None), (min(later) if later else None)


def _snap_to_word_gaps(ads, silence_spans, max_distance_s, min_silence_s, word_segments,
                       energy_reader, neighbours, duration) -> None:
    """Word-gap mode of snap_ad_boundaries_to_silence; records edge_snap and edge_pause_s.
    A cut never shrinks below min(its length, the floor) nor crosses or nears a neighbouring marker."""
    words = _flatten_words(word_segments)
    if not ads or not words:
        return
    others = neighbours if neighbours is not None else ads
    max_move_s = min(MAX_MOVE_S, max_distance_s)
    for ad in ads:
        try:
            start, end = float(ad['start']), float(ad['end'])
        except (KeyError, TypeError, ValueError):
            continue
        prior_end, next_start = _neighbour_limits(ad, others)
        origin = _short_ask_cap(ad)
        floor = SHORT_ASK_MIN_S if origin else MIN_AD_DURATION_FOR_REMOVAL
        result = {'start': start, 'end': end}
        record, pauses = {}, {}
        for side, edge in (('start', start), ('end', end)):
            if _word_snap_skip(ad, side, edge, duration):
                continue
            new, pause, acoustic = _snap_word_edge(
                edge, side, words, spans=silence_spans, energy_reader=energy_reader,
                min_silence_s=min_silence_s, max_move_s=max_move_s)
            if side == 'start':
                blocked = prior_end is not None and (
                    new < prior_end or new - prior_end < min(MERGE_GAP_SECONDS, edge - prior_end))
                blocked = blocked or bool(origin and abs(new - origin[0]) > origin[2])
                length = result['end'] - new
            else:
                blocked = next_start is not None and (
                    new > next_start or next_start - new < min(MERGE_GAP_SECONDS, next_start - edge))
                blocked = blocked or bool(origin and abs(new - origin[1]) > origin[2])
                length = new - result['start']
            if blocked or length < min(end - start, floor):
                new, pause = edge, None
            if new != edge:
                result[side] = new
                record[side] = {'acoustic': acoustic, 'moved_s': round(new - edge, 3)}
            if pause is not None:
                pauses[side] = round(pause, 3)
        if not record and not pauses:
            continue
        ad.update(result)
        ad['edge_snap'] = record
        ad['edge_pause_s'] = pauses
