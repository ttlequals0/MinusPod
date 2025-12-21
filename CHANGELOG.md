# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.152] - 2025-12-21

### Added
- **Health Check Endpoint**
  - GET /api/v1/health returns system health status
  - Checks database connectivity, storage writability, and queue availability
  - Returns 200 (healthy) or 503 (unhealthy) with detailed check results
  - Added to OpenAPI specification

- **Graceful Shutdown**
  - Server now handles SIGTERM/SIGINT signals gracefully
  - Waits up to 5 minutes for current processing to complete before exit
  - Background threads use shutdown_event for clean termination
  - Logs shutdown progress and current processing status

- **Rate Limiting**
  - Added flask-limiter for API rate limiting
  - Default limits: 200 requests/minute, 1000 requests/hour
  - Stricter limits on expensive endpoints:
    - Add feed: 10/minute
    - Refresh feed: 10/minute
    - Refresh all feeds: 2/minute
    - Reprocess episode: 5/minute
    - Retry ad detection: 5/minute

- **Database Backup Automation**
  - Automatic SQLite backup during cleanup cycle (every 15 minutes)
  - Uses SQLite backup API for consistency during writes
  - Backups stored in data/backups/ with timestamps
  - Retains last 7 backups by default (configurable)

- **Structured Logging (JSON Format)**
  - New LOG_FORMAT environment variable ('text' or 'json')
  - JSON format outputs structured logs for log aggregators
  - Includes timestamp, level, logger, message, and exception info
  - Default remains 'text' for human-readable output

### Changed
- **Request Timeouts**
  - Claude API calls now have 120-second timeout
  - Audio downloads use (10s connect, 300s read) timeout tuple
  - RSS feed fetching already had 30-second timeout

---

## [0.1.151] - 2025-12-21

### Fixed
- **Race Condition in ProcessingQueue**
  - Fixed lock release order in ProcessingQueue.release()
  - State was cleared before lock release, causing potential race conditions
  - Now releases lock first, then clears state

- **Auto-Process Tight Loop**
  - Added exponential backoff when queue is busy (30s to 5min max)
  - Prevents CPU spin when processing queue is perpetually occupied
  - Backoff resets on successful processing start

- **Retry Logic for Transient vs Permanent Errors**
  - Errors are now classified as transient (network, rate limits) or permanent (invalid data)
  - Only transient errors increment retry count
  - Permanent errors immediately mark episode as permanently_failed
  - Prevents wasting retries on errors that won't resolve

- **False Positive Handling in Pattern Matching**
  - Pattern matching now respects user-rejected ads (false positives)
  - Ads previously marked as false positive are excluded from pattern matches
  - Applies to both audio fingerprint and text pattern matching stages

### Added
- **was_cut Flag for Ad Markers**
  - Ad markers now include `was_cut: true/false` to indicate if ad was removed from audio
  - Ads with confidence < 80% are kept in audio but flagged as `was_cut: false`
  - Helps UI distinguish between cut and uncut ads

---

## [0.1.150] - 2025-12-21

### Fixed
- **Volume Analyzer UTF-8 Encoding Bug**
  - Fixed crash when FFMPEG ebur128 filter outputs non-UTF-8 characters
  - Same fix as v0.1.146 but for the audio analysis volume measurement
  - Root cause of "Single-pass loudness measurement failed" errors

---

## [0.1.149] - 2025-12-21

### Added
- **Clear Auto-Process Queue Endpoint**
  - DELETE /api/v1/system/queue - clears all pending items from auto-process queue
  - Useful for clearing backlog when queue was filled before 48-hour filter

---

## [0.1.148] - 2025-12-21

### Fixed
- **Episode Published Dates Now Show Correct Values**
  - Previously, all episodes showed their database creation date as the published date
  - Now stores and displays actual RSS pubDate (when episode was originally published)
  - Added `published_at` column to episodes table
  - API returns `published_at` with fallback to `created_at` for backward compatibility

### Changed
- **Auto-Process Queue**
  - Queue now stores episode published date for passing through to processing
  - Added `published_at` column to auto_process_queue table
  - Reprocess endpoint now fetches and stores pubDate from RSS

---

## [0.1.147] - 2025-12-21

### Fixed
- **Auto-Process Only Recent Episodes**
  - Now only queues episodes published within the last 48 hours
  - Prevents processing entire backlog when adding new podcasts
  - Parses RSS publish dates (RFC 2822 format) to determine recency

- **Pagination UI Improvements**
  - History page: Pagination now visible on mobile (moved outside desktop-only div)
  - History/Patterns pages: Added page number buttons with ellipsis for quick navigation
  - Example: 1 2 3 ... 10 for easier page jumping

- **Episode Detail Header**
  - Cleaner layout: Title + Edit button on first row
  - Pass info and time saved on separate line below
  - Less cluttered appearance on all screen sizes

### Changed
- **OpenAPI Documentation**
  - Added missing PATCH /feeds/{slug} endpoint
  - Added GET /system/queue endpoint for auto-process queue status
  - Added autoProcessEnabled to Settings schema
  - Added autoProcessOverride to Feed schema
  - Added totalPages to history response
  - Updated version to 0.1.147

---

## [0.1.146] - 2025-12-21

### Added
- **Auto-Process New Episodes**
  - Global setting to automatically download and process new episodes when feeds refresh (default: ON)
  - Per-podcast override (Use Global / Enable / Disable) in feed settings
  - Background queue processor handles auto-processing one at a time
  - New auto_process_queue table tracks pending auto-downloads

- **Retry Limit for Failed Episodes**
  - Episodes now track retry count (max 3 attempts)
  - After 3 failures, episode marked as `permanently_failed` (HTTP 410)
  - Manual reprocess resets retry counter

### Fixed
- **FFMPEG UTF-8 Encoding Bug**
  - Fixed crash when FFMPEG outputs non-UTF-8 characters in stderr
  - Now uses `errors='replace'` for safe decoding
  - Root cause of stuck episodes that kept failing

- **History Page Pagination**
  - Backend now returns `totalPages` field
  - Pagination controls work correctly

### Changed
- **Mobile UI Improvements**
  - Patterns page: Card layout on mobile, pagination added (20 per page)
  - History page: Card layout on mobile
  - Feed Detail: Stacked settings layout on mobile, auto-process control added
  - Episode Detail: Pencil icon on Edit Ads button, full-width action buttons
  - All touch targets increased to 40px+ for mobile

---

## [0.1.145] - 2025-12-20

### Changed
- **Theme Update: Bootswatch Slate**
  - Dark mode now uses Slate theme colors (#272b30 background, cyan accents)
  - Light mode updated to Slate-inspired light variant
  - Added Roboto font from Google Fonts
  - Responsive design applies to all screen sizes including mobile

- **Documentation Screenshots Updated**
  - New desktop and mobile screenshots for all major pages
  - README now shows side-by-side desktop/mobile views
  - Screenshots reflect new Slate theme

---

## [0.1.144] - 2025-12-19

### Added
- **Delete Pattern UI**
  - Pattern detail modal now has Delete button with confirmation
  - Allows removing duplicate or unwanted patterns from the database

### Fixed
- **Rejected Ads Section Badges**
  - Rejected ads now show "Confirmed" or "Not Ad" badges when corrections applied
  - Buttons hidden after correction is made
  - Consistent with badge styling in detected ads section

---

## [0.1.143] - 2025-12-19

### Added
- **Add New Sponsors on the Fly**
  - Pattern detail modal now has "Add New" button when entering unknown sponsor
  - Creates sponsor in database immediately for autocomplete
  - Shows helper text when sponsor doesn't exist in list

- **Pattern Management API Endpoints**
  - DELETE `/patterns/<id>` to remove individual patterns
  - POST `/patterns/deduplicate` for manual deduplication trigger
  - POST `/patterns/merge` to merge similar patterns into one

### Fixed
- **Navigation Arrows Only Work Once**
  - Fixed stale closure issue in transcript editor navigation
  - Arrow buttons now correctly use current selected ad index
  - Uses ref pattern to avoid capturing stale state in callbacks

- **Rejected Ads Buttons No Visual Feedback**
  - "Confirm as Ad" and "Not an Ad" buttons now show save status
  - Dynamic text: "Saving...", "Saved!", "Error!" based on state
  - Visual styling changes to indicate success/error states

- **Audio Analysis Override Not Visible**
  - Moved audio analysis control out of "Edit" mode
  - Now always visible as inline dropdown on podcast detail page
  - Shows status badge when override is active

---

## [0.1.142] - 2025-12-19

### Added
- **Podcast-Level Audio Analysis Override**
  - Per-podcast setting to enable/disable audio analysis independent of global setting
  - Three options: Use Global (default), Enable, Disable
  - UI in podcast settings page with visual indicator badge
  - Database migration for new `audio_analysis_override` column

- **Sponsor Autocomplete in Patterns UI**
  - Pattern detail modal now shows suggestions when editing sponsor
  - Fetches known sponsors from database for autocomplete
  - Still allows free text entry for new sponsors

- **Expandable Ad Reason in Transcript Editor**
  - "Show reason" button in ad header to expand detection reason
  - Displays why the segment was flagged as an ad
  - Collapsible to save screen space

- **Confirm/Not-Ad Actions for Rejected Ads**
  - Rejected ads section now has "Confirm as Ad" and "Not an Ad" buttons
  - Allows overriding the validator's rejection decision
  - Corrections are applied during reprocessing

### Fixed
- **"Not an Ad" Jumping to Beginning of Transcript**
  - Selected ad index now preserved across query refetches
  - Uses controlled component pattern to lift state to parent
  - Confirming or rejecting ads now advances to next ad correctly

- **Navigation Arrows (Removed Top-Left, Made Center Functional)**
  - Removed duplicate navigation arrows from header
  - Center navigation bar now has functional prev/next buttons
  - Visible on desktop and landscape mobile modes

- **Duplicate Patterns Display**
  - Enhanced deduplication to merge patterns with same text but different sponsors
  - Keeps pattern with highest confirmation count
  - Sums confirmation and false positive counts when merging
  - Preserves sponsor name from most confirmed pattern

---

## [0.1.141] - 2025-12-19

### Added
- **Apply User-Marked False Positives During Reprocessing**
  - When you mark a segment as "not an ad" in the UI, it's now remembered
  - On reprocess, any detected ads overlapping 50%+ with marked false positives are auto-rejected
  - Prevents the same false positive from being cut repeatedly
  - New database method `get_false_positive_corrections()` for loading corrections
  - Validator logs when corrections are loaded and applied

---

## [0.1.140] - 2025-12-19

### Fixed
- **Auto-Reject Segments Where Reason Indicates Not an Ad**
  - Validator now checks reason text for patterns like "not an advertisement", "episode content", "false positive"
  - Segments with these patterns are auto-rejected regardless of confidence score
  - Prevents false positives where Claude detected a segment but noted it's not actually an ad

---

## [0.1.139] - 2025-12-19

### Fixed
- **Music Detection Progress Still Showing 100% Repeatedly**
  - Capped streaming progress at 99% during processing
  - Single 100% message logged only after streaming loop completes
  - Prevents confusing repeated "100%" logs during music detection

---

## [0.1.138] - 2025-12-19

### Added
- **Minimum Confidence Threshold for Ad Cutting (80%)**
  - Ads with confidence below 80% are now kept in audio to prevent false positives
  - Low-confidence ads are still stored and displayed in UI but not cut
  - Addresses false positive cuts on long-form conversational podcasts

### Fixed
- **Music Detection Progress Calculation Bug**
  - Fixed progress reporting showing >100% for long episodes
  - Progress now correctly tracks actual advancement (excludes block overlap)
  - Affected streaming analysis for episodes >1 hour

---

## [0.1.137] - 2025-12-19

### Fixed
- **Infinite Loop in Chunked Speaker Diarization**
  - Fixed bug where final chunk would loop forever when chunk overlap > remaining audio
  - Added explicit exit condition when `chunk_end >= total_duration`
  - Affected episodes >3 hours using chunked processing with overlap

---

## [0.1.136] - 2025-12-19

### Added
- **Whisper Model Unloading Before Audio Analysis**
  - Automatically unloads Whisper model after transcription completes
  - Frees ~5-6GB memory before speaker diarization starts
  - Model lazy-reloads on next transcription request
  - New public `WhisperModelSingleton.unload_model()` method

### Changed
- **Reduced Chunk Size for 3-4 Hour Episodes**
  - Speaker diarization now uses 20-minute chunks (was 30 minutes) for episodes >3 hours
  - Reduces peak memory by ~33% per chunk
  - Increased overlap to 60s for better speaker matching across boundaries
  - Allows very long episodes to complete with 24GB system RAM

---

## [0.1.135] - 2025-12-19

### Added
- **Per-Component Timeouts for Audio Analysis**
  - Each analysis component (volume, music, speaker) now has its own timeout
  - Timeouts scale dynamically based on episode duration (~2s/min for volume, ~5s/min for music, ~8s/min for speaker)
  - Prevents indefinite hangs on any single component

- **Graceful Degradation in Audio Analysis**
  - If one component fails or times out, processing continues with remaining components
  - Partial results are still usable for ad detection
  - Errors are logged but don't abort entire analysis

- **Per-Chunk Retry Logic for Speaker Analysis**
  - Failed chunks are retried up to 2 times before skipping
  - CUDA OOM errors trigger memory clearing and 10s delay before retry
  - Other errors get 5s delay between retries
  - Logging shows retry attempts for debugging

- **Enhanced Memory Management**
  - `torch.cuda.synchronize()` called after CUDA operations to ensure completion
  - Memory logging on retry attempts for debugging
  - Aggressive garbage collection between chunks

- **Dynamic Chunk Configuration**
  - Chunk size and overlap now scale based on episode duration
  - 4+ hour episodes: 40min chunks with 60s overlap
  - 3-4 hour episodes: 30min chunks with 45s overlap
  - Stricter speaker matching threshold for longer episodes

### Changed
- Audio analysis now uses ThreadPoolExecutor for cross-platform timeout support

---

## [0.1.134] - 2025-12-19

### Added
- **Desktop Transcript Editor Navigation**
  - Added prev/next arrows to desktop header for navigating between ads
  - Improved desktop action button visibility with distinct colors (green for Confirm, border for Reset)

### Fixed
- **Jump Button Highlighting**
  - Jump button now correctly highlights the target ad instead of wrong section
  - Added tolerance for floating-point precision in ad time matching
- **Pattern Popup Podcast Name**
  - Pattern detail modal now shows podcast name instead of numeric ID for podcast-scoped patterns

---

## [0.1.133] - 2025-12-19

### Fixed
- **Speaker Embedding Extraction**
  - Handle pyannote embedding model returning numpy arrays instead of torch tensors
  - Fixes "'numpy.ndarray' object has no attribute 'cpu'" error

---

## [0.1.132] - 2025-12-19

### Added
- **Granular Status Updates for Audio Analysis**
  - Status bar now shows each analysis phase: "analyzing: volume", "analyzing: music", "analyzing: speakers"
  - Progress updates at each phase (25% -> 30% -> 35% -> 40% -> 50%)
  - No longer shows "transcribing" during the entire audio analysis

### Fixed
- **Streaming Music Detection Progress Calculation**
  - Progress now tracks actual samples processed instead of assuming fixed block size
  - Progress capped at 100% to prevent >100% display
  - Better error logging with exception type for debugging failures

---

## [0.1.130] - 2025-12-18

### Added
- **Streaming Music Detection for Long Episodes**
  - Episodes over 1 hour now use `librosa.stream()` for blockwise audio processing
  - Avoids loading entire audio file into memory
  - Processes in ~4-minute blocks with progress logging every 10 blocks
  - Short episodes (< 1 hour) continue using standard loading for simplicity

---

## [0.1.129] - 2025-12-18

### Added
- **Chunked Speaker Analysis for Long Episodes**
  - Episodes over 1 hour now processed in 30-minute chunks to prevent OOM crashes
  - Uses speaker embedding similarity to match speakers across chunk boundaries
  - Memory cleared between chunks via garbage collection and CUDA cache clearing
  - Graceful per-chunk error handling - continues processing if a chunk fails
  - Configurable chunk duration (1800s), overlap (30s), and duration threshold (3600s)

### Fixed
- **Mobile Episode Description Overflow**
  - Added `break-words` CSS class to episode description text
  - Long URLs and unbroken text now wrap correctly on mobile devices

---

## [0.1.128] - 2025-12-18

### Added
- **Sponsor Extraction from Ad Text**
  - Automatically extracts sponsor names from ad text by detecting URLs (hex.ai, thisisnewjersey.com)
  - Also detects "brought to you by", "sponsored by" patterns
  - Migration extracts sponsors for existing patterns on startup
  - Real-time pattern creation now auto-extracts sponsor when not provided

- **Podcast Name in Patterns**
  - Patterns API now returns `podcast_name` and `podcast_slug` via JOIN
  - Patterns page shows podcast name in scope badge instead of generic "Podcast"
  - TypeScript types updated to include new fields

---

## [0.1.127] - 2025-12-18

### Fixed
- **Pattern Deduplication**
  - Added `deduplicate_patterns()` migration to remove duplicate patterns on startup
  - Real-time pattern creation now checks for existing patterns with same text before creating new ones
  - Backfill now links corrections to existing patterns instead of creating duplicates
  - Added `find_pattern_by_text()` method for deduplication lookups
  - Fixes issue where confirming the same ad multiple times created duplicate patterns

---

## [0.1.126] - 2025-12-18

### Added
- **Pattern Backfill Migration**
  - Retroactively creates patterns from existing 'confirm' corrections submitted before v0.1.125
  - Runs on startup, finds corrections without pattern_id
  - Extracts ad text from transcript using timestamps in original_bounds
  - Links created patterns back to the original corrections
  - Your 13 previous confirmations will now populate the Patterns page

---

## [0.1.125] - 2025-12-18

### Added
- **Pattern Learning from User Confirmations**
  - When user confirms a Claude-detected ad (no pattern_id), system now creates a new pattern
  - Extracts ad text from transcript using VTT timestamps
  - Creates podcast-scoped pattern with intro/outro variants
  - Minimum 50 characters required for TF-IDF matching
  - Patterns page will now populate as users confirm ad detections
  - Helper function `extract_transcript_segment()` for VTT transcript parsing

---

## [0.1.124] - 2025-12-18

### Fixed
- **History Page Crash**
  - Fixed `TypeError: Cannot read properties of null (reading 'toFixed')` on History page
  - Root cause: Backfilled records have `processingDurationSeconds: null` but `formatDuration()` didn't handle null
  - Solution: Added null check to return '-' for missing duration values

---

## [0.1.123] - 2025-12-18

### Fixed
- **History Data Backfill Bug**
  - Fixed backfill query that was finding zero episodes to migrate
  - Root cause: Query required `processed_at IS NOT NULL` but this column was never populated historically
  - Solution: Use `COALESCE(processed_at, updated_at)` for timestamp, check status `IN ('processed', 'failed')` instead of `'completed'`

---

## [0.1.122] - 2025-12-18

### Added
- **Button Labels on Transcript Editor**
  - Feedback buttons now show text labels below icons: Not Ad, Reset, Confirm, Save
  - Improved mobile discoverability with stacked icon+text layout
  - Buttons fit on 320px+ screens with tighter spacing

- **History Data Backfill**
  - Automatically migrates existing processed episodes to processing_history table on startup
  - History page now shows all previously processed episodes (not just new ones)
  - Backfill runs once per startup, skipping episodes already in history

---

## [0.1.121] - 2025-12-18

### Added
- **Processing History Page**
  - New `/history` page showing all episode processing history
  - Stats summary: total processed, completed, failed, total ads detected
  - Sortable table columns: processed date, duration, ads detected, reprocess number
  - Filter by status (all/completed/failed) and by podcast
  - Pagination for large history sets
  - Links to podcast and episode detail pages
  - Error message tooltip on failed entries

- **History Export**
  - Export CSV and JSON buttons for processing history
  - Backend API: `GET /api/v1/history`, `GET /api/v1/history/stats`, `GET /api/v1/history/export`
  - Database: New `processing_history` table tracking all processing attempts

- **Processing History Recording**
  - Records processing history for both successful and failed episode processing
  - Tracks: podcast, episode, processed time, duration, ads detected, reprocess count, status, error message

### Fixed
- **Mobile Jump Button Bug**
  - Fixed: Clicking "Jump" then "Play" would start from beginning instead of jumped position
  - Root cause: `handlePlayPause` was resetting `currentTime` when outside ad bounds
  - Solution: Added `preserveSeekPosition` state to preserve jump position on first play

- **Transcript Scroll on Jump**
  - Fixed: Jump button didn't scroll transcript to the jumped-to time
  - Added `scrollToTime` helper function triggered on jump

- **Mobile Ad Description Layout**
  - Fixed: Ad description text was cramped on mobile devices
  - Moved description to full-width row below time badges and controls

---

## [0.1.120] - 2025-12-18

### Added
- **Pattern Management UI** (Gap 1)
  - New `/patterns` page for viewing and managing ad patterns
  - Filterable by scope (Global/Network/Podcast)
  - Searchable by sponsor name, text template, network
  - Sortable columns: scope, sponsor, confirmations, false positives, last matched
  - Toggle to show/hide inactive patterns
  - Pattern detail modal with edit capabilities

- **Network Override UI** (Gap 2)
  - Dropdown in Feed Detail to manually set network ID
  - Shows "Override" (orange) or "Detected" (green) badge
  - GET /networks API endpoint lists available networks
  - "Auto-detect" option clears override

- **Reprocessing Mode Dropdown** (Gap 3 - BUG FIX)
  - Fixed bug where reprocess mode was accepted but not actually used
  - Added `reprocess_mode` column to episodes table
  - "Reprocess" mode: Uses pattern DB + Claude (default)
  - "Full Analysis" mode: Skips pattern DB, Claude analyzes fresh
  - Mode passed to ad_detector via `skip_patterns` parameter

- **Queue Priority** (Gap 4)
  - Added `reprocess_requested_at` column to track reprocess requests
  - Column cleared after processing completes

- **Feedback UI Enhancements** (Gap 6)
  - "Not an Ad" button now larger and more prominent (right side)
  - "Confirm" button now secondary/muted styling
  - Scope badges on detected ads (Global/Network/Podcast)
  - Shows network name for network-scoped patterns

### Fixed
- **CUDA OOM for long episodes**
  - Adaptive batch sizing based on audio duration
  - Episodes >120 min use batch_size=4 (was 16)
  - Auto-retry with smaller batch on OOM error
  - Probes duration via ffprobe before transcription
  - Fixes windows-weekly (2h36m) transcription failures

### Changed
- `networkIdOverride` type changed from boolean to string|null

---

## [0.1.119] - 2025-12-17

### Added
- Mobile-first transcript editor optimization
  - **Touch targets**: All buttons now 44-48px for industry-standard accessibility
  - **Swipe gestures**: Swipe left/right on transcript to navigate between ads
  - **Haptic feedback**: Vibration on boundary changes, save, confirm, reject actions
  - **Bottom sheet audio**: Apple Podcasts-style collapsible audio player on mobile
  - **Draggable progress bar**: Touch-drag seeking with visual thumb indicator
  - **Icon-only buttons**: Compact X, reset, check, save icons on mobile (labels in expanded mode)
  - **Landscape mode**: Compact layout with hidden ad selector, swipe navigation hint

### Changed
- Transcript segments now have better spacing (p-3 on mobile, space-y-2)
- Ad selector shows only start time to fit more buttons
- Mobile toggle button includes chevron indicator
- Expanded player shows prev/next ad navigation buttons

---

## [0.1.118] - 2025-12-17

### Fixed
- Mobile transcript editor now shows transcript content
  - Boundary controls and touch mode toggles collapse by default on mobile
  - Tap "Adjust Boundaries" to expand controls when needed
  - Action buttons now horizontal on mobile with smaller text
  - Reclaims ~150px of vertical space for transcript display
  - Desktop layout unchanged (controls always visible)

---

## [0.1.117] - 2025-12-17

### Added
- Correction badges show on ad markers in episode detail
  - "Confirmed" (green) for ads marked as correct
  - "Not Ad" (yellow) for false positives
  - "Adjusted" (blue) for boundary adjustments
  - Badges persist across page refreshes (loaded from database)
- Backend support for episode corrections lookup
  - New `get_episode_corrections(episode_id)` method in database.py
  - Episode API now includes `corrections` array in response

### Fixed
- Mobile transcript editor height reduced to prevent sticky controls hiding content
  - Changed from 70vh to 50vh on mobile (50vh sm:70vh)
  - Reduced max-height from 800px to 600px on mobile (600px sm:800px)

---

## [0.1.116] - 2025-12-17

### Fixed
- Sticky positioning now works in transcript editor
  - Added fixed height (70vh, max 800px) to container to enable internal scrolling
  - Sticky top/bottom sections now stay visible while scrolling transcript
  - Previous issue: `h-full` with no parent height constraint caused no internal scroll

---

## [0.1.115] - 2025-12-17

### Fixed
- Transcript editor buttons now always visible without scrolling
  - Sticky header keeps ad selector, boundary controls visible at top
  - Sticky footer keeps audio player, action buttons visible at bottom
  - Only the transcript content scrolls

### Added
- Save feedback on action buttons
  - Buttons show "Saving..." while API call in progress
  - Buttons show "Saved!" (green) on success for 2 seconds
  - Buttons show "Error!" (red) on failure for 3 seconds
  - Buttons disabled during save to prevent double-clicks
- Auto-scroll transcript when selecting ad from selector
  - Clicking ad time button (e.g., "0:00-1:11") scrolls transcript to that ad
  - Added data-segment-start attribute for efficient element lookup

### Improved
- Mobile touch targets for ad selector buttons (px-3 py-2 vs px-2 py-1)
- Added momentum scrolling to ad selector with touch-pan-x
- Better overflow handling with overflow-hidden on container

---

## [0.1.114] - 2025-12-17

### Fixed
- Ad correction save functionality now works
  - Wired up submitCorrection API call in EpisodeDetail.tsx
  - Corrections (confirm/reject/adjust) now persist to database
  - Previously just logged to console with TODO comment

### Added
- Shift-click range selection for ad boundaries
  - Shift+Click on transcript segment sets END boundary
  - Alt/Cmd+Click on transcript segment sets START boundary
  - Visual indicators show boundary segments (green left border for start, orange right for end)
- Mobile touch controls for ad editing
  - Mode toggle buttons: Seek Mode / Set Start / Set End
  - Double-tap segment to set START boundary
  - Long-press (500ms) segment to set END boundary
  - Mobile-specific instructions replace keyboard hints
- Auto-focus editor for keyboard shortcuts
  - TranscriptEditor now auto-focuses when opened
  - Focus ring shows when editor has keyboard focus

### Improved
- Keyboard shortcuts hint now includes click modifiers
- Added select-none to transcript segments to prevent text selection during interaction

---

## [0.1.113] - 2025-12-17

### Fixed
- Episode count bug: Single feed API endpoint now correctly returns episode counts
  - Modified `get_podcast_by_slug()` to JOIN episodes table for counts
  - Matches behavior of feed list endpoint which already had correct counts

### Changed
- Post-roll ad handling: Skip remaining content if < 30 seconds after last ad
  - Prevents post-roll ad residue from appearing in processed audio
  - Configured threshold of 30 seconds catches most post-roll ads
- Short ad detection filtering: Skip removal of ads < 10 seconds
  - Very short detections are often false positives or audio gaps
  - These segments are now left in the processed audio

### Improved
- Mobile UI for ad marking in TranscriptEditor
  - Larger touch targets for nudge buttons on mobile (p-2 vs p-1)
  - Larger play button on mobile (p-3 vs p-2)
  - Taller progress bar on mobile for easier tapping
  - Keyboard shortcuts hint hidden on mobile (not useful)
  - Action buttons stack vertically on mobile for easier tapping
  - Added `touch-manipulation` and `active:` states for better touch feedback

---

## [0.1.112] - 2025-12-17

### Added
- Network display and edit on feed page
  - Shows Network and DAI Platform labels when available
  - Inline edit capability to set/update network and DAI platform
  - Calls PATCH /api/v1/feeds/{slug} to save changes
- Jump buttons on ad segments
  - Each detected ad now has a "Jump" button
  - Opens TranscriptEditor and seeks to that timestamp
  - Makes reviewing specific ads much easier
- Clickable progress bar in TranscriptEditor
  - Click anywhere on the progress bar to seek to that position
  - Bar grows on hover for easier clicking
  - Supports initialSeekTime prop for external seeking

---

## [0.1.111] - 2025-12-17

### Fixed
- Speaker diarization tensor size error on audio boundary
  - Added audio padding to prevent "Sizes of tensors must match" error
  - Preprocesses audio to align to 10-second chunk boundaries (160000 samples at 16kHz)
  - Falls back to direct file processing if preprocessing fails

### Added
- Network fields now exposed in API responses
  - GET /api/v1/feeds returns networkId, daiPlatform for each feed
  - GET /api/v1/feeds/{slug} returns networkId, daiPlatform, networkIdOverride
- PATCH /api/v1/feeds/{slug} endpoint for updating feed settings
  - Supports networkId, daiPlatform, networkIdOverride, title, description
  - Allows manual override of auto-detected network values
- Database update_podcast() now allows setting network_id, dai_platform, network_id_override

---

## [0.1.110] - 2025-12-17

### Fixed
- Worker crash during reprocessing (exit code 134) - COMPLETE FIX
  - v0.1.109 installed cuDNN via pip but libraries weren't in LD_LIBRARY_PATH
  - Added LD_LIBRARY_PATH to ENV to include pip-installed cuDNN/cuBLAS libs
  - Path: /usr/local/lib/python3.11/dist-packages/nvidia/cudnn/lib

---

## [0.1.109] - 2025-12-17

### Fixed
- Worker crash during reprocessing (exit code 134) - INCOMPLETE FIX
  - Root cause: Base Docker image changed to CUDA-only lacked cuDNN libraries
  - PyTorch RNN operations used by pyannote speaker diarization still require system cuDNN
  - Fix: Install nvidia-cudnn-cu12==8.9.2.26 via pip to provide cuDNN libraries
  - NOTE: Libraries installed but not in LD_LIBRARY_PATH - see v0.1.110
- API 500 errors on pattern/correction endpoints
  - Fixed db variable not initialized in list_patterns, get_pattern, update_pattern
  - Fixed db not initialized in submit_correction, export_patterns, import_patterns
  - Fixed db not initialized in reprocess_episode_with_mode
  - Fixed _find_similar_pattern helper missing db parameter

### Added
- RSS feed network detection integration
  - Now automatically detects DAI platform (megaphone, acast, art19, etc.) on feed refresh
  - Now automatically detects podcast network (TWiT, Relay FM, NPR, etc.) on feed refresh
  - Network and platform info stored in database for pattern scoping

---

## [0.1.108] - 2025-12-17

### Fixed
- Speaker diarization 10x performance improvement
  - Changed Docker base image from `nvidia/cuda:12.1.1-cudnn8-runtime` to `nvidia/cuda:12.1.1-runtime` (CUDA-only, no cuDNN)
  - Re-enabled cuDNN in speaker_analyzer.py - PyTorch now uses its bundled cuDNN without version conflicts
  - Diarization now runs with full GPU+cuDNN acceleration instead of CPU-fallback RNN kernels
- Database schema mismatch causing 500 errors on /api/v1/patterns endpoint
  - Fixed `_create_new_tables_only()` to match SCHEMA_SQL schema for ad_patterns table
  - Aligned audio_fingerprints and pattern_corrections table schemas
- GlobalStatusBar overlapping navigation buttons
  - Added padding-top to Layout component to account for fixed status bar

### Added
- TranscriptEditor integration in EpisodeDetail page
  - "Edit Ads" button to toggle transcript editor for reviewing/adjusting ad detections
  - Approximate transcript segmentation from plain text for editor display
  - Placeholder for correction submission API

---

## [0.1.107] - 2025-12-17

### Fixed
- Database schema migration failing on existing databases
  - Rewrote schema initialization to detect existing databases and only run migrations
  - Added comprehensive migrations for all new columns (network_id, dai_platform, created_at, processed_file, etc.)
  - Fixed known_sponsors table to include common_ctas column
  - New tables for cross-episode training created separately from indexes
  - Added get_podcast() alias method for backwards compatibility

---

## [0.1.106] - 2025-12-17

### Fixed
- Server failing to start with duplicate endpoint error
  - Flask AssertionError: "View function mapping is overwriting an existing endpoint function: api.reprocess_episode"
  - Renamed duplicate `reprocess_episode` function to `reprocess_episode_with_mode`

---

## [0.1.105] - 2025-12-17

### Added
- Cross-episode ad training system for improved ad detection accuracy
  - Audio fingerprinting using Chromaprint to detect identical DAI-inserted ads across episodes
  - Text pattern matching using TF-IDF vectorization and RapidFuzz for repeated sponsor reads
  - Three-stage detection pipeline: fingerprint match -> text pattern match -> Claude fallback
  - Pattern hierarchy system: Global -> Network -> Podcast scoping
  - Auto-promotion of patterns when confirmed across multiple episodes
- Sponsor management service with 100+ seed sponsors
  - Automatic text normalization (URLs, email addresses, phone numbers)
  - 5-minute cache for sponsor lookups
  - API endpoints for sponsor CRUD operations
- Real-time processing status via Server-Sent Events (SSE)
  - Global status bar component showing current processing activity
  - Live updates for feed refresh and episode processing
- Transcript editor UI with keyboard navigation
  - Segment boundary adjustment with J/K/L keys
  - Pattern correction submission (confirm, false positive, boundary adjustment)
  - Visual highlighting of ad segments
- Pattern correction workflow
  - Submit corrections to refine pattern boundaries
  - Track correction history per pattern
  - Auto-promote patterns after threshold confirmations
- Data retention and cleanup service
  - Configurable retention periods for episodes and patterns
  - Automatic cleanup of stale patterns with low confidence
  - Manual cleanup triggers via API
- Import/export functionality for patterns and sponsors
  - Export patterns to JSON for backup or sharing
  - Import patterns from other instances

### Changed
- Ad detector now uses 3-stage detection pipeline
  - Stage 1: Audio fingerprint matching (instant, no API cost)
  - Stage 2: Text pattern matching (fast, no API cost)
  - Stage 3: Claude API fallback (only for unknown ads)
- Updated Dockerfile with libchromaprint-tools for audio fingerprinting
- Added pyacoustid, rapidfuzz, scikit-learn to requirements.txt

### Technical
- New database tables: ad_patterns, audio_fingerprints, text_patterns, pattern_corrections, sponsors, sponsor_normalizations
- New services: sponsor_service.py, status_service.py, audio_fingerprinter.py, text_pattern_matcher.py, pattern_service.py, cleanup_service.py
- New frontend components: GlobalStatusBar.tsx, TranscriptEditor.tsx
- New API endpoints for patterns, corrections, sponsors, import/export, SSE status

---

## [0.1.104] - 2025-12-16

### Fixed
- Volume analysis (ebur128) regex not matching ffmpeg output format
  - ffmpeg outputs `TARGET:-23 LUFS` between `t:` and `M:` fields
  - Updated regex to allow flexible content between timestamp and loudness values

### Improved
- Reduced log spam from harmless warnings
  - Suppressed torchaudio MPEG_LAYER_III warnings (MP3 metadata, repeated per chunk)
  - Suppressed pyannote TF32 reproducibility warning
  - Suppressed pyannote std() degrees of freedom warning
  - Set ORT_LOG_LEVEL=3 to suppress onnxruntime GPU discovery warnings

---

## [0.1.103] - 2025-12-16

### Fixed
- Speaker diarization still failing with cuDNN error during inference
  - v0.1.102 disabled cuDNN only during pipeline load, then restored it
  - Actual diarization inference also uses LSTM/RNN and failed
  - Now disables cuDNN globally when pyannote is used (stays disabled)
  - GPU acceleration still works, using PyTorch native RNN kernels

---

## [0.1.102] - 2025-12-16

### Fixed
- Volume analysis (ebur128) not producing measurements
  - Changed ffmpeg verbosity from `-v info` to `-v verbose`
  - ebur128 filter needs verbose level to output frame-by-frame data
- Speaker diarization failing with cuDNN version mismatch
  - pyannote LSTMs triggered cuDNN RNN code path incompatible with our cuDNN 8
  - Disable cuDNN temporarily when moving pipeline to GPU
  - Still uses GPU acceleration, just PyTorch native RNN instead of cuDNN

---

## [0.1.101] - 2025-12-16

### Improved
- Better debugging for ebur128 volume analysis failures
  - Now logs lines containing ebur128 data patterns instead of just first 10 lines
  - Will show if ffmpeg output format differs from expected regex pattern
- Full traceback logging for speaker diarization failures
  - Helps diagnose pyannote internal errors like 'NoneType' has no attribute 'eval'

---

## [0.1.100] - 2025-12-16

### Fixed
- Cache permission denied error (take 2) - speaker diarization still failing
  - HOME=/app pointed to read-only container image directory
  - Changed to HOME=/app/data which is the writable volume mount
  - Now $HOME/.cache = /app/data/.cache (same as HF_HOME)

### Improved
- Volume analysis debugging - upgraded ffmpeg stderr logging from DEBUG to WARNING
  - Now shows ffmpeg return code and stderr when ebur128 fails
  - Will help diagnose why volume analysis is returning no measurements

---

## [0.1.99] - 2025-12-16

### Fixed
- Cache permission denied error in speaker diarization
  - Container was missing HOME environment variable
  - Libraries trying to write to $HOME/.cache failed with "Permission denied: /.cache"
  - Set HOME=/app in Dockerfile to provide writable cache location

---

## [0.1.98] - 2025-12-16

### Added
- Documentation for pyannote model license requirement in docker-compose.yml
  - Users must accept license at https://hf.co/pyannote/speaker-diarization-3.1
  - Token alone is not sufficient; explicit license acceptance required

### Improved
- Better error messages for speaker diarization failures
  - Now explicitly mentions license acceptance when pipeline returns None
  - Logs masked HF token status for debugging deployment issues
- Added debug logging for ebur128 volume analysis failures
  - Logs ffmpeg stderr sample when no measurements found

---

## [0.1.97] - 2025-12-16

### Fixed
- Speaker diarization failing due to huggingface_hub/pyannote version mismatch
  - pyannote 3.x uses `use_auth_token` internally when calling huggingface_hub
  - huggingface_hub v1.0+ removed support for `use_auth_token` parameter
  - Fix: Pin `huggingface_hub>=0.20.0,<1.0` to maintain compatibility
  - Speaker analysis has never worked since v0.1.85; this is the actual fix

---

## [0.1.96] - 2025-12-16

### Fixed
- RSS feed fetch failing for servers with malformed gzip responses
  - Some servers claim gzip encoding but send corrupted data
  - Added fallback: retry without compression when gzip decompression fails
- Speaker diarization fix attempt (incomplete - see v0.1.97)

---

## [0.1.95] - 2025-12-13

### Fixed
- Dashboard sorting by recent episodes not working
  - `lastEpisodeDate` field was missing from `/api/v1/feeds` response
  - Database correctly calculated the value but API didn't return it
- Orphan podcast directories not cleaned up after deletion
  - Directories could be recreated if accessed after database deletion
  - Added automatic cleanup in background task to remove orphan directories
- Speaker diarization failing with huggingface_hub deprecation (incomplete fix, see v0.1.96)

---

## [0.1.94] - 2025-12-12

### Fixed
- Ad detection window validation to prevent hallucinated ads
  - Claude sometimes hallucinates `start=0.0` when no ads found in a window
  - Ads are now validated against window bounds (with 2 min tolerance)
  - Ads exceeding 7 minutes are rejected as unrealistically long
  - Applied to both first pass and second pass detection
  - Logged as warnings when ads are rejected for debugging

### Changed
- Music detector now caps region duration at 2 minutes
  - Real music beds rarely exceed 2 minutes
  - Prevents unrealistically long music regions from being merged
- Audio signal filtering now excludes signals over 3 minutes
  - Prevents bad audio data from reaching Claude prompt

---

## [0.1.93] - 2025-12-12

### Fixed
- Volume analysis timeout on long episodes
  - Previous implementation ran ~2000 separate ffmpeg processes for a 2h45m episode
  - Now uses single-pass ebur128 filter analysis
  - 165-minute episode analyzed in ~2-3 minutes instead of timing out after 10 minutes
  - Dynamic timeout based on audio duration

---

## [0.1.92] - 2025-12-12

### Fixed
- Audio analysis setting not responding to UI toggle
  - `AudioAnalyzer.is_enabled()` was returning cached startup value
  - Now reads from database for live setting updates
  - Toggling audio analysis in Settings now takes effect immediately

---

## [0.1.91] - 2025-12-12

### Added
- Audio Analysis settings toggle in UI
  - New Settings page section for enabling/disabling audio analysis
  - API endpoint support for `audioAnalysisEnabled` setting
  - Analyzes volume changes, music detection, and speaker patterns
  - Experimental feature disabled by default

---

## [0.1.90] - 2025-12-12

### Fixed
- SQL error in dashboard API: `no such column: e.published`
  - Database column is `created_at`, not `published`
  - Fixes broken `/api/v1/feeds` endpoint that prevented dashboard from loading

---

## [0.1.89] - 2025-12-12

### Fixed
- Long ads with high confidence (>90%) being incorrectly rejected
  - Ads over 5 minutes were rejected even with high confidence
  - Now accepts long ads (up to 15 min) if confidence >= 90%
  - Improves detection for shows with longer host-read ads (e.g., TWiT network)

### Added
- Dashboard sorting by most recent episode (default)
  - New sort toggle in dashboard header (clock icon = recent, A-Z icon = alphabetical)
  - Podcasts with recent episodes appear first
  - Sort preference persisted in localStorage
  - Added `lastEpisodeDate` field to API response

---

## [0.1.88] - 2025-12-11

### Fixed
- ONNX Runtime cuDNN compatibility crash: `Could not load library libcudnn_ops_infer.so.8`
  - Root cause: CUDA 12.4 includes cuDNN 9.x, but ONNX Runtime (used by pyannote.audio) requires cuDNN 8.x
  - Workers crashed with code 134 (SIGABRT) when attempting speaker diarization
  - Rolled back to CUDA 12.1 with cuDNN 8 for full compatibility

### Changed
- Downgraded to CUDA 12.1 base image (nvidia/cuda:12.1.1-cudnn8-runtime-ubuntu22.04)
- Using PyTorch 2.3.0+cu121 and torchaudio 2.3.0+cu121
- Pinned pyannote.audio to >=3.1.0,<4.0.0 (v4.0 requires torch>=2.8.0 which needs CUDA 12.4)

---

## [0.1.87] - 2025-12-11

### Changed
- Upgraded to CUDA 12.4 base image (nvidia/cuda:12.4.1-cudnn-runtime-ubuntu22.04)
- Docker image size optimization: Pre-install PyTorch 2.8.0+cu124 (required by pyannote.audio)
  - Prevents duplicate torch installation during pip install
  - Using torch==2.8.0+cu124 and torchaudio==2.8.0+cu124 with CUDA 12.4

### Known Issues
- cuDNN 8 vs 9 incompatibility causes ONNX Runtime crash (fixed in v0.1.88)

---

## [0.1.86] - 2025-12-11

### Fixed
- App startup failure: `PermissionError: [Errno 13] Permission denied: '/app/src/audio_analysis/__init__.py'`
  - Root cause: `chmod -R 644 ./src/*.py` glob pattern only matched files in `./src/`, not subdirectories
  - Fixed by using `find ./src -type f -name '*.py' -exec chmod 644 {} \;` to recursively set permissions

### Changed
- Docker image optimizations to reduce size (~12GB -> ~8-9GB estimated)
  - Pre-install PyTorch with specific CUDA 12.1 build to prevent duplicate installations
  - Added `--no-install-recommends` to apt-get to skip unnecessary packages
  - Clean up pip cache and `__pycache__` directories after install
  - Removed unused `wget` package from apt-get install
- Reorganized requirements.txt with clearer sections (Core, API, Utilities, Audio analysis)
- Consolidated environment variables in Dockerfile using single ENV block

---

## [0.1.85] - 2025-12-11

### Added
- Comprehensive audio analysis module for enhanced ad detection
  - Volume/loudness analysis using ffmpeg loudnorm to detect dynamically inserted ads
  - Music bed detection using librosa spectral analysis (spectral flatness, low-freq energy, harmonic ratio)
  - Speaker diarization using pyannote.audio to detect monologue ad reads in conversational podcasts
- Audio analysis signals passed as context to Claude for improved detection accuracy
  - Volume changes (increases/decreases above threshold)
  - Music bed regions with confidence scores
  - Extended monologues with speaker identification and ad language detection
- New database settings for audio analysis configuration
  - `audio_analysis_enabled` - master toggle (default: false)
  - `volume_analysis_enabled`, `music_detection_enabled`, `speaker_analysis_enabled` - component toggles
  - `volume_threshold_db`, `music_confidence_threshold`, `monologue_duration_threshold` - tunable thresholds
- Audio analysis results stored in `episode_details.audio_analysis_json` for debugging
- HF_TOKEN environment variable for HuggingFace authentication (required for speaker diarization)

### Changed
- ad_detector.py now accepts optional audio_analysis parameter for both first and second pass detection
- process_episode() runs audio analysis when enabled and passes signals to Claude
- Updated requirements.txt with librosa, soundfile, pyannote.audio
- Updated Dockerfile with libsndfile system dependency
- Updated docker-compose.yml with HF_TOKEN environment variable

### Technical Details
- New module: `src/audio_analysis/` with volume_analyzer, music_detector, speaker_analyzer, and facade
- Audio analysis runs after transcription (uses same audio file)
- Each analyzer operates independently with graceful degradation on failure
- Volume analyzer: 5-second frames, 3dB threshold, 15s minimum anomaly duration
- Music detector: 0.5s frames, spectral analysis, 10s minimum region duration
- Speaker analyzer: pyannote diarization, 45s minimum monologue duration

---

## [0.1.84] - 2025-12-05

### Fixed
- Fixed startup crash: `sqlite3.OperationalError: no such column: slug`
  - Episodes table uses `podcast_id` foreign key, not `slug` column
  - Fixed SQL queries in `reset_stuck_processing_episodes()` and API endpoints
  - Properly joins episodes with podcasts table to get slug

---

## [0.1.83] - 2025-12-05

### Added
- Processing queue to prevent concurrent episode processing
  - Only one episode can process at a time to prevent OOM from multiple Whisper/FFMPEG processes
  - New `ProcessingQueue` singleton class with thread-safe locking
  - Additional requests return 503 with Retry-After header
- Background processing for non-blocking HTTP responses
  - Episode processing now runs in background thread
  - HTTP workers stay free for UI requests
  - Solves UI lockup during episode processing
- Startup recovery for stuck episodes
  - On server start, reset any episodes stuck in "processing" status to "pending"
  - Handles crash recovery automatically
- Settings UI for managing processing queue
  - New "Processing Queue" section shows episodes currently processing
  - Cancel button to reset stuck episodes to pending
  - Polls every 5 seconds for real-time updates
- API endpoints for processing management
  - `GET /api/v1/episodes/processing` - list all processing episodes
  - `POST /api/v1/feeds/<slug>/episodes/<episode_id>/cancel` - cancel stuck episode

### Fixed
- OOM crashes when two episodes process simultaneously
  - Workers were being killed: "Worker (pid:10) was sent SIGKILL! Perhaps out of memory?"
  - Queue ensures only one memory-intensive operation at a time
- Episodes stuck in "processing" status after worker crash
  - Previously required deleting and re-adding the entire podcast
  - Now auto-reset on startup and cancellable via UI

---

## [0.1.82] - 2025-12-05

### Added
- Episode-specific artwork support
  - Extract `<itunes:image>` from RSS episode entries
  - Store artwork URL in episodes database table
  - Pass through episode artwork in modified RSS feed
  - Include `artworkUrl` in API episode responses

### Fixed
- Long sponsor ads (5+ min) rejected despite being real sponsors
  - If sponsor name from ad matches sponsor listed in episode description, allow up to 15 minutes
  - Parses `<strong>Sponsors:</strong>` section and sponsor URLs from description
  - Bitwarden, ThreatLocker, and other confirmed sponsors now correctly processed
  - Added `MAX_AD_DURATION_CONFIRMED = 900.0` (15 min) for confirmed sponsors

### Changed
- Parallelized RSS feed refresh to prevent app lockup during bulk operations
  - Uses ThreadPoolExecutor with max_workers=5 for concurrent feed fetches
  - Each feed can take 30+ seconds; parallel refresh reduces total time significantly
- Increased gunicorn workers from 1 to 2 and threads from 4 to 8
  - Better handles concurrent requests during heavy operations
  - Reduces UI freezing during bulk feed refreshes

---

## [0.1.76] - 2025-12-03

### Fixed
- Same-sponsor ad merge extracting "read" as a sponsor name
  - `extract_sponsor_names()` was matching "sponsor read" and extracting "read" as a brand
  - Added exclusion list: read, segment, content, break, complete, partial, full, spot, mention, plug, insert, message, promo, promotion
  - Prevents false sponsor matches that caused unrelated ads to merge
- Same-sponsor merge creating over-long ads that get rejected by validator
  - Added 300s (5 min) maximum duration check before merging
  - If merge would exceed limit, ads are kept separate instead
  - Root cause: Two legitimate ads (~155s + ~75s) were incorrectly merged into 351s ad, which AdValidator rejected as too long

---

## [0.1.75] - 2025-12-02

### Added
- Configurable Whisper model via API and Settings UI
  - New `/settings/whisper-models` endpoint lists available models with VRAM/speed/quality info
  - Settings page now includes Whisper Model dropdown with resource requirements
  - Supports: tiny, base, small (default), medium, large-v3
  - Model hot-swap: changing model triggers reload on next transcription
- Podcast-aware initial prompt for Whisper transcription
  - Includes sponsor vocabulary (BetterHelp, Athletic Greens, Squarespace, etc.)
  - Improves accuracy of sponsor name transcription
- Hallucination filtering for Whisper output
  - Filters common artifacts: "thanks for watching", "[music]", repeated segments
  - Removes YouTube-style hallucinations that don't belong in podcasts
- Audio preprocessing before transcription
  - Normalizes to 16kHz mono (Whisper's native format)
  - Applies loudnorm filter for consistent volume levels
  - Highpass (80Hz) and lowpass (8kHz) for speech frequency focus

### Changed
- WhisperModelSingleton now reads configured model from database settings
- Model can be changed at runtime without server restart
- Transcription now logs which Whisper model is being used

---

## [0.1.74] - 2025-12-02

### Fixed
- Frontend now displays rejected ad detections in a separate "Rejected Detections" section
  - Shows validation flags explaining why each detection was rejected
  - Styled with red/warning colors to distinguish from accepted ads
  - Displays the reason and confidence for each rejected detection

---

## [0.1.73] - 2025-12-02

### Added
- Post-detection validation layer for ad markers (AdValidator)
  - Boundary validation: clamps negative start times and end times beyond episode duration
  - Duration checks: rejects ads <7s or >300s, warns on short (<30s) or long (>180s) segments
  - Confidence thresholds: rejects very low confidence (<0.3), warns on low (<0.5)
  - Position heuristics: boosts confidence for typical ad positions (pre-roll, mid-roll, post-roll)
  - Reason quality: penalizes vague reasons, boosts when sponsor name mentioned
  - Transcript verification: checks for sponsor names and ad signals in transcript text
  - Auto-correction: merges ads with <5s gaps, clamps boundaries to valid range
  - Decision engine: classifies ads as ACCEPT, REVIEW, or REJECT
  - Ad density warnings: flags if >30% of episode is ads or >1 ad per 5 minutes
- API now returns rejected ads separately in `rejectedAdMarkers` field
  - ACCEPT and REVIEW ads are in `adMarkers` (removed from audio)
  - REJECT ads are in `rejectedAdMarkers` (kept in audio for review)
- Timestamp precision guidance added to detection prompts
  - Instructs model to use exact [Xs] timestamps, not interpolate

### Changed
- Ad removal now only processes ACCEPT and REVIEW validated ads
- REJECT ads stay in audio but are stored for display in UI

---

## [0.1.72] - 2025-12-03

### Fixed
- Wrap descriptions in CDATA to fix invalid XML in RSS feeds
  - Channel descriptions were not escaped, causing raw HTML and `&nbsp;` entities to break XML parsing
  - Episode descriptions now also use CDATA for consistency
  - Fixes Pocket Casts rejecting feeds with HTML in descriptions (e.g., No Agenda, DTNS)

### Changed
- OpenAPI version is now dynamically injected from version.py
  - No longer need to manually update openapi.yaml version

---

## [0.1.71] - 2025-12-03

### Fixed
- Validate iTunes fields before outputting to RSS feed
  - `itunes:explicit` was outputting Python's `None` as string "None" (invalid XML)
  - `itunes:duration` could also output `None` in some cases
  - Now validates `itunes:explicit` against allowed values (true/false/yes/no)
  - Skips fields with invalid values instead of outputting malformed XML
  - Fixes Pocket Casts rejecting feeds with invalid iTunes tags

---

## [0.1.70] - 2025-12-03

### Fixed
- Limited RSS feed to 100 most recent episodes
  - Large feeds (2000+ episodes, 3MB+) were rejected by Pocket Casts during validation
  - Feed size now stays under ~500KB, compatible with all podcast apps

---

## [0.1.69] - 2025-12-02

### Fixed
- Removed `<itunes:block>Yes</itunes:block>` from modified RSS feeds
  - This tag was preventing podcast apps from subscribing to feeds
  - Original feeds (e.g., Acast) don't have this tag; it was being added unnecessarily

---

## [0.1.68] - 2025-12-02

### Changed
- Improved ad detection prompts to reduce false positives
  - Removed "EXPECT ADS" language that pressured model to invent ads
  - Made second pass truly blind (no reference to first pass)
  - Removed cross-promotion from ad detection targets
  - Added explicit "DO NOT MARK AS ADS" section for cross-promo and guest plugs
- Added window boundary guidance to prompts
  - Instructions for handling partial ads at window edges
  - Clear guidance on marking ads that span window boundaries
- Enhanced window context in API calls
  - Clearer formatting with explicit window boundaries
  - Instructions for partial ad handling
- Consolidated prompts: removed duplicate BLIND_SECOND_PASS_SYSTEM_PROMPT
  - Single source of truth in database.py
- Reduced second pass prompt from ~600 words to ~250 words

---

## [0.1.67] - 2025-12-02

### Fixed
- Removed hardcoded VALID_MODELS validation that rejected valid models like Haiku 4.5
  - Models are fetched dynamically from Anthropic API, so validation was unnecessary
  - Any model available in the dropdown is now accepted
- Updated OpenAPI documentation with secondPassModel field (was missing in 0.1.66)

---

## [0.1.66] - 2025-12-02

### Added
- Independent second pass model selection
  - New setting `secondPassModel` allows using a different Claude model for second pass
  - Visible in Settings UI when Multi-Pass Detection is enabled
  - Defaults to Claude Sonnet 4.5 for cost optimization
  - API: PUT /settings/ad-detection accepts `secondPassModel` field
- Sliding window approach for ad detection
  - Transcripts are now processed in 10-minute overlapping windows
  - 3-minute overlap between windows to catch ads at chunk boundaries
  - Applies to both first and second pass detection
  - Detections across windows are automatically merged and deduplicated
  - Improves accuracy for long episodes

### Technical
- New database setting: `second_pass_model`
- New helper functions: `create_windows()`, `deduplicate_window_ads()`
- New method: `get_second_pass_model()` in AdDetector class
- Constants: `WINDOW_SIZE_SECONDS=600`, `WINDOW_OVERLAP_SECONDS=180`
- Refactored JSON parsing into reusable `_parse_ads_from_response()` method

---

## [0.1.65] - 2025-12-01

### Added
- Second pass prompt is now configurable via Settings UI and API
  - New textarea in Settings page (shown when Multi-Pass Detection is enabled)
  - API endpoint PUT /settings/ad-detection accepts secondPassPrompt field
  - Stored in database like other settings, with reset-to-defaults support

### Changed
- Renamed "System Prompt" to "First Pass System Prompt" in Settings UI for clarity
- Updated OpenAPI documentation with secondPassPrompt fields

---

## [0.1.64] - 2025-12-01

### Changed
- Moved episode description below playback bar in episode detail view
  - Audio player now appears immediately after title/metadata
  - Description follows below for better UX (play first, read second)

---

## [0.1.63] - 2025-12-01

### Fixed
- Same-sponsor merge now works for short gaps without requiring sponsor mention in gap
  - If gap < 120 seconds AND both ads mention same sponsor: merge unconditionally
  - This fixes cases where transition content between ad parts doesn't mention sponsor
  - Example: Vention ad with 46s gap of "Mike Elgin" intro content now merges correctly

### Changed
- Sponsor extraction now also parses ad reason field
  - Extracts brand name from "Vention sponsor read" -> "vention"
  - Helps identify same-sponsor ads even when transcript doesn't have clear URL

---

## [0.1.62] - 2025-12-01

### Added
- Same-sponsor ad merging to fix fragmented ad detection
  - Extracts sponsor names from transcript (URLs, domain mentions)
  - If two ads mention same sponsor AND gap between them also mentions that sponsor, merge them
  - Fixes cases where Claude fragments long ads into pieces or mislabels parts
  - Example: Vention ad split into 3 parts with "Zapier" mislabel now merges correctly

### Technical
- New `extract_sponsor_names()` function - finds sponsors via URL/domain patterns
- New `get_transcript_text_for_range()` - gets transcript text for time ranges
- New `merge_same_sponsor_ads()` - merges ads with same sponsor in gap content
- Max gap of 5 minutes for sponsor-based merging
- Runs after boundary refinement, before audio processing

---

## [0.1.61] - 2025-12-01

### Added
- Intelligent ad boundary detection using word timestamps and keyword scanning
  - Whisper now returns word-level timestamps (without splitting segments)
  - Post-processing scans for transition phrases near detected ad boundaries
  - Transition phrases like "let's take a break", "word from our sponsor" adjust START time
  - Return phrases like "anyway", "back to the show" adjust END time
  - Falls back to segment-level boundaries if no keywords found
  - Adapts to each podcast's style instead of using hardcoded buffers

### Technical
- New `refine_ad_boundaries()` function in ad_detector.py
- AD_START_PHRASES and AD_END_PHRASES constants for keyword detection
- Word timestamps stored with segments but segments not split (avoids v0.1.59 issues)
- Refinement runs after merge_and_deduplicate(), before audio processing

---

## [0.1.60] - 2025-12-01

### Fixed
- Episode descriptions now have ALL blank lines removed (single-spaced)
  - Previous regex collapsed to paragraph breaks; now removes all blank lines
- Reverted segment splitting from v0.1.59 - it made ad detection WORSE
  - v0.1.59: Splitting disconnected transition phrases from sponsor content
  - Vention ad went from wrong END (26:04-26:34) to wrong START (27:51-28:19)
  - Original 45s segments were fine for finding ad START; problem was finding END
- Rate limit handling improved for 429 errors
  - Now waits 60 seconds for rate limit window to reset before retry
  - Both first and second pass have this handling

### Changed
- Ad extension heuristic improved
  - Threshold increased from 60s to 90s (detect more potentially incomplete ads)
  - Extension increased from 30s to 45s (catch more of the actual ad content)
- Streamlined system prompt (~70% size reduction)
  - Removed redundant "find all ads" messaging (repeated 5+ times)
  - Removed second example
  - Consolidated AD END guidance sections
  - Removed REMINDER sections that repeated earlier content
  - Kept brand lists (helpful for detection)
  - Result: ~3KB prompt instead of ~11KB, fewer tokens consumed

---

## [0.1.59] - 2025-12-01

### Fixed
- Improved whitespace collapsing in episode description display
  - Better regex that handles consecutive whitespace-only lines
  - Previous regex only handled pairs, not runs of blank lines

### Changed
- Dramatically improved ad detection precision with finer transcript granularity
  - **Root cause**: Whisper VAD was creating 45+ second segments, making precise ad boundaries impossible
  - Enabled word-level timestamps in Whisper transcription
  - Added segment splitting: long segments (>15s) are now split on word boundaries
  - Result: ~3x more segments but much more precise ad start/end detection
- Added automatic extension for short ads that end on URLs
  - If ad is under 60s and end_text contains a URL, extend by 30s
  - Safety net for cases where Claude still ends too early at first URL mention

---

## [0.1.58] - 2025-12-01

### Fixed
- Improved newline collapsing in episode description display
  - Now handles lines containing only whitespace (spaces/tabs)
  - Previous regex only matched truly empty lines

### Added
- end_text logging for ad detection debugging
  - Logs the last 50 chars of end_text for each detected ad segment
  - Helps understand why Claude thinks an ad ended where it did

### Changed
- Enhanced AD END SIGNALS guidance in both prompts
  - Added explicit "FINDING THE TRUE AD END" section
  - Clarifies that ad ends when SHOW CONTENT resumes, not when pitch ends
  - Lists signals to look for AFTER the pitch (topic change, "anyway", etc.)
  - Lists what NOT to end on (first URL, product description, pauses)

---

## [0.1.57] - 2025-12-01

### Fixed
- Removed seed parameter from API calls (not supported by Anthropic SDK)
- Collapsed excessive newlines in UI description display (3+ newlines -> 2)

---

## [0.1.56] - 2025-12-01

### Added
- Description logging: logs when episode description is/isn't included in prompts
- Prompt hash logging: logs MD5 hash of prompt for debugging non-determinism

### Changed
- Prompts now indicate ads are ALWAYS expected (empty result almost never correct)
- Description context clarified in prompts (describes content topics, may list sponsors)
- UI description display preserves formatting (line breaks, list items)

---

## [0.1.55] - 2025-12-01

### Fixed
- Improved ad segment end time detection in second pass prompt
  - Added explicit instructions for finding COMPLETE ad segments
  - Ads under 45 seconds now trigger verification prompt for true end time
  - Added AD END SIGNALS guidance (transitions, topic returns, stingers)
  - Root cause: DEEL ad detected as 29s when actual duration was 92s

### Added
- Episode descriptions now available in UI and API
  - Descriptions extracted from RSS feed and stored in database
  - Displayed below episode title in list and detail views
  - Passed to Claude for ad detection (helps identify sponsors, chapters)
  - HTML tags stripped for clean display
- Short ad duration warning in logs
  - Warns when detected ads are under 30 seconds (typical ads are 60-120s)
  - Helps identify potentially incomplete ad segment detection

### Changed
- Enhanced `BLIND_SECOND_PASS_SYSTEM_PROMPT` with boundary detection guidance
- `USER_PROMPT_TEMPLATE` now includes optional episode description field
- Database schema: added `description` column to episodes table

---

## [0.1.54] - 2025-12-01

### Fixed
- Fixed `adsRemovedFirstPass` and `adsRemovedSecondPass` count calculation
  - Previous: calculated as `total - firstPassCount` which gave negative/incorrect values after merging
  - New: counts based on actual `pass` field in merged results
  - `first_pass_count = first_only + merged` (ads found by first pass)
  - `second_pass_count = second_only + merged` (ads found by second pass)
- Improved logging to show breakdown: `first:X, second:Y, merged:Z`

---

## [0.1.53] - 2025-12-01

### Changed
- Second pass now runs BLIND (no knowledge of first pass results)
  - Previous approach: tell second pass what first pass found, ask to find more
  - New approach: second pass analyzes independently with different detection focus
  - Second pass specializes in subtle/baked-in ads that don't sound like traditional ads
  - Results merged automatically using improved algorithm
- Improved merge algorithm for combining pass results
  - Overlapping segments merged: takes earliest start, latest end
  - Adjacent segments (within 2s gap) also merged
  - Non-overlapping segments kept as separate ads
  - Ads now marked as `pass: 1`, `pass: 2`, or `pass: 'merged'`
- UI shows "Merged" badge (green) for segments detected by both passes

### Technical
- `BLIND_SECOND_PASS_SYSTEM_PROMPT` replaces previous informed prompt
- `detect_ads_second_pass()` no longer takes `first_pass_ads` parameter
- `merge_and_deduplicate()` rewritten with interval merging algorithm
- Frontend types: `AdSegment.pass` now `1 | 2 | 'merged'`

---

## [0.1.52] - 2025-12-01

### Changed
- Made second pass ad detection more aggressive
  - Reframes first pass reviewer as "junior/inexperienced" to encourage skepticism
  - Added "DETECTION BIAS: When in doubt, mark it as an ad"
  - Added explicit instruction to NOT just confirm first pass work
  - Removed verification step - focus only on finding missed ads
  - Should increase likelihood of catching non-obvious advertisements

---

## [0.1.51] - 2025-11-30

### Changed
- Multi-pass ad detection now uses parallel analysis instead of sequential re-transcription
  - Both passes analyze the SAME original transcript (not re-transcribed after processing)
  - Second pass now runs with different prompt to find ads first pass might have missed
  - Results merged with deduplication (>50% overlap = same ad)
  - Audio processed ONCE with all detected ads (faster, more efficient)
- Second pass prompt redesigned as "skeptical reviewer" approach
  - Given first pass results as context
  - Looks for: short ads, ads without sponsor language, baked-in ads, post-roll ads
  - Returns only NEW ads not already found by first pass

### Added
- Per-pass ad tracking in database and UI
  - New columns: `ads_removed_firstpass`, `ads_removed_secondpass`
  - API returns `adsRemovedFirstPass` and `adsRemovedSecondPass` fields
  - Each ad marker now has `pass` field (1 or 2) indicating which pass found it
- Pass badges in Episode Detail UI
  - Ads marked with "Pass 1" (blue) or "Pass 2" (purple) badges
  - Header shows breakdown: "Detected Ads (11) (5 first pass, 6 second pass)"
- `merge_and_deduplicate()` function for combining pass results

### Technical
- Database migration adds `ads_removed_firstpass`, `ads_removed_secondpass` columns
- Frontend types updated: `AdSegment.pass?: 1 | 2`, `EpisodeDetail.adsRemovedFirstPass/SecondPass`

---

## [0.1.50] - 2025-11-30

### Added
- UI toggle for multi-pass ad detection in Settings page
  - New styled toggle switch to enable/disable multi-pass detection
  - Settings now properly persisted and displayed

### Changed
- Database schema: renamed `claude_prompt`/`claude_raw_response` columns to `first_pass_prompt`/`first_pass_response`
- Added new columns: `second_pass_prompt`, `second_pass_response` to store multi-pass detection data
- API response field changes (breaking change for API consumers):
  - `claudePrompt` renamed to `firstPassPrompt`
  - `claudeRawResponse` renamed to `firstPassResponse`
  - Added `secondPassPrompt`, `secondPassResponse` fields
- Second pass detection now returns and stores prompt/response for debugging

---

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
