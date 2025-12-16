"""
Speaker diarization and conversation pattern analysis.

Key insight: In conversational podcasts, host-read ads break the natural
turn-taking pattern - one speaker talks for 60-90+ seconds uninterrupted
while others are silent.
"""

import logging
import os
import re
import traceback
import warnings
from typing import List, Dict, Optional, Tuple
from collections import defaultdict

from .base import (
    AudioSegmentSignal, SpeakerSegment, ConversationMetrics, SignalType
)

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
                    # Disable cuDNN globally for this process
                    # pyannote uses LSTMs which trigger cuDNN RNN code path
                    # cuDNN 8 + CUDA 12.1 has version mismatch issues with RNN ops
                    # This keeps GPU acceleration but uses PyTorch native RNN instead
                    torch.backends.cudnn.enabled = False
                    self._pipeline.to(torch.device("cuda"))
                    logger.info("Diarization pipeline loaded on GPU (cuDNN disabled)")
                else:
                    logger.info("Diarization pipeline loaded on CPU")

            except Exception as e:
                logger.error(f"Failed to load diarization pipeline: {e}")
                logger.error(f"Full traceback:\n{traceback.format_exc()}")
                raise

    def _analyze_speakers(
        self,
        audio_path: str,
        transcript_segments: Optional[List[Dict]]
    ) -> Tuple[List[AudioSegmentSignal], Optional[ConversationMetrics]]:
        """Perform speaker diarization and pattern analysis."""
        self._load_pipeline()

        # Run diarization
        logger.info(f"Running speaker diarization on {audio_path}")
        diarization = self._pipeline(audio_path)

        # Convert to segments
        segments = []
        for turn, _, speaker in diarization.itertracks(yield_label=True):
            segments.append(SpeakerSegment(
                start=turn.start,
                end=turn.end,
                speaker=speaker
            ))

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
