# MinusPod LLM Benchmark Report

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
| 1 | `x-ai/grok-4.1-fast` | 0.642 | $0.1509 | 11.1s | 0.87 |
| 2 | `qwen/qwen3.5-plus-02-15` | 0.616 | $0.0000 | 51.5s | 1.00 |
| 3 | `openai/gpt-5.5` | 0.613 | $3.4570 | 6.1s | 0.87 |
| 4 | `claude-opus-4-7` | 0.593 | $4.0998 | 2.3s | 1.00 |
| 5 | `google/gemini-2.5-pro` | 0.549 | $2.0297 | 13.5s | 0.97 |
| 6 | `openai/gpt-5.4` | 0.539 | $1.3382 | 1.8s | 0.79 |
| 7 | `openai/o3` | 0.531 | $1.6318 | 8.5s | 0.93 |
| 8 | `google/gemma-4-31b-it` | 0.444 | $0.0000 | 1.7s | 0.84 |
| 9 | `deepseek/deepseek-v4-flash` | 0.437 | $0.0000 | 2.2s | 0.79 |
| 10 | `deepseek/deepseek-r1` | 0.382 | $3.2723 | 19.3s | 0.96 |
| 11 | `nvidia/llama-3.3-nemotron-super-49b-v1.5` | 0.373 | $0.0000 | 23.1s | 0.72 |
| 12 | `mistralai/mistral-medium-3.1` | 0.370 | $0.0000 | 0.9s | 1.00 |
| 13 | `moonshotai/kimi-k2.6` | 0.365 | $1.5538 | 35.9s | 0.56 |
| 14 | `claude-sonnet-4-6` | 0.361 | $1.8552 | 1.5s | 0.96 |
| 15 | `cohere/command-a` | 0.337 | $0.0000 | 3.6s | 0.70 |
| 16 | `claude-haiku-4-5-20251001` | 0.325 | $0.6316 | 1.4s | 0.60 |
| 17 | `meta-llama/llama-3.3-70b-instruct` | 0.313 | $0.3412 | 1.6s | 0.84 |
| 18 | `meta-llama/llama-4-maverick` | 0.294 | $0.0000 | 1.3s | 0.76 |
| 19 | `deepseek/deepseek-v3.2` | 0.291 | $0.3335 | 2.1s | 0.94 |
| 20 | `deepseek/deepseek-r1-0528` | 0.283 | $0.1789 | 14.8s | 0.87 |
| 21 | `meta-llama/llama-4-scout` | 0.231 | $0.0000 | 0.8s | 0.81 |
| 22 | `google/gemini-2.5-flash` | 0.217 | $0.1800 | 1.0s | 1.00 |
| 23 | `openai/gpt-3.5-turbo` | 0.197 | $0.2686 | 1.2s | 0.70 |
| 24 | `mistralai/codestral-2508` | 0.190 | $0.1705 | 0.8s | 1.00 |
| 25 | `deepseek/deepseek-r1-distill-llama-70b` | 0.186 | $0.3917 | 1.9s | 0.73 |
| 26 | `cohere/command-r-plus-08-2024` | 0.156 | $1.3560 | 1.0s | 0.97 |
| 27 | `nvidia/nemotron-nano-9b-v2` | 0.133 | $0.0000 | 11.1s | 0.88 |
| 28 | `meta-llama/llama-3.1-8b-instruct` | 0.132 | $0.1063 | 0.7s | 0.89 |
| 29 | `mistralai/mistral-large-2512` | 0.129 | $0.2972 | 3.1s | 1.00 |
| 30 | `openai/o4-mini` | 0.073 | $1.0096 | 7.3s | 0.05 |
| 31 | `microsoft/phi-4` | 0.039 | $0.0757 | 2.3s | 0.85 |
| 32 | `mistralai/mistral-7b-instruct-v0.1` | 0.000 | $0.0000 | 10.5s | 0.07 |

### Best Value (F1 per dollar)

Paid-tier only. Free-tier models are excluded here because F1 / 0 is undefined; they are ranked separately under Best Free-Tier below.

| Rank | Model | F1/$ | F1 | Cost / episode |
|------|-------|------|----|----------------|
| 1 | `x-ai/grok-4.1-fast` | 4.25 | 0.642 | $0.1509 |
| 2 | `deepseek/deepseek-r1-0528` | 1.58 | 0.283 | $0.1789 |
| 3 | `meta-llama/llama-3.1-8b-instruct` | 1.24 | 0.132 | $0.1063 |
| 4 | `google/gemini-2.5-flash` | 1.21 | 0.217 | $0.1800 |
| 5 | `mistralai/codestral-2508` | 1.12 | 0.190 | $0.1705 |
| 6 | `meta-llama/llama-3.3-70b-instruct` | 0.92 | 0.313 | $0.3412 |
| 7 | `deepseek/deepseek-v3.2` | 0.87 | 0.291 | $0.3335 |
| 8 | `openai/gpt-3.5-turbo` | 0.73 | 0.197 | $0.2686 |
| 9 | `microsoft/phi-4` | 0.52 | 0.039 | $0.0757 |
| 10 | `claude-haiku-4-5-20251001` | 0.51 | 0.325 | $0.6316 |
| 11 | `deepseek/deepseek-r1-distill-llama-70b` | 0.48 | 0.186 | $0.3917 |
| 12 | `mistralai/mistral-large-2512` | 0.43 | 0.129 | $0.2972 |
| 13 | `openai/gpt-5.4` | 0.40 | 0.539 | $1.3382 |
| 14 | `openai/o3` | 0.33 | 0.531 | $1.6318 |
| 15 | `google/gemini-2.5-pro` | 0.27 | 0.549 | $2.0297 |
| 16 | `moonshotai/kimi-k2.6` | 0.23 | 0.365 | $1.5538 |
| 17 | `claude-sonnet-4-6` | 0.19 | 0.361 | $1.8552 |
| 18 | `openai/gpt-5.5` | 0.18 | 0.613 | $3.4570 |
| 19 | `claude-opus-4-7` | 0.14 | 0.593 | $4.0998 |
| 20 | `deepseek/deepseek-r1` | 0.12 | 0.382 | $3.2723 |
| 21 | `cohere/command-r-plus-08-2024` | 0.12 | 0.156 | $1.3560 |
| 22 | `openai/o4-mini` | 0.07 | 0.073 | $1.0096 |

### Best Free-Tier (F1)

Models that came back at $0.00 cost. F1 / $ is undefined for these, so they are ranked by F1 alone. Free-tier eligibility on OpenRouter depends on the attribution headers wired into the benchmark (`HTTP-Referer`, `X-Title`); a model showing as free here may bill on your own deployment if those headers are missing.

| Rank | Model | F1 | p50 latency | JSON compliance |
|------|-------|----|-------------|-----------------|
| 1 | `qwen/qwen3.5-plus-02-15` | 0.616 | 51.5s | 1.00 |
| 2 | `google/gemma-4-31b-it` | 0.444 | 1.7s | 0.84 |
| 3 | `deepseek/deepseek-v4-flash` | 0.437 | 2.2s | 0.79 |
| 4 | `nvidia/llama-3.3-nemotron-super-49b-v1.5` | 0.373 | 23.1s | 0.72 |
| 5 | `mistralai/mistral-medium-3.1` | 0.370 | 0.9s | 1.00 |
| 6 | `cohere/command-a` | 0.337 | 3.6s | 0.70 |
| 7 | `meta-llama/llama-4-maverick` | 0.294 | 1.3s | 0.76 |
| 8 | `meta-llama/llama-4-scout` | 0.231 | 0.8s | 0.81 |
| 9 | `nvidia/nemotron-nano-9b-v2` | 0.133 | 11.1s | 0.88 |
| 10 | `mistralai/mistral-7b-instruct-v0.1` | 0.000 | 10.5s | 0.07 |

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
| `x-ai/grok-4.1-fast` | 0.551 | 0.808 | 94 | 84 | 31 |
| `qwen/qwen3.5-plus-02-15` | 0.507 | 0.817 | 96 | 110 | 29 |
| `openai/gpt-5.5` | 0.523 | 0.774 | 89 | 90 | 36 |
| `claude-opus-4-7` | 0.487 | 0.794 | 93 | 110 | 32 |
| `google/gemini-2.5-pro` | 0.422 | 0.839 | 98 | 154 | 27 |
| `openai/gpt-5.4` | 0.440 | 0.791 | 92 | 134 | 33 |
| `openai/o3` | 0.702 | 0.482 | 59 | 26 | 66 |
| `google/gemma-4-31b-it` | 0.320 | 0.737 | 87 | 228 | 38 |
| `deepseek/deepseek-v4-flash` | 0.306 | 0.799 | 96 | 240 | 29 |
| `deepseek/deepseek-r1` | 0.276 | 0.647 | 79 | 218 | 46 |
| `nvidia/llama-3.3-nemotron-super-49b-v1.5` | 0.332 | 0.474 | 59 | 109 | 66 |
| `mistralai/mistral-medium-3.1` | 0.302 | 0.525 | 71 | 233 | 54 |
| `moonshotai/kimi-k2.6` | 0.372 | 0.448 | 49 | 77 | 76 |
| `claude-sonnet-4-6` | 0.287 | 0.511 | 70 | 211 | 55 |
| `cohere/command-a` | 0.239 | 0.676 | 75 | 244 | 50 |
| `claude-haiku-4-5-20251001` | 0.226 | 0.589 | 80 | 321 | 45 |
| `meta-llama/llama-3.3-70b-instruct` | 0.250 | 0.431 | 60 | 179 | 65 |
| `meta-llama/llama-4-maverick` | 0.221 | 0.500 | 67 | 179 | 58 |
| `deepseek/deepseek-v3.2` | 0.286 | 0.338 | 41 | 95 | 84 |
| `deepseek/deepseek-r1-0528` | 0.199 | 0.641 | 74 | 404 | 51 |
| `meta-llama/llama-4-scout` | 0.174 | 0.359 | 51 | 257 | 74 |
| `google/gemini-2.5-flash` | 0.144 | 0.450 | 65 | 410 | 60 |
| `openai/gpt-3.5-turbo` | 0.143 | 0.414 | 55 | 323 | 70 |
| `mistralai/codestral-2508` | 0.130 | 0.373 | 57 | 320 | 68 |
| `deepseek/deepseek-r1-distill-llama-70b` | 0.137 | 0.383 | 51 | 287 | 74 |
| `cohere/command-r-plus-08-2024` | 0.195 | 0.163 | 28 | 41 | 97 |
| `nvidia/nemotron-nano-9b-v2` | 0.099 | 0.212 | 29 | 263 | 96 |
| `meta-llama/llama-3.1-8b-instruct` | 0.119 | 0.234 | 30 | 616 | 95 |
| `mistralai/mistral-large-2512` | 0.075 | 0.472 | 63 | 667 | 62 |
| `openai/o4-mini` | 0.183 | 0.048 | 6 | 13 | 119 |
| `microsoft/phi-4` | 0.029 | 0.063 | 9 | 274 | 116 |
| `mistralai/mistral-7b-instruct-v0.1` | 0.000 | 0.000 | 0 | 0 | 125 |

## Boundary accuracy

For ads that match the truth at IoU >= 0.5, how far off were the predicted start and end timestamps? Lower is better. A model can hit F1 cleanly while still being 20s off on every boundary. Bad for any pipeline that cuts the audio.

| Model | Start MAE (s) | End MAE (s) |
|---|---:|---:|
| `claude-haiku-4-5-20251001` | 3.80 | 3.07 |
| `claude-sonnet-4-6` | 4.30 | 2.94 |
| `deepseek/deepseek-v3.2` | 7.34 | 0.03 |
| `google/gemini-2.5-flash` | 7.64 | 0.02 |
| `openai/o4-mini` | 8.72 | 0.10 |
| `deepseek/deepseek-r1` | 6.71 | 3.07 |
| `mistralai/mistral-large-2512` | 10.52 | 0.03 |
| `nvidia/llama-3.3-nemotron-super-49b-v1.5` | 7.41 | 3.34 |
| `qwen/qwen3.5-plus-02-15` | 10.19 | 0.81 |
| `x-ai/grok-4.1-fast` | 8.33 | 2.72 |
| `deepseek/deepseek-r1-0528` | 7.94 | 4.14 |
| `google/gemini-2.5-pro` | 9.35 | 2.74 |
| `cohere/command-a` | 9.14 | 3.82 |
| `meta-llama/llama-4-scout` | 6.67 | 6.47 |
| `meta-llama/llama-4-maverick` | 2.69 | 11.12 |
| `mistralai/codestral-2508` | 4.68 | 9.47 |
| `meta-llama/llama-3.3-70b-instruct` | 4.73 | 9.55 |
| `openai/gpt-5.4` | 13.70 | 1.09 |
| `openai/gpt-5.5` | 12.28 | 2.74 |
| `google/gemma-4-31b-it` | 15.09 | 1.02 |
| `mistralai/mistral-medium-3.1` | 11.08 | 5.66 |
| `deepseek/deepseek-r1-distill-llama-70b` | 4.02 | 12.78 |
| `claude-opus-4-7` | 13.24 | 4.15 |
| `openai/o3` | 15.51 | 3.87 |
| `nvidia/nemotron-nano-9b-v2` | 15.43 | 4.66 |
| `microsoft/phi-4` | 7.39 | 12.77 |
| `moonshotai/kimi-k2.6` | 18.23 | 2.86 |
| `deepseek/deepseek-v4-flash` | 12.63 | 9.64 |
| `openai/gpt-3.5-turbo` | 13.54 | 11.46 |
| `cohere/command-r-plus-08-2024` | 8.47 | 17.57 |
| `meta-llama/llama-3.1-8b-instruct` | 27.85 | 11.75 |

## Confidence calibration

Models include a self-reported `confidence` on each detected ad. A well-calibrated model should be right ~95% of the time when it claims 0.95 confidence. The table below bins each model's predictions and shows the actual hit rate (fraction that were true positives at IoU >= 0.5). A bin near 1.0 is well-calibrated; a low number with a high count means the model is overconfident.

| Model | 0.00-0.70 | 0.70-0.90 | 0.90-0.95 | 0.95-0.99 | 0.99+ | total |
|---|---:|---:|---:|---:|---:|---:|
| `claude-haiku-4-5-20251001` | -- | 0.00 (n=25) | 0.18 (n=111) | 0.23 (n=265) | -- | 401 |
| `claude-opus-4-7` | -- | 0.00 (n=5) | 0.00 (n=3) | 0.45 (n=172) | 0.65 (n=23) | 203 |
| `claude-sonnet-4-6` | -- | 0.00 (n=21) | 0.17 (n=35) | 0.25 (n=197) | 0.50 (n=28) | 281 |
| `cohere/command-a` | -- | 0.00 (n=22) | -- | 0.24 (n=308) | 0.00 (n=1) | 331 |
| `cohere/command-r-plus-08-2024` | -- | -- | 0.00 (n=1) | 0.27 (n=15) | 0.45 (n=53) | 69 |
| `deepseek/deepseek-r1` | -- | 0.00 (n=1) | 0.00 (n=1) | 0.27 (n=229) | 0.25 (n=71) | 302 |
| `deepseek/deepseek-r1-0528` | 0.00 (n=2) | 0.00 (n=4) | 0.00 (n=26) | 0.07 (n=361) | 0.39 (n=119) | 512 |
| `deepseek/deepseek-r1-distill-llama-70b` | -- | 0.02 (n=126) | -- | 0.22 (n=221) | -- | 347 |
| `deepseek/deepseek-v3.2` | -- | -- | 0.00 (n=2) | 0.09 (n=69) | 0.54 (n=65) | 136 |
| `deepseek/deepseek-v4-flash` | 0.00 (n=1) | 1.00 (n=1) | 0.00 (n=1) | 0.21 (n=211) | 0.41 (n=123) | 337 |
| `google/gemini-2.5-flash` | -- | -- | 0.00 (n=25) | 0.15 (n=328) | 0.12 (n=122) | 475 |
| `google/gemini-2.5-pro` | -- | 0.00 (n=9) | 0.00 (n=14) | 0.28 (n=65) | 0.48 (n=168) | 256 |
| `google/gemma-4-31b-it` | -- | 0.12 (n=8) | 0.14 (n=22) | 0.18 (n=114) | 0.35 (n=176) | 320 |
| `meta-llama/llama-3.1-8b-instruct` | -- | 0.00 (n=5) | 0.00 (n=2) | 0.05 (n=639) | -- | 646 |
| `meta-llama/llama-3.3-70b-instruct` | -- | 0.00 (n=10) | 0.26 (n=27) | 0.04 (n=91) | 0.44 (n=111) | 239 |
| `meta-llama/llama-4-maverick` | 0.00 (n=1) | 0.00 (n=41) | 0.14 (n=37) | 0.36 (n=170) | 0.00 (n=2) | 251 |
| `meta-llama/llama-4-scout` | -- | 0.00 (n=2) | 0.10 (n=10) | 0.15 (n=278) | 0.39 (n=18) | 308 |
| `microsoft/phi-4` | -- | 0.00 (n=20) | 0.00 (n=14) | 0.04 (n=254) | -- | 288 |
| `mistralai/codestral-2508` | -- | -- | 0.00 (n=4) | 0.15 (n=373) | -- | 377 |
| `mistralai/mistral-large-2512` | 0.00 (n=2) | 0.00 (n=20) | 0.00 (n=41) | 0.03 (n=336) | 0.16 (n=331) | 730 |
| `mistralai/mistral-medium-3.1` | -- | 0.00 (n=2) | 0.00 (n=13) | 0.24 (n=275) | 0.36 (n=14) | 304 |
| `moonshotai/kimi-k2.6` | 0.00 (n=20) | 0.05 (n=19) | -- | 0.48 (n=48) | 0.61 (n=41) | 128 |
| `nvidia/llama-3.3-nemotron-super-49b-v1.5` | -- | -- | 0.00 (n=7) | 0.36 (n=160) | 1.00 (n=1) | 168 |
| `nvidia/nemotron-nano-9b-v2` | -- | 0.00 (n=2) | 0.00 (n=19) | 0.11 (n=261) | 0.09 (n=11) | 293 |
| `openai/gpt-3.5-turbo` | -- | -- | 0.00 (n=1) | 0.19 (n=246) | 0.06 (n=142) | 389 |
| `openai/gpt-5.4` | 0.00 (n=10) | 0.09 (n=32) | 0.00 (n=21) | 0.45 (n=42) | 0.56 (n=126) | 231 |
| `openai/gpt-5.5` | -- | 0.50 (n=6) | 0.00 (n=4) | 0.49 (n=35) | 0.51 (n=135) | 180 |
| `openai/o3` | -- | -- | 0.50 (n=4) | 0.68 (n=76) | 1.00 (n=5) | 85 |
| `openai/o4-mini` | -- | 0.00 (n=1) | 0.00 (n=1) | 0.38 (n=16) | 0.00 (n=1) | 19 |
| `qwen/qwen3.5-plus-02-15` | -- | 0.00 (n=3) | 0.33 (n=3) | 0.48 (n=193) | 0.29 (n=7) | 206 |
| `x-ai/grok-4.1-fast` | -- | 1.00 (n=1) | 0.00 (n=1) | 0.51 (n=78) | 0.54 (n=98) | 178 |

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
| `mistralai/mistral-medium-3.1` | 19,102 | 304 | 63 | $0.0000 |
| `mistralai/codestral-2508` | 24,165 | 377 | 64 | $0.0030 |
| `meta-llama/llama-3.3-70b-instruct` | 16,099 | 239 | 67 | $0.0057 |
| `google/gemini-2.5-flash` | 34,635 | 475 | 73 | $0.0028 |
| `openai/gpt-3.5-turbo` | 28,749 | 389 | 74 | $0.0049 |
| `google/gemma-4-31b-it` | 24,421 | 320 | 76 | $0.0000 |
| `cohere/command-r-plus-08-2024` | 5,534 | 69 | 80 | $0.0484 |
| `meta-llama/llama-3.1-8b-instruct` | 52,273 | 646 | 81 | $0.0035 |
| `meta-llama/llama-4-scout` | 25,395 | 308 | 82 | $0.0000 |
| `deepseek/deepseek-v3.2` | 12,325 | 136 | 91 | $0.0081 |
| `claude-sonnet-4-6` | 25,526 | 281 | 91 | $0.0265 |
| `cohere/command-a` | 30,494 | 331 | 92 | $0.0000 |
| `claude-opus-4-7` | 18,811 | 203 | 93 | $0.0441 |
| `mistralai/mistral-large-2512` | 67,887 | 730 | 93 | $0.0047 |
| `claude-haiku-4-5-20251001` | 38,866 | 401 | 97 | $0.0079 |
| `openai/gpt-5.4` | 24,262 | 231 | 105 | $0.0145 |
| `meta-llama/llama-4-maverick` | 28,581 | 251 | 114 | $0.0000 |
| `deepseek/deepseek-r1-distill-llama-70b` | 101,093 | 347 | 291 | $0.0077 |
| `microsoft/phi-4` | 95,070 | 288 | 330 | $0.0084 |
| `deepseek/deepseek-v4-flash` | 169,352 | 337 | 503 | $0.0000 |
| `openai/gpt-5.5` | 154,347 | 180 | 857 | $0.0388 |
| `deepseek/deepseek-r1-0528` | 608,318 | 512 | 1188 | $0.0024 |
| `deepseek/deepseek-r1` | 440,399 | 302 | 1458 | $0.0414 |
| `nvidia/nemotron-nano-9b-v2` | 686,205 | 293 | 2342 | $0.0000 |
| `google/gemini-2.5-pro` | 676,791 | 256 | 2644 | $0.0207 |
| `x-ai/grok-4.1-fast` | 476,521 | 178 | 2677 | $0.0016 |
| `nvidia/llama-3.3-nemotron-super-49b-v1.5` | 742,021 | 168 | 4417 | $0.0000 |
| `openai/o3` | 387,167 | 85 | 4555 | $0.0277 |
| `qwen/qwen3.5-plus-02-15` | 1,782,733 | 206 | 8654 | $0.0000 |
| `moonshotai/kimi-k2.6` | 1,331,057 | 128 | 10399 | $0.0317 |
| `openai/o4-mini` | 514,467 | 19 | 27077 | $0.1683 |

## Trial variance (determinism check)

All trials run at temperature 0.0. If a model produces stable output you'd expect the F1 stdev across trials to be near zero. Higher numbers mean the model is non-deterministic even at temp=0. That's fine to know, but means you cannot trust a single trial's number for that model.

| Model | Mean F1 stdev across episodes | Highest single-episode stdev |
|---|---:|---:|
| `x-ai/grok-4.1-fast` | 0.0539 | 0.1168 |
| `qwen/qwen3.5-plus-02-15` | 0.0263 | 0.0569 |
| `openai/gpt-5.5` | 0.0663 | 0.1432 |
| `claude-opus-4-7` | 0.0400 | 0.0958 |
| `google/gemini-2.5-pro` | 0.0256 | 0.0632 |
| `openai/gpt-5.4` | 0.0777 | 0.1193 |
| `openai/o3` | 0.1999 | 0.4714 |
| `google/gemma-4-31b-it` | 0.0979 | 0.2739 |
| `deepseek/deepseek-v4-flash` | 0.0884 | 0.2392 |
| `deepseek/deepseek-r1` | 0.1145 | 0.3015 |
| `nvidia/llama-3.3-nemotron-super-49b-v1.5` | 0.1609 | 0.3249 |
| `mistralai/mistral-medium-3.1` | 0.0577 | 0.1373 |
| `moonshotai/kimi-k2.6` | 0.1740 | 0.2739 |
| `claude-sonnet-4-6` | 0.0173 | 0.0479 |
| `cohere/command-a` | 0.0371 | 0.0896 |
| `claude-haiku-4-5-20251001` | 0.0022 | 0.0116 |
| `meta-llama/llama-3.3-70b-instruct` | 0.0149 | 0.0349 |
| `meta-llama/llama-4-maverick` | 0.0245 | 0.1012 |
| `deepseek/deepseek-v3.2` | 0.1918 | 0.5477 |
| `deepseek/deepseek-r1-0528` | 0.1178 | 0.2807 |
| `meta-llama/llama-4-scout` | 0.0718 | 0.1375 |
| `google/gemini-2.5-flash` | 0.0000 | 0.0000 |
| `openai/gpt-3.5-turbo` | 0.0026 | 0.0060 |
| `mistralai/codestral-2508` | 0.0432 | 0.0914 |
| `deepseek/deepseek-r1-distill-llama-70b` | 0.0477 | 0.1589 |
| `cohere/command-r-plus-08-2024` | 0.0560 | 0.1547 |
| `nvidia/nemotron-nano-9b-v2` | 0.0744 | 0.1439 |
| `meta-llama/llama-3.1-8b-instruct` | 0.1318 | 0.5477 |
| `mistralai/mistral-large-2512` | 0.0205 | 0.0412 |
| `openai/o4-mini` | 0.1222 | 0.2490 |
| `microsoft/phi-4` | 0.0478 | 0.0925 |
| `mistralai/mistral-7b-instruct-v0.1` | 0.0000 | 0.0000 |

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

## Detection rate by ad characteristic

Aggregate detection rates often hide systematic blind spots. Below: for each model, what fraction of truth ads in each bucket were detected (matched at IoU >= 0.5).

### By ad length

Truth ads bucketed by duration: short (<30s), medium (30-90s), long (>=90s). Cell values are detection rate (fraction of truth ads in that bucket the model caught), with the sample size `n` so a misleading 1.00 on a 2-ad bucket doesn't get over-weighted. Models that systematically miss short ads usually fail on network-inserted brand-tagline spots; missing long ads is rarer and usually means the model gave up before processing the full window.

| Model | long (>=90s) | medium (30-90s) | short (<30s) |
|---|---:|---:|---:|
| `claude-haiku-4-5-20251001` | 0.53 (n=75) | 0.80 (n=25) | 0.80 (n=25) |
| `claude-opus-4-7` | 0.84 (n=75) | 0.60 (n=25) | 0.60 (n=25) |
| `claude-sonnet-4-6` | 0.56 (n=75) | 0.52 (n=25) | 0.60 (n=25) |
| `cohere/command-a` | 0.60 (n=75) | 0.40 (n=25) | 0.80 (n=25) |
| `cohere/command-r-plus-08-2024` | 0.35 (n=75) | 0.08 (n=25) | 0.00 (n=25) |
| `deepseek/deepseek-r1` | 0.57 (n=75) | 0.64 (n=25) | 0.80 (n=25) |
| `deepseek/deepseek-r1-0528` | 0.51 (n=75) | 0.64 (n=25) | 0.80 (n=25) |
| `deepseek/deepseek-r1-distill-llama-70b` | 0.32 (n=75) | 0.44 (n=25) | 0.64 (n=25) |
| `deepseek/deepseek-v3.2` | 0.36 (n=75) | 0.28 (n=25) | 0.28 (n=25) |
| `deepseek/deepseek-v4-flash` | 0.71 (n=75) | 0.92 (n=25) | 0.80 (n=25) |
| `google/gemini-2.5-flash` | 0.40 (n=75) | 0.60 (n=25) | 0.80 (n=25) |
| `google/gemini-2.5-pro` | 0.77 (n=75) | 0.80 (n=25) | 0.80 (n=25) |
| `google/gemma-4-31b-it` | 0.65 (n=75) | 0.76 (n=25) | 0.76 (n=25) |
| `meta-llama/llama-3.1-8b-instruct` | 0.23 (n=75) | 0.28 (n=25) | 0.24 (n=25) |
| `meta-llama/llama-3.3-70b-instruct` | 0.47 (n=75) | 0.40 (n=25) | 0.60 (n=25) |
| `meta-llama/llama-4-maverick` | 0.47 (n=75) | 0.68 (n=25) | 0.60 (n=25) |
| `meta-llama/llama-4-scout` | 0.36 (n=75) | 0.48 (n=25) | 0.48 (n=25) |
| `microsoft/phi-4` | 0.03 (n=75) | 0.08 (n=25) | 0.20 (n=25) |
| `mistralai/codestral-2508` | 0.52 (n=75) | 0.40 (n=25) | 0.32 (n=25) |
| `mistralai/mistral-7b-instruct-v0.1` | 0.00 (n=75) | 0.00 (n=25) | 0.00 (n=25) |
| `mistralai/mistral-large-2512` | 0.44 (n=75) | 0.60 (n=25) | 0.60 (n=25) |
| `mistralai/mistral-medium-3.1` | 0.45 (n=75) | 0.72 (n=25) | 0.76 (n=25) |
| `moonshotai/kimi-k2.6` | 0.39 (n=75) | 0.44 (n=25) | 0.36 (n=25) |
| `nvidia/llama-3.3-nemotron-super-49b-v1.5` | 0.44 (n=75) | 0.40 (n=25) | 0.64 (n=25) |
| `nvidia/nemotron-nano-9b-v2` | 0.19 (n=75) | 0.28 (n=25) | 0.32 (n=25) |
| `openai/gpt-3.5-turbo` | 0.40 (n=75) | 0.20 (n=25) | 0.80 (n=25) |
| `openai/gpt-5.4` | 0.79 (n=75) | 0.60 (n=25) | 0.72 (n=25) |
| `openai/gpt-5.5` | 0.71 (n=75) | 0.72 (n=25) | 0.72 (n=25) |
| `openai/o3` | 0.55 (n=75) | 0.28 (n=25) | 0.44 (n=25) |
| `openai/o4-mini` | 0.04 (n=75) | 0.04 (n=25) | 0.08 (n=25) |
| `qwen/qwen3.5-plus-02-15` | 0.85 (n=75) | 0.64 (n=25) | 0.64 (n=25) |
| `x-ai/grok-4.1-fast` | 0.75 (n=75) | 0.76 (n=25) | 0.76 (n=25) |

### By ad position

Truth ads bucketed by where they fall in the episode: pre-roll (first 10%), mid-roll (10-90%), post-roll (last 10%). Cell values are the same detection-rate-with-`n` format as ad length. A common failure pattern in our data: most models detect pre-roll and mid-roll reliably and miss post-roll, because the prompt windows near the end often catch the model mid-reasoning or with fewer transition phrases to anchor on.

| Model | pre-roll (<10%) | mid-roll (10-90%) | post-roll (>90%) |
|---|---:|---:|---:|
| `claude-haiku-4-5-20251001` | 0.71 (n=35) | 0.67 (n=60) | 0.50 (n=30) |
| `claude-opus-4-7` | 0.71 (n=35) | 0.85 (n=60) | 0.57 (n=30) |
| `claude-sonnet-4-6` | 0.71 (n=35) | 0.58 (n=60) | 0.33 (n=30) |
| `cohere/command-a` | 0.57 (n=35) | 0.75 (n=60) | 0.33 (n=30) |
| `cohere/command-r-plus-08-2024` | 0.11 (n=35) | 0.40 (n=60) | 0.00 (n=30) |
| `deepseek/deepseek-r1` | 0.60 (n=35) | 0.75 (n=60) | 0.43 (n=30) |
| `deepseek/deepseek-r1-0528` | 0.54 (n=35) | 0.68 (n=60) | 0.47 (n=30) |
| `deepseek/deepseek-r1-distill-llama-70b` | 0.49 (n=35) | 0.40 (n=60) | 0.33 (n=30) |
| `deepseek/deepseek-v3.2` | 0.29 (n=35) | 0.47 (n=60) | 0.10 (n=30) |
| `deepseek/deepseek-v4-flash` | 0.69 (n=35) | 0.92 (n=60) | 0.57 (n=30) |
| `google/gemini-2.5-flash` | 0.57 (n=35) | 0.58 (n=60) | 0.33 (n=30) |
| `google/gemini-2.5-pro` | 0.71 (n=35) | 0.97 (n=60) | 0.50 (n=30) |
| `google/gemma-4-31b-it` | 0.71 (n=35) | 0.77 (n=60) | 0.53 (n=30) |
| `meta-llama/llama-3.1-8b-instruct` | 0.26 (n=35) | 0.32 (n=60) | 0.07 (n=30) |
| `meta-llama/llama-3.3-70b-instruct` | 0.43 (n=35) | 0.58 (n=60) | 0.33 (n=30) |
| `meta-llama/llama-4-maverick` | 0.49 (n=35) | 0.67 (n=60) | 0.33 (n=30) |
| `meta-llama/llama-4-scout` | 0.43 (n=35) | 0.50 (n=60) | 0.20 (n=30) |
| `microsoft/phi-4` | 0.14 (n=35) | 0.03 (n=60) | 0.07 (n=30) |
| `mistralai/codestral-2508` | 0.43 (n=35) | 0.55 (n=60) | 0.30 (n=30) |
| `mistralai/mistral-7b-instruct-v0.1` | 0.00 (n=35) | 0.00 (n=60) | 0.00 (n=30) |
| `mistralai/mistral-large-2512` | 0.69 (n=35) | 0.57 (n=60) | 0.17 (n=30) |
| `mistralai/mistral-medium-3.1` | 0.66 (n=35) | 0.60 (n=60) | 0.40 (n=30) |
| `moonshotai/kimi-k2.6` | 0.31 (n=35) | 0.45 (n=60) | 0.37 (n=30) |
| `nvidia/llama-3.3-nemotron-super-49b-v1.5` | 0.34 (n=35) | 0.65 (n=60) | 0.27 (n=30) |
| `nvidia/nemotron-nano-9b-v2` | 0.09 (n=35) | 0.32 (n=60) | 0.23 (n=30) |
| `openai/gpt-3.5-turbo` | 0.14 (n=35) | 0.67 (n=60) | 0.33 (n=30) |
| `openai/gpt-5.4` | 0.69 (n=35) | 0.88 (n=60) | 0.50 (n=30) |
| `openai/gpt-5.5` | 0.63 (n=35) | 0.87 (n=60) | 0.50 (n=30) |
| `openai/o3` | 0.37 (n=35) | 0.60 (n=60) | 0.33 (n=30) |
| `openai/o4-mini` | 0.00 (n=35) | 0.07 (n=60) | 0.07 (n=30) |
| `qwen/qwen3.5-plus-02-15` | 0.71 (n=35) | 0.93 (n=60) | 0.50 (n=30) |
| `x-ai/grok-4.1-fast` | 0.66 (n=35) | 0.97 (n=60) | 0.43 (n=30) |

## Quick Comparison

One row per model, one column per episode. The headline columns (`F1`, `Cost/ep`, `p50`) summarize across all episodes; the per-episode columns let you see whether a model's average hides wide swings (a model that scores well overall might still bomb on a specific genre). The right-most `F1 stdev` column averages the per-trial standard deviations across episodes; high values mean the model isn't deterministic at temperature 0.0, so its single-trial F1 number is noisy.

| Model | F1 | Cost/ep | p50 | ep-daily-tech-news-show-c1904b8605f7 | ep-glt1412515089-373d5ba5007b | ep-it-s-a-thing-e339179dfad6 | ep-security-now-audio-2850b24903b2 | ep-the-brilliant-idiots-0bb9bf634c8e | ep-the-tim-dillon-show-f62bd5fa1cfe | ep-ai-cloud-essentials-e8dc897fbd6b (no-ad) | F1 stdev |
|---|---|---|---|---|---|---|---|---|---|---|---|
| `x-ai/grok-4.1-fast` | 0.642 | $0.1509 | 11.1s | 0.578 | 0.716 | 0.667 | 0.539 | 0.757 | 0.594 | PASS | 0.054 |
| `qwen/qwen3.5-plus-02-15` | 0.616 | $0.0000 | 51.5s | 0.518 | 0.625 | 0.667 | 0.476 | 0.771 | 0.636 | PASS | 0.026 |
| `openai/gpt-5.5` | 0.613 | $3.4570 | 6.1s | 0.547 | 0.636 | 0.667 | 0.505 | 0.776 | 0.546 | FAIL (1 FP) | 0.066 |
| `claude-opus-4-7` | 0.593 | $4.0998 | 2.3s | 0.445 | 0.600 | 0.667 | 0.520 | 0.733 | 0.592 | PASS | 0.040 |
| `google/gemini-2.5-pro` | 0.549 | $2.0297 | 13.5s | 0.448 | 0.646 | 0.667 | 0.451 | 0.510 | 0.569 | FAIL (1 FP) | 0.026 |
| `openai/gpt-5.4` | 0.539 | $1.3382 | 1.8s | 0.518 | 0.506 | 0.613 | 0.495 | 0.516 | 0.586 | FAIL (1 FP) | 0.078 |
| `openai/o3` | 0.531 | $1.6318 | 8.5s | 0.472 | 0.664 | 0.333 | 0.644 | 0.698 | 0.372 | PASS | 0.200 |
| `google/gemma-4-31b-it` | 0.444 | $0.0000 | 1.7s | 0.119 | 0.645 | 0.467 | 0.496 | 0.585 | 0.350 | FAIL (1 FP) | 0.098 |
| `deepseek/deepseek-v4-flash` | 0.437 | $0.0000 | 2.2s | 0.310 | 0.642 | 0.337 | 0.465 | 0.569 | 0.298 | FAIL (1 FP) | 0.088 |
| `deepseek/deepseek-r1` | 0.382 | $3.2723 | 19.3s | 0.158 | 0.630 | 0.313 | 0.462 | 0.401 | 0.327 | FAIL (1 FP) | 0.115 |
| `nvidia/llama-3.3-nemotron-super-49b-v1.5` | 0.373 | $0.0000 | 23.1s | 0.173 | 0.596 | 0.233 | 0.587 | 0.478 | 0.171 | PASS | 0.161 |
| `mistralai/mistral-medium-3.1` | 0.370 | $0.0000 | 0.9s | 0.095 | 0.671 | 0.000 | 0.640 | 0.591 | 0.223 | PASS | 0.058 |
| `moonshotai/kimi-k2.6` | 0.365 | $1.5538 | 35.9s | 0.600 | 0.469 | 0.200 | 0.196 | 0.538 | 0.184 | FAIL (1 FP) | 0.174 |
| `claude-sonnet-4-6` | 0.361 | $1.8552 | 1.5s | 0.237 | 0.400 | 0.000 | 0.516 | 0.836 | 0.179 | PASS | 0.017 |
| `cohere/command-a` | 0.337 | $0.0000 | 3.6s | 0.298 | 0.507 | 0.400 | 0.368 | 0.247 | 0.200 | FAIL (3 FP) | 0.037 |
| `claude-haiku-4-5-20251001` | 0.325 | $0.6316 | 1.4s | 0.073 | 0.571 | 0.000 | 0.551 | 0.600 | 0.154 | PASS | 0.002 |
| `meta-llama/llama-3.3-70b-instruct` | 0.313 | $0.3412 | 1.6s | 0.000 | 0.567 | 0.000 | 0.512 | 0.489 | 0.308 | PASS | 0.015 |
| `meta-llama/llama-4-maverick` | 0.294 | $0.0000 | 1.3s | 0.204 | 0.507 | 0.000 | 0.496 | 0.390 | 0.167 | FAIL (1 FP) | 0.024 |
| `deepseek/deepseek-v3.2` | 0.291 | $0.3335 | 2.1s | 0.140 | 0.518 | 0.400 | 0.528 | 0.100 | 0.057 | PASS | 0.192 |
| `deepseek/deepseek-r1-0528` | 0.283 | $0.1789 | 14.8s | 0.184 | 0.379 | 0.404 | 0.281 | 0.161 | 0.292 | FAIL (27 FP) | 0.118 |
| `meta-llama/llama-4-scout` | 0.231 | $0.0000 | 0.8s | 0.130 | 0.336 | 0.000 | 0.520 | 0.349 | 0.053 | PASS | 0.072 |
| `google/gemini-2.5-flash` | 0.217 | $0.1800 | 1.0s | 0.071 | 0.500 | 0.000 | 0.455 | 0.125 | 0.154 | PASS | 0.000 |
| `openai/gpt-3.5-turbo` | 0.197 | $0.2686 | 1.2s | 0.364 | 0.254 | 0.000 | 0.217 | 0.220 | 0.125 | FAIL (3 FP) | 0.003 |
| `mistralai/codestral-2508` | 0.190 | $0.1705 | 0.8s | 0.172 | 0.231 | 0.000 | 0.374 | 0.187 | 0.178 | PASS | 0.043 |
| `deepseek/deepseek-r1-distill-llama-70b` | 0.186 | $0.3917 | 1.9s | 0.205 | 0.265 | 0.000 | 0.262 | 0.222 | 0.163 | FAIL (2 FP) | 0.048 |
| `cohere/command-r-plus-08-2024` | 0.156 | $1.3560 | 1.0s | 0.313 | 0.000 | 0.000 | 0.569 | 0.000 | 0.057 | PASS | 0.056 |
| `nvidia/nemotron-nano-9b-v2` | 0.133 | $0.0000 | 11.1s | 0.069 | 0.242 | 0.000 | 0.238 | 0.205 | 0.044 | FAIL (1 FP) | 0.074 |
| `meta-llama/llama-3.1-8b-instruct` | 0.132 | $0.1063 | 0.7s | 0.029 | 0.183 | 0.400 | 0.133 | 0.000 | 0.049 | PASS | 0.132 |
| `mistralai/mistral-large-2512` | 0.129 | $0.2972 | 3.1s | 0.074 | 0.253 | 0.000 | 0.209 | 0.193 | 0.044 | PASS | 0.020 |
| `openai/o4-mini` | 0.073 | $1.0096 | 7.3s | 0.067 | 0.080 | 0.000 | 0.114 | 0.180 | 0.000 | PASS | 0.122 |
| `microsoft/phi-4` | 0.039 | $0.0757 | 2.3s | 0.056 | 0.000 | 0.000 | 0.033 | 0.067 | 0.079 | FAIL (3 FP) | 0.048 |
| `mistralai/mistral-7b-instruct-v0.1` | 0.000 | $0.0000 | 10.5s | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | PASS | 0.000 |

---

## Detailed Results

### Per-Model Detail

Full per-model profile: F1 averaged across episodes, total cost per episode at current pricing, p50 / p95 latency, JSON compliance, parse-failure rate, and the distribution of extraction methods the parser had to use. The `Extraction methods` list shows how often each route was hit. `json_array_direct` is the cleanest; the rest are recovery paths. Ordered by F1 descending so the best performers appear first.

#### `x-ai/grok-4.1-fast`

- F1 (avg across episodes): **0.642**
- Total cost / episode: **$0.1509**
- p50 / p95 latency: 11.07s / 35.37s
- JSON compliance: 0.87
- Parse failure rate: 0.2%
- Extraction methods: `json_object_no_ads`: 259, `json_object_single_ad`: 190, `parse_failure`: 1
- Schema violations: 263
- Extra keys observed: end_text, sponsor

#### `qwen/qwen3.5-plus-02-15`

- F1 (avg across episodes): **0.616**
- Total cost / episode: **$0.0000**
- p50 / p95 latency: 51.53s / 141.26s
- JSON compliance: 1.00
- Parse failure rate: 0.0%
- Extraction methods: `json_array_direct`: 450
- Schema violations: 384
- Extra keys observed: end_text, sponsor

#### `openai/gpt-5.5`

- F1 (avg across episodes): **0.613**
- Total cost / episode: **$3.4570**
- p50 / p95 latency: 6.12s / 19.67s
- JSON compliance: 0.87
- Parse failure rate: 0.2%
- Extraction methods: `json_object_no_ads`: 258, `json_object_single_ad`: 191, `parse_failure`: 1
- Schema violations: 332
- Extra keys observed: end_text, sponsor

#### `claude-opus-4-7`

- F1 (avg across episodes): **0.593**
- Total cost / episode: **$4.0998**
- p50 / p95 latency: 2.31s / 4.40s
- JSON compliance: 1.00
- Parse failure rate: 0.0%
- Extraction methods: `json_array_direct`: 448, `regex_json_array`: 2
- Schema violations: 397
- Extra keys observed: end_text, sponsor

#### `google/gemini-2.5-pro`

- F1 (avg across episodes): **0.549**
- Total cost / episode: **$2.0297**
- p50 / p95 latency: 13.46s / 27.10s
- JSON compliance: 0.97
- Parse failure rate: 0.4%
- Extraction methods: `json_array_direct`: 429, `parse_failure`: 2, `regex_json_array`: 19
- Schema violations: 389
- Extra keys observed: end_text, sponsor

#### `openai/gpt-5.4`

- F1 (avg across episodes): **0.539**
- Total cost / episode: **$1.3382**
- p50 / p95 latency: 1.83s / 2.99s
- JSON compliance: 0.79
- Parse failure rate: 0.0%
- Extraction methods: `json_object_no_ads`: 134, `json_object_single_ad`: 316
- Schema violations: 398
- Extra keys observed: end_text, sponsor

#### `openai/o3`

- F1 (avg across episodes): **0.531**
- Total cost / episode: **$1.6318**
- p50 / p95 latency: 8.50s / 21.22s
- JSON compliance: 0.93
- Parse failure rate: 0.9%
- Extraction methods: `json_object_ads_key`: 12, `json_object_no_ads`: 341, `json_object_segments_key`: 6, `json_object_single_ad`: 87, `parse_failure`: 4
- Schema violations: 132
- Extra keys observed: end_text, sponsor

#### `google/gemma-4-31b-it`

- F1 (avg across episodes): **0.444**
- Total cost / episode: **$0.0000**
- p50 / p95 latency: 1.71s / 17.76s
- JSON compliance: 0.84
- Parse failure rate: 0.0%
- Extraction methods: `json_object_ads_key`: 278, `json_object_no_ads`: 72, `json_object_single_ad`: 99, `json_object_single_ad_truncated`: 1
- Schema violations: 532
- Extra keys observed: end_text, sponsor

#### `deepseek/deepseek-v4-flash`

- F1 (avg across episodes): **0.437**
- Total cost / episode: **$0.0000**
- p50 / p95 latency: 2.22s / 30.96s
- JSON compliance: 0.79
- Parse failure rate: 2.9%
- Extraction methods: `json_array_direct`: 13, `json_object_ads_key`: 303, `json_object_no_ads`: 2, `json_object_single_ad`: 119, `parse_failure`: 13
- Schema violations: 631
- Extra keys observed: end_text, sponsor

#### `deepseek/deepseek-r1`

- F1 (avg across episodes): **0.382**
- Total cost / episode: **$3.2723**
- p50 / p95 latency: 19.29s / 138.59s
- JSON compliance: 0.96
- Parse failure rate: 1.3%
- Extraction methods: `json_array_direct`: 390, `json_object_ads_key`: 2, `json_object_no_ads`: 12, `json_object_segments_key`: 3, `json_object_single_ad`: 28, `markdown_code_block`: 7, `parse_failure`: 6, `regex_json_array`: 2
- Schema violations: 486
- Extra keys observed: end_text, sponsor

#### `nvidia/llama-3.3-nemotron-super-49b-v1.5`

- F1 (avg across episodes): **0.373**
- Total cost / episode: **$0.0000**
- p50 / p95 latency: 23.07s / 86.32s
- JSON compliance: 0.72
- Parse failure rate: 15.6%
- Extraction methods: `json_array_direct`: 242, `markdown_code_block`: 127, `parse_failure`: 70, `regex_json_array`: 11
- Schema violations: 266
- Extra keys observed: end_text, sponsor

#### `mistralai/mistral-medium-3.1`

- F1 (avg across episodes): **0.370**
- Total cost / episode: **$0.0000**
- p50 / p95 latency: 0.91s / 5.68s
- JSON compliance: 1.00
- Parse failure rate: 0.0%
- Extraction methods: `json_array_direct`: 450
- Schema violations: 608
- Extra keys observed: end_text, sponsor

#### `moonshotai/kimi-k2.6`

- F1 (avg across episodes): **0.365**
- Total cost / episode: **$1.5538**
- p50 / p95 latency: 35.92s / 118.25s
- JSON compliance: 0.56
- Parse failure rate: 28.0%
- Extraction methods: `json_array_direct`: 31, `json_object_ads_key`: 14, `json_object_no_ads`: 53, `json_object_single_ad`: 226, `parse_failure`: 126
- Schema violations: 234
- Extra keys observed: end_text, sponsor

#### `claude-sonnet-4-6`

- F1 (avg across episodes): **0.361**
- Total cost / episode: **$1.8552**
- p50 / p95 latency: 1.50s / 5.64s
- JSON compliance: 0.96
- Parse failure rate: 0.0%
- Extraction methods: `json_array_direct`: 413, `markdown_code_block`: 27, `regex_json_array`: 10
- Schema violations: 461
- Extra keys observed: end_text, sponsor

#### `cohere/command-a`

- F1 (avg across episodes): **0.337**
- Total cost / episode: **$0.0000**
- p50 / p95 latency: 3.63s / 10.78s
- JSON compliance: 0.70
- Parse failure rate: 0.0%
- Extraction methods: `json_object_no_ads`: 3, `json_object_single_ad`: 447
- Schema violations: 478
- Extra keys observed: end_text, sponsor

#### `claude-haiku-4-5-20251001`

- F1 (avg across episodes): **0.325**
- Total cost / episode: **$0.6316**
- p50 / p95 latency: 1.43s / 4.09s
- JSON compliance: 0.60
- Parse failure rate: 0.0%
- Extraction methods: `markdown_code_block`: 450
- Schema violations: 711
- Extra keys observed: end_text, sponsor

#### `meta-llama/llama-3.3-70b-instruct`

- F1 (avg across episodes): **0.313**
- Total cost / episode: **$0.3412**
- p50 / p95 latency: 1.63s / 5.55s
- JSON compliance: 0.84
- Parse failure rate: 0.2%
- Extraction methods: `json_array_direct`: 101, `json_object_no_ads`: 118, `json_object_single_ad`: 229, `parse_failure`: 1, `regex_json_array`: 1
- Schema violations: 378
- Extra keys observed: end_text, sponsor

#### `meta-llama/llama-4-maverick`

- F1 (avg across episodes): **0.294**
- Total cost / episode: **$0.0000**
- p50 / p95 latency: 1.26s / 2.42s
- JSON compliance: 0.76
- Parse failure rate: 0.0%
- Extraction methods: `json_object_no_ads`: 93, `json_object_single_ad`: 357
- Schema violations: 372
- Extra keys observed: end_text, sponsor

#### `deepseek/deepseek-v3.2`

- F1 (avg across episodes): **0.291**
- Total cost / episode: **$0.3335**
- p50 / p95 latency: 2.09s / 5.45s
- JSON compliance: 0.94
- Parse failure rate: 0.0%
- Extraction methods: `json_array_direct`: 355, `json_object_ads_key`: 4, `json_object_single_ad`: 91
- Schema violations: 263
- Extra keys observed: end_text, sponsor

#### `deepseek/deepseek-r1-0528`

- F1 (avg across episodes): **0.283**
- Total cost / episode: **$0.1789**
- p50 / p95 latency: 14.85s / 80.27s
- JSON compliance: 0.87
- Parse failure rate: 10.7%
- Extraction methods: `json_array_direct`: 354, `json_object_ads_key`: 23, `json_object_no_ads`: 3, `json_object_single_ad`: 21, `markdown_code_block`: 1, `parse_failure`: 48
- Schema violations: 694
- Extra keys observed: end_text, sponsor

#### `meta-llama/llama-4-scout`

- F1 (avg across episodes): **0.231**
- Total cost / episode: **$0.0000**
- p50 / p95 latency: 0.85s / 3.05s
- JSON compliance: 0.81
- Parse failure rate: 0.2%
- Extraction methods: `bracket_fallback`: 23, `json_array_direct`: 3, `json_object_ads_key`: 318, `json_object_no_ads`: 44, `json_object_single_ad`: 58, `parse_failure`: 1, `regex_json_array`: 3
- Schema violations: 507
- Extra keys observed: end_text, sponsor

#### `google/gemini-2.5-flash`

- F1 (avg across episodes): **0.217**
- Total cost / episode: **$0.1800**
- p50 / p95 latency: 0.96s / 2.96s
- JSON compliance: 1.00
- Parse failure rate: 0.0%
- Extraction methods: `json_array_direct`: 450
- Schema violations: 710
- Extra keys observed: end_text, sponsor

#### `openai/gpt-3.5-turbo`

- F1 (avg across episodes): **0.197**
- Total cost / episode: **$0.2686**
- p50 / p95 latency: 1.21s / 1.76s
- JSON compliance: 0.70
- Parse failure rate: 0.0%
- Extraction methods: `json_object_no_ads`: 4, `json_object_single_ad`: 446
- Schema violations: 593
- Extra keys observed: end_text, sponsor

#### `mistralai/codestral-2508`

- F1 (avg across episodes): **0.190**
- Total cost / episode: **$0.1705**
- p50 / p95 latency: 0.78s / 2.04s
- JSON compliance: 1.00
- Parse failure rate: 0.0%
- Extraction methods: `json_array_direct`: 450
- Schema violations: 622
- Extra keys observed: end_text, sponsor

#### `deepseek/deepseek-r1-distill-llama-70b`

- F1 (avg across episodes): **0.186**
- Total cost / episode: **$0.3917**
- p50 / p95 latency: 1.87s / 20.38s
- JSON compliance: 0.73
- Parse failure rate: 0.9%
- Extraction methods: `json_array_direct`: 14, `json_object_ads_key`: 15, `json_object_no_ads`: 32, `json_object_single_ad`: 385, `parse_failure`: 4
- Schema violations: 474
- Extra keys observed: end_text, sponsor

#### `cohere/command-r-plus-08-2024`

- F1 (avg across episodes): **0.156**
- Total cost / episode: **$1.3560**
- p50 / p95 latency: 1.00s / 3.58s
- JSON compliance: 0.97
- Parse failure rate: 0.0%
- Extraction methods: `json_object_ads_key`: 10, `json_object_no_ads`: 395, `json_object_single_ad`: 45
- Schema violations: 132
- Extra keys observed: end_text, sponsor

#### `nvidia/nemotron-nano-9b-v2`

- F1 (avg across episodes): **0.133**
- Total cost / episode: **$0.0000**
- p50 / p95 latency: 11.08s / 35.76s
- JSON compliance: 0.88
- Parse failure rate: 10.2%
- Extraction methods: `json_array_direct`: 394, `parse_failure`: 46, `regex_json_array`: 10
- Schema violations: 476
- Extra keys observed: end_text, sponsor

#### `meta-llama/llama-3.1-8b-instruct`

- F1 (avg across episodes): **0.132**
- Total cost / episode: **$0.1063**
- p50 / p95 latency: 0.73s / 2.80s
- JSON compliance: 0.89
- Parse failure rate: 0.2%
- Extraction methods: `json_array_direct`: 242, `json_object_no_ads`: 52, `json_object_single_ad`: 155, `parse_failure`: 1
- Schema violations: 1270
- Extra keys observed: end_text, sponsor

#### `mistralai/mistral-large-2512`

- F1 (avg across episodes): **0.129**
- Total cost / episode: **$0.2972**
- p50 / p95 latency: 3.08s / 6.65s
- JSON compliance: 1.00
- Parse failure rate: 0.0%
- Extraction methods: `json_array_direct`: 450
- Schema violations: 1342
- Extra keys observed: end_text, sponsor

#### `openai/o4-mini`

- F1 (avg across episodes): **0.073**
- Total cost / episode: **$1.0096**
- p50 / p95 latency: 7.25s / 23.34s
- JSON compliance: 0.05
- Parse failure rate: 94.0%
- Extraction methods: `json_object_no_ads`: 8, `json_object_single_ad`: 19, `parse_failure`: 423
- Schema violations: 28
- Extra keys observed: end_text, sponsor

#### `microsoft/phi-4`

- F1 (avg across episodes): **0.039**
- Total cost / episode: **$0.0757**
- p50 / p95 latency: 2.33s / 9.26s
- JSON compliance: 0.85
- Parse failure rate: 1.3%
- Extraction methods: `json_array_direct`: 209, `json_object_ads_key`: 21, `json_object_no_ads`: 19, `json_object_segments_key`: 11, `json_object_single_ad`: 181, `json_object_window_segments`: 2, `parse_failure`: 6, `regex_json_array`: 1
- Schema violations: 412
- Extra keys observed: end_text, sponsor

#### `mistralai/mistral-7b-instruct-v0.1`

- F1 (avg across episodes): **0.000**
- Total cost / episode: **$0.0000**
- p50 / p95 latency: 10.51s / 36.28s
- JSON compliance: 0.07
- Parse failure rate: 82.2%
- Extraction methods: `bracket_fallback`: 1, `parse_failure`: 370, `regex_json_array`: 79


### Per-Episode Detail

One subsection per episode in the corpus, showing how every model performed on that specific episode. For ad-bearing episodes you see F1 and the stdev across trials (low stdev means stable, high stdev means the model's number on this episode is noisy). For the no-ad episode you see PASS / FAIL on the negative control: PASS = zero false positives across all windows, FAIL = the model flagged something that wasn't an ad, with the count.

#### `ep-ai-cloud-essentials-e8dc897fbd6b`: How Physical AI is Streamlining Engineering

- Podcast: ai-cloud-essentials
- Duration: 16.4 min
- Truth: no-ads episode

| Model | Result | FP count |
|-------|--------|----------|
| `qwen/qwen3.5-plus-02-15` | PASS | 0 |
| `meta-llama/llama-3.3-70b-instruct` | PASS | 0 |
| `claude-sonnet-4-6` | PASS | 0 |
| `meta-llama/llama-3.1-8b-instruct` | PASS | 0 |
| `deepseek/deepseek-v3.2` | PASS | 0 |
| `mistralai/mistral-7b-instruct-v0.1` | PASS | 0 |
| `meta-llama/llama-4-scout` | PASS | 0 |
| `mistralai/mistral-large-2512` | PASS | 0 |
| `x-ai/grok-4.1-fast` | PASS | 0 |
| `claude-opus-4-7` | PASS | 0 |
| `mistralai/codestral-2508` | PASS | 0 |
| `openai/o4-mini` | PASS | 0 |
| `mistralai/mistral-medium-3.1` | PASS | 0 |
| `claude-haiku-4-5-20251001` | PASS | 0 |
| `nvidia/llama-3.3-nemotron-super-49b-v1.5` | PASS | 0 |
| `openai/o3` | PASS | 0 |
| `cohere/command-r-plus-08-2024` | PASS | 0 |
| `google/gemini-2.5-flash` | PASS | 0 |
| `google/gemini-2.5-pro` | FAIL | 1 |
| `meta-llama/llama-4-maverick` | FAIL | 1 |
| `deepseek/deepseek-v4-flash` | FAIL | 1 |
| `moonshotai/kimi-k2.6` | FAIL | 1 |
| `google/gemma-4-31b-it` | FAIL | 1 |
| `openai/gpt-5.4` | FAIL | 1 |
| `deepseek/deepseek-r1` | FAIL | 1 |
| `nvidia/nemotron-nano-9b-v2` | FAIL | 1 |
| `openai/gpt-5.5` | FAIL | 1 |
| `deepseek/deepseek-r1-distill-llama-70b` | FAIL | 2 |
| `microsoft/phi-4` | FAIL | 3 |
| `openai/gpt-3.5-turbo` | FAIL | 3 |
| `cohere/command-a` | FAIL | 3 |
| `deepseek/deepseek-r1-0528` | FAIL | 27 |

#### `ep-daily-tech-news-show-c1904b8605f7`: Switch 2 Prices Rise, Forecast Drops - DTNS 5265

- Podcast: daily-tech-news-show
- Duration: 38.6 min
- Truth ads: 5

| Model | F1 | F1 stdev |
|-------|----|----------|
| `moonshotai/kimi-k2.6` | 0.600 | 0.091 |
| `x-ai/grok-4.1-fast` | 0.578 | 0.030 |
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
| `nvidia/llama-3.3-nemotron-super-49b-v1.5` | 0.173 | 0.217 |
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
| `meta-llama/llama-3.3-70b-instruct` | 0.000 | 0.000 |
| `mistralai/mistral-7b-instruct-v0.1` | 0.000 | 0.000 |

#### `ep-glt1412515089-373d5ba5007b`: #2496 - Julia Mossbridge

- Podcast: glt1412515089
- Duration: 165.3 min
- Truth ads: 4

| Model | F1 | F1 stdev |
|-------|----|----------|
| `x-ai/grok-4.1-fast` | 0.716 | 0.072 |
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
| `qwen/qwen3.5-plus-02-15` | 0.667 | 0.000 |
| `google/gemini-2.5-pro` | 0.667 | 0.000 |
| `x-ai/grok-4.1-fast` | 0.667 | 0.000 |
| `claude-opus-4-7` | 0.667 | 0.000 |
| `openai/gpt-5.5` | 0.667 | 0.000 |
| `openai/gpt-5.4` | 0.613 | 0.119 |
| `google/gemma-4-31b-it` | 0.467 | 0.274 |
| `deepseek/deepseek-r1-0528` | 0.404 | 0.281 |
| `meta-llama/llama-3.1-8b-instruct` | 0.400 | 0.548 |
| `deepseek/deepseek-v3.2` | 0.400 | 0.548 |
| `cohere/command-a` | 0.400 | 0.000 |
| `deepseek/deepseek-v4-flash` | 0.337 | 0.239 |
| `openai/o3` | 0.333 | 0.471 |
| `deepseek/deepseek-r1` | 0.313 | 0.301 |
| `nvidia/llama-3.3-nemotron-super-49b-v1.5` | 0.233 | 0.325 |
| `moonshotai/kimi-k2.6` | 0.200 | 0.274 |
| `microsoft/phi-4` | 0.000 | 0.000 |
| `meta-llama/llama-4-maverick` | 0.000 | 0.000 |
| `meta-llama/llama-3.3-70b-instruct` | 0.000 | 0.000 |
| `claude-sonnet-4-6` | 0.000 | 0.000 |
| `mistralai/mistral-7b-instruct-v0.1` | 0.000 | 0.000 |
| `meta-llama/llama-4-scout` | 0.000 | 0.000 |
| `mistralai/mistral-large-2512` | 0.000 | 0.000 |
| `openai/gpt-3.5-turbo` | 0.000 | 0.000 |
| `mistralai/codestral-2508` | 0.000 | 0.000 |
| `openai/o4-mini` | 0.000 | 0.000 |
| `mistralai/mistral-medium-3.1` | 0.000 | 0.000 |
| `deepseek/deepseek-r1-distill-llama-70b` | 0.000 | 0.000 |
| `claude-haiku-4-5-20251001` | 0.000 | 0.000 |
| `nvidia/nemotron-nano-9b-v2` | 0.000 | 0.000 |
| `cohere/command-r-plus-08-2024` | 0.000 | 0.000 |
| `google/gemini-2.5-flash` | 0.000 | 0.000 |

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
| `x-ai/grok-4.1-fast` | 0.539 | 0.038 |
| `deepseek/deepseek-v3.2` | 0.528 | 0.073 |
| `claude-opus-4-7` | 0.520 | 0.055 |
| `meta-llama/llama-4-scout` | 0.520 | 0.050 |
| `claude-sonnet-4-6` | 0.516 | 0.014 |
| `meta-llama/llama-3.3-70b-instruct` | 0.512 | 0.035 |
| `openai/gpt-5.5` | 0.505 | 0.047 |
| `meta-llama/llama-4-maverick` | 0.496 | 0.021 |
| `google/gemma-4-31b-it` | 0.496 | 0.021 |
| `openai/gpt-5.4` | 0.495 | 0.022 |
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
| `qwen/qwen3.5-plus-02-15` | 0.771 | 0.048 |
| `x-ai/grok-4.1-fast` | 0.757 | 0.117 |
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
| `meta-llama/llama-3.1-8b-instruct` | 0.000 | 0.000 |
| `mistralai/mistral-7b-instruct-v0.1` | 0.000 | 0.000 |
| `cohere/command-r-plus-08-2024` | 0.000 | 0.000 |

#### `ep-the-tim-dillon-show-f62bd5fa1cfe`: 495 - Hantavirus Cruise & iPad Babies

- Podcast: the-tim-dillon-show
- Duration: 80.1 min
- Truth ads: 6

| Model | F1 | F1 stdev |
|-------|----|----------|
| `qwen/qwen3.5-plus-02-15` | 0.636 | 0.028 |
| `x-ai/grok-4.1-fast` | 0.594 | 0.066 |
| `claude-opus-4-7` | 0.592 | 0.052 |
| `openai/gpt-5.4` | 0.586 | 0.077 |
| `google/gemini-2.5-pro` | 0.569 | 0.063 |
| `openai/gpt-5.5` | 0.546 | 0.063 |
| `openai/o3` | 0.372 | 0.081 |
| `google/gemma-4-31b-it` | 0.350 | 0.072 |
| `deepseek/deepseek-r1` | 0.327 | 0.195 |
| `meta-llama/llama-3.3-70b-instruct` | 0.308 | 0.000 |
| `deepseek/deepseek-v4-flash` | 0.298 | 0.084 |
| `deepseek/deepseek-r1-0528` | 0.292 | 0.111 |
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
| `mistralai/mistral-7b-instruct-v0.1` | 0.000 | 0.000 |
| `openai/o4-mini` | 0.000 | 0.000 |


### Parser stress test

How each model's responses were actually parsed. Columns are extraction methods, ordered alphabetically; rows are models, sorted by parse-failure rate (cleanest at top). `json_array_direct` is the happy path: a bare JSON array we could `json.loads` and process immediately. `markdown_code_block` means we had to strip triple-backtick fences first; `json_object_*` means the model wrapped the array in an outer object and we had to find the array key; `regex_*` are last-resort recovery paths. A model that needs anything but `json_array_direct` for most calls is fragile. It works today, but a small prompt change can break the parser.

| Model | bracket_fallback | json_array_direct | json_object_ads_key | json_object_no_ads | json_object_segments_key | json_object_single_ad | json_object_single_ad_truncated | json_object_window_segments | markdown_code_block | parse_failure | regex_json_array |
|---|---|---|---|---|---|---|---|---|---|---|---|
| `qwen/qwen3.5-plus-02-15` | 0 | 450 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| `meta-llama/llama-4-maverick` | 0 | 0 | 0 | 93 | 0 | 357 | 0 | 0 | 0 | 0 | 0 |
| `claude-sonnet-4-6` | 0 | 413 | 0 | 0 | 0 | 0 | 0 | 0 | 27 | 0 | 10 |
| `deepseek/deepseek-v3.2` | 0 | 355 | 4 | 0 | 0 | 91 | 0 | 0 | 0 | 0 | 0 |
| `google/gemma-4-31b-it` | 0 | 0 | 278 | 72 | 0 | 99 | 1 | 0 | 0 | 0 | 0 |
| `mistralai/mistral-large-2512` | 0 | 450 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| `openai/gpt-3.5-turbo` | 0 | 0 | 0 | 4 | 0 | 446 | 0 | 0 | 0 | 0 | 0 |
| `claude-opus-4-7` | 0 | 448 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 2 |
| `mistralai/codestral-2508` | 0 | 450 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| `openai/gpt-5.4` | 0 | 0 | 0 | 134 | 0 | 316 | 0 | 0 | 0 | 0 | 0 |
| `mistralai/mistral-medium-3.1` | 0 | 450 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| `claude-haiku-4-5-20251001` | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 450 | 0 | 0 |
| `cohere/command-a` | 0 | 0 | 0 | 3 | 0 | 447 | 0 | 0 | 0 | 0 | 0 |
| `cohere/command-r-plus-08-2024` | 0 | 0 | 10 | 395 | 0 | 45 | 0 | 0 | 0 | 0 | 0 |
| `google/gemini-2.5-flash` | 0 | 450 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| `meta-llama/llama-3.3-70b-instruct` | 0 | 101 | 0 | 118 | 0 | 229 | 0 | 0 | 0 | 1 | 1 |
| `meta-llama/llama-3.1-8b-instruct` | 0 | 242 | 0 | 52 | 0 | 155 | 0 | 0 | 0 | 1 | 0 |
| `meta-llama/llama-4-scout` | 23 | 3 | 318 | 44 | 0 | 58 | 0 | 0 | 0 | 1 | 3 |
| `x-ai/grok-4.1-fast` | 0 | 0 | 0 | 259 | 0 | 190 | 0 | 0 | 0 | 1 | 0 |
| `openai/gpt-5.5` | 0 | 0 | 0 | 258 | 0 | 191 | 0 | 0 | 0 | 1 | 0 |
| `google/gemini-2.5-pro` | 0 | 429 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 2 | 19 |
| `deepseek/deepseek-r1-distill-llama-70b` | 0 | 14 | 15 | 32 | 0 | 385 | 0 | 0 | 0 | 4 | 0 |
| `openai/o3` | 0 | 0 | 12 | 341 | 6 | 87 | 0 | 0 | 0 | 4 | 0 |
| `microsoft/phi-4` | 0 | 209 | 21 | 19 | 11 | 181 | 0 | 2 | 0 | 6 | 1 |
| `deepseek/deepseek-r1` | 0 | 390 | 2 | 12 | 3 | 28 | 0 | 0 | 7 | 6 | 2 |
| `deepseek/deepseek-v4-flash` | 0 | 13 | 303 | 2 | 0 | 119 | 0 | 0 | 0 | 13 | 0 |
| `nvidia/nemotron-nano-9b-v2` | 0 | 394 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 46 | 10 |
| `deepseek/deepseek-r1-0528` | 0 | 354 | 23 | 3 | 0 | 21 | 0 | 0 | 1 | 48 | 0 |
| `nvidia/llama-3.3-nemotron-super-49b-v1.5` | 0 | 242 | 0 | 0 | 0 | 0 | 0 | 0 | 127 | 70 | 11 |
| `moonshotai/kimi-k2.6` | 0 | 31 | 14 | 53 | 0 | 226 | 0 | 0 | 0 | 126 | 0 |
| `mistralai/mistral-7b-instruct-v0.1` | 1 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 370 | 79 |
| `openai/o4-mini` | 0 | 0 | 0 | 8 | 0 | 19 | 0 | 0 | 0 | 423 | 0 |

### Methodology

Reproducibility settings used for this run. The benchmark sends the same prompts MinusPod sends in production (same system prompt, same sponsor list, same windowing) so the F1 numbers here are directly relevant to production accuracy decisions. Cost is recomputed at report time from token counts against the active pricing snapshot, so all rows compare at the same prices regardless of when the actual call ran.

- Trials per (model, episode): **5**, temperature 0.0
- max_tokens: 4096 (matches MinusPod production)
- response_format: json_object (with prompt-injection fallback when provider rejects native)
- Window size: 10 min, overlap: 3 min (imported from MinusPod's create_windows)
- Pricing snapshot: 2026-05-09T22:50:45.889333Z
- Corpus episodes: 7

### Transcript source

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

**Sponsor vocabulary** (254 canonical sponsors, 43 of them with explicit alias spellings totaling 46 aliases; from `src/utils/constants.py` `SEED_SPONSORS`):

| Sponsor | Aliases | Category |
|---|---|---|
| 1Password | `One Password` | tech |
| Acorns | - | finance |
| ADT | - | home |
| Affirm | - | finance_fintech |
| Airbnb | - | travel_hospitality |
| Airtable | - | tech_software_saas |
| Alani Nu | - | food_beverage_nutrition |
| Allbirds | - | ecommerce_retail_dtc |
| Alo Yoga | - | ecommerce_retail_dtc |
| Amazon | - | retail |
| Anthropic | - | tech_software_saas |
| Apple TV+ | - | media_streaming |
| Asana | - | tech_software_saas |
| AT&T | - | telecom |
| Athletic Brewing | - | beverage |
| Athletic Greens | `AG1`, `AG One` | health |
| Audible | - | entertainment |
| Aura | - | tech |
| Babbel | - | education |
| BetMGM | `Bet MGM` | gambling |
| BetterHelp | `Better Help` | health |
| Betterment | - | finance |
| Bill.com | - | finance_fintech |
| Birchbox | - | ecommerce_retail_dtc |
| Bitwarden | `Bit Warden` | tech |
| Blinkist | - | education |
| Bloom Nutrition | - | food_beverage_nutrition |
| Blue Apron | - | food |
| Bombas | - | apparel |
| Booking.com | - | travel_hospitality |
| Bose | - | electronics |
| Brex | - | finance_fintech |
| Brilliant | - | tech_software_saas |
| Brooklinen | - | home |
| Butcher Box | `ButcherBox` | food |
| CacheFly | - | tech |
| Caesars Sportsbook | - | gaming_sports_betting |
| Calm | - | health |
| Canva | - | tech |
| Capital One | - | finance |
| Care/of | `Care of`, `Careof` | health |
| CarMax | `Car Max` | auto |
| Carvana | - | auto |
| Casper | - | home |
| Cerebral | - | mental_health_wellness |
| Chime | - | finance_fintech |
| ClickUp | - | tech_software_saas |
| Cloudflare | - | tech_software_saas |
| Coinbase | - | finance_fintech |
| Comcast | - | telecom |
| Cozy Earth | - | home |
| Credit Karma | - | finance |
| CrowdStrike | - | tech_software_saas |
| Cursor | - | tech_software_saas |
| Databricks | - | tech_software_saas |
| Datadog | - | tech_software_saas |
| Deel | - | business |
| DeleteMe | `Delete Me` | tech |
| Disney+ | - | media_streaming |
| DocuSign | - | tech_software_saas |
| Dollar Shave Club | `DSC` | personal |
| DoorDash | `Door Dash` | food |
| DraftKings | `Draft Kings` | gambling |
| Duolingo | - | tech_software_saas |
| eBay Motors | - | auto |
| Eight Sleep | - | mental_health_wellness |
| ElevenLabs | - | tech_software_saas |
| ESPN Bet | - | gaming_sports_betting |
| Everlane | - | ecommerce_retail_dtc |
| EveryPlate | - | food_beverage_nutrition |
| Expedia | - | travel_hospitality |
| ExpressVPN | `Express VPN` | vpn |
| FabFitFun | - | ecommerce_retail_dtc |
| Factor | - | food |
| FanDuel | `Fan Duel` | gambling |
| Figma | - | tech_software_saas |
| Ford | - | auto |
| Framer | - | tech |
| FreshBooks | - | finance_fintech |
| Function Health | - | mental_health_wellness |
| Function of Beauty | - | personal |
| Gametime | `Game Time` | entertainment |
| Geico | - | finance |
| GitHub | - | tech_software_saas |
| GitHub Copilot | - | tech_software_saas |
| GOAT | - | ecommerce_retail_dtc |
| GoodRx | `Good Rx` | health |
| Gopuff | - | ecommerce_retail_dtc |
| Grammarly | - | tech |
| Green Chef | `GreenChef` | food |
| Grubhub | `Grub Hub` | food |
| Gusto | - | b2b_startup |
| Harry's | `Harrys` | personal |
| HBO Max | - | media_streaming |
| Headspace | `Head Space` | health |
| Helix Sleep | `Helix` | home |
| HelloFresh | `Hello Fresh` | food |
| Hers | - | health |
| Hims | - | health |
| Honeylove | `Honey Love` | apparel |
| Hopper | - | travel_hospitality |
| HubSpot | `Hub Spot` | tech |
| Huel | - | food_beverage_nutrition |
| Hyundai | - | auto |
| iHeartRadio | - | media_streaming |
| Imperfect Foods | - | food_beverage_nutrition |
| Incogni | - | tech |
| Indeed | - | jobs |
| Inside Tracker | - | mental_health_wellness |
| Instacart | - | food |
| Intuit | - | finance_fintech |
| Joovv | - | mental_health_wellness |
| Kayak | - | travel_hospitality |
| Keeps | - | health |
| Klarna | - | finance_fintech |
| Klaviyo | - | tech_software_saas |
| LegalZoom | - | insurance_legal |
| Lemonade | - | finance |
| Levels | - | mental_health_wellness |
| Liberty Mutual | - | finance |
| Lime | - | automotive_transport |
| Linear | - | tech_software_saas |
| LinkedIn | `LinkedIn Jobs` | jobs |
| Liquid IV | `Liquid I.V.` | health |
| LMNT | `Element` | health |
| Loom | - | tech_software_saas |
| Lululemon | - | ecommerce_retail_dtc |
| Lyft | - | automotive_transport |
| MacPaw | `CleanMyMac` | tech |
| Magic Mind | - | beverage |
| Magic Spoon | - | food |
| Mailchimp | - | tech_software_saas |
| Manscaped | - | personal |
| MasterClass | `Master Class` | education |
| McDonald's | - | food_beverage_nutrition |
| Mercury | - | finance_fintech |
| Meter | - | b2b_startup |
| Midjourney | - | tech_software_saas |
| Mint Mobile | `MintMobile` | telecom |
| Miro | - | tech |
| Momentous | - | mental_health_wellness |
| Monarch Money | - | finance |
| Monday.com | `Monday` | tech |
| Native | - | personal |
| NerdWallet | - | finance_fintech |
| Netflix | - | media_streaming |
| NetSuite | `Net Suite` | tech |
| Noom | - | mental_health_wellness |
| NordVPN | `Nord VPN` | vpn |
| Notion | - | tech |
| Nutrafol | - | health |
| Okta | - | tech_software_saas |
| OLIPOP | - | food_beverage_nutrition |
| OneSkin | `One Skin` | personal |
| OpenAI | - | tech_software_saas |
| Outdoor Voices | - | ecommerce_retail_dtc |
| OutSystems | - | tech |
| PagerDuty | - | b2b_startup |
| Paramount+ | - | media_streaming |
| Patreon | - | tech_software_saas |
| Perplexity | - | tech_software_saas |
| Plaid | - | finance_fintech |
| PolicyGenius | `Policy Genius` | finance |
| Poppi | - | food_beverage_nutrition |
| Poshmark | - | ecommerce_retail_dtc |
| Progressive | - | finance |
| Public.com | - | finance_fintech |
| Pura | - | home_security |
| Purple | - | home |
| QuickBooks | - | finance_fintech |
| Quince | - | apparel |
| Quip | - | personal |
| Ramp | - | finance_fintech |
| Raycon | - | electronics |
| Retool | - | tech_software_saas |
| Ring | - | home |
| Rippling | - | b2b_startup |
| Ritual | - | health |
| Ro | - | mental_health_wellness |
| Robinhood | - | finance_fintech |
| Rocket Lawyer | - | insurance_legal |
| Rocket Money | `RocketMoney`, `Truebill` | finance |
| Roman | - | health |
| Rosetta Stone | - | education |
| Rothy's | - | ecommerce_retail_dtc |
| Saatva | - | ecommerce_retail_dtc |
| Salesforce | - | tech_software_saas |
| SeatGeek | - | gaming_sports_betting |
| Seed | - | health |
| SendGrid | - | tech_software_saas |
| ServiceNow | - | tech_software_saas |
| Shein | - | ecommerce_retail_dtc |
| Shopify | - | tech |
| SimpliSafe | `Simpli Safe` | home |
| SiriusXM | - | media_streaming |
| Skillshare | - | tech_software_saas |
| SKIMS | - | ecommerce_retail_dtc |
| Skyscanner | - | travel_hospitality |
| Slack | - | tech_software_saas |
| Snowflake | - | tech_software_saas |
| SoFi | - | finance |
| Spaceship | - | tech |
| Splunk | - | b2b_startup |
| Spotify | - | media_streaming |
| Squarespace | `Square Space` | tech |
| Stamps.com | `Stamps` | business |
| Starbucks | - | food_beverage_nutrition |
| State Farm | - | finance |
| Stitch Fix | - | ecommerce_retail_dtc |
| StockX | - | ecommerce_retail_dtc |
| Stripe | - | finance_fintech |
| StubHub | - | gaming_sports_betting |
| Substack | - | tech_software_saas |
| T-Mobile | `TMobile` | telecom |
| Talkspace | - | mental_health_wellness |
| Temu | - | ecommerce_retail_dtc |
| Ten Thousand | - | ecommerce_retail_dtc |
| Thinkst Canary | - | tech |
| Thorne | - | mental_health_wellness |
| ThreatLocker | - | tech |
| ThredUp | - | ecommerce_retail_dtc |
| Thrive Market | - | food |
| Toyota | - | auto |
| Transparent Labs | - | food_beverage_nutrition |
| Turo | - | automotive_transport |
| Twilio | - | tech_software_saas |
| Uber | - | automotive_transport |
| Uber Eats | `UberEats` | food |
| UnitedHealth Group | - | finance_fintech |
| Vanta | - | tech |
| Veeam | - | tech |
| Vercel | - | tech_software_saas |
| Verizon | - | telecom |
| Visible | - | telecom |
| Vrbo | - | travel_hospitality |
| Vuori | - | ecommerce_retail_dtc |
| Warby Parker | - | ecommerce_retail_dtc |
| Wayfair | - | ecommerce_retail_dtc |
| Waymo | - | automotive_transport |
| Wealthfront | - | finance |
| WebBank | - | finance_fintech |
| Webflow | - | b2b_startup |
| WhatsApp | - | tech |
| WHOOP | - | mental_health_wellness |
| Workday | - | tech_software_saas |
| Xero | - | finance_fintech |
| YouTube | - | media_streaming |
| YouTube TV | - | media_streaming |
| Zapier | - | tech |
| Zendesk | - | tech_software_saas |
| ZipRecruiter | `Zip Recruiter` | jobs |
| ZocDoc | `Zoc Doc` | health |
| Zoom | - | tech_software_saas |
| Zscaler | - | tech |

**Mishearing corrections** (174 entries, from `src/utils/constants.py` `SPONSOR_ALIASES`). Applied post-transcription to normalize Whisper output toward the canonical sponsor name. Distinct from the `aliases` column above, which lists intentional alternative spellings (e.g. `AG1` vs `Athletic Greens`); the entries below are mostly Whisper mishearings (e.g. `a firm` -> `Affirm`, `xerox` -> `Xero`). Laid out in three side-by-side pairs, read top-to-bottom in each column.

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

### Run Metadata

- Report generated: 2026-05-11T16:43:25Z
- Unique work units (current state, last-write-wins after retries): 14400
- Raw rows in calls.jsonl: 14416 (16 superseded by later retries; kept for audit)
- Successful: 14400
- Failed: 0
- Lifetime actual spend (sum of at-runtime costs, includes superseded rows): $123.6996
- Active pricing snapshot: 2026-05-09T22:50:45.889333Z
