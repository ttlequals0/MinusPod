"""Per-feed audio cue template matcher (#350).

Each template is a short MFCC matrix the user marked on one episode. This
detector decodes the full episode to MFCC once, then for every template runs a
normalized cross-correlation against the episode-MFCC time axis. Each above-
threshold peak becomes an ``audio_cue`` ``AudioSegmentSignal`` that rides the
existing pipeline -- prompt injection in ``AudioEnforcer`` and boundary snap
in ``cue_boundary_snap``.

Why MFCC NCC and not Chromaprint / spectrogram pixel match:
- Chromaprint's sub-fingerprint resolution is ~124 ms, too coarse to snap an
  ad start edge to the millisecond-resolution we need for short stingers.
- MFCC NCC is the canonical short-acoustic-event template match in the
  literature. ~1-2s wall time per template on a 1-hour episode at 16 kHz.
- The zero-mean cross-correlation cancels the constant per-coefficient offset
  between the user's marked occurrence and other occurrences of the same sound.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Dict, List, Optional

import numpy as np
from scipy.signal import fftconvolve

from config import (
    AUDIO_CUE_TEMPLATE_SCORE,
    AUDIO_CUE_TYPE_DEFAULT,
    AUDIO_CUE_NEAR_MISS_MAX_PER_TEMPLATE,
    audio_cue_type_role,
)
from .base import AudioSegmentSignal, SignalType
from .cue_features import (
    FRAME_HOP_MS,
    SAMPLE_RATE_HZ,
    FORMANT_LO_HZ,
    FORMANT_HI_HZ,
    compute_mfcc,
    decode_pcm_window,
    deserialize_mfcc,
    int16_bytes_to_pcm,
)
from utils.audio import get_audio_duration

logger = logging.getLogger('podcast.audio_analysis.cue_template')


# Default threshold; tuneable via DB setting `audio_cue_template_score`. The
# value and its rationale live in config.AUDIO_CUE_TEMPLATE_SCORE.
DEFAULT_MATCH_SCORE = AUDIO_CUE_TEMPLATE_SCORE
# Max matches we report per template per episode -- bounds prompt size.
MAX_MATCHES_PER_TEMPLATE = 50
# Episode is decoded in chunks of this many seconds with `OVERLAP_S` overlap
# so a template straddling a chunk boundary still matches. Keep memory bounded
# on very long episodes.
CHUNK_SECONDS = 600
CHUNK_OVERLAP_SECONDS = 30


@dataclass
class _Template:
    template_id: int
    label: str
    mfcc: np.ndarray             # (n_frames, n_coeffs) float32
    duration_s: float
    n_coeffs: int
    cue_type: str
    role: str
    score_threshold: Optional[float] = None  # per-template override; None = use instance
    eff_threshold: float = 0.0   # precomputed: score_threshold if set, else instance threshold
    pick_floor: float = 0.0      # precomputed: min(eff_threshold, near_miss_floor) or eff_threshold


class AudioCueTemplateMatcher:
    """Detect occurrences of stored cue templates in an episode."""

    def __init__(
        self,
        templates: List[Dict],
        score_threshold: float = DEFAULT_MATCH_SCORE,
        max_matches_per_template: int = MAX_MATCHES_PER_TEMPLATE,
        formant_atten_db: float = 0.0,
        formant_lo_hz: float = FORMANT_LO_HZ,
        formant_hi_hz: float = FORMANT_HI_HZ,
        near_miss_floor: Optional[float] = None,
        ignore_per_template_thresholds: bool = False,
    ):
        self.score_threshold = score_threshold
        self.max_matches_per_template = max_matches_per_template
        # Peaks in [floor, threshold) become advisory near-misses when set.
        self.near_miss_floor = near_miss_floor
        # Global voiceover-robust profile (#350); 0 dB = off = un-weighted MFCC.
        self._formant_atten_db = float(formant_atten_db)
        self._formant_lo_hz = float(formant_lo_hz)
        self._formant_hi_hz = float(formant_hi_hz)
        self._templates: List[_Template] = []
        for row in templates:
            try:
                n_coeffs = int(row['n_coeffs'])
                mfcc = None
                if self._formant_atten_db > 0 and row.get('pcm_blob'):
                    # Re-derive the MFCC from the stored PCM under the formant
                    # profile so template and target share the same weighting (the
                    # stored mfcc_blob was computed at 0 dB). On any failure fall
                    # back to that stored blob rather than dropping the template.
                    try:
                        mfcc = compute_mfcc(
                            int16_bytes_to_pcm(row['pcm_blob']), n_coeffs=n_coeffs,
                            formant_atten_db=self._formant_atten_db,
                            formant_lo_hz=self._formant_lo_hz, formant_hi_hz=self._formant_hi_hz)
                    except Exception as e:
                        logger.warning(
                            "Cue template %s: formant re-derivation failed (%s); "
                            "using stored MFCC", row.get('id'), e)
                        mfcc = None
                if mfcc is None:
                    mfcc = deserialize_mfcc(row['mfcc_blob'], n_coeffs)
            except (ValueError, KeyError) as e:
                logger.warning(
                    f"Skipping cue template {row.get('id')}: bad mfcc blob ({e})"
                )
                continue
            if mfcc.shape[0] < 3:
                logger.warning(
                    f"Skipping cue template {row.get('id')}: only "
                    f"{mfcc.shape[0]} frames"
                )
                continue
            cue_type = row.get('cue_type') or AUDIO_CUE_TYPE_DEFAULT
            raw_thr = row.get('score_threshold')
            per_tpl_threshold = (None if ignore_per_template_thresholds
                                 else (float(raw_thr) if raw_thr is not None else None))
            eff_thr = per_tpl_threshold if per_tpl_threshold is not None else score_threshold
            pick_flr = (min(eff_thr, near_miss_floor)
                        if near_miss_floor is not None else eff_thr)
            self._templates.append(_Template(
                template_id=int(row['id']),
                label=row.get('label') or f"template-{row['id']}",
                mfcc=mfcc,
                duration_s=float(row['duration_s']),
                n_coeffs=n_coeffs,
                cue_type=cue_type,
                role=audio_cue_type_role(cue_type),
                score_threshold=per_tpl_threshold,
                eff_threshold=eff_thr,
                pick_floor=pick_flr,
            ))

    @property
    def is_usable(self) -> bool:
        return bool(self._templates)

    def detect(self, audio_path: str) -> List[AudioSegmentSignal]:
        """Run all templates against the episode at ``audio_path``.

        Production callers want just the signals; debug callers can use
        :meth:`detect_with_debug` to also receive per-template peak scores.
        """
        signals, _ = self.detect_with_debug(audio_path)
        return signals

    def detect_with_debug(self, audio_path: str):
        """Like :meth:`detect`, but also returns per-template peak scores.

        Returns ``(signals, debug)`` where ``debug`` is::

            {
                'templates': [
                    {'id': int, 'label': str, 'peak_score': float,
                     'duration_s': float, 'match_count': int},
                    ...
                ],
                'threshold': float,
                'elapsed_s': float,
            }
        """
        if not self._templates:
            return [], {'templates': [], 'threshold': self.score_threshold,
                        'elapsed_s': 0.0, 'near_misses': []}

        duration = get_audio_duration(audio_path)
        if not duration:
            logger.warning("Could not determine audio duration for cue template detection")
            return [], {'templates': [], 'threshold': self.score_threshold,
                        'elapsed_s': 0.0, 'near_misses': []}

        signals: List[AudioSegmentSignal] = []
        per_template_matches: Dict[int, List[AudioSegmentSignal]] = {
            t.template_id: [] for t in self._templates
        }
        # Track the highest correlation score per template across all chunks,
        # even when it does not clear the threshold, so tuning the cue score
        # is observable from the logs.
        per_template_peak_score: Dict[int, float] = {
            t.template_id: 0.0 for t in self._templates
        }
        # Sub-threshold peaks in [near_miss_floor, threshold) per template
        # (#350 Phase 6). Stays empty when near_miss_floor is None.
        per_template_near_misses: Dict[int, List[Dict]] = {
            t.template_id: [] for t in self._templates
        }

        start_wall = time.time()
        chunk_start = 0.0
        while chunk_start < duration:
            chunk_end = min(duration, chunk_start + CHUNK_SECONDS)
            try:
                pcm = decode_pcm_window(
                    audio_path, chunk_start, chunk_end, SAMPLE_RATE_HZ,
                )
            except RuntimeError as e:
                logger.warning(f"Cue chunk decode failed at {chunk_start:.1f}s: {e}")
                break
            chunk_mfcc = compute_mfcc(
                pcm, formant_atten_db=self._formant_atten_db,
                formant_lo_hz=self._formant_lo_hz, formant_hi_hz=self._formant_hi_hz)
            if chunk_mfcc.shape[0]:
                self._scan_chunk(
                    chunk_mfcc, chunk_start,
                    per_template_matches, per_template_peak_score,
                    per_template_near_misses,
                )

            if chunk_end >= duration:
                break
            chunk_start = chunk_end - CHUNK_OVERLAP_SECONDS

        kept_signals_by_template: Dict[int, List[AudioSegmentSignal]] = {}
        for tid, matches in per_template_matches.items():
            if not matches:
                continue
            matches.sort(key=lambda s: s.confidence, reverse=True)
            kept = matches[:self.max_matches_per_template]
            # Drop duplicates from chunk overlap: peaks within one template
            # duration of each other are the same event.
            kept = self._dedupe(kept)
            kept_signals_by_template[tid] = kept
            signals.extend(kept)

        # Dedupe and cap near-misses per template.
        near_misses: List[Dict] = []
        for tid, misses in per_template_near_misses.items():
            if not misses:
                continue
            deduped = self._dedupe_near_misses(
                misses, kept_signals_by_template.get(tid, []))
            near_misses.extend(deduped[:AUDIO_CUE_NEAR_MISS_MAX_PER_TEMPLATE])

        elapsed = time.time() - start_wall
        # Count matches per template in one pass, reused for both the tuning
        # log and the debug payload.
        match_counts: Dict[int, int] = {t.template_id: 0 for t in self._templates}
        for s in signals:
            tid = (s.details or {}).get('template_id')
            if tid in match_counts:
                match_counts[tid] += 1
        # Per-template tuning telemetry: even zero-match templates report
        # their best correlation against this episode so the user can see
        # whether to lower the threshold or remark the cue.
        for tpl in self._templates:
            peak = per_template_peak_score.get(tpl.template_id, 0.0)
            logger.info(
                f"Cue template {tpl.template_id} ({tpl.label!r}): "
                f"peak score {peak:.3f} vs threshold {tpl.eff_threshold:.3f}, "
                f"{match_counts[tpl.template_id]} match(es)"
            )
        logger.info(
            f"Cue template match: {len(self._templates)} template(s), "
            f"{len(signals)} signal(s), {len(near_misses)} near-miss(es) in {elapsed:.1f}s"
        )
        debug = {
            'threshold': self.score_threshold,
            'elapsed_s': round(elapsed, 2),
            'templates': [
                {
                    'id': tpl.template_id,
                    'label': tpl.label,
                    'duration_s': tpl.duration_s,
                    'peak_score': round(per_template_peak_score.get(tpl.template_id, 0.0), 3),
                    'match_count': match_counts[tpl.template_id],
                    # eff_threshold is what actually gates matches for this template;
                    # it may differ from the instance score_threshold when a per-template
                    # override is set. Surface it so callers can report accurate
                    # pass/fail without comparing against the instance value.
                    'eff_threshold': tpl.eff_threshold,
                }
                for tpl in self._templates
            ],
            'near_misses': near_misses,
        }
        return signals, debug

    def _scan_chunk(
        self,
        chunk_mfcc: np.ndarray,
        chunk_offset_s: float,
        per_template_matches: Dict[int, List[AudioSegmentSignal]],
        per_template_peak_score: Dict[int, float],
        per_template_near_misses: Dict[int, List[Dict]],
    ) -> None:
        hop_s = FRAME_HOP_MS / 1000.0
        for tpl in self._templates:
            eff_threshold = tpl.eff_threshold
            pick_floor = tpl.pick_floor
            if tpl.mfcc.shape[1] != chunk_mfcc.shape[1]:
                logger.warning(
                    f"Template {tpl.template_id} n_coeffs={tpl.mfcc.shape[1]} "
                    f"!= chunk n_coeffs={chunk_mfcc.shape[1]}; skipping"
                )
                continue
            if chunk_mfcc.shape[0] < tpl.mfcc.shape[0]:
                continue
            scores = _sliding_zncc(chunk_mfcc, tpl.mfcc)
            if not scores.size:
                continue
            chunk_peak = float(scores.max())
            if chunk_peak > per_template_peak_score[tpl.template_id]:
                per_template_peak_score[tpl.template_id] = chunk_peak
            # Local-maximum peak pick within a window of template duration.
            tpl_frames = tpl.mfcc.shape[0]
            suppress_frames = max(1, tpl_frames)
            peaks = _peak_pick(scores, pick_floor, suppress_frames)
            for frame_idx, score in peaks:
                start_s = chunk_offset_s + frame_idx * hop_s
                end_s = start_s + tpl.duration_s
                if score >= eff_threshold:
                    confidence = float(min(0.99, max(0.0, score)))
                    per_template_matches[tpl.template_id].append(AudioSegmentSignal(
                        start=round(start_s, 3),
                        end=round(end_s, 3),
                        signal_type=SignalType.AUDIO_CUE.value,
                        confidence=round(confidence, 3),
                        details={
                            'source': 'template',
                            'template_id': tpl.template_id,
                            'label': tpl.label,
                            'cue_type': tpl.cue_type,
                            'role': tpl.role,
                            'score': round(score, 3),
                        },
                    ))
                else:
                    # In [near_miss_floor, threshold): advisory near-miss only.
                    # Append unconditionally -- finalize() dedupes then caps by
                    # score so the strongest survive; _peak_pick bounds memory.
                    per_template_near_misses[tpl.template_id].append({
                        'template_id': tpl.template_id,
                        'label': tpl.label,
                        'cue_type': tpl.cue_type,
                        'role': tpl.role,
                        'start_s': round(start_s, 3),
                        'end_s': round(end_s, 3),
                        'score': round(score, 3),
                    })

    @staticmethod
    def _dedupe(matches: List[AudioSegmentSignal]) -> List[AudioSegmentSignal]:
        """Drop matches whose start is within 0.25s of a kept higher-score match.

        Templates are short so cross-chunk overlap and near-peaks of the same
        event can land within a hundred ms of each other.
        """
        matches.sort(key=lambda s: s.confidence, reverse=True)
        kept: List[AudioSegmentSignal] = []
        for m in matches:
            if any(abs(m.start - k.start) < 0.25 for k in kept):
                continue
            kept.append(m)
        kept.sort(key=lambda s: s.start)
        return kept

    @staticmethod
    def _dedupe_near_misses(misses: List[Dict],
                            kept_signals: List[AudioSegmentSignal]) -> List[Dict]:
        """Drop near-misses within 0.25s of a kept signal or a stronger miss.

        Highest-score first so the strongest miss in a cluster survives. A miss
        that coincides with a kept above-threshold signal is the same event and
        is dropped -- the signal already tells that story.
        """
        signal_starts = [s.start for s in kept_signals]
        misses.sort(key=lambda m: m['score'], reverse=True)
        kept: List[Dict] = []
        for m in misses:
            start = m['start_s']
            if any(abs(start - s) < 0.25 for s in signal_starts):
                continue
            if any(abs(start - k['start_s']) < 0.25 for k in kept):
                continue
            kept.append(m)
        kept.sort(key=lambda m: m['start_s'])
        return kept


def peak_zncc(haystack: np.ndarray, needle: np.ndarray) -> tuple:
    """Best sliding-ZNCC score of ``needle`` across ``haystack`` and its frame.

    Returns ``(score, frame_index)``; ``(0.0, 0)`` when the haystack is shorter
    than the needle. Public entry point for cross-module template scoring
    (the #350 window optimizer) so callers do not bind to ``_sliding_zncc``.
    """
    if haystack.shape[0] < needle.shape[0]:
        return 0.0, 0
    scores = _sliding_zncc(haystack, needle)
    if not scores.size:
        return 0.0, 0
    idx = int(np.argmax(scores))
    return float(scores[idx]), idx


def _sliding_zncc(haystack: np.ndarray, needle: np.ndarray) -> np.ndarray:
    """Sliding zero-mean normalized cross-correlation (ZNCC).

    Both inputs are float32 ``(n_frames, n_coeffs)``. Returns a 1D array of
    length ``haystack.shape[0] - needle.shape[0] + 1`` in ``[-1, 1]``.

    The score subtracts each window's per-coefficient mean before correlating
    with the (also per-coefficient zero-meaned) needle. That is the standard
    template-matching score because it is invariant to a constant per-coeff
    offset between the template and the haystack -- exactly the situation we
    hit, because raw MFCCs have small but nonzero per-coeff DC components and
    they differ between a 0.5 s template window and a 10-minute haystack.

    Implementation uses FFT cross-correlation per coefficient
    (``scipy.signal.fftconvolve``) plus rolling sum-of-squares for the window
    norm; both are ``O((N+M) log(N+M))`` per coefficient, well below the naive
    ``O(N*M)`` cost on long episodes.
    """
    n_haystack, n_coeffs = haystack.shape
    n_needle = needle.shape[0]
    n_out = n_haystack - n_needle + 1
    if n_out <= 0:
        return np.zeros(0, dtype=np.float32)

    # Zero-mean the needle per coefficient; the resulting needle is what the
    # haystack-window-minus-its-mean is correlated against.
    needle_f64 = needle.astype(np.float64)
    needle_zm = needle_f64 - needle_f64.mean(axis=0, keepdims=True)
    needle_zm_norm = float(np.linalg.norm(needle_zm))
    if needle_zm_norm <= 0:
        return np.zeros(n_out, dtype=np.float32)

    hay_f64 = haystack.astype(np.float64)

    # Per-coefficient rolling sum -> per-window per-coeff mean.
    pad = np.zeros((1, n_coeffs), dtype=np.float64)
    csum_h = np.concatenate([pad, np.cumsum(hay_f64, axis=0)], axis=0)
    window_sum = csum_h[n_needle:] - csum_h[:-n_needle]
    window_mean = window_sum / n_needle  # (n_out, n_coeffs)

    # Per-coefficient rolling sum-of-squares for the window-norm denominator.
    sq = hay_f64 ** 2
    csum_sq = np.concatenate([pad, np.cumsum(sq, axis=0)], axis=0)
    window_sum_sq = csum_sq[n_needle:] - csum_sq[:-n_needle]
    # ||window - window_mean||^2 across all coeffs.
    # = sum_c (sum_i window[i,c]^2 - n_needle * window_mean[c]^2)
    window_var_sum = (window_sum_sq - n_needle * (window_mean ** 2)).sum(axis=1)
    window_norm = np.sqrt(np.maximum(window_var_sum, 1e-12))

    # Numerator: sum_c sum_i (window[i,c] - window_mean[c]) * needle_zm[i,c]
    # The (- window_mean[c]) term drops out because needle_zm sums to zero per
    # column, so we just correlate the raw haystack column with the zero-mean
    # needle column.
    numerator = np.zeros(n_out, dtype=np.float64)
    for c in range(n_coeffs):
        corr = fftconvolve(hay_f64[:, c], needle_zm[::-1, c], mode='valid')
        numerator += corr[:n_out]

    scores = numerator / (window_norm * needle_zm_norm + 1e-12)
    # Cauchy-Schwarz guarantees [-1, 1]; clip removes ~1e-7 float drift.
    return np.clip(scores, -1.0, 1.0).astype(np.float32)


def _peak_pick(scores: np.ndarray, threshold: float,
               suppress_frames: int) -> List[tuple]:
    """Greedy peak picker: take the global max, suppress a window around it, repeat.

    Returns a list of ``(frame_index, score)`` tuples ordered by descending score.
    """
    if not scores.size:
        return []
    work = scores.copy()
    peaks: List[tuple] = []
    while True:
        idx = int(np.argmax(work))
        score = float(work[idx])
        if score < threshold:
            break
        peaks.append((idx, score))
        lo = max(0, idx - suppress_frames)
        hi = min(len(work), idx + suppress_frames + 1)
        work[lo:hi] = -np.inf
        if len(peaks) >= 200:  # absolute safety cap
            break
    return peaks
