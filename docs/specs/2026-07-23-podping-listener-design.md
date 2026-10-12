# Podping listener design (2.77.0)

Date: 2026-07-23
Status: implemented in 2.77.0

## Goal

MinusPod optionally subscribes to Podping (the Podcasting 2.0 publish
notification bus on the Hive blockchain) so that feeds whose hosts send
podpings get picked up within seconds of publishing instead of waiting
for the next 15-minute RSS poll. Polling remains the completeness
guarantee; podping is an accelerator only.

Decisions made during brainstorming (user-approved):

- Accelerator only. The 15-minute polling loop is unchanged and remains
  the fallback for hosts that do not ping and for any missed blocks.
- In-process listener thread using raw JSON-RPC over `requests` against
  public Hive API nodes. No new pip dependencies, no sidecar container.
- Opt-in: new `podping_enabled` setting, default off.
- Empirical context: only some hosts are observed pinging. The feature is cheap,
  self-contained, and aligned with the Podcasting 2.0 spec MinusPod
  already documents; adoption grows over time and per-feed
  `last_podping_at` gives ground truth.

## Component: src/podping_listener.py

A leader-only daemon thread started in `main_app/__init__.py` next to
the RSS refresh and queue processor threads.

Loop (every iteration):

1. If `podping_enabled` is false, `shutdown_event.wait(30)` and
   continue. Toggle takes effect without restart.
2. Poll `condenser_api.get_dynamic_global_properties` on the current
   node for `head_block_number`.
3. Fetch each new block via `condenser_api.get_block`. If more than
   100 blocks behind (startup, node outage, long GC pause), jump to
   `head - 1` and log at INFO; polling covers the gap.
4. For each transaction operation of type `custom_json` where the op
   id is `podping` or starts with `pp_`:
   - Sender check: the op's `required_posting_auths` must intersect
     the allow-list (below). Otherwise skip.
   - Parse the JSON payload. Collect feed IRIs from `iris` (v1.x
     list), `urls` (v0.x list), or `url` (v0.x single). Ignore
     payloads that fail to parse.
   - `reason` filtering: act only on `update`/`live` reasons and on
     v0.x payloads with no reason field. Ignore other reasons.
5. Match IRIs against the local feed map (below). On a match:
   - Stamp `podcasts.last_podping_at` (ISO UTC).
   - If the feed was podping-refreshed within the last 300s, log and
     skip (cooldown).
   - Otherwise call `refresh_single_feed(slug)` and log at INFO
     (`[slug] Podping received (reason=update), refreshing feed`).
6. Sleep so the cadence is ~3s per iteration (Hive block time).

Node handling: `PODPING_NODES` constant vendored from the reference
watcher's list at implementation time (verified so far:
`https://api.hive.blog`, `https://api.openhive.network`,
`https://hived.emre.sh`; the implementer copies the current full list
from hive-watcher.py). On request failure or malformed
response: log WARNING, rotate to the next node, back off (5s, 15s,
60s cap). The thread must never raise out of its loop; a top-level
`except Exception` logs `logger.exception` and sleeps 60s.

Allow-list: fetched at startup and refreshed hourly via
`condenser_api.get_accounts [["podping"]]`, taking the posting
`account_auths` names plus `podping` itself. On fetch failure keep the
previous list; if never fetched, process no ops (fail closed) and
retry next iteration.

Feed map: dict of normalized `source_url` to slug, rebuilt at most
every 60s from the podcasts table. Normalization (applied to both
sides): lowercase scheme and host, strip a single trailing slash.
Everything else (path case, query) is preserved verbatim.

## Component: refresh_single_feed

Small extraction from `refresh_all_feeds` in `main_app/feeds.py`: the
per-podcast body (fetch RSS, detect new episodes, enqueue
auto-processing) becomes `refresh_single_feed(slug)`;
`refresh_all_feeds` iterates over it. Behavior of the 15-minute loop
is unchanged. The listener calls it directly on match.

## Settings

Two new registry entries in `src/database/settings.py`, exposed via
GET/PUT `/api/v1/settings` like every other SettingSpec:

- `podping_enabled` (bool, default false). UI: toggle in the existing
  Podcasting 2.0 section (`Podcasting20Section.tsx`) with a short
  description and a note that only some hosts send podpings.
- `rss_refresh_interval_minutes` (int, default 15, range 5-1440). The
  refresh loop reads it at the top of each cycle, so a change applies
  after the current wait. UI: number row in the Processing Queue
  section (`ProcessingQueueSection.tsx`). This was not previously
  tunable (hardcoded 900s); it pairs with podping for users who want
  to stretch polling on ping-covered feeds, and stands alone for
  everyone else.

No other podping knobs. Cadence is protocol-fixed (3s blocks), the
node list is code, the 300s cooldown and 100-block skip threshold are
constants.

## Database

Additive migration: `podcasts.last_podping_at TEXT NULL`. No data
loss, no backfill. Exposed in the feed API payload as
`lastPodpingAt`.

## UI

- Podcasting 2.0 settings section: the enable toggle.
- Processing queue settings section: refresh interval number row.
- Feed detail page: a muted "Last podping: <relative time>" line
  shown only when `lastPodpingAt` is set. This is the user-facing
  diagnostic for whether their host pings and their source URL
  matches.

## Docs

- `docs/podcasting-2.0.md`: new "Podping" section covering both sides:
  MinusPod consumes podpings (this feature) and does not emit them
  (MinusPod is a proxy, not a publisher; served feeds change only when
  upstream changes, which players discover from the upstream schedule
  anyway). Explain opt-in, how it works (Hive block stream, no account
  or keys needed, outbound HTTPS to public Hive nodes every ~3s),
  which classes of hosts ping, and the `last_podping_at` diagnostic.
- `docs/configuration.md`: entries for both settings.
- `docs/glossary.md`: "Podping" entry.
- `docs/how-it-works.md`: one paragraph in the feed refresh section
  pointing at podcasting-2.0.md.
- README feature list: one bullet.

## Testing

Unit tests (mocked HTTP; no live Hive calls in CI):

- Payload parsing: v0.x `url` string, v0.x `urls` list, v1.0/1.1
  `iris`; malformed JSON ignored; reason filtering.
- Op filtering: id regex (`podping`, `pp_*`), allow-list enforcement,
  fail-closed when allow-list never fetched.
- URL normalization and matching, including trailing slash and case.
- Cooldown: second ping within 300s stamps `last_podping_at` but does
  not refresh.
- Catch-up skip: more than 100 blocks behind jumps to head.
- Node rotation on request failure.
- `refresh_single_feed` extraction: `refresh_all_feeds` behavior
  unchanged (existing tests keep passing), single-feed call refreshes
  exactly one feed.
- Settings: registry round-trip and validation ranges for both new
  settings; refresh loop honors `rss_refresh_interval_minutes`.

## Non-goals

- No automatic polling relaxation when podping is healthy (rejected in
  brainstorming; the manual interval setting covers it).
- No emitting podpings for MinusPod's own served feeds.
- No block catch-up/replay beyond the 100-block window.
- No per-feed podping enable/disable.

## Version

2.77.0 (next minor after the 2.76.1 release), one PR, one build, per
the batching rule.
