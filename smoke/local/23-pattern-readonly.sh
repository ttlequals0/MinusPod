#!/usr/bin/env bash
# T23: read-only pattern endpoints return well-shaped JSON on a clean DB.
#
# Covers: /patterns/stats, /patterns/health, /patterns/contaminated,
# /patterns?scope=..., /patterns?source=..., /patterns/deduplicate,
# /patterns/backfill-false-positives.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TEST_NAME="T23-pattern-readonly" source "$SCRIPT_DIR/../lib/common.sh"

JAR="$RESULTS_DIR/T23-cookies.jar"
rm -f "$JAR"
login "$LOCAL_BASE" "$LOCAL_PASSWORD" "$JAR" "$(smoke_ip 23)" >/dev/null
csrf=$(csrf_from_jar "$LOCAL_BASE" "$JAR")

assert_shape() {
    local endpoint="$1" required_keys="$2" desc="$3"
    local body
    body=$(curl -s -b "$JAR" "$LOCAL_BASE$endpoint")
    if ! printf '%s' "$body" | python3 -c "
import json, sys
try:
    d = json.load(sys.stdin)
except Exception as e:
    print(f'JSON parse error: {e}', file=sys.stderr)
    sys.exit(1)
if not isinstance(d, dict):
    print(f'expected dict, got {type(d).__name__}', file=sys.stderr)
    sys.exit(1)
missing = [k for k in '$required_keys'.split(',') if k not in d]
if missing:
    print(f'missing keys: {missing}', file=sys.stderr)
    sys.exit(1)
" 2>&1 >/dev/null; then
        fail_step "$desc"
        return
    fi
    pass_step "$desc"
}

assert_shape '/api/v1/patterns/stats' \
    'total,active,inactive,by_scope,no_sponsor,never_matched' \
    'GET /patterns/stats returns full shape'

assert_shape '/api/v1/patterns/health' \
    'total_patterns,healthy,issues_count,critical_count,warning_count,issues' \
    'GET /patterns/health returns full shape'

assert_shape '/api/v1/patterns/contaminated' \
    'patterns' \
    'GET /patterns/contaminated returns {patterns: [...]}'

# /patterns with filters
for f in 'scope=global' 'source=community' 'source=local' 'source=imported' 'active=false'; do
    code=$(curl -s -o /dev/null -w '%{http_code}' -b "$JAR" "$LOCAL_BASE/api/v1/patterns?$f")
    if [ "$code" = "200" ]; then
        pass_step "GET /patterns?$f returns 200"
    else
        fail_step "GET /patterns?$f returned $code"
    fi
done

# /patterns/deduplicate is idempotent on a clean DB
code=$(curl -s -o /dev/null -w '%{http_code}' \
    -b "$JAR" \
    -X POST "$LOCAL_BASE/api/v1/patterns/deduplicate" \
    -H "X-CSRF-Token: $csrf")
assert_eq "$code" "200" 'POST /patterns/deduplicate returns 200'

# /patterns/backfill-false-positives is also idempotent on a clean DB
code=$(curl -s -o /dev/null -w '%{http_code}' \
    -b "$JAR" \
    -X POST "$LOCAL_BASE/api/v1/patterns/backfill-false-positives" \
    -H "X-CSRF-Token: $csrf")
assert_eq "$code" "200" 'POST /patterns/backfill-false-positives returns 200'

rm -f "$JAR"
finish_test "T23-pattern-readonly"
