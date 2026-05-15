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

### Glossary

- **IoU (intersection over union)**: how much two time ranges overlap, expressed as `(overlap) / (union)`. 0 means no overlap, 1 means identical ranges. We use IoU >= 0.5 as the threshold for a predicted ad to count as matching a truth ad.
- **Trial**: each (model, episode) pair runs 5 trials at temperature 0.0 to surface non-determinism. F1 numbers in tables are averaged across trials.
- **Window**: each episode is split into ~85-second sliding windows; the model judges each window independently. Per-window predictions are stitched together for episode-level scoring.
- **Schema violations**: number of times the response had at least one missing-required-field, wrong-type, or extra-key issue. Doesn't tank F1, but signals brittleness.
- **Extraction method**: the route the parser took to recover the ad list. `json_array_direct` is the cleanest; method names with `regex_*` mean the JSON itself was malformed and we fell back to text matching.


## TL;DR

### Best Accuracy (F1 @ IoU >= 0.5)

All models ranked by F1 against human-verified ground truth. Cost includes free-tier models (shown at $0.00).

| Rank | Model | F1 | Cost / episode | p50 latency | JSON compliance |
|------|-------|----|----------------|-------------|-----------------|
| 1 | `qwen/qwen3.5-plus-02-15` | 0.649 | $0.0000 | 49.2s | 1.00 |
| 2 | `openai/gpt-5.5` | 0.636 | $4.6613 | 6.2s | 0.87 |
| 3 | `claude-opus-4-7` | 0.618 | $5.5394 | 2.3s | 1.00 |
| 4 | `openai/gpt-5.4` | 0.605 | $1.8008 | 1.8s | 0.80 |
| 5 | `google/gemini-2.5-pro` | 0.589 | $2.7901 | 13.7s | 0.97 |
| 6 | `openai/o3` | 0.576 | $2.1834 | 8.1s | 0.93 |
| 7 | `x-ai/grok-4.3` | 0.489 | $1.0593 | 3.3s | 1.00 |
| 8 | `deepseek/deepseek-v4-flash` | 0.464 | $0.0000 | 3.0s | 0.80 |
| 9 | `google/gemma-4-31b-it` | 0.463 | $0.0000 | 1.8s | 0.86 |
| 10 | `moonshotai/kimi-k2.6` | 0.456 | $2.0174 | 35.1s | 0.59 |
| 11 | `deepseek/deepseek-r1` | 0.438 | $4.4082 | 19.2s | 0.96 |
| 12 | `deepseek/deepseek-r1-0528` | 0.398 | $0.2395 | 14.9s | 0.89 |
| 13 | `cohere/command-a` | 0.395 | $0.0000 | 4.0s | 0.71 |
| 14 | `meta-llama/llama-4-maverick` | 0.390 | $0.0000 | 1.2s | 0.79 |
| 15 | `claude-sonnet-4-6` | 0.377 | $2.5061 | 1.6s | 0.96 |
| 16 | `nvidia/llama-3.3-nemotron-super-49b-v1.5` | 0.375 | $0.0000 | 22.8s | 0.71 |
| 17 | `mistralai/mistral-medium-3.1` | 0.367 | $0.0000 | 0.9s | 1.00 |
| 18 | `claude-haiku-4-5-20251001` | 0.340 | $0.8508 | 1.3s | 0.60 |
| 19 | `meta-llama/llama-3.3-70b-instruct` | 0.298 | $0.4599 | 1.5s | 0.67 |
| 20 | `mistralai/codestral-2508` | 0.284 | $0.2296 | 0.8s | 1.00 |
| 21 | `deepseek/deepseek-v3.2` | 0.279 | $0.4507 | 2.2s | 0.92 |
| 22 | `google/gemini-2.5-flash` | 0.246 | $0.2425 | 0.9s | 1.00 |
| 23 | `openai/gpt-3.5-turbo` | 0.221 | $0.3621 | 1.3s | 0.70 |
| 24 | `meta-llama/llama-4-scout` | 0.220 | $0.0000 | 0.8s | 0.81 |
| 25 | `deepseek/deepseek-r1-distill-llama-70b` | 0.206 | $0.5333 | 2.0s | 0.74 |
| 26 | `mistralai/mistral-large-2512` | 0.202 | $0.3977 | 2.7s | 1.00 |
| 27 | `nvidia/nemotron-nano-9b-v2` | 0.186 | $0.0000 | 12.3s | 0.90 |
| 28 | `meta-llama/llama-3.1-8b-instruct` | 0.166 | $0.1430 | 0.8s | 0.86 |
| 29 | `cohere/command-r-plus-08-2024` | 0.136 | $1.8262 | 1.0s | 0.97 |
| 30 | `openai/o4-mini` | 0.095 | $1.3462 | 7.2s | 0.05 |
| 31 | `microsoft/phi-4` | 0.050 | $0.1051 | 2.3s | 0.86 |
| 32 | `mistralai/mistral-7b-instruct-v0.1` | 0.000 | $0.0000 | 8.6s | 0.11 |

### Best Value (F1 per dollar)

Paid-tier only. Free-tier models are excluded here because F1 / 0 is undefined; they are ranked separately under Best Free-Tier below.

| Rank | Model | F1/$ | F1 | Cost / episode |
|------|-------|------|----|----------------|
| 1 | `deepseek/deepseek-r1-0528` | 1.66 | 0.398 | $0.2395 |
| 2 | `mistralai/codestral-2508` | 1.24 | 0.284 | $0.2296 |
| 3 | `meta-llama/llama-3.1-8b-instruct` | 1.16 | 0.166 | $0.1430 |
| 4 | `google/gemini-2.5-flash` | 1.02 | 0.246 | $0.2425 |
| 5 | `meta-llama/llama-3.3-70b-instruct` | 0.65 | 0.298 | $0.4599 |
| 6 | `deepseek/deepseek-v3.2` | 0.62 | 0.279 | $0.4507 |
| 7 | `openai/gpt-3.5-turbo` | 0.61 | 0.221 | $0.3621 |
| 8 | `mistralai/mistral-large-2512` | 0.51 | 0.202 | $0.3977 |
| 9 | `microsoft/phi-4` | 0.47 | 0.050 | $0.1051 |
| 10 | `x-ai/grok-4.3` | 0.46 | 0.489 | $1.0593 |
| 11 | `claude-haiku-4-5-20251001` | 0.40 | 0.340 | $0.8508 |
| 12 | `deepseek/deepseek-r1-distill-llama-70b` | 0.39 | 0.206 | $0.5333 |
| 13 | `openai/gpt-5.4` | 0.34 | 0.605 | $1.8008 |
| 14 | `openai/o3` | 0.26 | 0.576 | $2.1834 |
| 15 | `moonshotai/kimi-k2.6` | 0.23 | 0.456 | $2.0174 |
| 16 | `google/gemini-2.5-pro` | 0.21 | 0.589 | $2.7901 |
| 17 | `claude-sonnet-4-6` | 0.15 | 0.377 | $2.5061 |
| 18 | `openai/gpt-5.5` | 0.14 | 0.636 | $4.6613 |
| 19 | `claude-opus-4-7` | 0.11 | 0.618 | $5.5394 |
| 20 | `deepseek/deepseek-r1` | 0.10 | 0.438 | $4.4082 |
| 21 | `cohere/command-r-plus-08-2024` | 0.07 | 0.136 | $1.8262 |
| 22 | `openai/o4-mini` | 0.07 | 0.095 | $1.3462 |

### Best Free-Tier (F1)

Models that came back at $0.00 cost. F1 / $ is undefined for these, so they are ranked by F1 alone. Free-tier eligibility on OpenRouter depends on the attribution headers wired into the benchmark (`HTTP-Referer`, `X-Title`); a model showing as free here may bill on your own deployment if those headers are missing.

| Rank | Model | F1 | p50 latency | JSON compliance |
|------|-------|----|-------------|-----------------|
| 1 | `qwen/qwen3.5-plus-02-15` | 0.649 | 49.2s | 1.00 |
| 2 | `deepseek/deepseek-v4-flash` | 0.464 | 3.0s | 0.80 |
| 3 | `google/gemma-4-31b-it` | 0.463 | 1.8s | 0.86 |
| 4 | `cohere/command-a` | 0.395 | 4.0s | 0.71 |
| 5 | `meta-llama/llama-4-maverick` | 0.390 | 1.2s | 0.79 |
| 6 | `nvidia/llama-3.3-nemotron-super-49b-v1.5` | 0.375 | 22.8s | 0.71 |
| 7 | `mistralai/mistral-medium-3.1` | 0.367 | 0.9s | 1.00 |
| 8 | `meta-llama/llama-4-scout` | 0.220 | 0.8s | 0.81 |
| 9 | `nvidia/nemotron-nano-9b-v2` | 0.186 | 12.3s | 0.90 |
| 10 | `mistralai/mistral-7b-instruct-v0.1` | 0.000 | 8.6s | 0.11 |

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

**165 call(s) failed out of 20295 total (0.81%).** Failures are excluded from F1 / cost calculations, but they often surface real production-relevant gotchas worth knowing.

### By category

Errors classified into coarse buckets so failure patterns are visible at a glance. A model showing up here doesn't mean it's broken. Some categories are provider-side (content moderation, rate limits) and tell you more about routing reliability than model quality.

| Category | Calls | Affected models |
|----------|------:|-----------------|
| Unknown model (404) | 165 | `x-ai/grok-4.1-fast` |

### Per-model error count

Same errors grouped by model, with the failure rate as a fraction of that model's total calls. Rates under 1% are usually one-off provider hiccups; rates above 5% suggest the model isn't operationally viable for production with the current prompts and concurrency caps.

| Model | Errors | of total |
|---|---:|---:|
| `x-ai/grok-4.1-fast` | 165 | 165/615 (26.8%) |

### Sample messages (first 3 per category)

First three raw error messages per category, so you can see what the provider actually returned without grepping calls.jsonl. Messages are truncated to ~240 characters; full text lives in `results/raw/calls.jsonl`.

**Unknown model (404)** (165)
- `x-ai/grok-4.1-fast` on `ep-daily-tech-news-show-b576979e1fe8` (trial 0, window 0): Error code: 404 - {'error': {'message': 'Grok 4.1 Fast is deprecated. xAI recommends switching to Grok 4.3 (https://openrouter.ai/x-ai/grok-4.3)', 'code': 404}, 'user_id': 'user_3Axgz92LiCKJYG9UjIpfkpo3ZL8'}
- `x-ai/grok-4.1-fast` on `ep-daily-tech-news-show-b576979e1fe8` (trial 0, window 1): Error code: 404 - {'error': {'message': 'Grok 4.1 Fast is deprecated. xAI recommends switching to Grok 4.3 (https://openrouter.ai/x-ai/grok-4.3)', 'code': 404}, 'user_id': 'user_3Axgz92LiCKJYG9UjIpfkpo3ZL8'}
- `x-ai/grok-4.1-fast` on `ep-daily-tech-news-show-b576979e1fe8` (trial 0, window 2): Error code: 404 - {'error': {'message': 'Grok 4.1 Fast is deprecated. xAI recommends switching to Grok 4.3 (https://openrouter.ai/x-ai/grok-4.3)', 'code': 404}, 'user_id': 'user_3Axgz92LiCKJYG9UjIpfkpo3ZL8'}
- ... and 162 more

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
| `qwen/qwen3.5-plus-02-15` | 0.562 | 0.832 | 142 | 138 | 38 |
| `openai/gpt-5.5` | 0.580 | 0.774 | 128 | 111 | 52 |
| `claude-opus-4-7` | 0.538 | 0.791 | 133 | 138 | 47 |
| `openai/gpt-5.4` | 0.531 | 0.795 | 133 | 152 | 47 |
| `google/gemini-2.5-pro` | 0.486 | 0.839 | 142 | 188 | 38 |
| `openai/o3` | 0.751 | 0.523 | 90 | 34 | 90 |
| `x-ai/grok-4.3` | 0.366 | 0.776 | 133 | 281 | 47 |
| `deepseek/deepseek-v4-flash` | 0.335 | 0.801 | 138 | 311 | 42 |
| `google/gemma-4-31b-it` | 0.357 | 0.719 | 119 | 278 | 61 |
| `moonshotai/kimi-k2.6` | 0.520 | 0.511 | 79 | 85 | 101 |
| `deepseek/deepseek-r1` | 0.331 | 0.697 | 120 | 282 | 60 |
| `deepseek/deepseek-r1-0528` | 0.319 | 0.716 | 120 | 452 | 60 |
| `cohere/command-a` | 0.319 | 0.670 | 107 | 282 | 73 |
| `meta-llama/llama-4-maverick` | 0.325 | 0.572 | 102 | 213 | 78 |
| `claude-sonnet-4-6` | 0.286 | 0.591 | 108 | 309 | 72 |
| `nvidia/llama-3.3-nemotron-super-49b-v1.5` | 0.340 | 0.487 | 84 | 163 | 96 |
| `mistralai/mistral-medium-3.1` | 0.287 | 0.561 | 102 | 332 | 78 |
| `claude-haiku-4-5-20251001` | 0.235 | 0.626 | 115 | 436 | 65 |
| `meta-llama/llama-3.3-70b-instruct` | 0.296 | 0.366 | 75 | 206 | 105 |
| `mistralai/codestral-2508` | 0.212 | 0.486 | 93 | 383 | 87 |
| `deepseek/deepseek-v3.2` | 0.283 | 0.311 | 54 | 130 | 126 |
| `google/gemini-2.5-flash` | 0.163 | 0.511 | 95 | 545 | 85 |
| `openai/gpt-3.5-turbo` | 0.165 | 0.437 | 75 | 396 | 105 |
| `meta-llama/llama-4-scout` | 0.181 | 0.339 | 69 | 354 | 111 |
| `deepseek/deepseek-r1-distill-llama-70b` | 0.180 | 0.348 | 64 | 320 | 116 |
| `mistralai/mistral-large-2512` | 0.128 | 0.548 | 98 | 787 | 82 |
| `nvidia/nemotron-nano-9b-v2` | 0.151 | 0.267 | 48 | 334 | 132 |
| `meta-llama/llama-3.1-8b-instruct` | 0.155 | 0.245 | 43 | 665 | 137 |
| `cohere/command-r-plus-08-2024` | 0.171 | 0.136 | 34 | 51 | 146 |
| `openai/o4-mini` | 0.222 | 0.063 | 11 | 19 | 169 |
| `microsoft/phi-4` | 0.049 | 0.064 | 11 | 331 | 169 |
| `mistralai/mistral-7b-instruct-v0.1` | 0.000 | 0.000 | 0 | 0 | 180 |

## Boundary accuracy

For ads that match the truth at IoU >= 0.5, how far off were the predicted start and end timestamps? Lower is better. A model can hit F1 cleanly while still being 20s off on every boundary. Bad for any pipeline that cuts the audio.

| Model | Start MAE (s) | End MAE (s) |
|---|---:|---:|
| `qwen/qwen3.5-plus-02-15` | 6.87 | 0.68 |
| `google/gemini-2.5-pro` | 6.41 | 2.27 |
| `deepseek/deepseek-v3.2` | 5.62 | 3.33 |
| `deepseek/deepseek-r1` | 5.08 | 4.15 |
| `claude-sonnet-4-6` | 2.70 | 6.86 |
| `x-ai/grok-4.3` | 4.34 | 5.75 |
| `openai/o4-mini` | 4.81 | 5.29 |
| `claude-haiku-4-5-20251001` | 3.05 | 7.30 |
| `openai/gpt-5.5` | 8.80 | 2.05 |
| `nvidia/llama-3.3-nemotron-super-49b-v1.5` | 6.25 | 4.63 |
| `google/gemini-2.5-flash` | 5.79 | 5.39 |
| `deepseek/deepseek-r1-0528` | 6.99 | 4.21 |
| `openai/gpt-5.4` | 9.36 | 2.57 |
| `cohere/command-a` | 6.10 | 5.84 |
| `meta-llama/llama-4-scout` | 4.61 | 7.49 |
| `claude-opus-4-7` | 9.15 | 3.36 |
| `mistralai/mistral-large-2512` | 7.17 | 5.75 |
| `moonshotai/kimi-k2.6` | 11.52 | 1.87 |
| `mistralai/codestral-2508` | 3.26 | 10.16 |
| `openai/o3` | 10.81 | 2.65 |
| `deepseek/deepseek-r1-distill-llama-70b` | 2.79 | 11.25 |
| `meta-llama/llama-3.3-70b-instruct` | 4.15 | 11.68 |
| `meta-llama/llama-4-maverick` | 5.26 | 10.67 |
| `microsoft/phi-4` | 6.05 | 10.46 |
| `google/gemma-4-31b-it` | 12.40 | 5.12 |
| `mistralai/mistral-medium-3.1` | 9.25 | 8.86 |
| `cohere/command-r-plus-08-2024` | 6.29 | 12.19 |
| `deepseek/deepseek-v4-flash` | 9.11 | 9.94 |
| `openai/gpt-3.5-turbo` | 8.47 | 11.54 |
| `nvidia/nemotron-nano-9b-v2` | 17.15 | 4.01 |
| `meta-llama/llama-3.1-8b-instruct` | 15.79 | 9.44 |

## Confidence calibration

Models include a self-reported `confidence` on each detected ad. A well-calibrated model should be right ~95% of the time when it claims 0.95 confidence. The table below bins each model's predictions and shows the actual hit rate (fraction that were true positives at IoU >= 0.5). A bin near 1.0 is well-calibrated; a low number with a high count means the model is overconfident.

| Model | 0.00-0.70 | 0.70-0.90 | 0.90-0.95 | 0.95-0.99 | 0.99+ | total |
|---|---:|---:|---:|---:|---:|---:|
| `claude-haiku-4-5-20251001` | -- | 0.00 (n=25) | 0.17 (n=160) | 0.24 (n=366) | -- | 551 |
| `claude-opus-4-7` | 0.00 (n=2) | 0.00 (n=6) | 0.00 (n=3) | 0.50 (n=237) | 0.65 (n=23) | 271 |
| `claude-sonnet-4-6` | -- | 0.00 (n=34) | 0.27 (n=59) | 0.27 (n=291) | 0.42 (n=33) | 417 |
| `cohere/command-a` | -- | 0.00 (n=23) | -- | 0.28 (n=377) | 0.00 (n=1) | 401 |
| `cohere/command-r-plus-08-2024` | -- | -- | 0.00 (n=1) | 0.27 (n=15) | 0.43 (n=69) | 85 |
| `deepseek/deepseek-r1` | -- | 0.00 (n=3) | 0.12 (n=8) | 0.29 (n=306) | 0.34 (n=91) | 408 |
| `deepseek/deepseek-r1-0528` | 0.00 (n=2) | 0.00 (n=7) | 0.00 (n=30) | 0.11 (n=420) | 0.46 (n=162) | 621 |
| `deepseek/deepseek-r1-distill-llama-70b` | -- | 0.01 (n=151) | -- | 0.22 (n=287) | -- | 438 |
| `deepseek/deepseek-v3.2` | -- | 0.00 (n=1) | 0.00 (n=2) | 0.14 (n=88) | 0.42 (n=99) | 190 |
| `deepseek/deepseek-v4-flash` | 0.00 (n=2) | 0.25 (n=4) | 0.20 (n=5) | 0.26 (n=281) | 0.39 (n=158) | 450 |
| `google/gemini-2.5-flash` | -- | -- | 0.00 (n=50) | 0.17 (n=448) | 0.14 (n=142) | 640 |
| `google/gemini-2.5-pro` | -- | 0.00 (n=12) | 0.00 (n=25) | 0.34 (n=88) | 0.53 (n=211) | 336 |
| `google/gemma-4-31b-it` | -- | 0.12 (n=8) | 0.14 (n=22) | 0.20 (n=163) | 0.39 (n=209) | 402 |
| `meta-llama/llama-3.1-8b-instruct` | -- | 0.00 (n=5) | 0.00 (n=2) | 0.06 (n=701) | -- | 708 |
| `meta-llama/llama-3.3-70b-instruct` | -- | 0.00 (n=15) | 0.25 (n=28) | 0.10 (n=112) | 0.45 (n=126) | 281 |
| `meta-llama/llama-4-maverick` | 0.00 (n=1) | 0.00 (n=50) | 0.11 (n=47) | 0.44 (n=220) | 0.00 (n=2) | 320 |
| `meta-llama/llama-4-scout` | -- | 0.00 (n=3) | 0.07 (n=14) | 0.16 (n=385) | 0.38 (n=21) | 423 |
| `microsoft/phi-4` | -- | 0.00 (n=20) | 0.00 (n=16) | 0.03 (n=364) | -- | 400 |
| `mistralai/codestral-2508` | -- | 0.00 (n=1) | 0.00 (n=4) | 0.20 (n=470) | 0.00 (n=1) | 476 |
| `mistralai/mistral-large-2512` | 0.00 (n=2) | 0.00 (n=20) | 0.00 (n=44) | 0.05 (n=410) | 0.19 (n=409) | 885 |
| `mistralai/mistral-medium-3.1` | -- | 0.00 (n=3) | 0.00 (n=33) | 0.25 (n=382) | 0.31 (n=16) | 434 |
| `moonshotai/kimi-k2.6` | 0.00 (n=23) | 0.05 (n=19) | -- | 0.55 (n=73) | 0.61 (n=62) | 177 |
| `nvidia/llama-3.3-nemotron-super-49b-v1.5` | -- | 0.00 (n=4) | 0.11 (n=18) | 0.36 (n=224) | 1.00 (n=1) | 247 |
| `nvidia/nemotron-nano-9b-v2` | -- | 0.00 (n=5) | 0.00 (n=24) | 0.13 (n=340) | 0.21 (n=14) | 383 |
| `openai/gpt-3.5-turbo` | -- | -- | 0.00 (n=1) | 0.19 (n=346) | 0.04 (n=180) | 527 |
| `openai/gpt-5.4` | 0.00 (n=14) | 0.09 (n=32) | 0.00 (n=22) | 0.46 (n=57) | 0.62 (n=168) | 293 |
| `openai/gpt-5.5` | 0.00 (n=4) | 0.25 (n=12) | 0.00 (n=4) | 0.53 (n=53) | 0.58 (n=167) | 240 |
| `openai/o3` | 0.00 (n=1) | -- | 0.40 (n=5) | 0.73 (n=111) | 1.00 (n=7) | 124 |
| `openai/o4-mini` | -- | 0.00 (n=2) | 0.00 (n=1) | 0.42 (n=26) | 0.00 (n=1) | 30 |
| `qwen/qwen3.5-plus-02-15` | -- | 0.00 (n=10) | 0.14 (n=7) | 0.53 (n=248) | 0.67 (n=15) | 280 |
| `x-ai/grok-4.1-fast` | -- | 1.00 (n=1) | 0.00 (n=1) | 0.51 (n=78) | 0.54 (n=98) | 178 |
| `x-ai/grok-4.3` | -- | 0.11 (n=9) | 0.13 (n=31) | 0.34 (n=350) | 0.33 (n=24) | 414 |

See `report_assets/calibration.svg` for the visual reliability diagram.

## Latency tail

Median latency hides outliers. p99 and max are what determines queue depth and worst-case user wait. For OpenRouter-routed models the tail also reflects upstream provider load, not just model compute.

| Model | p50 | p90 | p95 | p99 | max |
|---|---:|---:|---:|---:|---:|
| `mistralai/codestral-2508` | 0.77s | 1.61s | 2.06s | 3.74s | 6.36s |
| `meta-llama/llama-3.1-8b-instruct` | 0.81s | 2.64s | 4.09s | 5.34s | 76.80s |
| `meta-llama/llama-4-scout` | 0.84s | 2.86s | 3.99s | 6.96s | 16.81s |
| `google/gemini-2.5-flash` | 0.93s | 2.53s | 3.65s | 9.88s | 16.04s |
| `mistralai/mistral-medium-3.1` | 0.93s | 4.24s | 5.97s | 8.38s | 11.71s |
| `cohere/command-r-plus-08-2024` | 0.96s | 2.36s | 3.45s | 25.32s | 62.06s |
| `meta-llama/llama-4-maverick` | 1.16s | 2.08s | 2.38s | 4.37s | 50.80s |
| `openai/gpt-3.5-turbo` | 1.26s | 1.84s | 2.00s | 2.53s | 8.25s |
| `claude-haiku-4-5-20251001` | 1.29s | 2.99s | 3.93s | 156.60s | 186.76s |
| `meta-llama/llama-3.3-70b-instruct` | 1.51s | 2.81s | 5.60s | 13.05s | 34.85s |
| `claude-sonnet-4-6` | 1.58s | 4.53s | 5.56s | 8.02s | 183.22s |
| `google/gemma-4-31b-it` | 1.82s | 12.46s | 16.97s | 46.19s | 132.78s |
| `openai/gpt-5.4` | 1.82s | 2.64s | 3.28s | 5.22s | 17.20s |
| `deepseek/deepseek-r1-distill-llama-70b` | 2.00s | 11.61s | 25.81s | 121.52s | 136.13s |
| `deepseek/deepseek-v3.2` | 2.19s | 4.84s | 5.93s | 12.43s | 63.86s |
| `claude-opus-4-7` | 2.30s | 3.62s | 4.33s | 6.24s | 183.21s |
| `microsoft/phi-4` | 2.32s | 6.70s | 11.72s | 202.58s | 221.74s |
| `mistralai/mistral-large-2512` | 2.73s | 5.74s | 6.39s | 9.75s | 18.09s |
| `deepseek/deepseek-v4-flash` | 2.98s | 20.87s | 33.56s | 52.44s | 80.55s |
| `x-ai/grok-4.3` | 3.34s | 7.08s | 8.36s | 12.29s | 33.29s |
| `cohere/command-a` | 3.97s | 8.81s | 12.10s | 37.85s | 65.77s |
| `openai/gpt-5.5` | 6.16s | 14.42s | 20.78s | 32.43s | 37.80s |
| `openai/o4-mini` | 7.22s | 19.38s | 24.00s | 37.87s | 63.63s |
| `openai/o3` | 8.10s | 18.18s | 22.32s | 31.07s | 57.77s |
| `mistralai/mistral-7b-instruct-v0.1` | 8.58s | 24.83s | 34.46s | 81.91s | 89.36s |
| `nvidia/nemotron-nano-9b-v2` | 12.29s | 33.49s | 37.48s | 51.39s | 65.31s |
| `google/gemini-2.5-pro` | 13.69s | 24.82s | 28.07s | 37.78s | 173.46s |
| `deepseek/deepseek-r1-0528` | 14.93s | 73.24s | 85.25s | 135.80s | 285.69s |
| `deepseek/deepseek-r1` | 19.23s | 86.79s | 147.57s | 272.64s | 364.40s |
| `nvidia/llama-3.3-nemotron-super-49b-v1.5` | 22.79s | 82.35s | 85.74s | 100.75s | 106.19s |
| `moonshotai/kimi-k2.6` | 35.13s | 111.00s | 154.67s | 224.82s | 296.21s |
| `qwen/qwen3.5-plus-02-15` | 49.16s | 121.39s | 142.74s | 171.03s | 1486.87s |

## Output token efficiency

How many output tokens the model spent per detected ad. Lower is more concise (the model finds an ad and returns the JSON). Higher means the model is producing a lot of text the parser will discard, which costs you whether or not the answer is right.

| Model | Total output tokens | Ads detected | Tokens / ad | Cost / TP |
|---|---:|---:|---:|---:|
| `mistralai/mistral-medium-3.1` | 27,290 | 434 | 63 | $0.0000 |
| `mistralai/codestral-2508` | 30,472 | 476 | 64 | $0.0025 |
| `meta-llama/llama-3.3-70b-instruct` | 20,252 | 281 | 72 | $0.0061 |
| `google/gemini-2.5-flash` | 46,310 | 640 | 72 | $0.0026 |
| `openai/gpt-3.5-turbo` | 38,656 | 527 | 73 | $0.0048 |
| `google/gemma-4-31b-it` | 30,335 | 402 | 75 | $0.0000 |
| `cohere/command-r-plus-08-2024` | 6,849 | 85 | 81 | $0.0537 |
| `meta-llama/llama-4-scout` | 34,256 | 423 | 81 | $0.0000 |
| `meta-llama/llama-3.1-8b-instruct` | 59,426 | 708 | 84 | $0.0033 |
| `claude-sonnet-4-6` | 36,463 | 417 | 87 | $0.0232 |
| `mistralai/mistral-large-2512` | 80,682 | 885 | 91 | $0.0041 |
| `claude-haiku-4-5-20251001` | 52,024 | 551 | 94 | $0.0074 |
| `claude-opus-4-7` | 26,188 | 271 | 97 | $0.0416 |
| `cohere/command-a` | 40,418 | 401 | 101 | $0.0000 |
| `meta-llama/llama-4-maverick` | 32,554 | 320 | 102 | $0.0000 |
| `deepseek/deepseek-v3.2` | 19,588 | 190 | 103 | $0.0083 |
| `openai/gpt-5.4` | 31,263 | 293 | 107 | $0.0135 |
| `deepseek/deepseek-r1-distill-llama-70b` | 154,778 | 438 | 353 | $0.0083 |
| `microsoft/phi-4` | 159,399 | 400 | 398 | $0.0096 |
| `deepseek/deepseek-v4-flash` | 293,130 | 450 | 651 | $0.0000 |
| `openai/gpt-5.5` | 207,858 | 240 | 866 | $0.0364 |
| `x-ai/grok-4.3` | 381,272 | 414 | 921 | $0.0080 |
| `deepseek/deepseek-r1-0528` | 806,306 | 621 | 1298 | $0.0020 |
| `deepseek/deepseek-r1` | 590,495 | 408 | 1447 | $0.0367 |
| `nvidia/nemotron-nano-9b-v2` | 935,212 | 383 | 2442 | $0.0000 |
| `google/gemini-2.5-pro` | 939,580 | 336 | 2796 | $0.0196 |
| `nvidia/llama-3.3-nemotron-super-49b-v1.5` | 1,017,284 | 247 | 4119 | $0.0000 |
| `openai/o3` | 511,096 | 124 | 4122 | $0.0243 |
| `qwen/qwen3.5-plus-02-15` | 2,264,276 | 280 | 8087 | $0.0000 |
| `moonshotai/kimi-k2.6` | 1,698,012 | 177 | 9593 | $0.0255 |
| `openai/o4-mini` | 676,192 | 30 | 22540 | $0.1224 |

## Trial variance (determinism check)

All trials run at temperature 0.0. If a model produces stable output you'd expect the F1 stdev across trials to be near zero. Higher numbers mean the model is non-deterministic even at temp=0. That's fine to know, but means you cannot trust a single trial's number for that model.

| Model | Mean F1 stdev across episodes | Highest single-episode stdev |
|---|---:|---:|
| `qwen/qwen3.5-plus-02-15` | 0.0327 | 0.0938 |
| `openai/gpt-5.5` | 0.0606 | 0.1432 |
| `claude-opus-4-7` | 0.0567 | 0.1351 |
| `openai/gpt-5.4` | 0.0745 | 0.1193 |
| `google/gemini-2.5-pro` | 0.0442 | 0.1157 |
| `openai/o3` | 0.1720 | 0.4714 |
| `x-ai/grok-4.3` | 0.1162 | 0.2528 |
| `deepseek/deepseek-v4-flash` | 0.1079 | 0.2392 |
| `google/gemma-4-31b-it` | 0.0863 | 0.2739 |
| `moonshotai/kimi-k2.6` | 0.1635 | 0.2739 |
| `deepseek/deepseek-r1` | 0.1321 | 0.3015 |
| `deepseek/deepseek-r1-0528` | 0.1378 | 0.2807 |
| `cohere/command-a` | 0.0433 | 0.1217 |
| `meta-llama/llama-4-maverick` | 0.0216 | 0.1012 |
| `claude-sonnet-4-6` | 0.0257 | 0.1278 |
| `nvidia/llama-3.3-nemotron-super-49b-v1.5` | 0.1668 | 0.3249 |
| `mistralai/mistral-medium-3.1` | 0.0493 | 0.1373 |
| `claude-haiku-4-5-20251001` | 0.0015 | 0.0116 |
| `meta-llama/llama-3.3-70b-instruct` | 0.0633 | 0.2981 |
| `mistralai/codestral-2508` | 0.0581 | 0.1493 |
| `deepseek/deepseek-v3.2` | 0.2061 | 0.5477 |
| `google/gemini-2.5-flash` | 0.0000 | 0.0000 |
| `openai/gpt-3.5-turbo` | 0.0028 | 0.0100 |
| `meta-llama/llama-4-scout` | 0.0898 | 0.1815 |
| `deepseek/deepseek-r1-distill-llama-70b` | 0.0697 | 0.1589 |
| `mistralai/mistral-large-2512` | 0.0149 | 0.0412 |
| `nvidia/nemotron-nano-9b-v2` | 0.0951 | 0.1941 |
| `meta-llama/llama-3.1-8b-instruct` | 0.1240 | 0.5477 |
| `cohere/command-r-plus-08-2024` | 0.0470 | 0.1547 |
| `openai/o4-mini` | 0.1574 | 0.2981 |
| `microsoft/phi-4` | 0.0660 | 0.3070 |
| `mistralai/mistral-7b-instruct-v0.1` | 0.0000 | 0.0000 |

## Cross-model agreement

For each of the 123 (episode, window, trial-equivalent) entries, how many of the 32 active models predicted at least one ad? High-agreement windows are unambiguous ads (or unambiguously not ads). Low-agreement windows are where individual models disagree, and are candidates for ensemble voting if you want a cheap accuracy boost.

| Models predicting an ad | Window count | Share |
|---:|---:|---:|
| 2 of 32 | 1 | 0.8% |
| 3 of 32 | 3 | 2.4% |
| 4 of 32 | 12 | 9.8% |
| 5 of 32 | 9 | 7.3% |
| 6 of 32 | 4 | 3.3% |
| 7 of 32 | 5 | 4.1% |
| 8 of 32 | 6 | 4.9% |
| 9 of 32 | 9 | 7.3% |
| 10 of 32 | 5 | 4.1% |
| 11 of 32 | 6 | 4.9% |
| 12 of 32 | 2 | 1.6% |
| 13 of 32 | 3 | 2.4% |
| 14 of 32 | 2 | 1.6% |
| 16 of 32 | 1 | 0.8% |
| 19 of 32 | 2 | 1.6% |
| 21 of 32 | 1 | 0.8% |
| 22 of 32 | 1 | 0.8% |
| 23 of 32 | 2 | 1.6% |
| 25 of 32 | 1 | 0.8% |
| 26 of 32 | 2 | 1.6% |
| 27 of 32 | 4 | 3.3% |
| 28 of 32 | 10 | 8.1% |
| 29 of 32 | 13 | 10.6% |
| 30 of 32 | 10 | 8.1% |
| 31 of 32 | 6 | 4.9% |
| 32 of 32 | 3 | 2.4% |

Read this as: rows near the top are windows where the field disagrees (most models said no, a few said yes, usually false positives); rows near the bottom are windows where the field broadly agrees (typical of clear sponsor reads).

### Per-model alignment with consensus

Same data, viewed per model. For each window, the **majority** is whether more than half of the 32 active models flagged an ad. Then for each model: did it vote with the majority or against it? Four buckets:

- **with-yes**: this model voted yes, majority also voted yes (likely true positive)
- **with-no**: this model voted no, majority also voted no (likely true negative)
- **broke-yes**: this model voted yes, majority voted no (likely false positive / hallucination)
- **broke-no**: this model voted no, majority voted yes (likely missed real ad)

Alignment rate is `(with-yes + with-no) / total`. High alignment means the model tracks the consensus; low alignment means it disagrees often, which could be brilliance or noise depending on whether its disagreements are also where its F1 wins or loses.

| Model | with-yes | with-no | broke-yes | broke-no | Alignment |
|---|---:|---:|---:|---:|---:|
| `x-ai/grok-4.3` | 55 | 68 | 0 | 0 | 100.0% |
| `qwen/qwen3.5-plus-02-15` | 53 | 68 | 0 | 2 | 98.4% |
| `google/gemini-2.5-flash` | 54 | 66 | 2 | 1 | 97.6% |
| `nvidia/llama-3.3-nemotron-super-49b-v1.5` | 52 | 68 | 0 | 3 | 97.6% |
| `openai/gpt-5.5` | 53 | 66 | 2 | 2 | 96.7% |
| `claude-opus-4-7` | 50 | 68 | 0 | 5 | 95.9% |
| `claude-sonnet-4-6` | 50 | 68 | 0 | 5 | 95.9% |
| `mistralai/mistral-medium-3.1` | 51 | 67 | 1 | 4 | 95.9% |
| `google/gemma-4-31b-it` | 53 | 64 | 4 | 2 | 95.1% |
| `claude-haiku-4-5-20251001` | 51 | 65 | 3 | 4 | 94.3% |
| `google/gemini-2.5-pro` | 55 | 60 | 8 | 0 | 93.5% |
| `openai/o3` | 47 | 68 | 0 | 8 | 93.5% |
| `meta-llama/llama-4-scout` | 54 | 58 | 10 | 1 | 91.1% |
| `deepseek/deepseek-v4-flash` | 55 | 55 | 13 | 0 | 89.4% |
| `meta-llama/llama-3.3-70b-instruct` | 49 | 61 | 7 | 6 | 89.4% |
| `x-ai/grok-4.1-fast` | 39 | 68 | 0 | 16 | 87.0% |
| `deepseek/deepseek-r1` | 55 | 50 | 18 | 0 | 85.4% |
| `nvidia/nemotron-nano-9b-v2` | 53 | 52 | 16 | 2 | 85.4% |
| `meta-llama/llama-3.1-8b-instruct` | 51 | 52 | 16 | 4 | 83.7% |
| `meta-llama/llama-4-maverick` | 53 | 50 | 18 | 2 | 83.7% |
| `openai/gpt-5.4` | 54 | 44 | 24 | 1 | 79.7% |
| `mistralai/codestral-2508` | 51 | 43 | 25 | 4 | 76.4% |
| `openai/o4-mini` | 22 | 68 | 0 | 33 | 73.2% |
| `cohere/command-r-plus-08-2024` | 21 | 68 | 0 | 34 | 72.4% |
| `deepseek/deepseek-v3.2` | 33 | 54 | 14 | 22 | 70.7% |
| `cohere/command-a` | 52 | 34 | 34 | 3 | 69.9% |
| `mistralai/mistral-large-2512` | 54 | 29 | 39 | 1 | 67.5% |
| `deepseek/deepseek-r1-0528` | 52 | 26 | 42 | 3 | 63.4% |
| `mistralai/mistral-7b-instruct-v0.1` | 0 | 68 | 0 | 55 | 55.3% |
| `openai/gpt-3.5-turbo` | 55 | 13 | 55 | 0 | 55.3% |
| `moonshotai/kimi-k2.6` | 37 | 28 | 40 | 18 | 52.8% |
| `deepseek/deepseek-r1-distill-llama-70b` | 48 | 4 | 64 | 7 | 42.3% |
| `microsoft/phi-4` | 39 | 8 | 60 | 16 | 38.2% |

## Detection rate by ad characteristic

Aggregate detection rates often hide systematic blind spots. Below: for each model, what fraction of truth ads in each bucket were detected (matched at IoU >= 0.5).

### By ad length

Truth ads bucketed by duration: short (<30s), medium (30-90s), long (>=90s). Cell values are detection rate (fraction of truth ads in that bucket the model caught), with the sample size `n` so a misleading 1.00 on a 2-ad bucket doesn't get over-weighted. Models that systematically miss short ads usually fail on network-inserted brand-tagline spots; missing long ads is rarer and usually means the model gave up before processing the full window.

| Model | long (>=90s) | medium (30-90s) | short (<30s) |
|---|---:|---:|---:|
| `claude-haiku-4-5-20251001` | 0.44 (n=90) | 0.83 (n=60) | 0.83 (n=30) |
| `claude-opus-4-7` | 0.80 (n=90) | 0.77 (n=60) | 0.50 (n=30) |
| `claude-sonnet-4-6` | 0.50 (n=90) | 0.72 (n=60) | 0.67 (n=30) |
| `cohere/command-a` | 0.50 (n=90) | 0.62 (n=60) | 0.83 (n=30) |
| `cohere/command-r-plus-08-2024` | 0.29 (n=90) | 0.05 (n=60) | 0.17 (n=30) |
| `deepseek/deepseek-r1` | 0.56 (n=90) | 0.75 (n=60) | 0.83 (n=30) |
| `deepseek/deepseek-r1-0528` | 0.56 (n=90) | 0.75 (n=60) | 0.83 (n=30) |
| `deepseek/deepseek-r1-distill-llama-70b` | 0.30 (n=90) | 0.28 (n=60) | 0.67 (n=30) |
| `deepseek/deepseek-v3.2` | 0.36 (n=90) | 0.23 (n=60) | 0.27 (n=30) |
| `deepseek/deepseek-v4-flash` | 0.69 (n=90) | 0.85 (n=60) | 0.83 (n=30) |
| `google/gemini-2.5-flash` | 0.33 (n=90) | 0.67 (n=60) | 0.83 (n=30) |
| `google/gemini-2.5-pro` | 0.77 (n=90) | 0.82 (n=60) | 0.80 (n=30) |
| `google/gemma-4-31b-it` | 0.62 (n=90) | 0.73 (n=60) | 0.63 (n=30) |
| `meta-llama/llama-3.1-8b-instruct` | 0.19 (n=90) | 0.25 (n=60) | 0.37 (n=30) |
| `meta-llama/llama-3.3-70b-instruct` | 0.39 (n=90) | 0.33 (n=60) | 0.67 (n=30) |
| `meta-llama/llama-4-maverick` | 0.50 (n=90) | 0.70 (n=60) | 0.50 (n=30) |
| `meta-llama/llama-4-scout` | 0.30 (n=90) | 0.42 (n=60) | 0.57 (n=30) |
| `microsoft/phi-4` | 0.02 (n=90) | 0.07 (n=60) | 0.17 (n=30) |
| `mistralai/codestral-2508` | 0.47 (n=90) | 0.63 (n=60) | 0.43 (n=30) |
| `mistralai/mistral-7b-instruct-v0.1` | 0.00 (n=90) | 0.00 (n=60) | 0.00 (n=30) |
| `mistralai/mistral-large-2512` | 0.37 (n=90) | 0.75 (n=60) | 0.67 (n=30) |
| `mistralai/mistral-medium-3.1` | 0.38 (n=90) | 0.73 (n=60) | 0.80 (n=30) |
| `moonshotai/kimi-k2.6` | 0.40 (n=90) | 0.57 (n=60) | 0.30 (n=30) |
| `nvidia/llama-3.3-nemotron-super-49b-v1.5` | 0.38 (n=90) | 0.52 (n=60) | 0.63 (n=30) |
| `nvidia/nemotron-nano-9b-v2` | 0.18 (n=90) | 0.38 (n=60) | 0.30 (n=30) |
| `openai/gpt-3.5-turbo` | 0.33 (n=90) | 0.33 (n=60) | 0.83 (n=30) |
| `openai/gpt-5.4` | 0.78 (n=90) | 0.75 (n=60) | 0.60 (n=30) |
| `openai/gpt-5.5` | 0.69 (n=90) | 0.80 (n=60) | 0.60 (n=30) |
| `openai/o3` | 0.59 (n=90) | 0.42 (n=60) | 0.40 (n=30) |
| `openai/o4-mini` | 0.03 (n=90) | 0.07 (n=60) | 0.13 (n=30) |
| `qwen/qwen3.5-plus-02-15` | 0.82 (n=90) | 0.80 (n=60) | 0.67 (n=30) |
| `x-ai/grok-4.1-fast` | 0.75 (n=75) | 0.76 (n=25) | 0.76 (n=25) |
| `x-ai/grok-4.3` | 0.62 (n=90) | 0.88 (n=60) | 0.80 (n=30) |

### By ad position

Truth ads bucketed by where they fall in the episode: pre-roll (first 10%), mid-roll (10-90%), post-roll (last 10%). Cell values are the same detection-rate-with-`n` format as ad length. A common failure pattern in our data: most models detect pre-roll and mid-roll reliably and miss post-roll, because the prompt windows near the end often catch the model mid-reasoning or with fewer transition phrases to anchor on.

| Model | pre-roll (<10%) | mid-roll (10-90%) | post-roll (>90%) |
|---|---:|---:|---:|
| `claude-haiku-4-5-20251001` | 0.60 (n=50) | 0.65 (n=85) | 0.67 (n=45) |
| `claude-opus-4-7` | 0.80 (n=50) | 0.72 (n=85) | 0.71 (n=45) |
| `claude-sonnet-4-6` | 0.66 (n=50) | 0.59 (n=85) | 0.56 (n=45) |
| `cohere/command-a` | 0.54 (n=50) | 0.65 (n=85) | 0.56 (n=45) |
| `cohere/command-r-plus-08-2024` | 0.08 (n=50) | 0.34 (n=85) | 0.02 (n=45) |
| `deepseek/deepseek-r1` | 0.58 (n=50) | 0.74 (n=85) | 0.62 (n=45) |
| `deepseek/deepseek-r1-0528` | 0.58 (n=50) | 0.73 (n=85) | 0.64 (n=45) |
| `deepseek/deepseek-r1-distill-llama-70b` | 0.34 (n=50) | 0.36 (n=85) | 0.36 (n=45) |
| `deepseek/deepseek-v3.2` | 0.34 (n=50) | 0.39 (n=85) | 0.09 (n=45) |
| `deepseek/deepseek-v4-flash` | 0.64 (n=50) | 0.88 (n=85) | 0.69 (n=45) |
| `google/gemini-2.5-flash` | 0.50 (n=50) | 0.53 (n=85) | 0.56 (n=45) |
| `google/gemini-2.5-pro` | 0.78 (n=50) | 0.86 (n=85) | 0.67 (n=45) |
| `google/gemma-4-31b-it` | 0.64 (n=50) | 0.66 (n=85) | 0.69 (n=45) |
| `meta-llama/llama-3.1-8b-instruct` | 0.18 (n=50) | 0.33 (n=85) | 0.13 (n=45) |
| `meta-llama/llama-3.3-70b-instruct` | 0.32 (n=50) | 0.51 (n=85) | 0.36 (n=45) |
| `meta-llama/llama-4-maverick` | 0.54 (n=50) | 0.65 (n=85) | 0.44 (n=45) |
| `meta-llama/llama-4-scout` | 0.34 (n=50) | 0.51 (n=85) | 0.20 (n=45) |
| `microsoft/phi-4` | 0.14 (n=50) | 0.02 (n=85) | 0.04 (n=45) |
| `mistralai/codestral-2508` | 0.40 (n=50) | 0.59 (n=85) | 0.51 (n=45) |
| `mistralai/mistral-7b-instruct-v0.1` | 0.00 (n=50) | 0.00 (n=85) | 0.00 (n=45) |
| `mistralai/mistral-large-2512` | 0.58 (n=50) | 0.58 (n=85) | 0.44 (n=45) |
| `mistralai/mistral-medium-3.1` | 0.56 (n=50) | 0.55 (n=85) | 0.60 (n=45) |
| `moonshotai/kimi-k2.6` | 0.40 (n=50) | 0.40 (n=85) | 0.56 (n=45) |
| `nvidia/llama-3.3-nemotron-super-49b-v1.5` | 0.30 (n=50) | 0.59 (n=85) | 0.42 (n=45) |
| `nvidia/nemotron-nano-9b-v2` | 0.12 (n=50) | 0.31 (n=85) | 0.36 (n=45) |
| `openai/gpt-3.5-turbo` | 0.20 (n=50) | 0.59 (n=85) | 0.33 (n=45) |
| `openai/gpt-5.4` | 0.78 (n=50) | 0.75 (n=85) | 0.67 (n=45) |
| `openai/gpt-5.5` | 0.74 (n=50) | 0.72 (n=85) | 0.67 (n=45) |
| `openai/o3` | 0.42 (n=50) | 0.56 (n=85) | 0.47 (n=45) |
| `openai/o4-mini` | 0.02 (n=50) | 0.08 (n=85) | 0.07 (n=45) |
| `qwen/qwen3.5-plus-02-15` | 0.80 (n=50) | 0.85 (n=85) | 0.67 (n=45) |
| `x-ai/grok-4.1-fast` | 0.66 (n=35) | 0.97 (n=60) | 0.43 (n=30) |
| `x-ai/grok-4.3` | 0.64 (n=50) | 0.82 (n=85) | 0.69 (n=45) |

## Quick Comparison

One row per model, one column per episode. The headline columns (`F1`, `Cost/ep`, `p50`) summarize across all episodes; the per-episode columns let you see whether a model's average hides wide swings (a model that scores well overall might still bomb on a specific genre). The right-most `F1 stdev` column averages the per-trial standard deviations across episodes; high values mean the model isn't deterministic at temperature 0.0, so its single-trial F1 number is noisy.

| Model | F1 | Cost/ep | p50 | ep-daily-tech-news-show-b576979e1fe8 | ep-daily-tech-news-show-c1904b8605f7 | ep-glt1412515089-373d5ba5007b | ep-it-s-a-thing-e339179dfad6 | ep-on-air-with-dan-and-alex2-574e4f303730 | ep-security-now-audio-2850b24903b2 | ep-the-brilliant-idiots-0bb9bf634c8e | ep-the-tim-dillon-show-f62bd5fa1cfe | ep-tosh-show-5f6894439bb6 | ep-ai-cloud-essentials-e8dc897fbd6b (no-ad) | ep-oxide-and-friends-ce789ff5b62e (no-ad) | F1 stdev |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| `qwen/qwen3.5-plus-02-15` | 0.649 | $0.0000 | 49.2s | 0.857 | 0.518 | 0.625 | 0.667 | 0.590 | 0.476 | 0.771 | 0.636 | 0.696 | PASS | PASS | 0.033 |
| `openai/gpt-5.5` | 0.636 | $4.6613 | 6.2s | 0.886 | 0.547 | 0.636 | 0.667 | 0.571 | 0.505 | 0.776 | 0.546 | 0.587 | FAIL (1 FP) | PASS | 0.061 |
| `claude-opus-4-7` | 0.618 | $5.5394 | 2.3s | 0.886 | 0.445 | 0.600 | 0.667 | 0.595 | 0.520 | 0.733 | 0.592 | 0.524 | PASS | PASS | 0.057 |
| `openai/gpt-5.4` | 0.605 | $1.8008 | 1.8s | 0.892 | 0.518 | 0.506 | 0.613 | 0.747 | 0.495 | 0.516 | 0.586 | 0.569 | FAIL (1 FP) | FAIL (1 FP) | 0.075 |
| `google/gemini-2.5-pro` | 0.589 | $2.7901 | 13.7s | 0.864 | 0.448 | 0.646 | 0.667 | 0.543 | 0.451 | 0.510 | 0.569 | 0.603 | FAIL (1 FP) | FAIL (1 FP) | 0.044 |
| `openai/o3` | 0.576 | $2.1834 | 8.1s | 0.848 | 0.472 | 0.664 | 0.333 | 0.687 | 0.644 | 0.698 | 0.372 | 0.464 | PASS | PASS | 0.172 |
| `x-ai/grok-4.3` | 0.489 | $1.0593 | 3.3s | 0.507 | 0.179 | 0.572 | 0.433 | 0.472 | 0.486 | 0.771 | 0.393 | 0.585 | PASS | PASS | 0.116 |
| `deepseek/deepseek-v4-flash` | 0.464 | $0.0000 | 3.0s | 0.445 | 0.310 | 0.642 | 0.337 | 0.535 | 0.465 | 0.569 | 0.298 | 0.574 | FAIL (1 FP) | PASS | 0.108 |
| `google/gemma-4-31b-it` | 0.463 | $0.0000 | 1.8s | 0.811 | 0.119 | 0.645 | 0.467 | 0.514 | 0.496 | 0.585 | 0.350 | 0.182 | FAIL (1 FP) | PASS | 0.086 |
| `moonshotai/kimi-k2.6` | 0.456 | $2.0174 | 35.1s | 0.914 | 0.600 | 0.469 | 0.200 | 0.734 | 0.196 | 0.538 | 0.184 | 0.267 | FAIL (1 FP) | FAIL (4 FP) | 0.164 |
| `deepseek/deepseek-r1` | 0.438 | $4.4082 | 19.2s | 0.658 | 0.158 | 0.630 | 0.313 | 0.555 | 0.462 | 0.401 | 0.327 | 0.435 | FAIL (1 FP) | FAIL (1 FP) | 0.132 |
| `deepseek/deepseek-r1-0528` | 0.398 | $0.2395 | 14.9s | 0.700 | 0.184 | 0.379 | 0.404 | 0.514 | 0.281 | 0.161 | 0.319 | 0.643 | FAIL (27 FP) | FAIL (12 FP) | 0.138 |
| `cohere/command-a` | 0.395 | $0.0000 | 4.0s | 0.500 | 0.298 | 0.507 | 0.400 | 0.503 | 0.368 | 0.247 | 0.200 | 0.533 | FAIL (3 FP) | PASS | 0.043 |
| `meta-llama/llama-4-maverick` | 0.390 | $0.0000 | 1.2s | 0.771 | 0.204 | 0.507 | 0.000 | 0.571 | 0.496 | 0.390 | 0.167 | 0.400 | FAIL (1 FP) | PASS | 0.022 |
| `claude-sonnet-4-6` | 0.377 | $2.5061 | 1.6s | 0.407 | 0.237 | 0.400 | 0.000 | 0.444 | 0.516 | 0.836 | 0.179 | 0.375 | PASS | PASS | 0.026 |
| `nvidia/llama-3.3-nemotron-super-49b-v1.5` | 0.375 | $0.0000 | 22.8s | 0.299 | 0.177 | 0.596 | 0.233 | 0.431 | 0.587 | 0.478 | 0.171 | 0.399 | PASS | PASS | 0.167 |
| `mistralai/mistral-medium-3.1` | 0.367 | $0.0000 | 0.9s | 0.162 | 0.095 | 0.671 | 0.000 | 0.500 | 0.640 | 0.591 | 0.223 | 0.421 | PASS | PASS | 0.049 |
| `claude-haiku-4-5-20251001` | 0.340 | $0.8508 | 1.3s | 0.235 | 0.073 | 0.571 | 0.000 | 0.500 | 0.551 | 0.600 | 0.154 | 0.375 | PASS | PASS | 0.001 |
| `meta-llama/llama-3.3-70b-instruct` | 0.298 | $0.4599 | 1.5s | 0.327 | 0.000 | 0.567 | 0.000 | 0.133 | 0.512 | 0.489 | 0.308 | 0.347 | PASS | PASS | 0.063 |
| `mistralai/codestral-2508` | 0.284 | $0.2296 | 0.8s | 0.379 | 0.172 | 0.231 | 0.000 | 0.469 | 0.374 | 0.187 | 0.178 | 0.564 | PASS | PASS | 0.058 |
| `deepseek/deepseek-v3.2` | 0.279 | $0.4507 | 2.2s | 0.327 | 0.140 | 0.518 | 0.400 | 0.300 | 0.528 | 0.100 | 0.057 | 0.140 | PASS | FAIL (2 FP) | 0.206 |
| `google/gemini-2.5-flash` | 0.246 | $0.2425 | 0.9s | 0.267 | 0.071 | 0.500 | 0.000 | 0.444 | 0.455 | 0.125 | 0.154 | 0.200 | PASS | PASS | 0.000 |
| `openai/gpt-3.5-turbo` | 0.221 | $0.3621 | 1.3s | 0.222 | 0.364 | 0.254 | 0.000 | 0.400 | 0.217 | 0.220 | 0.125 | 0.189 | FAIL (3 FP) | FAIL (10 FP) | 0.003 |
| `meta-llama/llama-4-scout` | 0.220 | $0.0000 | 0.8s | 0.242 | 0.130 | 0.336 | 0.000 | 0.094 | 0.520 | 0.349 | 0.053 | 0.258 | PASS | PASS | 0.090 |
| `deepseek/deepseek-r1-distill-llama-70b` | 0.206 | $0.5333 | 2.0s | 0.057 | 0.205 | 0.265 | 0.000 | 0.343 | 0.262 | 0.222 | 0.163 | 0.340 | FAIL (2 FP) | FAIL (10 FP) | 0.070 |
| `mistralai/mistral-large-2512` | 0.202 | $0.3977 | 2.7s | 0.250 | 0.074 | 0.253 | 0.000 | 0.444 | 0.209 | 0.193 | 0.044 | 0.353 | PASS | PASS | 0.015 |
| `nvidia/nemotron-nano-9b-v2` | 0.186 | $0.0000 | 12.3s | 0.274 | 0.069 | 0.242 | 0.000 | 0.361 | 0.238 | 0.205 | 0.044 | 0.240 | FAIL (1 FP) | PASS | 0.095 |
| `meta-llama/llama-3.1-8b-instruct` | 0.166 | $0.1430 | 0.8s | 0.231 | 0.029 | 0.183 | 0.400 | 0.284 | 0.133 | 0.000 | 0.049 | 0.185 | PASS | PASS | 0.124 |
| `cohere/command-r-plus-08-2024` | 0.136 | $1.8262 | 1.0s | 0.000 | 0.313 | 0.000 | 0.000 | 0.000 | 0.569 | 0.000 | 0.057 | 0.289 | PASS | PASS | 0.047 |
| `openai/o4-mini` | 0.095 | $1.3462 | 7.2s | 0.147 | 0.067 | 0.080 | 0.000 | 0.133 | 0.114 | 0.180 | 0.000 | 0.133 | PASS | PASS | 0.157 |
| `microsoft/phi-4` | 0.050 | $0.1051 | 2.3s | 0.000 | 0.056 | 0.000 | 0.000 | 0.213 | 0.033 | 0.067 | 0.079 | 0.000 | FAIL (3 FP) | FAIL (15 FP) | 0.066 |
| `mistralai/mistral-7b-instruct-v0.1` | 0.000 | $0.0000 | 8.6s | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | PASS | PASS | 0.000 |

---

## Detailed Results

### Per-Model Detail

Full per-model profile: F1 averaged across episodes, total cost per episode at current pricing, p50 / p95 latency, JSON compliance, parse-failure rate, the distribution of extraction methods the parser had to use, and verbosity / truncation telemetry. The `Extraction methods` list shows how often each route was hit. `json_array_direct` is the cleanest; the rest are recovery paths. The verbosity row flags models that emit long `reason` fields or run out of token budget mid-response. Ordered by F1 descending so the best performers appear first.

#### `qwen/qwen3.5-plus-02-15`

- F1 (avg across episodes): **0.649**
- Total cost / episode: **$0.0000**
- p50 / p95 latency: 49.16s / 142.74s
- JSON compliance: 1.00
- Parse failure rate: 0.2%
- Extraction methods: `json_array_direct`: 614, `parse_failure`: 1
- Verbosity: 547/615 calls over 1024 output tokens (88.9%); 0 hit max_tokens (0.0%); 0 salvaged from truncated JSON (0.0%)
- Schema violations: 384
- Extra keys observed: end_text, sponsor

#### `openai/gpt-5.5`

- F1 (avg across episodes): **0.636**
- Total cost / episode: **$4.6613**
- p50 / p95 latency: 6.16s / 20.78s
- JSON compliance: 0.87
- Parse failure rate: 0.2%
- Extraction methods: `json_object_no_ads`: 353, `json_object_single_ad`: 261, `parse_failure`: 1
- Verbosity: 43/615 calls over 1024 output tokens (7.0%); 0 hit max_tokens (0.0%); 0 salvaged from truncated JSON (0.0%)
- Schema violations: 332
- Extra keys observed: end_text, sponsor

#### `claude-opus-4-7`

- F1 (avg across episodes): **0.618**
- Total cost / episode: **$5.5394**
- p50 / p95 latency: 2.30s / 4.33s
- JSON compliance: 1.00
- Parse failure rate: 0.0%
- Extraction methods: `json_array_direct`: 613, `regex_json_array`: 2
- Verbosity: 0/615 calls over 1024 output tokens (0.0%); 0 hit max_tokens (0.0%); 0 salvaged from truncated JSON (0.0%)
- Schema violations: 397
- Extra keys observed: end_text, sponsor

#### `openai/gpt-5.4`

- F1 (avg across episodes): **0.605**
- Total cost / episode: **$1.8008**
- p50 / p95 latency: 1.82s / 3.28s
- JSON compliance: 0.80
- Parse failure rate: 0.0%
- Extraction methods: `json_object_no_ads`: 211, `json_object_single_ad`: 404
- Verbosity: 0/615 calls over 1024 output tokens (0.0%); 0 hit max_tokens (0.0%); 0 salvaged from truncated JSON (0.0%)
- Schema violations: 398
- Extra keys observed: end_text, sponsor

#### `google/gemini-2.5-pro`

- F1 (avg across episodes): **0.589**
- Total cost / episode: **$2.7901**
- p50 / p95 latency: 13.69s / 28.07s
- JSON compliance: 0.97
- Parse failure rate: 0.5%
- Extraction methods: `json_array_direct`: 590, `parse_failure`: 3, `regex_json_array`: 22
- Verbosity: 481/615 calls over 1024 output tokens (78.2%); 0 hit max_tokens (0.0%); 0 salvaged from truncated JSON (0.0%)
- Schema violations: 389
- Extra keys observed: end_text, sponsor

#### `openai/o3`

- F1 (avg across episodes): **0.576**
- Total cost / episode: **$2.1834**
- p50 / p95 latency: 8.10s / 22.32s
- JSON compliance: 0.93
- Parse failure rate: 0.7%
- Extraction methods: `json_object_ads_key`: 20, `json_object_no_ads`: 459, `json_object_segments_key`: 9, `json_object_single_ad`: 123, `parse_failure`: 4
- Verbosity: 175/615 calls over 1024 output tokens (28.5%); 0 hit max_tokens (0.0%); 0 salvaged from truncated JSON (0.0%)
- Schema violations: 132
- Extra keys observed: end_text, sponsor

#### `x-ai/grok-4.3`

- F1 (avg across episodes): **0.489**
- Total cost / episode: **$1.0593**
- p50 / p95 latency: 3.34s / 8.36s
- JSON compliance: 1.00
- Parse failure rate: 0.2%
- Extraction methods: `json_array_direct`: 614, `parse_failure`: 1
- Verbosity: 102/615 calls over 1024 output tokens (16.6%); 0 hit max_tokens (0.0%); 0 salvaged from truncated JSON (0.0%)

#### `deepseek/deepseek-v4-flash`

- F1 (avg across episodes): **0.464**
- Total cost / episode: **$0.0000**
- p50 / p95 latency: 2.98s / 33.56s
- JSON compliance: 0.80
- Parse failure rate: 2.3%
- Extraction methods: `json_array_direct`: 44, `json_object_ads_key`: 362, `json_object_no_ads`: 4, `json_object_segments_key`: 1, `json_object_single_ad`: 190, `parse_failure`: 14
- Verbosity: 106/615 calls over 1024 output tokens (17.2%); 0 hit max_tokens (0.0%); 0 salvaged from truncated JSON (0.0%)
- Schema violations: 631
- Extra keys observed: end_text, sponsor

#### `google/gemma-4-31b-it`

- F1 (avg across episodes): **0.463**
- Total cost / episode: **$0.0000**
- p50 / p95 latency: 1.82s / 16.97s
- JSON compliance: 0.86
- Parse failure rate: 0.0%
- Extraction methods: `json_object_ads_key`: 332, `json_object_no_ads`: 154, `json_object_single_ad`: 128, `json_object_single_ad_truncated`: 1
- Verbosity: 0/615 calls over 1024 output tokens (0.0%); 0 hit max_tokens (0.0%); 1 salvaged from truncated JSON (0.2%)
- Schema violations: 532
- Extra keys observed: end_text, sponsor

#### `moonshotai/kimi-k2.6`

- F1 (avg across episodes): **0.456**
- Total cost / episode: **$2.0174**
- p50 / p95 latency: 35.13s / 154.67s
- JSON compliance: 0.59
- Parse failure rate: 25.7%
- Extraction methods: `json_array_direct`: 44, `json_object_ads_key`: 21, `json_object_no_ads`: 91, `json_object_segments_key`: 2, `json_object_single_ad`: 296, `markdown_code_block`: 3, `parse_failure`: 158
- Verbosity: 550/615 calls over 1024 output tokens (89.4%); 32 hit max_tokens (5.2%); 0 salvaged from truncated JSON (0.0%)
- Schema violations: 234
- Extra keys observed: end_text, sponsor

#### `deepseek/deepseek-r1`

- F1 (avg across episodes): **0.438**
- Total cost / episode: **$4.4082**
- p50 / p95 latency: 19.23s / 147.57s
- JSON compliance: 0.96
- Parse failure rate: 1.0%
- Extraction methods: `json_array_direct`: 539, `json_object_ads_key`: 2, `json_object_no_ads`: 15, `json_object_segments_key`: 5, `json_object_single_ad`: 36, `markdown_code_block`: 10, `parse_failure`: 6, `regex_json_array`: 2
- Verbosity: 110/615 calls over 1024 output tokens (17.9%); 0 hit max_tokens (0.0%); 0 salvaged from truncated JSON (0.0%)
- Schema violations: 486
- Extra keys observed: end_text, sponsor

#### `deepseek/deepseek-r1-0528`

- F1 (avg across episodes): **0.398**
- Total cost / episode: **$0.2395**
- p50 / p95 latency: 14.93s / 85.25s
- JSON compliance: 0.89
- Parse failure rate: 8.6%
- Extraction methods: `json_array_direct`: 503, `json_object_ads_key`: 28, `json_object_no_ads`: 3, `json_object_single_ad`: 25, `json_object_single_ad_truncated`: 2, `markdown_code_block`: 1, `parse_failure`: 53
- Verbosity: 229/615 calls over 1024 output tokens (37.2%); 7 hit max_tokens (1.1%); 2 salvaged from truncated JSON (0.3%)
- Schema violations: 694
- Extra keys observed: end_text, sponsor

#### `cohere/command-a`

- F1 (avg across episodes): **0.395**
- Total cost / episode: **$0.0000**
- p50 / p95 latency: 3.97s / 12.10s
- JSON compliance: 0.71
- Parse failure rate: 0.0%
- Extraction methods: `json_object_no_ads`: 17, `json_object_single_ad`: 598
- Verbosity: 0/615 calls over 1024 output tokens (0.0%); 0 hit max_tokens (0.0%); 0 salvaged from truncated JSON (0.0%)
- Schema violations: 478
- Extra keys observed: end_text, sponsor

#### `meta-llama/llama-4-maverick`

- F1 (avg across episodes): **0.390**
- Total cost / episode: **$0.0000**
- p50 / p95 latency: 1.16s / 2.38s
- JSON compliance: 0.79
- Parse failure rate: 0.0%
- Extraction methods: `json_object_no_ads`: 184, `json_object_single_ad`: 431
- Verbosity: 3/615 calls over 1024 output tokens (0.5%); 0 hit max_tokens (0.0%); 0 salvaged from truncated JSON (0.0%)
- Schema violations: 372
- Extra keys observed: end_text, sponsor

#### `claude-sonnet-4-6`

- F1 (avg across episodes): **0.377**
- Total cost / episode: **$2.5061**
- p50 / p95 latency: 1.58s / 5.56s
- JSON compliance: 0.96
- Parse failure rate: 0.0%
- Extraction methods: `json_array_direct`: 567, `markdown_code_block`: 33, `regex_json_array`: 15
- Verbosity: 0/615 calls over 1024 output tokens (0.0%); 0 hit max_tokens (0.0%); 0 salvaged from truncated JSON (0.0%)
- Schema violations: 461
- Extra keys observed: end_text, sponsor

#### `nvidia/llama-3.3-nemotron-super-49b-v1.5`

- F1 (avg across episodes): **0.375**
- Total cost / episode: **$0.0000**
- p50 / p95 latency: 22.79s / 85.74s
- JSON compliance: 0.71
- Parse failure rate: 15.0%
- Extraction methods: `json_array_direct`: 321, `json_object_single_ad_truncated`: 1, `markdown_code_block`: 182, `parse_failure`: 92, `regex_json_array`: 19
- Verbosity: 338/615 calls over 1024 output tokens (55.0%); 24 hit max_tokens (3.9%); 1 salvaged from truncated JSON (0.2%)
- Schema violations: 266
- Extra keys observed: end_text, sponsor

#### `mistralai/mistral-medium-3.1`

- F1 (avg across episodes): **0.367**
- Total cost / episode: **$0.0000**
- p50 / p95 latency: 0.93s / 5.97s
- JSON compliance: 1.00
- Parse failure rate: 0.0%
- Extraction methods: `json_array_direct`: 615
- Verbosity: 0/615 calls over 1024 output tokens (0.0%); 0 hit max_tokens (0.0%); 0 salvaged from truncated JSON (0.0%)
- Schema violations: 608
- Extra keys observed: end_text, sponsor

#### `claude-haiku-4-5-20251001`

- F1 (avg across episodes): **0.340**
- Total cost / episode: **$0.8508**
- p50 / p95 latency: 1.29s / 3.93s
- JSON compliance: 0.60
- Parse failure rate: 0.0%
- Extraction methods: `markdown_code_block`: 615
- Verbosity: 0/615 calls over 1024 output tokens (0.0%); 0 hit max_tokens (0.0%); 0 salvaged from truncated JSON (0.0%)
- Schema violations: 711
- Extra keys observed: end_text, sponsor

#### `meta-llama/llama-3.3-70b-instruct`

- F1 (avg across episodes): **0.298**
- Total cost / episode: **$0.4599**
- p50 / p95 latency: 1.51s / 5.60s
- JSON compliance: 0.67
- Parse failure rate: 21.0%
- Extraction methods: `json_array_direct`: 117, `json_object_no_ads`: 126, `json_object_single_ad`: 242, `parse_failure`: 129, `regex_json_array`: 1
- Verbosity: 1/615 calls over 1024 output tokens (0.2%); 0 hit max_tokens (0.0%); 0 salvaged from truncated JSON (0.0%)
- Schema violations: 378
- Extra keys observed: end_text, sponsor

#### `mistralai/codestral-2508`

- F1 (avg across episodes): **0.284**
- Total cost / episode: **$0.2296**
- p50 / p95 latency: 0.77s / 2.06s
- JSON compliance: 1.00
- Parse failure rate: 0.0%
- Extraction methods: `json_array_direct`: 615
- Verbosity: 0/615 calls over 1024 output tokens (0.0%); 0 hit max_tokens (0.0%); 0 salvaged from truncated JSON (0.0%)
- Schema violations: 622
- Extra keys observed: end_text, sponsor

#### `deepseek/deepseek-v3.2`

- F1 (avg across episodes): **0.279**
- Total cost / episode: **$0.4507**
- p50 / p95 latency: 2.19s / 5.93s
- JSON compliance: 0.92
- Parse failure rate: 0.0%
- Extraction methods: `json_array_direct`: 440, `json_object_ads_key`: 8, `json_object_single_ad`: 167
- Verbosity: 1/615 calls over 1024 output tokens (0.2%); 0 hit max_tokens (0.0%); 0 salvaged from truncated JSON (0.0%)
- Schema violations: 263
- Extra keys observed: end_text, sponsor

#### `google/gemini-2.5-flash`

- F1 (avg across episodes): **0.246**
- Total cost / episode: **$0.2425**
- p50 / p95 latency: 0.93s / 3.65s
- JSON compliance: 1.00
- Parse failure rate: 0.0%
- Extraction methods: `json_array_direct`: 615
- Verbosity: 0/615 calls over 1024 output tokens (0.0%); 0 hit max_tokens (0.0%); 0 salvaged from truncated JSON (0.0%)
- Schema violations: 710
- Extra keys observed: end_text, sponsor

#### `openai/gpt-3.5-turbo`

- F1 (avg across episodes): **0.221**
- Total cost / episode: **$0.3621**
- p50 / p95 latency: 1.26s / 2.00s
- JSON compliance: 0.70
- Parse failure rate: 0.0%
- Extraction methods: `json_object_no_ads`: 9, `json_object_single_ad`: 606
- Verbosity: 0/615 calls over 1024 output tokens (0.0%); 0 hit max_tokens (0.0%); 0 salvaged from truncated JSON (0.0%)
- Schema violations: 593
- Extra keys observed: end_text, sponsor

#### `meta-llama/llama-4-scout`

- F1 (avg across episodes): **0.220**
- Total cost / episode: **$0.0000**
- p50 / p95 latency: 0.84s / 3.99s
- JSON compliance: 0.81
- Parse failure rate: 0.2%
- Extraction methods: `bracket_fallback`: 30, `json_array_direct`: 4, `json_object_ads_key`: 461, `json_object_no_ads`: 56, `json_object_single_ad`: 58, `parse_failure`: 1, `regex_json_array`: 5
- Verbosity: 1/615 calls over 1024 output tokens (0.2%); 0 hit max_tokens (0.0%); 0 salvaged from truncated JSON (0.0%)
- Schema violations: 507
- Extra keys observed: end_text, sponsor

#### `deepseek/deepseek-r1-distill-llama-70b`

- F1 (avg across episodes): **0.206**
- Total cost / episode: **$0.5333**
- p50 / p95 latency: 2.00s / 25.81s
- JSON compliance: 0.74
- Parse failure rate: 1.3%
- Extraction methods: `json_array_direct`: 20, `json_object_ads_key`: 44, `json_object_no_ads`: 58, `json_object_single_ad`: 482, `json_object_single_ad_truncated`: 2, `parse_failure`: 8, `regex_json_array`: 1
- Verbosity: 30/615 calls over 1024 output tokens (4.9%); 7 hit max_tokens (1.1%); 2 salvaged from truncated JSON (0.3%)
- Schema violations: 474
- Extra keys observed: end_text, sponsor

#### `mistralai/mistral-large-2512`

- F1 (avg across episodes): **0.202**
- Total cost / episode: **$0.3977**
- p50 / p95 latency: 2.73s / 6.39s
- JSON compliance: 1.00
- Parse failure rate: 0.0%
- Extraction methods: `json_array_direct`: 615
- Verbosity: 0/615 calls over 1024 output tokens (0.0%); 0 hit max_tokens (0.0%); 0 salvaged from truncated JSON (0.0%)
- Schema violations: 1342
- Extra keys observed: end_text, sponsor

#### `nvidia/nemotron-nano-9b-v2`

- F1 (avg across episodes): **0.186**
- Total cost / episode: **$0.0000**
- p50 / p95 latency: 12.29s / 37.48s
- JSON compliance: 0.90
- Parse failure rate: 8.5%
- Extraction methods: `json_array_direct`: 541, `json_object_single_ad_truncated`: 11, `parse_failure`: 52, `regex_json_array`: 11
- Verbosity: 375/615 calls over 1024 output tokens (61.0%); 7 hit max_tokens (1.1%); 11 salvaged from truncated JSON (1.8%)
- Schema violations: 476
- Extra keys observed: end_text, sponsor

#### `meta-llama/llama-3.1-8b-instruct`

- F1 (avg across episodes): **0.166**
- Total cost / episode: **$0.1430**
- p50 / p95 latency: 0.81s / 4.09s
- JSON compliance: 0.86
- Parse failure rate: 0.2%
- Extraction methods: `json_array_direct`: 275, `json_object_no_ads`: 63, `json_object_single_ad`: 276, `parse_failure`: 1
- Verbosity: 22/615 calls over 1024 output tokens (3.6%); 0 hit max_tokens (0.0%); 0 salvaged from truncated JSON (0.0%)
- Schema violations: 1270
- Extra keys observed: end_text, sponsor

#### `cohere/command-r-plus-08-2024`

- F1 (avg across episodes): **0.136**
- Total cost / episode: **$1.8262**
- p50 / p95 latency: 0.96s / 3.45s
- JSON compliance: 0.97
- Parse failure rate: 0.0%
- Extraction methods: `json_object_ads_key`: 16, `json_object_no_ads`: 554, `json_object_single_ad`: 45
- Verbosity: 0/615 calls over 1024 output tokens (0.0%); 0 hit max_tokens (0.0%); 0 salvaged from truncated JSON (0.0%)
- Schema violations: 132
- Extra keys observed: end_text, sponsor

#### `openai/o4-mini`

- F1 (avg across episodes): **0.095**
- Total cost / episode: **$1.3462**
- p50 / p95 latency: 7.22s / 24.00s
- JSON compliance: 0.05
- Parse failure rate: 93.2%
- Extraction methods: `json_object_no_ads`: 12, `json_object_single_ad`: 30, `parse_failure`: 573
- Verbosity: 255/615 calls over 1024 output tokens (41.5%); 0 hit max_tokens (0.0%); 0 salvaged from truncated JSON (0.0%)
- Schema violations: 28
- Extra keys observed: end_text, sponsor

#### `microsoft/phi-4`

- F1 (avg across episodes): **0.050**
- Total cost / episode: **$0.1051**
- p50 / p95 latency: 2.32s / 11.72s
- JSON compliance: 0.86
- Parse failure rate: 1.0%
- Extraction methods: `json_array_direct`: 291, `json_object_ads_key`: 29, `json_object_no_ads`: 27, `json_object_segments_key`: 19, `json_object_single_ad`: 234, `json_object_window_segments`: 2, `parse_failure`: 6, `regex_json_array`: 7
- Verbosity: 13/615 calls over 1024 output tokens (2.1%); 6 hit max_tokens (1.0%); 0 salvaged from truncated JSON (0.0%)
- Schema violations: 412
- Extra keys observed: end_text, sponsor

#### `mistralai/mistral-7b-instruct-v0.1`

- F1 (avg across episodes): **0.000**
- Total cost / episode: **$0.0000**
- p50 / p95 latency: 8.58s / 34.46s
- JSON compliance: 0.11
- Parse failure rate: 72.7%
- Extraction methods: `bracket_fallback`: 1, `parse_failure`: 447, `regex_json_array`: 167
- Verbosity: 13/615 calls over 1024 output tokens (2.1%); 0 hit max_tokens (0.0%); 0 salvaged from truncated JSON (0.0%)


### Per-Episode Detail

One subsection per episode in the corpus, showing how every model performed on that specific episode. For ad-bearing episodes you see F1 and the stdev across trials (low stdev means stable, high stdev means the model's number on this episode is noisy). For the no-ad episode you see PASS / FAIL on the negative control: PASS = zero false positives across all windows, FAIL = the model flagged something that wasn't an ad, with the count.

#### `ep-ai-cloud-essentials-e8dc897fbd6b`: How Physical AI is Streamlining Engineering

- Podcast: ai-cloud-essentials
- Duration: 16.4 min
- Truth: no-ads episode

| Model | Result | FP count |
|-------|--------|----------|
| `mistralai/mistral-medium-3.1` | PASS | 0 |
| `claude-haiku-4-5-20251001` | PASS | 0 |
| `claude-opus-4-7` | PASS | 0 |
| `meta-llama/llama-4-scout` | PASS | 0 |
| `x-ai/grok-4.3` | PASS | 0 |
| `deepseek/deepseek-v3.2` | PASS | 0 |
| `openai/o4-mini` | PASS | 0 |
| `mistralai/mistral-large-2512` | PASS | 0 |
| `claude-sonnet-4-6` | PASS | 0 |
| `google/gemini-2.5-flash` | PASS | 0 |
| `nvidia/llama-3.3-nemotron-super-49b-v1.5` | PASS | 0 |
| `openai/o3` | PASS | 0 |
| `mistralai/mistral-7b-instruct-v0.1` | PASS | 0 |
| `meta-llama/llama-3.3-70b-instruct` | PASS | 0 |
| `mistralai/codestral-2508` | PASS | 0 |
| `cohere/command-r-plus-08-2024` | PASS | 0 |
| `qwen/qwen3.5-plus-02-15` | PASS | 0 |
| `meta-llama/llama-3.1-8b-instruct` | PASS | 0 |
| `google/gemma-4-31b-it` | FAIL | 1 |
| `deepseek/deepseek-v4-flash` | FAIL | 1 |
| `google/gemini-2.5-pro` | FAIL | 1 |
| `meta-llama/llama-4-maverick` | FAIL | 1 |
| `moonshotai/kimi-k2.6` | FAIL | 1 |
| `nvidia/nemotron-nano-9b-v2` | FAIL | 1 |
| `deepseek/deepseek-r1` | FAIL | 1 |
| `openai/gpt-5.5` | FAIL | 1 |
| `openai/gpt-5.4` | FAIL | 1 |
| `deepseek/deepseek-r1-distill-llama-70b` | FAIL | 2 |
| `openai/gpt-3.5-turbo` | FAIL | 3 |
| `cohere/command-a` | FAIL | 3 |
| `microsoft/phi-4` | FAIL | 3 |
| `deepseek/deepseek-r1-0528` | FAIL | 27 |

#### `ep-daily-tech-news-show-b576979e1fe8`: Motorola Razr Fold is a Noble Competitor to the Galaxy Z Fold 7 - DTNS 5269

- Podcast: daily-tech-news-show
- Duration: 34.6 min
- Truth ads: 4

| Model | F1 | F1 stdev |
|-------|----|----------|
| `moonshotai/kimi-k2.6` | 0.914 | 0.078 |
| `openai/gpt-5.4` | 0.892 | 0.062 |
| `claude-opus-4-7` | 0.886 | 0.064 |
| `openai/gpt-5.5` | 0.886 | 0.064 |
| `google/gemini-2.5-pro` | 0.864 | 0.089 |
| `qwen/qwen3.5-plus-02-15` | 0.857 | 0.000 |
| `openai/o3` | 0.848 | 0.119 |
| `google/gemma-4-31b-it` | 0.811 | 0.132 |
| `meta-llama/llama-4-maverick` | 0.771 | 0.048 |
| `deepseek/deepseek-r1-0528` | 0.700 | 0.245 |
| `deepseek/deepseek-r1` | 0.658 | 0.259 |
| `x-ai/grok-4.3` | 0.507 | 0.246 |
| `cohere/command-a` | 0.500 | 0.000 |
| `deepseek/deepseek-v4-flash` | 0.445 | 0.191 |
| `claude-sonnet-4-6` | 0.407 | 0.128 |
| `mistralai/codestral-2508` | 0.379 | 0.069 |
| `deepseek/deepseek-v3.2` | 0.327 | 0.211 |
| `meta-llama/llama-3.3-70b-instruct` | 0.327 | 0.095 |
| `nvidia/llama-3.3-nemotron-super-49b-v1.5` | 0.299 | 0.206 |
| `nvidia/nemotron-nano-9b-v2` | 0.274 | 0.194 |
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
| `mistralai/mistral-7b-instruct-v0.1` | 0.000 | 0.000 |
| `cohere/command-r-plus-08-2024` | 0.000 | 0.000 |

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
| `openai/o3` | 0.472 | 0.150 |
| `google/gemini-2.5-pro` | 0.448 | 0.018 |
| `claude-opus-4-7` | 0.445 | 0.096 |
| `openai/gpt-3.5-turbo` | 0.364 | 0.000 |
| `cohere/command-r-plus-08-2024` | 0.313 | 0.155 |
| `deepseek/deepseek-v4-flash` | 0.310 | 0.043 |
| `cohere/command-a` | 0.298 | 0.090 |
| `claude-sonnet-4-6` | 0.237 | 0.037 |
| `deepseek/deepseek-r1-distill-llama-70b` | 0.205 | 0.017 |
| `meta-llama/llama-4-maverick` | 0.204 | 0.010 |
| `deepseek/deepseek-r1-0528` | 0.184 | 0.066 |
| `x-ai/grok-4.3` | 0.179 | 0.092 |
| `nvidia/llama-3.3-nemotron-super-49b-v1.5` | 0.177 | 0.215 |
| `mistralai/codestral-2508` | 0.172 | 0.046 |
| `deepseek/deepseek-r1` | 0.158 | 0.037 |
| `deepseek/deepseek-v3.2` | 0.140 | 0.142 |
| `meta-llama/llama-4-scout` | 0.130 | 0.115 |
| `google/gemma-4-31b-it` | 0.119 | 0.058 |
| `mistralai/mistral-medium-3.1` | 0.095 | 0.010 |
| `mistralai/mistral-large-2512` | 0.074 | 0.000 |
| `claude-haiku-4-5-20251001` | 0.073 | 0.001 |
| `google/gemini-2.5-flash` | 0.071 | 0.000 |
| `nvidia/nemotron-nano-9b-v2` | 0.069 | 0.098 |
| `openai/o4-mini` | 0.067 | 0.149 |
| `microsoft/phi-4` | 0.056 | 0.077 |
| `meta-llama/llama-3.1-8b-instruct` | 0.029 | 0.064 |
| `mistralai/mistral-7b-instruct-v0.1` | 0.000 | 0.000 |
| `meta-llama/llama-3.3-70b-instruct` | 0.000 | 0.000 |

#### `ep-glt1412515089-373d5ba5007b`: #2496 - Julia Mossbridge

- Podcast: glt1412515089
- Duration: 165.3 min
- Truth ads: 4

| Model | F1 | F1 stdev |
|-------|----|----------|
| `mistralai/mistral-medium-3.1` | 0.671 | 0.098 |
| `openai/o3` | 0.664 | 0.063 |
| `google/gemini-2.5-pro` | 0.646 | 0.028 |
| `google/gemma-4-31b-it` | 0.645 | 0.085 |
| `deepseek/deepseek-v4-flash` | 0.642 | 0.072 |
| `openai/gpt-5.5` | 0.636 | 0.143 |
| `deepseek/deepseek-r1` | 0.630 | 0.067 |
| `qwen/qwen3.5-plus-02-15` | 0.625 | 0.057 |
| `claude-opus-4-7` | 0.600 | 0.000 |
| `nvidia/llama-3.3-nemotron-super-49b-v1.5` | 0.596 | 0.074 |
| `x-ai/grok-4.3` | 0.572 | 0.131 |
| `claude-haiku-4-5-20251001` | 0.571 | 0.000 |
| `meta-llama/llama-3.3-70b-instruct` | 0.567 | 0.030 |
| `deepseek/deepseek-v3.2` | 0.518 | 0.085 |
| `cohere/command-a` | 0.507 | 0.027 |
| `meta-llama/llama-4-maverick` | 0.507 | 0.015 |
| `openai/gpt-5.4` | 0.506 | 0.059 |
| `google/gemini-2.5-flash` | 0.500 | 0.000 |
| `moonshotai/kimi-k2.6` | 0.469 | 0.066 |
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
| `mistralai/mistral-7b-instruct-v0.1` | 0.000 | 0.000 |
| `cohere/command-r-plus-08-2024` | 0.000 | 0.000 |

#### `ep-it-s-a-thing-e339179dfad6`: SOUP shots - It's a Thing 418

- Podcast: it-s-a-thing
- Duration: 26.7 min
- Truth ads: 1

| Model | F1 | F1 stdev |
|-------|----|----------|
| `google/gemini-2.5-pro` | 0.667 | 0.000 |
| `claude-opus-4-7` | 0.667 | 0.000 |
| `openai/gpt-5.5` | 0.667 | 0.000 |
| `qwen/qwen3.5-plus-02-15` | 0.667 | 0.000 |
| `openai/gpt-5.4` | 0.613 | 0.119 |
| `google/gemma-4-31b-it` | 0.467 | 0.274 |
| `x-ai/grok-4.3` | 0.433 | 0.253 |
| `deepseek/deepseek-r1-0528` | 0.404 | 0.281 |
| `deepseek/deepseek-v3.2` | 0.400 | 0.548 |
| `cohere/command-a` | 0.400 | 0.000 |
| `meta-llama/llama-3.1-8b-instruct` | 0.400 | 0.548 |
| `deepseek/deepseek-v4-flash` | 0.337 | 0.239 |
| `openai/o3` | 0.333 | 0.471 |
| `deepseek/deepseek-r1` | 0.313 | 0.301 |
| `nvidia/llama-3.3-nemotron-super-49b-v1.5` | 0.233 | 0.325 |
| `moonshotai/kimi-k2.6` | 0.200 | 0.274 |
| `mistralai/mistral-medium-3.1` | 0.000 | 0.000 |
| `claude-haiku-4-5-20251001` | 0.000 | 0.000 |
| `meta-llama/llama-4-maverick` | 0.000 | 0.000 |
| `deepseek/deepseek-r1-distill-llama-70b` | 0.000 | 0.000 |
| `openai/gpt-3.5-turbo` | 0.000 | 0.000 |
| `meta-llama/llama-4-scout` | 0.000 | 0.000 |
| `openai/o4-mini` | 0.000 | 0.000 |
| `nvidia/nemotron-nano-9b-v2` | 0.000 | 0.000 |
| `mistralai/mistral-large-2512` | 0.000 | 0.000 |
| `claude-sonnet-4-6` | 0.000 | 0.000 |
| `google/gemini-2.5-flash` | 0.000 | 0.000 |
| `microsoft/phi-4` | 0.000 | 0.000 |
| `mistralai/mistral-7b-instruct-v0.1` | 0.000 | 0.000 |
| `meta-llama/llama-3.3-70b-instruct` | 0.000 | 0.000 |
| `mistralai/codestral-2508` | 0.000 | 0.000 |
| `cohere/command-r-plus-08-2024` | 0.000 | 0.000 |

#### `ep-on-air-with-dan-and-alex2-574e4f303730`: Ryanair Wants Alcohol Bans, Emirates' $6.8B Record Profit & Buying Spirit Airlines?!

- Podcast: on-air-with-dan-and-alex2
- Duration: 58.1 min
- Truth ads: 2

| Model | F1 | F1 stdev |
|-------|----|----------|
| `openai/gpt-5.4` | 0.747 | 0.073 |
| `moonshotai/kimi-k2.6` | 0.734 | 0.200 |
| `openai/o3` | 0.687 | 0.124 |
| `claude-opus-4-7` | 0.595 | 0.071 |
| `qwen/qwen3.5-plus-02-15` | 0.590 | 0.043 |
| `meta-llama/llama-4-maverick` | 0.571 | 0.000 |
| `openai/gpt-5.5` | 0.571 | 0.000 |
| `deepseek/deepseek-r1` | 0.555 | 0.176 |
| `google/gemini-2.5-pro` | 0.543 | 0.039 |
| `deepseek/deepseek-v4-flash` | 0.535 | 0.112 |
| `google/gemma-4-31b-it` | 0.514 | 0.032 |
| `deepseek/deepseek-r1-0528` | 0.514 | 0.122 |
| `cohere/command-a` | 0.503 | 0.045 |
| `mistralai/mistral-medium-3.1` | 0.500 | 0.000 |
| `claude-haiku-4-5-20251001` | 0.500 | 0.000 |
| `x-ai/grok-4.3` | 0.472 | 0.066 |
| `mistralai/codestral-2508` | 0.469 | 0.045 |
| `mistralai/mistral-large-2512` | 0.444 | 0.000 |
| `claude-sonnet-4-6` | 0.444 | 0.000 |
| `google/gemini-2.5-flash` | 0.444 | 0.000 |
| `nvidia/llama-3.3-nemotron-super-49b-v1.5` | 0.431 | 0.109 |
| `openai/gpt-3.5-turbo` | 0.400 | 0.000 |
| `nvidia/nemotron-nano-9b-v2` | 0.361 | 0.091 |
| `deepseek/deepseek-r1-distill-llama-70b` | 0.343 | 0.062 |
| `deepseek/deepseek-v3.2` | 0.300 | 0.274 |
| `meta-llama/llama-3.1-8b-instruct` | 0.284 | 0.166 |
| `microsoft/phi-4` | 0.213 | 0.307 |
| `openai/o4-mini` | 0.133 | 0.298 |
| `meta-llama/llama-3.3-70b-instruct` | 0.133 | 0.298 |
| `meta-llama/llama-4-scout` | 0.094 | 0.130 |
| `mistralai/mistral-7b-instruct-v0.1` | 0.000 | 0.000 |
| `cohere/command-r-plus-08-2024` | 0.000 | 0.000 |

#### `ep-oxide-and-friends-ce789ff5b62e`: Mechanical Engineering at Oxide [chapter images]

- Podcast: oxide-and-friends
- Duration: 84.5 min
- Truth: no-ads episode

| Model | Result | FP count |
|-------|--------|----------|
| `mistralai/mistral-medium-3.1` | PASS | 0 |
| `google/gemma-4-31b-it` | PASS | 0 |
| `claude-haiku-4-5-20251001` | PASS | 0 |
| `deepseek/deepseek-v4-flash` | PASS | 0 |
| `meta-llama/llama-4-maverick` | PASS | 0 |
| `claude-opus-4-7` | PASS | 0 |
| `meta-llama/llama-4-scout` | PASS | 0 |
| `x-ai/grok-4.3` | PASS | 0 |
| `cohere/command-a` | PASS | 0 |
| `openai/o4-mini` | PASS | 0 |
| `nvidia/nemotron-nano-9b-v2` | PASS | 0 |
| `mistralai/mistral-large-2512` | PASS | 0 |
| `claude-sonnet-4-6` | PASS | 0 |
| `google/gemini-2.5-flash` | PASS | 0 |
| `nvidia/llama-3.3-nemotron-super-49b-v1.5` | PASS | 0 |
| `openai/o3` | PASS | 0 |
| `openai/gpt-5.5` | PASS | 0 |
| `mistralai/mistral-7b-instruct-v0.1` | PASS | 0 |
| `meta-llama/llama-3.3-70b-instruct` | PASS | 0 |
| `mistralai/codestral-2508` | PASS | 0 |
| `cohere/command-r-plus-08-2024` | PASS | 0 |
| `qwen/qwen3.5-plus-02-15` | PASS | 0 |
| `meta-llama/llama-3.1-8b-instruct` | PASS | 0 |
| `google/gemini-2.5-pro` | FAIL | 1 |
| `deepseek/deepseek-r1` | FAIL | 1 |
| `openai/gpt-5.4` | FAIL | 1 |
| `deepseek/deepseek-v3.2` | FAIL | 2 |
| `moonshotai/kimi-k2.6` | FAIL | 4 |
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
| `meta-llama/llama-3.3-70b-instruct` | 0.512 | 0.035 |
| `openai/gpt-5.5` | 0.505 | 0.047 |
| `google/gemma-4-31b-it` | 0.496 | 0.021 |
| `meta-llama/llama-4-maverick` | 0.496 | 0.021 |
| `openai/gpt-5.4` | 0.495 | 0.022 |
| `x-ai/grok-4.3` | 0.486 | 0.013 |
| `qwen/qwen3.5-plus-02-15` | 0.476 | 0.000 |
| `deepseek/deepseek-v4-flash` | 0.465 | 0.046 |
| `deepseek/deepseek-r1` | 0.462 | 0.044 |
| `google/gemini-2.5-flash` | 0.455 | 0.000 |
| `google/gemini-2.5-pro` | 0.451 | 0.009 |
| `mistralai/codestral-2508` | 0.374 | 0.032 |
| `cohere/command-a` | 0.368 | 0.015 |
| `deepseek/deepseek-r1-0528` | 0.281 | 0.103 |
| `deepseek/deepseek-r1-distill-llama-70b` | 0.262 | 0.018 |
| `nvidia/nemotron-nano-9b-v2` | 0.238 | 0.144 |
| `openai/gpt-3.5-turbo` | 0.217 | 0.004 |
| `mistralai/mistral-large-2512` | 0.209 | 0.017 |
| `moonshotai/kimi-k2.6` | 0.196 | 0.245 |
| `meta-llama/llama-3.1-8b-instruct` | 0.133 | 0.052 |
| `openai/o4-mini` | 0.114 | 0.156 |
| `microsoft/phi-4` | 0.033 | 0.045 |
| `mistralai/mistral-7b-instruct-v0.1` | 0.000 | 0.000 |

#### `ep-the-brilliant-idiots-0bb9bf634c8e`: Class Rank

- Podcast: the-brilliant-idiots
- Duration: 119.9 min
- Truth ads: 3

| Model | F1 | F1 stdev |
|-------|----|----------|
| `claude-sonnet-4-6` | 0.836 | 0.048 |
| `openai/gpt-5.5` | 0.776 | 0.081 |
| `x-ai/grok-4.3` | 0.771 | 0.048 |
| `qwen/qwen3.5-plus-02-15` | 0.771 | 0.048 |
| `claude-opus-4-7` | 0.733 | 0.037 |
| `openai/o3` | 0.698 | 0.273 |
| `claude-haiku-4-5-20251001` | 0.600 | 0.000 |
| `mistralai/mistral-medium-3.1` | 0.591 | 0.046 |
| `google/gemma-4-31b-it` | 0.585 | 0.077 |
| `deepseek/deepseek-v4-flash` | 0.569 | 0.045 |
| `moonshotai/kimi-k2.6` | 0.538 | 0.263 |
| `openai/gpt-5.4` | 0.516 | 0.110 |
| `google/gemini-2.5-pro` | 0.510 | 0.036 |
| `meta-llama/llama-3.3-70b-instruct` | 0.489 | 0.025 |
| `nvidia/llama-3.3-nemotron-super-49b-v1.5` | 0.478 | 0.160 |
| `deepseek/deepseek-r1` | 0.401 | 0.043 |
| `meta-llama/llama-4-maverick` | 0.390 | 0.101 |
| `meta-llama/llama-4-scout` | 0.349 | 0.080 |
| `cohere/command-a` | 0.247 | 0.013 |
| `deepseek/deepseek-r1-distill-llama-70b` | 0.222 | 0.056 |
| `openai/gpt-3.5-turbo` | 0.220 | 0.005 |
| `nvidia/nemotron-nano-9b-v2` | 0.205 | 0.056 |
| `mistralai/mistral-large-2512` | 0.193 | 0.041 |
| `mistralai/codestral-2508` | 0.187 | 0.024 |
| `openai/o4-mini` | 0.180 | 0.249 |
| `deepseek/deepseek-r1-0528` | 0.161 | 0.084 |
| `google/gemini-2.5-flash` | 0.125 | 0.000 |
| `deepseek/deepseek-v3.2` | 0.100 | 0.224 |
| `microsoft/phi-4` | 0.067 | 0.092 |
| `mistralai/mistral-7b-instruct-v0.1` | 0.000 | 0.000 |
| `cohere/command-r-plus-08-2024` | 0.000 | 0.000 |
| `meta-llama/llama-3.1-8b-instruct` | 0.000 | 0.000 |

#### `ep-the-tim-dillon-show-f62bd5fa1cfe`: 495 - Hantavirus Cruise & iPad Babies

- Podcast: the-tim-dillon-show
- Duration: 80.1 min
- Truth ads: 6

| Model | F1 | F1 stdev |
|-------|----|----------|
| `qwen/qwen3.5-plus-02-15` | 0.636 | 0.028 |
| `claude-opus-4-7` | 0.592 | 0.052 |
| `openai/gpt-5.4` | 0.586 | 0.077 |
| `google/gemini-2.5-pro` | 0.569 | 0.063 |
| `openai/gpt-5.5` | 0.546 | 0.063 |
| `x-ai/grok-4.3` | 0.393 | 0.123 |
| `openai/o3` | 0.372 | 0.081 |
| `google/gemma-4-31b-it` | 0.350 | 0.072 |
| `deepseek/deepseek-r1` | 0.327 | 0.195 |
| `deepseek/deepseek-r1-0528` | 0.319 | 0.119 |
| `meta-llama/llama-3.3-70b-instruct` | 0.308 | 0.000 |
| `deepseek/deepseek-v4-flash` | 0.298 | 0.084 |
| `mistralai/mistral-medium-3.1` | 0.223 | 0.054 |
| `cohere/command-a` | 0.200 | 0.078 |
| `moonshotai/kimi-k2.6` | 0.184 | 0.105 |
| `claude-sonnet-4-6` | 0.179 | 0.004 |
| `mistralai/codestral-2508` | 0.178 | 0.065 |
| `nvidia/llama-3.3-nemotron-super-49b-v1.5` | 0.171 | 0.125 |
| `meta-llama/llama-4-maverick` | 0.167 | 0.000 |
| `deepseek/deepseek-r1-distill-llama-70b` | 0.163 | 0.159 |
| `claude-haiku-4-5-20251001` | 0.154 | 0.000 |
| `google/gemini-2.5-flash` | 0.154 | 0.000 |
| `openai/gpt-3.5-turbo` | 0.125 | 0.000 |
| `microsoft/phi-4` | 0.079 | 0.072 |
| `deepseek/deepseek-v3.2` | 0.057 | 0.079 |
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
| `qwen/qwen3.5-plus-02-15` | 0.696 | 0.094 |
| `deepseek/deepseek-r1-0528` | 0.643 | 0.158 |
| `google/gemini-2.5-pro` | 0.603 | 0.116 |
| `openai/gpt-5.5` | 0.587 | 0.084 |
| `x-ai/grok-4.3` | 0.585 | 0.075 |
| `deepseek/deepseek-v4-flash` | 0.574 | 0.137 |
| `openai/gpt-5.4` | 0.569 | 0.070 |
| `mistralai/codestral-2508` | 0.564 | 0.149 |
| `cohere/command-a` | 0.533 | 0.122 |
| `claude-opus-4-7` | 0.524 | 0.135 |
| `openai/o3` | 0.464 | 0.106 |
| `deepseek/deepseek-r1` | 0.435 | 0.066 |
| `mistralai/mistral-medium-3.1` | 0.421 | 0.048 |
| `meta-llama/llama-4-maverick` | 0.400 | 0.000 |
| `nvidia/llama-3.3-nemotron-super-49b-v1.5` | 0.399 | 0.223 |
| `claude-haiku-4-5-20251001` | 0.375 | 0.000 |
| `claude-sonnet-4-6` | 0.375 | 0.000 |
| `mistralai/mistral-large-2512` | 0.353 | 0.000 |
| `meta-llama/llama-3.3-70b-instruct` | 0.347 | 0.087 |
| `deepseek/deepseek-r1-distill-llama-70b` | 0.340 | 0.152 |
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
| `mistralai/mistral-7b-instruct-v0.1` | 0.000 | 0.000 |


### Parser stress test

How each model's responses were actually parsed. Columns are extraction methods, ordered alphabetically; rows are models, sorted by parse-failure rate (cleanest at top). `json_array_direct` is the happy path: a bare JSON array we could `json.loads` and process immediately. `markdown_code_block` means we had to strip triple-backtick fences first; `json_object_*` means the model wrapped the array in an outer object and we had to find the array key; `regex_*` are last-resort recovery paths. A model that needs anything but `json_array_direct` for most calls is fragile. It works today, but a small prompt change can break the parser.

| Model | bracket_fallback | json_array_direct | json_object_ads_key | json_object_no_ads | json_object_segments_key | json_object_single_ad | json_object_single_ad_truncated | json_object_window_segments | markdown_code_block | parse_failure | regex_json_array |
|---|---|---|---|---|---|---|---|---|---|---|---|
| `mistralai/mistral-medium-3.1` | 0 | 615 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| `google/gemma-4-31b-it` | 0 | 0 | 332 | 154 | 0 | 128 | 1 | 0 | 0 | 0 | 0 |
| `claude-haiku-4-5-20251001` | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 615 | 0 | 0 |
| `meta-llama/llama-4-maverick` | 0 | 0 | 0 | 184 | 0 | 431 | 0 | 0 | 0 | 0 | 0 |
| `claude-opus-4-7` | 0 | 613 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 2 |
| `openai/gpt-3.5-turbo` | 0 | 0 | 0 | 9 | 0 | 606 | 0 | 0 | 0 | 0 | 0 |
| `deepseek/deepseek-v3.2` | 0 | 440 | 8 | 0 | 0 | 167 | 0 | 0 | 0 | 0 | 0 |
| `cohere/command-a` | 0 | 0 | 0 | 17 | 0 | 598 | 0 | 0 | 0 | 0 | 0 |
| `mistralai/mistral-large-2512` | 0 | 615 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| `claude-sonnet-4-6` | 0 | 567 | 0 | 0 | 0 | 0 | 0 | 0 | 33 | 0 | 15 |
| `google/gemini-2.5-flash` | 0 | 615 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| `mistralai/codestral-2508` | 0 | 615 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| `cohere/command-r-plus-08-2024` | 0 | 0 | 16 | 554 | 0 | 45 | 0 | 0 | 0 | 0 | 0 |
| `openai/gpt-5.4` | 0 | 0 | 0 | 211 | 0 | 404 | 0 | 0 | 0 | 0 | 0 |
| `meta-llama/llama-4-scout` | 30 | 4 | 461 | 56 | 0 | 58 | 0 | 0 | 0 | 1 | 5 |
| `x-ai/grok-4.3` | 0 | 614 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 1 | 0 |
| `openai/gpt-5.5` | 0 | 0 | 0 | 353 | 0 | 261 | 0 | 0 | 0 | 1 | 0 |
| `qwen/qwen3.5-plus-02-15` | 0 | 614 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 1 | 0 |
| `meta-llama/llama-3.1-8b-instruct` | 0 | 275 | 0 | 63 | 0 | 276 | 0 | 0 | 0 | 1 | 0 |
| `google/gemini-2.5-pro` | 0 | 590 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 3 | 22 |
| `openai/o3` | 0 | 0 | 20 | 459 | 9 | 123 | 0 | 0 | 0 | 4 | 0 |
| `deepseek/deepseek-r1` | 0 | 539 | 2 | 15 | 5 | 36 | 0 | 0 | 10 | 6 | 2 |
| `microsoft/phi-4` | 0 | 291 | 29 | 27 | 19 | 234 | 0 | 2 | 0 | 6 | 7 |
| `deepseek/deepseek-r1-distill-llama-70b` | 0 | 20 | 44 | 58 | 0 | 482 | 2 | 0 | 0 | 8 | 1 |
| `deepseek/deepseek-v4-flash` | 0 | 44 | 362 | 4 | 1 | 190 | 0 | 0 | 0 | 14 | 0 |
| `nvidia/nemotron-nano-9b-v2` | 0 | 541 | 0 | 0 | 0 | 0 | 11 | 0 | 0 | 52 | 11 |
| `deepseek/deepseek-r1-0528` | 0 | 503 | 28 | 3 | 0 | 25 | 2 | 0 | 1 | 53 | 0 |
| `nvidia/llama-3.3-nemotron-super-49b-v1.5` | 0 | 321 | 0 | 0 | 0 | 0 | 1 | 0 | 182 | 92 | 19 |
| `meta-llama/llama-3.3-70b-instruct` | 0 | 117 | 0 | 126 | 0 | 242 | 0 | 0 | 0 | 129 | 1 |
| `moonshotai/kimi-k2.6` | 0 | 44 | 21 | 91 | 2 | 296 | 0 | 0 | 3 | 158 | 0 |
| `mistralai/mistral-7b-instruct-v0.1` | 1 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 447 | 167 |
| `openai/o4-mini` | 0 | 0 | 0 | 12 | 0 | 30 | 0 | 0 | 0 | 573 | 0 |

### Deprecated Models

Historical data preserved; excluded from headline rankings.

- `x-ai/grok-4.1-fast`: F1 0.642, cost $0.1509/ep

## Methodology

Reproducibility settings used for this run. The benchmark sends the same prompts MinusPod sends in production (same system prompt, same sponsor list, same windowing) so the F1 numbers here are directly relevant to production accuracy decisions. Cost is recomputed at report time from token counts against the active pricing snapshot, so all rows compare at the same prices regardless of when the actual call ran.

- Trials per (model, episode): **5**, temperature 0.0
- max_tokens: 4096 (matches MinusPod production)
- response_format: json_object (with prompt-injection fallback when provider rejects native)
- Window size: 10 min, overlap: 3 min (imported from MinusPod's create_windows)
- Pricing snapshot: 2026-05-09T22:50:45.889333Z
- Corpus episodes: 11

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

- Report generated: 2026-05-15T23:29:46Z
- Unique work units (current state, last-write-wins after retries): 20295
- Raw rows in calls.jsonl: 20492 (197 superseded by later retries; kept for audit)
- Successful: 20130
- Failed: 165
- Lifetime actual spend (sum of at-runtime costs, includes superseded rows): $171.6756
- Active pricing snapshot: 2026-05-09T22:50:45.889333Z
