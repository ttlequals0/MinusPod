"""Audio chunk extraction, segment merging and chunk sizing helpers."""

import logging
import math
import tempfile
import os
import re
import subprocess
import utils.audio
from utils.errors import AudioExtractionTimeout
from utils.gpu import get_available_memory_gb
from utils.http import safe_url_for_log
import utils.subprocess_registry
from utils.ffmpeg_run import SAFE_MEDIA_INPUT_ARGS
from config import (
    CHUNK_MIN_DURATION_SECONDS,
    CHUNK_MAX_DURATION_SECONDS,
    CHUNK_DEFAULT_DURATION_SECONDS,
    MEMORY_SAFETY_MARGIN,
    WHISPER_MEMORY_PROFILES,
    WHISPER_DEFAULT_PROFILE,
    FFMPEG_LONG_TIMEOUT,
    FFMPEG_CHUNK_TIMEOUT,
)
from transcriber import common, settings, local

logger = logging.getLogger('transcriber')


# Whisper's encoder window. A clip handed to BatchedInferencePipeline longer
# than this is silently truncated to its first 30s.
WHISPER_CHUNK_SECONDS = 30.0

# Shortest clip worth handing to the pipeline.
MIN_CLIP_SECONDS = 0.1


def extract_audio_chunk(
    audio_path: str,
    start_time: float,
    end_time: float,
    preprocess: bool = False,
    flac: bool = False,
) -> str | None:
    """Extract a time range (seconds) from an audio file using ffmpeg.

    preprocess: apply the preprocess_audio filter chain in this pass; callers must then
        skip preprocessing downstream (transcribe(..., preprocessed=True)).
    flac: encode FLAC (ready for API upload) instead of PCM WAV.

    Returns the temporary chunk path (caller cleans it up), or None on failure.
    """
    tmp = tempfile.NamedTemporaryFile(suffix='.flac' if flac else '.wav', delete=False)
    output_path = tmp.name
    tmp.close()

    success = False
    try:
        duration = end_time - start_time
        cmd = [
            'ffmpeg', *SAFE_MEDIA_INPUT_ARGS, '-y',
            '-ss', str(start_time),
            '-i', audio_path,
            '-t', str(duration),
            # Ignore embedded cover art (#556): FLAC output can carry pictures, so a
            # malformed APIC frame (ID3 says PNG, bytes are JPEG) aborts the extract.
            '-vn',
            '-ar', '16000',  # Whisper native sample rate
            '-ac', '1',      # Mono
        ]
        if preprocess:
            cmd += ['-af', settings.PREPROCESS_AUDIO_FILTERS]
        # FLAC for upload; otherwise uncompressed for faster processing
        cmd += ['-c:a', 'flac' if flac else 'pcm_s16le', output_path]

        # With filtering folded in, use the standalone preprocess budget (FFMPEG_LONG_TIMEOUT
        # floor). Scale by chunk length (#644): chunks grow with GPU memory, so a flat budget fails.
        timeout = (FFMPEG_LONG_TIMEOUT if preprocess else FFMPEG_CHUNK_TIMEOUT) + int(duration / 12)
        result = utils.subprocess_registry.tracked_run(cmd, capture_output=True, timeout=timeout)

        if result.returncode == 0 and os.path.exists(output_path):
            logger.debug(f"Extracted chunk {start_time:.1f}s-{end_time:.1f}s to {output_path}")
            success = True
            return output_path

        logger.warning(
            f"Chunk extraction failed (returncode={result.returncode}): "
            f"{_ffmpeg_error_tail(result.stderr)}"
        )
        return None

    except subprocess.TimeoutExpired:
        logger.warning(
            f"Chunk extraction timed out after {timeout}s for "
            f"{start_time:.1f}s-{end_time:.1f}s ({duration / 60:.1f} min chunk)"
        )
        raise AudioExtractionTimeout(
            f'Audio chunk extraction timed out (ffmpeg exceeded {timeout}s '
            f'preparing a {duration / 60:.1f} min chunk)'
        ) from None
    except Exception as e:
        logger.warning(f"Chunk extraction error: {e}")
        return None
    finally:
        # Clean up temp file on failure (caller cleans up on success)
        if not success:
            common.unlink_quiet(output_path)


def merge_overlapping_segments(
    existing_segments: list[dict],
    new_segments: list[dict],
    chunk_start: float,
    overlap_duration: float
) -> list[dict]:
    """Merge new segments into existing, deduplicating the overlap_duration seconds transcribed in both chunks.

    Keeps previous-chunk segments up to the overlap zone, drops the new chunk's duplicates
    inside it, and appends the rest. new_segments timestamps are already adjusted to the full audio.
    """
    if not existing_segments:
        return new_segments

    if not new_segments:
        return existing_segments

    # The overlap zone is at the beginning of the new chunk
    overlap_end = chunk_start + overlap_duration

    result = existing_segments.copy()

    # Find the last segment end time from existing segments
    # to avoid adding duplicates (existing_segments is non-empty here;
    # the empty case returned early above)
    last_existing_end = max(seg['end'] for seg in existing_segments)

    for seg in new_segments:
        # Skip segments that are entirely in the overlap zone AND
        # have a corresponding segment in existing (based on end time)
        if seg['end'] <= overlap_end:
            # This segment is in the overlap zone
            # Check if there's already a segment covering this time
            overlap_covered = any(
                existing_seg['start'] <= seg['start'] and
                existing_seg['end'] >= seg['end'] - 1.0  # 1s tolerance
                for existing_seg in existing_segments
            )
            if overlap_covered:
                logger.debug("Skipping duplicate segment in overlap zone: %.1f-%.1f", seg['start'], seg['end'])
                continue

        # Skip segments that end before our last known position
        # (they're duplicates from the overlap)
        if seg['end'] <= last_existing_end:
            continue

        # For segments that span the overlap boundary, we keep them
        # since they extend beyond what we have
        result.append(seg)

    return result


def _chunk_bounds_ahead(start, chunk_duration, duration, overlap, count):
    """Next `count` (start, end_with_overlap) pairs of the chunked loop.

    Single owner of the boundary math: the loop and the prefetcher key on the same pairs.
    """
    bounds = []
    while start < duration and len(bounds) < count:
        end = min(start + chunk_duration, duration)
        end_with_overlap = min(end + overlap, duration) if end < duration else end
        bounds.append((start, end_with_overlap))
        start = end
    return bounds


def _unlink_future_chunk(future):
    """Done-callback for a discarded extraction: swallow its error, drop its file."""
    try:
        common.unlink_quiet(future.result())
    except Exception:
        pass


def _ffmpeg_error_tail(stderr, limit: int = 500) -> str:
    """The diagnosable part of ffmpeg stderr for logs.

    The head is all version banner and config (#556), so surface error-looking lines, else the tail.
    """
    if not stderr:
        return 'no error output'
    text = stderr.decode('utf-8', errors='replace')
    error_lines = [ln for ln in text.splitlines()
                   if re.search(r'error|invalid|fail|denied|no such',
                                ln, re.IGNORECASE)]
    picked = '\n'.join(error_lines[-5:]) if error_lines else text
    return picked[-limit:]


def _attach_top_level_words(segments, words) -> None:
    """Fold an OpenAI verbose_json top-level `words` array into segments.

    Servers return words top-level, not nested; each goes to the segment nearest its midpoint."""
    if not segments or not words:
        return
    if any(seg.get('words') for seg in segments):
        return
    for w in words:
        start, end = w.get('start'), w.get('end')
        if start is None or end is None:
            continue
        mid = (start + end) / 2
        target = next(
            (s for s in segments if s['start'] <= mid <= s['end']), None)
        if target is None:
            target = min(segments, key=lambda s: min(
                abs(s['start'] - mid), abs(s['end'] - mid)))
        target.setdefault('words', []).append(
            {'word': w.get('word', ''), 'start': start, 'end': end})
    # A server may return the array unsorted; refinement scans words in order.
    for s in segments:
        if s.get('words'):
            s['words'].sort(key=lambda x: x['start'])


def _warn_if_word_timestamps_missing(segments, whisper_settings) -> None:
    """Warn once when a transcription that asked for word timestamps got none.
    A 4xx on the word granularity is already reported; a 200 that simply omits the
    words silently disabled boundary refinement."""
    if not segments or any(seg.get('words') for seg in segments):
        return
    logger.warning(
        "Whisper API returned no word timestamps (provider=%s model=%s); "
        "boundary refinement will be skipped for this transcription",
        safe_url_for_log(whisper_settings.get('api_base_url') or ''),
        whisper_settings.get('api_model'),
    )


def calculate_optimal_chunk_duration(
    model_name: str,
    device: str = "cuda",
) -> tuple[int, str]:
    """Chunk duration for the model and available memory, as (chunk_duration_seconds, reasoning_message).

    Local backend only; the API backend branches to _transcribe_chunked_parallel_api first.
    device is "cuda" or "cpu".
    """
    # Get model memory profile
    profile = WHISPER_MEMORY_PROFILES.get(model_name, WHISPER_DEFAULT_PROFILE)
    base_memory_gb, memory_per_minute_gb = profile

    # Get available memory
    available_gb, memory_type = get_available_memory_gb(device)

    if available_gb is None:
        logger.warning("Could not determine available memory, using default chunk size")
        return CHUNK_DEFAULT_DURATION_SECONDS, "memory detection failed, using default"

    # A resident model's weights are already out of the free figure.
    if local.WhisperModelSingleton.is_loaded():
        base_memory_gb = 0.0

    # Apply safety margin
    usable_gb = available_gb * MEMORY_SAFETY_MARGIN

    # Calculate how much memory is available for audio processing
    # (total usable minus base model memory)
    available_for_audio_gb = usable_gb - base_memory_gb

    if available_for_audio_gb <= 0:
        # Not enough memory even for base model - use minimum chunk size
        logger.warning(
            f"Available memory ({available_gb:.1f}GB) barely covers model base "
            f"({base_memory_gb:.1f}GB), using minimum chunk size"
        )
        return CHUNK_MIN_DURATION_SECONDS, f"low memory ({available_gb:.1f}GB {memory_type})"

    # Calculate max duration that fits in available memory
    # available_for_audio = duration_minutes * memory_per_minute
    # duration_minutes = available_for_audio / memory_per_minute
    max_duration_minutes = available_for_audio_gb / memory_per_minute_gb
    max_duration_seconds = int(max_duration_minutes * 60)

    # Clamp to configured min/max
    chunk_duration = max(
        CHUNK_MIN_DURATION_SECONDS,
        min(max_duration_seconds, CHUNK_MAX_DURATION_SECONDS)
    )

    reason = (
        f"{available_gb:.1f}GB {memory_type} available, "
        f"model '{model_name}' ({base_memory_gb:.1f}GB base + {memory_per_minute_gb*1000:.0f}MB/min)"
    )

    logger.info(
        f"Calculated chunk duration: {chunk_duration/60:.0f} min "
        f"(max safe: {max_duration_minutes:.0f} min) - {reason}"
    )

    return chunk_duration, reason


# Within this many seconds a gap matches a recorded hole.
_HOLE_MATCH_S = 0.05


def _hole_recorded(hole, records) -> bool:
    """Whether a hole matches a recorded {start, end} within _HOLE_MATCH_S."""
    return any(abs(hole[0] - (rec.get('start') or 0.0)) <= _HOLE_MATCH_S
               and abs(hole[1] - (rec.get('end') or 0.0)) <= _HOLE_MATCH_S
               for rec in records if isinstance(rec, dict))


def _full_span_clips(duration: float | None) -> list[dict] | None:
    """Cover the whole file with 30s clips for a VAD-off transcription.

    BatchedInferencePipeline chunks from VAD timestamps and raises without clip_timestamps
    when VAD is off. None when the duration is unknown or too short.
    """
    if not duration or duration <= 0:
        return None
    clips = [
        {'start': i * WHISPER_CHUNK_SECONDS,
         'end': min((i + 1) * WHISPER_CHUNK_SECONDS, duration)}
        for i in range(math.ceil(duration / WHISPER_CHUNK_SECONDS))
    ]
    # A trailing clip under MIN_CLIP_SECONDS is dropped: too short for a word,
    # and a boundary-straddling sliver reaches the pipeline as an empty
    # feature array and raises.
    return [c for c in clips
            if c['end'] - c['start'] >= MIN_CLIP_SECONDS] or None
