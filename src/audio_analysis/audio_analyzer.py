"""Unified audio analysis facade: orchestrates volume and transition analysis to provide audio
signals for ad detection."""

import logging
import time
import os
from collections.abc import Callable
from typing import Any
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError

from .base import AudioAnalysisResult
from .volume_analyzer import VolumeAnalyzer
from .transition_detector import TransitionDetector
from .cue_template_matcher import AudioCueTemplateMatcher
from .silence_detector import SilenceDetector
from .splice_detector import SpliceDetector
from run_context import run_in_worker_thread
from config import (
    AUDIO_CUE_FORMANT_ATTEN_DB,
    resolve_cue_template_score,
    resolve_near_miss_floor,
    resolve_silence_snap_enabled,
    resolve_silence_snap_tunables,
)
from utils.audio import get_audio_duration
from utils.ffmpeg_run import ffmpeg_timeout

logger = logging.getLogger('podcast.audio_analysis')


# Default timeout multipliers (seconds per minute of audio)
DEFAULT_VOLUME_TIMEOUT_MULTIPLIER = 2.0    # ~2s per min of audio

# Minimum timeouts regardless of duration
MIN_VOLUME_TIMEOUT = 180    # 3 minutes


def calculate_component_timeouts(duration_seconds: float) -> dict[str, int]:
    """Per-component timeouts in seconds from episode duration; longer episodes get proportionally longer ones."""
    duration_minutes = duration_seconds / 60.0

    return {
        'volume': max(MIN_VOLUME_TIMEOUT, int(duration_minutes * DEFAULT_VOLUME_TIMEOUT_MULTIPLIER)),
        # Silence detection runs a full ffmpeg decode; size the timeout to match
        # what SilenceDetector's own ffmpeg pass would use so the wrapper does
        # not pre-empt the detector on long opted-in episodes.
        'silence': ffmpeg_timeout(duration_seconds),
        # Splice detection runs its own full 8kHz decode; size like silence.
        'splice': ffmpeg_timeout(duration_seconds),
    }


def _splice_pair(result) -> tuple[Any, Any]:
    """Unpack the detector's (evidence, peaks) result; ValueError on a malformed one."""
    evidence, peaks = result
    return evidence, peaks


class AudioAnalyzer:
    """Unified audio analysis for podcast ad detection (volume and transition signals)."""

    def __init__(
        self,
        db=None,
        volume_threshold_db: float = 3.0,
    ):
        """db supplies settings; volume_threshold_db is the dB change flagged as a volume anomaly."""
        self.db = db

        # Load settings from database if available
        settings = self._load_settings()

        # Initialize volume analyzer with settings
        self.volume_analyzer = VolumeAnalyzer(
            anomaly_threshold_db=settings.get('volume_threshold_db', volume_threshold_db)
        )

        # Initialize transition detector
        from config import TRANSITION_THRESHOLD_DB, MIN_TRANSITION_AD_DURATION, MAX_TRANSITION_AD_DURATION
        self.transition_detector = TransitionDetector(
            transition_threshold_db=settings.get('transition_threshold_db', TRANSITION_THRESHOLD_DB),
            min_ad_duration=MIN_TRANSITION_AD_DURATION,
            max_ad_duration=MAX_TRANSITION_AD_DURATION,
        )

        # Splice-evidence detector (spec 2.1). Stateless; thresholds are code
        # constants, so a single instance serves every run.
        self.splice_detector = SpliceDetector()

        # Audio cue detector (issue #350) is built per run in analyze() from
        # current settings, not here, so the experiment toggle takes effect
        # without a restart (this analyzer is a long-lived singleton).

    def _load_settings(self) -> dict[str, Any]:
        """Load settings from database."""
        settings = {}
        if self.db:
            try:
                # Numeric settings
                vol_threshold = self.db.get_setting('volume_threshold_db')
                if vol_threshold:
                    settings['volume_threshold_db'] = float(vol_threshold)

                transition_threshold = self.db.get_setting('transition_threshold_db')
                if transition_threshold:
                    settings['transition_threshold_db'] = float(transition_threshold)

            except Exception as e:
                logger.warning(f"Failed to load audio analysis settings: {e}")

        return settings

    def _load_cue_config(self, feed_id: int | None = None, force: bool = False,
                         errors: list[str] | None = None):
        """Resolve the audio cue detector for this run (issue #350); returns ``(enabled, detector)``.

        This analyzer is a long-lived singleton, so settings are read here, not at construction,
        letting the Settings toggle apply without a restart. Gated by ``audio_cue_detection_enabled``
        unless ``force`` (cue-only preset runs), which bypasses the toggle for the template path only,
        since forcing the spectral fallback would mint pair candidates from coarse bursts. With at
        least one enabled cue template the per-feed template matcher is used, else the spectral burst
        detector. If ``force`` is set and the template matcher fails to load, that is logged and
        surfaced via ``errors``. The detector is None when cue detection is off or no DB is available.
        """
        if not self.db:
            return False, None
        try:
            globally_enabled = self.db.get_setting_bool(
                'audio_cue_detection_enabled', default=False)
            if not force and not globally_enabled:
                return False, None

            # Per-feed templates take precedence when the feed has any enabled.
            # One query resolves both the gate and the template set.
            templates = (
                self.db.list_active_cue_templates_for_feed(feed_id)
                if feed_id is not None else []
            )
            if templates:
                try:
                    score = resolve_cue_template_score(self.db, feed_id)
                    near_miss_floor = resolve_near_miss_floor(score)
                    matcher = AudioCueTemplateMatcher(
                        templates=templates, score_threshold=score,
                        formant_atten_db=self.db.get_setting_float(
                            'audio_cue_formant_atten_db', AUDIO_CUE_FORMANT_ATTEN_DB),
                        near_miss_floor=near_miss_floor,
                    )
                except Exception as e:
                    logger.warning(
                        f"Cue detection: feed_id={feed_id} matcher construction "
                        f"failed: {e}")
                    if force:
                        msg = (f"Cue detection: force requested for feed_id={feed_id} "
                               f"but matcher construction failed: {e}")
                        logger.error(msg)
                        if errors is not None:
                            errors.append(msg)
                    return False, None
                if matcher.is_usable:
                    logger.info(
                        f"Cue detection: using {len(templates)} per-feed "
                        f"template(s) for feed_id={feed_id}"
                    )
                    return True, matcher
                logger.warning(
                    f"Cue detection: feed_id={feed_id} templates failed to "
                    "load; falling back to spectral detector"
                )
                if force:
                    # force (cue-only) with no usable matcher cuts nothing from
                    # templates this run; surface it instead of failing silently.
                    msg = (f"Cue detection: force requested for feed_id={feed_id} "
                           f"but {len(templates)} template(s) produced no usable "
                           f"matcher")
                    logger.error(msg)
                    if errors is not None:
                        errors.append(msg)

            # Force alone must never activate the spectral fallback.
            if not globally_enabled:
                return False, None

            from config import (
                AUDIO_CUE_FREQ_MIN_HZ, AUDIO_CUE_FREQ_MAX_HZ,
                AUDIO_CUE_PROMINENCE_DB, AUDIO_CUE_MIN_CONFIDENCE,
            )
            from .cue_detector import AudioCueDetector
            detector = AudioCueDetector(
                freq_min_hz=self.db.get_setting_float('audio_cue_freq_min_hz', AUDIO_CUE_FREQ_MIN_HZ),
                freq_max_hz=self.db.get_setting_float('audio_cue_freq_max_hz', AUDIO_CUE_FREQ_MAX_HZ),
                prominence_db=self.db.get_setting_float('audio_cue_prominence_db', AUDIO_CUE_PROMINENCE_DB),
                min_confidence=self.db.get_setting_float('audio_cue_min_confidence', AUDIO_CUE_MIN_CONFIDENCE),
            )
            return True, detector
        except Exception as e:
            logger.warning(f"Failed to load audio cue settings: {e}")
            return False, None

    def _load_silence_config(self, feed_id: int | None = None):
        """Return a SilenceDetector when silence-snap is enabled for this feed, else None (flag off or no DB).

        Settings are read per run so the toggle applies without a restart. Caches the resolved
        tunables on self._silence_tunables so analyze() can expose them without a second DB read.
        """
        self._silence_tunables = None
        if not self.db:
            return None
        try:
            if not resolve_silence_snap_enabled(self.db, feed_id):
                return None
            tunables = resolve_silence_snap_tunables(self.db)
            self._silence_tunables = tunables
            return SilenceDetector(
                noise_db=tunables['noise_db'],
                min_silence_s=tunables['min_duration_seconds'],
            )
        except Exception as exc:
            logger.warning('Failed to load silence config: %s', exc)
            return None

    def _splice_enabled(self) -> bool:
        """Global splice-evidence toggle (spec 2.1). Default on; DB-tunable."""
        if not self.db:
            return False
        try:
            return self.db.get_setting_bool('splice_evidence_enabled', default=True)
        except Exception as exc:
            logger.warning('Failed to read splice_evidence_enabled: %s', exc)
            return False

    @staticmethod
    def _collect_component(name: str, future, timeout: int,
                           shape=None) -> tuple[Any, str | None]:
        """Collect a pooled component future with timeout protection.

        Returns (result, error); result is None on timeout/error. ``shape`` reshapes the result
        inside the handler, so a malformed result degrades like any failure.
        """
        try:
            result = future.result(timeout=timeout)
            return (shape(result) if shape else result), None
        except FuturesTimeoutError:
            error_msg = f"{name} analysis exceeded {timeout}s timeout"
            logger.warning(error_msg)
            return None, error_msg
        except Exception as e:
            error_msg = f"{name} analysis failed: {type(e).__name__}: {e}"
            logger.warning(error_msg)
            return None, error_msg

    def analyze(
        self,
        audio_path: str,
        status_callback: Callable | None = None,
        feed_id: int | None = None,
        force_cue_detection: bool = False,
    ) -> AudioAnalysisResult:
        """Run audio analysis (volume + transition detection).

        status_callback(stage, progress) reports status. feed_id selects the per-feed cue template
        matcher over the spectral fallback (issue #350); force_cue_detection bypasses the global cue
        toggle for the template path (cue-only preset runs). Returns AudioAnalysisResult.
        """
        start_time = time.time()

        result = AudioAnalysisResult()

        if not os.path.exists(audio_path):
            result.errors.append(f"Audio file not found: {audio_path}")
            return result

        logger.info(f"Starting audio analysis: {audio_path}")

        # Get audio duration for timeout calculation
        duration = get_audio_duration(audio_path)
        if duration:
            timeouts = calculate_component_timeouts(duration)
            logger.info(f"Audio duration: {duration/60:.1f} min, "
                       f"timeout: volume={timeouts['volume']}s")
        else:
            timeouts = {'volume': MIN_VOLUME_TIMEOUT * 2}
            logger.warning("Could not determine audio duration, using default timeouts")

        signals = []
        errors = []
        baseline = None
        frames = []

        # Resolve per-run component config up front so independent components
        # can be scheduled together. DB reads only, no audio I/O.
        cue_enabled, cue_detector = self._load_cue_config(
            feed_id=feed_id, force=force_cue_detection, errors=errors)
        silence_detector = self._load_silence_config(feed_id=feed_id)
        splice_enabled = self._splice_enabled()

        # Each enabled component runs a full-file ffmpeg decode. Volume, cue and silence are independent,
        # so they run concurrently on a shared pool. RAW callables are submitted (no per-component inner
        # executor: it doubled threads and its shutdown(wait=True) pinned a pool worker on a hung decode).
        # Timeouts and status callbacks fire at ordered collection points, keeping progress monotonic
        # (30/35/40/45/50). Splice needs the volume frames, so it is submitted after volume resolves and
        # reuses its freed worker: peak concurrency is 1 + cue + silence.
        n_components = (1 + (1 if (cue_enabled and cue_detector) else 0)
                        + (1 if silence_detector else 0))
        # Timeouts were sized for sequential runs; concurrent full-file decodes contend for CPU, so on
        # low-core hosts a component can blow past its solo budget. Scale each concurrent component's
        # budget (volume/cue/silence, and splice which overlaps them) by n_components.
        timeout_factor = n_components

        cue_near_misses: list[dict[str, Any]] = []
        cue_templates_debug: list[dict[str, Any]] = []
        cue_scan_complete = False
        silence_spans: list[dict[str, Any]] = []
        splice_evidence = None
        splice_peaks = None

        pool = ThreadPoolExecutor(max_workers=n_components)
        try:
            vol_future = pool.submit(run_in_worker_thread(self.volume_analyzer.analyze), audio_path)

            # Audio cue detection (issue #350), opt-in. Settings are read per
            # run so the toggle takes effect without a restart. Runs its own
            # band-passed ffmpeg pass, so only when the experiment is enabled.
            cue_future = None
            is_matcher = False
            if cue_enabled and cue_detector:
                # Matcher path surfaces near-misses via detect_with_debug; spectral uses detect().
                is_matcher = isinstance(cue_detector, AudioCueTemplateMatcher)
                cue_future = pool.submit(
                    run_in_worker_thread(
                        cue_detector.detect_with_debug if is_matcher
                        else cue_detector.detect),
                    audio_path,
                )

            # Silence detection (Phase B), per-feed opt-in via silence_snap_enabled.
            # Runs its own ffmpeg silencedetect pass; skipped when flag is off (default).
            silence_future = None
            if silence_detector:
                silence_future = pool.submit(run_in_worker_thread(silence_detector.detect), audio_path)

            if status_callback:
                status_callback("analyzing: volume", 30)
            vol_result, error = self._collect_component(
                'volume', vol_future, timeouts['volume'] * timeout_factor)
            if error:
                errors.append(error)
                logger.warning(f"Volume analysis skipped: {error}")
            elif vol_result:
                vol_signals, vol_baseline, vol_frames = vol_result
                signals.extend(vol_signals)
                baseline = vol_baseline
                frames = vol_frames
                logger.info(f"Volume analysis complete: {len(vol_signals)} signals, {len(vol_frames)} frames")

            # Splice-evidence detection (spec 2.1): deep silences plus loudness and spectral steps at them.
            # Consumes the ebur128 frames from the volume pass, so it is submitted after volume resolves
            # (a timed-out or failed volume leaves frames=[] and splice degrades to no loudness steps);
            # its own 8kHz mono decode then overlaps any cue/silence work still running.
            splice_future = None
            if splice_enabled:
                splice_future = pool.submit(
                    run_in_worker_thread(self.splice_detector.detect_with_peaks),
                    audio_path, duration, frames)

            # Transition detection (runs on volume frames, no extra I/O)
            if frames and self.transition_detector:
                if status_callback:
                    status_callback("analyzing: transitions", 35)
                try:
                    transition_signals = self.transition_detector.detect_and_pair(frames)
                    signals.extend(transition_signals)
                    if transition_signals:
                        logger.info(f"Transition detection: {len(transition_signals)} DAI transition pairs")
                except Exception as e:
                    error_msg = f"Transition detection failed: {e}"
                    logger.warning(error_msg)
                    errors.append(error_msg)

            if cue_future is not None:
                if status_callback:
                    status_callback("analyzing: audio cues", 40)
                cue_result, cue_error = self._collect_component(
                    'audio_cue', cue_future,
                    timeouts.get('cue', timeouts['volume']) * timeout_factor)
                if cue_error:
                    errors.append(cue_error)
                elif is_matcher:
                    cue_signals, cue_debug = cue_result
                    signals.extend(cue_signals)
                    cue_near_misses = cue_debug.get('near_misses', [])
                    cue_templates_debug = cue_debug.get('templates', [])
                    cue_scan_complete = True
                else:
                    # The detector logs its own summary line (frames, baseline,
                    # peak vs threshold, cue count) including the zero-cue case,
                    # so no extra log here.
                    signals.extend(cue_result)

            if silence_future is not None:
                if status_callback:
                    status_callback("analyzing: silence", 45)
                silence_result, silence_error = self._collect_component(
                    'silence', silence_future,
                    timeouts.get('silence', timeouts['volume']) * timeout_factor)
                if silence_error:
                    errors.append(silence_error)
                else:
                    silence_spans = silence_result or []

            if splice_future is not None:
                if status_callback:
                    status_callback("analyzing: splice evidence", 50)
                splice_result, splice_error = self._collect_component(
                    'splice', splice_future,
                    timeouts.get('splice', timeouts['volume']) * timeout_factor,
                    shape=_splice_pair)
                if splice_error:
                    errors.append(splice_error)
                else:
                    splice_evidence, splice_peaks = splice_result
        finally:
            # wait=False: a hung decode must not block returning the degraded result; the worker ends when
            # the component's ffmpeg timeout fires. cancel_futures drops queued work already timed out.
            pool.shutdown(wait=False, cancel_futures=True)

        result.signals = signals
        result.cue_near_misses = cue_near_misses
        result.cue_templates_debug = cue_templates_debug
        result.cue_scan_complete = cue_scan_complete
        result.silence_spans = silence_spans
        # Expose the resolved silence tunables so processing.py can use them
        # for snap_ad_boundaries_to_silence without a second DB read.
        result.silence_tunables = getattr(self, '_silence_tunables', None)
        result.splice_evidence = splice_evidence
        result.peaks_10ms = splice_peaks
        result.errors = errors
        result.loudness_baseline = baseline
        result.loudness_frames = frames
        result.analysis_time_seconds = time.time() - start_time

        # Log summary
        if errors:
            logger.warning(f"Audio analysis completed with {len(errors)} errors: {errors}")
        else:
            logger.info(f"Audio analysis complete: {len(signals)} total signals")

        return result
