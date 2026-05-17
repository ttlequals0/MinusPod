#!/usr/bin/env bash
# T23: read-only pattern endpoints return well-shaped JSON on a clean DB.
# Covers /patterns/stats, /patterns/health, /patterns/contaminated,
# /patterns?scope=..., /patterns?source=..., /patterns/deduplicate,
# /patterns/backfill-false-positives.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TEST_NAME="T23-pattern-readonly" source "$SCRIPT_DIR/../lib/common.sh"

setup_authed_jar 23

assert_shape() {
    local endpoint="$1" required_keys="$2" desc="$3"
    if ! curl -s -b "$JAR" "$LOCAL_BASE$endpoint" | python3 -c "
import json, sys
d = json.load(sys.stdin)
assert isinstance(d, dict), 'not a dict'
missing = [k for k in '$required_keys'.split(',') if k not in d]
assert not missing, f'missing keys: {missing}'
" 2>/dev/null; then
        fail_step "$desc"
    else
        pass_step "$desc"
    fi
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

for f in 'scope=global' 'source=community' 'source=local' 'source=imported' 'active=false'; do
    code=$(curl -s -o /dev/null -w '%{http_code}' -b "$JAR" "$LOCAL_BASE/api/v1/patterns?$f")
    assert_eq "$code" "200" "GET /patterns?$f returns 200"
done

code=$(auth_code POST /api/v1/patterns/deduplicate 23)
assert_eq "$code" "200" 'POST /patterns/deduplicate returns 200'

code=$(auth_code POST /api/v1/patterns/backfill-false-positives 23)
assert_eq "$code" "200" 'POST /patterns/backfill-false-positives returns 200'

finish_test "T23-pattern-readonly"
