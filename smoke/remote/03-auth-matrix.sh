#!/usr/bin/env bash
# T03 (remote): exempt vs protected endpoints unauth.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TEST_NAME="R-T03-auth-matrix" source "$SCRIPT_DIR/../lib/common.sh"

for path in /api/v1/health /api/v1/auth/status; do
    code=$(http_code "$REMOTE_BASE$path")
    assert_in "$code" "200 204" "exempt $path returns 2xx unauth (got $code)"
done

# Every other /api/v1/* endpoint must 401 unauth. /status/stream used to
# be exempt-by-prefix; post-audit-gap-fix G01 removed that exemption --
# callers hitting the SSE endpoint unauth now receive JSON 401 at the
# HTTP level (no misleading 200-with-event-frame). The frontend
# GlobalStatusBar.tsx redirects to /ui/login on EventSource error +
# auth-status probe.
for path in /api/v1/feeds /api/v1/system/status /api/v1/history /api/v1/status/stream; do
    code=$(http_code "$REMOTE_BASE$path")
    assert_eq "$code" "401" "protected $path returns 401 unauth (got $code)"
done

finish_test "R-T03-auth-matrix"
