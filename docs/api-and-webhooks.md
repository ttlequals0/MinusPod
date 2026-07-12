# API & Webhooks

[< Docs index](README.md) | [Project README](../README.md)

---

## API

REST API available at `/api/v1/`. Interactive docs at `/api/v1/docs`. Full specification: [`openapi.yaml`](../openapi.yaml).

Write requests (`POST`, `PUT`, `DELETE`) require an `X-CSRF-Token` header matching the `minuspod_csrf` cookie. The built-in UI sends it for you; an external client has to read that cookie and echo it back on each write.

Key endpoints:
- `GET /api/v1/health` - Readiness check (database, storage); returns 503 if either is down
- `GET /api/v1/health/live` - Liveness probe (process up); always 200, safe for frequent polling
- `GET /api/v1/feeds` - List all feeds
- `POST /api/v1/feeds` - Add a new feed (supports `maxEpisodes` for RSS cap, `onlyExposeProcessedEpisodes` to hide unprocessed episodes from the served feed)
- `POST /api/v1/feeds/import-opml` - Import feeds from OPML file
- `GET /api/v1/feeds/export-opml?mode=original|modified` - Export feeds as OPML (original or ad-free URLs)
- `POST /api/v1/feeds/refresh-artwork` - Re-render every feed's cover art (used after toggling the cover-art badge or swapping the badge asset)
- `GET /api/v1/podcast-search?q=query` - Search podcasts via PodcastIndex.org
- `GET /api/v1/feeds/{slug}/episodes` - List episodes (supports `sort_by`, `sort_dir`, `status` filter, pagination)
- `POST /api/v1/feeds/{slug}/episodes/bulk` - Bulk episode actions (process, reprocess, reprocess_full, reprocess_llm, delete)
- `GET /api/v1/feeds/{slug}/episodes/{id}` - Get episode detail with ad markers and transcript
- `POST /api/v1/episodes/{slug}/{id}/reprocess` - Reprocess an episode (body `mode`: reprocess/full/llm/recut; `llm` re-detects on the existing transcript and `recut` re-cuts from the saved ad list, both skipping transcription). See [Reprocessing](configuration.md#reprocessing) for the full mode reference. The older `POST /api/v1/feeds/{slug}/episodes/{id}/reprocess` ignores `mode` and always runs a full reprocess.
- `POST /api/v1/feeds/{slug}/episodes/{id}/cancel` - Cancel processing for a stuck episode
- `POST /api/v1/feeds/{slug}/episodes/{id}/regenerate-chapters` - Regenerate chapter markers
- `POST /api/v1/feeds/{slug}/reprocess-all` - Batch reprocess all episodes
- `GET /api/v1/feeds/{slug}/ad-distribution` - Histogram of where ads have historically been cut across the feed's episodes, with learned prior zones. Informational; powers the feed detail Ad Distribution panel and is independent of the learned-positions experiment toggle.
- `POST /api/v1/feeds/{slug}/episodes/{id}/retry-ad-detection` - Retry ad detection only
- `POST /api/v1/feeds/{slug}/episodes/{id}/corrections` - Submit ad corrections
- `GET/POST /api/v1/feeds/{slug}/cue-templates` - List a feed's audio-cue templates, or mark a new one from a window of an episode's original audio (`episodeId`, `startS`, `endS`, `cueType`; 0.2 to 10 seconds, up to 60 for show intro/outro)
- `PATCH/DELETE /api/v1/cue-templates/{id}` - Enable/disable, change scope (`podcast` or `network`), set a per-template match threshold (`scoreThreshold`, 0.30-0.99, null clears), move the capture window (`sourceOffsetS`/`durationS`; re-extracts the audio blobs from the retained original, 409 when it has aged out), or delete a template
- `GET /api/v1/cue-templates/{id}/export` - Download a template as a portable zip (lossless WAV plus JSON manifest)
- `POST /api/v1/feeds/{slug}/cue-templates/import` - Import a template zip into a feed (multipart `file`); the MFCC is recomputed from the WAV, sample-rate or channel mismatches are rejected. The manifest carries a `schemaVersion` field that is reserved for a future breaking change; this release only checks that it parses and does not gate or migrate on it.
- `GET /api/v1/feeds/{slug}/episodes/{id}/cue-loud-spots` - Template-free energy pass over an episode's original audio; returns candidate "loud spots" the capture UI marks as jump points
- `GET /api/v1/feeds/{slug}/episodes/{id}/cue-candidates` - Find-audio-cues scan: recurring in-episode stings (speech-like ones dropped) plus intros and outros shared across the feed (powers the Find audio cues button)
- `POST /api/v1/feeds/{slug}/episodes/{id}/cue-candidates/dismiss` - Dismiss a candidate sound feed-wide (`start_s`, `end_s`, optional `label`; spans over 120 seconds are rejected). Stores the span's fingerprint from the retained original; future candidate scans suppress matching sounds
- `GET /api/v1/feeds/{slug}/cue-candidate-dismissals` - List the feed's dismissed sounds, newest first
- `DELETE /api/v1/cue-candidate-dismissals/{id}` - Undo a dismissal; the sound becomes suggestible again
- `POST /api/v1/feeds/{slug}/episodes/{id}/cue-scan` - Diagnostic: run every enabled template against an episode and return per-template peak scores and match times (optional `scoreThreshold` override)
- `POST /api/v1/feeds/{slug}/episodes/{id}/cue-template-preview` - Run a single template (`templateId`) against an episode
- `POST /api/v1/feeds/{slug}/cue-cross-episode-scan` - Full-body cross-episode scan for recurring segments (`episodeIds`, 2-5; the first sets the coordinate frame). Poll with the same body; `rescan: true` forces a fresh run
- `POST /api/v1/feeds/{slug}/cue-templates/{id}/optimize-window` - Sweep start/end trims (up to 0.5s each way, 0.1s steps) for the window with the best mean match score across the source episode and up to 4 siblings; 409 when the source original has aged out
- `GET /api/v1/detections` - List ad detections across all feeds with status filter (`needs_review`, `pending`, `rejected`, `accepted`, `all`; default `needs_review`), optional podcast slug (`feed`), free-text search (`q`), sort (`date`, `confidence`, `podcast`), order (`asc`, `desc`), and pagination (`page`, `limit` 1-100, default 20). Powers the Patterns > Ad Review tab.
- `GET /api/v1/patterns` - List ad patterns (filter by scope)
- `GET /api/v1/patterns/stats` - Pattern database statistics
- `GET /api/v1/sponsors` - List/create/update/delete sponsors (full CRUD)
- `GET /api/v1/search?q=query` - Full-text search across all content
- `GET /api/v1/episodes/processing` - List episodes currently processing
- `GET /api/v1/history` - Processing history with pagination and export
- `GET /api/v1/stats/dashboard` - Aggregate stats (avg/min/max time saved, ads, cost, tokens) with optional podcast filter
- `GET /api/v1/stats/by-day` - Episodes processed by day of week
- `GET /api/v1/stats/by-podcast` - Per-podcast stats (ads, time saved, tokens, cost)
- `GET /api/v1/status` - Current processing status
- `GET /api/v1/status/stream` - SSE endpoint for real-time status updates
- `GET /api/v1/system/token-usage` - LLM token usage and cost breakdown by model
- `GET /api/v1/system/model-pricing` - All known LLM model pricing rates
- `POST /api/v1/system/model-pricing/refresh` - Force refresh pricing from provider source
- `GET /api/v1/system/queue` - Auto-process queue status
- `POST /api/v1/system/vacuum` - Trigger SQLite VACUUM to reclaim disk space
- `GET /api/v1/system/backup` - Download SQLite database backup
- `POST /api/v1/system/db-backup/run` - Run a scheduled-style backup now, writing a plain SQLite snapshot to the configured destination (rate-limited to 6/hour; 409 if one is already running)
- `GET/PUT /api/v1/settings/db-backup` - Get or update scheduled backup settings (`enabled`, `cron`, `dest`, `keepCount`)
- `GET /api/v1/settings` - Get current settings (includes LLM provider, API key status)
- `GET/PUT /api/v1/settings/retention` - Get or update retention configuration. `retentionDays` controls how long the processed audio survives; `originalRetentionDays` (added in 2.5.14) controls the pre-cut original separately. Server clamps `originalRetentionDays` to `retentionDays` on save.
- `GET/PUT /api/v1/settings/audio` - Toggle whether originals are kept for ad editor review (`keepOriginalAudio`)
- `GET/PUT /api/v1/settings/processing-timeouts` - Soft and hard processing timeouts in seconds
- `GET /api/v1/feeds/{slug}/episodes/{id}/original.mp3` - Stream the retained pre-cut audio (used by ad editor Review mode)
- `PUT /api/v1/settings/ad-detection` - Update ad detection config (model, provider, prompts)
- `GET /api/v1/settings/models` - List available AI models from current provider
- `POST /api/v1/settings/models/refresh` - Force refresh model list from provider
- `GET/POST/PUT/DELETE /api/v1/settings/webhooks` - Webhook CRUD
- `POST /api/v1/settings/webhooks/{id}/test` - Fire test webhook
- `POST /api/v1/settings/webhooks/validate-template` - Validate and preview a payload template
- `GET/PUT /api/v1/settings/notifications/email` - Email notification settings
- `POST /api/v1/settings/notifications/email/test` - Send a test email

## Notifications

MinusPod can notify you when episodes complete processing, permanently fail, or when the LLM provider rejects requests (bad credentials, exhausted spend limits, oversized requests). Two channels share the same events: webhooks (HTTP POST to any endpoint) and native email through your own SMTP server. Configure both in **Settings > Notifications** in the web UI, or via the REST API.

## Webhooks

Webhooks fire an HTTP POST to configured URLs. Works with any HTTP endpoint. Use a custom Jinja2 payload template to match the receiver's expected format.

### Events

| Event | Fires when |
|---|---|
| `Episode Processed` | Episode completes processing successfully |
| `Episode Failed` | Episode reaches permanently failed status |
| `Auth Failure` | LLM provider rejects the API key as invalid or expired (401/403 without billing markers; rate-limited to one per 5 minutes) |
| `Limit Exceeded` | LLM provider rejects a request because a spend or usage limit is exhausted: a monthly key limit (OpenRouter 403), out of credits (402, Anthropic low balance), or OpenAI `insufficient_quota` (rate-limited to one per 5 minutes). The key is valid; add credits or raise the limit, then reprocess the episode (it is marked permanently failed rather than retried). |
| `Rate Limit Structural` | A single detection-window request exceeds the provider's per-minute token cap (rate-limited to one per 5 minutes). Retrying will not help; the operator needs to shrink the detection window or move to a higher tier. |

### Template Variables

Custom payload templates are Jinja2 strings rendered against these variables:

| Variable | Type | Description |
|---|---|---|
| `event` | string | `Episode Processed`, `Episode Failed`, `Auth Failure`, `Limit Exceeded`, or `Rate Limit Structural` |
| `timestamp` | string | ISO 8601 UTC timestamp |
| `podcast.name` | string | Podcast title (falls back to slug if unavailable) |
| `podcast.slug` | string | Feed slug |
| `episode.id` | string | Episode ID |
| `episode.title` | string | Episode title |
| `episode.slug` | string | Feed slug |
| `episode.url` | string | Full UI URL to episode |
| `episode.ads_removed` | int | Number of ads removed |
| `episode.processing_time_secs` | float | Processing duration in seconds |
| `episode.processing_time` | string | Processing duration formatted as M:SS or H:MM:SS |
| `episode.llm_cost` | float | LLM cost in USD |
| `episode.llm_cost_display` | string | LLM cost formatted as $X.XX |
| `episode.time_saved_secs` | float/null | Seconds of audio removed |
| `episode.time_saved` | string/null | Time saved formatted as M:SS or H:MM:SS |
| `episode.error_message` | string/null | Error message (failed events only) |
| `test` | bool | `true` only on test webhook fires; absent on real events |

**Auth Failure events use a different payload:**

| Variable | Type | Description |
|---|---|---|
| `event` | string | `Auth Failure` |
| `timestamp` | string | ISO 8601 UTC timestamp |
| `provider` | string | LLM provider name (anthropic, openrouter, etc.) |
| `model` | string | Model that failed authentication |
| `error_message` | string | Error details from the provider |
| `status_code` | int/null | HTTP status code (401 or 403) |

**Limit Exceeded events use a different payload:**

| Variable | Type | Description |
|---|---|---|
| `event` | string | `Limit Exceeded` |
| `timestamp` | string | ISO 8601 UTC timestamp |
| `provider` | string | LLM provider name (openrouter, openai, etc.) |
| `model` | string | Model the rejected request targeted |
| `error_message` | string | Error details from the provider |
| `status_code` | int/null | HTTP status code (402, 403, 429, or 400 depending on provider) |

**Rate Limit Structural events use a different payload:**

| Variable | Type | Description |
|---|---|---|
| `event` | string | `Rate Limit Structural` |
| `timestamp` | string | ISO 8601 UTC timestamp |
| `provider` | string | LLM provider name |
| `model` | string | Model that returned the 429 |
| `limit` | int | The provider's per-minute token cap |
| `used` | int | Tokens already consumed in the current minute |
| `requested` | int | Tokens this request asked for (greater than `limit` means the request structurally cannot fit) |
| `error_message` | string | Raw error details from the provider |

### Default Payloads

When no custom template is configured, MinusPod sends these JSON payloads.

**Episode Processed:**

```json
{
  "event": "Episode Processed",
  "timestamp": "2026-04-12T00:15:42Z",
  "podcast": {
    "name": "My Favorite Podcast",
    "slug": "my-favorite-podcast"
  },
  "episode": {
    "id": "a1b2c3d4e5f6",
    "title": "Episode 42: The Answer",
    "slug": "my-favorite-podcast",
    "url": "http://your-server:8000/ui/feeds/my-favorite-podcast/episodes/a1b2c3d4e5f6",
    "ads_removed": 3,
    "processing_time_secs": 42.5,
    "processing_time": "0:42",
    "llm_cost": 0.0035,
    "llm_cost_display": "$0.00",
    "time_saved_secs": 187.0,
    "time_saved": "3:07",
    "error_message": null
  }
}
```

**Episode Failed:**

```json
{
  "event": "Episode Failed",
  "timestamp": "2026-04-12T00:15:42Z",
  "podcast": {
    "name": "My Favorite Podcast",
    "slug": "my-favorite-podcast"
  },
  "episode": {
    "id": "a1b2c3d4e5f6",
    "title": "Episode 42: The Answer",
    "slug": "my-favorite-podcast",
    "url": "http://your-server:8000/ui/feeds/my-favorite-podcast/episodes/a1b2c3d4e5f6",
    "ads_removed": 0,
    "processing_time_secs": 12.3,
    "processing_time": "0:12",
    "llm_cost": 0.001,
    "llm_cost_display": "$0.00",
    "time_saved_secs": null,
    "time_saved": null,
    "error_message": "Transcription failed: audio file is corrupt or unsupported format"
  }
}
```

**Auth Failure:**

```json
{
  "event": "Auth Failure",
  "timestamp": "2026-04-12T00:15:42Z",
  "provider": "anthropic",
  "model": "claude-sonnet-4-20250514",
  "error_message": "Invalid API key provided",
  "status_code": 401
}
```

**Limit Exceeded:**

```json
{
  "event": "Limit Exceeded",
  "timestamp": "2026-04-12T00:15:42Z",
  "provider": "openrouter",
  "model": "anthropic/claude-sonnet-4",
  "error_message": "Key limit exceeded (monthly limit). Manage it using https://openrouter.ai/settings/keys",
  "status_code": 403
}
```

**Rate Limit Structural:**

```json
{
  "event": "Rate Limit Structural",
  "timestamp": "2026-04-12T00:15:42Z",
  "provider": "groq",
  "model": "llama-3.3-70b-versatile",
  "limit": 6000,
  "used": 2400,
  "requested": 8500,
  "error_message": "rate_limit_exceeded: Request too large for model on tokens per minute"
}
```

## Email notifications

Point MinusPod at an SMTP server and it emails you for the events you pick. Community webhook-to-email sidecars like minuspod-webhook-mailer are no longer needed. One configuration: SMTP host, port, security (None, STARTTLS, or SSL/TLS), optional username and password, a from address, and a comma-separated recipient list. The password is stored encrypted like provider API keys, so saving one needs `MINUSPOD_MASTER_PASSPHRASE` set.

Emails are HTML with the MinusPod logo embedded inline (no external image fetch) and a plain-text fallback part for text-only clients. Each event renders a subject like `[MinusPod] Episode Failed: My Show - Episode 42` with a short table of facts and, for alert events, the action to take. Alert events (`Auth Failure`, `Limit Exceeded`, `Rate Limit Structural`) keep their 5-minute dedup window, shared with webhooks, so a burst of failures produces one email. The webhook Test button never emails; the email form has its own **Send test email** button that delivers a real message through the saved settings.

By default the failure and alert events are checked and `Episode Processed` is not, so a working setup stays quiet. SMTP sending runs with a 10 second timeout in a background thread; a down mail server never blocks or fails episode processing.

### Example: Pushover

Pushover supports native webhook ingestion with data extraction selectors. No custom payload template needed. MinusPod's default JSON payload works directly.

1. Log in to [pushover.net/dashboard](https://pushover.net/dashboard), scroll to "Your Webhooks", click "Create a Webhook". Name it MinusPod.
2. Copy the unique webhook URL.
3. In MinusPod Settings > Webhooks: paste the URL, select events, **leave payload template blank**.
4. Click Test in MinusPod to fire a sample payload to Pushover.
5. In Pushover dashboard: click "Check for Update" in Last Payload to load MinusPod's JSON.
6. Configure data extraction selectors:

| Field | Selector |
|---|---|
| Title | `{{podcast.name}} - {{event}}` |
| Body | `{{episode.title}}`<br>`{{episode.ads_removed}} ads removed. Saved {{episode.time_saved}}. Cost {{episode.llm_cost_display}}` |
| URL | `{{episode.url}}` |
| URL Title | `Open in MinusPod` |

7. Click "Test Selectors on Last Payload" to preview, then Save.

> Pushover's `{{...}}` selector syntax is evaluated on Pushover's side; these are not Jinja2 templates.

### Example: ntfy

ntfy requires a custom payload template to match its expected JSON format.

1. Self-hosted or ntfy.sh: set your topic name
2. Add a webhook in Settings > Webhooks:
   - **URL:** `https://ntfy.sh/your-topic` (or your self-hosted instance)
   - **Payload template:**
     ```json
     {
       "topic": "your-topic",
       "title": "{{ podcast.name }} - {{ episode.title }}",
       "message": "Removed {{ episode.ads_removed }} ads in {{ episode.processing_time }}. Cost {{ episode.llm_cost_display }}",
       "actions": [{"action": "view", "label": "Open Episode", "url": "{{ episode.url }}"}]
     }
     ```

> ntfy also supports header-based delivery (`X-Title`, `X-Message`, `X-Click` headers with plain text body); either approach works with MinusPod's template system.

### Request Signing

If a webhook has a secret configured, MinusPod adds an `X-MinusPod-Signature: sha256=<hmac>` header to each POST, computed with HMAC-SHA256 over the request body.

---

[< Docs index](README.md) | [Project README](../README.md)
