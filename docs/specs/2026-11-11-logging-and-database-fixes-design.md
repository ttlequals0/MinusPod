# Logging, database upkeep and upstream fetch fixes: design

Ships in 2.102.0. A set of smaller problems seen in a day of logs from a
running instance. Each has a cause in the code below.

## Goal

Credentials never reach the logs. Log lines carry a level a shipper can read.
Recurring lines that describe a steady state stop repeating. The database
write-ahead log stays bounded.

## Non-goals

- Shared rate-limit counters. See "Rate limits" below.
- A log shipper configuration. The repo documents what a shipper needs and
  ships none.
- Keeping a transcription model loaded longer. The idle unload frees GPU
  memory on purpose.

## Decision: credentials in log lines

A failed upstream fetch logged its URL with the query string, including a
`key=` value. The log sites already pass the URL through `safe_url_for_log`.
The leak is the exception text appended after it: a `requests` HTTP error
embeds the full URL.

- One helper in `src/utils/http.py` removes the query string from any URL in a
  piece of text. It replaces `_scrub_query_strings` in `src/main_app/feeds.py`,
  which does the same for feed refresh errors only.
- A logging filter on the console handler applies it to every record, so a
  site that formats an exception into its message is covered without being
  found first.
- The same helper is applied before an error message is saved to the database
  or returned by the API. The audio availability check builds one such message
  from an exception today.

## Decision: upstream chapters and transcript fetches

The audio download retries with the feed's user agent when a host answers 403,
and records which one worked in that run's download outcome. The upstream chapters fetch
(`src/upstream_chapters.py`) and the upstream transcript fetch
(`src/transcript_differential.py`) send the download user agent once and give
up on 403.

Both use the user agent the audio download settled on for that run when there
is one, and otherwise retry once with the feed's user agent on 403. The
download outcome is passed to their three callers (`publish.py`, `signals.py`
and `passthrough_assets.py` under `src/main_app/pipeline/`), which pass the
download user agent directly today.

## Decision: write-ahead log size

Connections set `wal_autocheckpoint` and no `journal_size_limit`, and the only
explicit checkpoint is passive. A WAL file that grows once never shrinks. One
instance showed a 499 MB WAL beside a 3.0 GB database.

- Each connection sets `journal_size_limit` to 64 MB.
- The leader's maintenance tick runs a truncating checkpoint when no episode
  is processing and no backup is running. A checkpoint that finds a reader in
  the way gives up and tries on the next tick.
- `POST /api/v1/system/database/checkpoint` accepts a mode, passive by
  default, with truncate as an option.

The search index is rebuilt in full on every start. The time of the last full
rebuild is kept in memory (`_INDEX_STATE` in `src/main_app/background.py`), so
the first maintenance tick after a restart always finds one due. That rewrite
is the suspected source of the WAL growth. The time of the last full rebuild
is stored in the database, so a restart does not trigger one and the weekly
full rebuild keeps its schedule.

A second finding comes from the same code: a write transaction held for 5.5 s
at startup, opened by a `DROP`. Retired index tables are emptied in short
transactions before they are dropped. Stale shadow tables are dropped whole
(`_drop_stale_shadows` in `src/database/search.py`). They get the same
chunked purge.

## Decision: Recents feed query

One statement took 20.2 s: the Recents feed query, which filters processed
episodes by publish date and selects every episode column, including full show
notes. No index covers status with publish date.

The builder confirms the plan with `EXPLAIN QUERY PLAN` on a copy of a large
database, then adds the index the plan calls for through the existing
performance index list. The query selects the columns the feed needs in place
of `e.*` if that is what the plan shows to be slow. An index goes in only if
the plan shows it is used.

## Decision: log format and level

`LOG_FORMAT=json` gives each line a `level` field. Two gaps remain:

- gunicorn's own lines (access log, worker start and exit) stay plain text
  when the format is JSON. They follow `LOG_FORMAT` too. The formatters move
  from `src/main_app/__init__.py` to a small module that `gunicorn.conf.py`
  can import without loading the app, and the existing `RedactingLogger`
  applies them.
- Neither `LOG_FORMAT` nor `LOG_LEVEL` is in a compose file. Both compose
  files pass them through with `text` and `INFO` as defaults, and
  `.env.example` and `docs/environment-variables.md` list them.

The text format is unchanged. `docs/` says that a log shipper should read the
`level` field from JSON lines, because guessing the level from words in a
text line mislabels any line that contains "error".

## Decision: quieter lines

| Line | Today | Change |
|---|---|---|
| GPU selection | The chosen device is never logged | One INFO line at first use: index, name, PCI bus id, total memory |
| Rate limits per worker | WARNING at startup | INFO. See below |
| "Suppressed N upstream item(s)" | WARNING on every feed rebuild | INFO, and only when the count for that feed changes |
| "Skipping artwork retry" | DEBUG on every refresh during a 24 hour backoff | Once per backoff window per URL |
| `filelock` | About 20 DEBUG lines per model load | Logger set to WARNING |
| onnxruntime session-creation warnings (Memcpy and similar) | On every model load, from the native logger | Default logger severity set before the session is created. The device discovery warning printed when onnxruntime is first imported cannot be silenced and stays |

## Decision: rate limits

With more than one worker and in-memory counters, each worker counts on its
own, so the general API limits are about the worker count times the configured
value. This is accepted. The failed-login lockout is stored in the database
and is exact across workers. The startup line stays, at INFO, and still names
`RATE_LIMIT_STORAGE_URI` for an operator who wants exact limits.

## Decision: model load

Each Parakeet load used to check the model files over the network before
loading, which added a lock round trip and held the load lock longer. Once the
files are present, a load now reads the local copy and makes no network call.

At most once every 7 days a load starts a background check for a newer model
revision. The job that started it never waits for it, and a failed lookup does
not fail the job. A changed revision downloads into a staging directory and
replaces the local copy only when every file is present, so a failed or partial
download leaves the local copy untouched. The new revision is used from the
next load. `docs/transcription.md` describes the cases where updates are off.

One load stalled other threads for 5.4 s while the session was created. That
time is inside onnxruntime, so skipping the network check shortens the stall
and does not remove it.

## Tests

- Redaction: a record whose message contains a URL with a query string is
  logged without it, through both formatters. A stored error message has none.
- Fetchers: a 403 followed by a 200 on the feed user agent succeeds for
  chapters and transcript, and a run's settled user agent is used first.
- WAL: the limit is set on a new connection, the tick checkpoints when idle
  and skips when busy, and the route accepts both modes.
- Search index: a restart inside the weekly period does not rebuild, and a
  stale shadow table is purged in chunks.
- Recents: the query uses the new index, if one is added, and the feed output
  is byte-identical before and after.
- Logging: gunicorn lines are JSON under `LOG_FORMAT=json`, the GPU line
  appears once, and the suppressed-items line appears once for an unchanged
  count.
- Model load: no network call when the files exist.

## Check after deploy

On an instance running the release with `LOG_FORMAT=json`: no query string in
any logged URL, every line parses as JSON, the WAL is under its limit after an
idle period, and the Recents query is under a second.
