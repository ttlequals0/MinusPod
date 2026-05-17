#!/usr/bin/env bash
# T19: session rotation on login (2.4.9 security fix).
#
# Flask's signed-cookie session is mutated in place by `session['authenticated']=True`,
# which leaves the same cookie identifier in use. A network attacker who can plant a
# cookie pre-login (XSS on a sibling subdomain, MITM with insecure transport) would
# then retain that same cookie after the victim logs in.
#
# 2.4.9 fixed this by calling `session.clear()` before re-setting `permanent=True`
# and `authenticated=True` in both /auth/login and /auth/password. The cookie value
# after a successful login must therefore differ from the cookie value held before.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TEST_NAME="T19-session-rotation" source "$SCRIPT_DIR/../lib/common.sh"

JAR_PRE="$RESULTS_DIR/T19-pre.jar"
JAR_POST="$RESULTS_DIR/T19-post.jar"
rm -f "$JAR_PRE" "$JAR_POST"

# 1) Seed a session cookie by hitting an unauthenticated endpoint.
curl -s -c "$JAR_PRE" -o /dev/null "$LOCAL_BASE/api/v1/auth/status"
pre_session=$(awk '$6 == "session" {print $7}' "$JAR_PRE" | head -1)

if [ -z "$pre_session" ]; then
    skip_step 'no session cookie set on unauthenticated /auth/status; nothing to rotate against'
    finish_test "T19-session-rotation"
    exit 0
fi
note "pre-login session cookie length: ${#pre_session}"

# 2) Login carrying the pre-existing session cookie. -b reads, -c writes.
cp "$JAR_PRE" "$JAR_POST"
code=$(curl -s -o /dev/null -w '%{http_code}' \
    -b "$JAR_POST" -c "$JAR_POST" \
    -X POST "$LOCAL_BASE/api/v1/auth/login" \
    -H "Content-Type: application/json" \
    -d "{\"password\":\"$LOCAL_PASSWORD\"}")
assert_eq "$code" "200" 'login HTTP 200'

# 3) Read the post-login session cookie.
post_session=$(awk '$6 == "session" {print $7}' "$JAR_POST" | head -1)
if [ -z "$post_session" ]; then
    fail_step 'no session cookie present after login'
    finish_test "T19-session-rotation"
    exit 1
fi
note "post-login session cookie length: ${#post_session}"

# 4) The two must differ. Flask's signed cookies are deterministic for the same
# session payload, so a non-rotated session would yield identical cookie strings.
if [ "$pre_session" = "$post_session" ]; then
    fail_step 'session cookie unchanged after login -- rotation regressed (CVE-class)'
else
    pass_step 'session cookie rotated on login'
fi

# 5) The authenticated-session GET should now succeed.
auth_status=$(curl -s -b "$JAR_POST" "$LOCAL_BASE/api/v1/auth/status" \
    | python3 -c 'import json,sys; print(json.load(sys.stdin).get("authenticated"))' \
    2>/dev/null || echo "")
assert_eq "$auth_status" "True" 'post-rotation cookie authenticates'

rm -f "$JAR_PRE" "$JAR_POST"
finish_test "T19-session-rotation"
