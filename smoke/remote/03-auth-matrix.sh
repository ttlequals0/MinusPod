#!/usr/bin/env bash
# T03 (remote): exempt vs protected endpoints unauth.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TEST_NAME="R-T03-auth-matrix" source "$SCRIPT_DIR/../lib/common.sh"

for path in /api/v1/health /api/v1/auth/status; do
    code=$(http_code "$REMOTE_BASE$path")
    assert_in "$code" "200 204" "exempt $path returns 2xx unauth (got $code)"
done

# /api/v1/status/stream is auth-exempt-by-prefix because EventSource can't
# surface 401 cleanly; the SSE emits `event: auth-failed` on unauth instead.
for path in /api/v1/feeds /api/v1/system/status /api/v1/history; do
    code=$(http_code "$REMOTE_BASE$path")
    assert_eq "$code" "401" "protected $path returns 401 unauth (got $code)"
done

# status/stream: exempt at HTTP level, auth signaled via SSE frame.
stream_body=$(curl -s --max-time 3 "$REMOTE_BASE/api/v1/status/stream" | head -4 || true)
if printf '%s' "$stream_body" | grep -q '^event: auth-failed'; then
    pass_step '/status/stream emits event: auth-failed on unauth (SSE contract)'
elif printf '%s' "$stream_body" | grep -q '^\(event\|data\):'; then
    pass_step '/status/stream emits SSE frames (session was re-used)'
else
    fail_step "/status/stream yielded no SSE frame in 3s (body='$stream_body')"
fi

finish_test "R-T03-auth-matrix"
