"""Transcription using Faster Whisper."""
import logging
import tempfile
import os
import re
import subprocess
import hashlib
import requests
from typing import List, Dict, Optional, Tuple
from pathlib import Path

from utils.audio import get_audio_duration as _get_audio_duration
from utils.time import format_vtt_timestamp
from utils.gpu import clear_gpu_memory, get_available_memory_gb, get_gpu_memory_info
from config import (
    CHUNK_OVERLAP_SECONDS,
    CHUNK_MIN_DURATION_SECONDS,
    CHUNK_MAX_DURATION_SECONDS,
    CHUNK_DEFAULT_DURATION_SECONDS,
    MEMORY_SAFETY_MARGIN,
    WHISPER_MEMORY_PROFILES,
    WHISPER_DEFAULT_PROFILE,
    BROWSER_USER_AGENT, APP_USER_AGENT,
)

# Suppress ONNX Runtime warnings before importing faster_whisper
os.environ.setdefault('ORT_LOG_LEVEL', 'ERROR')

# Set cache directories to writable location (for running as non-root user)
# These must be set BEFORE importing faster_whisper/huggingface
cache_dir = os.environ.get('HF_HOME', '/app/data/.cache')
os.environ.setdefault('HF_HOME', cache_dir)
os.environ.setdefault('HUGGINGFACE_HUB_CACHE', os.path.join(cache_dir, 'hub'))
os.environ.setdefault('XDG_CACHE_HOME', cache_dir)

import ctranslate2
from faster_whisper import WhisperModel, BatchedInferencePipeline

logger = logging.getLogger(__name__)

# Maximum segment duration for precise ad detection
MAX_SEGMENT_DURATION = 15.0  # seconds

# Batch size tiers based on audio duration (in seconds)
# Longer episodes need smaller batches to avoid CUDA OOM
BATCH_SIZE_TIERS = [
    (60 * 60, 16),      # < 60 min: batch_size=16
    (90 * 60, 12),      # 60-90 min: batch_size=12
    (120 * 60, 8),      # 90-120 min: batch_size=8
    (float('inf'), 4),  # > 120 min: batch_size=4
]

# Podcast-aware initial prompt with sponsor vocabulary
AD_VOCABULARY = (
    "promo code, discount code, use code, "
    "sponsored by, brought to you by, "
    "Athletic Greens, AG1, BetterHelp, Squarespace, NordVPN, "
    "ExpressVPN, HelloFresh, Audible, Masterclass, ZipRecruiter, "
    "Raycon, Manscaped, Stamps.com, Indeed, LinkedIn, "
    "SimpliSafe, Casper, Helix Sleep, Brooklinen, Bombas, "
    "Calm, Headspace, Mint Mobile, Dollar Shave Club"
)

# Hallucination patterns to filter out (Whisper artifacts)
HALLUCINATION_PATTERNS = re.compile(
    r'^(thanks for watching|thank you for watching|please subscribe|'
    r'like and subscribe|see you next time|bye\.?|'
    r'\[music\]|\[applause\]|\[laughter\]|\[silence\]|'
    r'\.+|\s*|you)$',
    re.IGNORECASE
)

# Vocabulary hallucination patterns (Whisper sometimes outputs the initial prompt)
# These are partial matches - if any of these appear, the segment is likely a hallucination
VOCABULARY_HALLUCINATION_PATTERNS = re.compile(
    r'(promo code|discount code|use code|sponsored by|brought to you by|'
    r'Athletic Greens|AG1|BetterHelp|Squarespace|NordVPN|ExpressVPN|'
    r'HelloFresh|Audible|Masterclass|ZipRecruiter|Raycon|Manscaped|'
    r'Stamps\.com|Indeed|LinkedIn|SimpliSafe|Casper|Helix Sleep|'
    r'Brooklinen|Bombas|Calm|Headspace|Mint Mobile|Dollar Shave Club)',
    re.IGNORECASE
)


def split_long_segments(segments: List[Dict]) -> List[Dict]:
    """Split segments longer than MAX_SEGMENT_DURATION using word timestamps.

    This improves ad detection precision by giving Claude finer-grained
    timestamp boundaries to work with.
    """
    result = []
    for segment in segments:
        duration = segment['end'] - segment['start']
        if duration <= MAX_SEGMENT_DURATION:
            result.append(segment)
            continue

        # If we have word-level timestamps, split on word boundaries
        words = segment.get('words', [])
        if words:
            current_chunk = {'start': segment['start'], 'text': ''}
            for word in words:
                # Get word text - handle both dict and object formats
                word_text = word.get('word', '') if isinstance(word, dict) else getattr(word, 'word', '')
                word_end = word.get('end', segment['end']) if isinstance(word, dict) else getattr(word, 'end', segment['end'])

                current_chunk['text'] += word_text

                # Check if chunk duration exceeds target
                chunk_duration = word_end - current_chunk['start']
                if chunk_duration >= MAX_SEGMENT_DURATION:
                    current_chunk['end'] = word_end
                    result.append({
                        'start': current_chunk['start'],
                        'end': current_chunk['end'],
                        'text': current_chunk['text'].strip()
                    })
                    current_chunk = {'start': word_end, 'text': ''}

            # Add remaining words as final chunk
            if current_chunk['text'].strip():
                current_chunk['end'] = segment['end']
                result.append({
                    'start': current_chunk['start'],
                    'end': current_chunk['end'],
                    'text': current_chunk['text'].strip()
                })
        else:
            # No word timestamps - keep as is
            result.append(segment)

    return result


def extract_audio_chunk(audio_path: str, start_time: float, end_time: float) -> Optional[str]:
    """Extract a time range from an audio file using ffmpeg.

    Args:
        audio_path: Path to source audio file
        start_time: Start time in seconds
        end_time: End time in seconds

    Returns:
        Path to temporary chunk file, or None on failure.
        Caller is responsible for cleaning up the temp file.
    """
    output_path = tempfile.mktemp(suffix='.wav')

    try:
        duration = end_time - start_time
        cmd = [
            'ffmpeg', '-y',
            '-ss', str(start_time),
            '-i', audio_path,
            '-t', str(duration),
            '-ar', '16000',  # Whisper native sample rate
            '-ac', '1',      # Mono
            '-c:a', 'pcm_s16le',  # Uncompressed for faster processing
            output_path
        ]

        result = subprocess.run(
            cmd,
            capture_output=True,
            timeout=120  # 2 minutes should be enough for any chunk
        )

        if result.returncode == 0 and os.path.exists(output_path):
            logger.debug(f"Extracted chunk {start_time:.1f}s-{end_time:.1f}s to {output_path}")
            return output_path

        logger.warning(
            f"Chunk extraction failed (returncode={result.returncode}): "
            f"{result.stderr.decode('utf-8', errors='replace')[:200] if result.stderr else 'no error'}"
        )
        return None

    except subprocess.TimeoutExpired:
        logger.warning(f"Chunk extraction timed out for {start_time:.1f}s-{end_time:.1f}s")
        return None
    except Exception as e:
        logger.warning(f"Chunk extraction error: {e}")
        return None
    finally:
        # Clean up on failure
        if os.path.exists(output_path) and os.path.getsize(output_path) == 0:
            try:
                os.unlink(output_path)
            except OSError:
                pass


def merge_overlapping_segments(
    existing_segments: List[Dict],
    new_segments: List[Dict],
    chunk_start: float,
    overlap_duration: float
) -> List[Dict]:
    """Merge new segments into existing, handling overlap deduplication.

    At chunk boundaries, we have overlap_duration seconds of audio that was
    transcribed in both chunks. We need to:
    1. Keep segments from the previous chunk up to the overlap zone
    2. Discard duplicate segments in the overlap zone from the new chunk
    3. Add remaining segments from the new chunk

    Args:
        existing_segments: Segments from previous chunks
        new_segments: Segments from current chunk (timestamps already adjusted)
        chunk_start: Start time of the current chunk in the full audio
        overlap_duration: Duration of overlap in seconds

    Returns:
        Merged list of segments with duplicates removed
    """
    if not existing_segments:
        return new_segments

    if not new_segments:
        return existing_segments

    # The overlap zone is at the beginning of the new chunk
    overlap_start = chunk_start
    overlap_end = chunk_start + overlap_duration

    result = existing_segments.copy()

    # Find the last segment end time from existing segments
    # to avoid adding duplicates
    last_existing_end = max(seg['end'] for seg in existing_segments) if existing_segments else 0

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
                logger.debug(f"Skipping duplicate segment in overlap zone: {seg['start']:.1f}-{seg['end']:.1f}")
                continue

        # Skip segments that end before our last known position
        # (they're duplicates from the overlap)
        if seg['end'] <= last_existing_end:
            continue

        # For segments that span the overlap boundary, we keep them
        # since they extend beyond what we have
        result.append(seg)

    return result


def calculate_optimal_chunk_duration(
    model_name: str,
    device: str = "cuda"
) -> Tuple[int, str]:
    """Calculate optimal chunk duration based on available memory and model size.

    Uses model-specific memory profiles and current available memory to
    determine how much audio can be safely processed in one chunk.

    Args:
        model_name: Whisper model name (e.g., "small", "large-v3")
        device: "cuda" or "cpu"

    Returns:
        Tuple of (chunk_duration_seconds, reasoning_message)
    """
    # Get model memory profile
    profile = WHISPER_MEMORY_PROFILES.get(model_name, WHISPER_DEFAULT_PROFILE)
    base_memory_gb, memory_per_minute_gb = profile

    # Get available memory
    available_gb, memory_type = get_available_memory_gb(device)

    if available_gb is None:
        logger.warning("Could not determine available memory, using default chunk size")
        return CHUNK_DEFAULT_DURATION_SECONDS, "memory detection failed, using default"

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


class WhisperModelSingleton:
    _instance = None
    _base_model = None
    _current_model_name = None
    _needs_reload = False

    @classmethod
    def get_configured_model(cls) -> str:
        """Get the configured model from database settings."""
        try:
            from database import Database
            db = Database()
            model = db.get_setting('whisper_model')
            if model:
                return model
        except Exception as e:
            logger.warning(f"Could not read whisper_model from database: {e}")
        # Fall back to env var or default
        return os.getenv("WHISPER_MODEL", "small")

    @classmethod
    def mark_for_reload(cls):
        """Mark the model for reload on next use."""
        cls._needs_reload = True
        logger.info("Whisper model marked for reload")

    @classmethod
    def _should_reload(cls) -> bool:
        """Check if model needs to be reloaded."""
        if cls._needs_reload:
            return True
        configured = cls.get_configured_model()
        if cls._current_model_name and cls._current_model_name != configured:
            logger.info(f"Model changed from {cls._current_model_name} to {configured}")
            return True
        return False

    @classmethod
    def unload_model(cls):
        """Unload the current model and free GPU memory.

        Call this after transcription is complete to free ~5-6GB memory
        before memory-intensive operations like speaker diarization.
        The model will lazy-reload on the next transcription request.
        """
        if cls._instance is not None or cls._base_model is not None:
            logger.info(f"Unloading Whisper model: {cls._current_model_name}")
            cls._instance = None
            cls._base_model = None
            cls._current_model_name = None
            cls._needs_reload = False

            # Force garbage collection and clear CUDA cache
            clear_gpu_memory()
            logger.info("CUDA cache cleared")

    @classmethod
    def get_instance(cls) -> Tuple[WhisperModel, BatchedInferencePipeline]:
        """
        Get both the base model and batched pipeline instance.
        Will reload if the configured model has changed.
        Returns:
            Tuple[WhisperModel, BatchedInferencePipeline]: Base model for operations like language detection,
                                                          and batched pipeline for transcription
        """
        # Check if we need to reload
        if cls._instance is not None and cls._should_reload():
            cls.unload_model()

        if cls._instance is None:
            model_size = cls.get_configured_model()
            device = os.getenv("WHISPER_DEVICE", "cpu")

            # Check CUDA availability and set compute type
            if device == "cuda":
                cuda_device_count = ctranslate2.get_cuda_device_count()
                if cuda_device_count > 0:
                    logger.info(f"CUDA available: {cuda_device_count} device(s) detected")
                    compute_type = "float16"  # Use FP16 for GPU
                    logger.info(f"Initializing Whisper model: {model_size} on CUDA with float16")
                else:
                    logger.warning("CUDA requested but not available, falling back to CPU")
                    device = "cpu"
                    compute_type = "int8"
                    logger.info(f"Initializing Whisper model: {model_size} on CPU with int8")
            else:
                compute_type = "int8"  # Use INT8 for CPU
                logger.info(f"Initializing Whisper model: {model_size} on CPU with int8")

            # Initialize base model
            cls._base_model = WhisperModel(
                model_size,
                device=device,
                compute_type=compute_type,
            )

            # Initialize batched pipeline
            cls._instance = BatchedInferencePipeline(
                cls._base_model
            )
            cls._current_model_name = model_size
            cls._needs_reload = False
            logger.info(f"Whisper model '{model_size}' and batched pipeline initialized")

            # Log actual GPU memory usage after model load
            mem_info = get_gpu_memory_info()
            if mem_info:
                allocated_gb = mem_info.get('allocated', 0) / (1024 ** 3)
                reserved_gb = mem_info.get('cached', 0) / (1024 ** 3)
                logger.info(f"GPU memory after model load: {allocated_gb:.2f}GB allocated, {reserved_gb:.2f}GB reserved")

        return cls._base_model, cls._instance

    @classmethod
    def get_base_model(cls) -> WhisperModel:
        """
        Get just the base model for operations like language detection
        Returns:
            WhisperModel: Base Whisper model
        """
        if cls._base_model is None or cls._should_reload():
            cls.get_instance()
        return cls._base_model

    @classmethod
    def get_batched_pipeline(cls) -> BatchedInferencePipeline:
        """
        Get just the batched pipeline for transcription
        Returns:
            BatchedInferencePipeline: Batched pipeline for efficient transcription
        """
        if cls._instance is None or cls._should_reload():
            cls.get_instance()
        return cls._instance

    @classmethod
    def get_current_model_name(cls) -> Optional[str]:
        """Get the name of the currently loaded model."""
        return cls._current_model_name

class Transcriber:
    def __init__(self):
        # Model is now managed by singleton
        pass

    def get_initial_prompt(self, podcast_name: str = None) -> str:
        """Generate a podcast-aware initial prompt for Whisper."""
        if podcast_name:
            return f"Podcast: {podcast_name}. {AD_VOCABULARY}"
        return f"This is a podcast episode. {AD_VOCABULARY}"

    def filter_hallucinations(self, segments: List[Dict]) -> List[Dict]:
        """Filter out common Whisper hallucinations and artifacts."""
        filtered = []
        for seg in segments:
            text = seg.get('text', '').strip()
            if not text:
                continue
            if HALLUCINATION_PATTERNS.match(text):
                logger.debug(f"Filtered hallucination: {text}")
                continue
            # Filter vocabulary hallucinations (Whisper outputs the initial prompt)
            # Only filter short segments that are primarily vocabulary words
            if len(text) < 100 and VOCABULARY_HALLUCINATION_PATTERNS.search(text):
                # Check if the text is mostly vocabulary (not real speech with sponsor mention)
                # Real ad reads are typically longer and have more context
                word_count = len(text.split())
                if word_count < 15:
                    logger.debug(f"Filtered vocabulary hallucination: {text}")
                    continue
            # Skip repeated segments (Whisper loop artifacts)
            if filtered and text == filtered[-1].get('text', '').strip():
                logger.debug(f"Filtered repeated segment: {text}")
                continue
            filtered.append(seg)
        return filtered

    def _detect_non_english_segment(self, text: str, primary_language: str) -> bool:
        """Detect if a segment is likely non-English (potential DAI ad).

        Uses multiple heuristics:
        1. High ratio of non-ASCII characters (Spanish, etc.)
        2. Common Spanish/other language patterns
        3. If primary detected language is not English and segment has markers

        Args:
            text: The segment text
            primary_language: The overall detected language from Whisper

        Returns:
            True if segment appears to be non-English
        """
        if not text or len(text) < 10:
            return False

        # Check for high ratio of accented/non-ASCII characters
        non_ascii_chars = sum(1 for c in text if ord(c) > 127)
        non_ascii_ratio = non_ascii_chars / len(text)

        # Spanish and other language indicators
        spanish_patterns = [
            'usted', 'puede', 'para', 'como', 'ahora', 'llame', 'gratis',
            'oferta', 'hoy', 'desde', 'hasta', 'numero', 'telefono',
            'visite', 'compre', 'ahorre', 'descuento', 'promocion'
        ]

        text_lower = text.lower()

        # Check for Spanish ad patterns
        spanish_word_count = sum(1 for word in spanish_patterns if word in text_lower)

        # Heuristics for non-English detection
        is_likely_foreign = (
            # High non-ASCII ratio suggests accented language
            non_ascii_ratio > 0.05 or
            # Multiple Spanish words detected
            spanish_word_count >= 2 or
            # Primary language is not English and segment has some markers
            (primary_language not in ['en', 'english', 'unknown'] and
             (non_ascii_ratio > 0.02 or spanish_word_count >= 1))
        )

        if is_likely_foreign:
            logger.debug(f"Non-English segment detected: non_ascii={non_ascii_ratio:.2f}, "
                        f"spanish_words={spanish_word_count}, primary_lang={primary_language}")

        return is_likely_foreign

    def get_audio_duration(self, audio_path: str) -> Optional[float]:
        """Get audio duration in seconds using ffprobe.

        Delegates to utils.audio.get_audio_duration for consistent implementation.
        """
        duration = _get_audio_duration(audio_path)
        if duration is not None:
            logger.info(f"Audio duration: {duration:.1f}s ({duration/60:.1f} min)")
        return duration

    def get_batch_size_for_duration(self, duration_seconds: Optional[float]) -> int:
        """Get optimal batch size based on audio duration to prevent CUDA OOM."""
        if duration_seconds is None:
            # Default to conservative batch size if duration unknown
            return 8

        for threshold, batch_size in BATCH_SIZE_TIERS:
            if duration_seconds < threshold:
                return batch_size

        return 4  # Fallback for very long episodes

    def clear_cuda_cache(self):
        """Clear CUDA cache to free GPU memory.

        Delegates to utils.gpu.clear_gpu_memory().
        """
        clear_gpu_memory()
        logger.info("CUDA cache cleared")

    def preprocess_audio(self, input_path: str) -> Optional[str]:
        """
        Normalize audio for consistent transcription.
        Returns path to preprocessed file, or None if preprocessing fails.
        Caller is responsible for cleaning up the returned temp file.
        """
        output_path = tempfile.mktemp(suffix='.wav')
        success = False

        try:
            cmd = [
                'ffmpeg', '-y', '-i', input_path,
                '-ar', '16000',  # 16kHz (Whisper native sample rate)
                '-ac', '1',      # Mono
                '-af', 'loudnorm=I=-16:LRA=11:TP=-1.5,highpass=f=80,lowpass=f=8000',
                output_path
            ]
            result = subprocess.run(cmd, capture_output=True, timeout=300)
            if result.returncode == 0:
                logger.info(f"Audio preprocessed: {input_path} -> {output_path}")
                success = True
                return output_path
            logger.warning(
                f"Audio preprocessing failed (returncode={result.returncode}), "
                f"stderr: {result.stderr.decode('utf-8', errors='replace')[:200] if result.stderr else 'none'}"
            )
            return None
        except subprocess.TimeoutExpired:
            logger.warning("Audio preprocessing timed out, using original")
            return None
        except Exception as e:
            logger.warning(f"Audio preprocessing error: {e}, using original")
            return None
        finally:
            # Clean up temp file on any failure path
            if not success and os.path.exists(output_path):
                try:
                    os.unlink(output_path)
                except OSError:
                    pass

    def check_audio_availability(self, url: str, timeout: int = 10) -> tuple:
        """Check if audio URL is accessible without downloading.

        Performs a HEAD request to verify the CDN has the file ready.
        Use this before downloading to avoid failures on newly published episodes
        where the CDN hasn't propagated the file yet.

        Args:
            url: Audio file URL to check
            timeout: Request timeout in seconds

        Returns:
            Tuple of (available: bool, error_message: str or None)
        """
        try:
            headers = {
                'User-Agent': BROWSER_USER_AGENT,
            }
            response = requests.head(url, headers=headers, timeout=timeout, allow_redirects=True)

            if response.status_code == 200:
                return True, None
            elif response.status_code in (404, 403):
                return False, f"CDN not ready ({response.status_code})"
            elif response.status_code >= 500:
                return False, f"CDN server error ({response.status_code})"
            else:
                # Other 2xx/3xx - proceed with download
                return True, None
        except requests.exceptions.Timeout:
            return False, "CDN timeout"
        except requests.RequestException as e:
            return False, f"CDN check failed: {e}"

    def download_audio(self, url: str, timeout: tuple = (10, 300)) -> Optional[str]:
        """Download audio file from URL.

        Args:
            url: Audio file URL
            timeout: (connect_timeout, read_timeout) in seconds
        """
        try:
            logger.info(f"Downloading audio from: {url}")
            headers = {
                'User-Agent': BROWSER_USER_AGENT,
                'Accept': '*/*',
                'Accept-Language': 'en-US,en;q=0.9',
            }
            response = requests.get(url, headers=headers, stream=True, timeout=timeout)
            response.raise_for_status()

            # Check file size
            content_length = response.headers.get('Content-Length')
            if content_length:
                size_mb = int(content_length) / (1024 * 1024)
                if size_mb > 500:
                    logger.error(f"Audio file too large: {size_mb:.1f}MB (max 500MB)")
                    return None
                logger.info(f"Audio file size: {size_mb:.1f}MB")

            # Save to temp file
            with tempfile.NamedTemporaryFile(delete=False, suffix='.mp3') as tmp:
                for chunk in response.iter_content(chunk_size=8192):
                    tmp.write(chunk)
                temp_path = tmp.name

            logger.info(f"Downloaded audio to: {temp_path}")
            return temp_path
        except Exception as e:
            logger.error(f"Failed to download audio: {e}")
            return None

    def download_audio_with_resume(self, url: str, timeout: int = 600) -> Optional[str]:
        """Download audio file with resume support for interrupted downloads.

        Uses consistent temp file path based on URL hash so interrupted downloads
        can be resumed. Supports HTTP Range requests for partial content retrieval.

        Args:
            url: Audio file URL
            timeout: Read timeout in seconds (default 10 minutes)

        Returns:
            Path to downloaded file, or None on failure
        """
        # Generate consistent temp path based on URL hash
        url_hash = hashlib.md5(url.encode()).hexdigest()[:16]
        temp_path = os.path.join(tempfile.gettempdir(), f'podcast_dl_{url_hash}.mp3')

        downloaded = 0
        if os.path.exists(temp_path):
            downloaded = os.path.getsize(temp_path)
            logger.info(f"Resuming download from {downloaded} bytes: {url}")

        headers = {
            'User-Agent': f'Mozilla/5.0 (compatible; {APP_USER_AGENT})',
            'Accept': '*/*',
        }
        if downloaded > 0:
            headers['Range'] = f'bytes={downloaded}-'

        try:
            response = requests.get(url, headers=headers, stream=True, timeout=(10, timeout))

            # Check if server supports range requests
            if downloaded > 0 and response.status_code == 200:
                # Server doesn't support resume (returned full file), start fresh
                logger.info("Server doesn't support resume, starting fresh download")
                downloaded = 0
                try:
                    os.remove(temp_path)
                except OSError:
                    pass

            response.raise_for_status()

            # Validate file size
            content_length = response.headers.get('Content-Length')
            if content_length:
                total_size = int(content_length) + downloaded
                size_mb = total_size / (1024 * 1024)
                if size_mb > 500:
                    logger.error(f"Audio file too large: {size_mb:.1f}MB (max 500MB)")
                    return None
                logger.info(f"Audio file size: {size_mb:.1f}MB")

            # Download with resume support
            mode = 'ab' if downloaded > 0 else 'wb'
            with open(temp_path, mode) as f:
                for chunk in response.iter_content(chunk_size=8192):
                    f.write(chunk)

            logger.info(f"Downloaded audio to: {temp_path}")
            return temp_path

        except Exception as e:
            logger.error(f"Download failed (partial file kept for resume): {e}")
            # Keep partial file for resume on next attempt
            return None

    def transcribe(self, audio_path: str, podcast_name: str = None) -> List[Dict]:
        """Transcribe audio file using Faster Whisper with batched pipeline.

        Uses adaptive batch sizing based on audio duration to prevent CUDA OOM errors.
        Automatically retries with smaller batch size on OOM.
        """
        preprocessed_path = None
        try:
            # Get audio duration for adaptive batch sizing
            audio_duration = self.get_audio_duration(audio_path)

            # Get the batched pipeline for efficient transcription
            model = WhisperModelSingleton.get_batched_pipeline()
            current_model = WhisperModelSingleton.get_current_model_name()

            logger.info(f"Starting transcription of: {audio_path} (model: {current_model})")

            # Preprocess audio for consistent quality
            preprocessed_path = self.preprocess_audio(audio_path)
            transcribe_path = preprocessed_path if preprocessed_path else audio_path

            # Create podcast-aware prompt with sponsor vocabulary
            initial_prompt = self.get_initial_prompt(podcast_name)

            # Adjust batch size based on device and audio duration
            device = os.getenv("WHISPER_DEVICE", "cpu")
            if device == "cuda":
                # Use adaptive batch size based on duration to prevent OOM
                batch_size = self.get_batch_size_for_duration(audio_duration)
                duration_str = f"{audio_duration/60:.1f} min" if audio_duration else "unknown"
                logger.info(f"Using adaptive batch size: {batch_size} (duration: {duration_str})")
            else:
                batch_size = 8  # Smaller batch for CPU

            # Retry logic for CUDA OOM errors
            max_retries = 3
            retry_count = 0

            while retry_count < max_retries:
                try:
                    # Clear CUDA cache before each attempt
                    if device == "cuda":
                        self.clear_cuda_cache()

                    # Use the batched pipeline for transcription
                    # word_timestamps=True enables precise boundary refinement later
                    # language=None enables auto-detection to catch non-English DAI ads
                    segments_generator, info = model.transcribe(
                        transcribe_path,
                        language=None,  # Auto-detect to catch non-English ads (Spanish, etc.)
                        initial_prompt=initial_prompt,
                        beam_size=5,
                        batch_size=batch_size,
                        word_timestamps=True,  # Enable word-level timestamps for boundary refinement
                        vad_filter=True,  # Enable VAD filter to skip silent parts
                        vad_parameters=dict(
                            min_silence_duration_ms=1000,  # Increased from 500 - less aggressive skipping
                            speech_pad_ms=600,  # Increased from 400 - more padding for ad segments
                            threshold=0.3  # Lower threshold = more sensitive to speech in ads
                        )
                    )

                    # Log detected language
                    detected_lang = info.language if hasattr(info, 'language') else 'unknown'
                    lang_prob = info.language_probability if hasattr(info, 'language_probability') else 0
                    logger.info(f"Detected primary language: {detected_lang} (probability: {lang_prob:.2f})")

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

                        # Detect non-English segments (potential DAI ads)
                        # Whisper segments don't have per-segment language, but we can
                        # detect non-English by checking for non-ASCII characters or
                        # using the overall detected language with segment analysis
                        segment_text = segment.text.strip()
                        is_foreign = self._detect_non_english_segment(segment_text, detected_lang)

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
                            logger.info(f"[{self.format_timestamp(segment.start)}] {text_preview}")

                    # Filter out hallucinations
                    original_count = len(result)
                    result = self.filter_hallucinations(result)
                    if len(result) < original_count:
                        logger.info(f"Filtered {original_count - len(result)} hallucination segments")

                    # Log non-English segments (potential DAI ads)
                    if non_english_count > 0:
                        logger.info(f"Flagged {non_english_count} non-English segments as potential ads")

                    duration_min = result[-1]['end'] / 60 if result else 0
                    logger.info(f"Transcription completed: {len(result)} segments, {duration_min:.1f} minutes")

                    return result

                except Exception as inner_e:
                    error_str = str(inner_e).lower()
                    is_oom = 'out of memory' in error_str or 'cuda' in error_str

                    if is_oom and retry_count < max_retries - 1:
                        retry_count += 1
                        # Reduce batch size for retry
                        old_batch_size = batch_size
                        batch_size = max(1, batch_size // 2)
                        logger.warning(
                            f"CUDA OOM detected (attempt {retry_count}/{max_retries}). "
                            f"Reducing batch size: {old_batch_size} -> {batch_size}"
                        )
                        # Clear cache and retry
                        self.clear_cuda_cache()
                        continue
                    else:
                        # Non-OOM error or max retries reached
                        raise

        except Exception as e:
            logger.error(f"Transcription failed: {e}")
            # Clean up GPU memory on ANY failure to prevent memory leaks
            # This is critical for OOM recovery - free memory before retry
            try:
                clear_gpu_memory()
                WhisperModelSingleton.unload_model()
                logger.info("Cleaned up GPU memory after transcription failure")
            except Exception as cleanup_err:
                logger.warning(f"Failed to clean up GPU memory: {cleanup_err}")
            return None
        finally:
            # Clean up preprocessed file
            if preprocessed_path and os.path.exists(preprocessed_path):
                try:
                    os.unlink(preprocessed_path)
                    logger.debug(f"Cleaned up preprocessed file: {preprocessed_path}")
                except OSError:
                    pass

    def transcribe_chunked(self, audio_path: str, podcast_name: str = None) -> List[Dict]:
        """Transcribe audio files with dynamic chunking to prevent OOM errors.

        This method:
        1. Checks available memory and model size to calculate optimal chunk duration
        2. Processes audio in appropriately-sized chunks
        3. Catches OOM errors and retries with smaller chunks
        4. Clears GPU memory between chunks to limit peak usage

        Args:
            audio_path: Path to the audio file to transcribe
            podcast_name: Optional podcast name for context-aware prompting

        Returns:
            List of transcript segments with timestamps, or None on failure
        """
        # Get audio duration
        duration = self.get_audio_duration(audio_path)
        if duration is None:
            logger.error("Cannot determine audio duration for chunked transcription")
            return None

        # Get current model and device for memory calculation
        model_name = WhisperModelSingleton.get_configured_model()
        device = os.getenv("WHISPER_DEVICE", "cpu")

        # Calculate optimal chunk duration based on available memory
        chunk_duration, memory_reason = calculate_optimal_chunk_duration(model_name, device)
        overlap = CHUNK_OVERLAP_SECONDS

        # If calculated chunk can handle the entire audio, try regular transcription first
        if duration <= chunk_duration:
            logger.info(
                f"Audio duration {duration/60:.1f}min fits in calculated chunk "
                f"({chunk_duration/60:.0f}min), trying regular transcription"
            )
            try:
                result = self.transcribe(audio_path, podcast_name)
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
                    WhisperModelSingleton.unload_model()
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

        while chunk_start < duration:
            # Calculate chunk end with overlap for next chunk
            chunk_end = min(chunk_start + chunk_duration, duration)

            # For all but the last chunk, add overlap
            if chunk_end < duration:
                chunk_end_with_overlap = min(chunk_end + overlap, duration)
            else:
                chunk_end_with_overlap = chunk_end

            # Recalculate num_chunks with current chunk_duration (may have changed due to OOM)
            remaining_duration = duration - chunk_start
            remaining_chunks = max(1, int((remaining_duration - overlap) // (chunk_duration - overlap)) + 1)

            logger.info(
                f"Processing chunk {chunk_num + 1} (~{remaining_chunks} remaining): "
                f"{chunk_start/60:.1f}-{chunk_end_with_overlap/60:.1f} min "
                f"(chunk_size={chunk_duration/60:.0f}min)"
            )

            # Extract chunk using ffmpeg
            chunk_path = extract_audio_chunk(audio_path, chunk_start, chunk_end_with_overlap)
            if not chunk_path:
                logger.error(f"Failed to extract chunk {chunk_num + 1}")
                return None

            try:
                # Transcribe chunk (will handle its own batch sizing and retries)
                chunk_segments = self.transcribe(chunk_path, podcast_name)

                if chunk_segments is None:
                    logger.error(f"Chunk {chunk_num + 1} transcription failed")
                    return None

                # Reset OOM retry count on success
                oom_retry_count = 0

                # Adjust timestamps to be relative to full audio
                for seg in chunk_segments:
                    seg['start'] += chunk_start
                    seg['end'] += chunk_start
                    # Adjust word timestamps too if present
                    if 'words' in seg and seg['words']:
                        for word in seg['words']:
                            word['start'] += chunk_start
                            word['end'] += chunk_start

                # Merge with existing segments, handling overlap deduplication
                if not all_segments:
                    all_segments = chunk_segments
                else:
                    all_segments = merge_overlapping_segments(
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
                    WhisperModelSingleton.unload_model()
                    # Don't advance chunk_start - retry from same position
                    continue
                else:
                    # Non-OOM error or max retries reached
                    logger.error(f"Chunk {chunk_num + 1} failed: {e}")
                    raise

            finally:
                # Clean up chunk file
                if chunk_path and os.path.exists(chunk_path):
                    try:
                        os.unlink(chunk_path)
                    except OSError:
                        pass

                # Unload model and clear GPU memory between chunks
                # This is critical for keeping peak memory bounded
                clear_gpu_memory()
                WhisperModelSingleton.unload_model()
                logger.debug("Cleared GPU memory after chunk processing")

        # Final hallucination filtering on merged results
        original_count = len(all_segments)
        all_segments = self.filter_hallucinations(all_segments)
        if len(all_segments) < original_count:
            logger.info(f"Filtered {original_count - len(all_segments)} hallucination segments after merge")

        duration_min = all_segments[-1]['end'] / 60 if all_segments else 0
        logger.info(
            f"Chunked transcription complete: {len(all_segments)} segments, "
            f"{duration_min:.1f} minutes from {chunk_num} chunks"
        )

        return all_segments

    def format_timestamp(self, seconds: float) -> str:
        """Convert seconds to VTT timestamp format (HH:MM:SS.mmm)."""
        return format_vtt_timestamp(seconds)

    def segments_to_text(self, segments: List[Dict]) -> str:
        """Convert segments to readable text format."""
        lines = []
        for segment in segments:
            start_ts = format_vtt_timestamp(segment['start'])
            end_ts = format_vtt_timestamp(segment['end'])
            lines.append(f"[{start_ts} --> {end_ts}] {segment['text']}")
        return '\n'.join(lines)

    def process_episode(self, episode_url: str) -> Optional[Dict]:
        """Complete transcription pipeline for an episode."""
        audio_path = None
        try:
            # Download audio
            audio_path = self.download_audio(episode_url)
            if not audio_path:
                return None

            # Transcribe
            segments = self.transcribe(audio_path)
            if not segments:
                return None

            # Format transcript
            transcript_text = self.segments_to_text(segments)

            return {
                "segments": segments,
                "transcript": transcript_text,
                "segment_count": len(segments),
                "duration": segments[-1]['end'] if segments else 0
            }
        finally:
            # Clean up temp file
            if audio_path and os.path.exists(audio_path):
                try:
                    os.unlink(audio_path)
                    logger.info(f"Cleaned up temp file: {audio_path}")
                except OSError:
                    pass