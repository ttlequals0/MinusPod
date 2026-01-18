"""
Audio Fingerprinter - Chromaprint-based audio fingerprinting for ad detection.

Uses the Chromaprint library (via fpcalc binary) to generate audio fingerprints
that can identify identical or near-identical audio segments across episodes.
This is particularly effective for DAI (Dynamic Ad Insertion) ads that are
inserted as identical audio files.
"""
import logging
import os
import subprocess
import tempfile
from dataclasses import dataclass
from typing import List, Optional, Tuple
import json

from utils.audio import get_audio_duration as _utils_get_audio_duration

logger = logging.getLogger('podcast.fingerprint')

# Fingerprint matching threshold (0-1, lower = more strict)
# 0.65 allows for minor encoding differences while avoiding false positives
MATCH_THRESHOLD = 0.65

# Minimum duration for fingerprinting (seconds)
MIN_SEGMENT_DURATION = 5.0

# Fingerprint chunk size for sliding window search (seconds)
FINGERPRINT_CHUNK_SIZE = 10.0

# Step size for sliding window (seconds)
SLIDING_STEP_SIZE = 2.0


@dataclass
class FingerprintMatch:
    """Represents a fingerprint match in an audio file."""
    pattern_id: int
    start: float
    end: float
    confidence: float
    sponsor: Optional[str] = None


@dataclass
class AudioFingerprint:
    """Represents an audio fingerprint."""
    fingerprint: str  # Raw chromaprint fingerprint
    duration: float
    pattern_id: Optional[int] = None


class AudioFingerprinter:
    """
    Audio fingerprinting using Chromaprint for identifying repeated ads.

    This class provides functionality to:
    - Generate fingerprints for audio segments
    - Compare fingerprints to find matches
    - Search for known ad fingerprints in new episodes
    """

    def __init__(self, db=None):
        """
        Initialize the audio fingerprinter.

        Args:
            db: Database instance for storing/retrieving fingerprints
        """
        self.db = db
        self._fpcalc_path = self._find_fpcalc()

    def _find_fpcalc(self) -> Optional[str]:
        """Find the fpcalc binary."""
        # Check common locations
        paths = [
            '/usr/bin/fpcalc',
            '/usr/local/bin/fpcalc',
            'fpcalc'  # In PATH
        ]

        for path in paths:
            try:
                result = subprocess.run(
                    [path, '-version'],
                    capture_output=True,
                    timeout=5
                )
                if result.returncode == 0:
                    logger.debug(f"Found fpcalc at: {path}")
                    return path
            except (subprocess.SubprocessError, FileNotFoundError):
                continue

        logger.warning("fpcalc not found - audio fingerprinting disabled")
        return None

    def is_available(self) -> bool:
        """Check if audio fingerprinting is available."""
        return self._fpcalc_path is not None

    def generate_fingerprint(
        self,
        audio_path: str,
        start: float = 0,
        duration: float = None
    ) -> Optional[AudioFingerprint]:
        """
        Generate a fingerprint for an audio segment.

        Args:
            audio_path: Path to audio file
            start: Start time in seconds
            duration: Duration in seconds (None = entire file)

        Returns:
            AudioFingerprint or None if generation failed
        """
        if not self._fpcalc_path:
            return None

        try:
            # Build fpcalc command
            cmd = [self._fpcalc_path, '-json']

            # If we need a specific segment, extract it first
            if start > 0 or duration is not None:
                # Use ffmpeg to extract segment
                with tempfile.NamedTemporaryFile(suffix='.wav', delete=False) as tmp:
                    tmp_path = tmp.name

                try:
                    ffmpeg_cmd = [
                        'ffmpeg', '-y', '-i', audio_path,
                        '-ss', str(start),
                    ]
                    if duration:
                        ffmpeg_cmd.extend(['-t', str(duration)])
                    ffmpeg_cmd.extend([
                        '-ac', '1',  # Mono
                        '-ar', '16000',  # 16kHz
                        '-f', 'wav',
                        tmp_path
                    ])

                    subprocess.run(
                        ffmpeg_cmd,
                        capture_output=True,
                        timeout=30,
                        check=True
                    )

                    cmd.append(tmp_path)
                    result = subprocess.run(
                        cmd,
                        capture_output=True,
                        timeout=30
                    )
                finally:
                    if os.path.exists(tmp_path):
                        os.unlink(tmp_path)
            else:
                cmd.append(audio_path)
                result = subprocess.run(
                    cmd,
                    capture_output=True,
                    timeout=60
                )

            if result.returncode != 0:
                logger.warning(f"fpcalc failed: {result.stderr.decode()}")
                return None

            # Parse JSON output
            data = json.loads(result.stdout.decode())

            return AudioFingerprint(
                fingerprint=data.get('fingerprint', ''),
                duration=data.get('duration', duration or 0)
            )

        except subprocess.TimeoutExpired:
            logger.error("Fingerprint generation timed out")
            return None
        except Exception as e:
            logger.error(f"Fingerprint generation failed: {e}")
            return None

    def compare_fingerprints(
        self,
        fp1: str,
        fp2: str
    ) -> float:
        """
        Compare two fingerprints and return similarity score.

        Uses bit error rate comparison on the raw fingerprint data.

        Args:
            fp1: First fingerprint string
            fp2: Second fingerprint string

        Returns:
            Similarity score between 0 and 1
        """
        try:
            import acoustid

            # Decode fingerprints to integer arrays
            fp1_decoded = acoustid.chromaprint.decode_fingerprint(fp1)
            fp2_decoded = acoustid.chromaprint.decode_fingerprint(fp2)

            if not fp1_decoded or not fp2_decoded:
                return 0.0

            fp1_array = fp1_decoded[0]
            fp2_array = fp2_decoded[0]

            # Compare using bit error rate
            return self._calculate_similarity(fp1_array, fp2_array)

        except ImportError:
            logger.warning("acoustid module not available for fingerprint comparison")
            return 0.0
        except Exception as e:
            logger.error(f"Fingerprint comparison failed: {e}")
            return 0.0

    def _calculate_similarity(
        self,
        fp1: List[int],
        fp2: List[int]
    ) -> float:
        """
        Calculate similarity between two fingerprint arrays using bit error rate.

        Args:
            fp1: First fingerprint array
            fp2: Second fingerprint array

        Returns:
            Similarity score between 0 and 1
        """
        if not fp1 or not fp2:
            return 0.0

        # Use the shorter length for comparison
        min_len = min(len(fp1), len(fp2))
        if min_len == 0:
            return 0.0

        # Count matching bits
        total_bits = 0
        matching_bits = 0

        for i in range(min_len):
            xor = fp1[i] ^ fp2[i]
            # Count differing bits
            diff_bits = bin(xor).count('1')
            matching_bits += 32 - diff_bits  # 32 bits per int
            total_bits += 32

        return matching_bits / total_bits if total_bits > 0 else 0.0

    def find_matches(
        self,
        audio_path: str,
        known_fingerprints: List[Tuple[int, str, float, str]] = None
    ) -> List[FingerprintMatch]:
        """
        Search for known ad fingerprints in an audio file.

        Uses a sliding window approach to find matches at any position.

        Args:
            audio_path: Path to audio file to search
            known_fingerprints: List of (pattern_id, fingerprint, duration, sponsor)
                               If None, loads from database

        Returns:
            List of FingerprintMatch objects for found ads
        """
        if not self.is_available():
            return []

        # Load known fingerprints from database if not provided
        if known_fingerprints is None and self.db:
            known_fingerprints = self._load_fingerprints_from_db()

        if not known_fingerprints:
            return []

        matches = []

        # Get total duration of audio
        total_duration = self._get_audio_duration(audio_path)
        if total_duration <= 0:
            return []

        logger.info(f"Searching {total_duration:.1f}s audio for {len(known_fingerprints)} known fingerprints")

        # Slide through audio looking for matches
        position = 0.0
        while position < total_duration - MIN_SEGMENT_DURATION:
            # Generate fingerprint for current window
            chunk_fp = self.generate_fingerprint(
                audio_path,
                start=position,
                duration=FINGERPRINT_CHUNK_SIZE
            )

            if chunk_fp and chunk_fp.fingerprint:
                # Compare against known fingerprints
                for pattern_id, known_fp, known_duration, sponsor in known_fingerprints:
                    similarity = self.compare_fingerprints(
                        chunk_fp.fingerprint,
                        known_fp
                    )

                    if similarity >= MATCH_THRESHOLD:
                        # Found a match
                        match = FingerprintMatch(
                            pattern_id=pattern_id,
                            start=position,
                            end=position + known_duration,
                            confidence=similarity,
                            sponsor=sponsor
                        )
                        matches.append(match)
                        logger.info(
                            f"Fingerprint match: pattern={pattern_id} "
                            f"at {position:.1f}s (confidence={similarity:.2f})"
                        )
                        # Skip ahead past this match
                        position += known_duration
                        break
                else:
                    position += SLIDING_STEP_SIZE
            else:
                position += SLIDING_STEP_SIZE

        # Merge overlapping matches
        matches = self._merge_overlapping_matches(matches)

        return matches

    def _load_fingerprints_from_db(self) -> List[Tuple[int, str, float, str]]:
        """Load known fingerprints from database."""
        if not self.db:
            return []

        try:
            fingerprints = self.db.get_all_audio_fingerprints()
            result = []
            for fp in fingerprints:
                # Get pattern to find sponsor
                pattern = self.db.get_ad_pattern_by_id(fp['pattern_id'])
                sponsor = pattern.get('sponsor') if pattern else None

                # Fingerprint may be stored as bytes or string
                fp_data = fp.get('fingerprint', b'')
                if isinstance(fp_data, bytes):
                    fp_str = fp_data.decode('utf-8', errors='ignore')
                else:
                    fp_str = str(fp_data)

                result.append((
                    fp['pattern_id'],
                    fp_str,
                    fp['duration'],
                    sponsor
                ))
            return result
        except Exception as e:
            logger.error(f"Failed to load fingerprints from database: {e}")
            return []

    def _get_audio_duration(self, audio_path: str) -> float:
        """Get duration of audio file in seconds.

        Delegates to utils.audio.get_audio_duration for consistent implementation.
        """
        duration = _utils_get_audio_duration(audio_path)
        return duration if duration is not None else 0.0

    def _merge_overlapping_matches(
        self,
        matches: List[FingerprintMatch]
    ) -> List[FingerprintMatch]:
        """Merge overlapping fingerprint matches."""
        if not matches:
            return []

        # Sort by start time
        matches.sort(key=lambda m: m.start)

        merged = []
        current = matches[0]

        for match in matches[1:]:
            # Check for overlap
            if match.start <= current.end + 1.0:  # 1s tolerance
                # Extend current match
                current = FingerprintMatch(
                    pattern_id=current.pattern_id,
                    start=current.start,
                    end=max(current.end, match.end),
                    confidence=max(current.confidence, match.confidence),
                    sponsor=current.sponsor or match.sponsor
                )
            else:
                merged.append(current)
                current = match

        merged.append(current)
        return merged

    def store_fingerprint(
        self,
        pattern_id: int,
        audio_path: str,
        start: float,
        end: float
    ) -> bool:
        """
        Generate and store a fingerprint for a detected ad segment.

        Args:
            pattern_id: ID of the ad pattern
            audio_path: Path to the episode audio
            start: Start time of the ad
            end: End time of the ad

        Returns:
            True if fingerprint was stored successfully
        """
        if not self.db or not self.is_available():
            return False

        duration = end - start
        if duration < MIN_SEGMENT_DURATION:
            logger.debug(f"Segment too short for fingerprinting: {duration:.1f}s")
            return False

        fingerprint = self.generate_fingerprint(audio_path, start, duration)
        if not fingerprint or not fingerprint.fingerprint:
            return False

        try:
            # Store fingerprint as bytes
            fp_bytes = fingerprint.fingerprint.encode('utf-8')
            self.db.create_audio_fingerprint(
                pattern_id=pattern_id,
                fingerprint=fp_bytes,
                duration=duration
            )
            logger.info(f"Stored fingerprint for pattern {pattern_id}")
            return True
        except Exception as e:
            logger.error(f"Failed to store fingerprint: {e}")
            return False
