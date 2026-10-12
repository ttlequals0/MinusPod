# Detected Ads tab, category filters, marker split, chapter density, and six production bug fixes

Target release: 2.82.0

Ten independent workstreams shipped as one PR and one release, per the
batching rule in CLAUDE.md. A through D are features. E through J are
production bugs found in a log review of the 22 hours to 2026-07-29 21:53Z.

Two of the bugs cause silent data loss and are the reason this batch is not
features-only: E drops discovered episodes when SQLite is contended, and F
drops episodes when a feed body arrives truncated. Neither surfaces as an
error to the user. Both look like a podcast that has fewer episodes than it
really does.

| # | Workstream | Kind |
|---|---|---|
| A | Detected Ads tab | feature |
| B | Category filter on Patterns and Ad Review | feature |
| C | Marker split, issue #563 option 1 | feature |
| D | Chapter density | feature |
| E | Discovered-episode upsert loses data under lock contention | bug, data loss |
| F | Truncated feed bodies parsed as partial feeds | bug, data loss |
| G | Every short episode burns two failed CUDA attempts | bug, waste |
| H | Backup secret-exposure warning fires on the wrong condition | bug, misleading alert |
| I | Podping node failure log volume | bug, noise |
| J | Log-level noise on routine external traffic | bug, noise |

## A. Detected Ads tab

### Definition

A detection is "cut" when `marker_status` returns `accepted`, which is
equivalent to `was_cut=True` on the marker. This is the complement of the
Ad Review tab, which is the held and rejected queue.

One caveat to verify during implementation: `was_cut=True` is stamped at
confidence gating (`_gate_validation_by_confidence` in
`src/main_app/processing.py:920`), and `_mark_kept_markers` later stamps
`was_cut=False` for the `keep` action. The `beep` action's interaction with
`was_cut` is not yet confirmed, so each row carries an `actionApplied`
badge. A beeped marker then reads as beeped rather than silently counting
as a removal.

### Backend

`src/detection_review.py`:

- `flatten_detections` adds `category` (from marker `category`, may be
  absent) and `actionApplied` (from marker `action_applied`) to each item.
- `filter_detections` gains a `category` parameter. The value `none`
  matches markers with no category set; any member of `SEGMENT_CATEGORIES`
  matches exactly.
- New `summarize_cut_detections(items)` returning:
  - `count`: number of cut detections
  - `durationSeconds`: summed `end - start`
  - `byCategory`: count per category, plus a `none` key for unset
  - `distinctSponsors`: unique non-empty sponsor names
  - `distinctPodcasts`: unique feed slugs

`src/api/detections.py`:

- Accepts `category`, validated against `SEGMENT_CATEGORIES` plus `none`,
  400 on anything else, matching how `status` and `sort` are validated.
- Returns `cutSummary` alongside the existing `counts`.

Ordering of the summary matters. `cutSummary` is computed after the `feed`
and `q` filters but before the `category` filter. Computing it after the
category filter would collapse `byCategory` into a single bucket and make
the breakdown useless; computing it before the feed filter would make the
podcast selector have no effect on the header. The existing pre-filter
`counts` block is unchanged.

### Frontend

- `frontend/src/pages/PatternsPage.tsx` gains a third tab,
  `?tab=detected-ads`.
- New `frontend/src/pages/patterns/DetectedAdsTab.tsx`.
- Targeted refactor: extract the detection row, mobile card, badges, and
  action cluster out of `AdReviewTab.tsx` (527 lines) into
  `frontend/src/pages/patterns/DetectionRows.tsx`, consumed by both tabs.
  These components already carry non-obvious responsive layout comments;
  duplicating them would mean maintaining two copies of that reasoning.
- Filters: podcast, category, free-text search, and sort by
  date / confidence / podcast, reusing the existing query parameters.
- Stats header renders `cutSummary`: total detections, total duration,
  per-category counts, distinct sponsors, distinct podcasts.
- Row actions: play, open in the waveform editor, Not an ad, Split.
  "Not an ad" files a reject correction and triggers a recut to restore
  the audio, matching the episode page's behavior. Confirm is omitted
  because these detections are already accepted.

## B. Category filter on Patterns and Ad Review

- Patterns tab: client-side `categoryFilter` state over `pattern.category`,
  with an Uncategorized option for null, placed beside the existing Scope,
  Origin, and Source selects. The patterns list is already filtered
  client-side, so this needs no API change.
- Ad Review tab: reuses the `category` query parameter added in A.

## C. Marker split, issue #563 option 1

Options 2 and 3 of the issue already shipped, in 2.70.0 and 2.72.0
respectively. Option 1 is the draggable divider that mints N markers, and
it closes the issue.

### Backend

1. New endpoint `GET /feeds/<slug>/episodes/<episode_id>/split-candidates`
   in `src/api/episodes.py`, taking `start` and `end`.

   It loads the transcript via `db.get_transcript_for_timestamps`, extracts
   the span with `extract_transcript_segment`, segments it with
   `split_template_text`, maps each segment's character offset back to a
   timestamp through `parse_transcript_segments`, and returns candidate
   divider times plus each resulting piece's text and its
   `extract_sponsor_from_text` guess.

   This keeps `AD_TRANSITION_PHRASES` as the single source of truth in
   Python. Computing candidates client-side would mean a second copy of the
   phrase list in TypeScript that silently drifts.

2. New `split` correction type in `submit_correction`, `src/api/patterns.py`.

   Input: `original_ad` with the current bounds, `split_points` as a list of
   times, and optional per-piece sponsor overrides.

   Validation, all returning 400:
   - Every split point strictly inside `(start, end)`.
   - Points strictly increasing after sorting, and no two within
     `BOUNDS_TOLERANCE_S`.
   - Every resulting piece at least `MIN_AD_DURATION` (7.0s, from
     `src/config.py:35`), which is the validator's own floor for an ad.
   - The original marker must exist at the given bounds within tolerance.

   Effect: replaces the one marker in `ad_markers_json` with N markers that
   inherit `category`, `detection_stage`, `confidence`, `was_cut`, and
   `action_applied` from the original; derives each piece's text with
   `extract_transcript_segment`; mints one pattern per piece through the
   existing `_create_patterns_from_segments` dedup path; returns every
   resulting pattern id. The client then fires one recut, the same way the
   confirm path does.

3. No new value in the `correction_type` CHECK constraint
   (`src/database/schema/tables.py:198`). SQLite cannot alter a CHECK in
   place, so adding `split` would mean rebuilding a table that holds the
   entire correction history. Given the project rule against risking data in
   migrations, the split instead writes existing types: `boundary_adjustment`
   for the first piece against the original bounds, `create` for each
   remaining piece. Every downstream consumer, including `marker_resolution`
   in `src/detection_review.py` and pattern health, keeps working unchanged.

   Accepted cost: no correction row is labelled a split. The bounds and the
   server log line carry that fact.

### Frontend

- New `frontend/src/components/SplitMarkerModal.tsx`.

  Not additional state inside `AdReviewModal.tsx`, which is already 1382
  lines. Split is a different interaction from a two-pin boundary edit: N
  dividers, a sponsor field per piece, and a text preview per piece. The
  precedent is `CueMarkModal.tsx` at 971 lines, built the same way.

  It reuses the `ad-editor/` primitives: `Pin`, `useWaveformWindow`,
  `usePeaks`, `usePeakSlice`, `TransportBar`, `ZoomControl`, `SponsorInput`.

- `ad-editor/Pin.tsx` gains a `divider` kind. The current `kind` is
  `'start' | 'end'` with a single `otherBoundary` clamp; a divider has
  neighbours on both sides, so it needs `minBoundary` and `maxBoundary`.
- The 7.0s per-piece floor is enforced client-side too, so the Save button
  disables with a reason rather than round-tripping to a 400.
- Entry points: a Split button in `AdReviewModal`'s review mode, which
  covers the episode page, the Ad Review tab, and anywhere else the editor
  opens; plus a direct Split action on Detected Ads rows, so splitting a
  cut ad does not require opening the boundary editor first. Both open the
  same `SplitMarkerModal`.

## D. Chapter density

### Diagnosis

`src/chapters_generator.py:677`:

```python
num_splits = min(int(episode_duration / 600), 6)
```

A hard ceiling of 6 boundaries, so 7 chapters, regardless of episode
length. The prompt then asks for exactly that many.

Measured on a 2 h 47 m processed episode (11292 s original, 10004 s
processed, 124107-character transcript, about 31k tokens, sent in full):
6 boundaries requested, 4 returned, 5 chapters produced, with a 3196 s
chapter and a 4203 s chapter. Two separate failures compound. The cap
limits the count, and a single call over a long transcript clusters the
boundaries it does return, leaving a 70-minute tail chapter.

Not a cause: input truncation. The full transcript reaches the model.

### Change

Replace the single call with windowed detection. Partition the processed
timeline into windows, request boundaries per window, and pass the previous
window's final chapter title forward so the model does not restate a topic
it just closed. `_detect_topic_boundaries` already accepts `start_time` and
`end_time`, so the range plumbing exists and is never looped over.

Four new keys go in the stage-tunable registry. They are global rather than
per-stage, following `window_size_seconds` and `window_overlap_seconds`,
which already live in that registry as detection-window geometry rather than
LLM sampling parameters.

| Key | Default | Range | Replaces |
|---|---|---|---|
| `chapter_target_seconds` | 600 | 120 to 3600 | the `/600` divisor |
| `chapter_window_seconds` | 2700 | 600 to 10800 | new, transcript per call |
| `chapter_max_boundaries` | 40 | 1 to 200 | the hardcoded `6` |
| `chapter_min_duration_seconds` | 180 | 30 to 900 | `MIN_CHAPTER_DURATION` |

`MIN_CHAPTER_DURATION` becomes a tunable rather than staying a constant
because the absorption pass in `_enforce_min_duration` would otherwise
silently undo any `chapter_target_seconds` below 180.

`chapter_boundary_max_tokens` default rises from 300 to 1500. At 40
boundary lines the old 300-token output cap becomes the binding
constraint. It is not binding at 6, which is why it has not surfaced.

### Backend registration

A stage tunable is not one edit. Each key needs four registrations, and
`STAGE_TUNABLE_ENV_VARS` is the only one that comes free because it is
derived (`{key: key.upper() for key in STAGE_TUNABLE_DEFAULTS}`).

| Location | What to add |
|---|---|
| `STAGE_TUNABLE_DEFAULTS`, `src/config.py:1271` | the default value |
| `STAGE_TUNABLE_RANGES`, `src/config.py:1312` | the `(lo, hi)` bounds |
| `STAGE_TUNABLE_PAYLOAD_KEYS`, `src/config.py:1472` | `(camelCase, snake_case, 'int')` triple |
| `STAGE_TUNABLE_ENV_VARS` | derived, nothing to do |

`STAGE_TUNABLE_PAYLOAD_KEYS` is the one that carries the most weight: the
same tuple drives the GET response (`src/api/settings.py:583`) and the POST
validation loop (`src/api/settings.py:1500`). A key absent from it is
invisible to the API in both directions, whatever else is registered.

Payload keys, all `'int'`:

| Payload key | DB key |
|---|---|
| `chapterTargetSeconds` | `chapter_target_seconds` |
| `chapterWindowSeconds` | `chapter_window_seconds` |
| `chapterMaxBoundaries` | `chapter_max_boundaries` |
| `chapterMinDurationSeconds` | `chapter_min_duration_seconds` |

Cross-field validation at the API layer, since per-field ranges cannot
express it. The precedent is `windowOverlapSeconds must be less than
windowSizeSeconds` at `src/api/settings.py:1459`, and the transcribe-chunk
pair at `:996`. Two constraints, checked against effective values so a
partial payload cannot straddle a valid and a stored value into an invalid
combination:

- `chapterMinDurationSeconds` must be at most `chapterTargetSeconds`.
  Otherwise `_enforce_min_duration` absorbs every chapter the target asked
  for, and the target silently does nothing.
- `chapterTargetSeconds` must be at most `chapterWindowSeconds`. A target
  larger than the window means a window can never contain a whole chapter.

`openapi.yaml` gains the four fields on both the settings response and the
settings update request body.

### Frontend registration

`frontend/src/api/types.ts`: four `StageTunableEntry<number | null>` fields
on the `StageTunables` interface (near `:660`), and four optional
`number | null` fields on the settings-update payload interface (near
`:638`).

`frontend/src/pages/settings/StageTunablesSection.tsx` renders them as their
own labeled group, a sibling of the Chapter Boundary Detection block, not
inside it. The `StageBlock` type is a fixed four-key tuple
(`temperatureKey`, `maxTokensKey`, `budgetKey`, `levelKey`), and these are
durations and counts, so they do not fit that shape. The window-geometry
fields are the working precedent for a standalone integer group in this
section, rendered around `:416-449` with an env-override read, a
reset-to-default button, a default fallback, and an `onChange`.

Two registrations in that file are easy to miss and both fail quietly:

- `DRAFT_KEYS` (`:101`). A key missing here is skipped by `draftsEqual` and
  by the save payload, so the field renders, accepts input, and never
  saves.
- `buildBaseline` (`:121`). A key missing here has an `undefined` baseline,
  so the dirty check compares a number against `undefined` and the section
  reads as permanently unsaved.

A test asserting `DRAFT_KEYS` covers every key the section renders would
catch both, and would keep catching them for whoever adds the next tunable.

### Failure semantics

With one call, a parsed-but-empty result was correctly treated as a
failure. With windows, an empty window is legitimate: a 45-minute stretch
on one topic has no boundary. So:

- Degraded is flagged when a call fails, meaning `call_llm` returns None or
  raises.
- Degraded is also flagged when every window returns nothing.
- Boundaries from windows that succeeded are kept when others fail.
- The window count and total call count are logged, so the cost of a run is
  visible.

Cost on the measured episode under these defaults: 4 windows instead of 1
call, and roughly 16 boundaries requested instead of 6.

## E. Discovered-episode upsert loses data under write contention

### Evidence

Two write-lock storms in the 22 hours reviewed, each about 7 minutes and
each self-clearing:

| Storm | Window (UTC) | Peak rate |
|---|---|---|
| 1 | 2026-07-28 23:48 to 23:56 | 615 lines / 5 min |
| 2 | 2026-07-29 00:09 to 00:15 | 1014 lines / 5 min |

Roughly 1600 instances of a single line, `Failed to upsert discovered
episode <id>: database is locked`. Both storms overlap a 27-feed RSS
refresh cycle.

### Root cause

`bulk_upsert_discovered_episodes`, `src/database/episodes.py:950`:

- Takes a bare `conn` and loops per episode, so Python's default deferred
  transaction upgrades to a write lock at the first DML statement.
- That upgrade returns SQLITE_BUSY immediately, and `busy_timeout` does not
  retry it. This is the exact failure mode documented at
  `src/database/__init__.py:385-391`, whose remedy,
  `db.transaction(immediate=True)`, was added for issue #566 in 2.71.0.
  This function never adopted it.
- The per-episode `except Exception` at line 1047 logs and continues, so a
  transient lock silently drops discovery for every episode in the batch
  and still returns a low-but-successful `inserted` count.

Collateral in the same windows, all downstream of the same contention:
`RSS refresh failed: database is locked` aborted several feed refreshes,
plus queue processor, podping host recording, and podcast metadata update
failures. The 2.71.0 teardown guards did roll back every leaked
transaction, so nothing wedged permanently. This is contention and data
loss, not a hang.

### Change

1. Wrap the batch in `db.transaction(immediate=True)` so the write lock is
   taken up front where `PRAGMA busy_timeout = 30000` applies.
2. Stop swallowing lock errors per episode. A batch that cannot get its
   lock raises, so the caller retries the feed instead of reporting success
   having discovered nothing. Per-episode `except` narrows to genuinely
   per-row faults, such as a malformed published date.
3. Aggregate the per-episode warning into one line per batch reporting the
   count of skipped episodes. 1600 identical warnings is its own incident.

The existing snapshot-of-existing-GUIDs logic and the `inserted` count
semantics are preserved; only the transaction boundary and error handling
change.

## F. Truncated feed bodies are parsed as partial feeds

### Evidence

Three occurrences in the window, each a pair of warnings at the same second
on the same feed:

```
21:25:09 [rss_parser] Gzip decompression failed, retrying: url=https://feeds.megaphone.fm
21:25:09 [rss_parser] RSS parse warning: source=<feed> bytes=306645 err=<unknown>:4226:67: no element found
05:02:01 [rss_parser] Gzip decompression failed, retrying: ...
05:02:01 [rss_parser] RSS parse warning: source=<feed> bytes=307966 err=<unknown>:4211:133: no element found
04:01:34 [rss_parser] Gzip decompression failed, retrying: ...
04:01:35 [rss_parser] RSS parse warning: source=<feed> bytes=1244349 err=<unknown>:14604:68: unclosed token
```

These are not two problems, they are one event repeated. The gzip errors
("invalid code lengths set", "too many length or distance symbols",
"invalid distance code") are the signature of a gzip stream that ended
early, and "no element found" / "unclosed token" at a byte offset is a
truncated XML document. Body sizes vary between 306 KB and 1.2 MB for the
same feed, which is consistent with truncation at varying points rather
than a feed that is genuinely malformed.

### Root cause

The gzip retry itself is correct: both fetch paths
(`src/rss_parser.py:292` and `:410`) re-request with
`Accept-Encoding: identity`. The bug is downstream of the retry.

`read_response_capped` (`src/utils/safe_http.py:66`) enforces only an upper
bound. It raises `ResponseTooLargeError` above `max_bytes` and otherwise
returns whatever `iter_content` yielded, with no check that the body is
complete. On a response without `Content-Length`, a connection truncated
mid-body ends the iterator without raising, so a short read returns as a
successful fetch. The caller then does
`body.decode('utf-8', errors='replace')` and hands a partial document to a
tolerant parser, so the feed parses to a shorter episode list and the
circuit breaker records a success.

Net effect: episodes silently vanish from that feed on every refresh where
this happens, and nothing in the pipeline treats it as a failure.

### Change

- `read_response_capped` gains a completeness check. When the response
  declares `Content-Length` and the body is shorter, raise a new
  `IncompleteResponseError` rather than returning a short buffer. This
  helper is shared by every fetch path in the app, so the fix is not
  specific to RSS.
- `fetch_feed` and `fetch_feed_conditional` treat that error as a fetch
  failure: record a circuit-breaker failure and return None, which leaves
  the previously stored feed state intact instead of overwriting it with a
  truncated parse.
- An XML parse warning on a feed body promotes to a fetch failure rather
  than proceeding with a partial episode list. A feed that parsed clean on
  the previous refresh must not silently lose entries.

## G. Every short episode burns two failed CUDA attempts

### Evidence

Two transcriptions 100 seconds apart, each with the identical sequence:

```
19:02:39 [transcriber] CUDA OOM detected (attempt 1/3). Reducing batch size: 16 -> 8
19:02:42 [transcriber] CUDA OOM detected (attempt 2/3). Reducing batch size: 8 -> 4
19:04:18 [transcriber] CUDA OOM detected (attempt 1/3). Reducing batch size: 16 -> 8
19:04:20 [transcriber] CUDA OOM detected (attempt 2/3). Reducing batch size: 8 -> 4
```

Both start at 16 and settle at 4. Nothing is remembered between episodes.

### Root cause

`BATCH_SIZE_TIERS` (`src/transcriber.py:82`) picks a batch size from audio
duration alone: under 60 minutes gets 16. The OOM handler at
`src/transcriber.py:1707` halves and retries, which works, but the result is
discarded when the next episode starts. On a card where 16 never fits, every
short episode pays two OOM failures plus two `clear_cuda_cache` calls before
doing useful work.

The tiers encode an assumption about available VRAM that is not checked
against the device.

### Change

Persist the largest batch size that completed for the current device and
clamp the duration-derived tier to it, so the first episode pays
the OOM cost once and later episodes start at a size known to fit. Self-tunes
for any GPU rather than hardcoding a second set of tiers.

## H. Backup secret-exposure warning fires on the wrong condition

### Evidence

The application logs this about itself, once per scheduled backup:

```
03:31:23 [podcast.db_backup] db_backup: scheduled snapshot at
/app/data/backups/minuspod-backup.db is an unencrypted SQLite file
containing provider secrets (MINUSPOD_MASTER_PASSPHRASE does not apply to
scheduled backups): {... 'sizeBytes': 448131072, 'mode': 'overwrite' ...}
```

### The warning is wrong, and that is the actual bug

Tracing the code inverts this finding. The warning at
`src/db_backup_service.py:261` fires only when `MINUSPOD_MASTER_PASSPHRASE`
is set:

```python
if os.environ.get('MINUSPOD_MASTER_PASSPHRASE'):
    logger.warning('db_backup: scheduled snapshot at %s is an unencrypted '
                   'SQLite file containing provider secrets ...')
else:
    logger.info('db_backup: %s', summary)
```

But a passphrase being set is exactly the condition under which those
secrets are already encrypted. `Database.set_secret`
(`src/database/settings.py:759`) stores every key in `SECRET_SETTING_KEYS`
as `encrypt(self, plaintext)`, and `secrets_crypto.is_available()` is true
precisely when the passphrase is present. So with a passphrase configured,
the eight provider-secret rows in the snapshot hold `enc:v1:` ciphertext,
not API keys.

The file is an unencrypted SQLite database, which is true, but the sentence
"containing provider secrets" implies an exposure that is not there. What
does travel with the snapshot is the KDF salt, and the salt without the
passphrase does not recover a key.

Meanwhile the genuinely risky case is silent. With no passphrase configured
the secrets are plaintext in the live database and therefore plaintext in
the backup, and that path logs at INFO and says nothing about it. The
condition is backwards.

### Change

No whole-file backup encryption. It would buy nothing in the passphrase case
because the secrets are already ciphertext, and it is impossible in the
no-passphrase case because there is no key. It would also mean streaming
crypto over a 448 MB file and a new envelope format to read it back, which is
real risk for no gain.

Fix the condition and the wording instead:

- Warn when `secrets_crypto.count_plaintext_secrets(db) > 0`, which is the
  case where the snapshot really does contain readable API keys. Name the
  remedy: setting `MINUSPOD_MASTER_PASSPHRASE` migrates existing plaintext
  secrets to ciphertext.
- With a passphrase set, log at INFO and say what is true: provider secrets
  in the snapshot are encrypted, the KDF salt travels with the file, so the
  passphrase must be stored somewhere other than beside the backup.
- Either way log at most once per process rather than once per backup, so a
  nightly schedule does not reprint it forever.

No new crypto, no format change, no migration, and the restore path is
untouched.

## I. Podping node failures produce sustained log volume

Measured 44 to 66 warning lines per sample bucket across the full 22 hours,
from public Hive nodes failing in three ways: connection refused, SSL EOF,
and DNS resolution failure. Failover works and pings are not lost.

The cooldown already exists: `_node_failure`
(`src/podping_listener.py:235`) logs, rotates to the next node, and backs off
on a 5s/15s/60s capped schedule. Failover works, pings are not lost, and no
node is hammered.

So the fix is log level, not a cooldown. A single node failing while failover
succeeds is not actionable, and at the 60s backoff cap it prints roughly once
a minute per failing node, which is where the sustained volume comes from.

- First failure for a given node stays at WARNING, so a newly broken node is
  still visible.
- Repeat failures for that same node while it remains in backoff drop to
  DEBUG.
- Exhausting every node in the list, which is the actionable condition and is
  the one that actually loses pings, logs at ERROR. Today that case is
  indistinguishable from one node blipping.

`self._backoff_step` resets to 0 on success (`:256`) and is per-listener
rather than per-node, so tracking "which node last failed" needs a small
amount of new state rather than reusing the backoff counter.

## J. Log-level noise on routine external traffic

Two patterns logged at WARNING that are ordinary internet background noise
rather than problems with this install:

- `GET /upgrade 404` from an external scanner, paired with
  `[upgrade] Feed not found (no refresh-on-miss)`.
- `401 no auth key provided or is invalid` on cover-art requests from an
  external address, recurring, which is the expected response when feed auth
  is on and a client does not carry the key.

Both drop to INFO or DEBUG. A failed login attempt stays at WARNING, since
that one is worth seeing.

Worth confirming separately, not a code change here: if a podcast directory
is fetching cover art without the key, artwork will be missing in that
directory's listing. That is feed auth working as configured, but it is a
consequence worth knowing about.

## Field evidence for workstream C

The same 22 hours contain repeated confirmation that merged multi-ad blocks
are the live pattern-quality problem C addresses:

```
[podcast.textmatch] Skipping pattern creation: duration 404s exceeds max 120s (likely multi-ad contamination)
[podcast.textmatch] Skipping pattern creation: duration 377s exceeds max 120s (likely multi-ad contamination)
[podcast.textmatch] Skipping pattern creation: found 2 ad transitions - likely multi-ad contamination
```

Seven such skips in the window, at 194s, 214s, 226s, 284s, 287s, 377s, and
404s. The guard is doing its job, so no oversized pattern forms, but the
consequence is that no pattern forms at all for those ads. Split turns each
of them into usable single-sponsor patterns instead of a discard.

## UI design

The app already has a settled token system: semantic Tailwind colors
(`bg-card`, `border-border`, `text-muted-foreground`, `text-success`,
`text-warning`), light and dark variants on every surface, and established
components for stats cards, filter bars, pagination, and badges. That is the
pinned visual direction, so both new surfaces inherit it rather than
introducing a look of their own. Users move between Ad Review and Detected
Ads constantly, and a second visual language across that boundary would make
the app harder to learn.

### Detected Ads header

Two zones inside the existing stats-card container.

Lead figure is **total time cut**, set larger than the rest. It is the one
number that answers what the product did for you; detection count is a
means, not the outcome. Detections, distinct sponsors, and distinct podcasts
follow as smaller supporting figures.

The category breakdown is a badge-and-count list sorted by count
descending, not a chart. `SegmentCategoryBadge`
(`frontend/src/components/SegmentCategoryBadge.tsx:22`) renders every
category in one indigo tint today; the app has no per-category palette. A
stacked proportional bar would require inventing an 8-hue categorical
palette that exists nowhere else, so a category would read indigo as a badge
throughout the app and some other hue in this one bar. That is worse than
having no chart. Sorted counts beside the real badge give the mix without a
second color language.

If a per-category palette is ever wanted, that is a deliberate cross-app
change covering badges, filters, and any chart together, with the palette
validated for adjacent-pair colorblind separation. It does not get smuggled
in through this tab.

### Detected Ads filters and rows

The filter bar matches `AdReviewTab`'s existing one exactly: same
`flex-wrap` container, same label-and-select pairing, same responsive
widths, with one added Category select. Rows come from the extracted
`DetectionRows`, plus the `actionApplied` badge and the Split action.

### SplitMarkerModal

One new visual idea, everything else borrowed. The waveform, transport bar,
and zoom control are the existing `ad-editor` components, unchanged.

The signature element is the **piece strip**: a segmented horizontal bar
directly under the waveform, sharing its time window, one segment per
resulting ad. Each segment shows its sponsor and duration. Dragging a
divider resizes the two adjacent segments live, and clicking a segment
focuses its sponsor input. This makes "one block becomes N ads" literal, and
it is the only place the modal spends any novelty.

- A 2px surface gap between adjacent segments so boundaries read as
  boundaries rather than a color change.
- A segment under the 7.0s floor takes the `border-rose-500` treatment
  `AdReviewModal` already uses for `boundaryError`, and Save disables with a
  message naming which piece is too short and by how much. Errors say what
  happened and what to do, in the interface's voice.
- Copy keeps one verb through the flow. The button reads "Split into 3 ads"
  and the result says "Split into 3 ads", never "Submit" or "Apply".

### Accessibility

`ad-editor/Pin.tsx` sets `role="slider"` and `aria-valuenow` but has no
`tabIndex` and no key handler, so it is announced as a slider and cannot be
operated by keyboard. Extending it with the divider kind is the moment to
fix that: add `tabIndex`, arrow-key nudging with a coarser step on
Shift, and the missing `aria-valuemin` / `aria-valuemax`. This also repairs
the existing start and end pins.

Both surfaces hold the usual floor: responsive to mobile, visible keyboard
focus, and reduced motion respected.

### Copy review

Every new user-facing string, including filter labels, empty states, error
messages, and button text, gets a plain-language edit before commit. That
applies to the strings written during implementation as well as this spec.

## Testing

- Python: `PYTHONPATH=src .venv/bin/python -m pytest tests/ -v`, lint with
  `.venv/bin/ruff check src/ tests/`.
- Frontend: `npx tsc --noEmit`, `npm run lint`, `npm run test`.
- New unit coverage:
  - `filter_detections` with each category value and `none`.
  - `summarize_cut_detections` totals, including unset categories and
    duplicate sponsors.
  - `split-candidates` offset-to-timestamp mapping, including a span with
    no transition phrase, which must return zero candidates rather than
    erroring.
  - `split` correction validation: out-of-range points, points too close,
    a piece under 7.0s, and a marker whose bounds do not match.
  - Windowed chapter detection: window partitioning, per-window boundary
    targets, a failing window among successes, and every window empty.
  - Chapter tunable round trip: each key readable from the settings GET,
    writable via POST, rejected outside its range, and the two cross-field
    constraints rejected against effective rather than payload-only values.
  - `DRAFT_KEYS` in `StageTunablesSection` covers every key the section
    renders, so a future tunable cannot render without saving.
  - `bulk_upsert_discovered_episodes` raising rather than silently dropping
    when the immediate transaction cannot be acquired.
  - `read_response_capped` raising on a body shorter than a declared
    `Content-Length`, returning normally when no `Content-Length` is
    declared, and still raising above `max_bytes`. Plus a fetch-level test
    that a truncated body leaves stored feed state untouched instead of
    replacing it with a short episode list.
  - Batch-size clamping: the persisted ceiling is respected on the next
    call, a lower duration tier still wins over a higher ceiling, and a
    first run with no stored value behaves as today.
  - Backup warning condition: warns when plaintext secrets exist, logs at
    INFO when a passphrase is set, and emits at most once per process across
    repeated backups.
  - Podping log levels: first failure per node at WARNING, repeats at DEBUG,
    all-nodes-exhausted at ERROR.
- Manual: reprocess one long episode to confirm chapter density, and split
  one merged multi-sponsor marker to confirm N markers and N
  single-sponsor patterns.

## Not fixable in this repo

One log finding has no code change here. The log shipper tags INFO
transcriber lines as `level=error` because the word "error" appears in
transcript text, so podcast dialogue lands in error dashboards. The
collector config lives outside this repository. The application-side option
would be structured JSON log output, which is a larger change than this PR
should carry; raising it as its own issue is the right move.
