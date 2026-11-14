"""Per-episode immutable context passed through the ad-detection pipeline.

Bundling the identifiers and metadata strings in a frozen dataclass keeps
helper signatures short and prevents mutation of shared state.

Mutable plumbing (progress_callback, cancel_event, audio_analysis, audio_path,
skip_patterns, segments) stays as separate arguments because it varies per
stage or carries side effects.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class EpisodeContext:
    """Immutable identifiers + metadata for a single episode being processed."""

    slug: str
    episode_id: str
    podcast_name: str = "Unknown"
    episode_title: str = "Unknown"
    podcast_id: str | None = None
    podcast_description: str | None = None
    detection_notes: str | None = None
    episode_description: str | None = None
    podcast_tags: set[str] | None = None
