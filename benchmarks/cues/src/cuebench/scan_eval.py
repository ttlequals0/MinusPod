"""Evaluate Chromaprint-based recurring-sound discovery against sweep ground truth."""
from __future__ import annotations

import logging
import shutil
from pathlib import Path
from typing import Dict, List, Any, Optional

from config import (
    AUDIO_CUE_RECURRENCE_SIMILARITY,
    AUDIO_CUE_RECURRENCE_MIN_COUNT,
    AUDIO_CUE_TEMPLATE_SCORE,
)

try:
    from audio_fingerprinter import AudioFingerprinter
except ImportError:
    AudioFingerprinter = None

logger = logging.getLogger("cuebench.scan_eval")


def run(
    audio_paths: List[Path],
    sweep_per_template: Dict[str, Any],
    template_durations: Dict[str, float],
) -> Dict[str, Any]:
    """Run AudioFingerprinter discovery on *audio_paths* and compare with sweep.

    Skips cleanly (returns skip_reason) when fpcalc is unavailable.

    Ground truth per episode: sweep matches for that episode whose score meets
    the template's suggested threshold (when confident) or AUDIO_CUE_TEMPLATE_SCORE
    as a fallback. A discovery candidate matches a ground-truth occurrence when
    the occurrence midpoint falls inside the candidate span.

    Returns a dict:
      available: bool
      skip_reason: str | None
      results: list[dict]  -- one per episode
    """
    if AudioFingerprinter is None:
        return {
            "available": False,
            "skip_reason": "could not import AudioFingerprinter",
            "results": [],
        }

    fp = AudioFingerprinter(db=None)
    if not fp.is_available():
        if shutil.which("fpcalc") is None:
            reason = "fpcalc not found on PATH"
        else:
            reason = "AudioFingerprinter reports unavailable"
        return {"available": False, "skip_reason": reason, "results": []}

    episode_results = []
    for path in audio_paths:
        candidates = fp.discover_recurring_spots(
            str(path),
            similarity=AUDIO_CUE_RECURRENCE_SIMILARITY,
            min_count=AUDIO_CUE_RECURRENCE_MIN_COUNT,
        )
        episode_results.append(
            _eval_episode(path, candidates, sweep_per_template, template_durations)
        )

    return {"available": True, "skip_reason": None, "results": episode_results}


def _threshold_for(info: Dict[str, Any]) -> float:
    """Return the effective score threshold for ground-truth selection.

    Uses the sweep suggestion when confidence is 'high', otherwise falls back
    to AUDIO_CUE_TEMPLATE_SCORE.
    """
    suggestion = info.get("suggestion", {})
    if suggestion.get("confidence") == "high" and suggestion.get("suggested") is not None:
        return float(suggestion["suggested"])
    return AUDIO_CUE_TEMPLATE_SCORE


def _ground_truth_for_episode(
    path: Path,
    info: Dict[str, Any],
    threshold: float,
) -> List[Dict[str, float]]:
    """Return sweep matches for *path* that meet *threshold*."""
    path_str = str(path)
    episodes: Dict[str, List[Dict[str, float]]] = info.get("episodes", {})
    return [m for m in episodes.get(path_str, []) if m.get("score", 0.0) >= threshold]


def _eval_episode(
    path: Path,
    candidates: List[Dict],
    sweep_per_template: Dict[str, Any],
    template_durations: Dict[str, float],
) -> Dict[str, Any]:
    per_template = {}
    for tid_str, info in sweep_per_template.items():
        tpl_dur = template_durations.get(tid_str, info.get("duration_s", 0.0))
        label = info.get("label", tid_str)
        threshold = _threshold_for(info)
        occurrences = _ground_truth_for_episode(path, info, threshold)
        ground_truth_count = len(occurrences)

        found = False
        rank: Optional[int] = None
        span_accuracy: Optional[float] = None
        matched_occurrences = 0

        for i, cand in enumerate(candidates):
            start = float(cand.get("start", 0.0))
            end = float(cand.get("end", 0.0))
            # Check if any occurrence midpoint falls inside this candidate span.
            matching = [
                occ for occ in occurrences
                if start <= (occ["start"] + occ["end"]) / 2.0 < end
            ]
            if matching:
                if not found:
                    found = True
                    rank = i + 1
                    span = end - start
                    span_accuracy = round(span / max(tpl_dur, 1e-6), 3)
                matched_occurrences += len(matching)

        per_template[tid_str] = {
            "label": label,
            "ground_truth_count": ground_truth_count,
            "threshold_used": threshold,
            "found": found,
            "rank": rank,
            "span_accuracy": span_accuracy,
            "matched_occurrences": matched_occurrences,
            "candidates_total": len(candidates),
        }

    return {"episode": str(path), "per_template": per_template}
