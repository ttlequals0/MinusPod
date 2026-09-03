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
| **F1 stdev** | 0 to 1 | lower means more consistent | Standard deviation of F1 across the 12 ad-bearing episodes. High stdev = inconsistent across content types. |
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

Models ranked by F0.5 (precision weighted 2x recall) against human-verified ground truth. MinusPod cuts the segments it flags, so cutting real content (a false positive) is worse than leaving an ad in (a false negative), and F0.5 penalizes it more. A model shares the tier above it unless it scores consistently lower across the same episodes (paired one-sided t-test, 95%); models that trade wins episode to episode share a tier, so order within a tier is not meaningful on this 12-episode corpus. Flags caveat a model without changing its rank. Cost includes free-tier models (shown at $0.00).

| Tier | Model | F0.5 | 95% CI | Precision | Recall | F1 | Cost / episode | p50 latency | JSON compliance | Flags |
|------|-------|------|--------|-----------|--------|----|----------------|-------------|-----------------|-------|
| A | `claude-haiku-4-5-20251001` | 0.908 | +/-0.076 | 0.900 | 0.946 | 0.920 | $1.0820 | 24.2s | 1.00 |  |
| A | `qwen/qwen3.5-plus-02-15` | 0.865 | +/-0.101 | 0.862 | 0.900 | 0.874 | $1.0746 | 29.2s | 1.00 |  |
| A | `google/gemini-3.5-flash-lite` | 0.860 | +/-0.102 | 0.878 | 0.857 | 0.851 | $0.3643 | 0.7s | 1.00 |  |
| A | `claude-sonnet-4-6` | 0.859 | +/-0.098 | 0.857 | 0.892 | 0.866 | $3.2386 | 4.1s | 1.00 |  |
| A | `google/gemini-3.5-flash` | 0.851 | +/-0.111 | 0.844 | 0.905 | 0.867 | $3.4202 | 5.6s | 1.00 |  |
| A | `x-ai/grok-4.3` | 0.847 | +/-0.117 | 0.837 | 0.902 | 0.865 | $1.6048 | 3.7s | 1.00 |  |
| A | `google/gemini-3.6-flash` | 0.846 | +/-0.113 | 0.841 | 0.891 | 0.858 | $1.4431 | 4.5s | 1.00 |  |
| A | `x-ai/grok-4.5` | 0.843 | +/-0.110 | 0.834 | 0.905 | 0.861 | $3.3554 | 10.9s | 1.00 |  |
| A | `openai/gpt-5.5` | 0.839 | +/-0.123 | 0.836 | 0.870 | 0.847 | $7.6842 | 5.4s | 0.87 | (!) brittle JSON (!) fails no-ad control |
| A | `google/gemini-3.7-flash` | 0.838 | +/-0.112 | 0.833 | 0.878 | 0.848 | $0.6411 | 4.7s | 1.00 |  |
| A | `x-ai/grok-4.6` | 0.827 | +/-0.107 | 0.816 | 0.897 | 0.847 | $3.7063 | 13.4s | 1.00 |  |
| A | `openai/gpt-5.6-terra` | 0.823 | +/-0.113 | 0.822 | 0.858 | 0.830 | $2.3902 | 2.1s | 0.86 | (!) brittle JSON (!) fails no-ad control |
| A | `mistralai/mistral-medium-3-5` | 0.822 | +/-0.110 | 0.811 | 0.887 | 0.842 | $1.8393 | 1.1s | 1.00 |  |
| B | `claude-fable-5` | 0.815 | +/-0.104 | 0.799 | 0.930 | 0.847 | $10.7552 | 6.6s | 1.00 |  |
| B | `qwen/qwen3.7-max` | 0.810 | +/-0.115 | 0.803 | 0.871 | 0.826 | $2.8109 | 23.4s | 0.99 |  |
| B | `qwen/qwen3.6-flash` | 0.808 | +/-0.093 | 0.807 | 0.838 | 0.815 | $0.5435 | 7.6s | 0.96 |  |
| B | `stealth/ox-alpha` | 0.806 | +/-0.128 | 0.806 | 0.830 | 0.810 | $0.0000 | 66.6s | 0.94 |  |
| B | `claude-opus-4-7` | 0.803 | +/-0.136 | 0.795 | 0.880 | 0.822 | $5.3293 | 4.6s | 1.00 |  |
| B | `openai/gpt-5.6-luna` | 0.796 | +/-0.125 | 0.786 | 0.874 | 0.817 | $0.2804 | 3.5s | 0.83 | (!) brittle JSON |
| B | `claude-opus-4-8` | 0.793 | +/-0.099 | 0.775 | 0.908 | 0.827 | $5.3661 | 7.8s | 1.00 |  |
| B | `google/gemini-3.1-pro-preview` | 0.791 | +/-0.119 | 0.779 | 0.877 | 0.815 | $4.9160 | 8.3s | 1.00 |  |
| B | `google/gemini-3.1-flash-lite` | 0.780 | +/-0.130 | 0.754 | 0.963 | 0.829 | $0.2988 | 0.8s | 0.94 | (!) fails no-ad control |
| B | `claude-sonnet-5` | 0.778 | +/-0.124 | 0.765 | 0.867 | 0.804 | $2.1452 | 8.5s | 1.00 |  |
| B | `claude-opus-5` | 0.776 | +/-0.128 | 0.762 | 0.888 | 0.806 | $5.3761 | 3.7s | 1.00 |  |
| B | `deepseek/deepseek-v3.2` | 0.775 | +/-0.090 | 0.891 | 0.594 | 0.676 | $0.2914 | 1.7s | 1.00 |  |
| B | `mistralai/mistral-medium-3.1` | 0.773 | +/-0.110 | 0.764 | 0.842 | 0.793 | $0.4847 | 0.7s | 1.00 |  |
| B | `openai/gpt-5.6-sol` | 0.771 | +/-0.117 | 0.757 | 0.874 | 0.799 | $2.4303 | 3.7s | 0.84 | (!) brittle JSON (!) fails no-ad control |
| B | `qwen/qwen3.7-flash` | 0.767 | +/-0.097 | 0.766 | 0.801 | 0.774 | $0.0725 | 10.1s | 0.96 |  |
| B | `deepseek/deepseek-v4-flash` | 0.763 | +/-0.117 | 0.782 | 0.748 | 0.749 | $0.0715 | 6.3s | 0.82 | (!) brittle JSON |
| B | `moonshotai/kimi-k3` | 0.760 | +/-0.130 | 0.758 | 0.811 | 0.770 | $5.9242 | 13.8s | 0.81 | (!) brittle JSON (!) fails no-ad control |
| B | `meta/muse-spark-1.1` | 0.750 | +/-0.091 | 0.844 | 0.588 | 0.668 | $2.2536 | 4.3s | 0.93 |  |
| C | `deepseek/deepseek-v4-pro` | 0.723 | +/-0.138 | 0.753 | 0.672 | 0.695 | $0.6106 | 16.2s | 0.87 | (!) brittle JSON (!) fails no-ad control |
| C | `google/gemini-2.5-pro` | 0.720 | +/-0.121 | 0.704 | 0.834 | 0.752 | $4.1601 | 14.2s | 0.96 | (!) fails no-ad control |
| C | `google/gemini-2.5-flash` | 0.706 | +/-0.137 | 0.676 | 0.896 | 0.761 | $0.3796 | 0.8s | 1.00 |  |
| C | `openai/gpt-oss-120b` | 0.698 | +/-0.133 | 0.677 | 0.840 | 0.739 | $0.0634 | 6.5s | 0.88 | (!) brittle JSON (!) fails no-ad control |
| C | `google/gemma-4-31b-it` | 0.695 | +/-0.129 | 0.698 | 0.729 | 0.699 | $0.1181 | 2.4s | 0.87 | (!) brittle JSON (!) fails no-ad control |
| C | `meta/muse-glimmer-30b` | 0.690 | +/-0.130 | 0.689 | 0.710 | 0.694 | $0.6965 | 10.8s | 0.89 | (!) brittle JSON |
| C | `minimax/minimax-m3` | 0.683 | +/-0.061 | 0.750 | 0.605 | 0.633 | $0.3575 | 1.7s | 0.88 | (!) brittle JSON (!) fails no-ad control |
| C | `qwen/qwen3.6-plus` | 0.671 | +/-0.124 | 0.689 | 0.677 | 0.661 | $1.1534 | 36.9s | 0.90 | (!) brittle JSON |
| C | `deepseek/deepseek-v4-flash-0731` | 0.660 | +/-0.153 | 0.720 | 0.562 | 0.607 | $0.1371 | 13.2s | 0.73 | (!) brittle JSON |
| C | `qwen/qwen3.5-27b` | 0.659 | +/-0.080 | 0.727 | 0.587 | 0.612 | $0.8941 | 37.6s | 0.70 | (!) brittle JSON |
| C | `nvidia/nemotron-3-super-120b-a12b` | 0.659 | +/-0.170 | 0.688 | 0.628 | 0.635 | $0.2194 | 22.3s | 0.79 | (!) brittle JSON (!) fails no-ad control |
| D | `openai/gpt-5.4` | 0.633 | +/-0.107 | 0.606 | 0.827 | 0.685 | $2.7907 | 1.4s | 0.80 | (!) brittle JSON (!) fails no-ad control |
| D | `google/gemini-2.5-flash-lite` | 0.625 | +/-0.136 | 0.594 | 0.906 | 0.689 | $0.1132 | 0.8s | 0.97 | (!) fails no-ad control |
| D | `deepseek/deepseek-r1` | 0.618 | +/-0.138 | 0.613 | 0.721 | 0.638 | $1.4743 | 35.7s | 0.84 | (!) brittle JSON (!) fails no-ad control |
| D | `z-ai/glm-5.2` | 0.614 | +/-0.123 | 0.596 | 0.808 | 0.657 | $1.3083 | 3.5s | 0.73 | (!) brittle JSON (!) fails no-ad control |
| D | `openai/o3` | 0.613 | +/-0.159 | 0.762 | 0.399 | 0.499 | $3.3100 | 6.7s | 0.93 |  |
| D | `qwen/qwen3.8-27b` | 0.610 | +/-0.210 | 0.686 | 0.503 | 0.550 | $1.3947 | 70.3s | 0.69 | (!) brittle JSON |
| D | `deepseek/deepseek-r1-0528` | 0.587 | +/-0.097 | 0.589 | 0.702 | 0.606 | $1.1645 | 29.4s | 0.84 | (!) brittle JSON (!) fails no-ad control |
| D | `openai/gpt-5.4-mini` | 0.583 | +/-0.148 | 0.554 | 0.831 | 0.640 | $0.8470 | 1.1s | 0.78 | (!) brittle JSON (!) fails no-ad control |
| D | `google/gemma-4-26b-a4b-it` | 0.582 | +/-0.170 | 0.566 | 0.691 | 0.612 | $0.0825 | 1.4s | 0.84 | (!) brittle JSON (!) fails no-ad control |
| D | `xiaomi/mimo-v2.5-pro` | 0.574 | +/-0.085 | 0.654 | 0.454 | 0.506 | $0.4954 | 2.2s | 0.90 | (!) fails no-ad control |
| D | `deepseek/deepseek-v4-pro-0813` | 0.565 | +/-0.228 | 0.632 | 0.459 | 0.507 | $2.4576 | 26.6s | 0.73 | (!) brittle JSON |
| D | `tencent/hy3` | 0.561 | +/-0.222 | 0.622 | 0.452 | 0.503 | $0.3876 | 22.3s | 0.62 | (!) brittle JSON |
| D | `openai/gpt-oss-20b` | 0.558 | +/-0.154 | 0.558 | 0.625 | 0.572 | $0.0620 | 9.2s | 0.80 | (!) brittle JSON (!) fails no-ad control |
| D | `mistralai/mistral-large-2512` | 0.539 | +/-0.161 | 0.503 | 0.913 | 0.612 | $0.6184 | 3.3s | 1.00 | (!) fails no-ad control |
| D | `meta-llama/llama-4-maverick` | 0.518 | +/-0.148 | 0.495 | 0.678 | 0.561 | $0.2206 | 1.1s | 0.79 | (!) brittle JSON (!) fails no-ad control |
| D | `nvidia/nemotron-3-ultra-550b-a55b` | 0.512 | +/-0.137 | 0.554 | 0.453 | 0.476 | $0.7782 | 1.2s | 0.89 | (!) brittle JSON (!) fails no-ad control |
| D | `meta-llama/llama-3.3-70b-instruct` | 0.511 | +/-0.164 | 0.514 | 0.531 | 0.514 | $0.1107 | 1.3s | 0.88 | (!) brittle JSON |
| D | `meta-llama/llama-4-scout` | 0.500 | +/-0.150 | 0.539 | 0.455 | 0.469 | $0.1111 | 0.9s | 0.83 | (!) brittle JSON (!) fails no-ad control |
| D | `meituan/longcat-2.0` | 0.499 | +/-0.161 | 0.556 | 0.402 | 0.448 | $0.5573 | 10.5s | 0.42 | (!) brittle JSON |
| D | `qwen/qwen3.8-2.4t-a95b` | 0.499 | +/-0.229 | 0.564 | 0.414 | 0.446 | $4.2877 | 11.9s | 0.62 | (!) brittle JSON (!) fails no-ad control |
| E | `qwen/qwen3-235b-a22b-2507` | 0.491 | +/-0.117 | 0.470 | 0.654 | 0.532 | $0.1078 | 2.5s | 0.84 | (!) brittle JSON (!) fails no-ad control |
| E | `qwen/qwen3.7-plus` | 0.487 | +/-0.112 | 0.456 | 0.786 | 0.551 | $0.7238 | 23.7s | 0.80 | (!) brittle JSON (!) fails no-ad control |
| E | `stepfun/step-3.7-flash` | 0.485 | +/-0.253 | 0.593 | 0.345 | 0.409 | $0.4302 | 13.9s | 0.56 | (!) brittle JSON (!!) moderation blocked 15.2% |
| E | `mistralai/codestral-2508` | 0.484 | +/-0.127 | 0.458 | 0.704 | 0.536 | $0.3585 | 0.9s | 1.00 | (!) fails no-ad control |
| E | `moonshotai/kimi-k2.6` | 0.469 | +/-0.183 | 0.535 | 0.406 | 0.423 | $1.7617 | 55.7s | 0.63 | (!) brittle JSON (!) fails no-ad control |
| E | `deepseek/deepseek-r1-distill-llama-70b` | 0.465 | +/-0.139 | 0.512 | 0.412 | 0.433 | $0.6913 | 27.2s | 0.39 | (!) brittle JSON (!) fails no-ad control |
| E | `microsoft/phi-4` | 0.461 | +/-0.225 | 0.524 | 0.362 | 0.408 | $0.0792 | 0.5s | 0.98 |  |
| E | `xiaomi/mimo-v2.5` | 0.459 | +/-0.151 | 0.454 | 0.615 | 0.487 | $0.1692 | 4.2s | 0.73 | (!) brittle JSON (!) fails no-ad control |
| E | `cohere/command-r-plus-08-2024` | 0.451 | +/-0.175 | 0.529 | 0.340 | 0.389 | $2.8505 | 1.1s | 0.93 |  |
| F | `cohere/command-a` | 0.412 | +/-0.128 | 0.386 | 0.638 | 0.464 | $2.9415 | 1.7s | 0.71 | (!) brittle JSON (!) fails no-ad control |
| F | `thinkingmachines/inkling-small` | 0.409 | +/-0.142 | 0.435 | 0.409 | 0.395 | $1.0333 | 18.5s | 0.58 | (!) brittle JSON (!) fails no-ad control |
| F | `inclusionai/ring-2.6-1t` | 0.391 | +/-0.199 | 0.453 | 0.301 | 0.342 | $0.2468 | 10.8s | 0.81 | (!) brittle JSON |
| F | `qwen/qwen3-14b` | 0.380 | +/-0.181 | 0.394 | 0.379 | 0.372 | $0.1724 | 14.4s | 0.60 | (!) brittle JSON (!) fails no-ad control |
| F | `qwen/qwen3.8-max` | 0.354 | +/-0.210 | 0.450 | 0.228 | 0.285 | $4.2714 | 29.7s | 0.73 | (!) brittle JSON |
| F | `thinkingmachines/inkling` | 0.302 | +/-0.155 | 0.396 | 0.184 | 0.236 | $3.1580 | 65.6s | 0.35 | (!) brittle JSON (!) fails no-ad control |
| G | `nvidia/nemotron-3.5-lightning` | 0.274 | +/-0.148 | 0.302 | 0.297 | 0.262 | $0.1207 | 1.2s | 0.79 | (!) brittle JSON (!) fails no-ad control |
| G | `openai/gpt-3.5-turbo` | 0.263 | +/-0.144 | 0.246 | 0.466 | 0.299 | $0.5564 | 1.4s | 0.70 | (!) brittle JSON (!) fails no-ad control |
| G | `meta-llama/llama-3.1-8b-instruct` | 0.254 | +/-0.113 | 0.257 | 0.308 | 0.261 | $0.0572 | 0.9s | 0.82 | (!) brittle JSON (!) fails no-ad control |
| G | `openai/o4-mini` | 0.193 | +/-0.093 | 0.278 | 0.095 | 0.137 | $2.1179 | 7.3s | 0.07 | (!) brittle JSON |
| H | `bytedance-seed/seed-2-1-turbo` | 0.052 | +/-0.077 | 0.067 | 0.028 | 0.039 | $1.4469 | 38.2s | 0.40 | (!) brittle JSON (!) fails no-ad control |
| H | `mistralai/mistral-small-2603` | 0.049 | +/-0.108 | 0.056 | 0.033 | 0.042 | $0.1745 | 0.7s | 1.00 |  |
| H | `qwen/qwen3-8b` | 0.000 | +/-0.000 | 0.000 | 0.000 | 0.000 | $0.3256 | 39.8s | 0.10 | (!) brittle JSON |

### Best Value (F0.5 per dollar)

Paid-tier only, ranked by F0.5 per dollar. Free-tier models are excluded here because F0.5 / 0 is undefined; they are ranked separately under Best Free-Tier below. No confidence tiers on this table, since a point ratio does not group cleanly, but the reliability flags still apply.

| Rank | Model | F0.5/$ | F0.5 | F1 | Cost / episode | Flags |
|------|-------|--------|------|----|----------------|-------|
| 1 | `openai/gpt-oss-120b` | 11.01 | 0.698 | 0.739 | $0.0634 | (!) brittle JSON (!) fails no-ad control |
| 2 | `deepseek/deepseek-v4-flash` | 10.67 | 0.763 | 0.749 | $0.0715 | (!) brittle JSON |
| 3 | `qwen/qwen3.7-flash` | 10.58 | 0.767 | 0.774 | $0.0725 |  |
| 4 | `openai/gpt-oss-20b` | 9.00 | 0.558 | 0.572 | $0.0620 | (!) brittle JSON (!) fails no-ad control |
| 5 | `google/gemma-4-26b-a4b-it` | 7.06 | 0.582 | 0.612 | $0.0825 | (!) brittle JSON (!) fails no-ad control |
| 6 | `google/gemma-4-31b-it` | 5.88 | 0.695 | 0.699 | $0.1181 | (!) brittle JSON (!) fails no-ad control |
| 7 | `microsoft/phi-4` | 5.82 | 0.461 | 0.408 | $0.0792 |  |
| 8 | `google/gemini-2.5-flash-lite` | 5.52 | 0.625 | 0.689 | $0.1132 | (!) fails no-ad control |
| 9 | `deepseek/deepseek-v4-flash-0731` | 4.82 | 0.660 | 0.607 | $0.1371 | (!) brittle JSON |
| 10 | `meta-llama/llama-3.3-70b-instruct` | 4.62 | 0.511 | 0.514 | $0.1107 | (!) brittle JSON |
| 11 | `qwen/qwen3-235b-a22b-2507` | 4.56 | 0.491 | 0.532 | $0.1078 | (!) brittle JSON (!) fails no-ad control |
| 12 | `meta-llama/llama-4-scout` | 4.51 | 0.500 | 0.469 | $0.1111 | (!) brittle JSON (!) fails no-ad control |
| 13 | `meta-llama/llama-3.1-8b-instruct` | 4.45 | 0.254 | 0.261 | $0.0572 | (!) brittle JSON (!) fails no-ad control |
| 14 | `nvidia/nemotron-3-super-120b-a12b` | 3.00 | 0.659 | 0.635 | $0.2194 | (!) brittle JSON (!) fails no-ad control |
| 15 | `openai/gpt-5.6-luna` | 2.84 | 0.796 | 0.817 | $0.2804 | (!) brittle JSON |
| 16 | `xiaomi/mimo-v2.5` | 2.71 | 0.459 | 0.487 | $0.1692 | (!) brittle JSON (!) fails no-ad control |
| 17 | `deepseek/deepseek-v3.2` | 2.66 | 0.775 | 0.676 | $0.2914 |  |
| 18 | `google/gemini-3.1-flash-lite` | 2.61 | 0.780 | 0.829 | $0.2988 | (!) fails no-ad control |
| 19 | `google/gemini-3.5-flash-lite` | 2.36 | 0.860 | 0.851 | $0.3643 |  |
| 20 | `meta-llama/llama-4-maverick` | 2.35 | 0.518 | 0.561 | $0.2206 | (!) brittle JSON (!) fails no-ad control |
| 21 | `nvidia/nemotron-3.5-lightning` | 2.27 | 0.274 | 0.262 | $0.1207 | (!) brittle JSON (!) fails no-ad control |
| 22 | `qwen/qwen3-14b` | 2.20 | 0.380 | 0.372 | $0.1724 | (!) brittle JSON (!) fails no-ad control |
| 23 | `minimax/minimax-m3` | 1.91 | 0.683 | 0.633 | $0.3575 | (!) brittle JSON (!) fails no-ad control |
| 24 | `google/gemini-2.5-flash` | 1.86 | 0.706 | 0.761 | $0.3796 |  |
| 25 | `mistralai/mistral-medium-3.1` | 1.60 | 0.773 | 0.793 | $0.4847 |  |
| 26 | `inclusionai/ring-2.6-1t` | 1.58 | 0.391 | 0.342 | $0.2468 | (!) brittle JSON |
| 27 | `qwen/qwen3.6-flash` | 1.49 | 0.808 | 0.815 | $0.5435 |  |
| 28 | `tencent/hy3` | 1.45 | 0.561 | 0.503 | $0.3876 | (!) brittle JSON |
| 29 | `mistralai/codestral-2508` | 1.35 | 0.484 | 0.536 | $0.3585 | (!) fails no-ad control |
| 30 | `google/gemini-3.7-flash` | 1.31 | 0.838 | 0.848 | $0.6411 |  |
| 31 | `deepseek/deepseek-v4-pro` | 1.18 | 0.723 | 0.695 | $0.6106 | (!) brittle JSON (!) fails no-ad control |
| 32 | `xiaomi/mimo-v2.5-pro` | 1.16 | 0.574 | 0.506 | $0.4954 | (!) fails no-ad control |
| 33 | `stepfun/step-3.7-flash` | 1.13 | 0.485 | 0.409 | $0.4302 | (!) brittle JSON (!!) moderation blocked 15.2% |
| 34 | `meta/muse-glimmer-30b` | 0.99 | 0.690 | 0.694 | $0.6965 | (!) brittle JSON |
| 35 | `meituan/longcat-2.0` | 0.90 | 0.499 | 0.448 | $0.5573 | (!) brittle JSON |
| 36 | `mistralai/mistral-large-2512` | 0.87 | 0.539 | 0.612 | $0.6184 | (!) fails no-ad control |
| 37 | `claude-haiku-4-5-20251001` | 0.84 | 0.908 | 0.920 | $1.0820 |  |
| 38 | `qwen/qwen3.5-plus-02-15` | 0.81 | 0.865 | 0.874 | $1.0746 |  |
| 39 | `qwen/qwen3.5-27b` | 0.74 | 0.659 | 0.612 | $0.8941 | (!) brittle JSON |
| 40 | `openai/gpt-5.4-mini` | 0.69 | 0.583 | 0.640 | $0.8470 | (!) brittle JSON (!) fails no-ad control |
| 41 | `qwen/qwen3.7-plus` | 0.67 | 0.487 | 0.551 | $0.7238 | (!) brittle JSON (!) fails no-ad control |
| 42 | `deepseek/deepseek-r1-distill-llama-70b` | 0.67 | 0.465 | 0.433 | $0.6913 | (!) brittle JSON (!) fails no-ad control |
| 43 | `nvidia/nemotron-3-ultra-550b-a55b` | 0.66 | 0.512 | 0.476 | $0.7782 | (!) brittle JSON (!) fails no-ad control |
| 44 | `google/gemini-3.6-flash` | 0.59 | 0.846 | 0.858 | $1.4431 |  |
| 45 | `qwen/qwen3.6-plus` | 0.58 | 0.671 | 0.661 | $1.1534 | (!) brittle JSON |
| 46 | `x-ai/grok-4.3` | 0.53 | 0.847 | 0.865 | $1.6048 |  |
| 47 | `deepseek/deepseek-r1-0528` | 0.50 | 0.587 | 0.606 | $1.1645 | (!) brittle JSON (!) fails no-ad control |
| 48 | `openai/gpt-3.5-turbo` | 0.47 | 0.263 | 0.299 | $0.5564 | (!) brittle JSON (!) fails no-ad control |
| 49 | `z-ai/glm-5.2` | 0.47 | 0.614 | 0.657 | $1.3083 | (!) brittle JSON (!) fails no-ad control |
| 50 | `mistralai/mistral-medium-3-5` | 0.45 | 0.822 | 0.842 | $1.8393 |  |
| 51 | `qwen/qwen3.8-27b` | 0.44 | 0.610 | 0.550 | $1.3947 | (!) brittle JSON |
| 52 | `deepseek/deepseek-r1` | 0.42 | 0.618 | 0.638 | $1.4743 | (!) brittle JSON (!) fails no-ad control |
| 53 | `thinkingmachines/inkling-small` | 0.40 | 0.409 | 0.395 | $1.0333 | (!) brittle JSON (!) fails no-ad control |
| 54 | `claude-sonnet-5` | 0.36 | 0.778 | 0.804 | $2.1452 |  |
| 55 | `openai/gpt-5.6-terra` | 0.34 | 0.823 | 0.830 | $2.3902 | (!) brittle JSON (!) fails no-ad control |
| 56 | `meta/muse-spark-1.1` | 0.33 | 0.750 | 0.668 | $2.2536 |  |
| 57 | `openai/gpt-5.6-sol` | 0.32 | 0.771 | 0.799 | $2.4303 | (!) brittle JSON (!) fails no-ad control |
| 58 | `qwen/qwen3.7-max` | 0.29 | 0.810 | 0.826 | $2.8109 |  |
| 59 | `mistralai/mistral-small-2603` | 0.28 | 0.049 | 0.042 | $0.1745 |  |
| 60 | `moonshotai/kimi-k2.6` | 0.27 | 0.469 | 0.423 | $1.7617 | (!) brittle JSON (!) fails no-ad control |
| 61 | `claude-sonnet-4-6` | 0.27 | 0.859 | 0.866 | $3.2386 |  |
| 62 | `x-ai/grok-4.5` | 0.25 | 0.843 | 0.861 | $3.3554 |  |
| 63 | `google/gemini-3.5-flash` | 0.25 | 0.851 | 0.867 | $3.4202 |  |
| 64 | `deepseek/deepseek-v4-pro-0813` | 0.23 | 0.565 | 0.507 | $2.4576 | (!) brittle JSON |
| 65 | `openai/gpt-5.4` | 0.23 | 0.633 | 0.685 | $2.7907 | (!) brittle JSON (!) fails no-ad control |
| 66 | `x-ai/grok-4.6` | 0.22 | 0.827 | 0.847 | $3.7063 |  |
| 67 | `openai/o3` | 0.19 | 0.613 | 0.499 | $3.3100 |  |
| 68 | `google/gemini-2.5-pro` | 0.17 | 0.720 | 0.752 | $4.1601 | (!) fails no-ad control |
| 69 | `google/gemini-3.1-pro-preview` | 0.16 | 0.791 | 0.815 | $4.9160 |  |
| 70 | `cohere/command-r-plus-08-2024` | 0.16 | 0.451 | 0.389 | $2.8505 |  |
| 71 | `claude-opus-4-7` | 0.15 | 0.803 | 0.822 | $5.3293 |  |
| 72 | `claude-opus-4-8` | 0.15 | 0.793 | 0.827 | $5.3661 |  |
| 73 | `claude-opus-5` | 0.14 | 0.776 | 0.806 | $5.3761 |  |
| 74 | `cohere/command-a` | 0.14 | 0.412 | 0.464 | $2.9415 | (!) brittle JSON (!) fails no-ad control |
| 75 | `moonshotai/kimi-k3` | 0.13 | 0.760 | 0.770 | $5.9242 | (!) brittle JSON (!) fails no-ad control |
| 76 | `qwen/qwen3.8-2.4t-a95b` | 0.12 | 0.499 | 0.446 | $4.2877 | (!) brittle JSON (!) fails no-ad control |
| 77 | `openai/gpt-5.5` | 0.11 | 0.839 | 0.847 | $7.6842 | (!) brittle JSON (!) fails no-ad control |
| 78 | `thinkingmachines/inkling` | 0.10 | 0.302 | 0.236 | $3.1580 | (!) brittle JSON (!) fails no-ad control |
| 79 | `openai/o4-mini` | 0.09 | 0.193 | 0.137 | $2.1179 | (!) brittle JSON |
| 80 | `qwen/qwen3.8-max` | 0.08 | 0.354 | 0.285 | $4.2714 | (!) brittle JSON |
| 81 | `claude-fable-5` | 0.08 | 0.815 | 0.847 | $10.7552 |  |
| 82 | `bytedance-seed/seed-2-1-turbo` | 0.04 | 0.052 | 0.039 | $1.4469 | (!) brittle JSON (!) fails no-ad control |
| 83 | `qwen/qwen3-8b` | 0.00 | 0.000 | 0.000 | $0.3256 | (!) brittle JSON |

### Best Free-Tier (F0.5)

Models that came back at $0.00 cost, ranked by F0.5 with the same CI and flags as Best Accuracy. Tiers are computed within the free-tier set against its own leader, so a tier letter here is not comparable to the same letter in Best Accuracy. Free-tier eligibility on OpenRouter depends on the attribution headers wired into the benchmark (`HTTP-Referer`, `X-Title`); a model showing as free here may bill on your own deployment if those headers are missing.

| Tier | Model | F0.5 | 95% CI | Precision | Recall | F1 | p50 latency | JSON compliance | Flags |
|------|-------|------|--------|-----------|--------|----|-------------|-----------------|-------|
| A | `stealth/ox-alpha` | 0.806 | +/-0.128 | 0.806 | 0.830 | 0.810 | 66.6s | 0.94 |  |

## Charts

### Cost vs F1 (Pareto)

Each model is one colored point. Lower-left is unhelpful (expensive, inaccurate). Upper-left is the sweet spot (accurate, cheap). The legend below the chart shows each model's color next to its F1 and cost-per-episode.

![Cost vs F1 by model](report_assets/pareto.svg)

Source data: [Best Accuracy](#best-accuracy-f05--iou--05), [Best Value](#best-value-f05-per-dollar), [Best Free-Tier](#best-free-tier-f05)

### Accuracy vs latency

F0.5 (y) against p50 latency (x, log scale). The cost Pareto above answers what accuracy costs in dollars; this one answers what it costs in wall-clock time. Upper-left is accurate and fast. MinusPod's pipeline is offline, so a slow accurate model is usable, but the chart shows which models make you choose and which don't. The OpenRouter latency caveat from the Metric Key applies.

![Accuracy vs latency by model](report_assets/accuracy_latency.svg)

Source data: [Best Accuracy](#best-accuracy-f05--iou--05) (F0.5), [Latency tail](#latency-tail) (p50)

### JSON schema compliance

Fraction of each model's responses that parsed as a clean JSON array. 1.0 means every response came back exactly as requested; lower numbers mean the parser had to recover from markdown fences, object wrappers, or extra fields.

![JSON compliance per model](report_assets/compliance.svg)

Source data: [Per-Model Detail](#per-model-detail) (`JSON compliance` field)

### F1 by episode (heatmap)

F1 score for each (model, episode) pair. Greener is more accurate, redder is less. The no-ad episodes are excluded. They have no F1 because they're PASS/FAIL negative controls.

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

### Cost split (input vs output)

Stacked horizontal bars per model: blue is the input share of per-episode cost, orange is the output share, total labeled at the right, sorted by total ascending. Every model reads the same transcripts, so a long blue bar is an expensive input price and a long orange bar is a talkative model. Reasoning models show up as mostly orange.

![Cost split per model](report_assets/cost_split.svg)

Source data: [Cost breakdown (input vs output)](#cost-breakdown-input-vs-output) table

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

**130 call(s) failed out of 71820 total (0.18%).** Failures are excluded from F1 / cost calculations, but they often surface real production-relevant gotchas worth knowing.

### By category

Errors classified into coarse buckets so failure patterns are visible at a glance. A model showing up here doesn't mean it's broken. Some categories are provider-side (content moderation, rate limits) and tell you more about routing reliability than model quality.

| Category | Calls | Affected models |
|----------|------:|-----------------|
| Provider content moderation rejection | 130 | `stepfun/step-3.7-flash` |

### Per-model error count

Same errors grouped by model, with the failure rate as a fraction of that model's total calls. Rates under 1% are usually one-off provider hiccups; rates above 5% suggest the model isn't operationally viable for production with the current prompts and concurrency caps.

| Model | Errors | of total |
|---|---:|---:|
| `stepfun/step-3.7-flash` | 130 | 130/855 (15.2%) |

### Sample messages (first 3 per category)

First three raw error messages per category, so you can see what the provider actually returned without grepping calls.jsonl. Messages are truncated to ~240 characters; full text lives in `results/raw/calls.jsonl`.

**Provider content moderation rejection** (130)
- `stepfun/step-3.7-flash` on `ep-drink-champs-30c9a2d49f13` (trial 0, window 4): Error code: 451 - {'error': {'message': 'Provider returned error', 'code': 451, 'metadata': {'raw': '{"error":{"message":"The content you provided or machine outputted is blocked.","type":"censorship_blocked"}}', 'provider_name': 'StepFun',...
- `stepfun/step-3.7-flash` on `ep-drink-champs-30c9a2d49f13` (trial 0, window 9): Error code: 451 - {'error': {'message': 'Provider returned error', 'code': 451, 'metadata': {'raw': '{"error":{"message":"The content you provided or machine outputted is blocked.","type":"censorship_blocked"}}', 'provider_name': 'StepFun',...
- `stepfun/step-3.7-flash` on `ep-drink-champs-30c9a2d49f13` (trial 0, window 10): Error code: 451 - {'error': {'message': 'Provider returned error', 'code': 451, 'metadata': {'raw': '{"error":{"message":"The content you provided or machine outputted is blocked.","type":"censorship_blocked"}}', 'provider_name': 'StepFun',...
- ... and 127 more

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
| `claude-haiku-4-5-20251001` | 0.900 | 0.946 | 229 | 34 | 16 |
| `qwen/qwen3.5-plus-02-15` | 0.862 | 0.900 | 223 | 37 | 22 |
| `google/gemini-3.5-flash` | 0.844 | 0.905 | 225 | 46 | 20 |
| `claude-sonnet-4-6` | 0.857 | 0.892 | 221 | 42 | 24 |
| `x-ai/grok-4.3` | 0.837 | 0.902 | 224 | 47 | 21 |
| `x-ai/grok-4.5` | 0.834 | 0.905 | 225 | 51 | 20 |
| `google/gemini-3.6-flash` | 0.841 | 0.891 | 221 | 49 | 24 |
| `google/gemini-3.5-flash-lite` | 0.878 | 0.857 | 210 | 39 | 35 |
| `google/gemini-3.7-flash` | 0.833 | 0.878 | 217 | 50 | 28 |
| `x-ai/grok-4.6` | 0.816 | 0.897 | 223 | 56 | 22 |
| `claude-fable-5` | 0.799 | 0.930 | 228 | 79 | 17 |
| `openai/gpt-5.5` | 0.836 | 0.870 | 213 | 41 | 32 |
| `mistralai/mistral-medium-3-5` | 0.811 | 0.887 | 221 | 58 | 24 |
| `openai/gpt-5.6-terra` | 0.822 | 0.858 | 209 | 51 | 36 |
| `google/gemini-3.1-flash-lite` | 0.754 | 0.963 | 234 | 106 | 11 |
| `claude-opus-4-8` | 0.775 | 0.908 | 221 | 70 | 24 |
| `qwen/qwen3.7-max` | 0.803 | 0.871 | 210 | 61 | 35 |
| `claude-opus-4-7` | 0.795 | 0.880 | 220 | 69 | 25 |
| `openai/gpt-5.6-luna` | 0.786 | 0.874 | 213 | 58 | 32 |
| `qwen/qwen3.6-flash` | 0.807 | 0.838 | 203 | 53 | 42 |
| `google/gemini-3.1-pro-preview` | 0.779 | 0.877 | 217 | 69 | 28 |
| `stealth/ox-alpha` | 0.806 | 0.830 | 196 | 58 | 49 |
| `claude-opus-5` | 0.762 | 0.888 | 221 | 69 | 24 |
| `claude-sonnet-5` | 0.765 | 0.867 | 214 | 79 | 31 |
| `openai/gpt-5.6-sol` | 0.757 | 0.874 | 215 | 80 | 30 |
| `mistralai/mistral-medium-3.1` | 0.764 | 0.842 | 205 | 73 | 40 |
| `qwen/qwen3.7-flash` | 0.766 | 0.801 | 194 | 61 | 51 |
| `moonshotai/kimi-k3` | 0.758 | 0.811 | 190 | 69 | 55 |
| `google/gemini-2.5-flash` | 0.676 | 0.896 | 224 | 115 | 21 |
| `google/gemini-2.5-pro` | 0.704 | 0.834 | 209 | 92 | 36 |
| `deepseek/deepseek-v4-flash` | 0.782 | 0.748 | 181 | 54 | 64 |
| `openai/gpt-oss-120b` | 0.677 | 0.840 | 197 | 140 | 48 |
| `google/gemma-4-31b-it` | 0.698 | 0.729 | 184 | 89 | 61 |
| `deepseek/deepseek-v4-pro` | 0.753 | 0.672 | 166 | 54 | 79 |
| `meta/muse-glimmer-30b` | 0.689 | 0.710 | 167 | 86 | 78 |
| `google/gemini-2.5-flash-lite` | 0.594 | 0.906 | 219 | 203 | 26 |
| `openai/gpt-5.4` | 0.606 | 0.827 | 207 | 146 | 38 |
| `deepseek/deepseek-v3.2` | 0.891 | 0.594 | 145 | 20 | 100 |
| `meta/muse-spark-1.1` | 0.844 | 0.588 | 145 | 32 | 100 |
| `qwen/qwen3.6-plus` | 0.689 | 0.677 | 163 | 72 | 82 |
| `z-ai/glm-5.2` | 0.596 | 0.808 | 197 | 173 | 48 |
| `openai/gpt-5.4-mini` | 0.554 | 0.831 | 203 | 244 | 42 |
| `deepseek/deepseek-r1` | 0.613 | 0.721 | 176 | 169 | 69 |
| `nvidia/nemotron-3-super-120b-a12b` | 0.688 | 0.628 | 140 | 64 | 105 |
| `minimax/minimax-m3` | 0.750 | 0.605 | 156 | 58 | 89 |
| `qwen/qwen3.5-27b` | 0.727 | 0.587 | 144 | 61 | 101 |
| `google/gemma-4-26b-a4b-it` | 0.566 | 0.691 | 164 | 128 | 81 |
| `mistralai/mistral-large-2512` | 0.503 | 0.913 | 227 | 385 | 18 |
| `deepseek/deepseek-v4-flash-0731` | 0.720 | 0.562 | 128 | 47 | 117 |
| `deepseek/deepseek-r1-0528` | 0.589 | 0.702 | 167 | 177 | 78 |
| `openai/gpt-oss-20b` | 0.558 | 0.625 | 144 | 131 | 101 |
| `meta-llama/llama-4-maverick` | 0.495 | 0.678 | 161 | 179 | 84 |
| `qwen/qwen3.7-plus` | 0.456 | 0.786 | 191 | 319 | 54 |
| `qwen/qwen3.8-27b` | 0.686 | 0.503 | 111 | 33 | 134 |
| `mistralai/codestral-2508` | 0.458 | 0.704 | 172 | 236 | 73 |
| `qwen/qwen3-235b-a22b-2507` | 0.470 | 0.654 | 155 | 213 | 90 |
| `meta-llama/llama-3.3-70b-instruct` | 0.514 | 0.531 | 124 | 145 | 121 |
| `deepseek/deepseek-v4-pro-0813` | 0.632 | 0.459 | 99 | 33 | 146 |
| `xiaomi/mimo-v2.5-pro` | 0.654 | 0.454 | 112 | 65 | 133 |
| `tencent/hy3` | 0.622 | 0.452 | 105 | 38 | 140 |
| `openai/o3` | 0.762 | 0.399 | 98 | 16 | 147 |
| `xiaomi/mimo-v2.5` | 0.454 | 0.615 | 161 | 244 | 84 |
| `nvidia/nemotron-3-ultra-550b-a55b` | 0.554 | 0.453 | 101 | 100 | 144 |
| `meta-llama/llama-4-scout` | 0.539 | 0.455 | 112 | 150 | 133 |
| `cohere/command-a` | 0.386 | 0.638 | 144 | 337 | 101 |
| `meituan/longcat-2.0` | 0.556 | 0.402 | 102 | 63 | 143 |
| `qwen/qwen3.8-2.4t-a95b` | 0.564 | 0.414 | 92 | 34 | 153 |
| `deepseek/deepseek-r1-distill-llama-70b` | 0.512 | 0.412 | 100 | 88 | 145 |
| `moonshotai/kimi-k2.6` | 0.535 | 0.406 | 92 | 78 | 153 |
| `stepfun/step-3.7-flash` | 0.593 | 0.345 | 60 | 8 | 130 |
| `microsoft/phi-4` | 0.524 | 0.362 | 77 | 74 | 168 |
| `thinkingmachines/inkling-small` | 0.435 | 0.409 | 87 | 106 | 158 |
| `cohere/command-r-plus-08-2024` | 0.529 | 0.340 | 91 | 54 | 154 |
| `qwen/qwen3-14b` | 0.394 | 0.379 | 87 | 168 | 158 |
| `inclusionai/ring-2.6-1t` | 0.453 | 0.301 | 66 | 79 | 179 |
| `openai/gpt-3.5-turbo` | 0.246 | 0.466 | 103 | 491 | 142 |
| `qwen/qwen3.8-max` | 0.450 | 0.228 | 45 | 13 | 200 |
| `nvidia/nemotron-3.5-lightning` | 0.302 | 0.297 | 72 | 351 | 173 |
| `meta-llama/llama-3.1-8b-instruct` | 0.257 | 0.308 | 67 | 315 | 178 |
| `thinkingmachines/inkling` | 0.396 | 0.184 | 41 | 30 | 204 |
| `openai/o4-mini` | 0.278 | 0.095 | 21 | 29 | 224 |
| `mistralai/mistral-small-2603` | 0.056 | 0.033 | 10 | 5 | 235 |
| `bytedance-seed/seed-2-1-turbo` | 0.067 | 0.028 | 4 | 5 | 241 |
| `qwen/qwen3-8b` | 0.000 | 0.000 | 0 | 1 | 245 |

## Boundary accuracy

For ads that match the truth at IoU >= 0.5, how far off were the predicted start and end timestamps? Lower is better. A model can hit F1 cleanly while still being 20s off on every boundary. Bad for any pipeline that cuts the audio.

MAE is size of the miss; bias is its direction (mean of predicted minus truth). A negative start bias or positive end bias means the cut extends past the ad and eats surrounding content; the opposite signs mean ad audio is left in. MinusPod cuts what the model flags, so a model whose bias points outward over-cuts even when its MAE looks acceptable. Bias near zero with a large MAE means the misses are random rather than systematic.

| Model | Start MAE (s) | End MAE (s) | Start bias (s) | End bias (s) |
|---|---:|---:|---:|---:|
| `bytedance-seed/seed-2-1-turbo` | 0.03 | 0.01 | -0.01 | -0.01 |
| `mistralai/mistral-small-2603` | 0.01 | 2.64 | -0.01 | -2.64 |
| `stepfun/step-3.7-flash` | 0.73 | 3.43 | -0.17 | -2.15 |
| `deepseek/deepseek-v4-pro-0813` | 1.39 | 3.02 | -0.57 | -2.06 |
| `qwen/qwen3.5-plus-02-15` | 4.41 | 1.20 | -2.07 | -0.42 |
| `qwen/qwen3.8-27b` | 2.79 | 2.92 | -2.77 | -2.27 |
| `qwen/qwen3.6-plus` | 3.30 | 2.59 | -2.37 | -1.71 |
| `qwen/qwen3.7-plus` | 2.89 | 3.46 | -0.61 | -2.94 |
| `google/gemini-3.7-flash` | 3.12 | 3.28 | -0.26 | -2.24 |
| `claude-haiku-4-5-20251001` | 2.67 | 3.74 | +1.73 | -2.26 |
| `google/gemini-3.5-flash` | 3.42 | 3.14 | -0.33 | -2.26 |
| `google/gemini-3.1-flash-lite` | 2.41 | 4.20 | -0.06 | -3.23 |
| `google/gemini-3.6-flash` | 3.59 | 3.16 | +0.81 | -2.30 |
| `claude-opus-4-8` | 4.22 | 2.63 | -1.66 | -1.23 |
| `claude-sonnet-4-6` | 4.13 | 2.81 | -1.32 | -1.93 |
| `qwen/qwen3.7-flash` | 4.42 | 2.55 | +0.83 | -1.31 |
| `deepseek/deepseek-v4-flash` | 2.81 | 4.24 | +0.12 | -2.21 |
| `google/gemini-3.5-flash-lite` | 2.54 | 4.52 | +2.02 | -3.16 |
| `moonshotai/kimi-k2.6` | 4.62 | 2.45 | +0.19 | -2.44 |
| `claude-fable-5` | 3.79 | 3.35 | -1.00 | -0.88 |
| `qwen/qwen3.6-flash` | 4.52 | 2.67 | +0.14 | -1.21 |
| `x-ai/grok-4.5` | 4.03 | 3.19 | -1.09 | -2.43 |
| `stealth/ox-alpha` | 4.26 | 3.08 | -0.58 | -2.36 |
| `qwen/qwen3.8-2.4t-a95b` | 3.28 | 4.11 | -1.76 | -3.71 |
| `deepseek/deepseek-r1` | 3.09 | 4.34 | +1.53 | -3.57 |
| `qwen/qwen3-14b` | 1.59 | 5.89 | +1.45 | -4.66 |
| `deepseek/deepseek-v4-flash-0731` | 2.47 | 5.03 | -0.75 | -3.57 |
| `x-ai/grok-4.6` | 4.13 | 3.43 | -0.25 | -2.06 |
| `claude-sonnet-5` | 4.36 | 3.33 | -0.97 | -1.45 |
| `qwen/qwen3.8-max` | 0.82 | 7.06 | +0.03 | -6.43 |
| `openai/gpt-5.5` | 4.94 | 2.99 | -2.32 | -1.69 |
| `claude-opus-4-7` | 5.79 | 2.17 | -3.02 | +0.01 |
| `thinkingmachines/inkling` | 4.32 | 3.68 | -0.64 | -1.37 |
| `mistralai/mistral-medium-3-5` | 2.18 | 5.82 | +1.48 | -4.18 |
| `deepseek/deepseek-r1-0528` | 3.64 | 4.38 | +1.89 | -3.91 |
| `openai/gpt-5.6-terra` | 4.22 | 3.80 | -1.13 | -2.85 |
| `minimax/minimax-m3` | 5.35 | 2.86 | +2.04 | -1.08 |
| `qwen/qwen3.7-max` | 3.85 | 4.39 | -0.93 | -3.12 |
| `tencent/hy3` | 4.69 | 3.71 | -0.70 | -3.49 |
| `deepseek/deepseek-v4-pro` | 3.74 | 4.73 | +0.22 | -3.07 |
| `google/gemini-3.1-pro-preview` | 4.05 | 4.44 | -1.15 | -0.27 |
| `deepseek/deepseek-v3.2` | 5.58 | 3.00 | -1.48 | -0.87 |
| `qwen/qwen3.5-27b` | 6.07 | 2.52 | -1.41 | -1.54 |
| `google/gemini-2.5-flash` | 4.99 | 3.65 | -1.53 | -2.68 |
| `openai/gpt-5.6-luna` | 5.12 | 3.74 | -2.65 | -1.47 |
| `meta/muse-glimmer-30b` | 3.19 | 5.79 | +0.51 | -5.37 |
| `moonshotai/kimi-k3` | 4.20 | 4.86 | -1.05 | -3.50 |
| `x-ai/grok-4.3` | 5.06 | 4.01 | +0.02 | -3.59 |
| `openai/gpt-5.4` | 4.01 | 5.22 | -0.60 | -3.75 |
| `thinkingmachines/inkling-small` | 2.51 | 6.74 | +1.29 | -6.73 |
| `openai/gpt-5.6-sol` | 5.80 | 3.54 | -2.49 | -2.57 |
| `meta/muse-spark-1.1` | 5.74 | 3.80 | -1.08 | -2.68 |
| `google/gemma-4-31b-it` | 5.42 | 4.12 | -4.03 | -3.33 |
| `mistralai/mistral-large-2512` | 3.80 | 5.87 | +0.84 | -4.05 |
| `meituan/longcat-2.0` | 3.55 | 6.26 | +0.17 | -5.56 |
| `openai/o3` | 5.74 | 4.31 | -2.72 | -0.26 |
| `mistralai/mistral-medium-3.1` | 5.17 | 4.95 | +1.03 | -4.29 |
| `meta-llama/llama-3.3-70b-instruct` | 3.65 | 6.50 | +3.63 | -6.19 |
| `z-ai/glm-5.2` | 4.80 | 5.44 | -1.24 | -3.99 |
| `google/gemini-2.5-flash-lite` | 3.63 | 6.66 | -0.06 | -5.87 |
| `google/gemini-2.5-pro` | 6.78 | 3.57 | -4.38 | -1.69 |
| `claude-opus-5` | 7.91 | 2.75 | -3.82 | -0.57 |
| `nvidia/nemotron-3-super-120b-a12b` | 5.87 | 4.82 | +3.61 | -3.02 |
| `xiaomi/mimo-v2.5` | 4.61 | 8.00 | -0.16 | -3.75 |
| `xiaomi/mimo-v2.5-pro` | 4.12 | 8.51 | +0.32 | -3.93 |
| `cohere/command-a` | 2.67 | 10.31 | -0.59 | -8.72 |
| `qwen/qwen3-235b-a22b-2507` | 3.32 | 9.66 | -0.18 | -9.53 |
| `google/gemma-4-26b-a4b-it` | 1.11 | 12.16 | +0.22 | -10.72 |
| `openai/gpt-oss-20b` | 7.45 | 6.01 | +2.90 | -3.91 |
| `openai/gpt-5.4-mini` | 4.14 | 9.42 | -0.49 | -7.46 |
| `openai/gpt-oss-120b` | 8.54 | 5.05 | +1.81 | -3.57 |
| `nvidia/nemotron-3-ultra-550b-a55b` | 4.95 | 8.71 | +1.69 | -7.58 |
| `deepseek/deepseek-r1-distill-llama-70b` | 5.60 | 8.58 | +4.72 | -5.42 |
| `nvidia/nemotron-3.5-lightning` | 7.11 | 7.21 | -0.87 | -4.39 |
| `mistralai/codestral-2508` | 6.26 | 10.50 | +2.12 | -9.87 |
| `inclusionai/ring-2.6-1t` | 2.42 | 15.09 | +0.50 | -12.33 |
| `openai/o4-mini` | 5.18 | 13.50 | -2.20 | -13.48 |
| `meta-llama/llama-4-maverick` | 6.14 | 14.02 | -3.43 | -11.06 |
| `microsoft/phi-4` | 6.85 | 13.71 | +3.91 | -11.77 |
| `openai/gpt-3.5-turbo` | 7.84 | 13.95 | +6.98 | -12.73 |
| `meta-llama/llama-4-scout` | 6.34 | 16.17 | +3.46 | -14.94 |
| `cohere/command-r-plus-08-2024` | 6.06 | 16.70 | -0.32 | -11.26 |
| `meta-llama/llama-3.1-8b-instruct` | 16.93 | 8.01 | +2.03 | -2.76 |

## Confidence calibration

Models include a self-reported `confidence` on each detected ad. A well-calibrated model should be right ~95% of the time when it claims 0.95 confidence. The table below bins each model's predictions and shows the actual hit rate (fraction that were true positives at IoU >= 0.5). A bin near 1.0 is well-calibrated; a low number with a high count means the model is overconfident.

| Model | 0.00-0.70 | 0.70-0.90 | 0.90-0.95 | 0.95-0.99 | 0.99+ | total |
|---|---:|---:|---:|---:|---:|---:|
| `bytedance-seed/seed-2-1-turbo` | 0.00 (n=3) | 0.00 (n=2) | 0.00 (n=1) | 1.00 (n=4) | -- | 10 |
| `claude-fable-5` | 0.00 (n=17) | 0.00 (n=41) | 0.71 (n=7) | 0.92 (n=242) | -- | 307 |
| `claude-haiku-4-5-20251001` | 0.00 (n=1) | 0.44 (n=9) | 0.33 (n=9) | 0.91 (n=211) | 0.91 (n=33) | 263 |
| `claude-opus-4-7` | 0.00 (n=8) | 0.00 (n=17) | 0.00 (n=27) | 0.93 (n=228) | 1.00 (n=9) | 289 |
| `claude-opus-4-8` | 0.00 (n=9) | 0.00 (n=23) | 0.21 (n=19) | 0.90 (n=240) | -- | 291 |
| `claude-opus-5` | 0.00 (n=7) | 0.00 (n=31) | 0.00 (n=15) | 0.93 (n=237) | -- | 290 |
| `claude-sonnet-4-6` | 0.00 (n=2) | 0.05 (n=21) | 0.83 (n=6) | 0.94 (n=196) | 0.82 (n=38) | 263 |
| `claude-sonnet-5` | 0.00 (n=17) | 0.06 (n=31) | 0.31 (n=13) | 0.90 (n=232) | -- | 293 |
| `cohere/command-a` | 0.00 (n=60) | 0.00 (n=30) | 0.00 (n=45) | 0.38 (n=360) | 0.45 (n=20) | 515 |
| `cohere/command-r-plus-08-2024` | -- | -- | 0.60 (n=15) | 0.72 (n=54) | 0.57 (n=76) | 145 |
| `deepseek/deepseek-r1` | -- | 0.10 (n=10) | 0.00 (n=17) | 0.29 (n=171) | 0.81 (n=155) | 353 |
| `deepseek/deepseek-r1-0528` | 0.00 (n=6) | 0.00 (n=12) | 0.00 (n=19) | 0.28 (n=180) | 0.82 (n=141) | 358 |
| `deepseek/deepseek-r1-distill-llama-70b` | -- | 0.56 (n=9) | 0.00 (n=4) | 0.53 (n=145) | 0.51 (n=35) | 193 |
| `deepseek/deepseek-v3.2` | -- | -- | 0.00 (n=3) | 0.82 (n=61) | 0.94 (n=101) | 165 |
| `deepseek/deepseek-v4-flash` | 0.00 (n=9) | 0.00 (n=5) | 0.33 (n=3) | 0.81 (n=181) | 0.89 (n=37) | 235 |
| `deepseek/deepseek-v4-flash-0731` | 0.33 (n=3) | 0.00 (n=5) | -- | 0.76 (n=158) | 0.78 (n=9) | 175 |
| `deepseek/deepseek-v4-pro` | 0.00 (n=11) | 0.00 (n=9) | 0.00 (n=3) | 0.82 (n=164) | 0.91 (n=34) | 221 |
| `deepseek/deepseek-v4-pro-0813` | 0.00 (n=2) | 0.17 (n=6) | 0.00 (n=5) | 0.81 (n=89) | 0.87 (n=30) | 132 |
| `google/gemini-2.5-flash` | -- | 0.00 (n=20) | 0.17 (n=82) | 0.85 (n=147) | 0.94 (n=90) | 339 |
| `google/gemini-2.5-flash-lite` | -- | 0.00 (n=2) | 0.00 (n=58) | 0.60 (n=367) | -- | 427 |
| `google/gemini-2.5-pro` | -- | 0.00 (n=10) | 0.00 (n=22) | 0.54 (n=68) | 0.82 (n=209) | 309 |
| `google/gemini-3.1-flash-lite` | -- | 0.00 (n=3) | 0.00 (n=6) | 0.31 (n=117) | 0.90 (n=219) | 345 |
| `google/gemini-3.1-pro-preview` | -- | 0.00 (n=13) | 0.00 (n=6) | 0.19 (n=32) | 0.90 (n=235) | 286 |
| `google/gemini-3.5-flash` | -- | 0.00 (n=7) | 0.00 (n=1) | 0.60 (n=50) | 0.92 (n=213) | 271 |
| `google/gemini-3.5-flash-lite` | -- | 0.00 (n=1) | 0.00 (n=2) | 0.85 (n=209) | 0.86 (n=37) | 249 |
| `google/gemini-3.6-flash` | -- | 0.00 (n=4) | 0.00 (n=1) | 0.79 (n=206) | 1.00 (n=59) | 270 |
| `google/gemini-3.7-flash` | -- | 0.00 (n=11) | 0.00 (n=1) | 0.75 (n=108) | 0.93 (n=147) | 267 |
| `google/gemma-4-26b-a4b-it` | -- | 0.00 (n=9) | 0.12 (n=40) | 0.56 (n=140) | 0.74 (n=108) | 297 |
| `google/gemma-4-31b-it` | -- | 0.00 (n=7) | 0.07 (n=27) | 0.54 (n=80) | 0.85 (n=164) | 278 |
| `inclusionai/ring-2.6-1t` | 0.00 (n=4) | 0.07 (n=15) | 0.25 (n=16) | 0.58 (n=103) | 0.14 (n=7) | 145 |
| `meituan/longcat-2.0` | 0.00 (n=2) | 0.00 (n=13) | 0.08 (n=12) | 0.73 (n=138) | -- | 165 |
| `meta-llama/llama-3.1-8b-instruct` | 0.00 (n=17) | 0.22 (n=9) | 0.00 (n=22) | 0.20 (n=332) | 0.00 (n=8) | 388 |
| `meta-llama/llama-3.3-70b-instruct` | -- | 0.00 (n=11) | 0.00 (n=21) | 0.48 (n=155) | 0.60 (n=82) | 269 |
| `meta-llama/llama-4-maverick` | 0.00 (n=21) | 0.00 (n=60) | 0.11 (n=27) | 0.67 (n=237) | -- | 345 |
| `meta-llama/llama-4-scout` | 0.00 (n=12) | 0.00 (n=1) | 0.10 (n=21) | 0.48 (n=176) | 0.49 (n=53) | 263 |
| `meta/muse-glimmer-30b` | 0.00 (n=10) | 0.00 (n=18) | 0.18 (n=22) | 0.80 (n=203) | -- | 253 |
| `meta/muse-spark-1.1` | -- | 0.00 (n=9) | 0.11 (n=9) | 0.91 (n=158) | 1.00 (n=1) | 177 |
| `microsoft/phi-4` | -- | -- | 0.00 (n=4) | 0.52 (n=147) | -- | 151 |
| `minimax/minimax-m3` | 0.00 (n=14) | 0.05 (n=19) | 0.20 (n=5) | 0.84 (n=168) | 0.93 (n=14) | 220 |
| `mistralai/codestral-2508` | -- | 0.00 (n=2) | 0.10 (n=203) | 0.73 (n=208) | -- | 413 |
| `mistralai/mistral-large-2512` | 0.00 (n=15) | 0.00 (n=86) | 0.00 (n=57) | 0.22 (n=265) | 0.87 (n=194) | 617 |
| `mistralai/mistral-medium-3-5` | -- | -- | -- | 0.59 (n=63) | 0.85 (n=216) | 279 |
| `mistralai/mistral-medium-3.1` | -- | -- | 0.10 (n=10) | 0.77 (n=256) | 0.67 (n=12) | 278 |
| `mistralai/mistral-small-2603` | -- | -- | -- | 0.00 (n=5) | 1.00 (n=10) | 15 |
| `moonshotai/kimi-k2.6` | 0.00 (n=25) | 0.00 (n=18) | 0.00 (n=4) | 0.51 (n=79) | 0.95 (n=55) | 181 |
| `moonshotai/kimi-k3` | 0.00 (n=5) | 0.04 (n=27) | 0.00 (n=17) | 0.89 (n=204) | 1.00 (n=7) | 260 |
| `nvidia/nemotron-3-super-120b-a12b` | 0.00 (n=1) | 0.00 (n=4) | 0.00 (n=7) | 0.66 (n=149) | 0.93 (n=45) | 206 |
| `nvidia/nemotron-3-ultra-550b-a55b` | 0.00 (n=18) | 0.00 (n=4) | -- | 0.50 (n=171) | 0.84 (n=19) | 212 |
| `nvidia/nemotron-3.5-lightning` | 0.00 (n=1) | 0.04 (n=28) | 0.03 (n=74) | 0.21 (n=333) | -- | 436 |
| `openai/gpt-3.5-turbo` | -- | -- | 0.00 (n=30) | 0.19 (n=547) | 0.00 (n=76) | 653 |
| `openai/gpt-5.4` | 0.00 (n=58) | 0.03 (n=39) | 0.06 (n=16) | 0.55 (n=47) | 0.87 (n=206) | 366 |
| `openai/gpt-5.4-mini` | 0.00 (n=89) | 0.00 (n=66) | 0.00 (n=35) | 0.47 (n=51) | 0.82 (n=218) | 459 |
| `openai/gpt-5.5` | 0.00 (n=7) | 0.09 (n=22) | 0.57 (n=7) | 0.90 (n=84) | 0.96 (n=136) | 256 |
| `openai/gpt-5.6-luna` | 0.00 (n=7) | 0.17 (n=23) | 0.00 (n=7) | 0.38 (n=24) | 0.95 (n=210) | 271 |
| `openai/gpt-5.6-sol` | 0.00 (n=10) | 0.14 (n=22) | 0.00 (n=10) | 0.06 (n=32) | 0.91 (n=230) | 304 |
| `openai/gpt-5.6-terra` | 0.00 (n=7) | 0.00 (n=18) | 0.20 (n=10) | 0.44 (n=18) | 0.94 (n=212) | 265 |
| `openai/gpt-oss-120b` | 0.00 (n=1) | 0.00 (n=4) | 0.00 (n=3) | 0.34 (n=136) | 0.76 (n=199) | 343 |
| `openai/gpt-oss-20b` | -- | 0.00 (n=2) | 0.07 (n=15) | 0.39 (n=127) | 0.68 (n=137) | 281 |
| `openai/o3` | 0.00 (n=2) | 0.00 (n=3) | 0.75 (n=16) | 0.92 (n=90) | 1.00 (n=3) | 114 |
| `openai/o4-mini` | 0.00 (n=1) | 0.33 (n=3) | 0.00 (n=6) | 0.51 (n=39) | 0.00 (n=1) | 50 |
| `qwen/qwen3-14b` | 0.00 (n=16) | 0.00 (n=18) | 0.60 (n=5) | 0.38 (n=221) | -- | 260 |
| `qwen/qwen3-235b-a22b-2507` | 0.00 (n=18) | 0.00 (n=3) | 0.00 (n=1) | 0.44 (n=344) | 0.17 (n=23) | 389 |
| `qwen/qwen3-8b` | -- | -- | -- | 0.00 (n=1) | -- | 1 |
| `qwen/qwen3.5-27b` | -- | 0.20 (n=5) | 0.12 (n=16) | 0.77 (n=184) | -- | 205 |
| `qwen/qwen3.5-plus-02-15` | -- | 0.00 (n=4) | 0.00 (n=4) | 0.88 (n=237) | 1.00 (n=15) | 260 |
| `qwen/qwen3.6-flash` | 0.00 (n=1) | 0.00 (n=7) | 0.00 (n=6) | 0.84 (n=239) | 1.00 (n=3) | 256 |
| `qwen/qwen3.6-plus` | -- | -- | 1.00 (n=1) | 0.70 (n=220) | 0.64 (n=14) | 235 |
| `qwen/qwen3.7-flash` | -- | 0.00 (n=9) | 0.36 (n=11) | 0.81 (n=234) | 1.00 (n=1) | 255 |
| `qwen/qwen3.7-max` | 0.00 (n=9) | 0.06 (n=17) | 0.29 (n=7) | 0.87 (n=238) | -- | 271 |
| `qwen/qwen3.7-plus` | 0.00 (n=184) | 0.12 (n=26) | 0.00 (n=2) | 0.56 (n=337) | -- | 549 |
| `qwen/qwen3.8-2.4t-a95b` | 0.00 (n=14) | 0.17 (n=12) | 0.00 (n=6) | 0.93 (n=92) | 1.00 (n=4) | 128 |
| `qwen/qwen3.8-27b` | 0.00 (n=5) | 0.00 (n=11) | 0.20 (n=5) | 0.89 (n=123) | -- | 144 |
| `qwen/qwen3.8-max` | -- | -- | -- | 0.74 (n=42) | 0.88 (n=16) | 58 |
| `stealth/ox-alpha` | 0.00 (n=11) | 0.00 (n=18) | 0.44 (n=9) | 0.89 (n=210) | 1.00 (n=6) | 254 |
| `stepfun/step-3.7-flash` | -- | -- | 0.80 (n=5) | 0.89 (n=62) | 1.00 (n=1) | 68 |
| `tencent/hy3` | 0.11 (n=9) | 0.00 (n=6) | 0.00 (n=2) | 0.82 (n=125) | 1.00 (n=1) | 143 |
| `thinkingmachines/inkling` | 0.00 (n=1) | 0.14 (n=7) | 0.00 (n=4) | 0.52 (n=29) | 0.81 (n=31) | 72 |
| `thinkingmachines/inkling-small` | 0.00 (n=38) | 0.00 (n=9) | 0.00 (n=15) | 0.67 (n=129) | 0.05 (n=19) | 210 |
| `x-ai/grok-4.3` | -- | 0.25 (n=4) | 0.07 (n=14) | 0.87 (n=232) | 1.00 (n=21) | 271 |
| `x-ai/grok-4.5` | -- | 0.00 (n=7) | 0.00 (n=11) | 0.88 (n=224) | 0.79 (n=34) | 276 |
| `x-ai/grok-4.6` | 0.00 (n=1) | 0.00 (n=25) | 0.00 (n=11) | 0.94 (n=194) | 0.83 (n=48) | 279 |
| `xiaomi/mimo-v2.5` | 0.00 (n=26) | 0.02 (n=44) | 0.00 (n=28) | 0.42 (n=252) | 0.68 (n=80) | 430 |
| `xiaomi/mimo-v2.5-pro` | 0.00 (n=1) | 0.00 (n=15) | 0.00 (n=10) | 0.70 (n=115) | 0.86 (n=37) | 178 |
| `z-ai/glm-5.2` | 0.03 (n=113) | 0.00 (n=28) | 0.00 (n=8) | 0.80 (n=218) | 0.83 (n=23) | 390 |

See `report_assets/calibration.svg` for the visual reliability diagram.

## Latency tail

Median latency hides outliers. p99 and max are what determines queue depth and worst-case user wait. For OpenRouter-routed models the tail also reflects upstream provider load, not just model compute.

| Model | p50 | p90 | p95 | p99 | max |
|---|---:|---:|---:|---:|---:|
| `microsoft/phi-4` | 0.49s | 3.14s | 5.50s | 69.96s | 101.52s |
| `mistralai/mistral-small-2603` | 0.66s | 0.98s | 1.16s | 2.27s | 6.56s |
| `google/gemini-3.5-flash-lite` | 0.67s | 1.23s | 1.42s | 1.77s | 2.60s |
| `mistralai/mistral-medium-3.1` | 0.74s | 5.83s | 7.65s | 10.73s | 16.46s |
| `google/gemini-2.5-flash` | 0.83s | 1.91s | 2.21s | 2.98s | 15.68s |
| `google/gemini-2.5-flash-lite` | 0.84s | 1.50s | 1.75s | 3.01s | 6.73s |
| `google/gemini-3.1-flash-lite` | 0.85s | 1.57s | 2.11s | 12.92s | 46.86s |
| `mistralai/codestral-2508` | 0.88s | 2.14s | 2.54s | 3.45s | 8.33s |
| `meta-llama/llama-4-scout` | 0.93s | 3.35s | 4.49s | 7.51s | 49.85s |
| `meta-llama/llama-3.1-8b-instruct` | 0.93s | 2.92s | 4.34s | 5.14s | 9.27s |
| `mistralai/mistral-medium-3-5` | 1.10s | 2.80s | 3.29s | 4.54s | 14.30s |
| `meta-llama/llama-4-maverick` | 1.12s | 2.72s | 3.59s | 4.60s | 37.92s |
| `openai/gpt-5.4-mini` | 1.13s | 1.86s | 2.45s | 3.16s | 6.40s |
| `cohere/command-r-plus-08-2024` | 1.13s | 13.03s | 16.24s | 24.51s | 62.18s |
| `nvidia/nemotron-3.5-lightning` | 1.16s | 13.37s | 14.47s | 17.84s | 23.95s |
| `nvidia/nemotron-3-ultra-550b-a55b` | 1.24s | 3.00s | 5.94s | 14.87s | 68.31s |
| `meta-llama/llama-3.3-70b-instruct` | 1.29s | 5.55s | 8.60s | 19.20s | 41.61s |
| `openai/gpt-3.5-turbo` | 1.38s | 1.82s | 1.96s | 2.31s | 3.06s |
| `google/gemma-4-26b-a4b-it` | 1.40s | 4.17s | 6.52s | 13.03s | 99.69s |
| `openai/gpt-5.4` | 1.43s | 2.03s | 2.57s | 3.84s | 15.05s |
| `minimax/minimax-m3` | 1.68s | 4.84s | 6.67s | 15.92s | 146.60s |
| `cohere/command-a` | 1.72s | 2.54s | 2.92s | 4.17s | 5.73s |
| `deepseek/deepseek-v3.2` | 1.74s | 8.05s | 10.84s | 15.95s | 88.47s |
| `openai/gpt-5.6-terra` | 2.10s | 5.19s | 6.71s | 9.29s | 27.15s |
| `xiaomi/mimo-v2.5-pro` | 2.19s | 4.89s | 6.41s | 11.76s | 55.00s |
| `google/gemma-4-31b-it` | 2.45s | 7.79s | 11.72s | 24.30s | 164.44s |
| `qwen/qwen3-235b-a22b-2507` | 2.51s | 5.52s | 7.43s | 15.66s | 18.61s |
| `mistralai/mistral-large-2512` | 3.28s | 6.54s | 7.58s | 11.79s | 35.55s |
| `openai/gpt-5.6-luna` | 3.46s | 8.81s | 11.35s | 20.00s | 83.66s |
| `z-ai/glm-5.2` | 3.55s | 15.39s | 24.22s | 50.27s | 202.19s |
| `claude-opus-5` | 3.67s | 63.46s | 64.55s | 182.66s | 244.12s |
| `openai/gpt-5.6-sol` | 3.72s | 9.39s | 11.86s | 20.99s | 76.93s |
| `x-ai/grok-4.3` | 3.73s | 8.76s | 10.34s | 12.67s | 20.15s |
| `claude-sonnet-4-6` | 4.11s | 63.07s | 83.49s | 170.46s | 351.49s |
| `xiaomi/mimo-v2.5` | 4.16s | 12.88s | 21.43s | 62.00s | 201.00s |
| `meta/muse-spark-1.1` | 4.31s | 10.89s | 13.59s | 17.76s | 20.43s |
| `google/gemini-3.6-flash` | 4.51s | 8.72s | 10.24s | 14.59s | 19.88s |
| `claude-opus-4-7` | 4.60s | 64.27s | 66.02s | 183.69s | 245.11s |
| `google/gemini-3.7-flash` | 4.65s | 7.79s | 9.87s | 13.77s | 20.04s |
| `openai/gpt-5.5` | 5.36s | 16.34s | 23.68s | 45.89s | 61.80s |
| `google/gemini-3.5-flash` | 5.59s | 9.27s | 11.20s | 16.40s | 20.77s |
| `deepseek/deepseek-v4-flash` | 6.27s | 32.38s | 40.26s | 88.86s | 224.85s |
| `openai/gpt-oss-120b` | 6.54s | 30.43s | 44.55s | 78.52s | 1677.95s |
| `claude-fable-5` | 6.61s | 64.22s | 68.22s | 183.43s | 244.88s |
| `openai/o3` | 6.66s | 17.18s | 20.44s | 29.16s | 176.74s |
| `openai/o4-mini` | 7.34s | 20.82s | 25.55s | 54.13s | 73.95s |
| `qwen/qwen3.6-flash` | 7.55s | 17.78s | 19.48s | 24.46s | 35.86s |
| `claude-opus-4-8` | 7.76s | 63.15s | 68.92s | 104.92s | 245.48s |
| `google/gemini-3.1-pro-preview` | 8.35s | 16.39s | 20.73s | 28.85s | 32.09s |
| `claude-sonnet-5` | 8.48s | 62.69s | 66.02s | 146.69s | 248.07s |
| `openai/gpt-oss-20b` | 9.18s | 38.09s | 51.77s | 72.93s | 571.38s |
| `qwen/qwen3.7-flash` | 10.14s | 24.59s | 27.58s | 31.94s | 38.97s |
| `meituan/longcat-2.0` | 10.53s | 69.38s | 77.08s | 88.11s | 122.09s |
| `inclusionai/ring-2.6-1t` | 10.75s | 32.71s | 35.76s | 41.10s | 137.68s |
| `meta/muse-glimmer-30b` | 10.79s | 53.29s | 66.87s | 133.67s | 359.53s |
| `x-ai/grok-4.5` | 10.86s | 42.26s | 52.43s | 83.77s | 120.19s |
| `qwen/qwen3.8-2.4t-a95b` | 11.93s | 45.48s | 67.44s | 192.61s | 344.43s |
| `deepseek/deepseek-v4-flash-0731` | 13.20s | 44.63s | 61.85s | 130.27s | 326.89s |
| `x-ai/grok-4.6` | 13.41s | 48.42s | 60.93s | 83.83s | 153.73s |
| `moonshotai/kimi-k3` | 13.78s | 55.72s | 96.34s | 160.79s | 298.31s |
| `stepfun/step-3.7-flash` | 13.87s | 33.94s | 35.00s | 36.93s | 53.50s |
| `google/gemini-2.5-pro` | 14.22s | 27.92s | 34.09s | 130.41s | 154.37s |
| `qwen/qwen3-14b` | 14.39s | 42.03s | 59.03s | 163.41s | 246.83s |
| `deepseek/deepseek-v4-pro` | 16.17s | 56.57s | 68.15s | 95.60s | 165.32s |
| `thinkingmachines/inkling-small` | 18.51s | 28.02s | 30.60s | 36.06s | 82.20s |
| `tencent/hy3` | 22.27s | 55.36s | 59.98s | 88.55s | 126.83s |
| `nvidia/nemotron-3-super-120b-a12b` | 22.33s | 182.06s | 248.36s | 416.82s | 1201.50s |
| `qwen/qwen3.7-max` | 23.41s | 54.27s | 62.65s | 75.47s | 76.89s |
| `qwen/qwen3.7-plus` | 23.68s | 52.61s | 62.94s | 71.06s | 72.60s |
| `claude-haiku-4-5-20251001` | 24.23s | 105.59s | 135.48s | 248.50s | 680.52s |
| `deepseek/deepseek-v4-pro-0813` | 26.59s | 66.57s | 74.56s | 106.45s | 174.71s |
| `deepseek/deepseek-r1-distill-llama-70b` | 27.21s | 68.34s | 97.56s | 138.12s | 158.13s |
| `qwen/qwen3.5-plus-02-15` | 29.18s | 46.60s | 52.05s | 60.74s | 94.18s |
| `deepseek/deepseek-r1-0528` | 29.36s | 172.78s | 208.22s | 296.84s | 415.53s |
| `qwen/qwen3.8-max` | 29.66s | 87.68s | 90.22s | 93.28s | 97.65s |
| `deepseek/deepseek-r1` | 35.66s | 170.50s | 195.41s | 276.94s | 428.59s |
| `qwen/qwen3.6-plus` | 36.91s | 70.52s | 70.77s | 71.10s | 71.67s |
| `qwen/qwen3.5-27b` | 37.62s | 106.00s | 126.80s | 163.08s | 989.60s |
| `bytedance-seed/seed-2-1-turbo` | 38.23s | 65.91s | 70.24s | 85.71s | 276.85s |
| `qwen/qwen3-8b` | 39.80s | 133.65s | 170.51s | 252.88s | 1187.74s |
| `moonshotai/kimi-k2.6` | 55.69s | 126.30s | 164.28s | 249.82s | 485.45s |
| `thinkingmachines/inkling` | 65.63s | 226.27s | 324.79s | 505.49s | 1063.70s |
| `stealth/ox-alpha` | 66.56s | 156.74s | 190.15s | 279.75s | 447.25s |
| `qwen/qwen3.8-27b` | 70.26s | 292.36s | 337.40s | 459.64s | 774.26s |

## Output token efficiency

How many output tokens the model spent per detected ad. Lower is more concise (the model finds an ad and returns the JSON). Higher means the model is producing a lot of text the parser will discard, which costs you whether or not the answer is right.

| Model | Total output tokens | Ads detected | Tokens / ad | Cost / TP |
|---|---:|---:|---:|---:|
| `claude-opus-4-7` | 29,485 | 615 | 48 | $0.0242 |
| `claude-sonnet-5` | 36,216 | 612 | 59 | $0.0100 |
| `meta-llama/llama-3.3-70b-instruct` | 28,052 | 470 | 60 | $0.0009 |
| `google/gemini-3.1-flash-lite` | 50,395 | 842 | 60 | $0.0013 |
| `claude-opus-4-8` | 36,858 | 605 | 61 | $0.0243 |
| `claude-haiku-4-5-20251001` | 45,648 | 716 | 64 | $0.0047 |
| `claude-fable-5` | 39,151 | 566 | 69 | $0.0472 |
| `mistralai/codestral-2508` | 57,458 | 820 | 70 | $0.0021 |
| `google/gemini-3.5-flash-lite` | 47,096 | 665 | 71 | $0.0017 |
| `claude-opus-5` | 38,844 | 547 | 71 | $0.0243 |
| `mistralai/mistral-medium-3.1` | 51,122 | 705 | 73 | $0.0024 |
| `openai/gpt-3.5-turbo` | 56,074 | 767 | 73 | $0.0054 |
| `deepseek/deepseek-v3.2` | 31,971 | 432 | 74 | $0.0020 |
| `google/gemma-4-26b-a4b-it` | 37,850 | 495 | 76 | $0.0005 |
| `cohere/command-r-plus-08-2024` | 15,352 | 196 | 78 | $0.0313 |
| `mistralai/mistral-medium-3-5` | 65,551 | 833 | 79 | $0.0083 |
| `meta-llama/llama-4-maverick` | 38,620 | 476 | 81 | $0.0014 |
| `google/gemini-2.5-flash` | 80,965 | 975 | 83 | $0.0017 |
| `claude-sonnet-4-6` | 43,175 | 516 | 84 | $0.0147 |
| `cohere/command-a` | 60,841 | 655 | 93 | $0.0204 |
| `openai/gpt-5.4-mini` | 56,545 | 599 | 94 | $0.0042 |
| `google/gemma-4-31b-it` | 54,323 | 569 | 95 | $0.0006 |
| `openai/gpt-5.4` | 45,697 | 470 | 97 | $0.0135 |
| `mistralai/mistral-large-2512` | 126,901 | 1280 | 99 | $0.0027 |
| `qwen/qwen3-235b-a22b-2507` | 65,771 | 633 | 104 | $0.0007 |
| `google/gemini-2.5-flash-lite` | 102,580 | 987 | 104 | $0.0005 |
| `meta-llama/llama-3.1-8b-instruct` | 166,649 | 1525 | 109 | $0.0009 |
| `meta-llama/llama-4-scout` | 70,155 | 479 | 146 | $0.0010 |
| `mistralai/mistral-small-2603` | 3,207 | 20 | 160 | $0.0175 |
| `xiaomi/mimo-v2.5-pro` | 67,712 | 274 | 247 | $0.0044 |
| `minimax/minimax-m3` | 120,297 | 471 | 255 | $0.0023 |
| `openai/gpt-5.6-terra` | 111,389 | 365 | 305 | $0.0114 |
| `openai/gpt-5.6-sol` | 153,677 | 412 | 373 | $0.0113 |
| `xiaomi/mimo-v2.5` | 223,313 | 587 | 380 | $0.0011 |
| `microsoft/phi-4` | 113,671 | 298 | 381 | $0.0010 |
| `nvidia/nemotron-3-ultra-550b-a55b` | 112,208 | 229 | 490 | $0.0077 |
| `x-ai/grok-4.3` | 490,642 | 684 | 717 | $0.0072 |
| `openai/gpt-5.6-luna` | 283,948 | 390 | 728 | $0.0013 |
| `z-ai/glm-5.2` | 413,174 | 529 | 781 | $0.0066 |
| `nvidia/nemotron-3.5-lightning` | 693,400 | 835 | 830 | $0.0017 |
| `openai/gpt-oss-120b` | 698,041 | 701 | 996 | $0.0003 |
| `openai/gpt-5.5` | 396,159 | 363 | 1091 | $0.0361 |
| `google/gemini-3.7-flash` | 573,916 | 412 | 1393 | $0.0030 |
| `qwen/qwen3.7-plus` | 1,407,483 | 863 | 1631 | $0.0038 |
| `qwen/qwen3.7-max` | 1,282,965 | 762 | 1684 | $0.0134 |
| `google/gemini-3.6-flash` | 788,423 | 415 | 1900 | $0.0065 |
| `x-ai/grok-4.5` | 938,537 | 451 | 2081 | $0.0149 |
| `deepseek/deepseek-r1` | 1,397,958 | 657 | 2128 | $0.0084 |
| `google/gemini-3.5-flash` | 953,694 | 439 | 2172 | $0.0152 |
| `deepseek/deepseek-r1-0528` | 1,420,349 | 650 | 2185 | $0.0070 |
| `moonshotai/kimi-k3` | 874,937 | 386 | 2267 | $0.0312 |
| `deepseek/deepseek-r1-distill-llama-70b` | 888,615 | 385 | 2308 | $0.0069 |
| `openai/gpt-oss-20b` | 1,149,006 | 490 | 2345 | $0.0004 |
| `google/gemini-3.1-pro-preview` | 1,101,910 | 463 | 2380 | $0.0227 |
| `deepseek/deepseek-v4-flash` | 879,110 | 362 | 2428 | $0.0004 |
| `qwen/qwen3-14b` | 792,165 | 318 | 2491 | $0.0020 |
| `x-ai/grok-4.6` | 1,230,932 | 469 | 2625 | $0.0166 |
| `google/gemini-2.5-pro` | 1,371,169 | 512 | 2678 | $0.0199 |
| `meta/muse-glimmer-30b` | 1,068,404 | 397 | 2691 | $0.0042 |
| `stealth/ox-alpha` | 1,241,235 | 363 | 3419 | $0.0000 |
| `meituan/longcat-2.0` | 906,744 | 265 | 3422 | $0.0055 |
| `qwen/qwen3.6-flash` | 1,468,826 | 420 | 3497 | $0.0027 |
| `qwen/qwen3.5-plus-02-15` | 2,497,953 | 700 | 3569 | $0.0048 |
| `qwen/qwen3.7-flash` | 1,477,726 | 388 | 3809 | $0.0004 |
| `deepseek/deepseek-v4-pro` | 1,099,395 | 288 | 3817 | $0.0037 |
| `qwen/qwen3.6-plus` | 2,010,766 | 464 | 4334 | $0.0071 |
| `meta/muse-spark-1.1` | 1,075,701 | 236 | 4558 | $0.0155 |
| `nvidia/nemotron-3-super-120b-a12b` | 1,508,154 | 290 | 5201 | $0.0016 |
| `openai/o3` | 741,947 | 140 | 5300 | $0.0338 |
| `deepseek/deepseek-v4-flash-0731` | 1,339,909 | 218 | 6146 | $0.0011 |
| `inclusionai/ring-2.6-1t` | 1,286,827 | 169 | 7614 | $0.0037 |
| `qwen/qwen3.5-27b` | 2,155,828 | 273 | 7897 | $0.0062 |
| `thinkingmachines/inkling-small` | 2,312,665 | 260 | 8895 | $0.0119 |
| `qwen/qwen3.8-27b` | 1,563,261 | 172 | 9089 | $0.0126 |
| `qwen/qwen3.8-2.4t-a95b` | 1,669,886 | 165 | 10121 | $0.0466 |
| `deepseek/deepseek-v4-pro-0813` | 1,776,997 | 164 | 10835 | $0.0248 |
| `moonshotai/kimi-k2.6` | 2,581,920 | 202 | 12782 | $0.0191 |
| `tencent/hy3` | 2,286,538 | 171 | 13372 | $0.0037 |
| `qwen/qwen3.8-max` | 1,655,455 | 114 | 14522 | $0.0949 |
| `stepfun/step-3.7-flash` | 1,508,225 | 92 | 16394 | $0.0072 |
| `openai/o4-mini` | 1,079,864 | 52 | 20767 | $0.1009 |
| `thinkingmachines/inkling` | 2,652,449 | 79 | 33575 | $0.0770 |
| `bytedance-seed/seed-2-1-turbo` | 1,885,867 | 10 | 188587 | $0.3617 |
| `qwen/qwen3-8b` | 2,139,222 | 1 | 2139222 | n/a |

## Cost breakdown (input vs output)

Where each model's per-episode dollars go, at the same pricing snapshot as every other table. Every model reads the same transcripts, so the input side varies only with the provider's input price. The output side varies with how much the model writes: a high output share on a modest total usually means reasoning tokens, and a model with a low per-token price can still land mid-table by writing thousands of them. Failed calls are excluded, same as the cost column everywhere else.

| Model | Cost / episode | Input | Output | Output share |
|---|---:|---:|---:|---:|
| `claude-fable-5` | $10.7552 | $10.3637 | $0.3915 | 4% |
| `openai/gpt-5.5` | $7.6842 | $5.3073 | $2.3770 | 31% |
| `moonshotai/kimi-k3` | $5.9242 | $3.2994 | $2.6248 | 44% |
| `claude-opus-5` | $5.3761 | $5.1818 | $0.1942 | 4% |
| `claude-opus-4-8` | $5.3661 | $5.1818 | $0.1843 | 3% |
| `claude-opus-4-7` | $5.3293 | $5.1818 | $0.1474 | 3% |
| `google/gemini-3.1-pro-preview` | $4.9160 | $2.2714 | $2.6446 | 54% |
| `qwen/qwen3.8-2.4t-a95b` | $4.2877 | $2.2838 | $2.0039 | 47% |
| `qwen/qwen3.8-max` | $4.2714 | $2.2849 | $1.9865 | 47% |
| `google/gemini-2.5-pro` | $4.1601 | $1.4178 | $2.7423 | 66% |
| `x-ai/grok-4.6` | $3.7063 | $2.2292 | $1.4771 | 40% |
| `google/gemini-3.5-flash` | $3.4202 | $1.7036 | $1.7166 | 50% |
| `x-ai/grok-4.5` | $3.3554 | $2.2292 | $1.1262 | 34% |
| `openai/o3` | $3.3100 | $2.1229 | $1.1871 | 36% |
| `claude-sonnet-4-6` | $3.2386 | $3.1091 | $0.1295 | 4% |
| `thinkingmachines/inkling` | $3.1580 | $1.0095 | $2.1485 | 68% |
| `cohere/command-a` | $2.9415 | $2.8198 | $0.1217 | 4% |
| `cohere/command-r-plus-08-2024` | $2.8505 | $2.8198 | $0.0307 | 1% |
| `qwen/qwen3.7-max` | $2.8109 | $1.6755 | $1.1354 | 40% |
| `openai/gpt-5.4` | $2.7907 | $2.6536 | $0.1371 | 5% |
| `deepseek/deepseek-v4-pro-0813` | $2.4576 | $1.2613 | $1.1963 | 49% |
| `openai/gpt-5.6-sol` | $2.4303 | $2.1229 | $0.3074 | 13% |
| `openai/gpt-5.6-terra` | $2.3902 | $2.1229 | $0.2673 | 11% |
| `meta/muse-spark-1.1` | $2.2536 | $1.3393 | $0.9143 | 41% |
| `claude-sonnet-5` | $2.1452 | $2.0727 | $0.0724 | 3% |
| `openai/o4-mini` | $2.1179 | $1.1676 | $0.9503 | 45% |
| `mistralai/mistral-medium-3-5` | $1.8393 | $1.7410 | $0.0983 | 5% |
| `moonshotai/kimi-k2.6` | $1.7617 | $0.5844 | $1.1774 | 67% |
| `x-ai/grok-4.3` | $1.6048 | $1.3595 | $0.2453 | 15% |
| `deepseek/deepseek-r1` | $1.4743 | $0.7753 | $0.6990 | 47% |
| `bytedance-seed/seed-2-1-turbo` | $1.4469 | $0.5039 | $0.9429 | 65% |
| `google/gemini-3.6-flash` | $1.4431 | $0.8518 | $0.5913 | 41% |
| `qwen/qwen3.8-27b` | $1.3947 | $0.4568 | $0.9380 | 67% |
| `z-ai/glm-5.2` | $1.3083 | $1.0574 | $0.2509 | 19% |
| `deepseek/deepseek-r1-0528` | $1.1645 | $0.5538 | $0.6108 | 52% |
| `qwen/qwen3.6-plus` | $1.1534 | $0.3692 | $0.7842 | 68% |
| `claude-haiku-4-5-20251001` | $1.0820 | $1.0364 | $0.0456 | 4% |
| `qwen/qwen3.5-plus-02-15` | $1.0746 | $0.2952 | $0.7794 | 73% |
| `thinkingmachines/inkling-small` | $1.0333 | $0.4782 | $0.5550 | 54% |
| `qwen/qwen3.5-27b` | $0.8941 | $0.2215 | $0.6726 | 75% |
| `openai/gpt-5.4-mini` | $0.8470 | $0.7961 | $0.0509 | 6% |
| `nvidia/nemotron-3-ultra-550b-a55b` | $0.7782 | $0.6974 | $0.0808 | 10% |
| `qwen/qwen3.7-plus` | $0.7238 | $0.3635 | $0.3603 | 50% |
| `meta/muse-glimmer-30b` | $0.6965 | $0.3760 | $0.3205 | 46% |
| `deepseek/deepseek-r1-distill-llama-70b` | $0.6913 | $0.5492 | $0.1422 | 21% |
| `google/gemini-3.7-flash` | $0.6411 | $0.4259 | $0.2152 | 34% |
| `mistralai/mistral-large-2512` | $0.6184 | $0.5803 | $0.0381 | 6% |
| `deepseek/deepseek-v4-pro` | $0.6106 | $0.4360 | $0.1745 | 29% |
| `meituan/longcat-2.0` | $0.5573 | $0.3396 | $0.2176 | 39% |
| `openai/gpt-3.5-turbo` | $0.5564 | $0.5395 | $0.0168 | 3% |
| `qwen/qwen3.6-flash` | $0.5435 | $0.2130 | $0.3305 | 61% |
| `xiaomi/mimo-v2.5-pro` | $0.4954 | $0.4836 | $0.0118 | 2% |
| `mistralai/mistral-medium-3.1` | $0.4847 | $0.4643 | $0.0204 | 4% |
| `stepfun/step-3.7-flash` | $0.4302 | $0.1457 | $0.2846 | 66% |
| `tencent/hy3` | $0.3876 | $0.1461 | $0.2415 | 62% |
| `google/gemini-2.5-flash` | $0.3796 | $0.3391 | $0.0405 | 11% |
| `google/gemini-3.5-flash-lite` | $0.3643 | $0.3407 | $0.0235 | 6% |
| `mistralai/codestral-2508` | $0.3585 | $0.3482 | $0.0103 | 3% |
| `minimax/minimax-m3` | $0.3575 | $0.3287 | $0.0289 | 8% |
| `qwen/qwen3-8b` | $0.3256 | $0.1310 | $0.1947 | 60% |
| `google/gemini-3.1-flash-lite` | $0.2988 | $0.2837 | $0.0151 | 5% |
| `deepseek/deepseek-v3.2` | $0.2914 | $0.2889 | $0.0024 | 1% |
| `openai/gpt-5.6-luna` | $0.2804 | $0.2123 | $0.0681 | 24% |
| `inclusionai/ring-2.6-1t` | $0.2468 | $0.0860 | $0.1609 | 65% |
| `meta-llama/llama-4-maverick` | $0.2206 | $0.2144 | $0.0062 | 3% |
| `nvidia/nemotron-3-super-120b-a12b` | $0.2194 | $0.0988 | $0.1207 | 55% |
| `mistralai/mistral-small-2603` | $0.1745 | $0.1741 | $0.0004 | 0% |
| `qwen/qwen3-14b` | $0.1724 | $0.1344 | $0.0380 | 22% |
| `xiaomi/mimo-v2.5` | $0.1692 | $0.1567 | $0.0125 | 7% |
| `deepseek/deepseek-v4-flash-0731` | $0.1371 | $0.0888 | $0.0482 | 35% |
| `nvidia/nemotron-3.5-lightning` | $0.1207 | $0.0930 | $0.0277 | 23% |
| `google/gemma-4-31b-it` | $0.1181 | $0.1144 | $0.0037 | 3% |
| `google/gemini-2.5-flash-lite` | $0.1132 | $0.1050 | $0.0082 | 7% |
| `meta-llama/llama-4-scout` | $0.1111 | $0.1069 | $0.0042 | 4% |
| `meta-llama/llama-3.3-70b-instruct` | $0.1107 | $0.1089 | $0.0018 | 2% |
| `qwen/qwen3-235b-a22b-2507` | $0.1078 | $0.1005 | $0.0072 | 7% |
| `google/gemma-4-26b-a4b-it` | $0.0825 | $0.0799 | $0.0026 | 3% |
| `microsoft/phi-4` | $0.0792 | $0.0760 | $0.0032 | 4% |
| `qwen/qwen3.7-flash` | $0.0725 | $0.0341 | $0.0384 | 53% |
| `deepseek/deepseek-v4-flash` | $0.0715 | $0.0543 | $0.0172 | 24% |
| `openai/gpt-oss-120b` | $0.0634 | $0.0397 | $0.0237 | 37% |
| `openai/gpt-oss-20b` | $0.0620 | $0.0322 | $0.0299 | 48% |
| `meta-llama/llama-3.1-8b-instruct` | $0.0572 | $0.0545 | $0.0027 | 5% |

## Trial variance (determinism check)

All trials run at temperature 0.0. If a model produces stable output you'd expect the F1 stdev across trials to be near zero. Higher numbers mean the model is non-deterministic even at temp=0. That's fine to know, but means you cannot trust a single trial's number for that model.

| Model | Mean F1 stdev across episodes | Highest single-episode stdev |
|---|---:|---:|
| `claude-haiku-4-5-20251001` | 0.0375 | 0.1845 |
| `qwen/qwen3.5-plus-02-15` | 0.0264 | 0.1118 |
| `google/gemini-3.5-flash` | 0.0096 | 0.0446 |
| `claude-sonnet-4-6` | 0.0312 | 0.2236 |
| `x-ai/grok-4.3` | 0.0559 | 0.1565 |
| `x-ai/grok-4.5` | 0.0175 | 0.0894 |
| `google/gemini-3.6-flash` | 0.0094 | 0.0596 |
| `google/gemini-3.5-flash-lite` | 0.0720 | 0.2226 |
| `google/gemini-3.7-flash` | 0.0090 | 0.1084 |
| `x-ai/grok-4.6` | 0.0483 | 0.1459 |
| `claude-fable-5` | 0.0476 | 0.2449 |
| `openai/gpt-5.5` | 0.0262 | 0.0994 |
| `mistralai/mistral-medium-3-5` | 0.0295 | 0.1082 |
| `openai/gpt-5.6-terra` | 0.0555 | 0.1482 |
| `google/gemini-3.1-flash-lite` | 0.0205 | 0.1118 |
| `claude-opus-4-8` | 0.0380 | 0.1565 |
| `qwen/qwen3.7-max` | 0.0495 | 0.1848 |
| `claude-opus-4-7` | 0.0560 | 0.1491 |
| `openai/gpt-5.6-luna` | 0.0453 | 0.1571 |
| `qwen/qwen3.6-flash` | 0.0845 | 0.2300 |
| `google/gemini-3.1-pro-preview` | 0.0199 | 0.1118 |
| `stealth/ox-alpha` | 0.0325 | 0.1465 |
| `claude-opus-5` | 0.0121 | 0.1190 |
| `claude-sonnet-5` | 0.0867 | 0.2191 |
| `openai/gpt-5.6-sol` | 0.0735 | 0.2449 |
| `mistralai/mistral-medium-3.1` | 0.0392 | 0.0994 |
| `qwen/qwen3.7-flash` | 0.1159 | 0.3742 |
| `moonshotai/kimi-k3` | 0.0743 | 0.2478 |
| `google/gemini-2.5-flash` | 0.0075 | 0.0447 |
| `google/gemini-2.5-pro` | 0.0623 | 0.2528 |
| `deepseek/deepseek-v4-flash` | 0.1257 | 0.4150 |
| `openai/gpt-oss-120b` | 0.1207 | 0.2063 |
| `google/gemma-4-31b-it` | 0.1381 | 0.4472 |
| `deepseek/deepseek-v4-pro` | 0.1604 | 0.2733 |
| `meta/muse-glimmer-30b` | 0.1397 | 0.4472 |
| `google/gemini-2.5-flash-lite` | 0.0740 | 0.1789 |
| `openai/gpt-5.4` | 0.0774 | 0.1517 |
| `deepseek/deepseek-v3.2` | 0.0775 | 0.3492 |
| `meta/muse-spark-1.1` | 0.2203 | 0.4346 |
| `qwen/qwen3.6-plus` | 0.1726 | 0.3753 |
| `z-ai/glm-5.2` | 0.1280 | 0.1833 |
| `openai/gpt-5.4-mini` | 0.0694 | 0.1886 |
| `deepseek/deepseek-r1` | 0.1403 | 0.4150 |
| `nvidia/nemotron-3-super-120b-a12b` | 0.1549 | 0.3651 |
| `minimax/minimax-m3` | 0.2052 | 0.4580 |
| `qwen/qwen3.5-27b` | 0.2183 | 0.4346 |
| `google/gemma-4-26b-a4b-it` | 0.1180 | 0.2236 |
| `mistralai/mistral-large-2512` | 0.0464 | 0.1491 |
| `deepseek/deepseek-v4-flash-0731` | 0.1758 | 0.4082 |
| `deepseek/deepseek-r1-0528` | 0.1605 | 0.3578 |
| `openai/gpt-oss-20b` | 0.1704 | 0.4044 |
| `meta-llama/llama-4-maverick` | 0.0354 | 0.1789 |
| `qwen/qwen3.7-plus` | 0.0674 | 0.2431 |
| `qwen/qwen3.8-27b` | 0.1452 | 0.4714 |
| `mistralai/codestral-2508` | 0.0747 | 0.2236 |
| `qwen/qwen3-235b-a22b-2507` | 0.1636 | 0.3507 |
| `meta-llama/llama-3.3-70b-instruct` | 0.1257 | 0.3536 |
| `deepseek/deepseek-v4-pro-0813` | 0.1427 | 0.3651 |
| `xiaomi/mimo-v2.5-pro` | 0.2461 | 0.3651 |
| `tencent/hy3` | 0.1329 | 0.2460 |
| `openai/o3` | 0.1914 | 0.3651 |
| `xiaomi/mimo-v2.5` | 0.1554 | 0.3715 |
| `nvidia/nemotron-3-ultra-550b-a55b` | 0.2302 | 0.3664 |
| `meta-llama/llama-4-scout` | 0.2074 | 0.4714 |
| `cohere/command-a` | 0.0500 | 0.2140 |
| `meituan/longcat-2.0` | 0.1604 | 0.3651 |
| `qwen/qwen3.8-2.4t-a95b` | 0.1085 | 0.2981 |
| `deepseek/deepseek-r1-distill-llama-70b` | 0.1566 | 0.3789 |
| `moonshotai/kimi-k2.6` | 0.1782 | 0.2888 |
| `stepfun/step-3.7-flash` | 0.1632 | 0.4346 |
| `microsoft/phi-4` | 0.0918 | 0.2380 |
| `thinkingmachines/inkling-small` | 0.2001 | 0.3651 |
| `cohere/command-r-plus-08-2024` | 0.1463 | 0.3651 |
| `qwen/qwen3-14b` | 0.0983 | 0.1966 |
| `inclusionai/ring-2.6-1t` | 0.1483 | 0.2828 |
| `openai/gpt-3.5-turbo` | 0.0123 | 0.0843 |
| `qwen/qwen3.8-max` | 0.1219 | 0.2844 |
| `nvidia/nemotron-3.5-lightning` | 0.0884 | 0.2981 |
| `meta-llama/llama-3.1-8b-instruct` | 0.1640 | 0.3789 |
| `thinkingmachines/inkling` | 0.1644 | 0.4561 |
| `openai/o4-mini` | 0.1843 | 0.3651 |
| `mistralai/mistral-small-2603` | 0.0000 | 0.0000 |
| `bytedance-seed/seed-2-1-turbo` | 0.0533 | 0.3651 |
| `qwen/qwen3-8b` | 0.0000 | 0.0000 |

## Cross-model agreement

For each of the 171 (episode, window, trial-equivalent) entries, how many of the 84 active models predicted at least one ad? High-agreement windows are unambiguous ads (or unambiguously not ads). Low-agreement windows are where individual models disagree, and are candidates for ensemble voting if you want a cheap accuracy boost.

| Models predicting an ad | Window count | Share |
|---:|---:|---:|
| 5 of 84 | 1 | 0.6% |
| 7 of 84 | 6 | 3.5% |
| 8 of 84 | 4 | 2.3% |
| 9 of 84 | 5 | 2.9% |
| 10 of 84 | 8 | 4.7% |
| 11 of 84 | 9 | 5.3% |
| 12 of 84 | 4 | 2.3% |
| 13 of 84 | 6 | 3.5% |
| 14 of 84 | 9 | 5.3% |
| 15 of 84 | 4 | 2.3% |
| 16 of 84 | 9 | 5.3% |
| 17 of 84 | 6 | 3.5% |
| 18 of 84 | 5 | 2.9% |
| 19 of 84 | 4 | 2.3% |
| 20 of 84 | 1 | 0.6% |
| 21 of 84 | 2 | 1.2% |
| 23 of 84 | 2 | 1.2% |
| 24 of 84 | 3 | 1.8% |
| 26 of 84 | 1 | 0.6% |
| 27 of 84 | 1 | 0.6% |
| 28 of 84 | 1 | 0.6% |
| 29 of 84 | 2 | 1.2% |
| 34 of 84 | 1 | 0.6% |
| 37 of 84 | 1 | 0.6% |
| 44 of 84 | 1 | 0.6% |
| 45 of 84 | 1 | 0.6% |
| 46 of 84 | 1 | 0.6% |
| 49 of 84 | 1 | 0.6% |
| 53 of 84 | 2 | 1.2% |
| 60 of 84 | 1 | 0.6% |
| 61 of 84 | 1 | 0.6% |
| 65 of 84 | 2 | 1.2% |
| 66 of 84 | 1 | 0.6% |
| 67 of 84 | 1 | 0.6% |
| 68 of 84 | 8 | 4.7% |
| 70 of 84 | 3 | 1.8% |
| 71 of 84 | 3 | 1.8% |
| 72 of 84 | 3 | 1.8% |
| 73 of 84 | 13 | 7.6% |
| 74 of 84 | 6 | 3.5% |
| 75 of 84 | 8 | 4.7% |
| 76 of 84 | 3 | 1.8% |
| 77 of 84 | 7 | 4.1% |
| 78 of 84 | 6 | 3.5% |
| 79 of 84 | 3 | 1.8% |
| 80 of 84 | 1 | 0.6% |

Read this as: rows near the top are windows where the field disagrees (most models said no, a few said yes, usually false positives); rows near the bottom are windows where the field broadly agrees (typical of clear sponsor reads).

### Per-model alignment with consensus

Same data, viewed per model. For each window, the **majority** is whether more than half of the 84 active models flagged an ad. Then for each model: did it vote with the majority or against it? Four buckets:

- **with-yes**: this model voted yes, majority also voted yes (likely true positive)
- **with-no**: this model voted no, majority also voted no (likely true negative)
- **broke-yes**: this model voted yes, majority voted no (likely false positive / hallucination)
- **broke-no**: this model voted no, majority voted yes (likely missed real ad)

Alignment rate is `(with-yes + with-no) / total`. High alignment means the model tracks the consensus; low alignment means it disagrees often, which could be brilliance or noise depending on whether its disagreements are also where its F1 wins or loses.

| Model | with-yes | with-no | broke-yes | broke-no | Alignment |
|---|---:|---:|---:|---:|---:|
| `meta/muse-glimmer-30b` | 74 | 95 | 0 | 2 | 98.8% |
| `claude-opus-5` | 75 | 93 | 2 | 1 | 98.2% |
| `claude-opus-4-8` | 74 | 93 | 2 | 2 | 97.7% |
| `claude-fable-5` | 74 | 92 | 3 | 2 | 97.1% |
| `claude-sonnet-5` | 72 | 94 | 1 | 4 | 97.1% |
| `google/gemini-2.5-flash` | 75 | 91 | 4 | 1 | 97.1% |
| `google/gemini-3.1-pro-preview` | 72 | 94 | 1 | 4 | 97.1% |
| `openai/gpt-5.5` | 75 | 91 | 4 | 1 | 97.1% |
| `qwen/qwen3.5-plus-02-15` | 71 | 95 | 0 | 5 | 97.1% |
| `qwen/qwen3.6-flash` | 73 | 93 | 2 | 3 | 97.1% |
| `qwen/qwen3.7-max` | 72 | 94 | 1 | 4 | 97.1% |
| `x-ai/grok-4.3` | 72 | 94 | 1 | 4 | 97.1% |
| `claude-opus-4-7` | 74 | 91 | 4 | 2 | 96.5% |
| `google/gemini-3.5-flash` | 71 | 94 | 1 | 5 | 96.5% |
| `google/gemini-3.6-flash` | 71 | 94 | 1 | 5 | 96.5% |
| `google/gemini-3.7-flash` | 71 | 94 | 1 | 5 | 96.5% |
| `openai/gpt-oss-120b` | 76 | 89 | 6 | 0 | 96.5% |
| `qwen/qwen3.5-27b` | 72 | 93 | 2 | 4 | 96.5% |
| `qwen/qwen3.7-flash` | 72 | 93 | 2 | 4 | 96.5% |
| `stealth/ox-alpha` | 70 | 95 | 0 | 6 | 96.5% |
| `deepseek/deepseek-v4-flash` | 74 | 90 | 5 | 2 | 95.9% |
| `moonshotai/kimi-k3` | 73 | 91 | 4 | 3 | 95.9% |
| `x-ai/grok-4.5` | 70 | 94 | 1 | 6 | 95.9% |
| `x-ai/grok-4.6` | 71 | 93 | 2 | 5 | 95.9% |
| `claude-haiku-4-5-20251001` | 70 | 93 | 2 | 6 | 95.3% |
| `claude-sonnet-4-6` | 69 | 94 | 1 | 7 | 95.3% |
| `google/gemma-4-31b-it` | 75 | 88 | 7 | 1 | 95.3% |
| `meta/muse-spark-1.1` | 68 | 95 | 0 | 8 | 95.3% |
| `openai/gpt-5.6-luna` | 76 | 87 | 8 | 0 | 95.3% |
| `google/gemini-2.5-pro` | 76 | 86 | 9 | 0 | 94.7% |
| `deepseek/deepseek-v4-flash-0731` | 65 | 95 | 0 | 11 | 93.6% |
| `google/gemma-4-26b-a4b-it` | 75 | 84 | 11 | 1 | 93.0% |
| `qwen/qwen3.6-plus` | 70 | 89 | 6 | 6 | 93.0% |
| `nvidia/nemotron-3-super-120b-a12b` | 66 | 91 | 4 | 10 | 91.8% |
| `openai/gpt-5.6-terra` | 74 | 83 | 12 | 2 | 91.8% |
| `openai/gpt-oss-20b` | 73 | 84 | 11 | 3 | 91.8% |
| `openai/o3` | 62 | 95 | 0 | 14 | 91.8% |
| `google/gemini-3.5-flash-lite` | 62 | 94 | 1 | 14 | 91.2% |
| `meta-llama/llama-3.3-70b-instruct` | 70 | 86 | 9 | 6 | 91.2% |
| `mistralai/mistral-medium-3-5` | 67 | 89 | 6 | 9 | 91.2% |
| `google/gemini-3.1-flash-lite` | 76 | 79 | 16 | 0 | 90.6% |
| `mistralai/mistral-medium-3.1` | 61 | 93 | 2 | 15 | 90.1% |
| `qwen/qwen3-14b` | 72 | 82 | 13 | 4 | 90.1% |
| `deepseek/deepseek-v4-pro` | 71 | 81 | 14 | 5 | 88.9% |
| `inclusionai/ring-2.6-1t` | 61 | 91 | 4 | 15 | 88.9% |
| `minimax/minimax-m3` | 65 | 85 | 10 | 11 | 87.7% |
| `meta-llama/llama-4-maverick` | 76 | 72 | 23 | 0 | 86.5% |
| `tencent/hy3` | 55 | 93 | 2 | 21 | 86.5% |
| `qwen/qwen3.8-27b` | 51 | 95 | 0 | 25 | 85.4% |
| `deepseek/deepseek-v4-pro-0813` | 50 | 95 | 0 | 26 | 84.8% |
| `xiaomi/mimo-v2.5-pro` | 71 | 74 | 21 | 5 | 84.8% |
| `deepseek/deepseek-r1-distill-llama-70b` | 54 | 90 | 5 | 22 | 84.2% |
| `meta-llama/llama-4-scout` | 71 | 73 | 22 | 5 | 84.2% |
| `meituan/longcat-2.0` | 48 | 95 | 0 | 28 | 83.6% |
| `deepseek/deepseek-v3.2` | 44 | 95 | 0 | 32 | 81.3% |
| `google/gemini-2.5-flash-lite` | 75 | 59 | 36 | 1 | 78.4% |
| `deepseek/deepseek-r1` | 76 | 56 | 39 | 0 | 77.2% |
| `microsoft/phi-4` | 41 | 91 | 4 | 35 | 77.2% |
| `openai/o4-mini` | 38 | 94 | 1 | 38 | 77.2% |
| `openai/gpt-5.6-sol` | 75 | 55 | 40 | 1 | 76.0% |
| `thinkingmachines/inkling` | 41 | 88 | 7 | 35 | 75.4% |
| `qwen/qwen3.8-2.4t-a95b` | 44 | 84 | 11 | 32 | 74.9% |
| `meta-llama/llama-3.1-8b-instruct` | 70 | 57 | 38 | 6 | 74.3% |
| `mistralai/codestral-2508` | 67 | 60 | 35 | 9 | 74.3% |
| `stepfun/step-3.7-flash` | 32 | 95 | 0 | 44 | 74.3% |
| `cohere/command-r-plus-08-2024` | 41 | 84 | 11 | 35 | 73.1% |
| `openai/gpt-5.4` | 76 | 47 | 48 | 0 | 71.9% |
| `qwen/qwen3.8-max` | 25 | 95 | 0 | 51 | 70.2% |
| `nvidia/nemotron-3-ultra-550b-a55b` | 68 | 51 | 44 | 8 | 69.6% |
| `deepseek/deepseek-r1-0528` | 75 | 42 | 53 | 1 | 68.4% |
| `mistralai/mistral-large-2512` | 75 | 34 | 61 | 1 | 63.7% |
| `cohere/command-a` | 75 | 27 | 68 | 1 | 59.6% |
| `moonshotai/kimi-k2.6` | 47 | 52 | 43 | 29 | 57.9% |
| `thinkingmachines/inkling-small` | 60 | 39 | 56 | 16 | 57.9% |
| `openai/gpt-5.4-mini` | 76 | 22 | 73 | 0 | 57.3% |
| `z-ai/glm-5.2` | 75 | 23 | 72 | 1 | 57.3% |
| `mistralai/mistral-small-2603` | 1 | 95 | 0 | 75 | 56.1% |
| `qwen/qwen3-8b` | 1 | 95 | 0 | 75 | 56.1% |
| `bytedance-seed/seed-2-1-turbo` | 4 | 90 | 5 | 72 | 55.0% |
| `nvidia/nemotron-3.5-lightning` | 56 | 37 | 58 | 20 | 54.4% |
| `openai/gpt-3.5-turbo` | 75 | 13 | 82 | 1 | 51.5% |
| `qwen/qwen3-235b-a22b-2507` | 74 | 14 | 81 | 2 | 51.5% |
| `qwen/qwen3.7-plus` | 75 | 5 | 90 | 1 | 46.8% |
| `xiaomi/mimo-v2.5` | 76 | 3 | 92 | 0 | 46.2% |

### Windows flagged with no truth ad

The other side of the histogram: windows the ground truth marks ad-free, ranked by how many of the 84 models flagged them anyway (in at least one trial). A window near the top is either content that genuinely resembles an ad, which is what precision-focused validator rules should train against, or a spot the truth file missed. Either way these are the first windows worth a manual re-listen; on a corpus this size a single mislabeled window moves scores. No-ad control episodes are included and tagged.

| Episode | Window | Span | Models flagging |
|---|---:|---|---:|
| `ep-on-air-with-dan-and-alex2-574e4f303730` | 6 | 2520-3120s | 66 of 84 |
| `ep-on-air-with-dan-and-alex2-574e4f303730` | 5 | 2100-2700s | 61 of 84 |
| `ep-drink-champs-30c9a2d49f13` | 35 | 14700-15300s | 60 of 84 |
| `ep-drink-champs-30c9a2d49f13` | 12 | 5040-5640s | 53 of 84 |
| `ep-the-brilliant-idiots-0bb9bf634c8e` | 10 | 4200-4800s | 53 of 84 |
| `ep-the-brilliant-idiots-0bb9bf634c8e` | 9 | 3780-4380s | 49 of 84 |
| `ep-daily-gist-chicago-70a82fe93a5c` | 3 | 1260-1271s | 45 of 84 |
| `ep-daily-gist-chicago-70a82fe93a5c` | 2 | 840-1271s | 44 of 84 |
| `ep-the-brilliant-idiots-0bb9bf634c8e` | 14 | 5880-6480s | 34 of 84 |
| `ep-ai-cloud-essentials-e8dc897fbd6b` (no-ad control) | 0 | 0-600s | 29 of 84 |
| `ep-crime-junkie-8ce498f299d7` | 1 | 420-1020s | 29 of 84 |
| `ep-security-now-audio-2850b24903b2` | 11 | 4620-5220s | 28 of 84 |
| `ep-on-air-with-dan-and-alex2-574e4f303730` | 2 | 840-1440s | 27 of 84 |
| `ep-the-brilliant-idiots-0bb9bf634c8e` | 3 | 1260-1860s | 26 of 84 |
| `ep-glt1412515089-373d5ba5007b` | 22 | 9240-9840s | 24 of 84 |

... and 87 more with 2+ votes.

## Detection rate by ad characteristic

Aggregate detection rates often hide systematic blind spots. Below: for each model, what fraction of truth ads in each bucket were detected (matched at IoU >= 0.5).

### By ad length

Truth ads bucketed by duration: short (<30s), medium (30-90s), long (>=90s). Cell values are detection rate (fraction of truth ads in that bucket the model caught), with the sample size `n` so a misleading 1.00 on a 2-ad bucket doesn't get over-weighted. Models that systematically miss short ads usually fail on network-inserted brand-tagline spots; missing long ads is rarer and usually means the model gave up before processing the full window.

| Model | long (>=90s) | medium (30-90s) | short (<30s) |
|---|---:|---:|---:|
| `bytedance-seed/seed-2-1-turbo` | 0.00 (n=140) | 0.03 (n=75) | 0.07 (n=30) |
| `claude-fable-5` | 0.93 (n=140) | 1.00 (n=75) | 0.77 (n=30) |
| `claude-haiku-4-5-20251001` | 0.91 (n=140) | 0.96 (n=75) | 0.97 (n=30) |
| `claude-opus-4-7` | 0.97 (n=140) | 0.95 (n=75) | 0.43 (n=30) |
| `claude-opus-4-8` | 0.93 (n=140) | 0.97 (n=75) | 0.60 (n=30) |
| `claude-opus-5` | 0.97 (n=140) | 0.93 (n=75) | 0.50 (n=30) |
| `claude-sonnet-4-6` | 0.93 (n=140) | 0.93 (n=75) | 0.70 (n=30) |
| `claude-sonnet-5` | 0.93 (n=140) | 0.93 (n=75) | 0.47 (n=30) |
| `cohere/command-a` | 0.47 (n=140) | 0.77 (n=75) | 0.67 (n=30) |
| `cohere/command-r-plus-08-2024` | 0.51 (n=140) | 0.17 (n=75) | 0.23 (n=30) |
| `deepseek/deepseek-r1` | 0.67 (n=140) | 0.79 (n=75) | 0.77 (n=30) |
| `deepseek/deepseek-r1-0528` | 0.63 (n=140) | 0.75 (n=75) | 0.77 (n=30) |
| `deepseek/deepseek-r1-distill-llama-70b` | 0.34 (n=140) | 0.41 (n=75) | 0.73 (n=30) |
| `deepseek/deepseek-v3.2` | 0.72 (n=140) | 0.51 (n=75) | 0.20 (n=30) |
| `deepseek/deepseek-v4-flash` | 0.76 (n=140) | 0.73 (n=75) | 0.67 (n=30) |
| `deepseek/deepseek-v4-flash-0731` | 0.50 (n=140) | 0.55 (n=75) | 0.57 (n=30) |
| `deepseek/deepseek-v4-pro` | 0.71 (n=140) | 0.67 (n=75) | 0.53 (n=30) |
| `deepseek/deepseek-v4-pro-0813` | 0.34 (n=140) | 0.56 (n=75) | 0.33 (n=30) |
| `google/gemini-2.5-flash` | 0.92 (n=140) | 0.93 (n=75) | 0.83 (n=30) |
| `google/gemini-2.5-flash-lite` | 0.86 (n=140) | 0.91 (n=75) | 1.00 (n=30) |
| `google/gemini-2.5-pro` | 0.89 (n=140) | 0.88 (n=75) | 0.63 (n=30) |
| `google/gemini-3.1-flash-lite` | 0.92 (n=140) | 1.00 (n=75) | 1.00 (n=30) |
| `google/gemini-3.1-pro-preview` | 0.93 (n=140) | 0.96 (n=75) | 0.50 (n=30) |
| `google/gemini-3.5-flash` | 0.93 (n=140) | 1.00 (n=75) | 0.67 (n=30) |
| `google/gemini-3.5-flash-lite` | 0.91 (n=140) | 0.87 (n=75) | 0.60 (n=30) |
| `google/gemini-3.6-flash` | 0.93 (n=140) | 0.99 (n=75) | 0.57 (n=30) |
| `google/gemini-3.7-flash` | 0.93 (n=140) | 0.95 (n=75) | 0.53 (n=30) |
| `google/gemma-4-26b-a4b-it` | 0.56 (n=140) | 0.75 (n=75) | 1.00 (n=30) |
| `google/gemma-4-31b-it` | 0.79 (n=140) | 0.80 (n=75) | 0.47 (n=30) |
| `inclusionai/ring-2.6-1t` | 0.18 (n=140) | 0.35 (n=75) | 0.50 (n=30) |
| `meituan/longcat-2.0` | 0.39 (n=140) | 0.44 (n=75) | 0.50 (n=30) |
| `meta-llama/llama-3.1-8b-instruct` | 0.24 (n=140) | 0.37 (n=75) | 0.20 (n=30) |
| `meta-llama/llama-3.3-70b-instruct` | 0.41 (n=140) | 0.63 (n=75) | 0.67 (n=30) |
| `meta-llama/llama-4-maverick` | 0.62 (n=140) | 0.77 (n=75) | 0.53 (n=30) |
| `meta-llama/llama-4-scout` | 0.44 (n=140) | 0.49 (n=75) | 0.47 (n=30) |
| `meta/muse-glimmer-30b` | 0.63 (n=140) | 0.79 (n=75) | 0.67 (n=30) |
| `meta/muse-spark-1.1` | 0.65 (n=140) | 0.53 (n=75) | 0.47 (n=30) |
| `microsoft/phi-4` | 0.21 (n=140) | 0.57 (n=75) | 0.17 (n=30) |
| `minimax/minimax-m3` | 0.64 (n=140) | 0.64 (n=75) | 0.63 (n=30) |
| `mistralai/codestral-2508` | 0.74 (n=140) | 0.73 (n=75) | 0.47 (n=30) |
| `mistralai/mistral-large-2512` | 0.93 (n=140) | 0.99 (n=75) | 0.77 (n=30) |
| `mistralai/mistral-medium-3-5` | 0.90 (n=140) | 0.93 (n=75) | 0.83 (n=30) |
| `mistralai/mistral-medium-3.1` | 0.84 (n=140) | 0.88 (n=75) | 0.73 (n=30) |
| `mistralai/mistral-small-2603` | 0.00 (n=140) | 0.07 (n=75) | 0.17 (n=30) |
| `moonshotai/kimi-k2.6` | 0.34 (n=140) | 0.51 (n=75) | 0.23 (n=30) |
| `moonshotai/kimi-k3` | 0.75 (n=140) | 0.91 (n=75) | 0.57 (n=30) |
| `nvidia/nemotron-3-super-120b-a12b` | 0.48 (n=140) | 0.65 (n=75) | 0.80 (n=30) |
| `nvidia/nemotron-3-ultra-550b-a55b` | 0.37 (n=140) | 0.45 (n=75) | 0.50 (n=30) |
| `nvidia/nemotron-3.5-lightning` | 0.29 (n=140) | 0.28 (n=75) | 0.37 (n=30) |
| `openai/gpt-3.5-turbo` | 0.31 (n=140) | 0.47 (n=75) | 0.83 (n=30) |
| `openai/gpt-5.4` | 0.90 (n=140) | 0.85 (n=75) | 0.57 (n=30) |
| `openai/gpt-5.4-mini` | 0.86 (n=140) | 0.79 (n=75) | 0.80 (n=30) |
| `openai/gpt-5.5` | 0.91 (n=140) | 0.92 (n=75) | 0.57 (n=30) |
| `openai/gpt-5.6-luna` | 0.89 (n=140) | 0.88 (n=75) | 0.77 (n=30) |
| `openai/gpt-5.6-sol` | 0.89 (n=140) | 0.91 (n=75) | 0.77 (n=30) |
| `openai/gpt-5.6-terra` | 0.88 (n=140) | 0.88 (n=75) | 0.67 (n=30) |
| `openai/gpt-oss-120b` | 0.71 (n=140) | 0.92 (n=75) | 0.97 (n=30) |
| `openai/gpt-oss-20b` | 0.50 (n=140) | 0.67 (n=75) | 0.80 (n=30) |
| `openai/o3` | 0.46 (n=140) | 0.33 (n=75) | 0.30 (n=30) |
| `openai/o4-mini` | 0.06 (n=140) | 0.09 (n=75) | 0.17 (n=30) |
| `qwen/qwen3-14b` | 0.20 (n=140) | 0.47 (n=75) | 0.80 (n=30) |
| `qwen/qwen3-235b-a22b-2507` | 0.57 (n=140) | 0.72 (n=75) | 0.70 (n=30) |
| `qwen/qwen3-8b` | 0.00 (n=140) | 0.00 (n=75) | 0.00 (n=30) |
| `qwen/qwen3.5-27b` | 0.61 (n=140) | 0.53 (n=75) | 0.60 (n=30) |
| `qwen/qwen3.5-plus-02-15` | 0.91 (n=140) | 1.00 (n=75) | 0.67 (n=30) |
| `qwen/qwen3.6-flash` | 0.80 (n=140) | 0.95 (n=75) | 0.67 (n=30) |
| `qwen/qwen3.6-plus` | 0.61 (n=140) | 0.76 (n=75) | 0.70 (n=30) |
| `qwen/qwen3.7-flash` | 0.76 (n=140) | 0.91 (n=75) | 0.63 (n=30) |
| `qwen/qwen3.7-max` | 0.82 (n=140) | 1.00 (n=75) | 0.67 (n=30) |
| `qwen/qwen3.7-plus` | 0.72 (n=140) | 0.93 (n=75) | 0.67 (n=30) |
| `qwen/qwen3.8-2.4t-a95b` | 0.40 (n=140) | 0.35 (n=75) | 0.33 (n=30) |
| `qwen/qwen3.8-27b` | 0.44 (n=140) | 0.51 (n=75) | 0.37 (n=30) |
| `qwen/qwen3.8-max` | 0.19 (n=140) | 0.21 (n=75) | 0.07 (n=30) |
| `stealth/ox-alpha` | 0.80 (n=140) | 0.89 (n=75) | 0.57 (n=30) |
| `stepfun/step-3.7-flash` | 0.23 (n=95) | 0.39 (n=70) | 0.44 (n=25) |
| `tencent/hy3` | 0.41 (n=140) | 0.45 (n=75) | 0.47 (n=30) |
| `thinkingmachines/inkling` | 0.15 (n=140) | 0.21 (n=75) | 0.13 (n=30) |
| `thinkingmachines/inkling-small` | 0.29 (n=140) | 0.51 (n=75) | 0.27 (n=30) |
| `x-ai/grok-4.3` | 0.91 (n=140) | 0.96 (n=75) | 0.80 (n=30) |
| `x-ai/grok-4.5` | 0.93 (n=140) | 1.00 (n=75) | 0.67 (n=30) |
| `x-ai/grok-4.6` | 0.92 (n=140) | 1.00 (n=75) | 0.63 (n=30) |
| `xiaomi/mimo-v2.5` | 0.71 (n=140) | 0.59 (n=75) | 0.57 (n=30) |
| `xiaomi/mimo-v2.5-pro` | 0.49 (n=140) | 0.41 (n=75) | 0.40 (n=30) |
| `z-ai/glm-5.2` | 0.83 (n=140) | 0.84 (n=75) | 0.60 (n=30) |

### By ad position

Truth ads bucketed by where they fall in the episode: pre-roll (first 10%), mid-roll (10-90%), post-roll (last 10%). Cell values are the same detection-rate-with-`n` format as ad length. A common failure pattern in our data: most models detect pre-roll and mid-roll reliably and miss post-roll, because the prompt windows near the end often catch the model mid-reasoning or with fewer transition phrases to anchor on.

| Model | pre-roll (<10%) | mid-roll (10-90%) | post-roll (>90%) |
|---|---:|---:|---:|
| `bytedance-seed/seed-2-1-turbo` | 0.00 (n=70) | 0.00 (n=125) | 0.08 (n=50) |
| `claude-fable-5` | 1.00 (n=70) | 0.94 (n=125) | 0.80 (n=50) |
| `claude-haiku-4-5-20251001` | 1.00 (n=70) | 0.97 (n=125) | 0.76 (n=50) |
| `claude-opus-4-7` | 0.99 (n=70) | 0.86 (n=125) | 0.88 (n=50) |
| `claude-opus-4-8` | 1.00 (n=70) | 0.91 (n=125) | 0.74 (n=50) |
| `claude-opus-5` | 1.00 (n=70) | 0.84 (n=125) | 0.92 (n=50) |
| `claude-sonnet-4-6` | 0.93 (n=70) | 0.93 (n=125) | 0.80 (n=50) |
| `claude-sonnet-5` | 0.99 (n=70) | 0.87 (n=125) | 0.72 (n=50) |
| `cohere/command-a` | 0.67 (n=70) | 0.57 (n=125) | 0.52 (n=50) |
| `cohere/command-r-plus-08-2024` | 0.36 (n=70) | 0.48 (n=125) | 0.12 (n=50) |
| `deepseek/deepseek-r1` | 0.79 (n=70) | 0.74 (n=125) | 0.58 (n=50) |
| `deepseek/deepseek-r1-0528` | 0.76 (n=70) | 0.68 (n=125) | 0.58 (n=50) |
| `deepseek/deepseek-r1-distill-llama-70b` | 0.40 (n=70) | 0.39 (n=125) | 0.46 (n=50) |
| `deepseek/deepseek-v3.2` | 0.76 (n=70) | 0.66 (n=125) | 0.20 (n=50) |
| `deepseek/deepseek-v4-flash` | 0.76 (n=70) | 0.77 (n=125) | 0.64 (n=50) |
| `deepseek/deepseek-v4-flash-0731` | 0.51 (n=70) | 0.53 (n=125) | 0.52 (n=50) |
| `deepseek/deepseek-v4-pro` | 0.64 (n=70) | 0.73 (n=125) | 0.60 (n=50) |
| `deepseek/deepseek-v4-pro-0813` | 0.50 (n=70) | 0.36 (n=125) | 0.38 (n=50) |
| `google/gemini-2.5-flash` | 0.93 (n=70) | 0.95 (n=125) | 0.80 (n=50) |
| `google/gemini-2.5-flash-lite` | 0.91 (n=70) | 0.90 (n=125) | 0.84 (n=50) |
| `google/gemini-2.5-pro` | 0.89 (n=70) | 0.87 (n=125) | 0.76 (n=50) |
| `google/gemini-3.1-flash-lite` | 1.00 (n=70) | 0.99 (n=125) | 0.80 (n=50) |
| `google/gemini-3.1-pro-preview` | 1.00 (n=70) | 0.86 (n=125) | 0.78 (n=50) |
| `google/gemini-3.5-flash` | 1.00 (n=70) | 0.92 (n=125) | 0.80 (n=50) |
| `google/gemini-3.5-flash-lite` | 0.99 (n=70) | 0.88 (n=125) | 0.62 (n=50) |
| `google/gemini-3.6-flash` | 1.00 (n=70) | 0.89 (n=125) | 0.80 (n=50) |
| `google/gemini-3.7-flash` | 1.00 (n=70) | 0.86 (n=125) | 0.80 (n=50) |
| `google/gemma-4-26b-a4b-it` | 0.71 (n=70) | 0.69 (n=125) | 0.56 (n=50) |
| `google/gemma-4-31b-it` | 0.81 (n=70) | 0.75 (n=125) | 0.66 (n=50) |
| `inclusionai/ring-2.6-1t` | 0.26 (n=70) | 0.30 (n=125) | 0.22 (n=50) |
| `meituan/longcat-2.0` | 0.46 (n=70) | 0.44 (n=125) | 0.30 (n=50) |
| `meta-llama/llama-3.1-8b-instruct` | 0.20 (n=70) | 0.29 (n=125) | 0.34 (n=50) |
| `meta-llama/llama-3.3-70b-instruct` | 0.53 (n=70) | 0.52 (n=125) | 0.44 (n=50) |
| `meta-llama/llama-4-maverick` | 0.77 (n=70) | 0.70 (n=125) | 0.40 (n=50) |
| `meta-llama/llama-4-scout` | 0.46 (n=70) | 0.48 (n=125) | 0.40 (n=50) |
| `meta/muse-glimmer-30b` | 0.79 (n=70) | 0.65 (n=125) | 0.62 (n=50) |
| `meta/muse-spark-1.1` | 0.61 (n=70) | 0.64 (n=125) | 0.44 (n=50) |
| `microsoft/phi-4` | 0.54 (n=70) | 0.12 (n=125) | 0.48 (n=50) |
| `minimax/minimax-m3` | 0.49 (n=70) | 0.71 (n=125) | 0.66 (n=50) |
| `mistralai/codestral-2508` | 0.50 (n=70) | 0.77 (n=125) | 0.82 (n=50) |
| `mistralai/mistral-large-2512` | 0.99 (n=70) | 0.94 (n=125) | 0.80 (n=50) |
| `mistralai/mistral-medium-3-5` | 0.93 (n=70) | 0.93 (n=125) | 0.80 (n=50) |
| `mistralai/mistral-medium-3.1` | 1.00 (n=70) | 0.78 (n=125) | 0.74 (n=50) |
| `mistralai/mistral-small-2603` | 0.07 (n=70) | 0.04 (n=125) | 0.00 (n=50) |
| `moonshotai/kimi-k2.6` | 0.41 (n=70) | 0.31 (n=125) | 0.48 (n=50) |
| `moonshotai/kimi-k3` | 0.90 (n=70) | 0.73 (n=125) | 0.72 (n=50) |
| `nvidia/nemotron-3-super-120b-a12b` | 0.66 (n=70) | 0.56 (n=125) | 0.48 (n=50) |
| `nvidia/nemotron-3-ultra-550b-a55b` | 0.36 (n=70) | 0.44 (n=125) | 0.42 (n=50) |
| `nvidia/nemotron-3.5-lightning` | 0.17 (n=70) | 0.31 (n=125) | 0.42 (n=50) |
| `openai/gpt-3.5-turbo` | 0.29 (n=70) | 0.48 (n=125) | 0.46 (n=50) |
| `openai/gpt-5.4` | 0.89 (n=70) | 0.86 (n=125) | 0.74 (n=50) |
| `openai/gpt-5.4-mini` | 0.87 (n=70) | 0.90 (n=125) | 0.58 (n=50) |
| `openai/gpt-5.5` | 1.00 (n=70) | 0.86 (n=125) | 0.72 (n=50) |
| `openai/gpt-5.6-luna` | 1.00 (n=70) | 0.90 (n=125) | 0.62 (n=50) |
| `openai/gpt-5.6-sol` | 0.99 (n=70) | 0.88 (n=125) | 0.72 (n=50) |
| `openai/gpt-5.6-terra` | 0.93 (n=70) | 0.87 (n=125) | 0.70 (n=50) |
| `openai/gpt-oss-120b` | 0.83 (n=70) | 0.79 (n=125) | 0.80 (n=50) |
| `openai/gpt-oss-20b` | 0.59 (n=70) | 0.61 (n=125) | 0.54 (n=50) |
| `openai/o3` | 0.31 (n=70) | 0.45 (n=125) | 0.40 (n=50) |
| `openai/o4-mini` | 0.10 (n=70) | 0.08 (n=125) | 0.08 (n=50) |
| `qwen/qwen3-14b` | 0.34 (n=70) | 0.37 (n=125) | 0.34 (n=50) |
| `qwen/qwen3-235b-a22b-2507` | 0.60 (n=70) | 0.66 (n=125) | 0.62 (n=50) |
| `qwen/qwen3-8b` | 0.00 (n=70) | 0.00 (n=125) | 0.00 (n=50) |
| `qwen/qwen3.5-27b` | 0.61 (n=70) | 0.59 (n=125) | 0.54 (n=50) |
| `qwen/qwen3.5-plus-02-15` | 1.00 (n=70) | 0.90 (n=125) | 0.80 (n=50) |
| `qwen/qwen3.6-flash` | 0.90 (n=70) | 0.84 (n=125) | 0.70 (n=50) |
| `qwen/qwen3.6-plus` | 0.73 (n=70) | 0.67 (n=125) | 0.56 (n=50) |
| `qwen/qwen3.7-flash` | 0.90 (n=70) | 0.77 (n=125) | 0.70 (n=50) |
| `qwen/qwen3.7-max` | 0.94 (n=70) | 0.86 (n=125) | 0.72 (n=50) |
| `qwen/qwen3.7-plus` | 0.90 (n=70) | 0.76 (n=125) | 0.66 (n=50) |
| `qwen/qwen3.8-2.4t-a95b` | 0.33 (n=70) | 0.41 (n=125) | 0.36 (n=50) |
| `qwen/qwen3.8-27b` | 0.61 (n=70) | 0.43 (n=125) | 0.28 (n=50) |
| `qwen/qwen3.8-max` | 0.30 (n=70) | 0.08 (n=125) | 0.28 (n=50) |
| `stealth/ox-alpha` | 0.93 (n=70) | 0.76 (n=125) | 0.72 (n=50) |
| `stepfun/step-3.7-flash` | 0.38 (n=55) | 0.32 (n=95) | 0.23 (n=40) |
| `tencent/hy3` | 0.37 (n=70) | 0.47 (n=125) | 0.40 (n=50) |
| `thinkingmachines/inkling` | 0.11 (n=70) | 0.17 (n=125) | 0.24 (n=50) |
| `thinkingmachines/inkling-small` | 0.36 (n=70) | 0.32 (n=125) | 0.44 (n=50) |
| `x-ai/grok-4.3` | 0.96 (n=70) | 0.94 (n=125) | 0.78 (n=50) |
| `x-ai/grok-4.5` | 1.00 (n=70) | 0.92 (n=125) | 0.80 (n=50) |
| `x-ai/grok-4.6` | 0.99 (n=70) | 0.91 (n=125) | 0.80 (n=50) |
| `xiaomi/mimo-v2.5` | 0.54 (n=70) | 0.72 (n=125) | 0.66 (n=50) |
| `xiaomi/mimo-v2.5-pro` | 0.50 (n=70) | 0.47 (n=125) | 0.36 (n=50) |
| `z-ai/glm-5.2` | 0.86 (n=70) | 0.85 (n=125) | 0.62 (n=50) |

## Quick Comparison

One row per model, one column per episode. The headline columns (`F1`, `Cost/ep`, `p50`) summarize across all episodes; the per-episode columns let you see whether a model's average hides wide swings (a model that scores well overall might still bomb on a specific genre). The right-most `F1 stdev` column averages the per-trial standard deviations across episodes; high values mean the model isn't deterministic at temperature 0.0, so its single-trial F1 number is noisy. `Moderation blocked` is the share of attempted calls the provider refused on content grounds; those windows never reach scoring, so any non-zero value means that row's F1 was computed on a subset of the corpus and is not comparable to a row at `-`.

| Model | F1 | Cost/ep | p50 | ep-crime-junkie-8ce498f299d7 | ep-daily-gist-chicago-70a82fe93a5c | ep-daily-tech-news-show-b576979e1fe8 | ep-daily-tech-news-show-c1904b8605f7 | ep-drink-champs-30c9a2d49f13 | ep-glt1412515089-373d5ba5007b | ep-it-s-a-thing-e339179dfad6 | ep-on-air-with-dan-and-alex2-574e4f303730 | ep-security-now-audio-2850b24903b2 | ep-the-brilliant-idiots-0bb9bf634c8e | ep-the-tim-dillon-show-f62bd5fa1cfe | ep-tosh-show-5f6894439bb6 | ep-ai-cloud-essentials-e8dc897fbd6b (no-ad) | ep-oxide-and-friends-ce789ff5b62e (no-ad) | F1 stdev | Moderation blocked |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| `claude-haiku-4-5-20251001` | 0.920 | $1.0820 | 24.2s | 1.000 | 1.000 | 1.000 | 0.667 | 0.929 | 0.814 | 1.000 | 0.920 | 0.800 | 1.000 | 1.000 | 0.912 | PASS | PASS | 0.037 | - |
| `qwen/qwen3.5-plus-02-15` | 0.874 | $1.0746 | 29.2s | 1.000 | 0.500 | 0.889 | 0.750 | 0.963 | 0.857 | 1.000 | 0.960 | 0.773 | 0.800 | 1.000 | 1.000 | PASS | PASS | 0.026 | - |
| `google/gemini-3.5-flash` | 0.867 | $3.4202 | 5.6s | 1.000 | 0.500 | 1.000 | 0.640 | 0.911 | 0.857 | 1.000 | 0.800 | 0.800 | 1.000 | 1.000 | 0.894 | PASS | PASS | 0.010 | - |
| `claude-sonnet-4-6` | 0.866 | $3.2386 | 4.1s | 1.000 | 0.600 | 1.000 | 0.667 | 0.931 | 0.857 | 1.000 | 0.773 | 0.780 | 1.000 | 0.857 | 0.927 | PASS | PASS | 0.031 | - |
| `x-ai/grok-4.3` | 0.865 | $1.6048 | 3.7s | 1.000 | 0.460 | 0.978 | 0.640 | 0.933 | 0.921 | 1.000 | 1.000 | 0.800 | 0.743 | 0.956 | 0.945 | PASS | PASS | 0.056 | - |
| `x-ai/grok-4.5` | 0.861 | $3.3554 | 10.9s | 0.956 | 0.500 | 1.000 | 0.600 | 0.953 | 0.857 | 1.000 | 0.960 | 0.800 | 0.857 | 1.000 | 0.848 | PASS | PASS | 0.018 | - |
| `google/gemini-3.6-flash` | 0.858 | $1.4431 | 4.5s | 1.000 | 0.500 | 1.000 | 0.667 | 0.889 | 0.857 | 1.000 | 0.773 | 0.750 | 1.000 | 1.000 | 0.857 | PASS | PASS | 0.009 | - |
| `google/gemini-3.5-flash-lite` | 0.851 | $0.3643 | 0.7s | 0.876 | 1.000 | 0.892 | 0.564 | 0.954 | 0.531 | 1.000 | 0.933 | 0.783 | 0.700 | 0.978 | 1.000 | PASS | PASS | 0.072 | - |
| `google/gemini-3.7-flash` | 0.848 | $0.6411 | 4.7s | 1.000 | 0.500 | 1.000 | 0.667 | 0.889 | 0.857 | 1.000 | 0.800 | 0.750 | 1.000 | 1.000 | 0.715 | PASS | PASS | 0.009 | - |
| `x-ai/grok-4.6` | 0.847 | $3.7063 | 13.4s | 0.911 | 0.500 | 1.000 | 0.613 | 0.895 | 0.857 | 0.960 | 0.960 | 0.827 | 0.743 | 1.000 | 0.897 | PASS | PASS | 0.048 | - |
| `claude-fable-5` | 0.847 | $10.7552 | 6.6s | 0.933 | 0.700 | 0.978 | 0.643 | 0.808 | 0.857 | 1.000 | 0.960 | 0.750 | 0.667 | 1.000 | 0.864 | PASS | PASS | 0.048 | - |
| `openai/gpt-5.5` | 0.847 | $7.6842 | 5.4s | 1.000 | 0.400 | 1.000 | 0.750 | 0.872 | 0.914 | 1.000 | 0.800 | 0.857 | 0.943 | 1.000 | 0.622 | FAIL (1 FP) | PASS | 0.026 | - |
| `mistralai/mistral-medium-3-5` | 0.842 | $1.8393 | 1.1s | 1.000 | 0.500 | 1.000 | 0.733 | 0.872 | 0.740 | 1.000 | 1.000 | 0.800 | 0.667 | 0.809 | 0.982 | PASS | PASS | 0.030 | - |
| `openai/gpt-5.6-terra` | 0.830 | $2.3902 | 2.1s | 1.000 | 0.400 | 0.950 | 0.750 | 0.821 | 0.857 | 0.920 | 0.960 | 0.801 | 0.791 | 1.000 | 0.711 | FAIL (2 FP) | FAIL (1 FP) | 0.055 | - |
| `google/gemini-3.1-flash-lite` | 0.829 | $0.2988 | 0.8s | 0.950 | 0.800 | 1.000 | 0.667 | 0.843 | 0.681 | 1.000 | 0.800 | 0.667 | 0.540 | 1.000 | 1.000 | FAIL (1 FP) | PASS | 0.020 | - |
| `claude-opus-4-8` | 0.827 | $5.3661 | 7.8s | 0.889 | 0.800 | 0.956 | 0.653 | 0.920 | 0.743 | 1.000 | 0.800 | 0.780 | 0.556 | 1.000 | 0.822 | PASS | PASS | 0.038 | - |
| `qwen/qwen3.7-max` | 0.826 | $2.8109 | 23.4s | 0.950 | 0.500 | 1.000 | 0.600 | 0.639 | 0.857 | 1.000 | 0.840 | 0.780 | 0.821 | 1.000 | 0.930 | PASS | PASS | 0.050 | - |
| `claude-opus-4-7` | 0.822 | $5.3293 | 4.6s | 0.978 | 0.400 | 1.000 | 0.933 | 0.845 | 0.857 | 1.000 | 0.740 | 0.772 | 0.578 | 1.000 | 0.756 | PASS | PASS | 0.056 | - |
| `openai/gpt-5.6-luna` | 0.817 | $0.2804 | 3.5s | 0.933 | 0.400 | 1.000 | 0.700 | 0.824 | 0.949 | 1.000 | 0.693 | 0.846 | 0.793 | 1.000 | 0.667 | PASS | PASS | 0.045 | - |
| `qwen/qwen3.6-flash` | 0.815 | $0.5435 | 7.6s | 1.000 | 0.500 | 0.921 | 0.700 | 0.683 | 0.755 | 0.933 | 0.840 | 0.800 | 0.810 | 0.900 | 0.938 | PASS | PASS | 0.084 | - |
| `google/gemini-3.1-pro-preview` | 0.815 | $4.9160 | 8.3s | 1.000 | 0.500 | 0.950 | 0.640 | 0.880 | 0.857 | 1.000 | 0.667 | 0.750 | 0.857 | 1.000 | 0.676 | PASS | PASS | 0.020 | - |
| `stealth/ox-alpha` | 0.810 | $0.0000 | 66.6s | 1.000 | 0.480 | 1.000 | 0.683 | 0.607 | 0.857 | 1.000 | 0.800 | 0.725 | 1.000 | 1.000 | 0.569 | PASS | PASS | 0.032 | - |
| `claude-opus-5` | 0.806 | $5.3761 | 3.7s | 1.000 | 0.400 | 1.000 | 0.684 | 0.861 | 0.857 | 0.800 | 0.800 | 0.933 | 0.667 | 1.000 | 0.667 | PASS | PASS | 0.012 | - |
| `claude-sonnet-5` | 0.804 | $2.1452 | 8.5s | 0.938 | 0.560 | 0.978 | 0.627 | 0.850 | 0.857 | 0.960 | 0.960 | 0.775 | 0.514 | 0.978 | 0.647 | PASS | PASS | 0.087 | - |
| `openai/gpt-5.6-sol` | 0.799 | $2.4303 | 3.7s | 1.000 | 0.420 | 1.000 | 0.700 | 0.680 | 0.739 | 0.800 | 0.747 | 0.823 | 0.834 | 0.960 | 0.889 | FAIL (3 FP) | FAIL (2 FP) | 0.074 | - |
| `mistralai/mistral-medium-3.1` | 0.793 | $0.4847 | 0.7s | 1.000 | 0.567 | 0.750 | 0.613 | 0.800 | 0.711 | 0.800 | 1.000 | 0.613 | 0.681 | 0.978 | 1.000 | PASS | PASS | 0.039 | - |
| `qwen/qwen3.7-flash` | 0.774 | $0.0725 | 10.1s | 1.000 | 0.500 | 0.771 | 0.670 | 0.580 | 0.664 | 0.833 | 0.800 | 0.800 | 0.769 | 0.978 | 0.924 | PASS | PASS | 0.116 | - |
| `moonshotai/kimi-k3` | 0.770 | $5.9242 | 13.8s | 0.933 | 0.480 | 0.943 | 0.667 | 0.450 | 0.886 | 0.933 | 0.667 | 0.814 | 0.864 | 1.000 | 0.599 | FAIL (1 FP) | PASS | 0.074 | - |
| `google/gemini-2.5-flash` | 0.761 | $0.3796 | 0.8s | 1.000 | 0.420 | 0.889 | 0.600 | 0.780 | 1.000 | 0.667 | 0.800 | 0.706 | 0.500 | 1.000 | 0.769 | PASS | PASS | 0.007 | - |
| `google/gemini-2.5-pro` | 0.752 | $4.1601 | 14.2s | 0.933 | 0.400 | 0.943 | 0.580 | 0.854 | 0.750 | 0.733 | 0.800 | 0.741 | 0.633 | 1.000 | 0.658 | FAIL (1 FP) | FAIL (1 FP) | 0.062 | - |
| `deepseek/deepseek-v4-flash` | 0.749 | $0.0715 | 6.3s | 1.000 | 0.613 | 0.886 | 0.490 | 0.729 | 0.921 | 0.633 | 0.800 | 0.732 | 0.903 | 0.921 | 0.355 | PASS | PASS | 0.126 | - |
| `openai/gpt-oss-120b` | 0.739 | $0.0634 | 6.5s | 0.943 | 0.820 | 0.855 | 0.525 | 0.277 | 0.956 | 0.587 | 0.728 | 0.751 | 0.762 | 0.831 | 0.830 | FAIL (1 FP) | FAIL (1 FP) | 0.121 | - |
| `google/gemma-4-31b-it` | 0.699 | $0.1181 | 2.4s | 0.876 | 0.400 | 0.893 | 0.550 | 0.677 | 0.828 | 0.467 | 0.960 | 0.750 | 0.550 | 0.867 | 0.577 | FAIL (1 FP) | PASS | 0.138 | - |
| `deepseek/deepseek-v4-pro` | 0.695 | $0.6106 | 16.2s | 0.851 | 0.280 | 0.876 | 0.662 | 0.566 | 0.857 | 0.700 | 0.700 | 0.790 | 0.597 | 1.000 | 0.456 | FAIL (1 FP) | PASS | 0.160 | - |
| `meta/muse-glimmer-30b` | 0.694 | $0.6965 | 10.8s | 0.978 | 0.440 | 0.750 | 0.592 | 0.373 | 0.857 | 0.700 | 0.640 | 0.762 | 0.971 | 0.750 | 0.509 | PASS | PASS | 0.140 | - |
| `google/gemini-2.5-flash-lite` | 0.689 | $0.1132 | 0.8s | 0.943 | 0.800 | 0.971 | 0.667 | 0.691 | 0.460 | 0.533 | 0.720 | 0.639 | 0.466 | 0.570 | 0.802 | FAIL (2 FP) | PASS | 0.074 | - |
| `openai/gpt-5.4` | 0.685 | $2.7907 | 1.4s | 0.818 | 0.420 | 0.878 | 0.750 | 0.740 | 0.565 | 0.540 | 0.674 | 0.667 | 0.670 | 0.978 | 0.520 | FAIL (1 FP) | FAIL (3 FP) | 0.077 | - |
| `deepseek/deepseek-v3.2` | 0.676 | $0.2914 | 1.7s | 0.857 | 0.633 | 0.667 | 0.717 | 0.873 | 0.613 | 0.760 | 0.667 | 0.306 | 0.500 | 0.705 | 0.816 | PASS | PASS | 0.077 | - |
| `meta/muse-spark-1.1` | 0.668 | $2.2536 | 4.3s | 0.813 | 0.433 | 0.771 | 0.523 | 0.643 | 0.766 | 0.800 | 0.667 | 0.642 | 0.680 | 0.773 | 0.505 | PASS | PASS | 0.220 | - |
| `qwen/qwen3.6-plus` | 0.661 | $1.1534 | 36.9s | 0.971 | 0.433 | 0.686 | 0.400 | 0.447 | 0.844 | 0.360 | 0.800 | 0.645 | 0.886 | 0.833 | 0.630 | PASS | PASS | 0.173 | - |
| `z-ai/glm-5.2` | 0.657 | $1.3083 | 3.5s | 0.910 | 0.320 | 0.813 | 0.607 | 0.567 | 0.555 | 0.853 | 0.603 | 0.659 | 0.527 | 0.793 | 0.670 | FAIL (2 FP) | FAIL (5 FP) | 0.128 | - |
| `openai/gpt-5.4-mini` | 0.640 | $0.8470 | 1.1s | 0.956 | 0.800 | 1.000 | 0.600 | 0.606 | 0.456 | 0.467 | 0.800 | 0.569 | 0.241 | 0.642 | 0.542 | FAIL (2 FP) | FAIL (3 FP) | 0.069 | - |
| `deepseek/deepseek-r1` | 0.638 | $1.4743 | 35.7s | 0.857 | 0.360 | 0.848 | 0.429 | 0.346 | 0.762 | 0.633 | 0.800 | 0.584 | 0.474 | 0.764 | 0.802 | FAIL (1 FP) | FAIL (3 FP) | 0.140 | - |
| `nvidia/nemotron-3-super-120b-a12b` | 0.635 | $0.2194 | 22.3s | 0.971 | 0.760 | 0.785 | 0.545 | 0.031 | 0.892 | 0.400 | 0.800 | 0.794 | 0.754 | 0.529 | 0.364 | FAIL (1 FP) | PASS | 0.155 | - |
| `minimax/minimax-m3` | 0.633 | $0.3575 | 1.7s | 0.507 | 0.533 | 0.547 | 0.529 | 0.805 | 0.853 | 0.533 | 0.780 | 0.638 | 0.610 | 0.483 | 0.774 | FAIL (1 FP) | FAIL (1 FP) | 0.205 | - |
| `qwen/qwen3.5-27b` | 0.612 | $0.8941 | 37.6s | 0.373 | 0.640 | 0.598 | 0.486 | 0.663 | 0.800 | 0.733 | 0.693 | 0.779 | 0.586 | 0.491 | 0.507 | PASS | PASS | 0.218 | - |
| `google/gemma-4-26b-a4b-it` | 0.612 | $0.0825 | 1.4s | 0.950 | 0.920 | 0.428 | 0.239 | 0.403 | 0.867 | 0.100 | 0.667 | 0.800 | 0.697 | 0.778 | 0.498 | FAIL (1 FP) | PASS | 0.118 | - |
| `mistralai/mistral-large-2512` | 0.612 | $0.6184 | 3.3s | 0.933 | 0.407 | 0.911 | 0.667 | 0.545 | 0.400 | 0.551 | 0.800 | 0.400 | 0.239 | 0.581 | 0.909 | FAIL (1 FP) | PASS | 0.046 | - |
| `deepseek/deepseek-v4-flash-0731` | 0.607 | $0.1371 | 13.2s | 0.905 | 0.667 | 0.706 | 0.347 | 0.391 | 0.743 | 0.267 | 0.740 | 0.560 | 0.943 | 0.807 | 0.203 | PASS | PASS | 0.176 | - |
| `deepseek/deepseek-r1-0528` | 0.606 | $1.1645 | 29.4s | 0.728 | 0.440 | 0.744 | 0.487 | 0.417 | 0.750 | 0.573 | 0.773 | 0.544 | 0.499 | 0.677 | 0.640 | FAIL (1 FP) | FAIL (8 FP) | 0.160 | - |
| `openai/gpt-oss-20b` | 0.572 | $0.0620 | 9.2s | 0.943 | 0.780 | 0.421 | 0.349 | 0.136 | 0.692 | 0.293 | 0.560 | 0.765 | 0.825 | 0.648 | 0.457 | FAIL (1 FP) | FAIL (1 FP) | 0.170 | - |
| `meta-llama/llama-4-maverick` | 0.561 | $0.2206 | 1.1s | 0.889 | 0.320 | 1.000 | 0.600 | 0.303 | 0.518 | 0.400 | 0.693 | 0.675 | 0.667 | 0.222 | 0.444 | FAIL (1 FP) | PASS | 0.035 | - |
| `qwen/qwen3.7-plus` | 0.551 | $0.7238 | 23.7s | 0.744 | 0.400 | 0.667 | 0.557 | 0.342 | 0.455 | 0.693 | 0.514 | 0.527 | 0.270 | 0.715 | 0.728 | FAIL (2 FP) | FAIL (11 FP) | 0.067 | - |
| `qwen/qwen3.8-27b` | 0.550 | $1.3947 | 70.3s | 0.886 | 0.340 | 0.545 | 0.493 | 0.073 | 0.857 | 0.333 | 0.840 | 0.686 | 0.660 | 0.893 | 0.000 | PASS | PASS | 0.145 | - |
| `mistralai/codestral-2508` | 0.536 | $0.3585 | 0.9s | 0.800 | 0.400 | 0.800 | 0.200 | 0.520 | 0.371 | 0.667 | 0.610 | 0.554 | 0.300 | 0.485 | 0.727 | PASS | FAIL (1 FP) | 0.075 | - |
| `qwen/qwen3-235b-a22b-2507` | 0.532 | $0.1078 | 2.5s | 0.731 | 0.480 | 0.722 | 0.489 | 0.199 | 0.372 | 0.360 | 0.701 | 0.734 | 0.359 | 0.596 | 0.640 | FAIL (1 FP) | FAIL (6 FP) | 0.164 | - |
| `meta-llama/llama-3.3-70b-instruct` | 0.514 | $0.1107 | 1.3s | 0.800 | 0.567 | 0.789 | 0.339 | 0.042 | 0.659 | 0.500 | 0.480 | 0.857 | 0.571 | 0.150 | 0.410 | PASS | PASS | 0.126 | - |
| `deepseek/deepseek-v4-pro-0813` | 0.507 | $2.4576 | 26.6s | 0.914 | 0.100 | 0.667 | 0.147 | 0.143 | 0.819 | 0.267 | 0.800 | 0.418 | 0.960 | 0.788 | 0.057 | PASS | PASS | 0.143 | - |
| `xiaomi/mimo-v2.5-pro` | 0.506 | $0.4954 | 2.2s | 0.651 | 0.400 | 0.518 | 0.497 | 0.448 | 0.705 | 0.527 | 0.300 | 0.553 | 0.641 | 0.459 | 0.379 | PASS | FAIL (1 FP) | 0.246 | - |
| `tencent/hy3` | 0.503 | $0.3876 | 22.3s | 0.781 | 0.100 | 0.743 | 0.267 | 0.036 | 0.848 | 0.000 | 0.753 | 0.675 | 0.836 | 0.461 | 0.539 | PASS | PASS | 0.133 | - |
| `openai/o3` | 0.499 | $3.3100 | 6.7s | 0.507 | 0.000 | 0.545 | 0.423 | 0.501 | 0.857 | 0.600 | 0.733 | 0.574 | 0.451 | 0.560 | 0.233 | PASS | PASS | 0.191 | - |
| `xiaomi/mimo-v2.5` | 0.487 | $0.1692 | 4.2s | 0.843 | 0.080 | 0.649 | 0.639 | 0.376 | 0.402 | 0.260 | 0.441 | 0.557 | 0.341 | 0.635 | 0.622 | FAIL (2 FP) | FAIL (7 FP) | 0.155 | - |
| `nvidia/nemotron-3-ultra-550b-a55b` | 0.476 | $0.7782 | 1.2s | 0.813 | 0.473 | 0.547 | 0.588 | 0.056 | 0.598 | 0.600 | 0.513 | 0.374 | 0.244 | 0.438 | 0.471 | FAIL (1 FP) | FAIL (4 FP) | 0.230 | - |
| `meta-llama/llama-4-scout` | 0.469 | $0.1111 | 0.9s | 0.600 | 0.333 | 0.651 | 0.287 | 0.096 | 0.367 | 0.533 | 0.493 | 0.639 | 0.447 | 0.338 | 0.844 | FAIL (1 FP) | PASS | 0.207 | - |
| `cohere/command-a` | 0.464 | $2.9415 | 1.7s | 0.800 | 0.400 | 0.667 | 0.500 | 0.000 | 0.345 | 0.627 | 0.507 | 0.522 | 0.335 | 0.267 | 0.600 | FAIL (3 FP) | FAIL (5 FP) | 0.050 | - |
| `meituan/longcat-2.0` | 0.448 | $0.5573 | 10.5s | 0.000 | 0.233 | 0.613 | 0.505 | 0.432 | 0.781 | 0.600 | 0.500 | 0.559 | 0.280 | 0.213 | 0.664 | PASS | PASS | 0.160 | - |
| `qwen/qwen3.8-2.4t-a95b` | 0.446 | $4.2877 | 11.9s | 0.676 | 0.000 | 0.667 | 0.227 | 0.040 | 0.745 | 0.133 | 0.747 | 0.615 | 0.648 | 0.857 | 0.000 | PASS | FAIL (1 FP) | 0.108 | - |
| `deepseek/deepseek-r1-distill-llama-70b` | 0.433 | $0.6913 | 27.2s | 0.373 | 0.593 | 0.160 | 0.493 | 0.291 | 0.971 | 0.267 | 0.500 | 0.639 | 0.374 | 0.417 | 0.114 | FAIL (1 FP) | PASS | 0.157 | - |
| `moonshotai/kimi-k2.6` | 0.423 | $1.7617 | 55.7s | 0.581 | 0.080 | 0.819 | 0.213 | 0.040 | 0.473 | 0.100 | 0.813 | 0.522 | 0.397 | 0.526 | 0.510 | FAIL (1 FP) | FAIL (3 FP) | 0.178 | - |
| `stepfun/step-3.7-flash` | 0.409 | $0.4302 | 13.9s | 0.886 | 0.733 | 0.240 | 0.000 | - | 0.781 | 0.000 | 0.633 | 0.364 | - | 0.387 | 0.067 | PASS | PASS | 0.163 | 15.2% |
| `microsoft/phi-4` | 0.408 | $0.0792 | 0.5s | 0.246 | 0.000 | 0.427 | 0.271 | 0.000 | 0.000 | 0.867 | 0.800 | 0.363 | 0.620 | 0.721 | 0.583 | PASS | PASS | 0.092 | - |
| `thinkingmachines/inkling-small` | 0.395 | $1.0333 | 18.5s | 0.530 | 0.160 | 0.592 | 0.171 | 0.141 | 0.467 | 0.267 | 0.693 | 0.197 | 0.483 | 0.856 | 0.181 | FAIL (1 FP) | FAIL (6 FP) | 0.200 | - |
| `cohere/command-r-plus-08-2024` | 0.389 | $2.8505 | 1.1s | 0.613 | 0.267 | 0.133 | 0.481 | 0.153 | 0.712 | 0.000 | 0.600 | 0.825 | 0.460 | 0.124 | 0.295 | PASS | PASS | 0.146 | - |
| `qwen/qwen3-14b` | 0.372 | $0.1724 | 14.4s | 0.690 | 0.680 | 0.529 | 0.000 | 0.019 | 0.488 | 0.000 | 0.453 | 0.716 | 0.470 | 0.139 | 0.278 | FAIL (1 FP) | FAIL (1 FP) | 0.098 | - |
| `inclusionai/ring-2.6-1t` | 0.342 | $0.2468 | 10.8s | 0.507 | 0.867 | 0.522 | 0.067 | 0.033 | 0.537 | 0.000 | 0.400 | 0.566 | 0.350 | 0.124 | 0.129 | PASS | PASS | 0.148 | - |
| `openai/gpt-3.5-turbo` | 0.299 | $0.5564 | 1.4s | 0.871 | 0.400 | 0.222 | 0.500 | 0.000 | 0.263 | 0.000 | 0.444 | 0.364 | 0.211 | 0.092 | 0.218 | FAIL (2 FP) | FAIL (11 FP) | 0.012 | - |
| `qwen/qwen3.8-max` | 0.285 | $4.2714 | 29.7s | 0.280 | 0.000 | 0.320 | 0.270 | 0.000 | 0.373 | 0.000 | 0.900 | 0.089 | 0.460 | 0.724 | 0.000 | PASS | PASS | 0.122 | - |
| `nvidia/nemotron-3.5-lightning` | 0.262 | $0.1207 | 1.2s | 0.000 | 0.000 | 0.455 | 0.304 | 0.085 | 0.290 | 0.533 | 0.540 | 0.413 | 0.121 | 0.350 | 0.057 | PASS | FAIL (4 FP) | 0.088 | - |
| `meta-llama/llama-3.1-8b-instruct` | 0.261 | $0.0572 | 0.9s | 0.200 | 0.593 | 0.274 | 0.194 | 0.167 | 0.203 | 0.587 | 0.263 | 0.164 | 0.090 | 0.176 | 0.216 | PASS | FAIL (2 FP) | 0.164 | - |
| `thinkingmachines/inkling` | 0.236 | $3.1580 | 65.6s | 0.307 | 0.000 | 0.427 | 0.147 | 0.073 | 0.160 | 0.000 | 0.627 | 0.358 | 0.460 | 0.270 | 0.000 | PASS | FAIL (1 FP) | 0.164 | - |
| `openai/o4-mini` | 0.137 | $2.1179 | 7.3s | 0.160 | 0.133 | 0.147 | 0.080 | 0.000 | 0.147 | 0.000 | 0.267 | 0.219 | 0.360 | 0.000 | 0.133 | PASS | PASS | 0.184 | - |
| `mistralai/mistral-small-2603` | 0.042 | $0.1745 | 0.7s | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.500 | PASS | PASS | 0.000 | - |
| `bytedance-seed/seed-2-1-turbo` | 0.039 | $1.4469 | 38.2s | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.267 | 0.000 | 0.200 | 0.000 | 0.000 | PASS | FAIL (1 FP) | 0.053 | - |
| `qwen/qwen3-8b` | 0.000 | $0.3256 | 39.8s | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | PASS | PASS | 0.000 | - |

---

## Detailed Results

### Per-Model Detail

Full per-model profile: F1 averaged across episodes, total cost per episode at current pricing, p50 / p95 latency, JSON compliance, parse-failure rate, the distribution of extraction methods the parser had to use, and verbosity / truncation telemetry. The `Extraction methods` list shows how often each route was hit. `json_array_direct` is the cleanest; the rest are recovery paths. The verbosity row flags models that emit long `reason` fields or run out of token budget mid-response. Ordered by F1 descending so the best performers appear first.

#### `claude-haiku-4-5-20251001`

- F1 (avg across episodes): **0.920**
- Total cost / episode: **$1.0820**
- p50 / p95 latency: 24.23s / 135.48s
- JSON compliance: 1.00
- JSON mode: native (100% native, 855 calls)
- Parse failure rate: 0.0%
- Extraction methods: `json_array_direct`: 855
- Verbosity: 0/855 calls over 1024 output tokens (0.0%); 0 hit max_tokens (0.0%); 0 salvaged from truncated JSON (0.0%)
- Segment category named on 716/716 detections (100%); the rest stay uncategorized (resolver: production)

#### `qwen/qwen3.5-plus-02-15`

- F1 (avg across episodes): **0.874**
- Total cost / episode: **$1.0746**
- p50 / p95 latency: 29.18s / 52.05s
- JSON compliance: 1.00
- JSON mode: native (100% native, 855 calls)
- Parse failure rate: 0.5%
- Extraction methods: `json_array_direct`: 851, `parse_failure`: 4
- Verbosity: 809/855 calls over 1024 output tokens (94.6%); 0 hit max_tokens (0.0%); 0 salvaged from truncated JSON (0.0%)
- Segment category named on 700/700 detections (100%); the rest stay uncategorized (resolver: production)

#### `google/gemini-3.5-flash`

- F1 (avg across episodes): **0.867**
- Total cost / episode: **$3.4202**
- p50 / p95 latency: 5.59s / 11.20s
- JSON compliance: 1.00
- JSON mode: native (100% native, 855 calls)
- Parse failure rate: 0.0%
- Extraction methods: `json_array_direct`: 852, `regex_json_array`: 3
- Verbosity: 418/855 calls over 1024 output tokens (48.9%); 0 hit max_tokens (0.0%); 0 salvaged from truncated JSON (0.0%)
- Segment category named on 439/439 detections (100%); the rest stay uncategorized (resolver: production)

#### `claude-sonnet-4-6`

- F1 (avg across episodes): **0.866**
- Total cost / episode: **$3.2386**
- p50 / p95 latency: 4.11s / 83.49s
- JSON compliance: 1.00
- JSON mode: native (100% native, 855 calls)
- Parse failure rate: 0.0%
- Extraction methods: `json_array_direct`: 855
- Verbosity: 0/855 calls over 1024 output tokens (0.0%); 0 hit max_tokens (0.0%); 0 salvaged from truncated JSON (0.0%)
- Segment category named on 516/516 detections (100%); the rest stay uncategorized (resolver: production)

#### `x-ai/grok-4.3`

- F1 (avg across episodes): **0.865**
- Total cost / episode: **$1.6048**
- p50 / p95 latency: 3.73s / 10.34s
- JSON compliance: 1.00
- JSON mode: native (100% native, 855 calls)
- Parse failure rate: 0.0%
- Extraction methods: `json_array_direct`: 855
- Verbosity: 115/855 calls over 1024 output tokens (13.5%); 0 hit max_tokens (0.0%); 0 salvaged from truncated JSON (0.0%)
- Segment category named on 684/684 detections (100%); the rest stay uncategorized (resolver: production)

#### `x-ai/grok-4.5`

- F1 (avg across episodes): **0.861**
- Total cost / episode: **$3.3554**
- p50 / p95 latency: 10.86s / 52.43s
- JSON compliance: 1.00
- JSON mode: native (100% native, 855 calls)
- Parse failure rate: 0.0%
- Extraction methods: `json_array_direct`: 855
- Verbosity: 311/855 calls over 1024 output tokens (36.4%); 0 hit max_tokens (0.0%); 0 salvaged from truncated JSON (0.0%)
- Segment category named on 451/451 detections (100%); the rest stay uncategorized (resolver: production)

#### `google/gemini-3.6-flash`

- F1 (avg across episodes): **0.858**
- Total cost / episode: **$1.4431**
- p50 / p95 latency: 4.51s / 10.24s
- JSON compliance: 1.00
- JSON mode: native (100% native, 855 calls)
- Parse failure rate: 0.0%
- Extraction methods: `json_array_direct`: 854, `json_object_single_ad_truncated`: 1
- Verbosity: 302/855 calls over 1024 output tokens (35.3%); 1 hit max_tokens (0.1%); 1 salvaged from truncated JSON (0.1%)
- Segment category named on 414/415 detections (100%); the rest stay uncategorized (resolver: production)

#### `google/gemini-3.5-flash-lite`

- F1 (avg across episodes): **0.851**
- Total cost / episode: **$0.3643**
- p50 / p95 latency: 0.67s / 1.42s
- JSON compliance: 1.00
- JSON mode: native (100% native, 855 calls)
- Parse failure rate: 0.0%
- Extraction methods: `json_array_direct`: 853, `json_object_single_ad_truncated`: 1, `regex_json_array`: 1
- Verbosity: 0/855 calls over 1024 output tokens (0.0%); 0 hit max_tokens (0.0%); 1 salvaged from truncated JSON (0.1%)
- Segment category named on 664/665 detections (100%); the rest stay uncategorized (resolver: production)

#### `google/gemini-3.7-flash`

- F1 (avg across episodes): **0.848**
- Total cost / episode: **$0.6411**
- p50 / p95 latency: 4.65s / 9.87s
- JSON compliance: 1.00
- JSON mode: native (100% native, 855 calls)
- Parse failure rate: 0.0%
- Extraction methods: `json_array_direct`: 855
- Verbosity: 152/855 calls over 1024 output tokens (17.8%); 0 hit max_tokens (0.0%); 0 salvaged from truncated JSON (0.0%)
- Segment category named on 412/412 detections (100%); the rest stay uncategorized (resolver: production)

#### `x-ai/grok-4.6`

- F1 (avg across episodes): **0.847**
- Total cost / episode: **$3.7063**
- p50 / p95 latency: 13.41s / 60.93s
- JSON compliance: 1.00
- JSON mode: native (100% native, 855 calls)
- Parse failure rate: 0.0%
- Extraction methods: `json_array_direct`: 853, `json_object_ads_key`: 2
- Verbosity: 370/855 calls over 1024 output tokens (43.3%); 0 hit max_tokens (0.0%); 0 salvaged from truncated JSON (0.0%)
- Segment category named on 469/469 detections (100%); the rest stay uncategorized (resolver: production)

#### `claude-fable-5`

- F1 (avg across episodes): **0.847**
- Total cost / episode: **$10.7552**
- p50 / p95 latency: 6.61s / 68.22s
- JSON compliance: 1.00
- JSON mode: native (100% native, 855 calls)
- Parse failure rate: 0.0%
- Extraction methods: `json_array_direct`: 855
- Verbosity: 0/855 calls over 1024 output tokens (0.0%); 0 hit max_tokens (0.0%); 0 salvaged from truncated JSON (0.0%)
- Segment category named on 566/566 detections (100%); the rest stay uncategorized (resolver: production)

#### `openai/gpt-5.5`

- F1 (avg across episodes): **0.847**
- Total cost / episode: **$7.6842**
- p50 / p95 latency: 5.36s / 23.68s
- JSON compliance: 0.87
- JSON mode: native (100% native, 855 calls)
- Parse failure rate: 0.4%
- Extraction methods: `json_object_no_ads`: 489, `json_object_single_ad`: 363, `parse_failure`: 3
- Verbosity: 108/855 calls over 1024 output tokens (12.6%); 3 hit max_tokens (0.4%); 0 salvaged from truncated JSON (0.0%)
- Segment category named on 363/363 detections (100%); the rest stay uncategorized (resolver: production)

#### `mistralai/mistral-medium-3-5`

- F1 (avg across episodes): **0.842**
- Total cost / episode: **$1.8393**
- p50 / p95 latency: 1.10s / 3.29s
- JSON compliance: 1.00
- JSON mode: native (100% native, 855 calls)
- Parse failure rate: 0.0%
- Extraction methods: `json_array_direct`: 855
- Verbosity: 1/855 calls over 1024 output tokens (0.1%); 0 hit max_tokens (0.0%); 0 salvaged from truncated JSON (0.0%)
- Segment category named on 833/833 detections (100%); the rest stay uncategorized (resolver: production)

#### `openai/gpt-5.6-terra`

- F1 (avg across episodes): **0.830**
- Total cost / episode: **$2.3902**
- p50 / p95 latency: 2.10s / 6.71s
- JSON compliance: 0.86
- JSON mode: native (100% native, 855 calls)
- Parse failure rate: 0.0%
- Extraction methods: `json_object_no_ads`: 458, `json_object_single_ad`: 397
- Verbosity: 1/855 calls over 1024 output tokens (0.1%); 0 hit max_tokens (0.0%); 0 salvaged from truncated JSON (0.0%)
- Segment category named on 365/365 detections (100%); the rest stay uncategorized (resolver: production)

#### `google/gemini-3.1-flash-lite`

- F1 (avg across episodes): **0.829**
- Total cost / episode: **$0.2988**
- p50 / p95 latency: 0.85s / 2.11s
- JSON compliance: 0.94
- JSON mode: native (100% native, 855 calls)
- Parse failure rate: 0.0%
- Extraction methods: `json_array_direct`: 771, `json_object_single_ad_truncated`: 1, `regex_json_array`: 83
- Verbosity: 0/855 calls over 1024 output tokens (0.0%); 0 hit max_tokens (0.0%); 1 salvaged from truncated JSON (0.1%)
- Segment category named on 841/842 detections (100%); the rest stay uncategorized (resolver: production)

#### `claude-opus-4-8`

- F1 (avg across episodes): **0.827**
- Total cost / episode: **$5.3661**
- p50 / p95 latency: 7.76s / 68.92s
- JSON compliance: 1.00
- JSON mode: native (100% native, 855 calls)
- Parse failure rate: 0.0%
- Extraction methods: `json_array_direct`: 855
- Verbosity: 0/855 calls over 1024 output tokens (0.0%); 0 hit max_tokens (0.0%); 0 salvaged from truncated JSON (0.0%)
- Segment category named on 605/605 detections (100%); the rest stay uncategorized (resolver: production)

#### `qwen/qwen3.7-max`

- F1 (avg across episodes): **0.826**
- Total cost / episode: **$2.8109**
- p50 / p95 latency: 23.41s / 62.65s
- JSON compliance: 0.99
- JSON mode: native (100% native, 855 calls)
- Parse failure rate: 0.5%
- Extraction methods: `json_array_direct`: 833, `json_object_single_ad`: 8, `json_object_single_ad_truncated`: 10, `parse_failure`: 4
- Verbosity: 507/855 calls over 1024 output tokens (59.3%); 14 hit max_tokens (1.6%); 10 salvaged from truncated JSON (1.2%)
- Segment category named on 752/762 detections (99%); the rest stay uncategorized (resolver: production)

#### `claude-opus-4-7`

- F1 (avg across episodes): **0.822**
- Total cost / episode: **$5.3293**
- p50 / p95 latency: 4.60s / 66.02s
- JSON compliance: 1.00
- JSON mode: native (100% native, 855 calls)
- Parse failure rate: 0.0%
- Extraction methods: `json_array_direct`: 855
- Verbosity: 0/855 calls over 1024 output tokens (0.0%); 0 hit max_tokens (0.0%); 0 salvaged from truncated JSON (0.0%)
- Segment category named on 615/615 detections (100%); the rest stay uncategorized (resolver: production)

#### `openai/gpt-5.6-luna`

- F1 (avg across episodes): **0.817**
- Total cost / episode: **$0.2804**
- p50 / p95 latency: 3.46s / 11.35s
- JSON compliance: 0.83
- JSON mode: native (100% native, 855 calls)
- Parse failure rate: 1.1%
- Extraction methods: `json_object_ads_key`: 32, `json_object_no_ads`: 368, `json_object_single_ad`: 446, `parse_failure`: 9
- Verbosity: 45/855 calls over 1024 output tokens (5.3%); 9 hit max_tokens (1.1%); 0 salvaged from truncated JSON (0.0%)
- Segment category named on 390/390 detections (100%); the rest stay uncategorized (resolver: production)

#### `qwen/qwen3.6-flash`

- F1 (avg across episodes): **0.815**
- Total cost / episode: **$0.5435**
- p50 / p95 latency: 7.55s / 19.48s
- JSON compliance: 0.96
- JSON mode: native (100% native, 855 calls)
- Parse failure rate: 3.5%
- Extraction methods: `json_array_direct`: 820, `json_object_single_ad_truncated`: 5, `parse_failure`: 30
- Verbosity: 568/855 calls over 1024 output tokens (66.4%); 35 hit max_tokens (4.1%); 5 salvaged from truncated JSON (0.6%)
- Segment category named on 415/420 detections (99%); the rest stay uncategorized (resolver: production)

#### `google/gemini-3.1-pro-preview`

- F1 (avg across episodes): **0.815**
- Total cost / episode: **$4.9160**
- p50 / p95 latency: 8.35s / 20.73s
- JSON compliance: 1.00
- JSON mode: native (100% native, 855 calls)
- Parse failure rate: 0.0%
- Extraction methods: `json_array_direct`: 855
- Verbosity: 397/855 calls over 1024 output tokens (46.4%); 0 hit max_tokens (0.0%); 0 salvaged from truncated JSON (0.0%)
- Segment category named on 463/463 detections (100%); the rest stay uncategorized (resolver: production)

#### `stealth/ox-alpha`

- F1 (avg across episodes): **0.810**
- Total cost / episode: **$0.0000**
- p50 / p95 latency: 66.56s / 190.15s
- JSON compliance: 0.94
- JSON mode: native (100% native, 855 calls)
- Parse failure rate: 5.7%
- Extraction methods: `json_array_direct`: 793, `json_object_no_ads`: 1, `json_object_single_ad_truncated`: 8, `parse_failure`: 49, `regex_json_array`: 4
- Verbosity: 397/855 calls over 1024 output tokens (46.4%); 55 hit max_tokens (6.4%); 8 salvaged from truncated JSON (0.9%)
- Segment category named on 356/363 detections (98%); the rest stay uncategorized (resolver: production)

#### `claude-opus-5`

- F1 (avg across episodes): **0.806**
- Total cost / episode: **$5.3761**
- p50 / p95 latency: 3.67s / 64.55s
- JSON compliance: 1.00
- JSON mode: native (100% native, 855 calls)
- Parse failure rate: 0.0%
- Extraction methods: `json_array_direct`: 855
- Verbosity: 0/855 calls over 1024 output tokens (0.0%); 0 hit max_tokens (0.0%); 0 salvaged from truncated JSON (0.0%)
- Segment category named on 547/547 detections (100%); the rest stay uncategorized (resolver: production)

#### `claude-sonnet-5`

- F1 (avg across episodes): **0.804**
- Total cost / episode: **$2.1452**
- p50 / p95 latency: 8.48s / 66.02s
- JSON compliance: 1.00
- JSON mode: native (100% native, 855 calls)
- Parse failure rate: 0.0%
- Extraction methods: `json_array_direct`: 854, `json_object_single_ad`: 1
- Verbosity: 0/855 calls over 1024 output tokens (0.0%); 0 hit max_tokens (0.0%); 0 salvaged from truncated JSON (0.0%)
- Segment category named on 612/612 detections (100%); the rest stay uncategorized (resolver: production)

#### `openai/gpt-5.6-sol`

- F1 (avg across episodes): **0.799**
- Total cost / episode: **$2.4303**
- p50 / p95 latency: 3.72s / 11.86s
- JSON compliance: 0.84
- JSON mode: native (100% native, 855 calls)
- Parse failure rate: 0.0%
- Extraction methods: `json_object_ads_key`: 53, `json_object_no_ads`: 368, `json_object_segments_key`: 3, `json_object_single_ad`: 431
- Verbosity: 9/855 calls over 1024 output tokens (1.1%); 0 hit max_tokens (0.0%); 0 salvaged from truncated JSON (0.0%)
- Segment category named on 411/412 detections (100%); the rest stay uncategorized (resolver: production)

#### `mistralai/mistral-medium-3.1`

- F1 (avg across episodes): **0.793**
- Total cost / episode: **$0.4847**
- p50 / p95 latency: 0.74s / 7.65s
- JSON compliance: 1.00
- JSON mode: native (100% native, 855 calls)
- Parse failure rate: 0.0%
- Extraction methods: `json_array_direct`: 855
- Verbosity: 0/855 calls over 1024 output tokens (0.0%); 0 hit max_tokens (0.0%); 0 salvaged from truncated JSON (0.0%)
- Segment category named on 705/705 detections (100%); the rest stay uncategorized (resolver: production)

#### `qwen/qwen3.7-flash`

- F1 (avg across episodes): **0.774**
- Total cost / episode: **$0.0725**
- p50 / p95 latency: 10.14s / 27.58s
- JSON compliance: 0.96
- JSON mode: native (100% native, 855 calls)
- Parse failure rate: 3.9%
- Extraction methods: `json_array_direct`: 810, `json_object_single_ad_truncated`: 12, `parse_failure`: 33
- Verbosity: 559/855 calls over 1024 output tokens (65.4%); 44 hit max_tokens (5.1%); 12 salvaged from truncated JSON (1.4%)
- Segment category named on 377/388 detections (97%); the rest stay uncategorized (resolver: production)

#### `moonshotai/kimi-k3`

- F1 (avg across episodes): **0.770**
- Total cost / episode: **$5.9242**
- p50 / p95 latency: 13.78s / 96.34s
- JSON compliance: 0.81
- JSON mode: native (100% native, 855 calls)
- Parse failure rate: 5.8%
- Extraction methods: `json_array_direct`: 361, `json_object_ads_key`: 181, `json_object_no_ads`: 27, `json_object_single_ad`: 6, `json_object_single_ad_truncated`: 122, `parse_failure`: 50, `regex_json_array`: 108
- Verbosity: 276/855 calls over 1024 output tokens (32.3%); 53 hit max_tokens (6.2%); 122 salvaged from truncated JSON (14.3%)
- Segment category named on 264/386 detections (68%); the rest stay uncategorized (resolver: production)

#### `google/gemini-2.5-flash`

- F1 (avg across episodes): **0.761**
- Total cost / episode: **$0.3796**
- p50 / p95 latency: 0.83s / 2.21s
- JSON compliance: 1.00
- JSON mode: native (100% native, 855 calls)
- Parse failure rate: 0.0%
- Extraction methods: `json_array_direct`: 852, `json_object_single_ad_truncated`: 3
- Verbosity: 0/855 calls over 1024 output tokens (0.0%); 0 hit max_tokens (0.0%); 3 salvaged from truncated JSON (0.4%)
- Segment category named on 973/975 detections (100%); the rest stay uncategorized (resolver: production)

#### `google/gemini-2.5-pro`

- F1 (avg across episodes): **0.752**
- Total cost / episode: **$4.1601**
- p50 / p95 latency: 14.22s / 34.09s
- JSON compliance: 0.96
- JSON mode: native (100% native, 855 calls)
- Parse failure rate: 1.4%
- Extraction methods: `json_array_direct`: 805, `json_object_single_ad_truncated`: 9, `parse_failure`: 12, `regex_json_array`: 29
- Verbosity: 637/855 calls over 1024 output tokens (74.5%); 9 hit max_tokens (1.1%); 9 salvaged from truncated JSON (1.1%)
- Segment category named on 504/512 detections (98%); the rest stay uncategorized (resolver: production)

#### `deepseek/deepseek-v4-flash`

- F1 (avg across episodes): **0.749**
- Total cost / episode: **$0.0715**
- p50 / p95 latency: 6.27s / 40.26s
- JSON compliance: 0.82
- JSON mode: native (100% native, 855 calls)
- Parse failure rate: 5.5%
- Extraction methods: `json_array_direct`: 93, `json_object_ads_key`: 175, `json_object_no_ads`: 266, `json_object_segments_key`: 6, `json_object_single_ad`: 268, `parse_failure`: 47
- Verbosity: 299/855 calls over 1024 output tokens (35.0%); 46 hit max_tokens (5.4%); 0 salvaged from truncated JSON (0.0%)
- Segment category named on 362/362 detections (100%); the rest stay uncategorized (resolver: production)

#### `openai/gpt-oss-120b`

- F1 (avg across episodes): **0.739**
- Total cost / episode: **$0.0634**
- p50 / p95 latency: 6.54s / 44.55s
- JSON compliance: 0.88
- JSON mode: native (100% native, 855 calls)
- Parse failure rate: 1.1%
- Extraction methods: `json_array_direct`: 28, `json_object_ads_key`: 181, `json_object_no_ads`: 429, `json_object_single_ad`: 202, `json_object_single_ad_truncated`: 1, `parse_failure`: 9, `regex_json_array`: 5
- Verbosity: 224/855 calls over 1024 output tokens (26.2%); 3 hit max_tokens (0.4%); 1 salvaged from truncated JSON (0.1%)
- Segment category named on 700/701 detections (100%); the rest stay uncategorized (resolver: production)

#### `google/gemma-4-31b-it`

- F1 (avg across episodes): **0.699**
- Total cost / episode: **$0.1181**
- p50 / p95 latency: 2.45s / 11.72s
- JSON compliance: 0.87
- JSON mode: native (100% native, 855 calls)
- Parse failure rate: 0.0%
- Extraction methods: `json_array_direct`: 14, `json_object_ads_key`: 378, `json_object_no_ads`: 283, `json_object_single_ad`: 180
- Verbosity: 4/855 calls over 1024 output tokens (0.5%); 0 hit max_tokens (0.0%); 0 salvaged from truncated JSON (0.0%)
- Segment category named on 569/569 detections (100%); the rest stay uncategorized (resolver: production)

#### `deepseek/deepseek-v4-pro`

- F1 (avg across episodes): **0.695**
- Total cost / episode: **$0.6106**
- p50 / p95 latency: 16.17s / 68.15s
- JSON compliance: 0.87
- JSON mode: native (100% native, 855 calls)
- Parse failure rate: 5.3%
- Extraction methods: `json_array_direct`: 434, `json_object_ads_key`: 10, `json_object_no_ads`: 39, `json_object_segments_key`: 205, `json_object_single_ad`: 118, `json_object_single_ad_truncated`: 3, `parse_failure`: 45, `regex_json_array`: 1
- Verbosity: 376/855 calls over 1024 output tokens (44.0%); 39 hit max_tokens (4.6%); 3 salvaged from truncated JSON (0.4%)
- Segment category named on 282/288 detections (98%); the rest stay uncategorized (resolver: production)

#### `meta/muse-glimmer-30b`

- F1 (avg across episodes): **0.694**
- Total cost / episode: **$0.6965**
- p50 / p95 latency: 10.79s / 66.87s
- JSON compliance: 0.89
- JSON mode: native (100% native, 855 calls)
- Parse failure rate: 2.8%
- Extraction methods: `json_array_direct`: 548, `json_object_single_ad`: 204, `json_object_single_ad_truncated`: 79, `parse_failure`: 24
- Verbosity: 353/855 calls over 1024 output tokens (41.3%); 29 hit max_tokens (3.4%); 79 salvaged from truncated JSON (9.2%)
- Segment category named on 318/397 detections (80%); the rest stay uncategorized (resolver: production)

#### `google/gemini-2.5-flash-lite`

- F1 (avg across episodes): **0.689**
- Total cost / episode: **$0.1132**
- p50 / p95 latency: 0.84s / 1.75s
- JSON compliance: 0.97
- JSON mode: native (100% native, 855 calls)
- Parse failure rate: 2.0%
- Extraction methods: `json_array_direct`: 791, `json_object_single_ad_truncated`: 47, `parse_failure`: 17
- Verbosity: 6/855 calls over 1024 output tokens (0.7%); 0 hit max_tokens (0.0%); 47 salvaged from truncated JSON (5.5%)
- Segment category named on 945/987 detections (96%); the rest stay uncategorized (resolver: production)

#### `openai/gpt-5.4`

- F1 (avg across episodes): **0.685**
- Total cost / episode: **$2.7907**
- p50 / p95 latency: 1.43s / 2.57s
- JSON compliance: 0.80
- JSON mode: native (100% native, 855 calls)
- Parse failure rate: 0.0%
- Extraction methods: `json_object_no_ads`: 274, `json_object_single_ad`: 581
- Verbosity: 0/855 calls over 1024 output tokens (0.0%); 0 hit max_tokens (0.0%); 0 salvaged from truncated JSON (0.0%)
- Segment category named on 470/470 detections (100%); the rest stay uncategorized (resolver: production)

#### `deepseek/deepseek-v3.2`

- F1 (avg across episodes): **0.676**
- Total cost / episode: **$0.2914**
- p50 / p95 latency: 1.74s / 10.84s
- JSON compliance: 1.00
- JSON mode: native (100% native, 855 calls)
- Parse failure rate: 0.0%
- Extraction methods: `json_array_direct`: 853, `json_object_ads_key`: 2
- Verbosity: 0/855 calls over 1024 output tokens (0.0%); 0 hit max_tokens (0.0%); 0 salvaged from truncated JSON (0.0%)
- Segment category named on 432/432 detections (100%); the rest stay uncategorized (resolver: production)

#### `meta/muse-spark-1.1`

- F1 (avg across episodes): **0.668**
- Total cost / episode: **$2.2536**
- p50 / p95 latency: 4.31s / 13.59s
- JSON compliance: 0.93
- JSON mode: native (100% native, 855 calls)
- Parse failure rate: 0.5%
- Extraction methods: `json_object_ads_key`: 28, `json_object_no_ads`: 643, `json_object_segments_key`: 3, `json_object_single_ad`: 175, `json_object_single_ad_truncated`: 2, `parse_failure`: 4
- Verbosity: 432/855 calls over 1024 output tokens (50.5%); 6 hit max_tokens (0.7%); 2 salvaged from truncated JSON (0.2%)
- Segment category named on 235/236 detections (100%); the rest stay uncategorized (resolver: production)

#### `qwen/qwen3.6-plus`

- F1 (avg across episodes): **0.661**
- Total cost / episode: **$1.1534**
- p50 / p95 latency: 36.91s / 70.77s
- JSON compliance: 0.90
- JSON mode: native (100% native, 855 calls)
- Parse failure rate: 10.1%
- Extraction methods: `json_array_direct`: 748, `json_object_single_ad_truncated`: 21, `parse_failure`: 86
- Verbosity: 789/855 calls over 1024 output tokens (92.3%); 107 hit max_tokens (12.5%); 21 salvaged from truncated JSON (2.5%)
- Segment category named on 444/464 detections (96%); the rest stay uncategorized (resolver: production)

#### `z-ai/glm-5.2`

- F1 (avg across episodes): **0.657**
- Total cost / episode: **$1.3083**
- p50 / p95 latency: 3.55s / 24.22s
- JSON compliance: 0.73
- JSON mode: native (100% native, 855 calls)
- Parse failure rate: 3.4%
- Extraction methods: `json_array_direct`: 5, `json_object_ad_key`: 4, `json_object_ads_key`: 95, `json_object_no_ads`: 86, `json_object_single_ad`: 635, `json_object_single_ad_truncated`: 1, `parse_failure`: 29
- Verbosity: 103/855 calls over 1024 output tokens (12.0%); 6 hit max_tokens (0.7%); 1 salvaged from truncated JSON (0.1%)
- Segment category named on 522/529 detections (99%); the rest stay uncategorized (resolver: production)

#### `openai/gpt-5.4-mini`

- F1 (avg across episodes): **0.640**
- Total cost / episode: **$0.8470**
- p50 / p95 latency: 1.13s / 2.45s
- JSON compliance: 0.78
- JSON mode: native (100% native, 855 calls)
- Parse failure rate: 0.0%
- Extraction methods: `json_object_no_ads`: 218, `json_object_single_ad`: 637
- Verbosity: 0/855 calls over 1024 output tokens (0.0%); 0 hit max_tokens (0.0%); 0 salvaged from truncated JSON (0.0%)
- Segment category named on 599/599 detections (100%); the rest stay uncategorized (resolver: production)

#### `deepseek/deepseek-r1`

- F1 (avg across episodes): **0.638**
- Total cost / episode: **$1.4743**
- p50 / p95 latency: 35.66s / 195.41s
- JSON compliance: 0.84
- JSON mode: native (100% native, 855 calls)
- Parse failure rate: 9.9%
- Extraction methods: `json_array_direct`: 494, `json_object_ads_key`: 182, `json_object_segments_key`: 1, `json_object_single_ad`: 90, `json_object_single_ad_truncated`: 3, `parse_failure`: 85
- Verbosity: 387/855 calls over 1024 output tokens (45.3%); 88 hit max_tokens (10.3%); 3 salvaged from truncated JSON (0.4%)
- Segment category named on 654/657 detections (100%); the rest stay uncategorized (resolver: production)

#### `nvidia/nemotron-3-super-120b-a12b`

- F1 (avg across episodes): **0.635**
- Total cost / episode: **$0.2194**
- p50 / p95 latency: 22.33s / 248.36s
- JSON compliance: 0.79
- JSON mode: native (100% native, 855 calls)
- Parse failure rate: 14.9%
- Extraction methods: `json_array_direct`: 520, `json_object_ads_key`: 1, `json_object_no_ads`: 40, `json_object_single_ad`: 160, `json_object_single_ad_truncated`: 6, `parse_failure`: 127, `regex_json_array`: 1
- Verbosity: 486/855 calls over 1024 output tokens (56.8%); 128 hit max_tokens (15.0%); 6 salvaged from truncated JSON (0.7%)
- Segment category named on 284/290 detections (98%); the rest stay uncategorized (resolver: production)

#### `minimax/minimax-m3`

- F1 (avg across episodes): **0.633**
- Total cost / episode: **$0.3575**
- p50 / p95 latency: 1.68s / 6.67s
- JSON compliance: 0.88
- JSON mode: native (100% native, 855 calls)
- Parse failure rate: 0.5%
- Extraction methods: `json_array_direct`: 340, `json_object_ads_key`: 4, `json_object_no_ads`: 262, `json_object_single_ad`: 48, `markdown_code_block`: 188, `parse_failure`: 4, `regex_json_array`: 9
- Verbosity: 18/855 calls over 1024 output tokens (2.1%); 2 hit max_tokens (0.2%); 0 salvaged from truncated JSON (0.0%)
- Segment category named on 470/471 detections (100%); the rest stay uncategorized (resolver: production)

#### `qwen/qwen3.5-27b`

- F1 (avg across episodes): **0.612**
- Total cost / episode: **$0.8941**
- p50 / p95 latency: 37.62s / 126.80s
- JSON compliance: 0.70
- JSON mode: native (100% native, 855 calls)
- Parse failure rate: 29.6%
- Extraction methods: `json_array_direct`: 460, `json_object_no_ads`: 124, `json_object_single_ad`: 17, `json_object_single_ad_truncated`: 1, `parse_failure`: 253
- Verbosity: 574/855 calls over 1024 output tokens (67.1%); 132 hit max_tokens (15.4%); 1 salvaged from truncated JSON (0.1%)
- Segment category named on 272/273 detections (100%); the rest stay uncategorized (resolver: production)

#### `google/gemma-4-26b-a4b-it`

- F1 (avg across episodes): **0.612**
- Total cost / episode: **$0.0825**
- p50 / p95 latency: 1.40s / 6.52s
- JSON compliance: 0.84
- JSON mode: native (100% native, 855 calls)
- Parse failure rate: 0.0%
- Extraction methods: `json_object_ads_key`: 73, `json_object_no_ads`: 372, `json_object_single_ad`: 410
- Verbosity: 0/855 calls over 1024 output tokens (0.0%); 0 hit max_tokens (0.0%); 0 salvaged from truncated JSON (0.0%)
- Segment category named on 495/495 detections (100%); the rest stay uncategorized (resolver: production)

#### `mistralai/mistral-large-2512`

- F1 (avg across episodes): **0.612**
- Total cost / episode: **$0.6184**
- p50 / p95 latency: 3.28s / 7.58s
- JSON compliance: 1.00
- JSON mode: native (100% native, 855 calls)
- Parse failure rate: 0.0%
- Extraction methods: `json_array_direct`: 855
- Verbosity: 0/855 calls over 1024 output tokens (0.0%); 0 hit max_tokens (0.0%); 0 salvaged from truncated JSON (0.0%)
- Segment category named on 1280/1280 detections (100%); the rest stay uncategorized (resolver: production)

#### `deepseek/deepseek-v4-flash-0731`

- F1 (avg across episodes): **0.607**
- Total cost / episode: **$0.1371**
- p50 / p95 latency: 13.20s / 61.85s
- JSON compliance: 0.73
- JSON mode: native (100% native, 855 calls)
- Parse failure rate: 18.5%
- Extraction methods: `json_array_direct`: 224, `json_object_ads_key`: 9, `json_object_no_ads`: 222, `json_object_single_ad`: 238, `json_object_single_ad_truncated`: 3, `parse_failure`: 158, `regex_json_array`: 1
- Verbosity: 394/855 calls over 1024 output tokens (46.1%); 155 hit max_tokens (18.1%); 3 salvaged from truncated JSON (0.4%)
- Segment category named on 215/218 detections (99%); the rest stay uncategorized (resolver: production)

#### `deepseek/deepseek-r1-0528`

- F1 (avg across episodes): **0.606**
- Total cost / episode: **$1.1645**
- p50 / p95 latency: 29.36s / 208.22s
- JSON compliance: 0.84
- JSON mode: native (100% native, 855 calls)
- Parse failure rate: 11.2%
- Extraction methods: `json_array_direct`: 535, `json_object_ads_key`: 154, `json_object_no_ads`: 7, `json_object_segments_key`: 4, `json_object_single_ad`: 55, `json_object_single_ad_truncated`: 4, `parse_failure`: 96
- Verbosity: 391/855 calls over 1024 output tokens (45.7%); 100 hit max_tokens (11.7%); 4 salvaged from truncated JSON (0.5%)
- Segment category named on 644/650 detections (99%); the rest stay uncategorized (resolver: production)

#### `openai/gpt-oss-20b`

- F1 (avg across episodes): **0.572**
- Total cost / episode: **$0.0620**
- p50 / p95 latency: 9.18s / 51.77s
- JSON compliance: 0.80
- JSON mode: native (100% native, 855 calls)
- Parse failure rate: 11.6%
- Extraction methods: `json_array_direct`: 26, `json_object_ads_key`: 121, `json_object_no_ads`: 430, `json_object_single_ad`: 176, `json_object_single_ad_truncated`: 3, `parse_failure`: 99
- Verbosity: 349/855 calls over 1024 output tokens (40.8%); 94 hit max_tokens (11.0%); 3 salvaged from truncated JSON (0.4%)
- Segment category named on 486/490 detections (99%); the rest stay uncategorized (resolver: production)

#### `meta-llama/llama-4-maverick`

- F1 (avg across episodes): **0.561**
- Total cost / episode: **$0.2206**
- p50 / p95 latency: 1.12s / 3.59s
- JSON compliance: 0.79
- JSON mode: native (100% native, 855 calls)
- Parse failure rate: 0.0%
- Extraction methods: `json_object_no_ads`: 259, `json_object_single_ad`: 596
- Verbosity: 1/855 calls over 1024 output tokens (0.1%); 0 hit max_tokens (0.0%); 0 salvaged from truncated JSON (0.0%)
- Segment category named on 476/476 detections (100%); the rest stay uncategorized (resolver: production)

#### `qwen/qwen3.7-plus`

- F1 (avg across episodes): **0.551**
- Total cost / episode: **$0.7238**
- p50 / p95 latency: 23.68s / 62.94s
- JSON compliance: 0.80
- JSON mode: native (100% native, 855 calls)
- Parse failure rate: 2.6%
- Extraction methods: `json_array_direct`: 242, `json_object_ads_key`: 3, `json_object_no_ads`: 86, `json_object_single_ad`: 500, `json_object_single_ad_truncated`: 2, `parse_failure`: 22
- Verbosity: 678/855 calls over 1024 output tokens (79.3%); 23 hit max_tokens (2.7%); 2 salvaged from truncated JSON (0.2%)
- Segment category named on 859/863 detections (100%); the rest stay uncategorized (resolver: production)

#### `qwen/qwen3.8-27b`

- F1 (avg across episodes): **0.550**
- Total cost / episode: **$1.3947**
- p50 / p95 latency: 70.26s / 337.40s
- JSON compliance: 0.69
- JSON mode: native (100% native, 855 calls)
- Parse failure rate: 24.8%
- Extraction methods: `json_object_no_ads`: 470, `json_object_single_ad`: 164, `json_object_single_ad_truncated`: 9, `parse_failure`: 212
- Verbosity: 420/855 calls over 1024 output tokens (49.1%); 220 hit max_tokens (25.7%); 9 salvaged from truncated JSON (1.1%)
- Segment category named on 164/172 detections (95%); the rest stay uncategorized (resolver: production)

#### `mistralai/codestral-2508`

- F1 (avg across episodes): **0.536**
- Total cost / episode: **$0.3585**
- p50 / p95 latency: 0.88s / 2.54s
- JSON compliance: 1.00
- JSON mode: native (100% native, 855 calls)
- Parse failure rate: 0.0%
- Extraction methods: `json_array_direct`: 855
- Verbosity: 0/855 calls over 1024 output tokens (0.0%); 0 hit max_tokens (0.0%); 0 salvaged from truncated JSON (0.0%)
- Segment category named on 820/820 detections (100%); the rest stay uncategorized (resolver: production)

#### `qwen/qwen3-235b-a22b-2507`

- F1 (avg across episodes): **0.532**
- Total cost / episode: **$0.1078**
- p50 / p95 latency: 2.51s / 7.43s
- JSON compliance: 0.84
- JSON mode: native (100% native, 855 calls)
- Parse failure rate: 0.0%
- Extraction methods: `json_array_direct`: 227, `json_object_no_ads`: 169, `json_object_single_ad`: 459
- Verbosity: 0/855 calls over 1024 output tokens (0.0%); 0 hit max_tokens (0.0%); 0 salvaged from truncated JSON (0.0%)
- Segment category named on 633/633 detections (100%); the rest stay uncategorized (resolver: production)

#### `meta-llama/llama-3.3-70b-instruct`

- F1 (avg across episodes): **0.514**
- Total cost / episode: **$0.1107**
- p50 / p95 latency: 1.29s / 8.60s
- JSON compliance: 0.88
- JSON mode: native (100% native, 855 calls)
- Parse failure rate: 0.0%
- Extraction methods: `json_array_direct`: 147, `json_object_no_ads`: 380, `json_object_single_ad`: 322, `json_object_single_ad_truncated`: 4, `regex_json_array`: 2
- Verbosity: 0/855 calls over 1024 output tokens (0.0%); 0 hit max_tokens (0.0%); 4 salvaged from truncated JSON (0.5%)
- Segment category named on 466/470 detections (99%); the rest stay uncategorized (resolver: production)

#### `deepseek/deepseek-v4-pro-0813`

- F1 (avg across episodes): **0.507**
- Total cost / episode: **$2.4576**
- p50 / p95 latency: 26.59s / 74.56s
- JSON compliance: 0.73
- JSON mode: native (100% native, 855 calls)
- Parse failure rate: 26.5%
- Extraction methods: `json_array_direct`: 625, `json_object_single_ad_truncated`: 3, `parse_failure`: 227
- Verbosity: 522/855 calls over 1024 output tokens (61.1%); 225 hit max_tokens (26.3%); 3 salvaged from truncated JSON (0.4%)
- Segment category named on 161/164 detections (98%); the rest stay uncategorized (resolver: production)

#### `xiaomi/mimo-v2.5-pro`

- F1 (avg across episodes): **0.506**
- Total cost / episode: **$0.4954**
- p50 / p95 latency: 2.19s / 6.41s
- JSON compliance: 0.90
- JSON mode: native (100% native, 855 calls)
- Parse failure rate: 2.6%
- Extraction methods: `json_object_ads_detected_key`: 1, `json_object_ads_key`: 133, `json_object_advertisement_segments_key`: 7, `json_object_no_ads`: 552, `json_object_segments_key`: 11, `json_object_single_ad`: 119, `json_object_single_ad_truncated`: 5, `parse_failure`: 22, `regex_json_array`: 5
- Verbosity: 9/855 calls over 1024 output tokens (1.1%); 1 hit max_tokens (0.1%); 5 salvaged from truncated JSON (0.6%)
- Segment category named on 267/274 detections (97%); the rest stay uncategorized (resolver: production)

#### `tencent/hy3`

- F1 (avg across episodes): **0.503**
- Total cost / episode: **$0.3876**
- p50 / p95 latency: 22.27s / 59.98s
- JSON compliance: 0.62
- JSON mode: native (100% native, 855 calls)
- Parse failure rate: 27.6%
- Extraction methods: `bracket_fallback`: 12, `json_array_direct`: 144, `json_object_ads_key`: 10, `json_object_no_ads`: 198, `json_object_single_ad`: 249, `json_object_single_ad_truncated`: 6, `parse_failure`: 236
- Verbosity: 786/855 calls over 1024 output tokens (91.9%); 239 hit max_tokens (28.0%); 6 salvaged from truncated JSON (0.7%)
- Segment category named on 165/171 detections (96%); the rest stay uncategorized (resolver: production)

#### `openai/o3`

- F1 (avg across episodes): **0.499**
- Total cost / episode: **$3.3100**
- p50 / p95 latency: 6.66s / 20.44s
- JSON compliance: 0.93
- JSON mode: native (100% native, 855 calls)
- Parse failure rate: 0.7%
- Extraction methods: `json_object_ads_key`: 87, `json_object_no_ads`: 613, `json_object_segments_key`: 30, `json_object_single_ad`: 119, `parse_failure`: 6
- Verbosity: 280/855 calls over 1024 output tokens (32.7%); 0 hit max_tokens (0.0%); 0 salvaged from truncated JSON (0.0%)
- Segment category named on 140/140 detections (100%); the rest stay uncategorized (resolver: production)

#### `xiaomi/mimo-v2.5`

- F1 (avg across episodes): **0.487**
- Total cost / episode: **$0.1692**
- p50 / p95 latency: 4.16s / 21.43s
- JSON compliance: 0.73
- JSON mode: native (100% native, 855 calls)
- Parse failure rate: 3.0%
- Extraction methods: `json_object_ads_detected_key`: 1, `json_object_ads_key`: 54, `json_object_no_ads`: 160, `json_object_segments_key`: 2, `json_object_single_ad`: 492, `json_object_single_ad_truncated`: 48, `markdown_code_block`: 18, `parse_failure`: 26, `regex_json_array`: 54
- Verbosity: 36/855 calls over 1024 output tokens (4.2%); 20 hit max_tokens (2.3%); 48 salvaged from truncated JSON (5.6%)
- Segment category named on 533/587 detections (91%); the rest stay uncategorized (resolver: production)

#### `nvidia/nemotron-3-ultra-550b-a55b`

- F1 (avg across episodes): **0.476**
- Total cost / episode: **$0.7782**
- p50 / p95 latency: 1.24s / 5.94s
- JSON compliance: 0.89
- JSON mode: native (100% native, 855 calls)
- Parse failure rate: 0.4%
- Extraction methods: `json_array_direct`: 448, `json_object_ads_key`: 59, `json_object_no_ads`: 56, `json_object_segments_key`: 37, `json_object_single_ad`: 252, `parse_failure`: 3
- Verbosity: 25/855 calls over 1024 output tokens (2.9%); 1 hit max_tokens (0.1%); 0 salvaged from truncated JSON (0.0%)
- Segment category named on 229/229 detections (100%); the rest stay uncategorized (resolver: production)

#### `meta-llama/llama-4-scout`

- F1 (avg across episodes): **0.469**
- Total cost / episode: **$0.1111**
- p50 / p95 latency: 0.93s / 4.49s
- JSON compliance: 0.83
- JSON mode: native (100% native, 855 calls)
- Parse failure rate: 3.2%
- Extraction methods: `bracket_fallback`: 31, `json_array_direct`: 56, `json_object_ads_key`: 349, `json_object_no_ads`: 262, `json_object_segments_key`: 10, `json_object_single_ad`: 110, `json_object_single_ad_truncated`: 3, `parse_failure`: 27, `regex_json_array`: 7
- Verbosity: 9/855 calls over 1024 output tokens (1.1%); 0 hit max_tokens (0.0%); 3 salvaged from truncated JSON (0.4%)
- Segment category named on 473/479 detections (99%); the rest stay uncategorized (resolver: production)

#### `cohere/command-a`

- F1 (avg across episodes): **0.464**
- Total cost / episode: **$2.9415**
- p50 / p95 latency: 1.72s / 2.92s
- JSON compliance: 0.71
- JSON mode: native (100% native, 855 calls)
- Parse failure rate: 0.0%
- Extraction methods: `json_object_no_ads`: 21, `json_object_single_ad`: 834
- Verbosity: 0/855 calls over 1024 output tokens (0.0%); 0 hit max_tokens (0.0%); 0 salvaged from truncated JSON (0.0%)
- Segment category named on 653/655 detections (100%); the rest stay uncategorized (resolver: production)

#### `meituan/longcat-2.0`

- F1 (avg across episodes): **0.448**
- Total cost / episode: **$0.5573**
- p50 / p95 latency: 10.53s / 77.08s
- JSON compliance: 0.42
- JSON mode: native (100% native, 855 calls)
- Parse failure rate: 50.2%
- Extraction methods: `json_object_ads_key`: 414, `json_object_no_ads`: 8, `json_object_single_ad`: 1, `json_object_single_ad_truncated`: 2, `parse_failure`: 429, `regex_json_array`: 1
- Verbosity: 287/855 calls over 1024 output tokens (33.6%); 92 hit max_tokens (10.8%); 2 salvaged from truncated JSON (0.2%)
- Segment category named on 263/265 detections (99%); the rest stay uncategorized (resolver: production)

#### `qwen/qwen3.8-2.4t-a95b`

- F1 (avg across episodes): **0.446**
- Total cost / episode: **$4.2877**
- p50 / p95 latency: 11.93s / 67.44s
- JSON compliance: 0.62
- JSON mode: native (100% native, 855 calls)
- Parse failure rate: 28.2%
- Extraction methods: `json_object_ads_key`: 7, `json_object_no_ads`: 331, `json_object_segments_key`: 1, `json_object_single_ad`: 273, `json_object_single_ad_truncated`: 2, `parse_failure`: 241
- Verbosity: 467/855 calls over 1024 output tokens (54.6%); 240 hit max_tokens (28.1%); 2 salvaged from truncated JSON (0.2%)
- Segment category named on 163/165 detections (99%); the rest stay uncategorized (resolver: production)

#### `deepseek/deepseek-r1-distill-llama-70b`

- F1 (avg across episodes): **0.433**
- Total cost / episode: **$0.6913**
- p50 / p95 latency: 27.21s / 97.56s
- JSON compliance: 0.39
- JSON mode: prompt-inject (0% native, 855 calls)
- Parse failure rate: 15.8%
- Extraction methods: `json_array_direct`: 36, `json_object_single_ad_truncated`: 1, `markdown_code_block`: 110, `parse_failure`: 135, `regex_json_array`: 573
- Verbosity: 241/855 calls over 1024 output tokens (28.2%); 57 hit max_tokens (6.7%); 1 salvaged from truncated JSON (0.1%)
- Segment category named on 360/385 detections (94%); the rest stay uncategorized (resolver: production)

#### `moonshotai/kimi-k2.6`

- F1 (avg across episodes): **0.423**
- Total cost / episode: **$1.7617**
- p50 / p95 latency: 55.69s / 164.28s
- JSON compliance: 0.63
- JSON mode: native (100% native, 855 calls)
- Parse failure rate: 29.7%
- Extraction methods: `json_array_direct`: 270, `json_object_ads_key`: 57, `json_object_no_ads`: 77, `json_object_segments_key`: 3, `json_object_single_ad`: 188, `json_object_single_ad_truncated`: 6, `parse_failure`: 254
- Verbosity: 826/855 calls over 1024 output tokens (96.6%); 260 hit max_tokens (30.4%); 6 salvaged from truncated JSON (0.7%)
- Segment category named on 194/202 detections (96%); the rest stay uncategorized (resolver: production)

#### `stepfun/step-3.7-flash`

- F1 (avg across episodes): **0.409**
- Total cost / episode: **$0.4302**
- p50 / p95 latency: 13.87s / 35.00s
- JSON compliance: 0.56
- JSON mode: native (100% native, 725 calls)
- Parse failure rate: 32.3%
- Extraction methods: `json_object_ads_key`: 380, `json_object_no_ads`: 4, `json_object_single_ad`: 103, `json_object_single_ad_truncated`: 4, `parse_failure`: 234
- Verbosity: 388/725 calls over 1024 output tokens (53.5%); 238 hit max_tokens (32.8%); 4 salvaged from truncated JSON (0.6%)
- Segment category named on 89/92 detections (97%); the rest stay uncategorized (resolver: production)

#### `microsoft/phi-4`

- F1 (avg across episodes): **0.408**
- Total cost / episode: **$0.0792**
- p50 / p95 latency: 0.49s / 5.50s
- JSON compliance: 0.98
- JSON mode: native (100% native, 855 calls)
- Parse failure rate: 1.5%
- Extraction methods: `json_array_direct`: 828, `json_object_no_ads`: 4, `json_object_single_ad`: 3, `json_object_single_ad_truncated`: 7, `parse_failure`: 13
- Verbosity: 21/855 calls over 1024 output tokens (2.5%); 20 hit max_tokens (2.3%); 7 salvaged from truncated JSON (0.8%)
- Segment category named on 292/298 detections (98%); the rest stay uncategorized (resolver: production)

#### `thinkingmachines/inkling-small`

- F1 (avg across episodes): **0.395**
- Total cost / episode: **$1.0333**
- p50 / p95 latency: 18.51s / 30.60s
- JSON compliance: 0.58
- JSON mode: native (100% native, 855 calls)
- Parse failure rate: 29.2%
- Extraction methods: `json_object_no_ads`: 281, `json_object_single_ad`: 284, `json_object_single_ad_truncated`: 4, `parse_failure`: 250, `regex_json_array`: 36
- Verbosity: 715/855 calls over 1024 output tokens (83.6%); 290 hit max_tokens (33.9%); 4 salvaged from truncated JSON (0.5%)
- Segment category named on 251/260 detections (97%); the rest stay uncategorized (resolver: production)

#### `cohere/command-r-plus-08-2024`

- F1 (avg across episodes): **0.389**
- Total cost / episode: **$2.8505**
- p50 / p95 latency: 1.13s / 16.24s
- JSON compliance: 0.93
- JSON mode: native (100% native, 855 calls)
- Parse failure rate: 0.0%
- Extraction methods: `json_object_ads_key`: 5, `json_object_no_ads`: 661, `json_object_single_ad`: 189
- Verbosity: 0/855 calls over 1024 output tokens (0.0%); 0 hit max_tokens (0.0%); 0 salvaged from truncated JSON (0.0%)
- Segment category named on 196/196 detections (100%); the rest stay uncategorized (resolver: production)

#### `qwen/qwen3-14b`

- F1 (avg across episodes): **0.372**
- Total cost / episode: **$0.1724**
- p50 / p95 latency: 14.39s / 59.03s
- JSON compliance: 0.60
- JSON mode: native (100% native, 855 calls)
- Parse failure rate: 15.3%
- Extraction methods: `json_object_no_ads`: 10, `json_object_single_ad`: 712, `json_object_single_ad_truncated`: 2, `parse_failure`: 131
- Verbosity: 155/855 calls over 1024 output tokens (18.1%); 23 hit max_tokens (2.7%); 2 salvaged from truncated JSON (0.2%)
- Segment category named on 318/318 detections (100%); the rest stay uncategorized (resolver: production)

#### `inclusionai/ring-2.6-1t`

- F1 (avg across episodes): **0.342**
- Total cost / episode: **$0.2468**
- p50 / p95 latency: 10.75s / 35.76s
- JSON compliance: 0.81
- JSON mode: native (100% native, 855 calls)
- Parse failure rate: 12.9%
- Extraction methods: `bracket_fallback`: 1, `json_object_no_ads`: 568, `json_object_segments_key`: 6, `json_object_single_ad`: 168, `json_object_single_ad_truncated`: 2, `parse_failure`: 110
- Verbosity: 479/855 calls over 1024 output tokens (56.0%); 111 hit max_tokens (13.0%); 2 salvaged from truncated JSON (0.2%)
- Segment category named on 167/169 detections (99%); the rest stay uncategorized (resolver: production)

#### `openai/gpt-3.5-turbo`

- F1 (avg across episodes): **0.299**
- Total cost / episode: **$0.5564**
- p50 / p95 latency: 1.38s / 1.96s
- JSON compliance: 0.70
- JSON mode: native (100% native, 855 calls)
- Parse failure rate: 0.6%
- Extraction methods: `json_object_no_ads`: 19, `json_object_single_ad`: 831, `parse_failure`: 5
- Verbosity: 0/855 calls over 1024 output tokens (0.0%); 0 hit max_tokens (0.0%); 0 salvaged from truncated JSON (0.0%)
- Segment category named on 767/767 detections (100%); the rest stay uncategorized (resolver: production)

#### `qwen/qwen3.8-max`

- F1 (avg across episodes): **0.285**
- Total cost / episode: **$4.2714**
- p50 / p95 latency: 29.66s / 90.22s
- JSON compliance: 0.73
- JSON mode: native (100% native, 855 calls)
- Parse failure rate: 26.9%
- Extraction methods: `json_array_direct`: 621, `json_object_single_ad`: 3, `json_object_single_ad_truncated`: 1, `parse_failure`: 230
- Verbosity: 456/855 calls over 1024 output tokens (53.3%); 231 hit max_tokens (27.0%); 1 salvaged from truncated JSON (0.1%)
- Segment category named on 113/114 detections (99%); the rest stay uncategorized (resolver: production)

#### `nvidia/nemotron-3.5-lightning`

- F1 (avg across episodes): **0.262**
- Total cost / episode: **$0.1207**
- p50 / p95 latency: 1.16s / 14.47s
- JSON compliance: 0.79
- JSON mode: native (100% native, 855 calls)
- Parse failure rate: 8.3%
- Extraction methods: `json_object_ads_key`: 191, `json_object_no_ads`: 329, `json_object_segments_key`: 9, `json_object_single_ad`: 251, `json_object_single_ad_truncated`: 1, `parse_failure`: 71, `regex_json_array`: 3
- Verbosity: 172/855 calls over 1024 output tokens (20.1%); 75 hit max_tokens (8.8%); 1 salvaged from truncated JSON (0.1%)
- Segment category named on 809/835 detections (97%); the rest stay uncategorized (resolver: production)

#### `meta-llama/llama-3.1-8b-instruct`

- F1 (avg across episodes): **0.261**
- Total cost / episode: **$0.0572**
- p50 / p95 latency: 0.93s / 4.34s
- JSON compliance: 0.82
- JSON mode: native (100% native, 855 calls)
- Parse failure rate: 0.0%
- Extraction methods: `json_array_direct`: 208, `json_object_no_ads`: 138, `json_object_single_ad`: 509
- Verbosity: 45/855 calls over 1024 output tokens (5.3%); 5 hit max_tokens (0.6%); 0 salvaged from truncated JSON (0.0%)
- Segment category named on 1525/1525 detections (100%); the rest stay uncategorized (resolver: production)

#### `thinkingmachines/inkling`

- F1 (avg across episodes): **0.236**
- Total cost / episode: **$3.1580**
- p50 / p95 latency: 65.63s / 324.79s
- JSON compliance: 0.35
- JSON mode: native (100% native, 855 calls)
- Parse failure rate: 51.0%
- Extraction methods: `bracket_fallback`: 81, `json_object_ads_key`: 3, `json_object_no_ads`: 144, `json_object_single_ad`: 189, `json_object_single_ad_truncated`: 2, `parse_failure`: 436
- Verbosity: 711/855 calls over 1024 output tokens (83.2%); 511 hit max_tokens (59.8%); 2 salvaged from truncated JSON (0.2%)
- Segment category named on 77/79 detections (97%); the rest stay uncategorized (resolver: production)

#### `openai/o4-mini`

- F1 (avg across episodes): **0.137**
- Total cost / episode: **$2.1179**
- p50 / p95 latency: 7.34s / 25.55s
- JSON compliance: 0.07
- JSON mode: native (100% native, 855 calls)
- Parse failure rate: 91.0%
- Extraction methods: `json_object_no_ads`: 24, `json_object_segments_key`: 1, `json_object_single_ad`: 52, `parse_failure`: 778
- Verbosity: 380/855 calls over 1024 output tokens (44.4%); 17 hit max_tokens (2.0%); 0 salvaged from truncated JSON (0.0%)
- Segment category named on 52/52 detections (100%); the rest stay uncategorized (resolver: production)

#### `mistralai/mistral-small-2603`

- F1 (avg across episodes): **0.042**
- Total cost / episode: **$0.1745**
- p50 / p95 latency: 0.66s / 1.16s
- JSON compliance: 1.00
- JSON mode: native (100% native, 855 calls)
- Parse failure rate: 0.0%
- Extraction methods: `json_array_direct`: 855
- Verbosity: 0/855 calls over 1024 output tokens (0.0%); 0 hit max_tokens (0.0%); 0 salvaged from truncated JSON (0.0%)
- Segment category named on 20/20 detections (100%); the rest stay uncategorized (resolver: production)

#### `bytedance-seed/seed-2-1-turbo`

- F1 (avg across episodes): **0.039**
- Total cost / episode: **$1.4469**
- p50 / p95 latency: 38.23s / 70.24s
- JSON compliance: 0.40
- JSON mode: native (100% native, 855 calls)
- Parse failure rate: 55.3%
- Extraction methods: `json_array_direct`: 2, `json_object_ads_key`: 247, `json_object_no_ads`: 117, `json_object_single_ad`: 16, `parse_failure`: 473
- Verbosity: 501/855 calls over 1024 output tokens (58.6%); 364 hit max_tokens (42.6%); 0 salvaged from truncated JSON (0.0%)
- Segment category named on 10/10 detections (100%); the rest stay uncategorized (resolver: production)

#### `qwen/qwen3-8b`

- F1 (avg across episodes): **0.000**
- Total cost / episode: **$0.3256**
- p50 / p95 latency: 39.80s / 170.51s
- JSON compliance: 0.10
- JSON mode: native (100% native, 855 calls)
- Parse failure rate: 88.9%
- Extraction methods: `bracket_fallback`: 15, `json_array_direct`: 1, `json_object_no_ads`: 79, `parse_failure`: 760
- Verbosity: 547/855 calls over 1024 output tokens (64.0%); 263 hit max_tokens (30.8%); 0 salvaged from truncated JSON (0.0%)
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
| `claude-fable-5` | PASS | 0 |
| `claude-haiku-4-5-20251001` | PASS | 0 |
| `claude-opus-4-7` | PASS | 0 |
| `claude-opus-4-8` | PASS | 0 |
| `claude-opus-5` | PASS | 0 |
| `claude-sonnet-4-6` | PASS | 0 |
| `claude-sonnet-5` | PASS | 0 |
| `cohere/command-r-plus-08-2024` | PASS | 0 |
| `deepseek/deepseek-v3.2` | PASS | 0 |
| `deepseek/deepseek-v4-flash` | PASS | 0 |
| `deepseek/deepseek-v4-flash-0731` | PASS | 0 |
| `deepseek/deepseek-v4-pro-0813` | PASS | 0 |
| `google/gemini-2.5-flash` | PASS | 0 |
| `google/gemini-3.1-pro-preview` | PASS | 0 |
| `google/gemini-3.5-flash` | PASS | 0 |
| `google/gemini-3.5-flash-lite` | PASS | 0 |
| `google/gemini-3.6-flash` | PASS | 0 |
| `google/gemini-3.7-flash` | PASS | 0 |
| `inclusionai/ring-2.6-1t` | PASS | 0 |
| `meituan/longcat-2.0` | PASS | 0 |
| `meta-llama/llama-3.1-8b-instruct` | PASS | 0 |
| `meta-llama/llama-3.3-70b-instruct` | PASS | 0 |
| `meta/muse-glimmer-30b` | PASS | 0 |
| `meta/muse-spark-1.1` | PASS | 0 |
| `microsoft/phi-4` | PASS | 0 |
| `mistralai/codestral-2508` | PASS | 0 |
| `mistralai/mistral-medium-3-5` | PASS | 0 |
| `mistralai/mistral-medium-3.1` | PASS | 0 |
| `mistralai/mistral-small-2603` | PASS | 0 |
| `nvidia/nemotron-3.5-lightning` | PASS | 0 |
| `openai/gpt-5.6-luna` | PASS | 0 |
| `openai/o3` | PASS | 0 |
| `openai/o4-mini` | PASS | 0 |
| `qwen/qwen3-8b` | PASS | 0 |
| `qwen/qwen3.5-27b` | PASS | 0 |
| `qwen/qwen3.5-plus-02-15` | PASS | 0 |
| `qwen/qwen3.6-flash` | PASS | 0 |
| `qwen/qwen3.6-plus` | PASS | 0 |
| `qwen/qwen3.7-flash` | PASS | 0 |
| `qwen/qwen3.7-max` | PASS | 0 |
| `qwen/qwen3.8-2.4t-a95b` | PASS | 0 |
| `qwen/qwen3.8-27b` | PASS | 0 |
| `qwen/qwen3.8-max` | PASS | 0 |
| `stealth/ox-alpha` | PASS | 0 |
| `stepfun/step-3.7-flash` | PASS | 0 |
| `tencent/hy3` | PASS | 0 |
| `thinkingmachines/inkling` | PASS | 0 |
| `x-ai/grok-4.3` | PASS | 0 |
| `x-ai/grok-4.5` | PASS | 0 |
| `x-ai/grok-4.6` | PASS | 0 |
| `xiaomi/mimo-v2.5-pro` | PASS | 0 |
| `deepseek/deepseek-r1` | FAIL | 1 |
| `deepseek/deepseek-r1-0528` | FAIL | 1 |
| `deepseek/deepseek-r1-distill-llama-70b` | FAIL | 1 |
| `deepseek/deepseek-v4-pro` | FAIL | 1 |
| `google/gemini-2.5-pro` | FAIL | 1 |
| `google/gemini-3.1-flash-lite` | FAIL | 1 |
| `google/gemma-4-26b-a4b-it` | FAIL | 1 |
| `google/gemma-4-31b-it` | FAIL | 1 |
| `meta-llama/llama-4-maverick` | FAIL | 1 |
| `meta-llama/llama-4-scout` | FAIL | 1 |
| `minimax/minimax-m3` | FAIL | 1 |
| `mistralai/mistral-large-2512` | FAIL | 1 |
| `moonshotai/kimi-k2.6` | FAIL | 1 |
| `moonshotai/kimi-k3` | FAIL | 1 |
| `nvidia/nemotron-3-super-120b-a12b` | FAIL | 1 |
| `nvidia/nemotron-3-ultra-550b-a55b` | FAIL | 1 |
| `openai/gpt-5.4` | FAIL | 1 |
| `openai/gpt-5.5` | FAIL | 1 |
| `openai/gpt-oss-120b` | FAIL | 1 |
| `openai/gpt-oss-20b` | FAIL | 1 |
| `qwen/qwen3-14b` | FAIL | 1 |
| `qwen/qwen3-235b-a22b-2507` | FAIL | 1 |
| `thinkingmachines/inkling-small` | FAIL | 1 |
| `google/gemini-2.5-flash-lite` | FAIL | 2 |
| `openai/gpt-3.5-turbo` | FAIL | 2 |
| `openai/gpt-5.4-mini` | FAIL | 2 |
| `openai/gpt-5.6-terra` | FAIL | 2 |
| `qwen/qwen3.7-plus` | FAIL | 2 |
| `xiaomi/mimo-v2.5` | FAIL | 2 |
| `z-ai/glm-5.2` | FAIL | 2 |
| `cohere/command-a` | FAIL | 3 |
| `openai/gpt-5.6-sol` | FAIL | 3 |

#### `ep-crime-junkie-8ce498f299d7`: MISSING: Christopher “Cole” Thomas

- Podcast: crime-junkie
- Duration: 48.2 min
- Truth ads: 4

| Model | F1 | F1 stdev |
|-------|----|----------|
| `claude-haiku-4-5-20251001` | 1.000 | 0.000 |
| `claude-opus-5` | 1.000 | 0.000 |
| `claude-sonnet-4-6` | 1.000 | 0.000 |
| `deepseek/deepseek-v4-flash` | 1.000 | 0.000 |
| `google/gemini-2.5-flash` | 1.000 | 0.000 |
| `google/gemini-3.1-pro-preview` | 1.000 | 0.000 |
| `google/gemini-3.5-flash` | 1.000 | 0.000 |
| `google/gemini-3.6-flash` | 1.000 | 0.000 |
| `google/gemini-3.7-flash` | 1.000 | 0.000 |
| `mistralai/mistral-medium-3-5` | 1.000 | 0.000 |
| `mistralai/mistral-medium-3.1` | 1.000 | 0.000 |
| `openai/gpt-5.5` | 1.000 | 0.000 |
| `openai/gpt-5.6-sol` | 1.000 | 0.000 |
| `openai/gpt-5.6-terra` | 1.000 | 0.000 |
| `qwen/qwen3.5-plus-02-15` | 1.000 | 0.000 |
| `qwen/qwen3.6-flash` | 1.000 | 0.000 |
| `qwen/qwen3.7-flash` | 1.000 | 0.000 |
| `stealth/ox-alpha` | 1.000 | 0.000 |
| `x-ai/grok-4.3` | 1.000 | 0.000 |
| `claude-opus-4-7` | 0.978 | 0.050 |
| `meta/muse-glimmer-30b` | 0.978 | 0.050 |
| `nvidia/nemotron-3-super-120b-a12b` | 0.971 | 0.064 |
| `qwen/qwen3.6-plus` | 0.971 | 0.064 |
| `openai/gpt-5.4-mini` | 0.956 | 0.061 |
| `x-ai/grok-4.5` | 0.956 | 0.061 |
| `google/gemini-3.1-flash-lite` | 0.950 | 0.112 |
| `google/gemma-4-26b-a4b-it` | 0.950 | 0.112 |
| `qwen/qwen3.7-max` | 0.950 | 0.112 |
| `google/gemini-2.5-flash-lite` | 0.943 | 0.078 |
| `openai/gpt-oss-120b` | 0.943 | 0.078 |
| `openai/gpt-oss-20b` | 0.943 | 0.078 |
| `claude-sonnet-5` | 0.938 | 0.091 |
| `claude-fable-5` | 0.933 | 0.061 |
| `google/gemini-2.5-pro` | 0.933 | 0.061 |
| `moonshotai/kimi-k3` | 0.933 | 0.061 |
| `openai/gpt-5.6-luna` | 0.933 | 0.061 |
| `mistralai/mistral-large-2512` | 0.933 | 0.149 |
| `deepseek/deepseek-v4-pro-0813` | 0.914 | 0.078 |
| `x-ai/grok-4.6` | 0.911 | 0.050 |
| `z-ai/glm-5.2` | 0.910 | 0.124 |
| `deepseek/deepseek-v4-flash-0731` | 0.905 | 0.147 |
| `claude-opus-4-8` | 0.889 | 0.000 |
| `meta-llama/llama-4-maverick` | 0.889 | 0.000 |
| `qwen/qwen3.8-27b` | 0.886 | 0.064 |
| `stepfun/step-3.7-flash` | 0.886 | 0.064 |
| `google/gemini-3.5-flash-lite` | 0.876 | 0.137 |
| `google/gemma-4-31b-it` | 0.876 | 0.137 |
| `openai/gpt-3.5-turbo` | 0.871 | 0.040 |
| `deepseek/deepseek-r1` | 0.857 | 0.175 |
| `deepseek/deepseek-v3.2` | 0.857 | 0.000 |
| `deepseek/deepseek-v4-pro` | 0.851 | 0.260 |
| `xiaomi/mimo-v2.5` | 0.843 | 0.151 |
| `openai/gpt-5.4` | 0.818 | 0.040 |
| `nvidia/nemotron-3-ultra-550b-a55b` | 0.813 | 0.162 |
| `meta/muse-spark-1.1` | 0.813 | 0.272 |
| `cohere/command-a` | 0.800 | 0.000 |
| `meta-llama/llama-3.3-70b-instruct` | 0.800 | 0.209 |
| `mistralai/codestral-2508` | 0.800 | 0.112 |
| `tencent/hy3` | 0.781 | 0.104 |
| `qwen/qwen3.7-plus` | 0.744 | 0.091 |
| `qwen/qwen3-235b-a22b-2507` | 0.731 | 0.152 |
| `deepseek/deepseek-r1-0528` | 0.728 | 0.097 |
| `qwen/qwen3-14b` | 0.690 | 0.163 |
| `qwen/qwen3.8-2.4t-a95b` | 0.676 | 0.214 |
| `xiaomi/mimo-v2.5-pro` | 0.651 | 0.163 |
| `cohere/command-r-plus-08-2024` | 0.613 | 0.119 |
| `meta-llama/llama-4-scout` | 0.600 | 0.091 |
| `moonshotai/kimi-k2.6` | 0.581 | 0.144 |
| `thinkingmachines/inkling-small` | 0.530 | 0.314 |
| `inclusionai/ring-2.6-1t` | 0.507 | 0.146 |
| `minimax/minimax-m3` | 0.507 | 0.146 |
| `openai/o3` | 0.507 | 0.146 |
| `deepseek/deepseek-r1-distill-llama-70b` | 0.373 | 0.037 |
| `qwen/qwen3.5-27b` | 0.373 | 0.239 |
| `thinkingmachines/inkling` | 0.307 | 0.174 |
| `qwen/qwen3.8-max` | 0.280 | 0.284 |
| `microsoft/phi-4` | 0.246 | 0.238 |
| `meta-llama/llama-3.1-8b-instruct` | 0.200 | 0.209 |
| `openai/o4-mini` | 0.160 | 0.219 |
| `bytedance-seed/seed-2-1-turbo` | 0.000 | 0.000 |
| `meituan/longcat-2.0` | 0.000 | 0.000 |
| `mistralai/mistral-small-2603` | 0.000 | 0.000 |
| `nvidia/nemotron-3.5-lightning` | 0.000 | 0.000 |
| `qwen/qwen3-8b` | 0.000 | 0.000 |

#### `ep-daily-gist-chicago-70a82fe93a5c`: Suburban apartment market heats up

- Podcast: daily-gist-chicago
- Duration: 21.2 min
- Truth ads: 2

| Model | F1 | F1 stdev |
|-------|----|----------|
| `claude-haiku-4-5-20251001` | 1.000 | 0.000 |
| `google/gemini-3.5-flash-lite` | 1.000 | 0.000 |
| `google/gemma-4-26b-a4b-it` | 0.920 | 0.110 |
| `inclusionai/ring-2.6-1t` | 0.867 | 0.183 |
| `openai/gpt-oss-120b` | 0.820 | 0.205 |
| `claude-opus-4-8` | 0.800 | 0.000 |
| `google/gemini-2.5-flash-lite` | 0.800 | 0.000 |
| `google/gemini-3.1-flash-lite` | 0.800 | 0.000 |
| `openai/gpt-5.4-mini` | 0.800 | 0.000 |
| `openai/gpt-oss-20b` | 0.780 | 0.179 |
| `nvidia/nemotron-3-super-120b-a12b` | 0.760 | 0.219 |
| `stepfun/step-3.7-flash` | 0.733 | 0.435 |
| `claude-fable-5` | 0.700 | 0.245 |
| `qwen/qwen3-14b` | 0.680 | 0.164 |
| `deepseek/deepseek-v4-flash-0731` | 0.667 | 0.408 |
| `qwen/qwen3.5-27b` | 0.640 | 0.434 |
| `deepseek/deepseek-v3.2` | 0.633 | 0.075 |
| `deepseek/deepseek-v4-flash` | 0.613 | 0.236 |
| `claude-sonnet-4-6` | 0.600 | 0.224 |
| `deepseek/deepseek-r1-distill-llama-70b` | 0.593 | 0.379 |
| `meta-llama/llama-3.1-8b-instruct` | 0.593 | 0.379 |
| `meta-llama/llama-3.3-70b-instruct` | 0.567 | 0.091 |
| `mistralai/mistral-medium-3.1` | 0.567 | 0.091 |
| `claude-sonnet-5` | 0.560 | 0.219 |
| `minimax/minimax-m3` | 0.533 | 0.298 |
| `google/gemini-3.1-pro-preview` | 0.500 | 0.000 |
| `google/gemini-3.5-flash` | 0.500 | 0.000 |
| `google/gemini-3.6-flash` | 0.500 | 0.000 |
| `google/gemini-3.7-flash` | 0.500 | 0.000 |
| `mistralai/mistral-medium-3-5` | 0.500 | 0.000 |
| `qwen/qwen3.5-plus-02-15` | 0.500 | 0.000 |
| `qwen/qwen3.6-flash` | 0.500 | 0.000 |
| `qwen/qwen3.7-flash` | 0.500 | 0.000 |
| `qwen/qwen3.7-max` | 0.500 | 0.000 |
| `x-ai/grok-4.5` | 0.500 | 0.000 |
| `x-ai/grok-4.6` | 0.500 | 0.000 |
| `qwen/qwen3-235b-a22b-2507` | 0.480 | 0.179 |
| `moonshotai/kimi-k3` | 0.480 | 0.045 |
| `stealth/ox-alpha` | 0.480 | 0.045 |
| `nvidia/nemotron-3-ultra-550b-a55b` | 0.473 | 0.306 |
| `x-ai/grok-4.3` | 0.460 | 0.055 |
| `deepseek/deepseek-r1-0528` | 0.440 | 0.358 |
| `meta/muse-glimmer-30b` | 0.440 | 0.055 |
| `meta/muse-spark-1.1` | 0.433 | 0.435 |
| `qwen/qwen3.6-plus` | 0.433 | 0.253 |
| `google/gemini-2.5-flash` | 0.420 | 0.045 |
| `openai/gpt-5.4` | 0.420 | 0.045 |
| `openai/gpt-5.6-sol` | 0.420 | 0.045 |
| `mistralai/mistral-large-2512` | 0.407 | 0.060 |
| `claude-opus-4-7` | 0.400 | 0.000 |
| `claude-opus-5` | 0.400 | 0.000 |
| `cohere/command-a` | 0.400 | 0.000 |
| `google/gemini-2.5-pro` | 0.400 | 0.000 |
| `google/gemma-4-31b-it` | 0.400 | 0.000 |
| `mistralai/codestral-2508` | 0.400 | 0.224 |
| `openai/gpt-3.5-turbo` | 0.400 | 0.000 |
| `openai/gpt-5.5` | 0.400 | 0.000 |
| `openai/gpt-5.6-luna` | 0.400 | 0.000 |
| `openai/gpt-5.6-terra` | 0.400 | 0.000 |
| `qwen/qwen3.7-plus` | 0.400 | 0.000 |
| `xiaomi/mimo-v2.5-pro` | 0.400 | 0.365 |
| `deepseek/deepseek-r1` | 0.360 | 0.207 |
| `qwen/qwen3.8-27b` | 0.340 | 0.195 |
| `meta-llama/llama-4-scout` | 0.333 | 0.471 |
| `meta-llama/llama-4-maverick` | 0.320 | 0.179 |
| `z-ai/glm-5.2` | 0.320 | 0.179 |
| `deepseek/deepseek-v4-pro` | 0.280 | 0.259 |
| `cohere/command-r-plus-08-2024` | 0.267 | 0.365 |
| `meituan/longcat-2.0` | 0.233 | 0.325 |
| `thinkingmachines/inkling-small` | 0.160 | 0.219 |
| `openai/o4-mini` | 0.133 | 0.298 |
| `deepseek/deepseek-v4-pro-0813` | 0.100 | 0.224 |
| `tencent/hy3` | 0.100 | 0.224 |
| `moonshotai/kimi-k2.6` | 0.080 | 0.179 |
| `xiaomi/mimo-v2.5` | 0.080 | 0.179 |
| `bytedance-seed/seed-2-1-turbo` | 0.000 | 0.000 |
| `microsoft/phi-4` | 0.000 | 0.000 |
| `mistralai/mistral-small-2603` | 0.000 | 0.000 |
| `nvidia/nemotron-3.5-lightning` | 0.000 | 0.000 |
| `openai/o3` | 0.000 | 0.000 |
| `qwen/qwen3-8b` | 0.000 | 0.000 |
| `qwen/qwen3.8-2.4t-a95b` | 0.000 | 0.000 |
| `qwen/qwen3.8-max` | 0.000 | 0.000 |
| `thinkingmachines/inkling` | 0.000 | 0.000 |

#### `ep-daily-tech-news-show-b576979e1fe8`: Motorola Razr Fold is a Noble Competitor to the Galaxy Z Fold 7 - DTNS 5269

- Podcast: daily-tech-news-show
- Duration: 34.6 min
- Truth ads: 4

| Model | F1 | F1 stdev |
|-------|----|----------|
| `claude-haiku-4-5-20251001` | 1.000 | 0.000 |
| `claude-opus-4-7` | 1.000 | 0.000 |
| `claude-opus-5` | 1.000 | 0.000 |
| `claude-sonnet-4-6` | 1.000 | 0.000 |
| `google/gemini-3.1-flash-lite` | 1.000 | 0.000 |
| `google/gemini-3.5-flash` | 1.000 | 0.000 |
| `google/gemini-3.6-flash` | 1.000 | 0.000 |
| `google/gemini-3.7-flash` | 1.000 | 0.000 |
| `meta-llama/llama-4-maverick` | 1.000 | 0.000 |
| `mistralai/mistral-medium-3-5` | 1.000 | 0.000 |
| `openai/gpt-5.4-mini` | 1.000 | 0.000 |
| `openai/gpt-5.5` | 1.000 | 0.000 |
| `openai/gpt-5.6-luna` | 1.000 | 0.000 |
| `openai/gpt-5.6-sol` | 1.000 | 0.000 |
| `qwen/qwen3.7-max` | 1.000 | 0.000 |
| `stealth/ox-alpha` | 1.000 | 0.000 |
| `x-ai/grok-4.5` | 1.000 | 0.000 |
| `x-ai/grok-4.6` | 1.000 | 0.000 |
| `claude-fable-5` | 0.978 | 0.050 |
| `claude-sonnet-5` | 0.978 | 0.050 |
| `x-ai/grok-4.3` | 0.978 | 0.050 |
| `google/gemini-2.5-flash-lite` | 0.971 | 0.064 |
| `claude-opus-4-8` | 0.956 | 0.061 |
| `google/gemini-3.1-pro-preview` | 0.950 | 0.112 |
| `openai/gpt-5.6-terra` | 0.950 | 0.112 |
| `google/gemini-2.5-pro` | 0.943 | 0.078 |
| `moonshotai/kimi-k3` | 0.943 | 0.078 |
| `qwen/qwen3.6-flash` | 0.921 | 0.114 |
| `mistralai/mistral-large-2512` | 0.911 | 0.050 |
| `google/gemma-4-31b-it` | 0.893 | 0.107 |
| `google/gemini-3.5-flash-lite` | 0.892 | 0.062 |
| `google/gemini-2.5-flash` | 0.889 | 0.000 |
| `qwen/qwen3.5-plus-02-15` | 0.889 | 0.000 |
| `deepseek/deepseek-v4-flash` | 0.886 | 0.064 |
| `openai/gpt-5.4` | 0.878 | 0.125 |
| `deepseek/deepseek-v4-pro` | 0.876 | 0.137 |
| `openai/gpt-oss-120b` | 0.855 | 0.149 |
| `deepseek/deepseek-r1` | 0.848 | 0.119 |
| `moonshotai/kimi-k2.6` | 0.819 | 0.195 |
| `z-ai/glm-5.2` | 0.813 | 0.162 |
| `mistralai/codestral-2508` | 0.800 | 0.112 |
| `meta-llama/llama-3.3-70b-instruct` | 0.789 | 0.124 |
| `nvidia/nemotron-3-super-120b-a12b` | 0.785 | 0.255 |
| `qwen/qwen3.7-flash` | 0.771 | 0.192 |
| `meta/muse-spark-1.1` | 0.771 | 0.152 |
| `meta/muse-glimmer-30b` | 0.750 | 0.177 |
| `mistralai/mistral-medium-3.1` | 0.750 | 0.000 |
| `deepseek/deepseek-r1-0528` | 0.744 | 0.198 |
| `tencent/hy3` | 0.743 | 0.104 |
| `qwen/qwen3-235b-a22b-2507` | 0.722 | 0.199 |
| `deepseek/deepseek-v4-flash-0731` | 0.706 | 0.189 |
| `qwen/qwen3.6-plus` | 0.686 | 0.104 |
| `deepseek/deepseek-v3.2` | 0.667 | 0.000 |
| `deepseek/deepseek-v4-pro-0813` | 0.667 | 0.000 |
| `qwen/qwen3.8-2.4t-a95b` | 0.667 | 0.000 |
| `cohere/command-a` | 0.667 | 0.000 |
| `qwen/qwen3.7-plus` | 0.667 | 0.000 |
| `meta-llama/llama-4-scout` | 0.651 | 0.163 |
| `xiaomi/mimo-v2.5` | 0.649 | 0.175 |
| `meituan/longcat-2.0` | 0.613 | 0.119 |
| `qwen/qwen3.5-27b` | 0.598 | 0.197 |
| `thinkingmachines/inkling-small` | 0.592 | 0.206 |
| `minimax/minimax-m3` | 0.547 | 0.166 |
| `nvidia/nemotron-3-ultra-550b-a55b` | 0.547 | 0.117 |
| `openai/o3` | 0.545 | 0.209 |
| `qwen/qwen3.8-27b` | 0.545 | 0.209 |
| `qwen/qwen3-14b` | 0.529 | 0.039 |
| `inclusionai/ring-2.6-1t` | 0.522 | 0.118 |
| `xiaomi/mimo-v2.5-pro` | 0.518 | 0.332 |
| `nvidia/nemotron-3.5-lightning` | 0.455 | 0.110 |
| `google/gemma-4-26b-a4b-it` | 0.428 | 0.103 |
| `microsoft/phi-4` | 0.427 | 0.138 |
| `thinkingmachines/inkling` | 0.427 | 0.273 |
| `openai/gpt-oss-20b` | 0.421 | 0.144 |
| `qwen/qwen3.8-max` | 0.320 | 0.179 |
| `meta-llama/llama-3.1-8b-instruct` | 0.274 | 0.181 |
| `stepfun/step-3.7-flash` | 0.240 | 0.219 |
| `openai/gpt-3.5-turbo` | 0.222 | 0.000 |
| `deepseek/deepseek-r1-distill-llama-70b` | 0.160 | 0.219 |
| `openai/o4-mini` | 0.147 | 0.202 |
| `cohere/command-r-plus-08-2024` | 0.133 | 0.183 |
| `bytedance-seed/seed-2-1-turbo` | 0.000 | 0.000 |
| `mistralai/mistral-small-2603` | 0.000 | 0.000 |
| `qwen/qwen3-8b` | 0.000 | 0.000 |

#### `ep-daily-tech-news-show-c1904b8605f7`: Switch 2 Prices Rise, Forecast Drops - DTNS 5265

- Podcast: daily-tech-news-show
- Duration: 38.6 min
- Truth ads: 5

| Model | F1 | F1 stdev |
|-------|----|----------|
| `claude-opus-4-7` | 0.933 | 0.149 |
| `openai/gpt-5.4` | 0.750 | 0.000 |
| `openai/gpt-5.5` | 0.750 | 0.000 |
| `openai/gpt-5.6-terra` | 0.750 | 0.000 |
| `qwen/qwen3.5-plus-02-15` | 0.750 | 0.000 |
| `mistralai/mistral-medium-3-5` | 0.733 | 0.037 |
| `deepseek/deepseek-v3.2` | 0.717 | 0.046 |
| `openai/gpt-5.6-luna` | 0.700 | 0.112 |
| `openai/gpt-5.6-sol` | 0.700 | 0.112 |
| `qwen/qwen3.6-flash` | 0.700 | 0.046 |
| `claude-opus-5` | 0.684 | 0.119 |
| `stealth/ox-alpha` | 0.683 | 0.037 |
| `qwen/qwen3.7-flash` | 0.670 | 0.053 |
| `claude-haiku-4-5-20251001` | 0.667 | 0.000 |
| `claude-sonnet-4-6` | 0.667 | 0.000 |
| `google/gemini-2.5-flash-lite` | 0.667 | 0.000 |
| `google/gemini-3.1-flash-lite` | 0.667 | 0.000 |
| `google/gemini-3.6-flash` | 0.667 | 0.000 |
| `google/gemini-3.7-flash` | 0.667 | 0.000 |
| `mistralai/mistral-large-2512` | 0.667 | 0.000 |
| `moonshotai/kimi-k3` | 0.667 | 0.000 |
| `deepseek/deepseek-v4-pro` | 0.662 | 0.089 |
| `claude-opus-4-8` | 0.653 | 0.030 |
| `claude-fable-5` | 0.643 | 0.066 |
| `google/gemini-3.1-pro-preview` | 0.640 | 0.037 |
| `google/gemini-3.5-flash` | 0.640 | 0.037 |
| `x-ai/grok-4.3` | 0.640 | 0.037 |
| `xiaomi/mimo-v2.5` | 0.639 | 0.153 |
| `claude-sonnet-5` | 0.627 | 0.037 |
| `mistralai/mistral-medium-3.1` | 0.613 | 0.030 |
| `x-ai/grok-4.6` | 0.613 | 0.030 |
| `z-ai/glm-5.2` | 0.607 | 0.183 |
| `google/gemini-2.5-flash` | 0.600 | 0.000 |
| `meta-llama/llama-4-maverick` | 0.600 | 0.137 |
| `openai/gpt-5.4-mini` | 0.600 | 0.137 |
| `qwen/qwen3.7-max` | 0.600 | 0.000 |
| `x-ai/grok-4.5` | 0.600 | 0.000 |
| `meta/muse-glimmer-30b` | 0.592 | 0.153 |
| `nvidia/nemotron-3-ultra-550b-a55b` | 0.588 | 0.180 |
| `google/gemini-2.5-pro` | 0.580 | 0.045 |
| `google/gemini-3.5-flash-lite` | 0.564 | 0.113 |
| `qwen/qwen3.7-plus` | 0.557 | 0.197 |
| `google/gemma-4-31b-it` | 0.550 | 0.112 |
| `nvidia/nemotron-3-super-120b-a12b` | 0.545 | 0.172 |
| `minimax/minimax-m3` | 0.529 | 0.039 |
| `openai/gpt-oss-120b` | 0.525 | 0.179 |
| `meta/muse-spark-1.1` | 0.523 | 0.075 |
| `meituan/longcat-2.0` | 0.505 | 0.183 |
| `cohere/command-a` | 0.500 | 0.000 |
| `openai/gpt-3.5-turbo` | 0.500 | 0.000 |
| `xiaomi/mimo-v2.5-pro` | 0.497 | 0.179 |
| `qwen/qwen3.8-27b` | 0.493 | 0.161 |
| `deepseek/deepseek-r1-distill-llama-70b` | 0.493 | 0.139 |
| `deepseek/deepseek-v4-flash` | 0.490 | 0.115 |
| `qwen/qwen3-235b-a22b-2507` | 0.489 | 0.025 |
| `deepseek/deepseek-r1-0528` | 0.487 | 0.125 |
| `qwen/qwen3.5-27b` | 0.486 | 0.117 |
| `cohere/command-r-plus-08-2024` | 0.481 | 0.088 |
| `deepseek/deepseek-r1` | 0.429 | 0.134 |
| `openai/o3` | 0.423 | 0.248 |
| `qwen/qwen3.6-plus` | 0.400 | 0.279 |
| `openai/gpt-oss-20b` | 0.349 | 0.199 |
| `deepseek/deepseek-v4-flash-0731` | 0.347 | 0.030 |
| `meta-llama/llama-3.3-70b-instruct` | 0.339 | 0.123 |
| `nvidia/nemotron-3.5-lightning` | 0.304 | 0.064 |
| `meta-llama/llama-4-scout` | 0.287 | 0.046 |
| `microsoft/phi-4` | 0.271 | 0.020 |
| `qwen/qwen3.8-max` | 0.270 | 0.157 |
| `tencent/hy3` | 0.267 | 0.149 |
| `google/gemma-4-26b-a4b-it` | 0.239 | 0.158 |
| `qwen/qwen3.8-2.4t-a95b` | 0.227 | 0.209 |
| `moonshotai/kimi-k2.6` | 0.213 | 0.197 |
| `mistralai/codestral-2508` | 0.200 | 0.209 |
| `meta-llama/llama-3.1-8b-instruct` | 0.194 | 0.208 |
| `thinkingmachines/inkling-small` | 0.171 | 0.256 |
| `deepseek/deepseek-v4-pro-0813` | 0.147 | 0.202 |
| `thinkingmachines/inkling` | 0.147 | 0.202 |
| `openai/o4-mini` | 0.080 | 0.179 |
| `inclusionai/ring-2.6-1t` | 0.067 | 0.149 |
| `bytedance-seed/seed-2-1-turbo` | 0.000 | 0.000 |
| `mistralai/mistral-small-2603` | 0.000 | 0.000 |
| `qwen/qwen3-14b` | 0.000 | 0.000 |
| `qwen/qwen3-8b` | 0.000 | 0.000 |
| `stepfun/step-3.7-flash` | 0.000 | 0.000 |

#### `ep-drink-champs-30c9a2d49f13`: Episode 501 w/ Warren Sapp

- Podcast: drink-champs
- Duration: 258.6 min
- Truth ads: 9

| Model | F1 | F1 stdev |
|-------|----|----------|
| `qwen/qwen3.5-plus-02-15` | 0.963 | 0.056 |
| `google/gemini-3.5-flash-lite` | 0.954 | 0.047 |
| `x-ai/grok-4.5` | 0.953 | 0.026 |
| `x-ai/grok-4.3` | 0.933 | 0.149 |
| `claude-sonnet-4-6` | 0.931 | 0.023 |
| `claude-haiku-4-5-20251001` | 0.929 | 0.097 |
| `claude-opus-4-8` | 0.920 | 0.029 |
| `google/gemini-3.5-flash` | 0.911 | 0.045 |
| `x-ai/grok-4.6` | 0.895 | 0.047 |
| `google/gemini-3.6-flash` | 0.889 | 0.000 |
| `google/gemini-3.7-flash` | 0.889 | 0.000 |
| `google/gemini-3.1-pro-preview` | 0.880 | 0.021 |
| `deepseek/deepseek-v3.2` | 0.873 | 0.040 |
| `mistralai/mistral-medium-3-5` | 0.872 | 0.077 |
| `openai/gpt-5.5` | 0.872 | 0.059 |
| `claude-opus-5` | 0.861 | 0.026 |
| `google/gemini-2.5-pro` | 0.854 | 0.051 |
| `claude-sonnet-5` | 0.850 | 0.075 |
| `claude-opus-4-7` | 0.845 | 0.058 |
| `google/gemini-3.1-flash-lite` | 0.843 | 0.031 |
| `openai/gpt-5.6-luna` | 0.824 | 0.000 |
| `openai/gpt-5.6-terra` | 0.821 | 0.069 |
| `claude-fable-5` | 0.808 | 0.019 |
| `minimax/minimax-m3` | 0.805 | 0.103 |
| `mistralai/mistral-medium-3.1` | 0.800 | 0.053 |
| `google/gemini-2.5-flash` | 0.780 | 0.045 |
| `openai/gpt-5.4` | 0.740 | 0.119 |
| `deepseek/deepseek-v4-flash` | 0.729 | 0.081 |
| `google/gemini-2.5-flash-lite` | 0.691 | 0.167 |
| `qwen/qwen3.6-flash` | 0.683 | 0.090 |
| `openai/gpt-5.6-sol` | 0.680 | 0.094 |
| `google/gemma-4-31b-it` | 0.677 | 0.113 |
| `qwen/qwen3.5-27b` | 0.663 | 0.097 |
| `meta/muse-spark-1.1` | 0.643 | 0.242 |
| `qwen/qwen3.7-max` | 0.639 | 0.185 |
| `stealth/ox-alpha` | 0.607 | 0.105 |
| `openai/gpt-5.4-mini` | 0.606 | 0.111 |
| `qwen/qwen3.7-flash` | 0.580 | 0.237 |
| `z-ai/glm-5.2` | 0.567 | 0.072 |
| `deepseek/deepseek-v4-pro` | 0.566 | 0.200 |
| `mistralai/mistral-large-2512` | 0.545 | 0.021 |
| `mistralai/codestral-2508` | 0.520 | 0.082 |
| `openai/o3` | 0.501 | 0.214 |
| `moonshotai/kimi-k3` | 0.450 | 0.099 |
| `xiaomi/mimo-v2.5-pro` | 0.448 | 0.196 |
| `qwen/qwen3.6-plus` | 0.447 | 0.313 |
| `meituan/longcat-2.0` | 0.432 | 0.172 |
| `deepseek/deepseek-r1-0528` | 0.417 | 0.229 |
| `google/gemma-4-26b-a4b-it` | 0.403 | 0.191 |
| `deepseek/deepseek-v4-flash-0731` | 0.391 | 0.208 |
| `xiaomi/mimo-v2.5` | 0.376 | 0.122 |
| `meta/muse-glimmer-30b` | 0.373 | 0.165 |
| `deepseek/deepseek-r1` | 0.346 | 0.080 |
| `qwen/qwen3.7-plus` | 0.342 | 0.045 |
| `meta-llama/llama-4-maverick` | 0.303 | 0.007 |
| `deepseek/deepseek-r1-distill-llama-70b` | 0.291 | 0.115 |
| `openai/gpt-oss-120b` | 0.277 | 0.075 |
| `qwen/qwen3-235b-a22b-2507` | 0.199 | 0.164 |
| `meta-llama/llama-3.1-8b-instruct` | 0.167 | 0.036 |
| `cohere/command-r-plus-08-2024` | 0.153 | 0.086 |
| `deepseek/deepseek-v4-pro-0813` | 0.143 | 0.152 |
| `thinkingmachines/inkling-small` | 0.141 | 0.129 |
| `openai/gpt-oss-20b` | 0.136 | 0.050 |
| `meta-llama/llama-4-scout` | 0.096 | 0.076 |
| `nvidia/nemotron-3.5-lightning` | 0.085 | 0.064 |
| `qwen/qwen3.8-27b` | 0.073 | 0.100 |
| `thinkingmachines/inkling` | 0.073 | 0.100 |
| `nvidia/nemotron-3-ultra-550b-a55b` | 0.056 | 0.077 |
| `meta-llama/llama-3.3-70b-instruct` | 0.042 | 0.094 |
| `moonshotai/kimi-k2.6` | 0.040 | 0.089 |
| `qwen/qwen3.8-2.4t-a95b` | 0.040 | 0.089 |
| `tencent/hy3` | 0.036 | 0.081 |
| `inclusionai/ring-2.6-1t` | 0.033 | 0.075 |
| `nvidia/nemotron-3-super-120b-a12b` | 0.031 | 0.069 |
| `qwen/qwen3-14b` | 0.019 | 0.043 |
| `bytedance-seed/seed-2-1-turbo` | 0.000 | 0.000 |
| `cohere/command-a` | 0.000 | 0.000 |
| `microsoft/phi-4` | 0.000 | 0.000 |
| `mistralai/mistral-small-2603` | 0.000 | 0.000 |
| `openai/gpt-3.5-turbo` | 0.000 | 0.000 |
| `openai/o4-mini` | 0.000 | 0.000 |
| `qwen/qwen3-8b` | 0.000 | 0.000 |
| `qwen/qwen3.8-max` | 0.000 | 0.000 |

#### `ep-glt1412515089-373d5ba5007b`: #2496 - Julia Mossbridge

- Podcast: glt1412515089
- Duration: 165.3 min
- Truth ads: 4

| Model | F1 | F1 stdev |
|-------|----|----------|
| `google/gemini-2.5-flash` | 1.000 | 0.000 |
| `deepseek/deepseek-r1-distill-llama-70b` | 0.971 | 0.064 |
| `openai/gpt-oss-120b` | 0.956 | 0.061 |
| `openai/gpt-5.6-luna` | 0.949 | 0.070 |
| `deepseek/deepseek-v4-flash` | 0.921 | 0.114 |
| `x-ai/grok-4.3` | 0.921 | 0.114 |
| `openai/gpt-5.5` | 0.914 | 0.078 |
| `nvidia/nemotron-3-super-120b-a12b` | 0.892 | 0.062 |
| `moonshotai/kimi-k3` | 0.886 | 0.064 |
| `google/gemma-4-26b-a4b-it` | 0.867 | 0.122 |
| `claude-fable-5` | 0.857 | 0.000 |
| `claude-opus-4-7` | 0.857 | 0.000 |
| `claude-opus-5` | 0.857 | 0.000 |
| `claude-sonnet-4-6` | 0.857 | 0.000 |
| `claude-sonnet-5` | 0.857 | 0.000 |
| `deepseek/deepseek-v4-pro` | 0.857 | 0.000 |
| `google/gemini-3.1-pro-preview` | 0.857 | 0.000 |
| `google/gemini-3.5-flash` | 0.857 | 0.000 |
| `google/gemini-3.6-flash` | 0.857 | 0.000 |
| `google/gemini-3.7-flash` | 0.857 | 0.000 |
| `meta/muse-glimmer-30b` | 0.857 | 0.000 |
| `openai/gpt-5.6-terra` | 0.857 | 0.000 |
| `openai/o3` | 0.857 | 0.000 |
| `qwen/qwen3.5-plus-02-15` | 0.857 | 0.000 |
| `qwen/qwen3.7-max` | 0.857 | 0.000 |
| `qwen/qwen3.8-27b` | 0.857 | 0.000 |
| `stealth/ox-alpha` | 0.857 | 0.000 |
| `x-ai/grok-4.5` | 0.857 | 0.000 |
| `x-ai/grok-4.6` | 0.857 | 0.000 |
| `minimax/minimax-m3` | 0.853 | 0.094 |
| `tencent/hy3` | 0.848 | 0.119 |
| `qwen/qwen3.6-plus` | 0.844 | 0.099 |
| `google/gemma-4-31b-it` | 0.828 | 0.114 |
| `deepseek/deepseek-v4-pro-0813` | 0.819 | 0.085 |
| `claude-haiku-4-5-20251001` | 0.814 | 0.185 |
| `qwen/qwen3.5-27b` | 0.800 | 0.183 |
| `meituan/longcat-2.0` | 0.781 | 0.104 |
| `stepfun/step-3.7-flash` | 0.781 | 0.104 |
| `meta/muse-spark-1.1` | 0.766 | 0.204 |
| `deepseek/deepseek-r1` | 0.762 | 0.125 |
| `qwen/qwen3.6-flash` | 0.755 | 0.068 |
| `google/gemini-2.5-pro` | 0.750 | 0.000 |
| `deepseek/deepseek-r1-0528` | 0.750 | 0.095 |
| `qwen/qwen3.8-2.4t-a95b` | 0.745 | 0.227 |
| `claude-opus-4-8` | 0.743 | 0.156 |
| `deepseek/deepseek-v4-flash-0731` | 0.743 | 0.104 |
| `mistralai/mistral-medium-3-5` | 0.740 | 0.108 |
| `openai/gpt-5.6-sol` | 0.739 | 0.048 |
| `cohere/command-r-plus-08-2024` | 0.712 | 0.269 |
| `mistralai/mistral-medium-3.1` | 0.711 | 0.099 |
| `xiaomi/mimo-v2.5-pro` | 0.705 | 0.144 |
| `openai/gpt-oss-20b` | 0.692 | 0.075 |
| `google/gemini-3.1-flash-lite` | 0.681 | 0.047 |
| `qwen/qwen3.7-flash` | 0.664 | 0.374 |
| `meta-llama/llama-3.3-70b-instruct` | 0.659 | 0.073 |
| `deepseek/deepseek-v3.2` | 0.613 | 0.119 |
| `nvidia/nemotron-3-ultra-550b-a55b` | 0.598 | 0.208 |
| `openai/gpt-5.4` | 0.565 | 0.129 |
| `z-ai/glm-5.2` | 0.555 | 0.131 |
| `inclusionai/ring-2.6-1t` | 0.537 | 0.146 |
| `google/gemini-3.5-flash-lite` | 0.531 | 0.223 |
| `meta-llama/llama-4-maverick` | 0.518 | 0.025 |
| `qwen/qwen3-14b` | 0.488 | 0.148 |
| `moonshotai/kimi-k2.6` | 0.473 | 0.169 |
| `thinkingmachines/inkling-small` | 0.467 | 0.144 |
| `google/gemini-2.5-flash-lite` | 0.460 | 0.014 |
| `openai/gpt-5.4-mini` | 0.456 | 0.076 |
| `qwen/qwen3.7-plus` | 0.455 | 0.022 |
| `xiaomi/mimo-v2.5` | 0.402 | 0.031 |
| `mistralai/mistral-large-2512` | 0.400 | 0.080 |
| `qwen/qwen3.8-max` | 0.373 | 0.239 |
| `qwen/qwen3-235b-a22b-2507` | 0.372 | 0.218 |
| `mistralai/codestral-2508` | 0.371 | 0.016 |
| `meta-llama/llama-4-scout` | 0.367 | 0.342 |
| `cohere/command-a` | 0.345 | 0.011 |
| `nvidia/nemotron-3.5-lightning` | 0.290 | 0.092 |
| `openai/gpt-3.5-turbo` | 0.263 | 0.005 |
| `meta-llama/llama-3.1-8b-instruct` | 0.203 | 0.027 |
| `thinkingmachines/inkling` | 0.160 | 0.219 |
| `openai/o4-mini` | 0.147 | 0.202 |
| `bytedance-seed/seed-2-1-turbo` | 0.000 | 0.000 |
| `microsoft/phi-4` | 0.000 | 0.000 |
| `mistralai/mistral-small-2603` | 0.000 | 0.000 |
| `qwen/qwen3-8b` | 0.000 | 0.000 |

#### `ep-it-s-a-thing-e339179dfad6`: SOUP shots - It's a Thing 418

- Podcast: it-s-a-thing
- Duration: 26.7 min
- Truth ads: 2

| Model | F1 | F1 stdev |
|-------|----|----------|
| `claude-fable-5` | 1.000 | 0.000 |
| `claude-haiku-4-5-20251001` | 1.000 | 0.000 |
| `claude-opus-4-7` | 1.000 | 0.000 |
| `claude-opus-4-8` | 1.000 | 0.000 |
| `claude-sonnet-4-6` | 1.000 | 0.000 |
| `google/gemini-3.1-flash-lite` | 1.000 | 0.000 |
| `google/gemini-3.1-pro-preview` | 1.000 | 0.000 |
| `google/gemini-3.5-flash` | 1.000 | 0.000 |
| `google/gemini-3.5-flash-lite` | 1.000 | 0.000 |
| `google/gemini-3.6-flash` | 1.000 | 0.000 |
| `google/gemini-3.7-flash` | 1.000 | 0.000 |
| `mistralai/mistral-medium-3-5` | 1.000 | 0.000 |
| `openai/gpt-5.5` | 1.000 | 0.000 |
| `openai/gpt-5.6-luna` | 1.000 | 0.000 |
| `qwen/qwen3.5-plus-02-15` | 1.000 | 0.000 |
| `qwen/qwen3.7-max` | 1.000 | 0.000 |
| `stealth/ox-alpha` | 1.000 | 0.000 |
| `x-ai/grok-4.3` | 1.000 | 0.000 |
| `x-ai/grok-4.5` | 1.000 | 0.000 |
| `claude-sonnet-5` | 0.960 | 0.089 |
| `x-ai/grok-4.6` | 0.960 | 0.089 |
| `moonshotai/kimi-k3` | 0.933 | 0.149 |
| `qwen/qwen3.6-flash` | 0.933 | 0.149 |
| `openai/gpt-5.6-terra` | 0.920 | 0.110 |
| `microsoft/phi-4` | 0.867 | 0.183 |
| `z-ai/glm-5.2` | 0.853 | 0.145 |
| `qwen/qwen3.7-flash` | 0.833 | 0.236 |
| `claude-opus-5` | 0.800 | 0.000 |
| `meta/muse-spark-1.1` | 0.800 | 0.183 |
| `mistralai/mistral-medium-3.1` | 0.800 | 0.000 |
| `openai/gpt-5.6-sol` | 0.800 | 0.245 |
| `deepseek/deepseek-v3.2` | 0.760 | 0.146 |
| `google/gemini-2.5-pro` | 0.733 | 0.253 |
| `qwen/qwen3.5-27b` | 0.733 | 0.435 |
| `deepseek/deepseek-v4-pro` | 0.700 | 0.183 |
| `meta/muse-glimmer-30b` | 0.700 | 0.447 |
| `qwen/qwen3.7-plus` | 0.693 | 0.243 |
| `google/gemini-2.5-flash` | 0.667 | 0.000 |
| `mistralai/codestral-2508` | 0.667 | 0.000 |
| `deepseek/deepseek-r1` | 0.633 | 0.415 |
| `deepseek/deepseek-v4-flash` | 0.633 | 0.415 |
| `cohere/command-a` | 0.627 | 0.174 |
| `meituan/longcat-2.0` | 0.600 | 0.365 |
| `nvidia/nemotron-3-ultra-550b-a55b` | 0.600 | 0.365 |
| `openai/o3` | 0.600 | 0.365 |
| `meta-llama/llama-3.1-8b-instruct` | 0.587 | 0.179 |
| `openai/gpt-oss-120b` | 0.587 | 0.206 |
| `deepseek/deepseek-r1-0528` | 0.573 | 0.137 |
| `mistralai/mistral-large-2512` | 0.551 | 0.084 |
| `openai/gpt-5.4` | 0.540 | 0.152 |
| `google/gemini-2.5-flash-lite` | 0.533 | 0.075 |
| `meta-llama/llama-4-scout` | 0.533 | 0.298 |
| `minimax/minimax-m3` | 0.533 | 0.298 |
| `nvidia/nemotron-3.5-lightning` | 0.533 | 0.298 |
| `xiaomi/mimo-v2.5-pro` | 0.527 | 0.313 |
| `meta-llama/llama-3.3-70b-instruct` | 0.500 | 0.354 |
| `openai/gpt-5.4-mini` | 0.467 | 0.189 |
| `google/gemma-4-31b-it` | 0.467 | 0.447 |
| `meta-llama/llama-4-maverick` | 0.400 | 0.000 |
| `nvidia/nemotron-3-super-120b-a12b` | 0.400 | 0.365 |
| `qwen/qwen3-235b-a22b-2507` | 0.360 | 0.351 |
| `qwen/qwen3.6-plus` | 0.360 | 0.207 |
| `qwen/qwen3.8-27b` | 0.333 | 0.471 |
| `openai/gpt-oss-20b` | 0.293 | 0.404 |
| `deepseek/deepseek-r1-distill-llama-70b` | 0.267 | 0.365 |
| `deepseek/deepseek-v4-flash-0731` | 0.267 | 0.365 |
| `deepseek/deepseek-v4-pro-0813` | 0.267 | 0.365 |
| `thinkingmachines/inkling-small` | 0.267 | 0.365 |
| `xiaomi/mimo-v2.5` | 0.260 | 0.371 |
| `qwen/qwen3.8-2.4t-a95b` | 0.133 | 0.298 |
| `google/gemma-4-26b-a4b-it` | 0.100 | 0.224 |
| `moonshotai/kimi-k2.6` | 0.100 | 0.224 |
| `bytedance-seed/seed-2-1-turbo` | 0.000 | 0.000 |
| `cohere/command-r-plus-08-2024` | 0.000 | 0.000 |
| `inclusionai/ring-2.6-1t` | 0.000 | 0.000 |
| `mistralai/mistral-small-2603` | 0.000 | 0.000 |
| `openai/gpt-3.5-turbo` | 0.000 | 0.000 |
| `openai/o4-mini` | 0.000 | 0.000 |
| `qwen/qwen3-14b` | 0.000 | 0.000 |
| `qwen/qwen3-8b` | 0.000 | 0.000 |
| `qwen/qwen3.8-max` | 0.000 | 0.000 |
| `stepfun/step-3.7-flash` | 0.000 | 0.000 |
| `tencent/hy3` | 0.000 | 0.000 |
| `thinkingmachines/inkling` | 0.000 | 0.000 |

#### `ep-on-air-with-dan-and-alex2-574e4f303730`: Ryanair Wants Alcohol Bans, Emirates' $6.8B Record Profit & Buying Spirit Airlines?!

- Podcast: on-air-with-dan-and-alex2
- Duration: 58.1 min
- Truth ads: 2

| Model | F1 | F1 stdev |
|-------|----|----------|
| `mistralai/mistral-medium-3-5` | 1.000 | 0.000 |
| `mistralai/mistral-medium-3.1` | 1.000 | 0.000 |
| `x-ai/grok-4.3` | 1.000 | 0.000 |
| `claude-fable-5` | 0.960 | 0.089 |
| `claude-sonnet-5` | 0.960 | 0.089 |
| `google/gemma-4-31b-it` | 0.960 | 0.089 |
| `openai/gpt-5.6-terra` | 0.960 | 0.089 |
| `qwen/qwen3.5-plus-02-15` | 0.960 | 0.089 |
| `x-ai/grok-4.5` | 0.960 | 0.089 |
| `x-ai/grok-4.6` | 0.960 | 0.089 |
| `google/gemini-3.5-flash-lite` | 0.933 | 0.149 |
| `claude-haiku-4-5-20251001` | 0.920 | 0.110 |
| `qwen/qwen3.8-max` | 0.900 | 0.224 |
| `qwen/qwen3.6-flash` | 0.840 | 0.089 |
| `qwen/qwen3.7-max` | 0.840 | 0.089 |
| `qwen/qwen3.8-27b` | 0.840 | 0.089 |
| `moonshotai/kimi-k2.6` | 0.813 | 0.119 |
| `claude-opus-4-8` | 0.800 | 0.000 |
| `claude-opus-5` | 0.800 | 0.000 |
| `deepseek/deepseek-r1` | 0.800 | 0.000 |
| `deepseek/deepseek-v4-flash` | 0.800 | 0.000 |
| `deepseek/deepseek-v4-pro-0813` | 0.800 | 0.000 |
| `google/gemini-2.5-flash` | 0.800 | 0.000 |
| `google/gemini-2.5-pro` | 0.800 | 0.000 |
| `google/gemini-3.1-flash-lite` | 0.800 | 0.000 |
| `google/gemini-3.5-flash` | 0.800 | 0.000 |
| `google/gemini-3.7-flash` | 0.800 | 0.000 |
| `microsoft/phi-4` | 0.800 | 0.183 |
| `mistralai/mistral-large-2512` | 0.800 | 0.000 |
| `nvidia/nemotron-3-super-120b-a12b` | 0.800 | 0.000 |
| `openai/gpt-5.4-mini` | 0.800 | 0.000 |
| `openai/gpt-5.5` | 0.800 | 0.000 |
| `qwen/qwen3.6-plus` | 0.800 | 0.000 |
| `qwen/qwen3.7-flash` | 0.800 | 0.000 |
| `stealth/ox-alpha` | 0.800 | 0.000 |
| `minimax/minimax-m3` | 0.780 | 0.179 |
| `claude-sonnet-4-6` | 0.773 | 0.060 |
| `deepseek/deepseek-r1-0528` | 0.773 | 0.060 |
| `google/gemini-3.6-flash` | 0.773 | 0.060 |
| `tencent/hy3` | 0.753 | 0.185 |
| `openai/gpt-5.6-sol` | 0.747 | 0.073 |
| `qwen/qwen3.8-2.4t-a95b` | 0.747 | 0.073 |
| `claude-opus-4-7` | 0.740 | 0.134 |
| `deepseek/deepseek-v4-flash-0731` | 0.740 | 0.134 |
| `openai/o3` | 0.733 | 0.149 |
| `openai/gpt-oss-120b` | 0.728 | 0.105 |
| `google/gemini-2.5-flash-lite` | 0.720 | 0.179 |
| `qwen/qwen3-235b-a22b-2507` | 0.701 | 0.098 |
| `deepseek/deepseek-v4-pro` | 0.700 | 0.245 |
| `meta-llama/llama-4-maverick` | 0.693 | 0.060 |
| `openai/gpt-5.6-luna` | 0.693 | 0.060 |
| `qwen/qwen3.5-27b` | 0.693 | 0.213 |
| `thinkingmachines/inkling-small` | 0.693 | 0.060 |
| `openai/gpt-5.4` | 0.674 | 0.081 |
| `deepseek/deepseek-v3.2` | 0.667 | 0.000 |
| `google/gemini-3.1-pro-preview` | 0.667 | 0.000 |
| `google/gemma-4-26b-a4b-it` | 0.667 | 0.000 |
| `meta/muse-spark-1.1` | 0.667 | 0.408 |
| `moonshotai/kimi-k3` | 0.667 | 0.000 |
| `meta/muse-glimmer-30b` | 0.640 | 0.219 |
| `stepfun/step-3.7-flash` | 0.633 | 0.415 |
| `thinkingmachines/inkling` | 0.627 | 0.128 |
| `mistralai/codestral-2508` | 0.610 | 0.052 |
| `z-ai/glm-5.2` | 0.603 | 0.114 |
| `cohere/command-r-plus-08-2024` | 0.600 | 0.365 |
| `openai/gpt-oss-20b` | 0.560 | 0.146 |
| `nvidia/nemotron-3.5-lightning` | 0.540 | 0.152 |
| `qwen/qwen3.7-plus` | 0.514 | 0.032 |
| `nvidia/nemotron-3-ultra-550b-a55b` | 0.513 | 0.366 |
| `cohere/command-a` | 0.507 | 0.214 |
| `deepseek/deepseek-r1-distill-llama-70b` | 0.500 | 0.000 |
| `meituan/longcat-2.0` | 0.500 | 0.000 |
| `meta-llama/llama-4-scout` | 0.493 | 0.303 |
| `meta-llama/llama-3.3-70b-instruct` | 0.480 | 0.179 |
| `qwen/qwen3-14b` | 0.453 | 0.197 |
| `openai/gpt-3.5-turbo` | 0.444 | 0.000 |
| `xiaomi/mimo-v2.5` | 0.441 | 0.094 |
| `inclusionai/ring-2.6-1t` | 0.400 | 0.283 |
| `xiaomi/mimo-v2.5-pro` | 0.300 | 0.298 |
| `bytedance-seed/seed-2-1-turbo` | 0.267 | 0.365 |
| `openai/o4-mini` | 0.267 | 0.365 |
| `meta-llama/llama-3.1-8b-instruct` | 0.263 | 0.283 |
| `mistralai/mistral-small-2603` | 0.000 | 0.000 |
| `qwen/qwen3-8b` | 0.000 | 0.000 |

#### `ep-oxide-and-friends-ce789ff5b62e`: Mechanical Engineering at Oxide [chapter images]

- Podcast: oxide-and-friends
- Duration: 84.5 min
- Truth: no-ads episode

| Model | Result | FP count |
|-------|--------|----------|
| `claude-fable-5` | PASS | 0 |
| `claude-haiku-4-5-20251001` | PASS | 0 |
| `claude-opus-4-7` | PASS | 0 |
| `claude-opus-4-8` | PASS | 0 |
| `claude-opus-5` | PASS | 0 |
| `claude-sonnet-4-6` | PASS | 0 |
| `claude-sonnet-5` | PASS | 0 |
| `cohere/command-r-plus-08-2024` | PASS | 0 |
| `deepseek/deepseek-r1-distill-llama-70b` | PASS | 0 |
| `deepseek/deepseek-v3.2` | PASS | 0 |
| `deepseek/deepseek-v4-flash` | PASS | 0 |
| `deepseek/deepseek-v4-flash-0731` | PASS | 0 |
| `deepseek/deepseek-v4-pro` | PASS | 0 |
| `deepseek/deepseek-v4-pro-0813` | PASS | 0 |
| `google/gemini-2.5-flash` | PASS | 0 |
| `google/gemini-2.5-flash-lite` | PASS | 0 |
| `google/gemini-3.1-flash-lite` | PASS | 0 |
| `google/gemini-3.1-pro-preview` | PASS | 0 |
| `google/gemini-3.5-flash` | PASS | 0 |
| `google/gemini-3.5-flash-lite` | PASS | 0 |
| `google/gemini-3.6-flash` | PASS | 0 |
| `google/gemini-3.7-flash` | PASS | 0 |
| `google/gemma-4-26b-a4b-it` | PASS | 0 |
| `google/gemma-4-31b-it` | PASS | 0 |
| `inclusionai/ring-2.6-1t` | PASS | 0 |
| `meituan/longcat-2.0` | PASS | 0 |
| `meta-llama/llama-3.3-70b-instruct` | PASS | 0 |
| `meta-llama/llama-4-maverick` | PASS | 0 |
| `meta-llama/llama-4-scout` | PASS | 0 |
| `meta/muse-glimmer-30b` | PASS | 0 |
| `meta/muse-spark-1.1` | PASS | 0 |
| `microsoft/phi-4` | PASS | 0 |
| `mistralai/mistral-large-2512` | PASS | 0 |
| `mistralai/mistral-medium-3-5` | PASS | 0 |
| `mistralai/mistral-medium-3.1` | PASS | 0 |
| `mistralai/mistral-small-2603` | PASS | 0 |
| `moonshotai/kimi-k3` | PASS | 0 |
| `nvidia/nemotron-3-super-120b-a12b` | PASS | 0 |
| `openai/gpt-5.5` | PASS | 0 |
| `openai/gpt-5.6-luna` | PASS | 0 |
| `openai/o3` | PASS | 0 |
| `openai/o4-mini` | PASS | 0 |
| `qwen/qwen3-8b` | PASS | 0 |
| `qwen/qwen3.5-27b` | PASS | 0 |
| `qwen/qwen3.5-plus-02-15` | PASS | 0 |
| `qwen/qwen3.6-flash` | PASS | 0 |
| `qwen/qwen3.6-plus` | PASS | 0 |
| `qwen/qwen3.7-flash` | PASS | 0 |
| `qwen/qwen3.7-max` | PASS | 0 |
| `qwen/qwen3.8-27b` | PASS | 0 |
| `qwen/qwen3.8-max` | PASS | 0 |
| `stealth/ox-alpha` | PASS | 0 |
| `stepfun/step-3.7-flash` | PASS | 0 |
| `tencent/hy3` | PASS | 0 |
| `x-ai/grok-4.3` | PASS | 0 |
| `x-ai/grok-4.5` | PASS | 0 |
| `x-ai/grok-4.6` | PASS | 0 |
| `bytedance-seed/seed-2-1-turbo` | FAIL | 1 |
| `google/gemini-2.5-pro` | FAIL | 1 |
| `minimax/minimax-m3` | FAIL | 1 |
| `mistralai/codestral-2508` | FAIL | 1 |
| `openai/gpt-5.6-terra` | FAIL | 1 |
| `openai/gpt-oss-120b` | FAIL | 1 |
| `openai/gpt-oss-20b` | FAIL | 1 |
| `qwen/qwen3-14b` | FAIL | 1 |
| `qwen/qwen3.8-2.4t-a95b` | FAIL | 1 |
| `thinkingmachines/inkling` | FAIL | 1 |
| `xiaomi/mimo-v2.5-pro` | FAIL | 1 |
| `meta-llama/llama-3.1-8b-instruct` | FAIL | 2 |
| `openai/gpt-5.6-sol` | FAIL | 2 |
| `deepseek/deepseek-r1` | FAIL | 3 |
| `moonshotai/kimi-k2.6` | FAIL | 3 |
| `openai/gpt-5.4` | FAIL | 3 |
| `openai/gpt-5.4-mini` | FAIL | 3 |
| `nvidia/nemotron-3-ultra-550b-a55b` | FAIL | 4 |
| `nvidia/nemotron-3.5-lightning` | FAIL | 4 |
| `cohere/command-a` | FAIL | 5 |
| `z-ai/glm-5.2` | FAIL | 5 |
| `qwen/qwen3-235b-a22b-2507` | FAIL | 6 |
| `thinkingmachines/inkling-small` | FAIL | 6 |
| `xiaomi/mimo-v2.5` | FAIL | 7 |
| `deepseek/deepseek-r1-0528` | FAIL | 8 |
| `openai/gpt-3.5-turbo` | FAIL | 11 |
| `qwen/qwen3.7-plus` | FAIL | 11 |

#### `ep-security-now-audio-2850b24903b2`: SN 1077: A Browser AI API? - End of Bug Bounties?

- Podcast: security-now-audio
- Duration: 156.2 min
- Truth ads: 7

| Model | F1 | F1 stdev |
|-------|----|----------|
| `claude-opus-5` | 0.933 | 0.000 |
| `meta-llama/llama-3.3-70b-instruct` | 0.857 | 0.000 |
| `openai/gpt-5.5` | 0.857 | 0.000 |
| `openai/gpt-5.6-luna` | 0.846 | 0.026 |
| `x-ai/grok-4.6` | 0.827 | 0.060 |
| `cohere/command-r-plus-08-2024` | 0.825 | 0.034 |
| `openai/gpt-5.6-sol` | 0.823 | 0.031 |
| `moonshotai/kimi-k3` | 0.814 | 0.059 |
| `openai/gpt-5.6-terra` | 0.801 | 0.038 |
| `claude-haiku-4-5-20251001` | 0.800 | 0.000 |
| `google/gemini-3.5-flash` | 0.800 | 0.000 |
| `google/gemma-4-26b-a4b-it` | 0.800 | 0.000 |
| `mistralai/mistral-medium-3-5` | 0.800 | 0.000 |
| `qwen/qwen3.6-flash` | 0.800 | 0.000 |
| `qwen/qwen3.7-flash` | 0.800 | 0.000 |
| `x-ai/grok-4.3` | 0.800 | 0.000 |
| `x-ai/grok-4.5` | 0.800 | 0.000 |
| `nvidia/nemotron-3-super-120b-a12b` | 0.794 | 0.051 |
| `deepseek/deepseek-v4-pro` | 0.790 | 0.044 |
| `google/gemini-3.5-flash-lite` | 0.783 | 0.038 |
| `claude-opus-4-8` | 0.780 | 0.027 |
| `claude-sonnet-4-6` | 0.780 | 0.027 |
| `qwen/qwen3.7-max` | 0.780 | 0.027 |
| `qwen/qwen3.5-27b` | 0.779 | 0.099 |
| `claude-sonnet-5` | 0.775 | 0.056 |
| `qwen/qwen3.5-plus-02-15` | 0.773 | 0.060 |
| `claude-opus-4-7` | 0.772 | 0.038 |
| `openai/gpt-oss-20b` | 0.765 | 0.078 |
| `meta/muse-glimmer-30b` | 0.762 | 0.068 |
| `openai/gpt-oss-120b` | 0.751 | 0.033 |
| `claude-fable-5` | 0.750 | 0.000 |
| `google/gemini-3.1-pro-preview` | 0.750 | 0.000 |
| `google/gemini-3.6-flash` | 0.750 | 0.000 |
| `google/gemini-3.7-flash` | 0.750 | 0.000 |
| `google/gemma-4-31b-it` | 0.750 | 0.000 |
| `google/gemini-2.5-pro` | 0.741 | 0.020 |
| `qwen/qwen3-235b-a22b-2507` | 0.734 | 0.101 |
| `deepseek/deepseek-v4-flash` | 0.732 | 0.122 |
| `stealth/ox-alpha` | 0.725 | 0.056 |
| `qwen/qwen3-14b` | 0.716 | 0.063 |
| `google/gemini-2.5-flash` | 0.706 | 0.000 |
| `qwen/qwen3.8-27b` | 0.686 | 0.116 |
| `tencent/hy3` | 0.675 | 0.112 |
| `meta-llama/llama-4-maverick` | 0.675 | 0.018 |
| `openai/gpt-5.4` | 0.667 | 0.026 |
| `google/gemini-3.1-flash-lite` | 0.667 | 0.000 |
| `z-ai/glm-5.2` | 0.659 | 0.101 |
| `qwen/qwen3.6-plus` | 0.645 | 0.034 |
| `meta/muse-spark-1.1` | 0.642 | 0.098 |
| `meta-llama/llama-4-scout` | 0.639 | 0.134 |
| `deepseek/deepseek-r1-distill-llama-70b` | 0.639 | 0.042 |
| `google/gemini-2.5-flash-lite` | 0.639 | 0.081 |
| `minimax/minimax-m3` | 0.638 | 0.246 |
| `qwen/qwen3.8-2.4t-a95b` | 0.615 | 0.067 |
| `mistralai/mistral-medium-3.1` | 0.613 | 0.073 |
| `deepseek/deepseek-r1` | 0.584 | 0.084 |
| `openai/o3` | 0.574 | 0.160 |
| `openai/gpt-5.4-mini` | 0.569 | 0.045 |
| `inclusionai/ring-2.6-1t` | 0.566 | 0.074 |
| `deepseek/deepseek-v4-flash-0731` | 0.560 | 0.091 |
| `meituan/longcat-2.0` | 0.559 | 0.112 |
| `xiaomi/mimo-v2.5` | 0.557 | 0.128 |
| `mistralai/codestral-2508` | 0.554 | 0.047 |
| `xiaomi/mimo-v2.5-pro` | 0.553 | 0.173 |
| `deepseek/deepseek-r1-0528` | 0.544 | 0.138 |
| `qwen/qwen3.7-plus` | 0.527 | 0.019 |
| `moonshotai/kimi-k2.6` | 0.522 | 0.230 |
| `cohere/command-a` | 0.522 | 0.000 |
| `deepseek/deepseek-v4-pro-0813` | 0.418 | 0.246 |
| `nvidia/nemotron-3.5-lightning` | 0.413 | 0.056 |
| `mistralai/mistral-large-2512` | 0.400 | 0.009 |
| `nvidia/nemotron-3-ultra-550b-a55b` | 0.374 | 0.150 |
| `stepfun/step-3.7-flash` | 0.364 | 0.216 |
| `openai/gpt-3.5-turbo` | 0.364 | 0.000 |
| `microsoft/phi-4` | 0.363 | 0.076 |
| `thinkingmachines/inkling` | 0.358 | 0.137 |
| `deepseek/deepseek-v3.2` | 0.306 | 0.349 |
| `openai/o4-mini` | 0.219 | 0.212 |
| `thinkingmachines/inkling-small` | 0.197 | 0.192 |
| `meta-llama/llama-3.1-8b-instruct` | 0.164 | 0.225 |
| `qwen/qwen3.8-max` | 0.089 | 0.199 |
| `bytedance-seed/seed-2-1-turbo` | 0.000 | 0.000 |
| `mistralai/mistral-small-2603` | 0.000 | 0.000 |
| `qwen/qwen3-8b` | 0.000 | 0.000 |

#### `ep-the-brilliant-idiots-0bb9bf634c8e`: Class Rank

- Podcast: the-brilliant-idiots
- Duration: 119.9 min
- Truth ads: 3

| Model | F1 | F1 stdev |
|-------|----|----------|
| `claude-haiku-4-5-20251001` | 1.000 | 0.000 |
| `claude-sonnet-4-6` | 1.000 | 0.000 |
| `google/gemini-3.5-flash` | 1.000 | 0.000 |
| `google/gemini-3.6-flash` | 1.000 | 0.000 |
| `google/gemini-3.7-flash` | 1.000 | 0.000 |
| `stealth/ox-alpha` | 1.000 | 0.000 |
| `meta/muse-glimmer-30b` | 0.971 | 0.064 |
| `deepseek/deepseek-v4-pro-0813` | 0.960 | 0.089 |
| `deepseek/deepseek-v4-flash-0731` | 0.943 | 0.078 |
| `openai/gpt-5.5` | 0.943 | 0.078 |
| `deepseek/deepseek-v4-flash` | 0.903 | 0.092 |
| `qwen/qwen3.6-plus` | 0.886 | 0.186 |
| `moonshotai/kimi-k3` | 0.864 | 0.089 |
| `google/gemini-3.1-pro-preview` | 0.857 | 0.000 |
| `x-ai/grok-4.5` | 0.857 | 0.000 |
| `tencent/hy3` | 0.836 | 0.120 |
| `openai/gpt-5.6-sol` | 0.834 | 0.145 |
| `openai/gpt-oss-20b` | 0.825 | 0.120 |
| `qwen/qwen3.7-max` | 0.821 | 0.110 |
| `qwen/qwen3.6-flash` | 0.810 | 0.230 |
| `qwen/qwen3.5-plus-02-15` | 0.800 | 0.112 |
| `openai/gpt-5.6-luna` | 0.793 | 0.059 |
| `openai/gpt-5.6-terra` | 0.791 | 0.148 |
| `qwen/qwen3.7-flash` | 0.769 | 0.167 |
| `openai/gpt-oss-120b` | 0.762 | 0.135 |
| `nvidia/nemotron-3-super-120b-a12b` | 0.754 | 0.102 |
| `x-ai/grok-4.3` | 0.743 | 0.156 |
| `x-ai/grok-4.6` | 0.743 | 0.146 |
| `google/gemini-3.5-flash-lite` | 0.700 | 0.046 |
| `google/gemma-4-26b-a4b-it` | 0.697 | 0.115 |
| `mistralai/mistral-medium-3.1` | 0.681 | 0.074 |
| `meta/muse-spark-1.1` | 0.680 | 0.164 |
| `openai/gpt-5.4` | 0.670 | 0.053 |
| `claude-fable-5` | 0.667 | 0.000 |
| `claude-opus-5` | 0.667 | 0.000 |
| `meta-llama/llama-4-maverick` | 0.667 | 0.000 |
| `mistralai/mistral-medium-3-5` | 0.667 | 0.000 |
| `qwen/qwen3.8-27b` | 0.660 | 0.230 |
| `qwen/qwen3.8-2.4t-a95b` | 0.648 | 0.124 |
| `xiaomi/mimo-v2.5-pro` | 0.641 | 0.151 |
| `google/gemini-2.5-pro` | 0.633 | 0.075 |
| `microsoft/phi-4` | 0.620 | 0.164 |
| `minimax/minimax-m3` | 0.610 | 0.292 |
| `deepseek/deepseek-v4-pro` | 0.597 | 0.234 |
| `qwen/qwen3.5-27b` | 0.586 | 0.190 |
| `claude-opus-4-7` | 0.578 | 0.122 |
| `meta-llama/llama-3.3-70b-instruct` | 0.571 | 0.000 |
| `claude-opus-4-8` | 0.556 | 0.104 |
| `google/gemma-4-31b-it` | 0.550 | 0.112 |
| `google/gemini-3.1-flash-lite` | 0.540 | 0.055 |
| `z-ai/glm-5.2` | 0.527 | 0.120 |
| `claude-sonnet-5` | 0.514 | 0.180 |
| `deepseek/deepseek-v3.2` | 0.500 | 0.000 |
| `google/gemini-2.5-flash` | 0.500 | 0.000 |
| `deepseek/deepseek-r1-0528` | 0.499 | 0.146 |
| `thinkingmachines/inkling-small` | 0.483 | 0.149 |
| `deepseek/deepseek-r1` | 0.474 | 0.119 |
| `qwen/qwen3-14b` | 0.470 | 0.141 |
| `google/gemini-2.5-flash-lite` | 0.466 | 0.067 |
| `cohere/command-r-plus-08-2024` | 0.460 | 0.055 |
| `qwen/qwen3.8-max` | 0.460 | 0.055 |
| `thinkingmachines/inkling` | 0.460 | 0.456 |
| `openai/o3` | 0.451 | 0.306 |
| `meta-llama/llama-4-scout` | 0.447 | 0.274 |
| `moonshotai/kimi-k2.6` | 0.397 | 0.289 |
| `deepseek/deepseek-r1-distill-llama-70b` | 0.374 | 0.167 |
| `openai/o4-mini` | 0.360 | 0.351 |
| `qwen/qwen3-235b-a22b-2507` | 0.359 | 0.061 |
| `inclusionai/ring-2.6-1t` | 0.350 | 0.241 |
| `xiaomi/mimo-v2.5` | 0.341 | 0.181 |
| `cohere/command-a` | 0.335 | 0.102 |
| `mistralai/codestral-2508` | 0.300 | 0.021 |
| `meituan/longcat-2.0` | 0.280 | 0.284 |
| `qwen/qwen3.7-plus` | 0.270 | 0.009 |
| `nvidia/nemotron-3-ultra-550b-a55b` | 0.244 | 0.250 |
| `openai/gpt-5.4-mini` | 0.241 | 0.008 |
| `mistralai/mistral-large-2512` | 0.239 | 0.028 |
| `openai/gpt-3.5-turbo` | 0.211 | 0.008 |
| `bytedance-seed/seed-2-1-turbo` | 0.200 | 0.274 |
| `nvidia/nemotron-3.5-lightning` | 0.121 | 0.025 |
| `meta-llama/llama-3.1-8b-instruct` | 0.090 | 0.083 |
| `mistralai/mistral-small-2603` | 0.000 | 0.000 |
| `qwen/qwen3-8b` | 0.000 | 0.000 |

#### `ep-the-tim-dillon-show-f62bd5fa1cfe`: 495 - Hantavirus Cruise & iPad Babies

- Podcast: the-tim-dillon-show
- Duration: 80.1 min
- Truth ads: 6

| Model | F1 | F1 stdev |
|-------|----|----------|
| `claude-fable-5` | 1.000 | 0.000 |
| `claude-haiku-4-5-20251001` | 1.000 | 0.000 |
| `claude-opus-4-7` | 1.000 | 0.000 |
| `claude-opus-4-8` | 1.000 | 0.000 |
| `claude-opus-5` | 1.000 | 0.000 |
| `deepseek/deepseek-v4-pro` | 1.000 | 0.000 |
| `google/gemini-2.5-flash` | 1.000 | 0.000 |
| `google/gemini-2.5-pro` | 1.000 | 0.000 |
| `google/gemini-3.1-flash-lite` | 1.000 | 0.000 |
| `google/gemini-3.1-pro-preview` | 1.000 | 0.000 |
| `google/gemini-3.5-flash` | 1.000 | 0.000 |
| `google/gemini-3.6-flash` | 1.000 | 0.000 |
| `google/gemini-3.7-flash` | 1.000 | 0.000 |
| `moonshotai/kimi-k3` | 1.000 | 0.000 |
| `openai/gpt-5.5` | 1.000 | 0.000 |
| `openai/gpt-5.6-luna` | 1.000 | 0.000 |
| `openai/gpt-5.6-terra` | 1.000 | 0.000 |
| `qwen/qwen3.5-plus-02-15` | 1.000 | 0.000 |
| `qwen/qwen3.7-max` | 1.000 | 0.000 |
| `stealth/ox-alpha` | 1.000 | 0.000 |
| `x-ai/grok-4.5` | 1.000 | 0.000 |
| `x-ai/grok-4.6` | 1.000 | 0.000 |
| `claude-sonnet-5` | 0.978 | 0.050 |
| `google/gemini-3.5-flash-lite` | 0.978 | 0.050 |
| `mistralai/mistral-medium-3.1` | 0.978 | 0.050 |
| `openai/gpt-5.4` | 0.978 | 0.050 |
| `qwen/qwen3.7-flash` | 0.978 | 0.050 |
| `openai/gpt-5.6-sol` | 0.960 | 0.089 |
| `x-ai/grok-4.3` | 0.956 | 0.061 |
| `deepseek/deepseek-v4-flash` | 0.921 | 0.114 |
| `qwen/qwen3.6-flash` | 0.900 | 0.137 |
| `qwen/qwen3.8-27b` | 0.893 | 0.107 |
| `google/gemma-4-31b-it` | 0.867 | 0.298 |
| `claude-sonnet-4-6` | 0.857 | 0.000 |
| `qwen/qwen3.8-2.4t-a95b` | 0.857 | 0.000 |
| `thinkingmachines/inkling-small` | 0.856 | 0.107 |
| `qwen/qwen3.6-plus` | 0.833 | 0.156 |
| `openai/gpt-oss-120b` | 0.831 | 0.123 |
| `mistralai/mistral-medium-3-5` | 0.809 | 0.091 |
| `deepseek/deepseek-v4-flash-0731` | 0.807 | 0.159 |
| `z-ai/glm-5.2` | 0.793 | 0.088 |
| `deepseek/deepseek-v4-pro-0813` | 0.788 | 0.142 |
| `google/gemma-4-26b-a4b-it` | 0.778 | 0.187 |
| `meta/muse-spark-1.1` | 0.773 | 0.227 |
| `deepseek/deepseek-r1` | 0.764 | 0.153 |
| `meta/muse-glimmer-30b` | 0.750 | 0.177 |
| `qwen/qwen3.8-max` | 0.724 | 0.128 |
| `microsoft/phi-4` | 0.721 | 0.084 |
| `qwen/qwen3.7-plus` | 0.715 | 0.027 |
| `deepseek/deepseek-v3.2` | 0.705 | 0.085 |
| `deepseek/deepseek-r1-0528` | 0.677 | 0.218 |
| `openai/gpt-oss-20b` | 0.648 | 0.245 |
| `openai/gpt-5.4-mini` | 0.642 | 0.092 |
| `xiaomi/mimo-v2.5` | 0.635 | 0.126 |
| `qwen/qwen3-235b-a22b-2507` | 0.596 | 0.202 |
| `mistralai/mistral-large-2512` | 0.581 | 0.076 |
| `google/gemini-2.5-flash-lite` | 0.570 | 0.054 |
| `openai/o3` | 0.560 | 0.275 |
| `nvidia/nemotron-3-super-120b-a12b` | 0.529 | 0.233 |
| `moonshotai/kimi-k2.6` | 0.526 | 0.200 |
| `qwen/qwen3.5-27b` | 0.491 | 0.170 |
| `mistralai/codestral-2508` | 0.485 | 0.021 |
| `minimax/minimax-m3` | 0.483 | 0.458 |
| `tencent/hy3` | 0.461 | 0.151 |
| `xiaomi/mimo-v2.5-pro` | 0.459 | 0.285 |
| `nvidia/nemotron-3-ultra-550b-a55b` | 0.438 | 0.256 |
| `deepseek/deepseek-r1-distill-llama-70b` | 0.417 | 0.096 |
| `stepfun/step-3.7-flash` | 0.387 | 0.030 |
| `nvidia/nemotron-3.5-lightning` | 0.350 | 0.074 |
| `meta-llama/llama-4-scout` | 0.338 | 0.190 |
| `thinkingmachines/inkling` | 0.270 | 0.283 |
| `cohere/command-a` | 0.267 | 0.099 |
| `meta-llama/llama-4-maverick` | 0.222 | 0.000 |
| `meituan/longcat-2.0` | 0.213 | 0.197 |
| `meta-llama/llama-3.1-8b-instruct` | 0.176 | 0.128 |
| `meta-llama/llama-3.3-70b-instruct` | 0.150 | 0.137 |
| `qwen/qwen3-14b` | 0.139 | 0.127 |
| `cohere/command-r-plus-08-2024` | 0.124 | 0.170 |
| `inclusionai/ring-2.6-1t` | 0.124 | 0.170 |
| `openai/gpt-3.5-turbo` | 0.092 | 0.084 |
| `bytedance-seed/seed-2-1-turbo` | 0.000 | 0.000 |
| `mistralai/mistral-small-2603` | 0.000 | 0.000 |
| `openai/o4-mini` | 0.000 | 0.000 |
| `qwen/qwen3-8b` | 0.000 | 0.000 |

#### `ep-tosh-show-5f6894439bb6`: My Mom - Emergency Pod

- Podcast: tosh-show
- Duration: 41.4 min
- Truth ads: 5

| Model | F1 | F1 stdev |
|-------|----|----------|
| `google/gemini-3.1-flash-lite` | 1.000 | 0.000 |
| `google/gemini-3.5-flash-lite` | 1.000 | 0.000 |
| `mistralai/mistral-medium-3.1` | 1.000 | 0.000 |
| `qwen/qwen3.5-plus-02-15` | 1.000 | 0.000 |
| `mistralai/mistral-medium-3-5` | 0.982 | 0.041 |
| `x-ai/grok-4.3` | 0.945 | 0.050 |
| `qwen/qwen3.6-flash` | 0.938 | 0.091 |
| `qwen/qwen3.7-max` | 0.930 | 0.071 |
| `claude-sonnet-4-6` | 0.927 | 0.041 |
| `qwen/qwen3.7-flash` | 0.924 | 0.083 |
| `claude-haiku-4-5-20251001` | 0.912 | 0.059 |
| `mistralai/mistral-large-2512` | 0.909 | 0.000 |
| `x-ai/grok-4.6` | 0.897 | 0.069 |
| `google/gemini-3.5-flash` | 0.894 | 0.034 |
| `openai/gpt-5.6-sol` | 0.889 | 0.000 |
| `claude-fable-5` | 0.864 | 0.041 |
| `google/gemini-3.6-flash` | 0.857 | 0.053 |
| `x-ai/grok-4.5` | 0.848 | 0.034 |
| `meta-llama/llama-4-scout` | 0.844 | 0.099 |
| `openai/gpt-oss-120b` | 0.830 | 0.099 |
| `claude-opus-4-8` | 0.822 | 0.049 |
| `deepseek/deepseek-v3.2` | 0.816 | 0.070 |
| `google/gemini-2.5-flash-lite` | 0.802 | 0.108 |
| `deepseek/deepseek-r1` | 0.802 | 0.072 |
| `minimax/minimax-m3` | 0.774 | 0.143 |
| `google/gemini-2.5-flash` | 0.769 | 0.000 |
| `claude-opus-4-7` | 0.756 | 0.122 |
| `qwen/qwen3.7-plus` | 0.728 | 0.124 |
| `mistralai/codestral-2508` | 0.727 | 0.000 |
| `google/gemini-3.7-flash` | 0.715 | 0.108 |
| `openai/gpt-5.6-terra` | 0.711 | 0.099 |
| `google/gemini-3.1-pro-preview` | 0.676 | 0.070 |
| `z-ai/glm-5.2` | 0.670 | 0.115 |
| `openai/gpt-5.6-luna` | 0.667 | 0.157 |
| `claude-opus-5` | 0.667 | 0.000 |
| `meituan/longcat-2.0` | 0.664 | 0.064 |
| `google/gemini-2.5-pro` | 0.658 | 0.166 |
| `claude-sonnet-5` | 0.647 | 0.104 |
| `qwen/qwen3-235b-a22b-2507` | 0.640 | 0.215 |
| `deepseek/deepseek-r1-0528` | 0.640 | 0.123 |
| `qwen/qwen3.6-plus` | 0.630 | 0.375 |
| `openai/gpt-5.5` | 0.622 | 0.099 |
| `xiaomi/mimo-v2.5` | 0.622 | 0.154 |
| `cohere/command-a` | 0.600 | 0.000 |
| `moonshotai/kimi-k3` | 0.599 | 0.248 |
| `microsoft/phi-4` | 0.583 | 0.016 |
| `google/gemma-4-31b-it` | 0.577 | 0.129 |
| `stealth/ox-alpha` | 0.569 | 0.147 |
| `openai/gpt-5.4-mini` | 0.542 | 0.114 |
| `tencent/hy3` | 0.539 | 0.246 |
| `openai/gpt-5.4` | 0.520 | 0.110 |
| `moonshotai/kimi-k2.6` | 0.510 | 0.103 |
| `meta/muse-glimmer-30b` | 0.509 | 0.102 |
| `qwen/qwen3.5-27b` | 0.507 | 0.248 |
| `meta/muse-spark-1.1` | 0.505 | 0.183 |
| `mistralai/mistral-small-2603` | 0.500 | 0.000 |
| `google/gemma-4-26b-a4b-it` | 0.498 | 0.095 |
| `nvidia/nemotron-3-ultra-550b-a55b` | 0.471 | 0.325 |
| `openai/gpt-oss-20b` | 0.457 | 0.326 |
| `deepseek/deepseek-v4-pro` | 0.456 | 0.273 |
| `meta-llama/llama-4-maverick` | 0.444 | 0.000 |
| `meta-llama/llama-3.3-70b-instruct` | 0.410 | 0.124 |
| `xiaomi/mimo-v2.5-pro` | 0.379 | 0.353 |
| `nvidia/nemotron-3-super-120b-a12b` | 0.364 | 0.267 |
| `deepseek/deepseek-v4-flash` | 0.355 | 0.156 |
| `cohere/command-r-plus-08-2024` | 0.295 | 0.021 |
| `qwen/qwen3-14b` | 0.278 | 0.094 |
| `openai/o3` | 0.233 | 0.224 |
| `openai/gpt-3.5-turbo` | 0.218 | 0.010 |
| `meta-llama/llama-3.1-8b-instruct` | 0.216 | 0.032 |
| `deepseek/deepseek-v4-flash-0731` | 0.203 | 0.196 |
| `thinkingmachines/inkling-small` | 0.181 | 0.262 |
| `openai/o4-mini` | 0.133 | 0.183 |
| `inclusionai/ring-2.6-1t` | 0.129 | 0.197 |
| `deepseek/deepseek-r1-distill-llama-70b` | 0.114 | 0.256 |
| `stepfun/step-3.7-flash` | 0.067 | 0.149 |
| `deepseek/deepseek-v4-pro-0813` | 0.057 | 0.128 |
| `nvidia/nemotron-3.5-lightning` | 0.057 | 0.128 |
| `bytedance-seed/seed-2-1-turbo` | 0.000 | 0.000 |
| `qwen/qwen3-8b` | 0.000 | 0.000 |
| `qwen/qwen3.8-2.4t-a95b` | 0.000 | 0.000 |
| `qwen/qwen3.8-27b` | 0.000 | 0.000 |
| `qwen/qwen3.8-max` | 0.000 | 0.000 |
| `thinkingmachines/inkling` | 0.000 | 0.000 |


### Parser stress test

How each model's responses were actually parsed. Columns are extraction methods, ordered alphabetically; rows are models, sorted by parse-failure rate (cleanest at top). `json_array_direct` is the happy path: a bare JSON array we could `json.loads` and process immediately. `markdown_code_block` means we had to strip triple-backtick fences first; `json_object_*` means the model wrapped the array in an outer object and we had to find the array key; `regex_*` are last-resort recovery paths. A model that needs anything but `json_array_direct` for most calls is fragile. It works today, but a small prompt change can break the parser.

| Model | bracket_fallback | json_array_direct | json_object_ad_key | json_object_ads_detected_key | json_object_ads_key | json_object_advertisement_segments_key | json_object_no_ads | json_object_segments_key | json_object_single_ad | json_object_single_ad_truncated | markdown_code_block | parse_failure | regex_json_array |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| `claude-fable-5` | 0 | 855 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| `claude-haiku-4-5-20251001` | 0 | 855 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| `claude-opus-4-7` | 0 | 855 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| `claude-opus-4-8` | 0 | 855 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| `claude-opus-5` | 0 | 855 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| `claude-sonnet-4-6` | 0 | 855 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| `claude-sonnet-5` | 0 | 854 | 0 | 0 | 0 | 0 | 0 | 0 | 1 | 0 | 0 | 0 | 0 |
| `cohere/command-a` | 0 | 0 | 0 | 0 | 0 | 0 | 21 | 0 | 834 | 0 | 0 | 0 | 0 |
| `cohere/command-r-plus-08-2024` | 0 | 0 | 0 | 0 | 5 | 0 | 661 | 0 | 189 | 0 | 0 | 0 | 0 |
| `deepseek/deepseek-v3.2` | 0 | 853 | 0 | 0 | 2 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| `google/gemini-2.5-flash` | 0 | 852 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 3 | 0 | 0 | 0 |
| `google/gemini-3.1-flash-lite` | 0 | 771 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 1 | 0 | 0 | 83 |
| `google/gemini-3.1-pro-preview` | 0 | 855 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| `google/gemini-3.5-flash` | 0 | 852 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 3 |
| `google/gemini-3.5-flash-lite` | 0 | 853 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 1 | 0 | 0 | 1 |
| `google/gemini-3.6-flash` | 0 | 854 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 1 | 0 | 0 | 0 |
| `google/gemini-3.7-flash` | 0 | 855 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| `google/gemma-4-26b-a4b-it` | 0 | 0 | 0 | 0 | 73 | 0 | 372 | 0 | 410 | 0 | 0 | 0 | 0 |
| `google/gemma-4-31b-it` | 0 | 14 | 0 | 0 | 378 | 0 | 283 | 0 | 180 | 0 | 0 | 0 | 0 |
| `meta-llama/llama-3.1-8b-instruct` | 0 | 208 | 0 | 0 | 0 | 0 | 138 | 0 | 509 | 0 | 0 | 0 | 0 |
| `meta-llama/llama-3.3-70b-instruct` | 0 | 147 | 0 | 0 | 0 | 0 | 380 | 0 | 322 | 4 | 0 | 0 | 2 |
| `meta-llama/llama-4-maverick` | 0 | 0 | 0 | 0 | 0 | 0 | 259 | 0 | 596 | 0 | 0 | 0 | 0 |
| `mistralai/codestral-2508` | 0 | 855 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| `mistralai/mistral-large-2512` | 0 | 855 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| `mistralai/mistral-medium-3-5` | 0 | 855 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| `mistralai/mistral-medium-3.1` | 0 | 855 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| `mistralai/mistral-small-2603` | 0 | 855 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| `openai/gpt-5.4` | 0 | 0 | 0 | 0 | 0 | 0 | 274 | 0 | 581 | 0 | 0 | 0 | 0 |
| `openai/gpt-5.4-mini` | 0 | 0 | 0 | 0 | 0 | 0 | 218 | 0 | 637 | 0 | 0 | 0 | 0 |
| `openai/gpt-5.6-sol` | 0 | 0 | 0 | 0 | 53 | 0 | 368 | 3 | 431 | 0 | 0 | 0 | 0 |
| `openai/gpt-5.6-terra` | 0 | 0 | 0 | 0 | 0 | 0 | 458 | 0 | 397 | 0 | 0 | 0 | 0 |
| `qwen/qwen3-235b-a22b-2507` | 0 | 227 | 0 | 0 | 0 | 0 | 169 | 0 | 459 | 0 | 0 | 0 | 0 |
| `x-ai/grok-4.3` | 0 | 855 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| `x-ai/grok-4.5` | 0 | 855 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| `x-ai/grok-4.6` | 0 | 853 | 0 | 0 | 2 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| `nvidia/nemotron-3-ultra-550b-a55b` | 0 | 448 | 0 | 0 | 59 | 0 | 56 | 37 | 252 | 0 | 0 | 3 | 0 |
| `openai/gpt-5.5` | 0 | 0 | 0 | 0 | 0 | 0 | 489 | 0 | 363 | 0 | 0 | 3 | 0 |
| `meta/muse-spark-1.1` | 0 | 0 | 0 | 0 | 28 | 0 | 643 | 3 | 175 | 2 | 0 | 4 | 0 |
| `minimax/minimax-m3` | 0 | 340 | 0 | 0 | 4 | 0 | 262 | 0 | 48 | 0 | 188 | 4 | 9 |
| `qwen/qwen3.5-plus-02-15` | 0 | 851 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 4 | 0 |
| `qwen/qwen3.7-max` | 0 | 833 | 0 | 0 | 0 | 0 | 0 | 0 | 8 | 10 | 0 | 4 | 0 |
| `openai/gpt-3.5-turbo` | 0 | 0 | 0 | 0 | 0 | 0 | 19 | 0 | 831 | 0 | 0 | 5 | 0 |
| `openai/o3` | 0 | 0 | 0 | 0 | 87 | 0 | 613 | 30 | 119 | 0 | 0 | 6 | 0 |
| `openai/gpt-5.6-luna` | 0 | 0 | 0 | 0 | 32 | 0 | 368 | 0 | 446 | 0 | 0 | 9 | 0 |
| `openai/gpt-oss-120b` | 0 | 28 | 0 | 0 | 181 | 0 | 429 | 0 | 202 | 1 | 0 | 9 | 5 |
| `google/gemini-2.5-pro` | 0 | 805 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 9 | 0 | 12 | 29 |
| `microsoft/phi-4` | 0 | 828 | 0 | 0 | 0 | 0 | 4 | 0 | 3 | 7 | 0 | 13 | 0 |
| `google/gemini-2.5-flash-lite` | 0 | 791 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 47 | 0 | 17 | 0 |
| `qwen/qwen3.7-plus` | 0 | 242 | 0 | 0 | 3 | 0 | 86 | 0 | 500 | 2 | 0 | 22 | 0 |
| `xiaomi/mimo-v2.5-pro` | 0 | 0 | 0 | 1 | 133 | 7 | 552 | 11 | 119 | 5 | 0 | 22 | 5 |
| `meta/muse-glimmer-30b` | 0 | 548 | 0 | 0 | 0 | 0 | 0 | 0 | 204 | 79 | 0 | 24 | 0 |
| `xiaomi/mimo-v2.5` | 0 | 0 | 0 | 1 | 54 | 0 | 160 | 2 | 492 | 48 | 18 | 26 | 54 |
| `meta-llama/llama-4-scout` | 31 | 56 | 0 | 0 | 349 | 0 | 262 | 10 | 110 | 3 | 0 | 27 | 7 |
| `z-ai/glm-5.2` | 0 | 5 | 4 | 0 | 95 | 0 | 86 | 0 | 635 | 1 | 0 | 29 | 0 |
| `qwen/qwen3.6-flash` | 0 | 820 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 5 | 0 | 30 | 0 |
| `qwen/qwen3.7-flash` | 0 | 810 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 12 | 0 | 33 | 0 |
| `deepseek/deepseek-v4-pro` | 0 | 434 | 0 | 0 | 10 | 0 | 39 | 205 | 118 | 3 | 0 | 45 | 1 |
| `deepseek/deepseek-v4-flash` | 0 | 93 | 0 | 0 | 175 | 0 | 266 | 6 | 268 | 0 | 0 | 47 | 0 |
| `stealth/ox-alpha` | 0 | 793 | 0 | 0 | 0 | 0 | 1 | 0 | 0 | 8 | 0 | 49 | 4 |
| `moonshotai/kimi-k3` | 0 | 361 | 0 | 0 | 181 | 0 | 27 | 0 | 6 | 122 | 0 | 50 | 108 |
| `nvidia/nemotron-3.5-lightning` | 0 | 0 | 0 | 0 | 191 | 0 | 329 | 9 | 251 | 1 | 0 | 71 | 3 |
| `deepseek/deepseek-r1` | 0 | 494 | 0 | 0 | 182 | 0 | 0 | 1 | 90 | 3 | 0 | 85 | 0 |
| `qwen/qwen3.6-plus` | 0 | 748 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 21 | 0 | 86 | 0 |
| `deepseek/deepseek-r1-0528` | 0 | 535 | 0 | 0 | 154 | 0 | 7 | 4 | 55 | 4 | 0 | 96 | 0 |
| `openai/gpt-oss-20b` | 0 | 26 | 0 | 0 | 121 | 0 | 430 | 0 | 176 | 3 | 0 | 99 | 0 |
| `inclusionai/ring-2.6-1t` | 1 | 0 | 0 | 0 | 0 | 0 | 568 | 6 | 168 | 2 | 0 | 110 | 0 |
| `nvidia/nemotron-3-super-120b-a12b` | 0 | 520 | 0 | 0 | 1 | 0 | 40 | 0 | 160 | 6 | 0 | 127 | 1 |
| `qwen/qwen3-14b` | 0 | 0 | 0 | 0 | 0 | 0 | 10 | 0 | 712 | 2 | 0 | 131 | 0 |
| `deepseek/deepseek-r1-distill-llama-70b` | 0 | 36 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 1 | 110 | 135 | 573 |
| `deepseek/deepseek-v4-flash-0731` | 0 | 224 | 0 | 0 | 9 | 0 | 222 | 0 | 238 | 3 | 0 | 158 | 1 |
| `qwen/qwen3.8-27b` | 0 | 0 | 0 | 0 | 0 | 0 | 470 | 0 | 164 | 9 | 0 | 212 | 0 |
| `deepseek/deepseek-v4-pro-0813` | 0 | 625 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 3 | 0 | 227 | 0 |
| `qwen/qwen3.8-max` | 0 | 621 | 0 | 0 | 0 | 0 | 0 | 0 | 3 | 1 | 0 | 230 | 0 |
| `tencent/hy3` | 12 | 144 | 0 | 0 | 10 | 0 | 198 | 0 | 249 | 6 | 0 | 236 | 0 |
| `qwen/qwen3.8-2.4t-a95b` | 0 | 0 | 0 | 0 | 7 | 0 | 331 | 1 | 273 | 2 | 0 | 241 | 0 |
| `thinkingmachines/inkling-small` | 0 | 0 | 0 | 0 | 0 | 0 | 281 | 0 | 284 | 4 | 0 | 250 | 36 |
| `qwen/qwen3.5-27b` | 0 | 460 | 0 | 0 | 0 | 0 | 124 | 0 | 17 | 1 | 0 | 253 | 0 |
| `moonshotai/kimi-k2.6` | 0 | 270 | 0 | 0 | 57 | 0 | 77 | 3 | 188 | 6 | 0 | 254 | 0 |
| `stepfun/step-3.7-flash` | 0 | 0 | 0 | 0 | 380 | 0 | 4 | 0 | 103 | 4 | 0 | 234 | 0 |
| `meituan/longcat-2.0` | 0 | 0 | 0 | 0 | 414 | 0 | 8 | 0 | 1 | 2 | 0 | 429 | 1 |
| `thinkingmachines/inkling` | 81 | 0 | 0 | 0 | 3 | 0 | 144 | 0 | 189 | 2 | 0 | 436 | 0 |
| `bytedance-seed/seed-2-1-turbo` | 0 | 2 | 0 | 0 | 247 | 0 | 117 | 0 | 16 | 0 | 0 | 473 | 0 |
| `qwen/qwen3-8b` | 15 | 1 | 0 | 0 | 0 | 0 | 79 | 0 | 0 | 0 | 0 | 760 | 0 |
| `openai/o4-mini` | 0 | 0 | 0 | 0 | 0 | 0 | 24 | 1 | 52 | 0 | 0 | 778 | 0 |

## Methodology

Reproducibility settings used for this run. The benchmark sends the same prompts MinusPod sends in production (same system prompt, same sponsor list, same windowing) so the F1 numbers here are directly relevant to production accuracy decisions. Cost is recomputed at report time from token counts against the active pricing snapshot, so all rows compare at the same prices regardless of when the actual call ran.

- Trials per (model, episode): **5**, temperature 0.0
- max_tokens: 4096 (matches MinusPod production)
- response_format: json_object (with prompt-injection fallback when provider rejects native)
- Window size: 10 min, overlap: 3 min (imported from MinusPod's create_windows)
- Pricing snapshot: 2026-08-23T20:34:27.481692Z
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

- Report generated: 2026-09-03T18:33:41Z
- Unique work units (current state, last-write-wins after retries): 71820
- Raw rows in calls.jsonl: 79113 (7293 superseded by later retries; kept for audit)
- Successful: 71690
- Failed: 130
- Lifetime list-price cost (sum of at-runtime costs, includes superseded rows): $698.4264
- Lifetime tokens (same basis): 460,766,578 in + 65,503,116 out = 526,269,694
- Note: every input token is priced at list rate. Providers that serve a repeated prompt from cache bill less than this, and the harness does not record cache hits, so a real invoice for this run will come in under the figure above.
- Active pricing snapshot: 2026-08-23T20:34:27.481692Z
- Addressing mode: timestamps
- System prompt: snapshot:2026-08.txt (sha256:1030b29e)
