"""
Transition detection for dynamically inserted ads (DAI).

Detects abrupt frame-to-frame loudness jumps and pairs them into
candidate ad regions. Reuses volume analyzer frames - zero extra I/O cost.
"""

import logging
from dataclasses import dataclass
from typing import List

from .base import AudioSegmentSignal, LoudnessFrame, SignalType

logger = logging.getLogger('podcast.audio_analysis.transition')


@dataclass
class TransitionPoint:
    """An abrupt loudness transition at a specific time."""
    time: float
    delta_db: float
    direction: str  # 'up' or 'down'
    from_lufs: float
    to_lufs: float


@dataclass
class TransitionPair:
    """A paired up/down (or down/up) transition bounding a candidate ad region."""
    start_transition: TransitionPoint
    end_transition: TransitionPoint
    duration: float
    avg_delta_db: float
    confidence: float


class TransitionDetector:
    """
    Detects abrupt loudness transitions that indicate DAI ad boundaries.

    DAI ads are typically mastered independently from the podcast content,
    causing measurable loudness jumps at insertion points. This detector
    finds those jumps and pairs them into candidate ad regions.
    """

    def __init__(
        self,
        transition_threshold_db: float = 3.5,
        min_ad_duration: float = 15.0,
        max_ad_duration: float = 180.0,
    ):
        """
        Args:
            transition_threshold_db: Minimum dB jump between adjacent frames to flag
            min_ad_duration: Minimum seconds for a valid ad region
            max_ad_duration: Maximum seconds for a valid ad region
        """
        self.transition_threshold_db = transition_threshold_db
        self.min_ad_duration = min_ad_duration
        self.max_ad_duration = max_ad_duration

    def detect_transitions(self, frames: List[LoudnessFrame]) -> List[TransitionPoint]:
        """Find abrupt loudness transitions between adjacent frames."""
        if len(frames) < 2:
            return []

        transitions = []
        for i in range(1, len(frames)):
            prev = frames[i - 1]
            curr = frames[i]

            # Skip silence frames
            if prev.loudness_lufs < -70 or curr.loudness_lufs < -70:
                continue

            delta = curr.loudness_lufs - prev.loudness_lufs

            if abs(delta) >= self.transition_threshold_db:
                direction = 'up' if delta > 0 else 'down'
                transitions.append(TransitionPoint(
                    time=curr.start,
                    delta_db=abs(delta),
                    direction=direction,
                    from_lufs=prev.loudness_lufs,
                    to_lufs=curr.loudness_lufs,
                ))

        logger.debug(f"Found {len(transitions)} raw transitions "
                    f"(threshold={self.transition_threshold_db}dB)")
        return transitions

    def find_transition_pairs(
        self, transitions: List[TransitionPoint]
    ) -> List[TransitionPair]:
        """
        Pair transitions into candidate ad regions.

        Looks for up->down or down->up pairs within the ad duration window.
        Each transition can only be used in one pair (greedy matching).
        """
        if len(transitions) < 2:
            return []

        pairs = []
        used = set()

        for i, start_t in enumerate(transitions):
            if i in used:
                continue

            # Look for a complementary transition after this one
            for j in range(i + 1, len(transitions)):
                if j in used:
                    continue

                end_t = transitions[j]
                duration = end_t.time - start_t.time

                # Must be opposite direction (up then down, or down then up)
                if start_t.direction == end_t.direction:
                    continue

                # Duration must be in valid ad range
                if duration < self.min_ad_duration:
                    continue
                if duration > self.max_ad_duration:
                    break  # No point looking further for this start

                avg_delta = (start_t.delta_db + end_t.delta_db) / 2.0

                # Confidence based on average transition magnitude
                # 3.5dB = 0.5, 7dB = 0.75, 10dB+ = 0.9
                confidence = min(0.3 + (avg_delta / 15.0), 0.95)

                pairs.append(TransitionPair(
                    start_transition=start_t,
                    end_transition=end_t,
                    duration=duration,
                    avg_delta_db=avg_delta,
                    confidence=confidence,
                ))

                used.add(i)
                used.add(j)
                break  # Move to next start transition

        logger.debug(f"Paired {len(pairs)} transition pairs")
        return pairs

    def pairs_to_signals(self, pairs: List[TransitionPair]) -> List[AudioSegmentSignal]:
        """Convert transition pairs to AudioSegmentSignal objects."""
        signals = []
        for pair in pairs:
            signals.append(AudioSegmentSignal(
                start=pair.start_transition.time,
                end=pair.end_transition.time,
                signal_type=SignalType.DAI_TRANSITION_PAIR.value,
                confidence=pair.confidence,
                details={
                    'avg_delta_db': round(pair.avg_delta_db, 1),
                    'start_direction': pair.start_transition.direction,
                    'start_delta_db': round(pair.start_transition.delta_db, 1),
                    'end_delta_db': round(pair.end_transition.delta_db, 1),
                    'start_from_lufs': round(pair.start_transition.from_lufs, 1),
                    'start_to_lufs': round(pair.start_transition.to_lufs, 1),
                    'end_from_lufs': round(pair.end_transition.from_lufs, 1),
                    'end_to_lufs': round(pair.end_transition.to_lufs, 1),
                }
            ))
        return signals

    def detect_and_pair(self, frames: List[LoudnessFrame]) -> List[AudioSegmentSignal]:
        """Full pipeline: detect transitions, pair them, return signals."""
        transitions = self.detect_transitions(frames)
        pairs = self.find_transition_pairs(transitions)
        signals = self.pairs_to_signals(pairs)

        if signals:
            logger.info(f"Transition detection: {len(signals)} DAI candidate regions")
        return signals
