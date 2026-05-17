#!/usr/bin/env bash
# T19: session rotation on login (2.4.9 security fix).
#
# Flask's signed-cookie session is mutated in place by `session['authenticated']=True`,
# which leaves the same cookie identifier in use. A network attacker who plants a
# cookie pre-login would then retain that same cookie after the victim logs in.
# 2.4.9 fixes this by calling `session.clear()` before re-setting the auth state.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TEST_NAME="T19-session-rotation" source "$SCRIPT_DIR/../lib/common.sh"

JAR_PRE="$RESULTS_DIR/T19-pre.jar"
JAR_POST="$RESULTS_DIR/T19-post.jar"
rm -f "$JAR_PRE" "$JAR_POST"
trap "rm -f \"$JAR_PRE\" \"$JAR_POST\"" EXIT

# Seed an unauthenticated session cookie.
curl -s -c "$JAR_PRE" -o /dev/null "$LOCAL_BASE/api/v1/auth/status"
pre_session=$(awk '$6 == "session" {print $7}' "$JAR_PRE" | head -1)
if [ -z "$pre_session" ]; then
    skip_step 'no session cookie set on unauthenticated /auth/status; nothing to rotate against'
    finish_test "T19-session-rotation"
    exit 0
fi
note "pre-login session cookie length: ${#pre_session}"

# Login carrying the pre-existing session cookie. Per-run unique IP keeps
# /auth/login's 3/min rate limit from starving when this runs in a suite.
cp "$JAR_PRE" "$JAR_POST"
T19_IP=$(smoke_ip 19)
code=$(curl -s -o /dev/null -w '%{http_code}' \
    -b "$JAR_POST" -c "$JAR_POST" \
    -X POST "$LOCAL_BASE/api/v1/auth/login" \
    -H "Content-Type: application/json" \
    -H "X-Forwarded-For: $T19_IP" \
    -d "{\"password\":\"$LOCAL_PASSWORD\"}")
assert_eq "$code" "200" 'login HTTP 200'

post_session=$(awk '$6 == "session" {print $7}' "$JAR_POST" | head -1)
if [ -z "$post_session" ]; then
    fail_step 'no session cookie present after login'
    finish_test "T19-session-rotation"
    exit 1
fi
note "post-login session cookie length: ${#post_session}"

if [ "$pre_session" = "$post_session" ]; then
    fail_step 'session cookie unchanged after login -- rotation regressed (CVE-class)'
else
    pass_step 'session cookie rotated on login'
fi

auth_status=$(curl -s -b "$JAR_POST" "$LOCAL_BASE/api/v1/auth/status" | json_get authenticated)
assert_eq "$auth_status" "True" 'post-rotation cookie authenticates'

finish_test "T19-session-rotation"
