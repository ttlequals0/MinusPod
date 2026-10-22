"""Transcription using Faster Whisper."""

import logging
import tempfile
import os
import subprocess
import threading
from contextlib import ExitStack
from dataclasses import dataclass, field, replace
from typing import cast
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed
import failover
import run_context
from user_agent import download_user_agent
import utils.audio
from utils.errors import (
    ServiceUnavailableError, AudioTooLargeError, AudioExtractionError,
    AudioExtractionTimeout, ModelLoadError,
    TranscriptionRejectedError,
)
from utils.segments import normalize_segments
from utils.text import transcript_gaps
from utils.time import format_vtt_timestamp
from utils.gpu import clear_gpu_memory, get_gpu_device_name
from utils.url import SSRFError
from utils.http import redirect_chain_for_log, safe_url_for_log
from utils.safe_http import (
    URLTrust, safe_get, stream_to_file_capped,
    ResponseTooLargeError,
)
import utils.subprocess_registry
from utils.ffmpeg_run import SAFE_MEDIA_INPUT_ARGS
from whisper_pool import get_pool
import parakeet_transcriber
from parakeet_transcriber import ParakeetModelSingleton
from transcriber import common, settings, local, remote, chunking, segments
from config import (
    WHISPER_BACKEND_API,
    WHISPER_BACKEND_PARAKEET,
    PARAKEET_MODEL_NAME,
    resolve_whisper_device,
    CHUNK_OVERLAP_SECONDS,
    CHUNK_MIN_DURATION_SECONDS,
    CHUNK_DEFAULT_DURATION_SECONDS,
    FFMPEG_LONG_TIMEOUT,
    FFMPEG_SHORT_TIMEOUT,
    HTTP_MAX_REDIRECTS_FEED,
    log_download_query_enabled,
    HOLE_RETRANSCRIBE_MAX_HOLES, HOLE_RETRANSCRIBE_MAX_SECONDS, HOLE_RETRANSCRIBE_QUIET_DB,
)

# Suppress ONNX Runtime warnings before importing faster_whisper
os.environ.setdefault('ORT_LOG_LEVEL', 'ERROR')

# Set cache directories to writable location (for running as non-root user)
# These must be set BEFORE importing faster_whisper/huggingface
cache_dir = os.environ.get('HF_HOME', '/app/data/.cache')
os.environ.setdefault('HF_HOME', cache_dir)
os.environ.setdefault('HUGGINGFACE_HUB_CACHE', os.path.join(cache_dir, 'hub'))
os.environ.setdefault('XDG_CACHE_HOME', cache_dir)

logger = logging.getLogger(__name__)

# check_audio_availability reports failures as prose that main_app.processing's
# retry classifier matches on. Shared so rewording one cannot silently flip the
# other's verdict.
CDN_REFUSED_PREFIX = 'CDN refused'

# Chunk extraction (ffmpeg with the normalization filter chain) is CPU-bound
# and takes longer than GPU inference on a chunk, so it runs ahead of the GPU
# in a small pool: while chunk N transcribes, chunks N+1.. extract.
EXTRACT_PREFETCH_WORKERS = 2
# How many chunks past the current one to keep in flight. Bounds /tmp usage:
# each extracted chunk is a 16kHz mono WAV, ~2MB per audio minute.
EXTRACT_PREFETCH_AHEAD = 2


class _ChunkPrefetcher:
    """Runs chunk extractions ahead of the GPU during chunked transcription.

    Futures are keyed by (start, end_with_overlap) from _chunk_bounds_ahead,
    the same helper the chunk loop reads its own bounds from. When the chunk
    size changes mid-run (OOM shrink, extraction-timeout shrink) the next
    take() computes bounds the queue does not hold, and the whole stale
    queue is discarded. Discarded and leftover extractions unlink their
    temp files on completion. Single-threaded caller; no locking needed.
    """

    def __init__(self, audio_path):
        self._audio_path = audio_path
        self._executor = ThreadPoolExecutor(max_workers=EXTRACT_PREFETCH_WORKERS)
        self._pending = {}

    def _submit(self, bounds):
        if bounds not in self._pending:
            start, end = bounds
            # run_in_worker_thread keeps the pool thread's ffmpeg log lines
            # inside the episode's run log.
            self._pending[bounds] = self._executor.submit(
                run_context.run_in_worker_thread(chunking.extract_audio_chunk),
                self._audio_path, start, end, preprocess=True,
            )

    def _queue(self, start, chunk_duration, duration, overlap):
        """Queue the chunk at `start` plus the lookahead; return its bounds.

        Returns None past the end of the audio (a zero-length file reaches
        prime() this way); only prime() may pass such a start.
        """
        bounds = chunking._chunk_bounds_ahead(
            start, chunk_duration, duration, overlap, 1 + EXTRACT_PREFETCH_AHEAD,
        )
        if not bounds:
            return None
        if bounds[0] not in self._pending:
            self._discard_pending()
        for each in bounds:
            self._submit(each)
        return bounds[0]

    def prime(self, start, chunk_duration, duration, overlap):
        """Start extracting without blocking, so callers can overlap other
        setup work (model load) with the first chunk's ffmpeg pass."""
        self._queue(start, chunk_duration, duration, overlap)

    def take(self, start, chunk_duration, duration, overlap):
        """Blocking path for the chunk at `start`, extracting now if needed.

        Raises whatever the extraction raised (AudioExtractionTimeout
        included), exactly like calling extract_audio_chunk inline.
        """
        return self._pending.pop(
            self._queue(start, chunk_duration, duration, overlap)
        ).result()

    def _discard_pending(self):
        for future in self._pending.values():
            if not future.cancel():
                future.add_done_callback(chunking._unlink_future_chunk)
        self._pending.clear()

    def close(self):
        self._discard_pending()
        self._executor.shutdown(wait=False)


# Sentinel: a chunk plan's failure budget was blown with no failover switch
# available, so the caller aborts with no transcript (#806).
_ABORT_CHUNK_PLAN = object()


@dataclass(frozen=True)
class _ChunkPlanContext:
    """Per-episode inputs shared by every pass of a chunk plan.

    switch_event: set once this pass decides to switch backend or abort, so
    chunks already in flight stop before extraction or upload instead of
    wasting work on a result that will be discarded (#806). Scoped to one
    pass: a caller rerunning remaining chunks on a new backend builds a
    fresh context rather than reusing this one.
    """
    audio_path: str
    language_override: str | None
    prefix: str
    max_workers: int
    max_failed_chunks: int
    switch_event: threading.Event = field(default_factory=threading.Event)


_NOVAD_LABELS = {'novad_tail': 'Tail', 'novad_hole': 'Hole'}


class Transcriber:
    def __init__(self):
        # Model is now managed by singleton
        # Last local transcribe() outcome (batch_size/retry_count/device/etc),
        # read by callers that want it beside the per-phase stats (#519).
        self.last_transcription_stats = None
        backend = settings._get_whisper_settings()['backend']
        if backend != WHISPER_BACKEND_API and not local.local_transcription_available(backend):
            logger.warning(local._local_unavailable_message(backend))

    def _transcribe_via_api(
        self,
        audio_path: str,
        whisper_settings: dict[str, str] = None,
        language_override: str | None = None,
        preprocessed: bool = False,
        vad_filter: bool = True,
    ) -> list[dict] | None:
        """Transcribe audio using an OpenAI-compatible whisper API.

        Sends the preprocessed audio to a remote API endpoint and maps
        the verbose_json response to the internal segment format.

        Args:
            audio_path: Path to the audio file to transcribe.
            whisper_settings: Pre-fetched settings dict from _get_whisper_settings().
            preprocessed: The file already went through the preprocess filter
                chain (extract_audio_chunk(preprocess=True)); skip the
                redundant preprocess pass.
            vad_filter: False sends `vad_filter=false` so a server that
                supports the switch keeps quiet audio (tail pass, spec 1.2).
                True sends nothing, leaving the server's own default alone.

        Returns:
            List of transcript segments, or None on failure.
        """
        preprocessed_path = None
        flac_path = None
        try:
            if whisper_settings is None:
                whisper_settings = settings._get_whisper_settings()
            base_url = whisper_settings['api_base_url']
            api_key = whisper_settings['api_key']
            model = whisper_settings['api_model']

            if not base_url:
                logger.error("Whisper API base URL not configured")
                return None

            # Preprocess audio for consistent quality (skipped when the
            # chunk was extracted with the filter chain already applied)
            if not preprocessed:
                preprocessed_path = self.preprocess_audio(audio_path)
            transcribe_path = preprocessed_path if preprocessed_path else audio_path

            # After preprocessing, compress to FLAC for upload (lossless, ~4-5x smaller than WAV).
            # Prevents 413 errors from APIs with tight upload limits (e.g. OpenRouter).
            # Self-hosted Whisper servers that accept WAV directly can opt out via the
            # skip_flac_compression setting and avoid the extra encode pass.
            skip_flac = bool(whisper_settings.get('skip_flac_compression', False))
            if not skip_flac and transcribe_path.endswith('.wav'):
                fd, flac_path = tempfile.mkstemp(suffix='.flac')
                os.close(fd)
                try:
                    ffmpeg_result = utils.subprocess_registry.tracked_run(
                        ['ffmpeg', *SAFE_MEDIA_INPUT_ARGS, '-y', '-i', transcribe_path, '-c:a', 'flac', flac_path],
                        capture_output=True, timeout=FFMPEG_SHORT_TIMEOUT,
                    )
                    if ffmpeg_result.returncode == 0 and os.path.exists(flac_path):
                        transcribe_path = flac_path
                        logger.info(f"Compressed for upload: {os.path.getsize(flac_path) / 1024 / 1024:.1f}MB FLAC")
                    else:
                        # Compression failed -- remove orphaned temp file
                        common.unlink_quiet(flac_path)
                        flac_path = None
                except Exception as e:
                    logger.warning(f"FLAC compression failed, sending WAV: {e}")
                    common.unlink_quiet(flac_path)
                    flac_path = None

            # Build request
            url = remote._transcription_url(base_url)

            headers = remote._bearer_headers(api_key)

            language = settings._effective_language(language_override, whisper_settings)

            # Defensive: refuse to upload tiny or missing files. Avoids
            # remote "empty audio" / decode failures when preprocessing
            # silently produced an unusable chunk.
            try:
                upload_size = os.path.getsize(transcribe_path)
            except OSError:
                upload_size = 0
            if upload_size < 1024:
                logger.error(
                    f"Refusing to upload suspiciously small audio to Whisper API: "
                    f"{upload_size} bytes at {transcribe_path}"
                )
                return None

            logger.info("Sending audio to whisper API (size=%.1fMB)", upload_size / 1024 / 1024)

            def build_form_data(word_timestamps, vad_filter_allowed):
                form_data = {
                    'model': model,
                    'response_format': 'verbose_json',
                }
                # Only sent when disabling VAD; never send a redundant default.
                if not vad_filter and vad_filter_allowed:
                    form_data['vad_filter'] = 'false'
                if language and language != 'auto':
                    form_data['language'] = language
                form_data['timestamp_granularities[]'] = (
                    ['segment', 'word'] if word_timestamps else ['segment'])
                return form_data

            try:
                response = remote.request_transcription(
                    url, headers, transcribe_path, build_form_data, whisper_settings)
            except SSRFError as exc:
                logger.warning(f"Whisper API URL blocked: {exc}")
                return None

            if response.status_code != 200:
                if response.status_code in (401, 402, 403, 404, 429):
                    raise TranscriptionRejectedError(response.status_code)
                if response.status_code == 408 or response.status_code >= 500:
                    raise ServiceUnavailableError(
                        'whisper',
                        f"Whisper API returned {response.status_code} after retries")
                return None

            # Parse verbose_json response
            resp_json = response.json()
            api_segments = resp_json.get('segments', [])
            result = []

            for seg in api_segments:
                words = []
                for w in seg.get('words', []):
                    words.append({
                        'word': w.get('word', ''),
                        'start': w.get('start', 0),
                        'end': w.get('end', 0),
                    })

                text = seg.get('text', '').strip()
                if not text:
                    continue

                result.append({
                    'start': seg.get('start', 0),
                    'end': seg.get('end', 0),
                    'text': text,
                    'words': words,
                })

            # Spec-compliant servers return words in a top-level array, not
            # nested per segment; fold them in so boundary refinement sees them.
            chunking._attach_top_level_words(result, resp_json.get('words') or [])

            # response is a 200 here; non-200/None returned above
            if not result:
                if not vad_filter:
                    logger.debug("Whisper API returned 0 segments for repair span (no speech)")
                else:
                    body_len = len(response.text) if response.text else 0
                    logger.warning(
                        "Whisper API returned 200 but 0 usable segments (body %d chars). "
                        "This often means the server failed to decode the audio "
                        "(e.g. --convert writing to a non-writable directory).",
                        body_len,
                    )

            # Filter hallucinations
            original_count = len(result)
            result = segments.filter_hallucinations(result)
            if len(result) < original_count:
                logger.info(f"Filtered {original_count - len(result)} hallucination segments")

            duration_min = result[-1]['end'] / 60 if result else 0
            logger.info(f"API transcription completed: {len(result)} segments, {duration_min:.1f} minutes")

            return result

        except (ServiceUnavailableError, TranscriptionRejectedError):
            raise
        except Exception as e:
            logger.error(f"API transcription failed: {e}")
            return None
        finally:
            common.unlink_quiet(preprocessed_path)
            common.unlink_quiet(flac_path)

    def _record_local_stats(self, outcome, batch_size, retry_count, device, model,
                            backend, error=None) -> None:
        """Store and persist the outcome of a local batched transcription."""
        stats = {
            'outcome': outcome,
            'batch_size': batch_size,
            'retry_count': retry_count,
            'retry_succeeded': outcome == 'success' and retry_count > 0,
            'device': device,
            'gpu_device_name': get_gpu_device_name() if device == 'cuda' else None,
            'model': model,
            'backend': backend,
        }
        if error is not None:
            stats['error'] = error[:500]
        self.last_transcription_stats = stats
        local._record_local_transcription_outcome(stats)

    def _transcribe_sequential(self, audio_path: str,
                               language_override: str | None = None) -> list[dict] | None:
        """No-VAD decode on the base WhisperModel with its temperature fallback; None on failure.

        The batched pipeline can skip the start of a clip; this path is for short repair spans.
        """
        whisper_settings = settings.active_whisper_settings()
        if whisper_settings['backend'] == WHISPER_BACKEND_API:
            return self._transcribe_via_api(
                audio_path, whisper_settings, language_override=language_override,
                vad_filter=False)
        local._require_local_transcription(whisper_settings['backend'])
        language_setting = settings._effective_language(language_override, whisper_settings)
        language = None if language_setting == 'auto' else (language_setting or 'en')
        preprocessed_path = None
        with local._local_decode_activity():
            acquired = local._gpu_admission_acquire(resolve_whisper_device())
            try:
                preprocessed_path = self.preprocess_audio(audio_path)
                if whisper_settings['backend'] == WHISPER_BACKEND_PARAKEET:
                    settings._note_whisper_settings(whisper_settings)
                    return parakeet_transcriber.decode(
                        preprocessed_path or audio_path, language_setting or 'en')
                model, _batched = local.WhisperModelSingleton.get_instance()
                settings._note_whisper_settings(whisper_settings)
                segments, _info = model.transcribe(
                    preprocessed_path or audio_path, language=language, beam_size=5,
                    word_timestamps=True, vad_filter=False)
                return [{
                    'start': seg.start,
                    'end': seg.end,
                    'text': seg.text.strip(),
                    'words': [{'word': w.word, 'start': w.start, 'end': w.end}
                              for w in seg.words or []],
                } for seg in segments]
            except ModelLoadError:
                raise
            except Exception as e:
                logger.error(f"{settings._log_prefix()}Sequential transcription failed: {e}")
                return None
            finally:
                if acquired:
                    local._gpu_admission_release()
                common.unlink_quiet(preprocessed_path)

    def transcribe_span_no_vad(self, audio_path: str, start: float, end: float,
                               language_override: str | None, flag: str,
                               quiet_db: float | None = None) -> tuple[list[dict] | None, str | None]:
        """Sequential no-VAD decode of start-end. Returns (offset segments flagged `flag` or None, empty_reason).

        empty_reason is 'quiet', 'unreadable' or 'no_speech' when the span should not be retried.
        With quiet_db set, a span below it (or with an unreadable volume) is skipped before extraction.
        ModelLoadError propagates; other failures return (None, None).

        Segments are normalized before being returned.
        """
        label = _NOVAD_LABELS[flag]
        prefix = settings._log_prefix()
        if quiet_db is not None:
            volume = utils.audio.mean_volume_db(audio_path, start, end - start)
            if volume is None or volume < quiet_db:
                reading = 'unreadable' if volume is None else f'{volume:.1f} dB'
                logger.info(f"{prefix}{label} {start:.1f}s-{end:.1f}s mean volume {reading}; skipping")
                return None, 'unreadable' if volume is None else 'quiet'
        try:
            chunk_path = chunking.extract_audio_chunk(audio_path, start, end)
        except AudioExtractionTimeout as e:
            logger.warning(f"{prefix}{label} chunk extraction timed out; skipping: {e}")
            return None, None
        if not chunk_path:
            logger.warning(f"{prefix}{label} chunk extraction failed; skipping")
            return None, None
        try:
            new_segments = self._normalize_segments(
                self._transcribe_sequential(chunk_path, language_override))
        except ModelLoadError:
            raise
        except Exception as e:
            logger.warning(f"{prefix}{label} re-transcription failed; "
                           f"proceeding without {label.lower()}: {e}")
            return None, None
        finally:
            common.unlink_quiet(chunk_path)
        if new_segments is None:
            logger.warning(f"{prefix}{label} re-transcription failed; "
                           f"proceeding without {label.lower()}")
            return None, None
        new_segments = segments.filter_hallucinations(new_segments)
        if not new_segments:
            logger.info(f"{prefix}{label} re-transcription produced no segments")
            return None, 'no_speech'
        for seg in new_segments:
            seg['start'] += start
            seg['end'] += start
            for word in seg.get('words') or []:
                word['start'] += start
                word['end'] += start
            seg[flag] = True
        logger.info(
            f"{prefix}{label} re-transcription added {len(new_segments)} segment(s) "
            f"({new_segments[0]['start']:.1f}s-{new_segments[-1]['end']:.1f}s)")
        return new_segments, None

    def repair_gaps(self, audio_path: str, segments: list[dict], min_s: float,
                    language_override: str | None = None,
                    skip=()) -> tuple[list[dict], list[dict]]:
        """Re-decode transcript gaps of min_s or more the batched decoder skipped.

        Holes matching a `skip` record are left alone; caps keep the largest holes.
        Returns (added segments flagged novad_hole, holes that held nothing as {start, end, reason}).
        """
        prefix = settings._log_prefix()
        holes = transcript_gaps(segments, min_s)
        known = [hole for hole in holes if chunking._hole_recorded(hole, skip)]
        if known:
            logger.info(f"{prefix}Skipping {len(known)} untranscribed hole(s) "
                        f"already re-transcribed without speech")
            holes = [hole for hole in holes if hole not in known]
        picked, skipped = [], []
        budget = HOLE_RETRANSCRIBE_MAX_SECONDS
        for hole in sorted(holes, key=lambda h: h[1] - h[0], reverse=True):
            length = hole[1] - hole[0]
            if len(picked) < HOLE_RETRANSCRIBE_MAX_HOLES and length <= budget:
                picked.append(hole)
                budget -= length
            else:
                skipped.append(hole)
        if skipped:
            logger.info(
                f"{prefix}Skipping {len(skipped)} untranscribed hole(s) "
                f"({sum(e - s for s, e in skipped):.1f} s) over the per-episode cap of "
                f"{HOLE_RETRANSCRIBE_MAX_HOLES} holes / {HOLE_RETRANSCRIBE_MAX_SECONDS:.0f} s")
        added, empty = [], []
        for hole_start, hole_end in sorted(picked):
            logger.info(
                f"{prefix}Untranscribed hole {hole_start:.1f}s-{hole_end:.1f}s "
                f"({hole_end - hole_start:.1f} s); re-transcribing without VAD")
            recovered, reason = self.transcribe_span_no_vad(
                audio_path, hole_start, hole_end, language_override, 'novad_hole',
                quiet_db=HOLE_RETRANSCRIBE_QUIET_DB)
            if recovered:
                added.extend(recovered)
            elif reason:
                empty.append({'start': hole_start, 'end': hole_end, 'reason': reason})
        return added, empty

    def get_audio_duration(self, audio_path: str) -> float | None:
        """Get audio duration in seconds using ffprobe.

        Delegates to utils.audio.get_audio_duration for consistent implementation.
        """
        duration = utils.audio.get_audio_duration(audio_path)
        if duration is not None:
            logger.info(f"Audio duration: {duration:.1f}s ({duration/60:.1f} min)")
        return duration

    def clear_cuda_cache(self):
        """Clear CUDA cache to free GPU memory.

        Delegates to utils.gpu.clear_gpu_memory().
        """
        clear_gpu_memory()
        logger.info("CUDA cache cleared")

    def preprocess_audio(self, input_path: str) -> str | None:
        """
        Normalize audio for consistent transcription.
        Returns path to preprocessed file, or None if preprocessing fails.
        Caller is responsible for cleaning up the returned temp file.
        """
        fd, output_path = tempfile.mkstemp(suffix='.wav')
        os.close(fd)
        success = False

        try:
            cmd = [
                'ffmpeg', *SAFE_MEDIA_INPUT_ARGS, '-y', '-i', input_path,
                '-vn',           # ignore embedded cover art (#556)
                '-ar', '16000',  # 16kHz (Whisper native sample rate)
                '-ac', '1',      # Mono
                '-af', settings.PREPROCESS_AUDIO_FILTERS,
                output_path
            ]
            # Scale timeout by file size: 10s per MB (~1MB/min for podcasts)
            # e.g. 50MB file = 500s, 100MB = 1000s, floor at FFMPEG_LONG_TIMEOUT (300s)
            file_size_mb = os.path.getsize(input_path) / (1024 * 1024)
            preprocess_timeout = max(FFMPEG_LONG_TIMEOUT, int(file_size_mb * 10))
            result = utils.subprocess_registry.tracked_run(cmd, capture_output=True, timeout=preprocess_timeout)
            if result.returncode == 0:
                logger.info(f"Audio preprocessed: {input_path} -> {output_path}")
                success = True
                return output_path
            logger.warning(
                f"Audio preprocessing failed (returncode={result.returncode}), "
                f"stderr: {chunking._ffmpeg_error_tail(result.stderr)}"
            )
            return None
        except subprocess.TimeoutExpired:
            logger.warning(f"Audio preprocessing timed out after {preprocess_timeout}s, using original")
            return None
        except Exception as e:
            logger.warning(f"Audio preprocessing error: {e}, using original")
            return None
        finally:
            # Clean up temp file on any failure path
            if not success:
                common.unlink_quiet(output_path)

    def check_audio_availability(self, url: str, timeout: int = 10,
                                 user_agent: str = None) -> tuple:
        """Check if audio URL is accessible without downloading.

        Performs a HEAD request to verify the CDN has the file ready.
        Use this before downloading to avoid failures on newly published episodes
        where the CDN hasn't propagated the file yet.

        Args:
            url: Audio file URL to check
            timeout: Request timeout in seconds
            user_agent: Sent instead of the download User-Agent when given

        Returns:
            Tuple of (available: bool, error_message: str or None)
        """
        from utils.safe_http import safe_head
        headers = {'User-Agent': user_agent or download_user_agent()}
        try:
            response = safe_head(
                url,
                trust=URLTrust.FEED_CONTENT,
                timeout=timeout,
                # Megaphone / Art19 / simplecast often chain 6-8 redirects
                # (CDN edge -> regional -> asset), and Acast adds analytics
                # bouncers on top. Bumped to 10 so we don't false-fail
                # CDN checks for legitimate feeds.
                max_redirects=HTTP_MAX_REDIRECTS_FEED,
                headers=headers,
            )
        except SSRFError as e:
            logger.warning(f"SSRF blocked in check_audio_availability: {e}")
            return False, f"URL blocked: {e}"
        except requests.exceptions.Timeout:
            return False, "CDN timeout"
        except requests.RequestException as e:
            return False, f"CDN check failed: {e}"

        keep_query = log_download_query_enabled()
        logger.info("Checking audio availability: "
                    f"{safe_url_for_log(url, keep_path=True, keep_query=keep_query)}")
        for line in redirect_chain_for_log(response, keep_query=keep_query):
            logger.info(line)

        if response.status_code == 200:
            return True, None
        if response.status_code == 403:
            # Distinct from the 404 below so the retry classifier can treat it
            # as permanent: an access decision does not change on replay.
            return False, f"{CDN_REFUSED_PREFIX} the request (403)"
        if response.status_code == 404:
            return False, "CDN not ready (404)"
        if response.status_code >= 500:
            return False, f"CDN server error ({response.status_code})"
        return True, None

    def download_audio(self, url: str, timeout: tuple = (10, 300),
                       user_agent: str = None) -> str | None:
        """Download audio file from URL.

        Args:
            url: Audio file URL
            timeout: (connect_timeout, read_timeout) in seconds
            user_agent: Sent instead of the download User-Agent when given

        Raises:
            AudioTooLargeError: the enclosure exceeds the MAX_AUDIO_DOWNLOAD_MB
                cap (default 500MB). Other failures keep the return-None
                contract.
        """
        keep_query = log_download_query_enabled()
        try:
            logger.info("Downloading audio from: "
                        f"{safe_url_for_log(url, keep_path=True, keep_query=keep_query)}")
            headers = {
                'User-Agent': user_agent or download_user_agent(),
                'Accept': '*/*',
                'Accept-Language': 'en-US,en;q=0.9',
            }
            response = safe_get(
                url,
                trust=URLTrust.FEED_CONTENT,
                timeout=timeout,
                max_redirects=HTTP_MAX_REDIRECTS_FEED,
                stream=True,
                headers=headers,
            )
            for line in redirect_chain_for_log(response, keep_query=keep_query):
                logger.info(line)
            response.raise_for_status()
        except SSRFError as e:
            logger.warning(f"SSRF blocked in download_audio: {e}")
            return None

        temp_path = None
        try:
            max_mb = settings._max_download_mb()
            max_bytes = max_mb * 1024 * 1024

            # Check file size (isdigit guards malformed headers; the stream
            # cap below still protects when the header is unusable)
            content_length = response.headers.get('Content-Length')
            if content_length and content_length.isdigit():
                size_mb = int(content_length) / (1024 * 1024)
                if size_mb > max_mb:
                    response.close()
                    raise ResponseTooLargeError(
                        f"Audio file is {size_mb:.1f}MB, over the {max_mb}MB download cap"
                    )
                logger.info(f"Audio file size: {size_mb:.1f}MB")

            # Cap the stream independent of Content-Length (absent on chunked
            # responses) so a feed enclosure can't fill the disk (transcription-1).
            with tempfile.NamedTemporaryFile(delete=False, suffix='.mp3') as tmp:
                temp_path = tmp.name
                try:
                    stream_to_file_capped(response, tmp, max_bytes)
                except ResponseTooLargeError:
                    response.close()
                    raise ResponseTooLargeError(
                        f"Audio stream exceeded the {max_mb}MB download cap"
                    ) from None

            logger.info(f"Downloaded audio to: {temp_path}")
            return temp_path
        # Only the two audio-cap raises above can reach this handler:
        # safe_get runs in the first try block, and nothing else in this
        # try uses the capped-read helpers.
        except ResponseTooLargeError as e:
            common.unlink_quiet(temp_path)
            logger.error(f"{e} ({safe_url_for_log(url)})")
            raise AudioTooLargeError(str(e)) from e
        except Exception as e:
            common.unlink_quiet(temp_path)
            logger.error(f"Failed to download audio: {e}")
            return None

    def _normalize_segments(self, segments: list[dict] | None) -> list[dict] | None:
        if segments is not None:
            try:
                segments = cast(list[dict], normalize_segments(segments))
            except Exception as e:
                # `normalize_segments` shouldn't fail. We'll fix if errors occur.
                logger.error(f"Error normalizing transcript segments: {e}")
                raise
        return segments

    def transcribe(
        self,
        audio_path: str,
        language_override: str | None = None,
        vad_filter: bool = True,
        preprocessed: bool = False,
        whisper_settings: dict[str, str] | None = None,
    ) -> list[dict] | None:
        """Transcribe with OOM retries; explicit settings pin the backend.
        language_override replaces the global language; preprocessed skips filtering.
        vad_filter is forwarded to either backend.
        
        Segments are normalized before being returned.
        """
        return self._normalize_segments(
            self._transcribe(audio_path,
                             language_override=language_override,
                             vad_filter=vad_filter,
                             preprocessed=preprocessed,
                             whisper_settings=whisper_settings))

    def _transcribe(
        self,
        audio_path: str,
        language_override: str | None = None,
        vad_filter: bool = True,
        preprocessed: bool = False,
        whisper_settings: dict[str, str] | None = None,
    ) -> list[dict] | None:
        """Transcribe with OOM retries; explicit settings pin the backend.
        language_override replaces the global language; preprocessed skips filtering.
        vad_filter is forwarded to either backend."""
        whisper_settings = whisper_settings or settings.active_whisper_settings()
        if whisper_settings['backend'] == WHISPER_BACKEND_API:
            return self._transcribe_via_api(
                audio_path, whisper_settings,
                language_override=language_override,
                preprocessed=preprocessed,
                vad_filter=vad_filter,
            )
        # Outside the try below so the actionable message is not swallowed.
        local._require_local_transcription(whisper_settings['backend'])

        # Local decode under the failover config pins the configured failover
        # model; reset in the finally below so later calls see the primary
        # model again.
        override_token = None
        failover_local_model = (
            whisper_settings.get('local_model') if whisper_settings.get('is_failover') else None)
        if failover_local_model:
            override_token = settings._local_model_override.set(failover_local_model)

        activity = ExitStack()
        try:
            preprocessed_path = None
            # Defensive defaults: referenced in the stats recording below even if
            # an exception hits before the real assignments further down.
            device = None
            batch_size = None
            current_model = None
            retry_count = 0
            # Hold the device across preprocessing and retries; initialize the flag
            # before acquisition so finally can handle failed device resolution.
            gpu_admission_acquired = False
            activity.enter_context(local._local_decode_activity())
            gpu_admission_acquired = local._gpu_admission_acquire(resolve_whisper_device())

            language_setting = settings._effective_language(language_override, whisper_settings)
            transcribe_language = None if language_setting == 'auto' else (language_setting or 'en')

            # Get audio duration for adaptive batch sizing
            audio_duration = self.get_audio_duration(audio_path)

            if whisper_settings['backend'] == WHISPER_BACKEND_PARAKEET:
                device = resolve_whisper_device()
                current_model = PARAKEET_MODEL_NAME
                if not preprocessed:
                    preprocessed_path = self.preprocess_audio(audio_path)
                settings._note_whisper_settings(whisper_settings)
                result = segments.filter_hallucinations(parakeet_transcriber.decode(
                    preprocessed_path or audio_path, language_setting or 'en'))
                self._record_local_stats('success', None, 0, device, current_model,
                                         backend=WHISPER_BACKEND_PARAKEET)
                return result

            # Get the batched pipeline for efficient transcription
            model = local.WhisperModelSingleton.get_batched_pipeline()
            current_model = local.WhisperModelSingleton.get_current_model_name()

            logger.info(f"Starting transcription of: {audio_path} (model: {current_model})")

            # Preprocess audio for consistent quality (skipped when the
            # chunk was extracted with the filter chain already applied)
            if not preprocessed:
                preprocessed_path = self.preprocess_audio(audio_path)
            transcribe_path = preprocessed_path if preprocessed_path else audio_path
            if preprocessed_path and not vad_filter:
                # The no-VAD clips must cover the file actually transcribed;
                # loudnorm can shift the duration by tens of milliseconds.
                audio_duration = (self.get_audio_duration(preprocessed_path)
                                  or audio_duration)

            # Adjust batch size based on device and audio duration
            device = resolve_whisper_device()
            if device == "cuda":
                # Use adaptive batch size based on duration to prevent OOM
                batch_size = local.get_batch_size_for_duration(audio_duration)
                duration_str = f"{audio_duration/60:.1f} min" if audio_duration else "unknown"
                logger.info(f"Using adaptive batch size: {batch_size} (duration: {duration_str})")
            else:
                batch_size = 8  # Smaller batch for CPU

            tier_batch_size = local.tier_batch_size(audio_duration)

            # Retry logic for CUDA OOM errors
            max_retries = 3
            retry_count = 0
            reload_model = False
            segments_generator = None

            while retry_count < max_retries:
                if reload_model:
                    segments_generator = None
                    model = None
                    local.WhisperModelSingleton.unload_model()
                    clear_gpu_memory()
                    model = local.WhisperModelSingleton.get_batched_pipeline()
                    reload_model = False
                try:
                    # Clear CUDA cache before each attempt
                    if device == "cuda":
                        self.clear_cuda_cache()

                    # word_timestamps=True enables precise boundary refinement later.
                    # Pinning a language prevents Whisper from misdetecting on music
                    # intros or sound effects (e.g. English podcast detected as Spanish
                    # at 93% confidence). `whisper_language=auto` (or blank env var)
                    # lets Whisper auto-detect -- useful for multilingual / non-English
                    # podcasts. The non-English DAI heuristic downstream
                    # (segments.detect_non_english_segment) is gated by
                    # segments.should_detect_foreign_language so it only runs when the audio
                    # is English; on non-English podcasts it would false-positive
                    # every segment.
                    settings._note_whisper_settings(whisper_settings)
                    segments_generator, info = model.transcribe(
                        transcribe_path,
                        language=transcribe_language,
                        beam_size=5,
                        batch_size=batch_size,
                        word_timestamps=True,  # Enable word-level timestamps for boundary refinement
                        vad_filter=vad_filter,
                        vad_parameters=dict(
                            min_silence_duration_ms=1000,  # Increased from 500 - less aggressive skipping
                            speech_pad_ms=600,  # Increased from 400 - more padding for ad segments
                            threshold=0.3  # Lower threshold = more sensitive to speech in ads
                        ) if vad_filter else None,
                        # None here when duration probing failed: a no-VAD
                        # span of 30s or more then still fails, and the caller
                        # logs that as a failure rather than silence.
                        clip_timestamps=(
                            None if vad_filter
                            else chunking._full_span_clips(audio_duration)),
                    )

                    # Log detected language
                    detected_lang = info.language if hasattr(info, 'language') else 'unknown'
                    lang_prob = info.language_probability if hasattr(info, 'language_probability') else 0
                    logger.info(f"Detected primary language: {detected_lang} (probability: {lang_prob:.2f})")

                    should_detect_foreign = segments.should_detect_foreign_language(
                        transcribe_language, detected_lang
                    )
                    logger.info(
                        "Foreign-language DAI detector: %s",
                        "enabled" if should_detect_foreign else "disabled",
                    )

                    # Collect segments with real-time progress logging
                    result = []
                    segment_count = 0
                    last_log_time = 0

                    non_english_count = 0
                    for segment in segments_generator:
                        segment_count += 1
                        # Store word-level timestamps for boundary refinement
                        words = []
                        if segment.words:
                            for w in segment.words:
                                words.append({
                                    "word": w.word,
                                    "start": w.start,
                                    "end": w.end
                                })

                        # Detect non-English segments (potential DAI ads).
                        # Whisper segments don't have per-segment language, but we can
                        # detect non-English by checking for non-ASCII characters or
                        # using the overall detected language with segment analysis.
                        segment_text = segment.text.strip()
                        is_foreign = should_detect_foreign and segments.detect_non_english_segment(
                            segment_text, detected_lang
                        )

                        segment_dict = {
                            "start": segment.start,
                            "end": segment.end,
                            "text": segment_text,
                            "words": words  # Word timestamps for boundary refinement
                        }

                        # Flag non-English segments for ad detection
                        if is_foreign:
                            segment_dict["is_foreign_language"] = True
                            segment_dict["detected_language"] = "non-english"
                            non_english_count += 1

                        result.append(segment_dict)

                        # Log progress every 10 segments
                        if segment_count % 10 == 0:
                            progress_min = segment.end / 60
                            logger.info(f"Transcription progress: {segment_count} segments, {progress_min:.1f} minutes processed")

                        # Log every 30 seconds of audio processed
                        if segment.end - last_log_time > 30:
                            last_log_time = segment.end
                            # Log the last segment's text (truncated)
                            text_preview = segment.text.strip()[:100] + "..." if len(segment.text.strip()) > 100 else segment.text.strip()
                            logger.info(f"[{format_vtt_timestamp(segment.start)}] {text_preview}")

                    # Filter out hallucinations
                    original_count = len(result)
                    result = segments.filter_hallucinations(result)
                    if len(result) < original_count:
                        logger.info(f"Filtered {original_count - len(result)} hallucination segments")

                    # Log non-English segments (potential DAI ads)
                    if non_english_count > 0:
                        logger.info(f"Flagged {non_english_count} non-English segments as potential ads")

                    duration_min = result[-1]['end'] / 60 if result else 0
                    logger.info(f"Transcription completed: {len(result)} segments, {duration_min:.1f} minutes")

                    if device == "cuda" and batch_size < tier_batch_size:
                        # Completing below the tier (downshift, clamp, or probe)
                        # proves the size fits; failures never persist anything.
                        local.record_batch_size_ceiling(batch_size)

                    self._record_local_stats('success', batch_size, retry_count,
                                             device, current_model,
                                             backend=whisper_settings['backend'])

                    return result

                except Exception as inner_e:
                    error_str = str(inner_e).lower()
                    is_oom = 'out of memory' in error_str

                    if ('cuda' in error_str or is_oom) and retry_count < max_retries - 1:
                        retry_count += 1
                        if is_oom:
                            old_batch_size = batch_size
                            batch_size = max(1, batch_size // 2)
                            logger.warning(
                                f"CUDA OOM detected (attempt {retry_count}/{max_retries}). "
                                f"Reducing batch size: {old_batch_size} -> {batch_size}"
                            )
                        else:
                            # Only an OOM says the size does not fit; anything
                            # else is retried as-is so it cannot pin a ceiling.
                            logger.warning(
                                f"CUDA error (attempt {retry_count}/{max_retries}), "
                                f"retrying at batch size {batch_size}: {inner_e}"
                            )
                        reload_model = True
                        continue
                    # Non-OOM error or max retries reached
                    raise

        except Exception as e:
            logger.error(f"Transcription failed: {e}")
            self._record_local_stats('failed', batch_size, retry_count, device,
                                     current_model, error=str(e),
                                     backend=whisper_settings['backend'])
            # Clean up GPU memory on ANY failure to prevent memory leaks
            # This is critical for OOM recovery - free memory before retry
            try:
                clear_gpu_memory()
                local._unload_local_models()
                logger.info("Cleaned up GPU memory after transcription failure")
            except Exception as cleanup_err:
                logger.warning(f"Failed to clean up GPU memory: {cleanup_err}")
            if isinstance(e, ModelLoadError):
                raise
            return None
        finally:
            if gpu_admission_acquired:
                local._gpu_admission_release()
            activity.close()
            if override_token is not None:
                settings._local_model_override.reset(override_token)
            # Clean up preprocessed file
            if preprocessed_path and os.path.exists(preprocessed_path):
                try:
                    os.unlink(preprocessed_path)
                    logger.debug(f"Cleaned up preprocessed file: {preprocessed_path}")
                except OSError:
                    pass

    def _run_chunk_plan(
        self,
        ctx: _ChunkPlanContext,
        plan_subset: list[tuple[int, float, float]],
        whisper_settings: dict[str, str],
        results: list[list[dict] | None],
        stop_on_connectivity_error: bool,
    ) -> tuple[object, list[Exception]]:
        """Fill indexed results; return a failover chunk, _ABORT_CHUNK_PLAN, or None."""
        audio_path, language_override, prefix = ctx.audio_path, ctx.language_override, ctx.prefix
        max_failed_chunks = ctx.max_failed_chunks
        # Per pass, so each pass classifies on its own failures; stragglers from
        # an aborted pass keep appending only to that pass's lists.
        connectivity_errors: list[Exception] = []
        # Chunks whose ffmpeg extract failed before any API call, so an abort
        # can name local extraction as the cause (#556).
        extraction_failures: list[int] = []
        # Subset of the above that ran out of clock rather than failing to decode (#644).
        extraction_timeouts: list[int] = []
        extract_as_flac = not bool(whisper_settings.get('skip_flac_compression', False))

        def _process_chunk(chunk_idx: int, c_start: float, c_end: float):
            if ctx.switch_event.is_set():
                return chunk_idx, None
            try:
                chunk_path = chunking.extract_audio_chunk(
                    audio_path, c_start, c_end,
                    preprocess=True, flac=extract_as_flac,
                )
            except AudioExtractionTimeout as e:
                logger.error(f"{prefix}Chunk {chunk_idx + 1}: {e}")
                extraction_failures.append(chunk_idx)
                extraction_timeouts.append(chunk_idx)
                return chunk_idx, None
            if not chunk_path:
                logger.error(f"{prefix}Chunk {chunk_idx + 1}: ffmpeg extract failed")
                extraction_failures.append(chunk_idx)
                return chunk_idx, None
            try:
                if ctx.switch_event.is_set():
                    return chunk_idx, None
                segs = self._transcribe_via_api(
                    chunk_path, whisper_settings,
                    language_override=language_override,
                    preprocessed=True,
                )
                if segs is None:
                    return chunk_idx, None
                # Adjust timestamps to be relative to full audio
                for seg in segs:
                    seg['start'] += c_start
                    seg['end'] += c_start
                    if seg.get('words'):
                        for word in seg['words']:
                            word['start'] += c_start
                            word['end'] += c_start
                return chunk_idx, segs
            except (ServiceUnavailableError, TranscriptionRejectedError) as e:
                # list.append is thread-safe; recorded so an endpoint-down
                # abort can defer the episode (#482) or trigger failover (#806)
                # instead of failing it.
                logger.error(f"{prefix}Chunk {chunk_idx + 1} failed: {e}")
                connectivity_errors.append(e)
                return chunk_idx, None
            except Exception as e:
                logger.error(f"{prefix}Chunk {chunk_idx + 1} failed: {e}")
                return chunk_idx, None
            finally:
                common.unlink_quiet(chunk_path)

        failed = 0
        # Managed manually (not `with`) so an early abort can return promptly:
        # a `with` block's __exit__ calls shutdown(wait=True), which would
        # re-block on the in-flight workers and defeat the short-circuit.
        exe = ThreadPoolExecutor(max_workers=ctx.max_workers)
        seen_connectivity = 0
        try:
            futures = [
                exe.submit(run_context.run_in_worker_thread(_process_chunk), i, s, e)
                for i, s, e in plan_subset
            ]
            for completed, fut in enumerate(as_completed(futures), 1):
                chunk_idx, segs = fut.result()
                results[chunk_idx] = segs
                if segs is None:
                    failed += 1
                logger.info(
                    f"{prefix}Chunk {chunk_idx + 1} complete "
                    f"({completed}/{len(plan_subset)}): "
                    f"{len(segs) if segs else 0} segments"
                )
                # A connectivity failure switches backend immediately rather
                # than waiting on the failure budget below (#806).
                if (stop_on_connectivity_error and segs is None
                        and len(connectivity_errors) > seen_connectivity):
                    ctx.switch_event.set()
                    return chunk_idx, connectivity_errors
                seen_connectivity = len(connectivity_errors)
                # Short-circuit like the sequential path: once the failure
                # budget is blown the run can't succeed, so stop instead of
                # burning a full HTTP timeout on each remaining doomed chunk.
                # Returning here still runs the finally below (pool shutdown).
                if failed > max_failed_chunks:
                    ctx.switch_event.set()
                    logger.error(
                        f"{prefix}Too many failed chunks ({failed} > {max_failed_chunks}); "
                        f"aborting transcription early"
                    )
                    # Classify the abort as an outage only when connectivity
                    # failures are the majority cause; a single timeout among
                    # mostly content/request failures must not defer the
                    # episode (#482). The reverse mix falls back to the normal
                    # transient-retry ladder, whose next attempt re-classifies.
                    if len(connectivity_errors) * 2 > failed:
                        raise connectivity_errors[0]
                    # Name local extraction as the cause when it dominates
                    # (#556): "Failed to transcribe audio" points users at a
                    # healthy transcription provider.
                    if len(extraction_failures) * 2 > failed:
                        if len(extraction_timeouts) * 2 > len(extraction_failures):
                            raise AudioExtractionTimeout(
                                'Audio chunk extraction timed out (ffmpeg ran '
                                'out of time preparing chunks); the '
                                'transcription API was not the problem')
                        raise AudioExtractionError(
                            'Audio chunk extraction failed (ffmpeg could not '
                            'decode the source file); the transcription API '
                            'was not the problem')
                    return _ABORT_CHUNK_PLAN, connectivity_errors
        finally:
            # wait=False: return without blocking on in-flight workers (they
            # finish in the background and self-clean temp files via
            # _process_chunk's finally). cancel_futures drops queued chunks.
            exe.shutdown(wait=False, cancel_futures=True)
        return None, connectivity_errors

    def _transcribe_chunked_parallel_api(
        self,
        audio_path: str,
        duration: float,
        whisper_settings: dict[str, str],
        language_override: str | None = None,
        allow_failover: bool = True,
    ) -> list[dict] | None:
        """Merge parallel API chunks; failover retains completed chunks only for the same backend."""
        chunk_settings = settings._get_chunk_settings()
        chunk_duration = chunk_settings['max_chunk_seconds']
        overlap = chunk_settings['chunk_overlap_seconds']
        max_workers = get_pool().chunk_workers(chunk_settings['concurrent_chunks'])
        prefix = settings._log_prefix()

        # Computed once up front (not only for the chunk-plan path below) so
        # the single-shot branch can also switch on an outage instead of
        # just propagating it (#806).
        can_switch_on_outage = False
        if allow_failover and not whisper_settings.get('is_failover'):
            try:
                can_switch_on_outage = failover.is_configured(failover.TARGET_WHISPER)
            except Exception as e:
                logger.warning(f"Could not check whisper failover configuration: {e}")

        # Single-shot if entire audio fits in one chunk
        if duration <= chunk_duration:
            logger.info(
                f"Audio duration {duration/60:.1f}min fits in one chunk "
                f"({chunk_duration}s), single-shot API transcription"
            )
            try:
                return self.transcribe(audio_path, language_override=language_override,
                                       whisper_settings=whisper_settings)
            except (ServiceUnavailableError, TranscriptionRejectedError) as e:
                if not can_switch_on_outage:
                    raise
                fo = remote._switch_to_failover_whisper(e)
                if fo['backend'] != whisper_settings['backend']:
                    return self._transcribe_chunked_local(audio_path, duration, fo, language_override)
                return self.transcribe(audio_path, language_override=language_override,
                                       whisper_settings=fo)

        # Build chunk plan: list of (idx, start, end_with_overlap)
        plan: list[tuple[int, float, float]] = []
        chunk_start = 0.0
        idx = 0
        while chunk_start < duration:
            chunk_end = min(chunk_start + chunk_duration, duration)
            # A sub-second tail is not worth its own chunk/upload; fold it
            # into this one instead of leaving a near-empty final chunk.
            if 0 < duration - chunk_end < 1.0:
                chunk_end = duration
            chunk_end_with_overlap = (
                min(chunk_end + overlap, duration)
                if chunk_end < duration else chunk_end
            )
            plan.append((idx, chunk_start, chunk_end_with_overlap))
            idx += 1
            chunk_start = chunk_end

        num_chunks = len(plan)
        max_failed_chunks = max(1, num_chunks // 5)
        logger.info(
            f"Starting parallel chunked transcription: {duration/60:.1f} min "
            f"in {num_chunks} chunks (chunk_size={chunk_duration}s, "
            f"overlap={overlap}s, workers={max_workers})"
        )

        results: list[list[dict] | None] = [None] * num_chunks
        plan_ctx = _ChunkPlanContext(audio_path, language_override, prefix, max_workers, max_failed_chunks)

        # Class-qualified: some callers pass a duck-typed self.
        outage, connectivity_errors = Transcriber._run_chunk_plan(
            self, plan_ctx, plan, whisper_settings, results,
            stop_on_connectivity_error=can_switch_on_outage,
        )

        if isinstance(outage, int):
            fo = remote._switch_to_failover_whisper(connectivity_errors[0])
            if fo['backend'] != whisper_settings['backend']:
                # Different backend type: discard the partial chunk results
                # and rerun the whole episode on the failover backend.
                return self._transcribe_chunked_local(audio_path, duration, fo, language_override)
            # Same backend type: rerun only chunks still missing a result,
            # keeping what this pass finished. No further switch, already failover.
            remaining = [(i, s, e) for i, s, e in plan if results[i] is None]
            # Fresh event for this pass: the first pass's switch_event is
            # already set, which would make every remaining chunk bail.
            retry_ctx = replace(plan_ctx, switch_event=threading.Event())
            second, _ = Transcriber._run_chunk_plan(
                self, retry_ctx, remaining, fo, results, stop_on_connectivity_error=False,
            )
            if second is _ABORT_CHUNK_PLAN:
                return None
        elif outage is _ABORT_CHUNK_PLAN:
            return None

        # Merge in chronological order so merge_overlapping_segments
        # dedupes overlap zones the same way the sequential path does.
        all_segments: list[dict] = []
        failed_chunks: list[tuple[float, float]] = []
        for chunk_idx, c_start, c_end in plan:
            chunk_segs = results[chunk_idx]
            if chunk_segs is None:
                failed_chunks.append((c_start, c_end))
                continue
            if not all_segments:
                all_segments = chunk_segs
            else:
                all_segments = chunking.merge_overlapping_segments(
                    all_segments, chunk_segs, c_start, overlap
                )

        # Both passes above already abort (return None) once their failure
        # budget is blown, so by here failures are within budget; the
        # surviving failed_chunks only feed the gap-summary log below.
        original_count = len(all_segments)
        all_segments = segments.filter_hallucinations(all_segments)
        if len(all_segments) < original_count:
            logger.info(
                f"Filtered {original_count - len(all_segments)} "
                f"hallucination segments after merge"
            )

        duration_min = all_segments[-1]['end'] / 60 if all_segments else 0
        if failed_chunks:
            gap_summary = ", ".join(
                f"{s/60:.1f}-{e/60:.1f}min" for s, e in failed_chunks
            )
            logger.warning(
                f"Parallel chunked transcription complete with "
                f"{len(failed_chunks)} gap(s): {gap_summary}. "
                f"Partial transcript returned."
            )
        logger.info(
            f"Parallel chunked transcription complete: "
            f"{len(all_segments)} segments, {duration_min:.1f} minutes "
            f"from {num_chunks} chunks"
        )
        chunking._warn_if_word_timestamps_missing(all_segments, whisper_settings)
        return all_segments

    def transcribe_chunked(
        self,
        audio_path: str,
        language_override: str | None = None,
        whisper_settings: dict[str, str] | None = None,
    ) -> list[dict] | None:
        """Choose API or local chunking using explicit settings, otherwise the active configuration."""
        duration = self.get_audio_duration(audio_path)
        if duration is None:
            logger.error("Cannot determine audio duration for chunked transcription")
            return None

        # Branch to parallel path for remote API backends - no GPU or
        # WhisperModelSingleton constraints, so chunks can run concurrently.
        whisper_settings = whisper_settings or settings.active_whisper_settings()
        if whisper_settings['backend'] == WHISPER_BACKEND_API:
            with get_pool().transcribing():
                return self._transcribe_chunked_parallel_api(
                    audio_path, duration, whisper_settings,
                    language_override=language_override,
                )
        return self._transcribe_chunked_local(audio_path, duration, whisper_settings, language_override)

    def _transcribe_chunked_local(
        self,
        audio_path: str,
        duration: float,
        whisper_settings: dict[str, str],
        language_override: str | None = None,
    ) -> list[dict] | None:
        """Local chunked transcription; a trigger error reruns the whole episode
        on the failover whisper config, once (#806)."""
        failover_local_model = (
            whisper_settings.get('local_model') if whisper_settings.get('is_failover') else None)
        # Set for the whole body so chunk sizing also sees the failover model.
        override_token = settings._local_model_override.set(failover_local_model) if failover_local_model else None
        try:
            local._require_local_transcription(whisper_settings['backend'])
            with local._local_decode_activity():
                return self._transcribe_chunked_local_body(
                    audio_path, duration, whisper_settings, language_override)
        except Exception as e:
            if not whisper_settings.get('is_failover') and remote.is_whisper_failover_trigger(e):
                if failover.is_configured(failover.TARGET_WHISPER):
                    return self.transcribe_chunked(
                        audio_path, language_override,
                        whisper_settings=remote._switch_to_failover_whisper(e))
            raise
        finally:
            if override_token is not None:
                settings._local_model_override.reset(override_token)

    def _transcribe_chunked_local_body(
        self,
        audio_path: str,
        duration: float,
        whisper_settings: dict[str, str],
        language_override: str | None = None,
    ) -> list[dict] | None:
        """Decode memory-sized chunks, reducing their size after OOM."""
        # Get current model and device for memory calculation
        device = resolve_whisper_device()

        if whisper_settings['backend'] == WHISPER_BACKEND_PARAKEET:
            chunk_duration, memory_reason = CHUNK_DEFAULT_DURATION_SECONDS, 'parakeet fixed windows'
        else:
            model_name = local.WhisperModelSingleton.get_configured_model()
            chunk_duration, memory_reason = chunking.calculate_optimal_chunk_duration(
                model_name, device
            )
        overlap = CHUNK_OVERLAP_SECONDS

        # If calculated chunk can handle the entire audio, try regular transcription first
        if duration <= chunk_duration:
            logger.info(
                f"Audio duration {duration/60:.1f}min fits in calculated chunk "
                f"({chunk_duration/60:.0f}min), trying regular transcription"
            )
            try:
                result = self.transcribe(audio_path, language_override=language_override,
                                         whisper_settings=whisper_settings)
                if result is not None:
                    return result
                # If transcribe returns None but didn't raise, fall through to chunked
                logger.warning("Regular transcription returned None, falling back to chunked")
            except Exception as e:
                error_str = str(e).lower()
                if 'out of memory' in error_str or 'oom' in error_str or 'cuda' in error_str:
                    logger.warning(f"OOM during regular transcription, falling back to chunked: {e}")
                    # Reduce chunk size for chunked attempt
                    chunk_duration = max(CHUNK_MIN_DURATION_SECONDS, chunk_duration // 2)
                    clear_gpu_memory()
                    local._unload_local_models()
                else:
                    raise

        # Calculate number of chunks
        num_chunks = max(1, int((duration - overlap) // (chunk_duration - overlap)) + 1)
        logger.info(
            f"Starting chunked transcription: {duration/60:.1f} min audio in ~{num_chunks} chunks "
            f"(chunk_size={chunk_duration/60:.0f}min, overlap={overlap}s) - {memory_reason}"
        )

        all_segments = []
        chunk_start = 0
        chunk_num = 0
        oom_retry_count = 0
        max_oom_retries = 3
        extract_timeout_retries = 0
        max_extract_timeout_retries = 1
        failed_chunks: list[tuple[float, float]] = []
        # Tolerate a minority of failed chunks (e.g. flaky remote Whisper API)
        # rather than aborting the whole episode. Cap at ~20% of expected chunks.
        max_failed_chunks = max(1, num_chunks // 5)

        # Extractions overlap GPU inference; close() reaps whatever is
        # still in flight on any exit path so no pool thread or temp
        # chunk file outlives the run.
        prefetcher = _ChunkPrefetcher(audio_path)
        try:
            # Start the first extractions now and load the model while they
            # run; done in sequence these are the two longest serial waits
            # of the pass. Load failures surface inside the loop, which
            # keeps its OOM handling.
            prefetcher.prime(chunk_start, chunk_duration, duration, overlap)
            try:
                if whisper_settings['backend'] == WHISPER_BACKEND_PARAKEET:
                    ParakeetModelSingleton.get_instance()
                else:
                    local.WhisperModelSingleton.get_batched_pipeline()
            except Exception as e:
                logger.debug(f"Model warm-up deferred to first chunk: {e}")

            while chunk_start < duration:
                chunk_end = min(chunk_start + chunk_duration, duration)
                # Same helper the prefetcher keys on, so the lookup below
                # cannot drift from the bounds computed here.
                _, chunk_end_with_overlap = chunking._chunk_bounds_ahead(
                    chunk_start, chunk_duration, duration, overlap, 1,
                )[0]

                # Recalculate num_chunks with current chunk_duration (may have changed due to OOM)
                remaining_duration = duration - chunk_start
                remaining_chunks = max(1, int((remaining_duration - overlap) // (chunk_duration - overlap)) + 1)

                logger.info(
                    f"Processing chunk {chunk_num + 1} (~{remaining_chunks} remaining): "
                    f"{chunk_start/60:.1f}-{chunk_end_with_overlap/60:.1f} min "
                    f"(chunk_size={chunk_duration/60:.0f}min)"
                )

                # Take this chunk from the prefetcher; it queues the next
                # chunks so their ffmpeg passes (preprocess filter folded
                # in) run while the GPU transcribes this one.
                try:
                    chunk_path = prefetcher.take(
                        chunk_start, chunk_duration, duration, overlap)
                except AudioExtractionTimeout:
                    # Re-running the same call would time out again, so shrink the
                    # chunk once before giving up, mirroring the OOM path (#644).
                    if extract_timeout_retries >= max_extract_timeout_retries:
                        raise
                    extract_timeout_retries += 1
                    chunk_duration = max(CHUNK_MIN_DURATION_SECONDS, chunk_duration // 2)
                    logger.warning(
                        f"Chunk {chunk_num + 1} extraction timed out; retrying with "
                        f"chunk_size={chunk_duration/60:.0f}min"
                    )
                    continue
                if not chunk_path:
                    logger.error(f"Failed to extract chunk {chunk_num + 1}")
                    # Name local extraction as the cause (#556): the generic
                    # transcription-failure message points at the wrong layer.
                    raise AudioExtractionError(
                        'Audio chunk extraction failed (ffmpeg could not decode '
                        'the source file); transcription never started')

                try:
                    # Transcribe chunk (will handle its own batch sizing and retries)
                    chunk_segments = self.transcribe(
                        chunk_path,
                        language_override=language_override, preprocessed=True,
                        whisper_settings=whisper_settings,
                    )

                    if chunk_segments is None:
                        error = (self.last_transcription_stats or {}).get('error', '')
                        if 'cuda' in error.lower() or 'out of memory' in error.lower():
                            raise RuntimeError(
                                f"Local CUDA transcription failed: {error}")
                        failed_chunks.append((chunk_start, chunk_end_with_overlap))
                        logger.error(
                            f"Chunk {chunk_num + 1} transcription failed "
                            f"({chunk_start/60:.1f}-{chunk_end_with_overlap/60:.1f} min); "
                            f"leaving transcript gap and continuing"
                        )
                        if len(failed_chunks) > max_failed_chunks:
                            logger.error(
                                f"Too many failed chunks ({len(failed_chunks)} > {max_failed_chunks}); "
                                f"aborting transcription"
                            )
                            return None
                        chunk_num += 1
                        chunk_start = chunk_end
                        continue

                    # Reset OOM retry count on success
                    oom_retry_count = 0

                    # Adjust timestamps to be relative to full audio
                    for seg in chunk_segments:
                        seg['start'] += chunk_start
                        seg['end'] += chunk_start
                        # Adjust word timestamps too if present
                        if seg.get('words'):
                            for word in seg['words']:
                                word['start'] += chunk_start
                                word['end'] += chunk_start

                    # Merge with existing segments, handling overlap deduplication
                    if not all_segments:
                        all_segments = chunk_segments
                    else:
                        all_segments = chunking.merge_overlapping_segments(
                            all_segments, chunk_segments, chunk_start, overlap
                        )

                    # Increment chunk counter only after successful processing
                    chunk_num += 1

                    logger.info(
                        f"Chunk {chunk_num} complete: {len(chunk_segments)} segments "
                        f"(total: {len(all_segments)})"
                    )

                    # Move to next chunk
                    chunk_start = chunk_end

                except Exception as e:
                    error_str = str(e).lower()
                    is_oom = 'out of memory' in error_str or 'oom' in error_str or 'cuda' in error_str

                    if is_oom and oom_retry_count < max_oom_retries:
                        oom_retry_count += 1
                        old_chunk_duration = chunk_duration
                        chunk_duration = max(CHUNK_MIN_DURATION_SECONDS, chunk_duration // 2)

                        logger.warning(
                            f"OOM on chunk {chunk_num + 1} (attempt {oom_retry_count}/{max_oom_retries}). "
                            f"Reducing chunk size: {old_chunk_duration/60:.0f}min -> {chunk_duration/60:.0f}min"
                        )

                        # Clean up and retry this chunk with smaller size
                        clear_gpu_memory()
                        local._unload_local_models()
                        # Don't advance chunk_start - retry from same position
                        continue
                    # Non-OOM error or max retries reached
                    logger.error(f"Chunk {chunk_num + 1} failed: {e}")
                    raise

                finally:
                    # Clean up chunk file
                    common.unlink_quiet(chunk_path)

                    # Clear GPU cache between chunks (no reload cost)
                    clear_gpu_memory()
                    logger.debug("Cleared GPU memory after chunk processing")
        finally:
            prefetcher.close()

        # Final hallucination filtering on merged results
        original_count = len(all_segments)
        all_segments = segments.filter_hallucinations(all_segments)
        if len(all_segments) < original_count:
            logger.info(f"Filtered {original_count - len(all_segments)} hallucination segments after merge")

        duration_min = all_segments[-1]['end'] / 60 if all_segments else 0
        if failed_chunks:
            gap_summary = ", ".join(
                f"{s/60:.1f}-{e/60:.1f}min" for s, e in failed_chunks
            )
            logger.warning(
                f"Chunked transcription complete with {len(failed_chunks)} gap(s): "
                f"{gap_summary}. Partial transcript returned."
            )
        logger.info(
            f"Chunked transcription complete: {len(all_segments)} segments, "
            f"{duration_min:.1f} minutes from {chunk_num} chunks"
        )

        return all_segments

    def segments_to_text(self, segments: list[dict]) -> str:
        """Convert segments to readable text format."""
        lines = []
        for segment in segments:
            start_ts = format_vtt_timestamp(segment['start'])
            end_ts = format_vtt_timestamp(segment['end'])
            lines.append(f"[{start_ts} --> {end_ts}] {segment['text']}")
        return '\n'.join(lines)
