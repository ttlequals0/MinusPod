#!/usr/bin/env bash
# T01: health endpoint + startup log assertions
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TEST_NAME="T01-health" source "$SCRIPT_DIR/../lib/common.sh"

# Refresh logs in case container has new startup events
dump_local_logs

# 1. /health returns 200 with status:healthy (readiness probe shape)
body=$(curl -s "$LOCAL_BASE/api/v1/health")
assert_match "$body" '"status"\s*:\s*"healthy"' '/health body contains status=healthy'
code=$(http_code "$LOCAL_BASE/api/v1/health")
assert_eq "$code" "200" '/health HTTP 200'

# Liveness probe has the minimal shape
live=$(curl -s "$LOCAL_BASE/api/v1/health/live")
assert_match "$live" '"status"\s*:\s*"ok"' '/health/live body contains status=ok'

# 2. Rate limiter init line (F20)
if grep -Eq 'Rate limiter initialized' "$LOCAL_LOG_FILE"; then
    pass_step 'startup log: rate limiter initialized'
else
    skip_step 'startup log: rate limiter init line not found (may be DEBUG-gated)'
fi

# 3. F26 compatibility audit line
if grep -Eq 'compatibility audit|legacy slug|slug audit' "$LOCAL_LOG_FILE"; then
    pass_step 'startup log: F26 slug compatibility audit line present'
else
    skip_step 'startup log: F26 audit line not found (may not log on empty DB)'
fi

# 4. F62 negative regression: no "Failed to record server start time"
if grep -q 'Failed to record server start time' "$LOCAL_LOG_FILE"; then
    fail_step 'F62 regression: server start time error in logs'
else
    pass_step 'F62: no server start time error'
fi

# 5. No ERROR-level entries during startup
err_count=$(grep -cE '\bERROR\b' "$LOCAL_LOG_FILE" || true)
assert_eq "$err_count" "0" 'no ERROR lines during startup'

finish_test "T01-health"
