# Changelog


All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.0.18] - 2026-02-27

### Added
- **MinusPod logo in UI**: Header and login page now display the MinusPod logo (audio waveform bars with strike-through and wordmark) instead of plain text, with theme-aware light/dark variants
- **New favicon**: Replaced generic microphone icon with the MinusPod waveform icon extracted from the logo
- **README logo**: Added centered MinusPod logo at the top of README.md

## [1.0.17] - 2026-02-26

### Fixed
- **Thread safety for per-episode token accumulator**: Replaced shared module-level dict with `threading.local()` so each thread (background processor, HTTP handler) gets an independent accumulator. Prevents concurrent requests from corrupting each other's token counts under Gunicorn's `--threads 8`.
- **Missing try/finally for token tracking in standalone API endpoints**: `/regenerate-chapters` and `/retry-ad-detection` now wrap LLM calls in `try/finally` so `get_episode_token_totals()` and DB persistence always run even if the LLM call raises.

## [1.0.16] - 2026-02-26

### Fixed
- **Standalone API endpoints not tracking per-episode token usage**: `/regenerate-chapters` and `/retry-ad-detection` make LLM calls outside the processing pipeline without activating the per-episode token accumulator. Global `token_usage` table recorded these calls, but they were invisible in per-episode cost display. Both endpoints now activate `start_episode_token_tracking()` before LLM calls and persist totals via `increment_episode_token_usage()`.

### Added
- **`increment_episode_token_usage()` database method**: Increments `input_tokens`, `output_tokens`, and `llm_cost` on the most recent completed `processing_history` entry for an episode. Used by standalone endpoints that make LLM calls after the initial processing run.

## [1.0.15] - 2026-02-26

### Fixed
- **Processing history not saved due to SQL column mismatch**: `record_processing_history()` INSERT had 14 columns but only 13 VALUES placeholders -- the `?` for `llm_cost` was missing. All processing runs since v1.0.12 silently failed to write history rows (caught by try/except, logged as "Failed to record history: 13 values for 14 columns"). Token accumulator was working correctly but data was discarded at the DB write step.

## [1.0.14] - 2026-02-26

### Fixed
- **Always show LLM cost on episode detail page**: Previously hidden when tokens were zero (all pre-feature episodes). Now displays `LLM: $0.00 (0 in / 0 out)` for any completed episode with a processing_history entry.
- **2-digit cost precision in UI**: Changed LLM cost display from 4 decimal places to 2 in both episode detail and history pages for cleaner presentation.

### Added
- **Diagnostic logging for token accumulator lifecycle**: Added logging at accumulator activation, each token callback, and totals retrieval in `llm_client.py`. Added token totals logging before DB write in `main.py` for both success and failure paths. Enables verification via Loki after next processing run.

## [1.0.13] - 2026-02-26

### Fixed
- **Episode detail LLM cost placement**: Moved LLM cost/token display from inside the "Detected Ads" card (hidden when 0 ads found) to the episode metadata bar alongside date, duration, and status badges. Now visible on any processed episode regardless of ad count.
- **Episode token display suppressed when cost is zero**: `_get_episode_token_fields` was checking `llm_cost == 0.0` to hide the display, but models without pricing entries have $0 cost with non-zero tokens. Now checks for zero tokens instead.
- **Missing pricing for `claude-sonnet-4-6`**: Added to `DEFAULT_MODEL_PRICING` ($3/$15 per MTok). Previously all calls to this model recorded $0 cost.

## [1.0.12] - 2026-02-26

### Added
- **Per-episode LLM token usage and cost tracking**: Every processing run now records input/output token counts and estimated cost directly in `processing_history`. Module-level accumulator in `llm_client.py` aggregates all LLM calls during a single episode's processing pipeline (ad detection, verification, chapters) and passes totals to `record_processing_history()` on completion or failure.
- **Episode detail LLM cost display**: Episode detail page shows LLM cost and token breakdown (e.g. "LLM: $0.0034 (12.3K in / 1.5K out)") when cost data is available.
- **History page cost column**: New sortable "Cost" column in the processing history table shows per-episode LLM cost. Stats summary includes a "Total LLM Cost" tile.
- **Token data in API responses**: `GET /api/v1/feeds/{slug}/episodes/{id}` includes `inputTokens`, `outputTokens`, `llmCost`. History list, stats, and export endpoints include the same fields.
- **Database migration**: Adds `input_tokens`, `output_tokens`, `llm_cost` columns to `processing_history` table with zero defaults for backward compatibility.

## [1.0.11] - 2026-02-26

### Added
- **LLM token usage tracking with cost calculation**: Every LLM API call (ad detection, verification, chapters) now records input/output token counts and estimated cost. Tracks per-model breakdown in `token_usage` table with pricing from `model_pricing` table seeded with current Anthropic rates. Usage callback wired into `LLMClient` base class so all call sites are tracked automatically with zero code changes.
- **New API endpoint `GET /api/v1/system/token-usage`**: Returns global totals (input/output tokens, total cost) and per-model breakdown with pricing info.
- **LLM Tokens and LLM Cost tiles in System Status**: Settings page now shows cumulative token usage (formatted as "1.2M in / 456K out") and total USD cost alongside existing stats.
- **Model pricing refresh on `GET /settings/models`**: Newly discovered models are automatically priced from built-in defaults when the model list is fetched.
- **New API endpoint `GET /api/v1/system/model-pricing`**: Returns all known model pricing rates from the `model_pricing` table for API consumers.
- **Pricing enrichment on `GET /settings/models`**: Model list response now includes `inputCostPerMtok` and `outputCostPerMtok` fields when pricing is known.
- **Cost display in model dropdowns**: Settings page model selectors show per-token pricing inline (e.g. "Claude Haiku 4.5 ($1 / $5 per MTok)").

## [1.0.10] - 2026-02-24

### Fixed
- **Age limit on auto-retry for failed queue items**: `reset_failed_queue_items()` now skips items older than 48 hours (configurable via `max_age_hours`). Previously, ancient failed items with elapsed backoff timers were retried on first run, causing 8 stale episodes to be reprocessed on v1.0.9 deploy.

## [1.0.9] - 2026-02-23

### Added
- **Auto-retry for failed queue items in background_queue_processor**: Failed auto-process queue items are now automatically retried with exponential backoff (5/15/45 min). Previously, failed episodes were only retried when a podcast client happened to request them. Respects `MAX_EPISODE_RETRIES` limit and skips permanently failed episodes.

## [1.0.8] - 2026-02-21

### Changed
- **Tightened "WHAT IS NOT AN AD" host mention rule**: Added "organically" qualifier and conversational context to the host self-promotion exclusion in both system and verification prompts, preventing produced cross-promos from being incorrectly excluded
- **Removed blanket network cross-promo exclusion from verification prompt**: The rule "Cross-promotion of shows within the same podcast network (unless it includes promo codes or external URLs)" was too broad and caused produced promo segments to be missed

### Added
- **"PLATFORM-INSERTED ADS" section in both prompts**: New detection guidance for hosting platform pre/post-rolls (Acast, Spotify for Podcasters, iHeart Radio), cross-promotions for other podcasts, and network promos with clear distinction between organic host mentions and produced promotional segments
- **DB migration to auto-update default prompts on existing installs**: Migration uses `PLATFORM-INSERTED ADS` sentinel to detect old prompts and only updates if `is_default` is set (custom prompts are preserved)

## [1.0.7] - 2026-02-19

### Security
- **SSRF protection for outbound requests**: User-supplied feed URLs (via `add_feed` and `import_opml`) and second-order URLs from RSS content (artwork, audio) are now validated before any outbound request. Blocks private/reserved IPs, loopback, link-local, cloud metadata endpoints (169.254.169.254, 168.63.129.16), restricted schemes (only http/https allowed), and non-standard ports. Validation applied at API entry points and as defense-in-depth in `rss_parser.py`, `storage.py`, and `transcriber.py`.
- **Stored XSS fix in search snippets**: FTS5 search snippets containing unsanitized RSS description HTML are now sanitized server-side via `nh3` (only `<mark>` tags preserved). Frontend `Search.tsx` replaced unsafe innerHTML rendering with a safe React rendering helper that splits on `<mark>` boundaries and renders all other content as escaped text.

### Added
- `src/utils/url.py` -- SSRF URL validation module (`validate_url`, `SSRFError`)
- `ALLOWED_URL_SCHEMES` and `ALLOWED_URL_PORTS` constants in `src/utils/constants.py`
- `nh3` dependency for HTML sanitization

## [1.0.6] - 2026-02-19

### Fixed
- **`parse_timestamp` silently returning 0.0 on bad input**: Restored `ValueError` on unparseable timestamps (regression from v1.0.3 consolidation). All 7 callers with `try/except ValueError` were effectively dead code; garbage timestamps silently became 0.0 (episode start), creating false markers at time zero.
- **Permanent LLM errors retried indefinitely**: Added early `return False` in `is_retryable_error()` for non-retryable Anthropic/OpenAI status codes, preventing fallthrough to string-pattern matching. Added `is_llm_api_error()` helper and guard in `is_transient_error()` so permanent API errors (e.g. `BadRequestError`, `AuthenticationError`) are not misclassified as transient.
- **Stale schema comment on `pattern_corrections` table**: Updated from "audit log ... never deleted" to reflect that conflicting entries are cleaned up on reversal (v1.0.5 behavior).

## [1.0.5] - 2026-02-19

### Fixed
- **Conflicting corrections not cleaned up on user action reversal**: When a user changed their mind about a correction (e.g., marked false positive then confirmed, or vice versa), both corrections persisted in the database. The false_positive check has higher priority in validation, so a confirm could never override a prior false_positive for the same segment. Now `delete_conflicting_corrections()` removes the opposite correction type (with 50% overlap match) before inserting the new one.
- **Misleading flag prefix in ad_validator.py**: Changed "ERROR: User marked as false positive" to "INFO:" since this is an intentional user action, not an error condition.

## [1.0.4] - 2026-02-18

### Fixed
- **ChaptersGenerator `self.client` AttributeError**: Replaced 4 remaining references to the removed `client` backward-compat property with `self._llm_client` (the actual backing field). Regression introduced in v1.0.3 Phase D item 21 when backward-compatibility aliases were removed.

## [1.0.3] - 2026-02-18

### Changed (Code Simplification)

- **Consolidated duplicate `parse_timestamp` implementations**: Merged 3 separate versions (utils/time.py, ad_detector.py, chapters_generator.py) into a single canonical version in `utils/time.py` that handles all input types (int, float, string with 's' suffix, HH:MM:SS, MM:SS, VTT comma decimals).
- **Consolidated duplicate `adjust_timestamp` implementations**: Merged transcript_generator.py and chapters_generator.py versions into `utils/time.py`. Both modules now import the shared function.
- **Consolidated duplicate `format_vtt_timestamp`**: Merged transcriber.py and transcript_generator.py versions into `utils/time.py` (HH:MM:SS.mmm format).
- **Consolidated `FALLBACK_MODELS` list**: Defined once in `llm_client.py` at module level, replacing 3 identical lists in AnthropicClient, OpenAICompatibleClient, and AdDetector.
- **Simplified `is_transient_error` in main.py**: Now delegates LLM API error classification to `llm_client.is_retryable_error()` instead of duplicating the logic.
- **Moved `first_not_none` to utils/time.py**: Extracted from ad_detector.py for reuse; critical for preserving 0.0 pre-roll timestamps.
- **Consolidated FFPROBE_TIMEOUT**: utils/audio.py now imports from config.py instead of defining its own copy.
- **Consolidated User-Agent strings**: Added `BROWSER_USER_AGENT` and `APP_USER_AGENT` constants to config.py; updated storage.py, rss_parser.py, and transcriber.py.
- **Decomposed `process_episode()` (~640 lines)**: Extracted 7 named pipeline stage functions (`_download_and_transcribe`, `_run_audio_analysis`, `_detect_ads_first_pass`, `_refine_and_validate`, `_run_verification_pass`, `_generate_assets`, `_finalize_episode`) plus `_handle_processing_failure`. The orchestrator is now ~70 lines.
- **Extracted `_extract_json_ads_array()` from `_parse_ads_from_response()`**: 4 JSON extraction strategies (direct parse, markdown code block, regex scan, bracket fallback) now in a dedicated method.
- **Simplified `_run_schema_migrations()` (~590 lines -> ~120 lines)**: Added `_add_column_if_missing()`, `_rename_column_if_needed()`, and `_get_table_columns()` helpers. All 25+ repetitive ALTER TABLE blocks replaced with data-driven lists.
- **Updated hardcoded model in chapters_generator.py**: Replaced `"claude-3-5-haiku-20241022"` with configurable `CHAPTERS_MODEL` constant set to `"claude-haiku-4-5-20251001"`.
- **Merged identical `complete_job()`/`fail_job()` in status_service.py**: Both now delegate to shared `_clear_current_job()`.
- **Extracted common overlap check in ad_validator.py**: `_overlaps_false_positive()` and `_overlaps_confirmed()` now delegate to parameterized `_overlaps_corrections()`.
- **Fixed overly broad auth path exemptions in api.py**: Changed `/rss` substring check to `path.endswith('/rss')` and scoped `/audio` and `/artwork` checks to `/api/v1/feeds/` prefix.
- **Moved inline stdlib imports to module level**: `import re` and `import math` in api.py, `import time` in database.py.
- **Added `transaction()` context manager to Database**: Provides `with db.transaction() as conn:` for automatic commit/rollback.
- **Removed backward-compatibility aliases**: `get_podcast()` in database.py, `client` properties in ad_detector.py and chapters_generator.py.
- **Removed unnecessary ImportError guards**: Local module imports in ad_detector.py lazy properties (audio_fingerprinter, text_pattern_matcher, pattern_service, sponsor_service) no longer wrapped in try/except ImportError.
- **Added VTT parse failure logging**: transcript_generator.py now warns when VTT parsing returns empty segments.
- **Removed `parse_timestamp_to_seconds()` wrapper**: chapters_generator.py callers now use `parse_timestamp()` directly from utils.time.

## [1.0.2] - 2026-02-18

### Fixed
- **Missed tagline-style DAI ads**: Added detection guidance for short (15-45s) brand tagline
  ads that lack promo codes or URLs -- polished radio-commercial-style spots with concentrated
  marketing language. Added synthetic example to prompt and GNC to brand list. DB migration
  auto-updates default prompts (preserves user customizations).
- **Claude timestamp hallucination**: New `validate_ad_timestamps()` checks whether ad keywords
  actually appear at the reported transcript position. If not, searches the window for the
  correct location and corrects the timestamps before downstream filtering.
- **Pattern-overlap filtering silently dropping uncovered tails**: Replaced binary
  `_is_region_covered()` with `get_uncovered_portions()` in the Claude/pattern merge loop.
  Uncovered portions >= 15s are now preserved as separate ad segments instead of being
  discarded when a pattern covers >50% of a merged Claude ad.

## [1.0.1] - 2026-02-17

### Fixed
- **Rejected ad not restored after user confirmation**: Four cascading bugs prevented
  user "Confirm as Ad" corrections from taking effect on reprocess.
  - `NOT_AD_PATTERNS` regex false positive: "transition from show content" in ad reasons
    incorrectly triggered rejection. Replaced negative lookbehind with positive assertion.
  - Confirmed corrections ignored during reprocessing: Added `get_confirmed_corrections()`
    to database and `_overlaps_confirmed()` to validator. Confirmed ads now force-accept
    at confidence 1.0 (priority: false_positive REJECT > confirmed ACCEPT > normal).
  - Frontend omitted `sponsor` field from correction payload, preventing sponsor
    extraction on the backend.
  - Confirm handler sponsor extraction used adjusted timestamps against original
    timestamps. Added reason-text fallback before transcript-based extraction.

## [1.0.0] - 2026-02-14

Major release: pipeline redesign, MinusPod rebrand, and ad detection overhaul.

### Changed
- **Renamed to MinusPod**: Service name, Docker image (`ttlequals0/minuspod`), frontend title, package name, API docs, README, and deployment docs all updated from "Podcast Server" / "podcast-server".
- **Replaced two-pass architecture with verification pipeline**: The blind second pass is replaced by a post-cut verification pass that re-transcribes processed audio and runs detection with a "what doesn't belong" prompt. Missed ads are re-cut directly from pass 1 output.
- **Audio signals as Claude prompt context**: Volume anomalies and DAI transition pairs are formatted as text and injected into Claude's per-window prompts instead of running as an independent post-detection step. Claude makes all ad/not-ad decisions with full audio evidence.
- **Audio analysis always enabled**: Removed global `audioAnalysisEnabled` toggle and per-feed `audioAnalysisOverride`. Volume analysis via ffmpeg is lightweight and always runs.
- **AdMarker schema updated**: `pass` field replaced with `detection_stage` enum covering first_pass, claude, fingerprint, text_pattern, language, audio_enforced, and verification stages.
- **Confidence slider is single source of truth**: Removed hardcoded dual-thresholds that bypassed the user's min_cut_confidence slider. ACCEPT = always cut, REJECT = never cut, REVIEW = confidence gate.
- **Detection prompts rewritten**: Removed "when in doubt, mark it as an ad" bias. Both passes require identifiable promotional language. Added "WHAT IS NOT AN AD" guidance and "AUDIO SIGNALS" evidence-only framing.
- **Transition detection threshold raised from 3.5 dB to 12.0 dB**: Added delta-ratio symmetry filter and recalibrated confidence formula to eliminate false positives from normal audio variation.
- **Pattern learning quality gates**: Only creates patterns from ads that were actually cut. Sponsor extraction uses 4-tier DB resolution with prefix and short-word rejection gates.

### Added
- **Abrupt transition detection**: New `TransitionDetector` analyzes frame-to-frame loudness jumps in existing volume analyzer output. Pairs up/down transitions into candidate DAI regions.
- **Audio signal enforcement**: New `AudioEnforcer` formats audio signals for Claude prompts and extends existing ad boundaries when signals partially overlap.
- **Verification pass module**: New `VerificationPass` class encapsulates the full post-cut pipeline with separate model selection.
- **Heuristic pre/post-roll detection**: New `roll_detector.py` with regex-based detection for ads at episode boundaries that Claude missed. Requires 2+ pattern matches.
- **Transcript generation**: New `TranscriptGenerator` produces timestamp-aligned text stored in the database for search indexing and UI display.
- **Silent-gap ad merge**: Consecutive ads separated by up to 30s of silence (no speech) are merged into a single ad instead of requiring 5s proximity.
- **Incremental search index updates**: Episodes indexed immediately after processing; full rebuild every 6 hours.
- **VTT-based transcript timestamps in UI**: EpisodeDetail fetches actual VTT transcript for accurate timestamps instead of approximating.
- **Sponsor field on ad markers**: Ad markers now store the `sponsor` field separately for UI sponsor badges. Window deduplication preserves sponsor names during merges.

### Fixed
- **Pre-roll ads at 0.0s silently dropped**: Python `or`-chains treated `0.0` as falsy; replaced with `_first_not_none()` helper.
- **Pass 2 ads missing from UI and showing wrong timestamps**: Multiple fixes for verification ads not being saved or displaying with processed-audio timestamps instead of original coordinates.
- **Ad marker reasons showing bare sponsor names**: Three independent merge/dedup bugs caused markers to display unhelpful reasons instead of descriptive text.
- **Corrupt fingerprints causing stuck episodes**: Auto-detection and deletion of broken fingerprints; bail-out when all fingerprints are corrupt.
- **CTranslate2 cuDNN crash**: Added `LD_LIBRARY_PATH` for nvidia pip package directories.
- **Content segments parsed as ads**: Dynamic ad-evidence validation requires positive proof (known sponsor, ad-language patterns, or explicit sponsor field).

### Removed
- **Speaker diarization and music bed detection**: Dropped pyannote.audio and librosa dependencies. GPU memory pressure, processing time, and heavy dependencies for marginal benefit.
- **Dependencies**: `librosa`, `pyannote.audio`, `nvidia-cudnn-cu12` (re-added then managed via LD_LIBRARY_PATH).
- **Dead code**: Unused functions, blind second pass prompt, stale UI text, audio analysis toggle settings.

## [0.1.258] - 2026-02-14

### Fixed
- **Missing sponsor names and raw detection_stage in UI**: Claude-detected ads now store the `sponsor` field separately (extracted via `extract_sponsor_name`) so the UI can display sponsor badges. Window deduplication preserves sponsor names during merges. Frontend passes through `marker.sponsor` from the API instead of hardcoding `undefined`. TranscriptEditor maps raw `detection_stage` values to human-friendly labels (Pass 1, Pass 2, Fingerprint, Pattern, Language) instead of showing "claude" or "text_pattern".

## [0.1.257] - 2026-02-14

### Fixed
- **Ad marker reasons show bare sponsor names instead of descriptions**: Three independent bugs caused ad markers to display unhelpful reasons like "Ironclad" or "Contains" instead of descriptive text. (1) Cross-stage merge in `_merge_detection_results` never updated the `reason` field when merging overlapping ads from different detection stages -- now picks the longer (more descriptive) reason. (2) Window deduplication in `deduplicate_window_ads` replaced reason based solely on confidence -- now keeps the more descriptive reason regardless of which window had higher confidence. (3) Claude reason extraction preferred `extract_sponsor_name` (bare name) over Claude's raw `reason` field -- now falls back to Claude's reason when it is substantially more descriptive than the bare sponsor name.

## [0.1.256] - 2026-02-14

### Added
- **Silent-gap ad merge** (Phase 18): Consecutive ads separated by up to 30s of silence (no speech) are now merged into a single ad. Previously only ads within 5s were merged, leaving fragmented detections when an ad break contained a brief silence between sponsors. New `_has_speech_in_range()` method checks transcript segments to distinguish silent gaps from content. `MAX_SILENT_GAP` constant (30s) added to config.
- **Incremental search index updates** (Phase 19): New `index_episode()` method indexes a single episode immediately after processing, so it appears in search results without waiting for a full rebuild. Periodic full rebuild runs every 6 hours via `run_cleanup()`.
- **VTT-based transcript timestamps in UI** (Phase 20): EpisodeDetail now fetches and parses the actual VTT transcript file for accurate timestamps instead of approximating by evenly distributing text across the episode duration. Falls back to the old approximation when VTT is unavailable.
- **Processed transcript text storage** (Phase 20): New `generate_text()` method on TranscriptGenerator produces a `[HH:MM:SS.sss --> HH:MM:SS.sss] text` format stored in the database after processing. This is the ad-free, timestamp-adjusted transcript used by search indexing.

### Changed
- **Renamed to MinusPod** (Phase 22): Service name, Docker image, frontend title, package name, API docs title, README heading, and deployment docs all updated from "Podcast Server" / "podcast-server" to "MinusPod" / "minuspod".
- **Pass label text in UI** (Phase 20): Detection stage labels changed from "first pass" / "verification" to "pass 1" / "pass 2" for consistency.

### Fixed
- **Fingerprint scan wastes iterations when all fingerprints are broken** (Phase 17): When every known fingerprint in the database is corrupt, the sliding window loop still iterated through the entire audio file doing ffmpeg+fpcalc work for nothing. Added a bail-out check that breaks immediately when all known fingerprints are in the broken set.

### Removed
- **Dead code cleanup** (Phase 21): Removed three unused functions: `extract_url_sponsor()` from ad_detector.py, `extract_segments_with_timestamps()` from utils/text.py, `format_time_simple()` from utils/time.py.

## [0.1.255] - 2026-02-13

### Fixed
- **v0.1.254 fix missed ctypes.ArgumentError**: The corrupt fingerprint exception is `ctypes.ArgumentError` (from the C library binding), not Python's `TypeError`. The `<class 'TypeError'>` in the error message was the ctypes description of the type mismatch, not the exception class. Updated the catch to handle both `TypeError` and `ctypes.ArgumentError`.

## [0.1.254] - 2026-02-13

### Fixed
- **Stuck episode caused by corrupt audio fingerprint in database**: A corrupt fingerprint stored in the database caused `acoustid.chromaprint.decode_fingerprint()` to throw `TypeError` on every comparison. The `find_matches()` sliding window loop (3300 iterations for a 6605s episode) caught and swallowed the error each time, taking ~47 minutes of wasted work -- longer than the 37-minute orphan detector timeout. The episode was killed, reset, and retried in a loop it could never escape. Fix: `compare_fingerprints()` now returns -1.0 for TypeError (distinguishing broken data from no-match), and `find_matches()` tracks broken pattern IDs in a set, skipping them after the first failure. Corrupt fingerprints are auto-deleted from the database. A 47-minute scan of errors becomes 1 warning + fast completion.

## [0.1.253] - 2026-02-12

### Fixed
- **Pre-roll ads starting at 0.0s silently dropped**: The LLM response parser used Python `or`-chains to extract start/end timestamps from Claude's JSON response. Since `0.0` is falsy in Python, `0.0 or ad.get('start_time')` would skip the valid value and fall through to `None`, causing the ad to be silently discarded at the `start_val is not None` check. Replaced `or`-chains with `_first_not_none()` helper that correctly treats `0` and `0.0` as valid values. Every pre-roll ad starting at timestamp 0.0 was previously being lost.

## [0.1.252] - 2026-02-12

### Changed
- **Detection prompts updated to reduce false positives** (Phase 16): Removed "when in doubt, mark it as an ad" bias from Pass 1 prompt. Both Pass 1 and Pass 2 prompts now require identifiable promotional language (sponsor names, URLs, promo codes, product pitches, calls to action) to flag an ad. Added "WHAT IS NOT AN AD" section to Pass 1 listing silence/pauses, topic transitions, and audio-only anomalies. Added "AUDIO SIGNALS" section to Pass 1 explicitly stating signals are supporting evidence only. Added CRITICAL paragraph to Pass 2 requiring promotional transcript content. Removed "BE THOROUGH" over-flagging encouragement from Pass 2. Strengthened audio_enforcer.py header to reinforce that audio signals without promotional content are not ads. Addresses SN 1064 false positive where a 2935-2970s silence gap was flagged as an ad at 65% confidence.

## [0.1.251] - 2026-02-11

### Fixed
- **Pass 2 heuristic roll ads showing wrong timestamps in UI** (Phase 15.3): Pre/post-roll ads detected by heuristic on processed audio were copied directly into `verification_ads_original` with processed-audio timestamps. Since pass 1 cuts shift the timeline, these timestamps were wrong in the UI. Now maps heuristic roll ad timestamps through `_map_to_original` using the pass 1 cuts, matching how Claude's verification ads are already mapped.

## [0.1.250] - 2026-02-11

### Added
- **Heuristic pre/post-roll detection** (Phase 15): New `roll_detector.py` with regex-based detection for ads at episode boundaries that Claude missed due to LLM nondeterminism. Detects ad indicators (URLs, phone numbers, CTAs, promo codes) before show intro (pre-roll) and after sign-off (post-roll). Requires 2+ pattern matches with conservative confidence (0.80-0.95). Runs in both Pass 1 and Pass 2.

### Changed
- **Confidence slider is now the single source of truth** (Phase 11): AdValidator's `_make_decision` no longer has hardcoded 0.85/0.60 dual-thresholds that bypassed the user's min_cut_confidence slider. The ACCEPT/REVIEW boundary now uses the slider value (default 80%). Ads between REJECT_CONFIDENCE and the slider correctly get REVIEW instead of being silently auto-accepted. Removed `HIGH_CONFIDENCE` constant from config.py.
- **Pattern learning quality gates** (Phase 10): `_learn_from_detections` now only creates patterns from ads that were actually cut (`was_cut=True`). Sponsor extraction uses 4-tier DB resolution (DB lookup on sponsor field, DB lookup on reason text, regex extraction, raw sponsor fallback) with two rejection gates: prefix check (rejects "Capital" when "Capital One" exists in DB) and short-word check for unknown sponsors. Removed space-stripping from `_extract_sponsor_from_reason` that corrupted multi-word names.
- **Pattern learning moved from ad_detector to main.py**: `_learn_from_detections` call moved to after validation sets `was_cut`, so the `was_cut` gate works correctly.
- **Episode duration from audio file** (Phase 14): `episode_duration` now uses `audio_processor.get_audio_duration()` instead of `segments[-1]['end']`. Fixes trailing ads not being extended when audio file is longer than last transcribed word (Whisper stops at speech end, missing trailing silence/music/jingle).

### Fixed
- **Reason fallback logic** (Phase 9): `extract_sponsor_name()` is now tried first; only falls back to Claude's raw reason field if it returns the default "Advertisement detected". Previously the raw reason was checked first but rejected valid values like "mid-roll" or "host read" that appeared in `INVALID_SPONSOR_VALUES`.
- **Pass 2 ads displayed out of chronological order** (Phase 12): Combined ads list now sorted by start timestamp after appending pass 2 verification ads.
- **Pass 2 status showing "Verifying" instead of substages** (Phase 13): Changed verification detection callback from `verifying:N/M` to `detecting:N/M` so the UI shows "Pass 2: Detecting ads" instead of overwriting substage labels. Removed premature `pass2:verifying` status update. Cleaned up `pass2:verifying` label from frontend status bar.

## [0.1.249] - 2026-02-11

### Fixed
- **Pass 2 ads missing from UI**: `save_combined_ads` was called only with pass 1 ads. Pass 2 verification ads (`v_ads_for_ui`) were cut from audio but never appended to the stored ad markers, so they didn't appear in the UI or API response. Now re-saves combined ads after verification adds its ads.
- **Pass 2 status stuck on "Verifying"**: Verification pass only reported status during Claude detection (via progress callback), but transcription and audio analysis stages had no status updates. Added `progress_callback` calls for transcribing and analyzing steps inside `VerificationPass.verify()`, so the UI now shows "Pass 2: Transcribing", "Pass 2: Analyzing audio", "Pass 2: Detecting ads" progression.

## [0.1.248] - 2026-02-11

### Changed
- **Transition detection threshold raised from 3.5 dB to 12.0 dB**: The old threshold caught normal audio variation as DAI splices. Real DAI ad insertions produce 12+ dB jumps. Added delta-ratio symmetry filter (< 0.5 rejected) and recalibrated confidence formula.
- **Audio enforcer converted from independent actor to prompt formatter**: The old enforcer pattern-matched transcript text independently of Claude and created phantom ads. New `AudioEnforcer.format_for_window()` formats audio signals as text context injected into Claude's per-window prompts so Claude makes all ad/not-ad decisions with full audio evidence.
- **Audio signals now included in Claude's detection prompts**: Both pass 1 (`detect_ads`) and pass 2 (`run_verification_detection`) inject DAI transition pairs and volume anomalies into each window's prompt via the audio enforcer formatter.
- **Verification pass returns dual timestamps**: Pass 2 now maps processed-audio timestamps back to original-audio coordinates. `ads` (original timestamps) used for UI/DB display, `ads_processed` (processed timestamps) used for FFMPEG cutting. Fixes timestamp mismatch where pass 2 ads showed wrong positions in the UI.
- **Frontend status display shows pass 1/pass 2 stages**: Status bar labels prefixed with "Pass 1:" and "Pass 2:" for clarity. `getStageLabel()` function handles substage parsing (e.g., `pass1:detecting:2/5`). Detection stage badges renamed from "First Pass"/"Audio Enforced"/"Verification" to "Pass 1"/"Pass 2".

### Removed
- **Whisper model unload before audio analysis**: Audio analysis is CPU-only, so unloading the GPU model before it was unnecessary and wasted 10-15s on reload for the verification pass.
- **Audio enforcer post-detection step**: The independent enforcement step in main.py that created ads from uncovered audio signals has been removed. Audio signals now flow through Claude's prompt instead.
- **`DAI_CONFIDENCE_ONLY_THRESHOLD` config constant**: No longer needed since the enforcer no longer creates ads independently.

### Fixed
- **Verification pass `_transcribe_on_gpu` double exception handling**: The inner try/except caught all exceptions and returned `[]`, preventing the outer catch from ever setting `'transcription_failed'` status. Removed inner try/except so exceptions propagate to the caller.
- **Anthropic SDK pinned version**: Unpinned `anthropic==0.49.0` to `anthropic>=0.49.0` to allow compatible updates.

## [0.1.247] - 2026-02-10

### Fixed
- **Verification pass uses GPU instead of CPU**: Verification transcription was creating a fresh CPU model (20-30x slower) instead of reusing the GPU singleton. Now calls `WhisperModelSingleton.get_instance()` which lazy-reloads the GPU model after it was freed for audio analysis. ~30-min episodes go from 15-30 min to ~1-2 min for verification transcription.
- **ACCEPT decisions now always cut**: Validator ACCEPT (confidence >= 0.60) and the cutting filter (MIN_CUT_CONFIDENCE = 0.80) were contradictory -- ads with 0.60-0.79 confidence were ACCEPTed then not cut. Now ACCEPT = always cut, REJECT = never cut, REVIEW = confidence gate. This prevents validated ads like sponsor reads from being silently kept in audio.
- **AudioEnforcer false positives from confidence-only path**: DAI transition pairs with confidence >= 0.80 could create ads without any ad language in the transcript, causing false positives when strong audio transitions occurred during normal show content. Raised threshold from 0.80 to 0.95 (`DAI_CONFIDENCE_ONLY_THRESHOLD`). Ads with ad language in transcript are unaffected.
- **Mid-roll position boost gaps**: Position windows had dead zones (0.35-0.45, 0.55-0.65) where ads received no position boost. Simplified from three narrow windows (`MID_ROLL_1/2/3`) to a single continuous range (0.15-0.85) so all mid-roll ads get the +0.05 confidence boost.

## [0.1.246] - 2026-02-10

### Fixed
- **CTranslate2 cuDNN crash (SIGABRT code 134)**: The nvidia-cudnn-cu12 pip package installs `.so` files into Python's site-packages (`nvidia/cudnn/lib/`), but CTranslate2 uses `dlopen()` which only searches `LD_LIBRARY_PATH` and system paths. Added `LD_LIBRARY_PATH` to Dockerfile ENV pointing to the nvidia pip package lib directories. Removed redundant `nvidia-cudnn-cu12` from requirements.txt (already a dependency of torch).

## [0.1.245] - 2026-02-10

### Fixed
- **Restore nvidia-cudnn-cu12 dependency**: CTranslate2 (faster-whisper GPU backend) requires cuDNN for CUDA inference. Removal in v0.1.242 caused worker SIGABRT crashes during transcription. Re-added `nvidia-cudnn-cu12==8.9.2.26`.
- **Pattern backfill crash**: `extract_transcript_segment` was called in `database.py` but never imported. Replaced with already-imported `extract_text_in_range` (identical behavior).
- **Stuck episode reset killing active jobs**: `reset_stuck_processing_episodes()` ran on every Gunicorn worker boot and reset ALL processing episodes with no time check. A worker restart during active transcription would kill the in-progress job. Added 30-minute guard so only genuinely stuck episodes are reset.
- **Orphaned queue state blocking reprocessing**: When a worker crashes (SIGABRT), the flock is released by the OS but the state file still says "processing". `_clear_stale_state()` only checked the 60-minute timeout, so any reprocess attempt got "already_processing" for up to an hour. Now probes the flock to detect orphaned state immediately -- if no process holds the lock, the state is cleared regardless of elapsed time.

## [0.1.244] - 2026-02-10

### Changed
- **Detailed verification prompt**: Replaced simplified verification pass prompt with full version including fragment detection (highest priority), missed ad patterns, "how to identify fragments" guidance, ad boundary rules, and three concrete examples (fragment, missed ad, clean episode).
- **First pass prompt improvement**: Added "dynamically inserted ads" detection line to first pass prompt WHAT TO LOOK FOR section.

### Removed
- **Dead second pass prompt**: Removed unused `DEFAULT_SECOND_PASS_PROMPT` constant (blind second pass was replaced by verification pipeline in v0.1.242).
- **Stale UI text**: Removed "Can be skipped per-podcast" from verification pass Settings description (no longer applicable).

## [0.1.243] - 2026-02-10

### Fixed
- **Pin numpy<2.0 for CPU compatibility**: numpy 2.x requires X86_V2 CPU instructions which the target server lacks, causing a RuntimeError on startup via ctranslate2 import. Pinning numpy<2.0 resolves the crash introduced when the huggingface_hub upper pin was removed (pyannote constraint gone).

## [0.1.242] - 2026-02-10

### Changed
- **Replaced two-pass architecture with verification pipeline**: The blind second pass (re-analyzing the same transcript with a different prompt) is replaced by a post-cut verification pass that re-transcribes the processed audio on CPU and runs full detection with a "what doesn't belong" prompt. If missed ads are found, the pass 1 output is re-cut directly. No timestamp mapping needed since verification operates entirely in processed-audio coordinates.
- **Removed audio context injection from Claude's prompt**: Audio signals (volume anomalies, transitions) were previously formatted as text and injected into Claude's sliding window prompt. This indirect approach is replaced by programmatic audio enforcement (see below) that acts as a post-Claude step.
- **Removed speaker diarization and music bed detection**: Dropped pyannote.audio and librosa dependencies entirely. Speaker analysis and music detection added GPU memory pressure, processing time, and heavy dependencies (nvidia-cudnn-cu12, HF_TOKEN auth) for marginal benefit. Audio analysis now runs volume analysis only (ffmpeg ebur128, zero extra dependencies) plus the new transition detector.
- **Audio analysis always enabled**: Removed the global `audioAnalysisEnabled` toggle and per-feed `audioAnalysisOverride` settings. Volume analysis via ffmpeg is lightweight and always runs.
- **Settings renamed**: `secondPassPrompt` -> `verificationPrompt`, `secondPassModel` -> `verificationModel`. Old settings are automatically migrated. `multiPassEnabled` toggle removed (verification always runs).
- **AdMarker schema updated**: `pass` field (1, 2, "merged") replaced with `detection_stage` enum (`first_pass`, `audio_enforced`, `verification`).

### Added
- **Abrupt transition detection**: New `TransitionDetector` analyzes frame-to-frame loudness jumps in the existing volume analyzer output (zero extra cost). Pairs up/down transitions into candidate DAI (dynamically inserted ad) regions with configurable thresholds (`TRANSITION_THRESHOLD_DB`, `MIN/MAX_TRANSITION_AD_DURATION`).
- **Audio signal enforcement**: New `AudioEnforcer` runs after Claude's first pass to programmatically check whether audio signals overlap with detected ads. Uncovered DAI transition pairs with ad language in the transcript (or high confidence >= 0.8, or sponsor match) become new ads. Volume anomalies require both ad language AND sponsor match (higher bar). Existing ads are extended up to 30s when a signal partially overlaps their boundaries.
- **Verification pass module**: New `VerificationPass` class encapsulates the full post-cut pipeline: CPU re-transcription (using faster_whisper directly, not WhisperModelSingleton), audio analysis on processed audio, Claude detection with verification prompt/model, audio enforcement, and ad validation.
- **Separate verification model setting**: The verification pass can use a different Claude model from the first pass, configurable in Settings as "Verification Model".

### Removed
- **Dependencies**: `librosa>=0.10.0`, `pyannote.audio>=3.1.0,<4.0.0`, `nvidia-cudnn-cu12==8.9.2.26`. Removed `huggingface_hub` upper version pin (`<1.0` was a pyannote constraint).
- **Files**: `src/audio_analysis/speaker_analyzer.py`, `src/audio_analysis/music_detector.py`.
- **Methods**: `detect_ads_second_pass()`, `is_multi_pass_enabled()`, `get_second_pass_prompt()`, `get_second_pass_model()`, `_format_audio_context()`, `_format_time()`, `format_for_claude()`, `is_enabled_for_podcast()` from AudioAnalyzer.
- **Settings**: `multi_pass_enabled`, `audio_analysis_enabled`, `volume_analysis_enabled`, `music_detection_enabled`, `speaker_analysis_enabled`, `music_confidence_threshold`, `monologue_duration_threshold`.
- **Dataclasses**: `SpeakerSegment`, `ConversationMetrics`. `SignalType.MUSIC_BED`, `MONOLOGUE`, `SPEAKER_CHANGE` enum values.

---

## [0.1.241] - 2026-02-09

### Changed
- **Centralized shared constants into `utils/constants.py`**: Deduplicated `INVALID_SPONSOR_VALUES` (3 definitions across `ad_detector.py` and `text_pattern_matcher.py`), `STRUCTURAL_FIELDS`, `SPONSOR_PRIORITY_FIELDS`, `SPONSOR_PATTERN_KEYWORDS`, `INVALID_SPONSOR_CAPTURE_WORDS`, and `NOT_AD_CLASSIFICATIONS` into a single source of truth. All consumers now import from `utils.constants`.
- **Consolidated `extract_sponsor_from_text()` into `SponsorService`**: Removed 3 identical implementations (module-level in `api.py`, local function in `database.py`, and local function in `ad_detector.py`). `SponsorService.extract_sponsor_from_text()` is now the canonical static method; `api.py` delegates to it, `database.py` uses a lazy import.
- **Extracted `_parse_aliases()` helper in `SponsorService`**: Replaced 3 identical JSON alias-parsing blocks in `get_sponsor_names()`, `find_sponsor_in_text()`, and `get_sponsors_in_text()` with a single `_parse_aliases()` static method.
- **Precompiled sponsor regex patterns in `SponsorService`**: Word-boundary patterns for sponsor matching are now compiled once during `_refresh_cache_if_needed()` and stored as `_compiled_patterns` dict, instead of recompiling per search call.
- **Replaced `datetime.utcnow()` with `datetime.now(timezone.utc)`**: Updated all 19 call sites across 7 files (`cleanup_service.py`, `main.py`, `api.py`, `database.py`, `sponsor_service.py`, `pattern_service.py`, `text_pattern_matcher.py`). Removed `.replace(tzinfo=None)` workarounds that were needed for mixed tz-aware/naive comparisons. All timestamp strings stored to DB now use `strftime('%Y-%m-%dT%H:%M:%SZ')` to match SQLite's default format, replacing `.isoformat() + 'Z'` which would have produced malformed `+00:00Z` suffixes with tz-aware datetimes.
- **Replaced hardcoded thresholds with config constants**: Added `CONTENT_DURATION_THRESHOLD` (120s) and `LOW_EVIDENCE_WARN_THRESHOLD` (60s) to `config.py`. Updated `ad_detector.py` to use `LOW_CONFIDENCE`, `CONTENT_DURATION_THRESHOLD`, and `LOW_EVIDENCE_WARN_THRESHOLD` instead of hardcoded `0.5`, `120`, and `60`.
- **Eliminated redundant stale-state checks in `ProcessingQueue`**: `is_processing()` and `is_busy()` no longer call `_clear_stale_state()` directly since `get_current()` already does it.
- **Removed 4 redundant inline `import json` statements in `api.py`**: `json` is already imported at module level.

### Fixed
- **Atomic state file write in `ProcessingQueue`**: `_write_state()` now writes to a `.tmp` file and renames atomically, preventing corrupt state if the process crashes or OOMs mid-write.
- **Strategy 3 JSON parse unhandled exception**: `ad_detector.py` Strategy 3 (bracket fallback) `json.loads()` was not wrapped in try/except unlike Strategies 1-2. Now catches `json.JSONDecodeError` with diagnostic logging (content length, first/last chars).
- **5 bare `except:` clauses replaced with specific types**: `api.py` (json/type/key errors), `main.py` x2 (value/type errors), `transcriber.py` x2 (OS errors for file cleanup).

### Added
- **Updated OpenAPI spec from 0.1.184 to current**: Added 16 missing endpoint definitions (OPML import, batch reprocess, sponsor CRUD, normalization CRUD, pattern stats/health/merge, search endpoints, queue clear, prompts reset). Updated 3 existing sponsor/normalization endpoints to reflect the new SponsorService CRUD API. Added `Sponsor`, `Normalization`, and `SearchResult` schemas. Version is now served dynamically from `version.py` at runtime.
- **Wired up pattern learning pipeline**: 4 functions (260 lines) that were part of the designed pattern learning system but had zero callers are now connected:
  - `merge_similar_patterns()`: Called from `promote_pattern()` after promotion to consolidate similar patterns at the new scope level.
  - `check_sponsor_global_promotion()` and `auto_promote_sponsor_patterns()`: Called from `record_pattern_match()` when a sponsor hits the global threshold (3+ podcasts).
  - `store_fingerprint()`: Called from `_learn_from_detections()` after creating a text pattern, to also store the audio fingerprint for the same segment.

---

## [0.1.240] - 2026-02-08

### Fixed
- **Low-confidence segments without sponsor evidence accepted as ads**: Added a confidence gate (< 50%) before the existing duration gate in dynamic validation. Segments with no sponsor field, no known sponsor match, and no ad-language patterns are now rejected if confidence is below 50%, regardless of duration. Previously, short segments (< 120s) with confidence as low as 30-40% would pass through even when Claude's own reason described them as non-ads.
- **False positive sponsor matches from substring collision**: `find_sponsor_in_text()` and `get_sponsors_in_text()` used naive `in` substring matching, so short sponsor names or aliases (e.g., "cam") could match inside unrelated words (e.g., "Cam Newton"). Both functions now use `re.search()` with word boundaries (`\b`). Names and aliases shorter than 3 characters are skipped entirely to prevent false positives.
- **`was_cut=false` ads displayed alongside actually-removed ads in UI**: The API endpoint separated ad markers only by validator decision (REJECT vs everything else), so low-confidence REVIEW ads with `was_cut=false` appeared in `adMarkers` next to real removed ads. The separation logic now also checks `was_cut`: any ad with `was_cut=false` goes into `rejectedAdMarkers` regardless of validation decision.

---

## [0.1.239] - 2026-02-07

### Fixed
- **Content segments parsed as ads when LLM returns descriptive reasons without sponsor info**: Added dynamic ad-evidence validation in `_parse_ads_from_response()` that requires positive proof a segment is an ad (known sponsor in database, ad-language patterns, or explicit sponsor field) before accepting it. Segments >= 120s with no evidence are rejected; 60-120s segments log a warning. This replaces the whack-a-mole approach of growing blocklists with every new LLM output variation. The check is database-driven via `SponsorService.find_sponsor_in_text()` so new sponsors added via API automatically work without code changes.
- **Confidence values displayed as 10000% in UI**: When Claude returns confidence as a percentage (e.g., `100.0` instead of `1.0`), the parser now normalizes to 0-1 range by dividing values > 1.0 by 100, then clamping to [0.0, 1.0].

---

## [0.1.238] - 2026-02-07

### Fixed
- **Incorrect model IDs in fallback lists**: Fixed `claude-opus-4-1-20250414` to correct date suffix `claude-opus-4-1-20250805`. Removed `claude-3-5-sonnet-20241022` which is no longer in the Anthropic catalog. Added missing `claude-haiku-4-5-20251001` (Haiku 4.5) and `claude-opus-4-20250514` (legacy Opus 4) to all three fallback lists: `AnthropicClient._get_fallback_models()`, `OpenAICompatibleClient._get_fallback_models()`, and `AdDetector.get_available_models()`. Also added `claude-opus-4-1-20250805` and `claude-opus-4-20250514` to the `ad_detector.py` fallback which was missing them entirely.

---

## [0.1.237] - 2026-02-07

### Added
- **Dynamic sponsor injection into Claude prompts**: `SponsorService.get_claude_sponsor_list()` existed but was never called. System and second-pass prompts now append a "DYNAMIC SPONSOR DATABASE" section at detection time with all known sponsors from the database. This supplements the hardcoded seed list without modifying the stored/customizable prompt text. Sponsors added via API or discovered during processing now actually influence future detections.
- **Podcast-specific sponsor history in detection context**: Both first and second pass now query `ad_patterns` for the podcast being processed and include "Previously detected sponsors for this podcast: X, Y, Z" in the description section. This gives Claude prior knowledge of which sponsors have appeared in this podcast before.
- **Configured models always shown in model list**: New `_ensure_configured_models_present()` ensures that models set as first-pass or second-pass model always appear in the `/settings/models` API response, even if the wrapper API doesn't advertise them. Logs when a configured model is injected.

### Fixed
- **Opus 4.6 missing from fallback model lists**: Added `claude-opus-4-6` to fallback lists in `AnthropicClient`, `OpenAICompatibleClient`, and `AdDetector.get_available_models()`. Fallbacks are used when the wrapper API is unreachable.

---

## [0.1.236] - 2026-02-06

### Fixed
- **Non-ads extracted as ads when Claude marks them `is_ad: false`**: Added filtering in `_parse_ads_from_response()` to skip entries where `is_ad` is explicitly false/no/0 or where `classification`/`type` indicates non-ad content (content, editorial, organic, interview, etc.). This was the root cause of episodes like `it-s-a-thing:1af1082d376d` losing over half their duration -- Claude's second pass returned segments with `is_ad: false` and `classification: "content"` but the parser treated ALL entries as ads regardless.
- **Generic "Advertisement detected" fallback from unknown field names**: Replaced static allowlists for sponsor and description extraction with dynamic field scanning. Instead of maintaining lists of field names Claude might use, the parser now defines STRUCTURAL_FIELDS (timestamps, booleans, config) and treats everything else as a candidate for sponsor/description info. This eliminates the recurring need to patch field names (previously patched in v0.1.217, 218, 220, 232, 234, 235).
- **Reason field duplication when sponsor and description overlap**: Added `_text_is_duplicate()` helper that checks if one string starts with the other or they share >80% of words. Prevents output like "BetterHelp advertisement: BetterHelp advertisement for therapy services".
- **Processing queue kills long-running jobs via stale lock detection**: `_clear_stale_state()` was called from `is_busy()`/`get_current()` without `_fd_lock` protection. When a long episode exceeded `MAX_JOB_DURATION`, stale detection would release the lock from under the running thread, allowing another episode to acquire it and causing concurrent processing failures. Now checks if the current process holds the lock before clearing -- if it does, the job is still alive (just long-running) and only a warning is logged.
- **Queue timeouts too aggressive for long episodes**: Increased `MAX_JOB_DURATION` from 30 to 60 minutes in both `processing_queue.py` and `status_service.py`. Increased `background_queue_processor()` max_wait from 10 to 60 minutes and orphan check threshold from 35 to 65 minutes.

---

## [0.1.235] - 2026-02-06

### Fixed
- **Ad detection reason parsing shows generic "Advertisement detected" instead of sponsor names**: Fixed parsing logic in `_parse_ads_from_response()` that was missing field names Claude uses. Added `sponsor_name` to `SPONSOR_PRIORITY_FIELDS` (Claude often returns this instead of just `sponsor`). Added `reason` and `notes` to description fields (Claude provides context in these). Added pre-check for valid `reason` field before running sponsor extraction - if Claude already provided a valid reason, use it directly instead of overwriting with extraction logic. This fixes the cascade where bad parsing led to no pattern creation (patterns are rejected when sponsor is "Advertisement detected").
- **Reason field duplicated in sponsor + description output**: When Claude provided a valid `reason` field (e.g., "BetterHelp advertisement for therapy"), the pre-check block correctly used it as the sponsor reason, but then the description extraction loop also matched the same `reason` field, producing duplicated output like "BetterHelp advertisement: BetterHelp advertisement". Removed `reason` from `desc_fields` since it is already handled by the pre-check block.
- **Crash on `end_text: null` from Claude response**: When Claude returns `"end_text": null` in JSON, `dict.get('end_text', '')` returns `None` (not `""`) because the key exists with an explicit null value. This caused `TypeError: 'NoneType' object is not subscriptable` when slicing for log output. Fixed all three `end_text` access points to use `or ''` pattern which correctly converts None to empty string.

---

## [0.1.234] - 2026-02-05

### Fixed
- **Ad detection parsing missing ads_detected key and nested structures**: Fixed bug in `_parse_ads_from_response()` where Claude's ad detections were not being extracted due to missing parser support. Added support for `ads_detected` key (Claude sometimes uses this instead of `ads`). Added support for nested `window` structure (e.g., `{"window": {"ads_detected": [...]}}`). The `parse_timestamp()` function already handles string timestamps with "s" suffix (e.g., "28.8s"). This fixes episodes where Claude correctly detected ads but the parser failed to extract them.

---

## [0.1.233] - 2026-02-05

### Fixed
- **Reprocess button does nothing when queue is busy**: When clicking "Reprocess" while another episode was processing, the API returned "queued" but never actually added the episode to the processing queue. The `background_queue_processor()` only reads from the `auto_process_queue` table, so episodes that bypassed this table were never picked up. Both reprocess endpoints (`/reprocess` and `/episodes/{id}/reprocess`) now call `db.queue_episode_for_processing()` when the processing lock is busy, ensuring episodes are actually added to the queue for background processing.

---

## [0.1.232] - 2026-02-05

### Fixed
- **Ad detection parsing failures**: Fixed bug in `_parse_ads_from_response()` where valid ads were not being extracted from Claude's responses. Added support for `ads_and_sponsorships` response key (Claude sometimes uses this instead of just `ads`). Added support for `start_timestamp`/`end_timestamp` field names (Claude's alternate naming convention). This fixes 0 ads detected for episodes where Claude was correctly identifying ads but the parser couldn't extract them.
- **CUDA OOM from legacy reprocess endpoint**: The old `/feeds/<slug>/episodes/<episode_id>/reprocess` endpoint was calling `process_episode()` directly, bypassing the `ProcessingQueue` lock that prevents concurrent GPU processing. This allowed two episodes to transcribe simultaneously, exhausting GPU memory. Updated to use `start_background_processing()` like the new endpoint, ensuring proper queue coordination. The endpoint now returns 202 Accepted and processes asynchronously.

---

## [0.1.231] - 2026-02-05

### Fixed
- **Infinite episode retry loop**: Fixed bug introduced in v0.1.225 where `reset_orphaned_queue_items()` would reset stuck queue items to 'pending' indefinitely without incrementing the `attempts` counter. Episodes that repeatedly fail (e.g., CUDA OOM on long episodes) would cycle forever: fail -> reset to pending -> retry -> fail -> repeat. Now the function increments `attempts` on each reset and marks items as permanently 'failed' after exceeding `max_attempts` (default 3). This stops resource-consuming episodes from blocking the queue indefinitely.

---

## [0.1.230] - 2026-02-05

### Fixed
- **Concurrent episode processing despite fcntl.flock**: Fixed bug where two episodes could still process simultaneously within the same Gunicorn worker. The issue was that `acquire()` always opened a new file descriptor, overwriting `_lock_fd` and orphaning the previous fd. Since `flock()` is per-fd (not per-file) within the same process, the second `flock()` on a different fd would succeed. Added `_fd_lock` threading lock to synchronize access to `_lock_fd` across threads, and added early rejection if `_lock_fd` is already set (meaning this process already holds the lock). Promoted lock acquire/release logging from DEBUG to INFO for production visibility.

---

## [0.1.229] - 2026-02-05

### Fixed
- **Concurrent episode processing causing CUDA OOM**: ProcessingQueue was using `threading.Lock` which only prevents concurrent access within a single Python process. With 2 Gunicorn workers (separate processes), each had its own lock, allowing both to process episodes simultaneously and exhausting GPU memory. Replaced with `fcntl.flock()` file-based locking that coordinates across all worker processes. The lock file and state are stored in `/app/data/` for cross-process visibility.

- **Episode ID instability causing duplicates**: Same episodes were appearing multiple times in the database with different IDs because some RSS feeds (especially Megaphone) have unstable GUIDs or dynamic URL parameters. Added title+pubDate deduplication check in `refresh_rss_feed()` - before queuing a "new" episode, we now check if an episode with the same title and publish date already exists. If found, the episode is skipped with a warning log. Added `get_episode_by_title_and_date()` method to database.py.

---

## [0.1.228] - 2026-02-05

### Fixed
- **App startup failure**: Added migration to update episodes table CHECK constraint to include `permanently_failed` status. The `permanently_failed` status was added in v0.1.225 code but the migration to update existing databases' CHECK constraint was missing. SQLite requires table recreation to modify constraints.

---

## [0.1.227] - 2026-02-05

### Fixed
- **SQLite database locking**: Fixed "database is locked" errors that occurred during concurrent database access (e.g., when transcription completed while another operation was writing). Added `PRAGMA journal_mode = WAL` for Write-Ahead Logging (allows concurrent readers with one writer) and `PRAGMA busy_timeout = 30000` (SQLite retries for 30 seconds instead of failing immediately). The existing `timeout=30.0` in `sqlite3.connect()` is Python's lock acquisition timeout, not SQLite's busy timeout - SQLite's default busy_timeout is 0 which fails immediately on lock contention.

---

## [0.1.226] - 2026-02-05

### Fixed
- **Duplicate episode ID generation**: Fixed bug where the same episode would be processed repeatedly with different IDs. The issue occurred because `generate_episode_id()` used only the audio URL, which can include dynamic CDN tracking parameters (e.g., Megaphone's `awCollectionId`/`awEpisodeId`). When these parameters changed between RSS refreshes, the same episode appeared as "new" with a different ID, causing infinite reprocessing loops. Now uses RSS GUID (stable identifier per RSS spec) with URL fallback for feeds without GUIDs.

- **Dead code cleanup**: Removed two calls to non-existent `storage.delete_ads_json()` method in reprocess endpoints. The method was removed in v0.1.26 but calls remained wrapped in try/except, causing harmless warnings. Data clearing is already handled by `db.clear_episode_details()`.

- **Queue race condition**: Moved `status_service.start_job()` call from inside the processing thread to immediately after acquiring the ProcessingQueue lock in `start_background_processing()`. This prevents a new episode from starting before StatusService knows about the current one, closing a timing gap that allowed episode overlap.

- **Ad detection progress updates**: Added `progress_callback` parameter to `detect_ads()`, `detect_ads_second_pass()`, and `process_transcript()` methods. Now reports progress for each detection window (e.g., "detecting:3/12"), keeping the UI progress indicator alive during the 2-5+ minute ad detection phase that previously caused the progress bar to disappear.

---

## [0.1.225] - 2026-02-05

### Fixed
- **Sponsor extraction garbage capture**: Fixed regex patterns in `extract_sponsor_from_text()` that would incorrectly extract common English words as sponsor names (e.g., "not an" from "This is not an advertisement", "consistent with" from Claude reasoning). Added `INVALID_SPONSOR_CAPTURE_WORDS` validation and rejection of all-lowercase multi-word phrases.

- **Queue race condition**: Fixed race condition where `db.update_queue_status(queue_id, 'processing')` was called BEFORE the processing lock was acquired. If the worker crashed between these calls, the queue item would remain stuck in 'processing' status. Now the status is only updated AFTER successfully acquiring the lock.

- **Stuck episode retry tracking**: Enhanced `reset_stuck_processing_episodes()` to track retry count and mark episodes as `permanently_failed` after 3 crashes (MAX_EPISODE_RETRIES). Prevents infinite retry loops for episodes that consistently crash workers (e.g., OOM issues).

### Added
- **Orphaned queue detection**: Added `db.reset_orphaned_queue_items()` method to detect and reset queue items stuck in 'processing' for over 35 minutes. Called periodically from the queue processor to recover from worker crashes without restart.

- **Confidence threshold logging**: Added log line at start of episode processing showing current confidence threshold (e.g., "Confidence threshold: 80%"). Helps verify the aggressiveness slider setting is being applied.

- **Podcast description in prompts**: Now passes the podcast-level description (in addition to episode description) to Claude prompts for both first and second pass ad detection. This provides additional context about the show format and typical sponsors.

### Improved
- **JSON format instructions**: Enhanced JSON output instructions for Anthropic API to be more explicit: numbered requirements, explicit "use null not None" rule, clearer formatting. Reduces JSON parse errors from malformed responses.

- **Podcast description in UI**: Added missing `description` field to `/feeds/{slug}` API response. The UI already supported displaying podcast descriptions but the API wasn't returning it.

---

## [0.1.224] - 2026-02-02

### Fixed
- **Reprocess endpoint timeout**: Fixed 504 Gateway Timeout when reprocessing episodes. The endpoint was calling `process_episode()` synchronously, causing nginx to timeout before processing completed. Now uses `start_background_processing()` (same pattern as JIT processing) and returns 202 Accepted immediately. The frontend polls for status updates via existing mechanisms.

---

## [0.1.223] - 2026-02-02

### Fixed
- **Full Analysis mode now actually processes**: Fixed backend bug where the `/episodes/{slug}/{episodeId}/reprocess` endpoint only set `status='pending'` in the database but never triggered actual processing. Episodes would remain stuck in pending status indefinitely. The endpoint now clears cached data and calls `process_episode()` synchronously, matching the behavior of the legacy reprocess endpoint.

---

## [0.1.222] - 2026-02-02

### Added
- **Pattern ID column on Patterns page**: Added sortable ID column as the first column in the patterns table. Pattern IDs are now visible in both desktop table view and mobile card view.

- **Clickable pattern links in ad reasons**: Pattern references like "(pattern #63)" in detected ad descriptions are now clickable links that navigate to the pattern detail modal on the Patterns page.

- **Pattern search by ID**: The search filter on the Patterns page now also matches pattern IDs, so you can search for "63" to find pattern #63.

### Fixed
- **"Full Analysis" mode ignored during reprocess**: Fixed a bug where clicking "Full Analysis" in the reprocess menu would still use patterns instead of pure Claude analysis. The frontend was calling the wrong API endpoint (`/feeds/{slug}/episodes/{episodeId}/reprocess`) which ignored the mode parameter. Now correctly calls `/episodes/{slug}/{episodeId}/reprocess` which properly handles the mode.

---

## [0.1.221] - 2026-02-02

### Improved
- **Pattern match descriptions include pattern reference**: Pattern-matched ads now show "Sponsor (pattern #X)" format in the reason field instead of just the sponsor name. This provides traceability for pattern matches, making it easier to identify and manage patterns.

### Added
- **GET /patterns/contaminated endpoint**: New endpoint to find all active patterns containing multiple ad transition phrases, indicating merged multi-sponsor ads that should be split. Returns pattern IDs, sponsors, text lengths, and transition counts.

- **POST /patterns/{id}/split endpoint**: New endpoint to split a contaminated pattern into separate single-sponsor patterns. Uses the existing `TextPatternMatcher.split_pattern()` method to detect ad transitions and create individual patterns. The original pattern is disabled after successful split.

---

## [0.1.220] - 2026-02-01

### Fixed
- **Multi-sponsor pattern contamination**: Added `detect_multi_sponsor_pattern()` and `split_pattern()` methods to `TextPatternMatcher` to detect and split patterns that were incorrectly created with multiple sponsor reads merged together. These methods scan for common ad transition phrases ("this episode is brought to you by", "brought to you by", etc.) and create separate patterns for each sponsor.

- **Prevention of future contamination**: Added validation to `create_pattern_from_ad()` to reject patterns with:
  - Duration > 120 seconds (reduced from 180s) - single ads rarely exceed 2 minutes
  - Multiple ad transition phrases detected - indicates merged multi-ad spans
  - Sponsor name not appearing in intro text - may indicate misattribution

- **Missing descriptions in reason field**: Enhanced `_parse_ads_from_response()` to extract Claude's explanation/description from response and combine with sponsor name in the reason field. Now checks `explanation`, `content_summary`, `description`, `ad_description`, `message`, `content`, and `summary` fields. Descriptions over 150 characters are truncated.

---

## [0.1.219] - 2026-02-01

### Changed
- **Codebase cleanup**: Comprehensive cleanup to remove dead code, unused dependencies, and stale artifacts:
  - Deleted stale `tmp/` directory (docker-compose.wrapper.yml, llm_client.py copy, migration docs)
  - Removed unused `soundfile` dependency from requirements.txt
  - Removed unused imports from 9 Python files (ad_detector, chapters_generator, rss_parser, storage, text_pattern_matcher, transcriber, gpu, pattern_service, api)
  - Removed unused exception imports (APIError, APIConnectionError, RateLimitError, InternalServerError) from ad_detector.py
  - Fixed .gitignore duplicates (.env, *.db, *.log, pixelprobe.db) and removed contradictory CLAUDE.md entry
  - Removed TODO comment from main.py
  - Removed PLACEHOLDER env var from docker-compose.yml claude-wrapper service

---

## [0.1.218] - 2026-02-01

### Fixed
- **Reasoning field precedence bug in sponsor extraction**: Removed `reasoning` from `SPONSOR_PRIORITY_FIELDS` as it was incorrectly taking precedence over `sponsor_name` in Phase 2 pattern matching. The `reasoning` field contains descriptive text (e.g., "Host read ad for eBay promoting...") and was being returned instead of the actual sponsor name. Now `reasoning` is only used in Phase 4 for regex-based text extraction as a fallback when no direct sponsor name is found.

---

## [0.1.217] - 2026-02-01

### Improved
- **Enhanced sponsor name extraction from OpenAI wrapper responses**: Added Phase 4 text extraction to extract sponsor names from descriptive fields like `reasoning` and `summary` when direct fields are missing or invalid. Improvements include:
  - Added `reasoning` to priority fields (catches "This is a BetterHelp ad" style responses) [Note: reverted in v0.1.218]
  - Added `ad_name` and `note` to pattern keywords for fuzzy matching
  - Added `summary` to fallback fields
  - New regex-based extraction parses sponsor names from text like "X advertisement", "ad for X", "promoting X"
  - Reduces generic "Advertisement detected" labels when Claude provides sponsor info in descriptive fields

---

## [0.1.216] - 2026-02-01

### Fixed
- **XML entity encoding in RSS feeds**: Escape all text content and URLs when generating modified RSS feeds to prevent invalid XML from unescaped ampersands in URLs. Applies `_escape_xml()` to channel title, link, language, image fields, and item link, guid, pubDate fields. Fixes potential XML parsing errors in podcast apps when feed URLs contain tracking parameters with `&` characters.

---

## [0.1.215] - 2026-02-01

### Changed
- **Refactored advertiser field extraction**: Replaced the hardcoded 16-field fallback chain with a flexible three-phase approach:
  1. Priority fields checked in order: `reason`, `advertiser`, `sponsor`, `brand`, `company`, `product`, `name`
  2. Pattern matching scans all keys for substrings: `sponsor`, `brand`, `advertiser`, `company`, `product` (catches variants like `ad_sponsor`, `sponsor_name`, `detected_brand`)
  3. Fallback fields: `description`, `content_summary`, `ad_content`, `category`
- This eliminates the need to manually add new field names whenever Claude uses a variation

---

## [0.1.214] - 2026-02-01

### Fixed
- **Added `detected_brand` to fallback chain**: Claude sometimes uses `detected_brand` as the field name for advertiser. Added to the fallback chain to extract sponsor names from this field.

---

## [0.1.213] - 2026-02-01

### Fixed
- **Filter invalid sponsor values**: Added validation to filter out literal string values like "None", "unknown", "null", "n/a" that Claude sometimes returns as sponsor names. These invalid values are now properly skipped in the fallback chain, allowing the next valid field to be used or falling back to "Advertisement detected" instead of displaying unhelpful values.

---

## [0.1.212] - 2026-02-01

### Fixed
- **Expanded advertiser field fallback chain**: Added `ad_sponsor`, `sponsor_name`, and `sponsor_or_product` to the fallback chain for extracting advertiser names. These field names were discovered via enhanced logging when Claude uses alternate response structures. Fixes more cases where ads showed as generic "Advertisement detected" instead of actual advertiser names like "Stash", "Ethos", "Mint Mobile".

---

## [0.1.211] - 2026-01-31

### Changed
- **Enhanced ad extraction logging**: Promoted ad extraction logging from DEBUG to INFO level for production visibility. When ads are extracted from LLM responses, logs now show the timestamps, reason/advertiser, and available fields - helping diagnose when ad names show as generic "Advertisement detected" instead of actual advertiser names.

### Fixed
- **Added `category` to advertiser field fallback**: Some Claude responses use `category` as the advertiser/reason field. Added to the fallback chain to extract more descriptive ad names.

---

## [0.1.210] - 2026-01-31

### Fixed
- **Display advertiser names in pattern-matched ads**: Text pattern and fingerprint matches now use the sponsor name from the database pattern as the `reason` field instead of generic labels like "Text pattern match (outro, pattern 69)". Falls back to generic label if no sponsor is defined for the pattern.

---

## [0.1.209] - 2026-01-31

### Fixed
- **Expanded advertiser name extraction**: Added more field name patterns for extracting advertiser/sponsor names from Claude responses: `sponsor`, `brand`, `company`, `name`, `description`, `ad_content`. Fixes ads showing as generic "Advertisement detected" instead of the actual advertiser name.
- **Debug logging for ad extraction**: Added debug logging to show extracted ad details and available fields, helping diagnose future field name issues.

---

## [0.1.208] - 2026-01-31

### Fixed
- **Handle Claude's elaborate response structures**: Claude sometimes ignores the prompt's output format and returns elaborate structured objects with `segments` or `advertisement_segments` keys instead of `ads`. Updated Strategy 0 in `_parse_ads_from_response()` to extract ads from these alternate structures, filtering `segments` arrays to only include items with `type: "advertisement"`.
- **Handle alternate field names in ad validation**: Claude uses inconsistent field names (`start_time` vs `start`, `advertiser` vs `reason`, etc.). Updated validation loop to check multiple field name patterns: `start`/`start_time`/`ad_start_timestamp`/`start_time_seconds` for timestamps, and `reason`/`advertiser`/`product`/`content_summary` for descriptions.
- **v0.1.207 regression**: Fixed regression where objects with `segments` key containing ads were treated as "no ads found" because they lacked an explicit `ads` key.

---

## [0.1.207] - 2026-01-31

### Fixed
- **JSON object without 'ads' key**: When Claude returns a valid JSON object but without an "ads" key (e.g., `{"status": "no_ads_detected"}`), treat it as "no ads found" rather than falling through to legacy parsing strategies that could fail with "Extra data" errors.

---

## [0.1.206] - 2026-01-31

### Fixed
- **JSON object response parsing**: Claude in JSON mode sometimes returns `{"ads": [...]}` objects instead of raw arrays. Added Strategy 0 to `_parse_ads_from_response()` that extracts arrays from objects with "ads" key.
- **Timestamp format parsing**: Added `parse_timestamp()` helper that handles multiple formats: seconds (1178.5), MM:SS ("19:38"), HH:MM:SS ("1:19:38"), and strings with "s" suffix ("1178.5s"). Fixes "could not convert string to float" errors when Claude returns human-readable timestamps.

---

## [0.1.205] - 2026-01-31

### Fixed
- **JSON response format for OpenAI-compatible LLM backends**: Added `response_format` parameter to `LLMClient.messages_create()` interface and pass `{"type": "json_object"}` for ad detection calls. This triggers JSON mode in the Claude Code OpenAI wrapper, ensuring clean JSON responses instead of markdown-wrapped output that caused "No valid JSON array found in response" warnings.

---

## [0.1.203] - 2026-01-30

### Added
- **Optional OpenAI-compatible LLM support**: New abstraction layer (`llm_client.py`) allows using alternative LLM backends instead of direct Anthropic API. Supports:
  - Direct Anthropic API (default, uses API credits)
  - OpenAI-compatible APIs (Claude Code wrapper for Max subscription, Ollama, etc.)
- **LLM provider configuration**: New environment variables `LLM_PROVIDER`, `OPENAI_BASE_URL`, `OPENAI_API_KEY`, `OPENAI_MODEL` for configuring alternative backends
- **Docker Compose wrapper service**: Optional `claude-wrapper` service for running the Claude Code OpenAI wrapper (enable with `--profile wrapper`)

### Changed
- **Ad detector refactored for LLM abstraction**: `ad_detector.py` now uses `llm_client.py` for all LLM interactions, maintaining backward compatibility
- **Chapters generator refactored for LLM abstraction**: `chapters_generator.py` now uses `llm_client.py` for all LLM interactions
- **Updated requirements**: Added `openai>=1.0.0` dependency for OpenAI-compatible API support

---

## [0.1.202] - 2026-01-29

### Fixed
- **Incorrect Whisper VRAM profiles causing overly conservative chunking**: Updated `WHISPER_MEMORY_PROFILES` in `config.py` to match faster-whisper's actual VRAM requirements (from README). large-v3 now correctly uses 5.5GB base (was 10GB), medium uses 4GB (was 5GB), small uses 2GB (was 2.5GB). This allows larger chunk sizes and fewer chunks for long episodes.
- **System RAM incorrectly limiting GPU transcription**: Changed `get_available_memory_gb()` in `utils/gpu.py` to use GPU VRAM as the primary limit for CUDA devices, not `min(GPU, System)`. System RAM was incorrectly limiting chunk sizes when GPU VRAM was the only relevant constraint.
- **API showing incorrect VRAM requirements**: Updated `/settings/whisper-models` endpoint to show correct values: medium now shows "~4GB", large-v3 shows "~5-6GB".

### Added
- **GPU memory logging after model load**: Added INFO-level log showing actual GPU memory allocated and reserved after Whisper model initialization, helping verify correct VRAM usage.
- **Memory visibility logging**: `get_available_memory_gb()` now logs both GPU and System RAM values at INFO level when running on CUDA, providing visibility into memory decisions.

---

## [0.1.201] - 2026-01-29

### Fixed
- **Uptime not resetting on container restart**: The `_get_start_time()` function in `api.py` was returning the stale value from the status file without calling `set_server_start_time()`. Renamed to `_init_server_start_time()` and now always writes the current time on module load, ensuring uptime resets on every server restart.

---

## [0.1.200] - 2026-01-29

### Added
- **Dynamic memory-aware chunked transcription**: Long episodes are now transcribed in dynamically-sized chunks based on available system RAM and GPU VRAM. The system:
  1. Queries available memory before each episode using `/proc/meminfo` and `torch.cuda`
  2. Uses model-specific memory profiles (base memory + MB/minute coefficients for each Whisper model size)
  3. Calculates optimal chunk duration with 70% safety margin
  4. Catches OOM errors during transcription and automatically retries with smaller chunks (halving up to 3 times)
  - Chunk sizes range from 5-60 minutes, with 30-second overlap for boundary alignment
  - Configurable via `CHUNK_*` and `WHISPER_MEMORY_PROFILES` in `config.py`
- **Memory cleanup on all failure paths**: Both `transcriber.py` and `main.py` now clear GPU memory and unload the Whisper model when transcription fails for any reason, preventing memory leaks during retry cycles.
- **Memory utility functions**: New `get_available_system_memory_gb()`, `get_available_gpu_memory_gb()`, and `get_available_memory_gb()` in `utils/gpu.py` for runtime memory detection.

### Fixed
- **OOM retry loops causing repeated failures**: OOM errors are now classified as permanent (non-transient) in `is_transient_error()`, preventing the 3x3=9 retry attempts that were causing the same episodes to fail repeatedly. OOM episodes are now immediately marked as `permanently_failed` instead of retrying at the episode level (chunk-level retries still occur with smaller chunks).

---

## [0.1.199] - 2026-01-28

### Fixed
- **Uptime persists across deploys**: The `server_start_time` in the shared status file was never overwritten on container restart because `set_server_start_time()` only wrote if no value existed. Now always overwrites, ensuring uptime resets on deploy.

---

## [0.1.198] - 2026-01-28

### Fixed
- **ProcessingQueue staleness causing permanent queue_busy**: When a worker is SIGKILL'd (OOM), StatusService correctly auto-clears stale jobs after 30 minutes, but ProcessingQueue (in-memory, per-worker) retained stale `_current_episode` state forever. Added timestamp tracking and staleness detection to ProcessingQueue, matching StatusService behavior. Also added cross-check with StatusService as truth source - if StatusService says no job is running but ProcessingQueue thinks one is, ProcessingQueue clears its state.

---

## [0.1.197] - 2026-01-27

### Fixed
- **Text pattern matching completely broken**: Fixed numpy/scipy sparse matrix boolean evaluation error (`not self._pattern_vectors`) that caused "The truth value of an array with more than one element is ambiguous" on every processed episode since patterns were loaded. Changed to `self._pattern_vectors is None`. This was blocking ALL text pattern matching.
- **Settings page uptime flicker**: Different gunicorn workers had different `_start_time` values because each imports `api.py` independently. Server start time is now stored in shared `processing_status.json` so all workers report consistent uptime.
- **Stale processing/queue state after worker SIGKILL**: Added staleness detection to `StatusService._read_status_file()`. Jobs running longer than 30 minutes are auto-cleared; queue entries older than 1 hour are removed. This prevents permanently stuck status after OOM kills.
- **Chapter duration inconsistency**: Added `_enforce_min_duration()` to chapters generator that enforces the 3-minute minimum across all chapter sources (description timestamps, ad gaps, AI topic splits). Previously only ad-gap chapters had minimum duration enforcement.

### Added
- **Content-based ad boundary extension**: New `extend_ad_boundaries_by_content()` in ad detection pipeline checks transcript segments immediately before/after each detected ad for sponsor names, URLs, and promotional language. Extends boundaries to capture the full ad when detection cuts off ~5 seconds early (common with DAI ads). Configurable via `config.py` constants.
- **Created date column on Patterns page**: Added sortable "Created" column to the patterns table and changed default sort to newest-first (`created_at DESC`).

---

## [0.1.196] - 2026-01-20

### Fixed
- **Text pattern matching ineffective due to contaminated patterns**: Patterns were being created from merged multi-ad spans (3-8K+ chars) that could never match the 1500-char TF-IDF window. Added validation to reject patterns with duration >180s or text >3500 chars.
- **Auto-learning creating patterns from merged ads**: Adjacent ads within 3 seconds were merged before pattern learning, contaminating patterns with multiple ads. Added higher confidence threshold (0.92) for ads >90 seconds to prevent learning from merged spans.
- **Database lock race condition on startup**: Multiple gunicorn workers initializing simultaneously caused "database is locked" errors. Added retry logic with exponential backoff (5 attempts, 0.5s-8s delays) to handle concurrent schema initialization.

### Added
- **Pattern health check API** (`/api/v1/patterns/health`): New endpoint to identify oversized/contaminated patterns with severity levels (warning >2500 chars, critical >3500 chars) and recommendations
- **Enhanced pattern matching debug logging**: Lower threshold (0.4) for debug logging with pattern length vs window length comparison to help diagnose why patterns fail to match

### Changed
- **Database migration cleans contaminated patterns**: One-time migration deletes patterns with text_template >3500 chars on startup, removing patterns that were polluting the database and could never match

---

## [0.1.195] - 2026-01-20

### Fixed
- **Pattern detail page missing podcast info**: Fixed join condition in `get_ad_patterns()` which was incorrectly comparing slug against cast numeric ID. Also updated `get_ad_pattern_by_id()` to include the same join so individual pattern lookups return `podcast_name` and `podcast_slug`
- **Auto-learned patterns missing episode ID**: `create_pattern_from_ad()` and `_learn_from_detections()` now accept and pass through `episode_id` so auto-learned patterns have `created_from_episode_id` populated

### Changed
- **Pattern detail modal shows podcast link**: Podcast-scoped patterns now show the podcast slug as a clickable link to the podcast's episode list
- **Renamed "Created from" to "Origin Episode"**: Clearer label in pattern detail modal

---

## [0.1.194] - 2026-01-20

### Fixed
- **Podcast-scoped text patterns not matching**: Fixed three related bugs preventing podcast-scoped patterns from working:
  1. `podcast_id` was never passed to `process_transcript()`, so pattern matching always received `None` and filtered out all podcast-scoped patterns
  2. Auto-created patterns stored numeric database IDs instead of slug strings
  3. Added database migration to convert existing numeric podcast_ids to slugs for consistency

---

## [0.1.193] - 2026-01-19

### Fixed
- **Auto pattern learning not working**: Claude-detected ads did not include a `sponsor` field, causing `_learn_from_detections()` to skip all Claude ads. Added `_extract_sponsor_from_reason()` helper that uses `SponsorService` to look up sponsor names from the `known_sponsors` database table (e.g., "ZipRecruiter host-read sponsor segment" -> "ziprecruiter") so patterns can be created automatically.

---

## [0.1.192] - 2026-01-18

### Fixed
- **Slider invisible in dark mode**: Changed slider track background from `bg-secondary` to `bg-muted` so the ad detection aggressiveness slider is visible in dark mode

---

## [0.1.191] - 2026-01-18

### Fixed
- **Off-by-one error in text pattern matching**: Fixed asymmetric boundary comparison in `_char_pos_to_time()` that caused incorrect timestamp mapping for pattern matches at segment boundaries
- **Timestamp calculation in phrase finding**: Fixed character-to-word index mapping in `find_phrase_in_words()` which was breaking at the wrong word and failing for last-word matches
- **Race condition in ad merging**: Extracted sponsor mismatch extension into separate `_extend_ads_for_sponsor_mismatch()` function to prevent mutation during iteration
- **Temp file leak in audio preprocessing**: Added `finally` block cleanup in `preprocess_audio()` to prevent orphaned temp files on error paths
- **Division by zero in ad validation**: Added MIN_DURATION_THRESHOLD constant (1ms) to protect against edge cases in duration calculations
- **Pattern scope filtering not working**: Fixed `_filter_patterns_by_scope()` to actually compare podcast_id and network_id instead of just checking scope string
- **TF-IDF vocabulary mismatch**: Added BASE_AD_VOCABULARY with common podcast ad terms to prevent sklearn vectorizer from ignoring unseen terms in new text

### Added
- **Shared utilities module** (`src/utils/`): Consolidated duplicate functions across codebase
  - `utils/audio.py`: `get_audio_duration()` with ffprobe stderr logging, `AudioMetadata` caching class
  - `utils/time.py`: `parse_timestamp()`, `format_time()`
  - `utils/text.py`: `extract_text_in_range()`, `extract_text_from_segments()`
  - `utils/gpu.py`: `clear_gpu_memory()`, `get_gpu_memory_info()`
- **Automatic pattern learning**: High-confidence Claude detections (>=85%) now automatically create podcast-scoped patterns via `_learn_from_detections()`
- **Pattern match recording**: Pattern matches are now recorded for promotion metrics via `record_pattern_match()`
- **Centralized configuration constants**: Added TFIDF_MATCH_THRESHOLD, FUZZY_MATCH_THRESHOLD, FINGERPRINT_MATCH_THRESHOLD, subprocess timeouts to config.py

### Changed
- **Increased sliding window size**: Pattern matching window increased from 500 to 1500 characters (~60 seconds of speech) with 500 character step for better coverage of longer ads
- **Consolidated duplicate code**: Removed 6 copies of `get_audio_duration()`, 6 copies of `parse_timestamp()`, 5 copies of transcript extraction, 3 copies of GPU cleanup

---

## [0.1.190] - 2026-01-18

### Fixed
- **Music analysis timeout on long episodes**: Episodes over 1.5 hours now use "fast mode" that analyzes every 3rd frame and skips expensive HPSS (Harmonic-Percussive Source Separation) computation. This prevents the 805s+ timeouts that were occurring on 2+ hour episodes like Security Now.
- **Non-English DAI ads not detected**: Changed Whisper from `language="en"` to `language=None` for auto-detection. Non-English segments (especially Spanish ads) are now automatically flagged and treated as ads.
- **VAD filter too aggressive**: Adjusted VAD parameters to be more sensitive (`min_silence_duration_ms`: 500->1000, `speech_pad_ms`: 400->600, `threshold`: 0.3). This helps capture music-heavy ad segments that were being skipped.
- **End-of-episode ads not fully trimmed**: Ads that end within 30 seconds of the episode end are now extended to the actual end, eliminating leftover ad snippets at the end.

### Added
- **Ad detection aggressiveness slider**: New setting to control how confident the system must be before removing an ad. Lower values (50%) are more aggressive and remove more potential ads, while higher values (95%) are more conservative. Accessible via Settings page slider.
- Foreign language detection in transcription pipeline with `is_foreign_language` and `detected_language` segment attributes
- `_detect_foreign_language_ads()` method in ad detector that auto-detects non-English segments as DAI ads with 95% confidence
- Fast mode music detection: `_compute_music_probability_fast()` using only spectral flatness and bass energy

### Changed
- Music detector streaming analysis now uses adaptive frame skipping based on episode length
- Block length increased from 256 to 512 for more efficient streaming processing

---

## [0.1.189] - 2026-01-11

### Fixed
- **Duplicate episodes in RSS feeds**: Same episode appearing multiple times due to CDN updates (e.g., `?updated=` params) are now de-duplicated. Episodes are matched by normalized title + published date, keeping only the latest version - matching podcast app behavior.

### Changed
- **Ad editor UX improvement**: Replaced absolute MM:SS timestamp inputs with simpler relative adjustment controls. Users can now adjust ad boundaries with +/- buttons and see "Start: +X sec" / "End: -Y sec" which is more intuitive since the audio they hear is the processed version with ads removed.

### Added
- `cleanup_duplicate_episodes()` database function for removing existing duplicates

---

## [0.1.188] - 2026-01-09

### Fixed
- **RSS 503 errors after episode processing**: RSS cache was being deleted after processing, but when upstream returned 304 Not Modified there was no content to regenerate. Now regenerates RSS immediately after processing completes, and forces full fetch when cache is missing as a fallback.
- **Auto-process timeout incorrectly marked as failure**: When processing takes longer than 10 minutes (common for 1-2 hour episodes), the auto-process queue was marking the episode as "failed" with no error message, even though processing was still running. Now correctly detects ongoing processing and re-queues for later status check instead of failing.

---

## [0.1.187] - 2026-01-07

### Fixed
- **Transcripts/chapters now appear immediately in podcast apps**: RSS cache is now invalidated after episode processing completes, ensuring Podcasting 2.0 tags are included right away instead of waiting for next feed refresh
- **Reduced failures for newly published episodes**: Added CDN availability check (HEAD request) before downloading audio. When CDN returns 4xx/5xx, the error is classified as transient and will be retried instead of failing immediately
- **Improved back-to-back ad detection**: When an ad's end_text contains a different sponsor's URL (e.g., ad for "Better Wild" ending with "mintmobile.com"), the system now looks for the next detected ad. If that ad's sponsor matches the end_text URL, the current ad is extended to meet it, eliminating gaps between consecutive ads from different sponsors

---

## [0.1.186] - 2026-01-03

### Fixed
- **Podcasting 2.0 chapters not appearing in podcast apps**: Fixed incorrect MIME type for chapters. The spec requires `application/json+chapters` but we were using `application/json`. Updated both the RSS tag type attribute and HTTP Content-Type header.

---

## [0.1.185] - 2026-01-03

### Fixed
- **Episode descriptions missing for auto-processed episodes**: Episodes processed via the auto-process queue were not receiving descriptions from the RSS feed. The queue system now stores and passes descriptions through to processing. Episodes that were only auto-processed will need to be reprocessed to get their descriptions populated.

---

## [0.1.184] - 2026-01-03

### Fixed
- **Ad validation false positive bug**: Fixed `NOT_AD_PATTERNS` regex that incorrectly rejected high-confidence ads (99%) when Claude's reason contained phrases like "unrelated to episode content". The regex now uses negative lookbehinds to exclude "unrelated to", "different from", and "not " prefixes which actually indicate something IS an ad.

---

## [0.1.183] - 2026-01-03

### Fixed
- **AI topic detection prompt**: Made Claude output format explicit with "OUTPUT FORMAT: Return ONLY topic lines, one per line. No introduction, no explanation, no numbering." and added examples. This prevents Claude from adding preamble like "Here are the 6 major topic changes:" that caused parsing to fail.

---

## [0.1.181] - 2026-01-03

### Fixed
- **VTT chapter regeneration**: Fixed critical bug where regenerating chapters from VTT caused double timestamp adjustment. VTT segments are already adjusted for removed ads, so the regenerate endpoint now uses a new `generate_chapters_from_vtt` method that works directly with VTT timestamps and uses AI topic detection without ad-based adjustment.

### Changed
- **UI improvement**: Moved "Regenerate Chapters" into the Reprocess dropdown menu for cleaner UI

---

## [0.1.179] - 2026-01-03

### Added
- **Regenerate chapters endpoint**: New API endpoint `POST /feeds/<slug>/episodes/<episode_id>/regenerate-chapters` to regenerate chapters without full reprocessing. Uses existing VTT transcript and ad markers.
- **UI button for regenerate chapters**: Added "Regenerate Chapters" button to episode detail page for episodes with VTT transcripts

---

## [0.1.178] - 2026-01-03

### Fixed
- **Chapter generation debugging**: Added detailed logging to see Claude's response when detecting topic boundaries. This will help diagnose why AI split is returning 0 topics.
- Improved regex pattern to handle various timestamp formats (MM:SS, MM:SS:, MM:SS -)

---

## [0.1.177] - 2026-01-03

### Fixed
- **Chapter generation bugfix**: Fixed `_reverse_adjust_timestamp` to correctly map adjusted times back to original transcript times when first ad starts at 0. This was preventing AI topic splitting from finding the correct transcript segment.
- Added debug logging to `split_long_segments` for troubleshooting

---

## [0.1.176] - 2026-01-03

### Added
- **Improved chapter generation**:
  - Fixed HTML description parsing for timestamp extraction (handles `<br>` tags properly)
  - Content-aware chapter detection: Long segments (>15 min) are automatically split using AI topic detection
  - Topic-based chapter detection: Descriptions with topic headers but no timestamps (like Windows Weekly show notes) are matched to transcript positions using AI

---

## [0.1.175] - 2026-01-03

### Fixed
- **Force refresh option for feeds**: Added `force` parameter to feed refresh API endpoints (`POST /api/v1/feeds/<slug>/refresh` and `POST /api/v1/feeds/refresh`) to bypass conditional GET (304 Not Modified) and regenerate RSS even when source feed hasn't changed. This is needed after code updates that change RSS format.

---

## [0.1.174] - 2026-01-03

### Fixed
- **End-of-episode ad handling**: When the last ad has less than 30 seconds of content remaining after it, the episode now ends with a beep instead of including the trailing content (which is often post-roll ad residue)

---

## [0.1.173] - 2026-01-03

### Added
- **Podcasting 2.0 Transcript and Chapters Support**
  - VTT transcripts with timestamps adjusted for removed ads
  - JSON chapters generated from ad boundaries and episode description timestamps
  - AI-generated chapter titles using Claude Haiku
  - New RSS namespace: `xmlns:podcast="https://podcastindex.org/namespace/1.0"`
  - `<podcast:transcript>` tag with VTT file URL, `rel="captions"`, and language attribute
  - `<podcast:chapters>` tag with chapters JSON URL
- New serving endpoints:
  - `GET /episodes/<slug>/<episode_id>.vtt` - VTT transcript
  - `GET /episodes/<slug>/<episode_id>/chapters.json` - Chapters JSON
- Settings UI toggles for VTT transcripts and chapters generation
- Episode detail shows VTT and Chapters badges when available
- Download links for VTT and chapters in episode detail page

### Fixed
- **Chapters startTime compatibility with podcast apps**
  - Changed from float values (738.8) to integers (739)
  - Changed minimum startTime from 0 to 1 (required by some apps like Pocket Casts)
  - Based on analysis of working No Agenda podcast feed format

### Changed
- VTT and chapters stored in database instead of filesystem
- RSS transcript tag now includes `rel="captions"` attribute
- Chapters MIME type changed to `application/json` (from non-standard type)

---

## [0.1.172] - 2026-01-01

### Added
- **Early Ad Snapping to 0:00**
  - Ads that start within 30 seconds of episode start are snapped to 0:00
  - Pre-roll ads often have brief intro audio before detection kicks in
  - New constant `EARLY_AD_SNAP_THRESHOLD = 30.0` in ad_detector.py
  - Logged when snapping occurs: "Snapped early ad to 0:00: X.Xs -> 0.0s"

- **Queue Position Tracking**
  - New `get_queue_position()` method in StatusService
  - Returns 1-based queue position for episodes waiting to be processed
  - Enables users to know when their episode will be processed

### Changed
- **Queue Busy Response**
  - Changed from HTTP 302 redirect to HTTP 503 Service Unavailable
  - Previously: Redirected to original (unprocessed) audio URL when queue busy
  - Now: Returns 503 with JSON body containing queue position and Retry-After header
  - Podcast players will now retry instead of caching the unprocessed file
  - Response includes: status, message, queuePosition, retryAfter (60 seconds)

---

## [0.1.171] - 2025-12-24

### Fixed
- **Search Index Pattern Column Bug**
  - Fixed incorrect column name (`text` -> `text_template`) for ad_patterns indexing

---

## [0.1.170] - 2025-12-24

### Fixed
- **Search Index Query Bug**
  - Fixed incorrect column name (`transcript` -> `transcript_text`) in search index rebuild
  - Fixed incorrect JOIN condition for episode_details table
  - Search now properly indexes episode transcripts

---

## [0.1.169] - 2025-12-24

### Fixed
- **Search Index Auto-Population**
  - FTS5 search index now auto-populates on startup if empty
  - Fixes search returning 0 results after fresh deployment or migration
  - Index is rebuilt automatically during database initialization

- **Mobile Transcript Editor Scroll Position**
  - Closing the transcript editor now restores the previous scroll position
  - Fixes the issue where page would jump to "Detected Ads" section header on mobile
  - Saves scroll position when opening editor, restores on close

---

## [0.1.168] - 2025-12-23

### Added
- **Pattern Creation from Boundary Adjustments**
  - Saving an adjustment now creates a pattern (like confirm does)
  - Uses the ADJUSTED boundaries to extract transcript text
  - Enables cross-episode pattern learning from corrected ad boundaries
  - If pattern already exists, increments confirmation count
  - Stores adjusted text in correction for future matching

---

## [0.1.167] - 2025-12-23

### Added
- **Cross-Episode False Positive Matching**
  - When marking a segment as "not an ad", the transcript text is now stored
  - Future detections across all episodes of the same podcast are compared against rejected segments
  - Uses TF-IDF similarity matching (threshold: 0.75) to skip similar content
  - Prevents the same show intro/outro from being repeatedly flagged as ads
  - New API endpoint: `POST /api/v1/patterns/backfill-false-positives` to populate text for existing corrections
  - New database method: `get_podcast_false_positive_texts()` for cross-episode lookup
  - Logs when detections are skipped due to cross-episode false positive match

---

## [0.1.166] - 2025-12-23

### Added
- **Independent Prompt Reset**
  - New "Reset Prompts Only" button in Settings page
  - Resets only system prompts without affecting models or toggles
  - New API endpoint: `POST /api/v1/settings/prompts/reset`

- **Pattern Statistics & Audit**
  - New pattern stats display on Patterns page header
  - Shows: total, active, by scope, unknown sponsor count, high false positive count
  - New API endpoint: `GET /api/v1/patterns/stats`
  - Tracks stale patterns (not matched in 30+ days)

### Fixed
- **Pattern Creation Without Sponsor**
  - No longer creates patterns when sponsor cannot be detected
  - Previously created patterns with NULL sponsor showing as "(Unknown)"
  - Added logging to confirm/reject correction handlers for debugging

---

## [0.1.165] - 2025-12-23

### Added
- **Per-Podcast Second Pass Toggle**
  - New `skipSecondPass` setting for podcasts that discuss products (tech shows, etc.)
  - Second pass detection was too aggressive for shows like Windows Weekly
  - Prevents false positives where product discussions are flagged as "subtle ads"
  - Toggle via API: `PATCH /api/v1/feeds/{slug}` with `{"skipSecondPass": true}`
  - Setting is logged during processing: "Second pass skipped (podcast setting)"

### Fixed
- **Ad Merge Bug for Overlapping Segments**
  - Fixed bug where overlapping/contained ads would shrink instead of extend
  - Example: Ad A (100-300s) + Ad B (150-200s) now correctly merges to 100-300s
  - Previously would incorrectly shrink to 100-200s, causing audio artifacts

---

## [0.1.164] - 2025-12-21

### Changed
- **Mobile Time Input UX Improvements**
  - Hide ad selector chips when editing time inputs to free up screen space
  - Hide audio player and action buttons when editing time inputs
  - Show Start and End fields side-by-side when editing (row layout)
  - Transcript segments now visible while editing, providing context
  - UI elements restore automatically when done editing (on blur)
  - Desktop layout unchanged (uses responsive breakpoints)

---

## [0.1.163] - 2025-12-21

### Fixed
- **iOS Safari Mobile Keyboard Fix**
  - Changed container height from `vh` to `dvh` (dynamic viewport height)
  - `dvh` automatically adjusts when iOS keyboard opens
  - Time input fields now remain visible and usable on iOS Safari
  - Supported on iOS Safari 15.4+, Chrome 108+, Firefox 101+

---

## [0.1.162] - 2025-12-21

### Fixed
- **Mobile Keyboard No Longer Resizes Viewport**
  - Added `interactive-widget=overlays-content` to viewport meta tag
  - Keyboard now overlays content instead of pushing UI elements off screen
  - Supported in Chrome 108+, Firefox 132+ (Safari falls back gracefully)
  - Removed previous workaround that hid transcript during time input editing

- **Desktop Boundary Controls Spacing**
  - Start/End time fields now centered with consistent spacing
  - Changed from `justify-between` to `justify-center` layout

---

## [0.1.161] - 2025-12-21

### Fixed
- **Transcript Editor Boundary Controls Visibility**
  - Fixed boundary controls (Start/End time inputs) not visible on desktop
  - Removed `landscape:hidden` class that hid controls when viewport is wider than tall
  - Time input fields now stay visible when mobile keyboard opens
  - Transcript list hides temporarily on mobile during time input editing
  - Ensures boundary controls remain accessible on both desktop and mobile

---

## [0.1.160] - 2025-12-21

### Fixed
- **Transcript Editor Mobile Keyboard Bug**
  - Fixed keyboard dismissing when typing in time input fields on mobile
  - Added refs and useEffect to restore focus after state change re-renders
  - Added `inputMode="decimal"` for numeric keypad on mobile
  - Reordered onFocus logic to set value before editing state for smoother UX

---

## [0.1.159] - 2025-12-21

### Added
- **Transcript Editor Manual Time Entry**
  - Start and end times now editable via direct text input
  - Supports MM:SS format (e.g., "1:30") or seconds only (e.g., "90")
  - Click to edit, Enter to confirm, Escape to cancel
  - Auto-select on focus for easy replacement

### Changed
- **Transcript Editor Mobile Improvements**
  - Increased mobile viewport height from 75vh to 85vh (more transcript visible)
  - Increased max-height from 600px to 750px
  - Reduced segment padding and min-height for tighter layout
  - Smaller font sizes on mobile: timestamps 10px, text xs
  - Boundary time display now uses smaller font on mobile

---

## [0.1.158] - 2025-12-21

### Added
- **Phase 6: Documentation and Code Quality**

- **Centralized Configuration**
  - New `src/config.py` with all magic numbers and thresholds
  - Consolidated constants from ad_validator.py, ad_detector.py, pattern_service.py
  - Includes confidence thresholds, duration limits, pattern matching settings

- **Documentation**
  - `frontend/README.md` - Frontend development guide with tech stack and patterns
  - `docs/DEPLOYMENT.md` - Deployment runbook with prerequisites and troubleshooting
  - Added Advanced Features quick reference table to main README.md
  - Updated UI screenshots (dark mode, desktop + mobile views)

- **OpenAPI Specification Updates**
  - Added authentication endpoints (GET /auth/status, POST /auth/login, POST /auth/logout, PUT /auth/password)
  - Enhanced patterns and corrections endpoint descriptions
  - Updated version to 0.1.158

### Fixed
- **Status Service Multi-Worker Consistency**
  - Fixed status endpoint returning inconsistent results with multiple Gunicorn workers
  - Processing status (current job, queue, feed refreshes) now stored in shared file
  - All workers read from same source for consistent /api/v1/status responses
  - File-based storage with proper locking for cross-process synchronization

- **CLAUDE.md Path Reference**
  - Fixed hardcoded `/Users/` path to generic reference

- **Transcript Editor Arrow Navigation**
  - Fixed arrow buttons losing highlighting after navigation
  - Memoized detectedAds and transcriptSegments to prevent stale closure issues
  - Navigation between ads now works consistently

---

## [0.1.157] - 2025-12-21

### Fixed
- **Authentication Session Persistence**
  - Fixed multi-worker SECRET_KEY issue causing random 401 errors
  - SECRET_KEY now persisted in database instead of random per-worker generation
  - All Gunicorn workers now share the same key for consistent session validation
  - Session cookies now work correctly across all workers

- **Auth Exemptions**
  - Added SSE stream (/status/stream) to auth exemptions to prevent reconnect loops
  - Added artwork endpoints to auth exemptions for img tag compatibility

---

## [0.1.156] - 2025-12-21

### Added
- **Phase 6: Missing Features Implementation**

- **OPML Import UI**
  - File drag-and-drop support on Add Feed page
  - Visual feedback for import progress
  - Import results display (success/failed counts)
  - Podcast Index search link moved to Add Feed page

- **Batch Reprocess Dropdown**
  - Dropdown menu with two reprocess modes:
    - Patterns + Claude (uses learned patterns)
    - Claude Only (fresh analysis)
  - Mode passed to backend, stored in episode for processing
  - Confirmation modal shows selected mode
  - Results modal shows mode used

- **Simple Password Authentication**
  - Optional single password protection for entire app
  - Flask session-based authentication with configurable expiry
  - Auth endpoints: /auth/status, /auth/login, /auth/logout, /auth/password
  - Before-request middleware checks auth on all API routes
  - Exempt paths: /health, /auth/*, RSS feeds, audio files
  - Login page with password input
  - Settings page security section for password management
  - Logout button when password is set
  - 401 redirect handling in API client

- **Full-Text Search with SQLite FTS5**
  - FTS5 virtual table for search indexing
  - Indexes: episodes (transcripts), podcasts, patterns, sponsors
  - Search endpoint: GET /api/v1/search?q=query&type=episode&limit=50
  - Index rebuild endpoint: POST /api/v1/search/rebuild
  - Index stats endpoint: GET /api/v1/search/stats
  - Search page with real-time search and debouncing
  - Filter tabs for content types (All, Episodes, Podcasts, Patterns, Sponsors)
  - Grouped results view with highlighted snippets
  - Nav search icon now links to global search (was Podcast Index)

### Changed
- Reprocess All button changed to dropdown with mode selection
- Search icon in navigation now opens global search

---

## [0.1.155] - 2025-12-21

### Added
- **Phase 5: Features and UI Improvements**

- **Pattern Promotion Improvements**
  - Lowered similarity threshold from 0.85 to 0.75 for more pattern matches
  - Added sponsor-based global promotion (3+ podcasts with same sponsor)
  - Added debug logging for pattern match candidates (score > 0.5)
  - Added info logging for successful pattern matches

- **SSE Reconnection Enhancement**
  - Exponential backoff for SSE reconnection (1s, 2s, 4s... max 30s)
  - Tracks reconnection attempts and resets on successful connection

- **URL Validation Feedback**
  - Real-time URL validation in Add Feed form
  - Validates protocol (http/https required), domain format
  - Warning for non-https URLs

- **OPML Import**
  - POST /api/v1/feeds/import-opml endpoint for batch feed import
  - Accepts OPML file upload, parses RSS/Atom feeds
  - Returns imported/skipped/failed counts
  - Import modal in Dashboard with file upload

- **Batch Reprocess Endpoint**
  - POST /api/v1/feeds/{slug}/reprocess-all endpoint
  - Queues all processed episodes for reprocessing
  - "Reprocess All" button in Feed Detail with confirmation modal

- **Audio Output Quality Setting**
  - Configurable audio bitrate setting (64k, 96k, 128k, 192k, 256k)
  - Added audio_bitrate setting to database
  - AudioProcessor accepts bitrate parameter
  - Settings page dropdown for quality selection

---

## [0.1.154] - 2025-12-21

### Added
- **Phase 4: Testing Infrastructure**

- **pytest Test Framework**
  - Added pytest and pytest-cov dependencies
  - Created pytest.ini configuration with test discovery settings
  - Suppresses deprecation warnings for cleaner output

- **Shared Test Fixtures (tests/conftest.py)**
  - temp_db: Creates isolated temporary database for each test
  - sample_transcript: Sample transcript with ad segments
  - sample_ads: Sample ad markers for validation testing
  - mock_podcast/mock_episode: Database fixtures for testing
  - app_client: Flask test client for API tests
  - Proper singleton reset handling for Database class

- **Unit Tests for AdValidator (10 tests)**
  - Duration validation (too short, too long, sponsor-confirmed limits)
  - Confidence thresholds (accept/review/reject)
  - Ad merging for small gaps
  - Position-based confidence boosts (pre-roll, post-roll)
  - Boundary clamping (negative start, past-end)
  - False positive overlap handling
  - Reason quality checks

- **Unit Tests for Ad Detection Functions (8 tests)**
  - extract_sponsor_names: From text, URLs, and ad_reason
  - merge_and_deduplicate: Overlapping and adjacent ads
  - refine_ad_boundaries: Transition phrase detection
  - merge_same_sponsor_ads: Same-sponsor merging logic

- **Unit Tests for Database Operations (6 tests)**
  - Podcast CRUD (create, read, update, delete with cascade)
  - Episode upsert (create and update)
  - Ad pattern creation
  - Settings operations
  - Singleton pattern reset testing

- **Integration Tests for API Endpoints (5 tests)**
  - Health endpoint (/api/v1/health)
  - Feeds endpoints (list, validation)
  - Settings endpoint
  - Patterns endpoint
  - System status endpoints

---

## [0.1.153] - 2025-12-21

### Added
- **Phase 3: Performance Optimization**

- **TTL Cache for Feed Map**
  - Thread-safe TTLCache class with configurable expiration
  - Feed map cached for 30 seconds to reduce database queries
  - Automatic cache invalidation on feed create/update/delete

- **Gzip Response Compression**
  - Added flask-compress for automatic response compression
  - Compresses JSON, XML, RSS, and text responses over 500 bytes
  - Compression level 6 for balance between speed and size

- **Database Performance Indexes**
  - Compound index on episodes(podcast_id, status) for filtered queries
  - Index on episodes(published_at DESC) for sorting
  - Indexes on pattern_corrections for episode and type lookups
  - Index on ad_patterns(podcast_id) for podcast-scoped queries

- **In-Memory RSS Cache**
  - Parsed feed cache with 60-second TTL
  - Reduces redundant RSS fetching and parsing

- **RSS Conditional GET (ETag/Last-Modified)**
  - Added etag and last_modified_header columns to podcasts table
  - Uses If-None-Match and If-Modified-Since headers
  - Skips full refresh when feed returns 304 Not Modified
  - Reduces bandwidth and server load for unchanged feeds

- **Audio Download Resume**
  - New download_audio_with_resume() method with HTTP Range support
  - Consistent temp file path based on URL hash for resume tracking
  - Keeps partial files on failure for resume on next attempt
  - Graceful fallback when server doesn't support Range requests

---

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
