"""
Audio Fingerprinter - Chromaprint-based audio fingerprinting for ad detection.

Uses the Chromaprint library (via fpcalc binary) to generate audio fingerprints
that can identify identical or near-identical audio segments across episodes.
This is particularly effective for DAI (Dynamic Ad Insertion) ads that are
inserted as identical audio files.
"""
import ctypes
import logging
import os
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass
import json

import numpy as np

try:
    import acoustid
except ImportError:
    acoustid = None

from utils.audio import get_audio_duration
from utils.subprocess_registry import tracked_run
from utils.ffmpeg_run import SAFE_MEDIA_INPUT_ARGS
from config import (
    FFMPEG_SHORT_TIMEOUT,
    FPCALC_TIMEOUT,
    FPCALC_TIMEOUT_FULL,
    SUBPROCESS_VERSION_PROBE,
    AUDIO_CUE_FP_WINDOW_SECONDS,
    AUDIO_CUE_FP_KEY_BITS,
    AUDIO_CUE_FP_KEY_SAMPLES,
    AUDIO_CUE_FP_MIN_GAP_SECONDS,
    AUDIO_CUE_FP_MAX_COUNT,
    AUDIO_CUE_FP_MAX_LEN_SECONDS,
    AUDIO_CUE_FP_MAX_ANCHORS,
    AUDIO_CUE_FP_MAX_CANDIDATES,
    AUDIO_CUE_XEP_SIMILARITY,
    AUDIO_CUE_XEP_MIN_MATCHES,
    AUDIO_CUE_XEP_BODY_MIN_DURATION,
    FINGERPRINT_CHUNK_SIZE,
    FINGERPRINT_MIN_AD_LENGTH_S,
    FINGERPRINT_MATCH_STRICTNESS,
    FINGERPRINT_MIN_COVERAGE,
    FINGERPRINT_GAP_TOLERANCE_S,
    FINGERPRINT_TIMEOUT_FACTOR,
)
from utils.pattern_scope import pattern_passes_scope

logger = logging.getLogger('podcast.fingerprint')

FINGERPRINT_KEY_BITS = 12
FINGERPRINT_KEY_TABLES = 16
FINGERPRINT_MIN_OFFSET_VOTES = 3
FINGERPRINT_MAX_CANDIDATES = 512
# A key held by this many times its fair share of episode points carries no alignment information
FINGERPRINT_OVERFULL_KEY_FACTOR = 8
FINGERPRINT_MIN_OVERFULL_POSITIONS = 64
# Identical consecutive pattern points (silence, a tone) at or above this run length are not evidence
FINGERPRINT_CONSTANT_RUN = 4
# Unrelated real audio can agree for a few seconds: a match needs this much matching audio,
# or nearly all of a shorter pattern but never less than the floor
FINGERPRINT_MIN_EVIDENCE_S = 6.0
FINGERPRINT_SHORT_PATTERN_EVIDENCE = 0.9
FINGERPRINT_EVIDENCE_FLOOR_S = 3.5
# A point that differs from its predecessor by fewer bits counts as that share of a point of evidence
FINGERPRINT_NOVELTY_BITS = 3

# Minimum duration for fingerprinting (seconds)
MIN_SEGMENT_DURATION = 5.0

# Step size for sliding window (seconds)
SLIDING_STEP_SIZE = 2.0

# Cap for the per-window slow scan when the full-file fast path fails.
# When fpcalc can't decode the audio end-to-end, the per-window scan uses
# the same fpcalc binary on each window and almost always produces zero
# new matches -- the only realistic save is a single bad frame midway. 90s
# is enough to catch that case without burning the 10-minute upper bound.
FALLBACK_SLOW_TIMEOUT = 90

# 256-entry table for vectorized population count over uint32 numpy arrays.
_POPCOUNT8 = np.array([bin(i).count('1') for i in range(256)], dtype=np.uint16)


def _fpcalc_output_or_none(result, warn_prefix: str) -> dict | None:
    """Parse fpcalc's JSON stdout, or None when unusable.

    A non-zero exit is not by itself a failure: on a recoverable mid-stream
    decode glitch fpcalc stops reading, prints the fingerprint it got (often
    complete when libav recovers, sometimes a usable prefix), and exits 3
    (#690). Callers therefore gate on the parsed output, not the exit code;
    stderr is logged whenever the exit is non-zero either way.
    """
    stderr = result.stderr.decode(errors='replace').strip() if result.stderr else ''
    if result.returncode != 0:
        logger.warning(f"{warn_prefix}: exit {result.returncode}: {stderr[:300]}")
    try:
        data = json.loads(result.stdout.decode())
    except (ValueError, UnicodeDecodeError):
        if result.returncode == 0:
            logger.warning(f"{warn_prefix}: unparseable stdout: {stderr[:300]}")
        return None
    if not data.get('fingerprint'):
        return None
    return data


def _popcount32(x):
    """Vectorized population count for a uint32 numpy array."""
    return (_POPCOUNT8[x & 0xFF] + _POPCOUNT8[(x >> 8) & 0xFF]
            + _POPCOUNT8[(x >> 16) & 0xFF] + _POPCOUNT8[(x >> 24) & 0xFF])


# _window_similarity / _pair_similarity are the numpy-vectorized twins of the
# scalar AudioFingerprinter._calculate_similarity: the same bit-error-rate metric
# (1 - hamming(a XOR b) / bits) over uint32-masked Chromaprint ints. The np.uint32
# array dtype handles the signed-int masking that _calculate_similarity does with
# `& 0xFFFFFFFF`. Kept separate because these run the comparison over every offset
# at once, where the scalar version walks one pair in a Python loop.
def _window_similarity(arr, anchor, win):
    """Bit-similarity (0-1) of window [anchor, anchor+win) vs every start position."""
    m = len(arr) - win + 1
    diff = np.zeros(m, dtype=np.int64)
    for k in range(win):
        diff += _popcount32(arr[k:k + m] ^ arr[anchor + k])
    return 1.0 - diff / (win * 32)


def _pair_similarity(arr, a, b, length):
    """Bit-similarity (0-1) of the two length-``length`` windows at ``a`` and ``b``."""
    return _cross_pair_similarity(arr, a, arr, b, length)


def _greedy_hit_positions(sim, similarity, min_gap, claimed=None):
    """Walk a similarity curve left-to-right, taking one hit per ``min_gap`` run.

    Once a position clears ``similarity`` it is taken and the next ``min_gap``
    positions are skipped, so a single occurrence is counted once. ``claimed``,
    when given, suppresses positions an earlier candidate already owns.
    """
    hits = []
    p = 0
    m = len(sim)
    while p < m:
        if sim[p] >= similarity and (claimed is None or not claimed[p]):
            hits.append(p)
            p += min_gap
        else:
            p += 1
    return hits


def _count_window_matches(raw_ints, fp_duration, start_s, end_s, similarity):
    """Count how many times the [start_s, end_s] window recurs in the file.

    A self-match of a captured cue: 1 means it appears only where it was
    captured (a non-recurring, weak template); >=2 means it recurs and can
    bracket ad breaks. Pure function over the fpcalc ``-raw`` int array.
    """
    n = len(raw_ints)
    if n == 0 or fp_duration <= 0 or end_s <= start_s:
        return 0
    fps = n / fp_duration
    anchor = min(max(0, int(round(start_s * fps))), n - 1)
    win = min(max(4, int(round((end_s - start_s) * fps))), n - anchor)
    if win < 4:
        return 0
    arr = np.asarray(raw_ints, dtype=np.uint32)
    min_gap = max(1, int(round(AUDIO_CUE_FP_MIN_GAP_SECONDS * fps)))
    sim = _window_similarity(arr, anchor, win)
    return len(_greedy_hit_positions(sim, similarity, min_gap))


def enumerate_window_occurrences(window, ep_ints, ep_duration, similarity):
    """Every occurrence of ``window`` (uint32 array) in one episode's fingerprint.

    Cross-array twin of :func:`_count_window_matches` that keeps positions
    instead of just the count. Returns ``[(start_s, end_s)]`` in the episode's
    own timeline; occurrences closer than AUDIO_CUE_FP_MIN_GAP_SECONDS
    collapse to one (same greedy collection as the self-match counter).
    """
    ep_arr = np.asarray(ep_ints, dtype=np.uint32)
    win = len(window)
    if win == 0 or len(ep_arr) < win or ep_duration <= 0:
        return []
    fps = len(ep_arr) / ep_duration
    sim = _window_similarity(np.concatenate([window, ep_arr]), 0, win)[win:]
    min_gap = max(1, int(round(AUDIO_CUE_FP_MIN_GAP_SECONDS * fps)))
    win_s = win / fps
    return [(round(p / fps, 2), round(p / fps + win_s, 2))
            for p in _greedy_hit_positions(sim, similarity, min_gap)]


def _cross_pair_similarity(a_arr, a, b_arr, b, length):
    """Bit-similarity (0-1) of ``a_arr[a:a+length]`` vs ``b_arr[b:b+length]``.

    Cross-array twin of :func:`_pair_similarity` (which compares two windows in
    one array). Same bit-error-rate metric.
    """
    bad = int(_popcount32(a_arr[a:a + length] ^ b_arr[b:b + length]).sum())
    return 1.0 - bad / (length * 32)


def _find_shared_segments(target, siblings, win, similarity, min_matches,
                          min_len, max_len, step=None):
    """Every contiguous run of ``target`` that also appears in at least
    ``min_matches`` of the ``siblings`` fingerprint arrays.

    Pure function over raw Chromaprint int arrays. For each probe start in the
    target, find each sibling's best-matching offset, then grow the run -- both
    backward (to the true onset) and forward -- one ``step``-sized slice at a time,
    keeping it while >= ``min_matches`` siblings still match that *new* slice
    (incremental, so it stops at the real boundary rather than riding a strong
    prefix's cumulative average). Returns a list of ``(start, end, match_count)``
    runs as indices into ``target``, ordered left to right and non-overlapping
    (each found run is skipped past). Real intros/outros play once per episode but
    recur across episodes, which is exactly such a shared head/tail run; a show can
    have several (theme plus a recurring sponsor read), so all are returned.
    """
    target = np.asarray(target, dtype=np.int64).astype(np.uint32)
    sibs = [np.asarray(s, dtype=np.int64).astype(np.uint32) for s in siblings]
    sibs = [s for s in sibs if len(s) >= win]
    if len(target) < win or len(sibs) < min_matches:
        return []
    if step is None:
        step = max(1, win // 2)

    def _slice_ok(alive, p, length):
        """Survivors of ``alive`` whose run [p, p+length) (sibling-aligned) matches."""
        out = []
        for s, o in alive:
            so = o + (p - a)  # sibling index aligned to probe anchor `a`
            if so >= 0 and so + length <= len(s) \
                    and _cross_pair_similarity(target, p, s, so, length) >= similarity:
                out.append((s, o))
        return out

    found = []
    a = 0
    claimed_until = 0  # backward walk floor: never re-enter an already-emitted run
    while a + win <= len(target):
        # Each sibling's best offset for the probe window at `a`.
        alive = []
        for s in sibs:
            region = _window_similarity(np.concatenate([target[a:a + win], s]), 0, win)[win:]
            if region.size:
                o = int(np.argmax(region))
                if region[o] >= similarity:
                    alive.append((s, o))
        if len(alive) < min_matches:
            a += step
            continue
        lo, hi = a, a + win
        # Walk backward to the onset (not past an already-emitted run), then
        # forward, intersecting survivors so the final set matches the whole
        # [lo, hi]; bound the emitted length by max_len.
        while lo - step >= claimed_until and (hi - (lo - step)) <= max_len:
            cand = _slice_ok(alive, lo - step, step)
            if len(cand) < min_matches:
                break
            alive, lo = cand, lo - step
        while hi + step <= len(target) and (hi + step - lo) <= max_len:
            cand = _slice_ok(alive, hi, step)
            if len(cand) < min_matches:
                break
            alive, hi = cand, hi + step
        # Whether the coarse run hit the max_len cap BEFORE refinement; the
        # skip-past gate below must use this (refinement can pull hi back).
        coarse_capped = hi + step - lo > max_len
        # Fine refinement: retract coarse overshoot, then extend 1 subfp at a time using 4-subfp windows
        # (a 1-subfp slice is 32 bits -- too noisy at 0.73). Never drops a survivor; count stays coarse.
        R = min(4, win)
        fine_limit = max(1, step - 1)
        # Retraction floor: never shrink a coarse-valid run below min_len, else a
        # previously-emittable candidate vanishes (min_len can exceed win).
        retract_floor = max(win, min_len)
        # hi edge: retract overshoot, then extend.
        for _ in range(fine_limit):
            if (hi - lo) <= retract_floor or hi - R < lo:
                break
            if len(_slice_ok(alive, hi - R, R)) == len(alive):
                break
            hi -= 1
        for _ in range(fine_limit):
            if (hi + 1 - lo) > max_len or hi + 1 > len(target):
                break
            if len(_slice_ok(alive, hi + 1 - R, R)) < len(alive):
                break
            hi += 1
        # lo edge: retract overshoot, then extend.
        for _ in range(fine_limit):
            if (hi - lo) <= retract_floor:
                break
            if len(_slice_ok(alive, lo, R)) == len(alive):
                break
            lo += 1
        for _ in range(fine_limit):
            if (hi - (lo - 1)) > max_len or lo - 1 < claimed_until:
                break
            if len(_slice_ok(alive, lo - 1, R)) < len(alive):
                break
            lo -= 1
        if len(alive) >= min_matches and (hi - lo) >= min_len:
            found.append((lo, hi, len(alive)))
            seg_end = hi
            if coarse_capped:
                # The run was capped at max_len while the shared sound continues;
                # skip past its true end so one long segment yields one candidate,
                # not overlapping max_len fragments. (When the run instead ended at
                # its real boundary, hi already is that end -- no skip needed.)
                tail = alive
                while seg_end + step <= len(target):
                    cand = _slice_ok(tail, seg_end, step)
                    if len(cand) < min_matches:
                        break
                    tail, seg_end = cand, seg_end + step
            claimed_until = seg_end
            a = max(seg_end, a + step)
        else:
            a += step
    return found


def _discover_repeats(raw_ints, fp_duration, similarity, min_count):
    """Find windows of a raw Chromaprint fingerprint that recur across the file.

    Pure function over the fpcalc ``-raw`` int array (no I/O). A short probe
    window seeds LSH buckets; each bucket's first member anchors a full
    self-Hamming scan, the matched segment is grown to its true length, and its
    whole extent is claimed so a long recurring block surfaces as one candidate
    rather than many fragments. Loudness-independent.

    Args:
        raw_ints: fpcalc ``-raw`` fingerprint as a list/array of ints (~8/sec).
        fp_duration: duration the fingerprint covers, in seconds.
        similarity: per-window bit-similarity (0-1) two occurrences must reach.
        min_count: minimum occurrences for a sound to be suggested.

    Returns:
        Candidate dicts {start, end, count} in descending recurrence order,
        capped at AUDIO_CUE_FP_MAX_CANDIDATES.
    """
    n = len(raw_ints)
    if n == 0 or fp_duration <= 0:
        return []
    fps = n / fp_duration
    win = max(4, int(round(AUDIO_CUE_FP_WINDOW_SECONDS * fps)))
    if n < win * 2:
        return []
    min_gap = max(1, int(round(AUDIO_CUE_FP_MIN_GAP_SECONDS * fps)))
    max_len = max(win, int(round(AUDIO_CUE_FP_MAX_LEN_SECONDS * fps)))
    step = max(1, win // 2)
    # Via int64 so signed/out-of-range ints wrap into uint32 (matching the
    # `& 0xFFFFFFFF` masking in _calculate_similarity) instead of warning.
    arr = np.asarray(raw_ints, dtype=np.int64).astype(np.uint32)

    # LSH seed: bucket each probe window by the top KEY_BITS of KEY_SAMPLES
    # evenly spaced subfingerprints, so windows of the same sound collide.
    samples = [int(j * (win - 1) / (AUDIO_CUE_FP_KEY_SAMPLES - 1))
               for j in range(AUDIO_CUE_FP_KEY_SAMPLES)]
    shift = 32 - AUDIO_CUE_FP_KEY_BITS
    buckets = {}
    for i in range(0, n - win + 1, step):
        key = tuple(int(arr[i + s]) >> shift for s in samples)
        buckets.setdefault(key, []).append(i)
    anchors = [members[0] for members in
               sorted(buckets.values(), key=lambda m: -len(m))
               if len(members) >= 2][:AUDIO_CUE_FP_MAX_ANCHORS]

    claimed = np.zeros(n, dtype=bool)
    candidates = []
    for anchor in anchors:
        if claimed[anchor]:
            continue
        hits = _greedy_hit_positions(
            _window_similarity(arr, anchor, win), similarity, min_gap, claimed)
        if not (min_count <= len(hits) <= AUDIO_CUE_FP_MAX_COUNT):
            continue
        # Reference the first matching occurrence (hits is ascending), not the
        # LSH bucket member, which can sit mid-run and even after hits[-1]; using
        # the smallest hit keeps every shifted index in [0, n) below.
        ref = hits[0]
        # The match usually lands mid-sound. Walk the whole occurrence set back
        # to the true onset so the candidate points at the sound's start (and its
        # claim absorbs earlier fragments of the same block).
        back = 0
        while (ref - (back + step) >= 0
               and all(_pair_similarity(arr, ref - back - step, h - back - step, win) >= similarity
                       for h in hits[1:])):
            back += step
        seg_hits = [h - back for h in hits]   # ascending; seg_hits[0] == ref - back
        seg_start = seg_hits[0]
        # Backward extension can walk into a region an earlier (stronger)
        # candidate already claimed; if so this is the same sound seen from a
        # weaker anchor -- drop it rather than emit an overlapping duplicate.
        if claimed[seg_start]:
            continue
        # Grow the segment forward while every occurrence keeps matching. The
        # largest occurrence (seg_hits[-1]) bounds the in-file check.
        length = win + back
        while length + step <= max_len and seg_hits[-1] + length + step <= n:
            if all(_pair_similarity(arr, seg_start, sh, length + step) >= similarity
                   for sh in seg_hits[1:]):
                length += step
            else:
                break
        for sh in seg_hits:
            claimed[max(0, sh - min_gap):min(sh + length + min_gap, n)] = True
        start_s = seg_start / fps
        candidates.append({
            'start': round(start_s, 2),
            'end': round(start_s + length / fps, 2),
            'count': len(hits),
            'occurrences': [round(sh / fps, 2) for sh in seg_hits],
        })
    candidates.sort(key=lambda c: -c['count'])
    return candidates[:AUDIO_CUE_FP_MAX_CANDIDATES]


@dataclass
class FingerprintMatch:
    """Represents a fingerprint match in an audio file."""
    pattern_id: int
    start: float
    end: float
    confidence: float
    sponsor: str | None = None
    # Segment category (#565) inherited from the matched pattern; see
    # TextMatch.category in text_pattern_matcher.py for the same rationale.
    category: str | None = None
    similarity: float = 0.0
    coverage: float = 0.0


@dataclass(frozen=True)
class FingerprintStoreResult:
    """Outcome of store_fingerprint: stored, or the reason it was skipped."""
    stored: bool
    # store_fingerprint sets audio_removed, original_not_retained, segment_too_short and fingerprint_failed.
    # The corrections routes add fingerprint_exists and span_covers_multiple_patterns.
    skipped_reason: str | None = None


@dataclass
class AudioFingerprint:
    """Represents an audio fingerprint."""
    fingerprint: str  # Raw chromaprint fingerprint
    duration: float
    pattern_id: int | None = None


def _key_byte_tables(key_bits, tables):
    """(tables, 4, 256) lookups from each byte of a point to its share of each table's key."""
    rng = np.random.default_rng(0)
    values = np.arange(256, dtype=np.int64)
    lookup = np.zeros((tables, 4, 256), dtype=np.int64)
    for t in range(tables):
        for i, bit in enumerate(rng.permutation(32)[:key_bits].tolist()):
            lookup[t, bit // 8] |= ((values >> (bit % 8)) & 1) << i
        # Each table packs its own random bit subset and owns a separate key range
        lookup[t, 0] += t << key_bits
    return lookup


_KEY_LOOKUP = _key_byte_tables(FINGERPRINT_KEY_BITS, FINGERPRINT_KEY_TABLES)


def _table_keys(arr):
    """(points, tables) index keys of uint32 points."""
    keys = _KEY_LOOKUP[:, 0, arr & 0xFF]
    for b in range(1, 4):
        keys |= _KEY_LOOKUP[:, b, (arr >> (8 * b)) & 0xFF]
    return keys.T


def _informative_points(pat_arr):
    """False for points inside a run of FINGERPRINT_CONSTANT_RUN or more identical values."""
    change = np.flatnonzero(pat_arr[1:] != pat_arr[:-1]) + 1
    lengths = np.diff(np.concatenate(([0], change, [len(pat_arr)])))
    return np.repeat(lengths < FINGERPRINT_CONSTANT_RUN, lengths)


def _novelty(pat_arr, informative):
    """Per-point evidence weight: slowly changing audio says less per point than varied audio."""
    weights = np.ones(len(pat_arr))
    weights[1:] = np.minimum(1.0, _popcount32(pat_arr[1:] ^ pat_arr[:-1]) / FINGERPRINT_NOVELTY_BITS)
    return weights * informative


def _run_evidence(ep_arr, pat_arr, novelty, d, lo, hi, max_diff):
    """Novelty-weighted good points of the run [lo, hi), in episode points, at alignment d."""
    diff = _popcount32(ep_arr[lo:hi] ^ pat_arr[lo - d:hi - d])
    return float(novelty[lo - d:hi - d][diff <= max_diff].sum())


def _build_offset_index(ep_arr):
    """Episode key index as (bucket starts, bucket sizes, positions by key), over-full buckets emptied."""
    tables = _KEY_LOOKUP.shape[0]
    flat = _table_keys(ep_arr).ravel()
    sizes = np.bincount(flat, minlength=tables << FINGERPRINT_KEY_BITS)
    starts = np.cumsum(sizes) - sizes
    positions = (np.argsort(flat, kind='stable') // tables).astype(np.int32)
    cap = max(FINGERPRINT_MIN_OVERFULL_POSITIONS,
              (FINGERPRINT_OVERFULL_KEY_FACTOR * len(ep_arr)) >> FINGERPRINT_KEY_BITS)
    sizes[sizes > cap] = 0
    return starts, sizes, positions


def _candidate_offsets(pat_arr, informative, index, ep_len):
    """Alignments d (ep[d + j] ~ pat[j], d may be negative) with enough key votes, most votes first."""
    starts, sizes, positions = index
    j = np.flatnonzero(informative)
    keys = _table_keys(pat_arr[j])
    rows = np.broadcast_to(j[:, None], keys.shape).ravel()
    keys = keys.ravel()
    counts = sizes[keys]
    total = int(counts.sum())
    if total == 0:
        return np.empty(0, dtype=np.int64)
    ends = np.cumsum(counts)
    pick = np.arange(total) - np.repeat(ends - counts - starts[keys], counts)
    shifted = positions[pick] - np.repeat(rows, counts) + len(pat_arr)
    span = ep_len + len(pat_arr)
    votes = np.bincount(shifted, minlength=span)
    # Votes at a wrong alignment are roughly Poisson around the mean; demand a clear excess
    mean = total / span
    need = max(FINGERPRINT_MIN_OFFSET_VOTES, int(np.ceil(mean + 3.0 + 4.0 * np.sqrt(mean))))
    cands = np.flatnonzero(votes >= need)
    order = np.argsort(-votes[cands], kind='stable')[:FINGERPRINT_MAX_CANDIDATES]
    return cands[order] - len(pat_arr)


def _passing_alignments(ep_arr, pat_arr, informative, offsets, max_diff, min_good):
    """Offsets with at least min_good good informative points overall, a bound on any run there."""
    if len(offsets) == 0:
        return offsets
    j = np.flatnonzero(informative)
    idx = offsets[:, None] + j[None, :]
    inside = (idx >= 0) & (idx < len(ep_arr))
    diff = _popcount32(ep_arr[np.clip(idx, 0, len(ep_arr) - 1)] ^ pat_arr[j])
    return offsets[((diff <= max_diff) & inside).sum(axis=1) >= min_good]


def _trim_run(good, lo, hi, window):
    """Shrink [lo, hi) until each end's window is at least half good and both edges are good."""
    c = [0] + np.cumsum(good[lo:hi]).tolist()
    a, b = 0, hi - lo
    while b > a and 2 * (c[b] - c[max(a, b - window)]) < b - max(a, b - window):
        b -= 1
    while b > a and 2 * (c[min(b, a + window)] - c[a]) < min(b, a + window) - a:
        a += 1
    while b > a and c[b] == c[b - 1]:
        b -= 1
    while b > a and c[a + 1] == c[a]:
        a += 1
    return (lo + a, lo + b) if b > a else None


def _measure_run(ep_arr, pat_arr, informative, d, strictness, gap_points, window):
    """Best trimmed run at d as (ep_lo, ep_hi, similarity, coverage, good points), or None."""
    ep_base, pat_base = (d, 0) if d >= 0 else (0, -d)
    overlap = min(len(pat_arr) - pat_base, len(ep_arr) - ep_base)
    if overlap <= 0:
        return None
    diff = _popcount32(ep_arr[ep_base:ep_base + overlap] ^ pat_arr[pat_base:pat_base + overlap])
    inf = informative[pat_base:pat_base + overlap]
    good = (diff <= (1.0 - strictness) * 32) & inf
    # Runs are built from sustained matches only, so scattered chance hits in noise cannot bridge
    c = np.concatenate(([0], np.cumsum(good)))
    pos = np.arange(overlap)
    w_lo = np.maximum(0, pos - window // 2)
    w_hi = np.minimum(overlap, pos + window - window // 2)
    hits = np.flatnonzero(good & (2 * (c[w_hi] - c[w_lo]) >= w_hi - w_lo))
    if len(hits) == 0:
        return None
    best = None
    for run in np.split(hits, np.flatnonzero(np.diff(hits) > gap_points + 1) + 1):
        islands = np.split(run, np.flatnonzero(np.diff(run) > window) + 1)
        # A short island across a gap at either end is more likely chance than the ad
        while len(islands) > 1 and c[islands[0][-1] + 1] - c[islands[0][0]] < window:
            islands.pop(0)
        while len(islands) > 1 and c[islands[-1][-1] + 1] - c[islands[-1][0]] < window:
            islands.pop()
        trimmed = _trim_run(good, int(islands[0][0]), int(islands[-1][-1]) + 1, window)
        if trimmed is None:
            continue
        lo, hi = trimmed
        n_good = int(good[lo:hi].sum())
        similarity = 1.0 - float(diff[lo:hi][inf[lo:hi]].mean()) / 32.0
        rank = (similarity >= strictness, n_good, similarity)
        if best is None or rank > best[0]:
            best = (rank, lo, hi, similarity, n_good)
    if best is None:
        return None
    _, lo, hi, similarity, n_good = best
    return ep_base + lo, ep_base + hi, similarity, n_good / len(pat_arr), n_good


def _resolve_overlaps(scored):
    """From (evidence, match) pairs keep the best per stretch of time, sorted by start."""
    accepted = []
    for _, m in sorted(scored, key=lambda s: (-s[0], -s[1].coverage, -s[1].similarity, s[1].start)):
        if all(m.start >= a.end or m.end <= a.start for a in accepted):
            accepted.append(m)
    accepted.sort(key=lambda m: m.start)
    return accepted


def _scan_fingerprints(ep_ints, ep_duration, patterns, *, strictness, min_coverage,
                       gap_tolerance_s, should_continue=None):
    """match_fingerprints plus the number of alignments measured."""
    if len(ep_ints) == 0 or ep_duration <= 0:
        return [], 0
    ep_arr = np.asarray(ep_ints, dtype=np.int64).astype(np.uint32)
    fps = len(ep_arr) / ep_duration
    gap_points = int(round(gap_tolerance_s * fps))
    window = max(1, int(round(fps)))
    max_diff = (1.0 - strictness) * 32
    min_evidence = FINGERPRINT_MIN_EVIDENCE_S * fps
    evidence_floor = FINGERPRINT_EVIDENCE_FLOOR_S * fps
    index = _build_offset_index(ep_arr)
    scored = []
    measured = 0
    for pattern_id, pat_ints, _pat_duration, sponsor, category in patterns:
        if should_continue is not None and not should_continue():
            break
        pat_arr = np.asarray(pat_ints, dtype=np.int64).astype(np.uint32)
        informative = _informative_points(pat_arr)
        n_informative = int(informative.sum())
        if n_informative < FINGERPRINT_MIN_OFFSET_VOTES:
            logger.debug("Skipping fingerprint pattern %s: %d informative points",
                         pattern_id, n_informative)
            continue
        novelty = _novelty(pat_arr, informative)
        needed = min(min_evidence, max(evidence_floor, FINGERPRINT_SHORT_PATTERN_EVIDENCE
                                       * float(novelty.sum()))) - 1e-9
        # Weighted evidence never exceeds the good point count, so this bound is safe for the prefilter
        min_good = max(min_coverage * len(pat_arr) - 1e-9, needed)
        offsets = _candidate_offsets(pat_arr, informative, index, len(ep_arr))
        offsets = _passing_alignments(ep_arr, pat_arr, informative, offsets, max_diff, min_good)
        for d in offsets.tolist():
            measured += 1
            run = _measure_run(ep_arr, pat_arr, informative, d, strictness, gap_points, window)
            if run is None:
                continue
            lo, hi, similarity, coverage, n_good = run
            if (similarity < strictness or coverage < min_coverage - 1e-9
                    or _run_evidence(ep_arr, pat_arr, novelty, d, lo, hi, max_diff) < needed):
                continue
            # Coverage is worth up to one second of evidence, so a chance hit past a shorter
            # pattern's end cannot hand the stretch to a longer pattern that is only partly present
            scored.append((n_good + window * coverage, FingerprintMatch(
                pattern_id=pattern_id, start=lo / fps, end=hi / fps,
                confidence=similarity, sponsor=sponsor, category=category,
                similarity=similarity, coverage=coverage)))
    return _resolve_overlaps(scored), measured


def match_fingerprints(ep_ints, ep_duration, patterns, *, strictness, min_coverage,
                       gap_tolerance_s, should_continue=None):
    """Find stored patterns in an episode fingerprint and measure each run.

    ``patterns`` is a list of (pattern_id, pat_ints, pat_duration, sponsor, category).
    Stops early and returns what it has when ``should_continue`` returns False.
    """
    return _scan_fingerprints(
        ep_ints, ep_duration, patterns, strictness=strictness, min_coverage=min_coverage,
        gap_tolerance_s=gap_tolerance_s, should_continue=should_continue)[0]


class AudioFingerprinter:
    """
    Audio fingerprinting using Chromaprint for identifying repeated ads.

    This class provides functionality to:
    - Generate fingerprints for audio segments
    - Compare fingerprints to find matches
    - Search for known ad fingerprints in new episodes
    """

    def __init__(self, db=None):
        """
        Initialize the audio fingerprinter.

        Args:
            db: Database instance for storing/retrieving fingerprints
        """
        self.db = db
        self._fpcalc_path = self._find_fpcalc()

    def _find_fpcalc(self) -> str | None:
        """Find the fpcalc binary."""
        # Check common locations
        paths = [
            '/usr/bin/fpcalc',
            '/usr/local/bin/fpcalc',
            'fpcalc'  # In PATH
        ]

        for path in paths:
            try:
                result = subprocess.run(
                    [path, '-version'],
                    capture_output=True,
                    timeout=SUBPROCESS_VERSION_PROBE
                )
                if result.returncode == 0:
                    logger.debug(f"Found fpcalc at: {path}")
                    return path
            except (subprocess.SubprocessError, FileNotFoundError):
                continue

        logger.warning("fpcalc not found - audio fingerprinting disabled")
        return None

    def is_available(self) -> bool:
        """Check if audio fingerprinting is available."""
        return self._fpcalc_path is not None

    def generate_fingerprint(
        self,
        audio_path: str,
        start: float = 0,
        duration: float = None
    ) -> AudioFingerprint | None:
        """
        Generate a fingerprint for an audio segment.

        Args:
            audio_path: Path to audio file
            start: Start time in seconds
            duration: Duration in seconds (None = entire file)

        Returns:
            AudioFingerprint or None if generation failed
        """
        if not self._fpcalc_path:
            return None

        try:
            # Build fpcalc command
            cmd = [self._fpcalc_path, '-json']

            # If we need a specific segment, extract it first
            if start > 0 or duration is not None:
                with tempfile.NamedTemporaryFile(suffix='.wav', delete=False) as tmp:
                    tmp_path = tmp.name

                try:
                    self._extract_wav_segment(audio_path, tmp_path, start, duration)
                    cmd.append(tmp_path)
                    result = tracked_run(
                        cmd,
                        capture_output=True,
                        timeout=FPCALC_TIMEOUT,
                    )
                finally:
                    if os.path.exists(tmp_path):
                        os.unlink(tmp_path)
            else:
                cmd.append(audio_path)
                result = tracked_run(
                    cmd,
                    capture_output=True,
                    timeout=FPCALC_TIMEOUT,
                )

            data = _fpcalc_output_or_none(result, 'fpcalc')
            if data is None:
                return None

            return AudioFingerprint(
                fingerprint=data.get('fingerprint', ''),
                duration=data.get('duration', duration or 0)
            )

        except subprocess.TimeoutExpired:
            logger.error("Fingerprint generation timed out")
            return None
        except Exception as e:
            logger.error(f"Fingerprint generation failed: {e}")
            return None

    def compare_fingerprints(
        self,
        fp1: str,
        fp2: str
    ) -> float:
        """
        Compare two fingerprints and return similarity score.

        Uses bit error rate comparison on the raw fingerprint data.

        Args:
            fp1: First fingerprint string
            fp2: Second fingerprint string

        Returns:
            Similarity score between 0 and 1
        """
        if acoustid is None:
            logger.warning("acoustid module not available for fingerprint comparison")
            return 0.0
        try:
            # decode_fingerprint expects bytes, not str (ctypes c_char pointer)
            if isinstance(fp1, str):
                fp1 = fp1.encode('utf-8')
            if isinstance(fp2, str):
                fp2 = fp2.encode('utf-8')

            # Decode fingerprints to integer arrays
            fp1_decoded = acoustid.chromaprint.decode_fingerprint(fp1)
            fp2_decoded = acoustid.chromaprint.decode_fingerprint(fp2)

            if not fp1_decoded or not fp2_decoded:
                return 0.0

            fp1_array = fp1_decoded[0]
            fp2_array = fp2_decoded[0]

            # Compare using bit error rate
            return self._calculate_similarity(fp1_array, fp2_array)

        except (TypeError, ctypes.ArgumentError) as e:
            logger.error(f"Fingerprint comparison failed (bad data): {e}")
            return -1.0
        except Exception as e:
            logger.error(f"Fingerprint comparison failed: {e}")
            return 0.0

    def _calculate_similarity(
        self,
        fp1: list[int],
        fp2: list[int],
        fp1_start: int = 0,
        fp1_end: int = 0
    ) -> float:
        """
        Calculate similarity between two fingerprint arrays using bit error rate.

        Scalar twin of the module-level _window_similarity / _pair_similarity
        (same metric); this one walks one slice pair in a Python loop for the
        ad-matching path, those vectorize it over every offset for cue discovery.

        Args:
            fp1: First fingerprint array
            fp2: Second fingerprint array
            fp1_start: Start index into fp1 (default 0)
            fp1_end: End index into fp1 (default 0 means len(fp1))

        Returns:
            Similarity score between 0 and 1
        """
        if not fp1 or not fp2:
            return 0.0

        if fp1_end == 0:
            fp1_end = len(fp1)

        # Use the shorter length for comparison
        min_len = min(fp1_end - fp1_start, len(fp2))
        if min_len <= 0:
            return 0.0

        # Count matching bits
        total_bits = 0
        matching_bits = 0

        for i in range(min_len):
            # Mask to 32 bits: fpcalc -raw emits signed ints, and
            # int.bit_count() counts bits of abs(value), not two's complement
            xor = (fp1[fp1_start + i] ^ fp2[i]) & 0xFFFFFFFF
            diff_bits = xor.bit_count()
            matching_bits += 32 - diff_bits
            total_bits += 32

        return matching_bits / total_bits if total_bits > 0 else 0.0

    def _generate_full_fingerprint(
        self, audio_path: str, timeout: int = FPCALC_TIMEOUT_FULL
    ) -> tuple[list[int], float] | None:
        """Generate raw fingerprint for entire audio file in one fpcalc call.

        Args:
            audio_path: Path to the audio file.
            timeout: fpcalc wall-clock cap. Defaults to the full-file budget; a
                caller on a request thread can pass a shorter bound so a stalled
                decode degrades gracefully instead of holding the request.

        Returns:
            Tuple of (raw_int_array, duration) or None on failure
        """
        if not self._fpcalc_path:
            return None

        try:
            cmd = [self._fpcalc_path, '-raw', '-json', '-length', '0', audio_path]
            result = tracked_run(cmd, capture_output=True, timeout=timeout)

            data = _fpcalc_output_or_none(result, 'Full-file fpcalc')
            if data is None:
                return None

            raw_ints = data.get('fingerprint', [])
            duration = data.get('duration', 0)

            if not isinstance(raw_ints, list):
                return None

            logger.debug(f"Full-file fingerprint: {len(raw_ints)} ints for {duration:.1f}s")
            return (raw_ints, duration)

        except subprocess.TimeoutExpired:
            logger.error("Full-file fingerprint generation timed out")
            return None
        except Exception as e:
            logger.error(f"Full-file fingerprint generation failed: {e}")
            return None

    def _extract_wav_segment(self, audio_path, tmp_path, start, duration):
        """Decode [start, start+duration) (duration None = to EOF) to mono
        16 kHz WAV at tmp_path for fpcalc."""
        cmd = ['ffmpeg', *SAFE_MEDIA_INPUT_ARGS, '-y', '-i', audio_path, '-ss', str(start)]
        if duration is not None:
            cmd.extend(['-t', str(duration)])
        cmd.extend(['-ac', '1', '-ar', '16000', '-f', 'wav', tmp_path])
        tracked_run(cmd, capture_output=True, timeout=FFMPEG_SHORT_TIMEOUT, check=True)

    def generate_raw_span_fingerprint(
        self, audio_path: str, start_s: float, end_s: float,
    ) -> tuple[list[int], float] | None:
        """Raw chromaprint ints for the [start_s, end_s] span of a file.

        Extract-then-fingerprint twin of :meth:`generate_fingerprint` that
        keeps the ``-raw`` int array (comparable against full-file raw
        fingerprints via the window-occurrence helpers) instead of the
        compressed string. Returns (raw_ints, duration) or None on failure.
        """
        if not self._fpcalc_path or end_s <= start_s:
            return None
        try:
            with tempfile.NamedTemporaryFile(suffix='.wav', delete=False) as tmp:
                tmp_path = tmp.name
            try:
                self._extract_wav_segment(audio_path, tmp_path, start_s, end_s - start_s)
                result = tracked_run(
                    [self._fpcalc_path, '-raw', '-json', tmp_path],
                    capture_output=True, timeout=FPCALC_TIMEOUT,
                )
            finally:
                if os.path.exists(tmp_path):
                    os.unlink(tmp_path)
            data = _fpcalc_output_or_none(result, 'span fpcalc')
            if data is None:
                return None
            raw_ints = data.get('fingerprint', [])
            if not isinstance(raw_ints, list):
                return None
            return (raw_ints, data.get('duration', end_s - start_s))
        except subprocess.CalledProcessError as e:
            stderr = e.stderr.decode() if e.stderr else ''
            logger.warning(f"span ffmpeg extract failed: {stderr}")
            return None
        except subprocess.TimeoutExpired:
            logger.error("Span fingerprint generation timed out")
            return None
        except Exception as e:
            logger.error(f"Span fingerprint generation failed: {e}")
            return None

    def discover_recurring_spots(self, audio_path, *, similarity, min_count,
                                 target_fingerprint=None):
        """Find recurring sounds in an episode as cue-template candidates.

        Generates one full-file raw Chromaprint fingerprint, then surfaces the
        windows that recur at least ``min_count`` times. Loudness-independent,
        so it catches level-matched stings the loudness scan misses. Pass
        ``target_fingerprint`` (an already-computed ``(raw_ints, duration)``) to
        reuse a fingerprint the caller has, avoiding a second fpcalc pass.

        Returns candidate dicts {start, end, count} in descending recurrence
        order, or [] if fpcalc is unavailable or fails.
        """
        if not self._fpcalc_path:
            return []
        full_fp = target_fingerprint or self._generate_full_fingerprint(audio_path)
        if full_fp is None:
            logger.warning(
                "Cue candidate discovery: full-file fingerprint failed for %s",
                audio_path)
            return []
        raw_ints, fp_duration = full_fp
        candidates = _discover_repeats(raw_ints, fp_duration, similarity, min_count)
        logger.info(
            "Cue candidate discovery: %d candidates from %d subfingerprints (%.0fs)",
            len(candidates), len(raw_ints), fp_duration)
        return candidates

    def discover_cross_episode_cues(self, target_path, sibling_paths, *,
                                    head_seconds, tail_seconds, window_seconds,
                                    similarity, min_matches, min_duration,
                                    intro_max_duration, outro_max_duration,
                                    max_per_zone, target_fingerprint=None):
        """Find intro/outro cues by comparing this episode's head and tail
        fingerprint against recent completed sibling episodes.

        A real intro/outro plays once per episode (so within-episode recurrence
        misses it) but recurs across episodes near the start/end. Pass
        ``target_fingerprint`` (an already-computed ``(raw_ints, duration)``) to
        reuse a fingerprint the caller has. Returns
        ``[{start, end, kind: 'intro'|'outro', episodeMatches}]``, or [] when
        fpcalc is unavailable or fewer than ``min_matches`` siblings are usable.
        """
        if not self.is_available():
            return []
        target_fp = target_fingerprint or self._generate_full_fingerprint(target_path)
        if target_fp is None or not target_fp[0] or target_fp[1] <= 0:
            return []
        t_ints, t_dur = target_fp

        sib_fps = []
        for path in sibling_paths:
            fp = self._generate_full_fingerprint(path)
            if fp and fp[0] and fp[1] > 0:
                sib_fps.append(fp)
        if len(sib_fps) < min_matches:
            return []

        fps = len(t_ints) / t_dur
        win = max(4, int(round(window_seconds * fps)))
        min_len = max(win, int(round(min_duration * fps)))
        intro_max_len = max(min_len, int(round(intro_max_duration * fps)))
        outro_max_len = max(min_len, int(round(outro_max_duration * fps)))

        # Head and tail zones are split at the episode midpoint so they never
        # overlap, even on short episodes -- otherwise one shared segment would be
        # emitted as both intro and outro.
        def _head(ints, dur):
            n = len(ints)
            want = max(win, int(round(head_seconds * (n / dur))))
            return ints[:min(want, n // 2)]

        def _tail(ints, dur):
            n = len(ints)
            want = max(win, int(round(tail_seconds * (n / dur))))
            return ints[max(n - want, n // 2):]

        out = []
        intros = _find_shared_segments(
            _head(t_ints, t_dur), [_head(s, d) for s, d in sib_fps],
            win, similarity, min_matches, min_len, intro_max_len)
        for a, b, count in intros[:max_per_zone]:
            out.append({'start': round(a / fps, 2), 'end': round(b / fps, 2),
                        'kind': 'intro', 'episodeMatches': count})

        t_tail = _tail(t_ints, t_dur)
        tail_offset = (len(t_ints) - len(t_tail)) / fps
        outros = _find_shared_segments(
            t_tail, [_tail(s, d) for s, d in sib_fps],
            win, similarity, min_matches, min_len, outro_max_len)
        # Keep the runs nearest the episode end -- the true sign-off outro is the
        # last tail run, not the earliest. (Guard the -0 slice: outros[-0:] is the
        # whole list, which would ignore a max_per_zone of 0.)
        for a, b, count in (outros[-max_per_zone:] if max_per_zone > 0 else []):
            out.append({'start': round(tail_offset + a / fps, 2),
                        'end': round(tail_offset + b / fps, 2),
                        'kind': 'outro', 'episodeMatches': count})

        logger.info("Cross-episode cue discovery: %d intro/outro from %d siblings",
                    len(out), len(sib_fps))
        return out

    def discover_cross_episode_body(self, target_path, sibling_paths, *,
                                    window_seconds=AUDIO_CUE_FP_WINDOW_SECONDS,
                                    similarity=AUDIO_CUE_XEP_SIMILARITY,
                                    min_matches=AUDIO_CUE_XEP_MIN_MATCHES,
                                    min_duration=AUDIO_CUE_XEP_BODY_MIN_DURATION,
                                    max_len_s=AUDIO_CUE_FP_MAX_LEN_SECONDS,
                                    target_fingerprint=None):
        """Find recurring audio segments ANYWHERE in the episode body across siblings.

        Sibling of discover_cross_episode_cues but with no head/tail window
        restriction -- the full fingerprint arrays are compared so mid-episode
        stings (e.g. ad-break tones) are found alongside intro/outro material.

        Pass ``target_fingerprint`` (an already-computed ``(raw_ints, duration)``)
        to reuse a fingerprint the caller has; otherwise one fpcalc pass is made.

        Returns ``[{start, end, kind: 'recurring', episodeMatches}]``, or [] when
        fpcalc is unavailable or fewer than ``min_matches`` siblings are usable.
        The result shape is field-for-field compatible with discover_cross_episode_cues
        so downstream template-creation code can consume it unchanged.

        Default max_len_s is AUDIO_CUE_FP_MAX_LEN_SECONDS (30s): mid-episode
        stings are short; a 30s cap keeps false-positive body content from being
        surfaced as candidates while still covering realistic sting lengths.
        Default min_duration is AUDIO_CUE_XEP_BODY_MIN_DURATION (2s), not the 3s
        intro/outro floor, so recurring 1.5-2.5s ad stings survive the length gate.
        Duplicate candidates across sibling pairs are prevented natively: all
        sibling arrays are passed in a single _find_shared_segments call, whose
        claimed_until mechanism ensures non-overlapping, left-to-right results.
        """
        if not self.is_available():
            return []
        if not sibling_paths:
            return []
        target_fp = target_fingerprint or self._generate_full_fingerprint(target_path)
        if target_fp is None or not target_fp[0] or target_fp[1] <= 0:
            return []
        t_ints, t_dur = target_fp

        sib_fps = []  # (original sibling index, (ints, duration))
        for idx, path in enumerate(sibling_paths):
            fp = self._generate_full_fingerprint(path)
            if fp and fp[0] and fp[1] > 0:
                sib_fps.append((idx, fp))
        if len(sib_fps) < min_matches:
            return []

        fps = len(t_ints) / t_dur
        win = max(4, int(round(window_seconds * fps)))
        min_len = max(win, int(round(min_duration * fps)))
        max_len = max(min_len, int(round(max_len_s * fps)))

        segs = _find_shared_segments(
            t_ints, [s for _, (s, _) in sib_fps],
            win, similarity, min_matches, min_len, max_len)

        t_arr = np.asarray(t_ints, dtype=np.uint32)
        # Target first, then siblings at their original input index + 1.
        # Arrays are converted once here; the per-candidate enumeration reuses
        # them (np.asarray on a matching-dtype array is a no-op).
        all_fps = [(0, (t_arr, t_dur))] + [
            (i + 1, (np.asarray(s, dtype=np.uint32), d))
            for i, (s, d) in sib_fps]
        out = []
        for a, b, count in segs:
            cand = {'start': round(a / fps, 2), 'end': round(b / fps, 2),
                    'kind': 'recurring', 'episodeMatches': count}
            # Per-episode breakdown (issue #350): every occurrence of this run
            # in every episode, target included. An enumeration failure must
            # not cost the candidate itself.
            try:
                window = t_arr[a:b]
                episodes = []
                for index, (ep_ints, ep_dur) in all_fps:
                    occ = enumerate_window_occurrences(
                        window, ep_ints, ep_dur, similarity)
                    episodes.append({
                        'index': index,
                        'matchCount': len(occ),
                        'matches': [{'start': s, 'end': e} for s, e in occ],
                    })
                cand['episodes'] = episodes
            except Exception:
                logger.exception(
                    "per-episode enumeration failed; candidate kept without breakdown")
            out.append(cand)

        logger.info("Cross-episode body discovery: %d recurring from %d siblings",
                    len(out), len(sib_fps))
        return out

    def count_self_matches(self, audio_path, start_s, end_s, *, similarity):
        """Count how many times a captured cue window recurs in its episode.

        Used at template-create time to warn on a weak cue: 1 means the sound
        appears only where it was captured (it will not bracket ad breaks); >=2
        means it recurs. Returns 0 if fpcalc is unavailable or fails.

        Runs on the create request thread, so the fingerprint is bounded by the
        shorter FPCALC_TIMEOUT (not the full-file budget): a normal episode
        fingerprints in seconds, and a stalled decode gives up well before the
        proxy timeout, yielding 0 (no warning) rather than blocking the save.
        """
        if not self._fpcalc_path:
            return 0
        full_fp = self._generate_full_fingerprint(audio_path, timeout=FPCALC_TIMEOUT)
        if full_fp is None:
            return 0
        raw_ints, fp_duration = full_fp
        return _count_window_matches(raw_ints, fp_duration, start_s, end_s, similarity)

    def _decode_known_fingerprints(
        self,
        known_fingerprints: list[tuple[int, str, float, str, str | None]]
    ) -> list[tuple[int, list[int], float, str, str | None]]:
        """Decode known fingerprint strings to raw int arrays.

        Returns:
            List of (pattern_id, raw_int_array, duration, sponsor, category)
        """
        if acoustid is None:
            logger.warning("acoustid not available for fingerprint decoding")
            return []

        decoded = []
        for pattern_id, fp_str, duration, sponsor, category in known_fingerprints:
            try:
                fp_bytes = fp_str.encode('utf-8') if isinstance(fp_str, str) else fp_str
                result = acoustid.chromaprint.decode_fingerprint(fp_bytes)
                if result and result[0]:
                    decoded.append((pattern_id, result[0], duration, sponsor, category))
                else:
                    logger.debug(f"Skipping fingerprint pattern {pattern_id}: nothing decodable")
            except Exception as e:
                logger.debug(f"Skipping fingerprint pattern {pattern_id}: decode failed: {e}")

        return decoded

    def _scan_preamble(
        self, scan_start_time, last_log_time, position, total_duration,
        timeout, match_count, cancel_event
    ):
        """Shared per-iteration timeout/cancel/progress bookkeeping for scans.

        Returns (action, last_log_time) where action is one of
        "timeout", "cancel", or "continue".
        """
        now = time.time()
        elapsed = now - scan_start_time
        if elapsed > timeout:
            logger.warning(
                f"Fingerprint scan timed out after {elapsed:.0f}s "
                f"at {position:.1f}s/{total_duration:.1f}s with {match_count} matches"
            )
            return "timeout", last_log_time
        if cancel_event and cancel_event.is_set():
            logger.info(f"Fingerprint scan cancelled at {position:.1f}s/{total_duration:.1f}s")
            return "cancel", last_log_time
        if now - last_log_time >= 60:
            pct = (position / total_duration) * 100
            logger.info(
                f"Fingerprint scan progress: {position:.1f}s/{total_duration:.1f}s "
                f"({pct:.0f}%), {match_count} matches, {elapsed:.0f}s elapsed"
            )
            last_log_time = now
        return "continue", last_log_time

    @staticmethod
    def _full_fingerprint_timeout(duration: float) -> int:
        """fpcalc budget for a whole episode: scales with length, never below the floor."""
        return max(FPCALC_TIMEOUT_FULL, int(duration * FINGERPRINT_TIMEOUT_FACTOR))

    def _matching_settings(self) -> tuple[float, float, float]:
        """(strictness, min_coverage, gap_tolerance_s) from settings, config defaults otherwise."""
        db = self.db
        if not db:
            return FINGERPRINT_MATCH_STRICTNESS, FINGERPRINT_MIN_COVERAGE, FINGERPRINT_GAP_TOLERANCE_S
        return (
            db.get_setting_float('fingerprint_match_strictness', FINGERPRINT_MATCH_STRICTNESS),
            db.get_setting_float('fingerprint_min_coverage', FINGERPRINT_MIN_COVERAGE),
            db.get_setting_float('fingerprint_gap_tolerance_s', FINGERPRINT_GAP_TOLERANCE_S),
        )

    def _in_scope_fingerprints(
        self, podcast_id, network_id, podcast_tags
    ) -> list[tuple[int, str, float, str, str | None]]:
        """Stored fingerprints whose pattern scope applies to this podcast."""
        podcast_tag_set = set(podcast_tags) if podcast_tags else set()
        return [
            (row['pattern_id'], row['fingerprint'], row['duration'], row['sponsor'], row.get('category'))
            for row in self._load_fingerprints_from_db()
            if pattern_passes_scope(
                row['scope'], row['network_id'], row['podcast_id'], row['source'],
                set(row['sponsor_tags']),
                podcast_id=podcast_id, network_id=network_id, podcast_tag_set=podcast_tag_set)
        ]

    def _match_decoded(self, raw_ints, fp_duration, decoded_known, timeout, cancel_event):
        """Run the measured matcher, stopping on cancel or when the time budget is spent."""
        strictness, min_coverage, gap_tolerance_s = self._matching_settings()
        started = time.monotonic()
        stop_reason = []

        def should_continue():
            if cancel_event is not None and cancel_event.is_set():
                stop_reason.append('cancelled')
            elif time.monotonic() - started > timeout:
                stop_reason.append('timed out')
            return not stop_reason

        matches = match_fingerprints(
            raw_ints, fp_duration, decoded_known, strictness=strictness,
            min_coverage=min_coverage, gap_tolerance_s=gap_tolerance_s,
            should_continue=should_continue)
        for m in matches:
            logger.info(
                f"Fingerprint match: pattern={m.pattern_id} {m.start:.1f}s-{m.end:.1f}s "
                f"(similarity={m.similarity:.2f}, coverage={m.coverage:.2f})"
            )
        if stop_reason:
            logger.warning(f"Fingerprint scan {stop_reason[0]} after {time.monotonic() - started:.0f}s "
                           f"with {len(matches)} matches")
        return matches

    def find_matches(
        self,
        audio_path: str,
        known_fingerprints: list[tuple[int, str, float, str, str | None]] = None,
        timeout: int = 600,
        cancel_event: threading.Event | None = None,
        *,
        podcast_id: str | None = None,
        network_id: str | None = None,
        podcast_tags: set | None = None,
    ) -> list[FingerprintMatch]:
        """
        Search for known ad fingerprints in an audio file.

        Args:
            audio_path: Path to audio file to search
            known_fingerprints: List of (pattern_id, fingerprint, duration, sponsor, category),
                used as given. If None, loads from the database and keeps the ones whose
                pattern scope applies to podcast_id / network_id / podcast_tags.
            timeout: Maximum seconds to spend matching (default 600s / 10 minutes).
                     Returns partial results if exceeded.
            cancel_event: Optional threading.Event; if set, scanning stops early.

        Returns:
            List of FingerprintMatch objects for found ads
        """
        if not self.is_available():
            return []

        if self.db and not self.db.get_setting_bool('fingerprint_enabled', True):
            return []

        if known_fingerprints is None and self.db:
            known_fingerprints = self._in_scope_fingerprints(podcast_id, network_id, podcast_tags)

        if not known_fingerprints:
            return []

        matches = []
        broken_patterns = set()

        total_duration = self._get_audio_duration(audio_path)
        if total_duration <= 0:
            return []

        logger.info(f"Searching {total_duration:.1f}s audio for {len(known_fingerprints)} known fingerprints")

        full_fp = self._generate_full_fingerprint(
            audio_path, timeout=self._full_fingerprint_timeout(total_duration))
        if full_fp is not None:
            raw_ints, fp_duration = full_fp
            decoded_known = self._decode_known_fingerprints(known_fingerprints)
            if decoded_known:
                logger.info(
                    f"Matching fingerprints "
                    f"({len(raw_ints)} ints, {len(decoded_known)} patterns)"
                )
                return self._match_decoded(
                    raw_ints, fp_duration or total_duration, decoded_known, timeout, cancel_event)
            else:
                logger.warning("Could not decode known fingerprints, falling back to per-window scan")
        else:
            logger.warning("Full-file fingerprint failed, falling back to per-window scan")

        # Slow fallback: per-window subprocess scanning.
        # Cap separately at FALLBACK_SLOW_TIMEOUT (much shorter than the
        # full-file timeout). When the fast path fails because fpcalc can't
        # decode the audio source, the per-window scan uses the same fpcalc
        # and almost always produces zero new matches -- burning the full
        # 10-minute budget is wasted work. 90s is enough to catch the rare
        # case where the failure was a single bad frame.
        slow_timeout = min(timeout, FALLBACK_SLOW_TIMEOUT)
        strictness = self._matching_settings()[0]
        scan_start_time = time.time()
        last_log_time = scan_start_time
        position = 0.0
        while position < total_duration - MIN_SEGMENT_DURATION:
            action, last_log_time = self._scan_preamble(
                scan_start_time, last_log_time, position, total_duration,
                slow_timeout, len(matches), cancel_event
            )
            if action != "continue":
                break

            # Bail out if all known fingerprints are broken/corrupt
            if len(broken_patterns) >= len(known_fingerprints):
                logger.info("All known fingerprints are broken/skipped, ending scan early")
                break

            # Generate fingerprint for current window
            chunk_fp = self.generate_fingerprint(
                audio_path,
                start=position,
                duration=FINGERPRINT_CHUNK_SIZE
            )

            if chunk_fp and chunk_fp.fingerprint:
                # Compare against known fingerprints
                for pattern_id, known_fp, known_duration, sponsor, category in known_fingerprints:
                    if pattern_id in broken_patterns:
                        continue

                    similarity = self.compare_fingerprints(
                        chunk_fp.fingerprint,
                        known_fp
                    )

                    if similarity < 0:
                        broken_patterns.add(pattern_id)
                        logger.warning(f"Skipping broken fingerprint pattern {pattern_id} for remaining audio")
                        if self.db:
                            try:
                                self.db.delete_audio_fingerprint(pattern_id)
                                logger.warning(f"Deleted corrupt fingerprint for pattern {pattern_id}")
                            except Exception as del_err:
                                logger.error(f"Failed to delete corrupt fingerprint {pattern_id}: {del_err}")
                        continue

                    if similarity >= strictness:
                        # Found a match
                        match = FingerprintMatch(
                            pattern_id=pattern_id,
                            start=position,
                            end=position + known_duration,
                            confidence=similarity,
                            sponsor=sponsor,
                            category=category,
                            similarity=similarity,
                            coverage=1.0,
                        )
                        matches.append(match)
                        logger.info(
                            f"Fingerprint match: pattern={pattern_id} "
                            f"at {position:.1f}s (confidence={similarity:.2f})"
                        )
                        # Skip ahead past this match
                        position += known_duration
                        break
                else:
                    position += SLIDING_STEP_SIZE
            else:
                position += SLIDING_STEP_SIZE

        # Merge overlapping matches
        matches = self._merge_overlapping_matches(matches)

        return matches

    def _load_fingerprints_from_db(self) -> list[dict]:
        """Load enabled fingerprints of active patterns with scope fields (single JOIN query)."""
        if not self.db:
            return []

        try:
            rows = self.db.get_active_fingerprints_with_scope()
            for row in rows:
                data = row.get('fingerprint', b'')
                row['fingerprint'] = (data.decode('utf-8', errors='ignore')
                                      if isinstance(data, bytes) else str(data))
            return rows
        except Exception as e:
            logger.error(f"Failed to load fingerprints from database: {e}")
            return []

    def _get_audio_duration(self, audio_path: str) -> float:
        """Get duration of audio file in seconds.

        Delegates to utils.audio.get_audio_duration for consistent implementation.
        """
        duration = get_audio_duration(audio_path)
        return duration if duration is not None else 0.0

    def _merge_overlapping_matches(
        self,
        matches: list[FingerprintMatch]
    ) -> list[FingerprintMatch]:
        """Merge overlapping fingerprint matches."""
        if not matches:
            return []

        # Sort by start time
        matches.sort(key=lambda m: m.start)

        merged = []
        current = matches[0]

        for match in matches[1:]:
            # Check for overlap
            if match.start <= current.end + 1.0:  # 1s tolerance
                # Extend current match
                current = FingerprintMatch(
                    pattern_id=current.pattern_id,
                    start=current.start,
                    end=max(current.end, match.end),
                    confidence=max(current.confidence, match.confidence),
                    sponsor=current.sponsor or match.sponsor,
                    category=current.category or match.category,
                    similarity=max(current.similarity, match.similarity),
                    coverage=max(current.coverage, match.coverage),
                )
            else:
                merged.append(current)
                current = match

        merged.append(current)
        return merged

    def store_fingerprint(
        self,
        pattern_id: int,
        audio_path: str | None,
        start: float,
        end: float,
        *,
        source_podcast_slug: str | None = None,
        source_episode_id: str | None = None,
        force: bool = False,
    ) -> FingerprintStoreResult:
        """Fingerprint [start, end] of the original and store it; never raises.

        force=True ignores the pattern's audio_removed marker (explicit rebuilds).
        """
        try:
            return self._store_fingerprint(pattern_id, audio_path, start, end,
                                           source_podcast_slug, source_episode_id, force)
        except Exception as e:
            logger.error(f"Failed to store fingerprint for pattern {pattern_id}: {e}")
            return FingerprintStoreResult(False, 'fingerprint_failed')

    def _store_fingerprint(self, pattern_id, audio_path, start, end,
                           source_podcast_slug, source_episode_id, force):
        if not audio_path or not os.path.isfile(audio_path):
            return FingerprintStoreResult(False, 'original_not_retained')
        if not self.db or not self.is_available():
            return FingerprintStoreResult(False, 'fingerprint_failed')
        if not force:
            pattern = self.db.get_ad_pattern_by_id(pattern_id)
            if pattern and pattern.get('audio_removed'):
                return FingerprintStoreResult(False, 'audio_removed')

        duration = end - start
        min_length = self.db.get_setting_float(
            'fingerprint_min_ad_length_s', FINGERPRINT_MIN_AD_LENGTH_S)
        if duration < min_length:
            logger.debug(f"Segment too short for fingerprinting: {duration:.1f}s")
            return FingerprintStoreResult(False, 'segment_too_short')

        fingerprint = self.generate_fingerprint(audio_path, start, duration)
        if not fingerprint or not fingerprint.fingerprint:
            return FingerprintStoreResult(False, 'fingerprint_failed')

        self.db.create_audio_fingerprint(
            pattern_id=pattern_id,
            fingerprint=fingerprint.fingerprint.encode('utf-8'),
            duration=duration,
            source_podcast_slug=source_podcast_slug,
            source_episode_id=source_episode_id,
            source_start=start,
            source_end=end,
        )
        logger.info(f"Stored fingerprint for pattern {pattern_id}")
        return FingerprintStoreResult(True, None)
