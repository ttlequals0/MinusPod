# MinusPod LLM Benchmark

Offline tool that compares LLMs on ad-detection accuracy, cost, latency, and JSON compliance using real MinusPod transcripts. Produces a Markdown report committed to this repo.

## Layout

```
benchmarks/llm/
  pyproject.toml             # uv project, imports MinusPod modules at runtime
  benchmark.toml.example     # copy to benchmark.toml and fill in (gitignored)
  .env.example               # copy to .env (gitignored)
  src/benchmark/             # source
  data/
    corpus/                  # committed verified episodes
    candidates/              # gitignored work-in-progress captures
    pricing_snapshots/       # committed pricing history
  results/
    raw/                     # calls.jsonl, episode_results.jsonl, responses/, prompts/
    report.md                # current report
    report_assets/           # SVG charts referenced by report.md
    archive/                 # explicit snapshots: results/archive/<date>/
```

## Setup

From `benchmarks/llm/`:

```sh
cp benchmark.toml.example benchmark.toml   # edit to fill in your MinusPod base_url
cp .env.example .env                       # fill in MINUSPOD_PASSWORD and provider keys
uv sync                                    # or: pip install -e .
```

The benchmark imports MinusPod modules from `../../src/` via a path bootstrap, so MinusPod itself does not need to be installed as a package.

Requires MinusPod >= 2.0.26 on the server you point at: `benchmark capture` reads `GET /api/v1/feeds/{slug}/episodes/{id}/original-segments`, which was added in 2.0.26. Older episodes return 404 until reprocessed.

## Common workflows

### Capture a new episode

```sh
benchmark capture --episode-url https://podsrv.example.com/ui/feeds/<slug>/episodes/<id>
# edit data/candidates/ep-<slug>-<id>/truth.txt to verify ad markers
benchmark verify ep-<slug>-<id>
```

### Run the benchmark

```sh
benchmark refresh-pricing                  # fetch a fresh pricing snapshot
benchmark run                              # auto-fill all gaps, regenerate report
benchmark run --dry-run                    # preview what would run, no API calls
benchmark run --retry-errors               # also retry calls recorded with error
```

`benchmark run` always reads `[[models]]` from `benchmark.toml` and `data/corpus/` for episodes. To restrict scope, edit the config (set `deprecated = true`) or move episode directories. There are no `--model` or `--episode` filters.

### Regenerate the report from existing data

```sh
benchmark report
```

Useful after editing the report template; no LLM calls happen.

### Snapshot the report

```sh
benchmark archive
```

Copies `results/report.md` + assets to `results/archive/<YYYY-MM-DD>/`.

## Concurrency

`benchmark run` dispatches calls via `asyncio.gather` against the OpenAI / Anthropic SDKs. Two semaphores cap concurrency:

- `[run] max_concurrent_calls` (default 8) -- global cap
- `[run] max_concurrent_per_provider` (default 4) -- per-provider cap

Two simultaneous `benchmark run` invocations against the same `calls.jsonl` are unsupported and will produce duplicate entries. The runner is single-process by design.

## Auth

MinusPod uses Flask sessions. `benchmark capture` reads the password from `MINUSPOD_PASSWORD`, logs in once, and caches the cookie at `~/.cache/minuspod-benchmark/session.json` (mode 0600). Login is rate-limited to 3/min and 10/hour; the cache TTL is 23 hours. The 429 path is reported but never auto-retried.

## Determinism

Every `(model, episode, trial, window_index)` combination computes a `prompt_hash` over the system prompt, user prompt, model id, and temperature. The runner skips any tuple whose hash already appears in `calls.jsonl`. Editing windows (via `regenerate-windows --force`) changes the hash and forces a re-run for affected windows.

## Adding a new model or episode

- New model: append `[[models]]` to `benchmark.toml`. `benchmark run` will fill the gaps (existing models stay cached).
- New episode: capture + verify it. `benchmark run` will run the configured models against it.

## Cost

Costs are recomputed from token counts at report time using the latest pricing snapshot in `data/pricing_snapshots/`. The `*_at_runtime` fields in `calls.jsonl` preserve actual spend.

A full sweep across the recommended 14-model list and 6-episode corpus is roughly $80-$300 depending on model mix. Use `--dry-run` before kicking off to see the call count.

## Schema versions

Every record in `calls.jsonl` and `episode_results.jsonl` carries `schema_version`. v1 is the only version today. Schema changes require a coordinated writer/reader update + version bump.
