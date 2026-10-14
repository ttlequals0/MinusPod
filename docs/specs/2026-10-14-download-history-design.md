# Download history: design

Date: 2026-10-14
Status: implemented in 2.100.13.

## Context

MinusPod serves processed audio to podcast apps but kept no record of who fetched it. An operator who shares feeds with other people, or who exposes the server to the internet, could not tell which client downloaded which episode, whether a subscriber key was in use, or which app was hitting the server. Podcast apps also send several range requests per listen, so a naive per-request log is mostly noise.

## Goal

- One row per real download, with time, client IP, optional location, subscriber label and client app.
- No measurable cost on the audio serve path, including for range-request bursts.
- Operator actions from a row: inspect the request, block the client app, revoke the subscriber.
- Bounded storage and a clear privacy stance.

## Non-goals

- Bytes transferred, completion, or listen-through. A row says a client asked for the episode, not that it finished.
- Counting `HEAD` requests, 404s, or 410s.
- Per-request logging. The request log already covers that and omits query strings.
- Exact counts under bursts. Requests inside the burst window are deliberately not counted.

## Decision: where and what to record

### Recording on the audio serve route

`_record_download` in `src/main_app/routes.py` runs on the three paths that hand audio to a client: the published processed file, the processed file during a reprocess window, and the retained original of a local feed. It returns at once for any method other than `GET` and when `download_history_enabled` is off, and it swallows every exception, so history can never fail a download.

The subscriber key id comes from the feed-auth decorator through `g.subscriber_key_id`. A request made with the global feed key or no key records none.

### Dedupe and burst window

A row is keyed by podcast, episode, client IP and user agent. `record_download` in `src/database/downloads.py` works in this order:

1. Read the podcast and the most recent row for the key on the plain read connection, with no transaction.
2. If that row exists and was last seen under 60 seconds ago (`BURST_WINDOW`), return. No write happens, no lock is taken, and the request is not counted.
3. Otherwise open one `BEGIN IMMEDIATE` transaction, run the dedupe read again under the write lock, then either bump the row (`request_count + 1`, new `last_seen_at`, new request capture) or insert one.

A repeat is merged when the matching row was last seen within 15 minutes (`DEDUPE_WINDOW`). The second read under the lock exists because two apps routinely open a probe range and the main range together. Both can pass step 1 with no row, and without the re-check they would insert two rows for one download.

`request_count` therefore counts request bursts, not requests. The column header and the docs say so.

### Schema

| Table | Columns | Notes |
|---|---|---|
| `episode_downloads` | `id`, `podcast_id`, `episode_id`, `subscriber_key_id`, `client_ip`, `user_agent`, `first_seen_at`, `last_seen_at`, `request_count`, `last_request_line`, `last_request_headers` | `user_agent` truncated to 200 characters. `subscriber_key_id` is `ON DELETE SET NULL`. Deleting a podcast removes its rows. |
| `ip_geo_cache` | `ip`, `country`, `region`, `city`, `looked_up_at`, `status` | `status` is `ok`, `private`, `failed` or `unresolvable`. One row per IP, shared across episodes. |

Indexes cover the dedupe lookup, `client_ip` (for the geo pending query and the orphan sweep), `(podcast_id, last_seen_at)` (podcast filter) and `subscriber_key_id` (subscriber join). Sorting by podcast, location or subscriber is an expression ORDER BY that SQLite resolves with a filesort over the rows the page's filters select; the retention window bounds that set. The tables are in `TABLE_DDL`, the new-tables path for existing databases, and the fresh-install DDL. `last_request_line` and `last_request_headers` were added with `_add_column_if_missing`, so no existing data is touched.

### Request capture

`capture_request` stores the last request merged into a row:

- The request line is `METHOD path?query` with the value of any `key` query parameter replaced by `[redacted]`, truncated to 500 characters.
- Only these headers are stored, when present, each truncated to 300 characters: `Range`, `Accept`, `Accept-Encoding`, `Referer`, `If-Range`, `If-None-Match`. They are stored as a JSON object.
- Cookies, `Authorization` and every other header are never read.
- Capture is written on insert and on a bump. The under-60-seconds fast path writes nothing, so the stored request is the last counted one.
- Rows recorded before 2.100.13 have no capture, and the UI disables View request for them.

## Decision: geo lookup

- Provider: `https://ipwho.is/{ip}?fields=success,country,region,city`, keyless, one GET per IP, 5 second timeout.
- Off by default (`download_geo_lookup_enabled`). Turning it on is the operator's consent to send public client IPs to a third party.
- `is_private` unwraps IPv4-mapped IPv6 addresses and then treats only addresses where `ipaddress` reports `is_global` as public. Private, loopback, link-local, reserved and shared address space (100.64.0.0/10, which Tailscale and carrier NAT use) are stored as `private`, shown as "Local network", and never sent.
- `run_geo_lookup` is started by the maintenance tick (`run_cleanup`) on a short daemon thread (`download-geo`), so a slow provider never stalls feed refresh; a tick skips the start while the previous thread is alive. It looks up at most 25 public IPs per tick (`LOOKUPS_PER_TICK`) and writes all results in one transaction (`store_geo_many`).
- The first transport error or HTTP 429 stops the batch. The IPs not reached stay pending, and no row is written for the one that errored, so an outage costs one 5 second timeout per tick, not 25.
- A 4xx other than 429, or `success: false`, is stored as `unresolvable` and never retried. A non-JSON 2xx body is stored as `failed` for that IP only and the batch continues; a `failed` row is retried after 24 hours (`GEO_RETRY_AFTER`).
- `pending_geo_ips` reads at most 100 distinct IPs per tick (`GEO_BATCH_SIZE`).

## Decision: retention and pruning

- `download_history_retention_days`: default 90, range 1 to 3650, clamped on read by `resolve_download_history_retention_days`.
- The prune runs from the same maintenance tick, throttled to once per 24 hours (`DOWNLOAD_PRUNE_INTERVAL_SECONDS`) with an in-process timestamp, so rows can outlive the setting by up to a day. The timestamp advances only after a successful prune.
- Geo and prune sit in separate `try` blocks so a geo failure cannot skip the prune.

### Geo-cache pruning

In the same transaction as the row delete, and only when rows were deleted, `ip_geo_cache` rows whose IP no longer appears in `episode_downloads` are removed. A client IP therefore does not outlive its last download row.

## Decision: row actions

Each Downloads row has an Actions menu on the History page.

| Action | Behavior |
|---|---|
| View user agent | Full raw user agent with a copy button. |
| View request | The stored request line and allowlisted headers. Disabled with "No request captured" on older rows. |
| Block user agent | Appends `^` plus the first product token of the user agent (the text before the first `/` or `(`) to `jit_blocked_user_agents` after a confirmation that shows the exact pattern. The anchor keeps the block valid across app version bumps. Disabled for an empty user agent and for rows the server marks `userAgentBlocked`. |
| Revoke subscriber | Revokes the subscriber feed that fetched the row after a confirmation. Absent for rows fetched with the global key. Revoked rows show a Revoked badge. |

Details that tests can cite:

- `userAgentBlocked` is computed server-side in `GET /history/downloads` with the same `user_agent_is_jit_blocked` the serve path uses, resolving the list once per request. The client does not reimplement the matcher.
- Block fetches the current setting fresh, appends only when the pattern is absent, and skips the PUT when it is present, so a stale cached list is never written back.
- A JIT-blocked client asking for an unprocessed episode on a non-local feed is redirected to the original audio instead of starting processing. Processed episodes, local feeds, feed access and subscriber access are unaffected.
- The history payload carries `subscriberKeyId` and `subscriberRevoked`, so the menu needs no second request. Revoke reuses the same mutation as the feed Security panel, and both invalidate the same query keys.

## Decision: privacy stance

- Stored per row: client IP, user agent, subscriber key id, and the last request line and allowlisted headers. The `key` value is never stored.
- Client IP honors `MINUSPOD_TRUSTED_PROXY_COUNT` through `client_ip()`.
- Everything stays in the MinusPod database unless the operator enables geo lookup, which sends public IPs only, to one named provider, over HTTPS.
- Recording can be turned off. Existing rows then age out under the retention setting.
- Revoking a subscriber key keeps its rows and label. Deleting the key record clears the label.

## Decision: subscriber feed management additions

The Revoke action needs an operator to find and manage subscriber feeds without opening each podcast.

- `GET /api/v1/feeds/subscriber-keys` returns every subscriber key across all feeds with `id`, `label`, `createdAt`, `lastUsedAt`, `revokedAt`, `feedSlug` and `feedTitle`, sorted by feed title, label, then creation time. It never returns a secret. `feedTitle` falls back to the slug when a feed has no title.
- Settings > Authenticated feeds shows that list as a table and carries the download-history settings. The podcast picker there is sorted alphabetically.
- Each feed's settings page gains a Security section to create and revoke subscriber feeds for that podcast. It uses the same per-feed endpoints (`/feeds/{slug}/subscriber-keys`) as the global table, and a create or revoke in either place refreshes both.
- The secret and feed URL still appear only in the create response.

## Alternatives considered

| Alternative | Why rejected |
|---|---|
| ip-api.com | Its free tier is HTTP only, which would send client IPs in clear text. ipwho.is is keyless and serves HTTPS. |
| In-memory queue that flushes writes later | Loses rows on a restart or crash, adds a second writer thread, and still needs the dedupe read. The read-first path already removes the writes a burst would queue. |
| A process-wide write lock around recording | Serializes audio requests on a lock the serve path does not otherwise need. SQLite's immediate transaction plus a re-read under it gives the same correctness for the rare first request. |
| Count every request | Turns one stream into dozens of rows and a write per range request on the hot path. |
| Client-side blocked-agent matching | Duplicates the server matcher and drifts from it. The server computes the flag. |
