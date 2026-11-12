# Running without an LLM: design

Issue #994 asks for MinusPod to work with no LLM subscription.

## Context

Most of the pipeline already runs without a model. Audio fingerprints, text
patterns, the cross-fetch differential and SponsorBlock all run without a model, and
`process_transcript` has a `skip_llm` path that returns the other detectors'
results with status `llm_skipped`. `process_episode` sets `llm_off` for cue-only
and SponsorBlock runs, which also skips the reviewer, the verification pass and
the pre-roll and post-roll heuristics.

No setting chooses that path for a whole install. With no API
key a Standard run fails and is retried until it is marked permanently failed.
The workaround today is a free-tier key plus passthrough.

The detectors that run without a model have two limits:

- SponsorBlock marks are keyed to YouTube video IDs. They apply to YouTube
  feeds only.
- Fingerprints and text patterns match ads the install has already seen. A
  fingerprint is stored only after an LLM detection creates a pattern, so an
  install that never runs a model never gains one.

## Goal

An operator can pick "None" as the LLM provider. Episodes then process and
publish using the model-free detectors, nothing calls a model, and nothing is
reported as failed or degraded because the model is absent. A manually added ad
also stores an audio fingerprint, so it can be matched by audio in later
episodes.

## Non-goals

- Finding repeated audio across episodes automatically, the way Intro Skipper
  does. Without it, a regular podcast feed cuts a new ad only after the operator
  marks it once.
- Changing what a fresh install does. With nothing configured, behavior is the
  same as today.
- A second switch beside the provider setting.

## Decision: `none` is a provider value

`llm_provider` accepts `none`. It is set with `PUT /api/v1/settings/ad-detection`
(`llmProvider`) or `LLM_PROVIDER=none`, and is added to `VALID_LLM_PROVIDERS`,
the boot validator and the OpenAPI enum. No API key, base URL or model is
required while it is selected, and saved keys and models are kept so switching
back restores them.

One helper, `llm_disabled()`, next to `get_effective_provider()` in
`src/llm_client.py`, is the only place that compares the provider to `none`.
Every check below calls it. `secondaryProvider` does not accept `none`, and the
secondary and failover providers are ignored while the primary is `none`.

`GET /api/v1/settings` gains a read-only `llmEnabled` boolean so the UI and API
clients do not compare provider strings.

## Decision: pipeline

In `process_episode`, `llm_off` becomes
`cue_only or sponsorblock_only or llm_disabled()`. That one change gives a
Standard run under `none` the existing behavior of the flag:

| Stage | Under `none` |
|---|---|
| Transcription | Runs. Text patterns need the transcript |
| Audio cues, fingerprint match, text pattern match | Run |
| Cross-fetch differential | Runs. A region is cut when another detector agrees, otherwise held for review |
| SponsorBlock | YouTube feeds only, no LLM fallback |
| Provider reservation and rate-limit hold | Skipped |
| LLM detection | Skipped, status `llm_skipped` |
| Reviewer and verification pass | Skipped |
| Pre-roll and post-roll heuristics | Off |
| Hold rules, cuts, publish | Run as today |

An episode with no detections publishes uncut. The existing manual approval
setting, per feed or the global default, still holds it for review when set.
There is no new hold setting.

The run is recorded as a normal completed run. It is not a degraded publish and
it does not count toward the retry limit. `ModelNotConfiguredError` and the
missing-key failure are not raised under `none`.

Retry ad detection and Reprocess stay available. They rerun the model-free
detectors, which is how an operator applies a newly added pattern to an
existing episode.

## Decision: chapters

Chapter generation is skipped under `none`. Publisher chapters that survive the
cut and ad chapters are kept, as `chapters_mode` already allows. The run is not
marked `chapters_degraded`, because nothing was attempted. `chapters_mode`
`generate` behaves like `auto` while the provider is `none`.

## Decision: other LLM features

| Feature | Under `none` |
|---|---|
| Pattern cleanup job | Does not run |
| Reviewer calibration | Does not run |
| Startup connection check, rate-limit and failover probes | Skipped |
| `POST /patterns/cleanup/run` | 409, code `llm_disabled` |
| `POST /feeds/<slug>/episodes/<id>/regenerate-chapters` | 409, code `llm_disabled` |
| `GET /settings/models` | Empty list |
| Provider test routes | Unchanged. They test the provider named in the request, so an operator can check credentials before switching back |
| `/health` | Does not report the missing model as a problem |

## Decision: UI

The provider selector in Settings gains "None". While it is selected:

- Sections that only configure the model stay visible and disabled, each with
  one line saying the LLM provider is set to None. This covers models, failover,
  stage tunables, the reviewer, prompts and pattern cleanup.
- Chapters: the "Generate chapters" toggle stays enabled, because publisher and
  ad chapters still work. Only "Always generate" and chapter density are
  disabled, with a note that Always generate behaves like Auto.
- The YouTube feed selects stay enabled, with a note on the panel that
  detection then uses text patterns, audio fingerprints and audio cues.
- The "Regenerate chapters" action on an episode is disabled with the same
  reason.
- The Stats LLM tab keeps showing history.

Disabled is preferred over hidden so an operator can see what the mode turns
off. The UI reads `llmEnabled` and follows `design-guide/UI-CONTRACT.md`.

## Decision: "Add ad" stores a fingerprint

`_submit_correction_create` in `src/api/patterns.py` saves a text pattern today.
After the pattern is saved it also calls `AudioFingerprinter.store_fingerprint`
for each created pattern, using the retained original audio and the marked
start and end. This applies with any provider.

Storing is best effort. The pattern is saved either way, and the response gains:

- `fingerprintStored`: boolean
- `fingerprintSkippedReason`: the reason in the audio fingerprint spec's
  creation section (`audio_removed`, `fingerprint_exists`,
  `span_covers_multiple_patterns`, `original_not_retained`, `segment_too_short`,
  `not_distinctive` or `fingerprint_failed`), present only when
  `fingerprintStored` is false. `segment_too_short` follows the minimum ad
  length setting (5 s by default). Each pattern of a split read is fingerprinted
  over its own span only

The marked times are in original-audio time, the same timeline the fingerprint
match scans.

## Docs

- `openapi.yaml`: the `llmProvider` enum, `llmEnabled`, the two 409 responses
  and the two new fields on the create-correction response.
- `docs/api-and-webhooks.md`: the same routes.
- `README.md`, `docs/installation.md`, `docs/DEPLOYMENT.md`,
  `docs/environment-variables.md`, `docs/llm-providers.md` and
  `docs/workflows.md`: an LLM is optional. A new section explains the mode,
  states that SponsorBlock covers YouTube feeds only, and states that a regular
  podcast feed cuts a new ad only after it is marked once.
- `CHANGELOG.md`.

## Tests

- Settings: `none` is accepted by the API and the env var, rejected for the
  secondary provider, and needs no key or model. `llmEnabled` follows it.
- Pipeline: a Standard run under `none` makes no model call, skips provider
  reservation, cuts a fingerprint or text pattern match, publishes an episode
  with no detections uncut, and holds it when manual approval is set. The run
  is completed and not degraded.
- Chapters: no generation call and no `chapters_degraded`. Publisher and ad
  chapters are kept.
- Routes: the two 409 responses, the empty models list, and provider tests
  still answering.
- Add ad: a fingerprint row is created when the original is retained and the
  segment is long enough. Each skip reason is covered, and the pattern is saved
  in every case.
- OpenAPI conformance and registry tests cover the changed routes.
- Frontend: vitest for the disabled states, plus Playwright screenshots at the
  UI-CONTRACT widths.

## Alternatives considered

- A null client that returns empty answers at every call site. Prompts would
  still be built, and each feature would report an empty or degraded result
  where it should report that the feature is off.
- A separate `llm_enabled` setting. It can disagree with the provider setting,
  and every reader would have to check both.
- Treating a missing API key as the mode. A fresh install or a mistyped key
  would then publish uncut episodes with no error.
