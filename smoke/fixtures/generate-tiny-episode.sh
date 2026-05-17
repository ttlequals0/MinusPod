#!/usr/bin/env bash
# Generate smoke/fixtures/tiny-episode.mp3 if missing. Requires ffmpeg
# (installed by default in the production minuspod image; on a dev host
# install via `apt install ffmpeg`).
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OUT="$HERE/tiny-episode.mp3"
if [ -s "$OUT" ]; then
    echo "tiny-episode.mp3 already exists ($(stat -c%s "$OUT") bytes)"
    exit 0
fi
if ! command -v ffmpeg >/dev/null 2>&1; then
    echo "error: ffmpeg not found; install it or run this script inside the container" >&2
    exit 1
fi
ffmpeg -loglevel error -f lavfi \
    -i anullsrc=channel_layout=mono:sample_rate=22050 \
    -t 5 -b:a 64k "$OUT" -y
echo "wrote $OUT ($(stat -c%s "$OUT") bytes)"
