# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What MinusPod is

A self-hosted server that removes ads from podcasts before playback. The pipeline is: Whisper transcribes the episode -> an LLM detects ad segments (with a verification second pass) -> FFmpeg cuts the ads and inserts marker tones -> Flask serves rewritten RSS feeds and processed audio. Episodes are processed once, on-demand at play time or automatically when new episodes appear, then served from disk. The LLM provider is pluggable (Claude, Ollama, OpenRouter, any OpenAI-compatible endpoint), as is the Whisper backend (local GPU via faster-whisper, or a remote OpenAI-compatible API).

## Commands

Backend (Python 3.11, run from repo root):

```bash
PYTHONPATH=src pytest tests/                          # full suite (CI uses this)
PYTHONPATH=src pytest tests/unit/test_ad_validator.py # single file
PYTHONPATH=src pytest tests/ -k "lockout"             # single test by name
```

`PYTHONPATH=src` is mandatory: modules import each other by bare name (`from storage import Storage`), not by package path. There is no Python linter configured.

Frontend (Node 20, run from `frontend/`):

```bash
npm ci
npm run dev      # Vite dev server on :5173, proxies /api and /health to :8000
npm run lint     # eslint (CI gate)
npm run build    # tsc + vite build -> emits to ../static/ui
```

Frontend builds into `static/ui`, which Flask serves at `/ui/`. Some backend tests assert on built static assets (favicon, etc.), so CI builds the frontend before running pytest.

Run the whole app:

```bash
docker-compose up -d            # GPU image
docker compose -f docker-compose.cpu.yml up -d   # CPU image, needs a remote Whisper backend
```

In the container, gunicorn runs from `/app/src` as `gunicorn -c gunicorn.conf.py main_app:app`. The WSGI app is `src/main_app/__init__.py:app`.

## Architecture

**`src/` is the import root.** Top-level modules in `src/` (e.g. `storage.py`, `transcriber.py`, `ad_detector/`, `audio_processor.py`, `database/`) are imported by bare name. Subpackages group related modules.

**Web layer (`src/main_app/`)** -- Flask app factory and lifecycle in `__init__.py`: logging, secret-key minting under flock, security headers, CSRF cookie, secret migration, and `_startup()`. Request routing is split across `routes.py` (feed/audio serving), `feeds.py` (RSS rewrite + cache), `processing.py`, and `background.py`.

**REST API (`src/api/`)** -- Blueprint mounted at `/api/v1`, one module per resource (`episodes`, `feeds`, `patterns`, `sponsors`, `settings`, `providers`, etc.). `auth.py` handles login + lockout, `csrf.py` the double-submit token. `openapi.yaml` at repo root documents these endpoints.

**Detection pipeline** -- `transcriber.py` (Whisper) -> `ad_detector/` (LLM prompts in `prompts.py`, boundary snapping in `boundaries.py`) -> `verification_pass.py` (second LLM pass over processed audio to catch fragments/misses) -> `ad_validator.py` / `ad_reviewer.py` (confidence and duration sanity checks) -> `audio_processor.py` (FFmpeg cut + marker insertion). Tunable thresholds (confidence levels, min/max ad durations, merge gaps) live centrally in `src/config.py` -- change them there, not inline.

**LLM access** -- go through `llm_client.py` / `utils/llm_call.py` / `utils/llm_response.py`, never call providers directly. `llm_capabilities.py` and `pricing_fetcher.py` describe model features and cost.

**Pattern learning** -- user corrections become cross-episode ad patterns (`pattern_service.py`, `text_pattern_matcher.py`, `sponsor_service.py`, `sponsor_normalize.py`) so repeat sponsors get caught without re-asking the LLM. Patterns can be shared via the community export/sync flow (`community_export.py`, `community_sync.py`); shared patterns and themes live under `patterns/`.

**Background work & concurrency** -- gunicorn runs multiple workers but only ONE is the "background leader" (elected via an flock in `_try_become_background_leader`). The leader owns RSS refresh and the processing queue (`processing_queue.py`), because SQLite cannot tolerate concurrent writers. When adding startup or background work, gate it on the leader or you will cause "database is locked" cascades.

**Data layer (`src/database/`)** -- single SQLite DB behind a `Database` singleton assembled from mixins (`PodcastMixin`, `EpisodeMixin`, `PatternMixin`, etc.). Schema and migrations are in `database/schema/`; migrations apply on first connection. Tests reset `Database._instance` between cases (see `tests/conftest.py`).

**Secrets** -- provider API keys are encrypted at rest as `enc:v1:` rows when `MINUSPOD_MASTER_PASSPHRASE` is set (`secrets_crypto.py`). Migration from plaintext runs at startup and snapshots the DB to `data/backups/` first.

## Conventions

- **ASCII only.** A pre-commit hook (`.githooks/pre-commit`) rejects em-dashes, smart quotes, and the star glyph in staged files (except `CHANGELOG.md` and `benchmarks/llm/results/`). Use plain `--`, straight quotes, `*`. The same hook scans for credential-shaped strings. Enable hooks once per clone: `git config core.hooksPath .githooks`.
- SSRF protection: any outbound URL (provider base URLs, feed URLs) must pass `utils/url.py:validate_base_url`. New outbound-fetch code goes through `utils/safe_http.py` / `utils/http.py`.
- CORS is intentionally disabled; the app is single-origin (the Vite dev proxy is the only cross-origin path).
- `version.py` holds `__version__`; bump it with feature changes (commit messages follow `feat(x.y.z): ...` / `fix(...)` / `chore(...)`).
