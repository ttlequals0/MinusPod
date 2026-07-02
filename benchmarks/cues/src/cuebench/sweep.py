"""Sweep templates across episode audio and produce per-template score histograms.

Mirrors _run_cue_threshold_scan from api/cue_templates.py (lines 1001-1037).
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Dict, List, Any, Optional

import numpy as np

from audio_analysis.cue_template_matcher import AudioCueTemplateMatcher
from audio_analysis.cue_threshold_suggest import suggest_cue_threshold
from config import AUDIO_CUE_SUGGEST_FLOOR, AUDIO_CUE_EFFECT_FLOOR

logger = logging.getLogger("cuebench.sweep")

# Histogram bins: 0.35-1.0 in 0.05 steps
_HIST_BINS = np.arange(0.35, 1.05, 0.05)
# Threshold table range: 0.50-0.95
_THRESH_RANGE = np.arange(0.50, 1.00, 0.05)


def _build_histogram(scores: List[float]) -> Dict[str, int]:
    """Return count per bin label for scores in [0.35, 1.0)."""
    counts: Dict[str, int] = {}
    edges = list(_HIST_BINS) + [1.01]
    for i in range(len(edges) - 1):
        label = f"{edges[i]:.2f}"
        counts[label] = 0
    for s in scores:
        for i in range(len(edges) - 1):
            if edges[i] <= s < edges[i + 1]:
                counts[f"{edges[i]:.2f}"] += 1
                break
    return counts


def _build_threshold_table(scores: List[float]) -> List[Dict[str, Any]]:
    """For each threshold t in 0.50-0.95 report count of scores >= t."""
    rows = []
    for t in _THRESH_RANGE:
        t = round(float(t), 2)
        count = sum(1 for s in scores if s >= t)
        rows.append({"threshold": t, "matches": count})
    return rows


def run(
    template_rows: List[Dict[str, Any]],
    audio_paths: List[Path],
    formant_ab: bool = False,
    confirm: bool = False,
    effect_floor: float = AUDIO_CUE_EFFECT_FLOOR,
) -> Dict[str, Any]:
    """Sweep *template_rows* across every file in *audio_paths*.

    Parameters
    ----------
    template_rows:
        Dicts compatible with AudioCueTemplateMatcher (id, label, cue_type,
        duration_s, n_coeffs, mfcc_blob, pcm_blob).
    audio_paths:
        Local paths to episode audio files.
    formant_ab:
        When True run side-by-side at 0.0 dB and 12.0 dB formant attenuation
        and include both result sets.
    confirm:
        When True re-run detect at the suggested threshold and report true
        match counts.
    effect_floor:
        Passed verbatim to suggest_cue_threshold.

    Returns a dict:
      scores: list[float]           -- all per-occurrence scores collected
      per_template: dict            -- keyed by template id
        histogram: dict[str, int]
        threshold_table: list[dict]
        peak_score: float
        suggestion: dict
      formant_ab: dict | None       -- present when formant_ab=True
      confirm_counts: dict | None   -- present when confirm=True
      floor_used: float
      episodes_scanned: int
    """
    profiles: Dict[str, float] = {"0.0dB": 0.0}
    if formant_ab:
        profiles["12.0dB"] = 12.0

    results_by_profile: Dict[str, Dict] = {}
    for profile_name, atten_db in profiles.items():
        results_by_profile[profile_name] = _sweep_one_profile(
            template_rows, audio_paths, atten_db, effect_floor
        )

    main = results_by_profile["0.0dB"]

    out: Dict[str, Any] = {
        "scores": main["scores"],
        "per_template": main["per_template"],
        "floor_used": AUDIO_CUE_SUGGEST_FLOOR,
        "episodes_scanned": len(audio_paths),
        "formant_ab": None,
        "confirm_counts": None,
    }

    if formant_ab:
        out["formant_ab"] = {
            "0.0dB": results_by_profile["0.0dB"]["per_template"],
            "12.0dB": results_by_profile["12.0dB"]["per_template"],
        }

    if confirm:
        out["confirm_counts"] = _run_confirm(
            template_rows, audio_paths, main["per_template"]
        )

    return out


def _sweep_one_profile(
    template_rows: List[Dict],
    audio_paths: List[Path],
    atten_db: float,
    effect_floor: float,
) -> Dict[str, Any]:
    matcher = AudioCueTemplateMatcher(
        template_rows,
        score_threshold=AUDIO_CUE_SUGGEST_FLOOR,
        max_matches_per_template=200,
        formant_atten_db=atten_db,
    )
    if not matcher.is_usable:
        raise RuntimeError("no usable templates after loading")

    all_scores: List[float] = []
    # peak_score and raw scores per template id
    per_id_scores: Dict[int, List[float]] = {
        row["id"]: [] for row in template_rows
    }
    per_id_peak: Dict[int, float] = {row["id"]: 0.0 for row in template_rows}
    # per-episode match positions: per_id_episodes[tid][episode_path] = [{start,end,score}]
    per_id_episodes: Dict[int, Dict[str, List[Dict[str, float]]]] = {
        row["id"]: {} for row in template_rows
    }

    for path in audio_paths:
        path_str = str(path)
        logger.info("scanning %s (atten=%.1f dB)", path, atten_db)
        signals, debug = matcher.detect_with_debug(path_str)
        for sig in signals:
            details = sig.details or {}
            score = details.get("score", sig.confidence)
            tid = details.get("template_id")
            if tid not in per_id_scores:
                logger.warning(
                    "signal has unknown template_id %r; skipping from all_scores"
                    " and per-template to keep aggregations consistent",
                    tid,
                )
                continue
            all_scores.append(score)
            per_id_scores[tid].append(score)
            episode_matches = per_id_episodes[tid].setdefault(path_str, [])
            episode_matches.append({
                "start": float(sig.start),
                "end": float(sig.end),
                "score": float(score),
            })
        for t in debug.get("templates", []):
            tid = t["id"]
            if tid in per_id_peak:
                per_id_peak[tid] = max(per_id_peak[tid], t.get("peak_score", 0.0))

    per_template: Dict[str, Any] = {}
    for row in template_rows:
        tid = row["id"]
        scores = per_id_scores.get(tid, [])
        per_template[str(tid)] = {
            "label": row["label"],
            "cue_type": row["cue_type"],
            "duration_s": row["duration_s"],
            "scores": scores,
            "episodes": per_id_episodes.get(tid, {}),
            "histogram": _build_histogram(scores),
            "threshold_table": _build_threshold_table(scores),
            "peak_score": round(per_id_peak.get(tid, 0.0), 3),
            "suggestion": suggest_cue_threshold(scores, effect_floor=effect_floor),
        }

    return {"scores": all_scores, "per_template": per_template}


def _run_confirm(
    template_rows: List[Dict],
    audio_paths: List[Path],
    per_template: Dict,
) -> Dict[str, Any]:
    """Re-run detect at each template's suggested threshold; return true counts."""
    counts: Dict[str, int] = {}
    for row in template_rows:
        tid_str = str(row["id"])
        info = per_template.get(tid_str, {})
        suggestion = info.get("suggestion", {})
        threshold = suggestion.get("suggested")
        if threshold is None:
            counts[tid_str] = 0
            continue
        matcher = AudioCueTemplateMatcher(
            [row],
            score_threshold=threshold,
            max_matches_per_template=200,
        )
        n = 0
        for path in audio_paths:
            signals, _ = matcher.detect_with_debug(str(path))
            n += len(signals)
        counts[tid_str] = n
    return counts
