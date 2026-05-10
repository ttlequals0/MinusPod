"""Render Markdown report from calls.jsonl + episode_results.jsonl + corpus."""
from __future__ import annotations

import json
import statistics
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from . import metrics, pricing
from .corpus import Episode
from .storage import read_jsonl

DEFAULT_IOU_THRESHOLD = 0.5
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
    cost_per_tp: float | None = None
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
    tokens_per_detected_ad: float | None = None


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
    calls = list(read_jsonl(calls_path))
    if not calls:
        output_path.write_text("# MinusPod LLM Benchmark Report\n\nNo benchmark data yet. Run `benchmark run` first.\n")
        return

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
        _render_run_metadata(calls, pricing_snapshot=pricing_snapshot),
    ]

    body = "\n\n".join(s for s in sections if s) + "\n"
    output_path.write_text("# MinusPod LLM Benchmark Report\n\n" + body)

    assets_dir.mkdir(parents=True, exist_ok=True)
    _render_pareto(active, assets_dir / "pareto.svg")
    _render_compliance(active, assets_dir / "compliance.svg")
    _render_episode_heatmap(active, episodes, assets_dir / "episodes.svg")
    _render_calibration_chart(extras.calibration, assets_dir / "calibration.svg")
    _render_latency_tail_chart(active, assets_dir / "latency_tail.svg")


@dataclass
class _Extras:
    """Side data computed during aggregation that doesn't belong on ModelStats."""
    calibration: dict[str, list[tuple[float, bool]]]    # model -> [(confidence, is_tp), ...]
    agreement: dict[tuple[str, int], dict[str, int]]    # (episode, window_idx) -> {model: n_predicted_ads}
    detection_buckets: dict[str, dict[str, dict[str, list[bool]]]]
    # detection_buckets[model][bucket_kind][bucket_label] -> list of bool (was each truth-ad in this bucket detected?)
    output_tokens_per_model: dict[str, int]
    detected_ads_per_model: dict[str, int]
    response_times_per_model: dict[str, list[int]]


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
        output_tokens_per_model[rec["model"]] += int(rec.get("output_tokens", 0))
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
                length = ad.end - ad.start
                length_bucket = "short (<30s)" if length < 30 else "medium (30-90s)" if length < 90 else "long (>=90s)"
                if duration > 0:
                    rel = ad.start / duration
                    pos_bucket = "pre-roll (<10%)" if rel < 0.10 else "post-roll (>90%)" if rel > 0.90 else "mid-roll (10-90%)"
                else:
                    pos_bucket = "unknown"
                hit = ti in matched_truth_idxs
                detection_buckets[model]["length"][length_bucket].append(hit)
                detection_buckets[model]["position"][pos_bucket].append(hit)
        stats.trial_costs.append(_recompute_total_cost(records, pricing_snapshot))
        stats.trial_response_times.append(sum(r.get("response_time_ms", 0) for r in records))

    out: dict[str, ModelStats] = {}
    models_seen: set[str] = {model for (model, _) in me} | set(response_times_per_model)
    for model in models_seen:
        ms = ModelStats(model=model)
        all_start_maes: list[float] = []
        all_end_maes: list[float] = []
        for (m, ep_id), s in me.items():
            if m != model:
                continue
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
        if ms.tp_total > 0:
            ms.cost_per_tp = ms.total_episode_cost / ms.tp_total
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
        if ms.detected_ads_total > 0:
            ms.tokens_per_detected_ad = ms.output_tokens_total / ms.detected_ads_total
        out[model] = ms
    extras = _Extras(
        calibration=dict(calibration),
        agreement=dict(agreement),
        detection_buckets={k: {b: dict(buckets) for b, buckets in v.items()} for k, v in detection_buckets.items()},
        output_tokens_per_model=dict(output_tokens_per_model),
        detected_ads_per_model=dict(detected_ads_per_model),
        response_times_per_model=dict(response_times_per_model),
    )
    return out, extras


def _recompute_total_cost(records: list[dict], snap: pricing.PricingSnapshot) -> float:
    total = 0.0
    for r in records:
        price = snap.lookup(r["model"])
        if price is None:
            continue
        _, _, cost = pricing.cost_usd(price, input_tokens=int(r.get("input_tokens", 0)), output_tokens=int(r.get("output_tokens", 0)))
        total += cost
    return total


def _start(ad: dict) -> float:
    return float(ad.get("start", ad.get("start_time", 0.0)))


def _end(ad: dict) -> float:
    return float(ad.get("end", ad.get("end_time", 0.0)))


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

    lines = ["## Quick Comparison", "", "| " + " | ".join(header) + " |", "|" + "|".join("---" for _ in header) + "|"]
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
        "- **Extraction method**: the route the parser took to recover the ad list -- `json_array_direct` is the cleanest; method names with `regex_*` mean the JSON itself was malformed and we fell back to text matching.\n"
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

    # Classify each error message into a coarse bucket so the per-bucket call-out is meaningful.
    def bucket(msg: str) -> str:
        m = msg.lower()
        if "data_inspection_failed" in m or "inappropriate content" in m: return "Provider content moderation rejection"
        if "rate" in m and "limit" in m: return "Rate-limited"
        if "timeout" in m: return "Timeout"
        if "404" in m: return "Unknown model (404)"
        if "temperature" in m and "deprecated" in m: return "Deprecated parameter (`temperature`)"
        if "401" in m or "unauthor" in m: return "Auth failure"
        if "5" in m[:5] and "00" in m[:8]: return "Server-side 5xx"
        return "Other"

    by_bucket: dict[str, list[dict]] = defaultdict(list)
    by_model: dict[str, int] = defaultdict(int)
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
        "| Category | Calls | Affected models |",
        "|----------|------:|-----------------|",
    ]
    for cat, recs in sorted(by_bucket.items(), key=lambda x: -len(x[1])):
        affected = sorted({r["model"] for r in recs})
        lines.append(f"| {cat} | {len(recs)} | {', '.join(f'`{m}`' for m in affected)} |")

    lines += ["", "### Per-model error count", "", "| Model | Errors | of total |", "|---|---:|---:|"]
    for m in sorted(by_model, key=lambda k: -by_model[k]):
        total = sum(1 for r in calls if r["model"] == m)
        lines.append(f"| `{m}` | {by_model[m]} | {by_model[m]}/{total} ({by_model[m] * 100.0 / total:.1f}%) |")

    lines += ["", "### Sample messages (first 3 per category)", ""]
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
        "- **Content moderation rejections** (Alibaba on Qwen, Google on Gemma, sometimes others): the provider's classifier blocks the prompt before the model runs. For ad detection on real podcast transcripts, this can happen on episodes with adult content, profanity, or politically sensitive topics. Rate is small but non-zero -- plan for it.",
        "- **Deprecated parameters**: the Claude 4.x family rejects `temperature`. The benchmark memoizes this per-process and retries without, but it tells you which models you cannot pass legacy sampling controls to.",
        "- **Rate limits**: tail-latency or 429s under load -- not a model-quality issue but determines whether a given provider is operationally viable for your throughput.",
        "",
    ]
    return "\n".join(lines)


def _render_charts_section() -> str:
    return (
        "## Charts\n\n"
        "### Cost vs F1 (Pareto)\n\n"
        "Each model is one colored point. Lower-left is unhelpful (expensive, inaccurate). Upper-left is the sweet spot (accurate, cheap). The legend below the chart shows each model's color next to its F1 and cost-per-episode.\n\n"
        "![Cost vs F1 by model](report_assets/pareto.svg)\n\n"
        "### JSON schema compliance\n\n"
        "Fraction of each model's responses that parsed as a clean JSON array. 1.0 means every response came back exactly as requested; lower numbers mean the parser had to recover from markdown fences, object wrappers, or extra fields.\n\n"
        "![JSON compliance per model](report_assets/compliance.svg)\n\n"
        "### F1 by episode (heatmap)\n\n"
        "F1 score for each (model, episode) pair. Greener is more accurate, redder is less. The no-ad episode is excluded -- it has no F1 because it's a PASS/FAIL negative control.\n\n"
        "![F1 score per model and episode](report_assets/episodes.svg)\n\n"
        "### Confidence calibration (reliability diagram)\n\n"
        "Each line is one model. The x-axis is the model's self-reported confidence on its predictions (binned). The y-axis is the actual hit rate within that bin -- the fraction that turned out to be true positives at IoU >= 0.5. A model whose line tracks the diagonal is calibrated; lines below the diagonal are overconfident.\n\n"
        "![Confidence calibration per model](report_assets/calibration.svg)\n\n"
        "### Latency percentiles\n\n"
        "p50, p90, p99, and max per model on a log scale. The gap between p99 and max indicates how heavy the tail is. For OpenRouter-routed models, the tail also includes upstream provider load.\n\n"
        "![Latency percentiles per model](report_assets/latency_tail.svg)\n"
    )


def _render_per_model_detail(stats: dict[str, ModelStats]) -> str:
    lines = ["### Per-Model Detail", ""]
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
        if s.schema_violations_total:
            lines.append(f"- Schema violations: {s.schema_violations_total}")
        if s.extra_key_names:
            lines.append(f"- Extra keys observed: {', '.join(sorted(s.extra_key_names))}")
        lines.append("")
    return "\n".join(lines)


def _render_per_episode_detail(stats: dict[str, ModelStats], episodes: list[Episode]) -> str:
    lines = ["### Per-Episode Detail", ""]
    for ep in episodes:
        lines.append(f"#### `{ep.ep_id}` -- {ep.metadata.title}\n")
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
    lines = ["### Parser Stress Test", ""]
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
        "### Methodology",
        "",
        f"- Trials per (model, episode): **{cfg.run.trials}**, temperature {cfg.run.temperature}",
        f"- max_tokens: 4096 (matches MinusPod production)",
        f"- response_format: {cfg.run.response_format} (with prompt-injection fallback when provider rejects native)",
        f"- Window size: 10 min, overlap: 3 min (imported from MinusPod's create_windows)",
        f"- Pricing snapshot: {pricing_snapshot.captured_at}",
        f"- Corpus episodes: {len(episodes)}",
    ]
    return "\n".join(lines)


def _render_run_metadata(calls: list[dict], *, pricing_snapshot: pricing.PricingSnapshot) -> str:
    total_calls = len(calls)
    successful = sum(1 for c in calls if not c.get("error"))
    failed = total_calls - successful
    lifetime_actual = sum(float(c.get("total_cost_usd_at_runtime", 0.0)) for c in calls)
    lines = [
        "### Run Metadata",
        "",
        f"- Report generated: {datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}",
        f"- Total LLM calls recorded: {total_calls}",
        f"- Successful: {successful}",
        f"- Failed: {failed}",
        f"- Lifetime actual spend (sum of at-runtime costs): ${lifetime_actual:.4f}",
        f"- Active pricing snapshot: {pricing_snapshot.captured_at}",
    ]
    return "\n".join(lines)


def _avg_f1(stats: ModelStats) -> float:
    values = list(stats.f1_per_episode.values())
    return statistics.fmean(values) if values else 0.0


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

    ax.set_xlabel("Cost per episode (USD) -- lower is better", fontsize=10)
    ax.set_ylabel("F1 score (accuracy, 0-1) -- higher is better", fontsize=10)
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
    fig.savefig(path, format="svg")
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
    fig.savefig(path, format="svg")
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
    fig.savefig(path, format="svg")
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
        "For ads that match the truth at IoU >= 0.5, how far off were the predicted start and end timestamps? Lower is better. A model can hit F1 cleanly while still being 20s off on every boundary -- bad for any pipeline that cuts the audio.",
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
    bins = [(0.0, 0.7), (0.7, 0.9), (0.9, 0.95), (0.95, 0.99), (0.99, 1.001)]
    bin_labels = ["0.00-0.70", "0.70-0.90", "0.90-0.95", "0.95-0.99", "0.99+"]
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
    """Full latency percentile table including p99 and max -- not just p50/p95
    -- because the tail is what matters for production capacity planning."""
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
    output budget on chain-of-thought even when finding only 1-2 ads -- that
    cost shows up here even if total cost looks competitive."""
    lines = [
        "## Output token efficiency",
        "",
        "How many output tokens the model spent per detected ad. Lower is more concise -- the model finds an ad and returns the JSON. Higher means the model is producing a lot of text the parser will discard, which costs you whether or not the answer is right.",
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
        "All trials run at temperature 0.0. If a model produces stable output you'd expect the F1 stdev across trials to be near zero. Higher numbers mean the model is non-deterministic even at temp=0 -- which is fine to know, but means you cannot trust a single trial's number for that model.",
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
    bins = collections_Counter()
    for (_, _), per_model in agreement.items():
        n_voted = sum(1 for v in per_model.values() if v > 0)
        bins[n_voted] += 1
    total_windows = sum(bins.values())
    lines = [
        "## Cross-model agreement",
        "",
        f"For each of the {total_windows} (episode, window, trial-equivalent) entries, how many of the {n_models} active models predicted at least one ad? High-agreement windows are unambiguous ads (or unambiguously not ads). Low-agreement windows are where individual models disagree -- candidates for ensemble voting if you want a cheap accuracy boost.",
        "",
        "| Models predicting an ad | Window count | Share |",
        "|---:|---:|---:|",
    ]
    for k in sorted(bins):
        share = bins[k] / total_windows if total_windows else 0
        lines.append(f"| {k} of {n_models} | {bins[k]} | {share * 100:.1f}% |")
    lines += [
        "",
        "Read this as: rows near the top are windows where the field disagrees (most models said no, a few said yes -- usually false positives); rows near the bottom are windows where the field broadly agrees (typical of clear sponsor reads).",
    ]
    return "\n".join(lines)


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
    """Reliability diagram: x = self-reported confidence bin, y = actual hit rate.
    Diagonal reference line means perfect calibration."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    bins = [(0.0, 0.7), (0.7, 0.9), (0.9, 0.95), (0.95, 0.99), (0.99, 1.001)]
    bin_centers = [(lo + min(hi, 1.0)) / 2 for lo, hi in bins]

    cmap = plt.get_cmap("tab20")
    fig, ax = plt.subplots(figsize=(11, 7))
    ax.plot([0, 1], [0, 1], "--", color="gray", alpha=0.5, label="perfect calibration")
    plotted = 0
    for i, model in enumerate(sorted(calibration)):
        pairs = calibration[model]
        if len(pairs) < 5:
            continue
        xs, ys = [], []
        for c, (lo, hi) in zip(bin_centers, bins):
            ins = [t for conf, t in pairs if lo <= conf < hi]
            if not ins:
                continue
            xs.append(c)
            ys.append(sum(ins) / len(ins))
        if not xs:
            continue
        ax.plot(xs, ys, marker="o", color=cmap(plotted % 20), linewidth=1.4, markersize=6, label=model)
        plotted += 1
    ax.set_xlabel("Self-reported confidence (bin center)", fontsize=10)
    ax.set_ylabel("Actual hit rate (fraction TP)", fontsize=10)
    ax.set_title("Confidence calibration -- below diagonal means overconfident", fontsize=12, fontweight="bold")
    ax.set_xlim(0, 1.02)
    ax.set_ylim(-0.02, 1.02)
    ax.grid(True, alpha=0.3)
    ncol = 2 if plotted > 6 else 1
    fig.legend(loc="lower center", bbox_to_anchor=(0.5, 0.02), ncol=ncol, fontsize=9, frameon=True, edgecolor="lightgray")
    rows = (plotted + 1 + ncol - 1) // ncol
    bottom = min(0.50, 0.10 + 0.04 * rows)
    fig.subplots_adjust(left=0.10, right=0.96, top=0.93, bottom=bottom)
    fig.savefig(path, format="svg")
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
    ax.set_xlabel("Seconds (log scale) -- lower is better", fontsize=10)
    ax.set_title("Latency percentiles per model", fontsize=11, fontweight="bold")
    ax.grid(True, axis="x", alpha=0.3, which="both")
    ax.legend(loc="lower right", fontsize=9)
    fig.tight_layout()
    fig.savefig(path, format="svg")
    plt.close(fig)


# Local alias to avoid importing collections at module top-level just for one Counter.
import collections as _collections  # noqa: E402
collections_Counter = _collections.Counter
