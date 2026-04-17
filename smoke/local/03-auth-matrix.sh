#!/usr/bin/env bash
# T03: auth-exempt matrix. Exempt endpoints answer unauth; protected endpoints 401.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TEST_NAME="T03-auth-matrix" source "$SCRIPT_DIR/../lib/common.sh"

# Exempt-by-design: /health, /auth/status always answer unauth. /status/stream
# is exempt-by-prefix because EventSource can't handle 401; it signals auth
# failure via an `event: auth-failed` SSE frame instead.
for path in /api/v1/health /api/v1/auth/status; do
    code=$(http_code "$LOCAL_BASE$path")
    assert_in "$code" "200 204" "exempt $path returns 2xx unauth"
done

# Fresh smoke container has no admin password set, so api.check_auth
# short-circuits to public. Confirming 2xx here validates the exempt
# path; the authenticated matrix is covered by 11-lockout.
for path in /api/v1/feeds /api/v1/system/status /api/v1/history; do
    code=$(http_code "$LOCAL_BASE$path")
    assert_in "$code" "200 204" "public-mode $path returns 2xx (no password set)"
done

# SSE stream: read first 4 lines within 3s and assert any SSE frame arrived.
sse_body=$(curl -s --max-time 3 "$LOCAL_BASE/api/v1/status/stream" | head -4 || true)
if printf '%s' "$sse_body" | grep -q '^\(event\|data\):'; then
    pass_step '/status/stream emits SSE frames within 3s'
else
    fail_step "/status/stream did not emit any SSE frames within 3s (body='$sse_body')"
fi

finish_test "T03-auth-matrix"
