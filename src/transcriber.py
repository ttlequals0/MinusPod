"""Transcription using Faster Whisper."""
import logging
import tempfile
import os
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

class WhisperModelSingleton:
    _instance = None
    _base_model = None

    @classmethod
    def get_instance(cls) -> Tuple[WhisperModel, BatchedInferencePipeline]:
        """
        Get both the base model and batched pipeline instance
        Returns:
            Tuple[WhisperModel, BatchedInferencePipeline]: Base model for operations like language detection,
                                                          and batched pipeline for transcription
        """
        if cls._instance is None:
            model_size = os.getenv("WHISPER_MODEL", "small")
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
            logger.info("Whisper model and batched pipeline initialized")

        return cls._base_model, cls._instance

    @classmethod
    def get_base_model(cls) -> WhisperModel:
        """
        Get just the base model for operations like language detection
        Returns:
            WhisperModel: Base Whisper model
        """
        if cls._base_model is None:
            cls.get_instance()
        return cls._base_model

    @classmethod
    def get_batched_pipeline(cls) -> BatchedInferencePipeline:
        """
        Get just the batched pipeline for transcription
        Returns:
            BatchedInferencePipeline: Batched pipeline for efficient transcription
        """
        if cls._instance is None:
            cls.get_instance()
        return cls._instance

class Transcriber:
    def __init__(self):
        # Model is now managed by singleton
        pass

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

    def transcribe(self, audio_path: str) -> List[Dict]:
        """Transcribe audio file using Faster Whisper with batched pipeline."""
        try:
            # Get the batched pipeline for efficient transcription
            model = WhisperModelSingleton.get_batched_pipeline()

            logger.info(f"Starting transcription of: {audio_path}")

            # Create a simple prompt for podcast context
            initial_prompt = "This is a podcast episode."

            # Adjust batch size based on device
            device = os.getenv("WHISPER_DEVICE", "cpu")
            if device == "cuda":
                batch_size = 16  # Larger batch for GPU
                logger.info("Using GPU-optimized batch size: 16")
            else:
                batch_size = 8  # Smaller batch for CPU

            # Use the batched pipeline for transcription
            segments_generator, info = model.transcribe(
                audio_path,
                language="en",
                initial_prompt=initial_prompt,
                beam_size=5,
                batch_size=batch_size,
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
                segment_dict = {
                    "start": segment.start,
                    "end": segment.end,
                    "text": segment.text.strip()
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

            duration_min = result[-1]['end'] / 60 if result else 0
            logger.info(f"Transcription completed: {len(result)} segments, {duration_min:.1f} minutes")
            return result
        except Exception as e:
            logger.error(f"Transcription failed: {e}")
            return None

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