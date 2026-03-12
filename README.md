<p align="center">
  <img src="frontend/public/logo.png" alt="MinusPod" width="400" />
</p>

Removes ads from podcasts using Whisper transcription. Serves modified RSS feeds that work with any podcast app.

> **Disclaimer:** This tool is for personal use only. Only use it with podcasts you have permission to modify or where such modification is permitted under applicable laws. Respect content creators and their terms of service.

## Table of Contents

- [How It Works](#how-it-works)
- [Advanced Features (Quick Reference)](#advanced-features-quick-reference)
- [Requirements](#requirements)
- [Quick Start](#quick-start)
- [Web Interface](#web-interface)
  - [Ad Editor Workflow](#ad-editor-workflow)
  - [Screenshots](#screenshots)
- [Configuration](#configuration)
- [Finding Podcast RSS Feeds](#finding-podcast-rss-feeds)
- [Usage](#usage)
  - [Audiobookshelf](#audiobookshelf)
- [Environment Variables](#environment-variables)
  - [Using Claude Code Wrapper (Max Subscription)](#using-claude-code-wrapper-max-subscription)
- [Using Ollama (Local LLM)](#using-ollama-local-llm)
- [API](#api)
- [Webhooks](#webhooks)
- [Remote Access](#remote-access)
- [Data Storage](#data-storage)
- [Custom Assets (Optional)](#custom-assets-optional)

## How It Works

1. **Transcription** - Whisper converts audio to text with timestamps
2. **Ad Detection** - Claude API analyzes transcript to identify ad segments (with optional dual-pass detection)
3. **Audio Processing** - FFmpeg removes detected ads and inserts short audio markers
4. **Serving** - Flask serves modified RSS feeds and processed audio files

Processing happens on-demand when you play an episode. First play takes a few minutes, subsequent plays are instant (cached).

## Advanced Features (Quick Reference)

| Feature | Description | Enable In |
|---------|-------------|-----------|
| **Verification Pass** | Post-cut re-detection catches missed ads by re-transcribing processed audio | Automatic |
| **Audio Enforcement** | Volume and transition signals programmatically validate and extend ad detections | Automatic |
| **Pattern Learning** | System learns from corrections, patterns promote from podcast to network to global scope | Automatic |
| **Confidence Thresholds** | >=80% confidence: cut; 50-79%: kept for review; <50%: rejected | Automatic |

See detailed sections below for configuration and usage.

### Verification Pass

After the first pass detects and removes ads, a verification pipeline runs on the processed audio:

1. **Re-transcribe** - The processed audio is re-transcribed on CPU using Whisper
2. **Audio Analysis** - Volume analysis and transition detection run on the processed audio
3. **Claude Detection** - A "what doesn't belong" prompt detects any remaining ad content
4. **Audio Enforcement** - Programmatic signal matching validates and extends detections
5. **Re-cut** - If missed ads are found, the pass 1 output is re-cut directly

Each detected ad shows a badge indicating which stage found it:
- **First Pass** (blue) - Found by Claude's first pass
- **Audio Enforced** (orange) - Found by programmatic audio signal matching
- **Verification** (purple) - Found by the post-cut verification pass

The verification model can be configured separately from the first pass model in Settings.

### Sliding Window Processing

For long episodes, transcripts are processed in overlapping 10-minute windows:

- **Window Size** - 10 minutes of transcript per API call
- **Overlap** - 3 minutes between windows ensures ads at boundaries aren't missed
- **Deduplication** - Ads detected in multiple windows are automatically merged

This approach ensures consistent detection quality regardless of episode length. A 60-minute episode is processed as 9 overlapping windows, with any duplicate detections combined into a single ad marker.

### Processing Queue

To prevent memory issues from concurrent processing, episodes are processed one at a time:

- **Single Processing** - Only one episode processes at a time (Whisper + FFMPEG are memory-intensive)
- **Background Processing** - Processing runs in a background thread, keeping UI responsive
- **Automatic Recovery** - Episodes stuck in "processing" status are automatically reset on server restart
- **Queue Management** - View and cancel processing episodes in Settings

When you request an episode that needs processing:
1. If nothing is processing, it starts in the background and returns HTTP 503 with `Retry-After: 30`
2. If another episode is processing, it returns HTTP 503 (your podcast app will retry)
3. Once processed, subsequent requests serve the cached file instantly

HEAD requests (sent by podcast apps like Pocket Casts during feed refresh) proxy headers from the upstream audio source without triggering processing. This prevents feed refreshes from flooding the processing queue.

### Post-Detection Validation

After ad detection, a validation layer reviews each detection before audio processing:

- **Duration checks** - Rejects ads shorter than 7s or longer than 5 minutes
- **Confidence thresholds** - Rejects very low confidence detections (<0.3); only cuts ads with >=80% adjusted confidence
- **Position heuristics** - Boosts confidence for typical ad positions (pre-roll, mid-roll, post-roll)
- **Transcript verification** - Checks for sponsor names and ad signals in the transcript
- **Auto-correction** - Merges ads with tiny gaps, clamps boundaries to valid range

Ads are classified as:
- **ACCEPT** - High confidence, removed from audio
- **REVIEW** - Medium confidence, removed but flagged for review
- **REJECT** - Too short/long, low confidence, or missing ad signals - kept in audio

Rejected ads appear in a separate "Rejected Detections" section in the UI, allowing you to verify the validator's decisions.

### Cross-Episode Ad Pattern Learning

The system learns from ad detections across all episodes to improve accuracy over time. When an ad is detected and validated, text patterns are extracted and stored for future matching.

**Pattern Hierarchy:**
- **Global Patterns** - Match across all podcasts (e.g., common sponsors like Squarespace, BetterHelp)
- **Network Patterns** - Match within a podcast network (TWiT, Relay FM, Gimlet, etc.)
- **Podcast Patterns** - Match only for a specific podcast

When processing new episodes, the system first checks for known patterns before sending to Claude. Patterns with high confirmation counts and low false positive rates are matched with high confidence.

**Pattern Sources:**
- **Audio Fingerprinting** - Identifies DAI-inserted ads using Chromaprint acoustic fingerprints
- **Text Pattern Matching** - TF-IDF similarity and fuzzy matching against learned patterns
- **Claude Analysis** - Falls back to AI analysis for uncovered segments

**User Corrections:**
In the ad editor, you can confirm, reject, or adjust detected ads:
- **Confirm** - Creates/updates patterns in the database, incrementing confirmation count
- **Adjust Boundaries** - Corrects start/end times for an ad; also creates patterns from adjusted boundaries (like confirm), ensuring accurate pattern text is learned
- **Mark as Not Ad** - Flags as false positive and stores the transcript text. Similar text is automatically excluded in future episodes of the same podcast using TF-IDF similarity matching (cross-episode false positive learning)

**Pattern Management:**
Access the Patterns page from the navigation bar to:
- View all patterns with their scope, sponsor, and statistics
- Filter by scope (Global, Network, Podcast) or search by sponsor name
- Toggle patterns active/inactive
- View confirmation and false positive counts

### Real-Time Processing Status

A global status bar shows real-time processing progress via Server-Sent Events:

- **Processing Indicator** - Shows currently processing episode title
- **Stage Display** - Current stage (Transcribing, Detecting Ads, Processing Audio)
- **Progress Bar** - Visual progress indicator
- **Queue Depth** - Number of episodes waiting to be processed
- **Quick Navigation** - Click to view the processing episode

### Reprocessing Modes

When reprocessing an episode from the UI, two modes are available:

- **Reprocess** (default) - Uses learned patterns from the pattern database plus Claude analysis
- **Full Analysis** - Skips the pattern database entirely for a fresh Claude-only analysis

Full Analysis is useful when you want to re-evaluate an episode without the influence of learned patterns (e.g., after disabling patterns that caused false positives).

### Audio Analysis

Audio analysis runs automatically on every episode (lightweight, uses only ffmpeg):

- **Volume Analysis** - Detects loudness anomalies using EBU R128 measurement. Identifies sections mastered at different levels than the content baseline.
- **Transition Detection** - Finds abrupt frame-to-frame loudness jumps that indicate dynamically inserted ad (DAI) boundaries. Pairs up/down transitions into candidate ad regions.
- **Audio Enforcement** - After Claude detection, uncovered audio signals with ad language in the transcript are promoted to ads. DAI transitions with high confidence (>=0.8) or sponsor matches are also promoted. Existing ad boundaries are extended when signals partially overlap.

## Requirements

- Docker with NVIDIA GPU support (for Whisper)
- Anthropic API key **or** [Ollama](https://ollama.com) for local inference

### Memory Requirements

**GPU VRAM:**

| Whisper Model | VRAM Required |
|---------------|---------------|
| tiny | ~1 GB |
| base | ~1 GB |
| small | ~2 GB |
| medium | ~4 GB |
| large-v3 | ~5-6 GB |

**System RAM:**

| Episode Length | RAM Required |
|----------------|-------------|
| < 1 hour | 8 GB |
| 1-2 hours | 8 GB |
| 2-4 hours | 12 GB |
| > 4 hours | 16 GB |

## Quick Start

```bash
# 1. Create environment file
cat > .env << EOF
ANTHROPIC_API_KEY=your-key-here
BASE_URL=http://localhost:8000
EOF

# 2. Create data directory
mkdir -p data

# 3. Run
docker-compose up -d
```

Access the web UI at `http://localhost:8000/ui/` to add and manage feeds.

## Web Interface

The server includes a web-based management UI at `/ui/`:

- **Dashboard** - View all feeds with artwork and episode counts
- **Add Feed** - Add new podcasts by RSS URL with optional feed cap (max episodes served to clients)
- **Feed Management** - Refresh, delete, copy feed URLs, set network override, configure per-feed episode cap
- **Episode Discovery** - All episodes from a feed are surfaced as "discovered" on every refresh. Process any episode at any time from the feed detail page
- **Bulk Actions** - Select multiple episodes and apply Process, Reprocess, Reprocess (Full), or Delete in one action
- **Episode Sorting** - Sort by publish date, episode number, or creation date
- **Pagination** - Feed detail episode list is paginated (25/50/100/500 per page)
- **Patterns** - View and manage cross-episode ad patterns with sponsor names
- **History** - View processing history with stats, filtering, and export
- **Settings** - Configure LLM provider (Anthropic/Ollama/OpenAI-compatible), AI models, ad detection prompts, retention period, view system statistics, LLM token usage and cost
- **Real-Time Status Bar** - Shows current processing progress across all pages

### Ad Editor Workflow

The ad editor follows a **review-and-reprocess** model. When you listen to a detected ad segment, the audio player plays the **processed output** (post-cut audio), not the original. This is intentional: you are verifying what the final listener will hear. If a cut sounds wrong, adjust the boundaries and reprocess -- the system will re-cut from the original source audio.

The **Original Transcript** panel on the Episode Detail page shows the full pre-cut transcript so you can see exactly what text was identified and removed.

### Ad Editor (Mobile-First)

The ad editor allows you to review and adjust ad detections directly in the browser. Designed mobile-first since that's where most reviewing happens:

**Core Features:**
- **Reason Panel** - Shows why each ad was flagged, confidence percentage, and detection stage
- **Time Adjustment Controls** - Per-second +/- steppers for start and end boundaries with direct input
- **Pill Selector** - Quick navigation between ads by timestamp, visible on all viewports
- **Audio Playback** - Inline player with progress bar; auto-seeks to ad start when switching between ads
- **Haptic Feedback** - Vibration on boundary adjustments and actions

**Mobile Layout:**
- Stacked Start/End time controls (full-width rows for clear readability)
- Full-width progress bar at top of bottom sheet
- Compact action row: Not Ad, Reset, Confirm, Save
- Expandable bottom sheet with large play controls and prev/next navigation

**Desktop Layout:**
- Keyboard shortcuts: `Space` play/pause, `J/K` nudge end, `Shift+J/K` nudge start, `C` confirm, `X` reject, `Esc` reset
- Inline audio player with hover-expandable progress bar
- Start/End controls inline with time display and keyboard hints

### Screenshots

#### Dashboard
| Desktop | Mobile |
|---------|--------|
| <img src="docs/screenshots/dashboard-desktop.png" width="500"> | <img src="docs/screenshots/dashboard-mobile.png" width="200"> |

#### Feed Detail
| Desktop | Mobile |
|---------|--------|
| <img src="docs/screenshots/feed-detail-desktop.png" width="500"> | <img src="docs/screenshots/feed-detail-mobile.png" width="200"> |

#### Episode Detail
| Desktop | Mobile |
|---------|--------|
| <img src="docs/screenshots/episode-detail-desktop.png" width="500"> | <img src="docs/screenshots/episode-detail-mobile.png" width="200"> |

#### Detected Ads
| Desktop | Mobile |
|---------|--------|
| <img src="docs/screenshots/ads-detected-desktop.png" width="500"> | <img src="docs/screenshots/ads-detected-mobile.png" width="200"> |

#### Ad Editor
| Desktop | Mobile |
|---------|--------|
| <img src="docs/screenshots/ad-editor-desktop.png" width="500"> | <img src="docs/screenshots/ad-editor-mobile.png" width="200"> |

#### Ad Patterns
| Desktop | Mobile |
|---------|--------|
| <img src="docs/screenshots/patterns-desktop.png" width="500"> | <img src="docs/screenshots/patterns-mobile.png" width="200"> |

#### History
| Desktop | Mobile |
|---------|--------|
| <img src="docs/screenshots/history-desktop.png" width="500"> | <img src="docs/screenshots/history-mobile.png" width="200"> |

#### Settings
| Desktop | Mobile |
|---------|--------|
| <img src="docs/screenshots/settings-desktop.png" width="500"> | <img src="docs/screenshots/settings-mobile.png" width="200"> |

#### API Documentation

<img src="docs/screenshots/api-docs.png" width="600">

## Configuration

All configuration is managed through the web UI or REST API. No config files needed.

### Adding Feeds

1. Open `http://your-server:8000/ui/`
2. Click "Add Feed"
3. Enter the podcast RSS URL
4. Optionally set a custom slug (URL path)

### Ad Detection Settings

Customize ad detection in Settings:
- **LLM Provider** - Switch between Anthropic (direct API), Ollama (local), or OpenAI-compatible endpoints at runtime without restarting the container
- **AI Model** - Model for first pass ad detection
- **Verification Model** - Separate model for the post-cut verification pass
- **Chapters Model** - Model for chapter generation (defaults to Haiku for cost efficiency)
- **System Prompts** - Customizable prompts for first pass and verification detection

## Finding Podcast RSS Feeds

Most podcasts publish RSS feeds. Common ways to find them:

1. **Podcast website** - Look for "RSS" link in footer or subscription options
2. **Apple Podcasts** - Search on [podcastindex.org](https://podcastindex.org) using the Apple Podcasts URL
3. **Spotify-exclusive** - Not available (Spotify doesn't expose RSS feeds)
4. **Hosting platforms** - Common patterns:
   - Libsyn: `https://showname.libsyn.com/rss`
   - Spreaker: `https://www.spreaker.com/show/{id}/episodes/feed`
   - Omny: Check page source for `omnycontent.com` URLs

## Usage

Add your modified feed URL to any podcast app:
```
http://your-server:8000/your-feed-slug
```

The feed URL is shown in the web UI and can be copied to clipboard.

### Audiobookshelf

If using [Audiobookshelf](https://www.audiobookshelf.org/) as your podcast client, its SSRF protection will block requests to MinusPod when running on a local/private network. Add your MinusPod hostname or IP to Audiobookshelf's whitelist:

```
SSRF_REQUEST_FILTER_WHITELIST=minuspod.local,192.168.1.100
```

This is a comma-separated list of domains excluded from Audiobookshelf's SSRF filter. See [Audiobookshelf Security docs](https://www.audiobookshelf.org/docs/#security) for details.

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `ANTHROPIC_API_KEY` | _(none)_ | Claude API key (required when `LLM_PROVIDER=anthropic`, not needed for Ollama) |
| `LLM_PROVIDER` | `anthropic` | LLM backend: `anthropic` (direct API), `openai-compatible` (wrapper), or `ollama` |
| `OPENAI_BASE_URL` | `http://localhost:8000/v1` | Base URL for OpenAI-compatible API (only used with non-anthropic providers) |
| `OPENAI_API_KEY` | `not-needed` | API key for OpenAI-compatible endpoint (not required for Ollama or local wrappers) |
| `OPENAI_MODEL` | _(none)_ | Model for OpenAI-compatible/Ollama providers. **Required for Ollama** (e.g. `qwen3:14b`). Defaults to `claude-sonnet-4-5-20250929` for wrapper mode if unset. |
| `BASE_URL` | `http://localhost:8000` | Public URL for generated feed links |
| `UI_BASE_URL` | _(falls back to BASE_URL)_ | Public URL for UI links in webhooks (set if UI is on a different domain than feeds) |
| `WHISPER_MODEL` | `small` | Whisper model size (tiny/base/small/medium/large) |
| `WHISPER_DEVICE` | `cuda` | Device for Whisper (cuda/cpu) |
| `RETENTION_PERIOD` | `1440` | **Deprecated.** Legacy minutes-based retention (auto-converted to days on first startup). Use the Settings UI or `PUT /api/v1/settings/retention` instead. Retention now resets episodes to "discovered" instead of deleting them. |
| `TUNNEL_TOKEN` | optional | Cloudflare tunnel token for remote access |

### Using Claude Code Wrapper (Max Subscription)

Instead of using API credits, you can use the [Claude Code OpenAI Wrapper](https://github.com/ttlequals0/claude-code-openai-wrapper) to leverage your Claude Max subscription.

**Quick Start:**

1. Start the wrapper service:
   ```bash
   docker compose --profile wrapper up -d
   ```

2. Authenticate with Claude (first time only):
   ```bash
   docker compose --profile wrapper run --rm claude-wrapper claude auth login
   ```

3. Configure minuspod to use the wrapper by updating your `.env`:
   ```bash
   LLM_PROVIDER=openai-compatible
   OPENAI_BASE_URL=http://claude-wrapper:8000/v1
   OPENAI_API_KEY=not-needed
   ```

4. Restart minuspod:
   ```bash
   docker compose up -d minuspod
   ```

The wrapper exposes an OpenAI-compatible API that routes requests through your Claude Max subscription instead of consuming API credits.

**Other OpenAI-Compatible Endpoints:**

The `openai-compatible` provider can work with other endpoints by configuring `OPENAI_BASE_URL` and `OPENAI_API_KEY` accordingly. The model is selected via the Settings UI.

**Example `.env` for OpenAI-compatible mode:**

```bash
# LLM Configuration (OpenAI-compatible)
LLM_PROVIDER=openai-compatible
OPENAI_BASE_URL=http://claude-wrapper:8000/v1
OPENAI_API_KEY=not-needed

# Server Configuration
BASE_URL=http://localhost:8000
```

Note: The AI model is configured via the Settings UI, not environment variables.

## Using Ollama (Local LLM)

MinusPod supports [Ollama](https://ollama.com) as a drop-in replacement for the Anthropic API. This lets you run ad detection entirely locally with no API costs or data leaving your machine.

### Setup

1. Install and start Ollama on your host machine
2. Pull a model (see recommendations below): `ollama pull qwen3:14b`
3. Update your `docker-compose.yml`:

```yaml
environment:
  - LLM_PROVIDER=ollama
  - OPENAI_BASE_URL=http://host.docker.internal:11434/v1
  - OPENAI_MODEL=qwen3:14b
```

> **Linux users:** `host.docker.internal` doesn't resolve by default. Add this to your service definition:
> ```yaml
> extra_hosts:
>   - "host.docker.internal:host-gateway"
> ```

The `OPENAI_API_KEY` variable is not required for Ollama. Token counts will still be tracked in the UI but cost will always show as $0.00, which is accurate since local inference is free.

---

### Recommended Models

Models are loaded sequentially, not concurrently -- VRAM requirements are not additive between passes.

#### Pass 1 -- First Pass Detection

Hardest task. Contextual reasoning, host-read ads, new sponsors. Use your best model here.

| VRAM | Model | Quantization | Notes |
|------|-------|--------------|-------|
| 8GB | `qwen3:8b` | Q4_K_M | Entry level. Handles standard sponsor reads well. |
| 12GB | `qwen3:14b` | Q4_K_M | Best quality-to-VRAM ratio. **Recommended.** |
| 16GB | `qwen3:14b` | Q5_K_M | Higher quality quant; use if you have headroom. |
| 24GB | `qwen3.5:27b` | Q4_K_M | Strong contextual reasoning. 256K context. |
| 24GB | `qwen3.5:35b` | Q4_K_M | Best quality under 40GB. 256K context. |
| 40GB+ | `qwen3.5:122b` | Q4_K_M | Closest open-weights match to Claude Sonnet quality. |

#### Verification Pass

Easier task. Looks for remnants in already-cut audio. Speed matters more than raw accuracy.

| VRAM | Model | Quantization | Notes |
|------|-------|--------------|-------|
| 8GB | `qwen3:4b` | Q8_0 | Fast, good JSON compliance. Verification prompt is simpler. |
| 12GB | `qwen3:8b` | Q5_K_M | Strong JSON compliance, faster than 14B. |
| 16GB | `mistral-nemo:12b` | Q4_K_M | Excellent JSON reliability, fast inference. |
| 24GB | `qwen3:14b` | Q5_K_M | Overkill for verification but uses available VRAM productively. |

#### Chapters

Simplest task. Summarization only -- no structured detection. Minimize cost and latency.

| VRAM | Model | Quantization | Notes |
|------|-------|--------------|-------|
| Any | `qwen3:4b` | Q4_K_M | Sufficient for summarization. Fast. |
| Any | `phi4-mini` | Q4_K_M | Lean alternative, strong instruction following. |
| Any | `llama3.2:3b` | Q4_K_M | Smallest viable option if VRAM is tight. |

> **Example split for 16GB VRAM:** Pass 1 -> `qwen3:14b Q5_K_M` / Verification -> `qwen3:8b Q5_K_M` / Chapters -> `qwen3:4b Q4_K_M`

> **Avoid models under 7B for production use.** JSON reliability degrades significantly at smaller sizes, which causes silent detection failures rather than recoverable errors (see below).

---

### Accuracy vs. Claude

Switching to a local model will reduce detection accuracy. The impact depends on the content and model size.

**What is unaffected:** Audio fingerprinting, text pattern matching, pre/post-roll heuristics, and audio signal enforcement all run without the LLM. These catch a substantial portion of ads regardless of which model is used.

**What is affected:** The LLM passes (first pass and verification) handle the hard cases -- host-read ads that blend into content, new sponsors not yet in the pattern database, and ambiguous mid-rolls without explicit promo codes. This is where open-weights models fall short of Claude.

| Content Type | Expected Impact |
|---|---|
| Podcasts with standard sponsor reads and promo codes | Minimal -- patterns and fingerprinting cover most of these |
| Podcasts with heavy host-read / conversational ad integrations | Noticeable -- these require strong contextual reasoning |
| New sponsors not yet in the pattern database | Moderate -- depends heavily on model capability |

As a rough guide: a capable model like `qwen3:14b` will perform well on most podcasts. The gap becomes more apparent on shows where hosts weave sponsor content naturally into conversation without clear transitions.

---

### JSON Reliability Risks

MinusPod's ad detection pipeline requires models to return structured JSON. The Anthropic API enforces this reliably. With Ollama, enforcement is model-dependent and failures are more likely.

**How failures manifest:**

- **Malformed JSON** -- Missing brackets, trailing commas, or unquoted keys. The parser has multiple fallback strategies (direct parse, markdown code block extraction, regex scan) but structurally broken JSON will fall through all of them.
- **Truncated output** -- Models under memory pressure or processing long transcript windows may cut off mid-response, producing valid-looking but incomplete JSON that fails to parse.
- **Preamble text** -- Some models prefix their JSON with conversational text ("Sure, here are the ads I found:"). The parser handles this in most cases, but it adds fragility.

**When a window fails to parse, those ads are silently missed.** There is no error surfaced to the UI -- the episode will process normally but with gaps in detection coverage.

**How to reduce this risk:**

- Use a model of at least 7B parameters
- Prefer the Qwen3 or Mistral model families, which have strong JSON compliance
- Avoid running other GPU workloads concurrently -- memory pressure increases truncation risk
- Check processing logs for parse failures if detection quality seems lower than expected

**How to check for failures:**

Look for `json_parse_failed` or `extraction_method` entries in the application logs. A healthy run will show `json_array_direct` as the extraction method. Fallback methods (`markdown_code_block`, regex variants) indicate the model isn't returning clean JSON and you should consider upgrading to a larger model.

## API

REST API available at `/api/v1/`. Interactive docs at `/docs`. See `openapi.yaml` for full specification.

Key endpoints:
- `GET /api/v1/feeds` - List all feeds
- `POST /api/v1/feeds` - Add a new feed (supports `maxEpisodes` for RSS cap)
- `POST /api/v1/feeds/import-opml` - Import feeds from OPML file
- `GET /api/v1/feeds/export-opml` - Export all feeds as OPML file
- `GET /api/v1/feeds/{slug}/episodes` - List episodes (supports `sort_by`, `sort_dir`, `status` filter, pagination)
- `POST /api/v1/feeds/{slug}/episodes/bulk` - Bulk episode actions (process, reprocess, reprocess_full, delete)
- `POST /api/v1/feeds/{slug}/episodes/{id}/reprocess` - Force reprocess (supports `mode`: reprocess/full)
- `POST /api/v1/feeds/{slug}/reprocess-all` - Batch reprocess all episodes
- `POST /api/v1/feeds/{slug}/episodes/{id}/retry-ad-detection` - Retry ad detection only
- `POST /api/v1/feeds/{slug}/episodes/{id}/corrections` - Submit ad corrections
- `GET /api/v1/patterns` - List ad patterns (filter by scope)
- `GET /api/v1/patterns/stats` - Pattern database statistics
- `GET /api/v1/sponsors` - List/create/update/delete sponsors (full CRUD)
- `GET /api/v1/search?q=query` - Full-text search across all content
- `GET /api/v1/history` - Processing history with pagination and export
- `GET /api/v1/status` - Current processing status
- `GET /api/v1/status/stream` - SSE endpoint for real-time status updates
- `GET /api/v1/system/token-usage` - LLM token usage and cost breakdown by model
- `GET /api/v1/system/model-pricing` - All known LLM model pricing rates
- `POST /api/v1/system/vacuum` - Trigger SQLite VACUUM to reclaim disk space
- `GET /api/v1/system/backup` - Download SQLite database backup
- `GET /api/v1/settings` - Get current settings (includes LLM provider, API key status)
- `GET/PUT /api/v1/settings/retention` - Get or update retention configuration (days, enabled/disabled)
- `PUT /api/v1/settings/ad-detection` - Update ad detection config (model, provider, prompts)
- `GET /api/v1/settings/models` - List available AI models from current provider
- `POST /api/v1/settings/models/refresh` - Force refresh model list from provider
- `GET/POST/PUT/DELETE /api/v1/settings/webhooks` - Webhook CRUD
- `POST /api/v1/settings/webhooks/{id}/test` - Fire test webhook
- `POST /api/v1/settings/webhooks/validate-template` - Validate and preview a payload template

## Webhooks

MinusPod fires an HTTP POST to configured URLs when episodes complete processing or permanently fail. Works with any HTTP endpoint. Use a custom Jinja2 payload template to match the receiver's expected format.

Configure webhooks in **Settings > Webhooks** in the web UI, or via the REST API.

### Events

| Event | Fires when |
|---|---|
| `Episode Processed` | Episode completes processing successfully |
| `Episode Failed` | Episode reaches permanently failed status |

### Template Variables

Custom payload templates are Jinja2 strings rendered against these variables:

| Variable | Type | Description |
|---|---|---|
| `event` | string | `Episode Processed` or `Episode Failed` |
| `timestamp` | string | ISO 8601 UTC timestamp |
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

### Example: Pushover

Pushover supports native webhook ingestion with data extraction selectors. No custom payload template needed -- MinusPod's default JSON payload works directly.

1. Log in to [pushover.net/dashboard](https://pushover.net/dashboard), scroll to "Your Webhooks", click "Create a Webhook". Name it MinusPod.
2. Copy the unique webhook URL.
3. In MinusPod Settings > Webhooks: paste the URL, select events, **leave payload template blank**.
4. Click Test in MinusPod to fire a sample payload to Pushover.
5. In Pushover dashboard: click "Check for Update" in Last Payload to load MinusPod's JSON.
6. Configure data extraction selectors:

| Field | Selector |
|---|---|
| Title | `{{event}}` |
| Body | `{{episode.title}}`<br>`{{episode.ads_removed}} ads removed. Saved {{episode.time_saved}}. Cost {{episode.llm_cost_display}}` |
| URL | `{{episode.url}}` |
| URL Title | `Open in MinusPod` |

7. Click "Test Selectors on Last Payload" to preview, then Save.

> Pushover's `{{...}}` selector syntax is evaluated on Pushover's side -- these are not Jinja2 templates.

### Example: ntfy

ntfy requires a custom payload template to match its expected JSON format.

1. Self-hosted or ntfy.sh -- set your topic name
2. Add a webhook in Settings > Webhooks:
   - **URL:** `https://ntfy.sh/your-topic` (or your self-hosted instance)
   - **Payload template:**
     ```json
     {
       "topic": "your-topic",
       "title": "{{ episode.title }}",
       "message": "Removed {{ episode.ads_removed }} ads in {{ episode.processing_time }}. Cost {{ episode.llm_cost_display }}",
       "actions": [{"action": "view", "label": "Open Episode", "url": "{{ episode.url }}"}]
     }
     ```

> ntfy also supports header-based delivery (`X-Title`, `X-Message`, `X-Click` headers with plain text body) -- either approach works with MinusPod's template system.

When no custom template is configured, MinusPod sends its default JSON payload which works with custom scripts, n8n, Home Assistant webhooks, and other generic HTTP receivers.

### Request Signing

If a webhook has a secret configured, MinusPod adds an `X-MinusPod-Signature: sha256=<hmac>` header to each POST, computed with HMAC-SHA256 over the request body.

## Remote Access

The docker-compose includes an optional Cloudflare tunnel service for secure remote access without port forwarding:

1. Create a tunnel at [Cloudflare Zero Trust](https://one.dash.cloudflare.com/)
2. Add `TUNNEL_TOKEN` to your `.env` file
3. Configure the tunnel to point to `http://minuspod:8000`

### Security Recommendations

When exposing your feed to the internet (required for apps like Pocket Casts), consider adding WAF rules to:
- Only allow requests from known podcast app User-Agents
- Block access to admin endpoints (`/ui`, `/docs`, `/api`)

**Cloudflare WAF Example**

Create a custom rule to allow only Pocket Casts and block admin paths:

```
Rule name: feed_only_allow_pocketcasts

Expression:
(http.request.full_uri wildcard r"http*://feed.example.com/*" and not http.user_agent wildcard "*Pocket*Casts*") or (http.request.uri.path in {"/ui" "/docs"})

Action: Block
```

This blocks:
- Any request to your feed domain without "Pocket Casts" in the User-Agent
- All requests to `/ui` and `/docs` endpoints

Adjust the User-Agent pattern for your podcast app (e.g., `*Overcast*`, `*Castro*`, `*AntennaPod*`).

## Data Storage

All data is stored in the `./data` directory:
- `podcast.db` - SQLite database with feeds, episodes, and settings
- `{slug}/` - Per-feed directories with cached RSS and processed audio

## Custom Assets (Optional)

By default, a short audio marker is played where ads were removed. You can customize this by providing your own replacement audio:

1. Create an `assets` directory next to your docker-compose.yml
2. Place your custom `replace.mp3` file in the assets directory
3. Uncomment the assets volume mount in docker-compose.yml:
   ```yaml
   volumes:
     - ./data:/app/data
     - ./assets:/app/assets:ro  # Uncomment this line
   ```
4. Restart the container

The `replace.mp3` file will be inserted at each ad break. Keep it short (1-3 seconds) to avoid disrupting the listening experience. If no custom asset is provided, the built-in default marker is used.

## License

MIT
