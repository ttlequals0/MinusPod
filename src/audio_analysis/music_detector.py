"""
Music bed detection for identifying produced/inserted ads.

Key signals:
- Spectral flatness: Music is more tonal (lower flatness) than speech
- Low frequency energy: Music beds have bass/drums that speech lacks
- Harmonic ratio: Music has more sustained harmonic structure
"""

import logging
import os
from typing import List, Optional, Any

from .base import AudioSegmentSignal, SignalType

logger = logging.getLogger('podcast.audio_analysis.music')

# Check if librosa is available
LIBROSA_AVAILABLE = False
librosa = None
np = None

try:
    import librosa as _librosa
    import numpy as _np
    librosa = _librosa
    np = _np
    LIBROSA_AVAILABLE = True
except ImportError:
    logger.debug("librosa not available - music detection disabled")


class MusicBedDetector:
    """
    Detects music beds under speech, common in produced advertisements.

    Uses spectral analysis to distinguish music from pure speech content.
    """

    def __init__(
        self,
        sample_rate: int = 16000,
        frame_duration: float = 0.5,
        music_threshold: float = 0.6,
        min_region_duration: float = 10.0
    ):
        """
        Initialize the music detector.

        Args:
            sample_rate: Audio sample rate for analysis
            frame_duration: Analysis window size in seconds
            music_threshold: Probability threshold to flag as music
            min_region_duration: Minimum duration to report as music region
        """
        self.sr = sample_rate
        self.frame_duration = frame_duration
        self.music_threshold = music_threshold
        self.min_region_duration = min_region_duration

    def is_available(self) -> bool:
        """Check if this analyzer is available (librosa installed)."""
        return LIBROSA_AVAILABLE

    def analyze(self, audio_path: str) -> List[AudioSegmentSignal]:
        """
        Analyze audio for music bed presence.

        Args:
            audio_path: Path to the audio file

        Returns:
            List of music bed signals detected
        """
        if not LIBROSA_AVAILABLE:
            logger.warning("librosa not available, skipping music detection")
            return []

        if not os.path.exists(audio_path):
            logger.error(f"Audio file not found: {audio_path}")
            return []

        try:
            return self._analyze_with_librosa(audio_path)
        except Exception as e:
            logger.error(f"Music detection failed: {e}")
            return []

    def _analyze_with_librosa(self, audio_path: str) -> List[AudioSegmentSignal]:
        """Perform librosa-based spectral analysis."""
        # Load audio
        logger.info(f"Loading audio for music detection: {audio_path}")
        y, sr = librosa.load(audio_path, sr=self.sr, mono=True)

        duration = len(y) / sr
        logger.info(f"Analyzing {duration:.1f}s audio for music beds")

        hop_length = int(self.sr * self.frame_duration)
        frame_length = hop_length * 2

        music_frames = []

        # Process in frames
        for i in range(0, len(y) - frame_length, hop_length):
            start_time = i / self.sr
            end_time = (i + frame_length) / self.sr

            frame = y[i:i + frame_length]

            # Skip very quiet frames
            rms = np.sqrt(np.mean(frame ** 2))
            if rms < 0.001:
                continue

            # Extract features
            spectral_flatness = self._compute_spectral_flatness(frame)
            low_freq_energy = self._compute_low_freq_energy(frame)
            harmonic_ratio = self._compute_harmonic_ratio(frame)

            # Compute music probability
            music_prob = self._compute_music_probability(
                spectral_flatness,
                low_freq_energy,
                harmonic_ratio
            )

            if music_prob > self.music_threshold:
                music_frames.append({
                    'start': start_time,
                    'end': end_time,
                    'confidence': music_prob,
                    'spectral_flatness': spectral_flatness,
                    'low_freq_energy': low_freq_energy,
                    'harmonic_ratio': harmonic_ratio
                })

        # Merge consecutive frames into regions
        regions = self._merge_frames_to_regions(music_frames)

        logger.info(f"Found {len(regions)} music bed regions")
        return regions

    def _compute_spectral_flatness(self, frame: Any) -> float:
        """
        Compute spectral flatness.

        - Speech-only: Higher (more noise-like, ~0.1-0.3)
        - Music: Lower (more tonal, ~0.01-0.1)
        """
        spectrum = np.abs(librosa.stft(frame, n_fft=1024, hop_length=512))
        spectrum = spectrum + 1e-10  # Avoid log(0)

        flatness = librosa.feature.spectral_flatness(S=spectrum)[0]
        return float(np.mean(flatness))

    def _compute_low_freq_energy(self, frame: Any) -> float:
        """
        Compute ratio of energy below 300Hz.

        Music beds typically have more bass energy from drums and bass.
        """
        spectrum = np.abs(np.fft.rfft(frame))
        freqs = np.fft.rfftfreq(len(frame), 1/self.sr)

        low_mask = freqs < 300
        low_energy = np.sum(spectrum[low_mask] ** 2)
        total_energy = np.sum(spectrum ** 2)

        if total_energy > 0:
            return float(low_energy / total_energy)
        return 0.0

    def _compute_harmonic_ratio(self, frame: Any) -> float:
        """
        Compute harmonic-to-total energy ratio.

        Music has more sustained harmonic content than percussive speech.
        """
        D = librosa.stft(frame)
        H, P = librosa.decompose.hpss(D)

        harmonic_energy = np.sum(np.abs(H) ** 2)
        total_energy = np.sum(np.abs(D) ** 2)

        if total_energy > 0:
            return float(harmonic_energy / total_energy)
        return 0.0

    def _compute_music_probability(
        self,
        spectral_flatness: float,
        low_freq_energy: float,
        harmonic_ratio: float
    ) -> float:
        """Combine features into music probability score."""
        # Low spectral flatness = more musical (invert)
        flatness_score = 1.0 - min(spectral_flatness * 10, 1.0)

        # High low-freq energy = likely music bed
        bass_score = min(low_freq_energy * 5, 1.0)

        # High harmonic ratio = more musical
        harmonic_score = harmonic_ratio

        # Weighted combination
        music_prob = (
            0.35 * flatness_score +
            0.35 * bass_score +
            0.30 * harmonic_score
        )

        return float(np.clip(music_prob, 0, 1))

    def _merge_frames_to_regions(
        self,
        frames: List[dict],
        max_gap: float = 2.0,
        max_duration: float = 120.0  # Real music beds rarely exceed 2 min
    ) -> List[AudioSegmentSignal]:
        """Merge consecutive music frames into regions."""
        if not frames:
            return []

        regions = []
        current = {
            'start': frames[0]['start'],
            'end': frames[0]['end'],
            'confidences': [frames[0]['confidence']],
            'flatness': [frames[0]['spectral_flatness']],
            'bass': [frames[0]['low_freq_energy']],
            'harmonic': [frames[0]['harmonic_ratio']]
        }

        for frame in frames[1:]:
            gap = frame['start'] - current['end']
            current_duration = current['end'] - current['start']

            # Split if gap too large OR region exceeds max duration
            if gap > max_gap or current_duration >= max_duration:
                # Save current region if long enough
                duration = current['end'] - current['start']
                if duration >= self.min_region_duration:
                    regions.append(self._create_region_signal(current))

                # Start new region
                current = {
                    'start': frame['start'],
                    'end': frame['end'],
                    'confidences': [frame['confidence']],
                    'flatness': [frame['spectral_flatness']],
                    'bass': [frame['low_freq_energy']],
                    'harmonic': [frame['harmonic_ratio']]
                }
            else:
                # Extend current region
                current['end'] = frame['end']
                current['confidences'].append(frame['confidence'])
                current['flatness'].append(frame['spectral_flatness'])
                current['bass'].append(frame['low_freq_energy'])
                current['harmonic'].append(frame['harmonic_ratio'])

        # Save final region
        duration = current['end'] - current['start']
        if duration >= self.min_region_duration:
            regions.append(self._create_region_signal(current))

        return regions

    def _create_region_signal(self, region: dict) -> AudioSegmentSignal:
        """Create an AudioSegmentSignal from a region dict."""
        avg_confidence = sum(region['confidences']) / len(region['confidences'])
        avg_flatness = sum(region['flatness']) / len(region['flatness'])
        avg_bass = sum(region['bass']) / len(region['bass'])
        avg_harmonic = sum(region['harmonic']) / len(region['harmonic'])

        return AudioSegmentSignal(
            start=region['start'],
            end=region['end'],
            signal_type=SignalType.MUSIC_BED.value,
            confidence=avg_confidence,
            details={
                'spectral_flatness': round(avg_flatness, 3),
                'low_freq_energy': round(avg_bass, 3),
                'harmonic_ratio': round(avg_harmonic, 3)
            }
        )
