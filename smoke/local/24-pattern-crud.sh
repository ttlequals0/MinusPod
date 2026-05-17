#!/usr/bin/env bash
# T24: per-pattern CRUD round-trip (import -> GET -> PUT -> DELETE; bulk-disable
# and bulk-delete with their fat-finger guards).
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TEST_NAME="T24-pattern-crud" source "$SCRIPT_DIR/../lib/common.sh"

JAR="$RESULTS_DIR/T24-cookies.jar"
rm -f "$JAR"
login "$LOCAL_BASE" "$LOCAL_PASSWORD" "$JAR" "$(smoke_ip 24)" >/dev/null
csrf=$(csrf_from_jar "$LOCAL_BASE" "$JAR")

SPONSOR="smoke-T24-$(date +%s)"

# 1. Import a uniquely-tagged pattern.
PAYLOAD='{"patterns":[{"scope":"global","text_template":"This episode is brought to you by '"$SPONSOR"'. Use code '"$SPONSOR"' for 15% off.","intro_variants":["This episode is brought to you by"],"sponsor":"'"$SPONSOR"'"}],"mode":"merge"}'
curl -s -o /dev/null -b "$JAR" \
    -X POST "$LOCAL_BASE/api/v1/patterns/import" \
    -H "Content-Type: application/json" \
    -H "X-Forwarded-For: $(smoke_ip 24)" \
    -H "X-CSRF-Token: $csrf" \
    -d "$PAYLOAD"

# 2. Find the pattern id.
pattern_id=$(curl -s -b "$JAR" "$LOCAL_BASE/api/v1/patterns" \
    | python3 -c "
import json, sys
d=json.load(sys.stdin)
for p in d.get('patterns', []):
    if p.get('sponsor') == '$SPONSOR':
        print(p['id']); break
")
if [ -z "$pattern_id" ]; then
    fail_step 'could not locate imported test pattern'
    rm -f "$JAR"
    finish_test "T24-pattern-crud"
    exit 1
fi
note "test pattern id=$pattern_id"

# 3. GET single pattern.
body=$(curl -s -b "$JAR" "$LOCAL_BASE/api/v1/patterns/$pattern_id")
got_sponsor=$(printf '%s' "$body" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("sponsor",""))' 2>/dev/null || echo "")
assert_eq "$got_sponsor" "$SPONSOR" 'GET /patterns/<id> returns the right pattern'

# 4. GET non-existent -> 404.
code=$(curl -s -o /dev/null -w '%{http_code}' \
    -b "$JAR" "$LOCAL_BASE/api/v1/patterns/999999999")
assert_eq "$code" "404" 'GET /patterns/<missing-id> returns 404'

# 5. PUT update changes a mutable field.
NEW_SPONSOR="${SPONSOR}-renamed"
code=$(curl -s -o /dev/null -w '%{http_code}' \
    -b "$JAR" \
    -X PUT "$LOCAL_BASE/api/v1/patterns/$pattern_id" \
    -H "Content-Type: application/json" \
    -H "X-Forwarded-For: $(smoke_ip 24)" \
    -H "X-CSRF-Token: $csrf" \
    -d "{\"sponsor\":\"$NEW_SPONSOR\"}")
assert_eq "$code" "200" 'PUT /patterns/<id> with sponsor change returns 200'

# Verify the change stuck.
got=$(curl -s -b "$JAR" "$LOCAL_BASE/api/v1/patterns/$pattern_id" \
    | python3 -c 'import json,sys; print(json.load(sys.stdin).get("sponsor",""))' 2>/dev/null)
assert_eq "$got" "$NEW_SPONSOR" 'PUT update is persisted'

# 6. bulk-disable with expected_count guard.
code=$(curl -s -o /dev/null -w '%{http_code}' \
    -b "$JAR" \
    -X POST "$LOCAL_BASE/api/v1/patterns/bulk-disable" \
    -H "Content-Type: application/json" \
    -H "X-Forwarded-For: $(smoke_ip 24)" \
    -H "X-CSRF-Token: $csrf" \
    -d "{\"ids\":[$pattern_id],\"confirm\":true,\"expected_count\":1}")
assert_eq "$code" "200" 'POST /patterns/bulk-disable returns 200'

# 7. bulk-disable without confirm -> 400.
code=$(curl -s -o /dev/null -w '%{http_code}' \
    -b "$JAR" \
    -X POST "$LOCAL_BASE/api/v1/patterns/bulk-disable" \
    -H "Content-Type: application/json" \
    -H "X-Forwarded-For: $(smoke_ip 24)" \
    -H "X-CSRF-Token: $csrf" \
    -d "{\"ids\":[$pattern_id],\"expected_count\":1}")
assert_eq "$code" "400" 'bulk-disable without confirm rejected (400)'

# 8. bulk-delete with expected_count mismatch -> 400 (fat-finger guard).
code=$(curl -s -o /dev/null -w '%{http_code}' \
    -b "$JAR" \
    -X POST "$LOCAL_BASE/api/v1/patterns/bulk-delete" \
    -H "Content-Type: application/json" \
    -H "X-Forwarded-For: $(smoke_ip 24)" \
    -H "X-CSRF-Token: $csrf" \
    -d "{\"ids\":[$pattern_id],\"confirm\":true,\"expected_count\":99}")
assert_eq "$code" "400" 'bulk-delete with expected_count mismatch rejected (400)'

# 9. bulk-delete with correct guard -> 200, pattern gone.
code=$(curl -s -o /dev/null -w '%{http_code}' \
    -b "$JAR" \
    -X POST "$LOCAL_BASE/api/v1/patterns/bulk-delete" \
    -H "Content-Type: application/json" \
    -H "X-Forwarded-For: $(smoke_ip 24)" \
    -H "X-CSRF-Token: $csrf" \
    -d "{\"ids\":[$pattern_id],\"confirm\":true,\"expected_count\":1}")
assert_eq "$code" "200" 'bulk-delete with correct guard returns 200'

code=$(curl -s -o /dev/null -w '%{http_code}' \
    -b "$JAR" "$LOCAL_BASE/api/v1/patterns/$pattern_id")
assert_eq "$code" "404" 'pattern is gone after bulk-delete'

rm -f "$JAR"
finish_test "T24-pattern-crud"
