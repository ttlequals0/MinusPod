# cuebench -- MinusPod cue-template eval harness

Offline benchmark for MinusPod's MFCC template matcher and Chromaprint
discovery scanner. Run it before and after tuning changes to get hard numbers.

## Prerequisites

- Python 3.11+
- uv (https://github.com/astral-sh/uv)
- ffmpeg on PATH (for audio decode)
- fpcalc on PATH (for `scan` command only; skipped cleanly if absent)

## Setup

```
cd benchmarks/cues
uv sync
```

## Commands

### load-template

Validate a template export (zip or directory) and print metadata.

```
uv run cuebench load-template /path/to/export.zip
uv run cuebench load-template /path/to/dir/          # must contain cue.flac + template.json
```

### fetch

Pre-download episodes into the cache without running a sweep.

```
uv run cuebench fetch --rss "https://feeds.megaphone.fm/pivot" --max-episodes 5
```

### sweep

Sweep templates across episode audio and write `results/report.md` +
`results/report.json`.

**With local audio files (no network):**

```
uv run cuebench sweep \
  --template /path/to/wsj/          \
  --audio /path/to/episode1.mp3     \
  --audio /path/to/episode2.mp3
```

**With an RSS feed (downloads to cache):**

```
uv run cuebench sweep \
  --template /path/to/wsj/          \
  --rss "https://feeds.simplecast.com/NEWS_TEN_MINUTE"  \
  --max-episodes 10
```

**Formant A/B comparison (0 dB vs 12 dB attenuation):**

```
uv run cuebench sweep \
  --template /path/to/wsj/          \
  --audio /path/to/episode.mp3      \
  --formant-ab
```

**Confirm re-run at suggested threshold:**

```
uv run cuebench sweep \
  --template /path/to/wsj/          \
  --audio /path/to/episode.mp3      \
  --confirm
```

**Multiple templates:**

```
uv run cuebench sweep \
  --template /path/to/wsj/          \
  --template /path/to/pivot/        \
  --rss "https://feeds.megaphone.fm/pivot"
```

### scan

Run Chromaprint discovery eval against sweep ground truth.

```
uv run cuebench scan \
  --template /path/to/wsj/          \
  --audio /path/to/episode.mp3
```

Skips cleanly with a message when fpcalc is not available.

### report

Re-render `results/report.md` from an existing `results/report.json` (no
audio re-processing).

```
uv run cuebench report
uv run cuebench report --output-dir /path/to/results/
```

## Template export format

A template export is a zip file (or directory) containing:

- `cue.flac` -- 16 kHz mono s16 FLAC of the captured cue
- `template.json` -- manifest with schemaVersion, label, cueType, durationS, ...

Export from a running MinusPod instance via the cue template export button in
the UI, or via the API endpoint `GET /api/v1/cue-templates/<id>/export`.

Known-good local test inputs from Phase 1:

- `tests/fixtures/cues/wsj_content_transition.flac`
- `tests/fixtures/cues/pivot_content_transition.flac`

## Test

```
cd benchmarks/cues
uv run pytest -q
```

Tests are pure logic only (no network, no audio decode, no file I/O beyond
fixtures). They cover manifest parsing, zip/dir validation, WAV header
rejection, histogram math, and threshold table math.

## Cache

Episode audio is cached at `~/.cache/minuspod-cuebench/<feed-hash>/<ep-hash>.<ext>`.
Files are reused on subsequent runs. The 500 MB per-file cap prevents runaway
downloads. The cache directory is not cleaned automatically; remove it manually
when space is needed.

## Results

`results/` is gitignored. Each sweep overwrites `results/report.md` and
`results/report.json`. Use `uv run cuebench report --output-dir
results/archive/<date>/` to save a snapshot before re-running.

## Public RSS feeds used for baseline (no auth required)

- WSJ What's News: `https://feeds.simplecast.com/NEWS_TEN_MINUTE`
- Pivot: `https://feeds.megaphone.fm/pivot`
