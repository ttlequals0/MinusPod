# Download History

The History page has a Downloads tab (`/ui/history?tab=downloads`) that lists each time a client downloaded episode audio from MinusPod.

## What is recorded

| Column | Meaning |
|---|---|
| Time | When the client last requested the episode |
| Episode, Podcast | What was downloaded |
| Client IP | The address the request came from, after the trusted-proxy setting (`MINUSPOD_TRUSTED_PROXY_COUNT`) is applied |
| Location | City, region and country when geo lookup is on; "Local network" for private addresses; "Unknown" when a lookup failed or the provider could not resolve the address |
| Subscriber | The label of the subscriber key used, blank when the global feed key or no key was used |
| Client | The app name, the first product token of the user agent (the full string is in the tooltip) |
| Requests | Request bursts merged into the row; requests within 60 seconds of the last recorded one are not counted |

Only `GET` requests that serve audio are recorded. Requests for the same episode from the same IP and user agent within 15 minutes are merged into one row, so the range requests a podcast app sends while streaming count as one download. A request within 60 seconds of the row's last recorded request is absorbed without being counted or changing the time.

Filters: podcast, subscriber, a since/until date range, and a search over IP and user agent. Time, Podcast, Client IP, Location and Subscriber columns sort.

## Settings

Settings > Data & Security > Authenticated Feeds > Download history.

| Setting | Default | Notes |
|---|---|---|
| Record download history | On | Turn off to stop recording; existing rows stay until they expire |
| Look up client locations | Off | Sends public client IPs to ipwho.is over HTTPS |
| Keep history for (days) | 90 | 1 to 3650; older rows are deleted by the periodic cleanup |

API: `downloadHistoryEnabled`, `downloadGeoLookupEnabled` and `downloadHistoryRetentionDays` on `PUT /api/v1/settings/ad-detection`, read back from `GET /api/v1/settings`.

## Row actions

Each row has an Actions menu.

- View user agent: shows the full raw user agent with a copy button.
- View request: shows the most recent request merged into the row (method, path and query with any `key` value replaced by `[redacted]`) and its Range, Accept, Accept-Encoding, Referer, If-Range and If-None-Match headers when present. Rows recorded before this was captured show it disabled.
- Block user agent: adds `^` plus the client's product token (the name before the first `/` or `(`) to Settings > Security > JIT blocked user agents after a confirmation that shows the exact pattern, so the block survives app version bumps. Blocked clients requesting an episode that has not been processed yet are redirected to the original audio instead of starting processing; local feeds, processed episodes, feed access and subscriber access are unaffected. Rows whose client already matches show a Blocked badge and the item is disabled.
- Revoke subscriber: revokes the subscriber feed that fetched the row after a confirmation. Rows fetched with the global feed key have no subscriber, so the item is absent. Revoked subscribers show a Revoked badge.

## Privacy

- Client IP addresses, user agent strings and the latest request's path and allowlisted headers are stored in the MinusPod database for the retention period. The `key` query value is never stored, including in a Referer. The request line is capped at 500 characters, each header value at 300 and all headers together at 2048 bytes. Cached locations for IPs that no longer appear in any row are deleted with the periodic cleanup. Expired rows are pruned once every 24 hours, so rows can outlive the retention period by up to a day.
- Geo lookup is off by default. When on, public IPs are sent over HTTPS to the free ipwho.is service, which is a third party, one request per IP and at most 25 IPs per run. Lookups run within about five minutes of a download, and immediately when the setting is turned on. Private, loopback, link-local, reserved and shared (100.64.0.0/10, used by Tailscale and carrier NAT) addresses, and IPv4-mapped forms of them, are never sent. The first network error or rate-limit answer from the provider ends that run, and the remaining IPs wait for the next one.
- Results are cached per IP. A lookup that failed (unreadable provider reply) is retried after 24 hours. An address the provider permanently rejects is marked unresolvable and not retried. Lookups run on their own short background thread so a slow provider does not delay feed refresh.
- Recording adds no write to the audio response for a burst: a request within 60 seconds of the row's last counted request is detected with a read and skipped.
- Deleting a podcast removes its download rows. Revoking a subscriber key keeps the rows and their label; deleting the key record clears the label.

## API

`GET /api/v1/history/downloads` with `page`, `limit`, `sortBy` (`last_seen_at`, `podcast`, `client_ip`, `location`, `subscriber`), `sortDir`, `podcastSlug`, `subscriberKeyId`, `since`, `until` (both `YYYY-MM-DD`) and `search`. Each row carries the episode and podcast, client IP, user agent, location fields and `geoStatus`, `subscriberLabel`, `subscriberKeyId`, `subscriberRevoked`, first and last seen times, `requestCount`, `lastRequestLine`, `lastRequestHeaders` and `userAgentBlocked` (true when the user agent already matches the JIT blocked list). Revoke a subscriber with `DELETE /api/v1/feeds/{slug}/subscriber-keys/{id}` and list all keys with `GET /api/v1/feeds/subscriber-keys`. See the OpenAPI document for the response shape.

[< Docs index](README.md)
