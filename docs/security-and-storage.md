# Security, Storage & Custom Assets

[< Docs index](README.md) | [Project README](../README.md)

---

## Contents

- [Remote access and security](#remote-access-security)
- [Authenticated feeds](#authenticated-feeds-optional)
- [Provider admission controls](#provider-admission-controls)
- [Data storage](#data-storage)
- [Database backups](#database-backup-sensitivity)
- [Restore and passphrase rotation](#restore-and-passphrase-rotation)
- [Custom assets](#custom-assets-optional)

## Remote access / security

The docker-compose includes an optional Cloudflare tunnel service for secure remote access without port forwarding:

1. Create a tunnel at [Cloudflare Zero Trust](https://one.dash.cloudflare.com/)
2. Add `TUNNEL_TOKEN` to your `.env` file
3. Configure the tunnel to point to `http://minuspod:8000`

### Before enabling the tunnel profile

The tunnel exposes the admin interface to the public internet. Without all of these set, anyone who reaches the tunnel URL can hit unauthenticated paths and attempt to log in:

1. Set a password via Settings > Security.
2. `SESSION_COOKIE_SECURE=true` (the default).
3. `MINUSPOD_MASTER_PASSPHRASE` set so provider API keys are encrypted at rest.
4. Cloudflare WAF rule blocking `/ui` and `/api` (see below). The docs and OpenAPI spec live under `/api/v1/docs` and `/api/v1/openapi.yaml`, so they're already covered by the `/api` block.
5. `MINUSPOD_TRUSTED_PROXY_COUNT=1` so login lockout keys on the real client IP, not the tunnel loopback.

### Client IP for login lockout

The login lockout feature (5 fails / 15 min / 15 min block) keys on `request.remote_addr`. Depending on how traffic reaches the container, that address may or may not be the real client:

- Direct exposure (no proxy, ports published): `remote_addr` is the client. No config needed.
- Docker with published ports and no reverse proxy: `remote_addr` is the Docker bridge gateway; lockout will not fire. A startup WARN surfaces this. Deploy behind a proxy or switch to `network_mode: host`.
- Behind Cloudflare, nginx, Traefik, or cloudflared: set `MINUSPOD_TRUSTED_PROXY_COUNT=1`. Cloudflare sets `X-Forwarded-For` automatically.
- Multi-proxy chain (e.g., Cloudflare -> nginx -> MinusPod): set the count to the number of proxies you actually trust. Setting it too high lets an attacker spoof their client IP by prepending entries to `X-Forwarded-For`.

**What happens if you leave `MINUSPOD_TRUSTED_PROXY_COUNT=0` on a proxy-fronted deployment:**

1. **Login lockout never fires.** Every failed login appears to come from the proxy's IP, which is private or loopback (Cloudflare tunnel loopback, Docker bridge gateway, nginx on `127.0.0.1`, ...). The lockout excludes private IPs on purpose so NAT neighbors can't DoS each other, so it never triggers. Attackers can brute-force with no rate limit.
2. **Per-IP rate limits degrade to per-proxy.** `POST /feeds` (3/min), `POST /system/cleanup` (1/h), `DELETE /system/queue` (6/h), and the rest all key on the proxy as one client. One user can exhaust them for everyone; an attacker can't.
3. **Audit logs carry the wrong IP.** Every `[ip]` bracket in the access log is the proxy hop. Forensics are much harder.
4. **Auth-failure webhooks carry the wrong IP** in the `clientIp` field, so any Auth Failure alerting points at the proxy.

Startup logs a WARN (`Running in a container without MINUSPOD_TRUSTED_PROXY_COUNT set ...`) when the variable is unset. Treat the WARN as load-bearing: if you're behind a reverse proxy and it's still firing after a deploy, your lockout and rate limits are not working.

### Security recommendations

Operator checklist:

- Serve over HTTPS (`SESSION_COOKIE_SECURE=true` is the default).
- `MINUSPOD_TRUSTED_PROXY_COUNT=1` if behind a reverse proxy.
- `MINUSPOD_MASTER_PASSPHRASE` set so provider keys encrypt at rest. The
  passphrase encrypts stored API keys; it is not a login credential and does not
  restrict access on its own. The instance stays open until a login password is
  also set.
- Set a password in Settings > Security. **Without one the instance is fully open**: anyone with network access can read everything, change settings, delete feeds, and download a full database backup over the API. The password is the only gate on the API. With `MINUSPOD_MASTER_PASSPHRASE` unset, that backup also carries the session-signing key and provider keys in plaintext.
- `MINUSPOD_ENABLE_HSTS=true` once the deployment is HTTPS-only.
- WAF block on `/ui` and `/api`. Public feed paths must stay reachable: `/<slug>`, `/episodes/<slug>/<episode>.mp3`, `.vtt`, `/chapters.json`, and `/api/v1/feeds/<slug>/artwork`.

MinusPod ships the rest by default: CSRF, login lockout, SSRF guards, artwork magic-number validation, XXE defense, baseline security headers, non-root container, rate limits on destructive endpoints. See [`CHANGELOG.md`](../CHANGELOG.md) for the full list.

Compose requires a password before normal API use. Initial loopback setup needs no token; remote setup needs `MINUSPOD_SETUP_TOKEN` in the `X-MinusPod-Setup-Token` header. Later changes use the current password instead of CSRF. Setting or changing a password revokes older sessions and signs the caller into a new one. Removing it revokes every session, including the caller's.

**Cloudflare WAF example.** Allow only Pocket Casts on the feed host, block admin paths:

```
(http.request.full_uri wildcard r"http*://feed.example.com/*" and not http.user_agent wildcard "*Pocket*Casts*") or starts_with(http.request.uri.path, "/ui") or starts_with(http.request.uri.path, "/api")
```

Swap the User-Agent pattern for your app (`*Overcast*`, `*Castro*`, `*AntennaPod*`, ...).

### Outbound request safety

Every outbound fetch MinusPod makes itself (RSS sources, enclosures, artwork,
webhooks) runs through one SSRF-checked path. Operator-typed targets may point
at loopback or LAN addresses; URLs taken out of feed content may not, and cloud
metadata and link-local addresses are refused at both tiers. Redirects are
rechecked on every hop, HTTPS to HTTP downgrades are refused, and IPv4-mapped
IPv6 addresses are normalized before policy checks.

The resolved address is also pinned. A hostname is resolved once per hop, every
returned address is checked, and the connection goes to one of those addresses,
so a DNS record that answers with a public address for the check and a private
one a moment later cannot reach an internal service. When a connect fails, the
remaining validated addresses are tried in turn, each at most once, so a
multi-homed host stays reachable. The request URL, the `Host`
header, SNI, and certificate verification all stay on the original hostname, so
TLS trust is unchanged. If an outbound HTTP proxy is configured, the proxy
resolves the name itself and the pin does not apply. Set
`SSRF_IP_PINNING=false` to turn the pin off while isolating a fetch failure.

LLM completion traffic is the exception. The provider SDKs do their own HTTP,
so a configured base URL is checked when you save it and on the calls MinusPod
makes directly, but the SDK's own connections are not pinned. Point base URLs
at hosts you control.

### Rate limiting storage

The default `memory-threadsafe://` backend tracks limits inside each worker. With two workers, a client can receive up to twice a declared limit. Set `RATE_LIMIT_STORAGE_URI=redis://redis:6379` and provide Redis when the limit must apply across every worker or host. Validate Redis connectivity before exposing the service. Keep two or more application workers because long refresh requests can occupy one worker.

### Request correlation

Every response carries an `X-Request-ID` header. If you supply one on the request (up to 128 chars), it's preserved; otherwise a 16-char hex value is generated. When reporting a bug, including the `X-Request-ID` from the affected response makes log lookup one `grep` instead of a guessing game. Aggregated log viewers can filter by the `request_id` field on the JSON log records.

### Authenticated feeds (optional)

By default the feed URLs are open: anyone who learns them can read your RSS and download episodes, and a request for an unprocessed episode kicks off transcription. If your server is reachable from the internet, that can get expensive.

Settings > Data & Security > Authenticated Feeds locks this down. Existing global keys remain valid until you rotate them. Security > Feed subscriber keys can also create a credential scoped to one feed, label it for a device or subscriber, and revoke it without changing any other subscription.

- RSS, MP3, transcript, chapter, and artwork URLs carry `?key=<credential>`. Global credentials are 64 hex characters. Scoped credentials use a 16-character key ID, a dot, and a 64-character secret. Legacy cover URLs with a credential in the filename remain accepted.
- Requests without the key get a 401. The admin UI/API and `/health` are unaffected.
- The global key remains visible so you can subscribe. A scoped secret and feed URL appear only in its create response; later list responses show its label, ID, and timestamps without the secret.

Enabling or rotating the global key changes every global URL, so podcast apps using that key must re-add the feeds. A scoped subscriber receives RSS with only its own credential in enclosure, transcript, chapter, and artwork URLs. The cached feed never stores that credential. Revoking a scoped key stops it at once. You may then delete its audit record; deleting the record does not make the token valid again.

A key scoped to Recents can read `/recents`, its cover, and assets for episodes currently eligible for that feed. It cannot read source RSS or an unrelated, expired, or unprocessed episode. When an item falls outside the Recents window or item limit, that key can no longer read its asset.

Request and gunicorn access logs omit query strings by default. Source URLs shown in the UI or written to logs also drop user information and query strings. Legacy credential-bearing artwork paths are redacted, and public feed responses use `Referrer-Policy: no-referrer`. `LOG_DOWNLOAD_QUERY=true` is an explicit diagnostic override and can expose signed CDN or listener tokens.

### Provider admission controls

Queue Control > Provider admission can reserve a daily allowance and limit concurrent provider runs. Reservation writes use one SQLite transaction, so workers cannot both claim the final slot. Each reservation records its processing run, and an expired lease still holds its concurrency slot while that run has a live owner. Unknown-cost requests can be denied, admitted without a reservation, or charged a fixed reservation. A timed-out or lost response stays charged as uncertain until a later result reconciles it. This is an admission limit, not a guaranteed provider spending cap, because a final charge can exceed its estimate.

Custom model prices apply when completed calls are added to usage and cost history. Provider admission still uses the unknown-cost policy because the pipeline has no token estimate before provider work starts.

Budgets can be entered in supported ISO currencies. MinusPod fetches a Frankfurter reference rate only when a non-USD budget change is saved or a currency switch needs a preview. It rejects rates older than 7 days and saves the rate with the selected currency and source date. Admission checks always use the resulting USD micro-dollar limit, so no worker needs an exchange-rate lookup. The saved rate remains visible when the currency service is unavailable.

Compose deployments disable unauthenticated just-in-time processing by default. Set `MINUSPOD_ALLOW_PUBLIC_PROCESSING=true` only when a public request should be allowed to start transcription and provider work. Enabling Authenticated Feeds also permits a valid global or feed-scoped subscriber credential to start that work.

### Getting the feeds into an app

The OPML Export controls (Settings > Data Management) give two ways to move your subscriptions:

- **Download file** saves an `.opml` you import from your device.
- **Copy URL** copies a link the app can pull directly, for the many apps that support "import from URL." The link points at a key-gated route on the feed domain (`/opml/modified.opml?key=...`), so it only appears when authenticated feeds is on, and a request without the key 404s.

Both are offered for modified feeds (MinusPod ad-free URLs) and original feeds (upstream source URLs).

If you also run the Cloudflare WAF rule described above, the cover URL still ends in `.jpg` and query strings are not part of the matched path, so key-gated fetching is unaffected. One caveat for Copy URL: the `/opml/` path lives on the feed domain behind that rule, so an app fetching it needs to get past your UA/path filter. If a given app's import-from-URL is blocked, allow the `/opml/` path (or that app's user agent) in the rule. The two layers stack - Cloudflare filters by client, the key gates by possession.

## Data storage

All data is stored in the `./data` directory:
- `podcast.db` - SQLite database with feeds, episodes, and settings
- `{slug}/` - Per-feed directories with cached RSS and processed audio
- `backups/` - Pre-migration SQLite snapshots, periodic cleanup backups, and the default destination for scheduled database backups

### Container user

Runs as UID 1000 (`minuspod`). First boot chowns the data volume, then drops privileges via `setpriv` (from `util-linux`, present in the base image). Override with `APP_UID` / `APP_GID` if your host volume belongs to a different UID, or bypass entirely with `docker run --user <N>`.

### Database backup sensitivity

The SQLite backup files produced by `GET /api/v1/system/backup`, by the periodic cleanup task, and by scheduled backups (see below) all contain:

- Provider API keys (encrypted with `MINUSPOD_MASTER_PASSPHRASE` when set; plaintext legacy rows otherwise)
- Flask session signing key
- Webhook HMAC secrets
- Password hash (scrypt)

Treat the file like a credential. When `MINUSPOD_MASTER_PASSPHRASE` is set, downloadable backups use the versioned `MPBK02` envelope. Its authenticated header carries the PBKDF2 iteration count, random KDF salt, nonce, and plaintext size, so recovery does not require a live database. Decryption writes a private temporary file and publishes plaintext only after AES-GCM authentication succeeds. Legacy `MPBK01` files still require a database from the same instance for its salt. Scheduled snapshots remain plain SQLite files.

### Scheduled database backups

Scheduled backups copy the SQLite database to a directory you choose, on a cron schedule you control, so you can recover after a bad upgrade or a lost volume. The feature is off by default. Configure it in **Settings > Data & Security > Scheduled Backups**, or via `GET/PUT /api/v1/settings/db-backup`. There is also a "Back up now" button (`POST /api/v1/system/db-backup/run`) that runs one immediately whether or not scheduling is on.

The copies are written with SQLite's online backup API, so they stay consistent even while the app is running, and the snapshot is a single file with no `-wal` or `-shm` sidecars. Keep count controls the filenames:

- Keep 1 (overwrite): one fixed file, `minuspod-backup.db`, replaced on each run.
- Keep 2 or more (rotate): timestamped files named `minuspod-backup-auto-YYYYMMDD-HHMMSS.db`, with the oldest pruned once the count is exceeded. The `-auto-` prefix keeps scheduler files distinct from operator-saved downloads (`minuspod-backup-YYYYMMDD-HHMMSS.db`), so a download parked in the same directory is never pruned.

The default destination is `/app/data/backups/` inside the container. When MinusPod creates the destination directory it sets mode `0700`; a directory you point it at that already exists keeps its own permissions. Each backup file is written with mode `0600`, so other UIDs on the host cannot read a dump that holds provider secrets. These files carry the same sensitive contents listed under [Database backup sensitivity](#database-backup-sensitivity) above, and unlike the download path they are never encrypted. Point the destination at a directory you trust, and treat it like a credential store.

### Restore and passphrase rotation

Prepare a new database file while MinusPod is stopped:

```bash
MINUSPOD_MASTER_PASSPHRASE=your-passphrase \
  python scripts/restore_backup.py backup.db.enc data/podcast-restored.db
```

The restore command refuses a destination that exists, refuses stale `-wal` or `-shm` sidecars, verifies `PRAGMA integrity_check`, and publishes with mode `0600`. Keep the old `podcast.db` until this prepared file passes review. With every worker stopped, move the old database and its sidecars out of the data directory, move the prepared file to `podcast.db`, then start MinusPod. Never place a restored database beside stale sidecars from another database.

Rotate the provider-key passphrase offline:

```bash
python scripts/rotate_master_passphrase.py
```

The script takes an exclusive maintenance lock and refuses to run while an application worker holds its shared runtime lock. It snapshots the database before changing the salt or encrypted settings, updates them in one transaction, then verifies every encrypted value with the new passphrase. Update `MINUSPOD_MASTER_PASSPHRASE` in the deployment before restarting all workers together.

### Decrypting a backup

Encrypted `MPBK02` backup files use AES-GCM with a key derived from `MINUSPOD_MASTER_PASSPHRASE` via PBKDF2 with 600,000 iterations and SHA-256. Decryption needs two things:

1. The encrypted file.
2. The same `MINUSPOD_MASTER_PASSPHRASE` that produced it.

The ship-in-repo CLI handles this:

```bash
MINUSPOD_MASTER_PASSPHRASE=your-passphrase \
    python scripts/decrypt_backup.py /path/to/backup.db.enc /path/to/backup.db
```

Runs from inside the container or any host with the repo checked out and `cryptography` installed. `MPBK02` stores its own salt. For a legacy `MPBK01` backup, pass `--salt-db` to `scripts/decrypt_backup_standalone.py` with an intact database from the same instance.

Unencrypted `.db` files are regular SQLite databases. Restore them with `sqlite3` or by copying into place on a stopped instance.

### Pattern import / export

Before doing a `replace` import, export first so there's a round-trip backup:

```bash
curl -b cookies.txt https://your-minuspod/api/v1/patterns/export?include_corrections=true > patterns-backup.json
```

`POST /api/v1/patterns/import` runs validation on the entire payload before any writes and wraps the delete/update/insert pass in a single `BEGIN IMMEDIATE` transaction. A malformed entry or mid-transaction error rolls back to the pre-import state; `replace` mode can no longer leave an empty pattern table on a bad payload. Modes are `merge` (update matches, add new), `replace` (wipe then import), or `supplement` (add new only).

## Custom assets (optional)

By default, a short audio marker is played where ads were removed. You can customize this by providing your own replacement audio:

1. Create an `assets` directory next to your docker-compose.yml
2. Place your custom `replace.mp3` file in the assets directory
3. Uncomment the assets volume mount in docker-compose.yml:
   ```yaml
   volumes:
     - ./data:/app/data
     - ./assets:/app/assets:ro  # Uncomment this line
   ```
4. Restart the container

The `replace.mp3` file will be inserted at each ad break. Keep it short (1-3 seconds). If no custom asset is provided, the built-in default marker is used.

---

[< Docs index](README.md) | [Project README](../README.md)
