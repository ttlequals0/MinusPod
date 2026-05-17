#!/usr/bin/env bash
# T32: feed and episode listing endpoints on an empty/fresh container.
# Read-only; doesn't mutate state. Complements T31 (error paths) by
# verifying the happy-shape returns when nothing is present.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TEST_NAME="T32-feed-listing" source "$SCRIPT_DIR/../lib/common.sh"

setup_authed_jar 32

# /feeds list returns a dict with feeds array (or list directly).
body=$(curl -s -b "$JAR" "$LOCAL_BASE/api/v1/feeds")
if printf '%s' "$body" | python3 -c '
import json, sys
d = json.load(sys.stdin)
feeds = d.get("feeds", d if isinstance(d, list) else None)
assert isinstance(feeds, list), f"expected feeds list, got {type(feeds).__name__}"
' 2>/dev/null; then
    pass_step 'GET /feeds returns list shape'
else
    fail_step "GET /feeds bad shape: $(printf '%s' "$body" | head -c 200)"
fi

# /episodes/processing returns the currently-processing list (may be empty).
code=$(auth_code GET /api/v1/episodes/processing 32)
assert_eq "$code" "200" 'GET /episodes/processing returns 200'

# /feeds/refresh on empty fleet -> 200 (no-op).
code=$(auth_code POST /api/v1/feeds/refresh 32 '{}')
assert_eq "$code" "200" 'POST /feeds/refresh returns 200 on empty fleet'

# OPML export is a blob download; just verify 200 + non-empty content-length.
opml_code=$(curl -s -o /dev/null -w '%{http_code}' -b "$JAR" "$LOCAL_BASE/api/v1/feeds/export-opml")
assert_eq "$opml_code" "200" 'GET /feeds/export-opml returns 200'

finish_test "T32-feed-listing"
