#!/usr/bin/env bash
# T03: auth-exempt matrix. Exempt endpoints answer unauth; protected endpoints 401.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TEST_NAME="T03-auth-matrix" source "$SCRIPT_DIR/../lib/common.sh"

# Exempt-by-design: /health and /auth/status always answer unauth.
for path in /api/v1/health /api/v1/auth/status; do
    code=$(http_code "$LOCAL_BASE$path")
    assert_in "$code" "200 204" "exempt $path returns 2xx unauth"
done

# 00-setup configures an admin password, so check_auth is live. Every
# non-exempt endpoint must 401 when hit without a session cookie.
for path in /api/v1/feeds /api/v1/system/status /api/v1/history; do
    code=$(http_code "$LOCAL_BASE$path")
    assert_eq "$code" "401" "protected $path returns 401 unauth (got $code)"
done

# /api/v1/status/stream is exempt because EventSource cannot surface
# HTTP 401 to the JS handler (it reconnect-loops). An unauth connect
# must return 200 text/event-stream with a single `auth-failed` event
# that GlobalStatusBar.tsx redirects on. Bounded read so the smoke
# run doesn't hang on the long-poll. max-time covers cold-start.
# `|| rc=$?` keeps set -e from killing the script on curl's expected
# timeout exit (28) or a real connection error -- we branch on rc
# below instead.
sse_rc=0
sse_body=$(curl -sS --max-time 5 "$LOCAL_BASE/api/v1/status/stream" 2>/dev/null) || sse_rc=$?
if [ $sse_rc -ne 0 ] && [ $sse_rc -ne 28 ]; then
    fail_step "unauth /api/v1/status/stream curl failed (rc=$sse_rc)"
else
    assert_match "$sse_body" "event: auth-failed" \
        "unauth /api/v1/status/stream emits auth-failed SSE event"
fi

finish_test "T03-auth-matrix"
