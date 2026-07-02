"""Cross-episode intro/outro discovery via AudioFingerprinter."""
from __future__ import annotations

import logging
import shutil
from pathlib import Path
from typing import Any, Dict, List

from config import (
    AUDIO_CUE_XEP_HEAD_SECONDS,
    AUDIO_CUE_XEP_TAIL_SECONDS,
    AUDIO_CUE_XEP_SIMILARITY,
    AUDIO_CUE_XEP_MIN_MATCHES,
    AUDIO_CUE_XEP_MIN_DURATION,
    AUDIO_CUE_XEP_MAX_PER_ZONE,
    AUDIO_CUE_FP_WINDOW_SECONDS,
)

try:
    from audio_fingerprinter import AudioFingerprinter
except ImportError:
    AudioFingerprinter = None

logger = logging.getLogger("cuebench.cross_episode_eval")


def run(
    audio_paths: List[Path],
    intro_max_duration: float = 60.0,
    outro_max_duration: float = 60.0,
) -> Dict[str, Any]:
    """Run cross-episode intro/outro discovery on *audio_paths*.

    Returns a dict:
      available: bool
      skip_reason: str | None
      episodes: list[dict]  -- per-episode candidates
      summary: dict         -- aggregated stats across the run
    """
    if AudioFingerprinter is None:
        return {
            "available": False,
            "skip_reason": "could not import AudioFingerprinter",
            "episodes": [],
            "summary": {},
        }

    fp = AudioFingerprinter(db=None)
    if not fp.is_available():
        reason = (
            "fpcalc not found on PATH"
            if shutil.which("fpcalc") is None
            else "AudioFingerprinter reports unavailable"
        )
        return {"available": False, "skip_reason": reason, "episodes": [], "summary": {}}

    if len(audio_paths) < 2:
        return {
            "available": True,
            "skip_reason": "fewer than 2 episodes -- cross-episode comparison requires >= 2 siblings",
            "episodes": [],
            "summary": {},
        }

    episode_results = []
    for i, target in enumerate(audio_paths):
        # All episodes except the current one are siblings
        siblings = [p for j, p in enumerate(audio_paths) if j != i]
        candidates = fp.discover_cross_episode_cues(
            str(target),
            [str(s) for s in siblings],
            head_seconds=AUDIO_CUE_XEP_HEAD_SECONDS,
            tail_seconds=AUDIO_CUE_XEP_TAIL_SECONDS,
            window_seconds=AUDIO_CUE_FP_WINDOW_SECONDS,
            similarity=AUDIO_CUE_XEP_SIMILARITY,
            min_matches=AUDIO_CUE_XEP_MIN_MATCHES,
            min_duration=AUDIO_CUE_XEP_MIN_DURATION,
            intro_max_duration=intro_max_duration,
            outro_max_duration=outro_max_duration,
            max_per_zone=AUDIO_CUE_XEP_MAX_PER_ZONE,
        )
        episode_results.append({
            "episode": str(target),
            "candidates": [
                {
                    "kind": c["kind"],
                    "start": c["start"],
                    "end": c["end"],
                    "duration": round(c["end"] - c["start"], 2),
                    "episode_matches": c.get("episodeMatches", 0),
                }
                for c in candidates
            ],
        })

    summary = _summarize(episode_results)
    return {
        "available": True,
        "skip_reason": None,
        "episodes": episode_results,
        "summary": summary,
    }


def _summarize(episode_results: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Aggregate per-episode candidates into run-level stats."""
    n_total = len(episode_results)
    intro_starts: List[float] = []
    intro_ends: List[float] = []
    outro_starts: List[float] = []
    outro_ends: List[float] = []
    intro_found = 0
    outro_found = 0

    for ep in episode_results:
        has_intro = False
        has_outro = False
        for c in ep.get("candidates", []):
            if c["kind"] == "intro":
                has_intro = True
                intro_starts.append(c["start"])
                intro_ends.append(c["end"])
            elif c["kind"] == "outro":
                has_outro = True
                outro_starts.append(c["start"])
                outro_ends.append(c["end"])
        if has_intro:
            intro_found += 1
        if has_outro:
            outro_found += 1

    def _span_stats(values: List[float]) -> Dict[str, float]:
        if not values:
            return {}
        return {
            "min": round(min(values), 2),
            "max": round(max(values), 2),
            "mean": round(sum(values) / len(values), 2),
        }

    return {
        "episodes_total": n_total,
        "intro_found": intro_found,
        "outro_found": outro_found,
        "intro_start_span": _span_stats(intro_starts),
        "intro_end_span": _span_stats(intro_ends),
        "outro_start_span": _span_stats(outro_starts),
        "outro_end_span": _span_stats(outro_ends),
    }
