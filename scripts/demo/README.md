# Demo snapshot and crawl pipeline

The hosted demo is the frontend built with `VITE_DEMO=1`, running against recorded API fixtures instead of a backend. This folder holds the tooling that turns a real instance into those fixtures. A scrubbed copy of the database is served by a local backend, a browser walks every UI route against it, and the recorded traffic becomes fixture files. A few extras (provider settings, peaks, artwork) come from the live instance with GET requests only. No audio is exported.

Generated output is never committed. `frontend/demo/fixtures`, `frontend/demo/artwork`, `frontend/demo/search-index.json` and `frontend/dist-demo` are gitignored. The snapshot database and the live extras are published as release assets on the demo site repository. Crawled fixtures are rebuilt from them.

## Setup

- Python: `pip install -r scripts/demo/requirements.txt`, run from the repo root with `PYTHONPATH=src:.`.
- Node 24 or newer: `cd scripts/demo/crawl && npm install && npx playwright install chromium`.
- `hosts.txt` below stands for a file you keep outside the repo. The scripts take hosts as a comma-separated list, for example `--hosts "$(paste -sd, hosts.txt)"`. List every hostname the instance has ever used, public and private.
- Never put hostnames, feed slugs, cookies or keys in anything committed.

## 1. Local snapshot

Copies the live database with the SQLite backup API, applies the allowlist, scrubs it and writes a new file. Excluded feeds are removed with everything that references them.

```
PYTHONPATH=src:. python scripts/demo/snapshot.py \
  --source /path/to/podcast.db \
  --hosts "$(paste -sd, hosts.txt)" \
  --exclusions /path/to/demo-exclusions.json \
  --out demo-snapshot-YYYY-MM-DD.db
```

- `--days` (default 30) sets the episode window. `--export-day` fixes the day the timestamp shift is anchored to.
- `--exclusions` defaults to `scripts/demo/exclusions.json`. Keep the real list outside the repo.
- `--report-unclassified` lists tables that are in the source but not in the allowlist. Those tables are emptied.
- Exit code 2 means the post-scrub leak check found something. Do not use the output.

## 2. Run a backend on the snapshot

Start the backend container with the snapshot as its database and a throwaway data directory. Point it at `http://127.0.0.1:8000` or any address you pass to the next steps. Set `MINUSPOD_BACKGROUND_THREADS=0` so it serves the snapshot without starting downloads or processing.

## 3. Record live extras

The only step that touches the live instance. It sends GET requests only, using a Netscape cookie file for a signed-in session. It records provider and model overrides, waveform peaks and artwork. Run it with the same `--hosts` and `--exclusions` as the snapshot. It does not need the local backend, so it can run while the crawl does.

```
PYTHONPATH=src:. python scripts/demo/record_live.py \
  --base-url https://minuspod.example \
  --cookies /path/to/cookies.txt \
  --hosts "$(paste -sd, hosts.txt)" \
  --exclusions /path/to/demo-exclusions.json \
  --out live-extras
```

- Output: `live-extras/overrides/` (fixtures plus `index.json`), `live-extras/artwork/feeds/<slug>.jpg` and `live-extras/artwork/episodes/<slug>/<id>.jpg`.
- `--since-days` (default 30) limits episode artwork and peaks. `--no-peaks` skips peaks. `--synthesize-queue` builds a sample queue from history when the live queue is empty.
- Exit code 2 means a leak was found. The partial output directory must not be published.

## 4. Crawl

Playwright walks every route in `crawl/routes.json` (static pages, every feed, up to three episodes per feed, search types, settings sections and tabs) and records the API traffic.

```
cd scripts/demo/crawl
node crawl.mjs --base http://127.0.0.1:8000 --out crawl-out
```

- Writes `crawl-out/crawl.har` and `crawl-out/routes-visited.json`. Exit 1 on a page error, a 5xx response or an expected route that was not visited.
- Add `--ui-base /ui` if the backend serves the UI under a prefix.

## 5. HAR to fixtures

```
node har-to-fixtures.mjs --har crawl-out/crawl.har --out ../../../frontend/demo/fixtures
```

- Keys follow the same encoding as `frontend/src/demo/fixtures.ts`. `crawl/lib/key-vectors.json` holds shared test vectors for both sides.

## 6. Dumps

Fills in what a click-through does not reach: static lists, paged lists, and per-feed and per-episode endpoints. It reads the feed and episode lists from the backend and overwrites any fixture with the same key, so run it after the HAR step. Two final passes run after the dumps: `--overrides` copies the live extras on top, so they win over crawled and dumped responses, and `--transcript-window <seconds>` keeps only the transcript lines within N seconds of an ad marker in every episode fixture.

```
node dumps.mjs --base http://127.0.0.1:8000 --out ../../../frontend/demo/fixtures \
  --overrides ../../../live-extras/overrides --transcript-window 600
```

- The window lives here, not in the snapshot, so the release asset keeps whole transcripts. Omit it to keep them in the fixtures too.
- A rerun must pass both flags again, because the dumps refill the fixtures they overwrite.
- A 429 is retried with backoff. 404 responses are recorded as 404 fixtures. Exit 1 if a request still fails after retries.

## 7. Search index, artwork and coverage

From `scripts/demo/crawl`:

```
node build-search-index.mjs --fixtures ../../../frontend/demo/fixtures \
  --out ../../../frontend/demo/search-index.json
node stage-artwork.mjs --from ../../../live-extras/artwork --to ../../../frontend/demo/artwork
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
  frontend/demo/fixtures frontend/demo/search-index.json
```

It scans files and SQLite cells for the hostnames you list, public and private IPs, feed auth keys (`key=` query values), API keys and tokens, and filesystem paths. A hit exits 2. Excluded feeds are already dropped by `snapshot.py` and `record_live.py`.

## 9. Publish

Upload the snapshot database and the live extras as a release asset on the demo site repository:

```
tar -C live-extras -cf overrides.tar overrides
tar -C live-extras -cf artwork.tar artwork
gh release create snapshot-YYYY-MM-DD demo-snapshot-YYYY-MM-DD.db artwork.tar overrides.tar \
  --repo ttlequals0/minuspod.app --notes "Demo snapshot YYYY-MM-DD"
```

Crawled fixtures are regenerated from these assets by steps 2 and 4 to 7, so they are not uploaded.

## Preview the demo build

```
cd frontend
npm run build:demo
VITE_DEMO=1 npx vite preview --outDir dist-demo --port 4176
```

`VITE_DEMO=1` is required for the preview too. Without it the preview applies the `/ui/` base and the API proxy, and the demo does not load.
