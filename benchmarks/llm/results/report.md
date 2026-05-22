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
| 1 | `qwen/qwen3.6-plus` | 0.693 | 0.040 | $1.1119 | 39.9s | 1.00 | native |
| 2 | `qwen/qwen3.5-plus-02-15` | 0.679 | 0.034 | $1.2346 | 48.2s | 1.00 | native |
| 3 | `qwen/qwen3.6-flash` | 0.660 | 0.087 | $0.5461 | 13.0s | 1.00 | native |
| 4 | `openai/gpt-5.5` | 0.647 | 0.061 | $6.6806 | 6.4s | 0.87 | native |
| 5 | `google/gemini-3.5-flash` | 0.625 | 0.022 | $3.5200 | 5.2s | 1.00 | native |
| 6 | `qwen/qwen3.5-27b` | 0.622 | 0.161 | $3.2163 | 68.7s | 0.85 | native |
| 7 | `openai/gpt-5.4` | 0.620 | 0.067 | $2.5345 | 1.8s | 0.81 | native |
| 8 | `google/gemini-2.5-pro` | 0.613 | 0.041 | $3.8631 | 14.2s | 0.97 | native |
| 9 | `openai/gpt-5.4-mini` | 0.584 | 0.112 | $0.7644 | 1.2s | 0.81 | native |
| 10 | `claude-opus-4-7` | 0.576 | 0.043 | $7.8054 | 2.2s | 1.00 | prompt-inject |
| 11 | `openai/o3` | 0.567 | 0.150 | $3.0485 | 8.1s | 0.92 | native |
| 12 | `x-ai/grok-4.3` | 0.506 | 0.105 | $1.4987 | 3.9s | 1.00 | native |
| 13 | `deepseek/deepseek-v4-flash` | 0.475 | 0.123 | $0.1346 | 3.7s | 0.81 | native |
| 14 | `google/gemma-4-31b-it` | 0.474 | 0.080 | $0.1291 | 2.2s | 0.85 | native |
| 15 | `deepseek/deepseek-r1` | 0.469 | 0.143 | $1.1157 | 19.9s | 0.97 | native |
| 16 | `claude-sonnet-4-6` | 0.446 | 0.042 | $3.5376 | 1.4s | 0.96 | prompt-inject |
| 17 | `qwen/qwen3-235b-a22b-2507` | 0.433 | 0.082 | $0.0735 | 2.3s | 0.79 | native |
| 18 | `openai/gpt-oss-120b` | 0.432 | 0.138 | $0.0643 | 3.0s | 0.70 | native |
| 19 | `deepseek/deepseek-r1-0528` | 0.420 | 0.142 | $1.0181 | 16.5s | 0.88 | native |
| 20 | `nvidia/llama-3.3-nemotron-super-49b-v1.5` | 0.411 | 0.135 | $0.2159 | 24.2s | 0.71 | native |
| 21 | `cohere/command-a` | 0.408 | 0.040 | $2.6688 | 3.8s | 0.71 | native |
| 22 | `google/gemini-3.1-flash-lite` | 0.402 | 0.039 | $0.2762 | 0.8s | 0.96 | native |
| 23 | `moonshotai/kimi-k2.6` | 0.400 | 0.164 | $2.3369 | 35.3s | 0.57 | native |
| 24 | `meta-llama/llama-4-maverick` | 0.398 | 0.016 | $0.1508 | 1.1s | 0.81 | native |
| 25 | `mistralai/mistral-medium-3.1` | 0.368 | 0.047 | $0.4380 | 0.9s | 1.00 | native |
| 26 | `claude-haiku-4-5-20251001` | 0.367 | 0.001 | $1.2017 | 1.2s | 0.60 | prompt-inject |
| 27 | `deepseek/deepseek-v4-pro` | 0.367 | 0.221 | $0.6376 | 26.4s | 0.87 | native |
| 28 | `mistralai/codestral-2508` | 0.314 | 0.049 | $0.3241 | 0.7s | 1.00 | native |
| 29 | `google/gemini-2.5-flash-lite` | 0.302 | 0.023 | $0.1104 | 0.9s | 0.97 | native |
| 30 | `deepseek/deepseek-v3.2` | 0.298 | 0.191 | $0.2564 | 2.7s | 0.88 | native |
| 31 | `google/gemini-2.5-flash` | 0.293 | 0.000 | $0.3435 | 1.0s | 1.00 | native |
| 32 | `openai/gpt-3.5-turbo` | 0.286 | 0.013 | $0.5091 | 1.3s | 0.71 | native |
| 33 | `meta-llama/llama-3.3-70b-instruct` | 0.274 | 0.081 | $0.1008 | 1.5s | 0.55 | native |
| 34 | `mistralai/mistral-large-2512` | 0.249 | 0.016 | $0.5599 | 2.6s | 1.00 | native |
| 35 | `qwen/qwen3-14b` | 0.248 | 0.166 | $0.1261 | 20.9s | 0.28 | native |
| 36 | `deepseek/deepseek-r1-distill-llama-70b` | 0.241 | 0.097 | $0.7331 | 2.0s | 0.74 | native |
| 37 | `meta-llama/llama-4-scout` | 0.235 | 0.109 | $0.0807 | 0.8s | 0.82 | native |
| 38 | `nvidia/nemotron-nano-9b-v2` | 0.221 | 0.114 | $0.0813 | 12.0s | 0.92 | native |
| 39 | `meta-llama/llama-3.1-8b-instruct` | 0.198 | 0.123 | $0.0207 | 0.8s | 0.85 | native |
| 40 | `cohere/command-r-plus-08-2024` | 0.102 | 0.035 | $2.5787 | 1.0s | 0.98 | native |
| 41 | `openai/o4-mini` | 0.075 | 0.125 | $1.8999 | 6.9s | 0.05 | native |
| 42 | `microsoft/phi-4` | 0.055 | 0.061 | $0.0713 | 2.3s | 0.86 | native |
| 43 | `qwen/qwen3-8b` | 0.005 | 0.011 | $0.2578 | 59.4s | 0.01 | native |
| 44 | `mistralai/mistral-7b-instruct-v0.1` | 0.000 | 0.000 | $0.0285 | 7.1s | 0.16 | native |

### Best Value (F1 per dollar)

Paid-tier only. Free-tier models are excluded here because F1 / 0 is undefined; they are ranked separately under Best Free-Tier below.

| Rank | Model | F1/$ | F1 | Cost / episode |
|------|-------|------|----|----------------|
| 1 | `meta-llama/llama-3.1-8b-instruct` | 9.54 | 0.198 | $0.0207 |
| 2 | `openai/gpt-oss-120b` | 6.72 | 0.432 | $0.0643 |
| 3 | `qwen/qwen3-235b-a22b-2507` | 5.90 | 0.433 | $0.0735 |
| 4 | `google/gemma-4-31b-it` | 3.67 | 0.474 | $0.1291 |
| 5 | `deepseek/deepseek-v4-flash` | 3.53 | 0.475 | $0.1346 |
| 6 | `meta-llama/llama-4-scout` | 2.91 | 0.235 | $0.0807 |
| 7 | `google/gemini-2.5-flash-lite` | 2.74 | 0.302 | $0.1104 |
| 8 | `nvidia/nemotron-nano-9b-v2` | 2.72 | 0.221 | $0.0813 |
| 9 | `meta-llama/llama-3.3-70b-instruct` | 2.71 | 0.274 | $0.1008 |
| 10 | `meta-llama/llama-4-maverick` | 2.64 | 0.398 | $0.1508 |
| 11 | `qwen/qwen3-14b` | 1.96 | 0.248 | $0.1261 |
| 12 | `nvidia/llama-3.3-nemotron-super-49b-v1.5` | 1.90 | 0.411 | $0.2159 |
| 13 | `google/gemini-3.1-flash-lite` | 1.46 | 0.402 | $0.2762 |
| 14 | `qwen/qwen3.6-flash` | 1.21 | 0.660 | $0.5461 |
| 15 | `deepseek/deepseek-v3.2` | 1.16 | 0.298 | $0.2564 |
| 16 | `mistralai/codestral-2508` | 0.97 | 0.314 | $0.3241 |
| 17 | `google/gemini-2.5-flash` | 0.85 | 0.293 | $0.3435 |
| 18 | `mistralai/mistral-medium-3.1` | 0.84 | 0.368 | $0.4380 |
| 19 | `microsoft/phi-4` | 0.77 | 0.055 | $0.0713 |
| 20 | `openai/gpt-5.4-mini` | 0.76 | 0.584 | $0.7644 |
| 21 | `qwen/qwen3.6-plus` | 0.62 | 0.693 | $1.1119 |
| 22 | `deepseek/deepseek-v4-pro` | 0.57 | 0.367 | $0.6376 |
| 23 | `openai/gpt-3.5-turbo` | 0.56 | 0.286 | $0.5091 |
| 24 | `qwen/qwen3.5-plus-02-15` | 0.55 | 0.679 | $1.2346 |
| 25 | `mistralai/mistral-large-2512` | 0.45 | 0.249 | $0.5599 |
| 26 | `deepseek/deepseek-r1` | 0.42 | 0.469 | $1.1157 |
| 27 | `deepseek/deepseek-r1-0528` | 0.41 | 0.420 | $1.0181 |
| 28 | `x-ai/grok-4.3` | 0.34 | 0.506 | $1.4987 |
| 29 | `deepseek/deepseek-r1-distill-llama-70b` | 0.33 | 0.241 | $0.7331 |
| 30 | `claude-haiku-4-5-20251001` | 0.31 | 0.367 | $1.2017 |
| 31 | `openai/gpt-5.4` | 0.24 | 0.620 | $2.5345 |
| 32 | `qwen/qwen3.5-27b` | 0.19 | 0.622 | $3.2163 |
| 33 | `openai/o3` | 0.19 | 0.567 | $3.0485 |
| 34 | `google/gemini-3.5-flash` | 0.18 | 0.625 | $3.5200 |
| 35 | `moonshotai/kimi-k2.6` | 0.17 | 0.400 | $2.3369 |
| 36 | `google/gemini-2.5-pro` | 0.16 | 0.613 | $3.8631 |
| 37 | `cohere/command-a` | 0.15 | 0.408 | $2.6688 |
| 38 | `claude-sonnet-4-6` | 0.13 | 0.446 | $3.5376 |
| 39 | `openai/gpt-5.5` | 0.10 | 0.647 | $6.6806 |
| 40 | `claude-opus-4-7` | 0.07 | 0.576 | $7.8054 |
| 41 | `cohere/command-r-plus-08-2024` | 0.04 | 0.102 | $2.5787 |
| 42 | `openai/o4-mini` | 0.04 | 0.075 | $1.8999 |
| 43 | `qwen/qwen3-8b` | 0.02 | 0.005 | $0.2578 |
| 44 | `mistralai/mistral-7b-instruct-v0.1` | 0.00 | 0.000 | $0.0285 |

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

**3 call(s) failed out of 37620 total (0.01%).** Failures are excluded from F1 / cost calculations, but they often surface real production-relevant gotchas worth knowing.

### By category

Errors classified into coarse buckets so failure patterns are visible at a glance. A model showing up here doesn't mean it's broken. Some categories are provider-side (content moderation, rate limits) and tell you more about routing reliability than model quality.

| Category | Calls | Affected models |
|----------|------:|-----------------|
| Provider content moderation rejection | 3 | `qwen/qwen3.5-plus-02-15` |

### Per-model error count

Same errors grouped by model, with the failure rate as a fraction of that model's total calls. Rates under 1% are usually one-off provider hiccups; rates above 5% suggest the model isn't operationally viable for production with the current prompts and concurrency caps.

| Model | Errors | of total |
|---|---:|---:|
| `qwen/qwen3.5-plus-02-15` | 3 | 3/855 (0.4%) |

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
| `qwen/qwen3.6-plus` | 0.640 | 0.823 | 205 | 164 | 50 |
| `qwen/qwen3.5-plus-02-15` | 0.621 | 0.823 | 183 | 151 | 45 |
| `qwen/qwen3.6-flash` | 0.569 | 0.836 | 205 | 209 | 50 |
| `openai/gpt-5.5` | 0.600 | 0.760 | 185 | 144 | 70 |
| `google/gemini-3.5-flash` | 0.557 | 0.775 | 186 | 193 | 69 |
| `qwen/qwen3.5-27b` | 0.629 | 0.680 | 162 | 145 | 93 |
| `openai/gpt-5.4` | 0.553 | 0.781 | 190 | 195 | 65 |
| `google/gemini-2.5-pro` | 0.523 | 0.822 | 205 | 229 | 50 |
| `openai/gpt-5.4-mini` | 0.523 | 0.756 | 180 | 236 | 75 |
| `claude-opus-4-7` | 0.526 | 0.705 | 161 | 180 | 94 |
| `openai/o3` | 0.715 | 0.516 | 137 | 45 | 118 |
| `x-ai/grok-4.3` | 0.410 | 0.744 | 178 | 417 | 77 |
| `deepseek/deepseek-v4-flash` | 0.376 | 0.749 | 177 | 419 | 78 |
| `google/gemma-4-31b-it` | 0.382 | 0.690 | 158 | 407 | 97 |
| `deepseek/deepseek-r1` | 0.378 | 0.697 | 167 | 407 | 88 |
| `claude-sonnet-4-6` | 0.380 | 0.633 | 161 | 435 | 94 |
| `qwen/qwen3-235b-a22b-2507` | 0.351 | 0.632 | 146 | 388 | 109 |
| `openai/gpt-oss-120b` | 0.364 | 0.638 | 147 | 433 | 108 |
| `deepseek/deepseek-r1-0528` | 0.370 | 0.678 | 157 | 665 | 98 |
| `nvidia/llama-3.3-nemotron-super-49b-v1.5` | 0.411 | 0.488 | 110 | 260 | 145 |
| `cohere/command-a` | 0.339 | 0.631 | 134 | 376 | 121 |
| `google/gemini-3.1-flash-lite` | 0.293 | 0.709 | 152 | 658 | 103 |
| `moonshotai/kimi-k2.6` | 0.468 | 0.433 | 91 | 122 | 164 |
| `meta-llama/llama-4-maverick` | 0.346 | 0.540 | 137 | 273 | 118 |
| `mistralai/mistral-medium-3.1` | 0.327 | 0.518 | 123 | 523 | 132 |
| `claude-haiku-4-5-20251001` | 0.265 | 0.625 | 145 | 656 | 110 |
| `deepseek/deepseek-v4-pro` | 0.461 | 0.363 | 98 | 115 | 157 |
| `mistralai/codestral-2508` | 0.278 | 0.474 | 116 | 546 | 139 |
| `google/gemini-2.5-flash-lite` | 0.208 | 0.607 | 141 | 771 | 114 |
| `deepseek/deepseek-v3.2` | 0.307 | 0.332 | 83 | 259 | 172 |
| `google/gemini-2.5-flash` | 0.207 | 0.538 | 125 | 800 | 130 |
| `openai/gpt-3.5-turbo` | 0.237 | 0.458 | 103 | 518 | 152 |
| `meta-llama/llama-3.3-70b-instruct` | 0.273 | 0.329 | 87 | 240 | 168 |
| `mistralai/mistral-large-2512` | 0.168 | 0.566 | 128 | 1073 | 127 |
| `qwen/qwen3-14b` | 0.312 | 0.238 | 60 | 132 | 195 |
| `deepseek/deepseek-r1-distill-llama-70b` | 0.216 | 0.361 | 83 | 449 | 172 |
| `meta-llama/llama-4-scout` | 0.212 | 0.329 | 81 | 459 | 174 |
| `nvidia/nemotron-nano-9b-v2` | 0.208 | 0.282 | 65 | 490 | 190 |
| `meta-llama/llama-3.1-8b-instruct` | 0.209 | 0.252 | 55 | 960 | 200 |
| `cohere/command-r-plus-08-2024` | 0.128 | 0.102 | 34 | 94 | 221 |
| `openai/o4-mini` | 0.183 | 0.049 | 12 | 21 | 243 |
| `microsoft/phi-4` | 0.051 | 0.076 | 21 | 484 | 234 |
| `qwen/qwen3-8b` | 0.017 | 0.003 | 1 | 5 | 254 |
| `mistralai/mistral-7b-instruct-v0.1` | 0.000 | 0.000 | 0 | 0 | 255 |

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
| `claude-opus-4-7` | 9.86 | 5.63 |
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
| `claude-haiku-4-5-20251001` | -- | 0.00 (n=64) | 0.12 (n=264) | 0.24 (n=473) | -- | 801 |
| `claude-opus-4-7` | 0.00 (n=2) | 0.00 (n=7) | 0.26 (n=19) | 0.49 (n=290) | 0.65 (n=23) | 341 |
| `claude-sonnet-4-6` | -- | 0.04 (n=47) | 0.23 (n=84) | 0.27 (n=422) | 0.56 (n=43) | 596 |
| `cohere/command-a` | 0.00 (n=2) | 0.00 (n=38) | -- | 0.28 (n=481) | 0.00 (n=1) | 522 |
| `cohere/command-r-plus-08-2024` | -- | -- | 0.00 (n=1) | 0.07 (n=54) | 0.41 (n=73) | 128 |
| `deepseek/deepseek-r1` | -- | 0.00 (n=4) | 0.17 (n=12) | 0.28 (n=435) | 0.34 (n=129) | 580 |
| `deepseek/deepseek-r1-0528` | 0.00 (n=2) | 0.00 (n=19) | 0.02 (n=59) | 0.10 (n=587) | 0.47 (n=204) | 871 |
| `deepseek/deepseek-r1-distill-llama-70b` | -- | 0.02 (n=190) | -- | 0.20 (n=396) | -- | 586 |
| `deepseek/deepseek-v3.2` | 0.00 (n=1) | 0.00 (n=5) | 0.00 (n=10) | 0.10 (n=188) | 0.44 (n=144) | 348 |
| `deepseek/deepseek-v4-flash` | 0.00 (n=7) | 0.09 (n=11) | 0.08 (n=12) | 0.27 (n=370) | 0.38 (n=197) | 597 |
| `deepseek/deepseek-v4-pro` | 0.00 (n=1) | 0.00 (n=11) | 0.00 (n=4) | 0.49 (n=156) | 0.54 (n=41) | 213 |
| `google/gemini-2.5-flash` | -- | -- | 0.00 (n=60) | 0.15 (n=698) | 0.12 (n=167) | 925 |
| `google/gemini-2.5-flash-lite` | -- | 0.50 (n=2) | -- | 0.15 (n=905) | 0.50 (n=10) | 917 |
| `google/gemini-2.5-pro` | -- | 0.00 (n=16) | 0.00 (n=31) | 0.34 (n=119) | 0.60 (n=274) | 440 |
| `google/gemini-3.1-flash-lite` | -- | 0.00 (n=3) | 0.00 (n=33) | 0.10 (n=177) | 0.22 (n=602) | 815 |
| `google/gemini-3.5-flash` | -- | 0.00 (n=3) | 0.12 (n=25) | 0.10 (n=48) | 0.59 (n=303) | 379 |
| `google/gemma-4-31b-it` | -- | 0.10 (n=10) | 0.09 (n=33) | 0.18 (n=234) | 0.38 (n=293) | 570 |
| `meta-llama/llama-3.1-8b-instruct` | -- | 0.00 (n=5) | 0.00 (n=2) | 0.05 (n=1008) | -- | 1015 |
| `meta-llama/llama-3.3-70b-instruct` | -- | 0.00 (n=22) | 0.23 (n=31) | 0.12 (n=137) | 0.46 (n=137) | 327 |
| `meta-llama/llama-4-maverick` | 0.00 (n=1) | 0.00 (n=60) | 0.10 (n=52) | 0.44 (n=300) | 0.00 (n=2) | 415 |
| `meta-llama/llama-4-scout` | -- | 0.00 (n=6) | 0.05 (n=20) | 0.15 (n=489) | 0.32 (n=25) | 540 |
| `microsoft/phi-4` | -- | 0.00 (n=24) | 0.00 (n=18) | 0.04 (n=521) | -- | 563 |
| `mistralai/codestral-2508` | -- | 0.00 (n=1) | 0.00 (n=4) | 0.18 (n=656) | 0.00 (n=1) | 662 |
| `mistralai/mistral-large-2512` | 0.00 (n=2) | 0.00 (n=33) | 0.00 (n=61) | 0.05 (n=542) | 0.18 (n=563) | 1201 |
| `mistralai/mistral-medium-3.1` | -- | 0.00 (n=6) | 0.00 (n=57) | 0.20 (n=560) | 0.43 (n=23) | 646 |
| `moonshotai/kimi-k2.6` | 0.00 (n=35) | 0.04 (n=28) | 0.00 (n=2) | 0.49 (n=93) | 0.65 (n=68) | 226 |
| `nvidia/llama-3.3-nemotron-super-49b-v1.5` | -- | 0.00 (n=6) | 0.06 (n=32) | 0.32 (n=330) | 0.50 (n=2) | 370 |
| `nvidia/nemotron-nano-9b-v2` | -- | 0.00 (n=6) | 0.00 (n=28) | 0.12 (n=503) | 0.32 (n=19) | 556 |
| `openai/gpt-3.5-turbo` | -- | -- | 0.00 (n=5) | 0.19 (n=441) | 0.08 (n=231) | 677 |
| `openai/gpt-5.4` | 0.00 (n=19) | 0.08 (n=37) | 0.00 (n=25) | 0.39 (n=79) | 0.67 (n=233) | 393 |
| `openai/gpt-5.4-mini` | 0.00 (n=15) | 0.02 (n=49) | 0.06 (n=35) | 0.41 (n=95) | 0.61 (n=226) | 420 |
| `openai/gpt-5.5` | 0.00 (n=12) | 0.17 (n=18) | 0.38 (n=8) | 0.52 (n=73) | 0.64 (n=219) | 330 |
| `openai/gpt-oss-120b` | -- | 0.00 (n=6) | 0.11 (n=19) | 0.15 (n=267) | 0.36 (n=293) | 585 |
| `openai/o3` | 0.00 (n=1) | -- | 0.31 (n=16) | 0.79 (n=156) | 1.00 (n=9) | 182 |
| `openai/o4-mini` | -- | 0.00 (n=2) | 0.00 (n=1) | 0.41 (n=29) | 0.00 (n=1) | 33 |
| `qwen/qwen3-14b` | -- | -- | 0.00 (n=31) | 0.37 (n=163) | -- | 194 |
| `qwen/qwen3-235b-a22b-2507` | 0.00 (n=12) | 0.00 (n=5) | 0.00 (n=8) | 0.28 (n=471) | 0.22 (n=64) | 560 |
| `qwen/qwen3-8b` | -- | -- | -- | 0.17 (n=6) | -- | 6 |
| `qwen/qwen3.5-27b` | -- | 0.00 (n=9) | 0.12 (n=8) | 0.56 (n=288) | 0.00 (n=2) | 307 |
| `qwen/qwen3.5-plus-02-15` | -- | 0.00 (n=13) | 0.11 (n=9) | 0.58 (n=295) | 0.71 (n=17) | 334 |
| `qwen/qwen3.6-flash` | 0.00 (n=1) | 0.00 (n=6) | 0.00 (n=9) | 0.51 (n=394) | 0.83 (n=6) | 416 |
| `qwen/qwen3.6-plus` | 0.00 (n=1) | 0.00 (n=9) | 0.00 (n=4) | 0.59 (n=347) | 0.25 (n=8) | 369 |
| `x-ai/grok-4.3` | 0.00 (n=1) | 0.07 (n=15) | 0.10 (n=51) | 0.32 (n=497) | 0.35 (n=31) | 595 |

See `report_assets/calibration.svg` for the visual reliability diagram.

## Latency tail

Median latency hides outliers. p99 and max are what determines queue depth and worst-case user wait. For OpenRouter-routed models the tail also reflects upstream provider load, not just model compute.

| Model | p50 | p90 | p95 | p99 | max |
|---|---:|---:|---:|---:|---:|
| `mistralai/codestral-2508` | 0.73s | 1.72s | 2.24s | 4.67s | 6.36s |
| `google/gemini-3.1-flash-lite` | 0.79s | 1.27s | 1.44s | 1.96s | 17.47s |
| `meta-llama/llama-3.1-8b-instruct` | 0.79s | 2.39s | 4.04s | 7.07s | 76.80s |
| `meta-llama/llama-4-scout` | 0.84s | 3.23s | 4.44s | 6.25s | 16.81s |
| `mistralai/mistral-medium-3.1` | 0.91s | 4.84s | 6.18s | 8.28s | 11.71s |
| `google/gemini-2.5-flash-lite` | 0.92s | 1.92s | 3.23s | 6.53s | 17.01s |
| `cohere/command-r-plus-08-2024` | 0.95s | 2.23s | 3.45s | 14.64s | 62.06s |
| `google/gemini-2.5-flash` | 0.99s | 2.45s | 3.59s | 10.06s | 16.44s |
| `meta-llama/llama-4-maverick` | 1.06s | 2.06s | 2.38s | 3.94s | 50.80s |
| `claude-haiku-4-5-20251001` | 1.17s | 3.18s | 4.06s | 181.18s | 186.76s |
| `openai/gpt-5.4-mini` | 1.18s | 1.66s | 2.24s | 3.13s | 4.94s |
| `openai/gpt-3.5-turbo` | 1.26s | 1.77s | 1.95s | 2.58s | 8.25s |
| `claude-sonnet-4-6` | 1.44s | 4.71s | 6.04s | 8.75s | 185.04s |
| `meta-llama/llama-3.3-70b-instruct` | 1.47s | 3.22s | 4.82s | 14.03s | 45.01s |
| `openai/gpt-5.4` | 1.82s | 2.58s | 3.12s | 4.81s | 17.20s |
| `deepseek/deepseek-r1-distill-llama-70b` | 1.98s | 15.01s | 51.21s | 94.11s | 136.13s |
| `claude-opus-4-7` | 2.17s | 3.56s | 4.21s | 6.25s | 181.97s |
| `google/gemma-4-31b-it` | 2.21s | 14.13s | 19.39s | 66.10s | 377.81s |
| `qwen/qwen3-235b-a22b-2507` | 2.28s | 6.26s | 7.75s | 11.89s | 24.93s |
| `microsoft/phi-4` | 2.29s | 6.71s | 11.55s | 203.39s | 229.43s |
| `mistralai/mistral-large-2512` | 2.55s | 5.40s | 6.20s | 8.68s | 18.09s |
| `deepseek/deepseek-v3.2` | 2.67s | 5.26s | 7.32s | 12.54s | 63.86s |
| `openai/gpt-oss-120b` | 2.96s | 20.95s | 34.40s | 384.58s | 2880.77s |
| `deepseek/deepseek-v4-flash` | 3.67s | 19.68s | 28.76s | 47.61s | 80.55s |
| `cohere/command-a` | 3.78s | 8.49s | 11.46s | 30.31s | 65.77s |
| `x-ai/grok-4.3` | 3.88s | 9.63s | 12.56s | 18.90s | 33.29s |
| `google/gemini-3.5-flash` | 5.23s | 9.10s | 11.13s | 15.80s | 32.29s |
| `openai/gpt-5.5` | 6.38s | 18.56s | 24.05s | 37.19s | 76.20s |
| `openai/o4-mini` | 6.95s | 20.52s | 25.84s | 71.70s | 138.80s |
| `mistralai/mistral-7b-instruct-v0.1` | 7.11s | 23.05s | 33.05s | 79.21s | 89.36s |
| `openai/o3` | 8.08s | 19.36s | 27.07s | 57.15s | 76.89s |
| `nvidia/nemotron-nano-9b-v2` | 12.05s | 31.04s | 36.70s | 50.76s | 65.31s |
| `qwen/qwen3.6-flash` | 13.01s | 33.57s | 39.29s | 47.21s | 371.73s |
| `google/gemini-2.5-pro` | 14.17s | 24.82s | 27.87s | 80.22s | 250.33s |
| `deepseek/deepseek-r1-0528` | 16.52s | 81.21s | 94.87s | 136.08s | 285.69s |
| `deepseek/deepseek-r1` | 19.86s | 90.13s | 153.13s | 264.17s | 364.40s |
| `qwen/qwen3-14b` | 20.91s | 43.46s | 63.42s | 225.55s | 439.49s |
| `nvidia/llama-3.3-nemotron-super-49b-v1.5` | 24.21s | 82.63s | 86.23s | 99.39s | 106.19s |
| `deepseek/deepseek-v4-pro` | 26.42s | 85.12s | 101.25s | 136.77s | 190.30s |
| `moonshotai/kimi-k2.6` | 35.30s | 116.25s | 160.11s | 229.07s | 411.39s |
| `qwen/qwen3.6-plus` | 39.88s | 69.37s | 74.53s | 90.19s | 100.27s |
| `qwen/qwen3.5-plus-02-15` | 48.19s | 124.89s | 143.16s | 177.42s | 1486.87s |
| `qwen/qwen3-8b` | 59.45s | 131.55s | 144.22s | 187.38s | 233.05s |
| `qwen/qwen3.5-27b` | 68.74s | 273.44s | 1145.40s | 1651.28s | 2172.22s |

## Output token efficiency

How many output tokens the model spent per detected ad. Lower is more concise (the model finds an ad and returns the JSON). Higher means the model is producing a lot of text the parser will discard, which costs you whether or not the answer is right.

| Model | Total output tokens | Ads detected | Tokens / ad | Cost / TP |
|---|---:|---:|---:|---:|
| `mistralai/mistral-medium-3.1` | 40,161 | 646 | 62 | $0.0036 |
| `mistralai/codestral-2508` | 42,107 | 662 | 64 | $0.0028 |
| `google/gemini-2.5-flash` | 67,460 | 925 | 73 | $0.0027 |
| `google/gemini-3.1-flash-lite` | 60,147 | 815 | 74 | $0.0018 |
| `openai/gpt-3.5-turbo` | 50,066 | 677 | 74 | $0.0049 |
| `meta-llama/llama-3.3-70b-instruct` | 25,864 | 327 | 79 | $0.0012 |
| `cohere/command-r-plus-08-2024` | 10,247 | 128 | 80 | $0.0758 |
| `claude-sonnet-4-6` | 52,052 | 596 | 87 | $0.0220 |
| `meta-llama/llama-3.1-8b-instruct` | 90,169 | 1015 | 89 | $0.0004 |
| `meta-llama/llama-4-scout` | 48,083 | 540 | 89 | $0.0010 |
| `mistralai/mistral-large-2512` | 108,068 | 1201 | 90 | $0.0044 |
| `meta-llama/llama-4-maverick` | 38,620 | 415 | 93 | $0.0011 |
| `claude-haiku-4-5-20251001` | 74,744 | 801 | 93 | $0.0083 |
| `deepseek/deepseek-v3.2` | 33,405 | 348 | 96 | $0.0031 |
| `google/gemini-2.5-flash-lite` | 89,161 | 917 | 97 | $0.0008 |
| `google/gemma-4-31b-it` | 55,826 | 570 | 98 | $0.0008 |
| `qwen/qwen3-235b-a22b-2507` | 59,253 | 560 | 106 | $0.0005 |
| `cohere/command-a` | 55,492 | 522 | 106 | $0.0199 |
| `openai/gpt-5.4` | 41,810 | 393 | 106 | $0.0133 |
| `claude-opus-4-7` | 36,811 | 341 | 108 | $0.0485 |
| `openai/gpt-5.4-mini` | 46,289 | 420 | 110 | $0.0042 |
| `microsoft/phi-4` | 211,787 | 563 | 376 | $0.0034 |
| `deepseek/deepseek-r1-distill-llama-70b` | 264,150 | 586 | 451 | $0.0088 |
| `deepseek/deepseek-v4-flash` | 492,887 | 597 | 826 | $0.0008 |
| `x-ai/grok-4.3` | 547,955 | 595 | 921 | $0.0084 |
| `openai/gpt-5.5` | 310,390 | 330 | 941 | $0.0361 |
| `openai/gpt-oss-120b` | 736,151 | 585 | 1258 | $0.0004 |
| `deepseek/deepseek-r1-0528` | 1,197,928 | 871 | 1375 | $0.0065 |
| `deepseek/deepseek-r1` | 817,850 | 580 | 1410 | $0.0067 |
| `nvidia/nemotron-nano-9b-v2` | 1,220,752 | 556 | 2196 | $0.0013 |
| `qwen/qwen3-14b` | 504,429 | 194 | 2600 | $0.0021 |
| `google/gemini-3.5-flash` | 1,095,187 | 379 | 2890 | $0.0189 |
| `google/gemini-2.5-pro` | 1,295,072 | 440 | 2943 | $0.0188 |
| `qwen/qwen3.6-flash` | 1,566,281 | 416 | 3765 | $0.0027 |
| `openai/o3` | 700,781 | 182 | 3850 | $0.0223 |
| `nvidia/llama-3.3-nemotron-super-49b-v1.5` | 1,461,554 | 370 | 3950 | $0.0020 |
| `deepseek/deepseek-v4-pro` | 1,148,041 | 213 | 5390 | $0.0065 |
| `qwen/qwen3.6-plus` | 1,990,086 | 369 | 5393 | $0.0054 |
| `qwen/qwen3.5-plus-02-15` | 3,059,957 | 373 | 8204 | $0.0067 |
| `moonshotai/kimi-k2.6` | 2,323,592 | 226 | 10281 | $0.0257 |
| `openai/o4-mini` | 954,370 | 33 | 28920 | $0.1583 |
| `qwen/qwen3.5-27b` | 9,663,262 | 307 | 31476 | $0.0199 |
| `qwen/qwen3-8b` | 2,583,129 | 6 | 430522 | $0.2578 |

## Trial variance (determinism check)

All trials run at temperature 0.0. If a model produces stable output you'd expect the F1 stdev across trials to be near zero. Higher numbers mean the model is non-deterministic even at temp=0. That's fine to know, but means you cannot trust a single trial's number for that model.

| Model | Mean F1 stdev across episodes | Highest single-episode stdev |
|---|---:|---:|
| `qwen/qwen3.6-plus` | 0.0399 | 0.1194 |
| `qwen/qwen3.5-plus-02-15` | 0.0340 | 0.0938 |
| `qwen/qwen3.6-flash` | 0.0869 | 0.2449 |
| `openai/gpt-5.5` | 0.0608 | 0.1432 |
| `google/gemini-3.5-flash` | 0.0216 | 0.0782 |
| `qwen/qwen3.5-27b` | 0.1606 | 0.3651 |
| `openai/gpt-5.4` | 0.0666 | 0.1193 |
| `google/gemini-2.5-pro` | 0.0405 | 0.1157 |
| `openai/gpt-5.4-mini` | 0.1123 | 0.2887 |
| `claude-opus-4-7` | 0.0431 | 0.1351 |
| `openai/o3` | 0.1498 | 0.4714 |
| `x-ai/grok-4.3` | 0.1048 | 0.2528 |
| `deepseek/deepseek-v4-flash` | 0.1229 | 0.3347 |
| `google/gemma-4-31b-it` | 0.0795 | 0.2739 |
| `deepseek/deepseek-r1` | 0.1426 | 0.3015 |
| `claude-sonnet-4-6` | 0.0416 | 0.1826 |
| `qwen/qwen3-235b-a22b-2507` | 0.0821 | 0.2653 |
| `openai/gpt-oss-120b` | 0.1380 | 0.3651 |
| `deepseek/deepseek-r1-0528` | 0.1424 | 0.2807 |
| `nvidia/llama-3.3-nemotron-super-49b-v1.5` | 0.1348 | 0.3249 |
| `cohere/command-a` | 0.0402 | 0.1217 |
| `google/gemini-3.1-flash-lite` | 0.0389 | 0.2528 |
| `moonshotai/kimi-k2.6` | 0.1644 | 0.2739 |
| `meta-llama/llama-4-maverick` | 0.0162 | 0.1012 |
| `mistralai/mistral-medium-3.1` | 0.0467 | 0.1373 |
| `claude-haiku-4-5-20251001` | 0.0011 | 0.0116 |
| `deepseek/deepseek-v4-pro` | 0.2209 | 0.4333 |
| `mistralai/codestral-2508` | 0.0493 | 0.1493 |
| `google/gemini-2.5-flash-lite` | 0.0231 | 0.0523 |
| `deepseek/deepseek-v3.2` | 0.1908 | 0.5477 |
| `google/gemini-2.5-flash` | 0.0000 | 0.0000 |
| `openai/gpt-3.5-turbo` | 0.0129 | 0.0497 |
| `meta-llama/llama-3.3-70b-instruct` | 0.0814 | 0.2981 |
| `mistralai/mistral-large-2512` | 0.0162 | 0.0426 |
| `qwen/qwen3-14b` | 0.1663 | 0.3322 |
| `deepseek/deepseek-r1-distill-llama-70b` | 0.0974 | 0.3113 |
| `meta-llama/llama-4-scout` | 0.1091 | 0.3347 |
| `nvidia/nemotron-nano-9b-v2` | 0.1136 | 0.3651 |
| `meta-llama/llama-3.1-8b-instruct` | 0.1226 | 0.5477 |
| `cohere/command-r-plus-08-2024` | 0.0352 | 0.1547 |
| `openai/o4-mini` | 0.1255 | 0.2981 |
| `microsoft/phi-4` | 0.0614 | 0.3070 |
| `qwen/qwen3-8b` | 0.0106 | 0.1278 |
| `mistralai/mistral-7b-instruct-v0.1` | 0.0000 | 0.0000 |

## Cross-model agreement

For each of the 171 (episode, window, trial-equivalent) entries, how many of the 44 active models predicted at least one ad? High-agreement windows are unambiguous ads (or unambiguously not ads). Low-agreement windows are where individual models disagree, and are candidates for ensemble voting if you want a cheap accuracy boost.

| Models predicting an ad | Window count | Share |
|---:|---:|---:|
| 2 of 44 | 1 | 0.6% |
| 4 of 44 | 8 | 4.7% |
| 5 of 44 | 15 | 8.8% |
| 6 of 44 | 10 | 5.8% |
| 7 of 44 | 7 | 4.1% |
| 8 of 44 | 6 | 3.5% |
| 9 of 44 | 7 | 4.1% |
| 10 of 44 | 9 | 5.3% |
| 11 of 44 | 8 | 4.7% |
| 12 of 44 | 7 | 4.1% |
| 13 of 44 | 5 | 2.9% |
| 14 of 44 | 2 | 1.2% |
| 15 of 44 | 3 | 1.8% |
| 16 of 44 | 4 | 2.3% |
| 17 of 44 | 1 | 0.6% |
| 18 of 44 | 1 | 0.6% |
| 19 of 44 | 2 | 1.2% |
| 26 of 44 | 2 | 1.2% |
| 27 of 44 | 1 | 0.6% |
| 29 of 44 | 3 | 1.8% |
| 30 of 44 | 1 | 0.6% |
| 32 of 44 | 1 | 0.6% |
| 33 of 44 | 2 | 1.2% |
| 35 of 44 | 2 | 1.2% |
| 36 of 44 | 3 | 1.8% |
| 37 of 44 | 6 | 3.5% |
| 38 of 44 | 14 | 8.2% |
| 39 of 44 | 16 | 9.4% |
| 40 of 44 | 12 | 7.0% |
| 41 of 44 | 9 | 5.3% |
| 42 of 44 | 3 | 1.8% |

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
| `qwen/qwen3.5-plus-02-15` | 73 | 96 | 0 | 2 | 98.8% |
| `x-ai/grok-4.3` | 73 | 96 | 0 | 2 | 98.8% |
| `google/gemini-2.5-flash` | 74 | 94 | 2 | 1 | 98.2% |
| `qwen/qwen3.5-27b` | 72 | 96 | 0 | 3 | 98.2% |
| `openai/gpt-oss-120b` | 75 | 92 | 4 | 0 | 97.7% |
| `openai/gpt-5.5` | 73 | 93 | 3 | 2 | 97.1% |
| `nvidia/llama-3.3-nemotron-super-49b-v1.5` | 69 | 96 | 0 | 6 | 96.5% |
| `claude-sonnet-4-6` | 67 | 96 | 0 | 8 | 95.3% |
| `google/gemini-2.5-pro` | 75 | 88 | 8 | 0 | 95.3% |
| `google/gemini-3.5-flash` | 67 | 96 | 0 | 8 | 95.3% |
| `google/gemma-4-31b-it` | 73 | 90 | 6 | 2 | 95.3% |
| `mistralai/mistral-medium-3.1` | 68 | 95 | 1 | 7 | 95.3% |
| `qwen/qwen3.6-plus` | 68 | 95 | 1 | 7 | 95.3% |
| `claude-haiku-4-5-20251001` | 69 | 93 | 3 | 6 | 94.7% |
| `qwen/qwen3.6-flash` | 70 | 92 | 4 | 5 | 94.7% |
| `openai/o3` | 63 | 96 | 0 | 12 | 93.0% |
| `claude-opus-4-7` | 62 | 96 | 0 | 13 | 92.4% |
| `meta-llama/llama-4-scout` | 73 | 85 | 11 | 2 | 92.4% |
| `google/gemini-3.1-flash-lite` | 75 | 80 | 16 | 0 | 90.6% |
| `meta-llama/llama-3.3-70b-instruct` | 64 | 89 | 7 | 11 | 89.5% |
| `deepseek/deepseek-v4-flash` | 75 | 77 | 19 | 0 | 88.9% |
| `nvidia/nemotron-nano-9b-v2` | 73 | 78 | 18 | 2 | 88.3% |
| `meta-llama/llama-4-maverick` | 72 | 78 | 18 | 3 | 87.7% |
| `deepseek/deepseek-r1` | 75 | 74 | 22 | 0 | 87.1% |
| `meta-llama/llama-3.1-8b-instruct` | 70 | 78 | 18 | 5 | 86.5% |
| `deepseek/deepseek-v4-pro` | 61 | 82 | 14 | 14 | 83.6% |
| `google/gemini-2.5-flash-lite` | 75 | 68 | 28 | 0 | 83.6% |
| `mistralai/codestral-2508` | 69 | 71 | 25 | 6 | 81.9% |
| `openai/gpt-5.4` | 74 | 66 | 30 | 1 | 81.9% |
| `qwen/qwen3-14b` | 65 | 73 | 23 | 10 | 80.7% |
| `openai/gpt-5.4-mini` | 73 | 59 | 37 | 2 | 77.2% |
| `cohere/command-a` | 71 | 56 | 40 | 4 | 74.3% |
| `cohere/command-r-plus-08-2024` | 25 | 96 | 0 | 50 | 70.8% |
| `openai/o4-mini` | 25 | 96 | 0 | 50 | 70.8% |
| `deepseek/deepseek-v3.2` | 53 | 63 | 33 | 22 | 67.8% |
| `mistralai/mistral-large-2512` | 74 | 41 | 55 | 1 | 67.3% |
| `deepseek/deepseek-r1-0528` | 71 | 34 | 62 | 4 | 61.4% |
| `qwen/qwen3-235b-a22b-2507` | 75 | 30 | 66 | 0 | 61.4% |
| `openai/gpt-3.5-turbo` | 75 | 28 | 68 | 0 | 60.2% |
| `qwen/qwen3-8b` | 2 | 96 | 0 | 73 | 57.3% |
| `mistralai/mistral-7b-instruct-v0.1` | 0 | 96 | 0 | 75 | 56.1% |
| `moonshotai/kimi-k2.6` | 48 | 42 | 54 | 27 | 52.6% |
| `deepseek/deepseek-r1-distill-llama-70b` | 66 | 8 | 88 | 9 | 43.3% |
| `microsoft/phi-4` | 55 | 14 | 82 | 20 | 40.4% |

## Detection rate by ad characteristic

Aggregate detection rates often hide systematic blind spots. Below: for each model, what fraction of truth ads in each bucket were detected (matched at IoU >= 0.5).

### By ad length

Truth ads bucketed by duration: short (<30s), medium (30-90s), long (>=90s). Cell values are detection rate (fraction of truth ads in that bucket the model caught), with the sample size `n` so a misleading 1.00 on a 2-ad bucket doesn't get over-weighted. Models that systematically miss short ads usually fail on network-inserted brand-tagline spots; missing long ads is rarer and usually means the model gave up before processing the full window.

| Model | long (>=90s) | medium (30-90s) | short (<30s) |
|---|---:|---:|---:|
| `claude-haiku-4-5-20251001` | 0.36 (n=140) | 0.80 (n=75) | 0.88 (n=40) |
| `claude-opus-4-7` | 0.61 (n=140) | 0.75 (n=75) | 0.50 (n=40) |
| `claude-sonnet-4-6` | 0.54 (n=140) | 0.77 (n=75) | 0.68 (n=40) |
| `cohere/command-a` | 0.41 (n=140) | 0.63 (n=75) | 0.75 (n=40) |
| `cohere/command-r-plus-08-2024` | 0.19 (n=140) | 0.04 (n=75) | 0.12 (n=40) |
| `deepseek/deepseek-r1` | 0.56 (n=140) | 0.76 (n=75) | 0.80 (n=40) |
| `deepseek/deepseek-r1-0528` | 0.51 (n=140) | 0.73 (n=75) | 0.78 (n=40) |
| `deepseek/deepseek-r1-distill-llama-70b` | 0.26 (n=140) | 0.29 (n=75) | 0.62 (n=40) |
| `deepseek/deepseek-v3.2` | 0.35 (n=140) | 0.31 (n=75) | 0.28 (n=40) |
| `deepseek/deepseek-v4-flash` | 0.61 (n=140) | 0.81 (n=75) | 0.78 (n=40) |
| `deepseek/deepseek-v4-pro` | 0.49 (n=140) | 0.28 (n=75) | 0.20 (n=40) |
| `google/gemini-2.5-flash` | 0.29 (n=140) | 0.67 (n=75) | 0.88 (n=40) |
| `google/gemini-2.5-flash-lite` | 0.35 (n=140) | 0.76 (n=75) | 0.88 (n=40) |
| `google/gemini-2.5-pro` | 0.84 (n=140) | 0.79 (n=75) | 0.72 (n=40) |
| `google/gemini-3.1-flash-lite` | 0.34 (n=140) | 0.92 (n=75) | 0.88 (n=40) |
| `google/gemini-3.5-flash` | 0.72 (n=140) | 0.76 (n=75) | 0.70 (n=40) |
| `google/gemma-4-31b-it` | 0.56 (n=140) | 0.73 (n=75) | 0.60 (n=40) |
| `meta-llama/llama-3.1-8b-instruct` | 0.12 (n=140) | 0.28 (n=75) | 0.42 (n=40) |
| `meta-llama/llama-3.3-70b-instruct` | 0.30 (n=140) | 0.32 (n=75) | 0.53 (n=40) |
| `meta-llama/llama-4-maverick` | 0.50 (n=140) | 0.69 (n=75) | 0.38 (n=40) |
| `meta-llama/llama-4-scout` | 0.22 (n=140) | 0.36 (n=75) | 0.57 (n=40) |
| `microsoft/phi-4` | 0.04 (n=140) | 0.15 (n=75) | 0.12 (n=40) |
| `mistralai/codestral-2508` | 0.39 (n=140) | 0.57 (n=75) | 0.45 (n=40) |
| `mistralai/mistral-7b-instruct-v0.1` | 0.00 (n=140) | 0.00 (n=75) | 0.00 (n=40) |
| `mistralai/mistral-large-2512` | 0.31 (n=140) | 0.73 (n=75) | 0.75 (n=40) |
| `mistralai/mistral-medium-3.1` | 0.30 (n=140) | 0.69 (n=75) | 0.72 (n=40) |
| `moonshotai/kimi-k2.6` | 0.30 (n=140) | 0.52 (n=75) | 0.25 (n=40) |
| `nvidia/llama-3.3-nemotron-super-49b-v1.5` | 0.31 (n=140) | 0.57 (n=75) | 0.60 (n=40) |
| `nvidia/nemotron-nano-9b-v2` | 0.14 (n=140) | 0.43 (n=75) | 0.35 (n=40) |
| `openai/gpt-3.5-turbo` | 0.31 (n=140) | 0.40 (n=75) | 0.75 (n=40) |
| `openai/gpt-5.4` | 0.80 (n=140) | 0.73 (n=75) | 0.57 (n=40) |
| `openai/gpt-5.4-mini` | 0.74 (n=140) | 0.63 (n=75) | 0.75 (n=40) |
| `openai/gpt-5.5` | 0.74 (n=140) | 0.77 (n=75) | 0.57 (n=40) |
| `openai/gpt-oss-120b` | 0.46 (n=140) | 0.71 (n=75) | 0.75 (n=40) |
| `openai/o3` | 0.66 (n=140) | 0.44 (n=75) | 0.30 (n=40) |
| `openai/o4-mini` | 0.03 (n=140) | 0.05 (n=75) | 0.10 (n=40) |
| `qwen/qwen3-14b` | 0.23 (n=140) | 0.21 (n=75) | 0.30 (n=40) |
| `qwen/qwen3-235b-a22b-2507` | 0.44 (n=140) | 0.65 (n=75) | 0.88 (n=40) |
| `qwen/qwen3-8b` | 0.01 (n=140) | 0.00 (n=75) | 0.00 (n=40) |
| `qwen/qwen3.5-27b` | 0.59 (n=140) | 0.69 (n=75) | 0.68 (n=40) |
| `qwen/qwen3.5-plus-02-15` | 0.86 (n=116) | 0.81 (n=72) | 0.62 (n=40) |
| `qwen/qwen3.6-flash` | 0.87 (n=140) | 0.73 (n=75) | 0.70 (n=40) |
| `qwen/qwen3.6-plus` | 0.84 (n=140) | 0.83 (n=75) | 0.62 (n=40) |
| `x-ai/grok-4.3` | 0.58 (n=140) | 0.91 (n=75) | 0.72 (n=40) |

### By ad position

Truth ads bucketed by where they fall in the episode: pre-roll (first 10%), mid-roll (10-90%), post-roll (last 10%). Cell values are the same detection-rate-with-`n` format as ad length. A common failure pattern in our data: most models detect pre-roll and mid-roll reliably and miss post-roll, because the prompt windows near the end often catch the model mid-reasoning or with fewer transition phrases to anchor on.

| Model | pre-roll (<10%) | mid-roll (10-90%) | post-roll (>90%) |
|---|---:|---:|---:|
| `claude-haiku-4-5-20251001` | 0.53 (n=75) | 0.56 (n=125) | 0.64 (n=55) |
| `claude-opus-4-7` | 0.65 (n=75) | 0.56 (n=125) | 0.76 (n=55) |
| `claude-sonnet-4-6` | 0.77 (n=75) | 0.58 (n=125) | 0.55 (n=55) |
| `cohere/command-a` | 0.52 (n=75) | 0.52 (n=125) | 0.55 (n=55) |
| `cohere/command-r-plus-08-2024` | 0.05 (n=75) | 0.23 (n=125) | 0.02 (n=55) |
| `deepseek/deepseek-r1` | 0.61 (n=75) | 0.69 (n=125) | 0.64 (n=55) |
| `deepseek/deepseek-r1-0528` | 0.59 (n=75) | 0.63 (n=125) | 0.62 (n=55) |
| `deepseek/deepseek-r1-distill-llama-70b` | 0.31 (n=75) | 0.33 (n=125) | 0.35 (n=55) |
| `deepseek/deepseek-v3.2` | 0.41 (n=75) | 0.37 (n=125) | 0.11 (n=55) |
| `deepseek/deepseek-v4-flash` | 0.63 (n=75) | 0.75 (n=125) | 0.65 (n=55) |
| `deepseek/deepseek-v4-pro` | 0.27 (n=75) | 0.47 (n=125) | 0.35 (n=55) |
| `google/gemini-2.5-flash` | 0.47 (n=75) | 0.48 (n=125) | 0.55 (n=55) |
| `google/gemini-2.5-flash-lite` | 0.59 (n=75) | 0.54 (n=125) | 0.55 (n=55) |
| `google/gemini-2.5-pro` | 0.79 (n=75) | 0.85 (n=125) | 0.73 (n=55) |
| `google/gemini-3.1-flash-lite` | 0.60 (n=75) | 0.58 (n=125) | 0.62 (n=55) |
| `google/gemini-3.5-flash` | 0.67 (n=75) | 0.77 (n=125) | 0.73 (n=55) |
| `google/gemma-4-31b-it` | 0.59 (n=75) | 0.62 (n=125) | 0.67 (n=55) |
| `meta-llama/llama-3.1-8b-instruct` | 0.23 (n=75) | 0.23 (n=125) | 0.16 (n=55) |
| `meta-llama/llama-3.3-70b-instruct` | 0.23 (n=75) | 0.40 (n=125) | 0.36 (n=55) |
| `meta-llama/llama-4-maverick` | 0.49 (n=75) | 0.60 (n=125) | 0.45 (n=55) |
| `meta-llama/llama-4-scout` | 0.27 (n=75) | 0.40 (n=125) | 0.20 (n=55) |
| `microsoft/phi-4` | 0.17 (n=75) | 0.02 (n=125) | 0.11 (n=55) |
| `mistralai/codestral-2508` | 0.36 (n=75) | 0.48 (n=125) | 0.53 (n=55) |
| `mistralai/mistral-7b-instruct-v0.1` | 0.00 (n=75) | 0.00 (n=125) | 0.00 (n=55) |
| `mistralai/mistral-large-2512` | 0.52 (n=75) | 0.51 (n=125) | 0.45 (n=55) |
| `mistralai/mistral-medium-3.1` | 0.51 (n=75) | 0.44 (n=125) | 0.55 (n=55) |
| `moonshotai/kimi-k2.6` | 0.29 (n=75) | 0.31 (n=125) | 0.55 (n=55) |
| `nvidia/llama-3.3-nemotron-super-49b-v1.5` | 0.37 (n=75) | 0.46 (n=125) | 0.44 (n=55) |
| `nvidia/nemotron-nano-9b-v2` | 0.15 (n=75) | 0.26 (n=125) | 0.38 (n=55) |
| `openai/gpt-3.5-turbo` | 0.31 (n=75) | 0.48 (n=125) | 0.36 (n=55) |
| `openai/gpt-5.4` | 0.75 (n=75) | 0.78 (n=125) | 0.65 (n=55) |
| `openai/gpt-5.4-mini` | 0.60 (n=75) | 0.83 (n=125) | 0.56 (n=55) |
| `openai/gpt-5.5` | 0.73 (n=75) | 0.73 (n=125) | 0.71 (n=55) |
| `openai/gpt-oss-120b` | 0.53 (n=75) | 0.61 (n=125) | 0.56 (n=55) |
| `openai/o3` | 0.44 (n=75) | 0.59 (n=125) | 0.55 (n=55) |
| `openai/o4-mini` | 0.01 (n=75) | 0.06 (n=125) | 0.05 (n=55) |
| `qwen/qwen3-14b` | 0.13 (n=75) | 0.29 (n=125) | 0.25 (n=55) |
| `qwen/qwen3-235b-a22b-2507` | 0.56 (n=75) | 0.59 (n=125) | 0.55 (n=55) |
| `qwen/qwen3-8b` | 0.00 (n=75) | 0.01 (n=125) | 0.00 (n=55) |
| `qwen/qwen3.5-27b` | 0.56 (n=75) | 0.67 (n=125) | 0.65 (n=55) |
| `qwen/qwen3.5-plus-02-15` | 0.82 (n=66) | 0.84 (n=110) | 0.71 (n=52) |
| `qwen/qwen3.6-flash` | 0.71 (n=75) | 0.88 (n=125) | 0.76 (n=55) |
| `qwen/qwen3.6-plus` | 0.75 (n=75) | 0.88 (n=125) | 0.71 (n=55) |
| `x-ai/grok-4.3` | 0.67 (n=75) | 0.74 (n=125) | 0.65 (n=55) |

## Quick Comparison

One row per model, one column per episode. The headline columns (`F1`, `Cost/ep`, `p50`) summarize across all episodes; the per-episode columns let you see whether a model's average hides wide swings (a model that scores well overall might still bomb on a specific genre). The right-most `F1 stdev` column averages the per-trial standard deviations across episodes; high values mean the model isn't deterministic at temperature 0.0, so its single-trial F1 number is noisy.

| Model | F1 | Cost/ep | p50 | ep-crime-junkie-8ce498f299d7 | ep-daily-gist-chicago-70a82fe93a5c | ep-daily-tech-news-show-b576979e1fe8 | ep-daily-tech-news-show-c1904b8605f7 | ep-drink-champs-30c9a2d49f13 | ep-glt1412515089-373d5ba5007b | ep-it-s-a-thing-e339179dfad6 | ep-on-air-with-dan-and-alex2-574e4f303730 | ep-security-now-audio-2850b24903b2 | ep-the-brilliant-idiots-0bb9bf634c8e | ep-the-tim-dillon-show-f62bd5fa1cfe | ep-tosh-show-5f6894439bb6 | ep-ai-cloud-essentials-e8dc897fbd6b (no-ad) | ep-oxide-and-friends-ce789ff5b62e (no-ad) | F1 stdev |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| `qwen/qwen3.6-plus` | 0.693 | $1.1119 | 39.9s | 0.933 | 0.667 | 0.914 | 0.457 | 0.706 | 0.600 | 0.667 | 0.773 | 0.481 | 0.798 | 0.615 | 0.703 | PASS | PASS | 0.040 |
| `qwen/qwen3.5-plus-02-15` | 0.679 | $1.2346 | 48.2s | 1.000 | 0.600 | 0.857 | 0.518 | 0.711 | 0.625 | 0.667 | 0.590 | 0.476 | 0.771 | 0.636 | 0.696 | PASS | PASS | 0.034 |
| `qwen/qwen3.6-flash` | 0.660 | $0.5461 | 13.0s | 1.000 | 0.700 | 0.852 | 0.492 | 0.628 | 0.518 | 0.667 | 0.682 | 0.514 | 0.800 | 0.507 | 0.564 | FAIL (1 FP) | PASS | 0.087 |
| `openai/gpt-5.5` | 0.647 | $6.6806 | 6.4s | 0.898 | 0.500 | 0.886 | 0.547 | 0.642 | 0.636 | 0.667 | 0.571 | 0.505 | 0.776 | 0.546 | 0.587 | FAIL (1 FP) | PASS | 0.061 |
| `google/gemini-3.5-flash` | 0.625 | $3.5200 | 5.2s | 0.914 | 0.400 | 0.857 | 0.500 | 0.496 | 0.676 | 0.667 | 0.571 | 0.476 | 0.857 | 0.592 | 0.490 | PASS | PASS | 0.022 |
| `qwen/qwen3.5-27b` | 0.622 | $3.2163 | 68.7s | 0.892 | 0.727 | 0.743 | 0.506 | 0.494 | 0.601 | 0.600 | 0.634 | 0.414 | 0.671 | 0.537 | 0.647 | PASS | PASS | 0.161 |
| `openai/gpt-5.4` | 0.620 | $2.5345 | 1.8s | 0.933 | 0.500 | 0.892 | 0.518 | 0.571 | 0.506 | 0.613 | 0.747 | 0.495 | 0.516 | 0.586 | 0.569 | FAIL (1 FP) | FAIL (1 FP) | 0.067 |
| `google/gemini-2.5-pro` | 0.613 | $3.8631 | 14.2s | 0.971 | 0.400 | 0.864 | 0.448 | 0.679 | 0.646 | 0.667 | 0.543 | 0.451 | 0.510 | 0.569 | 0.603 | FAIL (1 FP) | FAIL (1 FP) | 0.041 |
| `openai/gpt-5.4-mini` | 0.584 | $0.7644 | 1.2s | 0.883 | 0.833 | 0.943 | 0.468 | 0.509 | 0.494 | 0.500 | 0.610 | 0.491 | 0.353 | 0.373 | 0.547 | FAIL (1 FP) | FAIL (1 FP) | 0.112 |
| `claude-opus-4-7` | 0.576 | $7.8054 | 2.2s | 0.857 | 0.400 | 0.863 | 0.231 | 0.334 | 0.600 | 0.667 | 0.595 | 0.520 | 0.733 | 0.592 | 0.524 | PASS | PASS | 0.043 |
| `openai/o3` | 0.567 | $3.0485 | 8.1s | 0.876 | 0.000 | 0.848 | 0.472 | 0.742 | 0.664 | 0.333 | 0.687 | 0.644 | 0.698 | 0.372 | 0.464 | PASS | PASS | 0.150 |
| `x-ai/grok-4.3` | 0.506 | $1.4987 | 3.9s | 0.938 | 0.533 | 0.507 | 0.179 | 0.208 | 0.572 | 0.433 | 0.472 | 0.486 | 0.771 | 0.393 | 0.585 | PASS | PASS | 0.105 |
| `deepseek/deepseek-v4-flash` | 0.475 | $0.1346 | 3.7s | 0.718 | 0.587 | 0.445 | 0.310 | 0.218 | 0.642 | 0.337 | 0.535 | 0.465 | 0.569 | 0.298 | 0.574 | FAIL (1 FP) | PASS | 0.123 |
| `google/gemma-4-31b-it` | 0.474 | $0.1291 | 2.2s | 0.879 | 0.480 | 0.811 | 0.119 | 0.158 | 0.645 | 0.467 | 0.514 | 0.496 | 0.585 | 0.350 | 0.182 | FAIL (1 FP) | PASS | 0.080 |
| `deepseek/deepseek-r1` | 0.469 | $1.1157 | 19.9s | 0.732 | 0.693 | 0.658 | 0.158 | 0.268 | 0.630 | 0.313 | 0.555 | 0.462 | 0.401 | 0.327 | 0.435 | FAIL (1 FP) | FAIL (1 FP) | 0.143 |
| `claude-sonnet-4-6` | 0.446 | $3.5376 | 1.4s | 0.889 | 0.800 | 0.407 | 0.237 | 0.275 | 0.400 | 0.000 | 0.444 | 0.516 | 0.836 | 0.179 | 0.375 | PASS | PASS | 0.042 |
| `qwen/qwen3-235b-a22b-2507` | 0.433 | $0.0735 | 2.3s | 0.774 | 0.853 | 0.756 | 0.142 | 0.033 | 0.540 | 0.000 | 0.517 | 0.460 | 0.342 | 0.421 | 0.362 | FAIL (2 FP) | FAIL (6 FP) | 0.082 |
| `openai/gpt-oss-120b` | 0.432 | $0.0643 | 3.0s | 0.819 | 0.600 | 0.270 | 0.201 | 0.058 | 0.625 | 0.227 | 0.457 | 0.469 | 0.630 | 0.387 | 0.443 | FAIL (1 FP) | PASS | 0.138 |
| `deepseek/deepseek-r1-0528` | 0.420 | $1.0181 | 16.5s | 0.717 | 0.647 | 0.700 | 0.184 | 0.125 | 0.379 | 0.404 | 0.514 | 0.281 | 0.161 | 0.283 | 0.643 | FAIL (27 FP) | FAIL (12 FP) | 0.142 |
| `nvidia/llama-3.3-nemotron-super-49b-v1.5` | 0.411 | $0.2159 | 24.2s | 0.888 | 0.667 | 0.299 | 0.139 | 0.045 | 0.596 | 0.233 | 0.431 | 0.587 | 0.478 | 0.171 | 0.399 | PASS | PASS | 0.135 |
| `cohere/command-a` | 0.408 | $2.6688 | 3.8s | 0.911 | 0.400 | 0.500 | 0.298 | 0.031 | 0.507 | 0.400 | 0.503 | 0.368 | 0.247 | 0.200 | 0.533 | FAIL (3 FP) | PASS | 0.040 |
| `google/gemini-3.1-flash-lite` | 0.402 | $0.2762 | 0.8s | 0.771 | 0.800 | 0.473 | 0.076 | 0.039 | 0.527 | 0.433 | 0.400 | 0.413 | 0.338 | 0.183 | 0.375 | FAIL (1 FP) | PASS | 0.039 |
| `moonshotai/kimi-k2.6` | 0.400 | $2.3369 | 35.3s | 0.547 | 0.100 | 0.914 | 0.600 | 0.053 | 0.469 | 0.200 | 0.734 | 0.196 | 0.538 | 0.184 | 0.267 | FAIL (1 FP) | FAIL (4 FP) | 0.164 |
| `meta-llama/llama-4-maverick` | 0.398 | $0.1508 | 1.1s | 1.000 | 0.000 | 0.771 | 0.204 | 0.273 | 0.507 | 0.000 | 0.571 | 0.496 | 0.390 | 0.167 | 0.400 | FAIL (1 FP) | PASS | 0.016 |
| `mistralai/mistral-medium-3.1` | 0.368 | $0.4380 | 0.9s | 0.397 | 0.667 | 0.162 | 0.095 | 0.046 | 0.671 | 0.000 | 0.500 | 0.640 | 0.591 | 0.223 | 0.421 | PASS | PASS | 0.047 |
| `claude-haiku-4-5-20251001` | 0.367 | $1.2017 | 1.2s | 0.500 | 0.800 | 0.235 | 0.073 | 0.042 | 0.571 | 0.000 | 0.500 | 0.551 | 0.600 | 0.154 | 0.375 | PASS | PASS | 0.001 |
| `deepseek/deepseek-v4-pro` | 0.367 | $0.6376 | 26.4s | 0.728 | 0.133 | 0.418 | 0.350 | 0.262 | 0.449 | 0.267 | 0.257 | 0.498 | 0.167 | 0.565 | 0.305 | PASS | PASS | 0.221 |
| `mistralai/codestral-2508` | 0.314 | $0.3241 | 0.7s | 0.520 | 0.667 | 0.379 | 0.172 | 0.033 | 0.231 | 0.000 | 0.469 | 0.374 | 0.187 | 0.178 | 0.564 | PASS | PASS | 0.049 |
| `google/gemini-2.5-flash-lite` | 0.302 | $0.1104 | 0.9s | 0.462 | 0.667 | 0.326 | 0.069 | 0.084 | 0.413 | 0.000 | 0.400 | 0.280 | 0.432 | 0.133 | 0.364 | FAIL (1 FP) | PASS | 0.023 |
| `deepseek/deepseek-v3.2` | 0.298 | $0.2564 | 2.7s | 0.590 | 0.300 | 0.327 | 0.140 | 0.176 | 0.518 | 0.400 | 0.300 | 0.528 | 0.100 | 0.057 | 0.140 | PASS | FAIL (2 FP) | 0.191 |
| `google/gemini-2.5-flash` | 0.293 | $0.3435 | 1.0s | 0.462 | 0.800 | 0.267 | 0.071 | 0.037 | 0.500 | 0.000 | 0.444 | 0.455 | 0.125 | 0.154 | 0.200 | PASS | PASS | 0.000 |
| `openai/gpt-3.5-turbo` | 0.286 | $0.5091 | 1.3s | 0.978 | 0.420 | 0.222 | 0.364 | 0.038 | 0.254 | 0.000 | 0.400 | 0.217 | 0.220 | 0.125 | 0.189 | FAIL (3 FP) | FAIL (10 FP) | 0.013 |
| `meta-llama/llama-3.3-70b-instruct` | 0.274 | $0.1008 | 1.5s | 0.502 | 0.100 | 0.327 | 0.000 | 0.000 | 0.567 | 0.000 | 0.133 | 0.512 | 0.489 | 0.308 | 0.347 | PASS | PASS | 0.081 |
| `mistralai/mistral-large-2512` | 0.249 | $0.5599 | 2.6s | 0.492 | 0.648 | 0.250 | 0.074 | 0.033 | 0.253 | 0.000 | 0.444 | 0.209 | 0.193 | 0.044 | 0.353 | PASS | PASS | 0.016 |
| `qwen/qwen3-14b` | 0.248 | $0.1261 | 20.9s | 0.190 | 0.000 | 0.518 | 0.100 | 0.045 | 0.434 | 0.000 | 0.330 | 0.336 | 0.372 | 0.233 | 0.412 | PASS | FAIL (1 FP) | 0.166 |
| `deepseek/deepseek-r1-distill-llama-70b` | 0.241 | $0.7331 | 2.0s | 0.590 | 0.440 | 0.057 | 0.205 | 0.000 | 0.265 | 0.000 | 0.343 | 0.262 | 0.222 | 0.163 | 0.340 | FAIL (2 FP) | FAIL (10 FP) | 0.097 |
| `meta-llama/llama-4-scout` | 0.235 | $0.0807 | 0.8s | 0.253 | 0.587 | 0.242 | 0.130 | 0.000 | 0.336 | 0.000 | 0.094 | 0.520 | 0.349 | 0.053 | 0.258 | PASS | PASS | 0.109 |
| `nvidia/nemotron-nano-9b-v2` | 0.221 | $0.0813 | 12.0s | 0.334 | 0.600 | 0.274 | 0.069 | 0.044 | 0.242 | 0.000 | 0.361 | 0.238 | 0.205 | 0.044 | 0.240 | FAIL (1 FP) | PASS | 0.114 |
| `meta-llama/llama-3.1-8b-instruct` | 0.198 | $0.0207 | 0.8s | 0.164 | 0.700 | 0.231 | 0.029 | 0.017 | 0.183 | 0.400 | 0.284 | 0.133 | 0.000 | 0.049 | 0.185 | PASS | PASS | 0.123 |
| `cohere/command-r-plus-08-2024` | 0.102 | $2.5787 | 1.0s | 0.000 | 0.000 | 0.000 | 0.313 | 0.000 | 0.000 | 0.000 | 0.000 | 0.569 | 0.000 | 0.057 | 0.289 | PASS | PASS | 0.035 |
| `openai/o4-mini` | 0.075 | $1.8999 | 6.9s | 0.000 | 0.000 | 0.147 | 0.067 | 0.040 | 0.080 | 0.000 | 0.133 | 0.114 | 0.180 | 0.000 | 0.133 | PASS | PASS | 0.125 |
| `microsoft/phi-4` | 0.055 | $0.0713 | 2.3s | 0.157 | 0.000 | 0.000 | 0.056 | 0.058 | 0.000 | 0.000 | 0.213 | 0.033 | 0.067 | 0.079 | 0.000 | FAIL (3 FP) | FAIL (15 FP) | 0.061 |
| `qwen/qwen3-8b` | 0.005 | $0.2578 | 59.4s | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.057 | 0.000 | PASS | PASS | 0.011 |
| `mistralai/mistral-7b-instruct-v0.1` | 0.000 | $0.0285 | 7.1s | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | PASS | PASS | 0.000 |

---

## Detailed Results

### Per-Model Detail

Full per-model profile: F1 averaged across episodes, total cost per episode at current pricing, p50 / p95 latency, JSON compliance, parse-failure rate, the distribution of extraction methods the parser had to use, and verbosity / truncation telemetry. The `Extraction methods` list shows how often each route was hit. `json_array_direct` is the cleanest; the rest are recovery paths. The verbosity row flags models that emit long `reason` fields or run out of token budget mid-response. Ordered by F1 descending so the best performers appear first.

#### `qwen/qwen3.6-plus`

- F1 (avg across episodes): **0.693**
- Total cost / episode: **$1.1119**
- p50 / p95 latency: 39.88s / 74.53s
- JSON compliance: 1.00
- JSON mode: native (100% native, 855 calls)
- Parse failure rate: 0.0%
- Extraction methods: `json_array_direct`: 855
- Verbosity: 813/855 calls over 1024 output tokens (95.1%); 0 hit max_tokens (0.0%); 0 salvaged from truncated JSON (0.0%)

#### `qwen/qwen3.5-plus-02-15`

- F1 (avg across episodes): **0.679**
- Total cost / episode: **$1.2346**
- p50 / p95 latency: 48.19s / 143.16s
- JSON compliance: 1.00
- JSON mode: native (100% native, 852 calls)
- Parse failure rate: 0.1%
- Extraction methods: `json_array_direct`: 851, `parse_failure`: 1
- Verbosity: 749/852 calls over 1024 output tokens (87.9%); 0 hit max_tokens (0.0%); 0 salvaged from truncated JSON (0.0%)
- Schema violations: 384
- Extra keys observed: end_text, sponsor

#### `qwen/qwen3.6-flash`

- F1 (avg across episodes): **0.660**
- Total cost / episode: **$0.5461**
- p50 / p95 latency: 13.01s / 39.29s
- JSON compliance: 1.00
- JSON mode: native (100% native, 855 calls)
- Parse failure rate: 0.0%
- Extraction methods: `json_array_direct`: 855
- Verbosity: 585/855 calls over 1024 output tokens (68.4%); 0 hit max_tokens (0.0%); 0 salvaged from truncated JSON (0.0%)

#### `openai/gpt-5.5`

- F1 (avg across episodes): **0.647**
- Total cost / episode: **$6.6806**
- p50 / p95 latency: 6.38s / 24.05s
- JSON compliance: 0.87
- JSON mode: native (100% native, 855 calls)
- Parse failure rate: 0.1%
- Extraction methods: `json_object_no_ads`: 494, `json_object_single_ad`: 360, `parse_failure`: 1
- Verbosity: 76/855 calls over 1024 output tokens (8.9%); 0 hit max_tokens (0.0%); 0 salvaged from truncated JSON (0.0%)
- Schema violations: 332
- Extra keys observed: end_text, sponsor

#### `google/gemini-3.5-flash`

- F1 (avg across episodes): **0.625**
- Total cost / episode: **$3.5200**
- p50 / p95 latency: 5.23s / 11.13s
- JSON compliance: 1.00
- JSON mode: native (100% native, 855 calls)
- Parse failure rate: 0.0%
- Extraction methods: `json_array_direct`: 850, `json_object_single_ad_truncated`: 1, `regex_json_array`: 4
- Verbosity: 519/855 calls over 1024 output tokens (60.7%); 1 hit max_tokens (0.1%); 1 salvaged from truncated JSON (0.1%)

#### `qwen/qwen3.5-27b`

- F1 (avg across episodes): **0.622**
- Total cost / episode: **$3.2163**
- p50 / p95 latency: 68.74s / 1145.40s
- JSON compliance: 0.85
- JSON mode: native (100% native, 855 calls)
- Parse failure rate: 14.2%
- Extraction methods: `json_array_direct`: 633, `json_object_no_ads`: 89, `json_object_single_ad`: 12, `parse_failure`: 121
- Verbosity: 798/855 calls over 1024 output tokens (93.3%); 108 hit max_tokens (12.6%); 0 salvaged from truncated JSON (0.0%)

#### `openai/gpt-5.4`

- F1 (avg across episodes): **0.620**
- Total cost / episode: **$2.5345**
- p50 / p95 latency: 1.82s / 3.12s
- JSON compliance: 0.81
- JSON mode: native (100% native, 855 calls)
- Parse failure rate: 0.0%
- Extraction methods: `json_object_no_ads`: 326, `json_object_single_ad`: 529
- Verbosity: 0/855 calls over 1024 output tokens (0.0%); 0 hit max_tokens (0.0%); 0 salvaged from truncated JSON (0.0%)
- Schema violations: 398
- Extra keys observed: end_text, sponsor

#### `google/gemini-2.5-pro`

- F1 (avg across episodes): **0.613**
- Total cost / episode: **$3.8631**
- p50 / p95 latency: 14.17s / 27.87s
- JSON compliance: 0.97
- JSON mode: native (100% native, 855 calls)
- Parse failure rate: 1.4%
- Extraction methods: `json_array_direct`: 818, `parse_failure`: 12, `regex_json_array`: 25
- Verbosity: 673/855 calls over 1024 output tokens (78.7%); 0 hit max_tokens (0.0%); 0 salvaged from truncated JSON (0.0%)
- Schema violations: 389
- Extra keys observed: end_text, sponsor

#### `openai/gpt-5.4-mini`

- F1 (avg across episodes): **0.584**
- Total cost / episode: **$0.7644**
- p50 / p95 latency: 1.18s / 2.24s
- JSON compliance: 0.81
- JSON mode: native (100% native, 855 calls)
- Parse failure rate: 0.0%
- Extraction methods: `json_object_ads_key`: 2, `json_object_no_ads`: 300, `json_object_segments_key`: 2, `json_object_single_ad`: 551
- Verbosity: 0/855 calls over 1024 output tokens (0.0%); 0 hit max_tokens (0.0%); 0 salvaged from truncated JSON (0.0%)

#### `claude-opus-4-7`

- F1 (avg across episodes): **0.576**
- Total cost / episode: **$7.8054**
- p50 / p95 latency: 2.17s / 4.21s
- JSON compliance: 1.00
- JSON mode: prompt-inject (0% native, 855 calls)
- Parse failure rate: 0.0%
- Extraction methods: `json_array_direct`: 850, `regex_json_array`: 5
- Verbosity: 0/855 calls over 1024 output tokens (0.0%); 0 hit max_tokens (0.0%); 0 salvaged from truncated JSON (0.0%)
- Schema violations: 341
- Extra keys observed: end_text, sponsor

#### `openai/o3`

- F1 (avg across episodes): **0.567**
- Total cost / episode: **$3.0485**
- p50 / p95 latency: 8.08s / 27.07s
- JSON compliance: 0.92
- JSON mode: native (100% native, 855 calls)
- Parse failure rate: 0.6%
- Extraction methods: `json_object_ads_key`: 34, `json_object_no_ads`: 621, `json_object_segments_key`: 12, `json_object_single_ad`: 183, `parse_failure`: 5
- Verbosity: 246/855 calls over 1024 output tokens (28.8%); 0 hit max_tokens (0.0%); 0 salvaged from truncated JSON (0.0%)
- Schema violations: 132
- Extra keys observed: end_text, sponsor

#### `x-ai/grok-4.3`

- F1 (avg across episodes): **0.506**
- Total cost / episode: **$1.4987**
- p50 / p95 latency: 3.88s / 12.56s
- JSON compliance: 1.00
- JSON mode: native (100% native, 855 calls)
- Parse failure rate: 0.1%
- Extraction methods: `json_array_direct`: 854, `parse_failure`: 1
- Verbosity: 165/855 calls over 1024 output tokens (19.3%); 0 hit max_tokens (0.0%); 0 salvaged from truncated JSON (0.0%)

#### `deepseek/deepseek-v4-flash`

- F1 (avg across episodes): **0.475**
- Total cost / episode: **$0.1346**
- p50 / p95 latency: 3.67s / 28.76s
- JSON compliance: 0.81
- JSON mode: native (100% native, 855 calls)
- Parse failure rate: 2.0%
- Extraction methods: `json_array_direct`: 102, `json_object_ads_key`: 435, `json_object_no_ads`: 21, `json_object_segments_key`: 7, `json_object_single_ad`: 272, `parse_failure`: 17, `regex_json_array`: 1
- Verbosity: 181/855 calls over 1024 output tokens (21.2%); 2 hit max_tokens (0.2%); 0 salvaged from truncated JSON (0.0%)
- Schema violations: 631
- Extra keys observed: end_text, sponsor

#### `google/gemma-4-31b-it`

- F1 (avg across episodes): **0.474**
- Total cost / episode: **$0.1291**
- p50 / p95 latency: 2.21s / 19.39s
- JSON compliance: 0.85
- JSON mode: native (100% native, 855 calls)
- Parse failure rate: 0.1%
- Extraction methods: `bracket_fallback`: 1, `json_object_ads_key`: 455, `json_object_no_ads`: 216, `json_object_single_ad`: 180, `json_object_single_ad_truncated`: 2, `parse_failure`: 1
- Verbosity: 3/855 calls over 1024 output tokens (0.4%); 3 hit max_tokens (0.4%); 2 salvaged from truncated JSON (0.2%)
- Schema violations: 532
- Extra keys observed: end_text, sponsor

#### `deepseek/deepseek-r1`

- F1 (avg across episodes): **0.469**
- Total cost / episode: **$1.1157**
- p50 / p95 latency: 19.86s / 153.13s
- JSON compliance: 0.97
- JSON mode: native (100% native, 855 calls)
- Parse failure rate: 0.7%
- Extraction methods: `json_array_direct`: 760, `json_object_ads_key`: 2, `json_object_no_ads`: 17, `json_object_segments_key`: 7, `json_object_single_ad`: 44, `markdown_code_block`: 17, `parse_failure`: 6, `regex_json_array`: 2
- Verbosity: 152/855 calls over 1024 output tokens (17.8%); 0 hit max_tokens (0.0%); 0 salvaged from truncated JSON (0.0%)
- Schema violations: 486
- Extra keys observed: end_text, sponsor

#### `claude-sonnet-4-6`

- F1 (avg across episodes): **0.446**
- Total cost / episode: **$3.5376**
- p50 / p95 latency: 1.44s / 6.04s
- JSON compliance: 0.96
- JSON mode: prompt-inject (0% native, 855 calls)
- Parse failure rate: 0.0%
- Extraction methods: `json_array_direct`: 783, `markdown_code_block`: 57, `regex_json_array`: 15
- Verbosity: 0/855 calls over 1024 output tokens (0.0%); 0 hit max_tokens (0.0%); 0 salvaged from truncated JSON (0.0%)
- Schema violations: 461
- Extra keys observed: end_text, sponsor

#### `qwen/qwen3-235b-a22b-2507`

- F1 (avg across episodes): **0.433**
- Total cost / episode: **$0.0735**
- p50 / p95 latency: 2.28s / 7.75s
- JSON compliance: 0.79
- JSON mode: native (100% native, 855 calls)
- Parse failure rate: 0.0%
- Extraction methods: `json_array_direct`: 173, `json_object_ads_key`: 1, `json_object_no_ads`: 94, `json_object_single_ad`: 587
- Verbosity: 0/855 calls over 1024 output tokens (0.0%); 0 hit max_tokens (0.0%); 0 salvaged from truncated JSON (0.0%)

#### `openai/gpt-oss-120b`

- F1 (avg across episodes): **0.432**
- Total cost / episode: **$0.0643**
- p50 / p95 latency: 2.96s / 34.40s
- JSON compliance: 0.70
- JSON mode: native (100% native, 855 calls)
- Parse failure rate: 17.4%
- Extraction methods: `json_array_direct`: 68, `json_object_ads_key`: 235, `json_object_no_ads`: 186, `json_object_single_ad`: 188, `json_object_single_ad_truncated`: 6, `parse_failure`: 149, `regex_json_array`: 23
- Verbosity: 162/855 calls over 1024 output tokens (18.9%); 2 hit max_tokens (0.2%); 6 salvaged from truncated JSON (0.7%)

#### `deepseek/deepseek-r1-0528`

- F1 (avg across episodes): **0.420**
- Total cost / episode: **$1.0181**
- p50 / p95 latency: 16.52s / 94.87s
- JSON compliance: 0.88
- JSON mode: native (100% native, 855 calls)
- Parse failure rate: 9.7%
- Extraction methods: `json_array_direct`: 700, `json_object_ads_key`: 34, `json_object_no_ads`: 3, `json_object_single_ad`: 30, `json_object_single_ad_truncated`: 3, `markdown_code_block`: 2, `parse_failure`: 83
- Verbosity: 341/855 calls over 1024 output tokens (39.9%); 38 hit max_tokens (4.4%); 3 salvaged from truncated JSON (0.4%)
- Schema violations: 694
- Extra keys observed: end_text, sponsor

#### `nvidia/llama-3.3-nemotron-super-49b-v1.5`

- F1 (avg across episodes): **0.411**
- Total cost / episode: **$0.2159**
- p50 / p95 latency: 24.21s / 86.23s
- JSON compliance: 0.71
- JSON mode: native (100% native, 855 calls)
- Parse failure rate: 14.5%
- Extraction methods: `json_array_direct`: 441, `json_object_single_ad_truncated`: 3, `markdown_code_block`: 257, `parse_failure`: 124, `regex_json_array`: 30
- Verbosity: 515/855 calls over 1024 output tokens (60.2%); 58 hit max_tokens (6.8%); 3 salvaged from truncated JSON (0.4%)
- Schema violations: 266
- Extra keys observed: end_text, sponsor

#### `cohere/command-a`

- F1 (avg across episodes): **0.408**
- Total cost / episode: **$2.6688**
- p50 / p95 latency: 3.78s / 11.46s
- JSON compliance: 0.71
- JSON mode: native (100% native, 855 calls)
- Parse failure rate: 0.0%
- Extraction methods: `json_object_no_ads`: 29, `json_object_single_ad`: 826
- Verbosity: 0/855 calls over 1024 output tokens (0.0%); 0 hit max_tokens (0.0%); 0 salvaged from truncated JSON (0.0%)
- Schema violations: 478
- Extra keys observed: end_text, sponsor

#### `google/gemini-3.1-flash-lite`

- F1 (avg across episodes): **0.402**
- Total cost / episode: **$0.2762**
- p50 / p95 latency: 0.79s / 1.44s
- JSON compliance: 0.96
- JSON mode: native (100% native, 855 calls)
- Parse failure rate: 0.0%
- Extraction methods: `json_array_direct`: 799, `regex_json_array`: 56
- Verbosity: 0/855 calls over 1024 output tokens (0.0%); 0 hit max_tokens (0.0%); 0 salvaged from truncated JSON (0.0%)

#### `moonshotai/kimi-k2.6`

- F1 (avg across episodes): **0.400**
- Total cost / episode: **$2.3369**
- p50 / p95 latency: 35.30s / 160.11s
- JSON compliance: 0.57
- JSON mode: native (100% native, 855 calls)
- Parse failure rate: 28.0%
- Extraction methods: `json_array_direct`: 68, `json_object_ads_key`: 35, `json_object_no_ads`: 109, `json_object_segments_key`: 2, `json_object_single_ad`: 397, `markdown_code_block`: 5, `parse_failure`: 239
- Verbosity: 767/855 calls over 1024 output tokens (89.7%); 113 hit max_tokens (13.2%); 0 salvaged from truncated JSON (0.0%)
- Schema violations: 234
- Extra keys observed: end_text, sponsor

#### `meta-llama/llama-4-maverick`

- F1 (avg across episodes): **0.398**
- Total cost / episode: **$0.1508**
- p50 / p95 latency: 1.06s / 2.38s
- JSON compliance: 0.81
- JSON mode: native (100% native, 855 calls)
- Parse failure rate: 0.0%
- Extraction methods: `json_object_no_ads`: 315, `json_object_single_ad`: 540
- Verbosity: 3/855 calls over 1024 output tokens (0.4%); 0 hit max_tokens (0.0%); 0 salvaged from truncated JSON (0.0%)
- Schema violations: 372
- Extra keys observed: end_text, sponsor

#### `mistralai/mistral-medium-3.1`

- F1 (avg across episodes): **0.368**
- Total cost / episode: **$0.4380**
- p50 / p95 latency: 0.91s / 6.18s
- JSON compliance: 1.00
- JSON mode: native (100% native, 855 calls)
- Parse failure rate: 0.0%
- Extraction methods: `json_array_direct`: 854, `json_object_single_ad_truncated`: 1
- Verbosity: 0/855 calls over 1024 output tokens (0.0%); 0 hit max_tokens (0.0%); 1 salvaged from truncated JSON (0.1%)
- Schema violations: 608
- Extra keys observed: end_text, sponsor

#### `claude-haiku-4-5-20251001`

- F1 (avg across episodes): **0.367**
- Total cost / episode: **$1.2017**
- p50 / p95 latency: 1.17s / 4.06s
- JSON compliance: 0.60
- JSON mode: prompt-inject (0% native, 855 calls)
- Parse failure rate: 0.0%
- Extraction methods: `markdown_code_block`: 855
- Verbosity: 0/855 calls over 1024 output tokens (0.0%); 0 hit max_tokens (0.0%); 0 salvaged from truncated JSON (0.0%)
- Schema violations: 711
- Extra keys observed: end_text, sponsor

#### `deepseek/deepseek-v4-pro`

- F1 (avg across episodes): **0.367**
- Total cost / episode: **$0.6376**
- p50 / p95 latency: 26.42s / 101.25s
- JSON compliance: 0.87
- JSON mode: native (100% native, 855 calls)
- Parse failure rate: 2.7%
- Extraction methods: `json_array_direct`: 229, `json_object_ads_key`: 63, `json_object_no_ads`: 131, `json_object_segments_key`: 314, `json_object_single_ad`: 86, `json_object_single_ad_truncated`: 2, `markdown_code_block`: 5, `parse_failure`: 23, `regex_json_array`: 2
- Verbosity: 433/855 calls over 1024 output tokens (50.6%); 15 hit max_tokens (1.8%); 2 salvaged from truncated JSON (0.2%)

#### `mistralai/codestral-2508`

- F1 (avg across episodes): **0.314**
- Total cost / episode: **$0.3241**
- p50 / p95 latency: 0.73s / 2.24s
- JSON compliance: 1.00
- JSON mode: native (100% native, 855 calls)
- Parse failure rate: 0.0%
- Extraction methods: `json_array_direct`: 855
- Verbosity: 0/855 calls over 1024 output tokens (0.0%); 0 hit max_tokens (0.0%); 0 salvaged from truncated JSON (0.0%)
- Schema violations: 622
- Extra keys observed: end_text, sponsor

#### `google/gemini-2.5-flash-lite`

- F1 (avg across episodes): **0.302**
- Total cost / episode: **$0.1104**
- p50 / p95 latency: 0.92s / 3.23s
- JSON compliance: 0.97
- JSON mode: native (100% native, 855 calls)
- Parse failure rate: 2.2%
- Extraction methods: `json_array_direct`: 793, `json_object_single_ad_truncated`: 43, `parse_failure`: 19
- Verbosity: 0/855 calls over 1024 output tokens (0.0%); 0 hit max_tokens (0.0%); 43 salvaged from truncated JSON (5.0%)

#### `deepseek/deepseek-v3.2`

- F1 (avg across episodes): **0.298**
- Total cost / episode: **$0.2564**
- p50 / p95 latency: 2.67s / 7.32s
- JSON compliance: 0.88
- JSON mode: native (100% native, 855 calls)
- Parse failure rate: 0.0%
- Extraction methods: `json_array_direct`: 501, `json_object_ads_key`: 16, `json_object_no_ads`: 3, `json_object_single_ad`: 335
- Verbosity: 1/855 calls over 1024 output tokens (0.1%); 0 hit max_tokens (0.0%); 0 salvaged from truncated JSON (0.0%)
- Schema violations: 263
- Extra keys observed: end_text, sponsor

#### `google/gemini-2.5-flash`

- F1 (avg across episodes): **0.293**
- Total cost / episode: **$0.3435**
- p50 / p95 latency: 0.99s / 3.59s
- JSON compliance: 1.00
- JSON mode: native (100% native, 855 calls)
- Parse failure rate: 0.0%
- Extraction methods: `json_array_direct`: 855
- Verbosity: 0/855 calls over 1024 output tokens (0.0%); 0 hit max_tokens (0.0%); 0 salvaged from truncated JSON (0.0%)
- Schema violations: 710
- Extra keys observed: end_text, sponsor

#### `openai/gpt-3.5-turbo`

- F1 (avg across episodes): **0.286**
- Total cost / episode: **$0.5091**
- p50 / p95 latency: 1.26s / 1.95s
- JSON compliance: 0.71
- JSON mode: native (100% native, 855 calls)
- Parse failure rate: 0.6%
- Extraction methods: `json_object_no_ads`: 50, `json_object_single_ad`: 800, `parse_failure`: 5
- Verbosity: 0/855 calls over 1024 output tokens (0.0%); 0 hit max_tokens (0.0%); 0 salvaged from truncated JSON (0.0%)
- Schema violations: 593
- Extra keys observed: end_text, sponsor

#### `meta-llama/llama-3.3-70b-instruct`

- F1 (avg across episodes): **0.274**
- Total cost / episode: **$0.1008**
- p50 / p95 latency: 1.47s / 4.82s
- JSON compliance: 0.55
- JSON mode: native (100% native, 855 calls)
- Parse failure rate: 35.1%
- Extraction methods: `json_array_direct`: 143, `json_object_ads_key`: 1, `json_object_no_ads`: 144, `json_object_single_ad`: 264, `parse_failure`: 300, `regex_json_array`: 3
- Verbosity: 1/855 calls over 1024 output tokens (0.1%); 0 hit max_tokens (0.0%); 0 salvaged from truncated JSON (0.0%)
- Schema violations: 378
- Extra keys observed: end_text, sponsor

#### `mistralai/mistral-large-2512`

- F1 (avg across episodes): **0.249**
- Total cost / episode: **$0.5599**
- p50 / p95 latency: 2.55s / 6.20s
- JSON compliance: 1.00
- JSON mode: native (100% native, 855 calls)
- Parse failure rate: 0.0%
- Extraction methods: `json_array_direct`: 855
- Verbosity: 0/855 calls over 1024 output tokens (0.0%); 0 hit max_tokens (0.0%); 0 salvaged from truncated JSON (0.0%)
- Schema violations: 1342
- Extra keys observed: end_text, sponsor

#### `qwen/qwen3-14b`

- F1 (avg across episodes): **0.248**
- Total cost / episode: **$0.1261**
- p50 / p95 latency: 20.91s / 63.42s
- JSON compliance: 0.28
- JSON mode: native (100% native, 855 calls)
- Parse failure rate: 60.4%
- Extraction methods: `json_object_no_ads`: 1, `json_object_single_ad`: 338, `parse_failure`: 516
- Verbosity: 91/855 calls over 1024 output tokens (10.6%); 10 hit max_tokens (1.2%); 0 salvaged from truncated JSON (0.0%)

#### `deepseek/deepseek-r1-distill-llama-70b`

- F1 (avg across episodes): **0.241**
- Total cost / episode: **$0.7331**
- p50 / p95 latency: 1.98s / 51.21s
- JSON compliance: 0.74
- JSON mode: native (100% native, 855 calls)
- Parse failure rate: 1.8%
- Extraction methods: `json_array_direct`: 20, `json_object_ads_key`: 68, `json_object_no_ads`: 104, `json_object_single_ad`: 642, `json_object_single_ad_truncated`: 5, `parse_failure`: 15, `regex_json_array`: 1
- Verbosity: 57/855 calls over 1024 output tokens (6.7%); 17 hit max_tokens (2.0%); 5 salvaged from truncated JSON (0.6%)
- Schema violations: 474
- Extra keys observed: end_text, sponsor

#### `meta-llama/llama-4-scout`

- F1 (avg across episodes): **0.235**
- Total cost / episode: **$0.0807**
- p50 / p95 latency: 0.84s / 4.44s
- JSON compliance: 0.82
- JSON mode: native (100% native, 855 calls)
- Parse failure rate: 1.1%
- Extraction methods: `bracket_fallback`: 37, `json_array_direct`: 5, `json_object_ads_key`: 644, `json_object_no_ads`: 92, `json_object_single_ad`: 62, `parse_failure`: 9, `regex_json_array`: 6
- Verbosity: 1/855 calls over 1024 output tokens (0.1%); 0 hit max_tokens (0.0%); 0 salvaged from truncated JSON (0.0%)
- Schema violations: 507
- Extra keys observed: end_text, sponsor

#### `nvidia/nemotron-nano-9b-v2`

- F1 (avg across episodes): **0.221**
- Total cost / episode: **$0.0813**
- p50 / p95 latency: 12.05s / 36.70s
- JSON compliance: 0.92
- JSON mode: native (100% native, 855 calls)
- Parse failure rate: 6.5%
- Extraction methods: `json_array_direct`: 770, `json_object_single_ad_truncated`: 15, `parse_failure`: 56, `regex_json_array`: 14
- Verbosity: 489/855 calls over 1024 output tokens (57.2%); 11 hit max_tokens (1.3%); 15 salvaged from truncated JSON (1.8%)
- Schema violations: 476
- Extra keys observed: end_text, sponsor

#### `meta-llama/llama-3.1-8b-instruct`

- F1 (avg across episodes): **0.198**
- Total cost / episode: **$0.0207**
- p50 / p95 latency: 0.79s / 4.04s
- JSON compliance: 0.85
- JSON mode: native (100% native, 855 calls)
- Parse failure rate: 0.1%
- Extraction methods: `json_array_direct`: 371, `json_object_no_ads`: 66, `json_object_single_ad`: 417, `parse_failure`: 1
- Verbosity: 26/855 calls over 1024 output tokens (3.0%); 0 hit max_tokens (0.0%); 0 salvaged from truncated JSON (0.0%)
- Schema violations: 1270
- Extra keys observed: end_text, sponsor

#### `cohere/command-r-plus-08-2024`

- F1 (avg across episodes): **0.102**
- Total cost / episode: **$2.5787**
- p50 / p95 latency: 0.95s / 3.45s
- JSON compliance: 0.98
- JSON mode: native (100% native, 855 calls)
- Parse failure rate: 0.0%
- Extraction methods: `json_object_ads_key`: 27, `json_object_no_ads`: 783, `json_object_single_ad`: 45
- Verbosity: 0/855 calls over 1024 output tokens (0.0%); 0 hit max_tokens (0.0%); 0 salvaged from truncated JSON (0.0%)
- Schema violations: 132
- Extra keys observed: end_text, sponsor

#### `openai/o4-mini`

- F1 (avg across episodes): **0.075**
- Total cost / episode: **$1.8999**
- p50 / p95 latency: 6.95s / 25.84s
- JSON compliance: 0.05
- JSON mode: native (100% native, 855 calls)
- Parse failure rate: 93.9%
- Extraction methods: `json_object_no_ads`: 19, `json_object_single_ad`: 33, `parse_failure`: 803
- Verbosity: 340/855 calls over 1024 output tokens (39.8%); 12 hit max_tokens (1.4%); 0 salvaged from truncated JSON (0.0%)
- Schema violations: 28
- Extra keys observed: end_text, sponsor

#### `microsoft/phi-4`

- F1 (avg across episodes): **0.055**
- Total cost / episode: **$0.0713**
- p50 / p95 latency: 2.29s / 11.55s
- JSON compliance: 0.86
- JSON mode: native (100% native, 855 calls)
- Parse failure rate: 1.1%
- Extraction methods: `json_array_direct`: 421, `json_object_ads_key`: 31, `json_object_no_ads`: 27, `json_object_segments_key`: 20, `json_object_single_ad`: 335, `json_object_window_segments`: 2, `parse_failure`: 9, `regex_json_array`: 10
- Verbosity: 19/855 calls over 1024 output tokens (2.2%); 12 hit max_tokens (1.4%); 0 salvaged from truncated JSON (0.0%)
- Schema violations: 412
- Extra keys observed: end_text, sponsor

#### `qwen/qwen3-8b`

- F1 (avg across episodes): **0.005**
- Total cost / episode: **$0.2578**
- p50 / p95 latency: 59.45s / 144.22s
- JSON compliance: 0.01
- JSON mode: native (100% native, 855 calls)
- Parse failure rate: 97.4%
- Extraction methods: `bracket_fallback`: 18, `json_array_direct`: 4, `parse_failure`: 833
- Verbosity: 588/855 calls over 1024 output tokens (68.8%); 119 hit max_tokens (13.9%); 0 salvaged from truncated JSON (0.0%)

#### `mistralai/mistral-7b-instruct-v0.1`

- F1 (avg across episodes): **0.000**
- Total cost / episode: **$0.0285**
- p50 / p95 latency: 7.11s / 33.05s
- JSON compliance: 0.16
- JSON mode: native (100% native, 855 calls)
- Parse failure rate: 59.5%
- Extraction methods: `bracket_fallback`: 1, `parse_failure`: 509, `regex_json_array`: 345
- Verbosity: 15/855 calls over 1024 output tokens (1.8%); 0 hit max_tokens (0.0%); 0 salvaged from truncated JSON (0.0%)


### Per-Episode Detail

One subsection per episode in the corpus, showing how every model performed on that specific episode. For ad-bearing episodes you see F1 and the stdev across trials (low stdev means stable, high stdev means the model's number on this episode is noisy). For the no-ad episode you see PASS / FAIL on the negative control: PASS = zero false positives across all windows, FAIL = the model flagged something that wasn't an ad, with the count.

#### `ep-ai-cloud-essentials-e8dc897fbd6b`: How Physical AI is Streamlining Engineering

- Podcast: ai-cloud-essentials
- Duration: 16.4 min
- Truth: no-ads episode

| Model | Result | FP count |
|-------|--------|----------|
| `openai/o4-mini` | PASS | 0 |
| `meta-llama/llama-3.1-8b-instruct` | PASS | 0 |
| `deepseek/deepseek-v4-pro` | PASS | 0 |
| `google/gemini-2.5-flash` | PASS | 0 |
| `qwen/qwen3-8b` | PASS | 0 |
| `claude-haiku-4-5-20251001` | PASS | 0 |
| `mistralai/mistral-large-2512` | PASS | 0 |
| `qwen/qwen3-14b` | PASS | 0 |
| `mistralai/codestral-2508` | PASS | 0 |
| `cohere/command-r-plus-08-2024` | PASS | 0 |
| `qwen/qwen3.6-plus` | PASS | 0 |
| `nvidia/llama-3.3-nemotron-super-49b-v1.5` | PASS | 0 |
| `qwen/qwen3.5-27b` | PASS | 0 |
| `meta-llama/llama-4-scout` | PASS | 0 |
| `meta-llama/llama-3.3-70b-instruct` | PASS | 0 |
| `mistralai/mistral-7b-instruct-v0.1` | PASS | 0 |
| `mistralai/mistral-medium-3.1` | PASS | 0 |
| `qwen/qwen3.5-plus-02-15` | PASS | 0 |
| `x-ai/grok-4.3` | PASS | 0 |
| `claude-sonnet-4-6` | PASS | 0 |
| `deepseek/deepseek-v3.2` | PASS | 0 |
| `claude-opus-4-7` | PASS | 0 |
| `openai/o3` | PASS | 0 |
| `google/gemini-3.5-flash` | PASS | 0 |
| `openai/gpt-oss-120b` | FAIL | 1 |
| `deepseek/deepseek-v4-flash` | FAIL | 1 |
| `deepseek/deepseek-r1` | FAIL | 1 |
| `qwen/qwen3.6-flash` | FAIL | 1 |
| `google/gemini-2.5-flash-lite` | FAIL | 1 |
| `google/gemini-3.1-flash-lite` | FAIL | 1 |
| `moonshotai/kimi-k2.6` | FAIL | 1 |
| `google/gemma-4-31b-it` | FAIL | 1 |
| `nvidia/nemotron-nano-9b-v2` | FAIL | 1 |
| `openai/gpt-5.4-mini` | FAIL | 1 |
| `google/gemini-2.5-pro` | FAIL | 1 |
| `meta-llama/llama-4-maverick` | FAIL | 1 |
| `openai/gpt-5.4` | FAIL | 1 |
| `openai/gpt-5.5` | FAIL | 1 |
| `qwen/qwen3-235b-a22b-2507` | FAIL | 2 |
| `deepseek/deepseek-r1-distill-llama-70b` | FAIL | 2 |
| `microsoft/phi-4` | FAIL | 3 |
| `openai/gpt-3.5-turbo` | FAIL | 3 |
| `cohere/command-a` | FAIL | 3 |
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
| `openai/gpt-5.4-mini` | 0.883 | 0.089 |
| `google/gemma-4-31b-it` | 0.879 | 0.097 |
| `openai/o3` | 0.876 | 0.137 |
| `claude-opus-4-7` | 0.857 | 0.000 |
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
| `google/gemini-2.5-flash` | 0.462 | 0.000 |
| `google/gemini-2.5-flash-lite` | 0.462 | 0.000 |
| `mistralai/mistral-medium-3.1` | 0.397 | 0.115 |
| `nvidia/nemotron-nano-9b-v2` | 0.334 | 0.117 |
| `meta-llama/llama-4-scout` | 0.253 | 0.167 |
| `qwen/qwen3-14b` | 0.190 | 0.294 |
| `meta-llama/llama-3.1-8b-instruct` | 0.164 | 0.157 |
| `microsoft/phi-4` | 0.157 | 0.089 |
| `openai/o4-mini` | 0.000 | 0.000 |
| `qwen/qwen3-8b` | 0.000 | 0.000 |
| `cohere/command-r-plus-08-2024` | 0.000 | 0.000 |
| `mistralai/mistral-7b-instruct-v0.1` | 0.000 | 0.000 |

#### `ep-daily-gist-chicago-70a82fe93a5c`: Suburban apartment market heats up

- Podcast: daily-gist-chicago
- Duration: 21.2 min
- Truth ads: 2

| Model | F1 | F1 stdev |
|-------|----|----------|
| `qwen/qwen3-235b-a22b-2507` | 0.853 | 0.145 |
| `openai/gpt-5.4-mini` | 0.833 | 0.236 |
| `google/gemini-2.5-flash` | 0.800 | 0.000 |
| `claude-haiku-4-5-20251001` | 0.800 | 0.000 |
| `google/gemini-3.1-flash-lite` | 0.800 | 0.000 |
| `claude-sonnet-4-6` | 0.800 | 0.183 |
| `qwen/qwen3.5-27b` | 0.727 | 0.186 |
| `meta-llama/llama-3.1-8b-instruct` | 0.700 | 0.183 |
| `qwen/qwen3.6-flash` | 0.700 | 0.245 |
| `deepseek/deepseek-r1` | 0.693 | 0.213 |
| `mistralai/codestral-2508` | 0.667 | 0.000 |
| `qwen/qwen3.6-plus` | 0.667 | 0.000 |
| `google/gemini-2.5-flash-lite` | 0.667 | 0.000 |
| `nvidia/llama-3.3-nemotron-super-49b-v1.5` | 0.667 | 0.000 |
| `mistralai/mistral-medium-3.1` | 0.667 | 0.000 |
| `mistralai/mistral-large-2512` | 0.648 | 0.043 |
| `deepseek/deepseek-r1-0528` | 0.647 | 0.228 |
| `openai/gpt-oss-120b` | 0.600 | 0.365 |
| `nvidia/nemotron-nano-9b-v2` | 0.600 | 0.365 |
| `qwen/qwen3.5-plus-02-15` | 0.600 | 0.091 |
| `deepseek/deepseek-v4-flash` | 0.587 | 0.335 |
| `meta-llama/llama-4-scout` | 0.587 | 0.335 |
| `x-ai/grok-4.3` | 0.533 | 0.075 |
| `openai/gpt-5.4` | 0.500 | 0.000 |
| `openai/gpt-5.5` | 0.500 | 0.000 |
| `google/gemma-4-31b-it` | 0.480 | 0.045 |
| `deepseek/deepseek-r1-distill-llama-70b` | 0.440 | 0.311 |
| `openai/gpt-3.5-turbo` | 0.420 | 0.045 |
| `google/gemini-2.5-pro` | 0.400 | 0.000 |
| `claude-opus-4-7` | 0.400 | 0.000 |
| `cohere/command-a` | 0.400 | 0.000 |
| `google/gemini-3.5-flash` | 0.400 | 0.000 |
| `deepseek/deepseek-v3.2` | 0.300 | 0.274 |
| `deepseek/deepseek-v4-pro` | 0.133 | 0.298 |
| `moonshotai/kimi-k2.6` | 0.100 | 0.224 |
| `meta-llama/llama-3.3-70b-instruct` | 0.100 | 0.224 |
| `openai/o4-mini` | 0.000 | 0.000 |
| `microsoft/phi-4` | 0.000 | 0.000 |
| `qwen/qwen3-8b` | 0.000 | 0.000 |
| `qwen/qwen3-14b` | 0.000 | 0.000 |
| `cohere/command-r-plus-08-2024` | 0.000 | 0.000 |
| `mistralai/mistral-7b-instruct-v0.1` | 0.000 | 0.000 |
| `openai/o3` | 0.000 | 0.000 |
| `meta-llama/llama-4-maverick` | 0.000 | 0.000 |

#### `ep-daily-tech-news-show-b576979e1fe8`: Motorola Razr Fold is a Noble Competitor to the Galaxy Z Fold 7 - DTNS 5269

- Podcast: daily-tech-news-show
- Duration: 34.6 min
- Truth ads: 4

| Model | F1 | F1 stdev |
|-------|----|----------|
| `openai/gpt-5.4-mini` | 0.943 | 0.078 |
| `qwen/qwen3.6-plus` | 0.914 | 0.078 |
| `moonshotai/kimi-k2.6` | 0.914 | 0.078 |
| `openai/gpt-5.4` | 0.892 | 0.062 |
| `openai/gpt-5.5` | 0.886 | 0.064 |
| `google/gemini-2.5-pro` | 0.864 | 0.089 |
| `claude-opus-4-7` | 0.863 | 0.014 |
| `qwen/qwen3.5-plus-02-15` | 0.857 | 0.000 |
| `google/gemini-3.5-flash` | 0.857 | 0.000 |
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
| `meta-llama/llama-3.3-70b-instruct` | 0.327 | 0.095 |
| `deepseek/deepseek-v3.2` | 0.327 | 0.211 |
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
| `microsoft/phi-4` | 0.000 | 0.000 |
| `qwen/qwen3-8b` | 0.000 | 0.000 |
| `cohere/command-r-plus-08-2024` | 0.000 | 0.000 |
| `mistralai/mistral-7b-instruct-v0.1` | 0.000 | 0.000 |

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
| `openai/gpt-3.5-turbo` | 0.364 | 0.000 |
| `deepseek/deepseek-v4-pro` | 0.350 | 0.229 |
| `cohere/command-r-plus-08-2024` | 0.313 | 0.155 |
| `deepseek/deepseek-v4-flash` | 0.310 | 0.043 |
| `cohere/command-a` | 0.298 | 0.090 |
| `claude-sonnet-4-6` | 0.237 | 0.037 |
| `claude-opus-4-7` | 0.231 | 0.082 |
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
| `qwen/qwen3-8b` | 0.000 | 0.000 |
| `meta-llama/llama-3.3-70b-instruct` | 0.000 | 0.000 |
| `mistralai/mistral-7b-instruct-v0.1` | 0.000 | 0.000 |

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
| `claude-opus-4-7` | 0.334 | 0.070 |
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
| `qwen/qwen3-8b` | 0.000 | 0.000 |
| `deepseek/deepseek-r1-distill-llama-70b` | 0.000 | 0.000 |
| `cohere/command-r-plus-08-2024` | 0.000 | 0.000 |
| `meta-llama/llama-4-scout` | 0.000 | 0.000 |
| `meta-llama/llama-3.3-70b-instruct` | 0.000 | 0.000 |
| `mistralai/mistral-7b-instruct-v0.1` | 0.000 | 0.000 |

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
| `microsoft/phi-4` | 0.000 | 0.000 |
| `qwen/qwen3-8b` | 0.000 | 0.000 |
| `cohere/command-r-plus-08-2024` | 0.000 | 0.000 |
| `mistralai/mistral-7b-instruct-v0.1` | 0.000 | 0.000 |

#### `ep-it-s-a-thing-e339179dfad6`: SOUP shots - It's a Thing 418

- Podcast: it-s-a-thing
- Duration: 26.7 min
- Truth ads: 1

| Model | F1 | F1 stdev |
|-------|----|----------|
| `qwen/qwen3.6-flash` | 0.667 | 0.000 |
| `qwen/qwen3.6-plus` | 0.667 | 0.000 |
| `google/gemini-2.5-pro` | 0.667 | 0.000 |
| `qwen/qwen3.5-plus-02-15` | 0.667 | 0.000 |
| `claude-opus-4-7` | 0.667 | 0.000 |
| `google/gemini-3.5-flash` | 0.667 | 0.000 |
| `openai/gpt-5.5` | 0.667 | 0.000 |
| `openai/gpt-5.4` | 0.613 | 0.119 |
| `qwen/qwen3.5-27b` | 0.600 | 0.365 |
| `openai/gpt-5.4-mini` | 0.500 | 0.289 |
| `google/gemma-4-31b-it` | 0.467 | 0.274 |
| `google/gemini-3.1-flash-lite` | 0.433 | 0.253 |
| `x-ai/grok-4.3` | 0.433 | 0.253 |
| `deepseek/deepseek-r1-0528` | 0.404 | 0.281 |
| `meta-llama/llama-3.1-8b-instruct` | 0.400 | 0.548 |
| `deepseek/deepseek-v3.2` | 0.400 | 0.548 |
| `cohere/command-a` | 0.400 | 0.000 |
| `deepseek/deepseek-v4-flash` | 0.337 | 0.239 |
| `openai/o3` | 0.333 | 0.471 |
| `deepseek/deepseek-r1` | 0.313 | 0.301 |
| `deepseek/deepseek-v4-pro` | 0.267 | 0.365 |
| `nvidia/llama-3.3-nemotron-super-49b-v1.5` | 0.233 | 0.325 |
| `openai/gpt-oss-120b` | 0.227 | 0.209 |
| `moonshotai/kimi-k2.6` | 0.200 | 0.274 |
| `openai/o4-mini` | 0.000 | 0.000 |
| `microsoft/phi-4` | 0.000 | 0.000 |
| `qwen/qwen3-235b-a22b-2507` | 0.000 | 0.000 |
| `google/gemini-2.5-flash` | 0.000 | 0.000 |
| `qwen/qwen3-8b` | 0.000 | 0.000 |
| `claude-haiku-4-5-20251001` | 0.000 | 0.000 |
| `mistralai/mistral-large-2512` | 0.000 | 0.000 |
| `deepseek/deepseek-r1-distill-llama-70b` | 0.000 | 0.000 |
| `qwen/qwen3-14b` | 0.000 | 0.000 |
| `mistralai/codestral-2508` | 0.000 | 0.000 |
| `cohere/command-r-plus-08-2024` | 0.000 | 0.000 |
| `openai/gpt-3.5-turbo` | 0.000 | 0.000 |
| `google/gemini-2.5-flash-lite` | 0.000 | 0.000 |
| `meta-llama/llama-4-scout` | 0.000 | 0.000 |
| `meta-llama/llama-3.3-70b-instruct` | 0.000 | 0.000 |
| `nvidia/nemotron-nano-9b-v2` | 0.000 | 0.000 |
| `mistralai/mistral-7b-instruct-v0.1` | 0.000 | 0.000 |
| `mistralai/mistral-medium-3.1` | 0.000 | 0.000 |
| `claude-sonnet-4-6` | 0.000 | 0.000 |
| `meta-llama/llama-4-maverick` | 0.000 | 0.000 |

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
| `meta-llama/llama-4-maverick` | 0.571 | 0.000 |
| `google/gemini-3.5-flash` | 0.571 | 0.000 |
| `openai/gpt-5.5` | 0.571 | 0.000 |
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
| `google/gemini-2.5-flash` | 0.444 | 0.000 |
| `mistralai/mistral-large-2512` | 0.444 | 0.000 |
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
| `openai/o4-mini` | 0.133 | 0.298 |
| `meta-llama/llama-3.3-70b-instruct` | 0.133 | 0.298 |
| `meta-llama/llama-4-scout` | 0.094 | 0.130 |
| `qwen/qwen3-8b` | 0.000 | 0.000 |
| `cohere/command-r-plus-08-2024` | 0.000 | 0.000 |
| `mistralai/mistral-7b-instruct-v0.1` | 0.000 | 0.000 |

#### `ep-oxide-and-friends-ce789ff5b62e`: Mechanical Engineering at Oxide [chapter images]

- Podcast: oxide-and-friends
- Duration: 84.5 min
- Truth: no-ads episode

| Model | Result | FP count |
|-------|--------|----------|
| `openai/o4-mini` | PASS | 0 |
| `meta-llama/llama-3.1-8b-instruct` | PASS | 0 |
| `deepseek/deepseek-v4-pro` | PASS | 0 |
| `google/gemini-2.5-flash` | PASS | 0 |
| `qwen/qwen3-8b` | PASS | 0 |
| `openai/gpt-oss-120b` | PASS | 0 |
| `deepseek/deepseek-v4-flash` | PASS | 0 |
| `claude-haiku-4-5-20251001` | PASS | 0 |
| `mistralai/mistral-large-2512` | PASS | 0 |
| `qwen/qwen3.6-flash` | PASS | 0 |
| `mistralai/codestral-2508` | PASS | 0 |
| `cohere/command-r-plus-08-2024` | PASS | 0 |
| `qwen/qwen3.6-plus` | PASS | 0 |
| `google/gemini-2.5-flash-lite` | PASS | 0 |
| `google/gemini-3.1-flash-lite` | PASS | 0 |
| `nvidia/llama-3.3-nemotron-super-49b-v1.5` | PASS | 0 |
| `qwen/qwen3.5-27b` | PASS | 0 |
| `meta-llama/llama-4-scout` | PASS | 0 |
| `meta-llama/llama-3.3-70b-instruct` | PASS | 0 |
| `google/gemma-4-31b-it` | PASS | 0 |
| `nvidia/nemotron-nano-9b-v2` | PASS | 0 |
| `mistralai/mistral-7b-instruct-v0.1` | PASS | 0 |
| `mistralai/mistral-medium-3.1` | PASS | 0 |
| `qwen/qwen3.5-plus-02-15` | PASS | 0 |
| `x-ai/grok-4.3` | PASS | 0 |
| `claude-sonnet-4-6` | PASS | 0 |
| `claude-opus-4-7` | PASS | 0 |
| `openai/o3` | PASS | 0 |
| `cohere/command-a` | PASS | 0 |
| `meta-llama/llama-4-maverick` | PASS | 0 |
| `google/gemini-3.5-flash` | PASS | 0 |
| `openai/gpt-5.5` | PASS | 0 |
| `deepseek/deepseek-r1` | FAIL | 1 |
| `qwen/qwen3-14b` | FAIL | 1 |
| `openai/gpt-5.4-mini` | FAIL | 1 |
| `google/gemini-2.5-pro` | FAIL | 1 |
| `openai/gpt-5.4` | FAIL | 1 |
| `deepseek/deepseek-v3.2` | FAIL | 2 |
| `moonshotai/kimi-k2.6` | FAIL | 4 |
| `qwen/qwen3-235b-a22b-2507` | FAIL | 6 |
| `deepseek/deepseek-r1-distill-llama-70b` | FAIL | 10 |
| `openai/gpt-3.5-turbo` | FAIL | 10 |
| `deepseek/deepseek-r1-0528` | FAIL | 12 |
| `microsoft/phi-4` | FAIL | 15 |

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
| `qwen/qwen3.5-plus-02-15` | 0.476 | 0.000 |
| `google/gemini-3.5-flash` | 0.476 | 0.000 |
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
| `qwen/qwen3-8b` | 0.000 | 0.000 |
| `mistralai/mistral-7b-instruct-v0.1` | 0.000 | 0.000 |

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
| `meta-llama/llama-3.1-8b-instruct` | 0.000 | 0.000 |
| `qwen/qwen3-8b` | 0.000 | 0.000 |
| `cohere/command-r-plus-08-2024` | 0.000 | 0.000 |
| `mistralai/mistral-7b-instruct-v0.1` | 0.000 | 0.000 |

#### `ep-the-tim-dillon-show-f62bd5fa1cfe`: 495 - Hantavirus Cruise & iPad Babies

- Podcast: the-tim-dillon-show
- Duration: 80.1 min
- Truth ads: 6

| Model | F1 | F1 stdev |
|-------|----|----------|
| `qwen/qwen3.5-plus-02-15` | 0.636 | 0.028 |
| `qwen/qwen3.6-plus` | 0.615 | 0.000 |
| `claude-opus-4-7` | 0.592 | 0.052 |
| `google/gemini-3.5-flash` | 0.592 | 0.052 |
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
| `qwen/qwen3-8b` | 0.057 | 0.128 |
| `cohere/command-r-plus-08-2024` | 0.057 | 0.128 |
| `meta-llama/llama-4-scout` | 0.053 | 0.049 |
| `meta-llama/llama-3.1-8b-instruct` | 0.049 | 0.019 |
| `nvidia/nemotron-nano-9b-v2` | 0.044 | 0.061 |
| `mistralai/mistral-large-2512` | 0.044 | 0.025 |
| `openai/o4-mini` | 0.000 | 0.000 |
| `mistralai/mistral-7b-instruct-v0.1` | 0.000 | 0.000 |

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
| `claude-haiku-4-5-20251001` | 0.375 | 0.000 |
| `google/gemini-3.1-flash-lite` | 0.375 | 0.000 |
| `claude-sonnet-4-6` | 0.375 | 0.000 |
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
| `microsoft/phi-4` | 0.000 | 0.000 |
| `qwen/qwen3-8b` | 0.000 | 0.000 |
| `mistralai/mistral-7b-instruct-v0.1` | 0.000 | 0.000 |


### Parser stress test

How each model's responses were actually parsed. Columns are extraction methods, ordered alphabetically; rows are models, sorted by parse-failure rate (cleanest at top). `json_array_direct` is the happy path: a bare JSON array we could `json.loads` and process immediately. `markdown_code_block` means we had to strip triple-backtick fences first; `json_object_*` means the model wrapped the array in an outer object and we had to find the array key; `regex_*` are last-resort recovery paths. A model that needs anything but `json_array_direct` for most calls is fragile. It works today, but a small prompt change can break the parser.

| Model | bracket_fallback | json_array_direct | json_object_ads_key | json_object_no_ads | json_object_segments_key | json_object_single_ad | json_object_single_ad_truncated | json_object_window_segments | markdown_code_block | parse_failure | regex_json_array |
|---|---|---|---|---|---|---|---|---|---|---|---|
| `qwen/qwen3-235b-a22b-2507` | 0 | 173 | 1 | 94 | 0 | 587 | 0 | 0 | 0 | 0 | 0 |
| `google/gemini-2.5-flash` | 0 | 855 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| `claude-haiku-4-5-20251001` | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 855 | 0 | 0 |
| `mistralai/mistral-large-2512` | 0 | 855 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| `qwen/qwen3.6-flash` | 0 | 855 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| `mistralai/codestral-2508` | 0 | 855 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| `cohere/command-r-plus-08-2024` | 0 | 0 | 27 | 783 | 0 | 45 | 0 | 0 | 0 | 0 | 0 |
| `qwen/qwen3.6-plus` | 0 | 855 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| `google/gemini-3.1-flash-lite` | 0 | 799 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 56 |
| `mistralai/mistral-medium-3.1` | 0 | 854 | 0 | 0 | 0 | 0 | 1 | 0 | 0 | 0 | 0 |
| `openai/gpt-5.4-mini` | 0 | 0 | 2 | 300 | 2 | 551 | 0 | 0 | 0 | 0 | 0 |
| `claude-sonnet-4-6` | 0 | 783 | 0 | 0 | 0 | 0 | 0 | 0 | 57 | 0 | 15 |
| `deepseek/deepseek-v3.2` | 0 | 501 | 16 | 3 | 0 | 335 | 0 | 0 | 0 | 0 | 0 |
| `claude-opus-4-7` | 0 | 850 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 5 |
| `cohere/command-a` | 0 | 0 | 0 | 29 | 0 | 826 | 0 | 0 | 0 | 0 | 0 |
| `meta-llama/llama-4-maverick` | 0 | 0 | 0 | 315 | 0 | 540 | 0 | 0 | 0 | 0 | 0 |
| `google/gemini-3.5-flash` | 0 | 850 | 0 | 0 | 0 | 0 | 1 | 0 | 0 | 0 | 4 |
| `openai/gpt-5.4` | 0 | 0 | 0 | 326 | 0 | 529 | 0 | 0 | 0 | 0 | 0 |
| `meta-llama/llama-3.1-8b-instruct` | 0 | 371 | 0 | 66 | 0 | 417 | 0 | 0 | 0 | 1 | 0 |
| `google/gemma-4-31b-it` | 1 | 0 | 455 | 216 | 0 | 180 | 2 | 0 | 0 | 1 | 0 |
| `x-ai/grok-4.3` | 0 | 854 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 1 | 0 |
| `openai/gpt-5.5` | 0 | 0 | 0 | 494 | 0 | 360 | 0 | 0 | 0 | 1 | 0 |
| `qwen/qwen3.5-plus-02-15` | 0 | 851 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 1 | 0 |
| `openai/gpt-3.5-turbo` | 0 | 0 | 0 | 50 | 0 | 800 | 0 | 0 | 0 | 5 | 0 |
| `openai/o3` | 0 | 0 | 34 | 621 | 12 | 183 | 0 | 0 | 0 | 5 | 0 |
| `deepseek/deepseek-r1` | 0 | 760 | 2 | 17 | 7 | 44 | 0 | 0 | 17 | 6 | 2 |
| `microsoft/phi-4` | 0 | 421 | 31 | 27 | 20 | 335 | 0 | 2 | 0 | 9 | 10 |
| `meta-llama/llama-4-scout` | 37 | 5 | 644 | 92 | 0 | 62 | 0 | 0 | 0 | 9 | 6 |
| `google/gemini-2.5-pro` | 0 | 818 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 12 | 25 |
| `deepseek/deepseek-r1-distill-llama-70b` | 0 | 20 | 68 | 104 | 0 | 642 | 5 | 0 | 0 | 15 | 1 |
| `deepseek/deepseek-v4-flash` | 0 | 102 | 435 | 21 | 7 | 272 | 0 | 0 | 0 | 17 | 1 |
| `google/gemini-2.5-flash-lite` | 0 | 793 | 0 | 0 | 0 | 0 | 43 | 0 | 0 | 19 | 0 |
| `deepseek/deepseek-v4-pro` | 0 | 229 | 63 | 131 | 314 | 86 | 2 | 0 | 5 | 23 | 2 |
| `nvidia/nemotron-nano-9b-v2` | 0 | 770 | 0 | 0 | 0 | 0 | 15 | 0 | 0 | 56 | 14 |
| `deepseek/deepseek-r1-0528` | 0 | 700 | 34 | 3 | 0 | 30 | 3 | 0 | 2 | 83 | 0 |
| `qwen/qwen3.5-27b` | 0 | 633 | 0 | 89 | 0 | 12 | 0 | 0 | 0 | 121 | 0 |
| `nvidia/llama-3.3-nemotron-super-49b-v1.5` | 0 | 441 | 0 | 0 | 0 | 0 | 3 | 0 | 257 | 124 | 30 |
| `openai/gpt-oss-120b` | 0 | 68 | 235 | 186 | 0 | 188 | 6 | 0 | 0 | 149 | 23 |
| `moonshotai/kimi-k2.6` | 0 | 68 | 35 | 109 | 2 | 397 | 0 | 0 | 5 | 239 | 0 |
| `meta-llama/llama-3.3-70b-instruct` | 0 | 143 | 1 | 144 | 0 | 264 | 0 | 0 | 0 | 300 | 3 |
| `mistralai/mistral-7b-instruct-v0.1` | 1 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 509 | 345 |
| `qwen/qwen3-14b` | 0 | 0 | 0 | 1 | 0 | 338 | 0 | 0 | 0 | 516 | 0 |
| `openai/o4-mini` | 0 | 0 | 0 | 19 | 0 | 33 | 0 | 0 | 0 | 803 | 0 |
| `qwen/qwen3-8b` | 18 | 4 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 833 | 0 |

## Methodology

Reproducibility settings used for this run. The benchmark sends the same prompts MinusPod sends in production (same system prompt, same sponsor list, same windowing) so the F1 numbers here are directly relevant to production accuracy decisions. Cost is recomputed at report time from token counts against the active pricing snapshot, so all rows compare at the same prices regardless of when the actual call ran.

- Trials per (model, episode): **5**, temperature 0.0
- max_tokens: 4096 (matches MinusPod production)
- response_format: json_object (with prompt-injection fallback when provider rejects native)
- Window size: 10 min, overlap: 3 min (imported from MinusPod's create_windows)
- Pricing snapshot: 2026-05-20T19:59:56.229629Z
- Corpus episodes: 14

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

- Report generated: 2026-05-22T21:01:58Z
- Unique work units (current state, last-write-wins after retries): 37620
- Raw rows in calls.jsonl: 38251 (631 superseded by later retries; kept for audit)
- Successful: 37617
- Failed: 3
- Lifetime actual spend (sum of at-runtime costs, includes superseded rows): $299.3819
- Active pricing snapshot: 2026-05-20T19:59:56.229629Z
