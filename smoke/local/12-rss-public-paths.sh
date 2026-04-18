#!/usr/bin/env bash
# T12: RSS consumer paths must be reachable WITHOUT authentication.
# Picks the first existing feed (if any) from /api/v1/feeds (authenticated)
# and asserts its public paths return 200/404 (never 401/403).
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TEST_NAME="T12-rss-public-paths" source "$SCRIPT_DIR/../lib/common.sh"

JAR="$RESULTS_DIR/T12-cookies.jar"
rm -f "$JAR"
login "$LOCAL_BASE" "$LOCAL_PASSWORD" "$JAR" >/dev/null

slug=$(curl -s -b "$JAR" "$LOCAL_BASE/api/v1/feeds" \
    | python3 -c 'import json,sys
d=json.load(sys.stdin)
feeds=d.get("feeds") or d.get("items") or d
if isinstance(feeds, list) and feeds:
    f=feeds[0]
    print(f.get("slug") or f.get("id") or "")
' 2>/dev/null || true)

if [ -z "$slug" ]; then
    skip_step 'no feeds present on local instance; T12 needs an existing feed'
    rm -f "$JAR"
    finish_test "T12-rss-public-paths"
    exit 0
fi

note "testing public paths for slug: $slug"

# RSS feed (no cookies)
code=$(http_code "$LOCAL_BASE/$slug")
assert_in "$code" "200 304" "RSS /$slug reachable unauth"

# Artwork
code=$(http_code "$LOCAL_BASE/api/v1/feeds/$slug/artwork")
assert_in "$code" "200 304 404" "artwork for $slug reachable unauth (404 ok if absent)"

rm -f "$JAR"
finish_test "T12-rss-public-paths"
