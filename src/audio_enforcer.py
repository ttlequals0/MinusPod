"""
Audio signal formatter for ad detection prompts.

Formats audio analysis signals (DAI transitions, volume anomalies) as text
context for Claude's per-window ad detection prompts. Claude sees the audio
evidence alongside the transcript and makes all ad/not-ad decisions itself.
"""

import logging
from typing import Optional

from config import (
    AUDIO_CUE_ROLE_DEFAULT,
    AUDIO_CUE_ROLE_NON_AD,
    AUDIO_CUE_SOURCE_SPECTRAL,
    AUDIO_CUE_SOURCE_TEMPLATE,
    AUDIO_CUE_TYPE_SHOW_INTRO,
    AUDIO_CUE_TYPE_SHOW_OUTRO,
)

logger = logging.getLogger('podcast.audio_enforcer')

# Only include signals above this confidence in the prompt
MIN_SIGNAL_CONFIDENCE = 0.80


def content_anchors(audio_analysis):
    """Return ``(pre_roll_boundary, post_roll_boundary)`` in seconds (#350).

    ``pre_roll_boundary`` is the start of the earliest show-intro cue (the show
    begins there, so audio before it is likely a pre-roll ad).
    ``post_roll_boundary`` is the end of the latest show-outro cue (the show
    ends there, so audio after it is likely a post-roll ad). Either is ``None``
    when no qualifying cue exists. Only cues at/above the prompt confidence
    floor count; these anchors bias the model, they never cut on their own.
    """
    if not audio_analysis or not getattr(audio_analysis, 'signals', None):
        return None, None
    pre = None
    post = None
    for s in audio_analysis.get_signals_by_type('audio_cue'):
        if s.confidence < MIN_SIGNAL_CONFIDENCE:
            continue
        cue_type = (s.details or {}).get('cue_type')
        if cue_type == AUDIO_CUE_TYPE_SHOW_INTRO:
            pre = s.start if pre is None else min(pre, s.start)
        elif cue_type == AUDIO_CUE_TYPE_SHOW_OUTRO:
            post = s.end if post is None else max(post, s.end)
    return pre, post


def _positional_guidance(window_start, window_end, pre_roll_boundary,
                         post_roll_boundary):
    """Pre/post-roll bias for a window that sits outside the content span."""
    notes = []
    if pre_roll_boundary is not None and window_start < pre_roll_boundary:
        notes.append(
            f"The show's intro marker is at {pre_roll_boundary:.0f}s; audio in "
            f"this window BEFORE that time is pre-roll, where promotional content "
            f"is more likely an ad (the show has not started yet)."
        )
    if post_roll_boundary is not None and window_end > post_roll_boundary:
        notes.append(
            f"The show's outro marker is at {post_roll_boundary:.0f}s; audio in "
            f"this window AFTER that time is post-roll, where promotional content "
            f"is more likely an ad (the show has ended)."
        )
    if not notes:
        return ""
    return (
        "\n=== POSITION ===\n"
        + " ".join(notes)
        + " This is a bias only -- you must still find promotional copy in the "
        "transcript to flag an ad.\n"
    )


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
        has_ad_cue = False
        has_non_ad_cue = False

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
                # A short non-spoken ding/stinger that some shows play around an
                # ad break. Ad-break-typed cues set an ad's edge when the nearby
                # transcript is promotional; intro/outro cues (role 'non_ad')
                # are the show's own open/close and must NOT move an ad boundary.
                details = signal.details or {}
                label = details.get('label')
                source = details.get('source', AUDIO_CUE_SOURCE_SPECTRAL)
                role = details.get('role', AUDIO_CUE_ROLE_DEFAULT)
                if role == AUDIO_CUE_ROLE_NON_AD:
                    descriptor = f'"{label}" marker' if label else 'Show intro/outro marker'
                    suffix = "marks the show's open/close, NOT an ad boundary"
                    has_non_ad_cue = True
                else:
                    descriptor = f'"{label}" cue' if (source == AUDIO_CUE_SOURCE_TEMPLATE and label) else 'Audio cue (ding/stinger)'
                    suffix = 'often just before an ad break'
                    has_ad_cue = True
                lines.append(
                    f"- {descriptor} at {signal.start:.1f}s "
                    f"({suffix}, confidence {signal.confidence:.0%})"
                )

        pre_roll_boundary, post_roll_boundary = content_anchors(audio_analysis)
        positional_guidance = _positional_guidance(
            window_start, window_end, pre_roll_boundary, post_roll_boundary)

        if not lines:
            # No in-window signals, but a pre/post-roll position bias may apply.
            return positional_guidance

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
        ) if has_ad_cue else ""

        # Intro/outro markers steer the model away from a false positive: the
        # show's own open/close sound is not a break boundary.
        non_ad_guidance = (
            "\nSHOW INTRO/OUTRO MARKERS: a marker above is the show's own opening or closing "
            "sound, not an ad cue. Do NOT treat it as an ad boundary or start or extend an ad "
            "at it.\n"
        ) if has_non_ad_cue else ""

        return (header + "\n".join(lines) + "\n"
                + cue_guidance + non_ad_guidance + positional_guidance)
