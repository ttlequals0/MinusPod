"""Audio Fingerprinter: Chromaprint-based audio fingerprinting for ad detection.

Uses Chromaprint (via the fpcalc binary) to fingerprint audio so identical or near-identical segments
can be identified across episodes. Particularly effective for DAI ads inserted as identical audio files.
"""
import hashlib
import logging
import os
import subprocess
import tempfile
import threading
import time
from collections import OrderedDict
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
    FINGERPRINT_TIMEOUT_FACTOR,
    FINGERPRINT_SETTING_RANGES,
)
from utils.pattern_scope import pattern_passes_scope

logger = logging.getLogger('podcast.fingerprint')

# Bits of a point that form one index key: few enough that a key often survives 30 percent bit error
FINGERPRINT_KEY_BITS = 12
# One bit subset per index table, fixed so the index never depends on a random number stream
FINGERPRINT_KEY_SUBSETS = (
    (2, 11, 25, 21, 10, 4, 29, 16, 23, 6, 18, 26),
    (7, 11, 21, 10, 31, 8, 4, 28, 14, 9, 23, 18),
    (24, 7, 5, 13, 1, 19, 20, 3, 29, 25, 16, 26),
    (0, 10, 18, 30, 17, 9, 21, 31, 26, 14, 3, 2),
    (26, 19, 6, 1, 22, 4, 17, 30, 29, 24, 27, 28),
    (20, 0, 5, 6, 7, 27, 24, 26, 1, 2, 13, 3),
    (31, 11, 10, 1, 7, 26, 22, 25, 27, 0, 19, 24),
    (3, 9, 16, 31, 26, 8, 20, 0, 27, 12, 19, 14),
    (17, 9, 15, 1, 28, 7, 6, 3, 5, 13, 11, 27),
    (12, 11, 6, 14, 20, 23, 13, 24, 22, 28, 0, 3),
    (17, 8, 7, 3, 29, 19, 25, 18, 16, 9, 0, 20),
    (3, 31, 23, 1, 22, 18, 6, 13, 19, 21, 14, 7),
    (3, 13, 1, 2, 7, 28, 29, 25, 26, 30, 14, 22),
    (1, 14, 7, 11, 5, 17, 31, 23, 2, 13, 22, 12),
    (19, 17, 4, 3, 2, 0, 14, 28, 11, 5, 24, 10),
    (26, 13, 18, 1, 31, 16, 23, 27, 17, 30, 10, 4),
)
# Fewest key votes that make an alignment a candidate
FINGERPRINT_MIN_OFFSET_VOTES = 3
# Votes a candidate alignment needs above the mean, since votes at a wrong alignment are near Poisson
FINGERPRINT_VOTE_MARGIN = 3.0
# Standard deviations above the mean vote count a candidate alignment needs
FINGERPRINT_VOTE_SIGMAS = 4.0
# Alignments measured per pattern, most votes first; enough for every repeat of an ad in an episode
FINGERPRINT_MAX_CANDIDATES = 512
# A key held by this many times its fair share of episode points carries no alignment information
FINGERPRINT_OVERFULL_KEY_FACTOR = 8
# A bucket this small is never treated as over-full, so short episodes keep every key
FINGERPRINT_MIN_OVERFULL_POSITIONS = 64
# Identical consecutive pattern points (silence, a tone) at or above this run length are not evidence
FINGERPRINT_CONSTANT_RUN = 4
# A point within this many differing bits of the pattern point is good, whatever the strictness
FINGERPRINT_GOOD_BITS = 11
# Floor of the differing-bit limit at a run edge, which is stricter than the interior
FINGERPRINT_EDGE_BITS = 8
# Bits added to the run's median good difference to set the edge limit, capped at the good threshold
FINGERPRINT_EDGE_MARGIN_BITS = 3
# Seconds of evidence a match needs, since unrelated audio agrees within the good threshold for a few seconds
FINGERPRINT_MIN_EVIDENCE_S = 6.0
# Share of a shorter pattern's own evidence a match needs instead
FINGERPRINT_SHORT_PATTERN_EVIDENCE = 0.9
# A pattern that cannot supply this much evidence is not distinctive enough to match safely
FINGERPRINT_EVIDENCE_FLOOR_S = 2.25
# Every match needs this much evidence within the edge floor: chance agreement rarely comes that close
FINGERPRINT_CLOSE_EVIDENCE_S = 2.25
# Evidence that accepts a run under the minimum coverage as a partial match: one ad of a stored break
FINGERPRINT_PARTIAL_EVIDENCE_S = 20.0
# Windows spanned when judging whether a point has support, since chance agreement comes in shorter clusters
FINGERPRINT_SUPPORT_WINDOWS = 3
# Window shifts, either way, at which a run is compared with itself to reject steady audio
FINGERPRINT_CONTRAST_SHIFTS = (2, 3, 4, 5)
# Similarity a run must beat its shifted self by
FINGERPRINT_CONTRAST_MARGIN = 0.12
# Chromaprint points per second at its default settings, for checks made without an episode
FINGERPRINT_NOMINAL_FPS = 11025 / 1365.33
# A point that differs from its predecessor by fewer bits counts as that share of a point of evidence
FINGERPRINT_NOVELTY_BITS = 3
# Shortest remainder of a clipped match that is kept, when the caller passes no setting
DEFAULT_MIN_AD_LENGTH_S = FINGERPRINT_SETTING_RANGES['fingerprint_min_ad_length_s'][0]

# Offsets measured per batch by the alignment prefilter, which bounds its peak memory
PASSING_ALIGNMENT_CHUNK = 64
# Entries kept of per-pattern derived data (decoded points, profiles, matchable verdicts)
PATTERN_CACHE_SIZE = 512

# 256-entry table for vectorized population count over uint32 numpy arrays.
_POPCOUNT8 = np.array([bin(i).count('1') for i in range(256)], dtype=np.uint16)


def _fpcalc_output_or_none(result, warn_prefix: str, level: int = logging.WARNING) -> dict | None:
    """Parse fpcalc's JSON stdout, or None when unusable.

    A non-zero exit is not by itself a failure: on a recoverable mid-stream decode glitch fpcalc prints the
    fingerprint it got (often complete, sometimes a usable prefix) and exits 3 (#690). Callers gate on the
    parsed output, not the exit code; stderr is logged whenever the exit is non-zero.
    """
    stderr = result.stderr.decode(errors='replace').strip() if result.stderr else ''
    if result.returncode != 0:
        logger.log(level, f"{warn_prefix}: exit {result.returncode}: {stderr[:300]}")
    try:
        data = json.loads(result.stdout.decode())
    except (ValueError, UnicodeDecodeError):
        if result.returncode == 0:
            logger.log(level, f"{warn_prefix}: unparseable stdout: {stderr[:300]}")
        return None
    if not data.get('fingerprint'):
        return None
    return data


def _popcount32(x):
    """Vectorized population count for a uint32 numpy array."""
    return (_POPCOUNT8[x & 0xFF] + _POPCOUNT8[(x >> 8) & 0xFF]
            + _POPCOUNT8[(x >> 16) & 0xFF] + _POPCOUNT8[(x >> 24) & 0xFF])


# Bit similarity is 1 - hamming(a XOR b) / bits over uint32-masked Chromaprint ints; the np.uint32
# dtype does the signed-int masking, and these compare every offset at once instead of one pair.
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

    A position clearing ``similarity`` is taken and the next ``min_gap`` positions skipped, so one occurrence
    counts once. ``claimed`` suppresses positions an earlier candidate already owns.
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
    """Count how many times the [start_s, end_s] window recurs in the file (pure function over the ``-raw`` int array).

    A self-match of a captured cue: 1 means it appears only where captured (a weak, non-recurring template);
    >=2 means it recurs and can bracket ad breaks.
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

    Cross-array twin of :func:`_count_window_matches` that keeps positions. Returns ``[(start_s, end_s)]`` on the
    episode's own timeline; occurrences closer than AUDIO_CUE_FP_MIN_GAP_SECONDS collapse to one.
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

    Cross-array twin of :func:`_pair_similarity` (two windows in one array); same bit-error-rate metric.
    """
    bad = int(_popcount32(a_arr[a:a + length] ^ b_arr[b:b + length]).sum())
    return 1.0 - bad / (length * 32)


def _find_shared_segments(target, siblings, win, similarity, min_matches,
                          min_len, max_len, step=None):
    """Every contiguous run of ``target`` that also appears in at least ``min_matches`` of the ``siblings``.

    Pure function over raw Chromaprint int arrays. For each probe start in the target, find each sibling's best-matching
    offset, then grow the run backward (to the true onset) and forward one ``step``-sized slice at a time while >=
    ``min_matches`` siblings still match that new slice (incremental, so it stops at the real boundary rather than
    riding a strong prefix's cumulative average). Returns non-overlapping ``(start, end, match_count)`` runs as indices
    into ``target``, left to right. A show can have several shared runs (theme plus a recurring sponsor read), so all
    are returned.
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
        # (a 1-subfp slice is 32 bits, too noisy at 0.73). Never drops a survivor; count stays coarse.
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
                # The run was capped at max_len while the shared sound continues; skip past its true end so one
                # long segment yields one candidate, not overlapping max_len fragments. (A run that ended at its
                # real boundary already has hi at that end.)
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

    Pure function over the fpcalc ``-raw`` int array (no I/O), loudness-independent. A short probe window seeds
    LSH buckets; each bucket's first member anchors a full self-Hamming scan, the matched segment is grown to its
    true length and its whole extent claimed, so a long recurring block surfaces as one candidate.

    raw_ints: fingerprint ints (~8/sec). fp_duration: seconds covered. similarity: per-window bit-similarity
    (0-1) two occurrences must reach. min_count: minimum occurrences to suggest a sound.

    Returns {start, end, count} dicts in descending recurrence order, capped at AUDIO_CUE_FP_MAX_CANDIDATES.
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
    # Via int64 so signed/out-of-range ints wrap into uint32 instead of warning.
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
        # weaker anchor; drop it rather than emit an overlapping duplicate.
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
    # Lead of the match's similarity over nearby shifted alignments
    contrast_margin: float | None = None
    # True for the part of a match kept after a stronger match took the rest; never sent to the API
    clipped_remainder: bool = False
    # True when accepted under the minimum coverage on FINGERPRINT_PARTIAL_EVIDENCE_S of evidence
    partial: bool = False
    # Span in seconds the match passed its gates on, before any clipping
    candidate_span: tuple[float, float] | None = None


@dataclass(frozen=True)
class FingerprintStoreResult:
    """Outcome of store_fingerprint: stored, or the reason it was skipped."""
    stored: bool
    # Reasons: audio_removed, original_not_retained, segment_too_short, not_distinctive, fingerprint_failed,
    # plus fingerprint_exists and span_covers_multiple_patterns from the corrections routes.
    skipped_reason: str | None = None


@dataclass
class AudioFingerprint:
    """Represents an audio fingerprint."""
    fingerprint: str  # Raw chromaprint fingerprint
    duration: float
    pattern_id: int | None = None


def _key_byte_tables(subsets, key_bits):
    """(tables, 4, 256) lookups from each byte of a point to its share of each table's key."""
    values = np.arange(256, dtype=np.int64)
    lookup = np.zeros((len(subsets), 4, 256), dtype=np.int64)
    for t, bits in enumerate(subsets):
        for i, bit in enumerate(bits):
            lookup[t, bit // 8] |= ((values >> (bit % 8)) & 1) << i
        # Each table owns a separate key range
        lookup[t, 0] += t << key_bits
    return lookup


_KEY_LOOKUP = _key_byte_tables(FINGERPRINT_KEY_SUBSETS, FINGERPRINT_KEY_BITS)


def _table_keys(arr):
    """(points, tables) index keys of uint32 points."""
    keys = _KEY_LOOKUP[:, 0, arr & 0xFF]
    for b in range(1, 4):
        keys |= _KEY_LOOKUP[:, b, (arr >> (8 * b)) & 0xFF]
    return keys.T


class _PatternCache:
    """Thread-safe bounded LRU for data derived from a pattern; keys carry the content, so a rebuild misses."""

    def __init__(self, size):
        self._size = size
        self._items = OrderedDict()
        self._lock = threading.Lock()

    def get_or_compute(self, key, compute, keep=lambda value: True):
        with self._lock:
            if key in self._items:
                self._items.move_to_end(key)
                return self._items[key]
        value = compute()
        if not keep(value):
            return value
        with self._lock:
            self._items[key] = value
            self._items.move_to_end(key)
            while len(self._items) > self._size:
                self._items.popitem(last=False)
        return value

    def clear(self):
        with self._lock:
            self._items.clear()

    def __len__(self):
        with self._lock:
            return len(self._items)


_PATTERN_CACHE = _PatternCache(PATTERN_CACHE_SIZE)


def _content_key(data):
    """Digest of a pattern's bytes, so equal content shares an entry and a rebuild gets a new one."""
    return hashlib.blake2b(data, digest_size=16).digest()


def _decode_chromaprint(fp_bytes):
    """acoustid's decode of a chromaprint, cached by content; raises as acoustid does."""
    decode = acoustid.chromaprint.decode_fingerprint
    return _PATTERN_CACHE.get_or_compute(('decode', decode, _content_key(fp_bytes)), lambda: decode(fp_bytes))


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


@dataclass(frozen=True)
class _PatternProfile:
    informative: np.ndarray
    novelty: np.ndarray
    unmatchable_reason: str | None


def _pattern_profile(pat_arr):
    """Informative mask, novelty weights and unmatchable reason of a uint32 pattern, cached by content."""
    def build():
        informative = _informative_points(pat_arr)
        novelty = _novelty(pat_arr, informative)
        informative.flags.writeable = novelty.flags.writeable = False
        return _PatternProfile(informative, novelty, _unmatchable_reason(novelty))
    return _PATTERN_CACHE.get_or_compute(('profile', _content_key(pat_arr.tobytes())), build)


def _evidence_needed(novelty, fps):
    """Evidence a match of this pattern needs, in novelty-weighted good points."""
    return min(FINGERPRINT_MIN_EVIDENCE_S * fps, FINGERPRINT_SHORT_PATTERN_EVIDENCE * float(novelty.sum()))


def _unmatchable_reason(novelty):
    """Why a pattern with these novelty weights can never be matched safely, or None."""
    capacity = FINGERPRINT_SHORT_PATTERN_EVIDENCE * float(novelty.sum())
    if capacity < FINGERPRINT_EVIDENCE_FLOOR_S * FINGERPRINT_NOMINAL_FPS:
        return (f"{capacity / FINGERPRINT_NOMINAL_FPS:.1f}s of distinctive audio, "
                f"{FINGERPRINT_EVIDENCE_FLOOR_S:.1f}s needed")
    return None


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
    """(alignments d with enough key votes, most votes first, capped; count before the cap)."""
    starts, sizes, positions = index
    j = np.flatnonzero(informative)
    keys = _table_keys(pat_arr[j])
    rows = np.broadcast_to(j[:, None], keys.shape).ravel()
    keys = keys.ravel()
    counts = sizes[keys]
    total = int(counts.sum())
    if total == 0:
        return np.empty(0, dtype=np.int64), 0
    ends = np.cumsum(counts)
    pick = np.arange(total) - np.repeat(ends - counts - starts[keys], counts)
    shifted = positions[pick] - np.repeat(rows, counts) + len(pat_arr)
    span = ep_len + len(pat_arr)
    votes = np.bincount(shifted, minlength=span)
    mean = total / span
    need = max(FINGERPRINT_MIN_OFFSET_VOTES, int(np.ceil(
        mean + FINGERPRINT_VOTE_MARGIN + FINGERPRINT_VOTE_SIGMAS * np.sqrt(mean))))
    cands = np.flatnonzero(votes >= need)
    order = np.argsort(-votes[cands], kind='stable')[:FINGERPRINT_MAX_CANDIDATES]
    return cands[order] - len(pat_arr), len(cands)


def _passing_alignments(ep_arr, pat_arr, informative, offsets, min_good):
    """Offsets with at least min_good good informative points overall, a bound on any run there."""
    if len(offsets) == 0:
        return offsets
    j = np.flatnonzero(informative)
    passing = []
    for lo in range(0, len(offsets), PASSING_ALIGNMENT_CHUNK):
        idx = offsets[lo:lo + PASSING_ALIGNMENT_CHUNK, None] + j[None, :]
        inside = (idx >= 0) & (idx < len(ep_arr))
        diff = _popcount32(ep_arr[np.clip(idx, 0, len(ep_arr) - 1)] ^ pat_arr[j])
        passing.append(((diff <= FINGERPRINT_GOOD_BITS) & inside).sum(axis=1) >= min_good)
    return offsets[np.concatenate(passing)]


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


def _solid_points(mask, window):
    """(indices of mask points with at least half of the window centred on them in mask, cumsum)."""
    c = np.concatenate(([0], np.cumsum(mask)))
    pos = np.arange(len(mask))
    w_lo = np.maximum(0, pos - window // 2)
    w_hi = np.minimum(len(mask), pos + window - window // 2)
    return np.flatnonzero(mask & (2 * (c[w_hi] - c[w_lo]) >= w_hi - w_lo)), c


def _edge_run(hits, c, mask, window, island_gap, far_islands=False):
    """Span of solid hits without short edge islands, trimmed to sustained agreement, or None."""
    if len(hits) == 0:
        return None
    islands = np.split(hits, np.flatnonzero(np.diff(hits) > island_gap) + 1)

    def stray(island, gap):
        # Under a window of agreement, cut off by a gap longer than itself or than a window
        n = c[island[-1] + 1] - c[island[0]]
        if n < window and (gap > window or gap >= island[-1] - island[0] + 1):
            return True
        # Chance agreement past an ad's end also comes as islands under two windows, two windows out
        return far_islands and n < 2 * window and gap >= 2 * window

    while len(islands) > 1 and stray(islands[0], islands[1][0] - islands[0][-1] - 1):
        islands.pop(0)
    while len(islands) > 1 and stray(islands[-1], islands[-1][0] - islands[-2][-1] - 1):
        islands.pop()
    return _trim_run(mask, int(islands[0][0]), int(islands[-1][-1]) + 1, window)


@dataclass
class _Alignment:
    """Per-point comparison of a pattern at one alignment and the run counted there."""
    ep_base: int
    diff: np.ndarray
    informative: np.ndarray
    novelty: np.ndarray
    good: np.ndarray
    tight: np.ndarray
    edge_bits: float
    run: tuple
    span: tuple = (0, 0)
    pat_base: int = 0


def _tight_span(al, run, window):
    """(lo, hi) of run with both edges built on the edge threshold."""
    hits, c = _solid_points(al.tight, window)
    edges = _edge_run(hits[(hits >= run[0]) & (hits < run[1])], c, al.tight, window, window // 2, True)
    return edges or run


def _similarity(al, run):
    """Mean bit similarity over the informative points of run."""
    diffs = al.diff[run[0]:run[1]][al.informative[run[0]:run[1]]]
    return 1.0 - float(diffs.mean()) / 32.0 if len(diffs) else 0.0


def _weighted(al, run, bits):
    """Novelty-weighted points of run within bits."""
    lo, hi = run
    return float(al.novelty[lo:hi][(al.diff[lo:hi] <= bits) & al.informative[lo:hi]].sum())


def _tally(al, run):
    """(good points, similarity, evidence, evidence within the edge floor) over run."""
    return (int(al.good[run[0]:run[1]].sum()), _similarity(al, run),
            _weighted(al, run, FINGERPRINT_GOOD_BITS), _weighted(al, run, FINGERPRINT_EDGE_BITS))


def _supported_points(good, inf, span):
    """Good points whose centred span is at least half good over its informative points."""
    c = np.concatenate(([0], np.cumsum(good)))
    ci = np.concatenate(([0], np.cumsum(inf)))
    pos = np.arange(len(good))
    lo = np.maximum(0, pos - span // 2)
    hi = np.minimum(len(good), pos + span - span // 2)
    return np.flatnonzero(good & (2 * (c[hi] - c[lo]) >= ci[hi] - ci[lo]))


def _split_at_gaps(hits, inf_c, gap_points):
    """Groups of hits split where more than gap_points informative points lie between two hits."""
    return np.split(hits, np.flatnonzero(inf_c[hits[1:]] - inf_c[hits[:-1] + 1] > gap_points) + 1)


def _measure_alignment(ep_arr, pat_arr, informative, novelty, d, gap_points, window):
    """Sustained runs of the pattern at alignment d, one _Alignment per piece; empty when none."""
    ep_base, pat_base = (d, 0) if d >= 0 else (0, -d)
    overlap = min(len(pat_arr) - pat_base, len(ep_arr) - ep_base)
    if overlap <= 0:
        return []
    diff = _popcount32(ep_arr[ep_base:ep_base + overlap] ^ pat_arr[pat_base:pat_base + overlap])
    inf = informative[pat_base:pat_base + overlap]
    good = (diff <= FINGERPRINT_GOOD_BITS) & inf
    # Runs are built from sustained matches only, so scattered chance hits in noise cannot bridge;
    # constant stretches carry no evidence either way, so only informative points count as a gap
    hits, c = _solid_points(good, window)
    if len(hits) == 0:
        return []
    inf_c = np.concatenate(([0], np.cumsum(inf)))
    runs = [r for r in (_edge_run(h, c, good, window, window)
                        for h in _split_at_gaps(hits, inf_c, gap_points)) if r]
    # Chance clusters can bridge unrelated audio inside a run, so it is split again where supported
    # agreement stops for longer than the gap tolerance; each piece is gated alone
    supported = _supported_points(good, inf, FINGERPRINT_SUPPORT_WINDOWS * window)
    pieces = []
    for lo, hi in runs:
        groups = _split_at_gaps(supported[(supported >= lo) & (supported < hi)], inf_c, gap_points)
        starts = [lo] + [int(g[0]) for g in groups[1:]]
        ends = [int(g[-1]) + 1 for g in groups[:-1]] + [hi]
        pieces += [p for p in (_trim_run(good, a, b, window) for a, b in zip(starts, ends, strict=True)) if p]
    out = []
    for lo, hi in pieces:
        interior = diff[lo:hi][good[lo:hi]]
        edge_bits = float(min(FINGERPRINT_GOOD_BITS, max(
            FINGERPRINT_EDGE_BITS, np.median(interior) + FINGERPRINT_EDGE_MARGIN_BITS)))
        al = _Alignment(ep_base, diff, inf, novelty[pat_base:pat_base + overlap], good,
                        (diff <= edge_bits) & inf, edge_bits, (lo, hi), pat_base=pat_base)
        al.span = _tight_span(al, al.run, window)
        out.append(al)
    return out


def _agreement(al):
    """Closeness of the counted run: bits to spare under the good threshold, summed."""
    lo, hi = al.run
    return float((FINGERPRINT_GOOD_BITS + 1 - al.diff[lo:hi])[al.good[lo:hi]].sum())


def _closest_alignments(alignments, window):
    """Of (d, alignment) pairs, the closest-agreeing one per stretch within each second of alignments."""
    # Neighbouring points of real audio are alike, so a slightly shifted alignment agrees too
    kept = []
    for d, al in sorted(alignments, key=lambda a: -_agreement(a[1])):
        lo, hi = al.ep_base + al.span[0], al.ep_base + al.span[1]
        if all(abs(d - k) > window or hi <= k_lo or lo >= k_hi for k, k_lo, k_hi, _ in kept):
            kept.append((d, lo, hi, al))
    return [al for *_, al in kept]


def _under_coverage(n_good, pat_len, min_coverage):
    """True when a run's good points cover less of the pattern than min_coverage."""
    return n_good / pat_len < min_coverage - 1e-9


def _rejecting_gate(tally, pat_len, needed, fps, strictness, min_coverage):
    """The first gate a measured alignment fails, or None when it is a match."""
    n_good, similarity, evidence, close = tally
    if (_under_coverage(n_good, pat_len, min_coverage)
            and evidence < FINGERPRINT_PARTIAL_EVIDENCE_S * fps - 1e-9):
        return 'coverage'
    # Chance agreement in unrelated real audio rarely comes this close for long
    if (evidence < needed - 1e-9
            or close < FINGERPRINT_CLOSE_EVIDENCE_S * fps - 1e-9):
        return 'evidence'
    if similarity < strictness:
        return 'similarity'
    return None


def _contrast_margin(ep_arr, pat_arr, al, window):
    """Similarity of al's run minus the median similarity of the same run at nearby shifted alignments."""
    lo, hi = al.run
    i = np.arange(lo, hi)
    inf = al.informative[lo:hi]
    sims = []
    for k in FINGERPRINT_CONTRAST_SHIFTS:
        for shift in (-k * window, k * window):
            e = al.ep_base + i + shift
            ok = inf & (e >= 0) & (e < len(ep_arr))
            if ok.sum() >= window:
                diffs = _popcount32(ep_arr[e[ok]] ^ pat_arr[al.pat_base + i[ok]])
                sims.append(1.0 - float(diffs.mean()) / 32.0)
    return _similarity(al, al.run) - (float(np.median(sims)) if sims else 0.0)


def _alignment_gate(ep_arr, pat_arr, al, tally, needed, fps, window, strictness, min_coverage):
    """(first gate al fails or None, contrast margin or None when an earlier gate failed)."""
    gate = _rejecting_gate(tally, len(pat_arr), needed, fps, strictness, min_coverage)
    if gate:
        return gate, None
    margin = _contrast_margin(ep_arr, pat_arr, al, window)
    return ('contrast' if margin < FINGERPRINT_CONTRAST_MARGIN else None), margin


@dataclass
class _Candidate:
    """A gated alignment waiting for overlap resolution."""
    score: float
    match: FingerprintMatch
    alignment: _Alignment
    pat_len: int


def _clipped_run(al, accepted):
    """The part of al's span outside accepted episode spans with the most good points, trimmed again."""
    parts = [p for p in [al.span] if p[1] > p[0]]
    for a_lo, a_hi in accepted:
        a_lo, a_hi = a_lo - al.ep_base, a_hi - al.ep_base
        parts = [p for x, y in parts for p in ((x, min(y, a_lo)), (max(x, a_hi), y)) if p[1] > p[0]]
    parts = [t for t in (_trim_run(al.good, x, y, 1) for x, y in parts) if t]
    return max(parts, key=lambda p: int(al.good[p[0]:p[1]].sum()), default=None)


def _episode_span(cand):
    """(lo, hi) episode points of a candidate's span."""
    al = cand.alignment
    return al.ep_base + al.span[0], al.ep_base + al.span[1]


def _resolve_overlaps(candidates, fps, window, min_points):
    """Best match per stretch of time; a lower-ranked overlapping match keeps a long enough remainder."""
    accepted, spans = [], []
    full = [(c.match.pattern_id, *_episode_span(c)) for c in candidates if not c.match.partial]

    def yields(c):
        # A partial match gives way to a full match of another pattern covering half its span or more
        if not c.match.partial:
            return False
        lo, hi = _episode_span(c)
        return any(pid != c.match.pattern_id and 2 * (min(hi, f_hi) - max(lo, f_lo)) >= hi - lo
                   for pid, f_lo, f_hi in full)
    order = sorted(candidates, key=lambda c: (yields(c), -c.score, -c.match.coverage,
                                              -c.match.similarity, c.match.start))
    for cand in order:
        al, m = cand.alignment, cand.match
        lo, hi = _episode_span(cand)
        if any(lo < a_hi and hi > a_lo for a_lo, a_hi in spans):
            run = _clipped_run(al, spans)
            if run is None:
                continue
            # The whole match already passed every gate, so its remainder needs only the length of an ad
            span = _tight_span(al, run, window)
            if span[1] - span[0] < min_points - 1e-9:
                continue
            n_good, similarity = int(al.good[run[0]:run[1]].sum()), _similarity(al, run)
            lo, hi = al.ep_base + span[0], al.ep_base + span[1]
            m = FingerprintMatch(pattern_id=m.pattern_id, start=lo / fps, end=hi / fps,
                                 confidence=similarity, sponsor=m.sponsor, category=m.category,
                                 similarity=similarity, coverage=n_good / cand.pat_len,
                                 contrast_margin=m.contrast_margin, clipped_remainder=True,
                                 candidate_span=m.candidate_span, partial=m.partial)
        spans.append((lo, hi))
        accepted.append(m)
    accepted.sort(key=lambda m: m.start)
    return accepted


def _scan_fingerprints(ep_ints, ep_duration, patterns, *, strictness, min_coverage,
                       gap_tolerance_s, min_ad_length_s=DEFAULT_MIN_AD_LENGTH_S,
                       should_continue=None):
    """match_fingerprints plus the number of alignments measured."""
    if len(ep_ints) == 0 or ep_duration <= 0:
        return [], 0
    ep_arr = np.asarray(ep_ints, dtype=np.int64).astype(np.uint32)
    fps = len(ep_arr) / ep_duration
    gap_points = int(round(gap_tolerance_s * fps))
    window = max(1, int(round(fps)))
    index = _build_offset_index(ep_arr)
    candidates = []
    measured = 0
    for pattern_id, pat_ints, _pat_duration, sponsor, category in patterns:
        if should_continue is not None and not should_continue():
            break
        pat_arr = np.asarray(pat_ints, dtype=np.int64).astype(np.uint32)
        profile = _pattern_profile(pat_arr)
        informative, novelty = profile.informative, profile.novelty
        if profile.unmatchable_reason:
            logger.debug("Skipping fingerprint pattern %s: %s", pattern_id, profile.unmatchable_reason)
            continue
        needed = _evidence_needed(novelty, fps)
        # Evidence never exceeds the good point count, so this bound is safe for the prefilter
        min_good = max(min(min_coverage * len(pat_arr), FINGERPRINT_PARTIAL_EVIDENCE_S * fps),
                       needed) - 1e-9
        offsets, found = _candidate_offsets(pat_arr, informative, index, len(ep_arr))
        if found > len(offsets):
            logger.debug("Fingerprint pattern %s: %d candidate alignments, measuring the first %d",
                         pattern_id, found, len(offsets))
        passing = _passing_alignments(ep_arr, pat_arr, informative, offsets, min_good)
        # A shifted neighbour can pass the prefilter while the true alignment misses it
        near = offsets[(np.abs(offsets[:, None] - passing[None, :]) <= window).any(axis=1)]
        alignments = []
        for d in near.tolist():
            measured += 1
            alignments += [(d, al) for al in _measure_alignment(
                ep_arr, pat_arr, informative, novelty, d, gap_points, window)]
        for al in _closest_alignments(alignments, window):
            tally = _tally(al, al.run)
            gate, margin = _alignment_gate(ep_arr, pat_arr, al, tally, needed, fps, window,
                                           strictness, min_coverage)
            if gate:
                continue
            n_good, similarity = tally[:2]
            coverage = n_good / len(pat_arr)
            lo, hi = al.ep_base + al.span[0], al.ep_base + al.span[1]
            # Coverage is worth up to one second of evidence, so a chance hit past a shorter
            # pattern's end cannot hand the stretch to a longer pattern that is only partly present
            candidates.append(_Candidate(n_good + window * coverage, FingerprintMatch(
                pattern_id=pattern_id, start=lo / fps, end=hi / fps,
                confidence=similarity, sponsor=sponsor, category=category,
                similarity=similarity, coverage=coverage, contrast_margin=margin,
                candidate_span=(lo / fps, hi / fps),
                partial=_under_coverage(n_good, len(pat_arr), min_coverage)), al, len(pat_arr)))
    return _resolve_overlaps(candidates, fps, window, min_ad_length_s * fps), measured


def match_fingerprints(ep_ints, ep_duration, patterns, *, strictness, min_coverage,
                       gap_tolerance_s, min_ad_length_s=DEFAULT_MIN_AD_LENGTH_S,
                       should_continue=None):
    """Find stored patterns in an episode fingerprint and measure each run.

    ``patterns`` is a list of (pattern_id, pat_ints, pat_duration, sponsor, category). Stops early, returning
    what it has, when ``should_continue`` returns False.
    """
    return _scan_fingerprints(
        ep_ints, ep_duration, patterns, strictness=strictness, min_coverage=min_coverage,
        gap_tolerance_s=gap_tolerance_s, min_ad_length_s=min_ad_length_s,
        should_continue=should_continue)[0]


def fingerprint_coverage_ceiling(ints):
    """Highest coverage a pattern can reach: its share of points outside constant stretches."""
    pat_arr = np.asarray(ints, dtype=np.int64).astype(np.uint32)
    return float(_pattern_profile(pat_arr).informative.mean()) if len(pat_arr) else 0.0


def fingerprint_unmatchable_reason(ints, *, min_coverage=None):
    """Why a fingerprint could never be matched in its own audio, or None when it can be."""
    pat_arr = np.asarray(ints, dtype=np.int64).astype(np.uint32)

    def verdict():
        profile = _pattern_profile(pat_arr)
        if profile.unmatchable_reason:
            return profile.unmatchable_reason
        coverage = FINGERPRINT_SETTING_RANGES['fingerprint_min_coverage'][0] if min_coverage is None else min_coverage
        ceiling = fingerprint_coverage_ceiling(pat_arr)
        # Enough evidence for a partial match makes the coverage ceiling irrelevant
        partial_ok = float(profile.novelty.sum()) >= FINGERPRINT_PARTIAL_EVIDENCE_S * FINGERPRINT_NOMINAL_FPS - 1e-9
        if ceiling < coverage - 1e-9 and not partial_ok:
            return f"at most {ceiling:.0%} coverage reachable, {coverage:.0%} needed"
        # A pause can leave pieces too short to count, so the pattern must also find itself
        if not match_fingerprints(pat_arr, len(pat_arr) / FINGERPRINT_NOMINAL_FPS,
                                  [(0, pat_arr, 0.0, None, None)],
                                  strictness=FINGERPRINT_SETTING_RANGES['fingerprint_match_strictness'][0],
                                  min_coverage=coverage,
                                  gap_tolerance_s=FINGERPRINT_SETTING_RANGES['fingerprint_gap_tolerance_s'][0]):
            return f"does not match its own audio at {coverage:.0%} coverage"
        return None
    return _PATTERN_CACHE.get_or_compute(('matchable', _content_key(pat_arr.tobytes()), min_coverage), verdict)


def fingerprint_is_matchable(ints, *, min_coverage=None):
    """True when the matcher can find this fingerprint in its own source audio."""
    return fingerprint_unmatchable_reason(ints, min_coverage=min_coverage) is None


def clamped_fingerprint_setting(db, key):
    """A fingerprint setting clamped to its range; the default when unset, NaN or not a number."""
    default, lo, hi = FINGERPRINT_SETTING_RANGES[key]
    value = db.get_setting_float(key, default) if db else default
    return min(max(value, lo), hi) if np.isfinite(value) else default


def decode_fingerprint_ints(data):
    """Raw chromaprint ints of a fingerprint string or bytes, or None when it cannot be decoded."""
    if acoustid is None or not data:
        return None
    try:
        ints = _decode_chromaprint(data.encode('utf-8') if isinstance(data, str) else data)[0]
    except Exception:
        return None
    return ints if len(ints) else None


def _degraded_stretches(al, window):
    """(count, longest in points) of stretches of at least a window without sustained agreement in the span."""
    lo, hi = al.span
    hits, _c = _solid_points(al.good, window)
    covered = np.zeros(hi - lo + 2, dtype=bool)
    covered[1:-1][hits[(hits >= lo) & (hits < hi)] - lo] = True
    covered[0] = covered[-1] = True
    edges = np.flatnonzero(np.diff(covered.astype(np.int8)))
    lengths = edges[1::2] - edges[::2]
    lengths = lengths[lengths >= window]
    return len(lengths), int(lengths.max()) if len(lengths) else 0


def explain_match(ep_ints, ep_duration, pattern, *, strictness, min_coverage, gap_tolerance_s):
    """Measurements of one pattern at its best alignment and the gate that decided it."""
    _pattern_id, pat_ints, _pat_duration, _sponsor, _category = pattern
    ep_arr = np.asarray(ep_ints, dtype=np.int64).astype(np.uint32)
    pat_arr = np.asarray(pat_ints, dtype=np.int64).astype(np.uint32)
    fps = len(ep_arr) / ep_duration if len(ep_arr) and ep_duration > 0 else FINGERPRINT_NOMINAL_FPS
    window = max(1, int(round(fps)))
    ceiling = fingerprint_coverage_ceiling(pat_arr)
    profile = _pattern_profile(pat_arr)
    informative, novelty = profile.informative, profile.novelty
    distinctive = profile.unmatchable_reason is None
    out = {'candidates': 0, 'coverage_ceiling': ceiling,
           'matchable': fingerprint_is_matchable(pat_arr, min_coverage=min_coverage),
           'rejected_by': 'candidates', 'matched': False, 'partial': False}
    if not distinctive:
        out['rejected_by'] = 'not_distinctive'
        return out
    if len(ep_arr) == 0:
        return out
    needed = _evidence_needed(novelty, fps)
    offsets, found = _candidate_offsets(pat_arr, informative, _build_offset_index(ep_arr), len(ep_arr))
    out['candidates'] = found
    gap_points = int(round(gap_tolerance_s * fps))
    measured = [(d, al) for d in offsets.tolist() for al in _measure_alignment(
        ep_arr, pat_arr, informative, novelty, d, gap_points, window)]
    best = None
    for al in _closest_alignments(measured, window):
        tally = _tally(al, al.run)
        n_good, similarity, evidence, close = tally
        gate, margin = _alignment_gate(ep_arr, pat_arr, al, tally, needed, fps, window, strictness,
                                       min_coverage)
        rank = (gate is None, n_good, similarity)
        if best is None or rank > best[0]:
            best = (rank, al, n_good, similarity, evidence, close, gate, margin)
    if best is None:
        # Candidates exist but none holds a sustained run, so no alignment can reach the coverage
        out['rejected_by'] = 'coverage' if found else 'candidates'
        return out
    _rank, al, n_good, similarity, evidence, close, gate, margin = best
    if margin is None:
        margin = _contrast_margin(ep_arr, pat_arr, al, window)
    lo, hi = al.span
    inf = al.informative[lo:hi]
    diffs = al.diff[lo:hi][inf]
    stretches, longest = _degraded_stretches(al, window)
    out.update({
        'start': (al.ep_base + lo) / fps, 'end': (al.ep_base + hi) / fps,
        'similarity': similarity,
        'diff_p50': float(np.percentile(diffs, 50)), 'diff_p90': float(np.percentile(diffs, 90)),
        'good_share': float((diffs <= FINGERPRINT_GOOD_BITS).mean()),
        'edge_bits': al.edge_bits,
        'edge_good_share': float((diffs <= al.edge_bits).mean()),
        'degraded_stretches': stretches, 'longest_degraded_s': longest / fps,
        'coverage': n_good / len(pat_arr),
        'evidence_s': evidence / fps, 'evidence_needed_s': needed / fps,
        'close_evidence_s': close / fps,
        'close_evidence_needed_s': FINGERPRINT_CLOSE_EVIDENCE_S,
        'contrast_margin': margin, 'contrast_margin_needed': FINGERPRINT_CONTRAST_MARGIN,
        'rejected_by': gate, 'matched': gate is None,
        'partial': gate is None and _under_coverage(n_good, len(pat_arr), min_coverage),
    })
    return out


class AudioFingerprinter:
    """Audio fingerprinting using Chromaprint for identifying repeated ads: generate fingerprints for segments,
    compare them, and search for known ad fingerprints in new episodes."""

    def __init__(self, db=None):
        """db is the Database instance used to store/retrieve fingerprints."""
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
        """Generate a fingerprint for an audio segment (start in seconds, duration None = entire file).

        Returns AudioFingerprint or None if generation failed.
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

    def _generate_full_fingerprint(
        self, audio_path: str, timeout: int = FPCALC_TIMEOUT_FULL, *, quiet: bool = False
    ) -> tuple[list[int], float] | None:
        """Generate raw fingerprint for entire audio file in one fpcalc call.

        timeout is the fpcalc wall-clock cap; defaults to the full-file budget, and a request-thread caller can
        pass a shorter bound so a stalled decode degrades gracefully. quiet logs failures at DEBUG (the caller
        reports them once, labelled). Returns (raw_int_array, duration) or None on failure.
        """
        if not self._fpcalc_path:
            return None

        try:
            cmd = [self._fpcalc_path, '-raw', '-json', '-length', '0', audio_path]
            result = tracked_run(cmd, capture_output=True, timeout=timeout)

            data = _fpcalc_output_or_none(
                result, 'Full-file fpcalc', logging.DEBUG if quiet else logging.WARNING)
            if data is None:
                return None

            raw_ints = data.get('fingerprint', [])
            duration = data.get('duration', 0)

            if not isinstance(raw_ints, list):
                return None

            logger.debug(f"Full-file fingerprint: {len(raw_ints)} ints for {duration:.1f}s")
            return (raw_ints, duration)

        except subprocess.TimeoutExpired:
            logger.log(logging.DEBUG if quiet else logging.ERROR, "Full-file fingerprint generation timed out")
            return None
        except Exception as e:
            if quiet:
                logger.debug(f"Full-file fingerprint generation failed: {type(e).__name__}")
            else:
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

        Extract-then-fingerprint twin of :meth:`generate_fingerprint` that keeps the ``-raw`` int array (comparable
        against full-file raw fingerprints via the window-occurrence helpers). Returns (raw_ints, duration) or None.
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

        One full-file raw Chromaprint fingerprint, then the windows recurring at least ``min_count`` times.
        Loudness-independent, so it catches level-matched stings the loudness scan misses. Pass
        ``target_fingerprint`` (``(raw_ints, duration)``) to avoid a second fpcalc pass. Returns {start, end,
        count} dicts in descending recurrence order, or [] if fpcalc is unavailable or fails.
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
        """Find intro/outro cues by comparing this episode's head and tail fingerprint against recent siblings.

        A real intro/outro plays once per episode (so within-episode recurrence misses it) but recurs across
        episodes near the start/end. Pass ``target_fingerprint`` (``(raw_ints, duration)``) to reuse a fingerprint.
        Returns ``[{start, end, kind: 'intro'|'outro', episodeMatches}]``, or [] when fpcalc is unavailable or
        fewer than ``min_matches`` siblings are usable.
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
        # overlap, even on short episodes; otherwise one shared segment would be
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
        # Keep the runs nearest the episode end; the true sign-off outro is the
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
        """Find recurring audio segments anywhere in the episode body across siblings.

        Sibling of discover_cross_episode_cues without the head/tail restriction, so mid-episode stings (e.g. ad-break
        tones) surface alongside intro/outro material. Pass ``target_fingerprint`` (``(raw_ints, duration)``) to reuse a
        fingerprint. Returns ``[{start, end, kind: 'recurring', episodeMatches}]`` (same shape as
        discover_cross_episode_cues), or [] when fpcalc is unavailable or fewer than ``min_matches`` siblings are usable.

        Default max_len_s is AUDIO_CUE_FP_MAX_LEN_SECONDS (30s): mid-episode stings are short and the cap keeps
        false-positive body content out. Default min_duration is AUDIO_CUE_XEP_BODY_MIN_DURATION (2s), not the 3s
        intro/outro floor, so recurring 1.5-2.5s ad stings survive. All sibling arrays go through one
        _find_shared_segments call, whose claimed_until keeps results non-overlapping and avoids duplicates.
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
        """Count how many times a captured cue window recurs in its episode (0 if fpcalc is unavailable or fails).

        Used at template-create time to warn on a weak cue: 1 means the sound appears only where captured; >=2
        means it recurs. Runs on the create request thread, so the fingerprint is bounded by the shorter
        FPCALC_TIMEOUT: a normal episode fingerprints in seconds, and a stalled decode gives up well before the
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
        """Decode known fingerprint strings to a list of (pattern_id, raw_int_array, duration, sponsor, category)."""
        if acoustid is None:
            logger.debug("acoustid not available for fingerprint decoding")
            return []

        decoded = []
        for pattern_id, fp_str, duration, sponsor, category in known_fingerprints:
            try:
                fp_bytes = fp_str.encode('utf-8') if isinstance(fp_str, str) else fp_str
                result = _decode_chromaprint(fp_bytes)
                if result and result[0]:
                    decoded.append((pattern_id, result[0], duration, sponsor, category))
                else:
                    logger.debug(f"Skipping fingerprint pattern {pattern_id}: nothing decodable")
            except Exception as e:
                logger.debug(f"Skipping fingerprint pattern {pattern_id}: decode failed: {e}")

        return decoded

    @staticmethod
    def _full_fingerprint_timeout(duration: float) -> int:
        """fpcalc budget for a whole episode: scales with length, never below the floor."""
        return max(FPCALC_TIMEOUT_FULL, int(duration * FINGERPRINT_TIMEOUT_FACTOR))

    def _matching_settings(self) -> tuple[float, float, float]:
        """(strictness, min_coverage, gap_tolerance_s) from settings, config defaults otherwise."""
        return (
            clamped_fingerprint_setting(self.db, 'fingerprint_match_strictness'),
            clamped_fingerprint_setting(self.db, 'fingerprint_min_coverage'),
            clamped_fingerprint_setting(self.db, 'fingerprint_gap_tolerance_s'),
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
                set(row.get('sponsor_tags') or ()),
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

        matches, measured = _scan_fingerprints(
            raw_ints, fp_duration, decoded_known, strictness=strictness,
            min_coverage=min_coverage, gap_tolerance_s=gap_tolerance_s,
            min_ad_length_s=clamped_fingerprint_setting(self.db, 'fingerprint_min_ad_length_s'),
            should_continue=should_continue)
        for m in matches:
            logger.debug(
                f"Fingerprint match: pattern={m.pattern_id} {m.start:.1f}s-{m.end:.1f}s "
                f"(similarity={m.similarity:.2f}, coverage={m.coverage:.2f})"
            )
        elapsed = time.monotonic() - started
        if stop_reason:
            logger.warning(f"Fingerprint scan {stop_reason[0]} after {elapsed:.0f}s "
                           f"with {len(matches)} matches")
        else:
            logger.info(f"Fingerprint scan: {len(decoded_known)} patterns in scope, "
                        f"{measured} candidates measured, {len(matches)} matches, {elapsed:.1f}s")
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
        episode_label: str | None = None,
    ) -> list[FingerprintMatch]:
        """Search for known ad fingerprints in an audio file; returns FingerprintMatch objects.

        known_fingerprints: (pattern_id, fingerprint, duration, sponsor, category) tuples, used as given; if None,
            loads from the database and keeps those whose scope applies to podcast_id / network_id / podcast_tags.
        timeout: maximum seconds to spend matching (default 600s / 10 minutes); partial results if exceeded.
        cancel_event: optional threading.Event; if set, scanning stops early.
        episode_label: "slug:episode_id", named in the warning when the audio cannot be fingerprinted.

        Empty when the whole-file fingerprint fails.
        """
        if not self.is_available():
            return []

        if self.db and not self.db.get_setting_bool('fingerprint_enabled', True):
            return []

        if known_fingerprints is None and self.db:
            known_fingerprints = self._in_scope_fingerprints(podcast_id, network_id, podcast_tags)

        if not known_fingerprints:
            return []

        total_duration = self._get_audio_duration(audio_path)
        if total_duration <= 0:
            return []

        logger.debug(f"Searching {total_duration:.1f}s audio for {len(known_fingerprints)} known fingerprints")

        label = episode_label or 'unknown episode'
        full_fp = self._generate_full_fingerprint(
            audio_path, timeout=self._full_fingerprint_timeout(total_duration), quiet=True)
        if full_fp is not None:
            raw_ints, fp_duration = full_fp
            decoded_known = self._decode_known_fingerprints(known_fingerprints)
            if decoded_known:
                logger.debug(
                    f"Matching fingerprints "
                    f"({len(raw_ints)} ints, {len(decoded_known)} patterns)"
                )
                return self._match_decoded(
                    raw_ints, fp_duration or total_duration, decoded_known, timeout, cancel_event)
            logger.warning(f"[{label}] Could not decode known fingerprints, skipping audio matching")
            return []
        logger.warning(f"[{label}] Full-file fingerprint failed, skipping audio matching")
        return []

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
        """Get duration of audio file in seconds (via utils.audio.get_audio_duration)."""
        duration = get_audio_duration(audio_path)
        return duration if duration is not None else 0.0

    def _probed_duration(self, audio_path: str) -> float:
        """_get_audio_duration, remembered per file version so the patterns of one episode probe it once."""
        try:
            stat = os.stat(audio_path)
        except OSError:
            return self._get_audio_duration(audio_path)
        return _PATTERN_CACHE.get_or_compute(
            ('duration', str(audio_path), stat.st_size, stat.st_mtime_ns),
            lambda: self._get_audio_duration(audio_path), keep=lambda value: value > 0)

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
        if not self.db:
            return FingerprintStoreResult(False, 'fingerprint_failed')
        if not force:
            pattern = self.db.get_ad_pattern_by_id(pattern_id)
            if pattern and pattern.get('audio_removed'):
                return FingerprintStoreResult(False, 'audio_removed')
        if not audio_path or not os.path.isfile(audio_path):
            return FingerprintStoreResult(False, 'original_not_retained')

        # A span past the end of the file is stored as the audio actually decoded
        decoded = self._probed_duration(audio_path)
        if decoded > 0:
            end = min(end, decoded)
        duration = end - start
        if duration < clamped_fingerprint_setting(self.db, 'fingerprint_min_ad_length_s'):
            logger.debug(f"Segment too short for fingerprinting: {duration:.1f}s")
            return FingerprintStoreResult(False, 'segment_too_short')
        if not self.is_available():
            return FingerprintStoreResult(False, 'fingerprint_failed')

        fingerprint = self.generate_fingerprint(audio_path, start, duration)
        if not fingerprint or not fingerprint.fingerprint:
            return FingerprintStoreResult(False, 'fingerprint_failed')

        # An undecodable string is stored unchecked; the matcher skips it
        ints = decode_fingerprint_ints(fingerprint.fingerprint)
        if ints is None:
            logger.warning(f"Pattern {pattern_id} fingerprint could not be decoded; stored unchecked")
        elif not fingerprint_is_matchable(
                ints, min_coverage=clamped_fingerprint_setting(self.db, 'fingerprint_min_coverage')):
            logger.info(f"Pattern {pattern_id} audio is not distinctive enough to match")
            return FingerprintStoreResult(False, 'not_distinctive')

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
