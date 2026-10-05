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

Failover is a standby account that takes over for one of three targets when the active one stops answering:

- **Provider A** (`llm-a`) - the main LLM provider.
- **Provider B** (`llm-b`) - the optional second LLM provider, when enabled.
- **Transcriber** (`transcriber`) - the active Whisper backend.

There is one shared LLM failover provider, used by whichever of Provider A or Provider B is in trouble, and one failover transcriber. Each has its own type, endpoint, key, timeout, retries, and (for the LLM side) its own models per pipeline stage.

Failover is different from the Provider A / Provider B slot a stage is configured to use (see [LLM Providers > Per-Stage Providers](llm-providers.md#per-stage-providers)). Picking Provider A or Provider B for a stage is routing, decided ahead of time and only re-evaluated when you change the setting. Failover moves work at runtime, without touching that setting, and moves it back once the active account is healthy again.

## What triggers it

| Condition | Triggers failover | Notes |
|---|---|---|
| Connection refused, DNS failure, timeout, open circuit breaker | Yes | |
| HTTP 408 (request timeout) or 5xx response | Yes | |
| 401 or 403, invalid/expired key or a spend/quota limit | Yes | |
| 402, or a 400 the provider marks as a credit/quota exhaustion | Yes | |
| 404 (model or resource not found) | Yes | |
| Provider 429 (rate limit) or exhausted daily quota | Yes | The standby provider has independent capacity. |
| Operator-configured RPM/RPD/TPM cap | No | Manual caps remain enforced. |
| 400 or 422 that is not a quota rejection (bad request, bad parameters) | No | Handled by retrying with fallback parameters, not by switching providers. |
| A token request exceeds the provider's per-request or per-minute cap | No | Reduce the window size or requested output budget. |

Real provider throttling and exhausted provider quotas can use the configured standby. Manual caps and structurally oversized requests cannot. If the standby also reports a rate limit, the existing [Rate-Limit Hold](configuration.md#rate-limit-hold) behavior applies to that account.

The transcriber side uses the same shape: connection errors and 5xx-equivalent backend outages trigger failover, and so do 401/402/403/404 and exhausted 429 retries from an API backend (`TranscriptionRejectedError`), exhausted HTTP 408 timeout retries, and a local backend's model-load failure. Other 4xx responses from an API backend are left alone, same as before this feature.

## What happens mid-run

**LLM calls.** A call that exhausts its normal retry ladder on a trigger error is retried once on the failover provider. That retry runs the failover provider's full retry ladder, with its own model for the pipeline phase, its own timeout, and its own retry count. Later calls in the same run follow immediately, because failover state is checked live, not just at run start. If the standby attempt also fails, its error determines deferral or retry. The original provider error is retained as context for diagnostics.

**Transcription.** The behavior differs by whether the failover transcriber is the same kind of backend as the active one:

- **API to API**: only the chunks that have not finished yet rerun on the failover settings; chunks the first pass already finished are kept.
- **Any switch with a local backend on either side** (local to local, local to API, or API to local): the partial results from the first pass are discarded and the whole episode reruns on the failover backend.
- **Short episodes** that never reach the chunk plan (single-shot transcription) switch the same way: the failed attempt reruns on the failover config, either as a single call (same backend type) or by rerunning the whole episode through the other backend's chunking path.

If the failover attempt also fails, the error propagates as it did before this feature: into the offline-queue deferral (when enabled) or the normal retry ladder.

Both down at once is not a new state: it is the existing behavior for an unreachable account, now reached after the failover attempt has also been tried and failed.

## Health probes

A background tick checks every enabled target on an interval (**Probe interval**, `failoverProbeIntervalMinutes`, 1-60 minutes, default 5): an LLM probe lists models on the provider's catalog endpoint (or the fixed Anthropic/OpenRouter endpoint); an API transcriber probe requests its `/models` endpoint. Any answer other than a 5xx, 401, or 403 counts as up, because many Whisper servers have no `/models` route. A local transcriber probe checks that the local Whisper stack is installed. None of them sends a real completion or transcription request.

- **Two consecutive failed probes** of an *active* target (Provider A, Provider B, or the transcriber, not a failover account itself) trigger failover automatically.
- **N consecutive healthy probes** of the original account (**Recovery probes**, `failoverRecoveryProbes`, 1-10, default 3) cancel an *automatic* failover and switch back. Only probes taken after the failover started count.
- A **manual** failover is never cancelled by probes. It stays active until you cancel it.
- A run-time trigger error (an actual failed call, not a probe) triggers failover immediately, without waiting for two failed probes.
- A local transcriber probe only checks that the local Whisper stack is importable, so it reads healthy even when a model load or GPU error breaks the local runtime. An automatic failover caused by such an error switches back after the configured number of healthy probes, then triggers again if the error recurs.

Before a run starts, MinusPod probes any target it is about to use whose last probe result is older than the probe interval, so a run never starts on stale health data. This happens once per run, not once per queue item, and does not probe a target that was already checked recently.

## Manual control

The Failover card (Settings > AI & Processing > Failover) shows a status row per target: badge (**Healthy**, **Failed over**, **Unprobed**, or **Not configured**), last probe time, and, while active, the source (automatic, probe, or manual) and reason, plus a **Trigger** or **Cancel** button. A **Probe now** button in the header runs every enabled probe immediately, and a collapsed **Recent events** list shows the last 20 trigger/cancel events.

The same actions are available over the API. All writes require the `X-CSRF-Token` header; see [API & Webhooks](api-and-webhooks.md#api).

```bash
# Current state, probes, policy, and recent events
curl -s http://your-server:8000/api/v1/failover \
  -H "Cookie: $COOKIE"

# Manually fail Provider A over, with an optional reason
curl -s -X POST http://your-server:8000/api/v1/failover/llm-a/trigger \
  -H "Cookie: $COOKIE" -H "X-CSRF-Token: $CSRF" \
  -H "Content-Type: application/json" \
  -d '{"reason": "testing the failover provider"}'

# Manually cancel it
curl -s -X POST http://your-server:8000/api/v1/failover/llm-a/cancel \
  -H "Cookie: $COOKIE" -H "X-CSRF-Token: $CSRF"

# Run every enabled health probe now
curl -s -X POST http://your-server:8000/api/v1/failover/probe \
  -H "Cookie: $COOKIE" -H "X-CSRF-Token: $CSRF"
```

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

A local-backend failover transcriber is considered configured once enabled; an API-backend one additionally needs its base URL set.

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

Provider A, Provider B, and the LLM failover provider each have their own request timeout and max-retries setting (`providerATimeoutSeconds`/`providerAMaxRetries`, `providerBTimeoutSeconds`/`providerBMaxRetries`, `failoverLlmTimeoutSeconds`/`failoverLlmMaxRetries`). Blank falls back to the provider-type default: 120 seconds and 3 retries for Anthropic and OpenRouter, 600 seconds and 2 retries for OpenAI-compatible endpoints and Ollama. A stage that fails over mid-run uses the failover provider's own timeout and retry count on the extra attempt, not Provider A's or Provider B's. See [Configuration > Per-provider timeout and retries](configuration.md#per-provider-timeout-and-retries) for the full table.

On the transcription side, `whisperMaxAttempts` (Settings > Transcription, 1-10, default 2) sets upload attempts per chunk, including the first upload. The standby transcriber inherits it unless `failoverWhisperMaxAttempts` is set. Configure the standby override under Settings > Failover, even when the active transcriber runs locally. Null or blank clears the override; omitting the field leaves it unchanged.

---

[< Docs index](README.md) | [Project README](../README.md)
