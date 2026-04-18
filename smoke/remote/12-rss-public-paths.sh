#!/usr/bin/env bash
# T12 (remote): public RSS paths reachable without auth.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TEST_NAME="R-T12-rss-public-paths" source "$SCRIPT_DIR/../lib/common.sh"

slug=$(curl -s -b "$REMOTE_COOKIES" "$REMOTE_BASE/api/v1/feeds" \
    | python3 -c 'import json,sys
d=json.load(sys.stdin)
feeds=d.get("feeds") or d.get("items") or d
if isinstance(feeds, list) and feeds:
    f=feeds[0]
    print(f.get("slug") or f.get("id") or "")
' 2>/dev/null || true)

if [ -z "$slug" ]; then
    skip_step 'remote feeds list empty or unparseable'
    finish_test "R-T12-rss-public-paths"
    exit 0
fi
note "remote slug: $slug"

# Some deployments require a PocketCasts User-Agent; others accept any UA.
# Try without UA first; fall back with the PocketCasts UA.
code=$(curl -s -o /dev/null -w '%{http_code}' "$REMOTE_BASE/$slug")
if [ "$code" = "401" ] || [ "$code" = "403" ]; then
    fail_step "RSS /$slug requires auth on remote (got $code) - regression"
elif [ "$code" = "400" ]; then
    code=$(curl -s -o /dev/null -w '%{http_code}' \
        -A "PocketCasts/1.0" "$REMOTE_BASE/$slug")
    assert_in "$code" "200 304" "RSS /$slug reachable with PocketCasts UA"
else
    assert_in "$code" "200 304" "RSS /$slug reachable unauth (got $code)"
fi

# Artwork
code=$(http_code "$REMOTE_BASE/api/v1/feeds/$slug/artwork")
assert_in "$code" "200 304 404" "artwork for $slug reachable unauth (got $code)"

finish_test "R-T12-rss-public-paths"
