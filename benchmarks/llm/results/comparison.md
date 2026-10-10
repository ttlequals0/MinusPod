# MinusPod LLM Benchmark: Prompt Variant Comparison

Detection scores every ad the model returned; segmentation keeps only sponsor, cross_promo, self_promo, and interaction segments, so the two variants are not scored against the same definition of 'ad'. Each cell's columns below come from that cell's own call records and match the corresponding per-cell report. One row per model that has rows in at least two cells; a model present in only one cell is omitted. The delta and p-value columns compare segmentation/segment_ids against detection/timestamps, paired on the episodes each model has scored in both of those two cells (not the full episode set in either cell's own columns). One exception: each cell's `cost/ep` here is this cell's total cost divided by this cell's own episode count, while the per-cell report's 'Cost / episode' column prints the corpus-wide total cost unchanged, so the two are not directly comparable.

Max_tokens per cell (derived from that cell's own call records): detection/timestamps 4096; segmentation/segment_ids 4096 (22% of calls), 10000 (0%), 12000 (4%), 16384 (73%).

| Model | detection/timestamps F0.5 | detection/timestamps precision | detection/timestamps recall | detection/timestamps F1 | detection/timestamps cost/ep | detection/timestamps p50 | detection/timestamps JSON compliance | detection/timestamps n episodes | segmentation/segment_ids F0.5 | segmentation/segment_ids precision | segmentation/segment_ids recall | segmentation/segment_ids F1 | segmentation/segment_ids cost/ep | segmentation/segment_ids p50 | segmentation/segment_ids JSON compliance | segmentation/segment_ids n episodes | delta F0.5 (segmentation/segment_ids - detection/timestamps) | p-value |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| `bytedance-seed/seed-2-1-turbo` | 0.080 | 0.108 | 0.041 | 0.058 | $0.1017 | 35.3s | 0.40 | 13 | 0.553 | 0.661 | 0.389 | 0.467 | $0.2970 | 145.8s | 0.61 | 13 | +0.473 | 0.000 |
| `claude-haiku-4-5-20251001` | 0.882 | 0.873 | 0.935 | 0.899 | $0.0766 | 18.9s | 1.00 | 13 | 0.787 | 0.763 | 0.930 | 0.829 | $0.0544 | 45.8s | 1.00 | 13 | -0.096 | 0.008 |
| `claude-opus-4-8` | 0.816 | 0.802 | 0.909 | 0.842 | $0.3799 | 4.7s | 1.00 | 13 | 0.788 | 0.768 | 0.932 | 0.827 | $0.2599 | 4.6s | 1.00 | 13 | -0.028 | 0.503 |
| `claude-opus-5` | 0.771 | 0.757 | 0.867 | 0.798 | $0.3796 | 4.0s | 1.00 | 13 | 0.822 | 0.807 | 0.942 | 0.852 | $0.2538 | 4.2s | 1.00 | 13 | +0.051 | 0.291 |
| `claude-opus-5-5` | 0.823 | 0.818 | 0.860 | 0.832 | $0.2996 | 3.2s | 1.00 | 13 | 0.789 | 0.772 | 0.915 | 0.824 | $0.2026 | 3.2s | 1.00 | 13 | -0.033 | 0.662 |
| `claude-sonnet-5` | 0.824 | 0.812 | 0.897 | 0.845 | $0.1503 | 5.8s | 1.00 | 13 | 0.754 | 0.740 | 0.877 | 0.785 | $0.1023 | 18.3s | 1.00 | 13 | -0.070 | 0.241 |
| `deepseek/deepseek-v4-flash` | 0.779 | 0.823 | 0.708 | 0.739 | $0.0246 | 8.3s | 0.77 | 13 | 0.732 | 0.780 | 0.735 | 0.715 | $0.0480 | 31.9s | 0.76 | 13 | -0.048 | 0.386 |
| `google/gemini-3.1-flash-lite` | 0.790 | 0.764 | 0.970 | 0.839 | $0.0214 | 1.3s | 1.00 | 13 | 0.776 | 0.749 | 0.944 | 0.824 | $0.0151 | 1.5s | 1.00 | 13 | -0.014 | 0.765 |
| `google/gemini-3.5-flash` | 0.843 | 0.832 | 0.912 | 0.863 | $0.2469 | 6.1s | 1.00 | 13 | 0.835 | 0.820 | 0.938 | 0.864 | $0.2455 | 8.1s | 1.00 | 13 | -0.007 | 0.907 |
| `google/gemini-3.5-flash-lite` | 0.868 | 0.896 | 0.835 | 0.847 | $0.0258 | 0.8s | 1.00 | 13 | 0.789 | 0.767 | 0.935 | 0.829 | $0.0191 | 1.1s | 1.00 | 13 | -0.080 | 0.111 |
| `microsoft/phi-4` | 0.291 | 0.316 | 0.247 | 0.268 | $0.0057 | 0.5s | 0.96 | 13 | 0.658 | 0.632 | 0.849 | 0.710 | $0.0035 | 3.4s | 1.00 | 13 | +0.367 | 0.000 |
| `mistralai/mistral-small-2603` | 0.053 | 0.074 | 0.028 | 0.038 | $0.0122 | 0.7s | 1.00 | 13 | 0.741 | 0.730 | 0.850 | 0.767 | $0.0088 | 3.0s | 1.00 | 13 | +0.688 | 0.000 |
| `openai/gpt-oss-120b` | 0.685 | 0.659 | 0.842 | 0.732 | $0.0045 | 6.6s | 0.88 | 13 | 0.691 | 0.670 | 0.825 | 0.729 | $0.0039 | 6.9s | 1.00 | 13 | +0.006 | 0.912 |
| `qwen/qwen3.5-plus-02-15` | 0.389 | 0.508 | 0.231 | 0.303 | $0.0766 | 54.2s | 0.62 | 13 | 0.827 | 0.804 | 0.958 | 0.868 | $0.1533 | 121.1s | 0.98 | 13 | +0.438 | 0.004 |
| `qwen/qwen3.7-flash` | 0.772 | 0.794 | 0.733 | 0.752 | $0.0053 | 15.8s | 0.93 | 13 | 0.790 | 0.772 | 0.904 | 0.824 | $0.0079 | 37.4s | 1.00 | 13 | +0.018 | 0.739 |
| `qwen/qwen3.8-flash` | 0.672 | 0.736 | 0.572 | 0.617 | $0.0226 | 36.1s | 0.78 | 13 | 0.759 | 0.739 | 0.883 | 0.795 | $0.0496 | 108.7s | 0.91 | 13 | +0.086 | 0.240 |
| `x-ai/grok-4.3` | 0.842 | 0.836 | 0.885 | 0.854 | $0.1127 | 5.0s | 1.00 | 13 | 0.790 | 0.772 | 0.900 | 0.823 | $0.0792 | 9.8s | 1.00 | 13 | -0.052 | 0.460 |

## Episode sets per cell

| Cell | Episodes |
|---|---|
| detection/timestamps | `ep-ai-cloud-essentials-e8dc897fbd6b`, `ep-andy-and-ari-e774e8022fab`, `ep-crime-junkie-8ce498f299d7`, `ep-daily-gist-chicago-70a82fe93a5c`, `ep-daily-tech-news-show-b576979e1fe8`, `ep-daily-tech-news-show-c1904b8605f7`, `ep-drink-champs-30c9a2d49f13`, `ep-glt1412515089-373d5ba5007b`, `ep-it-s-a-thing-e339179dfad6`, `ep-on-air-with-dan-and-alex2-574e4f303730`, `ep-oxide-and-friends-ce789ff5b62e`, `ep-security-now-audio-2850b24903b2`, `ep-the-brilliant-idiots-0bb9bf634c8e`, `ep-the-tim-dillon-show-f62bd5fa1cfe`, `ep-tosh-show-5f6894439bb6` |
| segmentation/segment_ids | `ep-ai-cloud-essentials-e8dc897fbd6b`, `ep-andy-and-ari-e774e8022fab`, `ep-crime-junkie-8ce498f299d7`, `ep-daily-gist-chicago-70a82fe93a5c`, `ep-daily-tech-news-show-b576979e1fe8`, `ep-daily-tech-news-show-c1904b8605f7`, `ep-drink-champs-30c9a2d49f13`, `ep-glt1412515089-373d5ba5007b`, `ep-it-s-a-thing-e339179dfad6`, `ep-on-air-with-dan-and-alex2-574e4f303730`, `ep-oxide-and-friends-ce789ff5b62e`, `ep-security-now-audio-2850b24903b2`, `ep-the-brilliant-idiots-0bb9bf634c8e`, `ep-the-tim-dillon-show-f62bd5fa1cfe`, `ep-tosh-show-5f6894439bb6` |
