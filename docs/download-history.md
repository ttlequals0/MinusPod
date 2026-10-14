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
| Requests | How many requests were merged into the row |

Only `GET` requests that serve audio are recorded. Requests for the same episode from the same IP and user agent within 15 minutes are merged into one row, so the range requests a podcast app sends while streaming count as one download.

Filters: podcast, subscriber, a since/until date range, and a search over IP and user agent. Time, Podcast, Client IP, Location and Subscriber columns sort.

## Settings

Settings > Authenticated Feeds > Download history.

| Setting | Default | Notes |
|---|---|---|
| Record download history | On | Turn off to stop recording; existing rows stay until they expire |
| Look up client locations | Off | Sends public client IPs to ipwho.is over HTTPS |
| Keep history for (days) | 90 | 1 to 3650; older rows are deleted by the periodic cleanup |

## Privacy

- Client IP addresses and user agent strings are stored in the MinusPod database for the retention period.
- Geo lookup is off by default. When on, public IPs are sent over HTTPS to the free ipwho.is service, which is a third party, one request per IP and at most 25 IPs per cleanup run. Private, loopback, link-local and reserved addresses are never sent.
- Results are cached per IP. A failed lookup is retried after 24 hours.
- Deleting a podcast removes its download rows. Revoking or deleting a subscriber key keeps the rows and clears the label.

## API

`GET /api/v1/history/downloads` with `page`, `limit`, `sortBy` (`last_seen_at`, `podcast`, `client_ip`, `location`, `subscriber`), `sortDir`, `podcastSlug`, `subscriberKeyId`, `since`, `until` (both `YYYY-MM-DD`) and `search`. See the OpenAPI document for the response shape.

[< Docs index](README.md)
