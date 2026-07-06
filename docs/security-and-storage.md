# Security, Storage & Custom Assets

[< Docs index](README.md) | [Project README](../README.md)

---

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

**Cloudflare WAF example.** Allow only Pocket Casts on the feed host, block admin paths:

```
(http.request.full_uri wildcard r"http*://feed.example.com/*" and not http.user_agent wildcard "*Pocket*Casts*") or starts_with(http.request.uri.path, "/ui") or starts_with(http.request.uri.path, "/api")
```

Swap the User-Agent pattern for your app (`*Overcast*`, `*Castro*`, `*AntennaPod*`, ...).

### Rate limiting storage

Rate limits are tracked per worker (memory-backed), so with the default two workers each declared limit is effectively doubled. For exact limits or multi-host scaling, set `RATE_LIMIT_STORAGE_URI=redis://redis:6379` and add a Redis sidecar. Don't drop below two workers. The UI freezes during bulk RSS refresh with only one.

### Request correlation

Every response carries an `X-Request-ID` header. If you supply one on the request (up to 128 chars), it's preserved; otherwise a 16-char hex value is generated. When reporting a bug, including the `X-Request-ID` from the affected response makes log lookup one `grep` instead of a guessing game. Aggregated log viewers can filter by the `request_id` field on the JSON log records.

### Authenticated feeds (optional)

By default the feed URLs are open: anyone who learns them can read your RSS and download episodes, and a request for an unprocessed episode kicks off transcription. If your server is reachable from the internet, that can get expensive.

Settings > Data & Security > Authenticated Feeds locks this down with a single private key. When enabled:

- Every feed and episode URL carries `?key=<64-hex-key>` (RSS, mp3, transcript vtt, chapters.json). Cover art carries the key inside the filename instead (`cover-minuspod-<version>-<key>.jpg`) because some podcast apps refuse image URLs with query strings.
- Requests without the key get a 401. The admin UI/API and `/health` are unaffected.
- The key is shown in the settings UI and the API on purpose - you need it to subscribe.

Enabling or rotating the key changes every URL, so podcast apps must re-add your feeds. The OPML export (`mode=modified`) includes the key, which makes re-subscribing a two-step job: export, re-import in the app. Served feeds also rebuild themselves with the current key on their next authenticated fetch, and the "Regenerate feeds" button forces that for everything at once. Regenerating re-fetches each source feed and re-renders the RSS - it never re-processes episodes or touches stats.

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
- `backups/` - Pre-migration SQLite snapshots + periodic cleanup backups

### Container user

Runs as UID 1000 (`minuspod`). First boot chowns the data volume, then drops privileges via `setpriv` (from `util-linux`, present in the base image). Override with `APP_UID` / `APP_GID` if your host volume belongs to a different UID, or bypass entirely with `docker run --user <N>`.

### Database backup sensitivity

The SQLite backup files produced by `GET /api/v1/system/backup` and by the periodic cleanup task contain:

- Provider API keys (encrypted with `MINUSPOD_MASTER_PASSPHRASE` when set; plaintext legacy rows otherwise)
- Flask session signing key
- Webhook HMAC secrets
- Password hash (scrypt)

Treat the file like a credential. When `MINUSPOD_MASTER_PASSPHRASE` is set, both backup paths produce AES-GCM encrypted `.db.enc` files, and restoring requires the same passphrase the source used. Without a passphrase, unencrypted `.db` files are written and a WARN is logged at creation time.

### Decrypting a backup

Encrypted backup files (`*.db.enc`) use AES-GCM with a key derived from `MINUSPOD_MASTER_PASSPHRASE` via PBKDF2 (600k iterations, SHA-256). The per-instance salt is stored in the running DB's `settings` table (`provider_crypto_salt`), not in the backup envelope. Decryption needs three things:

1. The encrypted file.
2. The same `MINUSPOD_MASTER_PASSPHRASE` that produced it.
3. The live container's DB (for the salt row).

The ship-in-repo CLI handles this:

```bash
MINUSPOD_MASTER_PASSPHRASE=your-passphrase \
    python scripts/decrypt_backup.py /path/to/backup.db.enc /path/to/backup.db
```

Runs from inside the container or any host with the repo checked out and `cryptography` installed. `DATA_PATH` points at the running instance's data dir (default `/app/data`).

**Important caveat:** the salt is per-DB, not per-passphrase. If you rotate the passphrase (`POST /api/v1/settings/providers/rotate-passphrase`), old backups made under the previous passphrase are still decryptable, because rotation re-encrypts rows under a new salt + new DEK in place. But if you lose the DB entirely (full-volume loss, fresh install) and only have backup files, you cannot decrypt them even with the original passphrase. The salt is gone. Treat the passphrase and the DB together as the recovery bundle.

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
