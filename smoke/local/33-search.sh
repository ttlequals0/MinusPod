#!/usr/bin/env bash
# T33: search endpoints. Fresh DB so search returns empty results, but
# the routes themselves must respond cleanly.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TEST_NAME="T33-search" source "$SCRIPT_DIR/../lib/common.sh"

setup_authed_jar 33

# /search with a query returns 200 even when no episodes index it yet.
code=$(auth_code GET '/api/v1/search?q=anything' 33)
assert_eq "$code" "200" 'GET /search?q=... returns 200'

# /search/stats returns a structured object (indexed counts etc).
body=$(curl -s -b "$JAR" "$LOCAL_BASE/api/v1/search/stats")
if printf '%s' "$body" | python3 -c '
import json, sys
d = json.load(sys.stdin)
assert isinstance(d, dict), "expected dict"
' 2>/dev/null; then
    pass_step 'GET /search/stats returns dict shape'
else
    fail_step "GET /search/stats bad shape: $(printf '%s' "$body" | head -c 200)"
fi

# /search/rebuild is idempotent on a clean DB.
code=$(auth_code POST /api/v1/search/rebuild 33 '{}')
assert_eq "$code" "200" 'POST /search/rebuild returns 200'

# /podcast-search hits the Podcast Index. Without an API key it returns
# 400 or 503; with one it returns 200. Accept either since the smoke env
# doesn't configure Podcast Index credentials.
code=$(auth_code GET '/api/v1/podcast-search?q=tech' 33)
assert_in "$code" "200 400 503 502" "GET /podcast-search returns 200 (configured) or 4xx/5xx (no key) (got $code)"

finish_test "T33-search"
