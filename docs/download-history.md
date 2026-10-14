# Download History

The History page has a Downloads tab (`/ui/history?tab=downloads`) that lists each time a client downloaded episode audio from MinusPod.

## What is recorded

| Column | Meaning |
|---|---|
| Time | When the client last requested the episode |
| Episode, Podcast | What was downloaded |
| Client IP | The address the request came from, after the trusted-proxy setting (`MINUSPOD_TRUSTED_PROXY_COUNT`) is applied |
| Location | City, region and country when geo lookup is on; "Local network" for private addresses; "Unknown" when a lookup failed |
| Subscriber | The label of the subscriber key used, blank when the global feed key or no key was used |
| Client | The app name, the first product token of the user agent (the full string is in the tooltip) |
| Requests | Request bursts merged into the row; requests within 60 seconds of the last recorded one are not counted |

Only `GET` requests that serve audio are recorded. Requests for the same episode from the same IP and user agent within 15 minutes are merged into one row, so the range requests a podcast app sends while streaming count as one download. A request within 60 seconds of the row's last recorded request is absorbed without being counted or changing the time.

Filters: podcast, subscriber, a since/until date range, and a search over IP and user agent. Time, Podcast, Client IP, Location and Subscriber columns sort.

## Settings

Settings > Authenticated Feeds > Download history.

| Setting | Default | Notes |
|---|---|---|
| Record download history | On | Turn off to stop recording; existing rows stay until they expire |
| Look up client locations | Off | Sends public client IPs to ipwho.is over HTTPS |
| Keep history for (days) | 90 | 1 to 3650; older rows are deleted by the periodic cleanup |

## Row actions

Each row has an Actions menu.

- Block user agent: adds `^` plus the client's name to Settings > Security > JIT blocked user agents after a confirmation that shows the exact pattern (the whole user agent when it is under 40 characters). Blocked clients requesting an episode that has not been processed yet are redirected to the original audio instead of starting processing; local feeds, processed episodes, feed access and subscriber access are unaffected. Rows whose client already matches show a Blocked badge and the item is disabled.
- Revoke subscriber: revokes the subscriber feed that fetched the row after a confirmation. Rows fetched with the global feed key have no subscriber, so the item is absent. Revoked subscribers show a Revoked badge.

## Privacy

- Client IP addresses and user agent strings are stored in the MinusPod database for the retention period. Cached locations for IPs that no longer appear in any row are deleted with the periodic cleanup.
- Geo lookup is off by default. When on, public IPs are sent over HTTPS to the free ipwho.is service, which is a third party, one request per IP and at most 25 IPs per cleanup run. Private, loopback, link-local and reserved addresses are never sent.
- Results are cached per IP. A failed lookup is retried after 24 hours.
- Deleting a podcast removes its download rows. Revoking a subscriber key keeps the rows and their label; deleting the key record clears the label.

## API

`GET /api/v1/history/downloads` with `page`, `limit`, `sortBy` (`last_seen_at`, `podcast`, `client_ip`, `location`, `subscriber`), `sortDir`, `podcastSlug`, `subscriberKeyId`, `since`, `until` (both `YYYY-MM-DD`) and `search`. See the OpenAPI document for the response shape.

[< Docs index](README.md)
