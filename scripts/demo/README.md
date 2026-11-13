# Demo snapshot and crawl pipeline

The hosted demo is the frontend built with `VITE_DEMO=1`, running against recorded API fixtures instead of a backend. This folder holds the tooling that turns a real instance into those fixtures. A scrubbed copy of the database is served by a local backend, a browser walks every UI route against it, and the recorded traffic becomes fixture files. A few extras (provider settings, peaks, artwork) come from the live instance with GET requests only. No audio is exported.

Generated output is never committed. `frontend/demo/fixtures`, `frontend/demo/artwork`, `frontend/demo/search-index.json` and `frontend/dist-demo` are gitignored. The snapshot database and the live extras are published as release assets on the demo site repository. Crawled fixtures are rebuilt from them.

## Setup

- Python: `pip install -r scripts/demo/requirements.txt`, run from the repo root with `PYTHONPATH=src:.`.
- Node 24 or newer: `cd scripts/demo/crawl && npm install && npx playwright install chromium`.
- `hosts.txt` below stands for a file you keep outside the repo. The scripts take hosts as a comma-separated list, for example `--hosts "$(paste -sd, hosts.txt)"`. List every hostname the instance has ever used, public and private.
- Never put hostnames, feed slugs, cookies or keys in anything committed.

## 1. Local snapshot

Start every run from empty `live-extras`, `frontend/demo/fixtures` and `frontend/demo/artwork` directories. The writers in steps 3, 5 and 7 refuse a non-empty output directory and take `--clean` to empty it first. A leftover from an earlier run could still hold a feed you have excluded since. Step 6 only adds to the fixtures directory, so it runs after step 5.

Copies the live database with the SQLite backup API, applies the allowlist, scrubs it and writes a new file. Excluded feeds are removed with everything that references them.

```
PYTHONPATH=src:. python scripts/demo/snapshot.py \
  --source /path/to/podcast.db \
  --hosts "$(paste -sd, hosts.txt)" \
  --exclusions /path/to/demo-exclusions.json \
  --out demo-snapshot-YYYY-MM-DD.db
```

- `--days` (default 30) sets the episode window. `--export-day` fixes the day the timestamp shift is anchored to.
- `--exclusions` and `--hosts` are required. `scripts/demo/exclusions.json` is only a shape example (an empty list); keep the real list outside the repo.
- Exit code 1 if a slug in the exclusions matches no feed, or if a feed whose `source_url` carries credentials (userinfo or a query string) or sits on a members-only feed host is not excluded. Episode URLs of such a feed carry the same tokens, so add the feed to the exclusions.
- `--hosts` entries must be hostnames. A path, an entry with whitespace or one with no dot is rejected.
- `--report-unclassified` lists tables that are in the source but not in the allowlist. Those tables are emptied.
- Exit code 2 means the post-scrub leak check found something. Do not use the output.

## 2. Run a backend on the snapshot

Start the backend container with the snapshot as its database and a throwaway data directory. Point it at `http://127.0.0.1:8000` or any address you pass to the next steps. Set `MINUSPOD_BACKGROUND_THREADS=0` so it serves the snapshot without starting downloads or processing. When the backend runs from source on macOS, start gunicorn with `OBJC_DISABLE_INITIALIZE_FORK_SAFETY=YES`. Without it the workers abort in the system proxy lookup on their first outbound request and the crawl sees empty responses. Also set `MINUSPOD_RATE_LIMIT_DISABLED=true`: a crawl easily passes the default limits, which are per gunicorn worker (200 per minute and 1000 per hour each), and a throttled crawl exits 1.

## 3. Record live extras

The only step that touches the live instance. It sends GET requests only, using a Netscape cookie file for a signed-in session, and records provider and model overrides, waveform peaks and artwork. Run it with the same `--hosts` and `--exclusions` as the snapshot. It does not need the local backend, so it can run while the crawl does.

```
PYTHONPATH=src:. python scripts/demo/record_live.py \
  --base-url https://minuspod.example \
  --cookies /path/to/cookies.txt \
  --hosts "$(paste -sd, hosts.txt)" \
  --exclusions /path/to/demo-exclusions.json \
  --out live-extras --clean
```

- Output: `live-extras/overrides/` (fixtures plus `index.json`), `live-extras/artwork/feeds/<slug>.jpg` and `live-extras/artwork/episodes/<slug>/<id>.jpg`.
- `--since-days` (default 30) limits episode artwork and peaks. `--no-peaks` skips peaks. `--synthesize-queue` builds a sample queue from history when the live queue is empty.
- Exit code 2 means a leak was found, a feed artwork fetch failed or returned an unreadable image, or a GET returned a non-OK status. The partial output directory must not be published.
- Exit code 1 for a bad `--hosts`, an exclusion slug that is not in `/feeds`, or a non-empty `--out` without `--clean`.

Model lists come back per provider and slot, and the live instance only fills the slots that hold a key. The recorder stores the union across slots for every slot, so the demo's model dropdowns are full for every provider. It also stores one whole-episode waveform per processed episode, and the demo slices that to the editor's zoom window.

## 4. Crawl

Playwright walks every route in `crawl/routes.json` (static pages, every feed, up to three episodes per feed, search types, settings sections and tabs) and records the API traffic.

```
cd scripts/demo/crawl
node crawl.mjs --base http://127.0.0.1:8000 --ui-base /ui --out crawl-out
```

- Writes `crawl-out/crawl.har` and `crawl-out/routes-visited.json`. Exit 1 on a page error, a 5xx or 429 response or an expected route that was not visited.
- Modal dialogs such as "What's new" are dismissed before sections are expanded; the dismissed headings are listed in `routes-visited.json` as `dialogsDismissed`.
- The backend serves the UI under `/ui`; without `--ui-base` every page is a 404 and the HAR holds almost no API traffic.

## 5. HAR to fixtures

```
node har-to-fixtures.mjs --har crawl-out/crawl.har --out ../../../frontend/demo/fixtures --clean
```

- Keys follow the same encoding as `frontend/src/demo/fixtures.ts`. `crawl/lib/key-vectors.json` holds shared test vectors for both sides.

## 6. Dumps

Fills in what a click-through does not reach: static lists, paged lists, and per-feed, per-episode and pattern-audio endpoints. Pattern audio includes metadata and every match-history page, using the UI's page size. No audio files are exported. The dumper reads the lists from the backend and overwrites any fixture with the same key, so run it after the HAR step. Two passes run after the dumps. `--overrides` copies the live extras on top, so they win over crawled and dumped responses. `--transcript-window <seconds>` keeps only the transcript lines within that many seconds of an ad marker in every episode fixture.

```
node dumps.mjs --base http://127.0.0.1:8000 --out ../../../frontend/demo/fixtures \
  --overrides ../../../live-extras/overrides --transcript-window 600
```

- The window lives here, not in the snapshot, so the release asset keeps whole transcripts. Omit it to keep them in the fixtures too.
- A rerun must pass both flags again, because the dumps refill the fixtures they overwrite.
- A 429 is retried with backoff. 404 responses are recorded as 404 fixtures. A 5xx response is counted as an error and no fixture is written for it. Exit 1 if a request still fails after retries.

## 7. Search index, artwork and coverage

From `scripts/demo/crawl`:

```
node build-search-index.mjs --fixtures ../../../frontend/demo/fixtures \
  --out ../../../frontend/demo/search-index.json
node stage-artwork.mjs --from ../../../live-extras/artwork --to ../../../frontend/demo/artwork --clean
node check-coverage.mjs --api-dir ../../../frontend/src/api \
  --fixtures ../../../frontend/demo/fixtures
```

- Every feed needs `artwork/feeds/<slug>.jpg`. The demo has no fallback to the API artwork URL.
- `check-coverage.mjs` lists GET templates the UI requests that have no fixture. It must print nothing except the documented allow list (`--allow <json>` extends it). Exit 1 otherwise.

## 8. Leak check

Run it on everything that ships, after all steps:

```
PYTHONPATH=src:. python scripts/demo/leak_check.py \
  --hosts "$(paste -sd, hosts.txt)" \
  --sqlite demo-snapshot-YYYY-MM-DD.db \
  --exclusions /path/to/demo-exclusions.json \
  frontend/demo/fixtures frontend/demo/artwork frontend/demo/search-index.json
```

It scans files and SQLite cells for the hostnames you list, globally routable IPs outside the documentation ranges, feed auth keys (`key=` query values), `token=`, `token-hash=` and `token-time=` query values, API keys and tokens, and filesystem paths. With `--exclusions`, each excluded slug is also searched for in file contents and SQLite cells, and in every file and directory name under the scanned paths, including `.jpg` files that are otherwise skipped. A hit exits 2. With `--redact`, which is on automatically when `GITHUB_ACTIONS` is `true`, hosts and slugs print as `host:#<n>` and `slug:#<n>` and excerpts are replaced by an offset, so a public CI log never shows them. `--hosts` is required and follows the same rules as in step 1.

## 9. Rendered feeds

The demo app runs without egress and cannot build a subscribed feed's RSS, so the snapshot ships the
rendered feeds as the app's RSS cache. Record them from the live instance after the database is final:

```
PYTHONPATH=src:. python scripts/demo/record_feeds.py --base-url https://your-instance \
  --cookies cookies.txt --sqlite demo-snapshot-YYYY-MM-DD.db \
  --hosts "$(paste -sd, hosts.txt)" --exclusions /path/to/demo-exclusions.json \
  --out live-feeds --tar rss.tar
```

It fetches `/<slug>` for every podcast in the snapshot (with the feed key when feed auth is on),
rewrites the instance hosts to `demo.minuspod.app`, removes the key from every URL and cover token,
drops items whose episode the snapshot lacks, replaces emails, runs the leak check and writes
`podcasts/<slug>/modified-rss.xml`. The render-version marker stays, so the app serves the files
without a refresh.

## 10. Publish

Upload the snapshot database, the live extras and the rendered feeds as release assets on the demo
site repository:

```
COPYFILE_DISABLE=1 tar -C live-extras -cf overrides.tar overrides
COPYFILE_DISABLE=1 tar -C live-extras -cf artwork.tar artwork
sha256sum demo-snapshot-YYYY-MM-DD.db artwork.tar overrides.tar rss.tar > SHA256SUMS.txt
gh release create snapshot-YYYY-MM-DD demo-snapshot-YYYY-MM-DD.db artwork.tar overrides.tar rss.tar SHA256SUMS.txt \
  --repo ttlequals0/minuspod.app --notes "Demo snapshot YYYY-MM-DD"
```

`COPYFILE_DISABLE=1` keeps macOS metadata files (`._*`) out of the archives.

Crawled fixtures are regenerated from these assets by steps 2 and 4 to 7, so they are not uploaded.

## Preview the demo build

```
cd frontend
npm run build:demo
VITE_DEMO=1 npx vite preview --outDir dist-demo --port 4176
```

`VITE_DEMO=1` is required for the preview too. Without it the preview applies the `/ui/` base and the API proxy, and the demo does not load.
