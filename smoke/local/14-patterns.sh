#!/usr/bin/env bash
# T14: pattern export/import round-trip. On a fresh local DB, the pattern set
# is empty/seed-only, so this is mostly a schema/contract test.
#
# Steps:
#   1. GET /patterns/export?include_corrections=true -> valid JSON
#   2. POST /patterns/import (mode=merge) the same payload back -> 200
#   3. POST /patterns/import with deliberately invalid scope -> 400
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TEST_NAME="T14-patterns" source "$SCRIPT_DIR/../lib/common.sh"

JAR="$RESULTS_DIR/T14-cookies.jar"
rm -f "$JAR"
login "$LOCAL_BASE" "$LOCAL_PASSWORD" "$JAR" >/dev/null
csrf=$(csrf_from_jar "$LOCAL_BASE" "$JAR")

EXPORT="$RESULTS_DIR/T14-patterns-export.json"
rm -f "$EXPORT"

code=$(curl -s -o "$EXPORT" -w '%{http_code}' \
    -b "$JAR" "$LOCAL_BASE/api/v1/patterns/export?include_corrections=true")
assert_eq "$code" "200" 'patterns export HTTP 200'

# Validate JSON shape
if python3 -c "
import json,sys
d=json.load(open('$EXPORT'))
assert isinstance(d, dict), 'top-level not dict'
assert 'patterns' in d, 'missing patterns key'
assert isinstance(d['patterns'], list), 'patterns not list'
print(len(d['patterns']))
" >"$RESULTS_DIR/T14-export-count.txt" 2>"$RESULTS_DIR/T14-export-error.txt"
then
    pass_step "export JSON valid (patterns: $(cat "$RESULTS_DIR/T14-export-count.txt"))"
else
    fail_step "export JSON invalid: $(cat "$RESULTS_DIR/T14-export-error.txt")"
fi

# Re-import with mode=merge (non-destructive)
python3 -c "
import json
d=json.load(open('$EXPORT'))
d['mode']='merge'
json.dump(d, open('$EXPORT.merge','w'))
"
code=$(curl -s -o /dev/null -w '%{http_code}' \
    -b "$JAR" \
    -X POST "$LOCAL_BASE/api/v1/patterns/import" \
    -H "Content-Type: application/json" \
    -H "X-CSRF-Token: $csrf" \
    --data-binary @"$EXPORT.merge")
assert_in "$code" "200 204" "merge re-import HTTP 200/204 (got $code)"

# Invalid payload should be 400
echo '{"patterns":[{"scope":"bogus_scope_xyz","text":"x"}],"mode":"replace"}' \
    > "$RESULTS_DIR/T14-bad.json"
code=$(curl -s -o /dev/null -w '%{http_code}' \
    -b "$JAR" \
    -X POST "$LOCAL_BASE/api/v1/patterns/import" \
    -H "Content-Type: application/json" \
    -H "X-CSRF-Token: $csrf" \
    --data-binary @"$RESULTS_DIR/T14-bad.json")
assert_eq "$code" "400" 'invalid pattern payload rejected with 400'

rm -f "$JAR" "$EXPORT" "$EXPORT.merge" "$RESULTS_DIR/T14-bad.json" \
      "$RESULTS_DIR/T14-export-count.txt" "$RESULTS_DIR/T14-export-error.txt"
finish_test "T14-patterns"
