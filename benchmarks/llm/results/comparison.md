# MinusPod LLM Benchmark: Prompt Variant Comparison

Detection scores every ad the model returned; segmentation keeps only sponsor, cross_promo, self_promo, and interaction segments, so the two variants are not scored against the same definition of 'ad'. Each cell's columns below come from that cell's own call records and match the corresponding per-cell report. One row per model that has rows in at least two cells; a model present in only one cell is omitted. The delta and p-value columns compare segmentation/segment_ids against detection/timestamps, paired on the episodes each model has scored in both of those two cells (not the full episode set in either cell's own columns). One exception: each cell's `cost/ep` here is this cell's total cost divided by this cell's own episode count, while the per-cell report's 'Cost / episode' column prints the corpus-wide total cost unchanged, so the two are not directly comparable.

| Model | detection/timestamps F0.5 | detection/timestamps precision | detection/timestamps recall | detection/timestamps F1 | detection/timestamps cost/ep | detection/timestamps p50 | detection/timestamps JSON compliance | detection/timestamps n episodes | segmentation/segment_ids F0.5 | segmentation/segment_ids precision | segmentation/segment_ids recall | segmentation/segment_ids F1 | segmentation/segment_ids cost/ep | segmentation/segment_ids p50 | segmentation/segment_ids JSON compliance | segmentation/segment_ids n episodes | delta F0.5 (segmentation/segment_ids - detection/timestamps) | p-value |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| `bytedance-seed/seed-2-1-turbo` | 0.052 | 0.067 | 0.028 | 0.039 | $0.1033 | 38.2s | 0.40 | 12 | 0.000 | 0.000 | 0.000 | 0.000 | $0.0640 | 64.9s | 0.06 | 7 | +0.000 | 1.000 |
| `claude-fable-5-1` | 0.809 | 0.805 | 0.836 | 0.816 | $0.3595 | 4.3s | 1.00 | 7 | 0.857 | 0.837 | 0.964 | 0.891 | $0.2481 | 5.4s | 1.00 | 7 | +0.048 | 0.636 |
| `claude-haiku-4-5-20251001` | 0.908 | 0.900 | 0.946 | 0.920 | $0.0773 | 24.2s | 1.00 | 12 | 0.822 | 0.799 | 0.964 | 0.865 | $0.0270 | 71.4s | 1.00 | 7 | -0.105 | 0.090 |
| `claude-opus-4-8` | 0.793 | 0.775 | 0.908 | 0.827 | $0.3833 | 7.8s | 1.00 | 12 | 0.808 | 0.786 | 0.937 | 0.846 | $0.1267 | 10.6s | 1.00 | 7 | -0.035 | 0.659 |
| `claude-opus-5` | 0.776 | 0.762 | 0.888 | 0.806 | $0.3840 | 3.7s | 1.00 | 12 | 0.849 | 0.835 | 0.936 | 0.874 | $0.1218 | 5.5s | 1.00 | 7 | +0.075 | 0.467 |
| `claude-opus-5-5` | 0.791 | 0.792 | 0.793 | 0.790 | $0.1420 | 3.5s | 1.00 | 7 | 0.799 | 0.777 | 0.941 | 0.839 | $0.0978 | 4.1s | 1.00 | 7 | +0.008 | 0.955 |
| `claude-sonnet-5` | 0.778 | 0.765 | 0.867 | 0.804 | $0.1532 | 8.5s | 1.00 | 12 | 0.801 | 0.790 | 0.896 | 0.826 | $0.0494 | 26.6s | 1.00 | 7 | +0.017 | 0.873 |
| `deepseek/deepseek-v4-flash` | 0.763 | 0.782 | 0.748 | 0.749 | $0.0185 | 6.3s | 0.82 | 12 | 0.787 | 0.836 | 0.751 | 0.758 | $0.0255 | 32.8s | 0.81 | 7 | +0.048 | 0.594 |
| `google/gemini-3.1-flash-lite` | 0.780 | 0.754 | 0.963 | 0.829 | $0.0213 | 0.8s | 0.94 | 12 | 0.782 | 0.763 | 0.896 | 0.816 | $0.0073 | 1.8s | 0.99 | 7 | -0.116 | 0.054 |
| `google/gemini-3.5-flash` | 0.851 | 0.844 | 0.905 | 0.867 | $0.2443 | 5.6s | 1.00 | 12 | 0.834 | 0.822 | 0.914 | 0.857 | $0.1339 | 10.4s | 1.00 | 7 | -0.013 | 0.915 |
| `google/gemini-3.5-flash-lite` | 0.860 | 0.878 | 0.857 | 0.851 | $0.0260 | 0.7s | 1.00 | 12 | 0.774 | 0.749 | 0.914 | 0.817 | $0.0094 | 1.4s | 1.00 | 7 | -0.129 | 0.062 |
| `microsoft/phi-4` | 0.461 | 0.524 | 0.362 | 0.408 | $0.0057 | 0.5s | 0.98 | 12 | 0.720 | 0.689 | 0.936 | 0.780 | $0.0017 | 7.7s | 1.00 | 7 | +0.226 | 0.233 |
| `mistralai/mistral-small-2603` | 0.049 | 0.056 | 0.033 | 0.042 | $0.0125 | 0.7s | 1.00 | 12 | 0.719 | 0.700 | 0.843 | 0.755 | $0.0040 | 3.9s | 1.00 | 7 | +0.635 | 0.001 |
| `openai/gpt-oss-120b` | 0.698 | 0.677 | 0.840 | 0.739 | $0.0045 | 6.5s | 0.88 | 12 | 0.754 | 0.741 | 0.837 | 0.779 | $0.0019 | 9.1s | 1.00 | 7 | +0.016 | 0.661 |
| `qwen/qwen3-8b` | 0.000 | 0.000 | 0.000 | 0.000 | $0.0233 | 39.8s | 0.10 | 12 | 0.555 | 0.640 | 0.441 | 0.491 | $0.0072 | 31.2s | 0.51 | 7 | +0.555 | 0.000 |
| `qwen/qwen3.5-plus-02-15` | 0.865 | 0.862 | 0.900 | 0.874 | $0.0768 | 29.2s | 1.00 | 12 | 0.000 | 0.000 | 0.000 | 0.000 | $0.0426 | 70.7s | 0.02 | 7 | -0.869 | 0.000 |
| `qwen/qwen3.7-flash` | 0.767 | 0.766 | 0.801 | 0.774 | $0.0052 | 10.1s | 0.96 | 12 | 0.813 | 0.803 | 0.871 | 0.831 | $0.0042 | 52.8s | 0.99 | 7 | -0.001 | 0.984 |
| `qwen/qwen3.8-flash` | 0.653 | 0.738 | 0.516 | 0.581 | $0.0125 | 45.3s | 0.68 | 7 | 0.167 | 0.224 | 0.094 | 0.127 | $0.0131 | 67.1s | 0.26 | 7 | -0.485 | 0.001 |
| `x-ai/grok-4.3` | 0.847 | 0.837 | 0.902 | 0.865 | $0.1146 | 3.7s | 1.00 | 12 | 0.814 | 0.792 | 0.939 | 0.853 | $0.0390 | 7.9s | 1.00 | 7 | -0.021 | 0.847 |

## Episode sets per cell

| Cell | Episodes |
|---|---|
| detection/timestamps | `ep-ai-cloud-essentials-e8dc897fbd6b`, `ep-crime-junkie-8ce498f299d7`, `ep-daily-gist-chicago-70a82fe93a5c`, `ep-daily-tech-news-show-b576979e1fe8`, `ep-daily-tech-news-show-c1904b8605f7`, `ep-drink-champs-30c9a2d49f13`, `ep-glt1412515089-373d5ba5007b`, `ep-it-s-a-thing-e339179dfad6`, `ep-on-air-with-dan-and-alex2-574e4f303730`, `ep-oxide-and-friends-ce789ff5b62e`, `ep-security-now-audio-2850b24903b2`, `ep-the-brilliant-idiots-0bb9bf634c8e`, `ep-the-tim-dillon-show-f62bd5fa1cfe`, `ep-tosh-show-5f6894439bb6` |
| segmentation/segment_ids | `ep-ai-cloud-essentials-e8dc897fbd6b`, `ep-crime-junkie-8ce498f299d7`, `ep-daily-gist-chicago-70a82fe93a5c`, `ep-daily-tech-news-show-b576979e1fe8`, `ep-daily-tech-news-show-c1904b8605f7`, `ep-it-s-a-thing-e339179dfad6`, `ep-the-tim-dillon-show-f62bd5fa1cfe`, `ep-tosh-show-5f6894439bb6` |
