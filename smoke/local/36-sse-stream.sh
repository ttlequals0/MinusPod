#!/usr/bin/env bash
# T36: /status/stream Server-Sent Events behavior (2.4.x).
#
# /status/stream is in AUTH_EXEMPT_PATHS because EventSource can't surface
# an HTTP 401 to JavaScript. Unauthenticated callers must receive a single
#   event: auth-failed
#   data: {}
# message and have the stream close immediately. Authenticated callers
# get an initial data frame with the current StatusService snapshot.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TEST_NAME="T36-sse-stream" source "$SCRIPT_DIR/../lib/common.sh"

setup_authed_jar 36

UNAUTH_OUT="$RESULTS_DIR/T36-unauth.txt"
AUTH_OUT="$RESULTS_DIR/T36-auth.txt"
rm -f "$UNAUTH_OUT" "$AUTH_OUT"
trap "rm -f \"$UNAUTH_OUT\" \"$AUTH_OUT\"" EXIT

# Unauthenticated stream -> auth-failed event then close.
# --max-time caps total wait; the server closes quickly after the event.
curl -s --max-time 3 "$LOCAL_BASE/api/v1/status/stream" > "$UNAUTH_OUT" 2>&1 || true
if grep -q "^event: auth-failed" "$UNAUTH_OUT"; then
    pass_step 'unauth /status/stream emits event: auth-failed'
else
    fail_step "unauth /status/stream did not emit auth-failed: $(head -c 200 "$UNAUTH_OUT")"
fi

# Authenticated stream -> initial data frame within 2s.
curl -s --max-time 3 -b "$JAR" "$LOCAL_BASE/api/v1/status/stream" > "$AUTH_OUT" 2>&1 || true
if grep -q "^data:" "$AUTH_OUT"; then
    pass_step 'authed /status/stream emits initial data frame'
else
    fail_step "authed /status/stream did not emit data: $(head -c 200 "$AUTH_OUT")"
fi

# Authed stream must NOT contain auth-failed (the snapshot was taken
# inside the request context so a valid session does not get the
# unauth event).
if grep -q "^event: auth-failed" "$AUTH_OUT"; then
    fail_step 'authed /status/stream wrongly emitted auth-failed'
else
    pass_step 'authed /status/stream does not emit auth-failed'
fi

finish_test "T36-sse-stream"
