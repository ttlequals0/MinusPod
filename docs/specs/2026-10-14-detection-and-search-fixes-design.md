# Detection and search fixes: design

Date: 2026-10-14
Status: implemented in 2.100.13.

## Context

Logs from a 2.100.11 instance showed seven defects. Each root cause was confirmed in the code:

| Symptom | Root cause |
|---|---|
| A local-feed reprocess showed "Downloading" although no download happened | `process_episode` published the `downloading` stage unconditionally; the transcribe stage decides later whether a download is needed. |
| Search results showed raw `<p>` tags | The search index stored the RSS description verbatim, and the snippet builder HTML-escapes it. |
| Other writers waited 9 to 23 s on the SQLite write lock during an index rebuild | A rebuild was about 360 back-to-back 50-row write transactions plus about 360 purge transactions, and the change journal was replayed only at swap time. |
| A break stinger stayed audible after the cut | The reviewer's cue-snap clamp floored an end edge at the cue start even when the cue was set to be removed with the ad. |
| Confirming the reviewer's trimmed span failed with "Adjusted bounds must lie within the reviewed span" | A held remainder carved out of a larger detection kept a reviewer proposal that did not overlap it, and the UI offered the button anyway. |
| One continuous 8 to 10 minute ad block was cut as six pieces with a gap left in | The detector drops any single LLM ad longer than a fixed 420 s constant, and the reviewer shift cap is 60 s. |
| Changing the category in a held marker's editor confirmed the marker | The standalone editor handled `reject` and treated every other submit as a confirm. |

The same release adds per-marker addressing-mode telemetry so the segment_ids experiment can be judged on outcomes.

## Goal

- Fix each root cause where it lives, without new workarounds.
- Keep every change behavior-neutral for installs that do not use the new settings.
- Record enough per marker to compare addressing modes on real corrections.

## Non-goals

- Raising the reviewer's per-edge shift cap or the validator's reject rules.
- Changing the transcript window size.
- Incremental search indexing on every write. The journal already exists; only its replay cadence changes.

## Decision: LLM window cap

### Per-call value, global tunable

The cap on one LLM-reported ad is a value passed to each detection call (`max_window_ad_seconds`) and threaded through `detect_ads`, `_run_detection_pass`, the worker kwargs including the lost-window sweep, and `_process_single_window`. No instance attribute holds it, so concurrent feeds with different caps cannot interfere.

The default moved from the `MAX_AD_DURATION_WINDOW` constant (420 s) to the global setting `max_llm_window_ad_seconds` (Settings > Ad Detection, "Longest single LLM detection"). The default stays 420 s, the range is 60 to 900 s (`MAX_LLM_WINDOW_AD_MIN`, `MAX_LLM_WINDOW_AD_MAX`). The PUT validator rejects values outside the range and non-finite numbers, and `resolve_max_llm_window_ad_seconds` clamps a hand-edited stored value on read.

### Per-feed derivation

`resolve_window_ad_cap(db, podcast_id)` returns the largest of:

1. the global `max_llm_window_ad_seconds`,
2. the feed's `max_ad_duration_override` (the hold cap),
3. the feed's `max_ad_duration_reject_override`.

Only the two feed values feed in. The global `max_ad_duration_seconds` reject threshold does not, because it defaults to 300 s and would otherwise hide any window setting below 300 s and raise the cap for every feed whenever it was raised. A feed with neither override gets the global window value.

`AdDetector._window_ad_cap(slug, override)` resolves it in the detector, so the pipeline detect stage, the re-detect route and the verification pass pass no cap of their own. An explicit `max_window_ad_seconds` argument still wins. The verification pass honors the same per-feed value, so a long block pass 1 missed is not dropped in pass 2.

## Decision: reviewer fixes

### Cue-snap clamp honors remove_with_ad

`cue_snap_edge_limit(record, edge)` in `src/ad_detector/cue_boundary_snap.py` is the single rule for the limit an edge stays on one side of:

- A kept cue stays outside the ad: a start edge may not move earlier than the cue end plus the gap, an end edge may not move later than the cue start minus the gap.
- A removed cue (`remove_with_ad` true) stays inside the ad, so the limit sits on the far side of the cue.
- A snap record with no `remove_with_ad` key comes from before 2.99.6 and means kept. A missing key must never read as removed.

`_cue_snap_limits` in `src/ad_reviewer.py` calls it for both edges, so a reviewer adjustment can no longer move an end back across a cue that is being cut.

### Stale proposals on held remainders

A held remainder (`pass2_hold_remainder`) is a piece carved out of a larger detection, and it inherits that detection's `reviewer_proposed_start` and `reviewer_proposed_end`. Three guards keep the inherited trim honest:

- Validator: `_clip_reviewer_proposal` drops the proposal when it does not overlap the piece and clamps it into the piece when it does.
- API: `_usable_reviewer_proposal` returns a proposal only when it overlaps the held span and stays within the reviewer's per-edge shift cap; `_proposal_overlaps` holds the overlap rule. When a stale proposal is the reason a trimmed confirm is out of bounds, the 400 names both spans ("Reviewer trim a-b does not overlap this held span c-d; confirm or reject the whole span").
- UI: the held-row editor passes `correctedBounds` only when `proposalOverlaps` is true, and the trimmed-confirm button is hidden otherwise. A stale proposal therefore never reaches the correction request.

### Held-marker editor recategorize

In the standalone `EpisodeDetail` editor, `onSubmit` handles `recategorize` first: it records the new category as a `recategorize` correction, leaves the editor open, and keeps the marker held. Only a `reject` or an explicit confirm falls through to those corrections. A category is a property of the span, not a verdict on it.

## Decision: addressing-mode telemetry

Per marker, `_process_single_window` stamps, with `setdefault` and only on LLM window ads:

| Field | Meaning |
|---|---|
| `addressing_mode` | `timestamps` or `segment_ids`, the mode that window was rendered under. Mode is drawn per pass, so it is stamped per ad. |
| `llm_start`, `llm_end` | The model's bounds after mapping and window clamp, before boundary snapping, cue snapping or review. |

Stamping happens before the window-dedup merge records members, so merged members keep their origin. Markers from before 2.100.13 carry none of the three fields and are skipped by every statistic below. Split pieces inherit the parent's `llm_*` values.

`ad_reviewer_log` and `pattern_corrections` gain an `addressing_mode` column (added with `_add_column_if_missing`; the corrections foreign-key rebuild DDL includes it). The reviewer verdict carries the mode from the ad into the log, and a correction copies it from the marker it corrects.

### Per-mode stats

| Endpoint | Block | Content |
|---|---|---|
| `GET /stats/reviewer` | `byMode.timestamps` and `byMode.segment_ids` | `reviews`, one count per verdict (`confirmed`, `adjusted`, `rejected`, `resurrected`, `inconclusive`, `failed`) and `avgBoundaryShiftSeconds`, the mean per-edge shift of `adjust` verdicts. Rows logged without a mode are excluded. |
| `GET /stats/addressing` | `byMode.<mode>` | `heldRate`, `approveRate`, `rejectRate` as fractions of the corrections that carry a mode, and `avgSnapDistance`. A mode with no data returns null for each. |

`avgSnapDistance` is the mean of `|start - llm_start| + |end - llm_end|` in seconds between the model's raw bounds and the stored marker. It includes every correction stage between the two (boundary snapping, cue snapping, reviewer adjustment, user edits), so it measures how far the stored marker ended up from what the model said, not only the snap step.

It is bounded so the stats request stays cheap: the 2000 most recent episodes processed in the last 90 days (`_SNAP_MAX_EPISODES`, `_SNAP_WINDOW_DAYS`), optionally narrowed to one feed, read from `ad_markers_json` with a cheap `LIKE '%"llm_start"%'` prefilter. The sum and count are accumulated while scanning.

## Decision: search index

### Plain-text storage and one-shot rebuild

The index stores plain text. Every indexing path (bulk rebuild and the single-row path) goes through shared row builders that apply `strip_html` to podcast and episode descriptions, so a description with markup is searchable and snippets show no tags. A one-shot migration rebuilds the index once on upgrade, because rows indexed before 2.100.13 hold raw HTML.

### Refresh cadence

| Operation | Cadence | Mechanism |
|---|---|---|
| Journal refresh (`refresh_search_index`) | Every 6 hours (`INDEX_REBUILD_INTERVAL_SECONDS` = 21600) | Applies `search_index_changes` to the live index in 50-key transactions, grouped by key, newest change per key, then deletes the applied journal rows. |
| Full rebuild (`rebuild_search_index`) | Weekly (`INDEX_FULL_REBUILD_INTERVAL_SECONDS` = 7 days) | Builds a shadow table, replays the journal into it, swaps it in. |

Both run from the maintenance tick and are deferred, up to twice the interval, while a processing slot is active. A failure backs the next attempt off instead of retrying on the next pass. The first tick after a restart runs a full rebuild because the in-process timestamps start at 0.

### Rowid deletes through MATCH

Removing indexed rows by `(content_type, content_id, podcast_slug)` used a column comparison, which FTS5 does not push down, so every delete scanned the whole table. `_delete_indexed_rows` now resolves rowids with one `MATCH` per chunk of up to 500 keys, a phrase query per key, then verifies all three columns on the returned rows (a phrase can match a longer id, and two podcasts can share an id) and deletes by `rowid IN (...)`. Ids with no alphanumeric character have no term to match and fall back to a row-value delete.

Measured on a seeded test index of 2000 episodes with 50,000-character bodies and 50 journaled keys, one delete chunk took 1.277 s with the column comparison and 0.005 s with the `MATCH` rowid lookup.

### Yield between writes

`_run_rebuild_write` retries on a lost write lock, and after each chunk write the rebuild sleeps `_REBUILD_YIELD_SECONDS` (20 ms) so a writer already waiting wins the next `BEGIN IMMEDIATE`. The pause comes after the write, so the last one lands before the swap transaction. The purge of the retired index table yields the same way. The podping listener and the processing queue no longer wait 10 to 20 s on the lock during a rebuild.

## Decision: preparing stage

`StatusService.start_job` already sets the stage to `preparing`. `process_episode` no longer overwrites it with `downloading`. The transcribe stage publishes `downloading` only on the branch that actually downloads, so a run that reuses a retained original or serves a local feed shows Preparing until transcription starts. The frontend stage label map already carries `preparing`, including the `preparing:2/5` pass-prefixed form.

## Alternatives considered

| Alternative | Why rejected |
|---|---|
| Raise the 420 s constant for everyone | A longer cap lets a hallucinated long span through on every feed. A per-feed lift keeps the default for shows that do not need it. |
| Let the global reject threshold feed the window cap | It defaults to 300 s, which would make any window value from 60 to 300 s a no-op and tie two unrelated settings together. |
| Raise the reviewer shift cap to cover long blocks | Fixes one symptom, loosens every adjustment. The loss was in the detector's drop rule. |
| Treat a missing `remove_with_ad` as removed | Reverses the meaning of every pre-2.99.6 record. |
| Hide the stale trim only in the UI | A direct API caller would still hit the 400. The validator and API guards remove the cause. |
| Strip HTML at query time | Pays the cost on every search and leaves the stored text, snippets and ranking wrong. |
| Incremental index updates on every write | Puts index maintenance on request paths. The journal replay already batches it. |
| One long rebuild transaction | Holds the write lock for the whole rebuild. Short chunks with a yield bound the wait. |
