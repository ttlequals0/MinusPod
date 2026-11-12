# API & Webhooks

[< Docs index](README.md) | [Project README](../README.md)

---

## Contents

- [API](#api)
- [Notifications](#notifications)
- [Webhooks](#webhooks)
- [Email notifications](#email-notifications)

## API

With `MINUSPOD_URL_PREFIX` set, root-relative URLs in API responses (`processedUrl`, artwork, transcript and chapter URLs) carry the prefix, so a client can fetch them as returned.

REST API available at `/api/v1/`. Interactive docs at `/api/v1/docs`. Full specification: [`openapi.yaml`](../openapi.yaml). The integration suite validates live responses for the common read and update routes against this document, so a response that drifts from the schema fails CI.

A path under `/api/` that no route matches answers 404 with `{"error": "Not found", "status": 404}`, and a method the path does not accept answers 405 with `{"error": "Method not allowed", "status": 405}` and an `Allow` header. Paths outside `/api/` keep their normal pages.

Authenticated write requests (`POST`, `PUT`, `PATCH`, `DELETE`) require an `X-CSRF-Token` header matching the `minuspod_csrf` cookie. The built-in UI sends it. An external client must echo the cookie on each write. `PUT /auth/password` is exempt: initial remote setup uses `X-MinusPod-Setup-Token`, while a later change requires `currentPassword`. Logout can clear stale unauthenticated state, but an authenticated logout still requires CSRF.

`POST /api/v1/auth/login` accepts an optional boolean `rememberMe` (default false; any other type is a 400). A normal session lasts `sessionLifetimeHours` from its last use and a remembered one lasts `sessionRememberDays`. Read both with `GET /api/v1/settings` and save them with `PUT /api/v1/settings/ad-detection`, which takes whole numbers only and resets a value to its default when you send `null`. A 429 from the login route, whether from the rate limit or the failed-login lockout, carries `Retry-After` in whole seconds and the same number as `retryAfterSeconds` in the body.

Key endpoints:
- `GET /api/v1/health` - Readiness check (database, storage); returns 503 if either is down. `checks.llm_model_configured` is always true while the LLM provider is `none`
- `GET /api/v1/health/live` - Liveness probe (process up); always 200, safe for frequent polling
- `GET /api/v1/feeds` - List feeds. With no `page`/`limit`, returns every feed. Passing `page`/`limit` paginates the list; `includeLatestEpisodes=true` (with optional `episodesPerFeed`, default 3, max 20) adds each feed's newest episodes without a separate request per feed
- `POST /api/v1/feeds` - Add a new feed (supports `maxEpisodes` for RSS cap, `onlyExposeProcessedEpisodes` to hide unprocessed episodes from the served feed, `retentionDaysOverride` for a per-feed retention window or archive, `keepOriginalAudioOverride` for the pre-cut original audio, `processingMode` to pick standard, keep_content, skip_detection or passthrough before the first refresh (cue_only must be set after creation)). Leading and trailing whitespace is stripped from `sourceUrl`; one still holding whitespace or a control character is rejected with 400, here and on `PATCH /api/v1/feeds/{slug}`
- `PATCH /api/v1/feeds/{slug}` - Update feed settings, including title and description rules, duration limits, and the `titleOverride` and `descriptionOverride` display overrides (null clears either). The OpenAPI spec lists all supported fields. `reviewLinksInNotes` (`on`, `off` or `null`) overrides the global review links setting for one feed. `queueOrder` (`queued`, `oldest_first`, `newest_first` or `null`) sets the order of the feed's queued episodes within one priority; `null` follows the global `queueOrder` setting saved with `PUT /api/v1/settings/ad-detection`.
- `POST /api/v1/feeds/import-opml` - Import feeds from OPML file. Leading and trailing whitespace is stripped from each feed URL; one still holding whitespace or a control character is listed under `failed`
- `GET /api/v1/feeds/export-opml?mode=original|modified` - Export feeds as OPML (original or ad-free URLs). Optional `slugs=a,b` filters the export
- `POST /api/v1/feeds/export-opml?mode=original|modified` - Export selected feeds; send `{"slugs":["a","b"]}` in the JSON body
- `POST /api/v1/feeds/refresh-artwork` - Re-render every feed's cover art (used after toggling the cover-art badge or swapping the badge asset)
- `POST /api/v1/feeds/{slug}/refresh` - Refresh one subscribed feed. Success includes a structured `outcome` with status, new and queued episode counts, plus refresh timestamps; source fetch or parse failures return 502.
- `POST /api/v1/feeds/refresh` - Refresh every subscribed feed. Returns per-feed outcomes and totals; HTTP 207 means the pass completed with at least one feed failure.
- `GET/HEAD /api/v1/feeds/{slug}/artwork` - Serve cached feed artwork without admin authentication. Sends an `ETag` and `Cache-Control: no-cache`, and answers a matching `If-None-Match` with 304. The feed object's `artworkUrl` carries a `v` query value that changes when the stored artwork changes. Traversal-like or over-200-character legacy slugs are rejected before this public exemption.
- `POST /api/v1/feeds` with `feedType: "youtube"` - Create a YouTube feed. Send `sourceKind` (`playlist` or `channel`), `sourceId` and `title`; `artworkUrl`, `includeShorts` and `maxEpisodes` are optional. Needs YouTube feeds enabled. The first listing runs before the response
- `GET/PUT /api/v1/settings/youtube` - Read or update the global YouTube settings, yt-dlp status, cookie status and setting bounds. The Data API key is write-only (`apiKeyConfigured` in responses). A generated SponsorBlock ID cannot be cleared or replaced: `PUT` with `sbUserId` returns 409 `error: "generated"` while one is stored, and `sbUserIdGenerated` reports it
- `GET /api/v1/settings/youtube/sb-user-id` - Reveal the stored SponsorBlock private user ID as `sbUserId`, or 404 `not_configured` (10 per minute)
- `POST /api/v1/settings/youtube/sb-user-id/generate` - Create and store a SponsorBlock private user ID for people without the browser extension. Returns 409 with `error: "configured"` whenever an ID is stored, and 409 with `error: "provider_crypto_unavailable"` when secret encryption is unavailable (10 per minute)
- `POST /api/v1/youtube/preview` - Resolve a pasted playlist, channel or handle URL to a source plus RSS directory matches. Returns `enabled: false` with no lookup when the feature is off
- `POST /api/v1/youtube/test-key` - Check a Data API key, or the stored one when `apiKey` is omitted
- `POST /api/v1/youtube/cookies`, `DELETE /api/v1/youtube/cookies` - Save or remove the Netscape cookies file used by yt-dlp
- `POST /api/v1/youtube/ytdlp/update` - Update the data-volume yt-dlp now (6 per hour)
- Episode responses on YouTube feeds include `sponsorBlock` (`state`, `attempts`, `nextCheckAt`, `segmentCount`, `note`, `lastError`); it is `null` elsewhere
- `GET /api/v1/podcast-search?q=query` - Search podcasts through the configured directory (Apple iTunes by default, or Podcast Index)
- `GET /api/v1/feeds/{slug}/episodes` - List episodes with sorting, status, pagination, and literal case-insensitive title `search`. Search applies across the full feed before pagination. `jobState` reports live queue state. `status=filtered` lists the episodes the feed's title, description, and duration filters match; `total` counts the episodes matched, and it combines with `search`, sorting, pagination, and `selection=true`.
- `GET /api/v1/feeds/{slug}/episodes?selection=true` - Return matching episode IDs and action eligibility without loading full episode rows, capped at 501 rows (`truncated` is `true` past that cap). The `selection` array contains `id`, `status`, `jobState`, `titleSkipped`, `descriptionSkipped`, and `durationSkipped`; `episodes` is empty. Pagination is ignored for this projection; bulk actions still accept at most 500 episode IDs.
- `POST /api/v1/feeds/{slug}/episodes/bulk` - Bulk episode actions (process, reprocess, reprocess_full, reprocess_llm, delete)
- `GET /api/v1/feeds/{slug}/episodes/{id}` - Get episode detail with ad markers (a marker whose pattern still exists also carries `patternAudioState`: `active`, `off` or `none`, the state of that pattern's audio fingerprint), transcript, live `jobState`, and processing-run statistics. New runs include elapsed totals, stage timings, and aggregate FFmpeg time; older runs can have no timing data. The elapsed total ends after the episode and feed are saved; history recording and notifications follow outside it. `transcriptSource` is `whisper`, `upstream` (imported from the publisher feed on a pass-through episode) or null when there is no transcript. `upstreamTranscript` reports the publisher-transcript diff (status, coverage, spans) when the stage ran; an ad marker it corroborates carries `corroborated_by: "transcript_differential"`. See [Upstream Transcript Differential](transcript-differential.md) and `openapi.yaml`
- `GET /api/v1/feeds/{slug}/episodes/{id}/peaks?start=&end=&resolution_ms=` - Waveform peaks for a window of the retained original, as `{"episodeId", "start", "end", "resolutionMs", "peaks"}` with each peak between 0 and 1. The server keeps a small peaks file beside the original and serves from it when `resolution_ms` is a multiple of 10 and `start` is on the 10 ms grid. Other requests are decoded from the audio. The first open of an episode builds the file. For an original longer than 4 hours the build runs in the background, and the route answers 503 with `Retry-After: 5` until it is ready. A shorter episode also gets that 503 when another request has been building its file for more than 30 seconds. A response is capped at 60000 peaks, so a long window gets a wider bucket rounded up to a multiple of 10 ms. `resolutionMs` reports the width used
- `GET /api/v1/feeds/{slug}/episodes/{id}/artwork` - Serve an episode's cover, fetching and caching it from the publisher on first request. Sends an `ETag` and `Cache-Control: no-cache`. Publishers block images requested with a cross-site Referer, so the web UI asks here instead of loading them directly. Redirects to the feed cover when the episode has none or the fetch is refused. The URL comes from the episode record, never from the caller
- `POST /api/v1/episodes/{slug}/{id}/reprocess` - Reprocess an episode (body `mode`: reprocess/full/llm/recut; `llm` re-detects on the existing transcript and `recut` re-cuts from the saved ad list, both skipping transcription). See [Reprocessing](configuration.md#reprocessing) for the full mode reference. The older `POST /api/v1/feeds/{slug}/episodes/{id}/reprocess` ignores `mode` and always runs a full reprocess.
- `POST /api/v1/feeds/{slug}/episodes/{id}/cancel` - Cancel processing for a stuck episode
- `POST /api/v1/feeds/{slug}/episodes/{id}/approve` - Publish an episode waiting for manual approval. Returns `{"approved": true, "episode": {...}}`, or 409 when the episode is not waiting or does not exist. Episode payloads carry `awaitingApproval`, `approvalPendingAt` and `approvedAt`; feed payloads carry `awaitingApprovalCount` and `requireManualApproval`
- `GET /api/v1/episodes/awaiting-approval?slug=&limit=&offset=` - Episodes waiting for manual approval across feeds, oldest first: `{"count": n, "episodes": [{"slug", "episodeId", "title", "podcast", "pendingSince", "adCount", "pendingReviewCount"}]}`
- `POST /api/v1/episodes/awaiting-approval/approve` - Approve `{"episodes": [{"slug": "...", "episodeId": "..."}]}` or every waiting episode of `{"slug": "..."}`. Returns `{"approved": n, "skipped": n, "remaining": n}` (a `slug` request approves at most 500 oldest first; `remaining` is the rest), rebuilds each feed once (10 per minute)
- `GET /api/v1/episodes/held?limit=&offset=` - Episodes with detections held for review across feeds, same row shape as awaiting-approval without `pendingSince`
- `POST /api/v1/feeds/{slug}/episodes/{id}/regenerate-chapters` - Regenerate chapter markers and rewrite the ID3 chapters embedded in the MP3. Returns 202 and runs in the background; the episode's `chaptersRegenerating` and `chaptersRegenError` fields report the run. Returns 409 with `code: llm_disabled` while the LLM provider is `none`
- `PUT /api/v1/feeds/{slug}/episodes/{id}/rating` - Rate an episode 1 to 5 with optional notes below 5. A 5 records a confirm correction for every cut ad marker and dismisses held ads without pattern learning; the response reports confirmedMarkers and dismissedMarkers. `DELETE` clears the rating
- `GET /api/v1/feeds/{slug}/episodes/{id}/sponsorblock/candidates` - Segments a person may submit to SponsorBlock for a YouTube episode, with the evidence behind each, or the reason none are allowed
- `POST /api/v1/feeds/{slug}/episodes/{id}/sponsorblock/submit` - Send the reviewed segments to SponsorBlock under the stored private user ID and record the outcome
- `POST /api/v1/feeds/{slug}/reprocess-all` - Batch reprocess all episodes
- `GET /api/v1/feeds/{slug}/ad-distribution` - Histogram of where ads have historically been cut across the feed's episodes, with learned prior zones. Informational; powers the feed detail Ad Distribution panel and is independent of the learned-positions experiment toggle.
- `POST /api/v1/feeds/{slug}/episodes/{id}/retry-ad-detection` - Retry ad detection only. With `llmProvider` set to `none` it reruns the model-free detectors (audio fingerprints and text patterns), so a newly added pattern is applied to an episode already processed
- `POST /api/v1/feeds/{slug}/episodes/{id}/corrections` - Submit ad corrections. A `create`, `confirm` or `adjust` correction also stores an audio fingerprint from the retained original, whatever the LLM provider. Each pattern is fingerprinted over its own span only, and a pattern whose span was split from a longer one and cannot be located gets none. `create` and `confirm` never replace an existing fingerprint or a deleted-audio marker, and `adjust` rebuilds from the adjusted span even after a delete. The response carries `fingerprintStored` and, when it is false, `fingerprintSkippedReason`. Both are absent when the request created or linked no pattern, for example when no sponsor could be resolved. In that case `pattern_id` is null. If reasons differ between patterns, the first of `audio_removed`, `fingerprint_exists`, `span_covers_multiple_patterns`, `original_not_retained`, `segment_too_short`, `not_distinctive` and `fingerprint_failed` is reported. A `reject` marks the matching fingerprint match as rejected, and a later `confirm` or `adjust` that removes that rejection restores the match. Storing a fingerprint is best effort and never fails the correction.
- `GET /api/v1/patterns/corrections/unresolved` - List legacy corrections with no feed assignment and the evidence for safe candidates
- `POST /api/v1/patterns/corrections/{id}/assign` - Assign one unresolved correction after `confirm: true`
- `DELETE /api/v1/patterns/corrections/{id}` - Delete one unresolved correction without changing patterns, history, media, or feeds
- `POST /api/v1/patterns/corrections/unresolved/bulk` - Assign or delete 1 to 500 unique correction IDs in one transaction. Both actions need `confirm: true`; assignment also needs one feed proven for every row. One stale or invalid row rejects the batch.
- `GET/POST /api/v1/feeds/{slug}/cue-templates` - List a feed's audio-cue templates, or mark a new one from a window of an episode's original audio (`episodeId`, `startS`, `endS`, `cueType`). See [Audio Cue Detection](audio-cues.md#marking-a-cue)
- `PATCH/DELETE /api/v1/cue-templates/{id}` - Enable/disable, change scope (`podcast` or `network`), set `scoreThreshold`, move the capture window (`sourceOffsetS`/`durationS`; 409 when the original has aged out), or delete a template. See [Managing cues](audio-cues.md#managing-cues)
- `GET /api/v1/cue-templates/{id}/export` - Download a template as a portable zip (lossless WAV plus JSON manifest)
- `POST /api/v1/feeds/{slug}/cue-templates/import` - Import a template zip into a feed (multipart `file`); the MFCC is recomputed from the WAV, sample-rate or channel mismatches are rejected. The manifest's `schemaVersion` field is reserved for a future format change and is only checked for parsing.
- `GET /api/v1/feeds/{slug}/episodes/{id}/cue-loud-spots` - Template-free energy pass over an episode's original audio; returns candidate "loud spots" the capture UI marks as jump points
- `GET /api/v1/feeds/{slug}/episodes/{id}/cue-candidates` - Find-audio-cues scan: recurring in-episode stings (speech-like ones dropped) plus intros and outros shared across the feed (powers the Find audio cues button)
- `POST /api/v1/feeds/{slug}/episodes/{id}/cue-candidates/dismiss` - Dismiss a candidate sound feed-wide (`start_s`, `end_s`, optional `label`; spans over 120 seconds are rejected). Stores the span's fingerprint from the retained original; future candidate scans suppress matching sounds
- `GET /api/v1/feeds/{slug}/cue-candidate-dismissals` - List the feed's dismissed sounds, newest first
- `DELETE /api/v1/cue-candidate-dismissals/{id}` - Undo a dismissal; the sound becomes suggestible again
- `POST /api/v1/feeds/{slug}/episodes/{id}/cue-scan` - Diagnostic: run every enabled template against an episode and return per-template peak scores and match times (optional `scoreThreshold` override)
- `POST /api/v1/feeds/{slug}/episodes/{id}/cue-template-preview` - Run a single template (`templateId`) against an episode
- `POST /api/v1/feeds/{slug}/cue-cross-episode-scan` - Full-body cross-episode scan for recurring segments (`episodeIds`, 2-5). See [Finding cues across episodes](audio-cues.md#finding-cues-across-episodes)
- `POST /api/v1/feeds/{slug}/cue-templates/{id}/optimize-window` - Find the capture window with the best mean match score. See [Optimizing the cue window](audio-cues.md#optimizing-the-cue-window)
- `GET /api/v1/detections` - List ad detections across all feeds with status filter (`needs_review`, `pending`, `rejected`, `accepted`, `all`; default `needs_review`), optional podcast slug (`feed`), free-text search (`q`), sort (`date`, `confidence`, `podcast`), order (`asc`, `desc`), and pagination (`page`, `limit` 1-100, default 20). Each detection with a `patternId` also carries `patternAudioState` (`active`, `off` or `none`). Powers the Patterns > Ad Review tab.
- `GET /api/v1/patterns` - List ad patterns (filter by scope). Each pattern carries an `audio` summary with `state` (`active`, `off` or `none`), `available`, `matchCount` and `meanSimilarity`. The summary comes from one database query and checks no files, so `available` means a source episode still has its original recorded. `GET /api/v1/patterns/{id}/audio` does the file check. `GET /api/v1/patterns/{id}` carries the same `audio` summary
- `PUT /api/v1/patterns/{id}` - Update a pattern's fields, including `scope`. Setting `scope` to `network` also sets the pattern's network. It is the optional `networkId` in the body (trimmed, at most 100 characters). Without it, a pattern that is already network scope keeps its network, and any other pattern takes the effective network of its feed (the network override when set, else the detected network). A pattern with no feed network and no `networkId` is a 400. The global and podcast scopes leave the network as it is
- `GET /api/v1/patterns/stats` - Pattern database statistics
- `GET /api/v1/patterns/{id}/audio` - Audio fingerprint detail of a pattern: `state` (`active`, `off` or `none`), `available`, `matchable` (false when the stored fingerprint can no longer match under the current minimum coverage setting, true when there is no fingerprint), `length`, `createdAt`, `builtFrom` (the episode span it was built from, with `podcastSlug`, `episodeId`, `start`, `end` and whether that original is still retained), `source` (the span preview plays), `addSource` (only for a pattern with no fingerprint: the span of its own marker in its origin episode that a fingerprint could be built from, as `podcastSlug`, `episodeId`, `start`, `end` and `available`, using the pre-trim span of a reviewer-adjusted marker and the earliest marker when there are several; null when there is no origin episode, no such marker, or the pattern has no podcast), `counts` (`matches`, `rejected`, `outOfScope`), `meanSimilarity` and `lastMatchedAt`. Matches marked "not this ad" are left out of `matches`, `meanSimilarity` and `lastMatchedAt` and counted in `rejected`
- `PUT /api/v1/patterns/{id}/audio` - Turn audio matching on or off with `{"enabled": true}`. Text matching is not affected. A non-boolean `enabled` is a 400, and a pattern with no fingerprint is a 404 with `code: no_audio`
- `DELETE /api/v1/patterns/{id}/audio` - Delete the fingerprint and its match history and keep the pattern. Automatic detection does not rebuild the audio afterwards. Answers `{"deleted": true}`, or 404 with `code: no_audio`
- `POST /api/v1/patterns/{id}/audio/rebuild` - Rebuild the fingerprint from `{"podcastSlug", "episodeId", "start", "end"}`, all required. A bad span or body is a 400, and so is a span longer than 600 seconds. An unknown pattern or episode is a 404. A span that runs past the end of the file is cut to the audio that was decoded, and `length` and `builtFrom.end` report the cut span. The pattern's match history is kept. A span that cannot be stored answers 200 with `fingerprintStored: false` and `fingerprintSkippedReason` (`original_not_retained`, `segment_too_short`, `not_distinctive` or `fingerprint_failed`), the same two fields the corrections route returns. The response is the audio detail object with those two fields added at the same level
- `GET /api/v1/patterns/{id}/audio/matches` - Match history of a pattern's audio, newest first, paged with `page` and `perPage` (default 10, at most 50). A page past the end is lowered to one page after the last, so the response `page` can differ from the one asked for. Each row has `id`, `episodeId`, `podcastSlug`, `episodeTitle`, `start`, `end`, `similarity`, `coverage`, `storedLength`, `inScope`, `audioOnly`, `rejected`, `available` (the episode's original is still retained) and `createdAt`. The response also carries `page`, `perPage` and `total`
- `POST /api/v1/patterns/deduplicate` - Merge near-duplicate patterns. A fingerprint and its match history move with the pattern that is kept, and deduplication never puts audio onto a pattern whose audio was deleted
- `GET /api/v1/patterns/{id}/audio/preview` - Play the source span of a pattern's audio as mp3 (Range requests work). Optional `matchId` plays that match instead of the newest retained one. The slice is capped at 600 seconds. A rejected match is never the default source, but its `matchId` plays it from the history list. A `matchId` that is not digits or has more than 18 digits is a 400, and an unsatisfiable `Range` is a 416. At most two slices are encoded at once per process, so a third request answers 503 with `Retry-After: 2`. A slice just encoded is kept for 120 seconds, so Range requests and seeks do not encode it again. Expired slices are dropped on every read and insert, and the cache holds at most 4 slices and 64 MiB per process, evicting the oldest first. A slice larger than 64 MiB is served but not kept. The response is sent with `Cache-Control: no-store`. A pattern with no fingerprint answers 404 with `code: no_audio`, and one whose source episodes no longer keep their originals answers 404 with `code: original_not_retained`
- `GET /api/v1/patterns/cleanup` - Pattern cleanup settings plus live state and pending suggestion counts by kind (Experiments). See [Pattern Cleanup](pattern-cleanup.md#api)
- `PUT /api/v1/settings/pattern-cleanup` - Update pattern cleanup settings (`enabled`, `cron`, `batchSize`, `unusedDays`, `provider`, `model`)
- `POST /api/v1/patterns/cleanup/run` - Start a cleanup batch; `{"force": true}` forces a recheck of all active learned patterns. Returns 202 with `{"runId"}`, 400 for invalid input, or 409 if already running or, with `code: llm_disabled`, while the LLM provider is `none`; rate limited to 6/hour
- `GET /api/v1/patterns/cleanup/runs` - Recent pattern cleanup runs, newest first
- `GET /api/v1/patterns/cleanup/suggestions` - List suggestions by `status` and `kind`, paginated with `limit` and `before_id`
- `POST /api/v1/patterns/cleanup/suggestions/{id}/approve` - Approve one suggestion, applying it to the pattern in place
- `POST /api/v1/patterns/cleanup/suggestions/{id}/reject` - Reject one suggestion
- `POST /api/v1/patterns/cleanup/suggestions/{id}/undo` - Undo an approval; 409 on a conflicting later edit
- `POST /api/v1/patterns/cleanup/suggestions/bulk` - Approve or reject several suggestion ids at once
- `GET /api/v1/sponsors` - List/create/update/delete sponsors (full CRUD)
- `GET /api/v1/search?q=query` - Full-text search across all content, grouped into shows, episodes, transcripts, patterns and sponsors. Optional `groups` (comma-separated subset of those five, default all) limits which are computed; an unrequested group is returned empty rather than omitted. Names are case-sensitive, empty tokens from a stray comma are ignored, and duplicates are tolerated. A query shorter than two characters returns every group empty. A request that sends the removed `type` parameter gets a 400
- `GET /api/v1/episodes/processing` - List episodes currently processing
- `GET /api/v1/history` - Processing history with pagination and export
- `GET /api/v1/history/downloads` - Episode download history with filters, sorting and pagination; see [Download History](download-history.md)
- `GET /api/v1/stats/reviewer` and `GET /api/v1/stats/addressing` - Reviewer and addressing statistics, each with a `byMode` block per addressing mode
- `GET /api/v1/stats/dashboard` - Aggregate stats (avg/min/max time saved, ads, cost, tokens) with optional podcast filter
- `GET /api/v1/stats/by-day` - Episodes processed by day of week
- `GET /api/v1/stats/by-podcast` - Per-podcast stats (ads, time saved, tokens, cost)
- `GET /api/v1/stats/audio-matching?from=&to=&podcastSlug=` - Audio fingerprint matching counts as `{patternsWithAudio, patternsTotal, audioMatches, foundByAudioOnly, notThisAd}`. `audioMatches` and `foundByAudioOnly` count match history rows created in the period that are not marked "not this ad", and `notThisAd` counts rows marked that way whose rejection time is in the period. They are the same rows the per-pattern audio history lists, so these totals agree with the pattern counts. `patternsTotal` counts active patterns and `patternsWithAudio` those with an enabled fingerprint. With `podcastSlug`, rows are limited to that feed and the pattern counts cover the patterns whose scope applies to that feed, by the rule the matchers use. An unknown `podcastSlug` answers 200 with zeros. `from` is the start of a UTC day and `to` includes the whole UTC day it names, as on the other ledger Stats routes. A value that is not a date or ISO 8601 timestamp is a 400
- `GET /api/v1/status` - Current processing status with an ETag; the browser polls every 2 seconds and backs off to 30 seconds after failures
- `GET /api/v1/status/stream` - Authenticated SSE compatibility endpoint, limited to 30 seconds and bounded worker slots
- `GET /api/v1/system/updates` - Latest stable and edge release info from GitHub Releases, cached 6 hours in-process (`?refresh=true` forces a live fetch, throttled to once per 30 seconds); returns 502 if GitHub is unreachable
- `GET /api/v1/system/token-usage` - LLM token usage and cost breakdown by model
- `GET /api/v1/system/model-pricing` - All known LLM model pricing rates
- `POST /api/v1/system/model-pricing/refresh` - Force refresh pricing from provider source; returns 409 `llm_disabled` while the LLM provider is none (the background refresh also makes no request then)
- `GET /api/v1/system/queue` - Auto-process queue status
- `POST /api/v1/system/vacuum` - Trigger SQLite VACUUM to reclaim disk space
- `GET /api/v1/changelog` - Released changelog sections, newest first (`limit` 1 to 50, default 10, larger values clamp); optional `since` X.Y.Z keeps only newer sections; 400 on a malformed `since` or `limit` below 1. Drives the What's new dialog
- `GET /api/v1/system/status` - Active Podping node, per-node HTTP status, and manual check progress. Includes worker-scoped SQLite WAL, checkpoint, transaction, and busy diagnostics
- `POST /api/v1/system/podping/check` - Check every Podping RPC node without changing the listener's active node; the request is deduplicated while a check is running
- `POST /api/v1/system/database/checkpoint` - Run a WAL checkpoint; the optional JSON body `{"mode": "passive" | "truncate"}` defaults to passive, and truncate also shrinks the WAL file. Returns 400 for any other mode, 409 when active readers prevent completion, and 503 when SQLite fails to run it
- `GET /api/v1/system/diagnostic-export` - Download bounded operational diagnostics for an optional UTC `start`/`end` range (24 hours at most). Each event has time, level, category, source file and line; warnings and errors also carry the static `message` template as written in the source and the `exception` class name. Logged values, exception text, prompts, transcripts, identifiers, secrets and URLs are never included. Rate limited to 6/hour
- `GET /api/v1/system/backup` - Download an encrypted SQLite backup by default. Returns 409 when encryption is unavailable; `encrypted=false` explicitly requests plaintext. See the [security guide](security-and-storage.md#database-backup-sensitivity)
- `POST /api/v1/system/db-backup/run` - Run a scheduled-style backup now, writing a plain SQLite snapshot to the configured destination (rate-limited to 6/hour; 409 if one is already running)
- `GET/PUT /api/v1/settings/db-backup` - Get or update scheduled backup settings (`enabled`, `cron`, `dest`, `keepCount`)
- `GET/PUT /api/v1/settings/provider-budget` - Read or update durable provider admission settings and current reserved/spent amounts
- `GET /api/v1/settings/provider-budget/currencies` - List supported ISO display currencies
- `GET /api/v1/settings/provider-budget/rate/{currency}` - Fetch a current USD reference rate and optional converted preview amounts
- `GET /api/v1/feeds/subscriber-keys` - List every subscriber key across all feeds with its feed slug and title, never a secret
- `GET/POST /api/v1/feeds/{slug}/subscriber-keys` - List scoped subscriber credentials or create one; the secret and feed URL appear only in the create response
- `DELETE /api/v1/feeds/{slug}/subscriber-keys/{id}` - Revoke one scoped subscriber credential without changing global or sibling credentials
- `DELETE /api/v1/feeds/{slug}/subscriber-keys/{id}/record` - Delete the audit record for a revoked key. Active keys return 409; deleting the record never revalidates the token.
- `GET /api/v1/settings` - Get current settings (includes LLM provider, API key status). `llmEnabled` is false when `llmProvider` is `none`. `reviewLinksInNotes` reports whether served episode descriptions carry the review and rate links; set it with `PUT /api/v1/settings/ad-detection`. `feedItunesBlock` reports whether every served feed carries `itunes:block` (default on) and is set the same way. Full show notes from the source feed are served as `content:encoded` with the same chapter block (`chaptersInNotes`) and timestamp shift, while `description` keeps the summary. `queueOrder` (`queued`, `oldest_first`, `newest_first`) sets the default processing order of queued episodes within one priority. The entries for the four fingerprint numbers, `sessionLifetimeHours` and `sessionRememberDays` also carry `min` and `max` (and `step` for the fingerprint numbers), and `queueOrder` carries `options`, so a client can build its inputs from the response.
- `GET/PUT /api/v1/settings/retention` - Get or update retention configuration. `retentionDays` controls how long the processed audio survives; `originalRetentionDays` controls the pre-cut original separately. Server clamps `originalRetentionDays` to `retentionDays` on save.
- `GET/PUT /api/v1/settings/audio` - Toggle whether originals are kept for ad editor review (`keepOriginalAudio`)
- `GET/PUT /api/v1/settings/processing-timeouts` - Soft and hard processing timeouts in seconds
- `GET/PUT /api/v1/settings/update-check` - Get or update the update-check settings (`enabled` for the daily auto-check, `channel`: `stable` or `edge`)
- `GET /api/v1/feeds/{slug}/episodes/{id}/original.mp3` - Stream the retained pre-cut audio (used by ad editor Review mode)
- `PUT /api/v1/settings/ad-detection` - Update ad detection config. `llmProvider` accepts `none` to run episodes with the model-free detectors only (no key, base URL or model needed; saved keys and models are kept); `secondaryProvider` and `failoverLlmProvider` reject it. Includes a partial `modelPricingOverrides` map. Each model entry has input and output prices in USD per 1 million tokens; `null` removes an override. The audio fingerprinting fields are `fingerprintEnabled` (boolean) and the numbers `fingerprintMatchStrictness` (0.65 to 0.95, default 0.65), `fingerprintMinCoverage` (0.1 to 1, default 0.5), `fingerprintGapToleranceS` (0 to 15 seconds, default 3.5) and `fingerprintMinAdLengthS` (3 to 30 seconds, default 5). A value outside its range, a string, a boolean in place of a number, or NaN or Infinity is a 400 that names the field and its range, and nothing in the request is saved. `GET /api/v1/settings` returns each as `{value, isDefault}` with the defaults in `defaults`, `POST /api/v1/settings/ad-detection/reset` restores them, and config export and import carry them. They work the same when `llmProvider` is `none`. A change affects later runs and re-detection and never re-fingerprints stored ads. `queueOrder` (`queued`, `oldest_first`, `newest_first`) sets the default processing order of queued episodes within one priority.
- `GET /api/v1/settings/models` - List available AI models from current provider (empty while the provider is `none`). Optional `?slot=` picks the credential slot to probe: `primary` (default), `secondary`, `failover`, or the `a`/`b` aliases for primary/secondary.
- `POST /api/v1/settings/models/refresh` - Force refresh model list from provider (the primary slot returns an empty list while the provider is `none`). Optional JSON body `{"slot": "primary" | "secondary" | "failover" | "a" | "b"}` picks the credential slot (default primary).
- `GET /api/v1/failover` - Current state, probe results, policy, and recent events for every failover target. See [Failover](failover.md#manual-control).
- `POST /api/v1/failover/{target}/trigger` - Manually switch `target` (`llm-a`, `llm-b`, or `transcriber`) onto its failover configuration. Body `{"reason": "..."}` is optional. 409 when that target has no failover configured.
- `POST /api/v1/failover/{target}/cancel` - Manually switch `target` back to its own configuration.
- `POST /api/v1/failover/probe` - Run a health probe for every enabled target immediately and return the results. LLM targets are skipped while the LLM provider is `none` and report `reachable` null with the detail "LLM disabled" (also in `GET /api/v1/failover`) instead of their older results.
- `POST /api/v1/settings/providers/failover/test-connection` - Test the shared LLM failover provider's connection, the same contract as the secondary provider test.
- `POST /api/v1/settings/providers/failover-whisper/test-connection` - Test the transcriber failover connection, including the sample-audio upload.
- `GET/PUT /api/v1/settings/processing-schedule` - Weekly window for starting new runs: `enabled`, `start` and `end` (`HH:MM`, 24-hour), `days` (`mon` to `sun`, the days a window starts). Times are read in the instance time zone (`notification_timezone`). An `end` at or before `start` runs past midnight. The response adds read-only `timezone`, `open` and `nextChangeAt`. Runs in progress finish; everything else, including manual Reprocess and play-triggered starts, waits in the queue until the window opens.
- `GET/PUT /api/v1/status/processing-admission` - Pause or resume new processing runs (survives restarts)
- `POST /api/v1/settings/rate-limit-hold/reset` - Clear every active rate-limit hold without disabling the hold feature
- `GET/POST/PUT/DELETE /api/v1/settings/webhooks` - Webhook CRUD
- `POST /api/v1/settings/webhooks/{id}/test` - Fire test webhook
- `POST /api/v1/settings/webhooks/validate-template` - Validate and preview a payload template
- `GET/PUT /api/v1/settings/notifications/email` - Email notification settings
- `POST /api/v1/settings/notifications/email/test` - Send a test email

### Queue hold state

`GET /api/v1/status` and every frame of `GET /api/v1/status/stream` carry a `hold` block saying why the queue is not moving. It reports what the maintenance tick last observed and never probes a service itself, so polling it makes no provider or Whisper request.

```json
{
  "hold": {
    "queuePaused": true,
    "holdUntil": "2026-01-01T12:30:00Z",
    "holdSince": "2026-01-01T11:45:00Z",
    "offlineHeld": 2,
    "offlineServices": [
      {"service": "whisper", "held": 2, "reachable": false, "checkedAt": "2026-01-01T11:58:00Z"}
    ]
  },
  "jobs": [
    {
      "slug": "my-favorite-podcast",
      "episodeId": "a1b2c3d4e5f6",
      "title": "Episode 42: The Answer",
      "podcastName": "My Favorite Podcast",
      "stage": "pass1:transcribing",
      "progress": 35,
      "startedAt": 1767261900,
      "elapsed": 120
    }
  ],
  "whisper": {
    "enabled": true,
    "backend": "openai-api",
    "active": true,
    "inactiveReason": null,
    "capacity": 4,
    "inFlight": 2,
    "transcribingEpisodes": 1,
    "maxEpisodes": {"configured": 2, "effective": 2},
    "leader": true
  }
}
```

`queuePaused` is true only while a rate-limit hold is stopping new claims; `holdUntil` is the provider's own reset time and `holdSince` is when the pause began, both null once the reset has passed. An offline wait parks specific episodes and leaves the rest of the queue running, so it never sets `queuePaused`. `offlineHeld` counts every episode deferred outside the rate-limit hold, including any service `offlineServices` does not break out. `offlineServices` also carries a `sponsorblock` entry while YouTube feeds are on and the SponsorBlock API is unreachable or YouTube episodes are waiting on it; its `held` counts those episodes and it is not part of `offlineHeld`. A service's `reachable` is `null` until the tick has probed it once, which means "not checked yet" rather than "up".

Both frames also carry `processingSchedule`: `{"enabled": true, "open": false, "nextChangeAt": "2026-01-01T22:00:00Z"}`. While `open` is false the queue holds new starts (a queued entry's `admission.reason` is `outside_schedule` and `resumesAt` is `nextChangeAt`). `nextChangeAt` is null when the schedule is disabled or never closes.

`jobs` lists every running job, oldest first, with the same fields as `currentJob`, which stays as the oldest one. `whisper` is the pool snapshot: whether it is on and active, the request cap, and how many requests and episodes are in flight. Pool counters are per gunicorn worker, so `inFlight` and `transcribingEpisodes` are only meaningful when `leader` is true; a non-leader worker always reports those as 0.

Both frames also carry a `failover` block: `{"active": ["llm-a"], "targets": {"llm-a": {"active": true, "source": "auto", "since": "2026-01-01T12:00:00Z"}, "llm-b": {...}, "transcriber": {...}}}`. `active` lists the targets currently on their failover configuration; `targets` gives every target's state regardless. See [Failover](failover.md) for what drives a switch. `GET /api/v1/system/status` additionally carries `failover.targets` (with the `configured` flag) and `failover.probes` (the health-probe results behind auto-trigger and auto-recovery).

### Public feed-domain routes

A handful of routes live on the feed domain itself, outside `/api/v1`, and are not part of the OpenAPI spec: the served RSS feed at `/{slug}`, episode audio at `/episodes/{slug}/{episodeId}.mp3`, transcripts and chapters (`.vtt`, `/chapters.json`), feed cover art at `/{slug}/cover-minuspod.jpg`, and:

- `GET /episodes/{slug}/{episodeId}/artwork` - Serve a cached per-episode cover. Sends an `ETag` and `Cache-Control: no-cache`. Local-feed episodes get one whenever an upload, import, or embedded-artwork extraction cached one; 404 if nothing is cached (never fetches on demand). This is the URL a local feed's per-item `<itunes:image>` points at.

All of these are gated by the feed auth key the same way as the RSS feed itself when Authenticated feeds is on; see [Security > Authenticated feeds](security-and-storage.md#authenticated-feeds-optional).

GET and HEAD enforce the same feed key on every public route. A HEAD request for unprocessed subscribed audio proxies the upstream headers and never starts processing. Local audio answers from the retained original. This lets podcast apps inspect media without filling the queue.

## Notifications

MinusPod can notify you when episodes complete processing or permanently fail, and when the LLM provider rejects requests (bad credentials, exhausted spend limits, oversized requests). It can also alert you when a rate-limit hold or an unreachable endpoint parks the queue. Two channels share the same events: webhooks (HTTP POST to any endpoint) and native email through your own SMTP server. Configure them on the Settings page under **Notifications > Email** and **Notifications > Webhooks**, or via the REST API.

The **Notifications** section contains the timezone setting and separate **Email** and **Webhooks** subsections. Each subsection collapses independently and remembers its open or closed state.

Every payload carries `timestamp` (UTC, `Z`-suffixed) and `timestamp_local` (the same instant in the configured `notification_timezone`, with a UTC offset). `notification_timezone` is an IANA zone name (default `UTC`, or the container's `TZ` env var when it names a valid zone); get or set it at `GET`/`PUT /api/v1/settings/notifications/timezone`. Email shows `timestamp_local` in the Timestamp row, falling back to `timestamp` when the zone is UTC.

## Webhooks

Webhooks fire an HTTP POST to configured URLs. Works with any HTTP endpoint. Use a custom Jinja2 payload template to match the receiver's expected format.

### Events

| Event | Fires when |
|---|---|
| `Episode Processed` | Episode completes processing successfully |
| `Episode Awaiting Approval` | Episode finishes processing but is held out of the served feed until approved (Require manual approval). On a feed that requires approval, `Episode Awaiting Approval` fires when processing finishes and `Episode Processed` fires when the episode is approved, so `Episode Processed` always means the episode is now served. Same payload as `Episode Processed` |
| `Episode Pending Review` | Episode finishes processing with one or more detections held for review. Fires alongside `Episode Processed` or `Episode Awaiting Approval`. The episode is served with the held detections left in until they are approved or rejected, unless it is also awaiting approval. Same payload as `Episode Processed`; `ads_held` carries the count |
| `Episode Failed` | Episode reaches permanently failed status |
| `Auth Failure` | LLM provider rejects the API key as invalid or expired (401/403 without billing markers; rate-limited to one per 5 minutes) |
| `Limit Exceeded` | LLM provider rejects a request because a spend or usage limit is exhausted: a monthly key limit (OpenRouter 403), out of credits (402, Anthropic low balance), or OpenAI `insufficient_quota` (rate-limited to one per 5 minutes). The key is valid; add credits or raise the limit, then reprocess the episode (it is marked permanently failed rather than retried). |
| `Rate Limit Structural` | A single detection-window request exceeds the provider's per-minute token cap (rate-limited to one per 5 minutes). Retrying will not help; the operator needs to shrink the detection window or move to a higher tier. |
| `Feed Refresh Failed` | A feed's upstream RSS fetch fails 3 times in a row. One alert per feed per 5 minutes, with a shared burst cap so an outage that breaks every feed at once sends one alert, not one per feed. |
| `Update Available` | The daily update check finds a newer release on the selected channel (`stable` or `edge`); fires once per version |
| `Cue Template Quiet` | An enabled audio cue template on a `cue_only` feed has matched before but has zero above-threshold matches across the feed's last 5 telemetry-recorded episodes. Rate-limited to one alert per template per 5 minutes. |
| `Queue Held` | A provider 429 with a reset time paused the queue (Queue Control > Rate-limit hold). One alert per pause; a later 429 that extends the pause is silent. |
| `Queue Resumed` | The rate-limit hold cleared and the queue is claiming work again. One alert per 5 minutes. |
| `Service Offline` | An episode deferred because the LLM or Whisper endpoint was unreachable (Offline queue). One alert per service per 5 minutes. |
| `Service Reachable` | The offline probe found a service back up and re-queued its deferred episodes. One alert per service per 5 minutes. |
| `Failover Triggered` | A target (`llm-a`, `llm-b`, or `transcriber`) switched to its failover configuration, automatically or by hand. Every state change sends an event. See [Failover](failover.md). |
| `Failover Cancelled` | A target switched back to its own configuration. Every state change sends an event. |

The **Test** button sends one sample payload per event the webhook is subscribed to, each shaped like that event's real payload (see Default Payloads below) with `test: true` set. A webhook subscribed to three events gets three test deliveries in one click; a custom payload template renders against each event's own variable set (episode-shaped for `Episode Processed`/`Episode Failed`, provider-shaped for the alert events, and so on).

If a custom template can't render for an event (for example it uses `episode.title`, which alert events don't have), MinusPod dispatches a default body. Network or endpoint errors can still prevent delivery:

- `text/*` content types get a one-line summary, such as `MinusPod Auth Failure: anthropic claude-sonnet-4 - Invalid API key provided`.
- Any other content type gets the default JSON payload.

The Test result lists each event's `delivered` status and `templateFallback` flag, so a fallback remains visible when delivery fails.

### Template Variables

Custom payload templates are Jinja2 strings rendered against these variables:

| Variable | Type | Description |
|---|---|---|
| `event` | string | `Episode Processed`, `Episode Awaiting Approval`, `Episode Pending Review`, `Episode Failed`, `Auth Failure`, `Limit Exceeded`, `Rate Limit Structural`, `Feed Refresh Failed`, `Update Available`, `Cue Template Quiet`, `Queue Held`, `Queue Resumed`, `Service Offline`, `Service Reachable`, `Failover Triggered`, or `Failover Cancelled` |
| `timestamp` | string | ISO 8601 UTC timestamp |
| `timestamp_local` | string | ISO 8601 local timestamp with UTC offset, per the notification_timezone setting |
| `podcast.name` | string | Podcast title (falls back to slug if unavailable) |
| `podcast.slug` | string | Feed slug |
| `episode.id` | string | Episode ID |
| `episode.title` | string | Episode title |
| `episode.slug` | string | Feed slug |
| `episode.url` | string | Full UI URL to episode |
| `episode.ads_removed` | int | Number of ads removed |
| `episode.ads_held` | int | Number of ads held for review (not cut) |
| `episode.ads_not_cut` | int | Number of detections not cut (rejected by validation, below threshold, or vetoed by the reviewer) |
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
| `timestamp_local` | string | ISO 8601 local timestamp with UTC offset, per the notification_timezone setting |
| `provider` | string | LLM provider name (anthropic, openrouter, etc.) |
| `model` | string | Model that failed authentication |
| `error_message` | string | Error details from the provider |
| `status_code` | int/null | HTTP status code (401 or 403) |

**Limit Exceeded events use a different payload:**

| Variable | Type | Description |
|---|---|---|
| `event` | string | `Limit Exceeded` |
| `timestamp` | string | ISO 8601 UTC timestamp |
| `timestamp_local` | string | ISO 8601 local timestamp with UTC offset, per the notification_timezone setting |
| `provider` | string | LLM provider name (openrouter, openai, etc.) |
| `model` | string | Model the rejected request targeted |
| `error_message` | string | Error details from the provider |
| `status_code` | int/null | HTTP status code (402, 403, 429, or 400 depending on provider) |

**Rate Limit Structural events use a different payload:**

| Variable | Type | Description |
|---|---|---|
| `event` | string | `Rate Limit Structural` |
| `timestamp` | string | ISO 8601 UTC timestamp |
| `timestamp_local` | string | ISO 8601 local timestamp with UTC offset, per the notification_timezone setting |
| `provider` | string | LLM provider name |
| `model` | string | Model that returned the 429 |
| `limit` | int | The provider's per-minute token cap |
| `used` | int | Tokens already consumed in the current minute |
| `requested` | int | Tokens this request asked for (greater than `limit` means the request structurally cannot fit) |
| `error_message` | string | Raw error details from the provider |

**Feed Refresh Failed events use a different payload:**

| Variable | Type | Description |
|---|---|---|
| `event` | string | `Feed Refresh Failed` |
| `timestamp` | string | ISO 8601 UTC timestamp |
| `timestamp_local` | string | ISO 8601 local timestamp with UTC offset, per the notification_timezone setting |
| `slug` | string | Feed slug |
| `podcast_name` | string | Podcast title (falls back to the slug) |
| `feed_url` | string | Upstream feed URL, with any credentials stripped |
| `error_message` | string | Error from the most recent fetch attempt |
| `failure_count` | int | Consecutive failed refreshes when the alert fired |

**Update Available events use a different payload:**

| Variable | Type | Description |
|---|---|---|
| `event` | string | `Update Available` |
| `timestamp` | string | ISO 8601 UTC timestamp |
| `timestamp_local` | string | ISO 8601 local timestamp with UTC offset, per the notification_timezone setting |
| `version` | string | Version number of the newer release |
| `channel` | string | Channel the release was found on: `stable` or `edge` |
| `release_date` | string/null | Release date (`YYYY-MM-DD`) |
| `release_url` | string/null | GitHub release page URL |

**Cue Template Quiet events use a different payload:**

| Variable | Type | Description |
|---|---|---|
| `event` | string | `Cue Template Quiet` |
| `timestamp` | string | ISO 8601 UTC timestamp |
| `timestamp_local` | string | ISO 8601 local timestamp with UTC offset, per the notification_timezone setting |
| `podcast.name` | string | Podcast title (falls back to slug if unavailable) |
| `podcast.slug` | string | Feed slug |
| `template.id` | int | Cue template ID |
| `template.label` | string | Cue template label |
| `last_match_at` | string/null | Timestamp of the template's last above-threshold match before it went quiet |

**Queue Held events use a different payload:**

| Variable | Type | Description |
|---|---|---|
| `event` | string | `Queue Held` |
| `timestamp` | string | ISO 8601 UTC timestamp |
| `timestamp_local` | string | ISO 8601 local timestamp with UTC offset, per the notification_timezone setting |
| `hold_until` | string | The provider's reset time; the queue claims no new work until then |
| `hold_until_local` | string | The same reset time in the notification_timezone setting, with UTC offset |
| `error_message` | string | The 429 response that triggered the hold |
| `slug` | string | Feed slug of the episode that hit the limit |
| `episode_id` | string | ID of that episode |
| `podcast_name` | string | Podcast title (falls back to the slug) |

**Queue Resumed events use a different payload:**

| Variable | Type | Description |
|---|---|---|
| `event` | string | `Queue Resumed` |
| `timestamp` | string | ISO 8601 UTC timestamp |
| `timestamp_local` | string | ISO 8601 local timestamp with UTC offset, per the notification_timezone setting |
| `held_since` | string/null | When the hold began; null when no start time was recorded |

**Service Offline events use a different payload:**

| Variable | Type | Description |
|---|---|---|
| `event` | string | `Service Offline` |
| `timestamp` | string | ISO 8601 UTC timestamp |
| `timestamp_local` | string | ISO 8601 local timestamp with UTC offset, per the notification_timezone setting |
| `service` | string | `llm` or `whisper` |
| `error_message` | string | The connection error that deferred the episode |
| `slug` | string | Feed slug of the deferred episode |
| `episode_id` | string | ID of that episode |
| `podcast_name` | string | Podcast title (falls back to the slug) |

**Service Reachable events use a different payload:**

| Variable | Type | Description |
|---|---|---|
| `event` | string | `Service Reachable` |
| `timestamp` | string | ISO 8601 UTC timestamp |
| `timestamp_local` | string | ISO 8601 local timestamp with UTC offset, per the notification_timezone setting |
| `service` | string | `llm` or `whisper` |
| `requeued` | int | Deferred episodes the probe pass sent back to the queue |

**Failover Triggered and Failover Cancelled events use a different payload:**

| Variable | Type | Description |
|---|---|---|
| `event` | string | `Failover Triggered` or `Failover Cancelled` |
| `timestamp` | string | ISO 8601 UTC timestamp |
| `timestamp_local` | string | ISO 8601 local timestamp with UTC offset, per the notification_timezone setting |
| `target` | string | `llm-a`, `llm-b`, or `transcriber` |
| `source` | string | `auto` (mid-run trigger error), `probe` (two failed health probes), or `manual` |
| `reason` | string | Free text: the triggering error, or empty on a manual cancel |

### Default Payloads

When no custom template is configured, MinusPod sends these JSON payloads.

**Episode Processed:**

```json
{
  "event": "Episode Processed",
  "timestamp": "2026-04-12T00:15:42Z",
  "timestamp_local": "2026-04-12T00:15:42+00:00",
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
    "ads_held": 1,
    "ads_not_cut": 1,
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
  "timestamp_local": "2026-04-12T00:15:42+00:00",
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
    "ads_held": 0,
    "ads_not_cut": 0,
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
  "timestamp_local": "2026-04-12T00:15:42+00:00",
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
  "timestamp_local": "2026-04-12T00:15:42+00:00",
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
  "timestamp_local": "2026-04-12T00:15:42+00:00",
  "provider": "groq",
  "model": "llama-3.3-70b-versatile",
  "limit": 6000,
  "used": 2400,
  "requested": 8500,
  "error_message": "rate_limit_exceeded: Request too large for model on tokens per minute"
}
```

**Feed Refresh Failed:**

```json
{
  "event": "Feed Refresh Failed",
  "timestamp": "2026-04-12T00:15:42Z",
  "timestamp_local": "2026-04-12T00:15:42+00:00",
  "slug": "my-podcast",
  "podcast_name": "My Podcast",
  "feed_url": "https://feeds.example.com/my-podcast",
  "error_message": "HTTP 522 from upstream",
  "failure_count": 3
}
```

**Update Available:**

```json
{
  "event": "Update Available",
  "timestamp": "2026-04-12T00:15:42Z",
  "timestamp_local": "2026-04-12T00:15:42+00:00",
  "version": "2.74.0",
  "channel": "stable",
  "release_date": "2026-07-22",
  "release_url": "https://github.com/ttlequals0/MinusPod/releases/tag/v2.74.0"
}
```

**Cue Template Quiet:**

```json
{
  "event": "Cue Template Quiet",
  "timestamp": "2026-04-12T00:15:42Z",
  "timestamp_local": "2026-04-12T00:15:42+00:00",
  "podcast": {
    "name": "My Favorite Podcast",
    "slug": "my-favorite-podcast"
  },
  "template": {
    "id": 3,
    "label": "break stinger"
  },
  "last_match_at": "2026-03-01T00:00:00Z"
}
```

**Queue Held:**

```json
{
  "event": "Queue Held",
  "timestamp": "2026-04-12T00:15:42Z",
  "timestamp_local": "2026-04-12T00:15:42+00:00",
  "hold_until": "2026-01-01T12:30:00Z",
  "hold_until_local": "2026-01-01T12:30:00+00:00",
  "error_message": "rate_limit_exceeded: retry after 900 seconds",
  "slug": "my-podcast",
  "episode_id": "a1b2c3d4e5f6",
  "podcast_name": "My Podcast"
}
```

**Queue Resumed:**

```json
{
  "event": "Queue Resumed",
  "timestamp": "2026-04-12T00:15:42Z",
  "timestamp_local": "2026-04-12T00:15:42+00:00",
  "held_since": "2026-01-01T12:15:00Z"
}
```

**Service Offline:**

```json
{
  "event": "Service Offline",
  "timestamp": "2026-04-12T00:15:42Z",
  "timestamp_local": "2026-04-12T00:15:42+00:00",
  "service": "llm",
  "error_message": "Connection refused",
  "slug": "my-podcast",
  "episode_id": "a1b2c3d4e5f6",
  "podcast_name": "My Podcast"
}
```

**Service Reachable:**

```json
{
  "event": "Service Reachable",
  "timestamp": "2026-04-12T00:15:42Z",
  "timestamp_local": "2026-04-12T00:15:42+00:00",
  "service": "llm",
  "requeued": 3
}
```

**Failover Triggered:**

```json
{
  "event": "Failover Triggered",
  "timestamp": "2026-04-12T00:15:42Z",
  "timestamp_local": "2026-04-12T00:15:42+00:00",
  "target": "llm-a",
  "source": "auto",
  "reason": "HTTP 503"
}
```

**Failover Cancelled:**

```json
{
  "event": "Failover Cancelled",
  "timestamp": "2026-04-12T00:15:42Z",
  "timestamp_local": "2026-04-12T00:15:42+00:00",
  "target": "llm-a",
  "source": "manual",
  "reason": ""
}
```

## Email notifications

Point MinusPod at an SMTP server and it emails you for the events you pick. One configuration: SMTP host, port, security (None, STARTTLS, or SSL/TLS), optional username and password, a from address, and a comma-separated recipient list. The password is stored encrypted like provider API keys, so saving one needs `MINUSPOD_MASTER_PASSPHRASE` set.

Emails are HTML with the MinusPod logo embedded inline (no external image fetch) and a plain-text fallback part for text-only clients. Each event renders a subject like `[MinusPod] Episode Failed: My Show - Episode 42` with a short table of facts and, for alert events, the action to take. Alert events (`Auth Failure`, `Limit Exceeded`, `Rate Limit Structural`, `Queue Held`, `Queue Resumed`, `Service Offline`, `Service Reachable`) keep their 5-minute dedup window, shared with webhooks, so a burst of failures produces one email. `Failover Triggered` and `Failover Cancelled` are sent once per state change with no dedup window. The webhook Test button never emails; the email form has its own **Send test email** button that delivers a real message through the saved settings.

By default the failure and alert events, including `Queue Held`, `Queue Resumed`, `Service Offline` and `Service Reachable`, are checked and `Episode Processed` is not, so a working setup stays quiet. SMTP sending runs with a 10 second timeout in a background thread; a down mail server never blocks or fails episode processing. An `Episode Processed` email adds an "Ads held for review" and/or "Detections not cut" row when the run produced either, so a quiet run's table stays short. A send failure logs the full traceback, for SMTP troubleshooting from container logs.

### Example: Pushover

Pushover supports native webhook ingestion with data extraction selectors. No custom payload template needed. MinusPod's default JSON payload works directly.

1. Create a webhook at [pushover.net/dashboard](https://pushover.net/dashboard) and copy its URL.
2. In MinusPod Settings, open **Notifications > Webhooks**: paste the URL, select events, **leave payload template blank**.
3. Click Test in MinusPod to fire a sample payload to Pushover.
4. In Pushover, load the last payload and configure data extraction selectors:

| Field | Selector |
|---|---|
| Title | `{{podcast.name}} - {{event}}` |
| Body | `{{episode.title}}`<br>`{{episode.ads_removed}} ads removed. Saved {{episode.time_saved}}. Cost {{episode.llm_cost_display}}` |
| URL | `{{episode.url}}` |
| URL Title | `Open in MinusPod` |

> Pushover's `{{...}}` selector syntax is evaluated on Pushover's side; these are not Jinja2 templates.

### Example: ntfy

ntfy requires a custom payload template to match its expected JSON format.

1. Self-hosted or ntfy.sh: set your topic name
2. Add a webhook under **Notifications > Webhooks** in Settings:
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

The template above only renders for the episode events. To receive alert events (Auth Failure, Queue Held, ...) on the same webhook, use a plain-text template instead:

- **URL:** `https://ntfy.sh/your-topic` (the topic is in the URL, so the template has no `topic` field)
- **Content type:** `text/plain` (ntfy shows the body as the message)

Episode failures put the error under `episode.error_message`, while alert events use top-level `error_message`. Use this template:

- **Payload template:**
  ```
  {{ event }}{% if episode is defined %}: {{ podcast.name }} - {{ episode.title }}{% endif %}{% if error_message is defined and error_message %} - {{ error_message }}{% elif episode is defined and episode.error_message is defined and episode.error_message %} - {{ episode.error_message }}{% endif %}
  ```

This renders for every event, for example `Episode Processed: My Show - Episode 42` and `Auth Failure - Invalid API key provided`. Use **Test** to confirm that no subscribed event falls back.

> ntfy also supports header-based delivery (`X-Title`, `X-Message`, `X-Click` headers with plain text body); either approach works with MinusPod's template system.

### Request Signing

If a webhook has a secret configured, MinusPod adds an `X-MinusPod-Signature: sha256=<hmac>` header to each POST, computed with HMAC-SHA256 over the request body.

---

[< Docs index](README.md) | [Project README](../README.md)
