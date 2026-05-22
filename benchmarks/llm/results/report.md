# MinusPod LLM Benchmark Report

## Table of Contents

- [Metric Key](#metric-key)
- [TL;DR](#tldr)
- [Charts](#charts)
- [Failures and provider issues](#failures-and-provider-issues)
- [Precision, recall, and FP/FN breakdown](#precision-recall-and-fpfn-breakdown)
- [Boundary accuracy](#boundary-accuracy)
- [Confidence calibration](#confidence-calibration)
- [Latency tail](#latency-tail)
- [Output token efficiency](#output-token-efficiency)
- [Trial variance (determinism check)](#trial-variance-determinism-check)
- [Cross-model agreement](#cross-model-agreement)
- [Detection rate by ad characteristic](#detection-rate-by-ad-characteristic)
- [Quick Comparison](#quick-comparison)
- [Detailed Results](#detailed-results)
- [Methodology](#methodology)
- [Transcript source](#transcript-source)
- [Run Metadata](#run-metadata)

## Metric Key

Quick reference for the columns in every table below.

| Metric | Range | Direction | What it means |
|--------|-------|-----------|---------------|
| **F1 (accuracy)** | 0 to 1 | higher is better | Combined score of precision and recall against the human-verified ground-truth ad spans. F1 = 0 means the model found nothing right; F1 = 1 means it found every ad with the correct boundaries. Uses IoU >= 0.5 (predicted span must overlap truth span by at least half) to count a match. |
| **Cost / episode** | USD | lower is better | Average dollars per episode at the current pricing snapshot. Recomputed from token counts so all rows compare at the same prices regardless of when the call ran. |
| **F1 / $** | ratio | higher is better | F1 divided by cost-per-episode. Cheap accurate models score highest. Free-tier models are rank-listed separately because the ratio is undefined. |
| **p50 / p95 latency** | seconds | lower is better, with caveats | Median (p50) and tail (p95) wall-clock response time. **Note**: for models routed through OpenRouter (everything except `claude-*`), this includes OpenRouter's queueing and upstream-provider latency, not just the model itself. Treat as a load/availability indicator, not a model-quality signal. |
| **JSON compliance** | 0 to 1 | higher is better | Fraction of responses that parsed as a clean JSON array matching the requested schema. 1.0 = always clean; lower = used object wrappers (`{ads: [...]}`), markdown fences, extra fields like `sponsor`, or required regex fallback to extract. |
| **No-ad episode** | PASS / FAIL | PASS desired | Negative-control test on `ep-ai-cloud-essentials` (which has no ads). PASS = zero predictions across all 15 windows. FAIL = the model false-positived on a non-ad segment, with the FP count shown. |
| **F1 stdev** | 0 to 1 | lower means more consistent | Standard deviation of F1 across the four ad-bearing episodes. High stdev = inconsistent across content types. |
| **JSON mode** | `native` / `prompt-inject` / `mixed` | -- | How the model received its JSON-output instruction. `native` = provider accepted `response_format=json_object` for at least 95% of calls; `prompt-inject` = provider rejected it and the runner fell back to instructing JSON in the prompt for at least 95% of calls; `mixed` = neither path crossed the threshold (sample mostly comes from intermittent provider rejections). Reads from `json_format_used` in `calls.jsonl`. Useful when picking a model whose provider may not support native JSON mode -- a strong `JSON compliance` score from a `prompt-inject` model carries different weight than the same score from a `native` model. |

### Glossary

- **IoU (intersection over union)**: how much two time ranges overlap, expressed as `(overlap) / (union)`. 0 means no overlap, 1 means identical ranges. We use IoU >= 0.5 as the threshold for a predicted ad to count as matching a truth ad.
- **Trial**: each (model, episode) pair runs 5 trials at temperature 0.0 to surface non-determinism. F1 numbers in tables are averaged across trials.
- **Window**: each episode is split into ~85-second sliding windows; the model judges each window independently. Per-window predictions are stitched together for episode-level scoring.
- **Schema violations**: number of times the response had at least one missing-required-field, wrong-type, or extra-key issue. Doesn't tank F1, but signals brittleness.
- **Extraction method**: the route the parser took to recover the ad list. `json_array_direct` is the cleanest; method names with `regex_*` mean the JSON itself was malformed and we fell back to text matching.


## TL;DR

### Best Accuracy (F1 @ IoU >= 0.5)

All models ranked by F1 against human-verified ground truth. Cost includes free-tier models (shown at $0.00).

| Rank | Model | F1 | F1 stdev | Cost / episode | p50 latency | JSON compliance | JSON mode |
|------|-------|----|----------|----------------|-------------|-----------------|-----------|
| 1 | `qwen/qwen3.6-plus` | 0.640 | 0.037 | $1.1813 | 39.9s | 1.00 | native |
| 2 | `qwen/qwen3.5-plus-02-15` | 0.627 | 0.031 | $1.2880 | 47.3s | 1.00 | native |
| 3 | `qwen/qwen3.6-flash` | 0.610 | 0.080 | $0.5817 | 13.1s | 1.00 | native |
| 4 | `openai/gpt-5.5` | 0.597 | 0.056 | $7.1204 | 6.4s | 0.88 | native |
| 5 | `google/gemini-3.5-flash` | 0.577 | 0.020 | $3.7363 | 5.2s | 1.00 | native |
| 6 | `qwen/qwen3.5-27b` | 0.574 | 0.148 | $3.4665 | 70.5s | 0.85 | native |
| 7 | `openai/gpt-5.4` | 0.573 | 0.061 | $2.7073 | 1.8s | 0.81 | native |
| 8 | `google/gemini-2.5-pro` | 0.566 | 0.037 | $4.1541 | 14.3s | 0.97 | native |
| 9 | `claude-opus-4-7` | 0.559 | 0.062 | $8.3310 | 2.2s | 1.00 | prompt-inject |
| 10 | `openai/gpt-5.4-mini` | 0.539 | 0.104 | $0.8159 | 1.2s | 0.81 | native |
| 11 | `openai/o3` | 0.523 | 0.138 | $3.2379 | 7.8s | 0.93 | native |
| 12 | `x-ai/grok-4.3` | 0.467 | 0.097 | $1.5978 | 4.0s | 1.00 | native |
| 13 | `deepseek/deepseek-v4-flash` | 0.438 | 0.113 | $0.1442 | 4.0s | 0.81 | native |
| 14 | `google/gemma-4-31b-it` | 0.437 | 0.073 | $0.1377 | 2.3s | 0.86 | native |
| 15 | `deepseek/deepseek-r1` | 0.433 | 0.132 | $1.1813 | 19.9s | 0.97 | native |
| 16 | `claude-sonnet-4-6` | 0.412 | 0.038 | $3.7663 | 1.4s | 0.97 | prompt-inject |
| 17 | `qwen/qwen3-235b-a22b-2507` | 0.400 | 0.076 | $0.0784 | 2.3s | 0.79 | native |
| 18 | `openai/gpt-oss-120b` | 0.399 | 0.127 | $0.0680 | 2.9s | 0.68 | native |
| 19 | `deepseek/deepseek-r1-0528` | 0.387 | 0.131 | $1.0738 | 16.0s | 0.89 | native |
| 20 | `nvidia/llama-3.3-nemotron-super-49b-v1.5` | 0.379 | 0.124 | $0.2283 | 24.0s | 0.72 | native |
| 21 | `cohere/command-a` | 0.377 | 0.037 | $2.8495 | 3.8s | 0.71 | native |
| 22 | `google/gemini-3.1-flash-lite` | 0.371 | 0.036 | $0.2941 | 0.8s | 0.96 | native |
| 23 | `moonshotai/kimi-k2.6` | 0.369 | 0.152 | $2.4862 | 36.2s | 0.58 | native |
| 24 | `meta-llama/llama-4-maverick` | 0.368 | 0.015 | $0.1610 | 1.0s | 0.81 | native |
| 25 | `mistralai/mistral-medium-3.1` | 0.340 | 0.043 | $0.4666 | 0.9s | 1.00 | native |
| 26 | `claude-haiku-4-5-20251001` | 0.339 | 0.001 | $1.2786 | 1.1s | 0.60 | prompt-inject |
| 27 | `deepseek/deepseek-v4-pro` | 0.338 | 0.204 | $0.6795 | 26.8s | 0.87 | native |
| 28 | `mistralai/codestral-2508` | 0.290 | 0.045 | $0.3456 | 0.7s | 1.00 | native |
| 29 | `google/gemini-2.5-flash-lite` | 0.279 | 0.021 | $0.1177 | 0.9s | 0.97 | native |
| 30 | `deepseek/deepseek-v3.2` | 0.275 | 0.176 | $0.2738 | 2.8s | 0.87 | native |
| 31 | `google/gemini-2.5-flash` | 0.270 | 0.000 | $0.3648 | 1.0s | 1.00 | native |
| 32 | `openai/gpt-3.5-turbo` | 0.264 | 0.012 | $0.5436 | 1.3s | 0.71 | native |
| 33 | `meta-llama/llama-3.3-70b-instruct` | 0.253 | 0.075 | $0.1076 | 1.4s | 0.56 | native |
| 34 | `mistralai/mistral-large-2512` | 0.230 | 0.015 | $0.5964 | 2.5s | 1.00 | native |
| 35 | `qwen/qwen3-14b` | 0.229 | 0.154 | $0.1346 | 20.9s | 0.27 | native |
| 36 | `deepseek/deepseek-r1-distill-llama-70b` | 0.222 | 0.090 | $0.7825 | 2.0s | 0.74 | native |
| 37 | `meta-llama/llama-4-scout` | 0.217 | 0.101 | $0.0861 | 0.8s | 0.81 | native |
| 38 | `nvidia/nemotron-nano-9b-v2` | 0.204 | 0.105 | $0.0872 | 12.4s | 0.92 | native |
| 39 | `meta-llama/llama-3.1-8b-instruct` | 0.183 | 0.113 | $0.0221 | 0.8s | 0.85 | native |
| 40 | `cohere/command-r-plus-08-2024` | 0.094 | 0.033 | $2.7520 | 0.9s | 0.98 | native |
| 41 | `openai/o4-mini` | 0.069 | 0.116 | $2.0113 | 6.9s | 0.05 | native |
| 42 | `microsoft/phi-4` | 0.051 | 0.057 | $0.0758 | 2.3s | 0.86 | native |
| 43 | `qwen/qwen3-8b` | 0.004 | 0.010 | $0.2734 | 58.4s | 0.01 | native |
| 44 | `mistralai/mistral-7b-instruct-v0.1` | 0.000 | 0.000 | $0.0301 | 7.0s | 0.17 | native |

### Best Value (F1 per dollar)

Paid-tier only. Free-tier models are excluded here because F1 / 0 is undefined; they are ranked separately under Best Free-Tier below.

| Rank | Model | F1/$ | F1 | Cost / episode |
|------|-------|------|----|----------------|
| 1 | `meta-llama/llama-3.1-8b-instruct` | 8.26 | 0.183 | $0.0221 |
| 2 | `openai/gpt-oss-120b` | 5.87 | 0.399 | $0.0680 |
| 3 | `qwen/qwen3-235b-a22b-2507` | 5.10 | 0.400 | $0.0784 |
| 4 | `google/gemma-4-31b-it` | 3.18 | 0.437 | $0.1377 |
| 5 | `deepseek/deepseek-v4-flash` | 3.04 | 0.438 | $0.1442 |
| 6 | `meta-llama/llama-4-scout` | 2.52 | 0.217 | $0.0861 |
| 7 | `google/gemini-2.5-flash-lite` | 2.37 | 0.279 | $0.1177 |
| 8 | `meta-llama/llama-3.3-70b-instruct` | 2.35 | 0.253 | $0.1076 |
| 9 | `nvidia/nemotron-nano-9b-v2` | 2.34 | 0.204 | $0.0872 |
| 10 | `meta-llama/llama-4-maverick` | 2.28 | 0.368 | $0.1610 |
| 11 | `qwen/qwen3-14b` | 1.70 | 0.229 | $0.1346 |
| 12 | `nvidia/llama-3.3-nemotron-super-49b-v1.5` | 1.66 | 0.379 | $0.2283 |
| 13 | `google/gemini-3.1-flash-lite` | 1.26 | 0.371 | $0.2941 |
| 14 | `qwen/qwen3.6-flash` | 1.05 | 0.610 | $0.5817 |
| 15 | `deepseek/deepseek-v3.2` | 1.00 | 0.275 | $0.2738 |
| 16 | `mistralai/codestral-2508` | 0.84 | 0.290 | $0.3456 |
| 17 | `google/gemini-2.5-flash` | 0.74 | 0.270 | $0.3648 |
| 18 | `mistralai/mistral-medium-3.1` | 0.73 | 0.340 | $0.4666 |
| 19 | `microsoft/phi-4` | 0.67 | 0.051 | $0.0758 |
| 20 | `openai/gpt-5.4-mini` | 0.66 | 0.539 | $0.8159 |
| 21 | `qwen/qwen3.6-plus` | 0.54 | 0.640 | $1.1813 |
| 22 | `deepseek/deepseek-v4-pro` | 0.50 | 0.338 | $0.6795 |
| 23 | `qwen/qwen3.5-plus-02-15` | 0.49 | 0.627 | $1.2880 |
| 24 | `openai/gpt-3.5-turbo` | 0.48 | 0.264 | $0.5436 |
| 25 | `mistralai/mistral-large-2512` | 0.39 | 0.230 | $0.5964 |
| 26 | `deepseek/deepseek-r1` | 0.37 | 0.433 | $1.1813 |
| 27 | `deepseek/deepseek-r1-0528` | 0.36 | 0.387 | $1.0738 |
| 28 | `x-ai/grok-4.3` | 0.29 | 0.467 | $1.5978 |
| 29 | `deepseek/deepseek-r1-distill-llama-70b` | 0.28 | 0.222 | $0.7825 |
| 30 | `claude-haiku-4-5-20251001` | 0.26 | 0.339 | $1.2786 |
| 31 | `openai/gpt-5.4` | 0.21 | 0.573 | $2.7073 |
| 32 | `qwen/qwen3.5-27b` | 0.17 | 0.574 | $3.4665 |
| 33 | `openai/o3` | 0.16 | 0.523 | $3.2379 |
| 34 | `google/gemini-3.5-flash` | 0.15 | 0.577 | $3.7363 |
| 35 | `moonshotai/kimi-k2.6` | 0.15 | 0.369 | $2.4862 |
| 36 | `google/gemini-2.5-pro` | 0.14 | 0.566 | $4.1541 |
| 37 | `cohere/command-a` | 0.13 | 0.377 | $2.8495 |
| 38 | `claude-sonnet-4-6` | 0.11 | 0.412 | $3.7663 |
| 39 | `openai/gpt-5.5` | 0.08 | 0.597 | $7.1204 |
| 40 | `claude-opus-4-7` | 0.07 | 0.559 | $8.3310 |
| 41 | `cohere/command-r-plus-08-2024` | 0.03 | 0.094 | $2.7520 |
| 42 | `openai/o4-mini` | 0.03 | 0.069 | $2.0113 |
| 43 | `qwen/qwen3-8b` | 0.02 | 0.004 | $0.2734 |
| 44 | `mistralai/mistral-7b-instruct-v0.1` | 0.00 | 0.000 | $0.0301 |

## Charts

### Cost vs F1 (Pareto)

Each model is one colored point. Lower-left is unhelpful (expensive, inaccurate). Upper-left is the sweet spot (accurate, cheap). The legend below the chart shows each model's color next to its F1 and cost-per-episode.

![Cost vs F1 by model](report_assets/pareto.svg)

Source data: [Best Accuracy](#best-accuracy-f1--iou--05), [Best Value](#best-value-f1-per-dollar), [Best Free-Tier](#best-free-tier-f1)

### JSON schema compliance

Fraction of each model's responses that parsed as a clean JSON array. 1.0 means every response came back exactly as requested; lower numbers mean the parser had to recover from markdown fences, object wrappers, or extra fields.

![JSON compliance per model](report_assets/compliance.svg)

Source data: [Per-Model Detail](#per-model-detail) (`JSON compliance` field)

### F1 by episode (heatmap)

F1 score for each (model, episode) pair. Greener is more accurate, redder is less. The no-ad episode is excluded. It has no F1 because it's a PASS/FAIL negative control.

![F1 score per model and episode](report_assets/episodes.svg)

Source data: [Quick Comparison](#quick-comparison), [Per-Episode Detail](#per-episode-detail)

### Confidence calibration (heatmap)

One row per model, one column per self-reported confidence bin. Cell text is the actual hit rate at that bin plus the sample size; cell color is the calibration error (actual minus bin midpoint). Red cells mean the model claimed high confidence but was usually wrong; green is well-calibrated; blue is underconfident. Empty cells mean the model never produced a prediction in that bin. Models are sorted from most overconfident at the top to most underconfident at the bottom.

![Confidence calibration per model](report_assets/calibration.svg)

Source data: [Confidence calibration](#confidence-calibration) table

### Latency percentiles

p50, p90, p99, and max per model on a log scale. The gap between p99 and max indicates how heavy the tail is. For OpenRouter-routed models, the tail also includes upstream provider load.

![Latency percentiles per model](report_assets/latency_tail.svg)

Source data: [Latency tail](#latency-tail) table

### Cross-model agreement (window distribution)

Histogram of how many models flagged at least one ad per (episode, window). The left side is windows nobody flagged (clear non-ad content), the right side is windows everyone flagged (clear sponsor reads). Bars in the middle are contested (some models said yes, some said no) and are candidates for ensemble voting or manual review. This view is anonymous (bars don't show which models contributed); the per-model breakdown is in the next chart.

![Cross-model agreement histogram](report_assets/agreement.svg)

Source data: [Cross-model agreement](#cross-model-agreement) table

### Per-model alignment with majority

Stacked horizontal bar per model. Green + blue segments are windows where the model voted with the majority (true positives + true negatives); orange is windows where it voted yes but most others voted no (likely false positive / hallucination); red is windows where it voted no but most others voted yes (likely missed real ad). Right-edge label is alignment rate. High alignment means the model tracks consensus; low alignment is either insight or noise depending on whether those broken-from-consensus calls were right.

![Per-model alignment with majority](report_assets/alignment.svg)

Source data: [Per-model alignment with consensus](#per-model-alignment-with-consensus) table

### Precision vs Recall (with F1 isocurves)

Scatter of precision (y) vs recall (x) for each model. Dashed gray lines are F1 isocurves; points on the same dashed line have the same F1. Top-right is ideal (high precision AND high recall). Top-left is cautious (high precision, low recall). Bottom-right is greedy (high recall, low precision). Useful for picking a model whose error profile matches your tolerance: precision-leaning for environments where false positives are expensive, recall-leaning for completeness-first.

![Precision vs recall scatter](report_assets/precision_recall.svg)

Source data: [Precision, recall, and FP/FN breakdown](#precision-recall-and-fpfn-breakdown) table

### Boundary accuracy (start + end MAE)

Stacked horizontal bars per model: blue is mean absolute error on the predicted ad START in seconds, orange is the same for END. Total error labeled at the right. Sorted by total ascending so the cleanest boundaries are at the top. Skewed bars (start much larger than end, or vice versa) mean the model systematically overshoots on one side. Relevant if you cut audio downstream.

![Boundary MAE per model](report_assets/boundary.svg)

Source data: [Boundary accuracy](#boundary-accuracy) table

### Token efficiency vs F1

Scatter of output tokens per detected ad (x, log scale) vs F1 (y). Upper-left is the efficient zone: high accuracy with few output tokens. Right-side points are reasoning-heavy models that emit chain-of-thought alongside their JSON. The chart answers whether the extra tokens buy more F1 or just burn output budget. A model that lands far right at modest F1 is paying for reasoning that didn't help.

![Token efficiency vs F1](report_assets/token_efficiency.svg)

Source data: [Output token efficiency](#output-token-efficiency) table

### Trial variance (determinism check)

Horizontal bars of mean F1 stdev across episodes per model. All trials run at temperature 0.0 so well-behaved models cluster near zero. Bars are color-graded: green below 0.02 (effectively deterministic), yellow 0.02-0.05 (slight noise), red above 0.05 (single-trial F1 numbers from this model should be treated with suspicion). Dotted reference lines mark the 0.02 and 0.05 thresholds.

![Trial F1 variance per model](report_assets/trial_variance.svg)

Source data: [Trial variance (determinism check)](#trial-variance-determinism-check) table

### Detection rate by ad length

Heatmap of model (row) vs ad-length bucket (column), cell = detection rate with sample size. Greener = caught more ads in that bucket; redder = missed more. Models are sorted by overall detection rate so the strongest are at the top. Empty (gray) cells mean that bucket had no truth ads for the corresponding model's trials.

![Detection rate by ad length](report_assets/detection_by_length.svg)

Source data: [Detection rate by ad characteristic > By ad length](#by-ad-length) table

### Detection rate by ad position

Same shape as the ad-length heatmap, but columns are episode position (pre-roll / mid-roll / post-roll). A common pattern: pre-roll is easy because of clear show-intro transitions; post-roll is harder because models near the end of long episodes often produce shorter responses or run out of context to anchor on.

![Detection rate by ad position](report_assets/detection_by_position.svg)

Source data: [Detection rate by ad characteristic > By ad position](#by-ad-position) table

### Parser stress (extraction-method usage)

Heatmap of model (row) vs extraction-method (column), cell = number of responses parsed via that method. Columns are ordered by total usage. `json_array_direct` is the clean path; everything else is a recovery path the parser had to take because the model added markdown fences, wrapped the array in an object, or returned malformed JSON. Models near the top of the chart use the clean path most often. They are operationally easier to consume.

![Parser stress heatmap](report_assets/parser_stress.svg)

Source data: [Parser stress test](#parser-stress-test) table


## Failures and provider issues

**3 call(s) failed out of 40040 total (0.01%).** Failures are excluded from F1 / cost calculations, but they often surface real production-relevant gotchas worth knowing.

### By category

Errors classified into coarse buckets so failure patterns are visible at a glance. A model showing up here doesn't mean it's broken. Some categories are provider-side (content moderation, rate limits) and tell you more about routing reliability than model quality.

| Category | Calls | Affected models |
|----------|------:|-----------------|
| Provider content moderation rejection | 3 | `qwen/qwen3.5-plus-02-15` |

### Per-model error count

Same errors grouped by model, with the failure rate as a fraction of that model's total calls. Rates under 1% are usually one-off provider hiccups; rates above 5% suggest the model isn't operationally viable for production with the current prompts and concurrency caps.

| Model | Errors | of total |
|---|---:|---:|
| `qwen/qwen3.5-plus-02-15` | 3 | 3/910 (0.3%) |

### Sample messages (first 3 per category)

First three raw error messages per category, so you can see what the provider actually returned without grepping calls.jsonl. Messages are truncated to ~240 characters; full text lives in `results/raw/calls.jsonl`.

**Provider content moderation rejection** (3)
- `qwen/qwen3.5-plus-02-15` on `ep-drink-champs-30c9a2d49f13` (trial 0, window 15): Error code: 400 - {'error': {'message': 'Provider returned error', 'code': 400, 'metadata': {'raw': '{"error":{"message":"<400> InternalError.Algo.DataInspectionFailed: Input text data may contain inappropriate content.","type":"data_inspec...
- `qwen/qwen3.5-plus-02-15` on `ep-drink-champs-30c9a2d49f13` (trial 1, window 15): Error code: 400 - {'error': {'message': 'Provider returned error', 'code': 400, 'metadata': {'raw': '{"error":{"message":"<400> InternalError.Algo.DataInspectionFailed: Input text data may contain inappropriate content.","type":"data_inspec...
- `qwen/qwen3.5-plus-02-15` on `ep-drink-champs-30c9a2d49f13` (trial 3, window 15): Error code: 400 - {'error': {'message': 'Provider returned error', 'code': 400, 'metadata': {'raw': '{"error":{"message":"<400> InternalError.Algo.DataInspectionFailed: Input text data may contain inappropriate content.","type":"data_inspec...

### Why this section exists

If you're picking a model for production, an aggregate compliance score doesn't tell you when the provider will simply refuse to answer. A few cases that have shown up here:

- **Content moderation rejections** (Alibaba on Qwen, Google on Gemma, sometimes others): the provider's classifier blocks the prompt before the model runs. For ad detection on real podcast transcripts, this can happen on episodes with adult content, profanity, or politically sensitive topics. Rate is small but non-zero; plan for it.
- **Deprecated parameters**: the Claude 4.x family rejects `temperature`. The benchmark memoizes this per-process and retries without, but it tells you which models you cannot pass legacy sampling controls to.
- **Rate limits**: tail-latency or 429s under load. Not a model-quality issue, but determines whether a given provider is operationally viable for your throughput.


## Precision, recall, and FP/FN breakdown

F1 collapses two failure modes into one number. A precision-leaning model misses ads but rarely flags non-ads; a recall-leaning model catches everything at the cost of false positives. Production tradeoffs hinge on which one you can tolerate.

### Column key

| Column | Meaning | Range |
|---|---|---|
| **TP** (true positive) | Predicted an ad and a real ad existed at that span (IoU >= 0.5) | 0 to total truth ads |
| **FP** (false positive) | Predicted an ad where no real ad existed | 0 to total predictions |
| **FN** (false negative) | Missed a real ad entirely (no prediction matched it at IoU >= 0.5) | 0 to total truth ads |
| **Precision** | `TP / (TP + FP)`. Of the ads the model claimed, how many were real? Higher means fewer false positives. | 0.000 to 1.000 |
| **Recall** | `TP / (TP + FN)`. Of the real ads, how many did the model find? Higher means fewer misses. | 0.000 to 1.000 |

Reading the table: high precision + low recall means the model is cautious. It rarely flags something that isn't an ad, but misses real ads. High recall + low precision means the opposite: catches everything but invents false positives. F1 is the harmonic mean of the two and rewards models that do both well.

| Model | Precision | Recall | TP | FP | FN |
|---|---:|---:|---:|---:|---:|
| `qwen/qwen3.6-plus` | 0.591 | 0.760 | 205 | 166 | 55 |
| `qwen/qwen3.5-plus-02-15` | 0.573 | 0.760 | 183 | 154 | 50 |
| `qwen/qwen3.6-flash` | 0.525 | 0.772 | 205 | 217 | 55 |
| `openai/gpt-5.5` | 0.554 | 0.701 | 185 | 153 | 75 |
| `google/gemini-3.5-flash` | 0.514 | 0.716 | 186 | 193 | 74 |
| `qwen/qwen3.5-27b` | 0.580 | 0.627 | 162 | 154 | 98 |
| `openai/gpt-5.4` | 0.511 | 0.721 | 190 | 224 | 70 |
| `google/gemini-2.5-pro` | 0.482 | 0.759 | 205 | 252 | 55 |
| `claude-opus-4-7` | 0.513 | 0.678 | 168 | 177 | 92 |
| `openai/gpt-5.4-mini` | 0.482 | 0.697 | 180 | 263 | 80 |
| `openai/o3` | 0.660 | 0.476 | 137 | 45 | 123 |
| `x-ai/grok-4.3` | 0.379 | 0.687 | 178 | 423 | 82 |
| `deepseek/deepseek-v4-flash` | 0.347 | 0.692 | 177 | 433 | 83 |
| `google/gemma-4-31b-it` | 0.353 | 0.637 | 158 | 422 | 102 |
| `deepseek/deepseek-r1` | 0.349 | 0.643 | 167 | 429 | 93 |
| `claude-sonnet-4-6` | 0.350 | 0.584 | 161 | 435 | 99 |
| `qwen/qwen3-235b-a22b-2507` | 0.324 | 0.583 | 146 | 419 | 114 |
| `openai/gpt-oss-120b` | 0.336 | 0.589 | 147 | 453 | 113 |
| `deepseek/deepseek-r1-0528` | 0.341 | 0.626 | 157 | 694 | 103 |
| `nvidia/llama-3.3-nemotron-super-49b-v1.5` | 0.379 | 0.450 | 110 | 271 | 150 |
| `cohere/command-a` | 0.313 | 0.583 | 134 | 411 | 126 |
| `google/gemini-3.1-flash-lite` | 0.270 | 0.655 | 152 | 679 | 108 |
| `moonshotai/kimi-k2.6` | 0.432 | 0.399 | 91 | 124 | 169 |
| `meta-llama/llama-4-maverick` | 0.320 | 0.499 | 137 | 301 | 123 |
| `mistralai/mistral-medium-3.1` | 0.302 | 0.478 | 123 | 524 | 137 |
| `claude-haiku-4-5-20251001` | 0.245 | 0.576 | 145 | 661 | 115 |
| `deepseek/deepseek-v4-pro` | 0.426 | 0.335 | 98 | 122 | 162 |
| `mistralai/codestral-2508` | 0.256 | 0.438 | 116 | 563 | 144 |
| `google/gemini-2.5-flash-lite` | 0.192 | 0.560 | 141 | 805 | 119 |
| `deepseek/deepseek-v3.2` | 0.283 | 0.306 | 83 | 292 | 177 |
| `google/gemini-2.5-flash` | 0.191 | 0.497 | 125 | 810 | 135 |
| `openai/gpt-3.5-turbo` | 0.218 | 0.423 | 103 | 573 | 157 |
| `meta-llama/llama-3.3-70b-instruct` | 0.252 | 0.303 | 87 | 247 | 173 |
| `mistralai/mistral-large-2512` | 0.155 | 0.523 | 128 | 1101 | 132 |
| `qwen/qwen3-14b` | 0.288 | 0.220 | 60 | 143 | 200 |
| `deepseek/deepseek-r1-distill-llama-70b` | 0.199 | 0.333 | 83 | 487 | 177 |
| `meta-llama/llama-4-scout` | 0.195 | 0.304 | 81 | 464 | 179 |
| `nvidia/nemotron-nano-9b-v2` | 0.192 | 0.261 | 65 | 502 | 195 |
| `meta-llama/llama-3.1-8b-instruct` | 0.193 | 0.233 | 55 | 987 | 205 |
| `cohere/command-r-plus-08-2024` | 0.118 | 0.094 | 34 | 94 | 226 |
| `openai/o4-mini` | 0.169 | 0.046 | 12 | 21 | 248 |
| `microsoft/phi-4` | 0.047 | 0.070 | 21 | 519 | 239 |
| `qwen/qwen3-8b` | 0.015 | 0.003 | 1 | 5 | 259 |
| `mistralai/mistral-7b-instruct-v0.1` | 0.000 | 0.000 | 0 | 0 | 260 |

## Boundary accuracy

For ads that match the truth at IoU >= 0.5, how far off were the predicted start and end timestamps? Lower is better. A model can hit F1 cleanly while still being 20s off on every boundary. Bad for any pipeline that cuts the audio.

| Model | Start MAE (s) | End MAE (s) |
|---|---:|---:|
| `qwen/qwen3-8b` | 0.02 | 0.01 |
| `qwen/qwen3.5-plus-02-15` | 5.43 | 1.26 |
| `qwen/qwen3.6-flash` | 5.40 | 2.59 |
| `google/gemini-3.5-flash` | 4.13 | 3.89 |
| `google/gemini-2.5-pro` | 4.83 | 3.55 |
| `qwen/qwen3.6-plus` | 5.02 | 3.38 |
| `google/gemini-3.1-flash-lite` | 1.81 | 7.28 |
| `x-ai/grok-4.3` | 3.38 | 6.38 |
| `nvidia/llama-3.3-nemotron-super-49b-v1.5` | 4.99 | 4.98 |
| `qwen/qwen3.5-27b` | 5.43 | 4.55 |
| `claude-sonnet-4-6` | 1.98 | 8.26 |
| `openai/gpt-5.4` | 7.02 | 3.50 |
| `deepseek/deepseek-v3.2` | 3.85 | 6.72 |
| `claude-haiku-4-5-20251001` | 2.24 | 8.38 |
| `openai/gpt-5.5` | 7.16 | 3.61 |
| `deepseek/deepseek-r1` | 4.50 | 6.29 |
| `google/gemini-2.5-flash` | 4.22 | 6.99 |
| `deepseek/deepseek-r1-0528` | 4.93 | 6.51 |
| `deepseek/deepseek-v4-pro` | 6.31 | 5.57 |
| `moonshotai/kimi-k2.6` | 9.52 | 2.37 |
| `openai/o4-mini` | 4.41 | 7.99 |
| `mistralai/mistral-large-2512` | 5.18 | 7.28 |
| `cohere/command-a` | 4.82 | 7.65 |
| `openai/o3` | 8.74 | 3.94 |
| `mistralai/codestral-2508` | 2.47 | 11.68 |
| `openai/gpt-oss-120b` | 4.55 | 9.60 |
| `meta-llama/llama-4-scout` | 3.72 | 10.74 |
| `deepseek/deepseek-r1-distill-llama-70b` | 2.23 | 12.34 |
| `claude-opus-4-7` | 9.62 | 5.40 |
| `microsoft/phi-4` | 8.58 | 7.07 |
| `mistralai/mistral-medium-3.1` | 6.74 | 10.03 |
| `openai/gpt-5.4-mini` | 6.31 | 10.47 |
| `google/gemma-4-31b-it` | 9.27 | 7.75 |
| `qwen/qwen3-235b-a22b-2507` | 6.70 | 10.72 |
| `deepseek/deepseek-v4-flash` | 7.29 | 10.23 |
| `meta-llama/llama-4-maverick` | 4.22 | 13.74 |
| `qwen/qwen3-14b` | 6.00 | 12.42 |
| `meta-llama/llama-3.3-70b-instruct` | 3.73 | 14.69 |
| `cohere/command-r-plus-08-2024` | 6.29 | 12.19 |
| `google/gemini-2.5-flash-lite` | 6.38 | 12.92 |
| `openai/gpt-3.5-turbo` | 6.40 | 13.03 |
| `meta-llama/llama-3.1-8b-instruct` | 12.70 | 6.99 |
| `nvidia/nemotron-nano-9b-v2` | 15.54 | 4.44 |

## Confidence calibration

Models include a self-reported `confidence` on each detected ad. A well-calibrated model should be right ~95% of the time when it claims 0.95 confidence. The table below bins each model's predictions and shows the actual hit rate (fraction that were true positives at IoU >= 0.5). A bin near 1.0 is well-calibrated; a low number with a high count means the model is overconfident.

| Model | 0.00-0.70 | 0.70-0.90 | 0.90-0.95 | 0.95-0.99 | 0.99+ | total |
|---|---:|---:|---:|---:|---:|---:|
| `claude-haiku-4-5-20251001` | -- | 0.00 (n=66) | 0.12 (n=267) | 0.24 (n=473) | -- | 806 |
| `claude-opus-4-7` | 0.00 (n=4) | 0.07 (n=15) | 0.24 (n=17) | 0.52 (n=286) | 0.65 (n=23) | 345 |
| `claude-sonnet-4-6` | -- | 0.04 (n=47) | 0.23 (n=84) | 0.27 (n=422) | 0.56 (n=43) | 596 |
| `cohere/command-a` | 0.00 (n=2) | 0.00 (n=58) | -- | 0.27 (n=496) | 0.00 (n=1) | 557 |
| `cohere/command-r-plus-08-2024` | -- | -- | 0.00 (n=1) | 0.07 (n=54) | 0.41 (n=73) | 128 |
| `deepseek/deepseek-r1` | -- | 0.00 (n=5) | 0.14 (n=14) | 0.27 (n=454) | 0.34 (n=129) | 602 |
| `deepseek/deepseek-r1-0528` | 0.00 (n=2) | 0.00 (n=22) | 0.02 (n=61) | 0.10 (n=610) | 0.46 (n=205) | 900 |
| `deepseek/deepseek-r1-distill-llama-70b` | -- | 0.01 (n=200) | -- | 0.19 (n=424) | -- | 624 |
| `deepseek/deepseek-v3.2` | 0.00 (n=1) | 0.00 (n=6) | 0.00 (n=17) | 0.09 (n=213) | 0.44 (n=144) | 381 |
| `deepseek/deepseek-v4-flash` | 0.00 (n=7) | 0.07 (n=14) | 0.07 (n=14) | 0.26 (n=379) | 0.38 (n=197) | 611 |
| `deepseek/deepseek-v4-pro` | 0.00 (n=1) | 0.00 (n=15) | 0.00 (n=5) | 0.48 (n=158) | 0.54 (n=41) | 220 |
| `google/gemini-2.5-flash` | -- | -- | 0.00 (n=65) | 0.15 (n=703) | 0.12 (n=167) | 935 |
| `google/gemini-2.5-flash-lite` | -- | 0.50 (n=2) | -- | 0.14 (n=939) | 0.50 (n=10) | 951 |
| `google/gemini-2.5-pro` | -- | 0.00 (n=18) | 0.00 (n=38) | 0.30 (n=133) | 0.60 (n=274) | 463 |
| `google/gemini-3.1-flash-lite` | -- | 0.00 (n=3) | 0.00 (n=33) | 0.09 (n=193) | 0.22 (n=607) | 836 |
| `google/gemini-3.5-flash` | -- | 0.00 (n=3) | 0.12 (n=25) | 0.10 (n=48) | 0.59 (n=303) | 379 |
| `google/gemma-4-31b-it` | -- | 0.10 (n=10) | 0.09 (n=34) | 0.17 (n=248) | 0.38 (n=293) | 585 |
| `meta-llama/llama-3.1-8b-instruct` | -- | 0.00 (n=5) | 0.00 (n=2) | 0.05 (n=1035) | -- | 1042 |
| `meta-llama/llama-3.3-70b-instruct` | -- | 0.00 (n=24) | 0.22 (n=32) | 0.12 (n=141) | 0.46 (n=137) | 334 |
| `meta-llama/llama-4-maverick` | 0.00 (n=1) | 0.00 (n=75) | 0.08 (n=65) | 0.44 (n=300) | 0.00 (n=2) | 443 |
| `meta-llama/llama-4-scout` | -- | 0.00 (n=8) | 0.05 (n=20) | 0.15 (n=492) | 0.32 (n=25) | 545 |
| `microsoft/phi-4` | -- | 0.00 (n=24) | 0.00 (n=18) | 0.04 (n=556) | -- | 598 |
| `mistralai/codestral-2508` | -- | 0.00 (n=1) | 0.00 (n=4) | 0.17 (n=673) | 0.00 (n=1) | 679 |
| `mistralai/mistral-large-2512` | 0.00 (n=2) | 0.00 (n=33) | 0.00 (n=61) | 0.05 (n=565) | 0.18 (n=568) | 1229 |
| `mistralai/mistral-medium-3.1` | -- | 0.00 (n=6) | 0.00 (n=57) | 0.20 (n=561) | 0.43 (n=23) | 647 |
| `moonshotai/kimi-k2.6` | 0.00 (n=35) | 0.03 (n=29) | 0.00 (n=2) | 0.49 (n=94) | 0.65 (n=68) | 228 |
| `nvidia/llama-3.3-nemotron-super-49b-v1.5` | -- | 0.00 (n=6) | 0.06 (n=32) | 0.31 (n=341) | 0.50 (n=2) | 381 |
| `nvidia/nemotron-nano-9b-v2` | -- | 0.00 (n=6) | 0.00 (n=31) | 0.12 (n=512) | 0.32 (n=19) | 568 |
| `openai/gpt-3.5-turbo` | -- | -- | 0.00 (n=5) | 0.17 (n=486) | 0.08 (n=241) | 732 |
| `openai/gpt-5.4` | 0.00 (n=26) | 0.05 (n=56) | 0.00 (n=28) | 0.39 (n=79) | 0.67 (n=233) | 422 |
| `openai/gpt-5.4-mini` | 0.00 (n=18) | 0.02 (n=60) | 0.05 (n=39) | 0.39 (n=101) | 0.60 (n=229) | 447 |
| `openai/gpt-5.5` | 0.00 (n=12) | 0.11 (n=27) | 0.38 (n=8) | 0.52 (n=73) | 0.64 (n=219) | 339 |
| `openai/gpt-oss-120b` | -- | 0.00 (n=7) | 0.11 (n=19) | 0.14 (n=283) | 0.35 (n=296) | 605 |
| `openai/o3` | 0.00 (n=1) | -- | 0.31 (n=16) | 0.79 (n=156) | 1.00 (n=9) | 182 |
| `openai/o4-mini` | -- | 0.00 (n=2) | 0.00 (n=1) | 0.41 (n=29) | 0.00 (n=1) | 33 |
| `qwen/qwen3-14b` | -- | -- | 0.00 (n=31) | 0.34 (n=174) | -- | 205 |
| `qwen/qwen3-235b-a22b-2507` | 0.00 (n=14) | 0.00 (n=5) | 0.00 (n=9) | 0.26 (n=499) | 0.22 (n=64) | 591 |
| `qwen/qwen3-8b` | -- | -- | -- | 0.17 (n=6) | -- | 6 |
| `qwen/qwen3.5-27b` | -- | 0.00 (n=9) | 0.10 (n=10) | 0.55 (n=295) | 0.00 (n=2) | 316 |
| `qwen/qwen3.5-plus-02-15` | -- | 0.00 (n=14) | 0.10 (n=10) | 0.57 (n=296) | 0.71 (n=17) | 337 |
| `qwen/qwen3.6-flash` | 0.00 (n=1) | 0.00 (n=9) | 0.00 (n=11) | 0.50 (n=397) | 0.83 (n=6) | 424 |
| `qwen/qwen3.6-plus` | 0.00 (n=1) | 0.00 (n=11) | 0.00 (n=4) | 0.59 (n=347) | 0.25 (n=8) | 371 |
| `x-ai/grok-4.3` | 0.00 (n=1) | 0.06 (n=18) | 0.09 (n=54) | 0.32 (n=497) | 0.35 (n=31) | 601 |

See `report_assets/calibration.svg` for the visual reliability diagram.

## Latency tail

Median latency hides outliers. p99 and max are what determines queue depth and worst-case user wait. For OpenRouter-routed models the tail also reflects upstream provider load, not just model compute.

| Model | p50 | p90 | p95 | p99 | max |
|---|---:|---:|---:|---:|---:|
| `mistralai/codestral-2508` | 0.73s | 1.68s | 2.23s | 4.41s | 6.36s |
| `meta-llama/llama-3.1-8b-instruct` | 0.77s | 2.31s | 3.95s | 7.43s | 76.80s |
| `google/gemini-3.1-flash-lite` | 0.78s | 1.25s | 1.44s | 1.88s | 17.47s |
| `meta-llama/llama-4-scout` | 0.83s | 3.12s | 4.33s | 6.19s | 16.81s |
| `mistralai/mistral-medium-3.1` | 0.90s | 4.68s | 6.09s | 8.11s | 11.71s |
| `google/gemini-2.5-flash-lite` | 0.90s | 1.88s | 2.98s | 6.31s | 17.01s |
| `cohere/command-r-plus-08-2024` | 0.95s | 2.19s | 3.24s | 13.57s | 62.06s |
| `google/gemini-2.5-flash` | 0.99s | 2.37s | 3.55s | 10.01s | 16.44s |
| `meta-llama/llama-4-maverick` | 1.04s | 2.04s | 2.39s | 4.06s | 50.80s |
| `claude-haiku-4-5-20251001` | 1.11s | 3.17s | 4.06s | 181.21s | 186.76s |
| `openai/gpt-5.4-mini` | 1.18s | 1.66s | 2.24s | 3.09s | 4.94s |
| `openai/gpt-3.5-turbo` | 1.26s | 1.76s | 1.93s | 2.56s | 8.25s |
| `claude-sonnet-4-6` | 1.38s | 4.70s | 5.87s | 8.66s | 185.04s |
| `meta-llama/llama-3.3-70b-instruct` | 1.44s | 3.22s | 4.53s | 13.86s | 45.01s |
| `openai/gpt-5.4` | 1.82s | 2.53s | 3.10s | 4.90s | 17.20s |
| `deepseek/deepseek-r1-distill-llama-70b` | 1.97s | 14.25s | 47.29s | 94.01s | 136.13s |
| `claude-opus-4-7` | 2.17s | 3.57s | 4.25s | 6.71s | 183.21s |
| `google/gemma-4-31b-it` | 2.26s | 13.62s | 18.73s | 64.27s | 377.81s |
| `microsoft/phi-4` | 2.29s | 6.70s | 11.31s | 202.66s | 229.43s |
| `qwen/qwen3-235b-a22b-2507` | 2.30s | 6.20s | 7.70s | 11.98s | 24.93s |
| `mistralai/mistral-large-2512` | 2.46s | 5.31s | 6.18s | 8.16s | 18.09s |
| `deepseek/deepseek-v3.2` | 2.81s | 5.30s | 7.50s | 12.65s | 63.86s |
| `openai/gpt-oss-120b` | 2.85s | 20.51s | 33.85s | 333.99s | 2880.77s |
| `cohere/command-a` | 3.78s | 8.71s | 12.50s | 28.17s | 65.77s |
| `deepseek/deepseek-v4-flash` | 3.95s | 19.52s | 28.27s | 45.80s | 80.55s |
| `x-ai/grok-4.3` | 3.98s | 9.56s | 12.40s | 18.49s | 33.29s |
| `google/gemini-3.5-flash` | 5.24s | 8.82s | 11.04s | 15.61s | 32.29s |
| `openai/gpt-5.5` | 6.37s | 18.52s | 24.09s | 37.00s | 76.20s |
| `openai/o4-mini` | 6.94s | 20.13s | 25.43s | 70.73s | 138.80s |
| `mistralai/mistral-7b-instruct-v0.1` | 6.97s | 22.80s | 32.95s | 78.94s | 89.36s |
| `openai/o3` | 7.79s | 19.20s | 27.19s | 63.30s | 76.89s |
| `nvidia/nemotron-nano-9b-v2` | 12.37s | 32.04s | 36.90s | 51.08s | 65.31s |
| `qwen/qwen3.6-flash` | 13.12s | 33.83s | 39.19s | 47.09s | 371.73s |
| `google/gemini-2.5-pro` | 14.32s | 25.14s | 28.66s | 55.05s | 250.33s |
| `deepseek/deepseek-r1-0528` | 15.99s | 80.78s | 93.33s | 134.74s | 285.69s |
| `deepseek/deepseek-r1` | 19.89s | 88.83s | 152.53s | 263.65s | 364.40s |
| `qwen/qwen3-14b` | 20.93s | 43.20s | 60.04s | 204.89s | 439.49s |
| `nvidia/llama-3.3-nemotron-super-49b-v1.5` | 24.05s | 82.52s | 85.93s | 99.35s | 106.19s |
| `deepseek/deepseek-v4-pro` | 26.79s | 84.13s | 99.89s | 137.33s | 190.30s |
| `moonshotai/kimi-k2.6` | 36.24s | 134.88s | 181.59s | 233.13s | 411.39s |
| `qwen/qwen3.6-plus` | 39.89s | 68.91s | 74.23s | 89.87s | 100.27s |
| `qwen/qwen3.5-plus-02-15` | 47.30s | 123.31s | 142.96s | 183.09s | 1486.87s |
| `qwen/qwen3-8b` | 58.44s | 130.49s | 141.13s | 185.62s | 233.05s |
| `qwen/qwen3.5-27b` | 70.53s | 446.47s | 1161.76s | 1642.26s | 2172.22s |

## Output token efficiency

How many output tokens the model spent per detected ad. Lower is more concise (the model finds an ad and returns the JSON). Higher means the model is producing a lot of text the parser will discard, which costs you whether or not the answer is right.

| Model | Total output tokens | Ads detected | Tokens / ad | Cost / TP |
|---|---:|---:|---:|---:|
| `mistralai/mistral-medium-3.1` | 40,325 | 647 | 62 | $0.0038 |
| `mistralai/codestral-2508` | 43,157 | 679 | 64 | $0.0030 |
| `google/gemini-2.5-flash` | 68,210 | 935 | 73 | $0.0029 |
| `openai/gpt-3.5-turbo` | 53,438 | 732 | 73 | $0.0053 |
| `google/gemini-3.1-flash-lite` | 61,772 | 836 | 74 | $0.0019 |
| `meta-llama/llama-3.3-70b-instruct` | 26,385 | 334 | 79 | $0.0012 |
| `cohere/command-r-plus-08-2024` | 10,302 | 128 | 80 | $0.0809 |
| `claude-sonnet-4-6` | 52,272 | 596 | 88 | $0.0234 |
| `meta-llama/llama-3.1-8b-instruct` | 92,936 | 1042 | 89 | $0.0004 |
| `mistralai/mistral-large-2512` | 110,858 | 1229 | 90 | $0.0047 |
| `meta-llama/llama-4-scout` | 49,166 | 545 | 90 | $0.0011 |
| `meta-llama/llama-4-maverick` | 40,119 | 443 | 91 | $0.0012 |
| `claude-haiku-4-5-20251001` | 75,670 | 806 | 94 | $0.0088 |
| `deepseek/deepseek-v3.2` | 36,057 | 381 | 95 | $0.0033 |
| `google/gemini-2.5-flash-lite` | 92,902 | 951 | 98 | $0.0008 |
| `google/gemma-4-31b-it` | 57,420 | 585 | 98 | $0.0009 |
| `openai/gpt-5.4` | 44,600 | 422 | 106 | $0.0142 |
| `cohere/command-a` | 59,269 | 557 | 106 | $0.0213 |
| `qwen/qwen3-235b-a22b-2507` | 63,074 | 591 | 107 | $0.0005 |
| `openai/gpt-5.4-mini` | 48,758 | 447 | 109 | $0.0045 |
| `claude-opus-4-7` | 37,773 | 345 | 109 | $0.0496 |
| `microsoft/phi-4` | 215,738 | 598 | 361 | $0.0036 |
| `deepseek/deepseek-r1-distill-llama-70b` | 280,588 | 624 | 450 | $0.0094 |
| `deepseek/deepseek-v4-flash` | 536,169 | 611 | 878 | $0.0008 |
| `x-ai/grok-4.3` | 579,523 | 601 | 964 | $0.0090 |
| `openai/gpt-5.5` | 328,897 | 339 | 970 | $0.0385 |
| `openai/gpt-oss-120b` | 763,927 | 605 | 1263 | $0.0005 |
| `deepseek/deepseek-r1-0528` | 1,248,070 | 900 | 1387 | $0.0068 |
| `deepseek/deepseek-r1` | 852,997 | 602 | 1417 | $0.0071 |
| `nvidia/nemotron-nano-9b-v2` | 1,314,112 | 568 | 2314 | $0.0013 |
| `qwen/qwen3-14b` | 537,150 | 205 | 2620 | $0.0022 |
| `google/gemini-2.5-pro` | 1,397,740 | 463 | 3019 | $0.0203 |
| `google/gemini-3.5-flash` | 1,157,215 | 379 | 3053 | $0.0201 |
| `qwen/qwen3.6-flash` | 1,666,264 | 424 | 3930 | $0.0028 |
| `nvidia/llama-3.3-nemotron-super-49b-v1.5` | 1,532,406 | 381 | 4022 | $0.0021 |
| `openai/o3` | 736,945 | 182 | 4049 | $0.0236 |
| `deepseek/deepseek-v4-pro` | 1,218,224 | 220 | 5537 | $0.0069 |
| `qwen/qwen3.6-plus` | 2,109,966 | 371 | 5687 | $0.0058 |
| `qwen/qwen3.5-plus-02-15` | 3,172,701 | 376 | 8438 | $0.0070 |
| `moonshotai/kimi-k2.6` | 2,467,609 | 228 | 10823 | $0.0273 |
| `openai/o4-mini` | 998,716 | 33 | 30264 | $0.1676 |
| `qwen/qwen3.5-27b` | 10,421,551 | 316 | 32980 | $0.0214 |
| `qwen/qwen3-8b` | 2,734,766 | 6 | 455794 | $0.2734 |

## Trial variance (determinism check)

All trials run at temperature 0.0. If a model produces stable output you'd expect the F1 stdev across trials to be near zero. Higher numbers mean the model is non-deterministic even at temp=0. That's fine to know, but means you cannot trust a single trial's number for that model.

| Model | Mean F1 stdev across episodes | Highest single-episode stdev |
|---|---:|---:|
| `qwen/qwen3.6-plus` | 0.0368 | 0.1194 |
| `qwen/qwen3.5-plus-02-15` | 0.0314 | 0.0938 |
| `qwen/qwen3.6-flash` | 0.0802 | 0.2449 |
| `openai/gpt-5.5` | 0.0561 | 0.1432 |
| `google/gemini-3.5-flash` | 0.0200 | 0.0782 |
| `qwen/qwen3.5-27b` | 0.1483 | 0.3651 |
| `openai/gpt-5.4` | 0.0615 | 0.1193 |
| `google/gemini-2.5-pro` | 0.0374 | 0.1157 |
| `claude-opus-4-7` | 0.0621 | 0.1789 |
| `openai/gpt-5.4-mini` | 0.1036 | 0.2887 |
| `openai/o3` | 0.1383 | 0.4714 |
| `x-ai/grok-4.3` | 0.0968 | 0.2528 |
| `deepseek/deepseek-v4-flash` | 0.1134 | 0.3347 |
| `google/gemma-4-31b-it` | 0.0734 | 0.2739 |
| `deepseek/deepseek-r1` | 0.1316 | 0.3015 |
| `claude-sonnet-4-6` | 0.0384 | 0.1826 |
| `qwen/qwen3-235b-a22b-2507` | 0.0758 | 0.2653 |
| `openai/gpt-oss-120b` | 0.1274 | 0.3651 |
| `deepseek/deepseek-r1-0528` | 0.1315 | 0.2807 |
| `nvidia/llama-3.3-nemotron-super-49b-v1.5` | 0.1245 | 0.3249 |
| `cohere/command-a` | 0.0371 | 0.1217 |
| `google/gemini-3.1-flash-lite` | 0.0359 | 0.2528 |
| `moonshotai/kimi-k2.6` | 0.1517 | 0.2739 |
| `meta-llama/llama-4-maverick` | 0.0150 | 0.1012 |
| `mistralai/mistral-medium-3.1` | 0.0431 | 0.1373 |
| `claude-haiku-4-5-20251001` | 0.0010 | 0.0116 |
| `deepseek/deepseek-v4-pro` | 0.2039 | 0.4333 |
| `mistralai/codestral-2508` | 0.0455 | 0.1493 |
| `google/gemini-2.5-flash-lite` | 0.0213 | 0.0523 |
| `deepseek/deepseek-v3.2` | 0.1762 | 0.5477 |
| `google/gemini-2.5-flash` | 0.0000 | 0.0000 |
| `openai/gpt-3.5-turbo` | 0.0119 | 0.0497 |
| `meta-llama/llama-3.3-70b-instruct` | 0.0752 | 0.2981 |
| `mistralai/mistral-large-2512` | 0.0150 | 0.0426 |
| `qwen/qwen3-14b` | 0.1535 | 0.3322 |
| `deepseek/deepseek-r1-distill-llama-70b` | 0.0899 | 0.3113 |
| `meta-llama/llama-4-scout` | 0.1008 | 0.3347 |
| `nvidia/nemotron-nano-9b-v2` | 0.1048 | 0.3651 |
| `meta-llama/llama-3.1-8b-instruct` | 0.1132 | 0.5477 |
| `cohere/command-r-plus-08-2024` | 0.0325 | 0.1547 |
| `openai/o4-mini` | 0.1158 | 0.2981 |
| `microsoft/phi-4` | 0.0566 | 0.3070 |
| `qwen/qwen3-8b` | 0.0098 | 0.1278 |
| `mistralai/mistral-7b-instruct-v0.1` | 0.0000 | 0.0000 |

## Cross-model agreement

For each of the 182 (episode, window, trial-equivalent) entries, how many of the 44 active models predicted at least one ad? High-agreement windows are unambiguous ads (or unambiguously not ads). Low-agreement windows are where individual models disagree, and are candidates for ensemble voting if you want a cheap accuracy boost.

| Models predicting an ad | Window count | Share |
|---:|---:|---:|
| 2 of 44 | 1 | 0.5% |
| 4 of 44 | 9 | 4.9% |
| 5 of 44 | 15 | 8.2% |
| 6 of 44 | 11 | 6.0% |
| 7 of 44 | 9 | 4.9% |
| 8 of 44 | 6 | 3.3% |
| 9 of 44 | 7 | 3.8% |
| 10 of 44 | 9 | 4.9% |
| 11 of 44 | 9 | 4.9% |
| 12 of 44 | 7 | 3.8% |
| 13 of 44 | 5 | 2.7% |
| 14 of 44 | 2 | 1.1% |
| 15 of 44 | 3 | 1.6% |
| 16 of 44 | 5 | 2.7% |
| 17 of 44 | 2 | 1.1% |
| 18 of 44 | 1 | 0.5% |
| 19 of 44 | 2 | 1.1% |
| 25 of 44 | 1 | 0.5% |
| 26 of 44 | 2 | 1.1% |
| 27 of 44 | 1 | 0.5% |
| 29 of 44 | 5 | 2.7% |
| 30 of 44 | 1 | 0.5% |
| 32 of 44 | 1 | 0.5% |
| 33 of 44 | 3 | 1.6% |
| 35 of 44 | 2 | 1.1% |
| 36 of 44 | 3 | 1.6% |
| 37 of 44 | 6 | 3.3% |
| 38 of 44 | 14 | 7.7% |
| 39 of 44 | 15 | 8.2% |
| 40 of 44 | 13 | 7.1% |
| 41 of 44 | 9 | 4.9% |
| 42 of 44 | 3 | 1.6% |

Read this as: rows near the top are windows where the field disagrees (most models said no, a few said yes, usually false positives); rows near the bottom are windows where the field broadly agrees (typical of clear sponsor reads).

### Per-model alignment with consensus

Same data, viewed per model. For each window, the **majority** is whether more than half of the 44 active models flagged an ad. Then for each model: did it vote with the majority or against it? Four buckets:

- **with-yes**: this model voted yes, majority also voted yes (likely true positive)
- **with-no**: this model voted no, majority also voted no (likely true negative)
- **broke-yes**: this model voted yes, majority voted no (likely false positive / hallucination)
- **broke-no**: this model voted no, majority voted yes (likely missed real ad)

Alignment rate is `(with-yes + with-no) / total`. High alignment means the model tracks the consensus; low alignment means it disagrees often, which could be brilliance or noise depending on whether its disagreements are also where its F1 wins or loses.

| Model | with-yes | with-no | broke-yes | broke-no | Alignment |
|---|---:|---:|---:|---:|---:|
| `qwen/qwen3.5-27b` | 75 | 103 | 0 | 4 | 97.8% |
| `x-ai/grok-4.3` | 75 | 103 | 0 | 4 | 97.8% |
| `google/gemini-2.5-flash` | 76 | 101 | 2 | 3 | 97.3% |
| `openai/gpt-oss-120b` | 79 | 98 | 5 | 0 | 97.3% |
| `qwen/qwen3.5-plus-02-15` | 74 | 103 | 0 | 5 | 97.3% |
| `openai/gpt-5.5` | 76 | 100 | 3 | 3 | 96.7% |
| `nvidia/llama-3.3-nemotron-super-49b-v1.5` | 72 | 103 | 0 | 7 | 96.2% |
| `google/gemma-4-31b-it` | 77 | 97 | 6 | 2 | 95.6% |
| `google/gemini-2.5-pro` | 79 | 94 | 9 | 0 | 95.1% |
| `qwen/qwen3.6-flash` | 74 | 99 | 4 | 5 | 95.1% |
| `qwen/qwen3.6-plus` | 70 | 102 | 1 | 9 | 94.5% |
| `mistralai/mistral-medium-3.1` | 69 | 102 | 1 | 10 | 94.0% |
| `claude-haiku-4-5-20251001` | 70 | 100 | 3 | 9 | 93.4% |
| `claude-opus-4-7` | 67 | 103 | 0 | 12 | 93.4% |
| `claude-sonnet-4-6` | 67 | 103 | 0 | 12 | 93.4% |
| `google/gemini-3.5-flash` | 67 | 103 | 0 | 12 | 93.4% |
| `openai/o3` | 63 | 103 | 0 | 16 | 91.2% |
| `google/gemini-3.1-flash-lite` | 79 | 86 | 17 | 0 | 90.7% |
| `meta-llama/llama-4-scout` | 75 | 90 | 13 | 4 | 90.7% |
| `deepseek/deepseek-v4-flash` | 79 | 81 | 22 | 0 | 87.9% |
| `meta-llama/llama-3.3-70b-instruct` | 65 | 95 | 8 | 14 | 87.9% |
| `meta-llama/llama-4-maverick` | 76 | 83 | 20 | 3 | 87.4% |
| `nvidia/nemotron-nano-9b-v2` | 75 | 84 | 19 | 4 | 87.4% |
| `deepseek/deepseek-r1` | 79 | 79 | 24 | 0 | 86.8% |
| `meta-llama/llama-3.1-8b-instruct` | 74 | 84 | 19 | 5 | 86.8% |
| `deepseek/deepseek-v4-pro` | 64 | 89 | 14 | 15 | 84.1% |
| `google/gemini-2.5-flash-lite` | 79 | 73 | 30 | 0 | 83.5% |
| `mistralai/codestral-2508` | 73 | 78 | 25 | 6 | 83.0% |
| `qwen/qwen3-14b` | 69 | 79 | 24 | 10 | 81.3% |
| `openai/gpt-5.4` | 78 | 69 | 34 | 1 | 80.8% |
| `openai/gpt-5.4-mini` | 77 | 60 | 43 | 2 | 75.3% |
| `cohere/command-a` | 75 | 60 | 43 | 4 | 74.2% |
| `cohere/command-r-plus-08-2024` | 25 | 103 | 0 | 54 | 70.3% |
| `openai/o4-mini` | 25 | 103 | 0 | 54 | 70.3% |
| `mistralai/mistral-large-2512` | 77 | 46 | 57 | 2 | 67.6% |
| `deepseek/deepseek-v3.2` | 57 | 64 | 39 | 22 | 66.5% |
| `deepseek/deepseek-r1-0528` | 75 | 36 | 67 | 4 | 61.0% |
| `qwen/qwen3-235b-a22b-2507` | 79 | 32 | 71 | 0 | 61.0% |
| `openai/gpt-3.5-turbo` | 79 | 28 | 75 | 0 | 58.8% |
| `qwen/qwen3-8b` | 2 | 103 | 0 | 77 | 57.7% |
| `mistralai/mistral-7b-instruct-v0.1` | 0 | 103 | 0 | 79 | 56.6% |
| `moonshotai/kimi-k2.6` | 48 | 47 | 56 | 31 | 52.2% |
| `deepseek/deepseek-r1-distill-llama-70b` | 70 | 11 | 92 | 9 | 44.5% |
| `microsoft/phi-4` | 58 | 15 | 88 | 21 | 40.1% |

## Detection rate by ad characteristic

Aggregate detection rates often hide systematic blind spots. Below: for each model, what fraction of truth ads in each bucket were detected (matched at IoU >= 0.5).

### By ad length

Truth ads bucketed by duration: short (<30s), medium (30-90s), long (>=90s). Cell values are detection rate (fraction of truth ads in that bucket the model caught), with the sample size `n` so a misleading 1.00 on a 2-ad bucket doesn't get over-weighted. Models that systematically miss short ads usually fail on network-inserted brand-tagline spots; missing long ads is rarer and usually means the model gave up before processing the full window.

| Model | long (>=90s) | medium (30-90s) | short (<30s) |
|---|---:|---:|---:|
| `claude-haiku-4-5-20251001` | 0.36 (n=140) | 0.75 (n=80) | 0.88 (n=40) |
| `claude-opus-4-7` | 0.65 (n=140) | 0.70 (n=80) | 0.53 (n=40) |
| `claude-sonnet-4-6` | 0.54 (n=140) | 0.72 (n=80) | 0.68 (n=40) |
| `cohere/command-a` | 0.41 (n=140) | 0.59 (n=80) | 0.75 (n=40) |
| `cohere/command-r-plus-08-2024` | 0.19 (n=140) | 0.04 (n=80) | 0.12 (n=40) |
| `deepseek/deepseek-r1` | 0.56 (n=140) | 0.71 (n=80) | 0.80 (n=40) |
| `deepseek/deepseek-r1-0528` | 0.51 (n=140) | 0.69 (n=80) | 0.78 (n=40) |
| `deepseek/deepseek-r1-distill-llama-70b` | 0.26 (n=140) | 0.28 (n=80) | 0.62 (n=40) |
| `deepseek/deepseek-v3.2` | 0.35 (n=140) | 0.29 (n=80) | 0.28 (n=40) |
| `deepseek/deepseek-v4-flash` | 0.61 (n=140) | 0.76 (n=80) | 0.78 (n=40) |
| `deepseek/deepseek-v4-pro` | 0.49 (n=140) | 0.26 (n=80) | 0.20 (n=40) |
| `google/gemini-2.5-flash` | 0.29 (n=140) | 0.62 (n=80) | 0.88 (n=40) |
| `google/gemini-2.5-flash-lite` | 0.35 (n=140) | 0.71 (n=80) | 0.88 (n=40) |
| `google/gemini-2.5-pro` | 0.84 (n=140) | 0.74 (n=80) | 0.72 (n=40) |
| `google/gemini-3.1-flash-lite` | 0.34 (n=140) | 0.86 (n=80) | 0.88 (n=40) |
| `google/gemini-3.5-flash` | 0.72 (n=140) | 0.71 (n=80) | 0.70 (n=40) |
| `google/gemma-4-31b-it` | 0.56 (n=140) | 0.69 (n=80) | 0.60 (n=40) |
| `meta-llama/llama-3.1-8b-instruct` | 0.12 (n=140) | 0.26 (n=80) | 0.42 (n=40) |
| `meta-llama/llama-3.3-70b-instruct` | 0.30 (n=140) | 0.30 (n=80) | 0.53 (n=40) |
| `meta-llama/llama-4-maverick` | 0.50 (n=140) | 0.65 (n=80) | 0.38 (n=40) |
| `meta-llama/llama-4-scout` | 0.22 (n=140) | 0.34 (n=80) | 0.57 (n=40) |
| `microsoft/phi-4` | 0.04 (n=140) | 0.14 (n=80) | 0.12 (n=40) |
| `mistralai/codestral-2508` | 0.39 (n=140) | 0.54 (n=80) | 0.45 (n=40) |
| `mistralai/mistral-7b-instruct-v0.1` | 0.00 (n=140) | 0.00 (n=80) | 0.00 (n=40) |
| `mistralai/mistral-large-2512` | 0.31 (n=140) | 0.69 (n=80) | 0.75 (n=40) |
| `mistralai/mistral-medium-3.1` | 0.30 (n=140) | 0.65 (n=80) | 0.72 (n=40) |
| `moonshotai/kimi-k2.6` | 0.30 (n=140) | 0.49 (n=80) | 0.25 (n=40) |
| `nvidia/llama-3.3-nemotron-super-49b-v1.5` | 0.31 (n=140) | 0.54 (n=80) | 0.60 (n=40) |
| `nvidia/nemotron-nano-9b-v2` | 0.14 (n=140) | 0.40 (n=80) | 0.35 (n=40) |
| `openai/gpt-3.5-turbo` | 0.31 (n=140) | 0.38 (n=80) | 0.75 (n=40) |
| `openai/gpt-5.4` | 0.80 (n=140) | 0.69 (n=80) | 0.57 (n=40) |
| `openai/gpt-5.4-mini` | 0.74 (n=140) | 0.59 (n=80) | 0.75 (n=40) |
| `openai/gpt-5.5` | 0.74 (n=140) | 0.72 (n=80) | 0.57 (n=40) |
| `openai/gpt-oss-120b` | 0.46 (n=140) | 0.66 (n=80) | 0.75 (n=40) |
| `openai/o3` | 0.66 (n=140) | 0.41 (n=80) | 0.30 (n=40) |
| `openai/o4-mini` | 0.03 (n=140) | 0.05 (n=80) | 0.10 (n=40) |
| `qwen/qwen3-14b` | 0.23 (n=140) | 0.20 (n=80) | 0.30 (n=40) |
| `qwen/qwen3-235b-a22b-2507` | 0.44 (n=140) | 0.61 (n=80) | 0.88 (n=40) |
| `qwen/qwen3-8b` | 0.01 (n=140) | 0.00 (n=80) | 0.00 (n=40) |
| `qwen/qwen3.5-27b` | 0.59 (n=140) | 0.65 (n=80) | 0.68 (n=40) |
| `qwen/qwen3.5-plus-02-15` | 0.86 (n=116) | 0.75 (n=77) | 0.62 (n=40) |
| `qwen/qwen3.6-flash` | 0.87 (n=140) | 0.69 (n=80) | 0.70 (n=40) |
| `qwen/qwen3.6-plus` | 0.84 (n=140) | 0.78 (n=80) | 0.62 (n=40) |
| `x-ai/grok-4.3` | 0.58 (n=140) | 0.85 (n=80) | 0.72 (n=40) |

### By ad position

Truth ads bucketed by where they fall in the episode: pre-roll (first 10%), mid-roll (10-90%), post-roll (last 10%). Cell values are the same detection-rate-with-`n` format as ad length. A common failure pattern in our data: most models detect pre-roll and mid-roll reliably and miss post-roll, because the prompt windows near the end often catch the model mid-reasoning or with fewer transition phrases to anchor on.

| Model | pre-roll (<10%) | mid-roll (10-90%) | post-roll (>90%) |
|---|---:|---:|---:|
| `claude-haiku-4-5-20251001` | 0.50 (n=80) | 0.56 (n=125) | 0.64 (n=55) |
| `claude-opus-4-7` | 0.61 (n=80) | 0.62 (n=125) | 0.76 (n=55) |
| `claude-sonnet-4-6` | 0.72 (n=80) | 0.58 (n=125) | 0.55 (n=55) |
| `cohere/command-a` | 0.49 (n=80) | 0.52 (n=125) | 0.55 (n=55) |
| `cohere/command-r-plus-08-2024` | 0.05 (n=80) | 0.23 (n=125) | 0.02 (n=55) |
| `deepseek/deepseek-r1` | 0.57 (n=80) | 0.69 (n=125) | 0.64 (n=55) |
| `deepseek/deepseek-r1-0528` | 0.55 (n=80) | 0.63 (n=125) | 0.62 (n=55) |
| `deepseek/deepseek-r1-distill-llama-70b` | 0.29 (n=80) | 0.33 (n=125) | 0.35 (n=55) |
| `deepseek/deepseek-v3.2` | 0.39 (n=80) | 0.37 (n=125) | 0.11 (n=55) |
| `deepseek/deepseek-v4-flash` | 0.59 (n=80) | 0.75 (n=125) | 0.65 (n=55) |
| `deepseek/deepseek-v4-pro` | 0.25 (n=80) | 0.47 (n=125) | 0.35 (n=55) |
| `google/gemini-2.5-flash` | 0.44 (n=80) | 0.48 (n=125) | 0.55 (n=55) |
| `google/gemini-2.5-flash-lite` | 0.55 (n=80) | 0.54 (n=125) | 0.55 (n=55) |
| `google/gemini-2.5-pro` | 0.74 (n=80) | 0.85 (n=125) | 0.73 (n=55) |
| `google/gemini-3.1-flash-lite` | 0.56 (n=80) | 0.58 (n=125) | 0.62 (n=55) |
| `google/gemini-3.5-flash` | 0.62 (n=80) | 0.77 (n=125) | 0.73 (n=55) |
| `google/gemma-4-31b-it` | 0.55 (n=80) | 0.62 (n=125) | 0.67 (n=55) |
| `meta-llama/llama-3.1-8b-instruct` | 0.21 (n=80) | 0.23 (n=125) | 0.16 (n=55) |
| `meta-llama/llama-3.3-70b-instruct` | 0.21 (n=80) | 0.40 (n=125) | 0.36 (n=55) |
| `meta-llama/llama-4-maverick` | 0.46 (n=80) | 0.60 (n=125) | 0.45 (n=55) |
| `meta-llama/llama-4-scout` | 0.25 (n=80) | 0.40 (n=125) | 0.20 (n=55) |
| `microsoft/phi-4` | 0.16 (n=80) | 0.02 (n=125) | 0.11 (n=55) |
| `mistralai/codestral-2508` | 0.34 (n=80) | 0.48 (n=125) | 0.53 (n=55) |
| `mistralai/mistral-7b-instruct-v0.1` | 0.00 (n=80) | 0.00 (n=125) | 0.00 (n=55) |
| `mistralai/mistral-large-2512` | 0.49 (n=80) | 0.51 (n=125) | 0.45 (n=55) |
| `mistralai/mistral-medium-3.1` | 0.47 (n=80) | 0.44 (n=125) | 0.55 (n=55) |
| `moonshotai/kimi-k2.6` | 0.28 (n=80) | 0.31 (n=125) | 0.55 (n=55) |
| `nvidia/llama-3.3-nemotron-super-49b-v1.5` | 0.35 (n=80) | 0.46 (n=125) | 0.44 (n=55) |
| `nvidia/nemotron-nano-9b-v2` | 0.14 (n=80) | 0.26 (n=125) | 0.38 (n=55) |
| `openai/gpt-3.5-turbo` | 0.29 (n=80) | 0.48 (n=125) | 0.36 (n=55) |
| `openai/gpt-5.4` | 0.70 (n=80) | 0.78 (n=125) | 0.65 (n=55) |
| `openai/gpt-5.4-mini` | 0.56 (n=80) | 0.83 (n=125) | 0.56 (n=55) |
| `openai/gpt-5.5` | 0.69 (n=80) | 0.73 (n=125) | 0.71 (n=55) |
| `openai/gpt-oss-120b` | 0.50 (n=80) | 0.61 (n=125) | 0.56 (n=55) |
| `openai/o3` | 0.41 (n=80) | 0.59 (n=125) | 0.55 (n=55) |
| `openai/o4-mini` | 0.01 (n=80) | 0.06 (n=125) | 0.05 (n=55) |
| `qwen/qwen3-14b` | 0.12 (n=80) | 0.29 (n=125) | 0.25 (n=55) |
| `qwen/qwen3-235b-a22b-2507` | 0.53 (n=80) | 0.59 (n=125) | 0.55 (n=55) |
| `qwen/qwen3-8b` | 0.00 (n=80) | 0.01 (n=125) | 0.00 (n=55) |
| `qwen/qwen3.5-27b` | 0.53 (n=80) | 0.67 (n=125) | 0.65 (n=55) |
| `qwen/qwen3.5-plus-02-15` | 0.76 (n=71) | 0.84 (n=110) | 0.71 (n=52) |
| `qwen/qwen3.6-flash` | 0.66 (n=80) | 0.88 (n=125) | 0.76 (n=55) |
| `qwen/qwen3.6-plus` | 0.70 (n=80) | 0.88 (n=125) | 0.71 (n=55) |
| `x-ai/grok-4.3` | 0.62 (n=80) | 0.74 (n=125) | 0.65 (n=55) |

## Quick Comparison

One row per model, one column per episode. The headline columns (`F1`, `Cost/ep`, `p50`) summarize across all episodes; the per-episode columns let you see whether a model's average hides wide swings (a model that scores well overall might still bomb on a specific genre). The right-most `F1 stdev` column averages the per-trial standard deviations across episodes; high values mean the model isn't deterministic at temperature 0.0, so its single-trial F1 number is noisy.

| Model | F1 | Cost/ep | p50 | ep-crime-junkie-8ce498f299d7 | ep-daily-gist-chicago-70a82fe93a5c | ep-daily-tech-news-show-b576979e1fe8 | ep-daily-tech-news-show-c1904b8605f7 | ep-drink-champs-30c9a2d49f13 | ep-glt1412515089-373d5ba5007b | ep-it-s-a-thing-e339179dfad6 | ep-on-air-with-dan-and-alex2-574e4f303730 | ep-politics-politics-politics-9d7642c84fc9 | ep-security-now-audio-2850b24903b2 | ep-the-brilliant-idiots-0bb9bf634c8e | ep-the-tim-dillon-show-f62bd5fa1cfe | ep-tosh-show-5f6894439bb6 | ep-ai-cloud-essentials-e8dc897fbd6b (no-ad) | ep-oxide-and-friends-ce789ff5b62e (no-ad) | F1 stdev |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| `qwen/qwen3.6-plus` | 0.640 | $1.1813 | 39.9s | 0.933 | 0.667 | 0.914 | 0.457 | 0.706 | 0.600 | 0.667 | 0.773 | 0.000 | 0.481 | 0.798 | 0.615 | 0.703 | PASS | PASS | 0.037 |
| `qwen/qwen3.5-plus-02-15` | 0.627 | $1.2880 | 47.3s | 1.000 | 0.600 | 0.857 | 0.518 | 0.711 | 0.625 | 0.667 | 0.590 | 0.000 | 0.476 | 0.771 | 0.636 | 0.696 | PASS | PASS | 0.031 |
| `qwen/qwen3.6-flash` | 0.610 | $0.5817 | 13.1s | 1.000 | 0.700 | 0.852 | 0.492 | 0.628 | 0.518 | 0.667 | 0.682 | 0.000 | 0.514 | 0.800 | 0.507 | 0.564 | FAIL (1 FP) | PASS | 0.080 |
| `openai/gpt-5.5` | 0.597 | $7.1204 | 6.4s | 0.898 | 0.500 | 0.886 | 0.547 | 0.642 | 0.636 | 0.667 | 0.571 | 0.000 | 0.505 | 0.776 | 0.546 | 0.587 | FAIL (1 FP) | PASS | 0.056 |
| `google/gemini-3.5-flash` | 0.577 | $3.7363 | 5.2s | 0.914 | 0.400 | 0.857 | 0.500 | 0.496 | 0.676 | 0.667 | 0.571 | 0.000 | 0.476 | 0.857 | 0.592 | 0.490 | PASS | PASS | 0.020 |
| `qwen/qwen3.5-27b` | 0.574 | $3.4665 | 70.5s | 0.892 | 0.727 | 0.743 | 0.506 | 0.494 | 0.601 | 0.600 | 0.634 | 0.000 | 0.414 | 0.671 | 0.537 | 0.647 | PASS | PASS | 0.148 |
| `openai/gpt-5.4` | 0.573 | $2.7073 | 1.8s | 0.933 | 0.500 | 0.892 | 0.518 | 0.571 | 0.506 | 0.613 | 0.747 | 0.000 | 0.495 | 0.516 | 0.586 | 0.569 | FAIL (1 FP) | FAIL (1 FP) | 0.061 |
| `google/gemini-2.5-pro` | 0.566 | $4.1541 | 14.3s | 0.971 | 0.400 | 0.864 | 0.448 | 0.679 | 0.646 | 0.667 | 0.543 | 0.000 | 0.451 | 0.510 | 0.569 | 0.603 | FAIL (1 FP) | FAIL (1 FP) | 0.037 |
| `claude-opus-4-7` | 0.559 | $8.3310 | 2.2s | 0.886 | 0.480 | 0.886 | 0.445 | 0.344 | 0.600 | 0.667 | 0.595 | 0.000 | 0.520 | 0.733 | 0.592 | 0.524 | PASS | PASS | 0.062 |
| `openai/gpt-5.4-mini` | 0.539 | $0.8159 | 1.2s | 0.883 | 0.833 | 0.943 | 0.468 | 0.509 | 0.494 | 0.500 | 0.610 | 0.000 | 0.491 | 0.353 | 0.373 | 0.547 | FAIL (1 FP) | FAIL (1 FP) | 0.104 |
| `openai/o3` | 0.523 | $3.2379 | 7.8s | 0.876 | 0.000 | 0.848 | 0.472 | 0.742 | 0.664 | 0.333 | 0.687 | 0.000 | 0.644 | 0.698 | 0.372 | 0.464 | PASS | PASS | 0.138 |
| `x-ai/grok-4.3` | 0.467 | $1.5978 | 4.0s | 0.938 | 0.533 | 0.507 | 0.179 | 0.208 | 0.572 | 0.433 | 0.472 | 0.000 | 0.486 | 0.771 | 0.393 | 0.585 | PASS | PASS | 0.097 |
| `deepseek/deepseek-v4-flash` | 0.438 | $0.1442 | 4.0s | 0.718 | 0.587 | 0.445 | 0.310 | 0.218 | 0.642 | 0.337 | 0.535 | 0.000 | 0.465 | 0.569 | 0.298 | 0.574 | FAIL (1 FP) | PASS | 0.113 |
| `google/gemma-4-31b-it` | 0.437 | $0.1377 | 2.3s | 0.879 | 0.480 | 0.811 | 0.119 | 0.158 | 0.645 | 0.467 | 0.514 | 0.000 | 0.496 | 0.585 | 0.350 | 0.182 | FAIL (1 FP) | PASS | 0.073 |
| `deepseek/deepseek-r1` | 0.433 | $1.1813 | 19.9s | 0.732 | 0.693 | 0.658 | 0.158 | 0.268 | 0.630 | 0.313 | 0.555 | 0.000 | 0.462 | 0.401 | 0.327 | 0.435 | FAIL (1 FP) | FAIL (1 FP) | 0.132 |
| `claude-sonnet-4-6` | 0.412 | $3.7663 | 1.4s | 0.889 | 0.800 | 0.407 | 0.237 | 0.275 | 0.400 | 0.000 | 0.444 | 0.000 | 0.516 | 0.836 | 0.179 | 0.375 | PASS | PASS | 0.038 |
| `qwen/qwen3-235b-a22b-2507` | 0.400 | $0.0784 | 2.3s | 0.774 | 0.853 | 0.756 | 0.142 | 0.033 | 0.540 | 0.000 | 0.517 | 0.000 | 0.460 | 0.342 | 0.421 | 0.362 | FAIL (2 FP) | FAIL (6 FP) | 0.076 |
| `openai/gpt-oss-120b` | 0.399 | $0.0680 | 2.9s | 0.819 | 0.600 | 0.270 | 0.201 | 0.058 | 0.625 | 0.227 | 0.457 | 0.000 | 0.469 | 0.630 | 0.387 | 0.443 | FAIL (1 FP) | PASS | 0.127 |
| `deepseek/deepseek-r1-0528` | 0.387 | $1.0738 | 16.0s | 0.717 | 0.647 | 0.700 | 0.184 | 0.125 | 0.379 | 0.404 | 0.514 | 0.000 | 0.281 | 0.161 | 0.283 | 0.643 | FAIL (27 FP) | FAIL (12 FP) | 0.131 |
| `nvidia/llama-3.3-nemotron-super-49b-v1.5` | 0.379 | $0.2283 | 24.0s | 0.888 | 0.667 | 0.299 | 0.139 | 0.045 | 0.596 | 0.233 | 0.431 | 0.000 | 0.587 | 0.478 | 0.171 | 0.399 | PASS | PASS | 0.124 |
| `cohere/command-a` | 0.377 | $2.8495 | 3.8s | 0.911 | 0.400 | 0.500 | 0.298 | 0.031 | 0.507 | 0.400 | 0.503 | 0.000 | 0.368 | 0.247 | 0.200 | 0.533 | FAIL (3 FP) | PASS | 0.037 |
| `google/gemini-3.1-flash-lite` | 0.371 | $0.2941 | 0.8s | 0.771 | 0.800 | 0.473 | 0.076 | 0.039 | 0.527 | 0.433 | 0.400 | 0.000 | 0.413 | 0.338 | 0.183 | 0.375 | FAIL (1 FP) | PASS | 0.036 |
| `moonshotai/kimi-k2.6` | 0.369 | $2.4862 | 36.2s | 0.547 | 0.100 | 0.914 | 0.600 | 0.053 | 0.469 | 0.200 | 0.734 | 0.000 | 0.196 | 0.538 | 0.184 | 0.267 | FAIL (1 FP) | FAIL (4 FP) | 0.152 |
| `meta-llama/llama-4-maverick` | 0.368 | $0.1610 | 1.0s | 1.000 | 0.000 | 0.771 | 0.204 | 0.273 | 0.507 | 0.000 | 0.571 | 0.000 | 0.496 | 0.390 | 0.167 | 0.400 | FAIL (1 FP) | PASS | 0.015 |
| `mistralai/mistral-medium-3.1` | 0.340 | $0.4666 | 0.9s | 0.397 | 0.667 | 0.162 | 0.095 | 0.046 | 0.671 | 0.000 | 0.500 | 0.000 | 0.640 | 0.591 | 0.223 | 0.421 | PASS | PASS | 0.043 |
| `claude-haiku-4-5-20251001` | 0.339 | $1.2786 | 1.1s | 0.500 | 0.800 | 0.235 | 0.073 | 0.042 | 0.571 | 0.000 | 0.500 | 0.000 | 0.551 | 0.600 | 0.154 | 0.375 | PASS | PASS | 0.001 |
| `deepseek/deepseek-v4-pro` | 0.338 | $0.6795 | 26.8s | 0.728 | 0.133 | 0.418 | 0.350 | 0.262 | 0.449 | 0.267 | 0.257 | 0.000 | 0.498 | 0.167 | 0.565 | 0.305 | PASS | PASS | 0.204 |
| `mistralai/codestral-2508` | 0.290 | $0.3456 | 0.7s | 0.520 | 0.667 | 0.379 | 0.172 | 0.033 | 0.231 | 0.000 | 0.469 | 0.000 | 0.374 | 0.187 | 0.178 | 0.564 | PASS | PASS | 0.045 |
| `google/gemini-2.5-flash-lite` | 0.279 | $0.1177 | 0.9s | 0.462 | 0.667 | 0.326 | 0.069 | 0.084 | 0.413 | 0.000 | 0.400 | 0.000 | 0.280 | 0.432 | 0.133 | 0.364 | FAIL (1 FP) | PASS | 0.021 |
| `deepseek/deepseek-v3.2` | 0.275 | $0.2738 | 2.8s | 0.590 | 0.300 | 0.327 | 0.140 | 0.176 | 0.518 | 0.400 | 0.300 | 0.000 | 0.528 | 0.100 | 0.057 | 0.140 | PASS | FAIL (2 FP) | 0.176 |
| `google/gemini-2.5-flash` | 0.270 | $0.3648 | 1.0s | 0.462 | 0.800 | 0.267 | 0.071 | 0.037 | 0.500 | 0.000 | 0.444 | 0.000 | 0.455 | 0.125 | 0.154 | 0.200 | PASS | PASS | 0.000 |
| `openai/gpt-3.5-turbo` | 0.264 | $0.5436 | 1.3s | 0.978 | 0.420 | 0.222 | 0.364 | 0.038 | 0.254 | 0.000 | 0.400 | 0.000 | 0.217 | 0.220 | 0.125 | 0.189 | FAIL (3 FP) | FAIL (10 FP) | 0.012 |
| `meta-llama/llama-3.3-70b-instruct` | 0.253 | $0.1076 | 1.4s | 0.502 | 0.100 | 0.327 | 0.000 | 0.000 | 0.567 | 0.000 | 0.133 | 0.000 | 0.512 | 0.489 | 0.308 | 0.347 | PASS | PASS | 0.075 |
| `mistralai/mistral-large-2512` | 0.230 | $0.5964 | 2.5s | 0.492 | 0.648 | 0.250 | 0.074 | 0.033 | 0.253 | 0.000 | 0.444 | 0.000 | 0.209 | 0.193 | 0.044 | 0.353 | PASS | PASS | 0.015 |
| `qwen/qwen3-14b` | 0.229 | $0.1346 | 20.9s | 0.190 | 0.000 | 0.518 | 0.100 | 0.045 | 0.434 | 0.000 | 0.330 | 0.000 | 0.336 | 0.372 | 0.233 | 0.412 | PASS | FAIL (1 FP) | 0.154 |
| `deepseek/deepseek-r1-distill-llama-70b` | 0.222 | $0.7825 | 2.0s | 0.590 | 0.440 | 0.057 | 0.205 | 0.000 | 0.265 | 0.000 | 0.343 | 0.000 | 0.262 | 0.222 | 0.163 | 0.340 | FAIL (2 FP) | FAIL (10 FP) | 0.090 |
| `meta-llama/llama-4-scout` | 0.217 | $0.0861 | 0.8s | 0.253 | 0.587 | 0.242 | 0.130 | 0.000 | 0.336 | 0.000 | 0.094 | 0.000 | 0.520 | 0.349 | 0.053 | 0.258 | PASS | PASS | 0.101 |
| `nvidia/nemotron-nano-9b-v2` | 0.204 | $0.0872 | 12.4s | 0.334 | 0.600 | 0.274 | 0.069 | 0.044 | 0.242 | 0.000 | 0.361 | 0.000 | 0.238 | 0.205 | 0.044 | 0.240 | FAIL (1 FP) | PASS | 0.105 |
| `meta-llama/llama-3.1-8b-instruct` | 0.183 | $0.0221 | 0.8s | 0.164 | 0.700 | 0.231 | 0.029 | 0.017 | 0.183 | 0.400 | 0.284 | 0.000 | 0.133 | 0.000 | 0.049 | 0.185 | PASS | PASS | 0.113 |
| `cohere/command-r-plus-08-2024` | 0.094 | $2.7520 | 0.9s | 0.000 | 0.000 | 0.000 | 0.313 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.569 | 0.000 | 0.057 | 0.289 | PASS | PASS | 0.033 |
| `openai/o4-mini` | 0.069 | $2.0113 | 6.9s | 0.000 | 0.000 | 0.147 | 0.067 | 0.040 | 0.080 | 0.000 | 0.133 | 0.000 | 0.114 | 0.180 | 0.000 | 0.133 | PASS | PASS | 0.116 |
| `microsoft/phi-4` | 0.051 | $0.0758 | 2.3s | 0.157 | 0.000 | 0.000 | 0.056 | 0.058 | 0.000 | 0.000 | 0.213 | 0.000 | 0.033 | 0.067 | 0.079 | 0.000 | FAIL (3 FP) | FAIL (15 FP) | 0.057 |
| `qwen/qwen3-8b` | 0.004 | $0.2734 | 58.4s | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.057 | 0.000 | PASS | PASS | 0.010 |
| `mistralai/mistral-7b-instruct-v0.1` | 0.000 | $0.0301 | 7.0s | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | PASS | PASS | 0.000 |

---

## Detailed Results

### Per-Model Detail

Full per-model profile: F1 averaged across episodes, total cost per episode at current pricing, p50 / p95 latency, JSON compliance, parse-failure rate, the distribution of extraction methods the parser had to use, and verbosity / truncation telemetry. The `Extraction methods` list shows how often each route was hit. `json_array_direct` is the cleanest; the rest are recovery paths. The verbosity row flags models that emit long `reason` fields or run out of token budget mid-response. Ordered by F1 descending so the best performers appear first.

#### `qwen/qwen3.6-plus`

- F1 (avg across episodes): **0.640**
- Total cost / episode: **$1.1813**
- p50 / p95 latency: 39.89s / 74.23s
- JSON compliance: 1.00
- JSON mode: native (100% native, 910 calls)
- Parse failure rate: 0.0%
- Extraction methods: `json_array_direct`: 910
- Verbosity: 865/910 calls over 1024 output tokens (95.1%); 0 hit max_tokens (0.0%); 0 salvaged from truncated JSON (0.0%)

#### `qwen/qwen3.5-plus-02-15`

- F1 (avg across episodes): **0.627**
- Total cost / episode: **$1.2880**
- p50 / p95 latency: 47.30s / 142.96s
- JSON compliance: 1.00
- JSON mode: native (100% native, 907 calls)
- Parse failure rate: 0.1%
- Extraction methods: `json_array_direct`: 906, `parse_failure`: 1
- Verbosity: 790/907 calls over 1024 output tokens (87.1%); 0 hit max_tokens (0.0%); 0 salvaged from truncated JSON (0.0%)
- Schema violations: 384
- Extra keys observed: end_text, sponsor

#### `qwen/qwen3.6-flash`

- F1 (avg across episodes): **0.610**
- Total cost / episode: **$0.5817**
- p50 / p95 latency: 13.12s / 39.19s
- JSON compliance: 1.00
- JSON mode: native (100% native, 910 calls)
- Parse failure rate: 0.0%
- Extraction methods: `json_array_direct`: 910
- Verbosity: 635/910 calls over 1024 output tokens (69.8%); 0 hit max_tokens (0.0%); 0 salvaged from truncated JSON (0.0%)

#### `openai/gpt-5.5`

- F1 (avg across episodes): **0.597**
- Total cost / episode: **$7.1204**
- p50 / p95 latency: 6.37s / 24.09s
- JSON compliance: 0.88
- JSON mode: native (100% native, 910 calls)
- Parse failure rate: 0.1%
- Extraction methods: `json_object_no_ads`: 540, `json_object_single_ad`: 369, `parse_failure`: 1
- Verbosity: 80/910 calls over 1024 output tokens (8.8%); 0 hit max_tokens (0.0%); 0 salvaged from truncated JSON (0.0%)
- Schema violations: 332
- Extra keys observed: end_text, sponsor

#### `google/gemini-3.5-flash`

- F1 (avg across episodes): **0.577**
- Total cost / episode: **$3.7363**
- p50 / p95 latency: 5.24s / 11.04s
- JSON compliance: 1.00
- JSON mode: native (100% native, 910 calls)
- Parse failure rate: 0.0%
- Extraction methods: `json_array_direct`: 905, `json_object_single_ad_truncated`: 1, `regex_json_array`: 4
- Verbosity: 549/910 calls over 1024 output tokens (60.3%); 1 hit max_tokens (0.1%); 1 salvaged from truncated JSON (0.1%)

#### `qwen/qwen3.5-27b`

- F1 (avg across episodes): **0.574**
- Total cost / episode: **$3.4665**
- p50 / p95 latency: 70.53s / 1161.76s
- JSON compliance: 0.85
- JSON mode: native (100% native, 910 calls)
- Parse failure rate: 14.4%
- Extraction methods: `json_array_direct`: 673, `json_object_no_ads`: 94, `json_object_single_ad`: 12, `parse_failure`: 131
- Verbosity: 852/910 calls over 1024 output tokens (93.6%); 117 hit max_tokens (12.9%); 0 salvaged from truncated JSON (0.0%)

#### `openai/gpt-5.4`

- F1 (avg across episodes): **0.573**
- Total cost / episode: **$2.7073**
- p50 / p95 latency: 1.82s / 3.10s
- JSON compliance: 0.81
- JSON mode: native (100% native, 910 calls)
- Parse failure rate: 0.0%
- Extraction methods: `json_object_no_ads`: 346, `json_object_single_ad`: 564
- Verbosity: 0/910 calls over 1024 output tokens (0.0%); 0 hit max_tokens (0.0%); 0 salvaged from truncated JSON (0.0%)
- Schema violations: 398
- Extra keys observed: end_text, sponsor

#### `google/gemini-2.5-pro`

- F1 (avg across episodes): **0.566**
- Total cost / episode: **$4.1541**
- p50 / p95 latency: 14.32s / 28.66s
- JSON compliance: 0.97
- JSON mode: native (100% native, 910 calls)
- Parse failure rate: 1.4%
- Extraction methods: `json_array_direct`: 870, `json_object_single_ad_truncated`: 2, `parse_failure`: 13, `regex_json_array`: 25
- Verbosity: 720/910 calls over 1024 output tokens (79.1%); 2 hit max_tokens (0.2%); 2 salvaged from truncated JSON (0.2%)
- Schema violations: 389
- Extra keys observed: end_text, sponsor

#### `claude-opus-4-7`

- F1 (avg across episodes): **0.559**
- Total cost / episode: **$8.3310**
- p50 / p95 latency: 2.17s / 4.25s
- JSON compliance: 1.00
- JSON mode: prompt-inject (0% native, 910 calls)
- Parse failure rate: 0.0%
- Extraction methods: `json_array_direct`: 905, `regex_json_array`: 5
- Verbosity: 0/910 calls over 1024 output tokens (0.0%); 0 hit max_tokens (0.0%); 0 salvaged from truncated JSON (0.0%)
- Schema violations: 397
- Extra keys observed: end_text, sponsor

#### `openai/gpt-5.4-mini`

- F1 (avg across episodes): **0.539**
- Total cost / episode: **$0.8159**
- p50 / p95 latency: 1.18s / 2.24s
- JSON compliance: 0.81
- JSON mode: native (100% native, 910 calls)
- Parse failure rate: 0.0%
- Extraction methods: `json_object_ads_key`: 2, `json_object_no_ads`: 325, `json_object_segments_key`: 2, `json_object_single_ad`: 581
- Verbosity: 0/910 calls over 1024 output tokens (0.0%); 0 hit max_tokens (0.0%); 0 salvaged from truncated JSON (0.0%)

#### `openai/o3`

- F1 (avg across episodes): **0.523**
- Total cost / episode: **$3.2379**
- p50 / p95 latency: 7.79s / 27.19s
- JSON compliance: 0.93
- JSON mode: native (100% native, 910 calls)
- Parse failure rate: 0.5%
- Extraction methods: `json_object_ads_key`: 36, `json_object_no_ads`: 674, `json_object_segments_key`: 12, `json_object_single_ad`: 183, `parse_failure`: 5
- Verbosity: 251/910 calls over 1024 output tokens (27.6%); 0 hit max_tokens (0.0%); 0 salvaged from truncated JSON (0.0%)
- Schema violations: 132
- Extra keys observed: end_text, sponsor

#### `x-ai/grok-4.3`

- F1 (avg across episodes): **0.467**
- Total cost / episode: **$1.5978**
- p50 / p95 latency: 3.98s / 12.40s
- JSON compliance: 1.00
- JSON mode: native (100% native, 910 calls)
- Parse failure rate: 0.1%
- Extraction methods: `json_array_direct`: 909, `parse_failure`: 1
- Verbosity: 167/910 calls over 1024 output tokens (18.4%); 0 hit max_tokens (0.0%); 0 salvaged from truncated JSON (0.0%)

#### `deepseek/deepseek-v4-flash`

- F1 (avg across episodes): **0.438**
- Total cost / episode: **$0.1442**
- p50 / p95 latency: 3.95s / 28.27s
- JSON compliance: 0.81
- JSON mode: native (100% native, 910 calls)
- Parse failure rate: 1.9%
- Extraction methods: `json_array_direct`: 109, `json_object_ads_key`: 453, `json_object_no_ads`: 33, `json_object_segments_key`: 9, `json_object_single_ad`: 288, `parse_failure`: 17, `regex_json_array`: 1
- Verbosity: 195/910 calls over 1024 output tokens (21.4%); 2 hit max_tokens (0.2%); 0 salvaged from truncated JSON (0.0%)
- Schema violations: 631
- Extra keys observed: end_text, sponsor

#### `google/gemma-4-31b-it`

- F1 (avg across episodes): **0.437**
- Total cost / episode: **$0.1377**
- p50 / p95 latency: 2.26s / 18.73s
- JSON compliance: 0.86
- JSON mode: native (100% native, 910 calls)
- Parse failure rate: 0.1%
- Extraction methods: `bracket_fallback`: 1, `json_object_ads_key`: 486, `json_object_no_ads`: 237, `json_object_single_ad`: 183, `json_object_single_ad_truncated`: 2, `parse_failure`: 1
- Verbosity: 3/910 calls over 1024 output tokens (0.3%); 3 hit max_tokens (0.3%); 2 salvaged from truncated JSON (0.2%)
- Schema violations: 532
- Extra keys observed: end_text, sponsor

#### `deepseek/deepseek-r1`

- F1 (avg across episodes): **0.433**
- Total cost / episode: **$1.1813**
- p50 / p95 latency: 19.89s / 152.53s
- JSON compliance: 0.97
- JSON mode: native (100% native, 910 calls)
- Parse failure rate: 0.7%
- Extraction methods: `json_array_direct`: 809, `json_object_ads_key`: 3, `json_object_no_ads`: 18, `json_object_segments_key`: 7, `json_object_single_ad`: 47, `markdown_code_block`: 17, `parse_failure`: 6, `regex_json_array`: 3
- Verbosity: 157/910 calls over 1024 output tokens (17.3%); 0 hit max_tokens (0.0%); 0 salvaged from truncated JSON (0.0%)
- Schema violations: 486
- Extra keys observed: end_text, sponsor

#### `claude-sonnet-4-6`

- F1 (avg across episodes): **0.412**
- Total cost / episode: **$3.7663**
- p50 / p95 latency: 1.38s / 5.87s
- JSON compliance: 0.97
- JSON mode: prompt-inject (0% native, 910 calls)
- Parse failure rate: 0.0%
- Extraction methods: `json_array_direct`: 838, `markdown_code_block`: 57, `regex_json_array`: 15
- Verbosity: 0/910 calls over 1024 output tokens (0.0%); 0 hit max_tokens (0.0%); 0 salvaged from truncated JSON (0.0%)
- Schema violations: 461
- Extra keys observed: end_text, sponsor

#### `qwen/qwen3-235b-a22b-2507`

- F1 (avg across episodes): **0.400**
- Total cost / episode: **$0.0784**
- p50 / p95 latency: 2.30s / 7.70s
- JSON compliance: 0.79
- JSON mode: native (100% native, 910 calls)
- Parse failure rate: 0.0%
- Extraction methods: `json_array_direct`: 181, `json_object_ads_key`: 1, `json_object_no_ads`: 103, `json_object_single_ad`: 625
- Verbosity: 0/910 calls over 1024 output tokens (0.0%); 0 hit max_tokens (0.0%); 0 salvaged from truncated JSON (0.0%)

#### `openai/gpt-oss-120b`

- F1 (avg across episodes): **0.399**
- Total cost / episode: **$0.0680**
- p50 / p95 latency: 2.85s / 33.85s
- JSON compliance: 0.68
- JSON mode: native (100% native, 910 calls)
- Parse failure rate: 20.1%
- Extraction methods: `json_array_direct`: 68, `json_object_ads_key`: 235, `json_object_no_ads`: 187, `json_object_single_ad`: 208, `json_object_single_ad_truncated`: 6, `parse_failure`: 183, `regex_json_array`: 23
- Verbosity: 163/910 calls over 1024 output tokens (17.9%); 2 hit max_tokens (0.2%); 6 salvaged from truncated JSON (0.7%)

#### `deepseek/deepseek-r1-0528`

- F1 (avg across episodes): **0.387**
- Total cost / episode: **$1.0738**
- p50 / p95 latency: 15.99s / 93.33s
- JSON compliance: 0.89
- JSON mode: native (100% native, 910 calls)
- Parse failure rate: 9.1%
- Extraction methods: `json_array_direct`: 754, `json_object_ads_key`: 34, `json_object_no_ads`: 3, `json_object_single_ad`: 31, `json_object_single_ad_truncated`: 3, `markdown_code_block`: 2, `parse_failure`: 83
- Verbosity: 357/910 calls over 1024 output tokens (39.2%); 38 hit max_tokens (4.2%); 3 salvaged from truncated JSON (0.3%)
- Schema violations: 694
- Extra keys observed: end_text, sponsor

#### `nvidia/llama-3.3-nemotron-super-49b-v1.5`

- F1 (avg across episodes): **0.379**
- Total cost / episode: **$0.2283**
- p50 / p95 latency: 24.05s / 85.93s
- JSON compliance: 0.72
- JSON mode: native (100% native, 910 calls)
- Parse failure rate: 14.0%
- Extraction methods: `json_array_direct`: 479, `json_object_single_ad_truncated`: 3, `markdown_code_block`: 271, `parse_failure`: 127, `regex_json_array`: 30
- Verbosity: 546/910 calls over 1024 output tokens (60.0%); 61 hit max_tokens (6.7%); 3 salvaged from truncated JSON (0.3%)
- Schema violations: 266
- Extra keys observed: end_text, sponsor

#### `cohere/command-a`

- F1 (avg across episodes): **0.377**
- Total cost / episode: **$2.8495**
- p50 / p95 latency: 3.78s / 12.50s
- JSON compliance: 0.71
- JSON mode: native (100% native, 910 calls)
- Parse failure rate: 0.0%
- Extraction methods: `json_object_no_ads`: 29, `json_object_single_ad`: 881
- Verbosity: 0/910 calls over 1024 output tokens (0.0%); 0 hit max_tokens (0.0%); 0 salvaged from truncated JSON (0.0%)
- Schema violations: 478
- Extra keys observed: end_text, sponsor

#### `google/gemini-3.1-flash-lite`

- F1 (avg across episodes): **0.371**
- Total cost / episode: **$0.2941**
- p50 / p95 latency: 0.78s / 1.44s
- JSON compliance: 0.96
- JSON mode: native (100% native, 910 calls)
- Parse failure rate: 0.0%
- Extraction methods: `json_array_direct`: 849, `regex_json_array`: 61
- Verbosity: 0/910 calls over 1024 output tokens (0.0%); 0 hit max_tokens (0.0%); 0 salvaged from truncated JSON (0.0%)

#### `moonshotai/kimi-k2.6`

- F1 (avg across episodes): **0.369**
- Total cost / episode: **$2.4862**
- p50 / p95 latency: 36.24s / 181.59s
- JSON compliance: 0.58
- JSON mode: native (100% native, 910 calls)
- Parse failure rate: 27.6%
- Extraction methods: `json_array_direct`: 104, `json_object_ads_key`: 35, `json_object_no_ads`: 111, `json_object_segments_key`: 2, `json_object_single_ad`: 402, `markdown_code_block`: 5, `parse_failure`: 251
- Verbosity: 818/910 calls over 1024 output tokens (89.9%); 124 hit max_tokens (13.6%); 0 salvaged from truncated JSON (0.0%)
- Schema violations: 234
- Extra keys observed: end_text, sponsor

#### `meta-llama/llama-4-maverick`

- F1 (avg across episodes): **0.368**
- Total cost / episode: **$0.1610**
- p50 / p95 latency: 1.04s / 2.39s
- JSON compliance: 0.81
- JSON mode: native (100% native, 910 calls)
- Parse failure rate: 0.0%
- Extraction methods: `json_object_no_ads`: 342, `json_object_single_ad`: 568
- Verbosity: 3/910 calls over 1024 output tokens (0.3%); 0 hit max_tokens (0.0%); 0 salvaged from truncated JSON (0.0%)
- Schema violations: 372
- Extra keys observed: end_text, sponsor

#### `mistralai/mistral-medium-3.1`

- F1 (avg across episodes): **0.340**
- Total cost / episode: **$0.4666**
- p50 / p95 latency: 0.90s / 6.09s
- JSON compliance: 1.00
- JSON mode: native (100% native, 910 calls)
- Parse failure rate: 0.0%
- Extraction methods: `json_array_direct`: 909, `json_object_single_ad_truncated`: 1
- Verbosity: 0/910 calls over 1024 output tokens (0.0%); 0 hit max_tokens (0.0%); 1 salvaged from truncated JSON (0.1%)
- Schema violations: 608
- Extra keys observed: end_text, sponsor

#### `claude-haiku-4-5-20251001`

- F1 (avg across episodes): **0.339**
- Total cost / episode: **$1.2786**
- p50 / p95 latency: 1.11s / 4.06s
- JSON compliance: 0.60
- JSON mode: prompt-inject (0% native, 910 calls)
- Parse failure rate: 0.0%
- Extraction methods: `markdown_code_block`: 910
- Verbosity: 0/910 calls over 1024 output tokens (0.0%); 0 hit max_tokens (0.0%); 0 salvaged from truncated JSON (0.0%)
- Schema violations: 711
- Extra keys observed: end_text, sponsor

#### `deepseek/deepseek-v4-pro`

- F1 (avg across episodes): **0.338**
- Total cost / episode: **$0.6795**
- p50 / p95 latency: 26.79s / 99.89s
- JSON compliance: 0.87
- JSON mode: native (100% native, 910 calls)
- Parse failure rate: 2.6%
- Extraction methods: `json_array_direct`: 243, `json_object_ads_key`: 63, `json_object_no_ads`: 133, `json_object_segments_key`: 336, `json_object_single_ad`: 102, `json_object_single_ad_truncated`: 2, `markdown_code_block`: 5, `parse_failure`: 24, `regex_json_array`: 2
- Verbosity: 465/910 calls over 1024 output tokens (51.1%); 16 hit max_tokens (1.8%); 2 salvaged from truncated JSON (0.2%)

#### `mistralai/codestral-2508`

- F1 (avg across episodes): **0.290**
- Total cost / episode: **$0.3456**
- p50 / p95 latency: 0.73s / 2.23s
- JSON compliance: 1.00
- JSON mode: native (100% native, 910 calls)
- Parse failure rate: 0.0%
- Extraction methods: `json_array_direct`: 910
- Verbosity: 0/910 calls over 1024 output tokens (0.0%); 0 hit max_tokens (0.0%); 0 salvaged from truncated JSON (0.0%)
- Schema violations: 622
- Extra keys observed: end_text, sponsor

#### `google/gemini-2.5-flash-lite`

- F1 (avg across episodes): **0.279**
- Total cost / episode: **$0.1177**
- p50 / p95 latency: 0.90s / 2.98s
- JSON compliance: 0.97
- JSON mode: native (100% native, 910 calls)
- Parse failure rate: 2.1%
- Extraction methods: `json_array_direct`: 845, `json_object_single_ad_truncated`: 46, `parse_failure`: 19
- Verbosity: 0/910 calls over 1024 output tokens (0.0%); 0 hit max_tokens (0.0%); 46 salvaged from truncated JSON (5.1%)

#### `deepseek/deepseek-v3.2`

- F1 (avg across episodes): **0.275**
- Total cost / episode: **$0.2738**
- p50 / p95 latency: 2.81s / 7.50s
- JSON compliance: 0.87
- JSON mode: native (100% native, 910 calls)
- Parse failure rate: 0.0%
- Extraction methods: `json_array_direct`: 511, `json_object_ads_key`: 19, `json_object_no_ads`: 3, `json_object_single_ad`: 377
- Verbosity: 1/910 calls over 1024 output tokens (0.1%); 0 hit max_tokens (0.0%); 0 salvaged from truncated JSON (0.0%)
- Schema violations: 263
- Extra keys observed: end_text, sponsor

#### `google/gemini-2.5-flash`

- F1 (avg across episodes): **0.270**
- Total cost / episode: **$0.3648**
- p50 / p95 latency: 0.99s / 3.55s
- JSON compliance: 1.00
- JSON mode: native (100% native, 910 calls)
- Parse failure rate: 0.0%
- Extraction methods: `json_array_direct`: 910
- Verbosity: 0/910 calls over 1024 output tokens (0.0%); 0 hit max_tokens (0.0%); 0 salvaged from truncated JSON (0.0%)
- Schema violations: 710
- Extra keys observed: end_text, sponsor

#### `openai/gpt-3.5-turbo`

- F1 (avg across episodes): **0.264**
- Total cost / episode: **$0.5436**
- p50 / p95 latency: 1.26s / 1.93s
- JSON compliance: 0.71
- JSON mode: native (100% native, 910 calls)
- Parse failure rate: 0.5%
- Extraction methods: `json_object_no_ads`: 50, `json_object_single_ad`: 855, `parse_failure`: 5
- Verbosity: 0/910 calls over 1024 output tokens (0.0%); 0 hit max_tokens (0.0%); 0 salvaged from truncated JSON (0.0%)
- Schema violations: 593
- Extra keys observed: end_text, sponsor

#### `meta-llama/llama-3.3-70b-instruct`

- F1 (avg across episodes): **0.253**
- Total cost / episode: **$0.1076**
- p50 / p95 latency: 1.44s / 4.53s
- JSON compliance: 0.56
- JSON mode: native (100% native, 910 calls)
- Parse failure rate: 35.3%
- Extraction methods: `json_array_direct`: 155, `json_object_ads_key`: 1, `json_object_no_ads`: 163, `json_object_single_ad`: 267, `parse_failure`: 321, `regex_json_array`: 3
- Verbosity: 1/910 calls over 1024 output tokens (0.1%); 0 hit max_tokens (0.0%); 0 salvaged from truncated JSON (0.0%)
- Schema violations: 378
- Extra keys observed: end_text, sponsor

#### `mistralai/mistral-large-2512`

- F1 (avg across episodes): **0.230**
- Total cost / episode: **$0.5964**
- p50 / p95 latency: 2.46s / 6.18s
- JSON compliance: 1.00
- JSON mode: native (100% native, 910 calls)
- Parse failure rate: 0.0%
- Extraction methods: `json_array_direct`: 910
- Verbosity: 0/910 calls over 1024 output tokens (0.0%); 0 hit max_tokens (0.0%); 0 salvaged from truncated JSON (0.0%)
- Schema violations: 1342
- Extra keys observed: end_text, sponsor

#### `qwen/qwen3-14b`

- F1 (avg across episodes): **0.229**
- Total cost / episode: **$0.1346**
- p50 / p95 latency: 20.93s / 60.04s
- JSON compliance: 0.27
- JSON mode: native (100% native, 910 calls)
- Parse failure rate: 60.8%
- Extraction methods: `json_object_no_ads`: 1, `json_object_single_ad`: 356, `parse_failure`: 553
- Verbosity: 99/910 calls over 1024 output tokens (10.9%); 10 hit max_tokens (1.1%); 0 salvaged from truncated JSON (0.0%)

#### `deepseek/deepseek-r1-distill-llama-70b`

- F1 (avg across episodes): **0.222**
- Total cost / episode: **$0.7825**
- p50 / p95 latency: 1.97s / 47.29s
- JSON compliance: 0.74
- JSON mode: native (100% native, 910 calls)
- Parse failure rate: 1.9%
- Extraction methods: `json_array_direct`: 20, `json_object_ads_key`: 68, `json_object_no_ads`: 115, `json_object_single_ad`: 684, `json_object_single_ad_truncated`: 5, `parse_failure`: 17, `regex_json_array`: 1
- Verbosity: 60/910 calls over 1024 output tokens (6.6%); 19 hit max_tokens (2.1%); 5 salvaged from truncated JSON (0.5%)
- Schema violations: 474
- Extra keys observed: end_text, sponsor

#### `meta-llama/llama-4-scout`

- F1 (avg across episodes): **0.217**
- Total cost / episode: **$0.0861**
- p50 / p95 latency: 0.83s / 4.33s
- JSON compliance: 0.81
- JSON mode: native (100% native, 910 calls)
- Parse failure rate: 1.4%
- Extraction methods: `bracket_fallback`: 42, `json_array_direct`: 5, `json_object_ads_key`: 678, `json_object_no_ads`: 92, `json_object_single_ad`: 74, `parse_failure`: 13, `regex_json_array`: 6
- Verbosity: 1/910 calls over 1024 output tokens (0.1%); 0 hit max_tokens (0.0%); 0 salvaged from truncated JSON (0.0%)
- Schema violations: 507
- Extra keys observed: end_text, sponsor

#### `nvidia/nemotron-nano-9b-v2`

- F1 (avg across episodes): **0.204**
- Total cost / episode: **$0.0872**
- p50 / p95 latency: 12.37s / 36.90s
- JSON compliance: 0.92
- JSON mode: native (100% native, 910 calls)
- Parse failure rate: 6.5%
- Extraction methods: `json_array_direct`: 820, `json_object_single_ad_truncated`: 17, `parse_failure`: 59, `regex_json_array`: 14
- Verbosity: 532/910 calls over 1024 output tokens (58.5%); 14 hit max_tokens (1.5%); 17 salvaged from truncated JSON (1.9%)
- Schema violations: 476
- Extra keys observed: end_text, sponsor

#### `meta-llama/llama-3.1-8b-instruct`

- F1 (avg across episodes): **0.183**
- Total cost / episode: **$0.0221**
- p50 / p95 latency: 0.77s / 3.95s
- JSON compliance: 0.85
- JSON mode: native (100% native, 910 calls)
- Parse failure rate: 0.1%
- Extraction methods: `json_array_direct`: 389, `json_object_no_ads`: 66, `json_object_single_ad`: 454, `parse_failure`: 1
- Verbosity: 26/910 calls over 1024 output tokens (2.9%); 0 hit max_tokens (0.0%); 0 salvaged from truncated JSON (0.0%)
- Schema violations: 1270
- Extra keys observed: end_text, sponsor

#### `cohere/command-r-plus-08-2024`

- F1 (avg across episodes): **0.094**
- Total cost / episode: **$2.7520**
- p50 / p95 latency: 0.95s / 3.24s
- JSON compliance: 0.98
- JSON mode: native (100% native, 910 calls)
- Parse failure rate: 0.0%
- Extraction methods: `json_object_ads_key`: 27, `json_object_no_ads`: 838, `json_object_single_ad`: 45
- Verbosity: 0/910 calls over 1024 output tokens (0.0%); 0 hit max_tokens (0.0%); 0 salvaged from truncated JSON (0.0%)
- Schema violations: 132
- Extra keys observed: end_text, sponsor

#### `openai/o4-mini`

- F1 (avg across episodes): **0.069**
- Total cost / episode: **$2.0113**
- p50 / p95 latency: 6.94s / 25.43s
- JSON compliance: 0.05
- JSON mode: native (100% native, 910 calls)
- Parse failure rate: 94.3%
- Extraction methods: `json_object_no_ads`: 19, `json_object_single_ad`: 33, `parse_failure`: 858
- Verbosity: 354/910 calls over 1024 output tokens (38.9%); 12 hit max_tokens (1.3%); 0 salvaged from truncated JSON (0.0%)
- Schema violations: 28
- Extra keys observed: end_text, sponsor

#### `microsoft/phi-4`

- F1 (avg across episodes): **0.051**
- Total cost / episode: **$0.0758**
- p50 / p95 latency: 2.29s / 11.31s
- JSON compliance: 0.86
- JSON mode: native (100% native, 910 calls)
- Parse failure rate: 1.0%
- Extraction methods: `json_array_direct`: 445, `json_object_ads_key`: 34, `json_object_no_ads`: 30, `json_object_segments_key`: 20, `json_object_single_ad`: 360, `json_object_window_segments`: 2, `parse_failure`: 9, `regex_json_array`: 10
- Verbosity: 19/910 calls over 1024 output tokens (2.1%); 12 hit max_tokens (1.3%); 0 salvaged from truncated JSON (0.0%)
- Schema violations: 412
- Extra keys observed: end_text, sponsor

#### `qwen/qwen3-8b`

- F1 (avg across episodes): **0.004**
- Total cost / episode: **$0.2734**
- p50 / p95 latency: 58.44s / 141.13s
- JSON compliance: 0.01
- JSON mode: native (100% native, 910 calls)
- Parse failure rate: 97.4%
- Extraction methods: `bracket_fallback`: 20, `json_array_direct`: 4, `parse_failure`: 886
- Verbosity: 621/910 calls over 1024 output tokens (68.2%); 121 hit max_tokens (13.3%); 0 salvaged from truncated JSON (0.0%)

#### `mistralai/mistral-7b-instruct-v0.1`

- F1 (avg across episodes): **0.000**
- Total cost / episode: **$0.0301**
- p50 / p95 latency: 6.97s / 32.95s
- JSON compliance: 0.17
- JSON mode: native (100% native, 910 calls)
- Parse failure rate: 57.6%
- Extraction methods: `bracket_fallback`: 1, `parse_failure`: 524, `regex_json_array`: 385
- Verbosity: 15/910 calls over 1024 output tokens (1.6%); 0 hit max_tokens (0.0%); 0 salvaged from truncated JSON (0.0%)


### Per-Episode Detail

One subsection per episode in the corpus, showing how every model performed on that specific episode. For ad-bearing episodes you see F1 and the stdev across trials (low stdev means stable, high stdev means the model's number on this episode is noisy). For the no-ad episode you see PASS / FAIL on the negative control: PASS = zero false positives across all windows, FAIL = the model flagged something that wasn't an ad, with the count.

#### `ep-ai-cloud-essentials-e8dc897fbd6b`: How Physical AI is Streamlining Engineering

- Podcast: ai-cloud-essentials
- Duration: 16.4 min
- Truth: no-ads episode

| Model | Result | FP count |
|-------|--------|----------|
| `qwen/qwen3.5-27b` | PASS | 0 |
| `cohere/command-r-plus-08-2024` | PASS | 0 |
| `nvidia/llama-3.3-nemotron-super-49b-v1.5` | PASS | 0 |
| `mistralai/mistral-7b-instruct-v0.1` | PASS | 0 |
| `mistralai/mistral-large-2512` | PASS | 0 |
| `qwen/qwen3-8b` | PASS | 0 |
| `qwen/qwen3.6-plus` | PASS | 0 |
| `meta-llama/llama-3.1-8b-instruct` | PASS | 0 |
| `openai/o3` | PASS | 0 |
| `google/gemini-2.5-flash` | PASS | 0 |
| `deepseek/deepseek-v3.2` | PASS | 0 |
| `meta-llama/llama-4-scout` | PASS | 0 |
| `meta-llama/llama-3.3-70b-instruct` | PASS | 0 |
| `deepseek/deepseek-v4-pro` | PASS | 0 |
| `qwen/qwen3-14b` | PASS | 0 |
| `google/gemini-3.5-flash` | PASS | 0 |
| `mistralai/codestral-2508` | PASS | 0 |
| `qwen/qwen3.5-plus-02-15` | PASS | 0 |
| `x-ai/grok-4.3` | PASS | 0 |
| `claude-opus-4-7` | PASS | 0 |
| `claude-sonnet-4-6` | PASS | 0 |
| `claude-haiku-4-5-20251001` | PASS | 0 |
| `mistralai/mistral-medium-3.1` | PASS | 0 |
| `openai/o4-mini` | PASS | 0 |
| `moonshotai/kimi-k2.6` | FAIL | 1 |
| `qwen/qwen3.6-flash` | FAIL | 1 |
| `nvidia/nemotron-nano-9b-v2` | FAIL | 1 |
| `openai/gpt-oss-120b` | FAIL | 1 |
| `google/gemini-2.5-flash-lite` | FAIL | 1 |
| `google/gemma-4-31b-it` | FAIL | 1 |
| `openai/gpt-5.4` | FAIL | 1 |
| `google/gemini-2.5-pro` | FAIL | 1 |
| `openai/gpt-5.5` | FAIL | 1 |
| `deepseek/deepseek-r1` | FAIL | 1 |
| `openai/gpt-5.4-mini` | FAIL | 1 |
| `meta-llama/llama-4-maverick` | FAIL | 1 |
| `google/gemini-3.1-flash-lite` | FAIL | 1 |
| `deepseek/deepseek-v4-flash` | FAIL | 1 |
| `qwen/qwen3-235b-a22b-2507` | FAIL | 2 |
| `deepseek/deepseek-r1-distill-llama-70b` | FAIL | 2 |
| `cohere/command-a` | FAIL | 3 |
| `openai/gpt-3.5-turbo` | FAIL | 3 |
| `microsoft/phi-4` | FAIL | 3 |
| `deepseek/deepseek-r1-0528` | FAIL | 27 |

#### `ep-crime-junkie-8ce498f299d7`: MISSING: Christopher “Cole” Thomas

- Podcast: crime-junkie
- Duration: 48.2 min
- Truth ads: 4

| Model | F1 | F1 stdev |
|-------|----|----------|
| `qwen/qwen3.6-flash` | 1.000 | 0.000 |
| `qwen/qwen3.5-plus-02-15` | 1.000 | 0.000 |
| `meta-llama/llama-4-maverick` | 1.000 | 0.000 |
| `openai/gpt-3.5-turbo` | 0.978 | 0.050 |
| `google/gemini-2.5-pro` | 0.971 | 0.064 |
| `x-ai/grok-4.3` | 0.938 | 0.091 |
| `qwen/qwen3.6-plus` | 0.933 | 0.061 |
| `openai/gpt-5.4` | 0.933 | 0.061 |
| `google/gemini-3.5-flash` | 0.914 | 0.078 |
| `cohere/command-a` | 0.911 | 0.050 |
| `openai/gpt-5.5` | 0.898 | 0.059 |
| `qwen/qwen3.5-27b` | 0.892 | 0.062 |
| `claude-sonnet-4-6` | 0.889 | 0.000 |
| `nvidia/llama-3.3-nemotron-super-49b-v1.5` | 0.888 | 0.115 |
| `claude-opus-4-7` | 0.886 | 0.064 |
| `openai/gpt-5.4-mini` | 0.883 | 0.089 |
| `google/gemma-4-31b-it` | 0.879 | 0.097 |
| `openai/o3` | 0.876 | 0.137 |
| `openai/gpt-oss-120b` | 0.819 | 0.152 |
| `qwen/qwen3-235b-a22b-2507` | 0.774 | 0.143 |
| `google/gemini-3.1-flash-lite` | 0.771 | 0.040 |
| `deepseek/deepseek-r1` | 0.732 | 0.156 |
| `deepseek/deepseek-v4-pro` | 0.728 | 0.201 |
| `deepseek/deepseek-v4-flash` | 0.718 | 0.106 |
| `deepseek/deepseek-r1-0528` | 0.717 | 0.187 |
| `deepseek/deepseek-v3.2` | 0.590 | 0.074 |
| `deepseek/deepseek-r1-distill-llama-70b` | 0.590 | 0.230 |
| `moonshotai/kimi-k2.6` | 0.547 | 0.203 |
| `mistralai/codestral-2508` | 0.520 | 0.038 |
| `meta-llama/llama-3.3-70b-instruct` | 0.502 | 0.183 |
| `claude-haiku-4-5-20251001` | 0.500 | 0.000 |
| `mistralai/mistral-large-2512` | 0.492 | 0.017 |
| `google/gemini-2.5-flash-lite` | 0.462 | 0.000 |
| `google/gemini-2.5-flash` | 0.462 | 0.000 |
| `mistralai/mistral-medium-3.1` | 0.397 | 0.115 |
| `nvidia/nemotron-nano-9b-v2` | 0.334 | 0.117 |
| `meta-llama/llama-4-scout` | 0.253 | 0.167 |
| `qwen/qwen3-14b` | 0.190 | 0.294 |
| `meta-llama/llama-3.1-8b-instruct` | 0.164 | 0.157 |
| `microsoft/phi-4` | 0.157 | 0.089 |
| `cohere/command-r-plus-08-2024` | 0.000 | 0.000 |
| `mistralai/mistral-7b-instruct-v0.1` | 0.000 | 0.000 |
| `qwen/qwen3-8b` | 0.000 | 0.000 |
| `openai/o4-mini` | 0.000 | 0.000 |

#### `ep-daily-gist-chicago-70a82fe93a5c`: Suburban apartment market heats up

- Podcast: daily-gist-chicago
- Duration: 21.2 min
- Truth ads: 2

| Model | F1 | F1 stdev |
|-------|----|----------|
| `qwen/qwen3-235b-a22b-2507` | 0.853 | 0.145 |
| `openai/gpt-5.4-mini` | 0.833 | 0.236 |
| `google/gemini-2.5-flash` | 0.800 | 0.000 |
| `google/gemini-3.1-flash-lite` | 0.800 | 0.000 |
| `claude-sonnet-4-6` | 0.800 | 0.183 |
| `claude-haiku-4-5-20251001` | 0.800 | 0.000 |
| `qwen/qwen3.5-27b` | 0.727 | 0.186 |
| `qwen/qwen3.6-flash` | 0.700 | 0.245 |
| `meta-llama/llama-3.1-8b-instruct` | 0.700 | 0.183 |
| `deepseek/deepseek-r1` | 0.693 | 0.213 |
| `nvidia/llama-3.3-nemotron-super-49b-v1.5` | 0.667 | 0.000 |
| `google/gemini-2.5-flash-lite` | 0.667 | 0.000 |
| `qwen/qwen3.6-plus` | 0.667 | 0.000 |
| `mistralai/codestral-2508` | 0.667 | 0.000 |
| `mistralai/mistral-medium-3.1` | 0.667 | 0.000 |
| `mistralai/mistral-large-2512` | 0.648 | 0.043 |
| `deepseek/deepseek-r1-0528` | 0.647 | 0.228 |
| `nvidia/nemotron-nano-9b-v2` | 0.600 | 0.365 |
| `openai/gpt-oss-120b` | 0.600 | 0.365 |
| `qwen/qwen3.5-plus-02-15` | 0.600 | 0.091 |
| `meta-llama/llama-4-scout` | 0.587 | 0.335 |
| `deepseek/deepseek-v4-flash` | 0.587 | 0.335 |
| `x-ai/grok-4.3` | 0.533 | 0.075 |
| `openai/gpt-5.4` | 0.500 | 0.000 |
| `openai/gpt-5.5` | 0.500 | 0.000 |
| `claude-opus-4-7` | 0.480 | 0.179 |
| `google/gemma-4-31b-it` | 0.480 | 0.045 |
| `deepseek/deepseek-r1-distill-llama-70b` | 0.440 | 0.311 |
| `openai/gpt-3.5-turbo` | 0.420 | 0.045 |
| `cohere/command-a` | 0.400 | 0.000 |
| `google/gemini-2.5-pro` | 0.400 | 0.000 |
| `google/gemini-3.5-flash` | 0.400 | 0.000 |
| `deepseek/deepseek-v3.2` | 0.300 | 0.274 |
| `deepseek/deepseek-v4-pro` | 0.133 | 0.298 |
| `moonshotai/kimi-k2.6` | 0.100 | 0.224 |
| `meta-llama/llama-3.3-70b-instruct` | 0.100 | 0.224 |
| `cohere/command-r-plus-08-2024` | 0.000 | 0.000 |
| `mistralai/mistral-7b-instruct-v0.1` | 0.000 | 0.000 |
| `qwen/qwen3-8b` | 0.000 | 0.000 |
| `openai/o3` | 0.000 | 0.000 |
| `qwen/qwen3-14b` | 0.000 | 0.000 |
| `meta-llama/llama-4-maverick` | 0.000 | 0.000 |
| `microsoft/phi-4` | 0.000 | 0.000 |
| `openai/o4-mini` | 0.000 | 0.000 |

#### `ep-daily-tech-news-show-b576979e1fe8`: Motorola Razr Fold is a Noble Competitor to the Galaxy Z Fold 7 - DTNS 5269

- Podcast: daily-tech-news-show
- Duration: 34.6 min
- Truth ads: 4

| Model | F1 | F1 stdev |
|-------|----|----------|
| `openai/gpt-5.4-mini` | 0.943 | 0.078 |
| `moonshotai/kimi-k2.6` | 0.914 | 0.078 |
| `qwen/qwen3.6-plus` | 0.914 | 0.078 |
| `openai/gpt-5.4` | 0.892 | 0.062 |
| `openai/gpt-5.5` | 0.886 | 0.064 |
| `claude-opus-4-7` | 0.886 | 0.064 |
| `google/gemini-2.5-pro` | 0.864 | 0.089 |
| `google/gemini-3.5-flash` | 0.857 | 0.000 |
| `qwen/qwen3.5-plus-02-15` | 0.857 | 0.000 |
| `qwen/qwen3.6-flash` | 0.852 | 0.186 |
| `openai/o3` | 0.848 | 0.119 |
| `google/gemma-4-31b-it` | 0.811 | 0.132 |
| `meta-llama/llama-4-maverick` | 0.771 | 0.048 |
| `qwen/qwen3-235b-a22b-2507` | 0.756 | 0.265 |
| `qwen/qwen3.5-27b` | 0.743 | 0.104 |
| `deepseek/deepseek-r1-0528` | 0.700 | 0.245 |
| `deepseek/deepseek-r1` | 0.658 | 0.259 |
| `qwen/qwen3-14b` | 0.518 | 0.332 |
| `x-ai/grok-4.3` | 0.507 | 0.246 |
| `cohere/command-a` | 0.500 | 0.000 |
| `google/gemini-3.1-flash-lite` | 0.473 | 0.061 |
| `deepseek/deepseek-v4-flash` | 0.445 | 0.191 |
| `deepseek/deepseek-v4-pro` | 0.418 | 0.159 |
| `claude-sonnet-4-6` | 0.407 | 0.128 |
| `mistralai/codestral-2508` | 0.379 | 0.069 |
| `deepseek/deepseek-v3.2` | 0.327 | 0.211 |
| `meta-llama/llama-3.3-70b-instruct` | 0.327 | 0.095 |
| `google/gemini-2.5-flash-lite` | 0.326 | 0.036 |
| `nvidia/llama-3.3-nemotron-super-49b-v1.5` | 0.299 | 0.206 |
| `nvidia/nemotron-nano-9b-v2` | 0.274 | 0.194 |
| `openai/gpt-oss-120b` | 0.270 | 0.117 |
| `google/gemini-2.5-flash` | 0.267 | 0.000 |
| `mistralai/mistral-large-2512` | 0.250 | 0.011 |
| `meta-llama/llama-4-scout` | 0.242 | 0.181 |
| `claude-haiku-4-5-20251001` | 0.235 | 0.000 |
| `meta-llama/llama-3.1-8b-instruct` | 0.231 | 0.132 |
| `openai/gpt-3.5-turbo` | 0.222 | 0.000 |
| `mistralai/mistral-medium-3.1` | 0.162 | 0.049 |
| `openai/o4-mini` | 0.147 | 0.202 |
| `deepseek/deepseek-r1-distill-llama-70b` | 0.057 | 0.128 |
| `cohere/command-r-plus-08-2024` | 0.000 | 0.000 |
| `mistralai/mistral-7b-instruct-v0.1` | 0.000 | 0.000 |
| `qwen/qwen3-8b` | 0.000 | 0.000 |
| `microsoft/phi-4` | 0.000 | 0.000 |

#### `ep-daily-tech-news-show-c1904b8605f7`: Switch 2 Prices Rise, Forecast Drops - DTNS 5265

- Podcast: daily-tech-news-show
- Duration: 38.6 min
- Truth ads: 5

| Model | F1 | F1 stdev |
|-------|----|----------|
| `moonshotai/kimi-k2.6` | 0.600 | 0.091 |
| `openai/gpt-5.5` | 0.547 | 0.064 |
| `qwen/qwen3.5-plus-02-15` | 0.518 | 0.025 |
| `openai/gpt-5.4` | 0.518 | 0.078 |
| `qwen/qwen3.5-27b` | 0.506 | 0.107 |
| `google/gemini-3.5-flash` | 0.500 | 0.000 |
| `qwen/qwen3.6-flash` | 0.492 | 0.017 |
| `openai/o3` | 0.472 | 0.150 |
| `openai/gpt-5.4-mini` | 0.468 | 0.144 |
| `qwen/qwen3.6-plus` | 0.457 | 0.036 |
| `google/gemini-2.5-pro` | 0.448 | 0.018 |
| `claude-opus-4-7` | 0.445 | 0.096 |
| `openai/gpt-3.5-turbo` | 0.364 | 0.000 |
| `deepseek/deepseek-v4-pro` | 0.350 | 0.229 |
| `cohere/command-r-plus-08-2024` | 0.313 | 0.155 |
| `deepseek/deepseek-v4-flash` | 0.310 | 0.043 |
| `cohere/command-a` | 0.298 | 0.090 |
| `claude-sonnet-4-6` | 0.237 | 0.037 |
| `deepseek/deepseek-r1-distill-llama-70b` | 0.205 | 0.017 |
| `meta-llama/llama-4-maverick` | 0.204 | 0.010 |
| `openai/gpt-oss-120b` | 0.201 | 0.141 |
| `deepseek/deepseek-r1-0528` | 0.184 | 0.066 |
| `x-ai/grok-4.3` | 0.179 | 0.092 |
| `mistralai/codestral-2508` | 0.172 | 0.046 |
| `deepseek/deepseek-r1` | 0.158 | 0.037 |
| `qwen/qwen3-235b-a22b-2507` | 0.142 | 0.087 |
| `deepseek/deepseek-v3.2` | 0.140 | 0.142 |
| `nvidia/llama-3.3-nemotron-super-49b-v1.5` | 0.139 | 0.150 |
| `meta-llama/llama-4-scout` | 0.130 | 0.115 |
| `google/gemma-4-31b-it` | 0.119 | 0.058 |
| `qwen/qwen3-14b` | 0.100 | 0.224 |
| `mistralai/mistral-medium-3.1` | 0.095 | 0.010 |
| `google/gemini-3.1-flash-lite` | 0.076 | 0.001 |
| `mistralai/mistral-large-2512` | 0.074 | 0.000 |
| `claude-haiku-4-5-20251001` | 0.073 | 0.001 |
| `google/gemini-2.5-flash` | 0.071 | 0.000 |
| `google/gemini-2.5-flash-lite` | 0.069 | 0.040 |
| `nvidia/nemotron-nano-9b-v2` | 0.069 | 0.098 |
| `openai/o4-mini` | 0.067 | 0.149 |
| `microsoft/phi-4` | 0.056 | 0.077 |
| `meta-llama/llama-3.1-8b-instruct` | 0.029 | 0.064 |
| `mistralai/mistral-7b-instruct-v0.1` | 0.000 | 0.000 |
| `qwen/qwen3-8b` | 0.000 | 0.000 |
| `meta-llama/llama-3.3-70b-instruct` | 0.000 | 0.000 |

#### `ep-drink-champs-30c9a2d49f13`: Episode 501 w/ Warren Sapp

- Podcast: drink-champs
- Duration: 258.6 min
- Truth ads: 9

| Model | F1 | F1 stdev |
|-------|----|----------|
| `openai/o3` | 0.742 | 0.113 |
| `qwen/qwen3.5-plus-02-15` | 0.711 | 0.022 |
| `qwen/qwen3.6-plus` | 0.706 | 0.027 |
| `google/gemini-2.5-pro` | 0.679 | 0.025 |
| `openai/gpt-5.5` | 0.642 | 0.125 |
| `qwen/qwen3.6-flash` | 0.628 | 0.076 |
| `openai/gpt-5.4` | 0.571 | 0.068 |
| `openai/gpt-5.4-mini` | 0.509 | 0.068 |
| `google/gemini-3.5-flash` | 0.496 | 0.021 |
| `qwen/qwen3.5-27b` | 0.494 | 0.184 |
| `claude-opus-4-7` | 0.344 | 0.054 |
| `claude-sonnet-4-6` | 0.275 | 0.085 |
| `meta-llama/llama-4-maverick` | 0.273 | 0.000 |
| `deepseek/deepseek-r1` | 0.268 | 0.153 |
| `deepseek/deepseek-v4-pro` | 0.262 | 0.111 |
| `deepseek/deepseek-v4-flash` | 0.218 | 0.063 |
| `x-ai/grok-4.3` | 0.208 | 0.047 |
| `deepseek/deepseek-v3.2` | 0.176 | 0.087 |
| `google/gemma-4-31b-it` | 0.158 | 0.035 |
| `deepseek/deepseek-r1-0528` | 0.125 | 0.058 |
| `google/gemini-2.5-flash-lite` | 0.084 | 0.006 |
| `microsoft/phi-4` | 0.058 | 0.053 |
| `openai/gpt-oss-120b` | 0.058 | 0.060 |
| `moonshotai/kimi-k2.6` | 0.053 | 0.074 |
| `mistralai/mistral-medium-3.1` | 0.046 | 0.003 |
| `nvidia/llama-3.3-nemotron-super-49b-v1.5` | 0.045 | 0.067 |
| `qwen/qwen3-14b` | 0.045 | 0.061 |
| `nvidia/nemotron-nano-9b-v2` | 0.044 | 0.025 |
| `claude-haiku-4-5-20251001` | 0.042 | 0.000 |
| `openai/o4-mini` | 0.040 | 0.089 |
| `google/gemini-3.1-flash-lite` | 0.039 | 0.002 |
| `openai/gpt-3.5-turbo` | 0.038 | 0.035 |
| `google/gemini-2.5-flash` | 0.037 | 0.000 |
| `mistralai/mistral-large-2512` | 0.033 | 0.001 |
| `qwen/qwen3-235b-a22b-2507` | 0.033 | 0.031 |
| `mistralai/codestral-2508` | 0.033 | 0.030 |
| `cohere/command-a` | 0.031 | 0.043 |
| `meta-llama/llama-3.1-8b-instruct` | 0.017 | 0.015 |
| `cohere/command-r-plus-08-2024` | 0.000 | 0.000 |
| `mistralai/mistral-7b-instruct-v0.1` | 0.000 | 0.000 |
| `deepseek/deepseek-r1-distill-llama-70b` | 0.000 | 0.000 |
| `qwen/qwen3-8b` | 0.000 | 0.000 |
| `meta-llama/llama-4-scout` | 0.000 | 0.000 |
| `meta-llama/llama-3.3-70b-instruct` | 0.000 | 0.000 |

#### `ep-glt1412515089-373d5ba5007b`: #2496 - Julia Mossbridge

- Podcast: glt1412515089
- Duration: 165.3 min
- Truth ads: 4

| Model | F1 | F1 stdev |
|-------|----|----------|
| `google/gemini-3.5-flash` | 0.676 | 0.070 |
| `mistralai/mistral-medium-3.1` | 0.671 | 0.098 |
| `openai/o3` | 0.664 | 0.063 |
| `google/gemini-2.5-pro` | 0.646 | 0.028 |
| `google/gemma-4-31b-it` | 0.645 | 0.085 |
| `deepseek/deepseek-v4-flash` | 0.642 | 0.072 |
| `openai/gpt-5.5` | 0.636 | 0.143 |
| `deepseek/deepseek-r1` | 0.630 | 0.067 |
| `qwen/qwen3.5-plus-02-15` | 0.625 | 0.057 |
| `openai/gpt-oss-120b` | 0.625 | 0.084 |
| `qwen/qwen3.5-27b` | 0.601 | 0.116 |
| `qwen/qwen3.6-plus` | 0.600 | 0.000 |
| `claude-opus-4-7` | 0.600 | 0.000 |
| `nvidia/llama-3.3-nemotron-super-49b-v1.5` | 0.596 | 0.074 |
| `x-ai/grok-4.3` | 0.572 | 0.131 |
| `claude-haiku-4-5-20251001` | 0.571 | 0.000 |
| `meta-llama/llama-3.3-70b-instruct` | 0.567 | 0.030 |
| `qwen/qwen3-235b-a22b-2507` | 0.540 | 0.070 |
| `google/gemini-3.1-flash-lite` | 0.527 | 0.015 |
| `qwen/qwen3.6-flash` | 0.518 | 0.025 |
| `deepseek/deepseek-v3.2` | 0.518 | 0.085 |
| `cohere/command-a` | 0.507 | 0.027 |
| `meta-llama/llama-4-maverick` | 0.507 | 0.015 |
| `openai/gpt-5.4` | 0.506 | 0.059 |
| `google/gemini-2.5-flash` | 0.500 | 0.000 |
| `openai/gpt-5.4-mini` | 0.494 | 0.052 |
| `moonshotai/kimi-k2.6` | 0.469 | 0.066 |
| `deepseek/deepseek-v4-pro` | 0.449 | 0.128 |
| `qwen/qwen3-14b` | 0.434 | 0.153 |
| `google/gemini-2.5-flash-lite` | 0.413 | 0.020 |
| `claude-sonnet-4-6` | 0.400 | 0.000 |
| `deepseek/deepseek-r1-0528` | 0.379 | 0.061 |
| `meta-llama/llama-4-scout` | 0.336 | 0.138 |
| `deepseek/deepseek-r1-distill-llama-70b` | 0.265 | 0.036 |
| `openai/gpt-3.5-turbo` | 0.254 | 0.006 |
| `mistralai/mistral-large-2512` | 0.253 | 0.039 |
| `nvidia/nemotron-nano-9b-v2` | 0.242 | 0.087 |
| `mistralai/codestral-2508` | 0.231 | 0.091 |
| `meta-llama/llama-3.1-8b-instruct` | 0.183 | 0.109 |
| `openai/o4-mini` | 0.080 | 0.179 |
| `cohere/command-r-plus-08-2024` | 0.000 | 0.000 |
| `mistralai/mistral-7b-instruct-v0.1` | 0.000 | 0.000 |
| `qwen/qwen3-8b` | 0.000 | 0.000 |
| `microsoft/phi-4` | 0.000 | 0.000 |

#### `ep-it-s-a-thing-e339179dfad6`: SOUP shots - It's a Thing 418

- Podcast: it-s-a-thing
- Duration: 26.7 min
- Truth ads: 1

| Model | F1 | F1 stdev |
|-------|----|----------|
| `qwen/qwen3.6-flash` | 0.667 | 0.000 |
| `qwen/qwen3.6-plus` | 0.667 | 0.000 |
| `google/gemini-2.5-pro` | 0.667 | 0.000 |
| `openai/gpt-5.5` | 0.667 | 0.000 |
| `google/gemini-3.5-flash` | 0.667 | 0.000 |
| `qwen/qwen3.5-plus-02-15` | 0.667 | 0.000 |
| `claude-opus-4-7` | 0.667 | 0.000 |
| `openai/gpt-5.4` | 0.613 | 0.119 |
| `qwen/qwen3.5-27b` | 0.600 | 0.365 |
| `openai/gpt-5.4-mini` | 0.500 | 0.289 |
| `google/gemma-4-31b-it` | 0.467 | 0.274 |
| `x-ai/grok-4.3` | 0.433 | 0.253 |
| `google/gemini-3.1-flash-lite` | 0.433 | 0.253 |
| `deepseek/deepseek-r1-0528` | 0.404 | 0.281 |
| `cohere/command-a` | 0.400 | 0.000 |
| `meta-llama/llama-3.1-8b-instruct` | 0.400 | 0.548 |
| `deepseek/deepseek-v3.2` | 0.400 | 0.548 |
| `deepseek/deepseek-v4-flash` | 0.337 | 0.239 |
| `openai/o3` | 0.333 | 0.471 |
| `deepseek/deepseek-r1` | 0.313 | 0.301 |
| `deepseek/deepseek-v4-pro` | 0.267 | 0.365 |
| `nvidia/llama-3.3-nemotron-super-49b-v1.5` | 0.233 | 0.325 |
| `openai/gpt-oss-120b` | 0.227 | 0.209 |
| `moonshotai/kimi-k2.6` | 0.200 | 0.274 |
| `qwen/qwen3-235b-a22b-2507` | 0.000 | 0.000 |
| `openai/gpt-3.5-turbo` | 0.000 | 0.000 |
| `cohere/command-r-plus-08-2024` | 0.000 | 0.000 |
| `mistralai/mistral-7b-instruct-v0.1` | 0.000 | 0.000 |
| `nvidia/nemotron-nano-9b-v2` | 0.000 | 0.000 |
| `mistralai/mistral-large-2512` | 0.000 | 0.000 |
| `deepseek/deepseek-r1-distill-llama-70b` | 0.000 | 0.000 |
| `qwen/qwen3-8b` | 0.000 | 0.000 |
| `google/gemini-2.5-flash-lite` | 0.000 | 0.000 |
| `google/gemini-2.5-flash` | 0.000 | 0.000 |
| `meta-llama/llama-4-scout` | 0.000 | 0.000 |
| `meta-llama/llama-3.3-70b-instruct` | 0.000 | 0.000 |
| `qwen/qwen3-14b` | 0.000 | 0.000 |
| `mistralai/codestral-2508` | 0.000 | 0.000 |
| `meta-llama/llama-4-maverick` | 0.000 | 0.000 |
| `claude-sonnet-4-6` | 0.000 | 0.000 |
| `claude-haiku-4-5-20251001` | 0.000 | 0.000 |
| `mistralai/mistral-medium-3.1` | 0.000 | 0.000 |
| `microsoft/phi-4` | 0.000 | 0.000 |
| `openai/o4-mini` | 0.000 | 0.000 |

#### `ep-on-air-with-dan-and-alex2-574e4f303730`: Ryanair Wants Alcohol Bans, Emirates' $6.8B Record Profit & Buying Spirit Airlines?!

- Podcast: on-air-with-dan-and-alex2
- Duration: 58.1 min
- Truth ads: 2

| Model | F1 | F1 stdev |
|-------|----|----------|
| `qwen/qwen3.6-plus` | 0.773 | 0.060 |
| `openai/gpt-5.4` | 0.747 | 0.073 |
| `moonshotai/kimi-k2.6` | 0.734 | 0.200 |
| `openai/o3` | 0.687 | 0.124 |
| `qwen/qwen3.6-flash` | 0.682 | 0.115 |
| `qwen/qwen3.5-27b` | 0.634 | 0.194 |
| `openai/gpt-5.4-mini` | 0.610 | 0.052 |
| `claude-opus-4-7` | 0.595 | 0.071 |
| `qwen/qwen3.5-plus-02-15` | 0.590 | 0.043 |
| `openai/gpt-5.5` | 0.571 | 0.000 |
| `google/gemini-3.5-flash` | 0.571 | 0.000 |
| `meta-llama/llama-4-maverick` | 0.571 | 0.000 |
| `deepseek/deepseek-r1` | 0.555 | 0.176 |
| `google/gemini-2.5-pro` | 0.543 | 0.039 |
| `deepseek/deepseek-v4-flash` | 0.535 | 0.112 |
| `qwen/qwen3-235b-a22b-2507` | 0.517 | 0.054 |
| `google/gemma-4-31b-it` | 0.514 | 0.032 |
| `deepseek/deepseek-r1-0528` | 0.514 | 0.122 |
| `cohere/command-a` | 0.503 | 0.045 |
| `claude-haiku-4-5-20251001` | 0.500 | 0.000 |
| `mistralai/mistral-medium-3.1` | 0.500 | 0.000 |
| `x-ai/grok-4.3` | 0.472 | 0.066 |
| `mistralai/codestral-2508` | 0.469 | 0.045 |
| `openai/gpt-oss-120b` | 0.457 | 0.178 |
| `mistralai/mistral-large-2512` | 0.444 | 0.000 |
| `google/gemini-2.5-flash` | 0.444 | 0.000 |
| `claude-sonnet-4-6` | 0.444 | 0.000 |
| `nvidia/llama-3.3-nemotron-super-49b-v1.5` | 0.431 | 0.109 |
| `openai/gpt-3.5-turbo` | 0.400 | 0.000 |
| `google/gemini-2.5-flash-lite` | 0.400 | 0.000 |
| `google/gemini-3.1-flash-lite` | 0.400 | 0.000 |
| `nvidia/nemotron-nano-9b-v2` | 0.361 | 0.091 |
| `deepseek/deepseek-r1-distill-llama-70b` | 0.343 | 0.062 |
| `qwen/qwen3-14b` | 0.330 | 0.242 |
| `deepseek/deepseek-v3.2` | 0.300 | 0.274 |
| `meta-llama/llama-3.1-8b-instruct` | 0.284 | 0.166 |
| `deepseek/deepseek-v4-pro` | 0.257 | 0.433 |
| `microsoft/phi-4` | 0.213 | 0.307 |
| `meta-llama/llama-3.3-70b-instruct` | 0.133 | 0.298 |
| `openai/o4-mini` | 0.133 | 0.298 |
| `meta-llama/llama-4-scout` | 0.094 | 0.130 |
| `cohere/command-r-plus-08-2024` | 0.000 | 0.000 |
| `mistralai/mistral-7b-instruct-v0.1` | 0.000 | 0.000 |
| `qwen/qwen3-8b` | 0.000 | 0.000 |

#### `ep-oxide-and-friends-ce789ff5b62e`: Mechanical Engineering at Oxide [chapter images]

- Podcast: oxide-and-friends
- Duration: 84.5 min
- Truth: no-ads episode

| Model | Result | FP count |
|-------|--------|----------|
| `cohere/command-a` | PASS | 0 |
| `qwen/qwen3.5-27b` | PASS | 0 |
| `cohere/command-r-plus-08-2024` | PASS | 0 |
| `nvidia/llama-3.3-nemotron-super-49b-v1.5` | PASS | 0 |
| `qwen/qwen3.6-flash` | PASS | 0 |
| `mistralai/mistral-7b-instruct-v0.1` | PASS | 0 |
| `nvidia/nemotron-nano-9b-v2` | PASS | 0 |
| `openai/gpt-oss-120b` | PASS | 0 |
| `mistralai/mistral-large-2512` | PASS | 0 |
| `qwen/qwen3-8b` | PASS | 0 |
| `google/gemini-2.5-flash-lite` | PASS | 0 |
| `qwen/qwen3.6-plus` | PASS | 0 |
| `meta-llama/llama-3.1-8b-instruct` | PASS | 0 |
| `google/gemma-4-31b-it` | PASS | 0 |
| `openai/o3` | PASS | 0 |
| `google/gemini-2.5-flash` | PASS | 0 |
| `openai/gpt-5.5` | PASS | 0 |
| `meta-llama/llama-4-scout` | PASS | 0 |
| `meta-llama/llama-3.3-70b-instruct` | PASS | 0 |
| `deepseek/deepseek-v4-pro` | PASS | 0 |
| `google/gemini-3.5-flash` | PASS | 0 |
| `mistralai/codestral-2508` | PASS | 0 |
| `qwen/qwen3.5-plus-02-15` | PASS | 0 |
| `x-ai/grok-4.3` | PASS | 0 |
| `claude-opus-4-7` | PASS | 0 |
| `meta-llama/llama-4-maverick` | PASS | 0 |
| `google/gemini-3.1-flash-lite` | PASS | 0 |
| `deepseek/deepseek-v4-flash` | PASS | 0 |
| `claude-sonnet-4-6` | PASS | 0 |
| `claude-haiku-4-5-20251001` | PASS | 0 |
| `mistralai/mistral-medium-3.1` | PASS | 0 |
| `openai/o4-mini` | PASS | 0 |
| `openai/gpt-5.4` | FAIL | 1 |
| `google/gemini-2.5-pro` | FAIL | 1 |
| `qwen/qwen3-14b` | FAIL | 1 |
| `deepseek/deepseek-r1` | FAIL | 1 |
| `openai/gpt-5.4-mini` | FAIL | 1 |
| `deepseek/deepseek-v3.2` | FAIL | 2 |
| `moonshotai/kimi-k2.6` | FAIL | 4 |
| `qwen/qwen3-235b-a22b-2507` | FAIL | 6 |
| `openai/gpt-3.5-turbo` | FAIL | 10 |
| `deepseek/deepseek-r1-distill-llama-70b` | FAIL | 10 |
| `deepseek/deepseek-r1-0528` | FAIL | 12 |
| `microsoft/phi-4` | FAIL | 15 |

#### `ep-politics-politics-politics-9d7642c84fc9`: Why Vance vs. Rubio 2028 Isn't Real! How AI Will Impact Midterms and Beyond (with Katie Harbath)

- Podcast: politics-politics-politics
- Duration: 72.0 min
- Truth ads: 1

| Model | F1 | F1 stdev |
|-------|----|----------|
| `moonshotai/kimi-k2.6` | 0.000 | 0.000 |
| `cohere/command-a` | 0.000 | 0.000 |
| `qwen/qwen3-235b-a22b-2507` | 0.000 | 0.000 |
| `openai/gpt-3.5-turbo` | 0.000 | 0.000 |
| `qwen/qwen3.5-27b` | 0.000 | 0.000 |
| `cohere/command-r-plus-08-2024` | 0.000 | 0.000 |
| `nvidia/llama-3.3-nemotron-super-49b-v1.5` | 0.000 | 0.000 |
| `qwen/qwen3.6-flash` | 0.000 | 0.000 |
| `mistralai/mistral-7b-instruct-v0.1` | 0.000 | 0.000 |
| `nvidia/nemotron-nano-9b-v2` | 0.000 | 0.000 |
| `openai/gpt-oss-120b` | 0.000 | 0.000 |
| `mistralai/mistral-large-2512` | 0.000 | 0.000 |
| `deepseek/deepseek-r1-distill-llama-70b` | 0.000 | 0.000 |
| `qwen/qwen3-8b` | 0.000 | 0.000 |
| `google/gemini-2.5-flash-lite` | 0.000 | 0.000 |
| `qwen/qwen3.6-plus` | 0.000 | 0.000 |
| `meta-llama/llama-3.1-8b-instruct` | 0.000 | 0.000 |
| `google/gemma-4-31b-it` | 0.000 | 0.000 |
| `openai/o3` | 0.000 | 0.000 |
| `google/gemini-2.5-flash` | 0.000 | 0.000 |
| `openai/gpt-5.4` | 0.000 | 0.000 |
| `deepseek/deepseek-v3.2` | 0.000 | 0.000 |
| `google/gemini-2.5-pro` | 0.000 | 0.000 |
| `deepseek/deepseek-r1-0528` | 0.000 | 0.000 |
| `openai/gpt-5.5` | 0.000 | 0.000 |
| `meta-llama/llama-4-scout` | 0.000 | 0.000 |
| `meta-llama/llama-3.3-70b-instruct` | 0.000 | 0.000 |
| `deepseek/deepseek-v4-pro` | 0.000 | 0.000 |
| `qwen/qwen3-14b` | 0.000 | 0.000 |
| `google/gemini-3.5-flash` | 0.000 | 0.000 |
| `deepseek/deepseek-r1` | 0.000 | 0.000 |
| `mistralai/codestral-2508` | 0.000 | 0.000 |
| `openai/gpt-5.4-mini` | 0.000 | 0.000 |
| `qwen/qwen3.5-plus-02-15` | 0.000 | 0.000 |
| `x-ai/grok-4.3` | 0.000 | 0.000 |
| `claude-opus-4-7` | 0.000 | 0.000 |
| `meta-llama/llama-4-maverick` | 0.000 | 0.000 |
| `google/gemini-3.1-flash-lite` | 0.000 | 0.000 |
| `deepseek/deepseek-v4-flash` | 0.000 | 0.000 |
| `claude-sonnet-4-6` | 0.000 | 0.000 |
| `claude-haiku-4-5-20251001` | 0.000 | 0.000 |
| `mistralai/mistral-medium-3.1` | 0.000 | 0.000 |
| `microsoft/phi-4` | 0.000 | 0.000 |
| `openai/o4-mini` | 0.000 | 0.000 |

#### `ep-security-now-audio-2850b24903b2`: SN 1077: A Browser AI API? - End of Bug Bounties?

- Podcast: security-now-audio
- Duration: 156.2 min
- Truth ads: 6

| Model | F1 | F1 stdev |
|-------|----|----------|
| `openai/o3` | 0.644 | 0.161 |
| `mistralai/mistral-medium-3.1` | 0.640 | 0.137 |
| `nvidia/llama-3.3-nemotron-super-49b-v1.5` | 0.587 | 0.064 |
| `cohere/command-r-plus-08-2024` | 0.569 | 0.053 |
| `claude-haiku-4-5-20251001` | 0.551 | 0.012 |
| `deepseek/deepseek-v3.2` | 0.528 | 0.073 |
| `claude-opus-4-7` | 0.520 | 0.055 |
| `meta-llama/llama-4-scout` | 0.520 | 0.050 |
| `claude-sonnet-4-6` | 0.516 | 0.014 |
| `qwen/qwen3.6-flash` | 0.514 | 0.052 |
| `meta-llama/llama-3.3-70b-instruct` | 0.512 | 0.035 |
| `openai/gpt-5.5` | 0.505 | 0.047 |
| `deepseek/deepseek-v4-pro` | 0.498 | 0.089 |
| `google/gemma-4-31b-it` | 0.496 | 0.021 |
| `meta-llama/llama-4-maverick` | 0.496 | 0.021 |
| `openai/gpt-5.4` | 0.495 | 0.022 |
| `openai/gpt-5.4-mini` | 0.491 | 0.027 |
| `x-ai/grok-4.3` | 0.486 | 0.013 |
| `qwen/qwen3.6-plus` | 0.481 | 0.011 |
| `google/gemini-3.5-flash` | 0.476 | 0.000 |
| `qwen/qwen3.5-plus-02-15` | 0.476 | 0.000 |
| `openai/gpt-oss-120b` | 0.469 | 0.033 |
| `deepseek/deepseek-v4-flash` | 0.465 | 0.046 |
| `deepseek/deepseek-r1` | 0.462 | 0.044 |
| `qwen/qwen3-235b-a22b-2507` | 0.460 | 0.031 |
| `google/gemini-2.5-flash` | 0.455 | 0.000 |
| `google/gemini-2.5-pro` | 0.451 | 0.009 |
| `qwen/qwen3.5-27b` | 0.414 | 0.081 |
| `google/gemini-3.1-flash-lite` | 0.413 | 0.007 |
| `mistralai/codestral-2508` | 0.374 | 0.032 |
| `cohere/command-a` | 0.368 | 0.015 |
| `qwen/qwen3-14b` | 0.336 | 0.241 |
| `deepseek/deepseek-r1-0528` | 0.281 | 0.103 |
| `google/gemini-2.5-flash-lite` | 0.280 | 0.051 |
| `deepseek/deepseek-r1-distill-llama-70b` | 0.262 | 0.018 |
| `nvidia/nemotron-nano-9b-v2` | 0.238 | 0.144 |
| `openai/gpt-3.5-turbo` | 0.217 | 0.004 |
| `mistralai/mistral-large-2512` | 0.209 | 0.017 |
| `moonshotai/kimi-k2.6` | 0.196 | 0.245 |
| `meta-llama/llama-3.1-8b-instruct` | 0.133 | 0.052 |
| `openai/o4-mini` | 0.114 | 0.156 |
| `microsoft/phi-4` | 0.033 | 0.045 |
| `mistralai/mistral-7b-instruct-v0.1` | 0.000 | 0.000 |
| `qwen/qwen3-8b` | 0.000 | 0.000 |

#### `ep-the-brilliant-idiots-0bb9bf634c8e`: Class Rank

- Podcast: the-brilliant-idiots
- Duration: 119.9 min
- Truth ads: 3

| Model | F1 | F1 stdev |
|-------|----|----------|
| `google/gemini-3.5-flash` | 0.857 | 0.000 |
| `claude-sonnet-4-6` | 0.836 | 0.048 |
| `qwen/qwen3.6-flash` | 0.800 | 0.128 |
| `qwen/qwen3.6-plus` | 0.798 | 0.087 |
| `openai/gpt-5.5` | 0.776 | 0.081 |
| `qwen/qwen3.5-plus-02-15` | 0.771 | 0.048 |
| `x-ai/grok-4.3` | 0.771 | 0.048 |
| `claude-opus-4-7` | 0.733 | 0.037 |
| `openai/o3` | 0.698 | 0.273 |
| `qwen/qwen3.5-27b` | 0.671 | 0.267 |
| `openai/gpt-oss-120b` | 0.630 | 0.067 |
| `claude-haiku-4-5-20251001` | 0.600 | 0.000 |
| `mistralai/mistral-medium-3.1` | 0.591 | 0.046 |
| `google/gemma-4-31b-it` | 0.585 | 0.077 |
| `deepseek/deepseek-v4-flash` | 0.569 | 0.045 |
| `moonshotai/kimi-k2.6` | 0.538 | 0.263 |
| `openai/gpt-5.4` | 0.516 | 0.110 |
| `google/gemini-2.5-pro` | 0.510 | 0.036 |
| `meta-llama/llama-3.3-70b-instruct` | 0.489 | 0.025 |
| `nvidia/llama-3.3-nemotron-super-49b-v1.5` | 0.478 | 0.160 |
| `google/gemini-2.5-flash-lite` | 0.432 | 0.046 |
| `deepseek/deepseek-r1` | 0.401 | 0.043 |
| `meta-llama/llama-4-maverick` | 0.390 | 0.101 |
| `qwen/qwen3-14b` | 0.372 | 0.099 |
| `openai/gpt-5.4-mini` | 0.353 | 0.145 |
| `meta-llama/llama-4-scout` | 0.349 | 0.080 |
| `qwen/qwen3-235b-a22b-2507` | 0.342 | 0.064 |
| `google/gemini-3.1-flash-lite` | 0.338 | 0.072 |
| `cohere/command-a` | 0.247 | 0.013 |
| `deepseek/deepseek-r1-distill-llama-70b` | 0.222 | 0.056 |
| `openai/gpt-3.5-turbo` | 0.220 | 0.005 |
| `nvidia/nemotron-nano-9b-v2` | 0.205 | 0.056 |
| `mistralai/mistral-large-2512` | 0.193 | 0.041 |
| `mistralai/codestral-2508` | 0.187 | 0.024 |
| `openai/o4-mini` | 0.180 | 0.249 |
| `deepseek/deepseek-v4-pro` | 0.167 | 0.236 |
| `deepseek/deepseek-r1-0528` | 0.161 | 0.084 |
| `google/gemini-2.5-flash` | 0.125 | 0.000 |
| `deepseek/deepseek-v3.2` | 0.100 | 0.224 |
| `microsoft/phi-4` | 0.067 | 0.092 |
| `cohere/command-r-plus-08-2024` | 0.000 | 0.000 |
| `mistralai/mistral-7b-instruct-v0.1` | 0.000 | 0.000 |
| `qwen/qwen3-8b` | 0.000 | 0.000 |
| `meta-llama/llama-3.1-8b-instruct` | 0.000 | 0.000 |

#### `ep-the-tim-dillon-show-f62bd5fa1cfe`: 495 - Hantavirus Cruise & iPad Babies

- Podcast: the-tim-dillon-show
- Duration: 80.1 min
- Truth ads: 6

| Model | F1 | F1 stdev |
|-------|----|----------|
| `qwen/qwen3.5-plus-02-15` | 0.636 | 0.028 |
| `qwen/qwen3.6-plus` | 0.615 | 0.000 |
| `google/gemini-3.5-flash` | 0.592 | 0.052 |
| `claude-opus-4-7` | 0.592 | 0.052 |
| `openai/gpt-5.4` | 0.586 | 0.077 |
| `google/gemini-2.5-pro` | 0.569 | 0.063 |
| `deepseek/deepseek-v4-pro` | 0.565 | 0.091 |
| `openai/gpt-5.5` | 0.546 | 0.063 |
| `qwen/qwen3.5-27b` | 0.537 | 0.134 |
| `qwen/qwen3.6-flash` | 0.507 | 0.183 |
| `qwen/qwen3-235b-a22b-2507` | 0.421 | 0.048 |
| `x-ai/grok-4.3` | 0.393 | 0.123 |
| `openai/gpt-oss-120b` | 0.387 | 0.066 |
| `openai/gpt-5.4-mini` | 0.373 | 0.103 |
| `openai/o3` | 0.372 | 0.081 |
| `google/gemma-4-31b-it` | 0.350 | 0.072 |
| `deepseek/deepseek-r1` | 0.327 | 0.195 |
| `meta-llama/llama-3.3-70b-instruct` | 0.308 | 0.000 |
| `deepseek/deepseek-v4-flash` | 0.298 | 0.084 |
| `deepseek/deepseek-r1-0528` | 0.283 | 0.115 |
| `qwen/qwen3-14b` | 0.233 | 0.158 |
| `mistralai/mistral-medium-3.1` | 0.223 | 0.054 |
| `cohere/command-a` | 0.200 | 0.078 |
| `moonshotai/kimi-k2.6` | 0.184 | 0.105 |
| `google/gemini-3.1-flash-lite` | 0.183 | 0.016 |
| `claude-sonnet-4-6` | 0.179 | 0.004 |
| `mistralai/codestral-2508` | 0.178 | 0.065 |
| `nvidia/llama-3.3-nemotron-super-49b-v1.5` | 0.171 | 0.125 |
| `meta-llama/llama-4-maverick` | 0.167 | 0.000 |
| `deepseek/deepseek-r1-distill-llama-70b` | 0.163 | 0.159 |
| `google/gemini-2.5-flash` | 0.154 | 0.000 |
| `claude-haiku-4-5-20251001` | 0.154 | 0.000 |
| `google/gemini-2.5-flash-lite` | 0.133 | 0.026 |
| `openai/gpt-3.5-turbo` | 0.125 | 0.000 |
| `microsoft/phi-4` | 0.079 | 0.072 |
| `deepseek/deepseek-v3.2` | 0.057 | 0.079 |
| `cohere/command-r-plus-08-2024` | 0.057 | 0.128 |
| `qwen/qwen3-8b` | 0.057 | 0.128 |
| `meta-llama/llama-4-scout` | 0.053 | 0.049 |
| `meta-llama/llama-3.1-8b-instruct` | 0.049 | 0.019 |
| `nvidia/nemotron-nano-9b-v2` | 0.044 | 0.061 |
| `mistralai/mistral-large-2512` | 0.044 | 0.025 |
| `mistralai/mistral-7b-instruct-v0.1` | 0.000 | 0.000 |
| `openai/o4-mini` | 0.000 | 0.000 |

#### `ep-tosh-show-5f6894439bb6`: My Mom - Emergency Pod

- Podcast: tosh-show
- Duration: 41.4 min
- Truth ads: 5

| Model | F1 | F1 stdev |
|-------|----|----------|
| `qwen/qwen3.6-plus` | 0.703 | 0.119 |
| `qwen/qwen3.5-plus-02-15` | 0.696 | 0.094 |
| `qwen/qwen3.5-27b` | 0.647 | 0.125 |
| `deepseek/deepseek-r1-0528` | 0.643 | 0.158 |
| `google/gemini-2.5-pro` | 0.603 | 0.116 |
| `openai/gpt-5.5` | 0.587 | 0.084 |
| `x-ai/grok-4.3` | 0.585 | 0.075 |
| `deepseek/deepseek-v4-flash` | 0.574 | 0.137 |
| `openai/gpt-5.4` | 0.569 | 0.070 |
| `mistralai/codestral-2508` | 0.564 | 0.149 |
| `qwen/qwen3.6-flash` | 0.564 | 0.017 |
| `openai/gpt-5.4-mini` | 0.547 | 0.064 |
| `cohere/command-a` | 0.533 | 0.122 |
| `claude-opus-4-7` | 0.524 | 0.135 |
| `google/gemini-3.5-flash` | 0.490 | 0.039 |
| `openai/o3` | 0.464 | 0.106 |
| `openai/gpt-oss-120b` | 0.443 | 0.185 |
| `deepseek/deepseek-r1` | 0.435 | 0.066 |
| `mistralai/mistral-medium-3.1` | 0.421 | 0.048 |
| `qwen/qwen3-14b` | 0.412 | 0.193 |
| `meta-llama/llama-4-maverick` | 0.400 | 0.000 |
| `nvidia/llama-3.3-nemotron-super-49b-v1.5` | 0.399 | 0.223 |
| `google/gemini-3.1-flash-lite` | 0.375 | 0.000 |
| `claude-sonnet-4-6` | 0.375 | 0.000 |
| `claude-haiku-4-5-20251001` | 0.375 | 0.000 |
| `google/gemini-2.5-flash-lite` | 0.364 | 0.052 |
| `qwen/qwen3-235b-a22b-2507` | 0.362 | 0.048 |
| `mistralai/mistral-large-2512` | 0.353 | 0.000 |
| `meta-llama/llama-3.3-70b-instruct` | 0.347 | 0.087 |
| `deepseek/deepseek-r1-distill-llama-70b` | 0.340 | 0.152 |
| `deepseek/deepseek-v4-pro` | 0.305 | 0.312 |
| `cohere/command-r-plus-08-2024` | 0.289 | 0.087 |
| `moonshotai/kimi-k2.6` | 0.267 | 0.149 |
| `meta-llama/llama-4-scout` | 0.258 | 0.066 |
| `nvidia/nemotron-nano-9b-v2` | 0.240 | 0.125 |
| `google/gemini-2.5-flash` | 0.200 | 0.000 |
| `openai/gpt-3.5-turbo` | 0.189 | 0.010 |
| `meta-llama/llama-3.1-8b-instruct` | 0.185 | 0.027 |
| `google/gemma-4-31b-it` | 0.182 | 0.025 |
| `deepseek/deepseek-v3.2` | 0.140 | 0.219 |
| `openai/o4-mini` | 0.133 | 0.183 |
| `mistralai/mistral-7b-instruct-v0.1` | 0.000 | 0.000 |
| `qwen/qwen3-8b` | 0.000 | 0.000 |
| `microsoft/phi-4` | 0.000 | 0.000 |


### Parser stress test

How each model's responses were actually parsed. Columns are extraction methods, ordered alphabetically; rows are models, sorted by parse-failure rate (cleanest at top). `json_array_direct` is the happy path: a bare JSON array we could `json.loads` and process immediately. `markdown_code_block` means we had to strip triple-backtick fences first; `json_object_*` means the model wrapped the array in an outer object and we had to find the array key; `regex_*` are last-resort recovery paths. A model that needs anything but `json_array_direct` for most calls is fragile. It works today, but a small prompt change can break the parser.

| Model | bracket_fallback | json_array_direct | json_object_ads_key | json_object_no_ads | json_object_segments_key | json_object_single_ad | json_object_single_ad_truncated | json_object_window_segments | markdown_code_block | parse_failure | regex_json_array |
|---|---|---|---|---|---|---|---|---|---|---|---|
| `cohere/command-a` | 0 | 0 | 0 | 29 | 0 | 881 | 0 | 0 | 0 | 0 | 0 |
| `qwen/qwen3-235b-a22b-2507` | 0 | 181 | 1 | 103 | 0 | 625 | 0 | 0 | 0 | 0 | 0 |
| `cohere/command-r-plus-08-2024` | 0 | 0 | 27 | 838 | 0 | 45 | 0 | 0 | 0 | 0 | 0 |
| `qwen/qwen3.6-flash` | 0 | 910 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| `mistralai/mistral-large-2512` | 0 | 910 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| `qwen/qwen3.6-plus` | 0 | 910 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| `google/gemini-2.5-flash` | 0 | 910 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| `openai/gpt-5.4` | 0 | 0 | 0 | 346 | 0 | 564 | 0 | 0 | 0 | 0 | 0 |
| `deepseek/deepseek-v3.2` | 0 | 511 | 19 | 3 | 0 | 377 | 0 | 0 | 0 | 0 | 0 |
| `google/gemini-3.5-flash` | 0 | 905 | 0 | 0 | 0 | 0 | 1 | 0 | 0 | 0 | 4 |
| `mistralai/codestral-2508` | 0 | 910 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| `openai/gpt-5.4-mini` | 0 | 0 | 2 | 325 | 2 | 581 | 0 | 0 | 0 | 0 | 0 |
| `claude-opus-4-7` | 0 | 905 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 5 |
| `meta-llama/llama-4-maverick` | 0 | 0 | 0 | 342 | 0 | 568 | 0 | 0 | 0 | 0 | 0 |
| `google/gemini-3.1-flash-lite` | 0 | 849 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 61 |
| `claude-sonnet-4-6` | 0 | 838 | 0 | 0 | 0 | 0 | 0 | 0 | 57 | 0 | 15 |
| `claude-haiku-4-5-20251001` | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 910 | 0 | 0 |
| `mistralai/mistral-medium-3.1` | 0 | 909 | 0 | 0 | 0 | 0 | 1 | 0 | 0 | 0 | 0 |
| `meta-llama/llama-3.1-8b-instruct` | 0 | 389 | 0 | 66 | 0 | 454 | 0 | 0 | 0 | 1 | 0 |
| `google/gemma-4-31b-it` | 1 | 0 | 486 | 237 | 0 | 183 | 2 | 0 | 0 | 1 | 0 |
| `openai/gpt-5.5` | 0 | 0 | 0 | 540 | 0 | 369 | 0 | 0 | 0 | 1 | 0 |
| `x-ai/grok-4.3` | 0 | 909 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 1 | 0 |
| `qwen/qwen3.5-plus-02-15` | 0 | 906 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 1 | 0 |
| `openai/gpt-3.5-turbo` | 0 | 0 | 0 | 50 | 0 | 855 | 0 | 0 | 0 | 5 | 0 |
| `openai/o3` | 0 | 0 | 36 | 674 | 12 | 183 | 0 | 0 | 0 | 5 | 0 |
| `deepseek/deepseek-r1` | 0 | 809 | 3 | 18 | 7 | 47 | 0 | 0 | 17 | 6 | 3 |
| `microsoft/phi-4` | 0 | 445 | 34 | 30 | 20 | 360 | 0 | 2 | 0 | 9 | 10 |
| `google/gemini-2.5-pro` | 0 | 870 | 0 | 0 | 0 | 0 | 2 | 0 | 0 | 13 | 25 |
| `meta-llama/llama-4-scout` | 42 | 5 | 678 | 92 | 0 | 74 | 0 | 0 | 0 | 13 | 6 |
| `deepseek/deepseek-r1-distill-llama-70b` | 0 | 20 | 68 | 115 | 0 | 684 | 5 | 0 | 0 | 17 | 1 |
| `deepseek/deepseek-v4-flash` | 0 | 109 | 453 | 33 | 9 | 288 | 0 | 0 | 0 | 17 | 1 |
| `google/gemini-2.5-flash-lite` | 0 | 845 | 0 | 0 | 0 | 0 | 46 | 0 | 0 | 19 | 0 |
| `deepseek/deepseek-v4-pro` | 0 | 243 | 63 | 133 | 336 | 102 | 2 | 0 | 5 | 24 | 2 |
| `nvidia/nemotron-nano-9b-v2` | 0 | 820 | 0 | 0 | 0 | 0 | 17 | 0 | 0 | 59 | 14 |
| `deepseek/deepseek-r1-0528` | 0 | 754 | 34 | 3 | 0 | 31 | 3 | 0 | 2 | 83 | 0 |
| `nvidia/llama-3.3-nemotron-super-49b-v1.5` | 0 | 479 | 0 | 0 | 0 | 0 | 3 | 0 | 271 | 127 | 30 |
| `qwen/qwen3.5-27b` | 0 | 673 | 0 | 94 | 0 | 12 | 0 | 0 | 0 | 131 | 0 |
| `openai/gpt-oss-120b` | 0 | 68 | 235 | 187 | 0 | 208 | 6 | 0 | 0 | 183 | 23 |
| `moonshotai/kimi-k2.6` | 0 | 104 | 35 | 111 | 2 | 402 | 0 | 0 | 5 | 251 | 0 |
| `meta-llama/llama-3.3-70b-instruct` | 0 | 155 | 1 | 163 | 0 | 267 | 0 | 0 | 0 | 321 | 3 |
| `mistralai/mistral-7b-instruct-v0.1` | 1 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 524 | 385 |
| `qwen/qwen3-14b` | 0 | 0 | 0 | 1 | 0 | 356 | 0 | 0 | 0 | 553 | 0 |
| `openai/o4-mini` | 0 | 0 | 0 | 19 | 0 | 33 | 0 | 0 | 0 | 858 | 0 |
| `qwen/qwen3-8b` | 20 | 4 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 886 | 0 |

## Methodology

Reproducibility settings used for this run. The benchmark sends the same prompts MinusPod sends in production (same system prompt, same sponsor list, same windowing) so the F1 numbers here are directly relevant to production accuracy decisions. Cost is recomputed at report time from token counts against the active pricing snapshot, so all rows compare at the same prices regardless of when the actual call ran.

- Trials per (model, episode): **5**, temperature 0.0
- max_tokens: 4096 (matches MinusPod production)
- response_format: json_object (with prompt-injection fallback when provider rejects native)
- Window size: 10 min, overlap: 3 min (imported from MinusPod's create_windows)
- Pricing snapshot: 2026-05-20T19:59:56.229629Z
- Corpus episodes: 15

## Transcript source

`segments.json` for every corpus episode is pulled byte-exact from the source MinusPod instance's `original-segments` endpoint. The transcript itself was generated by faster-whisper inside that instance, not by the benchmark. Model choice and decoding params affect what gets transcribed, which sets an upper bound on what every benchmarked LLM can find.

**Whisper config:**

| Setting | Value |
|---|---|
| Model | `large-v3` |
| Backend | local (faster-whisper, CUDA GPU) |
| Compute type | `auto` (resolves to `float16` on CUDA) |
| Language | `en` (forced English, not auto-detect) |
| VAD gap detection | on (start 3.0s / mid 8.0s / tail 3.0s) |

**`model.transcribe()` invocation** (from `src/transcriber.py`):

```python
WhisperModel(model_size="large-v3", device="cuda", compute_type="auto")
model.transcribe(
    audio,
    language="en",
    initial_prompt=<podcast name + SEED_SPONSORS vocabulary>,
    beam_size=5,
    batch_size=<adaptive: 16/12/8/4 by episode length>,
    word_timestamps=True,
    vad_filter=True,
    vad_parameters={"min_silence_duration_ms": 1000, "speech_pad_ms": 600, "threshold": 0.3},
)
```

The `initial_prompt` carries a sponsor vocabulary so Whisper produces consistent spellings (`Athletic Greens` rather than `AG1`, `ExpressVPN` rather than `express vpn`). This biases what shows up in the transcript and therefore what every benchmarked LLM is scored against.

**Sponsor vocabulary** (254 canonical sponsors, 43 of them with explicit alias spellings totaling 46 aliases; from `src/utils/constants.py` `SEED_SPONSORS`). Laid out in two side-by-side groups, read top-to-bottom in each group.

| Sponsor | Aliases | Category | Sponsor | Aliases | Category |
|---|---|---|---|---|---|
| 1Password | `One Password` | tech | Lyft | - | automotive_transport |
| Acorns | - | finance | MacPaw | `CleanMyMac` | tech |
| ADT | - | home | Magic Mind | - | beverage |
| Affirm | - | finance_fintech | Magic Spoon | - | food |
| Airbnb | - | travel_hospitality | Mailchimp | - | tech_software_saas |
| Airtable | - | tech_software_saas | Manscaped | - | personal |
| Alani Nu | - | food_beverage_nutrition | MasterClass | `Master Class` | education |
| Allbirds | - | ecommerce_retail_dtc | McDonald's | - | food_beverage_nutrition |
| Alo Yoga | - | ecommerce_retail_dtc | Mercury | - | finance_fintech |
| Amazon | - | retail | Meter | - | b2b_startup |
| Anthropic | - | tech_software_saas | Midjourney | - | tech_software_saas |
| Apple TV+ | - | media_streaming | Mint Mobile | `MintMobile` | telecom |
| Asana | - | tech_software_saas | Miro | - | tech |
| AT&T | - | telecom | Momentous | - | mental_health_wellness |
| Athletic Brewing | - | beverage | Monarch Money | - | finance |
| Athletic Greens | `AG1`, `AG One` | health | Monday.com | `Monday` | tech |
| Audible | - | entertainment | Native | - | personal |
| Aura | - | tech | NerdWallet | - | finance_fintech |
| Babbel | - | education | Netflix | - | media_streaming |
| BetMGM | `Bet MGM` | gambling | NetSuite | `Net Suite` | tech |
| BetterHelp | `Better Help` | health | Noom | - | mental_health_wellness |
| Betterment | - | finance | NordVPN | `Nord VPN` | vpn |
| Bill.com | - | finance_fintech | Notion | - | tech |
| Birchbox | - | ecommerce_retail_dtc | Nutrafol | - | health |
| Bitwarden | `Bit Warden` | tech | Okta | - | tech_software_saas |
| Blinkist | - | education | OLIPOP | - | food_beverage_nutrition |
| Bloom Nutrition | - | food_beverage_nutrition | OneSkin | `One Skin` | personal |
| Blue Apron | - | food | OpenAI | - | tech_software_saas |
| Bombas | - | apparel | Outdoor Voices | - | ecommerce_retail_dtc |
| Booking.com | - | travel_hospitality | OutSystems | - | tech |
| Bose | - | electronics | PagerDuty | - | b2b_startup |
| Brex | - | finance_fintech | Paramount+ | - | media_streaming |
| Brilliant | - | tech_software_saas | Patreon | - | tech_software_saas |
| Brooklinen | - | home | Perplexity | - | tech_software_saas |
| Butcher Box | `ButcherBox` | food | Plaid | - | finance_fintech |
| CacheFly | - | tech | PolicyGenius | `Policy Genius` | finance |
| Caesars Sportsbook | - | gaming_sports_betting | Poppi | - | food_beverage_nutrition |
| Calm | - | health | Poshmark | - | ecommerce_retail_dtc |
| Canva | - | tech | Progressive | - | finance |
| Capital One | - | finance | Public.com | - | finance_fintech |
| Care/of | `Care of`, `Careof` | health | Pura | - | home_security |
| CarMax | `Car Max` | auto | Purple | - | home |
| Carvana | - | auto | QuickBooks | - | finance_fintech |
| Casper | - | home | Quince | - | apparel |
| Cerebral | - | mental_health_wellness | Quip | - | personal |
| Chime | - | finance_fintech | Ramp | - | finance_fintech |
| ClickUp | - | tech_software_saas | Raycon | - | electronics |
| Cloudflare | - | tech_software_saas | Retool | - | tech_software_saas |
| Coinbase | - | finance_fintech | Ring | - | home |
| Comcast | - | telecom | Rippling | - | b2b_startup |
| Cozy Earth | - | home | Ritual | - | health |
| Credit Karma | - | finance | Ro | - | mental_health_wellness |
| CrowdStrike | - | tech_software_saas | Robinhood | - | finance_fintech |
| Cursor | - | tech_software_saas | Rocket Lawyer | - | insurance_legal |
| Databricks | - | tech_software_saas | Rocket Money | `RocketMoney`, `Truebill` | finance |
| Datadog | - | tech_software_saas | Roman | - | health |
| Deel | - | business | Rosetta Stone | - | education |
| DeleteMe | `Delete Me` | tech | Rothy's | - | ecommerce_retail_dtc |
| Disney+ | - | media_streaming | Saatva | - | ecommerce_retail_dtc |
| DocuSign | - | tech_software_saas | Salesforce | - | tech_software_saas |
| Dollar Shave Club | `DSC` | personal | SeatGeek | - | gaming_sports_betting |
| DoorDash | `Door Dash` | food | Seed | - | health |
| DraftKings | `Draft Kings` | gambling | SendGrid | - | tech_software_saas |
| Duolingo | - | tech_software_saas | ServiceNow | - | tech_software_saas |
| eBay Motors | - | auto | Shein | - | ecommerce_retail_dtc |
| Eight Sleep | - | mental_health_wellness | Shopify | - | tech |
| ElevenLabs | - | tech_software_saas | SimpliSafe | `Simpli Safe` | home |
| ESPN Bet | - | gaming_sports_betting | SiriusXM | - | media_streaming |
| Everlane | - | ecommerce_retail_dtc | Skillshare | - | tech_software_saas |
| EveryPlate | - | food_beverage_nutrition | SKIMS | - | ecommerce_retail_dtc |
| Expedia | - | travel_hospitality | Skyscanner | - | travel_hospitality |
| ExpressVPN | `Express VPN` | vpn | Slack | - | tech_software_saas |
| FabFitFun | - | ecommerce_retail_dtc | Snowflake | - | tech_software_saas |
| Factor | - | food | SoFi | - | finance |
| FanDuel | `Fan Duel` | gambling | Spaceship | - | tech |
| Figma | - | tech_software_saas | Splunk | - | b2b_startup |
| Ford | - | auto | Spotify | - | media_streaming |
| Framer | - | tech | Squarespace | `Square Space` | tech |
| FreshBooks | - | finance_fintech | Stamps.com | `Stamps` | business |
| Function Health | - | mental_health_wellness | Starbucks | - | food_beverage_nutrition |
| Function of Beauty | - | personal | State Farm | - | finance |
| Gametime | `Game Time` | entertainment | Stitch Fix | - | ecommerce_retail_dtc |
| Geico | - | finance | StockX | - | ecommerce_retail_dtc |
| GitHub | - | tech_software_saas | Stripe | - | finance_fintech |
| GitHub Copilot | - | tech_software_saas | StubHub | - | gaming_sports_betting |
| GOAT | - | ecommerce_retail_dtc | Substack | - | tech_software_saas |
| GoodRx | `Good Rx` | health | T-Mobile | `TMobile` | telecom |
| Gopuff | - | ecommerce_retail_dtc | Talkspace | - | mental_health_wellness |
| Grammarly | - | tech | Temu | - | ecommerce_retail_dtc |
| Green Chef | `GreenChef` | food | Ten Thousand | - | ecommerce_retail_dtc |
| Grubhub | `Grub Hub` | food | Thinkst Canary | - | tech |
| Gusto | - | b2b_startup | Thorne | - | mental_health_wellness |
| Harry's | `Harrys` | personal | ThreatLocker | - | tech |
| HBO Max | - | media_streaming | ThredUp | - | ecommerce_retail_dtc |
| Headspace | `Head Space` | health | Thrive Market | - | food |
| Helix Sleep | `Helix` | home | Toyota | - | auto |
| HelloFresh | `Hello Fresh` | food | Transparent Labs | - | food_beverage_nutrition |
| Hers | - | health | Turo | - | automotive_transport |
| Hims | - | health | Twilio | - | tech_software_saas |
| Honeylove | `Honey Love` | apparel | Uber | - | automotive_transport |
| Hopper | - | travel_hospitality | Uber Eats | `UberEats` | food |
| HubSpot | `Hub Spot` | tech | UnitedHealth Group | - | finance_fintech |
| Huel | - | food_beverage_nutrition | Vanta | - | tech |
| Hyundai | - | auto | Veeam | - | tech |
| iHeartRadio | - | media_streaming | Vercel | - | tech_software_saas |
| Imperfect Foods | - | food_beverage_nutrition | Verizon | - | telecom |
| Incogni | - | tech | Visible | - | telecom |
| Indeed | - | jobs | Vrbo | - | travel_hospitality |
| Inside Tracker | - | mental_health_wellness | Vuori | - | ecommerce_retail_dtc |
| Instacart | - | food | Warby Parker | - | ecommerce_retail_dtc |
| Intuit | - | finance_fintech | Wayfair | - | ecommerce_retail_dtc |
| Joovv | - | mental_health_wellness | Waymo | - | automotive_transport |
| Kayak | - | travel_hospitality | Wealthfront | - | finance |
| Keeps | - | health | WebBank | - | finance_fintech |
| Klarna | - | finance_fintech | Webflow | - | b2b_startup |
| Klaviyo | - | tech_software_saas | WhatsApp | - | tech |
| LegalZoom | - | insurance_legal | WHOOP | - | mental_health_wellness |
| Lemonade | - | finance | Workday | - | tech_software_saas |
| Levels | - | mental_health_wellness | Xero | - | finance_fintech |
| Liberty Mutual | - | finance | YouTube | - | media_streaming |
| Lime | - | automotive_transport | YouTube TV | - | media_streaming |
| Linear | - | tech_software_saas | Zapier | - | tech |
| LinkedIn | `LinkedIn Jobs` | jobs | Zendesk | - | tech_software_saas |
| Liquid IV | `Liquid I.V.` | health | ZipRecruiter | `Zip Recruiter` | jobs |
| LMNT | `Element` | health | ZocDoc | `Zoc Doc` | health |
| Loom | - | tech_software_saas | Zoom | - | tech_software_saas |
| Lululemon | - | ecommerce_retail_dtc | Zscaler | - | tech |

**Mishearing corrections** (174 entries, from `src/utils/constants.py` `SPONSOR_ALIASES`). Applied post-transcription to normalize Whisper output toward the canonical sponsor name. Distinct from the `aliases` column above, which lists intentional alternative spellings (e.g. `AG1` vs `Athletic Greens`); the entries below are mostly Whisper mishearings (e.g. `a firm` -> `Affirm`, `xerox` -> `Xero`). Laid out in three side-by-side groups, read top-to-bottom in each group.

| Heard as | Normalized to | Heard as | Normalized to | Heard as | Normalized to |
|---|---|---|---|---|---|
| `1 password` | 1Password | `good-rx` | GoodRx | `patron` | Patreon |
| `8 sleep` | Eight Sleep | `green chef` | Green Chef | `pay tree on` | Patreon |
| `8-sleep` | Eight Sleep | `green-chef` | Green Chef | `perplexity ai` | Perplexity |
| `a firm` | Affirm | `greenchef` | Green Chef | `perplexity-ai` | Perplexity |
| `a g one` | Athletic Greens | `grub hub` | Grubhub | `policy genius` | PolicyGenius |
| `ag 1` | Athletic Greens | `grub-hub` | Grubhub | `policy-genius` | PolicyGenius |
| `ag one` | Athletic Greens | `harrys` | Harry's | `pyura` | Pura |
| `ag1` | Athletic Greens | `head space` | Headspace | `ray con` | Raycon |
| `athlean x` | Athlean-X | `head-space` | Headspace | `ray-con` | Raycon |
| `athlean-x` | Athlean-X | `hello fresh` | HelloFresh | `re tool` | Retool |
| `athletic greens one` | Athletic Greens | `hello-fresh` | HelloFresh | `ro gain` | Rogaine |
| `athleticgreens` | Athletic Greens | `him's` | Hims | `ro-gaine` | Rogaine |
| `bet mgm` | BetMGM | `hims & hers` | Hims & Hers | `rocket money` | Rocket Money |
| `bet-mgm` | BetMGM | `hims and hers` | Hims & Hers | `rocket-money` | Rocket Money |
| `better help` | BetterHelp | `honey love` | Honeylove | `rocketlawyer` | Rocket Lawyer |
| `better-help` | BetterHelp | `honey-love` | Honeylove | `rocketmoney` | Rocket Money |
| `birch box` | Birchbox | `honeylove` | Honeylove | `rocketmortgage` | Rocket Mortgage |
| `birch-box` | Birchbox | `hub spot` | HubSpot | `seat geek` | SeatGeek |
| `bit warden` | Bitwarden | `hub-spot` | HubSpot | `seat-geek` | SeatGeek |
| `bit-warden` | Bitwarden | `hubs pot` | HubSpot | `shop a fly` | Shopify |
| `blueapron` | Blue Apron | `imperfect foods` | Imperfect Foods | `shop fly` | Shopify |
| `brecks` | Brex | `imperfectfoods` | Imperfect Foods | `shop ify` | Shopify |
| `butcher box` | Butcher Box | `insta cart` | Instacart | `simpli safe` | SimpliSafe |
| `butcher-box` | Butcher Box | `insta-cart` | Instacart | `simpli-safe` | SimpliSafe |
| `butcherbox` | Butcher Box | `l m n t` | LMNT | `simply safe` | SimpliSafe |
| `car max` | CarMax | `legal zoom` | LegalZoom | `sky scanner` | Skyscanner |
| `car-max` | CarMax | `legal-zoom` | LegalZoom | `sky-scanner` | Skyscanner |
| `cloud flare` | Cloudflare | `legalzoom` | LegalZoom | `so fi` | SoFi |
| `cloud-flare` | Cloudflare | `liquid i v` | Liquid IV | `so-fi` | SoFi |
| `co pilot` | GitHub Copilot | `liquid i.v.` | Liquid IV | `square space` | Squarespace |
| `co-pilot` | GitHub Copilot | `liquid iv` | Liquid IV | `square-space` | Squarespace |
| `copilot` | GitHub Copilot | `liquidiv` | Liquid IV | `stamp dot com` | Stamps.com |
| `creditkarma` | Credit Karma | `magic mind` | Magic Mind | `stitch fix` | Stitch Fix |
| `delete me` | DeleteMe | `magic spoon` | Magic Spoon | `stitch-fix` | Stitch Fix |
| `delete-me` | DeleteMe | `magicmind` | Magic Mind | `stitchfix` | Stitch Fix |
| `dollarshaveclub` | Dollar Shave Club | `magicspoon` | Magic Spoon | `stub hub` | StubHub |
| `door dash` | DoorDash | `master class` | MasterClass | `stub-hub` | StubHub |
| `door-dash` | DoorDash | `master-class` | MasterClass | `sub stack` | Substack |
| `draft kings` | DraftKings | `mercury bank` | Mercury | `sub-stack` | Substack |
| `draft-kings` | DraftKings | `mercury-bank` | Mercury | `thrive market` | Thrive Market |
| `eight-sleep` | Eight Sleep | `mint mobile` | Mint Mobile | `thrivemarket` | Thrive Market |
| `eightsleep` | Eight Sleep | `mint-mobile` | Mint Mobile | `transparent labs` | Transparent Labs |
| `element` | LMNT | `mintmobile` | Mint Mobile | `transparentlabs` | Transparent Labs |
| `every plate` | EveryPlate | `monarch money` | Monarch Money | `uber eats` | Uber Eats |
| `every-plate` | EveryPlate | `monarch-money` | Monarch Money | `uber-eats` | Uber Eats |
| `express vpn` | ExpressVPN | `monarchmoney` | Monarch Money | `ubereats` | Uber Eats |
| `express-vpn` | ExpressVPN | `my protein` | Myprotein | `ver cell` | Vercel |
| `fab fit fun` | FabFitFun | `my ro` | Miro | `ver sel` | Vercel |
| `fab-fit-fun` | FabFitFun | `myprotein` | Myprotein | `wealth front` | Wealthfront |
| `fan duel` | FanDuel | `net suite` | NetSuite | `wealth-front` | Wealthfront |
| `fan-duel` | FanDuel | `net-suite` | NetSuite | `woop` | Whoop |
| `game time` | Gametime | `nord vpn` | NordVPN | `xerox` | Xero |
| `game-time` | Gametime | `nord-vpn` | NordVPN | `zero` | Xero |
| `gametime` | Gametime | `one password` | 1Password | `zip recruiter` | ZipRecruiter |
| `github-copilot` | GitHub Copilot | `one skin` | OneSkin | `zip-recruiter` | ZipRecruiter |
| `go puff` | Gopuff | `one-password` | 1Password | `zoc doc` | ZocDoc |
| `go-puff` | Gopuff | `one-skin` | OneSkin | `zoc-doc` | ZocDoc |
| `good rx` | GoodRx | `p ninety x` | P90X | `zock doc` | ZocDoc |

## Run Metadata

- Report generated: 2026-05-22T15:37:34Z
- Unique work units (current state, last-write-wins after retries): 40040
- Raw rows in calls.jsonl: 40552 (512 superseded by later retries; kept for audit)
- Successful: 40037
- Failed: 3
- Lifetime actual spend (sum of at-runtime costs, includes superseded rows): $311.9972
- Active pricing snapshot: 2026-05-20T19:59:56.229629Z
