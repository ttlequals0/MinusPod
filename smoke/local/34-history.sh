#!/usr/bin/env bash
# T34: history endpoints. /history lists past processing runs;
# /history/stats aggregates; /history/export returns a downloadable file.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TEST_NAME="T34-history" source "$SCRIPT_DIR/../lib/common.sh"

setup_authed_jar 34

# GET /history returns a list shape (possibly empty on a fresh DB).
body=$(curl -s -b "$JAR" "$LOCAL_BASE/api/v1/history")
if printf '%s' "$body" | python3 -c '
import json, sys
d = json.load(sys.stdin)
if isinstance(d, dict):
    items = d.get("history") or d.get("items") or d.get("entries")
    assert items is None or isinstance(items, list), "history field is not a list"
elif isinstance(d, list):
    pass
else:
    raise AssertionError(f"unexpected top-level type {type(d).__name__}")
' 2>/dev/null; then
    pass_step 'GET /history returns list-shaped JSON'
else
    fail_step "GET /history bad shape: $(printf '%s' "$body" | head -c 200)"
fi

# /history/stats returns dict (counters).
code=$(auth_code GET /api/v1/history/stats 34)
assert_eq "$code" "200" 'GET /history/stats returns 200'

# /history/export returns a downloadable blob (CSV or JSON).
code=$(curl -s -o /dev/null -w '%{http_code}' -b "$JAR" "$LOCAL_BASE/api/v1/history/export")
assert_eq "$code" "200" 'GET /history/export returns 200'

finish_test "T34-history"
