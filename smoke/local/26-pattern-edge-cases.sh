#!/usr/bin/env bash
# T26: pattern endpoints reject malformed/inapplicable requests cleanly.
# Covers /patterns/<id>/split, /patterns/merge, /patterns/<id>/protect,
# /patterns/<id>/submit-to-community on inputs that don't qualify so we
# at least exercise the route handlers' error paths without needing a
# contaminated or community-sourced fixture pattern.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TEST_NAME="T26-pattern-edge-cases" source "$SCRIPT_DIR/../lib/common.sh"

JAR="$RESULTS_DIR/T26-cookies.jar"
rm -f "$JAR"
login "$LOCAL_BASE" "$LOCAL_PASSWORD" "$JAR" "$(smoke_ip 26)" >/dev/null
csrf=$(csrf_from_jar "$LOCAL_BASE" "$JAR")

# Create one local pattern (single sponsor, single transition phrase) so the
# split/protect cases have a concrete target.
SPONSOR="smoke-T26-$(date +%s)"
PAYLOAD='{"patterns":[{"scope":"global","text_template":"Today'"'"'s sponsor is '"$SPONSOR"'. Visit '"$SPONSOR"'.com.","intro_variants":["Today'"'"'s sponsor is"],"sponsor":"'"$SPONSOR"'"}],"mode":"merge"}'
curl -s -o /dev/null -b "$JAR" \
    -X POST "$LOCAL_BASE/api/v1/patterns/import" \
    -H "Content-Type: application/json" \
    -H "X-Forwarded-For: $(smoke_ip 26)" \
    -H "X-CSRF-Token: $csrf" \
    -d "$PAYLOAD"

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
    finish_test "T26-pattern-edge-cases"
    exit 1
fi
note "test pattern id=$pattern_id (source=local, single sponsor)"

# 1. split on a non-contaminated pattern -> 400 (no transitions to split on).
code=$(curl -s -o /dev/null -w '%{http_code}' \
    -b "$JAR" \
    -X POST "$LOCAL_BASE/api/v1/patterns/$pattern_id/split" \
    -H "X-CSRF-Token: $csrf")
assert_in "$code" "400 404" "split on non-contaminated pattern rejected (got $code)"

# 2. merge with empty/single source_ids -> 400.
code=$(curl -s -o /dev/null -w '%{http_code}' \
    -b "$JAR" \
    -X POST "$LOCAL_BASE/api/v1/patterns/merge" \
    -H "Content-Type: application/json" \
    -H "X-Forwarded-For: $(smoke_ip 26)" \
    -H "X-CSRF-Token: $csrf" \
    -d "{\"source_ids\":[]}")
assert_eq "$code" "400" 'merge with empty source_ids rejected (400)'

# 3. protect on a local (non-community) pattern -> 400.
code=$(curl -s -o /dev/null -w '%{http_code}' \
    -b "$JAR" \
    -X POST "$LOCAL_BASE/api/v1/patterns/$pattern_id/protect" \
    -H "X-CSRF-Token: $csrf")
assert_eq "$code" "400" 'protect on local pattern rejected (only community can be protected)'

# 4. protect on a non-existent id -> 404.
code=$(curl -s -o /dev/null -w '%{http_code}' \
    -b "$JAR" \
    -X POST "$LOCAL_BASE/api/v1/patterns/999999999/protect" \
    -H "X-CSRF-Token: $csrf")
assert_eq "$code" "404" 'protect on missing id returns 404'

# 5. unprotect on a local pattern -> 200 (no-op). The endpoint deliberately
# does NOT enforce the community-only source guard that POST does, because
# clearing protected_from_sync on a pattern that was never protected is
# harmless and the API treats it as idempotent.
code=$(curl -s -o /dev/null -w '%{http_code}' \
    -b "$JAR" \
    -X DELETE "$LOCAL_BASE/api/v1/patterns/$pattern_id/protect" \
    -H "X-CSRF-Token: $csrf")
assert_eq "$code" "200" 'unprotect on local pattern is idempotent (200)'

# 6. submit-to-community on a missing pattern -> 400. The export pipeline
# raises ExportError before any 404-shaped lookup, so the route returns
# 400 with the rejection reasons rather than a bare 404.
code=$(curl -s -o /dev/null -w '%{http_code}' \
    -b "$JAR" \
    -X POST "$LOCAL_BASE/api/v1/patterns/999999999/submit-to-community" \
    -H "X-CSRF-Token: $csrf")
assert_in "$code" "400 404" "submit-to-community on missing id rejected (got $code)"

# Cleanup
curl -s -o /dev/null -b "$JAR" \
    -X POST "$LOCAL_BASE/api/v1/patterns/bulk-delete" \
    -H "Content-Type: application/json" \
    -H "X-Forwarded-For: $(smoke_ip 26)" \
    -H "X-CSRF-Token: $csrf" \
    -d "{\"ids\":[$pattern_id],\"confirm\":true,\"expected_count\":1}"

rm -f "$JAR"
finish_test "T26-pattern-edge-cases"
