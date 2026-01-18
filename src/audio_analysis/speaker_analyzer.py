"""
Speaker diarization and conversation pattern analysis.

Key insight: In conversational podcasts, host-read ads break the natural
turn-taking pattern - one speaker talks for 60-90+ seconds uninterrupted
while others are silent.
"""

import gc
import logging
import os
import re
import time
import traceback
import warnings
from typing import List, Dict, Optional, Tuple, Any
from collections import defaultdict

from .base import (
    AudioSegmentSignal, SpeakerSegment, ConversationMetrics, SignalType
)
from utils.gpu import clear_gpu_memory, get_gpu_memory_info

logger = logging.getLogger('podcast.audio_analysis.speaker')

# Suppress harmless warnings from torchaudio and pyannote before importing
# These warnings don't affect functionality but spam the logs
warnings.filterwarnings('ignore', message='.*MPEG_LAYER_III.*')
warnings.filterwarnings('ignore', message='.*TensorFloat-32.*')
warnings.filterwarnings('ignore', message='.*degrees of freedom.*')

# Check if pyannote is available
try:
    from pyannote.audio import Pipeline
    import torch
    PYANNOTE_AVAILABLE = True
except ImportError:
    PYANNOTE_AVAILABLE = False
    logger.debug("pyannote.audio not available - speaker diarization disabled")

# NumPy is needed for calculations
try:
    import numpy as np
except ImportError:
    np = None


# Ad language patterns for transcript matching
AD_PATTERNS = re.compile(
    r'promo\s*code|use\s+code\s+\w+|\.com\/|'
    r'sponsored\s+by|brought\s+to\s+you|thanks\s+to\s+our\s+sponsor|'
    r'check\s+(it\s+)?out\s+at|go\s+to\s+\w+\.(com|co|io)|'
    r'percent\s+off|free\s+(trial|shipping)|sign\s+up|'
    r'link\s+in\s+(the\s+)?(description|show\s*notes)',
    re.IGNORECASE
)

# Chunked processing configuration for long episodes
# Episodes longer than LONG_EPISODE_THRESHOLD will be processed in chunks
# to prevent OOM errors from loading entire audio files into memory
CHUNK_DURATION_SECONDS = 1800  # 30 minutes per chunk
CHUNK_OVERLAP_SECONDS = 30     # Overlap between chunks for speaker continuity
SPEAKER_MATCH_THRESHOLD = 0.5  # Cosine distance threshold for same speaker
LONG_EPISODE_THRESHOLD = 3600  # Episodes > 1 hour use chunked processing

# Retry configuration for per-chunk processing
CHUNK_MAX_RETRIES = 2          # Max retries per chunk on failure
CHUNK_RETRY_DELAY = 5          # Seconds to wait between retries
OOM_RETRY_DELAY = 10           # Longer delay after OOM errors


def get_chunk_config_for_duration(duration_seconds: float) -> dict:
    """
    Get optimal chunk configuration based on episode duration.

    Longer episodes use larger chunks (fewer chunks = fewer chunk boundary
    speaker matching issues), but with more overlap to ensure continuity.

    Args:
        duration_seconds: Total episode duration in seconds

    Returns:
        Dict with chunk_duration, chunk_overlap, and speaker_match_threshold
    """
    if duration_seconds > 14400:  # > 4 hours
        return {
            'chunk_duration': 2400,  # 40 min chunks (fewer chunks)
            'chunk_overlap': 60,     # More overlap for better matching
            'speaker_match_threshold': 0.4,  # Stricter matching
        }
    elif duration_seconds > 10800:  # > 3 hours
        return {
            'chunk_duration': 1200,  # 20 min chunks (reduced for lower peak memory)
            'chunk_overlap': 60,     # Increased overlap for better speaker matching
            'speaker_match_threshold': 0.45,
        }
    elif duration_seconds > 7200:  # > 2 hours
        return {
            'chunk_duration': 1800,  # 30 min chunks
            'chunk_overlap': 30,
            'speaker_match_threshold': 0.5,
        }
    else:  # 1-2 hours
        return {
            'chunk_duration': CHUNK_DURATION_SECONDS,
            'chunk_overlap': CHUNK_OVERLAP_SECONDS,
            'speaker_match_threshold': SPEAKER_MATCH_THRESHOLD,
        }


class SpeakerAnalyzer:
    """
    Analyzes speaker patterns to detect monologue ad reads.

    Uses pyannote speaker diarization to identify speakers and detect
    anomalous monologue sections that may indicate ad reads.
    """

    def __init__(
        self,
        hf_token: Optional[str] = None,
        min_monologue_duration: float = 45.0,
        context_window: float = 120.0
    ):
        """
        Initialize the speaker analyzer.

        Args:
            hf_token: HuggingFace token for pyannote models
            min_monologue_duration: Minimum seconds to flag as monologue
            context_window: Seconds of context to analyze around monologues
        """
        self.hf_token = hf_token or os.environ.get('HF_TOKEN')
        self.min_monologue_duration = min_monologue_duration
        self.context_window = context_window
        self._pipeline = None

        # Log token status for debugging (masked)
        if self.hf_token:
            masked = self.hf_token[:7] + '...' if len(self.hf_token) > 7 else '***'
            logger.debug(f"HF token configured: {masked}")
        else:
            logger.debug("HF token not configured")

    def is_available(self) -> bool:
        """Check if this analyzer is available."""
        if not PYANNOTE_AVAILABLE:
            return False
        if not self.hf_token:
            logger.warning("HF_TOKEN not set - speaker diarization unavailable")
            return False
        return True

    def analyze(
        self,
        audio_path: str,
        transcript_segments: Optional[List[Dict]] = None
    ) -> Tuple[List[AudioSegmentSignal], Optional[ConversationMetrics]]:
        """
        Analyze audio for speaker patterns and monologues.

        Args:
            audio_path: Path to the audio file
            transcript_segments: Optional transcript for ad language detection

        Returns:
            Tuple of (list of monologue signals, conversation metrics)
        """
        if not self.is_available():
            logger.warning("Speaker analysis not available, skipping")
            return [], None

        if not os.path.exists(audio_path):
            logger.error(f"Audio file not found: {audio_path}")
            return [], None

        try:
            return self._analyze_speakers(audio_path, transcript_segments)
        except Exception as e:
            logger.error(f"Speaker analysis failed: {e}")
            return [], None

    def _load_pipeline(self):
        """Lazy load the diarization pipeline."""
        if self._pipeline is None:
            logger.info("Loading pyannote speaker diarization model...")
            try:
                # pyannote 3.x requires use_auth_token (not token)
                # huggingface_hub must be <1.0 for this to work
                self._pipeline = Pipeline.from_pretrained(
                    "pyannote/speaker-diarization-3.1",
                    use_auth_token=self.hf_token
                )

                if self._pipeline is None:
                    # Pipeline returned None - usually means license not accepted
                    logger.error(
                        "Pipeline.from_pretrained returned None. "
                        "This usually means the model license has not been accepted. "
                        "Visit https://hf.co/pyannote/speaker-diarization-3.1 "
                        "and accept the license while logged into your HuggingFace account."
                    )
                    raise RuntimeError("Failed to load pipeline - check model license acceptance")

                if torch.cuda.is_available():
                    # Use GPU with cuDNN enabled (PyTorch bundles compatible cuDNN)
                    # Base Docker image is CUDA-only (no system cuDNN) to avoid version mismatch
                    self._pipeline.to(torch.device("cuda"))
                    logger.info("Diarization pipeline loaded on GPU with cuDNN")
                else:
                    logger.info("Diarization pipeline loaded on CPU")

            except Exception as e:
                logger.error(f"Failed to load diarization pipeline: {e}")
                logger.error(f"Full traceback:\n{traceback.format_exc()}")
                raise

    def _get_audio_duration(self, audio_path: str) -> float:
        """Get audio duration in seconds without loading full file into memory."""
        import torchaudio
        info = torchaudio.info(audio_path)
        return info.num_frames / info.sample_rate

    def _load_audio_chunk(
        self,
        audio_path: str,
        start_sec: float,
        end_sec: float
    ) -> Tuple[torch.Tensor, int]:
        """
        Load a specific time range of audio without loading the entire file.

        Args:
            audio_path: Path to audio file
            start_sec: Start time in seconds
            end_sec: End time in seconds

        Returns:
            Tuple of (waveform tensor, sample_rate at 16kHz)
        """
        import torchaudio

        # Get file info without loading data
        info = torchaudio.info(audio_path)
        original_sr = info.sample_rate

        # Calculate frame offsets
        frame_offset = int(start_sec * original_sr)
        num_frames = int((end_sec - start_sec) * original_sr)

        # Load only the requested segment
        waveform, sr = torchaudio.load(
            audio_path,
            frame_offset=frame_offset,
            num_frames=num_frames
        )

        # Resample to 16kHz if needed (pyannote expects 16kHz)
        if sr != 16000:
            resampler = torchaudio.transforms.Resample(sr, 16000)
            waveform = resampler(waveform)

        return waveform, 16000

    def _clear_memory(self, force_gc: bool = True, log_usage: bool = False):
        """
        Clear CUDA cache and run garbage collection between chunks.

        Delegates to utils.gpu.clear_gpu_memory() for core cleanup.

        Args:
            force_gc: Whether to force garbage collection (passed to clear_gpu_memory)
            log_usage: Whether to log current memory usage
        """
        # Use consolidated GPU cleanup
        clear_gpu_memory()

        if log_usage:
            mem_info = get_gpu_memory_info()
            if mem_info:
                allocated = mem_info.get('allocated', 0) / (1024 ** 3)
                reserved = mem_info.get('cached', 0) / (1024 ** 3)
                logger.debug(
                    f"GPU memory: {allocated:.2f}GB allocated, "
                    f"{reserved:.2f}GB reserved"
                )

    def _process_chunk_with_retry(
        self,
        audio_path: str,
        chunk_start: float,
        chunk_end: float,
        chunk_idx: int,
        max_retries: int = CHUNK_MAX_RETRIES
    ) -> Tuple[Optional[Any], Optional[torch.Tensor], int]:
        """
        Process a single chunk with retry logic for transient failures.

        Args:
            audio_path: Path to audio file
            chunk_start: Start time in seconds
            chunk_end: End time in seconds
            chunk_idx: Chunk index for logging
            max_retries: Maximum number of retries

        Returns:
            Tuple of (diarization_result, waveform, sample_rate) or (None, None, 0) on failure
        """
        import torchaudio

        for attempt in range(max_retries + 1):
            try:
                # Clear memory before each attempt
                self._clear_memory(force_gc=True, log_usage=(attempt > 0))

                # Load audio chunk
                waveform, sample_rate = self._load_audio_chunk(
                    audio_path, chunk_start, chunk_end
                )

                # Pad to 10-second boundary to avoid pyannote chunk mismatch
                pyannote_chunk = 160000  # 10 seconds at 16kHz
                remainder = waveform.shape[1] % pyannote_chunk
                if remainder != 0:
                    waveform = torch.nn.functional.pad(
                        waveform, (0, pyannote_chunk - remainder)
                    )

                # Run diarization on chunk
                diarization = self._pipeline({
                    "waveform": waveform,
                    "sample_rate": sample_rate
                })

                return diarization, waveform, sample_rate

            except torch.cuda.OutOfMemoryError as e:
                logger.warning(
                    f"Chunk {chunk_idx + 1} attempt {attempt + 1}: CUDA OOM error"
                )
                self._clear_memory(force_gc=True, log_usage=True)

                if attempt < max_retries:
                    time.sleep(OOM_RETRY_DELAY)
                else:
                    logger.error(
                        f"Chunk {chunk_idx + 1} failed after {max_retries + 1} attempts: "
                        f"CUDA out of memory"
                    )
                    return None, None, 0

            except Exception as e:
                logger.warning(
                    f"Chunk {chunk_idx + 1} attempt {attempt + 1} failed: "
                    f"{type(e).__name__}: {e}"
                )

                if attempt < max_retries:
                    time.sleep(CHUNK_RETRY_DELAY)
                else:
                    logger.error(
                        f"Chunk {chunk_idx + 1} failed after {max_retries + 1} attempts: {e}"
                    )
                    return None, None, 0

        return None, None, 0

    def _extract_speaker_embeddings(
        self,
        segments: List[SpeakerSegment],
        waveform: torch.Tensor,
        sample_rate: int,
        chunk_offset: float = 0.0
    ) -> Dict[str, np.ndarray]:
        """
        Extract speaker embeddings for each unique speaker in the segments.

        Uses the longest segment for each speaker to get the most reliable embedding.

        Args:
            segments: List of speaker segments from diarization
            waveform: Audio waveform tensor
            sample_rate: Sample rate of the waveform
            chunk_offset: Time offset of this chunk in the full audio

        Returns:
            Dictionary mapping speaker ID to embedding numpy array
        """
        from pyannote.audio import Model, Inference

        # Load embedding model (uses same HF token as pipeline)
        embedding_model = Model.from_pretrained(
            "pyannote/embedding",
            use_auth_token=self.hf_token
        )

        device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        embedding_model.to(device)

        inference = Inference(embedding_model, window="whole")

        speaker_embeddings = {}
        for speaker in set(s.speaker for s in segments):
            # Get longest segment for this speaker (most reliable embedding)
            speaker_segs = [s for s in segments if s.speaker == speaker]
            longest = max(speaker_segs, key=lambda s: s.duration)

            # Ensure segment is long enough for embedding (at least 0.5 seconds)
            if longest.duration < 0.5:
                continue

            # Calculate sample indices relative to chunk start
            # Segments have global timestamps, so subtract chunk_offset
            local_start = longest.start - chunk_offset
            local_end = longest.end - chunk_offset

            start_sample = int(local_start * sample_rate)
            end_sample = int(local_end * sample_rate)

            # Bounds check
            start_sample = max(0, start_sample)
            end_sample = min(waveform.shape[1], end_sample)

            if end_sample - start_sample < sample_rate * 0.5:  # Less than 0.5 sec
                continue

            # Extract waveform for this segment
            speaker_waveform = waveform[:, start_sample:end_sample]

            try:
                # Get embedding - inference expects dict with waveform and sample_rate
                embedding = inference({
                    "waveform": speaker_waveform,
                    "sample_rate": sample_rate
                })
                # Handle both torch tensor and numpy array returns
                if hasattr(embedding, 'cpu'):
                    embedding = embedding.cpu().numpy()
                elif not isinstance(embedding, np.ndarray):
                    embedding = np.array(embedding)
                speaker_embeddings[speaker] = embedding
            except Exception as e:
                logger.warning(f"Failed to extract embedding for {speaker}: {e}")
                continue

        return speaker_embeddings

    def _match_speakers_across_chunks(
        self,
        new_embeddings: Dict[str, np.ndarray],
        global_embeddings: Dict[str, np.ndarray],
        next_speaker_id: int
    ) -> Tuple[Dict[str, str], Dict[str, np.ndarray], int]:
        """
        Match speakers from a new chunk to global speakers using cosine similarity.

        Args:
            new_embeddings: Speaker embeddings from the current chunk
            global_embeddings: Accumulated global speaker embeddings
            next_speaker_id: Next available global speaker ID number

        Returns:
            Tuple of (speaker mapping dict, updated global embeddings, next speaker ID)
        """
        from scipy.spatial.distance import cdist

        mapping = {}
        updated_global = global_embeddings.copy()
        used_global = set()

        for new_speaker, new_emb in new_embeddings.items():
            best_match = None
            best_distance = float('inf')

            # Compare against all global speakers
            for global_speaker, global_emb in global_embeddings.items():
                if global_speaker in used_global:
                    continue

                # Compute cosine distance (0 = identical, 2 = opposite)
                distance = cdist(
                    new_emb.reshape(1, -1),
                    global_emb.reshape(1, -1),
                    metric='cosine'
                )[0, 0]

                if distance < best_distance:
                    best_distance = distance
                    best_match = global_speaker

            if best_match and best_distance < SPEAKER_MATCH_THRESHOLD:
                # Match found - use existing global speaker ID
                mapping[new_speaker] = best_match
                used_global.add(best_match)
                logger.debug(
                    f"Matched {new_speaker} -> {best_match} "
                    f"(distance={best_distance:.3f})"
                )
            else:
                # New speaker - assign new global ID
                new_global_id = f"SPEAKER_{next_speaker_id:02d}"
                mapping[new_speaker] = new_global_id
                updated_global[new_global_id] = new_emb
                next_speaker_id += 1
                logger.debug(f"New speaker: {new_speaker} -> {new_global_id}")

        return mapping, updated_global, next_speaker_id

    def _analyze_speakers(
        self,
        audio_path: str,
        transcript_segments: Optional[List[Dict]]
    ) -> Tuple[List[AudioSegmentSignal], Optional[ConversationMetrics]]:
        """Perform speaker diarization and pattern analysis."""
        self._load_pipeline()

        # Get duration to decide processing strategy
        try:
            duration = self._get_audio_duration(audio_path)
        except Exception as e:
            logger.warning(f"Could not get audio duration: {e}")
            duration = 0  # Will use standard processing

        # Choose processing method based on episode length
        if duration > LONG_EPISODE_THRESHOLD:
            logger.info(
                f"Long episode ({duration/3600:.1f}h) - using chunked processing "
                f"({CHUNK_DURATION_SECONDS/60:.0f}min chunks)"
            )
            segments = self._diarize_chunked(audio_path, duration)
        else:
            logger.info(f"Standard episode ({duration/60:.1f}min) - using standard processing")
            segments = self._diarize_standard(audio_path)

        logger.info(f"Diarization complete: {len(segments)} segments")

        if not segments:
            return [], None

        # Analyze conversation patterns
        metrics = self._analyze_conversation(segments)

        # Find monologues only in conversational content
        signals = []
        if metrics.is_conversational:
            signals = self._find_monologues(
                segments, metrics, transcript_segments
            )

        return signals, metrics

    def _diarize_standard(self, audio_path: str) -> List[SpeakerSegment]:
        """
        Standard diarization for short/medium episodes.

        Loads entire audio file into memory (suitable for episodes < 1 hour).
        """
        logger.info(f"Running speaker diarization on {audio_path}")
        try:
            import torchaudio
            # Load and pad audio to prevent chunk boundary issues
            waveform, sample_rate = torchaudio.load(audio_path)

            # Resample to 16kHz if needed (pyannote expects 16kHz)
            if sample_rate != 16000:
                resampler = torchaudio.transforms.Resample(sample_rate, 16000)
                waveform = resampler(waveform)
                sample_rate = 16000

            # Pad to next 10-second boundary (160000 samples) to avoid chunk mismatch
            chunk_size = 160000  # 10 seconds at 16kHz
            current_length = waveform.shape[1]
            remainder = current_length % chunk_size
            if remainder != 0:
                padding_needed = chunk_size - remainder
                waveform = torch.nn.functional.pad(waveform, (0, padding_needed))
                logger.debug(f"Padded audio from {current_length} to {waveform.shape[1]} samples")

            # Run diarization on padded waveform
            diarization = self._pipeline({"waveform": waveform, "sample_rate": sample_rate})
        except Exception as e:
            # Fallback to direct file processing if preprocessing fails
            logger.warning(f"Audio preprocessing failed, using direct file: {e}")
            diarization = self._pipeline(audio_path)

        # Convert to segments
        segments = []
        for turn, _, speaker in diarization.itertracks(yield_label=True):
            segments.append(SpeakerSegment(
                start=turn.start,
                end=turn.end,
                speaker=speaker
            ))

        return segments

    def _diarize_chunked(
        self,
        audio_path: str,
        total_duration: float
    ) -> List[SpeakerSegment]:
        """
        Chunked diarization for long episodes to prevent OOM.

        Processes audio in time-based chunks with overlap, matching speakers
        across chunks using embedding similarity. Uses dynamic chunk sizing
        based on episode duration for optimal performance.
        """
        # Get dynamic chunk configuration based on duration
        chunk_config = get_chunk_config_for_duration(total_duration)
        chunk_duration = chunk_config['chunk_duration']
        chunk_overlap = chunk_config['chunk_overlap']
        speaker_threshold = chunk_config['speaker_match_threshold']

        total_chunks = int((total_duration + chunk_duration - 1) / (chunk_duration - chunk_overlap))
        logger.info(
            f"Chunked diarization: {total_duration/3600:.1f}h episode, "
            f"{chunk_duration/60:.0f}min chunks, {chunk_overlap}s overlap, "
            f"~{total_chunks} chunks"
        )

        all_segments = []
        global_embeddings = {}  # Global speaker ID -> embedding
        next_speaker_id = 0

        chunk_start = 0.0
        chunk_idx = 0

        while chunk_start < total_duration:
            chunk_end = min(chunk_start + chunk_duration, total_duration)

            logger.info(
                f"Processing chunk {chunk_idx + 1}: "
                f"{chunk_start/60:.1f}-{chunk_end/60:.1f} min "
                f"({(chunk_end - chunk_start)/60:.1f} min)"
            )

            try:
                # Process chunk with retry logic
                diarization, waveform, sample_rate = self._process_chunk_with_retry(
                    audio_path, chunk_start, chunk_end, chunk_idx
                )

                if diarization is None:
                    # Chunk processing failed after retries - continue with next chunk
                    logger.warning(
                        f"Chunk {chunk_idx + 1} could not be processed, skipping"
                    )
                    chunk_start = chunk_end - chunk_overlap
                    chunk_idx += 1
                    continue

                # Convert to segments with global timestamps
                chunk_segments = []
                for turn, _, speaker in diarization.itertracks(yield_label=True):
                    # Skip segments in overlap region (except first chunk)
                    # This prevents duplicate segments at boundaries
                    if chunk_idx > 0 and turn.start < chunk_overlap:
                        continue

                    chunk_segments.append(SpeakerSegment(
                        start=chunk_start + turn.start,
                        end=chunk_start + turn.end,
                        speaker=speaker
                    ))

                # Extract embeddings and match speakers across chunks
                if chunk_segments:
                    try:
                        chunk_embeddings = self._extract_speaker_embeddings(
                            chunk_segments, waveform, sample_rate, chunk_start
                        )

                        if chunk_embeddings:
                            if global_embeddings:
                                # Match to existing speakers
                                mapping, global_embeddings, next_speaker_id = \
                                    self._match_speakers_across_chunks(
                                        chunk_embeddings,
                                        global_embeddings,
                                        next_speaker_id
                                    )
                                # Remap speaker labels to global IDs
                                for seg in chunk_segments:
                                    if seg.speaker in mapping:
                                        seg.speaker = mapping[seg.speaker]
                            else:
                                # First chunk - initialize global embeddings
                                for speaker, emb in chunk_embeddings.items():
                                    global_id = f"SPEAKER_{next_speaker_id:02d}"
                                    global_embeddings[global_id] = emb
                                    # Remap this speaker
                                    for seg in chunk_segments:
                                        if seg.speaker == speaker:
                                            seg.speaker = global_id
                                    next_speaker_id += 1

                    except Exception as e:
                        logger.warning(
                            f"Speaker embedding extraction failed for chunk {chunk_idx + 1}: {e}. "
                            "Speakers may not be consistent across chunks."
                        )

                    all_segments.extend(chunk_segments)

                logger.info(
                    f"Chunk {chunk_idx + 1}: {len(chunk_segments)} segments, "
                    f"{len(global_embeddings)} total speakers"
                )

            except Exception as e:
                logger.warning(
                    f"Chunk {chunk_idx + 1} failed: {e}. "
                    "Continuing with remaining chunks."
                )

            finally:
                # Clear memory before next chunk
                self._clear_memory()

            # Check if we've processed the last chunk
            if chunk_end >= total_duration:
                # We've reached the end of the audio, exit the loop
                break

            # Move to next chunk (with overlap for continuity)
            chunk_start = chunk_end - chunk_overlap
            chunk_idx += 1

        logger.info(
            f"Chunked diarization complete: {len(all_segments)} total segments, "
            f"{len(global_embeddings)} speakers across {chunk_idx} chunks"
        )

        return all_segments

    def _analyze_conversation(
        self,
        segments: List[SpeakerSegment]
    ) -> ConversationMetrics:
        """Analyze overall conversation patterns."""
        if not segments or np is None:
            return ConversationMetrics(0, 0, 0, 0, False)

        # Count speakers
        speakers = set(s.speaker for s in segments)
        num_speakers = len(speakers)

        # Calculate speaker balance using entropy
        total_time = sum(s.duration for s in segments)
        speaker_times = defaultdict(float)
        for s in segments:
            speaker_times[s.speaker] += s.duration

        if total_time > 0 and num_speakers > 1:
            proportions = [t / total_time for t in speaker_times.values()]
            entropy = -sum(p * np.log(p + 1e-10) for p in proportions)
            max_entropy = np.log(num_speakers)
            speaker_balance = entropy / max_entropy if max_entropy > 0 else 0
        else:
            speaker_balance = 0

        # Find primary speaker (most airtime)
        primary_speaker = max(speaker_times.keys(), key=lambda s: speaker_times[s])

        # Turn statistics
        turn_durations = [s.duration for s in segments]
        avg_turn_duration = float(np.mean(turn_durations))

        total_duration = segments[-1].end - segments[0].start
        turn_frequency = (
            len(segments) / (total_duration / 60)
            if total_duration > 0 else 0
        )

        # Is this conversational?
        # Require: 2+ speakers, reasonable balance, frequent turn-taking
        is_conversational = (
            num_speakers >= 2 and
            speaker_balance > 0.3 and
            turn_frequency > 4  # More than 4 turns per minute
        )

        logger.info(
            f"Conversation: {num_speakers} speakers, "
            f"balance={speaker_balance:.2f}, "
            f"turns={turn_frequency:.1f}/min, "
            f"conversational={is_conversational}"
        )

        return ConversationMetrics(
            num_speakers=num_speakers,
            speaker_balance=round(speaker_balance, 2),
            avg_turn_duration=round(avg_turn_duration, 1),
            turn_frequency=round(turn_frequency, 1),
            is_conversational=is_conversational,
            primary_speaker=primary_speaker
        )

    def _find_monologues(
        self,
        segments: List[SpeakerSegment],
        metrics: ConversationMetrics,
        transcript_segments: Optional[List[Dict]]
    ) -> List[AudioSegmentSignal]:
        """Find anomalous monologue sections."""
        # Merge consecutive segments from same speaker
        merged = self._merge_consecutive_segments(segments)

        signals = []

        for seg in merged:
            if seg.duration < self.min_monologue_duration:
                continue

            # Calculate context turn rate
            context_before = [
                s for s in merged
                if s.end <= seg.start and s.end > seg.start - self.context_window
            ]
            context_after = [
                s for s in merged
                if s.start >= seg.end and s.start < seg.end + self.context_window
            ]

            context_count = len(context_before) + len(context_after)
            context_duration = self.context_window * 2
            context_turn_rate = (
                context_count / (context_duration / 60)
                if context_duration > 0 else metrics.turn_frequency
            )

            # Calculate monologue score based on expected turns
            expected_turns = (seg.duration / 60) * context_turn_rate
            monologue_score = 1.0 - (1.0 / (expected_turns + 1))

            # Duration factor (45-120s is typical ad length)
            if 45 <= seg.duration <= 120:
                monologue_score = min(monologue_score * 1.2, 1.0)
            elif seg.duration > 180:
                monologue_score *= 0.8  # Very long = probably not an ad

            # Boost if speaker is the primary host (likely ad reader)
            is_host = seg.speaker == metrics.primary_speaker

            # Check for ad language in transcript
            has_ad_language = False
            ad_matches = []
            if transcript_segments:
                mono_text = self._get_transcript_for_range(
                    transcript_segments, seg.start, seg.end
                )
                matches = AD_PATTERNS.findall(mono_text)
                has_ad_language = len(matches) > 0
                ad_matches = matches[:3]  # Limit to 3 examples

            # Final confidence
            confidence = monologue_score
            if has_ad_language:
                confidence = min(confidence + 0.2, 0.95)
            if is_host:
                confidence = min(confidence + 0.05, 0.95)

            signals.append(AudioSegmentSignal(
                start=seg.start,
                end=seg.end,
                signal_type=SignalType.MONOLOGUE.value,
                confidence=round(confidence, 2),
                details={
                    'speaker': seg.speaker,
                    'is_host': is_host,
                    'context_turn_rate': round(context_turn_rate, 1),
                    'has_ad_language': has_ad_language,
                    'ad_matches': ad_matches
                }
            ))

        # Sort by confidence descending
        signals.sort(key=lambda s: s.confidence, reverse=True)

        logger.info(f"Found {len(signals)} monologue regions")
        return signals

    def _merge_consecutive_segments(
        self,
        segments: List[SpeakerSegment],
        max_gap: float = 1.5
    ) -> List[SpeakerSegment]:
        """Merge consecutive segments from same speaker."""
        if not segments:
            return []

        merged = [SpeakerSegment(
            start=segments[0].start,
            end=segments[0].end,
            speaker=segments[0].speaker
        )]

        for seg in segments[1:]:
            last = merged[-1]

            if seg.speaker == last.speaker and (seg.start - last.end) < max_gap:
                # Extend the last segment
                merged[-1] = SpeakerSegment(
                    start=last.start,
                    end=seg.end,
                    speaker=last.speaker
                )
            else:
                merged.append(SpeakerSegment(
                    start=seg.start,
                    end=seg.end,
                    speaker=seg.speaker
                ))

        return merged

    def _get_transcript_for_range(
        self,
        segments: List[Dict],
        start: float,
        end: float
    ) -> str:
        """Get transcript text for a time range."""
        texts = []
        for seg in segments:
            seg_start = seg.get('start', 0)
            seg_end = seg.get('end', 0)
            if seg_end > start and seg_start < end:
                texts.append(seg.get('text', ''))
        return ' '.join(texts)
