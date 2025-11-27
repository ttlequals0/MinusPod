# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
