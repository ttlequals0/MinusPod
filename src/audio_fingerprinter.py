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
from typing import List, Optional, Tuple
import json

import numpy as np

try:
    import acoustid
except ImportError:
    acoustid = None

from utils.audio import get_audio_duration
from utils.subprocess_registry import tracked_run
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
    FINGERPRINT_MATCH_THRESHOLD as MATCH_THRESHOLD,
)

logger = logging.getLogger('podcast.fingerprint')

# Minimum duration for fingerprinting (seconds)
MIN_SEGMENT_DURATION = 5.0

# Fingerprint chunk size for sliding window search (seconds)
FINGERPRINT_CHUNK_SIZE = 10.0

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
    sponsor: Optional[str] = None


@dataclass
class AudioFingerprint:
    """Represents an audio fingerprint."""
    fingerprint: str  # Raw chromaprint fingerprint
    duration: float
    pattern_id: Optional[int] = None


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

    def _find_fpcalc(self) -> Optional[str]:
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
    ) -> Optional[AudioFingerprint]:
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

            if result.returncode != 0:
                logger.warning(f"fpcalc failed: {result.stderr.decode()}")
                return None

            # Parse JSON output
            data = json.loads(result.stdout.decode())

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
        fp1: List[int],
        fp2: List[int],
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
    ) -> Optional[Tuple[List[int], float]]:
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

            if result.returncode != 0:
                logger.warning(f"Full-file fpcalc failed: {result.stderr.decode()}")
                return None

            data = json.loads(result.stdout.decode())
            raw_ints = data.get('fingerprint', [])
            duration = data.get('duration', 0)

            if not raw_ints or not isinstance(raw_ints, list):
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
        cmd = ['ffmpeg', '-y', '-i', audio_path, '-ss', str(start)]
        if duration is not None:
            cmd.extend(['-t', str(duration)])
        cmd.extend(['-ac', '1', '-ar', '16000', '-f', 'wav', tmp_path])
        tracked_run(cmd, capture_output=True, timeout=FFMPEG_SHORT_TIMEOUT, check=True)

    def generate_raw_span_fingerprint(
        self, audio_path: str, start_s: float, end_s: float,
    ) -> Optional[Tuple[List[int], float]]:
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
            if result.returncode != 0:
                logger.warning(f"span fpcalc failed: {result.stderr.decode()}")
                return None
            data = json.loads(result.stdout.decode())
            raw_ints = data.get('fingerprint', [])
            if not raw_ints or not isinstance(raw_ints, list):
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
        known_fingerprints: List[Tuple[int, str, float, str]]
    ) -> List[Tuple[int, List[int], float, str]]:
        """Decode known fingerprint strings to raw int arrays.

        Returns:
            List of (pattern_id, raw_int_array, duration, sponsor)
        """
        if acoustid is None:
            logger.warning("acoustid not available for fingerprint decoding")
            return []

        decoded = []
        for pattern_id, fp_str, duration, sponsor in known_fingerprints:
            try:
                fp_bytes = fp_str.encode('utf-8') if isinstance(fp_str, str) else fp_str
                result = acoustid.chromaprint.decode_fingerprint(fp_bytes)
                if result and result[0]:
                    decoded.append((pattern_id, result[0], duration, sponsor))
                else:
                    logger.warning(f"Could not decode fingerprint for pattern {pattern_id}")
            except Exception as e:
                logger.warning(f"Failed to decode fingerprint for pattern {pattern_id}: {e}")

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

    def _find_matches_fast(
        self,
        raw_ints: List[int],
        fp_duration: float,
        decoded_known: List[Tuple[int, List[int], float, str]],
        total_duration: float,
        timeout: int,
        cancel_event: Optional[threading.Event]
    ) -> List[FingerprintMatch]:
        """Fast fingerprint matching using pre-computed full-file fingerprint.

        Slides through the raw int array comparing slices against decoded
        known fingerprints. No subprocess calls -- pure Python array operations.
        """
        matches = []
        # fpcalc default sample rate produces ~8 ints/sec; compute actual from data
        ints_per_second = len(raw_ints) / fp_duration if fp_duration > 0 else 8.0
        scan_start_time = time.time()
        last_log_time = scan_start_time
        position = 0.0

        while position < total_duration - MIN_SEGMENT_DURATION:
            action, last_log_time = self._scan_preamble(
                scan_start_time, last_log_time, position, total_duration,
                timeout, len(matches), cancel_event
            )
            if action != "continue":
                break

            # Compute indices into raw_ints for current window (avoid list copy)
            start_idx = int(position * ints_per_second)
            end_idx = int((position + FINGERPRINT_CHUNK_SIZE) * ints_per_second)
            end_idx = min(end_idx, len(raw_ints))

            if end_idx - start_idx < 4:
                position += SLIDING_STEP_SIZE
                continue

            matched = False
            for pattern_id, known_ints, known_duration, sponsor in decoded_known:
                similarity = self._calculate_similarity(
                    raw_ints, known_ints, fp1_start=start_idx, fp1_end=end_idx
                )

                if similarity >= MATCH_THRESHOLD:
                    match = FingerprintMatch(
                        pattern_id=pattern_id,
                        start=position,
                        end=position + known_duration,
                        confidence=similarity,
                        sponsor=sponsor
                    )
                    matches.append(match)
                    logger.info(
                        f"Fingerprint match: pattern={pattern_id} "
                        f"at {position:.1f}s (confidence={similarity:.2f})"
                    )
                    position += known_duration
                    matched = True
                    break

            if not matched:
                position += SLIDING_STEP_SIZE

        matches = self._merge_overlapping_matches(matches)

        scan_elapsed = time.time() - scan_start_time
        logger.info(
            f"Fast fingerprint scan completed in {scan_elapsed:.1f}s, "
            f"found {len(matches)} matches"
        )

        return matches

    def find_matches(
        self,
        audio_path: str,
        known_fingerprints: List[Tuple[int, str, float, str]] = None,
        timeout: int = 600,
        cancel_event: Optional[threading.Event] = None
    ) -> List[FingerprintMatch]:
        """
        Search for known ad fingerprints in an audio file.

        Uses a sliding window approach to find matches at any position.

        Args:
            audio_path: Path to audio file to search
            known_fingerprints: List of (pattern_id, fingerprint, duration, sponsor)
                               If None, loads from database
            timeout: Maximum seconds to spend scanning (default 600s / 10 minutes).
                     Returns partial results if exceeded.
            cancel_event: Optional threading.Event; if set, scanning stops early.

        Returns:
            List of FingerprintMatch objects for found ads
        """
        if not self.is_available():
            return []

        # Load known fingerprints from database if not provided
        if known_fingerprints is None and self.db:
            known_fingerprints = self._load_fingerprints_from_db()

        if not known_fingerprints:
            return []

        matches = []
        broken_patterns = set()

        # Get total duration of audio
        total_duration = self._get_audio_duration(audio_path)
        if total_duration <= 0:
            return []

        logger.info(f"Searching {total_duration:.1f}s audio for {len(known_fingerprints)} known fingerprints")

        # Fast path: generate one full-file fingerprint and compare by slicing
        full_fp = self._generate_full_fingerprint(audio_path)
        if full_fp is not None:
            raw_ints, fp_duration = full_fp
            decoded_known = self._decode_known_fingerprints(known_fingerprints)
            if decoded_known:
                logger.info(
                    f"Using fast fingerprint scan "
                    f"({len(raw_ints)} ints, {len(decoded_known)} patterns)"
                )
                return self._find_matches_fast(
                    raw_ints, fp_duration, decoded_known, total_duration,
                    timeout, cancel_event
                )
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
                for pattern_id, known_fp, known_duration, sponsor in known_fingerprints:
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

                    if similarity >= MATCH_THRESHOLD:
                        # Found a match
                        match = FingerprintMatch(
                            pattern_id=pattern_id,
                            start=position,
                            end=position + known_duration,
                            confidence=similarity,
                            sponsor=sponsor
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

    def _load_fingerprints_from_db(self) -> List[Tuple[int, str, float, str]]:
        """Load known fingerprints from database with sponsors (single JOIN query)."""
        if not self.db:
            return []

        try:
            fingerprints = self.db.get_all_fingerprints_with_sponsors()
            result = []
            for fp in fingerprints:
                # Fingerprint may be stored as bytes or string
                fp_data = fp.get('fingerprint', b'')
                if isinstance(fp_data, bytes):
                    fp_str = fp_data.decode('utf-8', errors='ignore')
                else:
                    fp_str = str(fp_data)

                result.append((
                    fp['pattern_id'],
                    fp_str,
                    fp['duration'],
                    fp.get('sponsor')
                ))
            return result
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
        matches: List[FingerprintMatch]
    ) -> List[FingerprintMatch]:
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
                    sponsor=current.sponsor or match.sponsor
                )
            else:
                merged.append(current)
                current = match

        merged.append(current)
        return merged

    def store_fingerprint(
        self,
        pattern_id: int,
        audio_path: str,
        start: float,
        end: float
    ) -> bool:
        """
        Generate and store a fingerprint for a detected ad segment.

        Args:
            pattern_id: ID of the ad pattern
            audio_path: Path to the episode audio
            start: Start time of the ad
            end: End time of the ad

        Returns:
            True if fingerprint was stored successfully
        """
        if not self.db or not self.is_available():
            return False

        duration = end - start
        if duration < MIN_SEGMENT_DURATION:
            logger.debug(f"Segment too short for fingerprinting: {duration:.1f}s")
            return False

        fingerprint = self.generate_fingerprint(audio_path, start, duration)
        if not fingerprint or not fingerprint.fingerprint:
            return False

        try:
            # Store fingerprint as bytes
            fp_bytes = fingerprint.fingerprint.encode('utf-8')
            self.db.create_audio_fingerprint(
                pattern_id=pattern_id,
                fingerprint=fp_bytes,
                duration=duration
            )
            logger.info(f"Stored fingerprint for pattern {pattern_id}")
            return True
        except Exception as e:
            logger.error(f"Failed to store fingerprint: {e}")
            return False
