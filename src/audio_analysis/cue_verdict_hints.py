"""Per-template verdict hints: what a template's rejections say to do.

Rejections clustered just above the feed's threshold mean the threshold is
too loose (raise it); rejections spread across the score range or overlapping
confirmed scores mean the capture itself matches the wrong audio (re-capture).
Rejections at or below the current threshold are ignored: they no longer match
today, so stale labels recorded under an older, lower threshold cannot keep a
hint alive. Pure -- no Flask/DB imports -- mirroring cue_threshold_suggest.
"""

from config import AUDIO_CUE_HINT_MIN_REJECTIONS, AUDIO_CUE_HINT_NEAR_BAND

HINT_RAISE_THRESHOLD = 'raise_threshold'
HINT_RECAPTURE = 'recapture'


def template_verdict_hint(
    rejected_scores: list[float | None],
    confirmed_scores: list[float | None],
    current_threshold: float,
) -> str | None:
    """Classify one template's rejection pattern; None below the count gate."""
    rejected = [s for s in (rejected_scores or [])
                if s is not None and s > current_threshold]
    if len(rejected) < AUDIO_CUE_HINT_MIN_REJECTIONS:
        return None
    confirmed = [s for s in (confirmed_scores or []) if s is not None]
    clustered = all(
        s <= current_threshold + AUDIO_CUE_HINT_NEAR_BAND for s in rejected)
    below_confirmed = not confirmed or max(rejected) < min(confirmed)
    if clustered and below_confirmed:
        return HINT_RAISE_THRESHOLD
    return HINT_RECAPTURE
