# Prompt cache stats: design

Status: approved 2026-10-26. Tracks #897.

## Context

Native Anthropic calls mark the system prompt `cache_control` ephemeral (since 2.98.7, #828). The ledger (`llm_call_usage`) stores cache reads and writes in `cache_read_tokens` and `cache_write_tokens` for every provider: Anthropic from `cache_read_input_tokens` and `cache_creation_input_tokens`, OpenAI-compatible and OpenRouter from `prompt_tokens_details.cached_tokens`. Both conventions count cached tokens inside `input_tokens`.

Only `/stats/llm` returns the sums, and the Stats page shows them as a hint string under one tile. Estimated cost on OpenAI-compatible and OpenRouter rows bills cached tokens at the full input rate because `model_pricing` has no cache rate column.

## Goal

Show how much of each model's input is served from cache, per model, per run, and per attempt, and price cached input on OpenAI-compatible and OpenRouter rows at the provider's read rate.

## Non-goals

- System One. The TypeSafe protocol sends no cache field. `cache_hit` stays as it is until the upstream reports one.
- Backfilling cost on existing rows. Cost is written once at finalize with a `rate_snapshot`.
- Sending cache directives on the OpenAI-compatible path.
- Cache columns on the `/stats/episode-costs` list rows. That table shows no token counts; the expanded per-run view carries them.

## Decision: API fields

`cacheReadTokens` and `cacheWriteTokens`, summed from the ledger columns, are added to:

| Endpoint | Where | Notes |
|---|---|---|
| `/stats/model-usage` | each item | plus `cacheHitRate` in the sort map, `SUM(cache_read_tokens) * 1.0 / NULLIF(SUM(input_tokens), 0)` |
| `/stats/episode-costs/runs` | each run | phases already carry both fields |
| `/stats/spend/attempts` | each attempt | from the same SELECT as the token columns |

Null sums serialize as 0, matching `inputTokens`. `openapi.yaml` documents the fields on the inline model-usage and spend-attempt schemas and on the run schema.

## Decision: hit rate

Hit rate is cache reads divided by input tokens. It is computed in the frontend from the raw counts and printed with `formatPercent` (whole percent). The hover `title` carries the raw counts as "N read, N write of N input". A cell shows "-" when input is 0 or both cache counts are 0.

## Decision: pricing cached input

- `model_pricing` gains a nullable `cache_read_cost_per_mtok` column via `_add_column_if_missing`. `SCHEMA_SQL` carries it for fresh databases.
- `fetch_litellm_pricing` reads `cache_read_input_token_cost` into it when present.
- The operator override accepts an optional `cacheReadCostPerMtok`; `_resolve_model_rate` returns it as a fourth element, `None` when unknown.
- In `finalize_llm_attempt`, for an estimated row whose provider is not `anthropic`, with `cache_read_tokens` above 0 and a known cache rate: billed input is `input - reads` at the input rate plus `reads` at the cache rate. `rate_snapshot` gains `cacheReadCostPerMtok`. With no known cache rate the row bills as today.
- The `anthropic` branch (read multiplier by model, 1.25x write) and provider-reported rows are unchanged.

## Decision: UI

- Model usage table: a "Cache hit" column after Calls, sortable through the `cacheHitRate` sort key. The expanded detail list adds Cache read, Cache write and Cache hit rate.
- Spend attempts: a "Cache" column with the percent, raw counts on hover.
- Per-run phase breakdown (`ProcessingRunsTable`): the "Cache" metric prints the percent instead of "N r / N w"; counts move to the hover title.
- LLM card: the Token usage hint gains the overall hit rate.
- Design guide and UI-CONTRACT.md apply. Numeric headings align with their values. Visual validation is required before the change is reported done: Playwright against the dev server with a mocked `/api/v1`, viewport screenshots at the contract widths, coarse pointer at phone widths.

## Tests

- Unit: ledger sums per endpoint query, finalize cost math with and without a cache rate and for the unchanged anthropic path, LiteLLM fetch of the cache rate, migration adds the column on an existing database.
- Integration: the three endpoints return the fields; `/stats/model-usage` sorts by `cacheHitRate`; openapi conformance passes.
- Vitest: model usage column and detail rows, spend attempts column, phase breakdown percent and title, LLM tile hint.

## Alternatives considered

- Returning `cacheHitRate` from the API. Rejected: the UI needs the raw counts for the hover anyway, and one derived number in SQL only for sorting keeps the response additive.
- Storing an OpenAI cache multiplier table in code like the anthropic one. Rejected: LiteLLM already publishes per-model cache read rates and the fetcher runs on a schedule.
