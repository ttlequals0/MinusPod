# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.49] - 2025-11-30

### Added
- API reliability with retry logic for transient Claude API errors
  - Retries up to 3 times on 529 overloaded, 500, 502, 503, rate limit errors
  - Exponential backoff with jitter (2s base, 60s max)
  - Episodes now track `adDetectionStatus` (success/failed) in database and API
  - New endpoint: `POST /feeds/<slug>/episodes/<episode_id>/retry-ad-detection`
    - Retries ad detection using existing transcript (no re-transcription needed)
- Multi-pass ad detection (opt-in feature)
  - Enable via Settings API: `PUT /settings/ad-detection` with `{"multiPassEnabled": true}`
  - When enabled, after first-pass processing:
    1. Re-transcribes the processed audio (where first-pass ads are now beeps)
    2. Runs second-pass detection looking for missed ads
    3. First-pass ads provided as context ("we found these, look for similar")
    4. Processes audio again if additional ads found
  - Combined ad count and time saved from both passes
  - Note: Approximately doubles transcription and API costs when enabled

### Changed
- Expanded DEFAULT_SYSTEM_PROMPT for better ad detection accuracy
  - Added DETECTION BIAS guidance: "When in doubt, mark it as an ad"
  - Added RETAIL/CONSUMER BRANDS list (Nordstrom, Macy's, Target, Nike, Sephora, etc.)
  - Added RETAIL/COMMERCIAL AD INDICATORS section (shopping CTAs, free shipping, price mentions)
  - Added NETWORK/RADIO-STYLE ADS section for ads without podcast-specific elements
  - Added second example showing Nordstrom-style retail ad detection
  - Strengthened REMINDER section to catch all ad types
  - Note: Users with custom prompts should reset to default in Settings to get improvements

### Fixed
- Joe Rogan episode type issue: Claude API 529 overloaded error was silently returning 0 ads
  - Now properly retries and blocks until success or permanent failure
  - Failed detection clearly marked in UI/API (adDetectionStatus: "failed")

---

## [0.1.48] - 2025-11-29

### Added
- Enhanced request logging with detailed info
  - All routes now log: IP address, user-agent, response time (ms), status code
  - Format: `GET /path 200 45ms [192.168.1.100] [Podcast App/1.0]`
  - Applied to RSS feeds (`/<slug>`), episodes (`/episodes/*`), health check, and all API routes
  - Static files (`/ui/*`, `/docs`) excluded to reduce noise

---

## [0.1.47] - 2025-11-29

### Changed
- Replaced load_data_json/save_data_json patterns with direct database calls in main.py
  - Eliminates race conditions during concurrent episode processing
  - More efficient single-episode updates (no longer loads/saves all episodes)
  - Affected: refresh_rss_feed, process_episode (start/complete/fail), serve_episode

### Added
- File size display in episode detail UI
  - Shows processed file size in MB next to duration
  - Added fileSize to API response and TypeScript types

---

## [0.1.46] - 2025-11-29

### Fixed
- "Detected Ads" section not showing in episode detail UI
  - Frontend still referenced `ad_segments` after API cleanup removed it in v0.1.45
  - Updated EpisodeDetail.tsx to use `adMarkers` field

---

## [0.1.45] - 2025-11-29

### Changed
- Improved ad detection system prompt for better boundary precision
  - Added AD START SIGNALS section to capture transitions ("let's take a break", etc.)
  - Added POST-ROLL ADS section to detect local business ads at end of episodes
  - Updated example to show transition phrase included in ad segment
- Longer fade-in after beep (0.8s instead of 0.5s) for smoother return to content
  - Content fade-out before beep: 0.5s (unchanged)
  - Content fade-in after beep: 0.8s (was 0.5s)
  - Beep fades: 0.5s (unchanged)
- "Run Cleanup" button renamed to "Delete All Episodes"
  - Now immediately deletes ALL processed episodes (ignores retention period)
  - Uses double-click confirmation pattern (click once to arm, click again to confirm)
  - Button turns red when armed, auto-resets after 3 seconds

### Fixed
- Removed duplicate snake_case fields from episode API response
  - Removed: original_url, processed_url, ad_segments, ad_count
  - Kept camelCase equivalents: originalUrl, processedUrl, adMarkers, adsRemoved

---

## [0.1.44] - 2025-11-29

### Fixed
- Beep replacement only playing for first ad when multiple ads detected
  - Root cause: ffmpeg input streams can only be used once in filter_complex
  - Added asplit to create N copies of beep input for N ads
  - Now all ads get proper beep replacement with fades
- RETENTION_PERIOD env var being ignored after initial database setup
  - Env var now takes precedence over database setting
  - Allows runtime override without database modification

---

## [0.1.43] - 2025-11-29

### Added
- Audio fading on replacement beep (0.5s fade-in and fade-out)
  - Creates smoother transitions: content fade-out -> beep fade-in -> beep fade-out -> content fade-in
- end_text field back in ad detection prompt for debugging ad boundary issues
  - Shows last 3-5 words Claude identified as the ad ending
  - Stored in API response for debugging, not used programmatically

### Changed
- Claude API temperature set to 0.0 (was 0.2)
  - Makes ad detection deterministic - same transcript produces same results
  - Fixes ad count varying between reprocesses of the same episode

---

## [0.1.42] - 2025-11-29

### Fixed
- Audio fading still not working after v0.1.41 fix
  - Root cause: ffmpeg atrim filter does not reset timestamps
  - Added asetpts=PTS-STARTPTS after atrim to reset timestamps to 0-based
  - Without this, afade st= parameter was looking for timestamps that did not exist in the trimmed stream

---

## [0.1.41] - 2025-11-29

### Fixed
- Audio fading not working due to incorrect ffmpeg afade timing
  - afade st= parameter was using absolute time instead of trimmed segment time
  - Now correctly calculates fade start relative to segment duration

---

## [0.1.40] - 2025-11-29

### Fixed
- Ad detection regression from v0.1.38 (5 ads -> 3 ads)
  - Removed complex MID-BLOCK BOUNDARY example that overwhelmed Claude
  - Removed end_text field requirement from output format
  - Simplified prompt restores ad detection accuracy

### Added
- Audio fading at ad boundaries (0.5s fade-in/fade-out)
  - Smooths transitions when ad boundaries are imprecise
  - Note: Users with custom prompts should reset to default in Settings

---

## [0.1.39] - 2025-11-29

### Fixed
- Ad detector not parsing "end_text" field from Claude response
  - Prompt requested end_text but ad_detector.py was not extracting it from response
  - Now correctly parses and includes end_text in ad segment data
  - Enables debugging of ad boundary precision issues

---

## [0.1.38] - 2025-11-29

### Changed
- Improved ad boundary precision in DEFAULT_SYSTEM_PROMPT
  - Added required "end_text" field to output format (last 3-5 words of ad)
  - Added concrete MID-BLOCK BOUNDARY example with calculation walkthrough
  - Helps Claude identify exact ad ending points within timestamp blocks
  - Note: Users with custom prompts should reset to default in Settings

---

## [0.1.37] - 2025-11-29

### Changed
- Improved DEFAULT_SYSTEM_PROMPT for better ad detection
  - Added PRIORITY instruction: "Focus on FINDING all ads first, then refining boundaries"
  - Added extended sponsor list (1Password, Bitwarden, ThreatLocker, Framer, Vanta, etc.)
  - Added AD END SIGNALS section for precise boundary detection
  - Added MID-BLOCK BOUNDARIES guidance for when ads end mid-timestamp
  - Removed "DO NOT INCLUDE" exclusion list that was causing missed detections
  - Enhanced REMINDER to not skip ads due to show content in same timestamp block
  - Note: Users with custom prompts should reset to default in Settings to get improvements

---

## [0.1.36] - 2025-11-29

### Fixed
- Ad detection returning 0 ads for host-read sponsor segments
  - Claude was distinguishing between "traditional ads" and "sponsor reads" and excluding the latter
  - Updated DEFAULT_SYSTEM_PROMPT with explicit instructions that host-read sponsor segments ARE ads
  - Added CRITICAL section and REMINDER to prevent Claude from excluding naturally-integrated sponsor content
  - Note: Users with custom system prompts should reset to default in Settings to get the fix

---

## [0.1.35] - 2025-11-29

### Changed
- Completed filesystem cleanup for transcript and ads data
  - Removed legacy filesystem fallback in `get_transcript()` - now reads only from database
  - Removed `delete_transcript()` and `delete_ads_json()` methods (database handles all data)
  - Simplified `cleanup_episode_files()` to only delete `.mp3` files
  - Removed filesystem migration code from database initialization
  - Reprocess endpoint now only clears database (no filesystem delete calls)
- Filesystem now stores only: artwork, processed mp3, feed.xml

---

## [0.1.34] - 2025-11-28

### Changed
- Use Gunicorn production WSGI server instead of Flask development server
  - Removes "WARNING: This is a development server" message from logs
  - 1 worker with 4 threads for concurrent request handling

---

## [0.1.33] - 2025-11-28

### Fixed
- Redundant file storage not actually removed in v0.1.26
  - `save_transcript()` and `save_ads_json()` were still writing `-transcript.txt` and `-ads.json` files
  - Now stores transcript and ad data exclusively in database (no more duplicate files)
  - Removed dead `save_prompt()` function (unused since v0.1.32)

---

## [0.1.32] - 2025-11-28

### Fixed
- `claudePrompt` field always null in episode API response
  - `save_ads_json()` in storage.py was not extracting `prompt` from ad_detector result
  - Now correctly saves prompt to database alongside raw_response and ad_markers
  - Note: Existing episodes will still have null prompt; only newly processed episodes will have it

---

## [0.1.31] - 2025-11-28

### Fixed
- `claudePrompt` and `claudeRawResponse` fields missing from episode detail API response
  - Fields were documented in v0.1.26 CHANGELOG but never added to the API response
  - Data was stored correctly in database, just not returned to clients

---

## [0.1.30] - 2025-11-28

### Fixed
- Settings page 500 error (ImportError for removed DEFAULT_USER_PROMPT_TEMPLATE)
  - Missed removing import statement in api.py when removing constant from database.py

---

## [0.1.29] - 2025-11-28

### Removed
- `userPromptTemplate` from Settings UI/API
  - This setting was not useful to customize (just formats the transcript)
  - Template is now hardcoded in ad_detector.py
  - Reduces API surface area and simplifies settings

---

## [0.1.28] - 2025-11-28

### Fixed
- `claudePrompt` field always null in episode API response
  - Ad detector was not returning the prompt in its result dictionary
  - Now properly saved to database and accessible via API

---

## [0.1.27] - 2025-11-28

### Fixed
- Warning during episode processing: "Storage object has no attribute save_prompt"
  - Removed dead code block in ad_detector.py that was calling removed storage method

---

## [0.1.26] - 2025-11-28

### Changed
- Removed redundant file storage for episode metadata
  - Transcript, ad markers, and Claude prompt/response now stored only in database
  - Previously written to both database AND filesystem (wasted disk space)
  - Files removed: `-transcript.txt`, `-ads.json`, `-prompt.txt`
- Simplified episode cleanup - only deletes `.mp3` files (database cascade handles metadata)
- `/transcript` endpoint now reads from database instead of filesystem

### Added
- `claudePrompt` and `claudeRawResponse` fields in episode detail API response
  - Useful for debugging ad detection issues

### Removed
- Unused storage methods: `save_transcript`, `get_transcript`, `save_ads_json`, `save_prompt`, `delete_transcript`, `delete_ads_json`, `cleanup_episode_files`

---

## [0.1.25] - 2025-11-28

### Fixed
- Episode cleanup not deleting files from correct path
  - Files were not being removed during retention cleanup due to incorrect directory path
  - Storage usage now properly decreases after cleanup

---

## [0.1.24] - 2025-11-27

### Added
- All-time cumulative "Time Saved" tracking
  - Persists total time saved across all processed episodes, even after episodes are deleted
  - Displayed in Settings page under System Status
  - Available via API at `/api/v1/system/status` in `stats.totalTimeSaved`
- New `stats` database table for persistent cumulative metrics

### Changed
- Episode detail page: changed "X:XX removed" to "X:XX time saved" wording

---

## [0.1.23] - 2025-11-27

### Changed
- Episode detail page now shows processed duration (time after ads removed) instead of original
- Version link in Settings now goes to main repository instead of specific release tag

### Added
- Time saved display next to "Detected Ads" heading (e.g., "Detected Ads (5) - 3:54 time saved")

---

## [0.1.22] - 2025-11-27

### Added
- Version number in Settings now links to GitHub releases page
- Podcast artwork displayed on episode detail page (responsive sizing for mobile/desktop)

### Fixed
- Episode detail page mobile UI:
  - Smaller title on mobile devices
  - Status badge and Reprocess button flow inline with metadata
  - Reduced padding on mobile
- Episode duration displaying with excessive decimal precision (e.g., "2:43:4.450500...")
  - Now correctly formats as HH:MM:SS
- Audio playback 403 error when UI and feed are on different domains
  - Audio player now uses relative path instead of full URL from API

---

## [0.1.21] - 2025-11-27

### Changed
- Improved ad detection system prompt with:
  - List of 90+ common podcast sponsors for higher confidence detection
  - Common ad phrases (promo codes, vanity URLs, sponsor transitions)
  - Ad duration hints (15-120 seconds typical)
  - One-shot example for improved model accuracy
  - Confidence score field (0.0-1.0) in ad segment output
- Ad detector now parses and includes confidence scores in results
  - Backward compatible: defaults to 1.0 if not provided by older prompts

### Note
- Existing users with customized system prompts in Settings will keep their prompts
- New installations and users who reset to defaults will get the improved prompt

---

## [0.1.20] - 2025-11-27

### Fixed
- Mobile UI improvements:
  - Feed detail page: Hide long feed URL on mobile, show "Copy Feed URL" button instead
  - Dashboard: Convert "Refresh All" and "Add Feed" buttons to icon-only on mobile

### Changed
- Consolidated all screenshots into docs/screenshots/ folder
- Updated README.md screenshot paths

---

## [0.1.19] - 2025-11-27

### Added
- Alphabetical sorting of podcasts by name on dashboard
- List/tile view toggle on dashboard
  - Grid view: card-based layout (default, previous behavior)
  - List view: compact row layout showing more feeds at once
  - View preference persisted to localStorage

---

## [0.1.18] - 2025-11-27

### Added
- Force reprocess episode feature via API and UI
  - New endpoint: POST `/api/v1/feeds/{slug}/episodes/{episode_id}/reprocess`
  - "Reprocess" button on episode detail page
  - Deletes cached files (audio, transcript, ads) and re-runs full pipeline
- API field name compatibility for frontend
  - Added `id`, `published`, `duration`, `ad_count` fields to episode list response
  - Added `processed_url`, `ad_segments`, `transcript` fields to episode detail response
  - Status now returns `completed` instead of `processed` for frontend compatibility

### Fixed
- Episode list showing "Invalid Date" - API now returns `published` field
- Episode links returning 404 with "undefined" - API now returns `id` field
- Episode detail page not showing ads/transcript - field names now match frontend types

### Changed
- Removed file-based logging (`server.log`) - logs only to console now
  - Docker captures stdout, eliminating unbounded log file growth

---

## [0.1.17] - 2025-11-27

### Fixed
- Audio download failing with 403 Forbidden on certain podcast CDNs (e.g., Acast)
  - Added browser-like User-Agent headers to audio and artwork download requests
  - CDNs were blocking requests with default python-requests User-Agent

---

## [0.1.16] - 2025-11-27

### Fixed
- Container fails to start with "Permission denied: /app/entrypoint.sh"
  - Changed entrypoint.sh permissions from 711 to 755 (readable by all users)
- RETENTION_PERIOD documentation was misleading (said "days" but code uses minutes)
  - Updated README, docker-compose, and Dockerfile to clarify it's in minutes
  - Changed default from 30 to 1440 (24 hours) to match original intent

---

## [0.1.15] - 2025-11-27

### Fixed
- Favicon not loading - file had restrictive permissions (600) preventing non-root access
- Set proper read permissions (644) on all static UI files in Docker build

---

## [0.1.14] - 2025-11-27

### Fixed
- Permission denied error when running as any non-root user
  - HuggingFace cache now writes to `/app/data/.cache` (inside the mounted volume)
  - Added entrypoint.sh to create required directories at runtime
  - Model downloads on first run to the mounted volume (owned by running user)
  - Works with any `user:` setting in docker-compose, not just 1000:1000

### Changed
- Removed pre-downloaded model from image (was being hidden by volume mount anyway)
- Switched from CMD to ENTRYPOINT for better container initialization

---

## [0.1.13] - 2025-11-27

### Fixed
- Permission denied error when running as non-root user (user: 1000:1000 in docker-compose)
  - Set HuggingFace cache to `/app/data/.cache` instead of `/.cache`
  - Pre-download Whisper model to user-accessible location during build
  - Set proper permissions (777) on data and cache directories

---

## [0.1.12] - 2025-11-27

### Fixed
- Claude JSON parsing - improved extraction with multiple fallback strategies:
  - First tries markdown code blocks
  - Then finds all valid JSON arrays and uses the last one with ad structure
  - Falls back to first-to-last bracket extraction
- System prompt simplified to explicitly request JSON-only output (no analysis text)

### Added
- Search icon in header linking to Podcast Index for finding podcast RSS feeds

---

## [0.1.11] - 2025-11-27

### Fixed
- Removed torch dependency - use ctranslate2 for CUDA detection (fixes "No module named torch" error)
- JSON parsing for Claude responses - now strips markdown code blocks before parsing
- MIME type error behind reverse proxy - return 404 for missing assets instead of index.html
- Asset fallback for Docker - if volume-mounted assets folder is empty, falls back to builtin assets

### Changed
- GPU logging now shows device count instead of GPU name/memory (torch no longer required)
- Dockerfile copies assets to both `/app/assets/` and `/app/assets_builtin/` for fallback support

---

## [0.1.10] - 2025-11-27

### Added
- Mobile navigation hamburger menu - Settings now accessible on mobile devices
- Podcast Index link on Dashboard - helps users find podcast RSS feeds at podcastindex.org
- Version logging on startup - logs app version when server starts
- GPU discovery logging - logs CUDA GPU name and memory when available

### Fixed
- Suppressed noisy ONNX Runtime GPU discovery warnings in logs
- Better Claude JSON parsing error logging - logs raw response for debugging

---

## [0.1.9] - 2025-11-27

### Fixed
- Podcast files now saved in correct location: `/app/data/podcasts/{slug}/` instead of `/app/data/{slug}/`

---

## [0.1.8] - 2025-11-27

### Fixed
- Auto-clear invalid Claude model IDs from database instead of just warning
- Fixed invalid model ID examples in openapi.yaml

---

## [0.1.7] - 2025-11-27

### Fixed
- Assets path resolution - use absolute path based on script location instead of relative path

---

## [0.1.6] - 2025-11-27

### Changed
- Version bump for Portainer cache refresh

---

## [0.1.5] - 2025-11-27

### Fixed
- Claude API 404 error - corrected model IDs (claude-sonnet-4-5-20250929, not 20250514)
- Duplicate log entries - clear existing handlers before adding new ones
- Feed slugs defaulting to "rss" - now generates slug from podcast title

### Changed
- Slug generation now fetches RSS feed to get podcast name (e.g., "tosh-show" instead of "rss")
- Added Claude Opus 4.5 to available models list
- Model validation now checks against VALID_MODELS list

---

## [0.1.3] - 2025-11-27

### Fixed
- Claude API 404 error - corrected invalid model IDs in DEFAULT_MODEL and fallback models
- Empty assets folder in Docker image - assets/replace.mp3 now properly included

### Changed
- Default model changed from invalid claude-opus-4-5-20250929 to claude-sonnet-4-5-20250514
- Updated fallback model list with correct model IDs:
  - claude-sonnet-4-5-20250514 (Claude Sonnet 4.5)
  - claude-sonnet-4-20250514 (Claude Sonnet 4)
  - claude-opus-4-1-20250414 (Claude Opus 4.1)
  - claude-3-5-sonnet-20241022 (Claude 3.5 Sonnet)

### Note
- Users must re-select model from Settings UI after update to save a valid model ID to database

---

## [0.1.2] - 2025-11-26

### Fixed
- Version display showing "unknown" - fixed Python import path for version.py
- GET /api/v1/feeds/{slug} returning 405 - added missing GET endpoint
- openapi.yaml 404 - added COPY to Dockerfile
- Copy URL showing "undefined" - updated frontend types to use camelCase (feedUrl, sourceUrl, etc.)
- Request logging disabled - changed werkzeug log level from WARNING to INFO

### Changed
- Removed User Prompt Template from Settings UI (unnecessary - system prompt contains all instructions)
- Added API Documentation link to Settings page

### Technical
- Docker image: ttlequals0/podcast-server:0.1.2

---

## [0.1.0] - 2025-11-26

### Added
- Web-based management UI (React + Vite) served at /ui/
- SQLite database for configuration and episode metadata storage
- REST API for feed management, settings, and system status
- Automatic migration from JSON files to SQLite on first startup
- Podcast artwork caching during feed refresh
- Configurable ad detection system prompt and Claude model via web UI
- Episode retention with automatic and manual cleanup
- Structured logging for all operations
- Dark/Light theme support in web UI
- Feed management: add, delete, refresh single or all feeds
- Copy-to-clipboard for feed URLs
- System status and statistics endpoint
- Cloudflared tunnel service in docker-compose for secure remote access
- OpenAPI documentation (openapi.yaml)

### Changed
- Data storage migrated from JSON files to SQLite database
- Ad detection prompts now stored in database and editable via UI
- Claude model is now configurable via API/UI
- Removed config/ directory dependency (feeds now managed via UI/API)
- Improved logging with categorized loggers and structured format

### Technical
- Added flask-cors for development CORS support
- Multi-stage Docker build for frontend assets
- Added RETENTION_PERIOD environment variable for episode cleanup
- Docker image: ttlequals0/podcast-server:0.1.0
