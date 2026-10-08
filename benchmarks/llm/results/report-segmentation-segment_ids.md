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
| A | `claude-fable-5-1` | 0.857 | +/-0.142 | 0.837 | 0.964 | 0.891 | $1.9845 | 5.4s | 1.00 |  |
| A | `claude-opus-5` | 0.849 | +/-0.157 | 0.835 | 0.936 | 0.874 | $0.9741 | 5.5s | 1.00 |  |
| A | `google/gemini-3.5-flash` | 0.834 | +/-0.221 | 0.822 | 0.914 | 0.857 | $1.0715 | 10.4s | 1.00 |  |
| A | `claude-haiku-4-5-20251001` | 0.822 | +/-0.164 | 0.799 | 0.964 | 0.865 | $0.2156 | 71.4s | 1.00 |  |
| A | `x-ai/grok-4.3` | 0.814 | +/-0.150 | 0.792 | 0.939 | 0.853 | $0.3117 | 7.9s | 1.00 |  |
| A | `qwen/qwen3.7-flash` | 0.813 | +/-0.171 | 0.803 | 0.871 | 0.831 | $0.0335 | 52.8s | 0.99 | (!) fails no-ad control |
| B | `claude-opus-4-8` | 0.808 | +/-0.162 | 0.786 | 0.937 | 0.846 | $1.0137 | 10.6s | 1.00 |  |
| B | `claude-sonnet-5` | 0.801 | +/-0.151 | 0.790 | 0.896 | 0.826 | $0.3954 | 26.6s | 1.00 |  |
| B | `claude-opus-5-5` | 0.799 | +/-0.180 | 0.777 | 0.941 | 0.839 | $0.7825 | 4.1s | 1.00 |  |
| B | `deepseek/deepseek-v4-flash` | 0.787 | +/-0.164 | 0.836 | 0.751 | 0.758 | $0.2042 | 32.8s | 0.81 | (!) brittle JSON |
| B | `google/gemini-3.1-flash-lite` | 0.782 | +/-0.160 | 0.763 | 0.896 | 0.816 | $0.0587 | 1.8s | 0.99 |  |
| B | `google/gemini-3.5-flash-lite` | 0.774 | +/-0.125 | 0.749 | 0.914 | 0.817 | $0.0752 | 1.4s | 1.00 | (!) fails no-ad control |
| B | `openai/gpt-oss-120b` | 0.754 | +/-0.124 | 0.741 | 0.837 | 0.779 | $0.0152 | 9.1s | 1.00 |  |
| C | `microsoft/phi-4` | 0.720 | +/-0.146 | 0.689 | 0.936 | 0.780 | $0.0139 | 7.7s | 1.00 |  |
| C | `mistralai/mistral-small-2603` | 0.719 | +/-0.184 | 0.700 | 0.843 | 0.755 | $0.0323 | 3.9s | 1.00 |  |
| C | `gemma4:e4b` | 0.682 | +/-0.225 | 0.659 | 0.817 | 0.723 | $0.0000 | 13.3s | 1.00 |  |
| D | `qwen/qwen3-8b` | 0.555 | +/-0.117 | 0.640 | 0.441 | 0.491 | $0.0576 | 31.2s | 0.51 | (!) brittle JSON (!) fails no-ad control |
| E | `qwen/qwen3.8-flash` | 0.167 | +/-0.186 | 0.224 | 0.094 | 0.127 | $0.1051 | 67.1s | 0.26 | (!) brittle JSON |
| F | `bytedance-seed/seed-2-1-turbo` | 0.000 | +/-0.000 | 0.000 | 0.000 | 0.000 | $0.5122 | 64.9s | 0.06 | (!) brittle JSON |
| F | `qwen/qwen3.5-plus-02-15` | 0.000 | +/-0.000 | 0.000 | 0.000 | 0.000 | $0.3407 | 70.7s | 0.02 | (!) brittle JSON |

### Best Value (F0.5 per dollar)

Paid-tier only, ranked by F0.5 per dollar. Free-tier models are excluded here because F0.5 / 0 is undefined; they are ranked separately under Best Free-Tier below. No confidence tiers on this table, since a point ratio does not group cleanly, but the reliability flags still apply.

| Rank | Model | F0.5/$ | F0.5 | F1 | Cost / episode | Flags |
|------|-------|--------|------|----|----------------|-------|
| 1 | `microsoft/phi-4` | 51.94 | 0.720 | 0.780 | $0.0139 |  |
| 2 | `openai/gpt-oss-120b` | 49.70 | 0.754 | 0.779 | $0.0152 |  |
| 3 | `qwen/qwen3.7-flash` | 24.26 | 0.813 | 0.831 | $0.0335 | (!) fails no-ad control |
| 4 | `mistralai/mistral-small-2603` | 22.26 | 0.719 | 0.755 | $0.0323 |  |
| 5 | `google/gemini-3.1-flash-lite` | 13.31 | 0.782 | 0.816 | $0.0587 |  |
| 6 | `google/gemini-3.5-flash-lite` | 10.30 | 0.774 | 0.817 | $0.0752 | (!) fails no-ad control |
| 7 | `qwen/qwen3-8b` | 9.64 | 0.555 | 0.491 | $0.0576 | (!) brittle JSON (!) fails no-ad control |
| 8 | `deepseek/deepseek-v4-flash` | 3.85 | 0.787 | 0.758 | $0.2042 | (!) brittle JSON |
| 9 | `claude-haiku-4-5-20251001` | 3.81 | 0.822 | 0.865 | $0.2156 |  |
| 10 | `x-ai/grok-4.3` | 2.61 | 0.814 | 0.853 | $0.3117 |  |
| 11 | `claude-sonnet-5` | 2.03 | 0.801 | 0.826 | $0.3954 |  |
| 12 | `qwen/qwen3.8-flash` | 1.59 | 0.167 | 0.127 | $0.1051 | (!) brittle JSON |
| 13 | `claude-opus-5-5` | 1.02 | 0.799 | 0.839 | $0.7825 |  |
| 14 | `claude-opus-5` | 0.87 | 0.849 | 0.874 | $0.9741 |  |
| 15 | `claude-opus-4-8` | 0.80 | 0.808 | 0.846 | $1.0137 |  |
| 16 | `google/gemini-3.5-flash` | 0.78 | 0.834 | 0.857 | $1.0715 |  |
| 17 | `claude-fable-5-1` | 0.43 | 0.857 | 0.891 | $1.9845 |  |
| 18 | `bytedance-seed/seed-2-1-turbo` | 0.00 | 0.000 | 0.000 | $0.5122 | (!) brittle JSON |
| 19 | `qwen/qwen3.5-plus-02-15` | 0.00 | 0.000 | 0.000 | $0.3407 | (!) brittle JSON |

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
| `claude-fable-5-1` | 0.837 | 0.964 | 120 | 27 | 5 |
| `claude-opus-5` | 0.835 | 0.936 | 115 | 26 | 10 |
| `claude-haiku-4-5-20251001` | 0.799 | 0.964 | 120 | 36 | 5 |
| `google/gemini-3.5-flash` | 0.822 | 0.914 | 116 | 29 | 9 |
| `x-ai/grok-4.3` | 0.792 | 0.939 | 116 | 37 | 9 |
| `claude-opus-4-8` | 0.786 | 0.937 | 116 | 38 | 9 |
| `claude-opus-5-5` | 0.777 | 0.941 | 116 | 41 | 9 |
| `qwen/qwen3.7-flash` | 0.803 | 0.871 | 110 | 31 | 15 |
| `claude-sonnet-5` | 0.790 | 0.896 | 110 | 37 | 15 |
| `google/gemini-3.5-flash-lite` | 0.749 | 0.914 | 112 | 39 | 13 |
| `google/gemini-3.1-flash-lite` | 0.763 | 0.896 | 110 | 35 | 15 |
| `microsoft/phi-4` | 0.689 | 0.936 | 116 | 63 | 9 |
| `openai/gpt-oss-120b` | 0.741 | 0.837 | 104 | 41 | 21 |
| `deepseek/deepseek-v4-flash` | 0.836 | 0.751 | 90 | 25 | 35 |
| `mistralai/mistral-small-2603` | 0.700 | 0.843 | 107 | 45 | 18 |
| `gemma4:e4b` | 0.659 | 0.817 | 105 | 60 | 20 |
| `qwen/qwen3-8b` | 0.640 | 0.441 | 55 | 35 | 70 |
| `qwen/qwen3.8-flash` | 0.224 | 0.094 | 14 | 11 | 111 |
| `bytedance-seed/seed-2-1-turbo` | 0.000 | 0.000 | 0 | 0 | 125 |
| `qwen/qwen3.5-plus-02-15` | 0.000 | 0.000 | 0 | 1 | 125 |

## Boundary accuracy

For ads that match the truth at IoU >= 0.5, how far off were the predicted start and end timestamps? Lower is better. A model can hit F1 cleanly while still being 20s off on every boundary. Bad for any pipeline that cuts the audio.

MAE is size of the miss; bias is its direction (mean of predicted minus truth). A negative start bias or positive end bias means the cut extends past the ad and eats surrounding content; the opposite signs mean ad audio is left in. MinusPod cuts what the model flags, so a model whose bias points outward over-cuts even when its MAE looks acceptable. Bias near zero with a large MAE means the misses are random rather than systematic.

| Model | Start MAE (s) | End MAE (s) | Start bias (s) | End bias (s) |
|---|---:|---:|---:|---:|
| `qwen/qwen3.8-flash` | 1.45 | 0.49 | -1.45 | -0.49 |
| `claude-fable-5-1` | 2.54 | 2.78 | +0.21 | -1.19 |
| `claude-opus-5` | 5.43 | 1.75 | -2.67 | -0.71 |
| `claude-opus-5-5` | 4.24 | 4.12 | +0.06 | -2.53 |
| `claude-opus-4-8` | 5.36 | 3.23 | -1.51 | -1.91 |
| `claude-sonnet-5` | 3.84 | 4.82 | -0.83 | -3.64 |
| `deepseek/deepseek-v4-flash` | 7.19 | 1.68 | -1.50 | -0.96 |
| `x-ai/grok-4.3` | 4.80 | 4.14 | -2.04 | -3.24 |
| `google/gemini-3.1-flash-lite` | 5.94 | 3.06 | -2.91 | -1.29 |
| `claude-haiku-4-5-20251001` | 4.65 | 4.47 | -1.64 | -3.56 |
| `google/gemini-3.5-flash` | 5.35 | 4.11 | -2.60 | -3.20 |
| `qwen/qwen3-8b` | 7.68 | 2.94 | -3.40 | +2.18 |
| `google/gemini-3.5-flash-lite` | 7.27 | 4.57 | -3.55 | -3.53 |
| `gemma4:e4b` | 3.53 | 8.91 | -0.26 | -7.24 |
| `qwen/qwen3.7-flash` | 8.99 | 3.90 | -6.23 | -3.72 |
| `mistralai/mistral-small-2603` | 8.72 | 5.37 | -5.96 | -1.64 |
| `openai/gpt-oss-120b` | 9.45 | 6.91 | -4.46 | -6.00 |
| `microsoft/phi-4` | 6.48 | 10.92 | +3.02 | -10.01 |

## Confidence calibration

Models include a self-reported `confidence` on each detected ad. A well-calibrated model should be right ~95% of the time when it claims 0.95 confidence. The table below bins each model's predictions and shows the actual hit rate (fraction that were true positives at IoU >= 0.5). A bin near 1.0 is well-calibrated; a low number with a high count means the model is overconfident.

| Model | 0.00-0.70 | 0.70-0.90 | 0.90-0.95 | 0.95-0.99 | 0.99+ | total |
|---|---:|---:|---:|---:|---:|---:|
| `claude-fable-5-1` | -- | 0.25 (n=16) | -- | 0.89 (n=131) | -- | 147 |
| `claude-haiku-4-5-20251001` | -- | 0.25 (n=4) | -- | 0.78 (n=152) | -- | 156 |
| `claude-opus-4-8` | -- | 0.15 (n=13) | -- | 0.81 (n=141) | -- | 154 |
| `claude-opus-5` | -- | 0.07 (n=15) | -- | 0.90 (n=126) | -- | 141 |
| `claude-opus-5-5` | -- | 0.00 (n=19) | -- | 0.84 (n=138) | -- | 157 |
| `claude-sonnet-5` | 0.00 (n=1) | 0.22 (n=18) | -- | 0.83 (n=128) | -- | 147 |
| `deepseek/deepseek-v4-flash` | -- | 0.25 (n=8) | -- | 0.82 (n=107) | -- | 115 |
| `gemma4:e4b` | -- | 0.00 (n=2) | -- | 0.64 (n=163) | -- | 165 |
| `google/gemini-3.1-flash-lite` | -- | -- | -- | 0.76 (n=145) | -- | 145 |
| `google/gemini-3.5-flash` | -- | -- | -- | 0.80 (n=145) | -- | 145 |
| `google/gemini-3.5-flash-lite` | -- | 0.00 (n=3) | -- | 0.74 (n=151) | -- | 154 |
| `microsoft/phi-4` | -- | 0.00 (n=4) | -- | 0.66 (n=175) | -- | 179 |
| `mistralai/mistral-small-2603` | -- | -- | -- | 0.70 (n=152) | -- | 152 |
| `openai/gpt-oss-120b` | -- | -- | -- | 0.72 (n=145) | -- | 145 |
| `qwen/qwen3-8b` | -- | 0.40 (n=15) | -- | 0.64 (n=76) | -- | 91 |
| `qwen/qwen3.5-plus-02-15` | -- | -- | -- | 0.00 (n=1) | -- | 1 |
| `qwen/qwen3.7-flash` | -- | 0.00 (n=6) | -- | 0.79 (n=139) | -- | 145 |
| `qwen/qwen3.8-flash` | 0.00 (n=1) | 0.00 (n=6) | -- | 0.78 (n=18) | -- | 25 |
| `x-ai/grok-4.3` | -- | 0.00 (n=2) | -- | 0.77 (n=151) | -- | 153 |

See `report_assets-segmentation-segment_ids/calibration.svg` for the visual reliability diagram.

## Latency tail

Median latency hides outliers. p99 and max are what determines queue depth and worst-case user wait. For OpenRouter-routed models the tail also reflects upstream provider load, not just model compute.

| Model | p50 | p90 | p95 | p99 | max |
|---|---:|---:|---:|---:|---:|
| `google/gemini-3.5-flash-lite` | 1.35s | 1.87s | 2.01s | 2.32s | 2.50s |
| `google/gemini-3.1-flash-lite` | 1.83s | 2.62s | 3.03s | 5.15s | 10.71s |
| `mistralai/mistral-small-2603` | 3.92s | 11.20s | 14.81s | 24.27s | 28.03s |
| `claude-opus-5-5` | 4.13s | 64.01s | 64.91s | 162.51s | 243.91s |
| `claude-fable-5-1` | 5.36s | 63.93s | 65.81s | 146.83s | 245.00s |
| `claude-opus-5` | 5.54s | 64.96s | 66.61s | 110.30s | 188.95s |
| `microsoft/phi-4` | 7.73s | 11.34s | 12.43s | 15.50s | 118.40s |
| `x-ai/grok-4.3` | 7.88s | 11.76s | 12.50s | 16.68s | 19.85s |
| `openai/gpt-oss-120b` | 9.13s | 47.82s | 90.31s | 230.98s | 284.82s |
| `google/gemini-3.5-flash` | 10.44s | 16.35s | 20.63s | 22.92s | 28.64s |
| `claude-opus-4-8` | 10.55s | 63.81s | 71.58s | 186.93s | 196.53s |
| `gemma4:e4b` | 13.29s | 23.36s | 25.52s | 30.41s | 31.89s |
| `claude-sonnet-5` | 26.56s | 61.20s | 76.53s | 205.73s | 249.45s |
| `qwen/qwen3-8b` | 31.22s | 60.93s | 82.10s | 104.66s | 139.87s |
| `deepseek/deepseek-v4-flash` | 32.84s | 252.16s | 261.32s | 272.85s | 366.50s |
| `qwen/qwen3.7-flash` | 52.83s | 73.55s | 79.41s | 91.16s | 94.10s |
| `bytedance-seed/seed-2-1-turbo` | 64.92s | 69.42s | 71.75s | 73.57s | 78.28s |
| `qwen/qwen3.8-flash` | 67.14s | 77.91s | 84.09s | 96.05s | 108.38s |
| `qwen/qwen3.5-plus-02-15` | 70.68s | 70.95s | 71.02s | 71.34s | 71.50s |
| `claude-haiku-4-5-20251001` | 71.38s | 152.84s | 166.69s | 323.72s | 440.29s |

## Output token efficiency

How many output tokens the model spent per detected ad. Lower is more concise (the model finds an ad and returns the JSON). Higher means the model is producing a lot of text the parser will discard, which costs you whether or not the answer is right.

| Model | Total output tokens | Ads detected | Tokens / ad | Cost / TP |
|---|---:|---:|---:|---:|
| `claude-opus-5-5` | 32,988 | 394 | 84 | $0.0067 |
| `claude-fable-5-1` | 35,823 | 403 | 89 | $0.0165 |
| `claude-opus-4-8` | 40,107 | 449 | 89 | $0.0087 |
| `claude-sonnet-5` | 35,069 | 346 | 101 | $0.0036 |
| `claude-opus-5` | 32,185 | 313 | 103 | $0.0085 |
| `google/gemini-3.5-flash-lite` | 54,976 | 469 | 117 | $0.0007 |
| `claude-haiku-4-5-20251001` | 52,973 | 415 | 128 | $0.0018 |
| `google/gemini-3.1-flash-lite` | 69,232 | 417 | 166 | $0.0005 |
| `mistralai/mistral-small-2603` | 75,200 | 393 | 191 | $0.0003 |
| `microsoft/phi-4` | 121,122 | 495 | 245 | $0.0001 |
| `x-ai/grok-4.3` | 236,465 | 450 | 525 | $0.0027 |
| `openai/gpt-oss-120b` | 284,892 | 364 | 783 | $0.0001 |
| `gemma4:e4b` | 392,648 | 450 | 873 | $0.0000 |
| `google/gemini-3.5-flash` | 462,963 | 388 | 1193 | $0.0092 |
| `qwen/qwen3-8b` | 437,875 | 199 | 2200 | $0.0010 |
| `qwen/qwen3.7-flash` | 1,108,593 | 364 | 3046 | $0.0003 |
| `deepseek/deepseek-v4-flash` | 779,874 | 251 | 3107 | $0.0023 |
| `qwen/qwen3.8-flash` | 866,071 | 43 | 20141 | $0.0075 |
| `qwen/qwen3.5-plus-02-15` | 961,920 | 1 | 961920 | n/a |

## Cost breakdown (input vs output)

Where each model's per-episode dollars go, at the same pricing snapshot as every other table. Every model reads the same transcripts, so the input side varies only with the provider's input price. The output side varies with how much the model writes: a high output share on a modest total usually means reasoning tokens, and a model with a low per-token price can still land mid-table by writing thousands of them. Failed calls are excluded, same as the cost column everywhere else.

| Model | Cost / episode | Input | Output | Output share |
|---|---:|---:|---:|---:|
| `claude-fable-5-1` | $1.9845 | $1.6263 | $0.3582 | 18% |
| `google/gemini-3.5-flash` | $1.0715 | $0.2382 | $0.8333 | 78% |
| `claude-opus-4-8` | $1.0137 | $0.8132 | $0.2005 | 20% |
| `claude-opus-5` | $0.9741 | $0.8132 | $0.1609 | 17% |
| `claude-opus-5-5` | $0.7825 | $0.6505 | $0.1320 | 17% |
| `bytedance-seed/seed-2-1-turbo` | $0.5122 | $0.0740 | $0.4383 | 86% |
| `claude-sonnet-5` | $0.3954 | $0.3253 | $0.0701 | 18% |
| `qwen/qwen3.5-plus-02-15` | $0.3407 | $0.0406 | $0.3001 | 88% |
| `x-ai/grok-4.3` | $0.3117 | $0.1935 | $0.1182 | 38% |
| `claude-haiku-4-5-20251001` | $0.2156 | $0.1626 | $0.0530 | 25% |
| `deepseek/deepseek-v4-flash` | $0.2042 | $0.0046 | $0.1996 | 98% |
| `qwen/qwen3.8-flash` | $0.1051 | $0.0237 | $0.0814 | 77% |
| `google/gemini-3.5-flash-lite` | $0.0752 | $0.0477 | $0.0275 | 37% |
| `google/gemini-3.1-flash-lite` | $0.0587 | $0.0380 | $0.0208 | 35% |
| `qwen/qwen3-8b` | $0.0576 | $0.0178 | $0.0398 | 69% |
| `qwen/qwen3.7-flash` | $0.0335 | $0.0047 | $0.0288 | 86% |
| `mistralai/mistral-small-2603` | $0.0323 | $0.0233 | $0.0090 | 28% |
| `openai/gpt-oss-120b` | $0.0152 | $0.0055 | $0.0097 | 64% |
| `microsoft/phi-4` | $0.0139 | $0.0105 | $0.0034 | 24% |

## Trial variance (determinism check)

All trials run at temperature 0.0. If a model produces stable output you'd expect the F1 stdev across trials to be near zero. Higher numbers mean the model is non-deterministic even at temp=0. That's fine to know, but means you cannot trust a single trial's number for that model.

| Model | Mean F1 stdev across episodes | Highest single-episode stdev |
|---|---:|---:|
| `claude-fable-5-1` | 0.0226 | 0.0609 |
| `claude-opus-5` | 0.0262 | 0.0730 |
| `claude-haiku-4-5-20251001` | 0.0339 | 0.0730 |
| `google/gemini-3.5-flash` | 0.0343 | 0.1461 |
| `x-ai/grok-4.3` | 0.0507 | 0.1193 |
| `claude-opus-4-8` | 0.0489 | 0.1135 |
| `claude-opus-5-5` | 0.0505 | 0.0894 |
| `qwen/qwen3.7-flash` | 0.0416 | 0.2236 |
| `claude-sonnet-5` | 0.0595 | 0.1372 |
| `google/gemini-3.5-flash-lite` | 0.0305 | 0.1369 |
| `google/gemini-3.1-flash-lite` | 0.0479 | 0.1342 |
| `microsoft/phi-4` | 0.0165 | 0.0596 |
| `openai/gpt-oss-120b` | 0.1541 | 0.2681 |
| `deepseek/deepseek-v4-flash` | 0.0990 | 0.1491 |
| `mistralai/mistral-small-2603` | 0.0261 | 0.0782 |
| `gemma4:e4b` | 0.0713 | 0.1826 |
| `qwen/qwen3-8b` | 0.1391 | 0.2074 |
| `qwen/qwen3.8-flash` | 0.1318 | 0.2594 |
| `bytedance-seed/seed-2-1-turbo` | 0.0000 | 0.0000 |
| `qwen/qwen3.5-plus-02-15` | 0.0000 | 0.0000 |

## Cross-model agreement

For each of the 47 (episode, window, trial-equivalent) entries, how many of the 20 active models predicted at least one ad? High-agreement windows are unambiguous ads (or unambiguously not ads). Low-agreement windows are where individual models disagree, and are candidates for ensemble voting if you want a cheap accuracy boost.

| Models predicting an ad | Window count | Share |
|---:|---:|---:|
| 0 of 20 | 11 | 23.4% |
| 1 of 20 | 1 | 2.1% |
| 2 of 20 | 2 | 4.3% |
| 3 of 20 | 1 | 2.1% |
| 4 of 20 | 1 | 2.1% |
| 7 of 20 | 1 | 2.1% |
| 15 of 20 | 1 | 2.1% |
| 16 of 20 | 4 | 8.5% |
| 17 of 20 | 19 | 40.4% |
| 18 of 20 | 5 | 10.6% |
| 19 of 20 | 1 | 2.1% |

Read this as: rows near the top are windows where the field disagrees (most models said no, a few said yes, usually false positives); rows near the bottom are windows where the field broadly agrees (typical of clear sponsor reads).

### Per-model alignment with consensus

Same data, viewed per model. For each window, the **majority** is whether more than half of the 20 active models flagged an ad. Then for each model: did it vote with the majority or against it? Four buckets:

- **with-yes**: this model voted yes, majority also voted yes (likely true positive)
- **with-no**: this model voted no, majority also voted no (likely true negative)
- **broke-yes**: this model voted yes, majority voted no (likely false positive / hallucination)
- **broke-no**: this model voted no, majority voted yes (likely missed real ad)

Alignment rate is `(with-yes + with-no) / total`. High alignment means the model tracks the consensus; low alignment means it disagrees often, which could be brilliance or noise depending on whether its disagreements are also where its F1 wins or loses.

| Model | with-yes | with-no | broke-yes | broke-no | Alignment |
|---|---:|---:|---:|---:|---:|
| `claude-fable-5-1` | 30 | 17 | 0 | 0 | 100.0% |
| `claude-opus-4-8` | 30 | 17 | 0 | 0 | 100.0% |
| `claude-opus-5` | 30 | 17 | 0 | 0 | 100.0% |
| `claude-sonnet-5` | 30 | 17 | 0 | 0 | 100.0% |
| `claude-haiku-4-5-20251001` | 30 | 16 | 1 | 0 | 97.9% |
| `gemma4:e4b` | 30 | 16 | 1 | 0 | 97.9% |
| `google/gemini-3.1-flash-lite` | 30 | 16 | 1 | 0 | 97.9% |
| `mistralai/mistral-small-2603` | 30 | 16 | 1 | 0 | 97.9% |
| `qwen/qwen3.7-flash` | 30 | 16 | 1 | 0 | 97.9% |
| `x-ai/grok-4.3` | 30 | 16 | 1 | 0 | 97.9% |
| `claude-opus-5-5` | 30 | 15 | 2 | 0 | 95.7% |
| `deepseek/deepseek-v4-flash` | 28 | 17 | 0 | 2 | 95.7% |
| `google/gemini-3.5-flash` | 30 | 15 | 2 | 0 | 95.7% |
| `microsoft/phi-4` | 29 | 16 | 1 | 1 | 95.7% |
| `openai/gpt-oss-120b` | 30 | 15 | 2 | 0 | 95.7% |
| `google/gemini-3.5-flash-lite` | 30 | 14 | 3 | 0 | 93.6% |
| `qwen/qwen3-8b` | 24 | 14 | 3 | 6 | 80.9% |
| `qwen/qwen3.8-flash` | 9 | 17 | 0 | 21 | 55.3% |
| `qwen/qwen3.5-plus-02-15` | 1 | 17 | 0 | 29 | 38.3% |
| `bytedance-seed/seed-2-1-turbo` | 0 | 17 | 0 | 30 | 36.2% |

### Windows flagged with no truth ad

The other side of the histogram: windows the ground truth marks ad-free, ranked by how many of the 20 models flagged them anyway (in at least one trial). A window near the top is either content that genuinely resembles an ad, which is what precision-focused validator rules should train against, or a spot the truth file missed. Either way these are the first windows worth a manual re-listen; on a corpus this size a single mislabeled window moves scores. No-ad control episodes are included and tagged.

| Episode | Window | Span | Models flagging |
|---|---:|---|---:|
| `ep-it-s-a-thing-e339179dfad6` | 2 | 840-1440s | 7 of 20 |
| `ep-daily-gist-chicago-70a82fe93a5c` | 2 | 840-1271s | 4 of 20 |
| `ep-ai-cloud-essentials-e8dc897fbd6b` (no-ad control) | 0 | 0-600s | 3 of 20 |
| `ep-crime-junkie-8ce498f299d7` | 1 | 420-1020s | 2 of 20 |
| `ep-daily-tech-news-show-b576979e1fe8` | 2 | 840-1440s | 2 of 20 |

## Detection rate by ad characteristic

Aggregate detection rates often hide systematic blind spots. Below: for each model, what fraction of truth ads in each bucket were detected (matched at IoU >= 0.5).

### By ad length

Truth ads bucketed by duration: short (<30s), medium (30-90s), long (>=90s). Cell values are detection rate (fraction of truth ads in that bucket the model caught), with the sample size `n` so a misleading 1.00 on a 2-ad bucket doesn't get over-weighted. Models that systematically miss short ads usually fail on network-inserted brand-tagline spots; missing long ads is rarer and usually means the model gave up before processing the full window.

| Model | long (>=90s) | medium (30-90s) | short (<30s) |
|---|---:|---:|---:|
| `bytedance-seed/seed-2-1-turbo` | 0.00 (n=60) | 0.00 (n=50) | 0.00 (n=15) |
| `claude-fable-5-1` | 0.92 (n=60) | 1.00 (n=50) | 1.00 (n=15) |
| `claude-haiku-4-5-20251001` | 0.92 (n=60) | 1.00 (n=50) | 1.00 (n=15) |
| `claude-opus-4-8` | 0.92 (n=60) | 0.94 (n=50) | 0.93 (n=15) |
| `claude-opus-5` | 0.92 (n=60) | 1.00 (n=50) | 0.67 (n=15) |
| `claude-opus-5-5` | 0.92 (n=60) | 1.00 (n=50) | 0.73 (n=15) |
| `claude-sonnet-5` | 0.80 (n=60) | 0.94 (n=50) | 1.00 (n=15) |
| `deepseek/deepseek-v4-flash` | 0.67 (n=60) | 0.76 (n=50) | 0.80 (n=15) |
| `gemma4:e4b` | 0.92 (n=60) | 0.74 (n=50) | 0.87 (n=15) |
| `google/gemini-3.1-flash-lite` | 0.88 (n=60) | 0.96 (n=50) | 0.60 (n=15) |
| `google/gemini-3.5-flash` | 0.90 (n=60) | 0.94 (n=50) | 1.00 (n=15) |
| `google/gemini-3.5-flash-lite` | 0.92 (n=60) | 0.94 (n=50) | 0.67 (n=15) |
| `microsoft/phi-4` | 1.00 (n=60) | 0.82 (n=50) | 1.00 (n=15) |
| `mistralai/mistral-small-2603` | 0.92 (n=60) | 0.94 (n=50) | 0.33 (n=15) |
| `openai/gpt-oss-120b` | 0.78 (n=60) | 0.92 (n=50) | 0.73 (n=15) |
| `qwen/qwen3-8b` | 0.43 (n=60) | 0.46 (n=50) | 0.40 (n=15) |
| `qwen/qwen3.5-plus-02-15` | 0.00 (n=60) | 0.00 (n=50) | 0.00 (n=15) |
| `qwen/qwen3.7-flash` | 0.92 (n=60) | 0.98 (n=50) | 0.40 (n=15) |
| `qwen/qwen3.8-flash` | 0.15 (n=60) | 0.06 (n=50) | 0.13 (n=15) |
| `x-ai/grok-4.3` | 0.92 (n=60) | 0.96 (n=50) | 0.87 (n=15) |

### By ad position

Truth ads bucketed by where they fall in the episode: pre-roll (first 10%), mid-roll (10-90%), post-roll (last 10%). Cell values are the same detection-rate-with-`n` format as ad length. A common failure pattern in our data: most models detect pre-roll and mid-roll reliably and miss post-roll, because the prompt windows near the end often catch the model mid-reasoning or with fewer transition phrases to anchor on.

| Model | pre-roll (<10%) | mid-roll (10-90%) | post-roll (>90%) |
|---|---:|---:|---:|
| `bytedance-seed/seed-2-1-turbo` | 0.00 (n=35) | 0.00 (n=60) | 0.00 (n=30) |
| `claude-fable-5-1` | 1.00 (n=35) | 1.00 (n=60) | 0.83 (n=30) |
| `claude-haiku-4-5-20251001` | 1.00 (n=35) | 1.00 (n=60) | 0.83 (n=30) |
| `claude-opus-4-8` | 1.00 (n=35) | 0.98 (n=60) | 0.73 (n=30) |
| `claude-opus-5` | 1.00 (n=35) | 0.92 (n=60) | 0.83 (n=30) |
| `claude-opus-5-5` | 1.00 (n=35) | 0.93 (n=60) | 0.83 (n=30) |
| `claude-sonnet-5` | 1.00 (n=35) | 0.88 (n=60) | 0.73 (n=30) |
| `deepseek/deepseek-v4-flash` | 0.63 (n=35) | 0.78 (n=60) | 0.70 (n=30) |
| `gemma4:e4b` | 0.86 (n=35) | 0.97 (n=60) | 0.57 (n=30) |
| `google/gemini-3.1-flash-lite` | 0.94 (n=35) | 0.88 (n=60) | 0.80 (n=30) |
| `google/gemini-3.5-flash` | 0.97 (n=35) | 1.00 (n=60) | 0.73 (n=30) |
| `google/gemini-3.5-flash-lite` | 1.00 (n=35) | 0.92 (n=60) | 0.73 (n=30) |
| `microsoft/phi-4` | 0.86 (n=35) | 1.00 (n=60) | 0.87 (n=30) |
| `mistralai/mistral-small-2603` | 0.77 (n=35) | 0.92 (n=60) | 0.83 (n=30) |
| `openai/gpt-oss-120b` | 0.94 (n=35) | 0.80 (n=60) | 0.77 (n=30) |
| `qwen/qwen3-8b` | 0.37 (n=35) | 0.52 (n=60) | 0.37 (n=30) |
| `qwen/qwen3.5-plus-02-15` | 0.00 (n=35) | 0.00 (n=60) | 0.00 (n=30) |
| `qwen/qwen3.7-flash` | 1.00 (n=35) | 0.85 (n=60) | 0.80 (n=30) |
| `qwen/qwen3.8-flash` | 0.14 (n=35) | 0.15 (n=60) | 0.00 (n=30) |
| `x-ai/grok-4.3` | 1.00 (n=35) | 0.97 (n=60) | 0.77 (n=30) |

## Quick Comparison

One row per model, one column per episode. The headline columns (`F1`, `Cost/ep`, `p50`) summarize across all episodes; the per-episode columns let you see whether a model's average hides wide swings (a model that scores well overall might still bomb on a specific genre). The right-most `F1 stdev` column averages the per-trial standard deviations across episodes; high values mean the model isn't deterministic at temperature 0.0, so its single-trial F1 number is noisy. `Moderation blocked` is the share of attempted calls the provider refused on content grounds; those windows never reach scoring, so any non-zero value means that row's F1 was computed on a subset of the corpus and is not comparable to a row at `-`.

| Model | F1 | Cost/ep | p50 | ep-andy-and-ari-e774e8022fab | ep-crime-junkie-8ce498f299d7 | ep-daily-gist-chicago-70a82fe93a5c | ep-daily-tech-news-show-b576979e1fe8 | ep-daily-tech-news-show-c1904b8605f7 | ep-drink-champs-30c9a2d49f13 | ep-glt1412515089-373d5ba5007b | ep-it-s-a-thing-e339179dfad6 | ep-on-air-with-dan-and-alex2-574e4f303730 | ep-security-now-audio-2850b24903b2 | ep-the-brilliant-idiots-0bb9bf634c8e | ep-the-tim-dillon-show-f62bd5fa1cfe | ep-tosh-show-5f6894439bb6 | ep-ai-cloud-essentials-e8dc897fbd6b (no-ad) | ep-oxide-and-friends-ce789ff5b62e (no-ad) | F1 stdev | Moderation blocked |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| `claude-fable-5-1` | 0.891 | $1.9845 | 5.4s | - | 0.933 | 1.000 | 0.956 | 0.640 | - | - | 0.800 | - | - | - | 1.000 | 0.909 | PASS | - | 0.023 | - |
| `claude-opus-5` | 0.874 | $0.9741 | 5.5s | - | 0.978 | 1.000 | 0.956 | 0.667 | - | - | 0.720 | - | - | - | 1.000 | 0.800 | PASS | - | 0.026 | - |
| `claude-haiku-4-5-20251001` | 0.865 | $0.2156 | 71.4s | - | 0.956 | 1.000 | 0.871 | 0.613 | - | - | 0.720 | - | - | - | 1.000 | 0.894 | PASS | - | 0.034 | - |
| `google/gemini-3.5-flash` | 0.857 | $1.0715 | 10.4s | - | 1.000 | 1.000 | 0.971 | 0.613 | - | - | 0.507 | - | - | - | 1.000 | 0.909 | PASS | - | 0.034 | - |
| `x-ai/grok-4.3` | 0.853 | $0.3117 | 7.9s | - | 0.911 | 1.000 | 0.764 | 0.613 | - | - | 0.813 | - | - | - | 1.000 | 0.865 | PASS | - | 0.051 | - |
| `claude-opus-4-8` | 0.846 | $1.0137 | 10.6s | - | 0.933 | 1.000 | 0.828 | 0.613 | - | - | 0.720 | - | - | - | 1.000 | 0.827 | PASS | - | 0.049 | - |
| `claude-opus-5-5` | 0.839 | $0.7825 | 4.1s | - | 0.978 | 0.960 | 0.916 | 0.600 | - | - | 0.674 | - | - | - | 1.000 | 0.748 | PASS | - | 0.050 | - |
| `qwen/qwen3.7-flash` | 0.831 | $0.0335 | 52.8s | - | 0.927 | 0.600 | 0.889 | 0.600 | - | - | 1.000 | - | - | - | 1.000 | 0.800 | FAIL (1 FP) | - | 0.042 | - |
| `claude-sonnet-5` | 0.826 | $0.3954 | 26.6s | - | 0.876 | 1.000 | 0.780 | 0.600 | - | - | 0.773 | - | - | - | 0.900 | 0.850 | PASS | - | 0.059 | - |
| `google/gemini-3.5-flash-lite` | 0.817 | $0.0752 | 1.4s | - | 0.850 | 0.800 | 0.889 | 0.627 | - | - | 0.800 | - | - | - | 1.000 | 0.756 | FAIL (1 FP) | - | 0.030 | - |
| `google/gemini-3.1-flash-lite` | 0.816 | $0.0587 | 1.8s | - | 1.000 | 0.740 | 0.861 | 0.580 | - | - | 0.800 | - | - | - | 1.000 | 0.733 | PASS | - | 0.048 | - |
| `microsoft/phi-4` | 0.780 | $0.0139 | 7.7s | - | 0.889 | 1.000 | 0.693 | 0.679 | - | - | 0.667 | - | - | - | 0.750 | 0.782 | PASS | - | 0.016 | - |
| `openai/gpt-oss-120b` | 0.779 | $0.0152 | 9.1s | - | 0.877 | 0.920 | 0.899 | 0.642 | - | - | 0.627 | - | - | - | 0.772 | 0.716 | PASS | - | 0.154 | - |
| `deepseek/deepseek-v4-flash` | 0.758 | $0.2042 | 32.8s | - | 0.886 | 0.933 | 0.434 | 0.533 | - | - | 0.853 | - | - | - | 0.950 | 0.716 | PASS | - | 0.099 | - |
| `mistralai/mistral-small-2603` | 0.755 | $0.0323 | 3.9s | - | 0.889 | 0.420 | 0.889 | 0.600 | - | - | 0.773 | - | - | - | 0.914 | 0.800 | PASS | - | 0.026 | - |
| `gemma4:e4b` | 0.723 | $0.0000 | 13.3s | - | 0.871 | 1.000 | 0.653 | 0.578 | - | - | 0.300 | - | - | - | 0.806 | 0.851 | PASS | - | 0.071 | - |
| `qwen/qwen3-8b` | 0.491 | $0.0576 | 31.2s | - | 0.672 | 0.567 | 0.429 | 0.457 | - | - | 0.360 | - | - | - | 0.440 | 0.515 | FAIL (1 FP) | - | 0.139 | - |
| `qwen/qwen3.8-flash` | 0.127 | $0.1051 | 67.1s | - | 0.373 | 0.000 | 0.248 | 0.000 | - | - | 0.000 | - | - | - | 0.080 | 0.189 | PASS | - | 0.132 | - |
| `bytedance-seed/seed-2-1-turbo` | 0.000 | $0.5122 | 64.9s | - | 0.000 | 0.000 | 0.000 | 0.000 | - | - | 0.000 | - | - | - | 0.000 | 0.000 | PASS | - | 0.000 | - |
| `qwen/qwen3.5-plus-02-15` | 0.000 | $0.3407 | 70.7s | - | 0.000 | 0.000 | 0.000 | 0.000 | - | - | 0.000 | - | - | - | 0.000 | 0.000 | PASS | - | 0.000 | - |

---

## Detailed Results

### Per-Model Detail

Full per-model profile: F1 averaged across episodes, total cost per episode at current pricing, p50 / p95 latency, JSON compliance, parse-failure rate, the distribution of extraction methods the parser had to use, and verbosity / truncation telemetry. The `Extraction methods` list shows how often each route was hit. `json_array_direct` is the cleanest; the rest are recovery paths. The verbosity row flags models that emit long `reason` fields or run out of token budget mid-response. Ordered by F1 descending so the best performers appear first.

#### `claude-fable-5-1`

- F1 (avg across episodes): **0.891**
- Total cost / episode: **$1.9845**
- p50 / p95 latency: 5.36s / 65.81s
- JSON compliance: 1.00
- JSON mode: native (100% native, 235 calls)
- Parse failure rate: 0.0%
- Extraction methods: `segment_id_direct`: 235
- Verbosity: 0/235 calls over 1024 output tokens (0.0%); 0 hit max_tokens (0.0%); 0 salvaged from truncated JSON (0.0%)
- Segment category named on 403/403 detections (100%); the rest stay uncategorized (resolver: production)

#### `claude-opus-5`

- F1 (avg across episodes): **0.874**
- Total cost / episode: **$0.9741**
- p50 / p95 latency: 5.54s / 66.61s
- JSON compliance: 1.00
- JSON mode: native (100% native, 235 calls)
- Parse failure rate: 0.0%
- Extraction methods: `segment_id_direct`: 235
- Verbosity: 0/235 calls over 1024 output tokens (0.0%); 0 hit max_tokens (0.0%); 0 salvaged from truncated JSON (0.0%)
- Segment category named on 313/313 detections (100%); the rest stay uncategorized (resolver: production)

#### `claude-haiku-4-5-20251001`

- F1 (avg across episodes): **0.865**
- Total cost / episode: **$0.2156**
- p50 / p95 latency: 71.38s / 166.69s
- JSON compliance: 1.00
- JSON mode: native (100% native, 235 calls)
- Parse failure rate: 0.0%
- Extraction methods: `segment_id_direct`: 235
- Verbosity: 0/235 calls over 1024 output tokens (0.0%); 0 hit max_tokens (0.0%); 0 salvaged from truncated JSON (0.0%)
- Segment category named on 415/415 detections (100%); the rest stay uncategorized (resolver: production)

#### `google/gemini-3.5-flash`

- F1 (avg across episodes): **0.857**
- Total cost / episode: **$1.0715**
- p50 / p95 latency: 10.44s / 20.63s
- JSON compliance: 1.00
- JSON mode: native (100% native, 235 calls)
- Parse failure rate: 0.0%
- Extraction methods: `json_object_single_ad_truncated`: 6, `segment_id_direct`: 229
- Verbosity: 196/235 calls over 1024 output tokens (83.4%); 6 hit max_tokens (2.6%); 6 salvaged from truncated JSON (2.6%)
- Segment category named on 388/388 detections (100%); the rest stay uncategorized (resolver: production)

#### `x-ai/grok-4.3`

- F1 (avg across episodes): **0.853**
- Total cost / episode: **$0.3117**
- p50 / p95 latency: 7.88s / 12.50s
- JSON compliance: 1.00
- JSON mode: native (100% native, 235 calls)
- Parse failure rate: 0.0%
- Extraction methods: `segment_id_direct`: 235
- Verbosity: 111/235 calls over 1024 output tokens (47.2%); 0 hit max_tokens (0.0%); 0 salvaged from truncated JSON (0.0%)
- Segment category named on 450/450 detections (100%); the rest stay uncategorized (resolver: production)

#### `claude-opus-4-8`

- F1 (avg across episodes): **0.846**
- Total cost / episode: **$1.0137**
- p50 / p95 latency: 10.55s / 71.58s
- JSON compliance: 1.00
- JSON mode: native (100% native, 235 calls)
- Parse failure rate: 0.0%
- Extraction methods: `segment_id_direct`: 235
- Verbosity: 0/235 calls over 1024 output tokens (0.0%); 0 hit max_tokens (0.0%); 0 salvaged from truncated JSON (0.0%)
- Segment category named on 449/449 detections (100%); the rest stay uncategorized (resolver: production)

#### `claude-opus-5-5`

- F1 (avg across episodes): **0.839**
- Total cost / episode: **$0.7825**
- p50 / p95 latency: 4.13s / 64.91s
- JSON compliance: 1.00
- JSON mode: native (100% native, 235 calls)
- Parse failure rate: 0.0%
- Extraction methods: `segment_id_direct`: 235
- Verbosity: 0/235 calls over 1024 output tokens (0.0%); 0 hit max_tokens (0.0%); 0 salvaged from truncated JSON (0.0%)
- Segment category named on 394/394 detections (100%); the rest stay uncategorized (resolver: production)

#### `qwen/qwen3.7-flash`

- F1 (avg across episodes): **0.831**
- Total cost / episode: **$0.0335**
- p50 / p95 latency: 52.83s / 79.41s
- JSON compliance: 0.99
- JSON mode: native (100% native, 235 calls)
- Parse failure rate: 0.9%
- Extraction methods: `parse_failure`: 2, `segment_id_direct`: 233
- Verbosity: 235/235 calls over 1024 output tokens (100.0%); 4 hit max_tokens (1.7%); 0 salvaged from truncated JSON (0.0%)
- Segment category named on 364/364 detections (100%); the rest stay uncategorized (resolver: production)

#### `claude-sonnet-5`

- F1 (avg across episodes): **0.826**
- Total cost / episode: **$0.3954**
- p50 / p95 latency: 26.56s / 76.53s
- JSON compliance: 1.00
- JSON mode: native (100% native, 235 calls)
- Parse failure rate: 0.0%
- Extraction methods: `segment_id_direct`: 235
- Verbosity: 0/235 calls over 1024 output tokens (0.0%); 0 hit max_tokens (0.0%); 0 salvaged from truncated JSON (0.0%)
- Segment category named on 346/346 detections (100%); the rest stay uncategorized (resolver: production)

#### `google/gemini-3.5-flash-lite`

- F1 (avg across episodes): **0.817**
- Total cost / episode: **$0.0752**
- p50 / p95 latency: 1.35s / 2.01s
- JSON compliance: 1.00
- JSON mode: native (100% native, 235 calls)
- Parse failure rate: 0.0%
- Extraction methods: `segment_id_direct`: 235
- Verbosity: 0/235 calls over 1024 output tokens (0.0%); 0 hit max_tokens (0.0%); 0 salvaged from truncated JSON (0.0%)
- Segment category named on 469/469 detections (100%); the rest stay uncategorized (resolver: production)

#### `google/gemini-3.1-flash-lite`

- F1 (avg across episodes): **0.816**
- Total cost / episode: **$0.0587**
- p50 / p95 latency: 1.83s / 3.03s
- JSON compliance: 0.99
- JSON mode: native (100% native, 235 calls)
- Parse failure rate: 0.0%
- Extraction methods: `json_object_single_ad_truncated`: 10, `segment_id_direct`: 225
- Verbosity: 0/235 calls over 1024 output tokens (0.0%); 0 hit max_tokens (0.0%); 10 salvaged from truncated JSON (4.3%)
- Segment category named on 417/417 detections (100%); the rest stay uncategorized (resolver: production)

#### `microsoft/phi-4`

- F1 (avg across episodes): **0.780**
- Total cost / episode: **$0.0139**
- p50 / p95 latency: 7.73s / 12.43s
- JSON compliance: 1.00
- JSON mode: native (100% native, 235 calls)
- Parse failure rate: 0.0%
- Extraction methods: `segment_id_direct`: 235
- Verbosity: 3/235 calls over 1024 output tokens (1.3%); 1 hit max_tokens (0.4%); 0 salvaged from truncated JSON (0.0%)
- Segment category named on 495/495 detections (100%); the rest stay uncategorized (resolver: production)

#### `openai/gpt-oss-120b`

- F1 (avg across episodes): **0.779**
- Total cost / episode: **$0.0152**
- p50 / p95 latency: 9.13s / 90.31s
- JSON compliance: 1.00
- JSON mode: native (100% native, 235 calls)
- Parse failure rate: 0.0%
- Extraction methods: `segment_id_direct`: 235
- Verbosity: 108/235 calls over 1024 output tokens (46.0%); 10 hit max_tokens (4.3%); 0 salvaged from truncated JSON (0.0%)
- Segment category named on 364/364 detections (100%); the rest stay uncategorized (resolver: production)

#### `deepseek/deepseek-v4-flash`

- F1 (avg across episodes): **0.758**
- Total cost / episode: **$0.2042**
- p50 / p95 latency: 32.84s / 261.32s
- JSON compliance: 0.81
- JSON mode: native (100% native, 235 calls)
- Parse failure rate: 19.1%
- ID-contract misses (fell back to timestamp parse): 13
- Extraction methods: `parse_failure`: 45, `segment_id_direct`: 190
- Verbosity: 160/235 calls over 1024 output tokens (68.1%); 46 hit max_tokens (19.6%); 0 salvaged from truncated JSON (0.0%)
- Segment category named on 251/251 detections (100%); the rest stay uncategorized (resolver: production)

#### `mistralai/mistral-small-2603`

- F1 (avg across episodes): **0.755**
- Total cost / episode: **$0.0323**
- p50 / p95 latency: 3.92s / 14.81s
- JSON compliance: 1.00
- JSON mode: native (100% native, 235 calls)
- Parse failure rate: 0.0%
- Extraction methods: `segment_id_direct`: 235
- Verbosity: 0/235 calls over 1024 output tokens (0.0%); 0 hit max_tokens (0.0%); 0 salvaged from truncated JSON (0.0%)
- Segment category named on 393/393 detections (100%); the rest stay uncategorized (resolver: production)

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

#### `qwen/qwen3-8b`

- F1 (avg across episodes): **0.491**
- Total cost / episode: **$0.0576**
- p50 / p95 latency: 31.22s / 82.10s
- JSON compliance: 0.51
- JSON mode: native (100% native, 235 calls)
- Parse failure rate: 49.4%
- Extraction methods: `json_object_no_ads`: 26, `parse_failure`: 116, `segment_id_direct`: 93
- Verbosity: 189/235 calls over 1024 output tokens (80.4%); 6 hit max_tokens (2.6%); 0 salvaged from truncated JSON (0.0%)
- Segment category named on 199/199 detections (100%); the rest stay uncategorized (resolver: production)

#### `qwen/qwen3.8-flash`

- F1 (avg across episodes): **0.127**
- Total cost / episode: **$0.1051**
- p50 / p95 latency: 67.14s / 84.09s
- JSON compliance: 0.26
- JSON mode: native (100% native, 235 calls)
- Parse failure rate: 73.6%
- Extraction methods: `json_object_single_ad_truncated`: 2, `parse_failure`: 173, `segment_id_direct`: 60
- Verbosity: 230/235 calls over 1024 output tokens (97.9%); 171 hit max_tokens (72.8%); 2 salvaged from truncated JSON (0.9%)
- Segment category named on 43/43 detections (100%); the rest stay uncategorized (resolver: production)

#### `bytedance-seed/seed-2-1-turbo`

- F1 (avg across episodes): **0.000**
- Total cost / episode: **$0.5122**
- p50 / p95 latency: 64.92s / 71.75s
- JSON compliance: 0.06
- JSON mode: native (100% native, 235 calls)
- Parse failure rate: 93.6%
- Extraction methods: `bracket_fallback`: 1, `parse_failure`: 220, `segment_id_direct`: 14
- Verbosity: 219/235 calls over 1024 output tokens (93.2%); 202 hit max_tokens (86.0%); 0 salvaged from truncated JSON (0.0%)

#### `qwen/qwen3.5-plus-02-15`

- F1 (avg across episodes): **0.000**
- Total cost / episode: **$0.3407**
- p50 / p95 latency: 70.68s / 71.02s
- JSON compliance: 0.02
- JSON mode: native (100% native, 235 calls)
- Parse failure rate: 97.9%
- Extraction methods: `parse_failure`: 230, `segment_id_direct`: 5
- Verbosity: 235/235 calls over 1024 output tokens (100.0%); 230 hit max_tokens (97.9%); 0 salvaged from truncated JSON (0.0%)
- Segment category named on 1/1 detections (100%); the rest stay uncategorized (resolver: production)


### Per-Episode Detail

One subsection per episode in the corpus, showing how every model performed on that specific episode. For ad-bearing episodes you see F1 and the stdev across trials (low stdev means stable, high stdev means the model's number on this episode is noisy). For the no-ad episode you see PASS / FAIL on the negative control: PASS = zero false positives across all windows, FAIL = the model flagged something that wasn't an ad, with the count.

#### `ep-ai-cloud-essentials-e8dc897fbd6b`: How Physical AI is Streamlining Engineering

- Podcast: ai-cloud-essentials
- Duration: 16.4 min
- Truth: no-ads episode

| Model | Result | FP count |
|-------|--------|----------|
| `bytedance-seed/seed-2-1-turbo` | PASS | 0 |
| `claude-fable-5-1` | PASS | 0 |
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
| `qwen/qwen3.5-plus-02-15` | PASS | 0 |
| `qwen/qwen3.8-flash` | PASS | 0 |
| `x-ai/grok-4.3` | PASS | 0 |
| `google/gemini-3.5-flash-lite` | FAIL | 1 |
| `qwen/qwen3-8b` | FAIL | 1 |
| `qwen/qwen3.7-flash` | FAIL | 1 |

#### `ep-andy-and-ari-e774e8022fab`: Indiana LOADS UP on the edge before Big Ten play vs Northwestern thanks to court ruling | Oregon at USC Deep DIVE | Trinidad Chambliss, QB1 in NFL Draft? Why the SEC is STEALING Big Ten's TV power

- Podcast: andy-and-ari
- Duration: 75.1 min
- Truth ads: 5

| Model | F1 | F1 stdev |
|-------|----|----------|

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
| `claude-haiku-4-5-20251001` | 0.956 | 0.061 |
| `claude-fable-5-1` | 0.933 | 0.061 |
| `claude-opus-4-8` | 0.933 | 0.061 |
| `qwen/qwen3.7-flash` | 0.927 | 0.068 |
| `x-ai/grok-4.3` | 0.911 | 0.050 |
| `microsoft/phi-4` | 0.889 | 0.000 |
| `mistralai/mistral-small-2603` | 0.889 | 0.000 |
| `deepseek/deepseek-v4-flash` | 0.886 | 0.064 |
| `openai/gpt-oss-120b` | 0.877 | 0.089 |
| `claude-sonnet-5` | 0.876 | 0.137 |
| `gemma4:e4b` | 0.871 | 0.040 |
| `google/gemini-3.5-flash-lite` | 0.850 | 0.137 |
| `qwen/qwen3-8b` | 0.672 | 0.189 |
| `qwen/qwen3.8-flash` | 0.373 | 0.239 |
| `bytedance-seed/seed-2-1-turbo` | 0.000 | 0.000 |
| `qwen/qwen3.5-plus-02-15` | 0.000 | 0.000 |

#### `ep-daily-gist-chicago-70a82fe93a5c`: Suburban apartment market heats up

- Podcast: daily-gist-chicago
- Duration: 21.2 min
- Truth ads: 2

| Model | F1 | F1 stdev |
|-------|----|----------|
| `claude-fable-5-1` | 1.000 | 0.000 |
| `claude-haiku-4-5-20251001` | 1.000 | 0.000 |
| `claude-opus-4-8` | 1.000 | 0.000 |
| `claude-opus-5` | 1.000 | 0.000 |
| `claude-sonnet-5` | 1.000 | 0.000 |
| `gemma4:e4b` | 1.000 | 0.000 |
| `google/gemini-3.5-flash` | 1.000 | 0.000 |
| `microsoft/phi-4` | 1.000 | 0.000 |
| `x-ai/grok-4.3` | 1.000 | 0.000 |
| `claude-opus-5-5` | 0.960 | 0.089 |
| `deepseek/deepseek-v4-flash` | 0.933 | 0.149 |
| `openai/gpt-oss-120b` | 0.920 | 0.110 |
| `google/gemini-3.5-flash-lite` | 0.800 | 0.000 |
| `google/gemini-3.1-flash-lite` | 0.740 | 0.134 |
| `qwen/qwen3.7-flash` | 0.600 | 0.224 |
| `qwen/qwen3-8b` | 0.567 | 0.091 |
| `mistralai/mistral-small-2603` | 0.420 | 0.045 |
| `bytedance-seed/seed-2-1-turbo` | 0.000 | 0.000 |
| `qwen/qwen3.5-plus-02-15` | 0.000 | 0.000 |
| `qwen/qwen3.8-flash` | 0.000 | 0.000 |

#### `ep-daily-tech-news-show-b576979e1fe8`: Motorola Razr Fold is a Noble Competitor to the Galaxy Z Fold 7 - DTNS 5269

- Podcast: daily-tech-news-show
- Duration: 34.6 min
- Truth ads: 4

| Model | F1 | F1 stdev |
|-------|----|----------|
| `google/gemini-3.5-flash` | 0.971 | 0.064 |
| `claude-fable-5-1` | 0.956 | 0.061 |
| `claude-opus-5` | 0.956 | 0.061 |
| `claude-opus-5-5` | 0.916 | 0.085 |
| `openai/gpt-oss-120b` | 0.899 | 0.105 |
| `google/gemini-3.5-flash-lite` | 0.889 | 0.000 |
| `mistralai/mistral-small-2603` | 0.889 | 0.000 |
| `qwen/qwen3.7-flash` | 0.889 | 0.000 |
| `claude-haiku-4-5-20251001` | 0.871 | 0.040 |
| `google/gemini-3.1-flash-lite` | 0.861 | 0.062 |
| `claude-opus-4-8` | 0.828 | 0.114 |
| `claude-sonnet-5` | 0.780 | 0.027 |
| `x-ai/grok-4.3` | 0.764 | 0.096 |
| `microsoft/phi-4` | 0.693 | 0.060 |
| `gemma4:e4b` | 0.653 | 0.087 |
| `deepseek/deepseek-v4-flash` | 0.434 | 0.077 |
| `qwen/qwen3-8b` | 0.429 | 0.130 |
| `qwen/qwen3.8-flash` | 0.248 | 0.246 |
| `bytedance-seed/seed-2-1-turbo` | 0.000 | 0.000 |
| `qwen/qwen3.5-plus-02-15` | 0.000 | 0.000 |

#### `ep-daily-tech-news-show-c1904b8605f7`: Switch 2 Prices Rise, Forecast Drops - DTNS 5265

- Podcast: daily-tech-news-show
- Duration: 38.6 min
- Truth ads: 5

| Model | F1 | F1 stdev |
|-------|----|----------|
| `microsoft/phi-4` | 0.679 | 0.027 |
| `claude-opus-5` | 0.667 | 0.000 |
| `openai/gpt-oss-120b` | 0.642 | 0.166 |
| `claude-fable-5-1` | 0.640 | 0.037 |
| `google/gemini-3.5-flash-lite` | 0.627 | 0.037 |
| `claude-haiku-4-5-20251001` | 0.613 | 0.030 |
| `claude-opus-4-8` | 0.613 | 0.030 |
| `google/gemini-3.5-flash` | 0.613 | 0.030 |
| `x-ai/grok-4.3` | 0.613 | 0.030 |
| `claude-opus-5-5` | 0.600 | 0.000 |
| `claude-sonnet-5` | 0.600 | 0.000 |
| `mistralai/mistral-small-2603` | 0.600 | 0.000 |
| `qwen/qwen3.7-flash` | 0.600 | 0.000 |
| `google/gemini-3.1-flash-lite` | 0.580 | 0.045 |
| `gemma4:e4b` | 0.578 | 0.030 |
| `deepseek/deepseek-v4-flash` | 0.533 | 0.075 |
| `qwen/qwen3-8b` | 0.457 | 0.096 |
| `bytedance-seed/seed-2-1-turbo` | 0.000 | 0.000 |
| `qwen/qwen3.5-plus-02-15` | 0.000 | 0.000 |
| `qwen/qwen3.8-flash` | 0.000 | 0.000 |

#### `ep-drink-champs-30c9a2d49f13`: Episode 501 w/ Warren Sapp

- Podcast: drink-champs
- Duration: 258.6 min
- Truth ads: 9

| Model | F1 | F1 stdev |
|-------|----|----------|

#### `ep-glt1412515089-373d5ba5007b`: #2496 - Julia Mossbridge

- Podcast: glt1412515089
- Duration: 165.3 min
- Truth ads: 4

| Model | F1 | F1 stdev |
|-------|----|----------|

#### `ep-it-s-a-thing-e339179dfad6`: SOUP shots - It's a Thing 418

- Podcast: it-s-a-thing
- Duration: 26.7 min
- Truth ads: 2

| Model | F1 | F1 stdev |
|-------|----|----------|
| `qwen/qwen3.7-flash` | 1.000 | 0.000 |
| `deepseek/deepseek-v4-flash` | 0.853 | 0.145 |
| `x-ai/grok-4.3` | 0.813 | 0.119 |
| `claude-fable-5-1` | 0.800 | 0.000 |
| `google/gemini-3.1-flash-lite` | 0.800 | 0.000 |
| `google/gemini-3.5-flash-lite` | 0.800 | 0.000 |
| `claude-sonnet-5` | 0.773 | 0.060 |
| `mistralai/mistral-small-2603` | 0.773 | 0.060 |
| `claude-haiku-4-5-20251001` | 0.720 | 0.073 |
| `claude-opus-4-8` | 0.720 | 0.073 |
| `claude-opus-5` | 0.720 | 0.073 |
| `claude-opus-5-5` | 0.674 | 0.081 |
| `microsoft/phi-4` | 0.667 | 0.000 |
| `openai/gpt-oss-120b` | 0.627 | 0.268 |
| `google/gemini-3.5-flash` | 0.507 | 0.146 |
| `qwen/qwen3-8b` | 0.360 | 0.207 |
| `gemma4:e4b` | 0.300 | 0.183 |
| `bytedance-seed/seed-2-1-turbo` | 0.000 | 0.000 |
| `qwen/qwen3.5-plus-02-15` | 0.000 | 0.000 |
| `qwen/qwen3.8-flash` | 0.000 | 0.000 |

#### `ep-on-air-with-dan-and-alex2-574e4f303730`: Ryanair Wants Alcohol Bans, Emirates' $6.8B Record Profit & Buying Spirit Airlines?!

- Podcast: on-air-with-dan-and-alex2
- Duration: 58.1 min
- Truth ads: 2

| Model | F1 | F1 stdev |
|-------|----|----------|

#### `ep-oxide-and-friends-ce789ff5b62e`: Mechanical Engineering at Oxide [chapter images]

- Podcast: oxide-and-friends
- Duration: 84.5 min
- Truth: no-ads episode

| Model | Result | FP count |
|-------|--------|----------|

#### `ep-security-now-audio-2850b24903b2`: SN 1077: A Browser AI API? - End of Bug Bounties?

- Podcast: security-now-audio
- Duration: 156.2 min
- Truth ads: 7

| Model | F1 | F1 stdev |
|-------|----|----------|

#### `ep-the-brilliant-idiots-0bb9bf634c8e`: Class Rank

- Podcast: the-brilliant-idiots
- Duration: 119.9 min
- Truth ads: 3

| Model | F1 | F1 stdev |
|-------|----|----------|

#### `ep-the-tim-dillon-show-f62bd5fa1cfe`: 495 - Hantavirus Cruise & iPad Babies

- Podcast: the-tim-dillon-show
- Duration: 80.1 min
- Truth ads: 6

| Model | F1 | F1 stdev |
|-------|----|----------|
| `claude-fable-5-1` | 1.000 | 0.000 |
| `claude-haiku-4-5-20251001` | 1.000 | 0.000 |
| `claude-opus-4-8` | 1.000 | 0.000 |
| `claude-opus-5` | 1.000 | 0.000 |
| `claude-opus-5-5` | 1.000 | 0.000 |
| `google/gemini-3.1-flash-lite` | 1.000 | 0.000 |
| `google/gemini-3.5-flash` | 1.000 | 0.000 |
| `google/gemini-3.5-flash-lite` | 1.000 | 0.000 |
| `qwen/qwen3.7-flash` | 1.000 | 0.000 |
| `x-ai/grok-4.3` | 1.000 | 0.000 |
| `deepseek/deepseek-v4-flash` | 0.950 | 0.112 |
| `mistralai/mistral-small-2603` | 0.914 | 0.078 |
| `claude-sonnet-5` | 0.900 | 0.137 |
| `gemma4:e4b` | 0.806 | 0.076 |
| `openai/gpt-oss-120b` | 0.772 | 0.193 |
| `microsoft/phi-4` | 0.750 | 0.000 |
| `qwen/qwen3-8b` | 0.440 | 0.130 |
| `qwen/qwen3.8-flash` | 0.080 | 0.179 |
| `bytedance-seed/seed-2-1-turbo` | 0.000 | 0.000 |
| `qwen/qwen3.5-plus-02-15` | 0.000 | 0.000 |

#### `ep-tosh-show-5f6894439bb6`: My Mom - Emergency Pod

- Podcast: tosh-show
- Duration: 41.4 min
- Truth ads: 5

| Model | F1 | F1 stdev |
|-------|----|----------|
| `claude-fable-5-1` | 0.909 | 0.000 |
| `google/gemini-3.5-flash` | 0.909 | 0.000 |
| `claude-haiku-4-5-20251001` | 0.894 | 0.034 |
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
| `qwen/qwen3-8b` | 0.515 | 0.130 |
| `qwen/qwen3.8-flash` | 0.189 | 0.259 |
| `bytedance-seed/seed-2-1-turbo` | 0.000 | 0.000 |
| `qwen/qwen3.5-plus-02-15` | 0.000 | 0.000 |


### Parser stress test

How each model's responses were actually parsed. Columns are extraction methods, ordered alphabetically; rows are models, sorted by parse-failure rate (cleanest at top). `json_array_direct` is the happy path: a bare JSON array we could `json.loads` and process immediately. `markdown_code_block` means we had to strip triple-backtick fences first; `json_object_*` means the model wrapped the array in an outer object and we had to find the array key; `regex_*` are last-resort recovery paths. A model that needs anything but `json_array_direct` for most calls is fragile. It works today, but a small prompt change can break the parser.

| Model | bracket_fallback | json_object_no_ads | json_object_single_ad_truncated | parse_failure | segment_id_direct |
|---|---|---|---|---|---|
| `claude-fable-5-1` | 0 | 0 | 0 | 0 | 235 |
| `claude-haiku-4-5-20251001` | 0 | 0 | 0 | 0 | 235 |
| `claude-opus-4-8` | 0 | 0 | 0 | 0 | 235 |
| `claude-opus-5` | 0 | 0 | 0 | 0 | 235 |
| `claude-opus-5-5` | 0 | 0 | 0 | 0 | 235 |
| `claude-sonnet-5` | 0 | 0 | 0 | 0 | 235 |
| `gemma4:e4b` | 0 | 0 | 0 | 0 | 235 |
| `google/gemini-3.1-flash-lite` | 0 | 0 | 10 | 0 | 225 |
| `google/gemini-3.5-flash` | 0 | 0 | 6 | 0 | 229 |
| `google/gemini-3.5-flash-lite` | 0 | 0 | 0 | 0 | 235 |
| `microsoft/phi-4` | 0 | 0 | 0 | 0 | 235 |
| `mistralai/mistral-small-2603` | 0 | 0 | 0 | 0 | 235 |
| `openai/gpt-oss-120b` | 0 | 0 | 0 | 0 | 235 |
| `x-ai/grok-4.3` | 0 | 0 | 0 | 0 | 235 |
| `qwen/qwen3.7-flash` | 0 | 0 | 0 | 2 | 233 |
| `deepseek/deepseek-v4-flash` | 0 | 0 | 0 | 45 | 190 |
| `qwen/qwen3-8b` | 0 | 26 | 0 | 116 | 93 |
| `qwen/qwen3.8-flash` | 0 | 0 | 2 | 173 | 60 |
| `bytedance-seed/seed-2-1-turbo` | 1 | 0 | 0 | 220 | 14 |
| `qwen/qwen3.5-plus-02-15` | 0 | 0 | 0 | 230 | 5 |

## Methodology

Reproducibility settings used for this run. The benchmark sends the same prompts MinusPod sends in production (same system prompt, same sponsor list, same windowing) so the F1 numbers here are directly relevant to production accuracy decisions. Cost is recomputed at report time from token counts against the active pricing snapshot, so all rows compare at the same prices regardless of when the actual call ran.

- Trials per (model, episode): **5**, temperature 0.0
- max_tokens: 4096 (matches MinusPod production)
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

- Report generated: 2026-10-08T03:42:29Z
- Unique work units (current state, last-write-wins after retries): 4700
- Raw call records: 5170 (470 superseded by later retries; kept for audit)
- Successful: 4700
- Failed: 0
- Lifetime list-price cost (sum of at-runtime costs, includes superseded rows): $40.4122
- Lifetime tokens (same basis): 17,621,050 in + 7,399,417 out = 25,020,467
- Note: every input token is priced at list rate. Providers that serve a repeated prompt from cache bill less than this, and the harness does not record cache hits, so a real invoice for this run will come in under the figure above.
- Active pricing snapshot: 2026-10-07T08:48:49.567770Z
- Addressing mode: segment_ids
- Prompt variant: segmentation
- System prompt: segmentation-v1.txt (sha256:85b53077)
