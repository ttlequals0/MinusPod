# Demo changelog

This file tracks the hosted demo and its tooling, which are not part of the self-hosted app.

## [Unreleased]

### Added

- The demo fixtures answer the new pattern audio routes (`/patterns/{id}/audio` and `/patterns/{id}/audio/matches`) and `/stats/audio-matching` with empty data, so the Audio section, Recent matches and the Audio matching stats tiles render in the hosted demo. Audio playback is disabled in the demo.

### Changed

- The fixture coverage check no longer expects recorded fixtures for `/patterns/{id}/audio` and `/patterns/{id}/audio/matches`, which the demo answers itself.
- The snapshot export empties the new `audio_fingerprint_matches` match-history table and clears the source feed and episode columns of `audio_fingerprints`.
- The snapshot export keeps the five audio fingerprinting settings (`fingerprint_enabled`, `fingerprint_match_strictness`, `fingerprint_min_coverage`, `fingerprint_gap_tolerance_s` and `fingerprint_min_ad_length_s`) and the new `queue_order`, `session_lifetime_hours` and `session_remember_days` settings in the demo database.
- The snapshot export empties the `deferred_upgrade_steps` table, which the app refills on the next start.
- The snapshot export drops `llm_pre_none_identity`, the setting that remembers a model identity from before the LLM provider was set to None.

## [2.101.34] - 2026-11-09

### Added

- The demo crawl records `/settings/processing-schedule`, and the demo status handler reports the schedule as off.

### Changed

- The snapshot export empties `episode_feed_details`, a cache table the app refills on first read.
- The snapshot export also empties `episode_snap_stats`, the per-episode cache behind the addressing snap distance.

## [2.101.31] - 2026-11-08

### Changed

- The demo fixture check skips when its inputs (app source, demo tooling, snapshot and site pipeline) match a previous green run. It now also runs for backend changes outside the API package.

### Fixed

- Crawled fixtures rewrite container paths such as `/app/data` to `/demo`, so the leak check passes on the backup settings response.
- The demo peaks handler answers 404 when a recorded waveform has no usable resolution.
- The beacon token is read from the snippet's `token` value, so another 32-character hex string earlier in the snippet is no longer picked up.
- `record_feeds.py` checks every scrubbed feed for leaks before writing any file, and leak reports from the demo scripts are redacted when they run under GitHub Actions.

## [2.101.30] - 2026-11-08

### Fixed

- The demo peaks handler answers 400 when `start` or `end` is not a number.
- Artwork staging skips macOS `._*` metadata files, which broke the demo fixture check. The snapshot recipe now sets `COPYFILE_DISABLE=1` so `tar` leaves them out of the archives.

## [2.101.28] - 2026-11-07

### Added

- Pushes to `main` notify the site repo. A new workflow sends `docs-updated` when docs, benchmarks, the README, theme tokens or logos change, and `demo-updated` when the frontend, backend, `openapi.yaml` or the demo tooling change. The docs site and the hosted demo rebuild from those events.
- Pull requests that touch the API client, the database layer, the API or the demo tooling build the CPU image and run the demo fixture pipeline against it. The check fails on a broken migration, a page error, a 5xx or 429 response, a leak or missing coverage.

### Fixed

- Demo build: the waveform editor loads again. The live recorder stores one whole-episode peaks fixture per episode, and the demo slices it to the zoom window the editor asks for. Before, every window got a 404.
- Demo build: model dropdowns list every provider's models in every slot. The live instance only lists models for slots that hold a key, so the recorder now takes the union across slots per provider.
- Demo build: the API explorer is readable on dark themes. Its panel stays white with dark text.
- The demo crawl and HAR converter no longer keep 429 responses. The crawl exits 1 on a throttled response and the converter skips and counts them, so a throttled run never produces fixtures.
- The demo crawl dismisses the What's new dialog, so clicks reach the page and the ad editor opens, and it no longer stalls for 30 seconds on the ad editor button. It also skips menu triggers when expanding sections.
- The demo leak check no longer fires on key prefixes inside words (such as `sk-` in a URL slug containing "disk-"). The Anthropic, OpenRouter and JWT patterns now need a non-alphanumeric character before the prefix, as the OpenAI pattern did.
- The demo build accepts a pasted Cloudflare Web Analytics snippet as well as a bare token for `VITE_CF_BEACON_TOKEN`.
- The demo artwork staging skips a file it cannot decode and names it in the log, instead of failing the whole build on one bad download.
- The snapshot ships each feed's rendered RSS (`rss.tar`, recorded by `scripts/demo/record_feeds.py`) as the demo app's RSS cache. Without it the app, which has no network in the demo pipeline, answered 503 for every feed.

## [2.101.27] - 2026-11-07

### Added

- Demo mode build. `VITE_DEMO=1` (or `npm run build:demo`) builds the frontend as a read-only hosted demo. The UI runs against recorded API fixtures served by a service worker. Writes return 403 with a visible notice, requests for data the snapshot does not hold say so instead of answering with something else, and audio playback is disabled with an explanation. Artwork is bundled, podcast search calls iTunes directly, and an in-app API explorer replaces the backend docs page. Normal builds are unchanged.
- Demo snapshot and crawl tooling under `scripts/demo/`. A scrubbed copy of a live database is served by a local backend, a Playwright crawl records every UI route into the fixtures the demo build replays, and a GET-only recorder adds provider settings, peaks and artwork from the live instance. A leak check scans the output for hostnames, IPs and feed keys before it is published.
