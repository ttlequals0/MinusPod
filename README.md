# Podcast Ad Removal Server

Removes ads from podcasts using Whisper transcription. Serves modified RSS feeds that work with any podcast app.

> **Disclaimer:** This tool is for personal use only. Only use it with podcasts you have permission to modify or where such modification is permitted under applicable laws. Respect content creators and their terms of service.

## How It Works

1. **Transcription** - Whisper converts audio to text with timestamps
2. **Ad Detection** - Claude API analyzes transcript to identify ad segments (with optional dual-pass detection)
3. **Audio Processing** - FFmpeg removes detected ads and inserts short audio markers
4. **Serving** - Flask serves modified RSS feeds and processed audio files

Processing happens on-demand when you play an episode. First play takes a few minutes, subsequent plays are instant (cached).

### Multi-Pass Detection

Enable dual-pass ad detection in Settings for improved accuracy:

- **First Pass** - Standard ad detection finds obvious sponsor reads and ad breaks
- **Second Pass** - Blind analysis with different focus catches subtle baked-in ads, casual product mentions, and cross-promotions that the first pass might miss
- **Smart Merge** - Overlapping detections from both passes are merged (earliest start, latest end) for maximum coverage

Each detected ad shows a badge indicating which pass found it:
- **Pass 1** (blue) - Found by first pass only
- **Pass 2** (purple) - Found by second pass only
- **Merged** (green) - Found by both passes (boundaries combined)

Multi-pass increases processing time and API costs but catches more ads.

### Sliding Window Processing

For long episodes, transcripts are processed in overlapping 10-minute windows:

- **Window Size** - 10 minutes of transcript per API call
- **Overlap** - 3 minutes between windows ensures ads at boundaries aren't missed
- **Deduplication** - Ads detected in multiple windows are automatically merged

This approach ensures consistent detection quality regardless of episode length. A 60-minute episode is processed as 9 overlapping windows, with any duplicate detections combined into a single ad marker.

### Post-Detection Validation

After ad detection, a validation layer reviews each detection before audio processing:

- **Duration checks** - Rejects ads shorter than 7s or longer than 5 minutes
- **Confidence thresholds** - Rejects very low confidence detections (<0.3)
- **Position heuristics** - Boosts confidence for typical ad positions (pre-roll, mid-roll, post-roll)
- **Transcript verification** - Checks for sponsor names and ad signals in the transcript
- **Auto-correction** - Merges ads with tiny gaps, clamps boundaries to valid range

Ads are classified as:
- **ACCEPT** - High confidence, removed from audio
- **REVIEW** - Medium confidence, removed but flagged for review
- **REJECT** - Too short/long, low confidence, or missing ad signals - kept in audio

Rejected ads appear in a separate "Rejected Detections" section in the UI, allowing you to verify the validator's decisions.

## Requirements

- Docker with NVIDIA GPU support (for Whisper)
- Anthropic API key

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
- **Add Feed** - Add new podcasts by RSS URL
- **Feed Management** - Refresh, delete, copy feed URLs
- **Settings** - Configure ad detection prompts and Claude model
- **System Status** - View statistics and run cleanup

### Screenshots

**Dashboard**

<img src="docs/screenshots/dashboard.png" width="600">

**Podcast View**

<img src="docs/screenshots/podcast-view.png" width="600">

**Episode View**

<img src="docs/screenshots/episode-view.png" width="600">

**Add Feed**

<img src="docs/screenshots/add-feed.png" width="600">

**Settings**

<img src="docs/screenshots/settings.png" width="600">

**Mobile**

<img src="docs/screenshots/mobile.png" width="300">

**API Documentation**

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
- **Claude Model** - Model for first pass ad detection
- **Multi-Pass Detection** - Enable dual-pass analysis for better accuracy
- **Second Pass Model** - Separate model for second pass (visible when multi-pass enabled)
- **System Prompts** - Customizable prompts for first and second pass detection

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

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `ANTHROPIC_API_KEY` | required | Claude API key |
| `BASE_URL` | `http://localhost:8000` | Public URL for generated feed links |
| `WHISPER_MODEL` | `small` | Whisper model size (tiny/base/small/medium/large) |
| `WHISPER_DEVICE` | `cuda` | Device for Whisper (cuda/cpu) |
| `RETENTION_PERIOD` | `1440` | Minutes to keep processed episodes (1440 = 24 hours) |
| `TUNNEL_TOKEN` | optional | Cloudflare tunnel token for remote access |

## API

REST API available at `/api/v1/`. Interactive docs at `/docs`. See `openapi.yaml` for full specification.

Key endpoints:
- `GET /api/v1/feeds` - List all feeds
- `GET /api/v1/feeds/{slug}` - Get feed details
- `POST /api/v1/feeds` - Add a new feed
- `POST /api/v1/feeds/{slug}/episodes/{id}/reprocess` - Force reprocess an episode
- `POST /api/v1/feeds/{slug}/episodes/{id}/retry-ad-detection` - Retry ad detection only
- `GET /api/v1/settings` - Get current settings
- `PUT /api/v1/settings/ad-detection` - Update ad detection config

## Remote Access

The docker-compose includes an optional Cloudflare tunnel service for secure remote access without port forwarding:

1. Create a tunnel at [Cloudflare Zero Trust](https://one.dash.cloudflare.com/)
2. Add `TUNNEL_TOKEN` to your `.env` file
3. Configure the tunnel to point to `http://podcast-server:8000`

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
