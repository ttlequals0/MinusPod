#!/usr/bin/env bash
# T31: episode endpoint error paths. Exercises every per-episode and
# per-feed route on a non-existent slug/id so each handler's missing-
# resource path is verified. Real-pipeline behavior (transcribe -> Claude
# -> cut) is exercised by T16 remote against an existing prod episode.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TEST_NAME="T31-episode-error-paths" source "$SCRIPT_DIR/../lib/common.sh"

setup_authed_jar 31

MISS_SLUG="smoke-missing-slug"
MISS_EP="missing0001"
BASE="/api/v1/feeds/$MISS_SLUG/episodes/$MISS_EP"

# Per-episode GETs on missing -> 404.
for ep in \
    "$BASE" \
    "$BASE/transcript" \
    "$BASE/original-transcript" \
    "$BASE/original-segments" \
    "$BASE/final-segments" \
    "$BASE/peaks" \
    "$BASE/transcript-span"; do
    code=$(curl -s -o /dev/null -w '%{http_code}' -b "$JAR" "$LOCAL_BASE$ep")
    assert_in "$code" "404 400" "GET $ep on missing returns 404/400"
done

# Per-episode mutating endpoints on missing -> 404 or 400.
code=$(auth_code POST "$BASE/reprocess" 31 '{}')
assert_in "$code" "404 400" "POST $BASE/reprocess on missing rejected (got $code)"

code=$(auth_code POST "$BASE/regenerate-chapters" 31 '{}')
assert_in "$code" "404 400" "POST $BASE/regenerate-chapters on missing rejected (got $code)"

code=$(auth_code POST "$BASE/cancel" 31 '{}')
assert_in "$code" "404 400" "POST $BASE/cancel on missing rejected (got $code)"

code=$(auth_code POST "$BASE/retry-ad-detection" 31 '{}')
assert_in "$code" "404 400" "POST $BASE/retry-ad-detection on missing rejected (got $code)"

# Alias path under /episodes/<slug>/<id>/reprocess.
code=$(auth_code POST "/api/v1/episodes/$MISS_SLUG/$MISS_EP/reprocess" 31 '{}')
assert_in "$code" "404 400" "alias POST /episodes/<slug>/<id>/reprocess on missing rejected (got $code)"

# Bulk endpoint requires payload.
code=$(auth_code POST "/api/v1/feeds/$MISS_SLUG/episodes/bulk" 31 '{}')
assert_in "$code" "400 404" "POST /feeds/<slug>/episodes/bulk with empty payload rejected (got $code)"

# Feed-level mutating endpoints on missing slug.
code=$(auth_code POST "/api/v1/feeds/$MISS_SLUG/refresh" 31 '{}')
assert_in "$code" "404 400" "POST /feeds/<missing>/refresh rejected (got $code)"

code=$(auth_code POST "/api/v1/feeds/$MISS_SLUG/reprocess-all" 31 '{}')
assert_in "$code" "404 400" "POST /feeds/<missing>/reprocess-all rejected (got $code)"

finish_test "T31-episode-error-paths"
