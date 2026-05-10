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
    trial_costs: list[float] = field(default_factory=list)
    trial_response_times: list[int] = field(default_factory=list)
    no_ad_passes: list[bool] = field(default_factory=list)
    no_ad_fp_counts: list[int] = field(default_factory=list)


@dataclass
class ModelStats:
    model: str
    f1_per_episode: dict[str, float] = field(default_factory=dict)
    f1_stdev_per_episode: dict[str, float] = field(default_factory=dict)
    no_ad_pass: dict[str, bool] = field(default_factory=dict)
    no_ad_fp_count: dict[str, int] = field(default_factory=dict)
    total_episode_cost: float = 0.0
    p50_call_latency_ms: float = 0.0
    p95_call_latency_ms: float = 0.0
    json_compliance_mean: float = 0.0
    parse_failure_rate: float = 0.0
    extraction_method_counts: dict[str, int] = field(default_factory=dict)
    schema_violations_total: int = 0
    extra_key_names: set[str] = field(default_factory=set)


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

    by_model = _aggregate(calls, episodes, pricing_snapshot=pricing_snapshot)
    deprecated_ids = {m.id for m in cfg.models if m.deprecated}
    active = {mid: s for mid, s in by_model.items() if mid not in deprecated_ids}
    deprecated = {mid: s for mid, s in by_model.items() if mid in deprecated_ids}

    sections = [
        _render_how_to_read(),
        _render_tldr(active, episodes),
        _render_charts_section(),
        _render_failures(calls),
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


def _aggregate(
    calls: list[dict],
    episodes: list[Episode],
    *,
    pricing_snapshot: pricing.PricingSnapshot,
) -> dict[str, ModelStats]:
    truths_by_episode = {ep.ep_id: ep for ep in episodes}
    me: dict[tuple[str, str], ModelEpisodeStats] = {}
    response_times_per_model: dict[str, list[int]] = defaultdict(list)
    method_counts: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    compliance_values_per_model: dict[str, list[float]] = defaultdict(list)
    parse_failures_per_model: dict[str, int] = defaultdict(int)
    parse_total_per_model: dict[str, int] = defaultdict(int)
    schema_totals_per_model: dict[str, int] = defaultdict(int)
    extra_keys_per_model: dict[str, set[str]] = defaultdict(set)

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

    for (model, ep_id, trial), records in by_trial.items():
        ep = truths_by_episode.get(ep_id)
        if ep is None:
            continue
        if len(records) < len(ep.windows):
            continue
        records.sort(key=lambda r: r["window_index"])
        per_window_pred: list[list[tuple[float, float]]] = [
            [(_start(ad), _end(ad)) for ad in (r.get("parsed_ads") or [])]
            for r in records
        ]
        flat_preds: list[tuple[float, float]] = [p for w in per_window_pred for p in w]

        stats = me.setdefault((model, ep_id), ModelEpisodeStats(model=model, episode_id=ep_id))
        if ep.truth.is_no_ad_episode:
            res = metrics.no_ad_score(per_window_pred)
            stats.no_ad_passes.append(res.passed)
            stats.no_ad_fp_counts.append(res.false_positive_count)
        else:
            truth_ranges = [(ad.start, ad.end) for ad in ep.truth.ads]
            r = metrics.match_predictions(flat_preds, truth_ranges, threshold=DEFAULT_IOU_THRESHOLD)
            stats.trial_f1s.append(r.f1)
        stats.trial_costs.append(_recompute_total_cost(records, pricing_snapshot))
        stats.trial_response_times.append(sum(r.get("response_time_ms", 0) for r in records))

    out: dict[str, ModelStats] = {}
    models_seen: set[str] = {model for (model, _) in me} | set(response_times_per_model)
    for model in models_seen:
        ms = ModelStats(model=model)
        for (m, ep_id), s in me.items():
            if m != model:
                continue
            if s.trial_f1s:
                ms.f1_per_episode[ep_id] = statistics.fmean(s.trial_f1s)
                ms.f1_stdev_per_episode[ep_id] = metrics.trial_stdev(s.trial_f1s)
            if s.no_ad_passes:
                ms.no_ad_pass[ep_id] = all(s.no_ad_passes)
                ms.no_ad_fp_count[ep_id] = max(s.no_ad_fp_counts) if s.no_ad_fp_counts else 0
            if s.trial_costs:
                ms.total_episode_cost += statistics.fmean(s.trial_costs)
        rts = sorted(response_times_per_model[model])
        if rts:
            ms.p50_call_latency_ms = _percentile(rts, 50)
            ms.p95_call_latency_ms = _percentile(rts, 95)
        comps = compliance_values_per_model[model]
        ms.json_compliance_mean = statistics.fmean(comps) if comps else 0.0
        if parse_total_per_model[model]:
            ms.parse_failure_rate = parse_failures_per_model[model] / parse_total_per_model[model]
        ms.extraction_method_counts = dict(method_counts[model])
        ms.schema_violations_total = schema_totals_per_model[model]
        ms.extra_key_names = extra_keys_per_model[model]
        out[model] = ms
    return out


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
    value_rows = sorted(
        (s for s in stats.values() if s.total_episode_cost > 0),
        key=lambda s: _avg_f1(s) / s.total_episode_cost,
        reverse=True,
    )
    lines = ["## TL;DR", "", "### Best Accuracy (F1 @ IoU >= 0.5)", ""]
    lines.append("| Rank | Model | F1 | Cost / episode | p50 latency | JSON compliance |")
    lines.append("|------|-------|----|----------------|-------------|-----------------|")
    for i, s in enumerate(accuracy_rows, 1):
        lines.append(
            f"| {i} | `{s.model}` | {_avg_f1(s):.3f} | ${s.total_episode_cost:.4f} | "
            f"{s.p50_call_latency_ms / 1000:.1f}s | {s.json_compliance_mean:.2f} |"
        )
    lines += ["", "### Best Value (F1 per dollar)", ""]
    lines.append("| Rank | Model | F1/$ | F1 | Cost / episode |")
    lines.append("|------|-------|------|----|----------------|")
    for i, s in enumerate(value_rows, 1):
        lines.append(
            f"| {i} | `{s.model}` | {_avg_f1(s) / s.total_episode_cost:.2f} | "
            f"{_avg_f1(s):.3f} | ${s.total_episode_cost:.4f} |"
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
        "### Cost vs Accuracy (Pareto)\n\n"
        "Each model is one numbered point. Lower-left = unhelpful (expensive, inaccurate). Upper-left = the sweet spot (accurate, cheap). The legend maps numbers back to model IDs.\n\n"
        "![Cost vs F1 by model](report_assets/pareto.svg)\n\n"
        "### JSON schema compliance\n\n"
        "Fraction of each model's responses that parsed as a clean JSON array. 1.0 = perfect.\n\n"
        "![JSON compliance per model](report_assets/compliance.svg)\n\n"
        "### F1 by episode (heatmap)\n\n"
        "F1 score for each (model, episode) pair. Greener = more accurate, redder = less. The no-ad episode is excluded (it doesn't have an F1 -- it's PASS/FAIL on the negative control).\n\n"
        "![F1 score per model and episode](report_assets/episodes.svg)\n"
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
    """Numbered scatter + legend so labels never overlap each other."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    points = [(s, _avg_f1(s)) for s in stats.values()]
    points = [(s, f1) for s, f1 in points if not (f1 == 0 and s.total_episode_cost == 0)]
    points.sort(key=lambda t: (-t[1], t[0].total_episode_cost))  # rank by F1 desc, then cost asc

    fig, ax = plt.subplots(figsize=(11, 6.5))
    for i, (s, f1) in enumerate(points, 1):
        x = s.total_episode_cost
        y = f1
        ax.scatter(x, y, s=140, edgecolors="black", linewidths=0.6, zorder=3)
        ax.annotate(str(i), (x, y), ha="center", va="center", fontsize=8, fontweight="bold", zorder=4)
    ax.set_xlabel("Cost per episode (USD) -- lower is better", fontsize=10)
    ax.set_ylabel("F1 score (accuracy, 0-1) -- higher is better", fontsize=10)
    ax.set_title("Cost vs F1 by model", fontsize=12, fontweight="bold")
    ax.grid(True, alpha=0.3)

    # Legend mapping numbers -> model IDs, sorted by rank
    legend_lines = [f"{i}. {s.model}  (F1 {f1:.3f}, ${s.total_episode_cost:.4f})" for i, (s, f1) in enumerate(points, 1)]
    legend_text = "\n".join(legend_lines)
    fig.text(0.78, 0.5, legend_text, fontsize=8, family="monospace",
             verticalalignment="center", bbox=dict(boxstyle="round,pad=0.5", facecolor="white", edgecolor="lightgray"))
    fig.subplots_adjust(left=0.08, right=0.75, top=0.92, bottom=0.10)
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
