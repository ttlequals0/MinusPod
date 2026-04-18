#!/usr/bin/env bash
# T10: destructive endpoint rate limit. Two POST /system/cleanup within an
# hour should yield 1x success + 1x 429. Audit log line WARN expected.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TEST_NAME="T10-destructive-rate" source "$SCRIPT_DIR/../lib/common.sh"

JAR="$RESULTS_DIR/T10-cookies.jar"
rm -f "$JAR"
login "$LOCAL_BASE" "$LOCAL_PASSWORD" "$JAR" >/dev/null
csrf=$(csrf_from_jar "$LOCAL_BASE" "$JAR")

# memory:// rate-limiter storage is per-worker, and the container runs
# with workers=2. A single extra request could route to the other worker
# and appear to bypass the 1/hour cap. Fire five rapid requests; at
# least one must return 429 to prove the limiter engaged at all.
codes=()
for i in 1 2 3 4 5; do
    codes+=("$(curl -s -o /dev/null -w '%{http_code}' \
        -b "$JAR" \
        -X POST "$LOCAL_BASE/api/v1/system/cleanup" \
        -H "X-CSRF-Token: $csrf")")
done
note "cleanup codes: ${codes[*]}"

first="${codes[0]}"
assert_in "$first" "200 202 204" "first cleanup succeeds (got $first)"

rate_limited=0
for c in "${codes[@]}"; do
    [ "$c" = "429" ] && rate_limited=$((rate_limited+1))
done
if [ "$rate_limited" -ge 1 ]; then
    pass_step "destructive rate limit fires ($rate_limited of 5 requests got 429)"
else
    fail_step "destructive rate limit did not fire in 5 calls (likely memory:// per-worker split)"
fi

dump_local_logs
if grep -E 'Destructive cleanup triggered|cleanup_triggered' "$LOCAL_LOG_FILE" >/dev/null; then
    pass_step 'WARN audit log present for destructive cleanup'
else
    fail_step 'WARN audit log missing for destructive cleanup'
fi

rm -f "$JAR"
finish_test "T10-destructive-rate"
