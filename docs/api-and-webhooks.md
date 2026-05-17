# API & Webhooks

[< Docs index](README.md) | [Project README](../README.md)

---

## API

REST API available at `/api/v1/`. Interactive docs at `/api/v1/docs`. Full specification: [`openapi.yaml`](../openapi.yaml).

Key endpoints:
- `GET /api/v1/health` - Readiness check (database, storage); returns 503 if either is down
- `GET /api/v1/health/live` - Liveness probe (process up); always 200, safe for frequent polling
- `GET /api/v1/feeds` - List all feeds
- `POST /api/v1/feeds` - Add a new feed (supports `maxEpisodes` for RSS cap, `onlyExposeProcessedEpisodes` to hide unprocessed episodes from the served feed)
- `POST /api/v1/feeds/import-opml` - Import feeds from OPML file
- `GET /api/v1/feeds/export-opml?mode=original|modified` - Export feeds as OPML (original or ad-free URLs)
- `GET /api/v1/podcast-search?q=query` - Search podcasts via PodcastIndex.org
- `GET /api/v1/feeds/{slug}/episodes` - List episodes (supports `sort_by`, `sort_dir`, `status` filter, pagination)
- `POST /api/v1/feeds/{slug}/episodes/bulk` - Bulk episode actions (process, reprocess, reprocess_full, delete)
- `GET /api/v1/feeds/{slug}/episodes/{id}` - Get episode detail with ad markers and transcript
- `POST /api/v1/feeds/{slug}/episodes/{id}/reprocess` - Force reprocess (supports `mode`: reprocess/full)
- `POST /api/v1/feeds/{slug}/episodes/{id}/cancel` - Cancel processing for a stuck episode
- `POST /api/v1/feeds/{slug}/episodes/{id}/regenerate-chapters` - Regenerate chapter markers
- `POST /api/v1/feeds/{slug}/reprocess-all` - Batch reprocess all episodes
- `POST /api/v1/feeds/{slug}/episodes/{id}/retry-ad-detection` - Retry ad detection only
- `POST /api/v1/feeds/{slug}/episodes/{id}/corrections` - Submit ad corrections
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
- `GET /api/v1/settings` - Get current settings (includes LLM provider, API key status)
- `GET/PUT /api/v1/settings/retention` - Get or update retention configuration (days, enabled/disabled)
- `GET/PUT /api/v1/settings/audio` - Toggle whether originals are kept for ad editor review (`keepOriginalAudio`)
- `GET/PUT /api/v1/settings/processing-timeouts` - Soft and hard processing timeouts in seconds
- `GET /api/v1/feeds/{slug}/episodes/{id}/original.mp3` - Stream the retained pre-cut audio (used by ad editor Review mode)
- `PUT /api/v1/settings/ad-detection` - Update ad detection config (model, provider, prompts)
- `GET /api/v1/settings/models` - List available AI models from current provider
- `POST /api/v1/settings/models/refresh` - Force refresh model list from provider
- `GET/POST/PUT/DELETE /api/v1/settings/webhooks` - Webhook CRUD
- `POST /api/v1/settings/webhooks/{id}/test` - Fire test webhook
- `POST /api/v1/settings/webhooks/validate-template` - Validate and preview a payload template

## Webhooks

MinusPod fires an HTTP POST to configured URLs when episodes complete processing, permanently fail, or when LLM authentication fails. Works with any HTTP endpoint. Use a custom Jinja2 payload template to match the receiver's expected format.

Configure webhooks in **Settings > Webhooks** in the web UI, or via the REST API.

### Events

| Event | Fires when |
|---|---|
| `Episode Processed` | Episode completes processing successfully |
| `Episode Failed` | Episode reaches permanently failed status |
| `Auth Failure` | LLM provider returns 401/403 (rate-limited to one per 5 minutes) |

### Template Variables

Custom payload templates are Jinja2 strings rendered against these variables:

| Variable | Type | Description |
|---|---|---|
| `event` | string | `Episode Processed`, `Episode Failed`, or `Auth Failure` |
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
