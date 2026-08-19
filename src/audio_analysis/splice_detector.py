"""Splice-evidence detection (spec 2.1).

Detects encoding artifacts that mark dynamic ad-insertion splice points:

- deep silences: RMS on 10ms frames of an 8kHz mono decode. Runs below
  -80 dBFS for >= 0.5s are 'digital_silence' (encoded zeros); runs below
  -70 dBFS for >= 1.4s are 'deep_silence'.
- loudness_steps: level change straddling a silence event, measured on the
  ebur128 frames the volume pass already produced.
- spectral_steps: change in energy-weighted spectral centroid/flatness
  across a silence event. Per-frame values are power-weighted over each
  side window; naive per-frame means are silence-contaminated and can flip
  a boundary's sign.

Evidence only: consumers corroborate, snap, veto, and annotate LLM prompts.
Nothing here cuts audio on its own.
"""

import logging
import os
import subprocess
from typing import Dict, List, Optional

import numpy as np

from config import (
    SPLICE_DIGITAL_SILENCE_DBFS, SPLICE_DIGITAL_SILENCE_MIN_SECONDS,
    SPLICE_DEEP_SILENCE_DBFS, SPLICE_DEEP_SILENCE_MIN_SECONDS,
    SPLICE_LOUDNESS_GATE_LUFS, SPLICE_LOUDNESS_STEP_MIN_LU,
    SPLICE_CENTROID_STEP_MIN_HZ, SPLICE_FLATNESS_STEP_MIN,
    SPLICE_STEP_SIDE_WINDOW_SECONDS,
)
from utils.ffmpeg_run import ffmpeg_timeout
from utils.subprocess_registry import tracked_run

logger = logging.getLogger('podcast.audio_analysis.splice')

SPLICE_EVIDENCE_VERSION = 1
SAMPLE_RATE_HZ = 8000
RMS_FRAME_SAMPLES = 80           # 10ms at 8kHz
SPECTRAL_FRAME_SAMPLES = 8000    # 1s at 8kHz
_CHUNK_RMS_FRAMES = 60000        # process RMS in 600s blocks to bound memory
_EPS = 1e-12
_SILENCE_FLOOR_DB = -120.0       # clamp for log of all-zero frames


def _empty_payload() -> Dict:
    return {'version': SPLICE_EVIDENCE_VERSION, 'events': [],
            'calibration': {'status': 'cold_start'}}


def _make_event(etype: str, start: float, end: float,
                depth_dbfs: Optional[float] = None) -> Dict:
    return {
        'time': round(float(start), 3),
        'end_time': round(float(end), 3),
        'type': etype,
        'depth_dbfs': None if depth_dbfs is None else round(float(depth_dbfs), 1),
        'duration_s': round(float(end - start), 3),
        'loudness_step_lu': None,
        'centroid_step_hz': None,
        'flatness_step': None,
    }


def _runs_below(values: np.ndarray, threshold: float) -> List:
    """(start, end) index pairs of maximal runs strictly below threshold."""
    below = np.concatenate(([False], values < threshold, [False]))
    edges = np.flatnonzero(np.diff(below.astype(np.int8)))
    return list(zip(edges[0::2], edges[1::2], strict=True))


class SpliceDetector:
    """Compute splice-evidence events for one episode."""

    def detect(self, audio_path: str, duration: Optional[float],
               loudness_frames) -> Dict:
        """Return the splice_evidence payload; empty payload on any failure."""
        if not os.path.exists(audio_path):
            logger.warning('Splice detector: file not found: %s', audio_path)
            return _empty_payload()

        pcm = self._decode_8k_mono(audio_path, duration or 0.0)
        if pcm is None or len(pcm) < SPECTRAL_FRAME_SAMPLES:
            return _empty_payload()

        rms_db = self._frame_rms_db(pcm)
        events = self._find_silence_events(rms_db)

        out = []
        for ev in events:
            step = self._loudness_step(loudness_frames or [],
                                       ev['time'], ev['end_time'])
            ev['loudness_step_lu'] = step
            centroid_step, flatness_step = self._spectral_step(
                pcm, ev['time'], ev['end_time'])
            ev['centroid_step_hz'] = centroid_step
            ev['flatness_step'] = flatness_step
            out.append(ev)
            if step is not None and abs(step) >= SPLICE_LOUDNESS_STEP_MIN_LU:
                step_ev = _make_event('loudness_step', ev['time'], ev['end_time'])
                step_ev['loudness_step_lu'] = step
                out.append(step_ev)
            centroid_hit = (centroid_step is not None
                            and abs(centroid_step) >= SPLICE_CENTROID_STEP_MIN_HZ)
            flatness_hit = (flatness_step is not None
                            and abs(flatness_step) >= SPLICE_FLATNESS_STEP_MIN)
            if centroid_hit or flatness_hit:
                step_ev = _make_event('spectral_step', ev['time'], ev['end_time'])
                step_ev['centroid_step_hz'] = centroid_step
                step_ev['flatness_step'] = flatness_step
                out.append(step_ev)

        out.sort(key=lambda e: e['time'])
        logger.info('Splice detector: %d event(s) in %s', len(out), audio_path)
        return {'version': SPLICE_EVIDENCE_VERSION, 'events': out,
                'calibration': {'status': 'cold_start'}}

    def _decode_8k_mono(self, audio_path: str,
                        duration: float) -> Optional[np.ndarray]:
        """Decode to 8kHz mono s16 PCM. Returns int16 array or None."""
        cmd = [
            'ffmpeg', '-v', 'error', '-i', audio_path,
            '-f', 's16le', '-acodec', 'pcm_s16le',
            '-ac', '1', '-ar', str(SAMPLE_RATE_HZ), '-',
        ]
        timeout = ffmpeg_timeout(duration)
        try:
            result = tracked_run(cmd, capture_output=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            logger.error('Splice detector decode timed out after %ds', timeout)
            return None
        except Exception as exc:
            logger.error('Splice detector decode failed: %s', exc)
            return None
        if result.returncode != 0 or not result.stdout:
            logger.error('Splice detector ffmpeg exited %d for %s',
                         result.returncode, audio_path)
            return None
        return np.frombuffer(result.stdout, dtype='<i2')

    def _frame_rms_db(self, pcm: np.ndarray) -> np.ndarray:
        """Per-10ms-frame RMS in dBFS, computed in bounded chunks."""
        n_frames = len(pcm) // RMS_FRAME_SAMPLES
        out = np.empty(n_frames, dtype=np.float32)
        for i in range(0, n_frames, _CHUNK_RMS_FRAMES):
            j = min(i + _CHUNK_RMS_FRAMES, n_frames)
            block = pcm[i * RMS_FRAME_SAMPLES:j * RMS_FRAME_SAMPLES]
            block = (block.astype(np.float32) / 32768.0).reshape(
                -1, RMS_FRAME_SAMPLES)
            rms = np.sqrt(np.mean(np.square(block), axis=1))
            out[i:j] = 20.0 * np.log10(np.maximum(rms, _EPS))
        return np.maximum(out, _SILENCE_FLOOR_DB)

    def _find_silence_events(self, rms_db: np.ndarray) -> List[Dict]:
        """digital_silence / deep_silence events from the RMS frame series."""
        frame_s = RMS_FRAME_SAMPLES / SAMPLE_RATE_HZ
        events = []
        for run_start, run_end in _runs_below(rms_db, SPLICE_DEEP_SILENCE_DBFS):
            run_db = rms_db[run_start:run_end]
            emitted_digital = False
            for sub_start, sub_end in _runs_below(run_db, SPLICE_DIGITAL_SILENCE_DBFS):
                if (sub_end - sub_start) * frame_s >= SPLICE_DIGITAL_SILENCE_MIN_SECONDS:
                    a = run_start + sub_start
                    b = run_start + sub_end
                    events.append(_make_event(
                        'digital_silence', a * frame_s, b * frame_s,
                        depth_dbfs=float(np.min(rms_db[a:b]))))
                    emitted_digital = True
            if (not emitted_digital
                    and (run_end - run_start) * frame_s >= SPLICE_DEEP_SILENCE_MIN_SECONDS):
                events.append(_make_event(
                    'deep_silence', run_start * frame_s, run_end * frame_s,
                    depth_dbfs=float(np.min(run_db))))
        return events

    def _loudness_step(self, loudness_frames, event_start: float,
                       event_end: float) -> Optional[float]:
        """step = mean(M over [end+1, end+6]) - mean(M over [start-6, start-1])."""
        before = self._side_mean(loudness_frames, event_start - 6.0, event_start - 1.0)
        after = self._side_mean(loudness_frames, event_end + 1.0, event_end + 6.0)
        if before is None or after is None:
            return None
        return round(after - before, 1)

    @staticmethod
    def _side_mean(loudness_frames, lo: float, hi: float) -> Optional[float]:
        vals = [f.loudness_lufs for f in loudness_frames
                if lo <= (f.start + f.end) / 2.0 <= hi
                and f.loudness_lufs > SPLICE_LOUDNESS_GATE_LUFS]
        return sum(vals) / len(vals) if vals else None

    def _spectral_step(self, pcm: np.ndarray, event_start: float,
                       event_end: float):
        """(centroid_step_hz, flatness_step) across the event, or (None, None)."""
        n_frames = len(pcm) // SPECTRAL_FRAME_SAMPLES
        side = int(SPLICE_STEP_SIDE_WINDOW_SECONDS)
        before_end = int(event_start)
        after_start = int(np.ceil(event_end))
        before = self._window_spectral(pcm, max(0, before_end - side), before_end)
        after = self._window_spectral(pcm, after_start,
                                      min(n_frames, after_start + side))
        if before is None or after is None:
            return None, None
        return (round(after[0] - before[0], 1), round(after[1] - before[1], 4))

    @staticmethod
    def _window_spectral(pcm: np.ndarray, frame_lo: int, frame_hi: int):
        """Power-weighted centroid and flatness aggregate over 1s frames."""
        if frame_hi <= frame_lo:
            return None
        freqs = np.fft.rfftfreq(SPECTRAL_FRAME_SAMPLES, d=1.0 / SAMPLE_RATE_HZ)
        window = np.hanning(SPECTRAL_FRAME_SAMPLES)
        total_energy = 0.0
        centroid_acc = 0.0
        flatness_acc = 0.0
        for i in range(frame_lo, frame_hi):
            frame = pcm[i * SPECTRAL_FRAME_SAMPLES:(i + 1) * SPECTRAL_FRAME_SAMPLES]
            if len(frame) < SPECTRAL_FRAME_SAMPLES:
                break
            spec = np.abs(np.fft.rfft(
                frame.astype(np.float32) / 32768.0 * window)) ** 2
            energy = float(np.sum(spec))
            if energy <= _EPS:
                continue
            centroid = float(np.sum(freqs * spec) / energy)
            flatness = float(np.exp(np.mean(np.log(spec + _EPS)))
                             / (np.mean(spec) + _EPS))
            total_energy += energy
            centroid_acc += energy * centroid
            flatness_acc += energy * flatness
        if total_energy <= _EPS:
            return None
        return centroid_acc / total_energy, flatness_acc / total_energy
