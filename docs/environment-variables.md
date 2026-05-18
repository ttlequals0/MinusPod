# Environment Variables

[< Docs index](README.md) | [Project README](../README.md)

---

## Environment Variables

Grouped by how often you'll touch them. **Standard** is what a typical deployment sets; **Security** is the 2.0.0+ hardening surface; **Advanced** are tuning knobs for edge cases; **Optional** are opt-in features.

### Standard

| Variable | Default | Description |
|----------|---------|-------------|
| `ANTHROPIC_API_KEY` | _(none)_ | Claude API key (required when `LLM_PROVIDER=anthropic`, not needed for Ollama) |
| `LLM_PROVIDER` | `anthropic` | LLM backend: `anthropic`, `openrouter`, `openai-compatible`, or `ollama` |
| `OPENROUTER_API_KEY` | _(none)_ | OpenRouter API key (required when `LLM_PROVIDER=openrouter`) |
| `OPENAI_BASE_URL` | `http://localhost:8000/v1` | Base URL for OpenAI-compatible API (only used with non-anthropic providers) |
| `OPENROUTER_BASE_URL` | `https://openrouter.ai/api/v1` | OpenRouter API base URL. Override for private proxies or regional endpoints. |
| `ANTHROPIC_BASE_URL` | _(anthropic default)_ | Anthropic API base URL. Override for private proxies. |
| `OPENAI_API_KEY` | `not-needed` | API key for OpenAI-compatible endpoint (not required for Ollama or local wrappers) |
| `OPENAI_MODEL` | _(none)_ | Model for OpenAI-compatible/Ollama providers. **Required for Ollama** (e.g. `qwen3:14b`). Defaults to `claude-sonnet-4-5-20250929` for openai-compatible if unset. |
| `BASE_URL` | `http://localhost:8000` | Public URL for generated feed links |
| `UI_BASE_URL` | _(falls back to BASE_URL)_ | Public URL for UI links in webhooks (set if UI is on a different domain than feeds) |
| `WHISPER_MODEL` | `small` | Whisper model size. `tiny`, `base`, `small`, `medium`, `large-v3`, `turbo`, plus `.en` variants |
| `WHISPER_DEVICE` | `cuda` | `cuda` or `cpu`. Set to `cpu` when using API backend to skip GPU init. |
| `WHISPER_BACKEND` | `local` | `local` (faster-whisper) or `openai-api` (remote HTTP) |
| `WHISPER_API_BASE_URL` | _(none)_ | Base URL for OpenAI-compatible whisper API |
| `WHISPER_API_KEY` | _(none)_ | API key for whisper API |
| `WHISPER_API_MODEL` | `whisper-1` | Model name sent to whisper API |
| `WHISPER_LANGUAGE` | `en` | ISO 639-1 language code, or `auto`. Seeds fresh installs only; runtime value is in Settings > Transcription. |
| `WHISPER_COMPUTE_TYPE` | `auto` | `auto`, `float16`, `int8_float16`, `int8`, or `float32`. `auto` picks `float16` on CUDA and `int8` on CPU. Seeds fresh installs only; runtime value is in Settings > Transcription. See [GPU Compute Type](transcription.md#gpu-compute-type) for per-GPU recommendations. |
| `VAD_GAP_DETECTION_ENABLED` | `true` | `true` or `false`. Toggles the VAD gap detector, which cuts audio regions Whisper's VAD dropped (sped-up disclaimers, distorted ad tails) that the transcript-based detectors never see. Seeds the DB row on fresh installs; runtime value is at `GET/PUT /api/v1/settings`. Advanced tuning, not surfaced in the UI. |
| `VAD_GAP_START_MIN_SECONDS` | `3.0` | Minimum pre-transcript gap (seconds) at episode start that the VAD detector will cut. Anything shorter is left alone. Seeds fresh installs only. |
| `VAD_GAP_MID_MIN_SECONDS` | `8.0` | Minimum mid-episode untranscribed gap. Standalone mid-gaps still require BOTH signoff-before AND resume-after context to emit; gaps adjacent to a detected ad extend that ad in place regardless of this threshold. Seeds fresh installs only. |
| `VAD_GAP_TAIL_MIN_SECONDS` | `3.0` | Minimum post-transcript gap at episode end that the VAD detector will cut when no postroll marker already covers it. Seeds fresh installs only. |
| `APP_PASSWORD` | _(none)_ | Initial password for web UI (can also be set in Settings > Security) |
| `OLLAMA_API_KEY` | _(none)_ | Ollama Cloud key. Leave unset for local. |
| `PODCAST_INDEX_API_KEY` | _(none)_ | PodcastIndex.org API key for podcast search |
| `PODCAST_INDEX_API_SECRET` | _(none)_ | PodcastIndex.org API secret |
| `LOG_LEVEL` | `INFO` | `DEBUG`, `INFO`, `WARNING`, or `ERROR` |
| `LOG_FORMAT` | `text` | `text` or `json`. JSON output plays nicely with log aggregators (Loki, CloudWatch). |
| `DATA_DIR` | `/app/data` | Data storage directory. Aliases `DATA_PATH` and `MINUSPOD_DATA_DIR` are also honored. |

### Security

| Variable | Default | Description |
|----------|---------|-------------|
| `MINUSPOD_MASTER_PASSPHRASE` | _(unset)_ | Unlocks encrypted provider-key store. Strongly recommended for production; first boot migrates any plaintext rows to `enc:v1:`. Losing it makes stored keys unrecoverable (env fallback still works). |
| `SESSION_COOKIE_SECURE` | `true` | Set to `false` only when serving over plain HTTP. |
| `SESSION_COOKIE_SAMESITE` | `Strict` | Override to `Lax` only if a specific integration breaks. |
| `MINUSPOD_ENABLE_HSTS` | `false` | Set to `true` once the deployment is HTTPS-only. HSTS traps browsers so don't flip this on a dual-protocol setup. |
| `MINUSPOD_TRUSTED_PROXY_COUNT` | `0` | Reverse-proxy hops to trust when reading `X-Forwarded-For`. `1` behind Cloudflare / cloudflared / nginx / Traefik, higher for a multi-proxy chain. **Leaving this at `0` behind a proxy breaks login lockout** (the proxy IP is private/loopback, which the lockout excludes) and per-IP rate limits (they key on the proxy instead of the client); audit logs + auth-failure webhooks also carry the wrong IP. Startup logs a WARN when unset. |

### Advanced

| Variable | Default | Description |
|----------|---------|-------------|
| `PROCESSING_SOFT_TIMEOUT` | `3600` | Seconds before a stuck job is auto-cleared. Seeds fresh installs; runtime value lives in Settings > Transcription. |
| `PROCESSING_HARD_TIMEOUT` | `7200` | Seconds before the processing lock is force-released. Must exceed the soft timeout. |
| `AD_DETECTION_MAX_TOKENS` | `4096` | Max tokens for LLM ad detection responses. |
| `REVIEW_MAX_TOKENS` | `4096` | Max tokens for the opt-in ad reviewer's per-ad JSON response. |
| `MINUSPOD_MAX_ARTWORK_BYTES` | `5242880` (5 MB) | Cap on podcast artwork download size. Clamped to `[65536, 52428800]`. |
| `MINUSPOD_MAX_RSS_BYTES` | `209715200` (200 MB) | Cap on RSS response body size. Floor is 1 MB. |
| `RATE_LIMIT_STORAGE_URI` | `memory://` | Flask-limiter storage backend. Default is per-worker; set to `redis://host:6379` + run a Redis sidecar for exact declared limits across workers. |
| `APP_UID` | `1000` | UID gunicorn runs as inside the container. Override to match host volume ownership. |
| `APP_GID` | `1000` | GID counterpart to `APP_UID`. |
| `GUNICORN_WORKERS` | `2` | Worker count. Lower means single-threaded UI blocking during RSS refresh; higher multiplies per-worker rate-limit counters (when using `memory://`). |
| `GUNICORN_TIMEOUT` | `600` | Per-request hard timeout. |
| `GUNICORN_GRACEFUL_TIMEOUT` | `330` | Seconds between SIGTERM and SIGKILL on shutdown. |
| `SECRET_KEY` | _(auto-generated)_ | Flask session signing key. If unset, a random value is generated on first boot and persisted at `$DATA_DIR/.secret_key`. Set explicitly only for multi-instance deployments sharing a session store. Rotating invalidates all existing sessions. |
| `SESSION_LIFETIME_HOURS` | `24` | How long authenticated sessions stay valid, in hours. |

### Optional

| Variable | Default | Description |
|----------|---------|-------------|
| `TUNNEL_TOKEN` | _(none)_ | Cloudflare tunnel token. See Remote Access / Security > Before enabling the tunnel profile. |
| `SENTRY_DSN` | _(none)_ | Opt-in Sentry. Requires `sentry-sdk` installed. Headers, cookies, CSRF tokens, and credential-like query params are scrubbed before send; no performance tracing. |
| `MINUSPOD_RELEASE` | _(none)_ | Optional release tag forwarded to Sentry. |
| `SENTRY_ENVIRONMENT` | `production` | Environment tag forwarded to Sentry. |

### Deprecated

| Variable | Description |
|----------|-------------|
| `RETENTION_PERIOD` | Legacy minutes-based retention. Auto-converted to days on first startup. Use Settings UI or `PUT /api/v1/settings/retention` instead. |

---

[< Docs index](README.md) | [Project README](../README.md)
