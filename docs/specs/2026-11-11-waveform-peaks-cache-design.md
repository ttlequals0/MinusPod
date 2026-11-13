# Cached waveform peaks: design

Issue #995. The design follows the proposal in that issue.

## Context

The ad, cue and split editors draw a waveform from
`GET /api/v1/feeds/{slug}/episodes/{id}/peaks`. The editors ask for the whole
episode once and slice it in the browser. The route decodes the whole retained
original with ffmpeg on every request and caches nothing
(`src/audio_peaks.py`).

- A decode costs about 3 s and about 120 MB of worker memory per hour of
  audio, as measured in the issue. The PCM is read into memory in one piece.
- A window over 4 hours is refused, so a longer episode has no waveform.
- The timeout is a fixed 60 s whatever the length.
- The decode always seeks, even to 0. On AAC and low-bitrate MP3 that drops
  the decoder's pre-roll and the first 10 to 50 ms are drawn flat.
- React Query keeps the result for 5 minutes after the editor closes. A
  reload, another device or a later reopen decodes again.

The audio fingerprint work in this release opens the same editor for "Edit
edges", so it would pay the same cost.

## Goal

Opening an editor reads a small file. The waveform is the same as a fresh
decode, episodes over 4 hours have one, and processing does no extra decode
to produce it.

## Non-goals

- HTTP caching or an ETag on the route.
- A backfill job for existing episodes.
- Buckets finer than 10 ms.
- Peaks for processed audio. The route serves the retained original only, as
  today.

## Decision: the peaks file

`<id>-original.peaks` sits next to `<id>-original.mp3`. It holds the largest
absolute sample of every 10 ms of an 8 kHz mono decode, as unsigned 16-bit
values, about 0.7 MB per hour of audio. A header records a format version, the
bucket width, the bucket count and the source file's size, modification time
and SHA-256. The file is written to a temporary name and renamed into place.

The file is valid for an original when size and modification time match. That
stamp is the only staleness check, so a replaced original needs no explicit
delete. The SHA-256 is stored only by writers that decode the original anyway
(the first-open build), never by the write at retention time. When the size
matches but the modification time differs, a stored hash is compared and the
header restamped on a match; a file without a stored hash is rebuilt. A file
with an older format version is treated as missing. A replacement with identical size and modification time is not detected.

The exact header layout is left to the builder.

## Decision: filled during processing

The splice detector already decodes the file that becomes the retained
original to 8 kHz mono (`SpliceDetector._decode_8k_mono`). A
`detect_with_peaks()` beside `detect()` also returns the 10 ms maxima from
that decode. `detect()` and its result are unchanged. The analyzer passes the
maxima along in memory, they are not serialized into the analysis result, and
the retain step writes the file after the original is moved into place.

Only the first-pass analysis of the working copy feeds the file. The
verification pass analyzes the processed audio, and its decode is not used.

Processing never decodes for peaks alone. When analysis did not decode the
file (detection skipped, a SponsorBlock-only run, splice evidence off, or a
decode failure), no file is written.

## Decision: built on first open otherwise

When the route finds no valid file, it builds one from the decode it would
have done anyway and serves from it.

- Up to 45 minutes: built inside the request, as the decode is today.
- Over 45 minutes: built in the background. The route answers 503 with
  `Retry-After: 5` until the file exists, and the editor retries every 5 s.
  The build runs on a thread in the web worker that took the request, as the
  cue scans do. An `fcntl` lock file beside the peaks file keeps it to one
  build per episode across workers, the way the search index rebuild is
  locked.

The build streams PCM, so memory stays flat, and its timeout scales with
duration through the existing `ffmpeg_timeout`.

## Decision: the route

The request, the response shape and the 60,000 bucket limit do not change.

- The route serves from the file when the bucket width is a whole multiple of
  10 ms and the window starts on the 10 ms grid. That covers every request the
  editors make. Each returned bucket is the maximum of the 10 ms buckets it
  spans.
- A width the route coarsens to stay under the bucket limit is rounded up to a
  whole multiple of 10 ms. A 3.3 hour window gets 200 ms buckets where it got
  198 ms. The response already reports the width used.
- Any other request streams decoded PCM and keeps the 4 hour window cap.
  Output seeking decodes from the beginning to preserve AAC priming and
  resampling alignment, then emits only the requested window. The timeout
  includes the skipped duration. Aligned editor requests use the cache.
- The 4 hour cap does not apply to a request served from the file.
- New response: 503 with `Retry-After` while a long episode's file is being
  built.

## Decision: deletion

The file is removed wherever its original is: `delete_processed_file`,
`delete_original_only` and `cleanup_episode_files` in `src/storage.py`, and
the local import path that replaces an original. Deleting a feed removes its
directory. A peaks file with no original is never served.

## Decision: frontend

- `usePeaks` retries a 503 every 5 s and shows "Loading waveform..." while it
  waits. The API client retries any 5xx twice at 1 s and 3 s today and reads
  `Retry-After` only on a 429. So the peaks request turns the client's retry
  off and the hook owns the loop.
- `SplitMarkerModal` shows the same "Could not load waveform" text the other
  two editors show. Today it ignores the error.

The demo recorder and demo handler depend only on the response shape and do
not change.

## Docs

- `openapi.yaml`: the 503 response and the rounding note on `resolution_ms`.
- `docs/api-and-webhooks.md`: the peaks route, which is not listed today.
- `CHANGELOG.md`, crediting the issue's author for the design.

## Tests

- File: write and read round trip, header validation, restamp on a changed
  modification time with the same hash, rebuild on a changed hash, and an
  interrupted write leaving no partial file.
- Same answers: peaks from the file equal a fresh decode bucket for bucket, on
  generated audio, for whole-episode and windowed requests.
- Processing: a run with splice detection writes the file with one decode. A
  run without it writes none and decodes nothing extra. A reprocess rewrites the file when the original changed, and records the original's duration on the episode row.
- Route: file path, decode fallback for off-grid requests, rounding of
  coarsened widths, an episode over 45 minutes answering 503 then 200, an episode just under it built inline, and one
  build when two requests arrive together.
- Deletion: each path removes the file with its original.
- Decode: no seek at 0, and the first buckets are not flat on an AAC fixture.
- Frontend: vitest for the 503 retry and the split editor's error text, plus
  Playwright screenshots of the three editors at the UI-CONTRACT widths.

## Alternatives considered

- Decoding at retain every time. Simpler, but it pays a decode for episodes
  nobody opens.
- HTTP caching. Each browser still pays the first decode.
- Going back to window requests. Each zoom or pan would refetch.
- Raising the 4 hour cap. Longer episodes would get a waveform, and each open
  would cost more.
