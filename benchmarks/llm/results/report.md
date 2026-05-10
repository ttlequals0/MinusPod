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
- **Extraction method**: the route the parser took to recover the ad list -- `json_array_direct` is the cleanest; method names with `regex_*` mean the JSON itself was malformed and we fell back to text matching.


## TL;DR

### Best Accuracy (F1 @ IoU >= 0.5)

| Rank | Model | F1 | Cost / episode | p50 latency | JSON compliance |
|------|-------|----|----------------|-------------|-----------------|
| 1 | `x-ai/grok-4.1-fast` | 0.607 | $0.1152 | 12.3s | 0.86 |
| 2 | `qwen/qwen3.5-plus-02-15` | 0.566 | $0.0000 | 53.1s | 1.00 |
| 3 | `openai/gpt-5.5` | 0.559 | $2.6183 | 6.4s | 0.86 |
| 4 | `claude-opus-4-7` | 0.539 | $3.0596 | 2.5s | 1.00 |
| 5 | `openai/gpt-5.4` | 0.526 | $0.9972 | 1.8s | 0.79 |
| 6 | `deepseek/deepseek-v4-flash` | 0.429 | $0.0000 | 2.6s | 0.78 |
| 7 | `mistralai/mistral-medium-3.1` | 0.407 | $0.0000 | 0.9s | 1.00 |
| 8 | `google/gemma-4-31b-it` | 0.403 | $0.0000 | 1.7s | 0.84 |
| 9 | `moonshotai/kimi-k2.6` | 0.362 | $1.1589 | 35.4s | 0.53 |
| 10 | `claude-haiku-4-5-20251001` | 0.337 | $0.4734 | 1.6s | 0.60 |
| 11 | `claude-sonnet-4-6` | 0.333 | $1.3840 | 1.9s | 0.96 |
| 12 | `deepseek/deepseek-v3.2` | 0.311 | $0.2487 | 2.3s | 0.92 |
| 13 | `mistralai/mistral-large-2512` | 0.145 | $0.2222 | 3.1s | 1.00 |
| 14 | `microsoft/phi-4` | 0.042 | $0.0570 | 2.1s | 0.85 |

### Best Value (F1 per dollar)

| Rank | Model | F1/$ | F1 | Cost / episode |
|------|-------|------|----|----------------|
| 1 | `x-ai/grok-4.1-fast` | 5.27 | 0.607 | $0.1152 |
| 2 | `deepseek/deepseek-v3.2` | 1.25 | 0.311 | $0.2487 |
| 3 | `microsoft/phi-4` | 0.73 | 0.042 | $0.0570 |
| 4 | `claude-haiku-4-5-20251001` | 0.71 | 0.337 | $0.4734 |
| 5 | `mistralai/mistral-large-2512` | 0.65 | 0.145 | $0.2222 |
| 6 | `openai/gpt-5.4` | 0.53 | 0.526 | $0.9972 |
| 7 | `moonshotai/kimi-k2.6` | 0.31 | 0.362 | $1.1589 |
| 8 | `claude-sonnet-4-6` | 0.24 | 0.333 | $1.3840 |
| 9 | `openai/gpt-5.5` | 0.21 | 0.559 | $2.6183 |
| 10 | `claude-opus-4-7` | 0.18 | 0.539 | $3.0596 |

## Charts

### Cost vs F1 (Pareto)

Each model is one colored point. Lower-left is unhelpful (expensive, inaccurate). Upper-left is the sweet spot (accurate, cheap). The legend below the chart shows each model's color next to its F1 and cost-per-episode.

![Cost vs F1 by model](report_assets/pareto.svg)

### JSON schema compliance

Fraction of each model's responses that parsed as a clean JSON array. 1.0 means every response came back exactly as requested; lower numbers mean the parser had to recover from markdown fences, object wrappers, or extra fields.

![JSON compliance per model](report_assets/compliance.svg)

### F1 by episode (heatmap)

F1 score for each (model, episode) pair. Greener is more accurate, redder is less. The no-ad episode is excluded -- it has no F1 because it's a PASS/FAIL negative control.

![F1 score per model and episode](report_assets/episodes.svg)

### Confidence calibration (reliability diagram)

Each line is one model. The x-axis is the model's self-reported confidence on its predictions (binned). The y-axis is the actual hit rate within that bin -- the fraction that turned out to be true positives at IoU >= 0.5. A model whose line tracks the diagonal is calibrated; lines below the diagonal are overconfident.

![Confidence calibration per model](report_assets/calibration.svg)

### Latency percentiles

p50, p90, p99, and max per model on a log scale. The gap between p99 and max indicates how heavy the tail is. For OpenRouter-routed models, the tail also includes upstream provider load.

![Latency percentiles per model](report_assets/latency_tail.svg)


## Failures and provider issues

**1 call(s) failed out of 4760 total (0.02%).** Failures are excluded from F1 / cost calculations, but they often surface real production-relevant gotchas worth knowing.

### By category

| Category | Calls | Affected models |
|----------|------:|-----------------|
| Provider content moderation rejection | 1 | `qwen/qwen3.5-plus-02-15` |

### Per-model error count

| Model | Errors | of total |
|---|---:|---:|
| `qwen/qwen3.5-plus-02-15` | 1 | 1/340 (0.3%) |

### Sample messages (first 3 per category)

**Provider content moderation rejection** (1)
- `qwen/qwen3.5-plus-02-15` on `ep-glt1412515089-373d5ba5007b` (trial 2, window 8): Error code: 400 - {'error': {'message': 'Provider returned error', 'code': 400, 'metadata': {'raw': '{"error":{"message":"<400> InternalError.Algo.DataInspectionFailed: Input text data may contain inappropriate content.","type":"data_inspec...

### Why this section exists

If you're picking a model for production, an aggregate compliance score doesn't tell you when the provider will simply refuse to answer. A few cases that have shown up here:

- **Content moderation rejections** (Alibaba on Qwen, Google on Gemma, sometimes others): the provider's classifier blocks the prompt before the model runs. For ad detection on real podcast transcripts, this can happen on episodes with adult content, profanity, or politically sensitive topics. Rate is small but non-zero -- plan for it.
- **Deprecated parameters**: the Claude 4.x family rejects `temperature`. The benchmark memoizes this per-process and retries without, but it tells you which models you cannot pass legacy sampling controls to.
- **Rate limits**: tail-latency or 429s under load -- not a model-quality issue but determines whether a given provider is operationally viable for your throughput.


## Precision, recall, and FP/FN breakdown

F1 collapses two failure modes into one number. A precision-leaning model misses ads but rarely flags non-ads; a recall-leaning model catches everything at the cost of false positives. Production tradeoffs hinge on which one you can tolerate.

| Model | Precision | Recall | TP | FP | FN |
|---|---:|---:|---:|---:|---:|
| `x-ai/grok-4.1-fast` | 0.542 | 0.729 | 75 | 71 | 30 |
| `qwen/qwen3.5-plus-02-15` | 0.479 | 0.728 | 73 | 93 | 28 |
| `openai/gpt-5.5` | 0.491 | 0.677 | 70 | 78 | 35 |
| `claude-opus-4-7` | 0.460 | 0.691 | 73 | 94 | 32 |
| `openai/gpt-5.4` | 0.458 | 0.703 | 73 | 101 | 32 |
| `deepseek/deepseek-v4-flash` | 0.303 | 0.748 | 77 | 200 | 28 |
| `mistralai/mistral-medium-3.1` | 0.338 | 0.571 | 58 | 191 | 47 |
| `google/gemma-4-31b-it` | 0.292 | 0.656 | 68 | 195 | 37 |
| `moonshotai/kimi-k2.6` | 0.417 | 0.355 | 34 | 44 | 71 |
| `claude-haiku-4-5-20251001` | 0.231 | 0.633 | 65 | 276 | 40 |
| `claude-sonnet-4-6` | 0.250 | 0.517 | 55 | 185 | 50 |
| `deepseek/deepseek-v3.2` | 0.278 | 0.390 | 38 | 93 | 67 |
| `mistralai/mistral-large-2512` | 0.085 | 0.492 | 50 | 533 | 55 |
| `microsoft/phi-4` | 0.033 | 0.062 | 7 | 206 | 98 |

## Boundary accuracy

For ads that match the truth at IoU >= 0.5, how far off were the predicted start and end timestamps? Lower is better. A model can hit F1 cleanly while still being 20s off on every boundary -- bad for any pipeline that cuts the audio.

| Model | Start MAE (s) | End MAE (s) |
|---|---:|---:|
| `claude-haiku-4-5-20251001` | 3.67 | 0.03 |
| `claude-sonnet-4-6` | 4.30 | 3.67 |
| `deepseek/deepseek-v3.2` | 7.94 | 0.04 |
| `mistralai/mistral-large-2512` | 12.60 | 0.03 |
| `x-ai/grok-4.1-fast` | 11.30 | 4.08 |
| `qwen/qwen3.5-plus-02-15` | 14.94 | 1.27 |
| `google/gemma-4-31b-it` | 17.05 | 1.44 |
| `mistralai/mistral-medium-3.1` | 12.56 | 7.07 |
| `openai/gpt-5.4` | 19.68 | 1.63 |
| `deepseek/deepseek-v4-flash` | 11.61 | 10.17 |
| `openai/gpt-5.5` | 17.98 | 4.10 |
| `microsoft/phi-4` | 7.43 | 16.42 |
| `claude-opus-4-7` | 18.77 | 6.21 |
| `moonshotai/kimi-k2.6` | 24.20 | 4.04 |

## Confidence calibration

Models include a self-reported `confidence` on each detected ad. A well-calibrated model should be right ~95% of the time when it claims 0.95 confidence. The table below bins each model's predictions and shows the actual hit rate (fraction that were true positives at IoU >= 0.5). A bin near 1.0 is well-calibrated; a low number with a high count means the model is overconfident.

| Model | 0.00-0.70 | 0.70-0.90 | 0.90-0.95 | 0.95-0.99 | 0.99+ | total |
|---|---:|---:|---:|---:|---:|---:|
| `claude-haiku-4-5-20251001` | -- | 0.00 (n=20) | 0.20 (n=101) | 0.20 (n=220) | -- | 341 |
| `claude-opus-4-7` | -- | -- | 0.00 (n=2) | 0.43 (n=147) | 0.56 (n=18) | 167 |
| `claude-sonnet-4-6` | -- | 0.00 (n=21) | 0.20 (n=30) | 0.23 (n=164) | 0.48 (n=25) | 240 |
| `deepseek/deepseek-v3.2` | -- | -- | 0.00 (n=2) | 0.09 (n=68) | 0.52 (n=61) | 131 |
| `deepseek/deepseek-v4-flash` | 0.00 (n=1) | 1.00 (n=1) | 0.00 (n=1) | 0.21 (n=174) | 0.39 (n=101) | 278 |
| `google/gemma-4-31b-it` | -- | 0.00 (n=7) | 0.23 (n=13) | 0.18 (n=102) | 0.32 (n=146) | 268 |
| `microsoft/phi-4` | -- | 0.00 (n=13) | 0.00 (n=11) | 0.04 (n=194) | -- | 218 |
| `mistralai/mistral-large-2512` | 0.00 (n=1) | 0.00 (n=5) | 0.00 (n=17) | 0.04 (n=274) | 0.14 (n=286) | 583 |
| `mistralai/mistral-medium-3.1` | -- | -- | 0.00 (n=8) | 0.23 (n=230) | 0.45 (n=11) | 249 |
| `moonshotai/kimi-k2.6` | 0.00 (n=10) | 0.08 (n=12) | -- | 0.48 (n=29) | 0.66 (n=29) | 80 |
| `openai/gpt-5.4` | 0.00 (n=4) | 0.17 (n=18) | 0.00 (n=18) | 0.46 (n=35) | 0.52 (n=104) | 179 |
| `openai/gpt-5.5` | -- | 0.75 (n=4) | 0.00 (n=4) | 0.38 (n=29) | 0.50 (n=112) | 149 |
| `qwen/qwen3.5-plus-02-15` | -- | 0.00 (n=2) | 0.50 (n=2) | 0.45 (n=155) | 0.29 (n=7) | 166 |
| `x-ai/grok-4.1-fast` | -- | 1.00 (n=1) | 0.00 (n=1) | 0.51 (n=72) | 0.51 (n=72) | 146 |

See `report_assets/calibration.svg` for the visual reliability diagram.

## Latency tail

Median latency hides outliers. p99 and max are what determine queue depth and worst-case user wait. For OpenRouter-routed models the tail also reflects upstream provider load, not just model compute.

| Model | p50 | p90 | p95 | p99 | max |
|---|---:|---:|---:|---:|---:|
| `mistralai/mistral-medium-3.1` | 0.86s | 3.52s | 5.97s | 7.77s | 9.92s |
| `claude-haiku-4-5-20251001` | 1.55s | 3.23s | 4.22s | 181.73s | 186.76s |
| `google/gemma-4-31b-it` | 1.68s | 12.49s | 17.78s | 63.72s | 132.78s |
| `openai/gpt-5.4` | 1.84s | 2.36s | 3.04s | 3.90s | 4.62s |
| `claude-sonnet-4-6` | 1.86s | 4.50s | 5.65s | 7.89s | 183.22s |
| `microsoft/phi-4` | 2.11s | 5.86s | 8.59s | 189.07s | 221.74s |
| `deepseek/deepseek-v3.2` | 2.28s | 4.69s | 6.21s | 12.70s | 63.86s |
| `claude-opus-4-7` | 2.52s | 3.95s | 4.48s | 5.77s | 183.21s |
| `deepseek/deepseek-v4-flash` | 2.55s | 23.66s | 34.37s | 53.21s | 56.22s |
| `mistralai/mistral-large-2512` | 3.06s | 6.01s | 6.51s | 9.64s | 18.09s |
| `openai/gpt-5.5` | 6.43s | 13.28s | 20.01s | 23.82s | 30.37s |
| `x-ai/grok-4.1-fast` | 12.33s | 32.23s | 38.07s | 52.20s | 64.85s |
| `moonshotai/kimi-k2.6` | 35.44s | 100.18s | 118.03s | 158.13s | 224.67s |
| `qwen/qwen3.5-plus-02-15` | 53.05s | 127.09s | 146.66s | 993.22s | 1486.87s |

## Output token efficiency

How many output tokens the model spent per detected ad. Lower is more concise -- the model finds an ad and returns the JSON. Higher means the model is producing a lot of text the parser will discard, which costs you whether or not the answer is right.

| Model | Total output tokens | Ads detected | Tokens / ad | Cost / TP |
|---|---:|---:|---:|---:|
| `mistralai/mistral-medium-3.1` | 15,468 | 249 | 62 | $0.0000 |
| `google/gemma-4-31b-it` | 19,960 | 268 | 74 | $0.0000 |
| `claude-sonnet-4-6` | 20,560 | 240 | 86 | $0.0252 |
| `deepseek/deepseek-v3.2` | 11,722 | 131 | 89 | $0.0065 |
| `mistralai/mistral-large-2512` | 53,206 | 583 | 91 | $0.0044 |
| `claude-opus-4-7` | 15,430 | 167 | 92 | $0.0419 |
| `claude-haiku-4-5-20251001` | 32,742 | 341 | 96 | $0.0073 |
| `openai/gpt-5.4` | 18,389 | 179 | 103 | $0.0137 |
| `microsoft/phi-4` | 77,912 | 218 | 357 | $0.0081 |
| `deepseek/deepseek-v4-flash` | 151,791 | 278 | 546 | $0.0000 |
| `openai/gpt-5.5` | 122,356 | 149 | 821 | $0.0374 |
| `x-ai/grok-4.1-fast` | 382,947 | 146 | 2623 | $0.0015 |
| `qwen/qwen3.5-plus-02-15` | 1,458,099 | 172 | 8477 | $0.0000 |
| `moonshotai/kimi-k2.6` | 994,276 | 80 | 12428 | $0.0341 |

## Trial variance (determinism check)

All trials run at temperature 0.0. If a model produces stable output you'd expect the F1 stdev across trials to be near zero. Higher numbers mean the model is non-deterministic even at temp=0 -- which is fine to know, but means you cannot trust a single trial's number for that model.

| Model | Mean F1 stdev across episodes | Highest single-episode stdev |
|---|---:|---:|
| `x-ai/grok-4.1-fast` | 0.0516 | 0.0723 |
| `qwen/qwen3.5-plus-02-15` | 0.0292 | 0.0636 |
| `openai/gpt-5.5` | 0.0791 | 0.1432 |
| `claude-opus-4-7` | 0.0506 | 0.0958 |
| `openai/gpt-5.4` | 0.0592 | 0.0784 |
| `deepseek/deepseek-v4-flash` | 0.0614 | 0.0845 |
| `mistralai/mistral-medium-3.1` | 0.0749 | 0.1373 |
| `google/gemma-4-31b-it` | 0.0591 | 0.0854 |
| `moonshotai/kimi-k2.6` | 0.1268 | 0.2451 |
| `claude-haiku-4-5-20251001` | 0.0033 | 0.0116 |
| `claude-sonnet-4-6` | 0.0139 | 0.0369 |
| `deepseek/deepseek-v3.2` | 0.0948 | 0.1422 |
| `mistralai/mistral-large-2512` | 0.0204 | 0.0391 |
| `microsoft/phi-4` | 0.0486 | 0.0770 |

## Cross-model agreement

For each of the 68 (episode, window, trial-equivalent) entries, how many of the 14 active models predicted at least one ad? High-agreement windows are unambiguous ads (or unambiguously not ads). Low-agreement windows are where individual models disagree -- candidates for ensemble voting if you want a cheap accuracy boost.

| Models predicting an ad | Window count | Share |
|---:|---:|---:|
| 0 of 14 | 2 | 2.9% |
| 1 of 14 | 1 | 1.5% |
| 2 of 14 | 7 | 10.3% |
| 3 of 14 | 10 | 14.7% |
| 4 of 14 | 5 | 7.4% |
| 5 of 14 | 6 | 8.8% |
| 6 of 14 | 4 | 5.9% |
| 8 of 14 | 1 | 1.5% |
| 11 of 14 | 5 | 7.4% |
| 12 of 14 | 5 | 7.4% |
| 13 of 14 | 14 | 20.6% |
| 14 of 14 | 8 | 11.8% |

Read this as: rows near the top are windows where the field disagrees (most models said no, a few said yes -- usually false positives); rows near the bottom are windows where the field broadly agrees (typical of clear sponsor reads).

## Detection rate by ad characteristic

Aggregate detection rates often hide systematic blind spots. Below: for each model, what fraction of truth ads in each bucket were detected (matched at IoU >= 0.5).

### By ad length

| Model | long (>=90s) | medium (30-90s) | short (<30s) |
|---|---:|---:|---:|
| `claude-haiku-4-5-20251001` | 0.50 (n=70) | 1.00 (n=15) | 0.75 (n=20) |
| `claude-opus-4-7` | 0.83 (n=70) | 0.33 (n=15) | 0.50 (n=20) |
| `claude-sonnet-4-6` | 0.53 (n=70) | 0.53 (n=15) | 0.50 (n=20) |
| `deepseek/deepseek-v3.2` | 0.37 (n=70) | 0.33 (n=15) | 0.35 (n=20) |
| `deepseek/deepseek-v4-flash` | 0.69 (n=70) | 0.93 (n=15) | 0.75 (n=20) |
| `google/gemma-4-31b-it` | 0.63 (n=70) | 0.67 (n=15) | 0.70 (n=20) |
| `microsoft/phi-4` | 0.03 (n=70) | 0.13 (n=15) | 0.15 (n=20) |
| `mistralai/mistral-large-2512` | 0.43 (n=70) | 0.67 (n=15) | 0.50 (n=20) |
| `mistralai/mistral-medium-3.1` | 0.41 (n=70) | 1.00 (n=15) | 0.70 (n=20) |
| `moonshotai/kimi-k2.6` | 0.34 (n=70) | 0.40 (n=15) | 0.20 (n=20) |
| `openai/gpt-5.4` | 0.79 (n=70) | 0.33 (n=15) | 0.65 (n=20) |
| `openai/gpt-5.5` | 0.70 (n=70) | 0.53 (n=15) | 0.65 (n=20) |
| `qwen/qwen3.5-plus-02-15` | 0.84 (n=69) | 0.36 (n=14) | 0.56 (n=18) |
| `x-ai/grok-4.1-fast` | 0.73 (n=70) | 0.67 (n=15) | 0.70 (n=20) |

### By ad position

| Model | pre-roll (<10%) | mid-roll (10-90%) | post-roll (>90%) |
|---|---:|---:|---:|
| `claude-haiku-4-5-20251001` | 0.67 (n=30) | 0.64 (n=55) | 0.50 (n=20) |
| `claude-opus-4-7` | 0.67 (n=30) | 0.84 (n=55) | 0.35 (n=20) |
| `claude-sonnet-4-6` | 0.67 (n=30) | 0.55 (n=55) | 0.25 (n=20) |
| `deepseek/deepseek-v3.2` | 0.33 (n=30) | 0.49 (n=55) | 0.05 (n=20) |
| `deepseek/deepseek-v4-flash` | 0.63 (n=30) | 0.91 (n=55) | 0.40 (n=20) |
| `google/gemma-4-31b-it` | 0.67 (n=30) | 0.75 (n=55) | 0.35 (n=20) |
| `microsoft/phi-4` | 0.17 (n=30) | 0.04 (n=55) | 0.00 (n=20) |
| `mistralai/mistral-large-2512` | 0.63 (n=30) | 0.56 (n=55) | 0.00 (n=20) |
| `mistralai/mistral-medium-3.1` | 0.67 (n=30) | 0.56 (n=55) | 0.35 (n=20) |
| `moonshotai/kimi-k2.6` | 0.27 (n=30) | 0.40 (n=55) | 0.20 (n=20) |
| `openai/gpt-5.4` | 0.63 (n=30) | 0.89 (n=55) | 0.25 (n=20) |
| `openai/gpt-5.5` | 0.57 (n=30) | 0.87 (n=55) | 0.25 (n=20) |
| `qwen/qwen3.5-plus-02-15` | 0.66 (n=29) | 0.94 (n=52) | 0.25 (n=20) |
| `x-ai/grok-4.1-fast` | 0.63 (n=30) | 0.96 (n=55) | 0.15 (n=20) |

## Quick Comparison

| Model | F1 | Cost/ep | p50 | ep-daily-tech-news-show-c1904b8605f7 | ep-glt1412515089-373d5ba5007b | ep-security-now-audio-2850b24903b2 | ep-the-tim-dillon-show-f62bd5fa1cfe | ep-ai-cloud-essentials-e8dc897fbd6b (no-ad) | F1 stdev |
|---|---|---|---|---|---|---|---|---|---|
| `x-ai/grok-4.1-fast` | 0.607 | $0.1152 | 12.3s | 0.578 | 0.716 | 0.539 | 0.594 | PASS | 0.052 |
| `qwen/qwen3.5-plus-02-15` | 0.566 | $0.0000 | 53.1s | 0.518 | 0.632 | 0.476 | 0.636 | PASS | 0.029 |
| `openai/gpt-5.5` | 0.559 | $2.6183 | 6.4s | 0.547 | 0.636 | 0.505 | 0.546 | FAIL (1 FP) | 0.079 |
| `claude-opus-4-7` | 0.539 | $3.0596 | 2.5s | 0.445 | 0.600 | 0.520 | 0.592 | PASS | 0.051 |
| `openai/gpt-5.4` | 0.526 | $0.9972 | 1.8s | 0.518 | 0.506 | 0.495 | 0.586 | FAIL (1 FP) | 0.059 |
| `deepseek/deepseek-v4-flash` | 0.429 | $0.0000 | 2.6s | 0.310 | 0.642 | 0.465 | 0.298 | FAIL (1 FP) | 0.061 |
| `mistralai/mistral-medium-3.1` | 0.407 | $0.0000 | 0.9s | 0.095 | 0.671 | 0.640 | 0.223 | PASS | 0.075 |
| `google/gemma-4-31b-it` | 0.403 | $0.0000 | 1.7s | 0.119 | 0.645 | 0.496 | 0.350 | FAIL (1 FP) | 0.059 |
| `moonshotai/kimi-k2.6` | 0.362 | $1.1589 | 35.4s | 0.600 | 0.469 | 0.196 | 0.184 | FAIL (1 FP) | 0.127 |
| `claude-haiku-4-5-20251001` | 0.337 | $0.4734 | 1.6s | 0.073 | 0.571 | 0.551 | 0.154 | PASS | 0.003 |
| `claude-sonnet-4-6` | 0.333 | $1.3840 | 1.9s | 0.237 | 0.400 | 0.516 | 0.179 | PASS | 0.014 |
| `deepseek/deepseek-v3.2` | 0.311 | $0.2487 | 2.3s | 0.140 | 0.518 | 0.528 | 0.057 | PASS | 0.095 |
| `mistralai/mistral-large-2512` | 0.145 | $0.2222 | 3.1s | 0.074 | 0.253 | 0.209 | 0.044 | PASS | 0.020 |
| `microsoft/phi-4` | 0.042 | $0.0570 | 2.1s | 0.056 | 0.000 | 0.033 | 0.079 | FAIL (3 FP) | 0.049 |

---

## Detailed Results

### Per-Model Detail

#### `x-ai/grok-4.1-fast`

- F1 (avg across episodes): **0.607**
- Total cost / episode: **$0.1152**
- p50 / p95 latency: 12.33s / 38.07s
- JSON compliance: 0.86
- Parse failure rate: 0.3%
- Extraction methods: `json_object_no_ads`: 181, `json_object_single_ad`: 158, `parse_failure`: 1
- Schema violations: 263
- Extra keys observed: end_text, sponsor

#### `qwen/qwen3.5-plus-02-15`

- F1 (avg across episodes): **0.566**
- Total cost / episode: **$0.0000**
- p50 / p95 latency: 53.05s / 146.66s
- JSON compliance: 1.00
- Parse failure rate: 0.0%
- Extraction methods: `json_array_direct`: 339
- Schema violations: 343
- Extra keys observed: end_text, sponsor

#### `openai/gpt-5.5`

- F1 (avg across episodes): **0.559**
- Total cost / episode: **$2.6183**
- p50 / p95 latency: 6.43s / 20.01s
- JSON compliance: 0.86
- Parse failure rate: 0.3%
- Extraction methods: `json_object_no_ads`: 180, `json_object_single_ad`: 159, `parse_failure`: 1
- Schema violations: 276
- Extra keys observed: end_text, sponsor

#### `claude-opus-4-7`

- F1 (avg across episodes): **0.539**
- Total cost / episode: **$3.0596**
- p50 / p95 latency: 2.52s / 4.48s
- JSON compliance: 1.00
- Parse failure rate: 0.0%
- Extraction methods: `json_array_direct`: 339, `regex_json_array`: 1
- Schema violations: 334
- Extra keys observed: end_text, sponsor

#### `openai/gpt-5.4`

- F1 (avg across episodes): **0.526**
- Total cost / episode: **$0.9972**
- p50 / p95 latency: 1.84s / 3.04s
- JSON compliance: 0.79
- Parse failure rate: 0.0%
- Extraction methods: `json_object_no_ads`: 97, `json_object_single_ad`: 243
- Schema violations: 317
- Extra keys observed: end_text, sponsor

#### `deepseek/deepseek-v4-flash`

- F1 (avg across episodes): **0.429**
- Total cost / episode: **$0.0000**
- p50 / p95 latency: 2.55s / 34.37s
- JSON compliance: 0.78
- Parse failure rate: 3.5%
- Extraction methods: `json_array_direct`: 12, `json_object_ads_key`: 214, `json_object_no_ads`: 2, `json_object_single_ad`: 100, `parse_failure`: 12
- Schema violations: 520
- Extra keys observed: end_text, sponsor

#### `mistralai/mistral-medium-3.1`

- F1 (avg across episodes): **0.407**
- Total cost / episode: **$0.0000**
- p50 / p95 latency: 0.86s / 5.97s
- JSON compliance: 1.00
- Parse failure rate: 0.0%
- Extraction methods: `json_array_direct`: 340
- Schema violations: 498
- Extra keys observed: end_text, sponsor

#### `google/gemma-4-31b-it`

- F1 (avg across episodes): **0.403**
- Total cost / episode: **$0.0000**
- p50 / p95 latency: 1.68s / 17.78s
- JSON compliance: 0.84
- Parse failure rate: 0.0%
- Extraction methods: `json_object_ads_key`: 199, `json_object_no_ads`: 58, `json_object_single_ad`: 83
- Schema violations: 532
- Extra keys observed: end_text, sponsor

#### `moonshotai/kimi-k2.6`

- F1 (avg across episodes): **0.362**
- Total cost / episode: **$1.1589**
- p50 / p95 latency: 35.44s / 118.03s
- JSON compliance: 0.53
- Parse failure rate: 33.5%
- Extraction methods: `json_array_direct`: 31, `json_object_ads_key`: 11, `json_object_no_ads`: 34, `json_object_single_ad`: 150, `parse_failure`: 114
- Schema violations: 150
- Extra keys observed: end_text, sponsor

#### `claude-haiku-4-5-20251001`

- F1 (avg across episodes): **0.337**
- Total cost / episode: **$0.4734**
- p50 / p95 latency: 1.55s / 4.22s
- JSON compliance: 0.60
- Parse failure rate: 0.0%
- Extraction methods: `markdown_code_block`: 340
- Schema violations: 612
- Extra keys observed: end_text, sponsor

#### `claude-sonnet-4-6`

- F1 (avg across episodes): **0.333**
- Total cost / episode: **$1.3840**
- p50 / p95 latency: 1.86s / 5.65s
- JSON compliance: 0.96
- Parse failure rate: 0.0%
- Extraction methods: `json_array_direct`: 308, `markdown_code_block`: 27, `regex_json_array`: 5
- Schema violations: 405
- Extra keys observed: end_text, sponsor

#### `deepseek/deepseek-v3.2`

- F1 (avg across episodes): **0.311**
- Total cost / episode: **$0.2487**
- p50 / p95 latency: 2.28s / 6.21s
- JSON compliance: 0.92
- Parse failure rate: 0.0%
- Extraction methods: `json_array_direct`: 251, `json_object_ads_key`: 3, `json_object_single_ad`: 86
- Schema violations: 254
- Extra keys observed: end_text, sponsor

#### `mistralai/mistral-large-2512`

- F1 (avg across episodes): **0.145**
- Total cost / episode: **$0.2222**
- p50 / p95 latency: 3.06s / 6.51s
- JSON compliance: 1.00
- Parse failure rate: 0.0%
- Extraction methods: `json_array_direct`: 340
- Schema violations: 1097
- Extra keys observed: end_text, sponsor

#### `microsoft/phi-4`

- F1 (avg across episodes): **0.042**
- Total cost / episode: **$0.0570**
- p50 / p95 latency: 2.11s / 8.59s
- JSON compliance: 0.85
- Parse failure rate: 1.8%
- Extraction methods: `json_array_direct`: 161, `json_object_ads_key`: 18, `json_object_no_ads`: 10, `json_object_segments_key`: 8, `json_object_single_ad`: 137, `parse_failure`: 6
- Schema violations: 318
- Extra keys observed: end_text, sponsor


### Per-Episode Detail

#### `ep-ai-cloud-essentials-e8dc897fbd6b` -- How Physical AI is Streamlining Engineering

- Podcast: ai-cloud-essentials
- Duration: 16.4 min
- Truth: no-ads episode

| Model | Result | FP count |
|-------|--------|----------|
| `x-ai/grok-4.1-fast` | PASS | 0 |
| `claude-haiku-4-5-20251001` | PASS | 0 |
| `claude-sonnet-4-6` | PASS | 0 |
| `deepseek/deepseek-v3.2` | PASS | 0 |
| `mistralai/mistral-medium-3.1` | PASS | 0 |
| `claude-opus-4-7` | PASS | 0 |
| `mistralai/mistral-large-2512` | PASS | 0 |
| `qwen/qwen3.5-plus-02-15` | PASS | 0 |
| `openai/gpt-5.5` | FAIL | 1 |
| `openai/gpt-5.4` | FAIL | 1 |
| `google/gemma-4-31b-it` | FAIL | 1 |
| `moonshotai/kimi-k2.6` | FAIL | 1 |
| `deepseek/deepseek-v4-flash` | FAIL | 1 |
| `microsoft/phi-4` | FAIL | 3 |

#### `ep-daily-tech-news-show-c1904b8605f7` -- Switch 2 Prices Rise, Forecast Drops - DTNS 5265

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
| `claude-opus-4-7` | 0.445 | 0.096 |
| `deepseek/deepseek-v4-flash` | 0.310 | 0.043 |
| `claude-sonnet-4-6` | 0.237 | 0.037 |
| `deepseek/deepseek-v3.2` | 0.140 | 0.142 |
| `google/gemma-4-31b-it` | 0.119 | 0.058 |
| `mistralai/mistral-medium-3.1` | 0.095 | 0.010 |
| `mistralai/mistral-large-2512` | 0.074 | 0.000 |
| `claude-haiku-4-5-20251001` | 0.073 | 0.001 |
| `microsoft/phi-4` | 0.056 | 0.077 |

#### `ep-glt1412515089-373d5ba5007b` -- #2496 - Julia Mossbridge

- Podcast: glt1412515089
- Duration: 165.3 min
- Truth ads: 4

| Model | F1 | F1 stdev |
|-------|----|----------|
| `x-ai/grok-4.1-fast` | 0.716 | 0.072 |
| `mistralai/mistral-medium-3.1` | 0.671 | 0.098 |
| `google/gemma-4-31b-it` | 0.645 | 0.085 |
| `deepseek/deepseek-v4-flash` | 0.642 | 0.072 |
| `openai/gpt-5.5` | 0.636 | 0.143 |
| `qwen/qwen3.5-plus-02-15` | 0.632 | 0.064 |
| `claude-opus-4-7` | 0.600 | 0.000 |
| `claude-haiku-4-5-20251001` | 0.571 | 0.000 |
| `deepseek/deepseek-v3.2` | 0.518 | 0.085 |
| `openai/gpt-5.4` | 0.506 | 0.059 |
| `moonshotai/kimi-k2.6` | 0.469 | 0.066 |
| `claude-sonnet-4-6` | 0.400 | 0.000 |
| `mistralai/mistral-large-2512` | 0.253 | 0.039 |
| `microsoft/phi-4` | 0.000 | 0.000 |

#### `ep-security-now-audio-2850b24903b2` -- SN 1077: A Browser AI API? - End of Bug Bounties?

- Podcast: security-now-audio
- Duration: 156.2 min
- Truth ads: 6

| Model | F1 | F1 stdev |
|-------|----|----------|
| `mistralai/mistral-medium-3.1` | 0.640 | 0.137 |
| `claude-haiku-4-5-20251001` | 0.551 | 0.012 |
| `x-ai/grok-4.1-fast` | 0.539 | 0.038 |
| `deepseek/deepseek-v3.2` | 0.528 | 0.073 |
| `claude-opus-4-7` | 0.520 | 0.055 |
| `claude-sonnet-4-6` | 0.516 | 0.014 |
| `openai/gpt-5.5` | 0.505 | 0.047 |
| `google/gemma-4-31b-it` | 0.496 | 0.021 |
| `openai/gpt-5.4` | 0.495 | 0.022 |
| `qwen/qwen3.5-plus-02-15` | 0.476 | 0.000 |
| `deepseek/deepseek-v4-flash` | 0.465 | 0.046 |
| `mistralai/mistral-large-2512` | 0.209 | 0.017 |
| `moonshotai/kimi-k2.6` | 0.196 | 0.245 |
| `microsoft/phi-4` | 0.033 | 0.045 |

#### `ep-the-tim-dillon-show-f62bd5fa1cfe` -- 495 - Hantavirus Cruise & iPad Babies

- Podcast: the-tim-dillon-show
- Duration: 80.1 min
- Truth ads: 6

| Model | F1 | F1 stdev |
|-------|----|----------|
| `qwen/qwen3.5-plus-02-15` | 0.636 | 0.028 |
| `x-ai/grok-4.1-fast` | 0.594 | 0.066 |
| `claude-opus-4-7` | 0.592 | 0.052 |
| `openai/gpt-5.4` | 0.586 | 0.077 |
| `openai/gpt-5.5` | 0.546 | 0.063 |
| `google/gemma-4-31b-it` | 0.350 | 0.072 |
| `deepseek/deepseek-v4-flash` | 0.298 | 0.084 |
| `mistralai/mistral-medium-3.1` | 0.223 | 0.054 |
| `moonshotai/kimi-k2.6` | 0.184 | 0.105 |
| `claude-sonnet-4-6` | 0.179 | 0.004 |
| `claude-haiku-4-5-20251001` | 0.154 | 0.000 |
| `microsoft/phi-4` | 0.079 | 0.072 |
| `deepseek/deepseek-v3.2` | 0.057 | 0.079 |
| `mistralai/mistral-large-2512` | 0.044 | 0.025 |


### Parser Stress Test

| Model | json_array_direct | json_object_ads_key | json_object_no_ads | json_object_segments_key | json_object_single_ad | markdown_code_block | parse_failure | regex_json_array |
|---|---|---|---|---|---|---|---|---|
| `claude-haiku-4-5-20251001` | 0 | 0 | 0 | 0 | 0 | 340 | 0 | 0 |
| `openai/gpt-5.4` | 0 | 0 | 97 | 0 | 243 | 0 | 0 | 0 |
| `claude-sonnet-4-6` | 308 | 0 | 0 | 0 | 0 | 27 | 0 | 5 |
| `google/gemma-4-31b-it` | 0 | 199 | 58 | 0 | 83 | 0 | 0 | 0 |
| `deepseek/deepseek-v3.2` | 251 | 3 | 0 | 0 | 86 | 0 | 0 | 0 |
| `mistralai/mistral-medium-3.1` | 340 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| `claude-opus-4-7` | 339 | 0 | 0 | 0 | 0 | 0 | 0 | 1 |
| `mistralai/mistral-large-2512` | 340 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| `qwen/qwen3.5-plus-02-15` | 339 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| `openai/gpt-5.5` | 0 | 0 | 180 | 0 | 159 | 0 | 1 | 0 |
| `x-ai/grok-4.1-fast` | 0 | 0 | 181 | 0 | 158 | 0 | 1 | 0 |
| `microsoft/phi-4` | 161 | 18 | 10 | 8 | 137 | 0 | 6 | 0 |
| `deepseek/deepseek-v4-flash` | 12 | 214 | 2 | 0 | 100 | 0 | 12 | 0 |
| `moonshotai/kimi-k2.6` | 31 | 11 | 34 | 0 | 150 | 0 | 114 | 0 |

### Methodology

- Trials per (model, episode): **5**, temperature 0.0
- max_tokens: 4096 (matches MinusPod production)
- response_format: json_object (with prompt-injection fallback when provider rejects native)
- Window size: 10 min, overlap: 3 min (imported from MinusPod's create_windows)
- Pricing snapshot: 2026-05-09T22:50:45.889333Z
- Corpus episodes: 5

### Run Metadata

- Report generated: 2026-05-10T18:11:22Z
- Total LLM calls recorded: 4760
- Successful: 4759
- Failed: 1
- Lifetime actual spend (sum of at-runtime costs): $51.6724
- Active pricing snapshot: 2026-05-09T22:50:45.889333Z
