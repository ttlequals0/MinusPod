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
        has_cue = False

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
            elif signal.signal_type == 'audio_cue':
                # A short non-spoken ding/stinger that some shows play just
                # before an ad break. Use it to set an ad's START edge when the
                # transcript right after it is promotional. When the signal came
                # from a user-marked template the label is shown.
                details = signal.details or {}
                label = details.get('label')
                source = details.get('source', 'spectral')
                if source == 'template' and label:
                    descriptor = f'"{label}" cue'
                else:
                    descriptor = 'Audio cue (ding/stinger)'
                has_cue = True
                lines.append(
                    f"- {descriptor} at {signal.start:.1f}s "
                    f"(often just before an ad break, confidence {signal.confidence:.0%})"
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

        # When a cue actually fired in this window, inject the detailed cue
        # interpretation at runtime so it reaches every user -- including those
        # who customized their system prompt (is_default=0) and therefore do not
        # carry the static LABELLED AUDIO CUES guidance (#350).
        cue_guidance = (
            "\nLABELLED AUDIO CUES: a cue above is a recurring non-spoken sound this show plays "
            "around an ad break. A cue immediately before promotional copy marks the ad's START "
            "(begin the span at the cue, not the first spoken word); a cue immediately after the "
            "last promotional phrase marks the ad's END. Multiple cues can fire inside one break "
            "(intro stinger, mid-break bumper, outro stinger); two cues within ~30 seconds with no "
            "show content between them sit inside the same break, so do not end the ad at an "
            "intermediate cue while the transcript is still promotional -- extend to the last cue "
            "before show content resumes. The cue is never an ad on its own; it sharpens the "
            "boundary of an ad you find in the transcript.\n"
        ) if has_cue else ""

        return header + "\n".join(lines) + "\n" + cue_guidance
