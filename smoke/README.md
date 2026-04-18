# MinusPod 2.0.0 smoke test harness

Tests the deployed `ttlequals0/minuspod:2.0.0` image against the F01-F75
security-audit checklist. Local instance is the same image as production.

## Layout

- `lib/common.sh` - shared helpers (curl wrapper, asserts, logging)
- `local/` - tests against local container on port 8001 (destructive tests live here)
- `remote/` - tests against `https://your-server.example.com` (read-only + T16 episode reprocess)
- `results/` - per-run artifacts (gitignored)
- `run-all.sh` - orchestrator: local then remote, then writes SUMMARY.md

## Usage

Run everything:

    ./smoke/run-all.sh

Run only local or only remote:

    ./smoke/run-all.sh local
    ./smoke/run-all.sh remote

Individual test (after setup):

    LOCAL_BASE=http://localhost:8001 ./smoke/local/01-health.sh

## Prerequisites

- `docker` (to pull and run the local container)
- `curl`, `jq`, `python3`
- `cookies.txt` in repo root for remote auth
- Grafana MCP available (for remote log assertions)
- Playwright MCP available (for UI tests)

## Environment

Default ports and creds:

| Var | Default |
|-----|---------|
| `LOCAL_BASE` | `http://localhost:8001` |
| `LOCAL_PASSWORD` | `SmokeTestPass123!` |
| `LOCAL_CONTAINER` | `minuspod-smoke` |
| `LOCAL_VOLUME` | `minuspod-smoke-data` |
| `REMOTE_BASE` | `https://your-server.example.com` |
| `REMOTE_COOKIES` | `./cookies.txt` |

## Test matrix

See repo issue / spec; T01-T18 mapped to scripts in `local/` and `remote/`.

Local skips T16 (episode processing) by user request. Remote skips destructive
tests (T09 rate flood, T10 destructive cleanup, T11 lockout, T13 backup,
T14 pattern replace) and runs T16 against an existing processed episode.
