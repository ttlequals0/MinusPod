"""Per-episode immutable context passed through the ad-detection pipeline.

The processing pipeline (download -> transcribe -> detect -> verify -> review)
threads the same handful of identifiers and metadata strings through many helper
calls. Bundling them into a frozen dataclass keeps signatures short and prevents
the helpers from accidentally mutating shared state.

This holds ONLY the immutable per-episode context (ids, names, descriptions,
tags). Mutable plumbing -- progress_callback, cancel_event, audio_analysis,
audio_path, skip_patterns, segments -- stays as separate arguments because it
either varies stage to stage or carries side effects.
"""

from dataclasses import dataclass
from typing import Optional, Set


@dataclass(frozen=True)
class EpisodeContext:
    """Immutable identifiers + metadata for a single episode being processed."""

    slug: str
    episode_id: str
    podcast_name: str = "Unknown"
    episode_title: str = "Unknown"
    podcast_id: Optional[str] = None
    podcast_description: Optional[str] = None
    episode_description: Optional[str] = None
    podcast_tags: Optional[Set[str]] = None
