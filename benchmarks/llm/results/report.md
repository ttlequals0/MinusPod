# MinusPod LLM Benchmark Report

## Table of Contents

- [Metric Key](#metric-key)
- [TL;DR](#tldr)
- [Charts](#charts)
- [Failures and provider issues](#failures-and-provider-issues)
- [Precision, recall, and FP/FN breakdown](#precision-recall-and-fpfn-breakdown)
- [Confidence calibration](#confidence-calibration)
- [Latency tail](#latency-tail)
- [Output token efficiency](#output-token-efficiency)
- [Trial variance (determinism check)](#trial-variance-determinism-check)
- [Cross-model agreement](#cross-model-agreement)
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
| 1 | `deepseek/deepseek-v4-flash` | 0.000 | $0.0000 | 2.2s | 0.79 |
| 2 | `openai/gpt-5.5` | 0.000 | $0.0000 | 6.1s | 0.87 |
| 3 | `google/gemini-2.5-flash` | 0.000 | $0.0000 | 1.0s | 1.00 |
| 4 | `nvidia/nemotron-nano-9b-v2` | 0.000 | $0.0000 | 11.1s | 0.88 |
| 5 | `mistralai/mistral-large-2512` | 0.000 | $0.0000 | 3.1s | 1.00 |
| 6 | `meta-llama/llama-3.3-70b-instruct` | 0.000 | $0.0000 | 1.6s | 0.84 |
| 7 | `meta-llama/llama-3.1-8b-instruct` | 0.000 | $0.0000 | 0.7s | 0.89 |
| 8 | `openai/o3` | 0.000 | $0.0000 | 8.5s | 0.93 |
| 9 | `openai/o4-mini` | 0.000 | $0.0000 | 7.3s | 0.05 |
| 10 | `mistralai/mistral-medium-3.1` | 0.000 | $0.0000 | 0.9s | 1.00 |
| 11 | `deepseek/deepseek-v3.2` | 0.000 | $0.0000 | 2.1s | 0.94 |
| 12 | `nvidia/llama-3.3-nemotron-super-49b-v1.5` | 0.000 | $0.0000 | 23.1s | 0.72 |
| 13 | `mistralai/codestral-2508` | 0.000 | $0.0000 | 0.8s | 1.00 |
| 14 | `openai/gpt-3.5-turbo` | 0.000 | $0.0000 | 1.2s | 0.70 |
| 15 | `claude-opus-4-7` | 0.000 | $0.0000 | 2.3s | 1.00 |
| 16 | `openai/gpt-5.4` | 0.000 | $0.0000 | 1.8s | 0.79 |
| 17 | `claude-haiku-4-5-20251001` | 0.000 | $0.0000 | 1.4s | 0.60 |
| 18 | `deepseek/deepseek-r1` | 0.000 | $0.0000 | 19.3s | 0.96 |
| 19 | `qwen/qwen3.5-plus-02-15` | 0.000 | $0.0000 | 51.5s | 1.00 |
| 20 | `cohere/command-a` | 0.000 | $0.0000 | 3.6s | 0.70 |
| 21 | `deepseek/deepseek-r1-distill-llama-70b` | 0.000 | $0.0000 | 1.9s | 0.73 |
| 22 | `x-ai/grok-4.1-fast` | 0.000 | $0.0000 | 11.1s | 0.87 |
| 23 | `mistralai/mistral-7b-instruct-v0.1` | 0.000 | $0.0000 | 10.5s | 0.07 |
| 24 | `google/gemma-4-31b-it` | 0.000 | $0.0000 | 1.7s | 0.84 |
| 25 | `cohere/command-r-plus-08-2024` | 0.000 | $0.0000 | 1.0s | 0.97 |
| 26 | `meta-llama/llama-4-maverick` | 0.000 | $0.0000 | 1.3s | 0.76 |
| 27 | `deepseek/deepseek-r1-0528` | 0.000 | $0.0000 | 14.8s | 0.87 |
| 28 | `meta-llama/llama-4-scout` | 0.000 | $0.0000 | 0.8s | 0.81 |
| 29 | `google/gemini-2.5-pro` | 0.000 | $0.0000 | 13.5s | 0.97 |
| 30 | `claude-sonnet-4-6` | 0.000 | $0.0000 | 1.5s | 0.96 |
| 31 | `microsoft/phi-4` | 0.000 | $0.0000 | 2.3s | 0.85 |
| 32 | `moonshotai/kimi-k2.6` | 0.000 | $0.0000 | 35.9s | 0.56 |

### Best Value (F1 per dollar)

Paid-tier only. Free-tier models are excluded here because F1 / 0 is undefined; they are ranked separately under Best Free-Tier below.

| Rank | Model | F1/$ | F1 | Cost / episode |
|------|-------|------|----|----------------|

### Best Free-Tier (F1)

Models that came back at $0.00 cost. F1 / $ is undefined for these, so they are ranked by F1 alone. Free-tier eligibility on OpenRouter depends on the attribution headers wired into the benchmark (`HTTP-Referer`, `X-Title`); a model showing as free here may bill on your own deployment if those headers are missing.

| Rank | Model | F1 | p50 latency | JSON compliance |
|------|-------|----|-------------|-----------------|
| 1 | `deepseek/deepseek-v4-flash` | 0.000 | 2.2s | 0.79 |
| 2 | `openai/gpt-5.5` | 0.000 | 6.1s | 0.87 |
| 3 | `google/gemini-2.5-flash` | 0.000 | 1.0s | 1.00 |
| 4 | `nvidia/nemotron-nano-9b-v2` | 0.000 | 11.1s | 0.88 |
| 5 | `mistralai/mistral-large-2512` | 0.000 | 3.1s | 1.00 |
| 6 | `meta-llama/llama-3.3-70b-instruct` | 0.000 | 1.6s | 0.84 |
| 7 | `meta-llama/llama-3.1-8b-instruct` | 0.000 | 0.7s | 0.89 |
| 8 | `openai/o3` | 0.000 | 8.5s | 0.93 |
| 9 | `openai/o4-mini` | 0.000 | 7.3s | 0.05 |
| 10 | `mistralai/mistral-medium-3.1` | 0.000 | 0.9s | 1.00 |
| 11 | `deepseek/deepseek-v3.2` | 0.000 | 2.1s | 0.94 |
| 12 | `nvidia/llama-3.3-nemotron-super-49b-v1.5` | 0.000 | 23.1s | 0.72 |
| 13 | `mistralai/codestral-2508` | 0.000 | 0.8s | 1.00 |
| 14 | `openai/gpt-3.5-turbo` | 0.000 | 1.2s | 0.70 |
| 15 | `claude-opus-4-7` | 0.000 | 2.3s | 1.00 |
| 16 | `openai/gpt-5.4` | 0.000 | 1.8s | 0.79 |
| 17 | `claude-haiku-4-5-20251001` | 0.000 | 1.4s | 0.60 |
| 18 | `deepseek/deepseek-r1` | 0.000 | 19.3s | 0.96 |
| 19 | `qwen/qwen3.5-plus-02-15` | 0.000 | 51.5s | 1.00 |
| 20 | `cohere/command-a` | 0.000 | 3.6s | 0.70 |
| 21 | `deepseek/deepseek-r1-distill-llama-70b` | 0.000 | 1.9s | 0.73 |
| 22 | `x-ai/grok-4.1-fast` | 0.000 | 11.1s | 0.87 |
| 23 | `mistralai/mistral-7b-instruct-v0.1` | 0.000 | 10.5s | 0.07 |
| 24 | `google/gemma-4-31b-it` | 0.000 | 1.7s | 0.84 |
| 25 | `cohere/command-r-plus-08-2024` | 0.000 | 1.0s | 0.97 |
| 26 | `meta-llama/llama-4-maverick` | 0.000 | 1.3s | 0.76 |
| 27 | `deepseek/deepseek-r1-0528` | 0.000 | 14.8s | 0.87 |
| 28 | `meta-llama/llama-4-scout` | 0.000 | 0.8s | 0.81 |
| 29 | `google/gemini-2.5-pro` | 0.000 | 13.5s | 0.97 |
| 30 | `claude-sonnet-4-6` | 0.000 | 1.5s | 0.96 |
| 31 | `microsoft/phi-4` | 0.000 | 2.3s | 0.85 |
| 32 | `moonshotai/kimi-k2.6` | 0.000 | 35.9s | 0.56 |

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

No call errors observed across this run. Every (model, episode, trial, window) tuple returned a parseable response.


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

## Confidence calibration

Models include a self-reported `confidence` on each detected ad. A well-calibrated model should be right ~95% of the time when it claims 0.95 confidence. The table below bins each model's predictions and shows the actual hit rate (fraction that were true positives at IoU >= 0.5). A bin near 1.0 is well-calibrated; a low number with a high count means the model is overconfident.

| Model | 0.00-0.70 | 0.70-0.90 | 0.90-0.95 | 0.95-0.99 | 0.99+ | total |
|---|---:|---:|---:|---:|---:|---:|

See `report_assets/calibration.svg` for the visual reliability diagram.

## Latency tail

Median latency hides outliers. p99 and max are what determines queue depth and worst-case user wait. For OpenRouter-routed models the tail also reflects upstream provider load, not just model compute.

| Model | p50 | p90 | p95 | p99 | max |
|---|---:|---:|---:|---:|---:|
| `meta-llama/llama-3.1-8b-instruct` | 0.73s | 2.28s | 2.80s | 3.82s | 76.80s |
| `mistralai/codestral-2508` | 0.78s | 1.62s | 2.04s | 5.01s | 6.36s |
| `meta-llama/llama-4-scout` | 0.85s | 2.34s | 3.05s | 4.37s | 7.33s |
| `mistralai/mistral-medium-3.1` | 0.91s | 3.32s | 5.68s | 7.69s | 9.92s |
| `google/gemini-2.5-flash` | 0.96s | 2.07s | 2.96s | 5.01s | 16.04s |
| `cohere/command-r-plus-08-2024` | 1.00s | 2.60s | 3.58s | 27.62s | 62.06s |
| `openai/gpt-3.5-turbo` | 1.21s | 1.58s | 1.76s | 2.22s | 3.85s |
| `meta-llama/llama-4-maverick` | 1.26s | 2.11s | 2.42s | 5.59s | 50.80s |
| `claude-haiku-4-5-20251001` | 1.43s | 2.98s | 4.09s | 181.18s | 186.76s |
| `claude-sonnet-4-6` | 1.50s | 4.48s | 5.64s | 8.41s | 183.22s |
| `meta-llama/llama-3.3-70b-instruct` | 1.63s | 2.91s | 5.55s | 12.89s | 22.51s |
| `google/gemma-4-31b-it` | 1.71s | 13.88s | 17.76s | 55.17s | 132.78s |
| `openai/gpt-5.4` | 1.83s | 2.47s | 2.99s | 4.18s | 4.92s |
| `deepseek/deepseek-r1-distill-llama-70b` | 1.87s | 9.82s | 20.38s | 75.01s | 92.09s |
| `deepseek/deepseek-v3.2` | 2.09s | 4.56s | 5.45s | 11.86s | 63.86s |
| `deepseek/deepseek-v4-flash` | 2.22s | 19.25s | 30.96s | 50.03s | 56.22s |
| `claude-opus-4-7` | 2.31s | 3.75s | 4.40s | 6.06s | 183.21s |
| `microsoft/phi-4` | 2.33s | 6.31s | 9.26s | 186.53s | 221.74s |
| `mistralai/mistral-large-2512` | 3.08s | 6.03s | 6.65s | 11.28s | 18.09s |
| `cohere/command-a` | 3.63s | 7.40s | 10.78s | 38.93s | 65.77s |
| `openai/gpt-5.5` | 6.12s | 13.26s | 19.67s | 25.28s | 36.54s |
| `openai/o4-mini` | 7.25s | 19.19s | 23.34s | 46.68s | 63.63s |
| `openai/o3` | 8.50s | 17.40s | 21.22s | 34.23s | 57.77s |
| `mistralai/mistral-7b-instruct-v0.1` | 10.51s | 27.90s | 36.28s | 83.05s | 89.36s |
| `x-ai/grok-4.1-fast` | 11.07s | 29.80s | 35.37s | 51.03s | 64.85s |
| `nvidia/nemotron-nano-9b-v2` | 11.08s | 27.68s | 35.76s | 39.61s | 41.56s |
| `google/gemini-2.5-pro` | 13.46s | 23.58s | 27.10s | 35.29s | 40.62s |
| `deepseek/deepseek-r1-0528` | 14.85s | 66.76s | 80.27s | 109.94s | 242.84s |
| `deepseek/deepseek-r1` | 19.29s | 83.86s | 138.59s | 284.39s | 364.40s |
| `nvidia/llama-3.3-nemotron-super-49b-v1.5` | 23.07s | 82.59s | 86.32s | 101.56s | 106.19s |
| `moonshotai/kimi-k2.6` | 35.92s | 95.70s | 118.25s | 197.01s | 296.21s |
| `qwen/qwen3.5-plus-02-15` | 51.53s | 119.44s | 141.26s | 182.00s | 1486.87s |

## Output token efficiency

How many output tokens the model spent per detected ad. Lower is more concise (the model finds an ad and returns the JSON). Higher means the model is producing a lot of text the parser will discard, which costs you whether or not the answer is right.

| Model | Total output tokens | Ads detected | Tokens / ad | Cost / TP |
|---|---:|---:|---:|---:|
| `mistralai/mistral-medium-3.1` | 19,102 | 304 | 63 | n/a |
| `mistralai/codestral-2508` | 24,165 | 377 | 64 | n/a |
| `meta-llama/llama-3.3-70b-instruct` | 16,099 | 239 | 67 | n/a |
| `google/gemini-2.5-flash` | 34,635 | 475 | 73 | n/a |
| `openai/gpt-3.5-turbo` | 28,749 | 389 | 74 | n/a |
| `google/gemma-4-31b-it` | 24,421 | 320 | 76 | n/a |
| `cohere/command-r-plus-08-2024` | 5,534 | 69 | 80 | n/a |
| `meta-llama/llama-3.1-8b-instruct` | 52,273 | 646 | 81 | n/a |
| `meta-llama/llama-4-scout` | 25,395 | 308 | 82 | n/a |
| `deepseek/deepseek-v3.2` | 12,325 | 136 | 91 | n/a |
| `claude-sonnet-4-6` | 25,526 | 281 | 91 | n/a |
| `cohere/command-a` | 30,494 | 331 | 92 | n/a |
| `claude-opus-4-7` | 18,811 | 203 | 93 | n/a |
| `mistralai/mistral-large-2512` | 67,887 | 730 | 93 | n/a |
| `claude-haiku-4-5-20251001` | 38,866 | 401 | 97 | n/a |
| `openai/gpt-5.4` | 24,262 | 231 | 105 | n/a |
| `meta-llama/llama-4-maverick` | 28,581 | 251 | 114 | n/a |
| `deepseek/deepseek-r1-distill-llama-70b` | 101,093 | 347 | 291 | n/a |
| `microsoft/phi-4` | 95,070 | 288 | 330 | n/a |
| `deepseek/deepseek-v4-flash` | 169,352 | 337 | 503 | n/a |
| `openai/gpt-5.5` | 154,347 | 180 | 857 | n/a |
| `deepseek/deepseek-r1-0528` | 608,318 | 512 | 1188 | n/a |
| `deepseek/deepseek-r1` | 440,399 | 302 | 1458 | n/a |
| `nvidia/nemotron-nano-9b-v2` | 686,205 | 293 | 2342 | n/a |
| `google/gemini-2.5-pro` | 676,791 | 256 | 2644 | n/a |
| `x-ai/grok-4.1-fast` | 476,521 | 178 | 2677 | n/a |
| `nvidia/llama-3.3-nemotron-super-49b-v1.5` | 742,021 | 168 | 4417 | n/a |
| `openai/o3` | 387,167 | 85 | 4555 | n/a |
| `qwen/qwen3.5-plus-02-15` | 1,782,733 | 206 | 8654 | n/a |
| `moonshotai/kimi-k2.6` | 1,331,057 | 128 | 10399 | n/a |
| `openai/o4-mini` | 514,467 | 19 | 27077 | n/a |

## Trial variance (determinism check)

All trials run at temperature 0.0. If a model produces stable output you'd expect the F1 stdev across trials to be near zero. Higher numbers mean the model is non-deterministic even at temp=0. That's fine to know, but means you cannot trust a single trial's number for that model.

| Model | Mean F1 stdev across episodes | Highest single-episode stdev |
|---|---:|---:|

## Cross-model agreement

For each of the 90 (episode, window, trial-equivalent) entries, how many of the 32 active models predicted at least one ad? High-agreement windows are unambiguous ads (or unambiguously not ads). Low-agreement windows are where individual models disagree, and are candidates for ensemble voting if you want a cheap accuracy boost.

| Models predicting an ad | Window count | Share |
|---:|---:|---:|
| 4 of 32 | 4 | 4.4% |
| 5 of 32 | 6 | 6.7% |
| 6 of 32 | 2 | 2.2% |
| 7 of 32 | 4 | 4.4% |
| 8 of 32 | 6 | 6.7% |
| 9 of 32 | 9 | 10.0% |
| 10 of 32 | 5 | 5.6% |
| 11 of 32 | 5 | 5.6% |
| 12 of 32 | 2 | 2.2% |
| 13 of 32 | 3 | 3.3% |
| 14 of 32 | 2 | 2.2% |
| 16 of 32 | 1 | 1.1% |
| 18 of 32 | 1 | 1.1% |
| 21 of 32 | 1 | 1.1% |
| 22 of 32 | 2 | 2.2% |
| 25 of 32 | 1 | 1.1% |
| 26 of 32 | 2 | 2.2% |
| 27 of 32 | 6 | 6.7% |
| 28 of 32 | 12 | 13.3% |
| 29 of 32 | 8 | 8.9% |
| 30 of 32 | 5 | 5.6% |
| 31 of 32 | 3 | 3.3% |

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
| `qwen/qwen3.5-plus-02-15` | 40 | 49 | 0 | 1 | 98.9% |
| `google/gemini-2.5-flash` | 41 | 47 | 2 | 0 | 97.8% |
| `mistralai/mistral-medium-3.1` | 40 | 48 | 1 | 1 | 97.8% |
| `nvidia/llama-3.3-nemotron-super-49b-v1.5` | 39 | 49 | 0 | 2 | 97.8% |
| `x-ai/grok-4.1-fast` | 39 | 49 | 0 | 2 | 97.8% |
| `claude-sonnet-4-6` | 37 | 49 | 0 | 4 | 95.6% |
| `google/gemma-4-31b-it` | 41 | 45 | 4 | 0 | 95.6% |
| `openai/gpt-5.5` | 39 | 47 | 2 | 2 | 95.6% |
| `claude-haiku-4-5-20251001` | 39 | 46 | 3 | 2 | 94.4% |
| `claude-opus-4-7` | 36 | 49 | 0 | 5 | 94.4% |
| `google/gemini-2.5-pro` | 41 | 43 | 6 | 0 | 93.3% |
| `openai/o3` | 34 | 49 | 0 | 7 | 92.2% |
| `meta-llama/llama-3.3-70b-instruct` | 40 | 42 | 7 | 1 | 91.1% |
| `deepseek/deepseek-v4-flash` | 41 | 38 | 11 | 0 | 87.8% |
| `meta-llama/llama-4-scout` | 40 | 39 | 10 | 1 | 87.8% |
| `deepseek/deepseek-r1` | 41 | 33 | 16 | 0 | 82.2% |
| `nvidia/nemotron-nano-9b-v2` | 39 | 35 | 14 | 2 | 82.2% |
| `meta-llama/llama-3.1-8b-instruct` | 37 | 34 | 15 | 4 | 78.9% |
| `meta-llama/llama-4-maverick` | 39 | 31 | 18 | 2 | 77.8% |
| `openai/gpt-5.4` | 41 | 28 | 21 | 0 | 76.7% |
| `cohere/command-r-plus-08-2024` | 19 | 49 | 0 | 22 | 75.6% |
| `mistralai/codestral-2508` | 38 | 27 | 22 | 3 | 72.2% |
| `deepseek/deepseek-v3.2` | 25 | 37 | 12 | 16 | 68.9% |
| `openai/o4-mini` | 13 | 49 | 0 | 28 | 68.9% |
| `cohere/command-a` | 40 | 18 | 31 | 1 | 64.4% |
| `deepseek/deepseek-r1-0528` | 38 | 14 | 35 | 3 | 57.8% |
| `mistralai/mistral-large-2512` | 41 | 10 | 39 | 0 | 56.7% |
| `openai/gpt-3.5-turbo` | 41 | 9 | 40 | 0 | 55.6% |
| `mistralai/mistral-7b-instruct-v0.1` | 0 | 49 | 0 | 41 | 54.4% |
| `moonshotai/kimi-k2.6` | 27 | 19 | 30 | 14 | 51.1% |
| `deepseek/deepseek-r1-distill-llama-70b` | 37 | 2 | 47 | 4 | 43.3% |
| `microsoft/phi-4` | 30 | 8 | 41 | 11 | 42.2% |

## Quick Comparison

One row per model, one column per episode. The headline columns (`F1`, `Cost/ep`, `p50`) summarize across all episodes; the per-episode columns let you see whether a model's average hides wide swings (a model that scores well overall might still bomb on a specific genre). The right-most `F1 stdev` column averages the per-trial standard deviations across episodes; high values mean the model isn't deterministic at temperature 0.0, so its single-trial F1 number is noisy.

| Model | F1 | Cost/ep | p50 | F1 stdev |
|---|---|---|---|---|
| `deepseek/deepseek-v4-flash` | 0.000 | $0.0000 | 2.2s | - |
| `openai/gpt-5.5` | 0.000 | $0.0000 | 6.1s | - |
| `google/gemini-2.5-flash` | 0.000 | $0.0000 | 1.0s | - |
| `nvidia/nemotron-nano-9b-v2` | 0.000 | $0.0000 | 11.1s | - |
| `mistralai/mistral-large-2512` | 0.000 | $0.0000 | 3.1s | - |
| `meta-llama/llama-3.3-70b-instruct` | 0.000 | $0.0000 | 1.6s | - |
| `meta-llama/llama-3.1-8b-instruct` | 0.000 | $0.0000 | 0.7s | - |
| `openai/o3` | 0.000 | $0.0000 | 8.5s | - |
| `openai/o4-mini` | 0.000 | $0.0000 | 7.3s | - |
| `mistralai/mistral-medium-3.1` | 0.000 | $0.0000 | 0.9s | - |
| `deepseek/deepseek-v3.2` | 0.000 | $0.0000 | 2.1s | - |
| `nvidia/llama-3.3-nemotron-super-49b-v1.5` | 0.000 | $0.0000 | 23.1s | - |
| `mistralai/codestral-2508` | 0.000 | $0.0000 | 0.8s | - |
| `openai/gpt-3.5-turbo` | 0.000 | $0.0000 | 1.2s | - |
| `claude-opus-4-7` | 0.000 | $0.0000 | 2.3s | - |
| `openai/gpt-5.4` | 0.000 | $0.0000 | 1.8s | - |
| `claude-haiku-4-5-20251001` | 0.000 | $0.0000 | 1.4s | - |
| `deepseek/deepseek-r1` | 0.000 | $0.0000 | 19.3s | - |
| `qwen/qwen3.5-plus-02-15` | 0.000 | $0.0000 | 51.5s | - |
| `cohere/command-a` | 0.000 | $0.0000 | 3.6s | - |
| `deepseek/deepseek-r1-distill-llama-70b` | 0.000 | $0.0000 | 1.9s | - |
| `x-ai/grok-4.1-fast` | 0.000 | $0.0000 | 11.1s | - |
| `mistralai/mistral-7b-instruct-v0.1` | 0.000 | $0.0000 | 10.5s | - |
| `google/gemma-4-31b-it` | 0.000 | $0.0000 | 1.7s | - |
| `cohere/command-r-plus-08-2024` | 0.000 | $0.0000 | 1.0s | - |
| `meta-llama/llama-4-maverick` | 0.000 | $0.0000 | 1.3s | - |
| `deepseek/deepseek-r1-0528` | 0.000 | $0.0000 | 14.8s | - |
| `meta-llama/llama-4-scout` | 0.000 | $0.0000 | 0.8s | - |
| `google/gemini-2.5-pro` | 0.000 | $0.0000 | 13.5s | - |
| `claude-sonnet-4-6` | 0.000 | $0.0000 | 1.5s | - |
| `microsoft/phi-4` | 0.000 | $0.0000 | 2.3s | - |
| `moonshotai/kimi-k2.6` | 0.000 | $0.0000 | 35.9s | - |

---

## Detailed Results

### Per-Model Detail

Full per-model profile: F1 averaged across episodes, total cost per episode at current pricing, p50 / p95 latency, JSON compliance, parse-failure rate, the distribution of extraction methods the parser had to use, and verbosity / truncation telemetry. The `Extraction methods` list shows how often each route was hit. `json_array_direct` is the cleanest; the rest are recovery paths. The verbosity row flags models that emit long `reason` fields or run out of token budget mid-response. Ordered by F1 descending so the best performers appear first.

#### `deepseek/deepseek-v4-flash`

- F1 (avg across episodes): **0.000**
- Total cost / episode: **$0.0000**
- p50 / p95 latency: 2.22s / 30.96s
- JSON compliance: 0.79
- Parse failure rate: 2.9%
- Extraction methods: `json_array_direct`: 13, `json_object_ads_key`: 303, `json_object_no_ads`: 2, `json_object_single_ad`: 119, `parse_failure`: 13
- Verbosity: 58/450 calls over 1024 output tokens (12.9%); 0 hit max_tokens (0.0%); 0 salvaged from truncated JSON (0.0%)
- Schema violations: 631
- Extra keys observed: end_text, sponsor

#### `openai/gpt-5.5`

- F1 (avg across episodes): **0.000**
- Total cost / episode: **$0.0000**
- p50 / p95 latency: 6.12s / 19.67s
- JSON compliance: 0.87
- Parse failure rate: 0.2%
- Extraction methods: `json_object_no_ads`: 258, `json_object_single_ad`: 191, `parse_failure`: 1
- Verbosity: 28/450 calls over 1024 output tokens (6.2%); 0 hit max_tokens (0.0%); 0 salvaged from truncated JSON (0.0%)
- Schema violations: 332
- Extra keys observed: end_text, sponsor

#### `google/gemini-2.5-flash`

- F1 (avg across episodes): **0.000**
- Total cost / episode: **$0.0000**
- p50 / p95 latency: 0.96s / 2.96s
- JSON compliance: 1.00
- Parse failure rate: 0.0%
- Extraction methods: `json_array_direct`: 450
- Verbosity: 0/450 calls over 1024 output tokens (0.0%); 0 hit max_tokens (0.0%); 0 salvaged from truncated JSON (0.0%)
- Schema violations: 710
- Extra keys observed: end_text, sponsor

#### `nvidia/nemotron-nano-9b-v2`

- F1 (avg across episodes): **0.000**
- Total cost / episode: **$0.0000**
- p50 / p95 latency: 11.08s / 35.76s
- JSON compliance: 0.88
- Parse failure rate: 10.2%
- Extraction methods: `json_array_direct`: 394, `parse_failure`: 46, `regex_json_array`: 10
- Verbosity: 275/450 calls over 1024 output tokens (61.1%); 0 hit max_tokens (0.0%); 0 salvaged from truncated JSON (0.0%)
- Schema violations: 476
- Extra keys observed: end_text, sponsor

#### `mistralai/mistral-large-2512`

- F1 (avg across episodes): **0.000**
- Total cost / episode: **$0.0000**
- p50 / p95 latency: 3.08s / 6.65s
- JSON compliance: 1.00
- Parse failure rate: 0.0%
- Extraction methods: `json_array_direct`: 450
- Verbosity: 0/450 calls over 1024 output tokens (0.0%); 0 hit max_tokens (0.0%); 0 salvaged from truncated JSON (0.0%)
- Schema violations: 1342
- Extra keys observed: end_text, sponsor

#### `meta-llama/llama-3.3-70b-instruct`

- F1 (avg across episodes): **0.000**
- Total cost / episode: **$0.0000**
- p50 / p95 latency: 1.63s / 5.55s
- JSON compliance: 0.84
- Parse failure rate: 0.2%
- Extraction methods: `json_array_direct`: 101, `json_object_no_ads`: 118, `json_object_single_ad`: 229, `parse_failure`: 1, `regex_json_array`: 1
- Verbosity: 1/450 calls over 1024 output tokens (0.2%); 0 hit max_tokens (0.0%); 0 salvaged from truncated JSON (0.0%)
- Schema violations: 378
- Extra keys observed: end_text, sponsor

#### `meta-llama/llama-3.1-8b-instruct`

- F1 (avg across episodes): **0.000**
- Total cost / episode: **$0.0000**
- p50 / p95 latency: 0.73s / 2.80s
- JSON compliance: 0.89
- Parse failure rate: 0.2%
- Extraction methods: `json_array_direct`: 242, `json_object_no_ads`: 52, `json_object_single_ad`: 155, `parse_failure`: 1
- Verbosity: 22/450 calls over 1024 output tokens (4.9%); 0 hit max_tokens (0.0%); 0 salvaged from truncated JSON (0.0%)
- Schema violations: 1270
- Extra keys observed: end_text, sponsor

#### `openai/o3`

- F1 (avg across episodes): **0.000**
- Total cost / episode: **$0.0000**
- p50 / p95 latency: 8.50s / 21.22s
- JSON compliance: 0.93
- Parse failure rate: 0.9%
- Extraction methods: `json_object_ads_key`: 12, `json_object_no_ads`: 341, `json_object_segments_key`: 6, `json_object_single_ad`: 87, `parse_failure`: 4
- Verbosity: 134/450 calls over 1024 output tokens (29.8%); 0 hit max_tokens (0.0%); 0 salvaged from truncated JSON (0.0%)
- Schema violations: 132
- Extra keys observed: end_text, sponsor

#### `openai/o4-mini`

- F1 (avg across episodes): **0.000**
- Total cost / episode: **$0.0000**
- p50 / p95 latency: 7.25s / 23.34s
- JSON compliance: 0.05
- Parse failure rate: 94.0%
- Extraction methods: `json_object_no_ads`: 8, `json_object_single_ad`: 19, `parse_failure`: 423
- Verbosity: 190/450 calls over 1024 output tokens (42.2%); 0 hit max_tokens (0.0%); 0 salvaged from truncated JSON (0.0%)
- Schema violations: 28
- Extra keys observed: end_text, sponsor

#### `mistralai/mistral-medium-3.1`

- F1 (avg across episodes): **0.000**
- Total cost / episode: **$0.0000**
- p50 / p95 latency: 0.91s / 5.68s
- JSON compliance: 1.00
- Parse failure rate: 0.0%
- Extraction methods: `json_array_direct`: 450
- Verbosity: 0/450 calls over 1024 output tokens (0.0%); 0 hit max_tokens (0.0%); 0 salvaged from truncated JSON (0.0%)
- Schema violations: 608
- Extra keys observed: end_text, sponsor

#### `deepseek/deepseek-v3.2`

- F1 (avg across episodes): **0.000**
- Total cost / episode: **$0.0000**
- p50 / p95 latency: 2.09s / 5.45s
- JSON compliance: 0.94
- Parse failure rate: 0.0%
- Extraction methods: `json_array_direct`: 355, `json_object_ads_key`: 4, `json_object_single_ad`: 91
- Verbosity: 1/450 calls over 1024 output tokens (0.2%); 0 hit max_tokens (0.0%); 0 salvaged from truncated JSON (0.0%)
- Schema violations: 263
- Extra keys observed: end_text, sponsor

#### `nvidia/llama-3.3-nemotron-super-49b-v1.5`

- F1 (avg across episodes): **0.000**
- Total cost / episode: **$0.0000**
- p50 / p95 latency: 23.07s / 86.32s
- JSON compliance: 0.72
- Parse failure rate: 15.6%
- Extraction methods: `json_array_direct`: 242, `markdown_code_block`: 127, `parse_failure`: 70, `regex_json_array`: 11
- Verbosity: 246/450 calls over 1024 output tokens (54.7%); 0 hit max_tokens (0.0%); 0 salvaged from truncated JSON (0.0%)
- Schema violations: 266
- Extra keys observed: end_text, sponsor

#### `mistralai/codestral-2508`

- F1 (avg across episodes): **0.000**
- Total cost / episode: **$0.0000**
- p50 / p95 latency: 0.78s / 2.04s
- JSON compliance: 1.00
- Parse failure rate: 0.0%
- Extraction methods: `json_array_direct`: 450
- Verbosity: 0/450 calls over 1024 output tokens (0.0%); 0 hit max_tokens (0.0%); 0 salvaged from truncated JSON (0.0%)
- Schema violations: 622
- Extra keys observed: end_text, sponsor

#### `openai/gpt-3.5-turbo`

- F1 (avg across episodes): **0.000**
- Total cost / episode: **$0.0000**
- p50 / p95 latency: 1.21s / 1.76s
- JSON compliance: 0.70
- Parse failure rate: 0.0%
- Extraction methods: `json_object_no_ads`: 4, `json_object_single_ad`: 446
- Verbosity: 0/450 calls over 1024 output tokens (0.0%); 0 hit max_tokens (0.0%); 0 salvaged from truncated JSON (0.0%)
- Schema violations: 593
- Extra keys observed: end_text, sponsor

#### `claude-opus-4-7`

- F1 (avg across episodes): **0.000**
- Total cost / episode: **$0.0000**
- p50 / p95 latency: 2.31s / 4.40s
- JSON compliance: 1.00
- Parse failure rate: 0.0%
- Extraction methods: `json_array_direct`: 448, `regex_json_array`: 2
- Verbosity: 0/450 calls over 1024 output tokens (0.0%); 0 hit max_tokens (0.0%); 0 salvaged from truncated JSON (0.0%)
- Schema violations: 397
- Extra keys observed: end_text, sponsor

#### `openai/gpt-5.4`

- F1 (avg across episodes): **0.000**
- Total cost / episode: **$0.0000**
- p50 / p95 latency: 1.83s / 2.99s
- JSON compliance: 0.79
- Parse failure rate: 0.0%
- Extraction methods: `json_object_no_ads`: 134, `json_object_single_ad`: 316
- Verbosity: 0/450 calls over 1024 output tokens (0.0%); 0 hit max_tokens (0.0%); 0 salvaged from truncated JSON (0.0%)
- Schema violations: 398
- Extra keys observed: end_text, sponsor

#### `claude-haiku-4-5-20251001`

- F1 (avg across episodes): **0.000**
- Total cost / episode: **$0.0000**
- p50 / p95 latency: 1.43s / 4.09s
- JSON compliance: 0.60
- Parse failure rate: 0.0%
- Extraction methods: `markdown_code_block`: 450
- Verbosity: 0/450 calls over 1024 output tokens (0.0%); 0 hit max_tokens (0.0%); 0 salvaged from truncated JSON (0.0%)
- Schema violations: 711
- Extra keys observed: end_text, sponsor

#### `deepseek/deepseek-r1`

- F1 (avg across episodes): **0.000**
- Total cost / episode: **$0.0000**
- p50 / p95 latency: 19.29s / 138.59s
- JSON compliance: 0.96
- Parse failure rate: 1.3%
- Extraction methods: `json_array_direct`: 390, `json_object_ads_key`: 2, `json_object_no_ads`: 12, `json_object_segments_key`: 3, `json_object_single_ad`: 28, `markdown_code_block`: 7, `parse_failure`: 6, `regex_json_array`: 2
- Verbosity: 87/450 calls over 1024 output tokens (19.3%); 0 hit max_tokens (0.0%); 0 salvaged from truncated JSON (0.0%)
- Schema violations: 486
- Extra keys observed: end_text, sponsor

#### `qwen/qwen3.5-plus-02-15`

- F1 (avg across episodes): **0.000**
- Total cost / episode: **$0.0000**
- p50 / p95 latency: 51.53s / 141.26s
- JSON compliance: 1.00
- Parse failure rate: 0.0%
- Extraction methods: `json_array_direct`: 450
- Verbosity: 414/450 calls over 1024 output tokens (92.0%); 0 hit max_tokens (0.0%); 0 salvaged from truncated JSON (0.0%)
- Schema violations: 384
- Extra keys observed: end_text, sponsor

#### `cohere/command-a`

- F1 (avg across episodes): **0.000**
- Total cost / episode: **$0.0000**
- p50 / p95 latency: 3.63s / 10.78s
- JSON compliance: 0.70
- Parse failure rate: 0.0%
- Extraction methods: `json_object_no_ads`: 3, `json_object_single_ad`: 447
- Verbosity: 0/450 calls over 1024 output tokens (0.0%); 0 hit max_tokens (0.0%); 0 salvaged from truncated JSON (0.0%)
- Schema violations: 478
- Extra keys observed: end_text, sponsor

#### `deepseek/deepseek-r1-distill-llama-70b`

- F1 (avg across episodes): **0.000**
- Total cost / episode: **$0.0000**
- p50 / p95 latency: 1.87s / 20.38s
- JSON compliance: 0.73
- Parse failure rate: 0.9%
- Extraction methods: `json_array_direct`: 14, `json_object_ads_key`: 15, `json_object_no_ads`: 32, `json_object_single_ad`: 385, `parse_failure`: 4
- Verbosity: 20/450 calls over 1024 output tokens (4.4%); 0 hit max_tokens (0.0%); 0 salvaged from truncated JSON (0.0%)
- Schema violations: 474
- Extra keys observed: end_text, sponsor

#### `x-ai/grok-4.1-fast`

- F1 (avg across episodes): **0.000**
- Total cost / episode: **$0.0000**
- p50 / p95 latency: 11.07s / 35.37s
- JSON compliance: 0.87
- Parse failure rate: 0.2%
- Extraction methods: `json_object_no_ads`: 259, `json_object_single_ad`: 190, `parse_failure`: 1
- Verbosity: 185/450 calls over 1024 output tokens (41.1%); 0 hit max_tokens (0.0%); 0 salvaged from truncated JSON (0.0%)
- Schema violations: 263
- Extra keys observed: end_text, sponsor

#### `mistralai/mistral-7b-instruct-v0.1`

- F1 (avg across episodes): **0.000**
- Total cost / episode: **$0.0000**
- p50 / p95 latency: 10.51s / 36.28s
- JSON compliance: 0.07
- Parse failure rate: 82.2%
- Extraction methods: `bracket_fallback`: 1, `parse_failure`: 370, `regex_json_array`: 79
- Verbosity: 12/450 calls over 1024 output tokens (2.7%); 0 hit max_tokens (0.0%); 0 salvaged from truncated JSON (0.0%)

#### `google/gemma-4-31b-it`

- F1 (avg across episodes): **0.000**
- Total cost / episode: **$0.0000**
- p50 / p95 latency: 1.71s / 17.76s
- JSON compliance: 0.84
- Parse failure rate: 0.0%
- Extraction methods: `json_object_ads_key`: 278, `json_object_no_ads`: 72, `json_object_single_ad`: 99, `json_object_single_ad_truncated`: 1
- Verbosity: 0/450 calls over 1024 output tokens (0.0%); 0 hit max_tokens (0.0%); 1 salvaged from truncated JSON (0.2%)
- Schema violations: 532
- Extra keys observed: end_text, sponsor

#### `cohere/command-r-plus-08-2024`

- F1 (avg across episodes): **0.000**
- Total cost / episode: **$0.0000**
- p50 / p95 latency: 1.00s / 3.58s
- JSON compliance: 0.97
- Parse failure rate: 0.0%
- Extraction methods: `json_object_ads_key`: 10, `json_object_no_ads`: 395, `json_object_single_ad`: 45
- Verbosity: 0/450 calls over 1024 output tokens (0.0%); 0 hit max_tokens (0.0%); 0 salvaged from truncated JSON (0.0%)
- Schema violations: 132
- Extra keys observed: end_text, sponsor

#### `meta-llama/llama-4-maverick`

- F1 (avg across episodes): **0.000**
- Total cost / episode: **$0.0000**
- p50 / p95 latency: 1.26s / 2.42s
- JSON compliance: 0.76
- Parse failure rate: 0.0%
- Extraction methods: `json_object_no_ads`: 93, `json_object_single_ad`: 357
- Verbosity: 3/450 calls over 1024 output tokens (0.7%); 0 hit max_tokens (0.0%); 0 salvaged from truncated JSON (0.0%)
- Schema violations: 372
- Extra keys observed: end_text, sponsor

#### `deepseek/deepseek-r1-0528`

- F1 (avg across episodes): **0.000**
- Total cost / episode: **$0.0000**
- p50 / p95 latency: 14.85s / 80.27s
- JSON compliance: 0.87
- Parse failure rate: 10.7%
- Extraction methods: `json_array_direct`: 354, `json_object_ads_key`: 23, `json_object_no_ads`: 3, `json_object_single_ad`: 21, `markdown_code_block`: 1, `parse_failure`: 48
- Verbosity: 174/450 calls over 1024 output tokens (38.7%); 0 hit max_tokens (0.0%); 0 salvaged from truncated JSON (0.0%)
- Schema violations: 694
- Extra keys observed: end_text, sponsor

#### `meta-llama/llama-4-scout`

- F1 (avg across episodes): **0.000**
- Total cost / episode: **$0.0000**
- p50 / p95 latency: 0.85s / 3.05s
- JSON compliance: 0.81
- Parse failure rate: 0.2%
- Extraction methods: `bracket_fallback`: 23, `json_array_direct`: 3, `json_object_ads_key`: 318, `json_object_no_ads`: 44, `json_object_single_ad`: 58, `parse_failure`: 1, `regex_json_array`: 3
- Verbosity: 1/450 calls over 1024 output tokens (0.2%); 0 hit max_tokens (0.0%); 0 salvaged from truncated JSON (0.0%)
- Schema violations: 507
- Extra keys observed: end_text, sponsor

#### `google/gemini-2.5-pro`

- F1 (avg across episodes): **0.000**
- Total cost / episode: **$0.0000**
- p50 / p95 latency: 13.46s / 27.10s
- JSON compliance: 0.97
- Parse failure rate: 0.4%
- Extraction methods: `json_array_direct`: 429, `parse_failure`: 2, `regex_json_array`: 19
- Verbosity: 351/450 calls over 1024 output tokens (78.0%); 0 hit max_tokens (0.0%); 0 salvaged from truncated JSON (0.0%)
- Schema violations: 389
- Extra keys observed: end_text, sponsor

#### `claude-sonnet-4-6`

- F1 (avg across episodes): **0.000**
- Total cost / episode: **$0.0000**
- p50 / p95 latency: 1.50s / 5.64s
- JSON compliance: 0.96
- Parse failure rate: 0.0%
- Extraction methods: `json_array_direct`: 413, `markdown_code_block`: 27, `regex_json_array`: 10
- Verbosity: 0/450 calls over 1024 output tokens (0.0%); 0 hit max_tokens (0.0%); 0 salvaged from truncated JSON (0.0%)
- Schema violations: 461
- Extra keys observed: end_text, sponsor

#### `microsoft/phi-4`

- F1 (avg across episodes): **0.000**
- Total cost / episode: **$0.0000**
- p50 / p95 latency: 2.33s / 9.26s
- JSON compliance: 0.85
- Parse failure rate: 1.3%
- Extraction methods: `json_array_direct`: 209, `json_object_ads_key`: 21, `json_object_no_ads`: 19, `json_object_segments_key`: 11, `json_object_single_ad`: 181, `json_object_window_segments`: 2, `parse_failure`: 6, `regex_json_array`: 1
- Verbosity: 7/450 calls over 1024 output tokens (1.6%); 0 hit max_tokens (0.0%); 0 salvaged from truncated JSON (0.0%)
- Schema violations: 412
- Extra keys observed: end_text, sponsor

#### `moonshotai/kimi-k2.6`

- F1 (avg across episodes): **0.000**
- Total cost / episode: **$0.0000**
- p50 / p95 latency: 35.92s / 118.25s
- JSON compliance: 0.56
- Parse failure rate: 28.0%
- Extraction methods: `json_array_direct`: 31, `json_object_ads_key`: 14, `json_object_no_ads`: 53, `json_object_single_ad`: 226, `parse_failure`: 126
- Verbosity: 424/450 calls over 1024 output tokens (94.2%); 0 hit max_tokens (0.0%); 0 salvaged from truncated JSON (0.0%)
- Schema violations: 234
- Extra keys observed: end_text, sponsor


### Per-Episode Detail

One subsection per episode in the corpus, showing how every model performed on that specific episode. For ad-bearing episodes you see F1 and the stdev across trials (low stdev means stable, high stdev means the model's number on this episode is noisy). For the no-ad episode you see PASS / FAIL on the negative control: PASS = zero false positives across all windows, FAIL = the model flagged something that wasn't an ad, with the count.


### Parser stress test

How each model's responses were actually parsed. Columns are extraction methods, ordered alphabetically; rows are models, sorted by parse-failure rate (cleanest at top). `json_array_direct` is the happy path: a bare JSON array we could `json.loads` and process immediately. `markdown_code_block` means we had to strip triple-backtick fences first; `json_object_*` means the model wrapped the array in an outer object and we had to find the array key; `regex_*` are last-resort recovery paths. A model that needs anything but `json_array_direct` for most calls is fragile. It works today, but a small prompt change can break the parser.

| Model | bracket_fallback | json_array_direct | json_object_ads_key | json_object_no_ads | json_object_segments_key | json_object_single_ad | json_object_single_ad_truncated | json_object_window_segments | markdown_code_block | parse_failure | regex_json_array |
|---|---|---|---|---|---|---|---|---|---|---|---|
| `google/gemini-2.5-flash` | 0 | 450 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| `mistralai/mistral-large-2512` | 0 | 450 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| `mistralai/mistral-medium-3.1` | 0 | 450 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| `deepseek/deepseek-v3.2` | 0 | 355 | 4 | 0 | 0 | 91 | 0 | 0 | 0 | 0 | 0 |
| `mistralai/codestral-2508` | 0 | 450 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| `openai/gpt-3.5-turbo` | 0 | 0 | 0 | 4 | 0 | 446 | 0 | 0 | 0 | 0 | 0 |
| `claude-opus-4-7` | 0 | 448 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 2 |
| `openai/gpt-5.4` | 0 | 0 | 0 | 134 | 0 | 316 | 0 | 0 | 0 | 0 | 0 |
| `claude-haiku-4-5-20251001` | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 450 | 0 | 0 |
| `qwen/qwen3.5-plus-02-15` | 0 | 450 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| `cohere/command-a` | 0 | 0 | 0 | 3 | 0 | 447 | 0 | 0 | 0 | 0 | 0 |
| `google/gemma-4-31b-it` | 0 | 0 | 278 | 72 | 0 | 99 | 1 | 0 | 0 | 0 | 0 |
| `cohere/command-r-plus-08-2024` | 0 | 0 | 10 | 395 | 0 | 45 | 0 | 0 | 0 | 0 | 0 |
| `meta-llama/llama-4-maverick` | 0 | 0 | 0 | 93 | 0 | 357 | 0 | 0 | 0 | 0 | 0 |
| `claude-sonnet-4-6` | 0 | 413 | 0 | 0 | 0 | 0 | 0 | 0 | 27 | 0 | 10 |
| `openai/gpt-5.5` | 0 | 0 | 0 | 258 | 0 | 191 | 0 | 0 | 0 | 1 | 0 |
| `meta-llama/llama-3.3-70b-instruct` | 0 | 101 | 0 | 118 | 0 | 229 | 0 | 0 | 0 | 1 | 1 |
| `meta-llama/llama-3.1-8b-instruct` | 0 | 242 | 0 | 52 | 0 | 155 | 0 | 0 | 0 | 1 | 0 |
| `x-ai/grok-4.1-fast` | 0 | 0 | 0 | 259 | 0 | 190 | 0 | 0 | 0 | 1 | 0 |
| `meta-llama/llama-4-scout` | 23 | 3 | 318 | 44 | 0 | 58 | 0 | 0 | 0 | 1 | 3 |
| `google/gemini-2.5-pro` | 0 | 429 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 2 | 19 |
| `openai/o3` | 0 | 0 | 12 | 341 | 6 | 87 | 0 | 0 | 0 | 4 | 0 |
| `deepseek/deepseek-r1-distill-llama-70b` | 0 | 14 | 15 | 32 | 0 | 385 | 0 | 0 | 0 | 4 | 0 |
| `deepseek/deepseek-r1` | 0 | 390 | 2 | 12 | 3 | 28 | 0 | 0 | 7 | 6 | 2 |
| `microsoft/phi-4` | 0 | 209 | 21 | 19 | 11 | 181 | 0 | 2 | 0 | 6 | 1 |
| `deepseek/deepseek-v4-flash` | 0 | 13 | 303 | 2 | 0 | 119 | 0 | 0 | 0 | 13 | 0 |
| `nvidia/nemotron-nano-9b-v2` | 0 | 394 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 46 | 10 |
| `deepseek/deepseek-r1-0528` | 0 | 354 | 23 | 3 | 0 | 21 | 0 | 0 | 1 | 48 | 0 |
| `nvidia/llama-3.3-nemotron-super-49b-v1.5` | 0 | 242 | 0 | 0 | 0 | 0 | 0 | 0 | 127 | 70 | 11 |
| `moonshotai/kimi-k2.6` | 0 | 31 | 14 | 53 | 0 | 226 | 0 | 0 | 0 | 126 | 0 |
| `mistralai/mistral-7b-instruct-v0.1` | 1 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 370 | 79 |
| `openai/o4-mini` | 0 | 0 | 0 | 8 | 0 | 19 | 0 | 0 | 0 | 423 | 0 |

## Methodology

Reproducibility settings used for this run. The benchmark sends the same prompts MinusPod sends in production (same system prompt, same sponsor list, same windowing) so the F1 numbers here are directly relevant to production accuracy decisions. Cost is recomputed at report time from token counts against the active pricing snapshot, so all rows compare at the same prices regardless of when the actual call ran.

- Trials per (model, episode): **5**, temperature 0.0
- max_tokens: 4096 (matches MinusPod production)
- response_format: json_object (with prompt-injection fallback when provider rejects native)
- Window size: 10 min, overlap: 3 min (imported from MinusPod's create_windows)
- Pricing snapshot: 2026-05-09T22:50:45.889333Z
- Corpus episodes: 0

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

**Sponsor vocabulary** (255 canonical sponsors, 44 of them with explicit alias spellings totaling 48 aliases; from `src/utils/constants.py` `SEED_SPONSORS`). Laid out in two side-by-side groups, read top-to-bottom in each group.

| Sponsor | Aliases | Category | Sponsor | Aliases | Category |
|---|---|---|---|---|---|
| 1Password | `One Password` | tech | MacPaw | `CleanMyMac` | tech |
| Acorns | - | finance | Magic Mind | - | beverage |
| ADT | - | home | Magic Spoon | - | food |
| Affirm | - | finance_fintech | Mailchimp | - | tech_software_saas |
| Airbnb | - | travel_hospitality | Manscaped | - | personal |
| Airtable | - | tech_software_saas | MasterClass | `Master Class` | education |
| Alani Nu | - | food_beverage_nutrition | McDonald's | - | food_beverage_nutrition |
| Allbirds | - | ecommerce_retail_dtc | Mercury | - | finance_fintech |
| Alo Yoga | - | ecommerce_retail_dtc | Meter | - | b2b_startup |
| Amazon | - | retail | Midjourney | - | tech_software_saas |
| Anthropic | - | tech_software_saas | Mint Mobile | `MintMobile` | telecom |
| Apple TV+ | - | media_streaming | Miro | - | tech |
| Asana | - | tech_software_saas | Momentous | - | mental_health_wellness |
| AT&T | - | telecom | Monarch Money | - | finance |
| Athletic Brewing | - | beverage | Monday.com | `Monday` | tech |
| Athletic Greens | `AG1`, `AG One` | health | Native | - | personal |
| Audible | - | entertainment | NerdWallet | - | finance_fintech |
| Aura | - | tech | Netflix | - | media_streaming |
| Babbel | - | education | NetSuite | `Net Suite` | tech |
| BetMGM | `Bet MGM` | gambling | Noom | - | mental_health_wellness |
| BetterHelp | `Better Help` | health | NordVPN | `Nord VPN` | vpn |
| Betterment | - | finance | Notion | - | tech |
| Bill.com | - | finance_fintech | Nutrafol | - | health |
| Birchbox | - | ecommerce_retail_dtc | Okta | - | tech_software_saas |
| Bitwarden | `Bit Warden` | tech | OLIPOP | - | food_beverage_nutrition |
| Blinkist | - | education | OneSkin | `One Skin` | personal |
| Bloom Nutrition | - | food_beverage_nutrition | OpenAI | - | tech_software_saas |
| Blue Apron | - | food | Outdoor Voices | - | ecommerce_retail_dtc |
| Bombas | - | apparel | OutSystems | - | tech |
| Booking.com | - | travel_hospitality | PagerDuty | - | b2b_startup |
| Bose | - | electronics | Paramount+ | - | media_streaming |
| Brex | - | finance_fintech | Patreon | - | tech_software_saas |
| Brilliant | - | tech_software_saas | Perplexity | - | tech_software_saas |
| Brooklinen | - | home | Plaid | - | finance_fintech |
| Butcher Box | `ButcherBox` | food | PolicyGenius | `Policy Genius` | finance |
| CacheFly | - | tech | Poppi | - | food_beverage_nutrition |
| Caesars Sportsbook | - | gaming_sports_betting | Poshmark | - | ecommerce_retail_dtc |
| Calm | - | health | Progressive | - | finance |
| Canva | - | tech | Public.com | - | finance_fintech |
| Capital One | - | finance | Pura | - | home_security |
| Care/of | `Care of`, `Careof` | health | Purple | - | home |
| CarMax | `Car Max` | auto | QuickBooks | - | finance_fintech |
| Carvana | - | auto | Quince | - | apparel |
| Casper | - | home | Quip | - | personal |
| Cerebral | - | mental_health_wellness | Ramp | - | finance_fintech |
| Chime | - | finance_fintech | Raycon | - | electronics |
| ClickUp | - | tech_software_saas | Retool | - | tech_software_saas |
| Cloudflare | - | tech_software_saas | Ring | - | home |
| Coinbase | - | finance_fintech | Rippling | - | b2b_startup |
| Comcast | - | telecom | Ritual | - | health |
| Cozy Earth | - | home | Ro | - | mental_health_wellness |
| Credit Karma | - | finance | Robinhood | - | finance_fintech |
| CrowdStrike | - | tech_software_saas | Rocket Lawyer | - | insurance_legal |
| Cursor | - | tech_software_saas | Rocket Money | `RocketMoney`, `Truebill` | finance |
| Databricks | - | tech_software_saas | Roman | - | health |
| Datadog | - | tech_software_saas | Rosetta Stone | - | education |
| Deel | - | business | Rothy's | - | ecommerce_retail_dtc |
| DeleteMe | `Delete Me` | tech | Saatva | - | ecommerce_retail_dtc |
| Disney+ | - | media_streaming | Salesforce | - | tech_software_saas |
| DocuSign | - | tech_software_saas | SeatGeek | - | gaming_sports_betting |
| Dollar Shave Club | `DSC` | personal | Seed | - | health |
| DoorDash | `Door Dash` | food | SendGrid | - | tech_software_saas |
| DraftKings | `Draft Kings` | gambling | ServiceNow | - | tech_software_saas |
| Duolingo | - | tech_software_saas | Shein | - | ecommerce_retail_dtc |
| eBay Motors | - | auto | Shopify | - | tech |
| Eight Sleep | - | mental_health_wellness | SimpliSafe | `Simpli Safe` | home |
| ElevenLabs | - | tech_software_saas | SiriusXM | - | media_streaming |
| ESPN Bet | - | gaming_sports_betting | Skillshare | - | tech_software_saas |
| Everlane | - | ecommerce_retail_dtc | SKIMS | - | ecommerce_retail_dtc |
| EveryPlate | - | food_beverage_nutrition | Skyscanner | - | travel_hospitality |
| Expedia | - | travel_hospitality | Slack | - | tech_software_saas |
| ExpressVPN | `Express VPN` | vpn | Snowflake | - | tech_software_saas |
| FabFitFun | - | ecommerce_retail_dtc | SoFi | - | finance |
| Factor | - | food | Spaceship | - | tech |
| FanDuel | `Fan Duel` | gambling | Splunk | - | b2b_startup |
| Figma | - | tech_software_saas | Spotify | - | media_streaming |
| Ford | - | auto | Squarespace | `Square Space` | tech |
| Framer | - | tech | Stamps.com | `Stamps` | business |
| FreshBooks | - | finance_fintech | Starbucks | - | food_beverage_nutrition |
| Function Health | - | mental_health_wellness | State Farm | - | finance |
| Function of Beauty | - | personal | Stitch Fix | - | ecommerce_retail_dtc |
| Gametime | `Game Time` | entertainment | StockX | - | ecommerce_retail_dtc |
| Geico | - | finance | Stripe | - | finance_fintech |
| GitHub | - | tech_software_saas | StubHub | - | gaming_sports_betting |
| GitHub Copilot | - | tech_software_saas | Substack | - | tech_software_saas |
| GOAT | - | ecommerce_retail_dtc | T-Mobile | `TMobile` | telecom |
| GoodRx | `Good Rx` | health | Talkspace | - | mental_health_wellness |
| Gopuff | - | ecommerce_retail_dtc | Temu | - | ecommerce_retail_dtc |
| Grammarly | - | tech | Ten Thousand | - | ecommerce_retail_dtc |
| Green Chef | `GreenChef` | food | Thinkst Canary | - | tech |
| Grubhub | `Grub Hub` | food | Thorne | - | mental_health_wellness |
| Gusto | - | b2b_startup | ThreatLocker | - | tech |
| Harry's | `Harrys` | personal | ThredUp | - | ecommerce_retail_dtc |
| HBO Max | - | media_streaming | Thrive Market | - | food |
| Headspace | `Head Space` | health | Toyota | - | auto |
| Helix Sleep | `Helix` | home | Transparent Labs | - | food_beverage_nutrition |
| HelloFresh | `Hello Fresh` | food | Turo | - | automotive_transport |
| Hers | - | health | Twilio | - | tech_software_saas |
| Hims | - | health | Uber | - | automotive_transport |
| Honeylove | `Honey Love` | apparel | Uber Eats | `UberEats` | food |
| Hopper | - | travel_hospitality | UnitedHealth Group | - | finance_fintech |
| HubSpot | `Hub Spot` | tech | Vanta | - | tech |
| Huel | - | food_beverage_nutrition | Veeam | - | tech |
| Hyundai | - | auto | Vercel | - | tech_software_saas |
| iHeartRadio | - | media_streaming | Verizon | - | telecom |
| Imperfect Foods | - | food_beverage_nutrition | Visible | - | telecom |
| Incogni | - | tech | Vrbo | - | travel_hospitality |
| Indeed | - | jobs | Vuori | - | ecommerce_retail_dtc |
| Inside Tracker | - | mental_health_wellness | Warby Parker | - | ecommerce_retail_dtc |
| Instacart | - | food | Wayfair | - | ecommerce_retail_dtc |
| Intuit | - | finance_fintech | Waymo | - | automotive_transport |
| Joovv | - | mental_health_wellness | Wealthfront | - | finance |
| Kayak | - | travel_hospitality | WebBank | - | finance_fintech |
| Keeps | - | health | Webflow | - | b2b_startup |
| Klarna | - | finance_fintech | WhatsApp | - | tech |
| Klaviyo | - | tech_software_saas | WHOOP | - | mental_health_wellness |
| LegalZoom | - | insurance_legal | Workday | - | tech_software_saas |
| Lemonade | - | finance | Xero | - | finance_fintech |
| Levels | - | mental_health_wellness | YouTube | - | media_streaming |
| Liberty Mutual | - | finance | YouTube TV | - | media_streaming |
| Lime | - | automotive_transport | Zapier | - | tech |
| Linear | - | tech_software_saas | Zendesk | - | tech_software_saas |
| LinkedIn | `LinkedIn Jobs` | jobs | ZipRecruiter | `Zip Recruiter` | jobs |
| Liquid IV | `Liquid I.V.` | health | ZocDoc | `Zoc Doc` | health |
| LMNT | `Element` | health | Zoom | - | tech_software_saas |
| Loom | - | tech_software_saas | Zscaler | - | tech |
| Lululemon | - | ecommerce_retail_dtc | Zyn | `ZYN`, `Zinn` | tobacco_nicotine |
| Lyft | - | automotive_transport |  |  |  |

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

- Report generated: 2026-05-13T16:26:08Z
- Unique work units (current state, last-write-wins after retries): 14400
- Raw rows in calls.jsonl: 14416 (16 superseded by later retries; kept for audit)
- Successful: 14400
- Failed: 0
- Lifetime actual spend (sum of at-runtime costs, includes superseded rows): $123.6996
- Active pricing snapshot: 2026-05-09T22:50:45.889333Z
