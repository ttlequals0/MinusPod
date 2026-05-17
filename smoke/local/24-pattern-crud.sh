#!/usr/bin/env bash
# T24: per-pattern CRUD round-trip (import -> GET -> PUT -> DELETE; bulk-disable
# and bulk-delete with their fat-finger guards).
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TEST_NAME="T24-pattern-crud" source "$SCRIPT_DIR/../lib/common.sh"

setup_authed_jar 24

SPONSOR="smoke-T24-$(date +%s)"
TEXT="This episode is brought to you by ${SPONSOR}. Use code ${SPONSOR} for 15% off."

pattern_id=$(import_test_pattern 24 "$SPONSOR" "$TEXT")
if [ -z "$pattern_id" ]; then
    fail_step 'could not locate imported test pattern'
    finish_test "T24-pattern-crud"
    exit 1
fi
note "test pattern id=$pattern_id"

# GET single + GET missing.
got_sponsor=$(curl -s -b "$JAR" "$LOCAL_BASE/api/v1/patterns/$pattern_id" | json_get sponsor)
assert_eq "$got_sponsor" "$SPONSOR" 'GET /patterns/<id> returns the right pattern'

code=$(curl -s -o /dev/null -w '%{http_code}' -b "$JAR" "$LOCAL_BASE/api/v1/patterns/999999999")
assert_eq "$code" "404" 'GET /patterns/<missing-id> returns 404'

# PUT update + verify persisted.
NEW_SPONSOR="${SPONSOR}-renamed"
code=$(auth_code PUT "/api/v1/patterns/$pattern_id" 24 "{\"sponsor\":\"$NEW_SPONSOR\"}")
assert_eq "$code" "200" 'PUT /patterns/<id> with sponsor change returns 200'

got=$(curl -s -b "$JAR" "$LOCAL_BASE/api/v1/patterns/$pattern_id" | json_get sponsor)
assert_eq "$got" "$NEW_SPONSOR" 'PUT update is persisted'

# bulk-disable + the without-confirm 400.
code=$(auth_code POST /api/v1/patterns/bulk-disable 24 \
    "{\"ids\":[$pattern_id],\"confirm\":true,\"expected_count\":1}")
assert_eq "$code" "200" 'POST /patterns/bulk-disable returns 200'

code=$(auth_code POST /api/v1/patterns/bulk-disable 24 \
    "{\"ids\":[$pattern_id],\"expected_count\":1}")
assert_eq "$code" "400" 'bulk-disable without confirm rejected (400)'

# bulk-delete with mismatched expected_count -> 400 (fat-finger guard).
code=$(auth_code POST /api/v1/patterns/bulk-delete 24 \
    "{\"ids\":[$pattern_id],\"confirm\":true,\"expected_count\":99}")
assert_eq "$code" "400" 'bulk-delete with expected_count mismatch rejected (400)'

# bulk-delete with correct guard -> 200, pattern gone.
code=$(auth_code POST /api/v1/patterns/bulk-delete 24 \
    "{\"ids\":[$pattern_id],\"confirm\":true,\"expected_count\":1}")
assert_eq "$code" "200" 'bulk-delete with correct guard returns 200'

code=$(curl -s -o /dev/null -w '%{http_code}' -b "$JAR" "$LOCAL_BASE/api/v1/patterns/$pattern_id")
assert_eq "$code" "404" 'pattern is gone after bulk-delete'

finish_test "T24-pattern-crud"
