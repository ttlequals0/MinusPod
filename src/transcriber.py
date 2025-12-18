"""Transcription using Faster Whisper."""
import logging
import tempfile
import os
import re
import gc
import subprocess
import requests
from typing import List, Dict, Optional, Tuple
from pathlib import Path

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
    def _unload_model(cls):
        """Unload the current model and free GPU memory."""
        if cls._instance is not None or cls._base_model is not None:
            logger.info(f"Unloading Whisper model: {cls._current_model_name}")
            cls._instance = None
            cls._base_model = None
            cls._current_model_name = None
            cls._needs_reload = False

            # Force garbage collection and clear CUDA cache
            gc.collect()
            try:
                import torch
                if torch.cuda.is_available():
                    torch.cuda.empty_cache()
                    logger.info("CUDA cache cleared")
            except ImportError:
                pass

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
            cls._unload_model()

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
            # Skip repeated segments (Whisper loop artifacts)
            if filtered and text == filtered[-1].get('text', '').strip():
                logger.debug(f"Filtered repeated segment: {text}")
                continue
            filtered.append(seg)
        return filtered

    def get_audio_duration(self, audio_path: str) -> Optional[float]:
        """Get audio duration in seconds using ffprobe."""
        try:
            cmd = [
                'ffprobe', '-v', 'error', '-show_entries', 'format=duration',
                '-of', 'default=noprint_wrappers=1:nokey=1', audio_path
            ]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            if result.returncode == 0:
                duration = float(result.stdout.strip())
                logger.info(f"Audio duration: {duration:.1f}s ({duration/60:.1f} min)")
                return duration
        except Exception as e:
            logger.warning(f"Could not get audio duration: {e}")
        return None

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
        """Clear CUDA cache to free GPU memory."""
        gc.collect()
        try:
            import torch
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
                logger.info("CUDA cache cleared")
        except ImportError:
            pass

    def preprocess_audio(self, input_path: str) -> Optional[str]:
        """
        Normalize audio for consistent transcription.
        Returns path to preprocessed file, or original path if preprocessing fails.
        """
        output_path = tempfile.mktemp(suffix='.wav')
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
                return output_path
            logger.warning(f"Audio preprocessing failed (returncode={result.returncode}), using original")
            if os.path.exists(output_path):
                os.unlink(output_path)
            return None
        except subprocess.TimeoutExpired:
            logger.warning("Audio preprocessing timed out, using original")
            if os.path.exists(output_path):
                os.unlink(output_path)
            return None
        except Exception as e:
            logger.warning(f"Audio preprocessing error: {e}, using original")
            if os.path.exists(output_path):
                try:
                    os.unlink(output_path)
                except:
                    pass
            return None

    def download_audio(self, url: str, timeout: int = 600) -> Optional[str]:
        """Download audio file from URL."""
        try:
            logger.info(f"Downloading audio from: {url}")
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
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
                    segments_generator, info = model.transcribe(
                        transcribe_path,
                        language="en",
                        initial_prompt=initial_prompt,
                        beam_size=5,
                        batch_size=batch_size,
                        word_timestamps=True,  # Enable word-level timestamps for boundary refinement
                        vad_filter=True,  # Enable VAD filter to skip silent parts
                        vad_parameters=dict(
                            min_silence_duration_ms=500,
                            speech_pad_ms=400
                        )
                    )

                    # Collect segments with real-time progress logging
                    result = []
                    segment_count = 0
                    last_log_time = 0

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
                        segment_dict = {
                            "start": segment.start,
                            "end": segment.end,
                            "text": segment.text.strip(),
                            "words": words  # Word timestamps for boundary refinement
                        }
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
            return None
        finally:
            # Clean up preprocessed file
            if preprocessed_path and os.path.exists(preprocessed_path):
                try:
                    os.unlink(preprocessed_path)
                    logger.debug(f"Cleaned up preprocessed file: {preprocessed_path}")
                except:
                    pass

    def format_timestamp(self, seconds: float) -> str:
        """Convert seconds to timestamp format."""
        hours = int(seconds // 3600)
        minutes = int((seconds % 3600) // 60)
        secs = seconds % 60
        return f"{hours:02d}:{minutes:02d}:{secs:06.3f}"

    def segments_to_text(self, segments: List[Dict]) -> str:
        """Convert segments to readable text format."""
        lines = []
        for segment in segments:
            start_ts = self.format_timestamp(segment['start'])
            end_ts = self.format_timestamp(segment['end'])
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
                except:
                    pass