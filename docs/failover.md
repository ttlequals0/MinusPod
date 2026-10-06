# Failover

[< Docs index](README.md) | [Project README](../README.md)

---

## Contents

- [What failover is](#what-failover-is)
- [What triggers it](#what-triggers-it)
- [What happens mid-run](#what-happens-mid-run)
- [Health probes](#health-probes)
- [Manual control](#manual-control)
- [Configuration reference](#configuration-reference)
- [Webhook events](#webhook-events)
- [Per-provider timeouts and retries](#per-provider-timeouts-and-retries)

## What failover is

Failover is a standby account that takes over when a configured provider fails:

- **Provider A** (`llm-a`) - the first LLM provider slot.
- **Provider B** (`llm-b`) - the second LLM provider slot, when enabled.
- **Transcriber** (`transcriber`) - the active Whisper backend.

There is one shared LLM standby provider, used by whichever of Provider A or Provider B is in trouble, and one standby transcriber. Each has its own type, endpoint, key, timeout, retries, and (for the LLM side) its own models per pipeline stage.

Failover is different from the Provider A / Provider B slot a stage is configured to use (see [LLM Providers > Per-Stage Providers](llm-providers.md#per-stage-providers)). Picking Provider A or Provider B for a stage is routing, decided ahead of time and only re-evaluated when you change the setting. Failover moves work at runtime, without touching that setting, and moves it back once the active account is healthy again.

## What triggers it

| Condition | Triggers failover | Notes |
|---|---|---|
| Connection refused, DNS failure, timeout, open circuit breaker | Yes | |
| HTTP 408 (request timeout) or 5xx response | Yes | |
| 401 or 403, invalid/expired key or a spend/quota limit | Yes | |
| 402, or a 400 the provider marks as a credit/quota exhaustion | Yes | |
| 404 (model or resource not found) | Yes | |
| 429 (rate limit) or an exhausted daily quota | No | The [Rate-Limit Hold](configuration.md#rate-limit-hold) waits out the provider's reset instead. |
| Operator-configured RPM/RPD/TPM cap | No | Manual caps remain enforced. |
| 400 or 422 that is not a quota rejection (bad request, bad parameters) | No | Handled by retrying with fallback parameters, not by switching providers. |
| A token request exceeds the provider's per-request or per-minute cap | No | Reduce the window size or requested output budget. |

A rate limit or daily quota never moves LLM work to the standby, with or without a standby configured. If the standby itself returns a rate limit while it is in use, the [Rate-Limit Hold](configuration.md#rate-limit-hold) pauses only the standby account.

The transcriber side uses the same shape: connection errors and 5xx-equivalent backend outages trigger failover, and so do 401/402/403/404 and exhausted 429 retries from an API backend (`TranscriptionRejectedError`), exhausted HTTP 408 timeout retries, and a local backend's model-load failure. Other 4xx responses from an API backend are left alone, same as before this feature. Unlike the LLM side, a transcription 429 that outlasts its retry deadline does switch to the standby transcriber, because transcription has no rate-limit hold to wait it out.

## What happens mid-run

**LLM calls.** A call that exhausts its normal retry ladder on a trigger error is retried once on the standby provider. That retry runs the standby provider's full retry ladder, with its own model for the pipeline phase, its own timeout, and its own retry count. Later calls in the same run follow immediately, because failover state is checked live, not just at run start. If the standby attempt also fails, the original provider error determines deferral or retry. The standby error is reported instead only when the standby rejected the request itself (HTTP 400 or 422 that is not a credit or quota rejection) or returned a rate-limit hold, cancellation, or account change.

The run retains its original per-phase Provider A/B routes. Cancelling failover or recovering the original provider restores those routes for subsequent calls. Processing history marks a completed or failed run that actually dispatched to the standby, even if recovery happens before the run ends. Deferred, held, cancelled, and requeued runs write no history row; their standby requests stay in the per-run usage ledger.

**Transcription.** The behavior differs by whether the standby transcriber is the same kind of backend as the active one:

- **API to API**: only the chunks that have not finished yet rerun on the failover settings; chunks the first pass already finished are kept.
- **Any switch with a local backend on either side** (local to local, local to API, or API to local): the partial results from the first pass are discarded and the whole episode reruns on the failover backend.
- **Short episodes** that never reach the chunk plan (single-shot transcription) switch the same way: the failed attempt reruns on the failover config, either as a single call (same backend type) or by rerunning the whole episode through the other backend's chunking path.

If the standby attempt also fails, its error determines offline-queue deferral or the normal failure/retry path. Deferred episodes resume when the endpoints required by their current processing mode and phase routes are healthy, including an active standby. An unused provider does not block them.

## Health probes

A background tick checks enabled targets at the configured **Probe interval** (`failoverProbeIntervalMinutes`, 1-60 minutes, default 5).

- LLM probes require a successful response with a valid model catalog or provider-specific key metadata. Malformed responses, authentication failures, timeouts, and throttling do not count as healthy. An OpenAI-compatible or Ollama endpoint must answer `GET /models` with a `data` list, the same requirement as model discovery and Test Connection; an endpoint without one probes unhealthy and fails over after two probes. Anthropic and OpenRouter with no API key read as **Not configured** and are not contacted.
- API transcriber probes request `/models`. A 2xx response counts as reachable, as do HTTP 404 and 405 because many transcription servers do not expose that route or only accept POST there. Other errors do not count as healthy.
- Routine local checks verify that the Whisper stack is available. After a local model or runtime failure, recovery requires a successful diagnostic decode using the original model, device, and compute type, normalized the same way the transcriber loads them. The decode runs only in the background worker that owns the model; **Probe now** from the web UI hands it to that worker and shows the last stored result until it runs. A probe that finds local processing busy leaves the healthy and failed counts unchanged, and a standby-model decode does not invalidate a probe of the original model. When the standby is also local with a different model, each recovery probe loads the original model between episodes, so expect one extra model load per probe interval until recovery.

Two consecutive failed probes of an original provider trigger automatic failover. Recovery requires **N consecutive healthy probes** of that provider (**Recovery probes**, `failoverRecoveryProbes`, 1-10, default 3). Results from before the failover or from an obsolete provider configuration cannot recover it. A manual failover stays active until cancelled.

An eligible failed processing call triggers failover immediately after its normal retries, without waiting for two failed probes.

Before processing, MinusPod checks the effective endpoints needed for the run when their cached probes are older than the interval or no longer match the provider configuration, failover state, or local transcription outcome. Concurrent background, manual, and pre-run checks share probe ownership across workers. Scheduled checks continue probing the original providers so automatic recovery can occur while work uses standby endpoints.

## Manual control

The Failover card (Settings > AI & Processing > Failover) shows a status row per target: badge (**Healthy**, **Failed over**, **Unprobed**, or **Not configured**), last probe time, and, while active, the source (automatic, probe, or manual) and reason, plus a **Trigger** or **Cancel** button. A **Probe now** button in the header requests checks for every enabled target, sharing checks already in progress, and a collapsed **Recent events** list shows the last 20 trigger/cancel events.

The same actions are available over the API. All writes require the `X-CSRF-Token` header; see [API & Webhooks](api-and-webhooks.md#api).

```bash
# Current state, probes, policy, and recent events
curl -s http://your-server:8000/api/v1/failover \
  -H "Cookie: $COOKIE"

# Manually fail Provider A over, with an optional reason
curl -s -X POST http://your-server:8000/api/v1/failover/llm-a/trigger \
  -H "Cookie: $COOKIE" -H "X-CSRF-Token: $CSRF" \
  -H "Content-Type: application/json" \
  -d '{"reason": "testing the standby provider"}'

# Manually cancel it
curl -s -X POST http://your-server:8000/api/v1/failover/llm-a/cancel \
  -H "Cookie: $COOKIE" -H "X-CSRF-Token: $CSRF"

# Run every enabled health probe now
curl -s -X POST http://your-server:8000/api/v1/failover/probe \
  -H "Cookie: $COOKIE" -H "X-CSRF-Token: $CSRF"
```

Malformed trigger bodies and non-string reasons return HTTP 400. If a state change cannot be saved, trigger and cancel return `503 failover_transition_failed`. Repeating an already-applied action succeeds without duplicating its event.

A manual trigger on a target with no failover configured returns `409 failover_not_configured`. Triggering an already-active automatic failover by hand upgrades it to manual, so it no longer auto-recovers on healthy probes; cancelling with `source=manual` (the API default) clears either kind.

`POST /api/v1/settings/providers/failover/test-connection` and `POST /api/v1/settings/providers/failover-whisper/test-connection` validate the saved (or not-yet-saved, via the request body) failover configuration before you rely on it, the same way the existing Provider B and Whisper test-connection routes do. See [API & Webhooks](api-and-webhooks.md#api) for the full route list, including `GET /api/v1/settings/models?slot=failover` for the failover model catalog.

## Configuration reference

All settings are under `PUT /api/v1/settings/ad-detection`; database-only, no environment variable backing.

**LLM failover** (`failoverLlmEnabled`, default off):

| Payload key | Kind / range | Default |
|---|---|---|
| `failoverLlmEnabled` | bool | `false` |
| `failoverLlmProvider` | `anthropic`, `openrouter`, `openai-compatible`, or `ollama` | unset |
| `failoverLlmBaseUrl` | string (openai-compatible/ollama only) | `http://localhost:8000/v1` |
| `failoverLlmApiKey` | secret; write-only, omit to leave unchanged | - |
| `failoverLlmTimeoutSeconds` | int 10-3600, or blank | blank (provider-type default) |
| `failoverLlmMaxRetries` | int 0-10, or blank | blank (provider-type default) |
| `failoverLlmDetectionModel` | string | - |
| `failoverLlmReviewModel` | string, blank inherits the detection model | - |
| `failoverLlmVerificationModel` | string, blank inherits the detection model | - |
| `failoverLlmChaptersModel` | string, blank inherits the detection model | - |

`GET /api/v1/settings` reports `failoverLlmApiKeyConfigured` (boolean) instead of the key. Failover is considered configured once it is enabled, a provider type is set, and a detection model is set.

**Transcriber failover** (`failoverWhisperEnabled`, default off):

| Payload key | Kind / range | Default |
|---|---|---|
| `failoverWhisperEnabled` | bool | `false` |
| `failoverWhisperBackend` | `local` or `openai-api` | `openai-api` |
| `failoverWhisperModel` | string (local backend) | - |
| `failoverWhisperApiBaseUrl` | string (API backend) | - |
| `failoverWhisperApiKey` | secret; write-only, omit to leave unchanged | - |
| `failoverWhisperApiModel` | string | `whisper-1` |
| `failoverWhisperApiTimeoutSeconds` | int 30-3600 | `600` |
| `failoverWhisperMaxAttempts` | int 1-10 or null; blank inherits `whisperMaxAttempts` | null |
| `failoverWhisperLanguage` | string, blank matches the active transcriber's language | - |

Local standby requires its switch enabled and the local Whisper stack available. API standby requires its switch enabled and a base URL. Disabling or clearing a standby configuration sends subsequent calls to the original transcriber, even if its failover state remains active.

**Policy:**

| Payload key | Kind / range | Default |
|---|---|---|
| `failoverProbeIntervalMinutes` | int 1-60 | `5` |
| `failoverRecoveryProbes` | int 1-10 | `3` |

Every numeric field above must be a JSON integer, or `null`/omitted where blank is meaningful; a numeric string (`"30"`) is rejected with a 400. An invalid field in a settings update is rejected without partially saving the rest of that request.

## Webhook events

| Event | Fires when |
|---|---|
| `Failover Triggered` | A target switched to its failover configuration, automatically or by hand |
| `Failover Cancelled` | A target switched back to its own configuration |

Both carry `target` (`llm-a`, `llm-b`, or `transcriber`), `source` (`auto`, `probe`, or `manual`), and `reason` (free text, empty on a manual cancel), and share the same 5-minute per-target dedup as the other alert events. See [API & Webhooks > Events](api-and-webhooks.md#events) for the full payload shape.

## Per-provider timeouts and retries

Provider A, Provider B, and the LLM standby provider each have their own request timeout and max-retries setting (`providerATimeoutSeconds`/`providerAMaxRetries`, `providerBTimeoutSeconds`/`providerBMaxRetries`, `failoverLlmTimeoutSeconds`/`failoverLlmMaxRetries`). Blank falls back to the provider-type default: 120 seconds and 3 retries for Anthropic and OpenRouter, 600 seconds and 2 retries for OpenAI-compatible endpoints and Ollama. A stage that fails over mid-run uses the standby provider's own timeout and retry count on the extra attempt, not Provider A's or Provider B's. See [Configuration > Per-provider timeout and retries](configuration.md#per-provider-timeout-and-retries) for the full table.

On the transcription side, `whisperMaxAttempts` (Settings > Transcription, 1-10, default 2) sets upload attempts per chunk, including the first upload. The standby transcriber inherits it unless `failoverWhisperMaxAttempts` is set. Configure the standby override under Settings > Failover, even when the active transcriber runs locally. Null or blank clears the override; omitting the field leaves it unchanged.

---

[< Docs index](README.md) | [Project README](../README.md)
