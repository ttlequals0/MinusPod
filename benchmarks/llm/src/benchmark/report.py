"""Render Markdown report from calls.jsonl + episode_results.jsonl + corpus."""
from __future__ import annotations

import json
import math
import re
import statistics
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

from utils.time import utc_now_iso

from . import metrics, pricing
from .corpus import Episode
from .storage import read_jsonl

DEFAULT_IOU_THRESHOLD = 0.5

# Confidence bins used by the calibration heatmap. (lo, hi) half-open, plus a
# parallel label list. Keep these aligned.
CALIBRATION_BINS: tuple[tuple[float, float], ...] = (
    (0.0, 0.7), (0.7, 0.9), (0.9, 0.95), (0.95, 0.99), (0.99, 1.001),
)
CALIBRATION_BIN_LABELS = ("0.00-0.70", "0.70-0.90", "0.90-0.95", "0.95-0.99", "0.99+")

# Trial-to-trial F1 stdev thresholds: <STABLE green, <WOBBLY orange, else red.
F1_STDEV_STABLE = 0.02
F1_STDEV_WOBBLY = 0.05

# HTTP 5xx detector for the failures classifier. Matches "500", "502", etc.
# but not arbitrary substrings like "5 minutes".
_SERVER_5XX_RE = re.compile(r"\b5\d{2}\b")
IOU_THRESHOLDS = (0.3, 0.5, 0.7)


@dataclass
class ModelEpisodeStats:
    model: str
    episode_id: str
    trial_f1s: list[float] = field(default_factory=list)
    trial_precisions: list[float] = field(default_factory=list)
    trial_recalls: list[float] = field(default_factory=list)
    trial_tps: list[int] = field(default_factory=list)
    trial_fps: list[int] = field(default_factory=list)
    trial_fns: list[int] = field(default_factory=list)
    trial_start_maes: list[float] = field(default_factory=list)
    trial_end_maes: list[float] = field(default_factory=list)
    trial_costs: list[float] = field(default_factory=list)
    trial_response_times: list[int] = field(default_factory=list)
    no_ad_passes: list[bool] = field(default_factory=list)
    no_ad_fp_counts: list[int] = field(default_factory=list)


@dataclass
class ModelStats:
    model: str
    f1_per_episode: dict[str, float] = field(default_factory=dict)
    f1_stdev_per_episode: dict[str, float] = field(default_factory=dict)
    precision_per_episode: dict[str, float] = field(default_factory=dict)
    recall_per_episode: dict[str, float] = field(default_factory=dict)
    tp_total: int = 0
    fp_total: int = 0
    fn_total: int = 0
    boundary_start_mae: float | None = None
    boundary_end_mae: float | None = None
    no_ad_pass: dict[str, bool] = field(default_factory=dict)
    no_ad_fp_count: dict[str, int] = field(default_factory=dict)
    total_episode_cost: float = 0.0
    p50_call_latency_ms: float = 0.0
    p90_call_latency_ms: float = 0.0
    p95_call_latency_ms: float = 0.0
    p99_call_latency_ms: float = 0.0
    max_call_latency_ms: float = 0.0
    json_compliance_mean: float = 0.0
    parse_failure_rate: float = 0.0
    extraction_method_counts: dict[str, int] = field(default_factory=dict)
    schema_violations_total: int = 0
    extra_key_names: set[str] = field(default_factory=set)
    output_tokens_total: int = 0
    detected_ads_total: int = 0
    # Verbosity / truncation telemetry. Useful for spotting models that
    # don't follow "respond with valid JSON only" and emit chatty `reason`
    # fields (phi-4, some Gemini variants). `call_count` is the denominator
    # for the three percentages computed at render time.
    call_count: int = 0
    truncated_count: int = 0
    over_1024_count: int = 0
    salvaged_count: int = 0
    avg_f1: float = 0.0
    mean_f1_stdev: float = 0.0

    @property
    def cost_per_tp(self) -> float | None:
        return self.total_episode_cost / self.tp_total if self.tp_total > 0 else None

    @property
    def tokens_per_detected_ad(self) -> float | None:
        return self.output_tokens_total / self.detected_ads_total if self.detected_ads_total > 0 else None


def _dedup_last_write_wins(calls: list[dict]) -> list[dict]:
    """`calls.jsonl` is append-only. A `benchmark run --retry-errors` that
    successfully retries a previously-failed tuple appends a new row alongside
    the original error row. The report should reflect the final state, not the
    historical errors, so dedup per (model, episode_id, trial, window_index)
    and keep the last row encountered.
    """
    by_key: dict[tuple, dict] = {}
    for rec in calls:
        key = (rec.get("model"), rec.get("episode_id"), rec.get("trial"), rec.get("window_index"))
        by_key[key] = rec
    return list(by_key.values())


def render(
    *,
    cfg,
    episodes: list[Episode],
    calls_path: Path,
    episode_results_path: Path,
    pricing_snapshot: pricing.PricingSnapshot,
    output_path: Path,
    assets_dir: Path,
) -> None:
    raw_calls = list(read_jsonl(calls_path))
    if not raw_calls:
        output_path.write_text("# MinusPod LLM Benchmark Report\n\nNo benchmark data yet. Run `benchmark run` first.\n")
        return
    calls = _dedup_last_write_wins(raw_calls)

    by_model, extras = _aggregate(calls, episodes, pricing_snapshot=pricing_snapshot)
    deprecated_ids = {m.id for m in cfg.models if m.deprecated}
    active = {mid: s for mid, s in by_model.items() if mid not in deprecated_ids}
    deprecated = {mid: s for mid, s in by_model.items() if mid in deprecated_ids}

    sections = [
        _render_how_to_read(),
        _render_tldr(active, episodes),
        _render_charts_section(),
        _render_failures(calls),
        _render_accuracy_breakdown(active),
        _render_boundary_accuracy(active),
        _render_calibration_table(extras.calibration),
        _render_latency_tail(active),
        _render_token_efficiency(active),
        _render_trial_variance(active),
        _render_cross_model_agreement(extras.agreement, active),
        _render_detection_buckets(extras.detection_buckets),
        _render_quick_comparison(active, episodes),
        "---",
        "## Detailed Results",
        _render_per_model_detail(active),
        _render_per_episode_detail(active, episodes),
        _render_parser_stress(active),
    ]
    if deprecated:
        sections.append(_render_deprecated(deprecated))
    sections += [
        _render_methodology(cfg, episodes, pricing_snapshot=pricing_snapshot),
        _render_transcript_source(),
        _render_run_metadata(calls, pricing_snapshot=pricing_snapshot, raw_calls=raw_calls),
    ]

    body = "\n\n".join(s for s in sections if s) + "\n"
    toc = _build_toc(body)
    output_path.write_text("# MinusPod LLM Benchmark Report\n\n" + toc + "\n\n" + body)

    assets_dir.mkdir(parents=True, exist_ok=True)
    _render_pareto(active, assets_dir / "pareto.svg")
    _render_compliance(active, assets_dir / "compliance.svg")
    _render_episode_heatmap(active, episodes, assets_dir / "episodes.svg")
    _render_calibration_chart(extras.calibration, assets_dir / "calibration.svg")
    _render_latency_tail_chart(active, assets_dir / "latency_tail.svg")
    _render_agreement_chart(extras.agreement, len(active), assets_dir / "agreement.svg")
    _render_alignment_chart(extras.agreement, len(active), assets_dir / "alignment.svg")
    _render_precision_recall_chart(active, assets_dir / "precision_recall.svg")
    _render_boundary_chart(active, assets_dir / "boundary.svg")
    _render_token_efficiency_chart(active, assets_dir / "token_efficiency.svg")
    _render_trial_variance_chart(active, assets_dir / "trial_variance.svg")
    _render_detection_bucket_chart(
        extras.detection_buckets, "length",
        ["short (<30s)", "medium (30-90s)", "long (>=90s)"],
        "Detection rate by ad length (rows sorted by overall detection rate, descending)",
        assets_dir / "detection_by_length.svg",
    )
    _render_detection_bucket_chart(
        extras.detection_buckets, "position",
        ["pre-roll (<10%)", "mid-roll (10-90%)", "post-roll (>90%)"],
        "Detection rate by ad position (rows sorted by overall detection rate, descending)",
        assets_dir / "detection_by_position.svg",
    )
    _render_parser_stress_chart(active, assets_dir / "parser_stress.svg")


@dataclass
class _Extras:
    """Side data computed during aggregation that doesn't belong on ModelStats."""
    calibration: dict[str, list[tuple[float, bool]]]    # model -> [(confidence, is_tp), ...]
    agreement: dict[tuple[str, int], dict[str, int]]    # (episode, window_idx) -> {model: n_predicted_ads}
    detection_buckets: dict[str, dict[str, dict[str, list[bool]]]]
    # detection_buckets[model][bucket_kind][bucket_label] -> list of bool (was each truth-ad in this bucket detected?)


def _aggregate(
    calls: list[dict],
    episodes: list[Episode],
    *,
    pricing_snapshot: pricing.PricingSnapshot,
) -> tuple[dict[str, ModelStats], _Extras]:
    truths_by_episode = {ep.ep_id: ep for ep in episodes}
    me: dict[tuple[str, str], ModelEpisodeStats] = {}
    response_times_per_model: dict[str, list[int]] = defaultdict(list)
    method_counts: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    compliance_values_per_model: dict[str, list[float]] = defaultdict(list)
    parse_failures_per_model: dict[str, int] = defaultdict(int)
    parse_total_per_model: dict[str, int] = defaultdict(int)
    schema_totals_per_model: dict[str, int] = defaultdict(int)
    extra_keys_per_model: dict[str, set[str]] = defaultdict(set)

    output_tokens_per_model: dict[str, int] = defaultdict(int)
    detected_ads_per_model: dict[str, int] = defaultdict(int)
    call_count_per_model: dict[str, int] = defaultdict(int)
    truncated_per_model: dict[str, int] = defaultdict(int)
    over_1024_per_model: dict[str, int] = defaultdict(int)
    calibration: dict[str, list[tuple[float, bool]]] = defaultdict(list)
    agreement: dict[tuple[str, int], dict[str, int]] = defaultdict(lambda: defaultdict(int))
    detection_buckets: dict[str, dict[str, dict[str, list[bool]]]] = defaultdict(
        lambda: defaultdict(lambda: defaultdict(list))
    )

    by_trial: dict[tuple[str, str, int], list[dict]] = defaultdict(list)
    for rec in calls:
        if rec.get("error"):
            continue
        by_trial[(rec["model"], rec["episode_id"], rec["trial"])].append(rec)
        response_times_per_model[rec["model"]].append(int(rec.get("response_time_ms", 0)))
        method = rec.get("extraction_method")
        if method is None:
            parse_failures_per_model[rec["model"]] += 1
        method_counts[rec["model"]][method or "parse_failure"] += 1
        parse_total_per_model[rec["model"]] += 1
        compliance_values_per_model[rec["model"]].append(float(rec.get("compliance_score", 0.0)))
        sv = rec.get("schema_violations") or {}
        schema_totals_per_model[rec["model"]] += int(sv.get("missing_required", 0)) + int(sv.get("wrong_type", 0)) + int(sv.get("extra_keys", 0)) + int(sv.get("out_of_range", 0))
        for k in sv.get("extra_key_names") or []:
            extra_keys_per_model[rec["model"]].add(k)
        out_toks = int(rec.get("output_tokens", 0))
        output_tokens_per_model[rec["model"]] += out_toks
        call_count_per_model[rec["model"]] += 1
        if rec.get("truncated"):
            truncated_per_model[rec["model"]] += 1
        # over_1024 has an explicit flag on new records and falls back to a
        # direct comparison on output_tokens for historical records collected
        # before the flag existed. Truncated has no fallback: stop_reason
        # was never captured pre-flag, so we accept 0% on legacy data.
        if rec.get("over_1024_tokens") or out_toks > 1024:
            over_1024_per_model[rec["model"]] += 1
        ads = rec.get("parsed_ads") or []
        detected_ads_per_model[rec["model"]] += len(ads)
        # Cross-model agreement: count predictions per (episode, window) per model
        agreement[(rec["episode_id"], int(rec.get("window_index", 0)))][rec["model"]] += len(ads)

    for (model, ep_id, trial), records in by_trial.items():
        ep = truths_by_episode.get(ep_id)
        if ep is None:
            continue
        if len(records) < len(ep.windows):
            continue
        records.sort(key=lambda r: r["window_index"])
        per_window_ads: list[list[dict]] = [list(r.get("parsed_ads") or []) for r in records]
        per_window_pred: list[list[tuple[float, float]]] = [
            [(_start(ad), _end(ad)) for ad in w] for w in per_window_ads
        ]
        flat_ads: list[dict] = [ad for w in per_window_ads for ad in w]
        flat_preds: list[tuple[float, float]] = [p for w in per_window_pred for p in w]

        stats = me.setdefault((model, ep_id), ModelEpisodeStats(model=model, episode_id=ep_id))
        if ep.truth.is_no_ad_episode:
            res = metrics.no_ad_score(per_window_pred)
            stats.no_ad_passes.append(res.passed)
            stats.no_ad_fp_counts.append(res.false_positive_count)
            # On a no-ad episode every detection is a false positive -> calibration is_tp=False
            for ad in flat_ads:
                conf = ad.get("confidence")
                if isinstance(conf, (int, float)):
                    calibration[model].append((float(conf), False))
        else:
            truth_ranges = [(ad.start, ad.end) for ad in ep.truth.ads]
            r = metrics.match_predictions(flat_preds, truth_ranges, threshold=DEFAULT_IOU_THRESHOLD)
            stats.trial_f1s.append(r.f1)
            stats.trial_precisions.append(r.precision)
            stats.trial_recalls.append(r.recall)
            stats.trial_tps.append(r.true_positives)
            stats.trial_fps.append(r.false_positives)
            stats.trial_fns.append(r.false_negatives)
            be = metrics.boundary_error(flat_preds, truth_ranges, r.matches)
            if be is not None:
                stats.trial_start_maes.append(be.start_mae)
                stats.trial_end_maes.append(be.end_mae)
            # Calibration: for each prediction, was it a TP (matched a truth) or FP?
            matched_pred_idxs = {m.pred_index for m in r.matches}
            for i, ad in enumerate(flat_ads):
                conf = ad.get("confidence")
                if isinstance(conf, (int, float)):
                    calibration[model].append((float(conf), i in matched_pred_idxs))
            # Detection-by-bucket: for each truth ad, was it detected (i.e. matched at IoU>=0.5)?
            duration = float(ep.metadata.duration) if getattr(ep.metadata, "duration", None) else 0.0
            matched_truth_idxs = {m.truth_index for m in r.matches}
            for ti, ad in enumerate(ep.truth.ads):
                hit = ti in matched_truth_idxs
                detection_buckets[model]["length"][_length_bucket(ad.end - ad.start)].append(hit)
                detection_buckets[model]["position"][_position_bucket(ad.start, duration)].append(hit)
        stats.trial_costs.append(_recompute_total_cost(records, pricing_snapshot))
        stats.trial_response_times.append(sum(r.get("response_time_ms", 0) for r in records))

    me_by_model: dict[str, list[tuple[str, ModelEpisodeStats]]] = defaultdict(list)
    for (m, ep_id), s in me.items():
        me_by_model[m].append((ep_id, s))

    out: dict[str, ModelStats] = {}
    models_seen: set[str] = set(me_by_model) | set(response_times_per_model)
    for model in models_seen:
        ms = ModelStats(model=model)
        all_start_maes: list[float] = []
        all_end_maes: list[float] = []
        for ep_id, s in me_by_model[model]:
            if s.trial_f1s:
                ms.f1_per_episode[ep_id] = statistics.fmean(s.trial_f1s)
                ms.f1_stdev_per_episode[ep_id] = metrics.trial_stdev(s.trial_f1s)
            if s.trial_precisions:
                ms.precision_per_episode[ep_id] = statistics.fmean(s.trial_precisions)
            if s.trial_recalls:
                ms.recall_per_episode[ep_id] = statistics.fmean(s.trial_recalls)
            ms.tp_total += sum(s.trial_tps)
            ms.fp_total += sum(s.trial_fps)
            ms.fn_total += sum(s.trial_fns)
            all_start_maes.extend(s.trial_start_maes)
            all_end_maes.extend(s.trial_end_maes)
            if s.no_ad_passes:
                ms.no_ad_pass[ep_id] = all(s.no_ad_passes)
                ms.no_ad_fp_count[ep_id] = max(s.no_ad_fp_counts) if s.no_ad_fp_counts else 0
            if s.trial_costs:
                ms.total_episode_cost += statistics.fmean(s.trial_costs)
        if all_start_maes:
            ms.boundary_start_mae = statistics.fmean(all_start_maes)
            ms.boundary_end_mae = statistics.fmean(all_end_maes)
        rts = sorted(response_times_per_model[model])
        if rts:
            ms.p50_call_latency_ms = _percentile(rts, 50)
            ms.p90_call_latency_ms = _percentile(rts, 90)
            ms.p95_call_latency_ms = _percentile(rts, 95)
            ms.p99_call_latency_ms = _percentile(rts, 99)
            ms.max_call_latency_ms = float(max(rts))
        comps = compliance_values_per_model[model]
        ms.json_compliance_mean = statistics.fmean(comps) if comps else 0.0
        if parse_total_per_model[model]:
            ms.parse_failure_rate = parse_failures_per_model[model] / parse_total_per_model[model]
        ms.extraction_method_counts = dict(method_counts[model])
        ms.schema_violations_total = schema_totals_per_model[model]
        ms.extra_key_names = extra_keys_per_model[model]
        ms.output_tokens_total = output_tokens_per_model[model]
        ms.detected_ads_total = detected_ads_per_model[model]
        ms.call_count = call_count_per_model[model]
        ms.truncated_count = truncated_per_model[model]
        ms.over_1024_count = over_1024_per_model[model]
        ms.salvaged_count = method_counts[model].get("json_object_single_ad_truncated", 0)
        f1s = list(ms.f1_per_episode.values())
        ms.avg_f1 = statistics.fmean(f1s) if f1s else 0.0
        stdevs = list(ms.f1_stdev_per_episode.values())
        ms.mean_f1_stdev = statistics.fmean(stdevs) if stdevs else 0.0
        out[model] = ms
    extras = _Extras(
        calibration=dict(calibration),
        agreement=dict(agreement),
        detection_buckets={k: {b: dict(buckets) for b, buckets in v.items()} for k, v in detection_buckets.items()},
    )
    return out, extras


def _recompute_total_cost(records: list[dict], snap: pricing.PricingSnapshot) -> float:
    if not records:
        return 0.0
    price = snap.lookup(records[0]["model"])
    if price is None:
        return 0.0
    total = 0.0
    for r in records:
        _, _, cost = pricing.cost_usd(price, input_tokens=int(r.get("input_tokens", 0)), output_tokens=int(r.get("output_tokens", 0)))
        total += cost
    return total


def _start(ad: dict) -> float:
    return float(ad.get("start", ad.get("start_time", 0.0)))


def _end(ad: dict) -> float:
    return float(ad.get("end", ad.get("end_time", 0.0)))


def _length_bucket(seconds: float) -> str:
    if seconds < 30:
        return "short (<30s)"
    if seconds < 90:
        return "medium (30-90s)"
    return "long (>=90s)"


def _position_bucket(start: float, duration: float) -> str:
    if duration <= 0:
        return "unknown"
    rel = start / duration
    if rel < 0.10:
        return "pre-roll (<10%)"
    if rel > 0.90:
        return "post-roll (>90%)"
    return "mid-roll (10-90%)"


def _percentile(sorted_values: list[int], p: int) -> float:
    if not sorted_values:
        return 0.0
    k = (p / 100) * (len(sorted_values) - 1)
    lo = int(k)
    hi = min(lo + 1, len(sorted_values) - 1)
    return sorted_values[lo] + (sorted_values[hi] - sorted_values[lo]) * (k - lo)


def _render_tldr(stats: dict[str, ModelStats], episodes: list[Episode]) -> str:
    accuracy_rows = sorted(stats.values(), key=lambda s: _avg_f1(s), reverse=True)
    paid_rows = [s for s in stats.values() if s.total_episode_cost > 0]
    free_rows = [s for s in stats.values() if s.total_episode_cost == 0]
    value_rows = sorted(paid_rows, key=lambda s: _avg_f1(s) / s.total_episode_cost, reverse=True)
    free_by_f1 = sorted(free_rows, key=lambda s: _avg_f1(s), reverse=True)

    lines = ["## TL;DR", "", "### Best Accuracy (F1 @ IoU >= 0.5)", ""]
    lines.append("All models ranked by F1 against human-verified ground truth. Cost includes free-tier models (shown at $0.00).")
    lines.append("")
    lines.append("| Rank | Model | F1 | Cost / episode | p50 latency | JSON compliance |")
    lines.append("|------|-------|----|----------------|-------------|-----------------|")
    for i, s in enumerate(accuracy_rows, 1):
        lines.append(
            f"| {i} | `{s.model}` | {_avg_f1(s):.3f} | ${s.total_episode_cost:.4f} | "
            f"{s.p50_call_latency_ms / 1000:.1f}s | {s.json_compliance_mean:.2f} |"
        )

    lines += ["", "### Best Value (F1 per dollar)", ""]
    lines.append(
        "Paid-tier only. Free-tier models are excluded here because F1 / 0 is undefined; "
        "they are ranked separately under Best Free-Tier below."
    )
    lines.append("")
    lines.append("| Rank | Model | F1/$ | F1 | Cost / episode |")
    lines.append("|------|-------|------|----|----------------|")
    for i, s in enumerate(value_rows, 1):
        lines.append(
            f"| {i} | `{s.model}` | {_avg_f1(s) / s.total_episode_cost:.2f} | "
            f"{_avg_f1(s):.3f} | ${s.total_episode_cost:.4f} |"
        )

    if free_by_f1:
        lines += ["", "### Best Free-Tier (F1)", ""]
        lines.append(
            "Models that came back at $0.00 cost. F1 / $ is undefined for these, so they are ranked by F1 alone. "
            "Free-tier eligibility on OpenRouter depends on the attribution headers wired into the benchmark "
            "(`HTTP-Referer`, `X-Title`); a model showing as free here may bill on your own deployment if those headers are missing."
        )
        lines.append("")
        lines.append("| Rank | Model | F1 | p50 latency | JSON compliance |")
        lines.append("|------|-------|----|-------------|-----------------|")
        for i, s in enumerate(free_by_f1, 1):
            lines.append(
                f"| {i} | `{s.model}` | {_avg_f1(s):.3f} | "
                f"{s.p50_call_latency_ms / 1000:.1f}s | {s.json_compliance_mean:.2f} |"
            )

    return "\n".join(lines)


def _render_quick_comparison(stats: dict[str, ModelStats], episodes: list[Episode]) -> str:
    ad_eps = [ep for ep in episodes if not ep.truth.is_no_ad_episode]
    no_ad_eps = [ep for ep in episodes if ep.truth.is_no_ad_episode]
    header = ["Model", "F1", "Cost/ep", "p50"]
    header += [ep.ep_id for ep in ad_eps]
    header += [f"{ep.ep_id} (no-ad)" for ep in no_ad_eps]
    header += ["F1 stdev"]

    lines = [
        "## Quick Comparison",
        "",
        "One row per model, one column per episode. The headline columns (`F1`, `Cost/ep`, `p50`) summarize across all episodes; the per-episode columns let you see whether a model's average hides wide swings (a model that scores well overall might still bomb on a specific genre). The right-most `F1 stdev` column averages the per-trial standard deviations across episodes; high values mean the model isn't deterministic at temperature 0.0, so its single-trial F1 number is noisy.",
        "",
        "| " + " | ".join(header) + " |",
        "|" + "|".join("---" for _ in header) + "|",
    ]
    for s in sorted(stats.values(), key=lambda s: _avg_f1(s), reverse=True):
        cells = [f"`{s.model}`", f"{_avg_f1(s):.3f}", f"${s.total_episode_cost:.4f}", f"{s.p50_call_latency_ms / 1000:.1f}s"]
        for ep in ad_eps:
            f1 = s.f1_per_episode.get(ep.ep_id)
            cells.append(f"{f1:.3f}" if f1 is not None else "-")
        for ep in no_ad_eps:
            if ep.ep_id in s.no_ad_pass:
                if s.no_ad_pass[ep.ep_id]:
                    cells.append("PASS")
                else:
                    cells.append(f"FAIL ({s.no_ad_fp_count.get(ep.ep_id, 0)} FP)")
            else:
                cells.append("-")
        stdevs = [s.f1_stdev_per_episode[k] for k in s.f1_stdev_per_episode]
        cells.append(f"{statistics.fmean(stdevs):.3f}" if stdevs else "-")
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)


def _render_how_to_read() -> str:
    return (
        "## Metric Key\n\n"
        "Quick reference for the columns in every table below.\n\n"
        "| Metric | Range | Direction | What it means |\n"
        "|--------|-------|-----------|---------------|\n"
        "| **F1 (accuracy)** | 0 to 1 | higher is better | Combined score of precision and recall against the human-verified ground-truth ad spans. F1 = 0 means the model found nothing right; F1 = 1 means it found every ad with the correct boundaries. Uses IoU >= 0.5 (predicted span must overlap truth span by at least half) to count a match. |\n"
        "| **Cost / episode** | USD | lower is better | Average dollars per episode at the current pricing snapshot. Recomputed from token counts so all rows compare at the same prices regardless of when the call ran. |\n"
        "| **F1 / $** | ratio | higher is better | F1 divided by cost-per-episode. Cheap accurate models score highest. Free-tier models are rank-listed separately because the ratio is undefined. |\n"
        "| **p50 / p95 latency** | seconds | lower is better, with caveats | Median (p50) and tail (p95) wall-clock response time. **Note**: for models routed through OpenRouter (everything except `claude-*`), this includes OpenRouter's queueing and upstream-provider latency, not just the model itself. Treat as a load/availability indicator, not a model-quality signal. |\n"
        "| **JSON compliance** | 0 to 1 | higher is better | Fraction of responses that parsed as a clean JSON array matching the requested schema. 1.0 = always clean; lower = used object wrappers (`{ads: [...]}`), markdown fences, extra fields like `sponsor`, or required regex fallback to extract. |\n"
        "| **No-ad episode** | PASS / FAIL | PASS desired | Negative-control test on `ep-ai-cloud-essentials` (which has no ads). PASS = zero predictions across all 15 windows. FAIL = the model false-positived on a non-ad segment, with the FP count shown. |\n"
        "| **F1 stdev** | 0 to 1 | lower means more consistent | Standard deviation of F1 across the four ad-bearing episodes. High stdev = inconsistent across content types. |\n\n"
        "### Glossary\n\n"
        "- **IoU (intersection over union)**: how much two time ranges overlap, expressed as `(overlap) / (union)`. 0 means no overlap, 1 means identical ranges. We use IoU >= 0.5 as the threshold for a predicted ad to count as matching a truth ad.\n"
        "- **Trial**: each (model, episode) pair runs 5 trials at temperature 0.0 to surface non-determinism. F1 numbers in tables are averaged across trials.\n"
        "- **Window**: each episode is split into ~85-second sliding windows; the model judges each window independently. Per-window predictions are stitched together for episode-level scoring.\n"
        "- **Schema violations**: number of times the response had at least one missing-required-field, wrong-type, or extra-key issue. Doesn't tank F1, but signals brittleness.\n"
        "- **Extraction method**: the route the parser took to recover the ad list. `json_array_direct` is the cleanest; method names with `regex_*` mean the JSON itself was malformed and we fell back to text matching.\n"
    )


def _render_failures(calls: list[dict]) -> str:
    """Surface every error row, classified, since failures often signal real
    production-relevant gotchas (provider content moderation, deprecated params,
    rate-limit ceilings) that don't show up in the aggregated F1 / cost tables.
    """
    errors = [r for r in calls if r.get("error")]
    if not errors:
        return (
            "## Failures and provider issues\n\n"
            "No call errors observed across this run. Every (model, episode, trial, window) tuple returned a parseable response.\n"
        )

    def bucket(msg: str) -> str:
        m = msg.lower()
        if "data_inspection_failed" in m or "inappropriate content" in m: return "Provider content moderation rejection"
        if "rate" in m and "limit" in m: return "Rate-limited"
        if "timeout" in m: return "Timeout"
        if "404" in m: return "Unknown model (404)"
        if "temperature" in m and "deprecated" in m: return "Deprecated parameter (`temperature`)"
        if "401" in m or "unauthor" in m: return "Auth failure"
        if _SERVER_5XX_RE.search(m): return "Server-side 5xx"
        return "Other"

    by_bucket: dict[str, list[dict]] = defaultdict(list)
    by_model: dict[str, int] = defaultdict(int)
    calls_by_model: dict[str, int] = defaultdict(int)
    for r in calls:
        calls_by_model[r["model"]] += 1
    for r in errors:
        err = r.get("error", {})
        msg = (err.get("message") if isinstance(err, dict) else str(err)) or ""
        by_bucket[bucket(msg)].append({**r, "_msg": msg})
        by_model[r["model"]] += 1

    lines = [
        "## Failures and provider issues",
        "",
        f"**{len(errors)} call(s) failed out of {len(calls)} total ({len(errors) * 100.0 / len(calls):.2f}%).** "
        "Failures are excluded from F1 / cost calculations, but they often surface real production-relevant gotchas worth knowing.",
        "",
        "### By category",
        "",
        "Errors classified into coarse buckets so failure patterns are visible at a glance. A model showing up here doesn't mean it's broken. Some categories are provider-side (content moderation, rate limits) and tell you more about routing reliability than model quality.",
        "",
        "| Category | Calls | Affected models |",
        "|----------|------:|-----------------|",
    ]
    for cat, recs in sorted(by_bucket.items(), key=lambda x: -len(x[1])):
        affected = sorted({r["model"] for r in recs})
        lines.append(f"| {cat} | {len(recs)} | {', '.join(f'`{m}`' for m in affected)} |")

    lines += [
        "",
        "### Per-model error count",
        "",
        "Same errors grouped by model, with the failure rate as a fraction of that model's total calls. Rates under 1% are usually one-off provider hiccups; rates above 5% suggest the model isn't operationally viable for production with the current prompts and concurrency caps.",
        "",
        "| Model | Errors | of total |",
        "|---|---:|---:|",
    ]
    for m in sorted(by_model, key=lambda k: -by_model[k]):
        total = calls_by_model[m]
        lines.append(f"| `{m}` | {by_model[m]} | {by_model[m]}/{total} ({by_model[m] * 100.0 / total:.1f}%) |")

    lines += [
        "",
        "### Sample messages (first 3 per category)",
        "",
        "First three raw error messages per category, so you can see what the provider actually returned without grepping calls.jsonl. Messages are truncated to ~240 characters; full text lives in `results/raw/calls.jsonl`.",
        "",
    ]
    for cat, recs in sorted(by_bucket.items(), key=lambda x: -len(x[1])):
        lines.append(f"**{cat}** ({len(recs)})")
        for r in recs[:3]:
            preview = r["_msg"].replace("\n", " ")
            preview = preview[:240] + ("..." if len(preview) > 240 else "")
            lines.append(f"- `{r['model']}` on `{r['episode_id']}` (trial {r.get('trial')}, window {r.get('window_index')}): {preview}")
        if len(recs) > 3:
            lines.append(f"- ... and {len(recs) - 3} more")
        lines.append("")

    lines += [
        "### Why this section exists",
        "",
        "If you're picking a model for production, an aggregate compliance score doesn't tell you when the provider will simply refuse to answer. A few cases that have shown up here:",
        "",
        "- **Content moderation rejections** (Alibaba on Qwen, Google on Gemma, sometimes others): the provider's classifier blocks the prompt before the model runs. For ad detection on real podcast transcripts, this can happen on episodes with adult content, profanity, or politically sensitive topics. Rate is small but non-zero; plan for it.",
        "- **Deprecated parameters**: the Claude 4.x family rejects `temperature`. The benchmark memoizes this per-process and retries without, but it tells you which models you cannot pass legacy sampling controls to.",
        "- **Rate limits**: tail-latency or 429s under load. Not a model-quality issue, but determines whether a given provider is operationally viable for your throughput.",
        "",
    ]
    return "\n".join(lines)


def _render_charts_section() -> str:
    return (
        "## Charts\n\n"
        "### Cost vs F1 (Pareto)\n\n"
        "Each model is one colored point. Lower-left is unhelpful (expensive, inaccurate). Upper-left is the sweet spot (accurate, cheap). The legend below the chart shows each model's color next to its F1 and cost-per-episode.\n\n"
        "![Cost vs F1 by model](report_assets/pareto.svg)\n\n"
        "Source data: [Best Accuracy](#best-accuracy-f1--iou--05), [Best Value](#best-value-f1-per-dollar), [Best Free-Tier](#best-free-tier-f1)\n\n"
        "### JSON schema compliance\n\n"
        "Fraction of each model's responses that parsed as a clean JSON array. 1.0 means every response came back exactly as requested; lower numbers mean the parser had to recover from markdown fences, object wrappers, or extra fields.\n\n"
        "![JSON compliance per model](report_assets/compliance.svg)\n\n"
        "Source data: [Per-Model Detail](#per-model-detail) (`JSON compliance` field)\n\n"
        "### F1 by episode (heatmap)\n\n"
        "F1 score for each (model, episode) pair. Greener is more accurate, redder is less. The no-ad episode is excluded. It has no F1 because it's a PASS/FAIL negative control.\n\n"
        "![F1 score per model and episode](report_assets/episodes.svg)\n\n"
        "Source data: [Quick Comparison](#quick-comparison), [Per-Episode Detail](#per-episode-detail)\n\n"
        "### Confidence calibration (heatmap)\n\n"
        "One row per model, one column per self-reported confidence bin. Cell text is the actual hit rate at that bin plus the sample size; cell color is the calibration error (actual minus bin midpoint). Red cells mean the model claimed high confidence but was usually wrong; green is well-calibrated; blue is underconfident. Empty cells mean the model never produced a prediction in that bin. Models are sorted from most overconfident at the top to most underconfident at the bottom.\n\n"
        "![Confidence calibration per model](report_assets/calibration.svg)\n\n"
        "Source data: [Confidence calibration](#confidence-calibration) table\n\n"
        "### Latency percentiles\n\n"
        "p50, p90, p99, and max per model on a log scale. The gap between p99 and max indicates how heavy the tail is. For OpenRouter-routed models, the tail also includes upstream provider load.\n\n"
        "![Latency percentiles per model](report_assets/latency_tail.svg)\n\n"
        "Source data: [Latency tail](#latency-tail) table\n\n"
        "### Cross-model agreement (window distribution)\n\n"
        "Histogram of how many models flagged at least one ad per (episode, window). The left side is windows nobody flagged (clear non-ad content), the right side is windows everyone flagged (clear sponsor reads). Bars in the middle are contested (some models said yes, some said no) and are candidates for ensemble voting or manual review. This view is anonymous (bars don't show which models contributed); the per-model breakdown is in the next chart.\n\n"
        "![Cross-model agreement histogram](report_assets/agreement.svg)\n\n"
        "Source data: [Cross-model agreement](#cross-model-agreement) table\n\n"
        "### Per-model alignment with majority\n\n"
        "Stacked horizontal bar per model. Green + blue segments are windows where the model voted with the majority (true positives + true negatives); orange is windows where it voted yes but most others voted no (likely false positive / hallucination); red is windows where it voted no but most others voted yes (likely missed real ad). Right-edge label is alignment rate. High alignment means the model tracks consensus; low alignment is either insight or noise depending on whether those broken-from-consensus calls were right.\n\n"
        "![Per-model alignment with majority](report_assets/alignment.svg)\n\n"
        "Source data: [Per-model alignment with consensus](#per-model-alignment-with-consensus) table\n\n"
        "### Precision vs Recall (with F1 isocurves)\n\n"
        "Scatter of precision (y) vs recall (x) for each model. Dashed gray lines are F1 isocurves; points on the same dashed line have the same F1. Top-right is ideal (high precision AND high recall). Top-left is cautious (high precision, low recall). Bottom-right is greedy (high recall, low precision). Useful for picking a model whose error profile matches your tolerance: precision-leaning for environments where false positives are expensive, recall-leaning for completeness-first.\n\n"
        "![Precision vs recall scatter](report_assets/precision_recall.svg)\n\n"
        "Source data: [Precision, recall, and FP/FN breakdown](#precision-recall-and-fpfn-breakdown) table\n\n"
        "### Boundary accuracy (start + end MAE)\n\n"
        "Stacked horizontal bars per model: blue is mean absolute error on the predicted ad START in seconds, orange is the same for END. Total error labeled at the right. Sorted by total ascending so the cleanest boundaries are at the top. Skewed bars (start much larger than end, or vice versa) mean the model systematically overshoots on one side. Relevant if you cut audio downstream.\n\n"
        "![Boundary MAE per model](report_assets/boundary.svg)\n\n"
        "Source data: [Boundary accuracy](#boundary-accuracy) table\n\n"
        "### Token efficiency vs F1\n\n"
        "Scatter of output tokens per detected ad (x, log scale) vs F1 (y). Upper-left is the efficient zone: high accuracy with few output tokens. Right-side points are reasoning-heavy models that emit chain-of-thought alongside their JSON. The chart answers whether the extra tokens buy more F1 or just burn output budget. A model that lands far right at modest F1 is paying for reasoning that didn't help.\n\n"
        "![Token efficiency vs F1](report_assets/token_efficiency.svg)\n\n"
        "Source data: [Output token efficiency](#output-token-efficiency) table\n\n"
        "### Trial variance (determinism check)\n\n"
        "Horizontal bars of mean F1 stdev across episodes per model. All trials run at temperature 0.0 so well-behaved models cluster near zero. Bars are color-graded: green below 0.02 (effectively deterministic), yellow 0.02-0.05 (slight noise), red above 0.05 (single-trial F1 numbers from this model should be treated with suspicion). Dotted reference lines mark the 0.02 and 0.05 thresholds.\n\n"
        "![Trial F1 variance per model](report_assets/trial_variance.svg)\n\n"
        "Source data: [Trial variance (determinism check)](#trial-variance-determinism-check) table\n\n"
        "### Detection rate by ad length\n\n"
        "Heatmap of model (row) vs ad-length bucket (column), cell = detection rate with sample size. Greener = caught more ads in that bucket; redder = missed more. Models are sorted by overall detection rate so the strongest are at the top. Empty (gray) cells mean that bucket had no truth ads for the corresponding model's trials.\n\n"
        "![Detection rate by ad length](report_assets/detection_by_length.svg)\n\n"
        "Source data: [Detection rate by ad characteristic > By ad length](#by-ad-length) table\n\n"
        "### Detection rate by ad position\n\n"
        "Same shape as the ad-length heatmap, but columns are episode position (pre-roll / mid-roll / post-roll). A common pattern: pre-roll is easy because of clear show-intro transitions; post-roll is harder because models near the end of long episodes often produce shorter responses or run out of context to anchor on.\n\n"
        "![Detection rate by ad position](report_assets/detection_by_position.svg)\n\n"
        "Source data: [Detection rate by ad characteristic > By ad position](#by-ad-position) table\n\n"
        "### Parser stress (extraction-method usage)\n\n"
        "Heatmap of model (row) vs extraction-method (column), cell = number of responses parsed via that method. Columns are ordered by total usage. `json_array_direct` is the clean path; everything else is a recovery path the parser had to take because the model added markdown fences, wrapped the array in an object, or returned malformed JSON. Models near the top of the chart use the clean path most often. They are operationally easier to consume.\n\n"
        "![Parser stress heatmap](report_assets/parser_stress.svg)\n\n"
        "Source data: [Parser stress test](#parser-stress-test) table\n"
    )


def _render_per_model_detail(stats: dict[str, ModelStats]) -> str:
    lines = [
        "### Per-Model Detail",
        "",
        "Full per-model profile: F1 averaged across episodes, total cost per episode at current pricing, p50 / p95 latency, JSON compliance, parse-failure rate, the distribution of extraction methods the parser had to use, and verbosity / truncation telemetry. The `Extraction methods` list shows how often each route was hit. `json_array_direct` is the cleanest; the rest are recovery paths. The verbosity row flags models that emit long `reason` fields or run out of token budget mid-response. Ordered by F1 descending so the best performers appear first.",
        "",
    ]
    for s in sorted(stats.values(), key=lambda s: _avg_f1(s), reverse=True):
        lines.append(f"#### `{s.model}`\n")
        lines.append(f"- F1 (avg across episodes): **{_avg_f1(s):.3f}**")
        lines.append(f"- Total cost / episode: **${s.total_episode_cost:.4f}**")
        lines.append(f"- p50 / p95 latency: {s.p50_call_latency_ms / 1000:.2f}s / {s.p95_call_latency_ms / 1000:.2f}s")
        lines.append(f"- JSON compliance: {s.json_compliance_mean:.2f}")
        lines.append(f"- Parse failure rate: {s.parse_failure_rate * 100:.1f}%")
        if s.extraction_method_counts:
            counts = ", ".join(f"`{k}`: {v}" for k, v in sorted(s.extraction_method_counts.items()))
            lines.append(f"- Extraction methods: {counts}")
        if s.call_count:
            verbose_pct = 100.0 * s.over_1024_count / s.call_count
            truncated_pct = 100.0 * s.truncated_count / s.call_count
            salvaged_pct = 100.0 * s.salvaged_count / s.call_count
            lines.append(
                f"- Verbosity: {s.over_1024_count}/{s.call_count} calls over 1024 output tokens ({verbose_pct:.1f}%); "
                f"{s.truncated_count} hit max_tokens ({truncated_pct:.1f}%); "
                f"{s.salvaged_count} salvaged from truncated JSON ({salvaged_pct:.1f}%)"
            )
        if s.schema_violations_total:
            lines.append(f"- Schema violations: {s.schema_violations_total}")
        if s.extra_key_names:
            lines.append(f"- Extra keys observed: {', '.join(sorted(s.extra_key_names))}")
        lines.append("")
    return "\n".join(lines)


def _render_per_episode_detail(stats: dict[str, ModelStats], episodes: list[Episode]) -> str:
    lines = [
        "### Per-Episode Detail",
        "",
        "One subsection per episode in the corpus, showing how every model performed on that specific episode. For ad-bearing episodes you see F1 and the stdev across trials (low stdev means stable, high stdev means the model's number on this episode is noisy). For the no-ad episode you see PASS / FAIL on the negative control: PASS = zero false positives across all windows, FAIL = the model flagged something that wasn't an ad, with the count.",
        "",
    ]
    for ep in episodes:
        lines.append(f"#### `{ep.ep_id}`: {ep.metadata.title}\n")
        lines.append(f"- Podcast: {ep.metadata.podcast_name}")
        lines.append(f"- Duration: {ep.metadata.duration / 60:.1f} min")
        if ep.truth.is_no_ad_episode:
            lines.append("- Truth: no-ads episode")
        else:
            lines.append(f"- Truth ads: {len(ep.truth.ads)}")
        lines.append("")
        if ep.truth.is_no_ad_episode:
            lines.append("| Model | Result | FP count |")
            lines.append("|-------|--------|----------|")
            for s in sorted(stats.values(), key=lambda s: s.no_ad_fp_count.get(ep.ep_id, 0)):
                if ep.ep_id in s.no_ad_pass:
                    result = "PASS" if s.no_ad_pass[ep.ep_id] else "FAIL"
                    lines.append(f"| `{s.model}` | {result} | {s.no_ad_fp_count.get(ep.ep_id, 0)} |")
        else:
            lines.append("| Model | F1 | F1 stdev |")
            lines.append("|-------|----|----------|")
            for s in sorted(stats.values(), key=lambda s: s.f1_per_episode.get(ep.ep_id, 0.0), reverse=True):
                f1 = s.f1_per_episode.get(ep.ep_id)
                if f1 is None:
                    continue
                stdev = s.f1_stdev_per_episode.get(ep.ep_id, 0.0)
                lines.append(f"| `{s.model}` | {f1:.3f} | {stdev:.3f} |")
        lines.append("")
    return "\n".join(lines)


def _render_parser_stress(stats: dict[str, ModelStats]) -> str:
    lines = [
        "### Parser stress test",
        "",
        "How each model's responses were actually parsed. Columns are extraction methods, ordered alphabetically; rows are models, sorted by parse-failure rate (cleanest at top). `json_array_direct` is the happy path: a bare JSON array we could `json.loads` and process immediately. `markdown_code_block` means we had to strip triple-backtick fences first; `json_object_*` means the model wrapped the array in an outer object and we had to find the array key; `regex_*` are last-resort recovery paths. A model that needs anything but `json_array_direct` for most calls is fragile. It works today, but a small prompt change can break the parser.",
        "",
    ]
    methods = sorted({m for s in stats.values() for m in s.extraction_method_counts})
    if not methods:
        return "\n".join(lines + ["No data."])
    header = ["Model"] + methods
    lines.append("| " + " | ".join(header) + " |")
    lines.append("|" + "|".join("---" for _ in header) + "|")
    for s in sorted(stats.values(), key=lambda s: s.parse_failure_rate):
        cells = [f"`{s.model}`"] + [str(s.extraction_method_counts.get(m, 0)) for m in methods]
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)


def _render_deprecated(stats: dict[str, ModelStats]) -> str:
    lines = ["### Deprecated Models", ""]
    lines.append("Historical data preserved; excluded from headline rankings.")
    lines.append("")
    for s in stats.values():
        lines.append(f"- `{s.model}`: F1 {_avg_f1(s):.3f}, cost ${s.total_episode_cost:.4f}/ep")
    return "\n".join(lines)


def _render_methodology(cfg, episodes, *, pricing_snapshot: pricing.PricingSnapshot) -> str:
    lines = [
        "## Methodology",
        "",
        "Reproducibility settings used for this run. The benchmark sends the same prompts MinusPod sends in production (same system prompt, same sponsor list, same windowing) so the F1 numbers here are directly relevant to production accuracy decisions. Cost is recomputed at report time from token counts against the active pricing snapshot, so all rows compare at the same prices regardless of when the actual call ran.",
        "",
        f"- Trials per (model, episode): **{cfg.run.trials}**, temperature {cfg.run.temperature}",
        f"- max_tokens: 4096 (matches MinusPod production)",
        f"- response_format: {cfg.run.response_format} (with prompt-injection fallback when provider rejects native)",
        f"- Window size: 10 min, overlap: 3 min (imported from MinusPod's create_windows)",
        f"- Pricing snapshot: {pricing_snapshot.captured_at}",
        f"- Corpus episodes: {len(episodes)}",
    ]
    return "\n".join(lines)


# Whisper configuration used by the production MinusPod instance that supplied
# every transcript in data/corpus/. Hardcoded here because these are production
# defaults, not benchmark config. If the source instance ever changes, update
# this table and regenerate the report.
_WHISPER_CONFIG_TABLE = [
    ("Model", "`large-v3`"),
    ("Backend", "local (faster-whisper, CUDA GPU)"),
    ("Compute type", "`auto` (resolves to `float16` on CUDA)"),
    ("Language", "`en` (forced English, not auto-detect)"),
    ("VAD gap detection", "on (start 3.0s / mid 8.0s / tail 3.0s)"),
]

_WHISPER_TRANSCRIBE_SNIPPET = """\
WhisperModel(model_size=\"large-v3\", device=\"cuda\", compute_type=\"auto\")
model.transcribe(
    audio,
    language=\"en\",
    initial_prompt=<podcast name + SEED_SPONSORS vocabulary>,
    beam_size=5,
    batch_size=<adaptive: 16/12/8/4 by episode length>,
    word_timestamps=True,
    vad_filter=True,
    vad_parameters={\"min_silence_duration_ms\": 1000, \"speech_pad_ms\": 600, \"threshold\": 0.3},
)"""


def _seed_sponsors() -> list[dict] | None:
    """Pull the SEED_SPONSORS list from MinusPod at render time so the report
    reflects whatever's in production today. SEED_SPONSORS is a list of
    {name, aliases, category} dicts. Returns the entries sorted by canonical
    name (case-insensitively). Returns None if the import path isn't
    available (e.g. running outside a MinusPod checkout)."""
    try:
        from utils.constants import SEED_SPONSORS  # type: ignore[import-not-found]
    except ImportError:
        return None
    entries = [s for s in SEED_SPONSORS if isinstance(s, dict) and s.get("name")]
    return sorted(entries, key=lambda s: s["name"].lower())


def _sponsor_aliases() -> dict | None:
    """Pull SPONSOR_ALIASES from MinusPod at render time. This is the
    post-transcription mishearing-correction map (`a firm` -> `Affirm`),
    distinct from the canonical-aliases field on each SEED_SPONSORS entry.
    Returns None if the import fails."""
    try:
        from utils.constants import SPONSOR_ALIASES  # type: ignore[import-not-found]
    except ImportError:
        return None
    return dict(SPONSOR_ALIASES)


def _render_transcript_source() -> str:
    lines = [
        "## Transcript source",
        "",
        "`segments.json` for every corpus episode is pulled byte-exact from the source MinusPod instance's `original-segments` endpoint. The transcript itself was generated by faster-whisper inside that instance, not by the benchmark. Model choice and decoding params affect what gets transcribed, which sets an upper bound on what every benchmarked LLM can find.",
        "",
        "**Whisper config:**",
        "",
        "| Setting | Value |",
        "|---|---|",
    ]
    for k, v in _WHISPER_CONFIG_TABLE:
        lines.append(f"| {k} | {v} |")
    lines += [
        "",
        "**`model.transcribe()` invocation** (from `src/transcriber.py`):",
        "",
        "```python",
        _WHISPER_TRANSCRIBE_SNIPPET,
        "```",
        "",
        "The `initial_prompt` carries a sponsor vocabulary so Whisper produces consistent spellings (`Athletic Greens` rather than `AG1`, `ExpressVPN` rather than `express vpn`). This biases what shows up in the transcript and therefore what every benchmarked LLM is scored against.",
        "",
    ]
    sponsors = _seed_sponsors()
    if sponsors is None:
        lines.append("**Sponsor vocabulary:** [unable to import `SEED_SPONSORS` from MinusPod at render time]")
    else:
        with_aliases = sum(1 for s in sponsors if s.get("aliases"))
        total_aliases = sum(len(s.get("aliases") or []) for s in sponsors)
        lines += [
            f"**Sponsor vocabulary** ({len(sponsors)} canonical sponsors, "
            f"{with_aliases} of them with explicit alias spellings totaling {total_aliases} aliases; "
            "from `src/utils/constants.py` `SEED_SPONSORS`). Laid out in two side-by-side groups, "
            "read top-to-bottom in each group.",
            "",
        ]
        rows = [
            (s["name"], ", ".join(f"`{a}`" for a in (s.get("aliases") or [])) or "-", s.get("category") or "-")
            for s in sponsors
        ]
        lines.extend(_render_multi_column_table(rows, headers=["Sponsor", "Aliases", "Category"], num_cols=2))

    aliases_map = _sponsor_aliases()
    if aliases_map is None:
        lines.append("")
        lines.append("**Mishearing corrections:** [unable to import `SPONSOR_ALIASES` from MinusPod at render time]")
    else:
        lines += [
            "",
            f"**Mishearing corrections** ({len(aliases_map)} entries, from `src/utils/constants.py` `SPONSOR_ALIASES`). "
            "Applied post-transcription to normalize Whisper output toward the canonical sponsor name. "
            "Distinct from the `aliases` column above, which lists intentional alternative spellings "
            "(e.g. `AG1` vs `Athletic Greens`); the entries below are mostly Whisper mishearings "
            "(e.g. `a firm` -> `Affirm`, `xerox` -> `Xero`). Laid out in three side-by-side groups, "
            "read top-to-bottom in each group.",
            "",
        ]
        rows = [(f"`{h}`", c) for h, c in sorted(aliases_map.items(), key=lambda kv: kv[0].lower())]
        lines.extend(_render_multi_column_table(rows, headers=["Heard as", "Normalized to"], num_cols=3))
    return "\n".join(lines)


def _md_anchor(heading: str) -> str:
    """GitHub-style markdown anchor: lowercase, strip non-word chars (except
    hyphens), collapse whitespace to single hyphens."""
    s = heading.lower()
    s = re.sub(r"[^\w\s-]", "", s)
    s = re.sub(r"\s+", "-", s.strip())
    return s


def _build_toc(body: str) -> str:
    """Build a Table of Contents from the H2 headings in the rendered body.
    Skips H3+ to keep the ToC scannable. Anchors follow GitHub's slug rules
    so the same Markdown renders correctly on GitHub and in the IDE preview."""
    lines = ["## Table of Contents", ""]
    for raw in body.split("\n"):
        if not raw.startswith("## ") or raw.startswith("## Table of Contents"):
            continue
        title = raw[3:].strip()
        lines.append(f"- [{title}](#{_md_anchor(title)})")
    return "\n".join(lines)


def _render_multi_column_table(items: list[tuple], *, headers: list[str], num_cols: int) -> list[str]:
    """Render a long Markdown table as `num_cols` side-by-side column-groups so
    it takes less vertical space. Each row in `items` is a tuple matching the
    shape of `headers`. Reading order is top-to-bottom within a column-group
    (column-major), then left-to-right across groups. Pads the last group with
    empty cells when the count doesn't divide evenly.
    """
    rows_per_col = math.ceil(len(items) / num_cols) if items else 0
    cell_count = len(headers) * num_cols
    header_row = "| " + " | ".join(headers * num_cols) + " |"
    sep = "|" + "|".join(["---"] * cell_count) + "|"
    out = [header_row, sep]
    for r in range(rows_per_col):
        cells: list[str] = []
        for c in range(num_cols):
            idx = c * rows_per_col + r
            if idx < len(items):
                cells.extend(str(x) for x in items[idx])
            else:
                cells.extend([""] * len(headers))
        out.append("| " + " | ".join(cells) + " |")
    return out


def _render_run_metadata(
    calls: list[dict],
    *,
    pricing_snapshot: pricing.PricingSnapshot,
    raw_calls: list[dict] | None = None,
) -> str:
    total_calls = len(calls)
    successful = sum(1 for c in calls if not c.get("error"))
    failed = total_calls - successful
    lifetime_actual = sum(float(c.get("total_cost_usd_at_runtime", 0.0)) for c in (raw_calls or calls))
    lines = [
        "## Run Metadata",
        "",
        f"- Report generated: {utc_now_iso()}",
        f"- Unique work units (current state, last-write-wins after retries): {total_calls}",
    ]
    if raw_calls is not None and len(raw_calls) != total_calls:
        lines.append(
            f"- Raw rows in calls.jsonl: {len(raw_calls)} "
            f"({len(raw_calls) - total_calls} superseded by later retries; kept for audit)"
        )
    lines += [
        f"- Successful: {successful}",
        f"- Failed: {failed}",
        f"- Lifetime actual spend (sum of at-runtime costs, includes superseded rows): ${lifetime_actual:.4f}",
        f"- Active pricing snapshot: {pricing_snapshot.captured_at}",
    ]
    return "\n".join(lines)


def _avg_f1(stats: ModelStats) -> float:
    return stats.avg_f1


def _render_pareto(stats: dict[str, ModelStats], path: Path) -> None:
    """Distinct color per model, legend rendered as a real matplotlib legend
    below the plot so each model's color sits next to its name."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    points = [(s, _avg_f1(s)) for s in stats.values()]
    points = [(s, f1) for s, f1 in points if not (f1 == 0 and s.total_episode_cost == 0)]
    points.sort(key=lambda t: (-t[1], t[0].total_episode_cost))  # rank by F1 desc, then cost asc

    cmap = plt.get_cmap("tab20")
    fig, ax = plt.subplots(figsize=(11, 9))
    for i, (s, f1) in enumerate(points):
        color = cmap(i % 20)
        ax.scatter(
            s.total_episode_cost, f1,
            s=180, color=color,
            edgecolors="black", linewidths=0.7, zorder=3,
            label=f"{s.model}  (F1 {f1:.3f}, ${s.total_episode_cost:.4f}/ep)",
        )

    ax.set_xlabel("Cost per episode (USD), lower is better", fontsize=10)
    ax.set_ylabel("F1 score (accuracy, 0-1), higher is better", fontsize=10)
    ax.set_title("Cost vs F1 by model", fontsize=12, fontweight="bold")
    ax.grid(True, alpha=0.3)

    ncol = 2 if len(points) > 6 else 1
    legend = fig.legend(
        loc="lower center",
        bbox_to_anchor=(0.5, 0.02),
        ncol=ncol,
        fontsize=9,
        frameon=True,
        edgecolor="lightgray",
        columnspacing=2.0,
        handletextpad=0.7,
        borderpad=0.8,
    )
    legend.get_frame().set_alpha(0.95)

    # Reserve enough bottom space for the legend; 0.45 fits ~7-row 2-column legend
    rows = (len(points) + ncol - 1) // ncol
    bottom = min(0.55, 0.10 + 0.038 * rows)
    fig.subplots_adjust(left=0.10, right=0.96, top=0.93, bottom=bottom)
    fig.savefig(path, format="svg", bbox_inches="tight")
    plt.close(fig)


def _render_compliance(stats: dict[str, ModelStats], path: Path) -> None:
    """Horizontal bar chart of JSON-array compliance, sorted descending."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    rows = sorted(stats.values(), key=lambda s: s.json_compliance_mean)
    if not rows:
        return
    labels = [s.model for s in rows]
    values = [s.json_compliance_mean for s in rows]
    colors = ["#2ca02c" if v >= 0.95 else "#f0a020" if v >= 0.7 else "#d62728" for v in values]

    fig, ax = plt.subplots(figsize=(10, max(4, 0.45 * len(rows))))
    bars = ax.barh(labels, values, color=colors, edgecolor="black", linewidth=0.4)
    ax.set_xlim(0, 1.05)
    ax.set_xlabel("JSON schema compliance (0 to 1, higher is better)", fontsize=10)
    ax.set_title("How often each model returned the requested JSON shape cleanly", fontsize=11, fontweight="bold")
    ax.axvline(0.95, color="gray", linestyle=":", linewidth=0.8, alpha=0.7)
    ax.grid(True, axis="x", alpha=0.3)
    for bar, v in zip(bars, values):
        ax.text(v + 0.01, bar.get_y() + bar.get_height() / 2, f"{v:.2f}",
                va="center", fontsize=8)
    fig.tight_layout()
    fig.savefig(path, format="svg", bbox_inches="tight")
    plt.close(fig)


def _render_episode_heatmap(stats: dict[str, ModelStats], episodes: list[Episode], path: Path) -> None:
    """Heatmap of F1 across (model, episode). Skips the no-ad episode."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np

    ad_episodes = [ep for ep in episodes if not ep.truth.is_no_ad_episode]
    if not ad_episodes or not stats:
        return
    # Sort models by avg F1 desc so the best are at the top
    models_sorted = sorted(stats.values(), key=lambda s: _avg_f1(s), reverse=True)

    matrix = np.zeros((len(models_sorted), len(ad_episodes)))
    for i, s in enumerate(models_sorted):
        for j, ep in enumerate(ad_episodes):
            matrix[i, j] = s.f1_per_episode.get(ep.ep_id, 0.0)

    fig, ax = plt.subplots(figsize=(max(8, 1.5 * len(ad_episodes)), max(4, 0.4 * len(models_sorted))))
    im = ax.imshow(matrix, cmap="RdYlGn", vmin=0, vmax=1, aspect="auto")
    ax.set_xticks(range(len(ad_episodes)))
    # Use podcast slug if title would be too long
    ax.set_xticklabels([ep.metadata.podcast_slug for ep in ad_episodes], rotation=30, ha="right", fontsize=9)
    ax.set_yticks(range(len(models_sorted)))
    ax.set_yticklabels([s.model for s in models_sorted], fontsize=9)
    for i in range(len(models_sorted)):
        for j in range(len(ad_episodes)):
            v = matrix[i, j]
            ax.text(j, i, f"{v:.2f}", ha="center", va="center",
                    fontsize=8, color="black" if v > 0.4 else "white")
    ax.set_title("F1 score by model and episode (no-ad episode excluded)", fontsize=11, fontweight="bold")
    fig.colorbar(im, ax=ax, label="F1 score (0 to 1)", shrink=0.6)
    fig.tight_layout()
    fig.savefig(path, format="svg", bbox_inches="tight")
    plt.close(fig)


# ---------------------------------------------------------------------------
# Extended-analysis render functions (Section A items added after first run)
# ---------------------------------------------------------------------------


def _render_accuracy_breakdown(stats: dict[str, ModelStats]) -> str:
    """Per-model precision and recall plus TP/FP/FN counts. F1 hides which side
    a model errs on; this table answers it directly."""
    lines = [
        "## Precision, recall, and FP/FN breakdown",
        "",
        "F1 collapses two failure modes into one number. A precision-leaning model misses ads but rarely flags non-ads; a recall-leaning model catches everything at the cost of false positives. Production tradeoffs hinge on which one you can tolerate.",
        "",
        "### Column key",
        "",
        "| Column | Meaning | Range |",
        "|---|---|---|",
        "| **TP** (true positive) | Predicted an ad and a real ad existed at that span (IoU >= 0.5) | 0 to total truth ads |",
        "| **FP** (false positive) | Predicted an ad where no real ad existed | 0 to total predictions |",
        "| **FN** (false negative) | Missed a real ad entirely (no prediction matched it at IoU >= 0.5) | 0 to total truth ads |",
        "| **Precision** | `TP / (TP + FP)`. Of the ads the model claimed, how many were real? Higher means fewer false positives. | 0.000 to 1.000 |",
        "| **Recall** | `TP / (TP + FN)`. Of the real ads, how many did the model find? Higher means fewer misses. | 0.000 to 1.000 |",
        "",
        "Reading the table: high precision + low recall means the model is cautious. It rarely flags something that isn't an ad, but misses real ads. High recall + low precision means the opposite: catches everything but invents false positives. F1 is the harmonic mean of the two and rewards models that do both well.",
        "",
        "| Model | Precision | Recall | TP | FP | FN |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    rows = sorted(stats.values(), key=lambda s: _avg_f1(s), reverse=True)
    for s in rows:
        if not s.precision_per_episode:
            continue
        p = statistics.fmean(s.precision_per_episode.values())
        r = statistics.fmean(s.recall_per_episode.values())
        lines.append(f"| `{s.model}` | {p:.3f} | {r:.3f} | {s.tp_total} | {s.fp_total} | {s.fn_total} |")
    return "\n".join(lines)


def _render_boundary_accuracy(stats: dict[str, ModelStats]) -> str:
    """Mean absolute error on start / end timestamps for matched ads. A high-F1
    model with a 30s boundary error is unhelpful for any audio editing use case."""
    lines = [
        "## Boundary accuracy",
        "",
        "For ads that match the truth at IoU >= 0.5, how far off were the predicted start and end timestamps? Lower is better. A model can hit F1 cleanly while still being 20s off on every boundary. Bad for any pipeline that cuts the audio.",
        "",
        "| Model | Start MAE (s) | End MAE (s) |",
        "|---|---:|---:|",
    ]
    rows = sorted(
        [s for s in stats.values() if s.boundary_start_mae is not None],
        key=lambda s: (s.boundary_start_mae or 0) + (s.boundary_end_mae or 0),
    )
    if not rows:
        return ""
    for s in rows:
        lines.append(f"| `{s.model}` | {s.boundary_start_mae:.2f} | {s.boundary_end_mae:.2f} |")
    return "\n".join(lines)


def _render_calibration_table(calibration: dict[str, list[tuple[float, bool]]]) -> str:
    """Bin self-reported confidence and show the actual hit rate in each bin.
    Reveals overconfident models (e.g., phi-4 reports ~0.95 confidence on
    detections that are wrong nearly all the time).
    """
    bins = CALIBRATION_BINS
    bin_labels = CALIBRATION_BIN_LABELS
    lines = [
        "## Confidence calibration",
        "",
        "Models include a self-reported `confidence` on each detected ad. A well-calibrated model should be right ~95% of the time when it claims 0.95 confidence. The table below bins each model's predictions and shows the actual hit rate (fraction that were true positives at IoU >= 0.5). A bin near 1.0 is well-calibrated; a low number with a high count means the model is overconfident.",
        "",
        "| Model | " + " | ".join(bin_labels) + " | total |",
        "|---|" + "|".join(["---:"] * (len(bin_labels) + 1)) + "|",
    ]
    for model in sorted(calibration):
        pairs = calibration[model]
        if not pairs:
            continue
        cells = []
        total_n = 0
        for lo, hi in bins:
            ins = [t for c, t in pairs if lo <= c < hi]
            n = len(ins)
            total_n += n
            if n == 0:
                cells.append("--")
            else:
                hit = sum(1 for t in ins if t) / n
                cells.append(f"{hit:.2f} (n={n})")
        lines.append(f"| `{model}` | " + " | ".join(cells) + f" | {total_n} |")
    lines += ["", "See `report_assets/calibration.svg` for the visual reliability diagram."]
    return "\n".join(lines)


def _render_latency_tail(stats: dict[str, ModelStats]) -> str:
    """Full latency percentile table including p99 and max, not just p50/p95.
    The tail is what matters for production capacity planning."""
    lines = [
        "## Latency tail",
        "",
        "Median latency hides outliers. p99 and max are what determines queue depth and worst-case user wait. For OpenRouter-routed models the tail also reflects upstream provider load, not just model compute.",
        "",
        "| Model | p50 | p90 | p95 | p99 | max |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    rows = sorted(stats.values(), key=lambda s: s.p50_call_latency_ms)
    for s in rows:
        lines.append(
            f"| `{s.model}` | {s.p50_call_latency_ms / 1000:.2f}s | {s.p90_call_latency_ms / 1000:.2f}s | "
            f"{s.p95_call_latency_ms / 1000:.2f}s | {s.p99_call_latency_ms / 1000:.2f}s | {s.max_call_latency_ms / 1000:.2f}s |"
        )
    return "\n".join(lines)


def _render_token_efficiency(stats: dict[str, ModelStats]) -> str:
    """Output tokens per detected ad. Reasoning-style models burn through
    output budget on chain-of-thought even when finding only 1-2 ads, and that
    cost shows up here even if total cost looks competitive."""
    lines = [
        "## Output token efficiency",
        "",
        "How many output tokens the model spent per detected ad. Lower is more concise (the model finds an ad and returns the JSON). Higher means the model is producing a lot of text the parser will discard, which costs you whether or not the answer is right.",
        "",
        "| Model | Total output tokens | Ads detected | Tokens / ad | Cost / TP |",
        "|---|---:|---:|---:|---:|",
    ]
    rows = sorted(stats.values(), key=lambda s: (s.tokens_per_detected_ad or float("inf")))
    for s in rows:
        if s.tokens_per_detected_ad is None:
            continue
        ctp = f"${s.cost_per_tp:.4f}" if s.cost_per_tp is not None else "n/a"
        lines.append(
            f"| `{s.model}` | {s.output_tokens_total:,} | {s.detected_ads_total} | {s.tokens_per_detected_ad:.0f} | {ctp} |"
        )
    return "\n".join(lines)


def _render_trial_variance(stats: dict[str, ModelStats]) -> str:
    """Mean F1 stdev across trials per (model, episode). At temperature 0.0
    you'd expect low variance. High variance means the model isn't actually
    deterministic at temp=0 and a single-trial number can't be trusted."""
    rows = sorted(stats.values(), key=lambda s: _avg_f1(s), reverse=True)
    lines = [
        "## Trial variance (determinism check)",
        "",
        "All trials run at temperature 0.0. If a model produces stable output you'd expect the F1 stdev across trials to be near zero. Higher numbers mean the model is non-deterministic even at temp=0. That's fine to know, but means you cannot trust a single trial's number for that model.",
        "",
        "| Model | Mean F1 stdev across episodes | Highest single-episode stdev |",
        "|---|---:|---:|",
    ]
    for s in rows:
        if not s.f1_stdev_per_episode:
            continue
        mean_sd = statistics.fmean(s.f1_stdev_per_episode.values())
        max_sd = max(s.f1_stdev_per_episode.values())
        lines.append(f"| `{s.model}` | {mean_sd:.4f} | {max_sd:.4f} |")
    return "\n".join(lines)


def _render_cross_model_agreement(
    agreement: dict[tuple[str, int], dict[str, int]],
    stats: dict[str, ModelStats],
) -> str:
    """For each (episode, window) tuple, count how many distinct models flagged
    at least one ad. Windows where all models agree are easy; windows where
    only a couple flag are either edge cases or noise."""
    if not agreement:
        return ""
    n_models = len(stats)
    bins = Counter()
    for (_, _), per_model in agreement.items():
        n_voted = sum(1 for v in per_model.values() if v > 0)
        bins[n_voted] += 1
    total_windows = sum(bins.values())
    lines = [
        "## Cross-model agreement",
        "",
        f"For each of the {total_windows} (episode, window, trial-equivalent) entries, how many of the {n_models} active models predicted at least one ad? High-agreement windows are unambiguous ads (or unambiguously not ads). Low-agreement windows are where individual models disagree, and are candidates for ensemble voting if you want a cheap accuracy boost.",
        "",
        "| Models predicting an ad | Window count | Share |",
        "|---:|---:|---:|",
    ]
    for k in sorted(bins):
        share = bins[k] / total_windows if total_windows else 0
        lines.append(f"| {k} of {n_models} | {bins[k]} | {share * 100:.1f}% |")
    lines += [
        "",
        "Read this as: rows near the top are windows where the field disagrees (most models said no, a few said yes, usually false positives); rows near the bottom are windows where the field broadly agrees (typical of clear sponsor reads).",
        "",
        "### Per-model alignment with consensus",
        "",
        f"Same data, viewed per model. For each window, the **majority** is whether more than half of the {n_models} active models flagged an ad. Then for each model: did it vote with the majority or against it? Four buckets:",
        "",
        "- **with-yes**: this model voted yes, majority also voted yes (likely true positive)",
        "- **with-no**: this model voted no, majority also voted no (likely true negative)",
        "- **broke-yes**: this model voted yes, majority voted no (likely false positive / hallucination)",
        "- **broke-no**: this model voted no, majority voted yes (likely missed real ad)",
        "",
        "Alignment rate is `(with-yes + with-no) / total`. High alignment means the model tracks the consensus; low alignment means it disagrees often, which could be brilliance or noise depending on whether its disagreements are also where its F1 wins or loses.",
        "",
        "| Model | with-yes | with-no | broke-yes | broke-no | Alignment |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    per_model = _per_model_alignment(agreement, n_models)
    for row in sorted(per_model, key=lambda r: -r["alignment"]):
        lines.append(
            f"| `{row['model']}` | {row['with_yes']} | {row['with_no']} | "
            f"{row['broke_yes']} | {row['broke_no']} | {row['alignment'] * 100:.1f}% |"
        )
    return "\n".join(lines)


def _per_model_alignment(
    agreement: dict[tuple[str, int], dict[str, int]],
    n_models: int,
) -> list[dict]:
    """For each (episode, window): majority = >half of active models voted yes.
    For each model: count its alignment vs that majority into 4 buckets.
    Returns list of {model, with_yes, with_no, broke_yes, broke_no, alignment}.
    """
    # Pre-resolve majority per window
    window_majority: dict[tuple[str, int], bool] = {}
    for key, per_model in agreement.items():
        n_yes = sum(1 for v in per_model.values() if v > 0)
        window_majority[key] = n_yes > n_models / 2
    # Tally per model
    models = {m for per_model in agreement.values() for m in per_model.keys()}
    out: list[dict] = []
    for model in sorted(models):
        wy = wn = by = bn = 0
        for key, per_model in agreement.items():
            voted_yes = per_model.get(model, 0) > 0
            maj_yes = window_majority[key]
            if voted_yes and maj_yes: wy += 1
            elif not voted_yes and not maj_yes: wn += 1
            elif voted_yes and not maj_yes: by += 1
            else: bn += 1
        total = wy + wn + by + bn
        alignment = (wy + wn) / total if total else 0
        out.append({"model": model, "with_yes": wy, "with_no": wn,
                    "broke_yes": by, "broke_no": bn, "alignment": alignment})
    return out


def _render_detection_buckets(
    detection_buckets: dict[str, dict[str, dict[str, list[bool]]]],
) -> str:
    """Detection rate per (model, ad-length bucket) and (model, ad-position
    bucket). Surfaces whether models systematically miss short ads or post-roll
    spots."""
    if not detection_buckets:
        return ""
    sample_model = next(iter(detection_buckets.values()))
    length_labels = sorted(sample_model.get("length", {}).keys()) if "length" in sample_model else []
    position_labels = ["pre-roll (<10%)", "mid-roll (10-90%)", "post-roll (>90%)"]
    position_labels = [p for p in position_labels if p in sample_model.get("position", {})]
    lines = [
        "## Detection rate by ad characteristic",
        "",
        "Aggregate detection rates often hide systematic blind spots. Below: for each model, what fraction of truth ads in each bucket were detected (matched at IoU >= 0.5).",
        "",
    ]
    if length_labels:
        lines += [
            "### By ad length",
            "",
            "Truth ads bucketed by duration: short (<30s), medium (30-90s), long (>=90s). Cell values are detection rate (fraction of truth ads in that bucket the model caught), with the sample size `n` so a misleading 1.00 on a 2-ad bucket doesn't get over-weighted. Models that systematically miss short ads usually fail on network-inserted brand-tagline spots; missing long ads is rarer and usually means the model gave up before processing the full window.",
            "",
            "| Model | " + " | ".join(length_labels) + " |",
            "|---|" + "|".join(["---:"] * len(length_labels)) + "|",
        ]
        for model in sorted(detection_buckets):
            buckets = detection_buckets[model].get("length", {})
            cells = []
            for label in length_labels:
                hits = buckets.get(label, [])
                if not hits:
                    cells.append("--")
                else:
                    rate = sum(hits) / len(hits)
                    cells.append(f"{rate:.2f} (n={len(hits)})")
            lines.append(f"| `{model}` | " + " | ".join(cells) + " |")
        lines.append("")

    if position_labels:
        lines += [
            "### By ad position",
            "",
            "Truth ads bucketed by where they fall in the episode: pre-roll (first 10%), mid-roll (10-90%), post-roll (last 10%). Cell values are the same detection-rate-with-`n` format as ad length. A common failure pattern in our data: most models detect pre-roll and mid-roll reliably and miss post-roll, because the prompt windows near the end often catch the model mid-reasoning or with fewer transition phrases to anchor on.",
            "",
            "| Model | " + " | ".join(position_labels) + " |",
            "|---|" + "|".join(["---:"] * len(position_labels)) + "|",
        ]
        for model in sorted(detection_buckets):
            buckets = detection_buckets[model].get("position", {})
            cells = []
            for label in position_labels:
                hits = buckets.get(label, [])
                if not hits:
                    cells.append("--")
                else:
                    rate = sum(hits) / len(hits)
                    cells.append(f"{rate:.2f} (n={len(hits)})")
            lines.append(f"| `{model}` | " + " | ".join(cells) + " |")
    return "\n".join(lines)


def _render_calibration_chart(
    calibration: dict[str, list[tuple[float, bool]]],
    path: Path,
) -> None:
    """Calibration heatmap: one row per model, one column per confidence bin,
    cell color = calibration error (actual hit rate minus bin midpoint), cell
    text = actual hit rate plus sample size. Replaces the prior line-overlay
    chart, which crowded near-identical points at the high-confidence end and
    rendered the x-axis labels unreadable.

    Diverging colormap centered on 0:
      green  -> well-calibrated (actual close to expected)
      red    -> overconfident   (actual << expected)
      blue   -> underconfident  (actual >> expected)
    """
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np

    bins = CALIBRATION_BINS
    bin_labels = CALIBRATION_BIN_LABELS
    bin_midpoints = [(lo + min(hi, 1.0)) / 2 for lo, hi in bins]

    # Build the (model, bin) matrices: actual hit rate, sample count, calibration error.
    model_rows = []
    for model in sorted(calibration):
        pairs = calibration[model]
        if len(pairs) < 5:
            continue
        row_actual = [float("nan")] * len(bins)
        row_n = [0] * len(bins)
        row_err = [float("nan")] * len(bins)
        for j, (lo, hi) in enumerate(bins):
            ins = [t for conf, t in pairs if lo <= conf < hi]
            if not ins:
                continue
            actual = sum(ins) / len(ins)
            row_actual[j] = actual
            row_n[j] = len(ins)
            row_err[j] = actual - bin_midpoints[j]
        # Sort key: largest dominant-bin sample size, used to put high-volume models near the top.
        dominant_n = max(row_n) if row_n else 0
        model_rows.append((model, row_actual, row_n, row_err, dominant_n))

    if not model_rows:
        return

    # Sort by overall mean calibration error (negative -> overconfident at top)
    def mean_err(r):
        errs = [e for e in r[3] if not np.isnan(e)]
        return sum(errs) / len(errs) if errs else 0
    model_rows.sort(key=mean_err)

    n_models = len(model_rows)
    fig, ax = plt.subplots(figsize=(max(9, 1.6 * len(bins)), max(5, 0.40 * n_models)))
    matrix = np.array([r[3] for r in model_rows], dtype=float)
    masked = np.ma.masked_invalid(matrix)
    im = ax.imshow(masked, cmap="RdYlGn", vmin=-0.6, vmax=0.6, aspect="auto")
    cmap = im.get_cmap().copy()
    cmap.set_bad(color="#eeeeee")
    im.set_cmap(cmap)

    ax.set_xticks(range(len(bin_labels)))
    ax.set_xticklabels(bin_labels, fontsize=9)
    ax.set_yticks(range(n_models))
    ax.set_yticklabels([r[0] for r in model_rows], fontsize=9)
    ax.set_xlabel("Self-reported confidence bin", fontsize=10)

    # Annotate each cell: actual hit rate + (n=sample size). Blank cells (NaN) stay empty.
    for i, (_, row_actual, row_n, row_err, _) in enumerate(model_rows):
        for j in range(len(bins)):
            if np.isnan(row_actual[j]):
                continue
            color = "black" if abs(row_err[j]) < 0.4 else "white"
            ax.text(j, i, f"{row_actual[j]:.2f}\n(n={row_n[j]})",
                    ha="center", va="center", fontsize=7, color=color)

    ax.set_title(
        "Confidence calibration (cell text = actual hit rate, n = sample size)\n"
        "Red = overconfident   Green = well-calibrated   Blue = underconfident",
        fontsize=10, fontweight="bold",
    )
    fig.colorbar(im, ax=ax, label="actual hit rate minus bin midpoint", shrink=0.7)
    fig.tight_layout()
    fig.savefig(path, format="svg", bbox_inches="tight")
    plt.close(fig)


def _render_latency_tail_chart(stats: dict[str, ModelStats], path: Path) -> None:
    """Bar chart of p50/p90/p99/max per model on a log scale. Visually surfaces
    which models have well-behaved tails vs which have multi-minute outliers."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np

    rows = sorted(stats.values(), key=lambda s: s.p50_call_latency_ms)
    if not rows:
        return
    labels = [s.model for s in rows]
    p50s = [s.p50_call_latency_ms / 1000 for s in rows]
    p90s = [s.p90_call_latency_ms / 1000 for s in rows]
    p99s = [s.p99_call_latency_ms / 1000 for s in rows]
    maxes = [s.max_call_latency_ms / 1000 for s in rows]

    y = np.arange(len(rows))
    height = 0.2
    fig, ax = plt.subplots(figsize=(11, max(5, 0.55 * len(rows))))
    ax.barh(y - 1.5 * height, p50s, height, label="p50", color="#2ca02c", edgecolor="black", linewidth=0.3)
    ax.barh(y - 0.5 * height, p90s, height, label="p90", color="#1f77b4", edgecolor="black", linewidth=0.3)
    ax.barh(y + 0.5 * height, p99s, height, label="p99", color="#f0a020", edgecolor="black", linewidth=0.3)
    ax.barh(y + 1.5 * height, maxes, height, label="max", color="#d62728", edgecolor="black", linewidth=0.3)
    ax.set_yticks(y)
    ax.set_yticklabels(labels, fontsize=9)
    ax.set_xscale("log")
    ax.set_xlabel("Seconds (log scale), lower is better", fontsize=10)
    ax.set_title("Latency percentiles per model", fontsize=11, fontweight="bold")
    ax.grid(True, axis="x", alpha=0.3, which="both")
    ax.legend(loc="lower right", fontsize=9)
    fig.tight_layout()
    fig.savefig(path, format="svg", bbox_inches="tight")
    plt.close(fig)


def _render_agreement_chart(
    agreement: dict[tuple[str, int], dict[str, int]],
    n_models: int,
    path: Path,
) -> None:
    """Histogram of how many models flagged each (episode, window). Tall bars
    at the extremes (0-of-N or N-of-N) mean the field broadly agrees on those
    windows; bars in the middle are contested cases worth inspecting."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np

    if not agreement:
        return
    counts = np.zeros(n_models + 1, dtype=int)
    for per_model in agreement.values():
        n_voted = sum(1 for v in per_model.values() if v > 0)
        counts[n_voted] += 1
    total = counts.sum()
    if total == 0:
        return
    xs = np.arange(n_models + 1)
    # Color gradient: low agreement = red, mid = yellow, high = green
    colors = ["#d62728" if i < n_models * 0.25 else "#f0a020" if i < n_models * 0.75 else "#2ca02c" for i in xs]

    fig, ax = plt.subplots(figsize=(11, 5.5))
    bars = ax.bar(xs, counts, color=colors, edgecolor="black", linewidth=0.4)
    for bar, c in zip(bars, counts):
        if c == 0:
            continue
        ax.text(bar.get_x() + bar.get_width() / 2, c + max(counts) * 0.01,
                f"{c}\n({c * 100 / total:.0f}%)",
                ha="center", va="bottom", fontsize=8)
    ax.set_xlabel(f"Models predicting at least one ad (out of {n_models})", fontsize=10)
    ax.set_ylabel("Window count", fontsize=10)
    ax.set_title(
        "Cross-model agreement per window\n"
        "Left = nobody flags (clear non-ad), right = everyone agrees (clear ad), middle = contested",
        fontsize=11, fontweight="bold",
    )
    ax.set_xticks(xs)
    ax.set_xticklabels([str(i) for i in xs], fontsize=8)
    ax.set_ylim(0, max(counts) * 1.15)
    ax.grid(True, axis="y", alpha=0.3)
    fig.tight_layout()
    fig.savefig(path, format="svg", bbox_inches="tight")
    plt.close(fig)


def _render_alignment_chart(
    agreement: dict[tuple[str, int], dict[str, int]],
    n_models: int,
    path: Path,
) -> None:
    """Per-model stacked bars of agreement-with-majority. Each model has a
    horizontal bar split into 4 segments: with-yes / with-no / broke-yes /
    broke-no. Sorted by alignment so highest-consensus models are at top."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np

    rows = _per_model_alignment(agreement, n_models)
    if not rows:
        return
    rows.sort(key=lambda r: r["alignment"])
    labels = [r["model"] for r in rows]
    wy = np.array([r["with_yes"] for r in rows])
    wn = np.array([r["with_no"] for r in rows])
    by_arr = np.array([r["broke_yes"] for r in rows])
    bn = np.array([r["broke_no"] for r in rows])
    y = np.arange(len(rows))

    fig, ax = plt.subplots(figsize=(12, max(5, 0.40 * len(rows))))
    ax.barh(y, wy, color="#2ca02c", edgecolor="black", linewidth=0.3, label="with-yes (matches majority yes)")
    ax.barh(y, wn, left=wy, color="#1f77b4", edgecolor="black", linewidth=0.3, label="with-no (matches majority no)")
    ax.barh(y, by_arr, left=wy + wn, color="#f0a020", edgecolor="black", linewidth=0.3, label="broke-yes (likely false positive)")
    ax.barh(y, bn, left=wy + wn + by_arr, color="#d62728", edgecolor="black", linewidth=0.3, label="broke-no (likely miss)")
    for i, r in enumerate(rows):
        total = r["with_yes"] + r["with_no"] + r["broke_yes"] + r["broke_no"]
        ax.text(total + 1, i, f"{r['alignment'] * 100:.0f}%", va="center", fontsize=8)
    ax.set_yticks(y)
    ax.set_yticklabels(labels, fontsize=9)
    ax.set_xlabel(f"Windows (of {sum(wy + wn + by_arr + bn) // max(len(rows), 1)} total)", fontsize=10)
    ax.set_title(
        "Per-model alignment with majority vote (right-edge label = alignment rate)\n"
        "Green + blue = matches consensus   |   Orange = likely false positive   |   Red = likely missed real ad",
        fontsize=11, fontweight="bold",
    )
    ax.legend(loc="lower right", fontsize=8, framealpha=0.95)
    ax.grid(True, axis="x", alpha=0.3)
    fig.tight_layout()
    fig.savefig(path, format="svg", bbox_inches="tight")
    plt.close(fig)


def _render_precision_recall_chart(stats: dict[str, ModelStats], path: Path) -> None:
    """Scatter of precision vs recall per model, with F1 isocurves for reference.
    Top-right = perfect; top-left = cautious (high precision, low recall);
    bottom-right = greedy (high recall, low precision); bottom-left = bad both
    ways."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np

    points = []
    for s in stats.values():
        if not s.precision_per_episode or not s.recall_per_episode:
            continue
        p = statistics.fmean(s.precision_per_episode.values())
        r = statistics.fmean(s.recall_per_episode.values())
        if p == 0 and r == 0:
            continue
        points.append((s, p, r))
    if not points:
        return
    points.sort(key=lambda t: -(2 * t[1] * t[2] / (t[1] + t[2]) if (t[1] + t[2]) > 0 else 0))  # F1 desc

    cmap = plt.get_cmap("tab20")
    fig, ax = plt.subplots(figsize=(11, 9))

    # F1 isocurves: for each target F1, plot the curve precision*recall*2 / (p+r) = F1
    # Equivalent: r = (F1 * p) / (2p - F1) for p > F1/2
    for f1_iso in [0.2, 0.4, 0.6, 0.8]:
        ps = np.linspace(f1_iso / 2 + 0.001, 1.0, 200)
        rs = (f1_iso * ps) / (2 * ps - f1_iso)
        rs = np.clip(rs, 0, 1)
        ax.plot(rs, ps, "--", color="gray", linewidth=0.6, alpha=0.5)
        # Label at top-right end of each curve
        ax.text(rs[-1] + 0.005, ps[-1] - 0.015, f"F1={f1_iso}",
                fontsize=7, color="gray", alpha=0.7)

    for i, (s, p, r) in enumerate(points):
        f1 = 2 * p * r / (p + r) if (p + r) > 0 else 0
        ax.scatter(r, p, s=180, color=cmap(i % 20),
                   edgecolors="black", linewidths=0.7, zorder=3,
                   label=f"{s.model}  (P {p:.2f}, R {r:.2f}, F1 {f1:.2f})")

    ax.set_xlabel("Recall: of the real ads, what fraction the model found", fontsize=10)
    ax.set_ylabel("Precision: of the model's flags, what fraction were real ads", fontsize=10)
    ax.set_title(
        "Precision vs Recall per model (dashed lines are F1 isocurves)\n"
        "Top-right = ideal   |   Top-left = cautious   |   Bottom-right = greedy   |   Bottom-left = poor",
        fontsize=11, fontweight="bold",
    )
    ax.set_xlim(-0.02, 1.02)
    ax.set_ylim(-0.02, 1.02)
    ax.grid(True, alpha=0.3)

    ncol = 2 if len(points) > 6 else 1
    fig.legend(loc="lower center", bbox_to_anchor=(0.5, 0.02), ncol=ncol,
               fontsize=8, frameon=True, edgecolor="lightgray")
    rows = (len(points) + ncol - 1) // ncol
    bottom = min(0.55, 0.10 + 0.035 * rows)
    fig.subplots_adjust(left=0.10, right=0.96, top=0.90, bottom=bottom)
    fig.savefig(path, format="svg", bbox_inches="tight")
    plt.close(fig)


def _render_boundary_chart(stats: dict[str, ModelStats], path: Path) -> None:
    """Stacked horizontal bars of start MAE and end MAE per model. Sorted by
    total error so the cleanest boundaries appear at the top. Models with
    skewed bars (one side much larger than the other) consistently overshoot
    on one boundary. Worth knowing if you cut audio downstream."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np

    rows = [s for s in stats.values() if s.boundary_start_mae is not None]
    if not rows:
        return
    rows.sort(key=lambda s: (s.boundary_start_mae or 0) + (s.boundary_end_mae or 0))
    labels = [s.model for s in rows]
    starts = [s.boundary_start_mae or 0 for s in rows]
    ends = [s.boundary_end_mae or 0 for s in rows]
    y = np.arange(len(rows))

    fig, ax = plt.subplots(figsize=(11, max(5, 0.40 * len(rows))))
    ax.barh(y, starts, color="#1f77b4", edgecolor="black", linewidth=0.3, label="start MAE")
    ax.barh(y, ends, left=starts, color="#ff7f0e", edgecolor="black", linewidth=0.3, label="end MAE")
    for i, (s_v, e_v) in enumerate(zip(starts, ends)):
        ax.text(s_v + e_v + 0.3, i, f"{s_v + e_v:.1f}s total",
                va="center", fontsize=8)
    ax.set_yticks(y)
    ax.set_yticklabels(labels, fontsize=9)
    ax.set_xlabel("Boundary error in seconds (lower is better)", fontsize=10)
    ax.set_title("Boundary accuracy per model (matched ads only, IoU >= 0.5)", fontsize=11, fontweight="bold")
    ax.legend(loc="lower right", fontsize=9)
    ax.grid(True, axis="x", alpha=0.3)
    fig.tight_layout()
    fig.savefig(path, format="svg", bbox_inches="tight")
    plt.close(fig)


def _render_token_efficiency_chart(stats: dict[str, ModelStats], path: Path) -> None:
    """Scatter of tokens-per-detected-ad vs F1. Upper-left is the efficient
    zone (high F1 with few output tokens). Right side is verbose reasoning
    models; the question is whether they buy more F1 with the extra tokens
    or just burn output budget."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    points = [(s, _avg_f1(s)) for s in stats.values()
              if s.tokens_per_detected_ad is not None and s.detected_ads_total > 0]
    if not points:
        return
    points.sort(key=lambda t: -t[1])

    cmap = plt.get_cmap("tab20")
    fig, ax = plt.subplots(figsize=(11, 8))
    for i, (s, f1) in enumerate(points):
        ax.scatter(s.tokens_per_detected_ad, f1, s=180, color=cmap(i % 20),
                   edgecolors="black", linewidths=0.7, zorder=3,
                   label=f"{s.model}  (F1 {f1:.2f}, {s.tokens_per_detected_ad:.0f} tok/ad)")
    ax.set_xscale("log")
    ax.set_xlabel("Output tokens per detected ad (log scale, lower is more concise)", fontsize=10)
    ax.set_ylabel("F1 score (higher is better)", fontsize=10)
    ax.set_title(
        "Token efficiency vs accuracy: does verbose output buy more F1?\n"
        "Upper-left = efficient (high F1, few tokens)   |   Lower-right = burning tokens for no gain",
        fontsize=11, fontweight="bold",
    )
    ax.set_ylim(-0.02, 1.02)
    ax.grid(True, alpha=0.3, which="both")

    ncol = 2 if len(points) > 6 else 1
    fig.legend(loc="lower center", bbox_to_anchor=(0.5, 0.02), ncol=ncol,
               fontsize=8, frameon=True, edgecolor="lightgray")
    rows = (len(points) + ncol - 1) // ncol
    bottom = min(0.55, 0.10 + 0.035 * rows)
    fig.subplots_adjust(left=0.10, right=0.96, top=0.92, bottom=bottom)
    fig.savefig(path, format="svg", bbox_inches="tight")
    plt.close(fig)


def _render_trial_variance_chart(stats: dict[str, ModelStats], path: Path) -> None:
    """Horizontal bars of mean F1 stdev across episodes per model. Color
    threshold at 0.05: below = stable at temp=0, above = wobbly."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    rows = [(s, statistics.fmean(s.f1_stdev_per_episode.values()))
            for s in stats.values() if s.f1_stdev_per_episode]
    if not rows:
        return
    rows.sort(key=lambda t: t[1])
    labels = [r[0].model for r in rows]
    values = [r[1] for r in rows]
    colors = ["#2ca02c" if v < F1_STDEV_STABLE else "#f0a020" if v < F1_STDEV_WOBBLY else "#d62728" for v in values]

    fig, ax = plt.subplots(figsize=(11, max(5, 0.40 * len(rows))))
    bars = ax.barh(labels, values, color=colors, edgecolor="black", linewidth=0.4)
    for bar, v in zip(bars, values):
        ax.text(v + max(values) * 0.01, bar.get_y() + bar.get_height() / 2,
                f"{v:.4f}", va="center", fontsize=8)
    ax.axvline(F1_STDEV_STABLE, color="gray", linestyle=":", linewidth=0.8, alpha=0.7)
    ax.axvline(F1_STDEV_WOBBLY, color="gray", linestyle=":", linewidth=0.8, alpha=0.7)
    ax.set_xlabel("Mean F1 stdev across episodes (lower is more deterministic at temp=0)", fontsize=10)
    ax.set_title("Trial-to-trial F1 variance per model", fontsize=11, fontweight="bold")
    ax.grid(True, axis="x", alpha=0.3)
    fig.tight_layout()
    fig.savefig(path, format="svg", bbox_inches="tight")
    plt.close(fig)


def _render_detection_bucket_chart(
    detection_buckets: dict[str, dict[str, dict[str, list[bool]]]],
    bucket_kind: str,
    bucket_order: list[str],
    title: str,
    path: Path,
) -> None:
    """Heatmap of detection rate per (model, bucket) for a given bucket kind
    ('length' or 'position'). Cell text shows rate + sample size."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np

    if not detection_buckets:
        return
    # Sort models by overall detection rate (sum of hits / total) for that bucket kind, desc
    def overall_rate(model):
        all_hits = []
        for label in bucket_order:
            all_hits.extend(detection_buckets[model].get(bucket_kind, {}).get(label, []))
        return sum(all_hits) / len(all_hits) if all_hits else 0
    models_sorted = sorted(detection_buckets, key=overall_rate, reverse=True)
    if not models_sorted:
        return

    matrix = np.full((len(models_sorted), len(bucket_order)), np.nan)
    sizes = [[0] * len(bucket_order) for _ in models_sorted]
    for i, model in enumerate(models_sorted):
        buckets = detection_buckets[model].get(bucket_kind, {})
        for j, label in enumerate(bucket_order):
            hits = buckets.get(label, [])
            if hits:
                matrix[i, j] = sum(hits) / len(hits)
                sizes[i][j] = len(hits)

    fig, ax = plt.subplots(figsize=(max(7, 1.7 * len(bucket_order)), max(5, 0.40 * len(models_sorted))))
    masked = np.ma.masked_invalid(matrix)
    im = ax.imshow(masked, cmap="RdYlGn", vmin=0, vmax=1, aspect="auto")
    cmap = im.get_cmap().copy(); cmap.set_bad(color="#eeeeee"); im.set_cmap(cmap)
    ax.set_xticks(range(len(bucket_order)))
    ax.set_xticklabels(bucket_order, rotation=20, ha="right", fontsize=9)
    ax.set_yticks(range(len(models_sorted)))
    ax.set_yticklabels(models_sorted, fontsize=9)
    for i in range(len(models_sorted)):
        for j in range(len(bucket_order)):
            v = matrix[i, j]
            if np.isnan(v):
                continue
            color = "black" if 0.3 < v < 0.7 else "white"
            ax.text(j, i, f"{v:.2f}\n(n={sizes[i][j]})", ha="center", va="center",
                    fontsize=7, color=color)
    ax.set_title(title, fontsize=11, fontweight="bold")
    fig.colorbar(im, ax=ax, label="detection rate", shrink=0.7)
    fig.tight_layout()
    fig.savefig(path, format="svg", bbox_inches="tight")
    plt.close(fig)


def _render_parser_stress_chart(stats: dict[str, ModelStats], path: Path) -> None:
    """Heatmap of extraction-method usage per model. Rows = models, columns =
    methods sorted by total usage (most common first), cell = call count."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np

    methods_global: dict[str, int] = {}
    for s in stats.values():
        for m, n in s.extraction_method_counts.items():
            methods_global[m] = methods_global.get(m, 0) + n
    methods = sorted(methods_global, key=lambda m: -methods_global[m])
    if not methods:
        return
    models_sorted = sorted(
        stats.values(),
        key=lambda s: -(s.extraction_method_counts.get("json_array_direct", 0)
                        / max(sum(s.extraction_method_counts.values()), 1)),
    )

    matrix = np.zeros((len(models_sorted), len(methods)), dtype=int)
    for i, s in enumerate(models_sorted):
        for j, m in enumerate(methods):
            matrix[i, j] = s.extraction_method_counts.get(m, 0)

    fig, ax = plt.subplots(figsize=(max(10, 1.4 * len(methods)), max(5, 0.40 * len(models_sorted))))
    im = ax.imshow(matrix, cmap="YlOrRd", aspect="auto")
    ax.set_xticks(range(len(methods)))
    ax.set_xticklabels(methods, rotation=25, ha="right", fontsize=8)
    ax.set_yticks(range(len(models_sorted)))
    ax.set_yticklabels([s.model for s in models_sorted], fontsize=9)
    max_v = matrix.max() if matrix.size else 1
    for i in range(len(models_sorted)):
        for j in range(len(methods)):
            v = matrix[i, j]
            if v == 0:
                continue
            color = "white" if v > max_v * 0.6 else "black"
            ax.text(j, i, str(v), ha="center", va="center", fontsize=7, color=color)
    ax.set_title(
        "Extraction-method usage per model (cell = call count)\n"
        "Models at top use the clean json_array_direct path most often",
        fontsize=11, fontweight="bold",
    )
    fig.colorbar(im, ax=ax, label="call count", shrink=0.7)
    fig.tight_layout()
    fig.savefig(path, format="svg", bbox_inches="tight")
    plt.close(fig)


