"""
Audio signal formatter for ad detection prompts.

Formats audio analysis signals (DAI transitions, volume anomalies) as text
context for Claude's per-window ad detection prompts. Claude sees the audio
evidence alongside the transcript and makes all ad/not-ad decisions itself.
"""

import logging
from typing import Optional

logger = logging.getLogger('podcast.audio_enforcer')

# Only include signals above this confidence in the prompt
MIN_SIGNAL_CONFIDENCE = 0.80


class AudioEnforcer:
    """
    Formats audio analysis signals as prompt context for Claude.

    Converts DAI transition pairs and volume anomalies overlapping a given
    time window into a human-readable text block that gets injected into
    Claude's per-window prompt. No ad creation -- Claude decides.
    """

    def format_for_window(self, audio_analysis, window_start: float,
                          window_end: float) -> str:
        """Format audio signals overlapping a window as prompt context.

        Args:
            audio_analysis: AudioAnalysisResult (or None)
            window_start: Window start time in seconds
            window_end: Window end time in seconds

        Returns:
            Formatted string for prompt injection, or empty string if no signals
        """
        if not audio_analysis or not audio_analysis.signals:
            return ""

        lines = []

        for signal in audio_analysis.signals:
            # Skip low-confidence signals
            if signal.confidence < MIN_SIGNAL_CONFIDENCE:
                continue

            # Skip signals outside this window
            if signal.end <= window_start or signal.start >= window_end:
                continue

            if signal.signal_type == 'dai_transition_pair':
                details = signal.details or {}
                avg_db = details.get('avg_delta_db', 0)
                lines.append(
                    f"- DAI transition pair at {signal.start:.1f}s-{signal.end:.1f}s "
                    f"(avg {avg_db:.1f} dB jump, confidence {signal.confidence:.0%})"
                )
            elif signal.signal_type in ('volume_increase', 'volume_decrease'):
                lines.append(
                    f"- Volume anomaly at {signal.start:.1f}s-{signal.end:.1f}s "
                    f"({signal.signal_type}, confidence {signal.confidence:.0%})"
                )

        if not lines:
            return ""

        header = (
            "\n=== AUDIO SIGNALS ===\n"
            "The following audio signals were detected in this window. "
            "These are SUPPORTING EVIDENCE ONLY. They may indicate ad boundaries "
            "but do NOT constitute ads by themselves. You MUST find promotional "
            "content in the transcript (sponsor names, URLs, promo codes, product "
            "pitches) to flag an ad. Silence gaps, volume changes, or transitions "
            "with no promotional transcript content are NOT ads.\n"
        )

        return header + "\n".join(lines) + "\n"
