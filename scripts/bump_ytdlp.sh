#!/usr/bin/env bash
# Rewrite ytdlp-version.txt with the latest yt-dlp release tag.
set -euo pipefail
cd "$(dirname "$0")/.."
latest=$(curl -fsSL https://api.github.com/repos/yt-dlp/yt-dlp/releases/latest \
  | python3 -c 'import json,sys; print(json.load(sys.stdin)["tag_name"])')
current=$(cat ytdlp-version.txt 2>/dev/null || echo none)
if [ "$latest" = "$current" ]; then
  echo "yt-dlp pin already at $latest"
  exit 0
fi
echo "$latest" > ytdlp-version.txt
echo "yt-dlp pin $current -> $latest"
