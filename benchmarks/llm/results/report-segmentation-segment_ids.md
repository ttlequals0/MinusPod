# MinusPod LLM Benchmark Report (prompt variant: segmentation, addressing mode: segment_ids)

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
- [Cost breakdown (input vs output)](#cost-breakdown-input-vs-output)
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
| **F1 (accuracy)** | 0 to 1 | higher is better | Combined score of precision and recall against the human-verified ground-truth ad spans. F1 = 0 means the model found nothing right; F1 = 1 means it found every ad with the correct boundaries. Uses IoU >= 0.5 (predicted span must overlap truth span by at least half) to count a match, after both sides are canonicalized to per-break spans. |
| **Cost / episode** | USD | lower is better | Average dollars per episode at the current pricing snapshot. Recomputed from token counts so all rows compare at the same prices regardless of when the call ran. |
| **F1 / $** | ratio | higher is better | F1 divided by cost-per-episode. Cheap accurate models score highest. Free-tier models (when the roster has any) are rank-listed separately because the ratio is undefined. |
| **p50 / p95 latency** | seconds | lower is better, with caveats | Median (p50) and tail (p95) wall-clock response time. **Note**: for models routed through OpenRouter (everything except `claude-*`), this includes OpenRouter's queueing and upstream-provider latency, not just the model itself. Treat as a load/availability indicator, not a model-quality signal. |
| **JSON compliance** | 0 to 1 | higher is better | Fraction of responses that parsed as a clean JSON array matching the requested schema. 1.0 = always clean; lower = used object wrappers (`{ads: [...]}`), markdown fences, extra fields like `sponsor`, or required regex fallback to extract. |
| **No-ad episode** | PASS / FAIL | PASS desired | Negative-control test on the 2 episode(s) verified to contain no ads (`ep-ai-cloud-essentials-e8dc897fbd6b`, `ep-oxide-and-friends-ce789ff5b62e`). PASS = zero predictions across all 16 of their windows. FAIL = the model false-positived on a non-ad segment, with the FP count shown. |
| **F1 stdev** | 0 to 1 | lower means more consistent | Standard deviation of F1 across the 13 ad-bearing episodes. High stdev = inconsistent across content types. |
| **Moderation blocked** | 0 to 100% | 0 desired | Share of attempted calls the provider refused on content grounds. Refused windows never reach scoring, so any non-zero value means that model's F1 was computed on a subset of the corpus and is not comparable to an unblocked row. |
| **JSON mode** | `native` / `prompt-inject` / `mixed` | -- | How the model received its JSON-output instruction. `native` = provider accepted `response_format=json_object` for at least 95% of calls; `prompt-inject` = provider rejected it and the runner fell back to instructing JSON in the prompt for at least 95% of calls; `mixed` = neither path crossed the threshold (sample mostly comes from intermittent provider rejections). Reads from `json_format_used` in `calls.jsonl`. Useful when picking a model whose provider may not support native JSON mode -- a strong `JSON compliance` score from a `prompt-inject` model carries different weight than the same score from a `native` model. |

### Glossary

- **IoU (intersection over union)**: how much two time ranges overlap, expressed as `(overlap) / (union)`. 0 means no overlap, 1 means identical ranges. We use IoU >= 0.5 as the threshold for a predicted ad to count as matching a truth ad.
- **Per-break canonicalization**: before matching, predicted and truth spans separated by gaps under 15 seconds are merged, so one span means one contiguous ad break. This mirrors the detection prompt's own merge rule; a model that reports one break as several adjacent spots is not penalized for the split.
- **Trial**: each (model, episode) pair runs 5 trials at temperature 0.0 to surface non-determinism. F1 numbers in tables are averaged across trials.
- **Window**: each episode is split into ~85-second sliding windows; the model judges each window independently. Per-window predictions are stitched together for episode-level scoring.
- **Schema violations**: number of times the response had at least one missing-required-field, wrong-type, or extra-key issue. Doesn't tank F1, but signals brittleness.
- **Extraction method**: the route the parser took to recover the ad list. `json_array_direct` is the cleanest; method names with `regex_*` mean the JSON itself was malformed and we fell back to text matching.


## TL;DR

### Best Accuracy (F0.5 @ IoU >= 0.5)

Models ranked by F0.5 (precision weighted 2x recall) against human-verified ground truth. MinusPod cuts the segments it flags, so cutting real content (a false positive) is worse than leaving an ad in (a false negative), and F0.5 penalizes it more. A model shares the tier above it unless it scores consistently lower across the same episodes (paired one-sided t-test, 95%); models that trade wins episode to episode share a tier, so order within a tier is not meaningful on this 13-episode corpus. Flags caveat a model without changing its rank. Cost includes free-tier models (shown at $0.00).

| Tier | Model | F0.5 | 95% CI | Precision | Recall | F1 | Cost / episode | p50 latency | JSON compliance | Flags |
|------|-------|------|--------|-----------|--------|----|----------------|-------------|-----------------|-------|
| A | `google/gemini-3.5-flash` | 0.835 | +/-0.115 | 0.820 | 0.938 | 0.864 | $3.6825 | 8.1s | 1.00 | (!) fails no-ad control |
| A | `qwen/qwen3.5-plus-02-15` | 0.827 | +/-0.086 | 0.804 | 0.958 | 0.868 | $2.2988 | 121.1s | 0.98 | (!) fails no-ad control |
| A | `claude-opus-5` | 0.822 | +/-0.107 | 0.807 | 0.942 | 0.852 | $3.8067 | 4.2s | 1.00 | (!) fails no-ad control |
| A | `qwen/qwen3.7-flash` | 0.790 | +/-0.094 | 0.772 | 0.904 | 0.824 | $0.1181 | 37.4s | 1.00 | (!) fails no-ad control |
| A | `x-ai/grok-4.3` | 0.790 | +/-0.096 | 0.772 | 0.900 | 0.823 | $1.1875 | 9.8s | 1.00 |  |
| B | `claude-opus-5-5` | 0.789 | +/-0.113 | 0.772 | 0.915 | 0.824 | $3.0395 | 3.2s | 1.00 | (!) fails no-ad control |
| B | `google/gemini-3.5-flash-lite` | 0.789 | +/-0.093 | 0.767 | 0.935 | 0.829 | $0.2866 | 1.1s | 1.00 | (!) fails no-ad control |
| B | `claude-opus-4-8` | 0.788 | +/-0.114 | 0.768 | 0.932 | 0.827 | $3.8987 | 4.6s | 1.00 | (!) fails no-ad control |
| B | `claude-haiku-4-5-20251001` | 0.787 | +/-0.100 | 0.763 | 0.930 | 0.829 | $0.8158 | 45.8s | 1.00 | (!) fails no-ad control |
| B | `google/gemini-3.1-flash-lite` | 0.776 | +/-0.102 | 0.749 | 0.944 | 0.824 | $0.2260 | 1.5s | 1.00 |  |
| B | `qwen/qwen3.8-flash` | 0.759 | +/-0.100 | 0.739 | 0.883 | 0.795 | $0.7437 | 108.7s | 0.91 | (!) fails no-ad control |
| B | `claude-sonnet-5` | 0.754 | +/-0.112 | 0.740 | 0.877 | 0.785 | $1.5348 | 18.3s | 1.00 | (!) fails no-ad control |
| B | `mistralai/mistral-small-2603` | 0.741 | +/-0.109 | 0.730 | 0.850 | 0.767 | $0.1314 | 3.0s | 1.00 |  |
| B | `deepseek/deepseek-v4-flash` | 0.732 | +/-0.125 | 0.780 | 0.735 | 0.715 | $0.7204 | 31.9s | 0.76 | (!) brittle JSON |
| C | `openai/gpt-oss-120b` | 0.691 | +/-0.096 | 0.670 | 0.825 | 0.729 | $0.0588 | 6.9s | 1.00 | (!) fails no-ad control |
| C | `gemma4:e4b` | 0.682 | +/-0.225 | 0.659 | 0.817 | 0.723 | $0.0000 | 13.3s | 1.00 |  |
| C | `microsoft/phi-4` | 0.658 | +/-0.115 | 0.632 | 0.849 | 0.710 | $0.0525 | 3.4s | 1.00 |  |
| C | `bytedance-seed/seed-2-1-turbo` | 0.553 | +/-0.170 | 0.661 | 0.389 | 0.467 | $4.4551 | 145.8s | 0.61 | (!) brittle JSON |

### Best Value (F0.5 per dollar)

Paid-tier only, ranked by F0.5 per dollar. Free-tier models are excluded here because F0.5 / 0 is undefined; they are ranked separately under Best Free-Tier below. No confidence tiers on this table, since a point ratio does not group cleanly, but the reliability flags still apply.

| Rank | Model | F0.5/$ | F0.5 | F1 | Cost / episode | Flags |
|------|-------|--------|------|----|----------------|-------|
| 1 | `microsoft/phi-4` | 12.53 | 0.658 | 0.710 | $0.0525 |  |
| 2 | `openai/gpt-oss-120b` | 11.75 | 0.691 | 0.729 | $0.0588 | (!) fails no-ad control |
| 3 | `qwen/qwen3.7-flash` | 6.69 | 0.790 | 0.824 | $0.1181 | (!) fails no-ad control |
| 4 | `mistralai/mistral-small-2603` | 5.64 | 0.741 | 0.767 | $0.1314 |  |
| 5 | `google/gemini-3.1-flash-lite` | 3.43 | 0.776 | 0.824 | $0.2260 |  |
| 6 | `google/gemini-3.5-flash-lite` | 2.75 | 0.789 | 0.829 | $0.2866 | (!) fails no-ad control |
| 7 | `qwen/qwen3.8-flash` | 1.02 | 0.759 | 0.795 | $0.7437 | (!) fails no-ad control |
| 8 | `deepseek/deepseek-v4-flash` | 1.02 | 0.732 | 0.715 | $0.7204 | (!) brittle JSON |
| 9 | `claude-haiku-4-5-20251001` | 0.96 | 0.787 | 0.829 | $0.8158 | (!) fails no-ad control |
| 10 | `x-ai/grok-4.3` | 0.67 | 0.790 | 0.823 | $1.1875 |  |
| 11 | `claude-sonnet-5` | 0.49 | 0.754 | 0.785 | $1.5348 | (!) fails no-ad control |
| 12 | `qwen/qwen3.5-plus-02-15` | 0.36 | 0.827 | 0.868 | $2.2988 | (!) fails no-ad control |
| 13 | `claude-opus-5-5` | 0.26 | 0.789 | 0.824 | $3.0395 | (!) fails no-ad control |
| 14 | `google/gemini-3.5-flash` | 0.23 | 0.835 | 0.864 | $3.6825 | (!) fails no-ad control |
| 15 | `claude-opus-5` | 0.22 | 0.822 | 0.852 | $3.8067 | (!) fails no-ad control |
| 16 | `claude-opus-4-8` | 0.20 | 0.788 | 0.827 | $3.8987 | (!) fails no-ad control |
| 17 | `bytedance-seed/seed-2-1-turbo` | 0.12 | 0.553 | 0.467 | $4.4551 | (!) brittle JSON |

### Best Free-Tier (F0.5)

Models that came back at $0.00 cost, ranked by F0.5 with the same CI and flags as Best Accuracy. Tiers are computed within the free-tier set against its own leader, so a tier letter here is not comparable to the same letter in Best Accuracy. Free-tier eligibility on OpenRouter depends on the attribution headers wired into the benchmark (`HTTP-Referer`, `X-Title`); a model showing as free here may bill on your own deployment if those headers are missing.

| Tier | Model | F0.5 | 95% CI | Precision | Recall | F1 | p50 latency | JSON compliance | Flags |
|------|-------|------|--------|-----------|--------|----|-------------|-----------------|-------|
| A | `gemma4:e4b` | 0.682 | +/-0.225 | 0.659 | 0.817 | 0.723 | 13.3s | 1.00 |  |

## Charts

### Cost vs F1 (Pareto)

Each model is one colored point. Lower-left is unhelpful (expensive, inaccurate). Upper-left is the sweet spot (accurate, cheap). The legend below the chart shows each model's color next to its F1 and cost-per-episode.

![Cost vs F1 by model](report_assets-segmentation-segment_ids/pareto.svg)

Source data: [Best Accuracy](#best-accuracy-f05--iou--05), [Best Value](#best-value-f05-per-dollar), [Best Free-Tier](#best-free-tier-f05)

### Accuracy vs latency

F0.5 (y) against p50 latency (x, log scale). The cost Pareto above answers what accuracy costs in dollars; this one answers what it costs in wall-clock time. Upper-left is accurate and fast. MinusPod's pipeline is offline, so a slow accurate model is usable, but the chart shows which models make you choose and which don't. The OpenRouter latency caveat from the Metric Key applies.

![Accuracy vs latency by model](report_assets-segmentation-segment_ids/accuracy_latency.svg)

Source data: [Best Accuracy](#best-accuracy-f05--iou--05) (F0.5), [Latency tail](#latency-tail) (p50)

### JSON schema compliance

Fraction of each model's responses that parsed as a clean JSON array. 1.0 means every response came back exactly as requested; lower numbers mean the parser had to recover from markdown fences, object wrappers, or extra fields.

![JSON compliance per model](report_assets-segmentation-segment_ids/compliance.svg)

Source data: [Per-Model Detail](#per-model-detail) (`JSON compliance` field)

### F1 by episode (heatmap)

F1 score for each (model, episode) pair. Greener is more accurate, redder is less. The no-ad episodes are excluded. They have no F1 because they're PASS/FAIL negative controls.

![F1 score per model and episode](report_assets-segmentation-segment_ids/episodes.svg)

Source data: [Quick Comparison](#quick-comparison), [Per-Episode Detail](#per-episode-detail)

### Confidence calibration (heatmap)

One row per model, one column per self-reported confidence bin. Cell text is the actual hit rate at that bin plus the sample size; cell color is the calibration error (actual minus bin midpoint). Red cells mean the model claimed high confidence but was usually wrong; green is well-calibrated; blue is underconfident. Empty cells mean the model never produced a prediction in that bin. Models are sorted from most overconfident at the top to most underconfident at the bottom.

![Confidence calibration per model](report_assets-segmentation-segment_ids/calibration.svg)

Source data: [Confidence calibration](#confidence-calibration) table

### Latency percentiles

p50, p90, p99, and max per model on a log scale. The gap between p99 and max indicates how heavy the tail is. For OpenRouter-routed models, the tail also includes upstream provider load.

![Latency percentiles per model](report_assets-segmentation-segment_ids/latency_tail.svg)

Source data: [Latency tail](#latency-tail) table

### Cross-model agreement (window distribution)

Histogram of how many models flagged at least one ad per (episode, window). The left side is windows nobody flagged (clear non-ad content), the right side is windows everyone flagged (clear sponsor reads). Bars in the middle are contested (some models said yes, some said no) and are candidates for ensemble voting or manual review. This view is anonymous (bars don't show which models contributed); the per-model breakdown is in the next chart.

![Cross-model agreement histogram](report_assets-segmentation-segment_ids/agreement.svg)

Source data: [Cross-model agreement](#cross-model-agreement) table

### Per-model alignment with majority

Stacked horizontal bar per model. Green + blue segments are windows where the model voted with the majority (true positives + true negatives); orange is windows where it voted yes but most others voted no (likely false positive / hallucination); red is windows where it voted no but most others voted yes (likely missed real ad). Right-edge label is alignment rate. High alignment means the model tracks consensus; low alignment is either insight or noise depending on whether those broken-from-consensus calls were right.

![Per-model alignment with majority](report_assets-segmentation-segment_ids/alignment.svg)

Source data: [Per-model alignment with consensus](#per-model-alignment-with-consensus) table

### Precision vs Recall (with F1 isocurves)

Scatter of precision (y) vs recall (x) for each model. Dashed gray lines are F1 isocurves; points on the same dashed line have the same F1. Top-right is ideal (high precision AND high recall). Top-left is cautious (high precision, low recall). Bottom-right is greedy (high recall, low precision). Useful for picking a model whose error profile matches your tolerance: precision-leaning for environments where false positives are expensive, recall-leaning for completeness-first.

![Precision vs recall scatter](report_assets-segmentation-segment_ids/precision_recall.svg)

Source data: [Precision, recall, and FP/FN breakdown](#precision-recall-and-fpfn-breakdown) table

### Boundary accuracy (start + end MAE)

Stacked horizontal bars per model: blue is mean absolute error on the predicted ad START in seconds, orange is the same for END. Total error labeled at the right. Sorted by total ascending so the cleanest boundaries are at the top. Skewed bars (start much larger than end, or vice versa) mean the model systematically overshoots on one side. Relevant if you cut audio downstream.

![Boundary MAE per model](report_assets-segmentation-segment_ids/boundary.svg)

Source data: [Boundary accuracy](#boundary-accuracy) table

### Token efficiency vs F1

Scatter of output tokens per detected ad (x, log scale) vs F1 (y). Upper-left is the efficient zone: high accuracy with few output tokens. Right-side points are reasoning-heavy models that emit chain-of-thought alongside their JSON. The chart answers whether the extra tokens buy more F1 or just burn output budget. A model that lands far right at modest F1 is paying for reasoning that didn't help.

![Token efficiency vs F1](report_assets-segmentation-segment_ids/token_efficiency.svg)

Source data: [Output token efficiency](#output-token-efficiency) table

### Cost split (input vs output)

Stacked horizontal bars per model: blue is the input share of per-episode cost, orange is the output share, total labeled at the right, sorted by total ascending. Every model reads the same transcripts, so a long blue bar is an expensive input price and a long orange bar is a talkative model. Reasoning models show up as mostly orange.

![Cost split per model](report_assets-segmentation-segment_ids/cost_split.svg)

Source data: [Cost breakdown (input vs output)](#cost-breakdown-input-vs-output) table

### Trial variance (determinism check)

Horizontal bars of mean F1 stdev across episodes per model. All trials run at temperature 0.0 so well-behaved models cluster near zero. Bars are color-graded: green below 0.02 (effectively deterministic), yellow 0.02-0.05 (slight noise), red above 0.05 (single-trial F1 numbers from this model should be treated with suspicion). Dotted reference lines mark the 0.02 and 0.05 thresholds.

![Trial F1 variance per model](report_assets-segmentation-segment_ids/trial_variance.svg)

Source data: [Trial variance (determinism check)](#trial-variance-determinism-check) table

### Detection rate by ad length

Heatmap of model (row) vs ad-length bucket (column), cell = detection rate with sample size. Greener = caught more ads in that bucket; redder = missed more. Models are sorted by overall detection rate so the strongest are at the top. Empty (gray) cells mean that bucket had no truth ads for the corresponding model's trials.

![Detection rate by ad length](report_assets-segmentation-segment_ids/detection_by_length.svg)

Source data: [Detection rate by ad characteristic > By ad length](#by-ad-length) table

### Detection rate by ad position

Same shape as the ad-length heatmap, but columns are episode position (pre-roll / mid-roll / post-roll). A common pattern: pre-roll is easy because of clear show-intro transitions; post-roll is harder because models near the end of long episodes often produce shorter responses or run out of context to anchor on.

![Detection rate by ad position](report_assets-segmentation-segment_ids/detection_by_position.svg)

Source data: [Detection rate by ad characteristic > By ad position](#by-ad-position) table

### Parser stress (extraction-method usage)

Heatmap of model (row) vs extraction-method (column), cell = number of responses parsed via that method. Columns are ordered by total usage. `json_array_direct` is the clean path; everything else is a recovery path the parser had to take because the model added markdown fences, wrapped the array in an object, or returned malformed JSON. Models near the top of the chart use the clean path most often. They are operationally easier to consume.

![Parser stress heatmap](report_assets-segmentation-segment_ids/parser_stress.svg)

Source data: [Parser stress test](#parser-stress-test) table


## Failures and provider issues

No unresolved call errors across this run. Every (model, episode, trial, window) tuple ended with a parseable response.


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
| `qwen/qwen3.5-plus-02-15` | 0.804 | 0.958 | 256 | 73 | 14 |
| `google/gemini-3.5-flash` | 0.820 | 0.938 | 257 | 61 | 13 |
| `claude-opus-5` | 0.807 | 0.942 | 253 | 74 | 17 |
| `claude-haiku-4-5-20251001` | 0.763 | 0.930 | 252 | 82 | 18 |
| `google/gemini-3.5-flash-lite` | 0.767 | 0.935 | 252 | 82 | 18 |
| `claude-opus-4-8` | 0.768 | 0.932 | 253 | 94 | 17 |
| `google/gemini-3.1-flash-lite` | 0.749 | 0.944 | 255 | 85 | 15 |
| `qwen/qwen3.7-flash` | 0.772 | 0.904 | 248 | 84 | 22 |
| `claude-opus-5-5` | 0.772 | 0.915 | 247 | 80 | 23 |
| `x-ai/grok-4.3` | 0.772 | 0.900 | 245 | 71 | 25 |
| `qwen/qwen3.8-flash` | 0.739 | 0.883 | 235 | 106 | 35 |
| `claude-sonnet-5` | 0.740 | 0.877 | 237 | 99 | 33 |
| `mistralai/mistral-small-2603` | 0.730 | 0.850 | 233 | 102 | 37 |
| `openai/gpt-oss-120b` | 0.670 | 0.825 | 222 | 127 | 48 |
| `gemma4:e4b` | 0.659 | 0.817 | 105 | 60 | 20 |
| `deepseek/deepseek-v4-flash` | 0.780 | 0.735 | 188 | 62 | 82 |
| `microsoft/phi-4` | 0.632 | 0.849 | 220 | 176 | 50 |
| `bytedance-seed/seed-2-1-turbo` | 0.661 | 0.389 | 102 | 24 | 168 |

## Boundary accuracy

For ads that match the truth at IoU >= 0.5, how far off were the predicted start and end timestamps? Lower is better. A model can hit F1 cleanly while still being 20s off on every boundary. Bad for any pipeline that cuts the audio.

MAE is size of the miss; bias is its direction (mean of predicted minus truth). A negative start bias or positive end bias means the cut extends past the ad and eats surrounding content; the opposite signs mean ad audio is left in. MinusPod cuts what the model flags, so a model whose bias points outward over-cuts even when its MAE looks acceptable. Bias near zero with a large MAE means the misses are random rather than systematic.

| Model | Start MAE (s) | End MAE (s) | Start bias (s) | End bias (s) |
|---|---:|---:|---:|---:|
| `qwen/qwen3.5-plus-02-15` | 3.16 | 1.76 | +0.51 | -1.27 |
| `claude-opus-5` | 4.81 | 1.93 | -0.97 | +0.20 |
| `claude-sonnet-5` | 3.99 | 3.08 | +0.63 | -2.26 |
| `claude-opus-5-5` | 4.77 | 2.93 | +1.08 | -1.18 |
| `google/gemini-3.1-flash-lite` | 5.36 | 2.54 | -0.26 | +0.12 |
| `deepseek/deepseek-v4-flash` | 6.24 | 1.74 | +0.25 | -0.02 |
| `google/gemini-3.5-flash` | 4.96 | 3.27 | -0.76 | -2.78 |
| `x-ai/grok-4.3` | 4.89 | 3.41 | -0.05 | -2.40 |
| `claude-opus-4-8` | 5.41 | 3.22 | +0.02 | -1.14 |
| `claude-haiku-4-5-20251001` | 4.72 | 4.06 | -0.11 | -3.27 |
| `bytedance-seed/seed-2-1-turbo` | 7.53 | 2.06 | -0.24 | -1.94 |
| `google/gemini-3.5-flash-lite` | 6.05 | 3.78 | -1.34 | -2.25 |
| `qwen/qwen3.8-flash` | 6.49 | 4.46 | -1.76 | -3.31 |
| `mistralai/mistral-small-2603` | 7.01 | 4.03 | -1.58 | -0.89 |
| `qwen/qwen3.7-flash` | 7.71 | 4.15 | -3.03 | -2.56 |
| `gemma4:e4b` | 3.53 | 8.91 | -0.26 | -7.24 |
| `openai/gpt-oss-120b` | 7.69 | 6.51 | -1.59 | -5.29 |
| `microsoft/phi-4` | 6.58 | 8.36 | +3.16 | -7.39 |

## Confidence calibration

Models include a self-reported `confidence` on each detected ad. A well-calibrated model should be right ~95% of the time when it claims 0.95 confidence. The table below bins each model's predictions and shows the actual hit rate (fraction that were true positives at IoU >= 0.5). A bin near 1.0 is well-calibrated; a low number with a high count means the model is overconfident.

| Model | 0.00-0.70 | 0.70-0.90 | 0.90-0.95 | 0.95-0.99 | 0.99+ | total |
|---|---:|---:|---:|---:|---:|---:|
| `bytedance-seed/seed-2-1-turbo` | -- | 0.12 (n=8) | -- | 0.86 (n=118) | -- | 126 |
| `claude-haiku-4-5-20251001` | 0.00 (n=5) | 0.25 (n=8) | -- | 0.78 (n=322) | -- | 335 |
| `claude-opus-4-8` | 0.00 (n=18) | 0.07 (n=40) | -- | 0.85 (n=293) | -- | 351 |
| `claude-opus-5` | 0.00 (n=11) | 0.12 (n=56) | -- | 0.94 (n=263) | -- | 330 |
| `claude-opus-5-5` | 0.00 (n=18) | 0.15 (n=33) | -- | 0.87 (n=277) | -- | 328 |
| `claude-sonnet-5` | 0.00 (n=10) | 0.12 (n=42) | -- | 0.81 (n=286) | -- | 338 |
| `deepseek/deepseek-v4-flash` | 0.00 (n=5) | 0.14 (n=22) | -- | 0.83 (n=223) | -- | 250 |
| `gemma4:e4b` | -- | 0.00 (n=2) | -- | 0.64 (n=163) | -- | 165 |
| `google/gemini-3.1-flash-lite` | -- | -- | -- | 0.75 (n=340) | -- | 340 |
| `google/gemini-3.5-flash` | -- | -- | -- | 0.80 (n=323) | -- | 323 |
| `google/gemini-3.5-flash-lite` | -- | 0.00 (n=18) | -- | 0.79 (n=319) | -- | 337 |
| `microsoft/phi-4` | -- | 0.00 (n=6) | -- | 0.56 (n=390) | -- | 396 |
| `mistralai/mistral-small-2603` | 0.00 (n=2) | 0.00 (n=1) | -- | 0.70 (n=332) | -- | 335 |
| `openai/gpt-oss-120b` | -- | 0.00 (n=12) | -- | 0.64 (n=345) | -- | 357 |
| `qwen/qwen3.5-plus-02-15` | 0.00 (n=10) | 0.00 (n=2) | -- | 0.79 (n=323) | -- | 335 |
| `qwen/qwen3.7-flash` | 0.00 (n=4) | 0.00 (n=24) | -- | 0.81 (n=308) | -- | 336 |
| `qwen/qwen3.8-flash` | 0.06 (n=32) | 0.44 (n=86) | -- | 0.86 (n=228) | -- | 346 |
| `x-ai/grok-4.3` | 0.00 (n=1) | 0.00 (n=6) | -- | 0.79 (n=309) | -- | 316 |

See `report_assets-segmentation-segment_ids/calibration.svg` for the visual reliability diagram.

## Latency tail

Median latency hides outliers. p99 and max are what determines queue depth and worst-case user wait. For OpenRouter-routed models the tail also reflects upstream provider load, not just model compute.

| Model | p50 | p90 | p95 | p99 | max |
|---|---:|---:|---:|---:|---:|
| `google/gemini-3.5-flash-lite` | 1.05s | 1.76s | 1.89s | 2.21s | 2.50s |
| `google/gemini-3.1-flash-lite` | 1.47s | 2.25s | 2.48s | 3.23s | 10.71s |
| `mistralai/mistral-small-2603` | 2.96s | 9.31s | 15.10s | 26.48s | 28.62s |
| `claude-opus-5-5` | 3.24s | 62.52s | 64.52s | 184.02s | 243.91s |
| `microsoft/phi-4` | 3.44s | 11.35s | 14.30s | 22.18s | 118.40s |
| `claude-opus-5` | 4.15s | 62.99s | 65.13s | 122.82s | 245.73s |
| `claude-opus-4-8` | 4.61s | 53.80s | 68.11s | 185.18s | 219.71s |
| `openai/gpt-oss-120b` | 6.88s | 35.25s | 59.30s | 427.63s | 502.12s |
| `google/gemini-3.5-flash` | 8.14s | 14.43s | 17.90s | 22.74s | 28.64s |
| `x-ai/grok-4.3` | 9.78s | 18.84s | 22.15s | 30.78s | 321.54s |
| `gemma4:e4b` | 13.29s | 23.36s | 25.52s | 30.41s | 31.89s |
| `claude-sonnet-5` | 18.27s | 57.28s | 73.64s | 208.32s | 249.45s |
| `deepseek/deepseek-v4-flash` | 31.90s | 144.12s | 259.32s | 962.30s | 1095.88s |
| `qwen/qwen3.7-flash` | 37.40s | 62.97s | 69.15s | 80.51s | 94.10s |
| `claude-haiku-4-5-20251001` | 45.76s | 129.66s | 154.67s | 300.86s | 440.29s |
| `qwen/qwen3.8-flash` | 108.75s | 223.88s | 246.60s | 273.85s | 301.73s |
| `qwen/qwen3.5-plus-02-15` | 121.07s | 180.41s | 202.04s | 260.31s | 279.58s |
| `bytedance-seed/seed-2-1-turbo` | 145.77s | 252.90s | 259.92s | 277.71s | 413.00s |

## Output token efficiency

How many output tokens the model spent per detected ad. Lower is more concise (the model finds an ad and returns the JSON). Higher means the model is producing a lot of text the parser will discard, which costs you whether or not the answer is right.

| Model | Total output tokens | Ads detected | Tokens / ad | Cost / TP |
|---|---:|---:|---:|---:|
| `claude-opus-5-5` | 91,196 | 882 | 103 | $0.0123 |
| `claude-opus-4-8` | 111,066 | 1012 | 110 | $0.0154 |
| `claude-sonnet-5` | 98,750 | 837 | 118 | $0.0065 |
| `claude-opus-5` | 92,674 | 779 | 119 | $0.0150 |
| `claude-haiku-4-5-20251001` | 147,127 | 931 | 158 | $0.0032 |
| `google/gemini-3.5-flash-lite` | 175,963 | 1071 | 164 | $0.0011 |
| `google/gemini-3.1-flash-lite` | 207,443 | 1019 | 204 | $0.0009 |
| `microsoft/phi-4` | 319,664 | 1090 | 293 | $0.0002 |
| `mistralai/mistral-small-2603` | 287,841 | 956 | 301 | $0.0006 |
| `x-ai/grok-4.3` | 773,819 | 945 | 819 | $0.0048 |
| `gemma4:e4b` | 392,648 | 450 | 873 | $0.0000 |
| `openai/gpt-oss-120b` | 1,056,845 | 901 | 1173 | $0.0003 |
| `google/gemini-3.5-flash` | 1,494,285 | 918 | 1628 | $0.0143 |
| `qwen/qwen3.7-flash` | 3,791,528 | 874 | 4338 | $0.0005 |
| `deepseek/deepseek-v4-flash` | 2,739,622 | 542 | 5055 | $0.0038 |
| `qwen/qwen3.5-plus-02-15` | 6,825,689 | 1058 | 6452 | $0.0090 |
| `qwen/qwen3.8-flash` | 6,862,809 | 809 | 8483 | $0.0032 |
| `bytedance-seed/seed-2-1-turbo` | 8,332,801 | 198 | 42085 | $0.0437 |

## Cost breakdown (input vs output)

Where each model's per-episode dollars go, at the same pricing snapshot as every other table. Every model reads the same transcripts, so the input side varies only with the provider's input price. The output side varies with how much the model writes: a high output share on a modest total usually means reasoning tokens, and a model with a low per-token price can still land mid-table by writing thousands of them. Failed calls are excluded, same as the cost column everywhere else.

| Model | Cost / episode | Input | Output | Output share |
|---|---:|---:|---:|---:|
| `bytedance-seed/seed-2-1-turbo` | $4.4551 | $0.2887 | $4.1664 | 94% |
| `claude-opus-4-8` | $3.8987 | $3.3434 | $0.5553 | 14% |
| `claude-opus-5` | $3.8067 | $3.3434 | $0.4634 | 12% |
| `google/gemini-3.5-flash` | $3.6825 | $0.9928 | $2.6897 | 73% |
| `claude-opus-5-5` | $3.0395 | $2.6747 | $0.3648 | 12% |
| `qwen/qwen3.5-plus-02-15` | $2.2988 | $0.1692 | $2.1296 | 93% |
| `claude-sonnet-5` | $1.5348 | $1.3373 | $0.1975 | 13% |
| `x-ai/grok-4.3` | $1.1875 | $0.8006 | $0.3869 | 33% |
| `claude-haiku-4-5-20251001` | $0.8158 | $0.6687 | $0.1471 | 18% |
| `qwen/qwen3.8-flash` | $0.7437 | $0.0986 | $0.6451 | 87% |
| `deepseek/deepseek-v4-flash` | $0.7204 | $0.0190 | $0.7013 | 97% |
| `google/gemini-3.5-flash-lite` | $0.2866 | $0.1986 | $0.0880 | 31% |
| `google/gemini-3.1-flash-lite` | $0.2260 | $0.1637 | $0.0622 | 28% |
| `mistralai/mistral-small-2603` | $0.1314 | $0.0969 | $0.0345 | 26% |
| `qwen/qwen3.7-flash` | $0.1181 | $0.0195 | $0.0986 | 83% |
| `openai/gpt-oss-120b` | $0.0588 | $0.0229 | $0.0359 | 61% |
| `microsoft/phi-4` | $0.0525 | $0.0436 | $0.0090 | 17% |

## Trial variance (determinism check)

All trials run at temperature 0.0. If a model produces stable output you'd expect the F1 stdev across trials to be near zero. Higher numbers mean the model is non-deterministic even at temp=0. That's fine to know, but means you cannot trust a single trial's number for that model.

| Model | Mean F1 stdev across episodes | Highest single-episode stdev |
|---|---:|---:|
| `qwen/qwen3.5-plus-02-15` | 0.0198 | 0.1217 |
| `google/gemini-3.5-flash` | 0.0270 | 0.1461 |
| `claude-opus-5` | 0.0295 | 0.0857 |
| `claude-haiku-4-5-20251001` | 0.0444 | 0.1201 |
| `google/gemini-3.5-flash-lite` | 0.0230 | 0.1369 |
| `claude-opus-4-8` | 0.0403 | 0.1135 |
| `google/gemini-3.1-flash-lite` | 0.0258 | 0.1342 |
| `qwen/qwen3.7-flash` | 0.0445 | 0.2236 |
| `claude-opus-5-5` | 0.0501 | 0.0963 |
| `x-ai/grok-4.3` | 0.0379 | 0.1193 |
| `qwen/qwen3.8-flash` | 0.0837 | 0.2169 |
| `claude-sonnet-5` | 0.0625 | 0.1565 |
| `mistralai/mistral-small-2603` | 0.0399 | 0.1278 |
| `openai/gpt-oss-120b` | 0.1312 | 0.2681 |
| `gemma4:e4b` | 0.0713 | 0.1826 |
| `deepseek/deepseek-v4-flash` | 0.1082 | 0.2510 |
| `microsoft/phi-4` | 0.0275 | 0.1220 |
| `bytedance-seed/seed-2-1-turbo` | 0.1606 | 0.4472 |

## Cross-model agreement

For each of the 182 (episode, window, trial-equivalent) entries, how many of the 18 active models predicted at least one ad? High-agreement windows are unambiguous ads (or unambiguously not ads). Low-agreement windows are where individual models disagree, and are candidates for ensemble voting if you want a cheap accuracy boost.

| Models predicting an ad | Window count | Share |
|---:|---:|---:|
| 0 of 18 | 69 | 37.9% |
| 1 of 18 | 7 | 3.8% |
| 2 of 18 | 5 | 2.7% |
| 3 of 18 | 9 | 4.9% |
| 4 of 18 | 2 | 1.1% |
| 5 of 18 | 3 | 1.6% |
| 6 of 18 | 4 | 2.2% |
| 8 of 18 | 1 | 0.5% |
| 13 of 18 | 1 | 0.5% |
| 14 of 18 | 5 | 2.7% |
| 15 of 18 | 10 | 5.5% |
| 16 of 18 | 12 | 6.6% |
| 17 of 18 | 35 | 19.2% |
| 18 of 18 | 19 | 10.4% |

Read this as: rows near the top are windows where the field disagrees (most models said no, a few said yes, usually false positives); rows near the bottom are windows where the field broadly agrees (typical of clear sponsor reads).

### Per-model alignment with consensus

Same data, viewed per model. For each window, the **majority** is whether more than half of the 18 active models flagged an ad. Then for each model: did it vote with the majority or against it? Four buckets:

- **with-yes**: this model voted yes, majority also voted yes (likely true positive)
- **with-no**: this model voted no, majority also voted no (likely true negative)
- **broke-yes**: this model voted yes, majority voted no (likely false positive / hallucination)
- **broke-no**: this model voted no, majority voted yes (likely missed real ad)

Alignment rate is `(with-yes + with-no) / total`. High alignment means the model tracks the consensus; low alignment means it disagrees often, which could be brilliance or noise depending on whether its disagreements are also where its F1 wins or loses.

| Model | with-yes | with-no | broke-yes | broke-no | Alignment |
|---|---:|---:|---:|---:|---:|
| `x-ai/grok-4.3` | 82 | 99 | 1 | 0 | 99.5% |
| `claude-opus-5-5` | 82 | 96 | 4 | 0 | 97.8% |
| `claude-sonnet-5` | 82 | 96 | 4 | 0 | 97.8% |
| `claude-haiku-4-5-20251001` | 82 | 95 | 5 | 0 | 97.3% |
| `claude-opus-5` | 82 | 95 | 5 | 0 | 97.3% |
| `google/gemini-3.5-flash` | 82 | 95 | 5 | 0 | 97.3% |
| `google/gemini-3.1-flash-lite` | 82 | 94 | 6 | 0 | 96.7% |
| `claude-opus-4-8` | 82 | 93 | 7 | 0 | 96.2% |
| `google/gemini-3.5-flash-lite` | 81 | 94 | 6 | 1 | 96.2% |
| `mistralai/mistral-small-2603` | 78 | 96 | 4 | 4 | 95.6% |
| `microsoft/phi-4` | 76 | 97 | 3 | 6 | 95.1% |
| `qwen/qwen3.5-plus-02-15` | 82 | 91 | 9 | 0 | 95.1% |
| `qwen/qwen3.7-flash` | 79 | 93 | 7 | 3 | 94.5% |
| `qwen/qwen3.8-flash` | 81 | 91 | 9 | 1 | 94.5% |
| `openai/gpt-oss-120b` | 82 | 84 | 16 | 0 | 91.2% |
| `deepseek/deepseek-v4-flash` | 67 | 94 | 6 | 15 | 88.5% |
| `bytedance-seed/seed-2-1-turbo` | 50 | 99 | 1 | 32 | 81.9% |
| `gemma4:e4b` | 30 | 99 | 1 | 52 | 70.9% |

### Windows flagged with no truth ad

The other side of the histogram: windows the ground truth marks ad-free, ranked by how many of the 18 models flagged them anyway (in at least one trial). A window near the top is either content that genuinely resembles an ad, which is what precision-focused validator rules should train against, or a spot the truth file missed. Either way these are the first windows worth a manual re-listen; on a corpus this size a single mislabeled window moves scores. No-ad control episodes are included and tagged.

| Episode | Window | Span | Models flagging |
|---|---:|---|---:|
| `ep-on-air-with-dan-and-alex2-574e4f303730` | 5 | 2100-2700s | 17 of 18 |
| `ep-on-air-with-dan-and-alex2-574e4f303730` | 6 | 2520-3120s | 16 of 18 |
| `ep-drink-champs-30c9a2d49f13` | 12 | 5040-5640s | 15 of 18 |
| `ep-the-brilliant-idiots-0bb9bf634c8e` | 9 | 3780-4380s | 15 of 18 |
| `ep-the-brilliant-idiots-0bb9bf634c8e` | 10 | 4200-4800s | 14 of 18 |
| `ep-drink-champs-30c9a2d49f13` | 35 | 14700-15300s | 13 of 18 |
| `ep-on-air-with-dan-and-alex2-574e4f303730` | 2 | 840-1440s | 8 of 18 |
| `ep-it-s-a-thing-e339179dfad6` | 2 | 840-1440s | 6 of 18 |
| `ep-oxide-and-friends-ce789ff5b62e` (no-ad control) | 11 | 4620-5070s | 6 of 18 |
| `ep-oxide-and-friends-ce789ff5b62e` (no-ad control) | 12 | 5040-5070s | 6 of 18 |
| `ep-the-brilliant-idiots-0bb9bf634c8e` | 14 | 5880-6480s | 6 of 18 |
| `ep-security-now-audio-2850b24903b2` | 7 | 2940-3540s | 5 of 18 |
| `ep-the-brilliant-idiots-0bb9bf634c8e` | 15 | 6300-6900s | 5 of 18 |
| `ep-daily-gist-chicago-70a82fe93a5c` | 2 | 840-1271s | 4 of 18 |
| `ep-glt1412515089-373d5ba5007b` | 22 | 9240-9840s | 4 of 18 |

... and 13 more with 2+ votes.

## Detection rate by ad characteristic

Aggregate detection rates often hide systematic blind spots. Below: for each model, what fraction of truth ads in each bucket were detected (matched at IoU >= 0.5).

### By ad length

Truth ads bucketed by duration: short (<30s), medium (30-90s), long (>=90s). Cell values are detection rate (fraction of truth ads in that bucket the model caught), with the sample size `n` so a misleading 1.00 on a 2-ad bucket doesn't get over-weighted. Models that systematically miss short ads usually fail on network-inserted brand-tagline spots; missing long ads is rarer and usually means the model gave up before processing the full window.

| Model | long (>=90s) | medium (30-90s) | short (<30s) |
|---|---:|---:|---:|
| `bytedance-seed/seed-2-1-turbo` | 0.39 (n=165) | 0.33 (n=75) | 0.40 (n=30) |
| `claude-haiku-4-5-20251001` | 0.96 (n=165) | 0.91 (n=75) | 0.83 (n=30) |
| `claude-opus-4-8` | 0.97 (n=165) | 0.93 (n=75) | 0.77 (n=30) |
| `claude-opus-5` | 0.96 (n=165) | 1.00 (n=75) | 0.67 (n=30) |
| `claude-opus-5-5` | 0.95 (n=165) | 0.93 (n=75) | 0.70 (n=30) |
| `claude-sonnet-5` | 0.90 (n=165) | 0.85 (n=75) | 0.83 (n=30) |
| `deepseek/deepseek-v4-flash` | 0.67 (n=165) | 0.73 (n=75) | 0.77 (n=30) |
| `gemma4:e4b` | 0.92 (n=60) | 0.74 (n=50) | 0.87 (n=15) |
| `google/gemini-3.1-flash-lite` | 0.96 (n=165) | 0.97 (n=75) | 0.80 (n=30) |
| `google/gemini-3.5-flash` | 0.96 (n=165) | 0.96 (n=75) | 0.87 (n=30) |
| `google/gemini-3.5-flash-lite` | 0.97 (n=165) | 0.96 (n=75) | 0.67 (n=30) |
| `microsoft/phi-4` | 0.84 (n=165) | 0.75 (n=75) | 0.83 (n=30) |
| `mistralai/mistral-small-2603` | 0.92 (n=165) | 0.89 (n=75) | 0.47 (n=30) |
| `openai/gpt-oss-120b` | 0.82 (n=165) | 0.88 (n=75) | 0.70 (n=30) |
| `qwen/qwen3.5-plus-02-15` | 0.93 (n=165) | 1.00 (n=75) | 0.93 (n=30) |
| `qwen/qwen3.7-flash` | 0.97 (n=165) | 0.99 (n=75) | 0.47 (n=30) |
| `qwen/qwen3.8-flash` | 0.86 (n=165) | 0.91 (n=75) | 0.83 (n=30) |
| `x-ai/grok-4.3` | 0.96 (n=165) | 0.84 (n=75) | 0.77 (n=30) |

### By ad position

Truth ads bucketed by where they fall in the episode: pre-roll (first 10%), mid-roll (10-90%), post-roll (last 10%). Cell values are the same detection-rate-with-`n` format as ad length. A common failure pattern in our data: most models detect pre-roll and mid-roll reliably and miss post-roll, because the prompt windows near the end often catch the model mid-reasoning or with fewer transition phrases to anchor on.

| Model | pre-roll (<10%) | mid-roll (10-90%) | post-roll (>90%) |
|---|---:|---:|---:|
| `bytedance-seed/seed-2-1-turbo` | 0.20 (n=75) | 0.46 (n=140) | 0.42 (n=55) |
| `claude-haiku-4-5-20251001` | 0.97 (n=75) | 0.93 (n=140) | 0.89 (n=55) |
| `claude-opus-4-8` | 0.97 (n=75) | 0.96 (n=140) | 0.84 (n=55) |
| `claude-opus-5` | 1.00 (n=75) | 0.93 (n=140) | 0.87 (n=55) |
| `claude-opus-5-5` | 0.93 (n=75) | 0.92 (n=140) | 0.87 (n=55) |
| `claude-sonnet-5` | 0.93 (n=75) | 0.88 (n=140) | 0.80 (n=55) |
| `deepseek/deepseek-v4-flash` | 0.63 (n=75) | 0.71 (n=140) | 0.75 (n=55) |
| `gemma4:e4b` | 0.86 (n=35) | 0.97 (n=60) | 0.57 (n=30) |
| `google/gemini-3.1-flash-lite` | 0.97 (n=75) | 0.95 (n=140) | 0.89 (n=55) |
| `google/gemini-3.5-flash` | 0.99 (n=75) | 0.97 (n=140) | 0.85 (n=55) |
| `google/gemini-3.5-flash-lite` | 1.00 (n=75) | 0.93 (n=140) | 0.85 (n=55) |
| `microsoft/phi-4` | 0.81 (n=75) | 0.79 (n=140) | 0.89 (n=55) |
| `mistralai/mistral-small-2603` | 0.83 (n=75) | 0.90 (n=140) | 0.82 (n=55) |
| `openai/gpt-oss-120b` | 0.87 (n=75) | 0.86 (n=140) | 0.65 (n=55) |
| `qwen/qwen3.5-plus-02-15` | 1.00 (n=75) | 0.98 (n=140) | 0.80 (n=55) |
| `qwen/qwen3.7-flash` | 1.00 (n=75) | 0.89 (n=140) | 0.89 (n=55) |
| `qwen/qwen3.8-flash` | 0.93 (n=75) | 0.86 (n=140) | 0.82 (n=55) |
| `x-ai/grok-4.3` | 0.93 (n=75) | 0.91 (n=140) | 0.87 (n=55) |

## Quick Comparison

One row per model, one column per episode. The headline columns (`F1`, `Cost/ep`, `p50`) summarize across all episodes; the per-episode columns let you see whether a model's average hides wide swings (a model that scores well overall might still bomb on a specific genre). The right-most `F1 stdev` column averages the per-trial standard deviations across episodes; high values mean the model isn't deterministic at temperature 0.0, so its single-trial F1 number is noisy. `Moderation blocked` is the share of attempted calls the provider refused on content grounds; those windows never reach scoring, so any non-zero value means that row's F1 was computed on a subset of the corpus and is not comparable to a row at `-`.

| Model | F1 | Cost/ep | p50 | ep-andy-and-ari-e774e8022fab | ep-crime-junkie-8ce498f299d7 | ep-daily-gist-chicago-70a82fe93a5c | ep-daily-tech-news-show-b576979e1fe8 | ep-daily-tech-news-show-c1904b8605f7 | ep-drink-champs-30c9a2d49f13 | ep-glt1412515089-373d5ba5007b | ep-it-s-a-thing-e339179dfad6 | ep-on-air-with-dan-and-alex2-574e4f303730 | ep-security-now-audio-2850b24903b2 | ep-the-brilliant-idiots-0bb9bf634c8e | ep-the-tim-dillon-show-f62bd5fa1cfe | ep-tosh-show-5f6894439bb6 | ep-ai-cloud-essentials-e8dc897fbd6b (no-ad) | ep-oxide-and-friends-ce789ff5b62e (no-ad) | F1 stdev | Moderation blocked |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| `qwen/qwen3.5-plus-02-15` | 0.868 | $2.2988 | 121.1s | 1.000 | 0.889 | 1.000 | 0.889 | 0.667 | 0.857 | 0.800 | 1.000 | 0.800 | 0.773 | 0.700 | 1.000 | 0.909 | FAIL (1 FP) | FAIL (1 FP) | 0.020 | - |
| `google/gemini-3.5-flash` | 0.864 | $3.6825 | 8.1s | 1.000 | 1.000 | 1.000 | 0.971 | 0.613 | 0.880 | 0.886 | 0.507 | 0.800 | 0.922 | 0.750 | 1.000 | 0.909 | PASS | FAIL (2 FP) | 0.027 | - |
| `claude-opus-5` | 0.852 | $3.8067 | 4.2s | 1.000 | 0.978 | 1.000 | 0.956 | 0.667 | 0.821 | 0.857 | 0.720 | 0.773 | 0.922 | 0.589 | 1.000 | 0.800 | PASS | FAIL (1 FP) | 0.030 | - |
| `claude-haiku-4-5-20251001` | 0.829 | $0.8158 | 45.8s | 0.982 | 0.956 | 1.000 | 0.871 | 0.613 | 0.902 | 0.543 | 0.720 | 0.800 | 0.834 | 0.664 | 1.000 | 0.894 | PASS | FAIL (1 FP) | 0.044 | - |
| `google/gemini-3.5-flash-lite` | 0.829 | $0.2866 | 1.1s | 1.000 | 0.850 | 0.800 | 0.889 | 0.627 | 0.861 | 0.857 | 0.800 | 0.800 | 0.933 | 0.605 | 1.000 | 0.756 | FAIL (1 FP) | PASS | 0.023 | - |
| `claude-opus-4-8` | 0.827 | $3.8987 | 4.6s | 1.000 | 0.933 | 1.000 | 0.828 | 0.613 | 0.826 | 0.857 | 0.720 | 0.800 | 0.867 | 0.478 | 1.000 | 0.827 | PASS | FAIL (1 FP) | 0.040 | - |
| `google/gemini-3.1-flash-lite` | 0.824 | $0.2260 | 1.5s | 1.000 | 1.000 | 0.740 | 0.861 | 0.580 | 0.842 | 0.889 | 0.800 | 0.667 | 0.933 | 0.667 | 1.000 | 0.733 | PASS | PASS | 0.026 | - |
| `qwen/qwen3.7-flash` | 0.824 | $0.1181 | 37.4s | 1.000 | 0.927 | 0.600 | 0.889 | 0.600 | 0.787 | 0.669 | 1.000 | 0.800 | 0.866 | 0.771 | 1.000 | 0.800 | FAIL (1 FP) | PASS | 0.045 | - |
| `claude-opus-5-5` | 0.824 | $3.0395 | 3.2s | 0.960 | 0.978 | 0.960 | 0.916 | 0.600 | 0.831 | 0.857 | 0.674 | 0.720 | 0.933 | 0.529 | 1.000 | 0.748 | PASS | FAIL (1 FP) | 0.050 | - |
| `x-ai/grok-4.3` | 0.823 | $1.1875 | 9.8s | 0.942 | 0.911 | 1.000 | 0.764 | 0.613 | 0.931 | 0.571 | 0.813 | 0.800 | 0.922 | 0.571 | 1.000 | 0.865 | PASS | PASS | 0.038 | - |
| `qwen/qwen3.8-flash` | 0.795 | $0.7437 | 108.7s | 0.942 | 0.978 | 1.000 | 0.810 | 0.600 | 0.647 | 0.684 | 0.773 | 0.840 | 0.710 | 0.565 | 0.914 | 0.873 | PASS | FAIL (2 FP) | 0.084 | - |
| `claude-sonnet-5` | 0.785 | $1.5348 | 18.3s | 1.000 | 0.876 | 1.000 | 0.780 | 0.600 | 0.853 | 0.686 | 0.773 | 0.693 | 0.743 | 0.456 | 0.900 | 0.850 | PASS | FAIL (1 FP) | 0.062 | - |
| `mistralai/mistral-small-2603` | 0.767 | $0.1314 | 3.0s | 1.000 | 0.889 | 0.420 | 0.889 | 0.600 | 0.733 | 0.800 | 0.773 | 0.693 | 0.691 | 0.773 | 0.914 | 0.800 | PASS | PASS | 0.040 | - |
| `openai/gpt-oss-120b` | 0.729 | $0.0588 | 6.9s | 0.822 | 0.877 | 0.920 | 0.899 | 0.642 | 0.688 | 0.807 | 0.627 | 0.693 | 0.629 | 0.388 | 0.772 | 0.716 | PASS | FAIL (4 FP) | 0.131 | - |
| `gemma4:e4b` | 0.723 | $0.0000 | 13.3s | - | 0.871 | 1.000 | 0.653 | 0.578 | - | - | 0.300 | - | - | - | 0.806 | 0.851 | PASS | - | 0.071 | - |
| `deepseek/deepseek-v4-flash` | 0.715 | $0.7204 | 31.9s | 1.000 | 0.886 | 0.933 | 0.434 | 0.533 | 0.209 | 0.757 | 0.853 | 0.560 | 0.856 | 0.611 | 0.950 | 0.716 | PASS | PASS | 0.108 | - |
| `microsoft/phi-4` | 0.710 | $0.0525 | 3.4s | 0.855 | 0.889 | 1.000 | 0.693 | 0.679 | 0.270 | 0.571 | 0.667 | 0.667 | 0.831 | 0.571 | 0.750 | 0.782 | PASS | PASS | 0.028 | - |
| `bytedance-seed/seed-2-1-turbo` | 0.467 | $4.4551 | 145.8s | 0.744 | 0.613 | 0.200 | 0.465 | 0.293 | 0.084 | 0.629 | 0.000 | 0.560 | 0.485 | 0.713 | 0.724 | 0.559 | PASS | PASS | 0.161 | - |

---

## Detailed Results

### Per-Model Detail

Full per-model profile: F1 averaged across episodes, total cost per episode at current pricing, p50 / p95 latency, JSON compliance, parse-failure rate, the distribution of extraction methods the parser had to use, and verbosity / truncation telemetry. The `Extraction methods` list shows how often each route was hit. `json_array_direct` is the cleanest; the rest are recovery paths. The verbosity row flags models that emit long `reason` fields or run out of token budget mid-response. Ordered by F1 descending so the best performers appear first.

#### `qwen/qwen3.5-plus-02-15`

- F1 (avg across episodes): **0.868**
- Total cost / episode: **$2.2988**
- p50 / p95 latency: 121.07s / 202.04s
- JSON compliance: 0.98
- JSON mode: native (100% native, 910 calls)
- Parse failure rate: 2.3%
- Extraction methods: `parse_failure`: 21, `segment_id_direct`: 889
- Verbosity: 910/910 calls over 1024 output tokens (100.0%); 7 hit max_tokens (0.8%); 0 salvaged from truncated JSON (0.0%)
- Segment category named on 1058/1058 detections (100%); the rest stay uncategorized (resolver: production)

#### `google/gemini-3.5-flash`

- F1 (avg across episodes): **0.864**
- Total cost / episode: **$3.6825**
- p50 / p95 latency: 8.14s / 17.90s
- JSON compliance: 1.00
- JSON mode: native (100% native, 910 calls)
- Parse failure rate: 0.0%
- Extraction methods: `json_object_single_ad_truncated`: 6, `segment_id_direct`: 904
- Verbosity: 604/910 calls over 1024 output tokens (66.4%); 6 hit max_tokens (0.7%); 6 salvaged from truncated JSON (0.7%)
- Segment category named on 918/918 detections (100%); the rest stay uncategorized (resolver: production)

#### `claude-opus-5`

- F1 (avg across episodes): **0.852**
- Total cost / episode: **$3.8067**
- p50 / p95 latency: 4.15s / 65.13s
- JSON compliance: 1.00
- JSON mode: native (100% native, 910 calls)
- Parse failure rate: 0.0%
- Extraction methods: `segment_id_direct`: 910
- Verbosity: 0/910 calls over 1024 output tokens (0.0%); 0 hit max_tokens (0.0%); 0 salvaged from truncated JSON (0.0%)
- Segment category named on 779/779 detections (100%); the rest stay uncategorized (resolver: production)

#### `claude-haiku-4-5-20251001`

- F1 (avg across episodes): **0.829**
- Total cost / episode: **$0.8158**
- p50 / p95 latency: 45.76s / 154.67s
- JSON compliance: 1.00
- JSON mode: native (100% native, 910 calls)
- Parse failure rate: 0.0%
- Extraction methods: `segment_id_direct`: 910
- Verbosity: 0/910 calls over 1024 output tokens (0.0%); 0 hit max_tokens (0.0%); 0 salvaged from truncated JSON (0.0%)
- Segment category named on 931/931 detections (100%); the rest stay uncategorized (resolver: production)

#### `google/gemini-3.5-flash-lite`

- F1 (avg across episodes): **0.829**
- Total cost / episode: **$0.2866**
- p50 / p95 latency: 1.05s / 1.89s
- JSON compliance: 1.00
- JSON mode: native (100% native, 910 calls)
- Parse failure rate: 0.0%
- Extraction methods: `segment_id_direct`: 910
- Verbosity: 0/910 calls over 1024 output tokens (0.0%); 0 hit max_tokens (0.0%); 0 salvaged from truncated JSON (0.0%)
- Segment category named on 1071/1071 detections (100%); the rest stay uncategorized (resolver: production)

#### `claude-opus-4-8`

- F1 (avg across episodes): **0.827**
- Total cost / episode: **$3.8987**
- p50 / p95 latency: 4.61s / 68.11s
- JSON compliance: 1.00
- JSON mode: native (100% native, 910 calls)
- Parse failure rate: 0.0%
- Extraction methods: `segment_id_direct`: 910
- Verbosity: 0/910 calls over 1024 output tokens (0.0%); 0 hit max_tokens (0.0%); 0 salvaged from truncated JSON (0.0%)
- Segment category named on 1012/1012 detections (100%); the rest stay uncategorized (resolver: production)

#### `google/gemini-3.1-flash-lite`

- F1 (avg across episodes): **0.824**
- Total cost / episode: **$0.2260**
- p50 / p95 latency: 1.47s / 2.48s
- JSON compliance: 1.00
- JSON mode: native (100% native, 910 calls)
- Parse failure rate: 0.0%
- Extraction methods: `json_object_single_ad_truncated`: 10, `segment_id_direct`: 900
- Verbosity: 0/910 calls over 1024 output tokens (0.0%); 0 hit max_tokens (0.0%); 10 salvaged from truncated JSON (1.1%)
- Segment category named on 1019/1019 detections (100%); the rest stay uncategorized (resolver: production)

#### `qwen/qwen3.7-flash`

- F1 (avg across episodes): **0.824**
- Total cost / episode: **$0.1181**
- p50 / p95 latency: 37.40s / 69.15s
- JSON compliance: 1.00
- JSON mode: native (100% native, 910 calls)
- Parse failure rate: 0.2%
- Extraction methods: `parse_failure`: 2, `segment_id_direct`: 908
- Verbosity: 910/910 calls over 1024 output tokens (100.0%); 4 hit max_tokens (0.4%); 0 salvaged from truncated JSON (0.0%)
- Segment category named on 874/874 detections (100%); the rest stay uncategorized (resolver: production)

#### `claude-opus-5-5`

- F1 (avg across episodes): **0.824**
- Total cost / episode: **$3.0395**
- p50 / p95 latency: 3.24s / 64.52s
- JSON compliance: 1.00
- JSON mode: native (100% native, 910 calls)
- Parse failure rate: 0.0%
- Extraction methods: `segment_id_direct`: 910
- Verbosity: 0/910 calls over 1024 output tokens (0.0%); 0 hit max_tokens (0.0%); 0 salvaged from truncated JSON (0.0%)
- Segment category named on 882/882 detections (100%); the rest stay uncategorized (resolver: production)

#### `x-ai/grok-4.3`

- F1 (avg across episodes): **0.823**
- Total cost / episode: **$1.1875**
- p50 / p95 latency: 9.78s / 22.15s
- JSON compliance: 1.00
- JSON mode: native (100% native, 910 calls)
- Parse failure rate: 0.0%
- Extraction methods: `segment_id_direct`: 910
- Verbosity: 279/910 calls over 1024 output tokens (30.7%); 0 hit max_tokens (0.0%); 0 salvaged from truncated JSON (0.0%)
- Segment category named on 945/945 detections (100%); the rest stay uncategorized (resolver: production)

#### `qwen/qwen3.8-flash`

- F1 (avg across episodes): **0.795**
- Total cost / episode: **$0.7437**
- p50 / p95 latency: 108.75s / 246.60s
- JSON compliance: 0.91
- JSON mode: native (100% native, 910 calls)
- Parse failure rate: 9.3%
- Extraction methods: `json_object_single_ad_truncated`: 4, `parse_failure`: 85, `segment_id_direct`: 821
- Verbosity: 877/910 calls over 1024 output tokens (96.4%); 71 hit max_tokens (7.8%); 4 salvaged from truncated JSON (0.4%)
- Segment category named on 809/809 detections (100%); the rest stay uncategorized (resolver: production)

#### `claude-sonnet-5`

- F1 (avg across episodes): **0.785**
- Total cost / episode: **$1.5348**
- p50 / p95 latency: 18.27s / 73.64s
- JSON compliance: 1.00
- JSON mode: native (100% native, 910 calls)
- Parse failure rate: 0.0%
- Extraction methods: `segment_id_direct`: 910
- Verbosity: 0/910 calls over 1024 output tokens (0.0%); 0 hit max_tokens (0.0%); 0 salvaged from truncated JSON (0.0%)
- Segment category named on 837/837 detections (100%); the rest stay uncategorized (resolver: production)

#### `mistralai/mistral-small-2603`

- F1 (avg across episodes): **0.767**
- Total cost / episode: **$0.1314**
- p50 / p95 latency: 2.96s / 15.10s
- JSON compliance: 1.00
- JSON mode: native (100% native, 910 calls)
- Parse failure rate: 0.0%
- Extraction methods: `segment_id_direct`: 910
- Verbosity: 3/910 calls over 1024 output tokens (0.3%); 0 hit max_tokens (0.0%); 0 salvaged from truncated JSON (0.0%)
- Segment category named on 956/956 detections (100%); the rest stay uncategorized (resolver: production)

#### `openai/gpt-oss-120b`

- F1 (avg across episodes): **0.729**
- Total cost / episode: **$0.0588**
- p50 / p95 latency: 6.88s / 59.30s
- JSON compliance: 1.00
- JSON mode: native (100% native, 910 calls)
- Parse failure rate: 0.0%
- Extraction methods: `json_object_single_ad_truncated`: 22, `segment_id_direct`: 888
- Verbosity: 257/910 calls over 1024 output tokens (28.2%); 32 hit max_tokens (3.5%); 22 salvaged from truncated JSON (2.4%)
- Segment category named on 901/901 detections (100%); the rest stay uncategorized (resolver: production)

#### `gemma4:e4b`

- F1 (avg across episodes): **0.723**
- Total cost / episode: **$0.0000**
- p50 / p95 latency: 13.29s / 25.52s
- JSON compliance: 1.00
- JSON mode: native (100% native, 235 calls)
- Parse failure rate: 0.0%
- Extraction methods: `segment_id_direct`: 235
- Verbosity: 148/235 calls over 1024 output tokens (63.0%); 0 hit max_tokens (0.0%); 0 salvaged from truncated JSON (0.0%)
- Segment category named on 450/450 detections (100%); the rest stay uncategorized (resolver: production)

#### `deepseek/deepseek-v4-flash`

- F1 (avg across episodes): **0.715**
- Total cost / episode: **$0.7204**
- p50 / p95 latency: 31.90s / 259.32s
- JSON compliance: 0.76
- JSON mode: native (100% native, 910 calls)
- Parse failure rate: 24.2%
- ID-contract misses (fell back to timestamp parse): 13
- Extraction methods: `parse_failure`: 220, `segment_id_direct`: 690
- Verbosity: 643/910 calls over 1024 output tokens (70.7%); 67 hit max_tokens (7.4%); 0 salvaged from truncated JSON (0.0%)
- Segment category named on 542/542 detections (100%); the rest stay uncategorized (resolver: production)

#### `microsoft/phi-4`

- F1 (avg across episodes): **0.710**
- Total cost / episode: **$0.0525**
- p50 / p95 latency: 3.44s / 14.30s
- JSON compliance: 1.00
- JSON mode: native (100% native, 910 calls)
- Parse failure rate: 0.0%
- ID-contract misses (fell back to timestamp parse): 5
- Extraction methods: `segment_id_direct`: 905, `segmentation_object_direct`: 5
- Verbosity: 32/910 calls over 1024 output tokens (3.5%); 1 hit max_tokens (0.1%); 0 salvaged from truncated JSON (0.0%)
- Segment category named on 1090/1090 detections (100%); the rest stay uncategorized (resolver: production)

#### `bytedance-seed/seed-2-1-turbo`

- F1 (avg across episodes): **0.467**
- Total cost / episode: **$4.4551**
- p50 / p95 latency: 145.77s / 259.92s
- JSON compliance: 0.61
- JSON mode: native (100% native, 910 calls)
- Parse failure rate: 38.6%
- Extraction methods: `json_object_single_ad_truncated`: 5, `parse_failure`: 351, `regex_json_array`: 4, `segment_id_direct`: 550
- Verbosity: 802/910 calls over 1024 output tokens (88.1%); 244 hit max_tokens (26.8%); 5 salvaged from truncated JSON (0.5%)
- Segment category named on 198/198 detections (100%); the rest stay uncategorized (resolver: production)


### Per-Episode Detail

One subsection per episode in the corpus, showing how every model performed on that specific episode. For ad-bearing episodes you see F1 and the stdev across trials (low stdev means stable, high stdev means the model's number on this episode is noisy). For the no-ad episode you see PASS / FAIL on the negative control: PASS = zero false positives across all windows, FAIL = the model flagged something that wasn't an ad, with the count.

#### `ep-ai-cloud-essentials-e8dc897fbd6b`: How Physical AI is Streamlining Engineering

- Podcast: ai-cloud-essentials
- Duration: 16.4 min
- Truth: no-ads episode

| Model | Result | FP count |
|-------|--------|----------|
| `bytedance-seed/seed-2-1-turbo` | PASS | 0 |
| `claude-haiku-4-5-20251001` | PASS | 0 |
| `claude-opus-4-8` | PASS | 0 |
| `claude-opus-5` | PASS | 0 |
| `claude-opus-5-5` | PASS | 0 |
| `claude-sonnet-5` | PASS | 0 |
| `deepseek/deepseek-v4-flash` | PASS | 0 |
| `gemma4:e4b` | PASS | 0 |
| `google/gemini-3.1-flash-lite` | PASS | 0 |
| `google/gemini-3.5-flash` | PASS | 0 |
| `microsoft/phi-4` | PASS | 0 |
| `mistralai/mistral-small-2603` | PASS | 0 |
| `openai/gpt-oss-120b` | PASS | 0 |
| `qwen/qwen3.8-flash` | PASS | 0 |
| `x-ai/grok-4.3` | PASS | 0 |
| `google/gemini-3.5-flash-lite` | FAIL | 1 |
| `qwen/qwen3.5-plus-02-15` | FAIL | 1 |
| `qwen/qwen3.7-flash` | FAIL | 1 |

#### `ep-andy-and-ari-e774e8022fab`: Indiana LOADS UP on the edge before Big Ten play vs Northwestern thanks to court ruling | Oregon at USC Deep DIVE | Trinidad Chambliss, QB1 in NFL Draft? Why the SEC is STEALING Big Ten's TV power

- Podcast: andy-and-ari
- Duration: 75.1 min
- Truth ads: 5

| Model | F1 | F1 stdev |
|-------|----|----------|
| `claude-opus-4-8` | 1.000 | 0.000 |
| `claude-opus-5` | 1.000 | 0.000 |
| `claude-sonnet-5` | 1.000 | 0.000 |
| `deepseek/deepseek-v4-flash` | 1.000 | 0.000 |
| `google/gemini-3.1-flash-lite` | 1.000 | 0.000 |
| `google/gemini-3.5-flash` | 1.000 | 0.000 |
| `google/gemini-3.5-flash-lite` | 1.000 | 0.000 |
| `mistralai/mistral-small-2603` | 1.000 | 0.000 |
| `qwen/qwen3.5-plus-02-15` | 1.000 | 0.000 |
| `qwen/qwen3.7-flash` | 1.000 | 0.000 |
| `claude-haiku-4-5-20251001` | 0.982 | 0.041 |
| `claude-opus-5-5` | 0.960 | 0.089 |
| `qwen/qwen3.8-flash` | 0.942 | 0.089 |
| `x-ai/grok-4.3` | 0.942 | 0.089 |
| `microsoft/phi-4` | 0.855 | 0.122 |
| `openai/gpt-oss-120b` | 0.822 | 0.049 |
| `bytedance-seed/seed-2-1-turbo` | 0.744 | 0.091 |

#### `ep-crime-junkie-8ce498f299d7`: MISSING: Christopher “Cole” Thomas

- Podcast: crime-junkie
- Duration: 48.2 min
- Truth ads: 4

| Model | F1 | F1 stdev |
|-------|----|----------|
| `google/gemini-3.1-flash-lite` | 1.000 | 0.000 |
| `google/gemini-3.5-flash` | 1.000 | 0.000 |
| `claude-opus-5` | 0.978 | 0.050 |
| `claude-opus-5-5` | 0.978 | 0.050 |
| `qwen/qwen3.8-flash` | 0.978 | 0.050 |
| `claude-haiku-4-5-20251001` | 0.956 | 0.061 |
| `claude-opus-4-8` | 0.933 | 0.061 |
| `qwen/qwen3.7-flash` | 0.927 | 0.068 |
| `x-ai/grok-4.3` | 0.911 | 0.050 |
| `microsoft/phi-4` | 0.889 | 0.000 |
| `mistralai/mistral-small-2603` | 0.889 | 0.000 |
| `qwen/qwen3.5-plus-02-15` | 0.889 | 0.000 |
| `deepseek/deepseek-v4-flash` | 0.886 | 0.064 |
| `openai/gpt-oss-120b` | 0.877 | 0.089 |
| `claude-sonnet-5` | 0.876 | 0.137 |
| `gemma4:e4b` | 0.871 | 0.040 |
| `google/gemini-3.5-flash-lite` | 0.850 | 0.137 |
| `bytedance-seed/seed-2-1-turbo` | 0.613 | 0.119 |

#### `ep-daily-gist-chicago-70a82fe93a5c`: Suburban apartment market heats up

- Podcast: daily-gist-chicago
- Duration: 21.2 min
- Truth ads: 2

| Model | F1 | F1 stdev |
|-------|----|----------|
| `claude-haiku-4-5-20251001` | 1.000 | 0.000 |
| `claude-opus-4-8` | 1.000 | 0.000 |
| `claude-opus-5` | 1.000 | 0.000 |
| `claude-sonnet-5` | 1.000 | 0.000 |
| `gemma4:e4b` | 1.000 | 0.000 |
| `google/gemini-3.5-flash` | 1.000 | 0.000 |
| `microsoft/phi-4` | 1.000 | 0.000 |
| `qwen/qwen3.5-plus-02-15` | 1.000 | 0.000 |
| `qwen/qwen3.8-flash` | 1.000 | 0.000 |
| `x-ai/grok-4.3` | 1.000 | 0.000 |
| `claude-opus-5-5` | 0.960 | 0.089 |
| `deepseek/deepseek-v4-flash` | 0.933 | 0.149 |
| `openai/gpt-oss-120b` | 0.920 | 0.110 |
| `google/gemini-3.5-flash-lite` | 0.800 | 0.000 |
| `google/gemini-3.1-flash-lite` | 0.740 | 0.134 |
| `qwen/qwen3.7-flash` | 0.600 | 0.224 |
| `mistralai/mistral-small-2603` | 0.420 | 0.045 |
| `bytedance-seed/seed-2-1-turbo` | 0.200 | 0.447 |

#### `ep-daily-tech-news-show-b576979e1fe8`: Motorola Razr Fold is a Noble Competitor to the Galaxy Z Fold 7 - DTNS 5269

- Podcast: daily-tech-news-show
- Duration: 34.6 min
- Truth ads: 4

| Model | F1 | F1 stdev |
|-------|----|----------|
| `google/gemini-3.5-flash` | 0.971 | 0.064 |
| `claude-opus-5` | 0.956 | 0.061 |
| `claude-opus-5-5` | 0.916 | 0.085 |
| `openai/gpt-oss-120b` | 0.899 | 0.105 |
| `google/gemini-3.5-flash-lite` | 0.889 | 0.000 |
| `mistralai/mistral-small-2603` | 0.889 | 0.000 |
| `qwen/qwen3.5-plus-02-15` | 0.889 | 0.000 |
| `qwen/qwen3.7-flash` | 0.889 | 0.000 |
| `claude-haiku-4-5-20251001` | 0.871 | 0.040 |
| `google/gemini-3.1-flash-lite` | 0.861 | 0.062 |
| `claude-opus-4-8` | 0.828 | 0.114 |
| `qwen/qwen3.8-flash` | 0.810 | 0.099 |
| `claude-sonnet-5` | 0.780 | 0.027 |
| `x-ai/grok-4.3` | 0.764 | 0.096 |
| `microsoft/phi-4` | 0.693 | 0.060 |
| `gemma4:e4b` | 0.653 | 0.087 |
| `bytedance-seed/seed-2-1-turbo` | 0.465 | 0.324 |
| `deepseek/deepseek-v4-flash` | 0.434 | 0.077 |

#### `ep-daily-tech-news-show-c1904b8605f7`: Switch 2 Prices Rise, Forecast Drops - DTNS 5265

- Podcast: daily-tech-news-show
- Duration: 38.6 min
- Truth ads: 5

| Model | F1 | F1 stdev |
|-------|----|----------|
| `microsoft/phi-4` | 0.679 | 0.027 |
| `claude-opus-5` | 0.667 | 0.000 |
| `qwen/qwen3.5-plus-02-15` | 0.667 | 0.000 |
| `openai/gpt-oss-120b` | 0.642 | 0.166 |
| `google/gemini-3.5-flash-lite` | 0.627 | 0.037 |
| `claude-haiku-4-5-20251001` | 0.613 | 0.030 |
| `claude-opus-4-8` | 0.613 | 0.030 |
| `google/gemini-3.5-flash` | 0.613 | 0.030 |
| `x-ai/grok-4.3` | 0.613 | 0.030 |
| `claude-opus-5-5` | 0.600 | 0.000 |
| `claude-sonnet-5` | 0.600 | 0.000 |
| `mistralai/mistral-small-2603` | 0.600 | 0.000 |
| `qwen/qwen3.7-flash` | 0.600 | 0.000 |
| `qwen/qwen3.8-flash` | 0.600 | 0.000 |
| `google/gemini-3.1-flash-lite` | 0.580 | 0.045 |
| `gemma4:e4b` | 0.578 | 0.030 |
| `deepseek/deepseek-v4-flash` | 0.533 | 0.075 |
| `bytedance-seed/seed-2-1-turbo` | 0.293 | 0.289 |

#### `ep-drink-champs-30c9a2d49f13`: Episode 501 w/ Warren Sapp

- Podcast: drink-champs
- Duration: 258.6 min
- Truth ads: 9

| Model | F1 | F1 stdev |
|-------|----|----------|
| `x-ai/grok-4.3` | 0.931 | 0.023 |
| `claude-haiku-4-5-20251001` | 0.902 | 0.054 |
| `google/gemini-3.5-flash` | 0.880 | 0.021 |
| `google/gemini-3.5-flash-lite` | 0.861 | 0.026 |
| `qwen/qwen3.5-plus-02-15` | 0.857 | 0.030 |
| `claude-sonnet-5` | 0.853 | 0.033 |
| `google/gemini-3.1-flash-lite` | 0.842 | 0.000 |
| `claude-opus-5-5` | 0.831 | 0.096 |
| `claude-opus-4-8` | 0.826 | 0.036 |
| `claude-opus-5` | 0.821 | 0.030 |
| `qwen/qwen3.7-flash` | 0.787 | 0.052 |
| `mistralai/mistral-small-2603` | 0.733 | 0.052 |
| `openai/gpt-oss-120b` | 0.688 | 0.186 |
| `qwen/qwen3.8-flash` | 0.647 | 0.074 |
| `microsoft/phi-4` | 0.270 | 0.047 |
| `deepseek/deepseek-v4-flash` | 0.209 | 0.142 |
| `bytedance-seed/seed-2-1-turbo` | 0.084 | 0.116 |

#### `ep-glt1412515089-373d5ba5007b`: #2496 - Julia Mossbridge

- Podcast: glt1412515089
- Duration: 165.3 min
- Truth ads: 4

| Model | F1 | F1 stdev |
|-------|----|----------|
| `google/gemini-3.1-flash-lite` | 0.889 | 0.000 |
| `google/gemini-3.5-flash` | 0.886 | 0.064 |
| `claude-opus-4-8` | 0.857 | 0.000 |
| `claude-opus-5` | 0.857 | 0.000 |
| `claude-opus-5-5` | 0.857 | 0.000 |
| `google/gemini-3.5-flash-lite` | 0.857 | 0.000 |
| `openai/gpt-oss-120b` | 0.807 | 0.135 |
| `mistralai/mistral-small-2603` | 0.800 | 0.128 |
| `qwen/qwen3.5-plus-02-15` | 0.800 | 0.122 |
| `deepseek/deepseek-v4-flash` | 0.757 | 0.117 |
| `claude-sonnet-5` | 0.686 | 0.156 |
| `qwen/qwen3.8-flash` | 0.684 | 0.091 |
| `qwen/qwen3.7-flash` | 0.669 | 0.141 |
| `bytedance-seed/seed-2-1-turbo` | 0.629 | 0.052 |
| `microsoft/phi-4` | 0.571 | 0.000 |
| `x-ai/grok-4.3` | 0.571 | 0.000 |
| `claude-haiku-4-5-20251001` | 0.543 | 0.039 |

#### `ep-it-s-a-thing-e339179dfad6`: SOUP shots - It's a Thing 418

- Podcast: it-s-a-thing
- Duration: 26.7 min
- Truth ads: 2

| Model | F1 | F1 stdev |
|-------|----|----------|
| `qwen/qwen3.5-plus-02-15` | 1.000 | 0.000 |
| `qwen/qwen3.7-flash` | 1.000 | 0.000 |
| `deepseek/deepseek-v4-flash` | 0.853 | 0.145 |
| `x-ai/grok-4.3` | 0.813 | 0.119 |
| `google/gemini-3.1-flash-lite` | 0.800 | 0.000 |
| `google/gemini-3.5-flash-lite` | 0.800 | 0.000 |
| `claude-sonnet-5` | 0.773 | 0.060 |
| `mistralai/mistral-small-2603` | 0.773 | 0.060 |
| `qwen/qwen3.8-flash` | 0.773 | 0.060 |
| `claude-haiku-4-5-20251001` | 0.720 | 0.073 |
| `claude-opus-4-8` | 0.720 | 0.073 |
| `claude-opus-5` | 0.720 | 0.073 |
| `claude-opus-5-5` | 0.674 | 0.081 |
| `microsoft/phi-4` | 0.667 | 0.000 |
| `openai/gpt-oss-120b` | 0.627 | 0.268 |
| `google/gemini-3.5-flash` | 0.507 | 0.146 |
| `gemma4:e4b` | 0.300 | 0.183 |
| `bytedance-seed/seed-2-1-turbo` | 0.000 | 0.000 |

#### `ep-on-air-with-dan-and-alex2-574e4f303730`: Ryanair Wants Alcohol Bans, Emirates' $6.8B Record Profit & Buying Spirit Airlines?!

- Podcast: on-air-with-dan-and-alex2
- Duration: 58.1 min
- Truth ads: 2

| Model | F1 | F1 stdev |
|-------|----|----------|
| `qwen/qwen3.8-flash` | 0.840 | 0.089 |
| `claude-haiku-4-5-20251001` | 0.800 | 0.000 |
| `claude-opus-4-8` | 0.800 | 0.000 |
| `google/gemini-3.5-flash` | 0.800 | 0.000 |
| `google/gemini-3.5-flash-lite` | 0.800 | 0.000 |
| `qwen/qwen3.5-plus-02-15` | 0.800 | 0.000 |
| `qwen/qwen3.7-flash` | 0.800 | 0.000 |
| `x-ai/grok-4.3` | 0.800 | 0.000 |
| `claude-opus-5` | 0.773 | 0.060 |
| `claude-opus-5-5` | 0.720 | 0.073 |
| `claude-sonnet-5` | 0.693 | 0.060 |
| `mistralai/mistral-small-2603` | 0.693 | 0.060 |
| `openai/gpt-oss-120b` | 0.693 | 0.060 |
| `google/gemini-3.1-flash-lite` | 0.667 | 0.000 |
| `microsoft/phi-4` | 0.667 | 0.000 |
| `bytedance-seed/seed-2-1-turbo` | 0.560 | 0.134 |
| `deepseek/deepseek-v4-flash` | 0.560 | 0.251 |

#### `ep-oxide-and-friends-ce789ff5b62e`: Mechanical Engineering at Oxide [chapter images]

- Podcast: oxide-and-friends
- Duration: 84.5 min
- Truth: no-ads episode

| Model | Result | FP count |
|-------|--------|----------|
| `bytedance-seed/seed-2-1-turbo` | PASS | 0 |
| `deepseek/deepseek-v4-flash` | PASS | 0 |
| `google/gemini-3.1-flash-lite` | PASS | 0 |
| `google/gemini-3.5-flash-lite` | PASS | 0 |
| `microsoft/phi-4` | PASS | 0 |
| `mistralai/mistral-small-2603` | PASS | 0 |
| `qwen/qwen3.7-flash` | PASS | 0 |
| `x-ai/grok-4.3` | PASS | 0 |
| `claude-haiku-4-5-20251001` | FAIL | 1 |
| `claude-opus-4-8` | FAIL | 1 |
| `claude-opus-5` | FAIL | 1 |
| `claude-opus-5-5` | FAIL | 1 |
| `claude-sonnet-5` | FAIL | 1 |
| `qwen/qwen3.5-plus-02-15` | FAIL | 1 |
| `google/gemini-3.5-flash` | FAIL | 2 |
| `qwen/qwen3.8-flash` | FAIL | 2 |
| `openai/gpt-oss-120b` | FAIL | 4 |

#### `ep-security-now-audio-2850b24903b2`: SN 1077: A Browser AI API? - End of Bug Bounties?

- Podcast: security-now-audio
- Duration: 156.2 min
- Truth ads: 7

| Model | F1 | F1 stdev |
|-------|----|----------|
| `claude-opus-5-5` | 0.933 | 0.000 |
| `google/gemini-3.1-flash-lite` | 0.933 | 0.000 |
| `google/gemini-3.5-flash-lite` | 0.933 | 0.000 |
| `claude-opus-5` | 0.922 | 0.086 |
| `google/gemini-3.5-flash` | 0.922 | 0.026 |
| `x-ai/grok-4.3` | 0.922 | 0.026 |
| `claude-opus-4-8` | 0.867 | 0.056 |
| `qwen/qwen3.7-flash` | 0.866 | 0.046 |
| `deepseek/deepseek-v4-flash` | 0.856 | 0.049 |
| `claude-haiku-4-5-20251001` | 0.834 | 0.086 |
| `microsoft/phi-4` | 0.831 | 0.073 |
| `qwen/qwen3.5-plus-02-15` | 0.773 | 0.060 |
| `claude-sonnet-5` | 0.743 | 0.121 |
| `qwen/qwen3.8-flash` | 0.710 | 0.048 |
| `mistralai/mistral-small-2603` | 0.691 | 0.037 |
| `openai/gpt-oss-120b` | 0.629 | 0.057 |
| `bytedance-seed/seed-2-1-turbo` | 0.485 | 0.055 |

#### `ep-the-brilliant-idiots-0bb9bf634c8e`: Class Rank

- Podcast: the-brilliant-idiots
- Duration: 119.9 min
- Truth ads: 3

| Model | F1 | F1 stdev |
|-------|----|----------|
| `mistralai/mistral-small-2603` | 0.773 | 0.060 |
| `qwen/qwen3.7-flash` | 0.771 | 0.048 |
| `google/gemini-3.5-flash` | 0.750 | 0.000 |
| `bytedance-seed/seed-2-1-turbo` | 0.713 | 0.132 |
| `qwen/qwen3.5-plus-02-15` | 0.700 | 0.046 |
| `google/gemini-3.1-flash-lite` | 0.667 | 0.000 |
| `claude-haiku-4-5-20251001` | 0.664 | 0.120 |
| `deepseek/deepseek-v4-flash` | 0.611 | 0.156 |
| `google/gemini-3.5-flash-lite` | 0.605 | 0.061 |
| `claude-opus-5` | 0.589 | 0.024 |
| `microsoft/phi-4` | 0.571 | 0.000 |
| `x-ai/grok-4.3` | 0.571 | 0.000 |
| `qwen/qwen3.8-flash` | 0.565 | 0.217 |
| `claude-opus-5-5` | 0.529 | 0.039 |
| `claude-opus-4-8` | 0.478 | 0.090 |
| `claude-sonnet-5` | 0.456 | 0.025 |
| `openai/gpt-oss-120b` | 0.388 | 0.140 |

#### `ep-the-tim-dillon-show-f62bd5fa1cfe`: 495 - Hantavirus Cruise & iPad Babies

- Podcast: the-tim-dillon-show
- Duration: 80.1 min
- Truth ads: 6

| Model | F1 | F1 stdev |
|-------|----|----------|
| `claude-haiku-4-5-20251001` | 1.000 | 0.000 |
| `claude-opus-4-8` | 1.000 | 0.000 |
| `claude-opus-5` | 1.000 | 0.000 |
| `claude-opus-5-5` | 1.000 | 0.000 |
| `google/gemini-3.1-flash-lite` | 1.000 | 0.000 |
| `google/gemini-3.5-flash` | 1.000 | 0.000 |
| `google/gemini-3.5-flash-lite` | 1.000 | 0.000 |
| `qwen/qwen3.5-plus-02-15` | 1.000 | 0.000 |
| `qwen/qwen3.7-flash` | 1.000 | 0.000 |
| `x-ai/grok-4.3` | 1.000 | 0.000 |
| `deepseek/deepseek-v4-flash` | 0.950 | 0.112 |
| `mistralai/mistral-small-2603` | 0.914 | 0.078 |
| `qwen/qwen3.8-flash` | 0.914 | 0.192 |
| `claude-sonnet-5` | 0.900 | 0.137 |
| `gemma4:e4b` | 0.806 | 0.076 |
| `openai/gpt-oss-120b` | 0.772 | 0.193 |
| `microsoft/phi-4` | 0.750 | 0.000 |
| `bytedance-seed/seed-2-1-turbo` | 0.724 | 0.128 |

#### `ep-tosh-show-5f6894439bb6`: My Mom - Emergency Pod

- Podcast: tosh-show
- Duration: 41.4 min
- Truth ads: 5

| Model | F1 | F1 stdev |
|-------|----|----------|
| `google/gemini-3.5-flash` | 0.909 | 0.000 |
| `qwen/qwen3.5-plus-02-15` | 0.909 | 0.000 |
| `claude-haiku-4-5-20251001` | 0.894 | 0.034 |
| `qwen/qwen3.8-flash` | 0.873 | 0.081 |
| `x-ai/grok-4.3` | 0.865 | 0.060 |
| `gemma4:e4b` | 0.851 | 0.084 |
| `claude-sonnet-5` | 0.850 | 0.055 |
| `claude-opus-4-8` | 0.827 | 0.065 |
| `claude-opus-5` | 0.800 | 0.000 |
| `mistralai/mistral-small-2603` | 0.800 | 0.000 |
| `qwen/qwen3.7-flash` | 0.800 | 0.000 |
| `microsoft/phi-4` | 0.782 | 0.029 |
| `google/gemini-3.5-flash-lite` | 0.756 | 0.040 |
| `claude-opus-5-5` | 0.748 | 0.047 |
| `google/gemini-3.1-flash-lite` | 0.733 | 0.094 |
| `openai/gpt-oss-120b` | 0.716 | 0.147 |
| `deepseek/deepseek-v4-flash` | 0.716 | 0.072 |
| `bytedance-seed/seed-2-1-turbo` | 0.559 | 0.199 |


### Parser stress test

How each model's responses were actually parsed. Columns are extraction methods, ordered alphabetically; rows are models, sorted by parse-failure rate (cleanest at top). `json_array_direct` is the happy path: a bare JSON array we could `json.loads` and process immediately. `markdown_code_block` means we had to strip triple-backtick fences first; `json_object_*` means the model wrapped the array in an outer object and we had to find the array key; `regex_*` are last-resort recovery paths. A model that needs anything but `json_array_direct` for most calls is fragile. It works today, but a small prompt change can break the parser.

| Model | json_object_single_ad_truncated | parse_failure | regex_json_array | segment_id_direct | segmentation_object_direct |
|---|---|---|---|---|---|
| `claude-haiku-4-5-20251001` | 0 | 0 | 0 | 910 | 0 |
| `claude-opus-4-8` | 0 | 0 | 0 | 910 | 0 |
| `claude-opus-5` | 0 | 0 | 0 | 910 | 0 |
| `claude-opus-5-5` | 0 | 0 | 0 | 910 | 0 |
| `claude-sonnet-5` | 0 | 0 | 0 | 910 | 0 |
| `gemma4:e4b` | 0 | 0 | 0 | 235 | 0 |
| `google/gemini-3.1-flash-lite` | 10 | 0 | 0 | 900 | 0 |
| `google/gemini-3.5-flash` | 6 | 0 | 0 | 904 | 0 |
| `google/gemini-3.5-flash-lite` | 0 | 0 | 0 | 910 | 0 |
| `microsoft/phi-4` | 0 | 0 | 0 | 905 | 5 |
| `mistralai/mistral-small-2603` | 0 | 0 | 0 | 910 | 0 |
| `openai/gpt-oss-120b` | 22 | 0 | 0 | 888 | 0 |
| `x-ai/grok-4.3` | 0 | 0 | 0 | 910 | 0 |
| `qwen/qwen3.7-flash` | 0 | 2 | 0 | 908 | 0 |
| `qwen/qwen3.5-plus-02-15` | 0 | 21 | 0 | 889 | 0 |
| `qwen/qwen3.8-flash` | 4 | 85 | 0 | 821 | 0 |
| `deepseek/deepseek-v4-flash` | 0 | 220 | 0 | 690 | 0 |
| `bytedance-seed/seed-2-1-turbo` | 5 | 351 | 4 | 550 | 0 |

## Methodology

Reproducibility settings used for this run. The benchmark sends the same prompts MinusPod sends in production (same system prompt, same sponsor list, same windowing) so the F1 numbers here are directly relevant to production accuracy decisions. Cost is recomputed at report time from token counts against the active pricing snapshot, so all rows compare at the same prices regardless of when the actual call ran.

- Trials per (model, episode): **5**, temperature 0.0
- max_tokens: 4096 (22% of calls), 10000 (0%), 12000 (4%), 16384 (73%)
- response_format: json_object (with prompt-injection fallback when provider rejects native)
- Window size: 10 min, overlap: 3 min (imported from MinusPod's create_windows)
- Pricing snapshot: 2026-10-07T08:48:49.567770Z
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

**`model.transcribe()` invocation** (`src/transcriber.py` as of corpus transcription, before 2.96.0):

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

The `initial_prompt` seeded a sponsor vocabulary so Whisper produced consistent spellings (`Athletic Greens` rather than `AG1`, `ExpressVPN` rather than `express vpn`), biasing what showed up in the transcript and therefore what every benchmarked LLM is scored against. 2.96.0 removed the prompt because it dropped 7-17% of each episode's speech on the batched pipeline; the corpus has not been re-transcribed without it.

**Sponsor vocabulary** (254 canonical sponsors, 44 of them with explicit alias spellings totaling 48 aliases; from `src/utils/constants.py` `SEED_SPONSORS`). Laid out in two side-by-side groups, read top-to-bottom in each group.

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
| Lyft | - | automotive_transport | Zyn | `ZYN`, `Zinn` | tobacco_nicotine |

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

- Report generated: 2026-10-10T18:33:27Z
- Unique work units (current state, last-write-wins after retries): 15705
- Raw call records: 17987 (2282 superseded by later retries; kept for audit)
- Successful: 15705
- Failed: 0
- Lifetime list-price cost (sum of at-runtime costs, includes superseded rows): $134.7086
- Lifetime tokens (same basis): 57,839,098 in + 34,243,691 out = 92,082,789
- Note: every input token is priced at list rate. Providers that serve a repeated prompt from cache bill less than this, and the harness does not record cache hits, so a real invoice for this run will come in under the figure above.
- Active pricing snapshot: 2026-10-07T08:48:49.567770Z
- Addressing mode: segment_ids
- Prompt variant: segmentation
- System prompt: segmentation-v1.txt (sha256:85b53077)
