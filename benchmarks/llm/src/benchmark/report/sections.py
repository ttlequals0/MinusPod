"""Markdown section builders for the benchmark report."""
from __future__ import annotations

import math
import re
import statistics
from collections import Counter, defaultdict

from utils.time import utc_now_iso

from .. import pricing
from ..corpus import Episode
from .aggregate import (
    CALIBRATION_BIN_LABELS,
    CALIBRATION_BINS,
    ModelStats,
    _assign_tiers,
    _avg_f1,
    _ci_half_width,
    _per_model_alignment,
)


# HTTP 5xx detector for the failures classifier. Matches "500", "502", etc.
# but not arbitrary substrings like "5 minutes".
_SERVER_5XX_RE = re.compile(r"\b5\d{2}\b")


def _reliability_flags(s: ModelStats) -> str:
    """Flags that caveat a ranking without reordering it."""
    flags: list[str] = []
    if s.json_compliance_mean < 0.90:
        flags.append("(!) brittle JSON")
    if s.no_ad_pass and not all(s.no_ad_pass.values()):
        flags.append("(!) fails no-ad control")
    return " ".join(flags)


def _render_tldr(stats: dict[str, ModelStats], episodes: list[Episode]) -> str:
    cis = {s.model: _ci_half_width(list(s.f05_per_episode.values())) for s in stats.values()}

    accuracy_rows = sorted(stats.values(), key=lambda s: s.avg_f05, reverse=True)
    acc_tiers = _assign_tiers(accuracy_rows)
    paid_rows = [s for s in stats.values() if s.total_episode_cost > 0]
    free_rows = [s for s in stats.values() if s.total_episode_cost == 0]
    value_rows = sorted(paid_rows, key=lambda s: s.avg_f05 / s.total_episode_cost, reverse=True)
    free_by_f05 = sorted(free_rows, key=lambda s: s.avg_f05, reverse=True)

    lines = ["## TL;DR", "", "### Best Accuracy (F0.5 @ IoU >= 0.5)", ""]
    lines.append(
        "Models ranked by F0.5 (precision weighted 2x recall) against human-verified ground truth. "
        "MinusPod cuts the segments it flags, so cutting real content (a false positive) is worse than "
        "leaving an ad in (a false negative), and F0.5 penalizes it more. A model shares the tier above it "
        "unless it scores consistently lower across the same episodes (paired one-sided t-test, 95%); models "
        "that trade wins episode to episode share a tier, so order within a tier is not meaningful on this "
        f"{sum(1 for e in episodes if not e.truth.is_no_ad_episode)}-episode corpus. Flags caveat a model "
        "without changing its rank. Cost includes free-tier models (shown at $0.00)."
    )
    lines.append("")
    lines.append("| Tier | Model | F0.5 | 95% CI | Precision | Recall | F1 | Cost / episode | p50 latency | JSON compliance | Flags |")
    lines.append("|------|-------|------|--------|-----------|--------|----|----------------|-------------|-----------------|-------|")
    for tier, s in zip(acc_tiers, accuracy_rows):
        lines.append(
            f"| {tier} | `{s.model}` | {s.avg_f05:.3f} | +/-{cis[s.model]:.3f} | "
            f"{s.avg_precision:.3f} | {s.avg_recall:.3f} | {s.avg_f1:.3f} | "
            f"${s.total_episode_cost:.4f} | {s.p50_call_latency_ms / 1000:.1f}s | "
            f"{s.json_compliance_mean:.2f} | {_reliability_flags(s)} |"
        )

    lines += ["", "### Best Value (F0.5 per dollar)", ""]
    lines.append(
        "Paid-tier only, ranked by F0.5 per dollar. Free-tier models are excluded here because F0.5 / 0 is "
        "undefined; they are ranked separately under Best Free-Tier below. No confidence tiers on this table -- "
        "a point ratio does not group cleanly -- but the reliability flags still apply."
    )
    lines.append("")
    lines.append("| Rank | Model | F0.5/$ | F0.5 | F1 | Cost / episode | Flags |")
    lines.append("|------|-------|--------|------|----|----------------|-------|")
    for i, s in enumerate(value_rows, 1):
        lines.append(
            f"| {i} | `{s.model}` | {s.avg_f05 / s.total_episode_cost:.2f} | {s.avg_f05:.3f} | "
            f"{s.avg_f1:.3f} | ${s.total_episode_cost:.4f} | {_reliability_flags(s)} |"
        )

    if free_by_f05:
        free_tiers = _assign_tiers(free_by_f05)
        lines += ["", "### Best Free-Tier (F0.5)", ""]
        lines.append(
            "Models that came back at $0.00 cost, ranked by F0.5 with the same CI and flags as Best Accuracy. "
            "Tiers are computed within the free-tier set against its own leader, so a tier letter here is not "
            "comparable to the same letter in Best Accuracy. Free-tier eligibility on OpenRouter depends on the "
            "attribution headers wired into the "
            "benchmark (`HTTP-Referer`, `X-Title`); a model showing as free here may bill on your own deployment "
            "if those headers are missing."
        )
        lines.append("")
        lines.append("| Tier | Model | F0.5 | 95% CI | Precision | Recall | F1 | p50 latency | JSON compliance | Flags |")
        lines.append("|------|-------|------|--------|-----------|--------|----|-------------|-----------------|-------|")
        for tier, s in zip(free_tiers, free_by_f05):
            lines.append(
                f"| {tier} | `{s.model}` | {s.avg_f05:.3f} | +/-{cis[s.model]:.3f} | "
                f"{s.avg_precision:.3f} | {s.avg_recall:.3f} | {s.avg_f1:.3f} | "
                f"{s.p50_call_latency_ms / 1000:.1f}s | {s.json_compliance_mean:.2f} | {_reliability_flags(s)} |"
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
        "| **F1 stdev** | 0 to 1 | lower means more consistent | Standard deviation of F1 across the four ad-bearing episodes. High stdev = inconsistent across content types. |\n"
        "| **JSON mode** | `native` / `prompt-inject` / `mixed` | -- | How the model received its JSON-output instruction. `native` = provider accepted `response_format=json_object` for at least 95% of calls; `prompt-inject` = provider rejected it and the runner fell back to instructing JSON in the prompt for at least 95% of calls; `mixed` = neither path crossed the threshold (sample mostly comes from intermittent provider rejections). Reads from `json_format_used` in `calls.jsonl`. Useful when picking a model whose provider may not support native JSON mode -- a strong `JSON compliance` score from a `prompt-inject` model carries different weight than the same score from a `native` model. |\n\n"
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
        "Source data: [Best Accuracy](#best-accuracy-f05--iou--05), [Best Value](#best-value-f05-per-dollar), [Best Free-Tier](#best-free-tier-f05)\n\n"
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
        lines.append(
            f"- JSON mode: {s.json_format_primary} "
            f"({s.json_format_native_pct:.0%} native, "
            f"{s.json_format_total} calls)"
        )
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
    prompt_source: str = "live",
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
        f"- System prompt: {prompt_source}",
    ]
    return "\n".join(lines)


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

