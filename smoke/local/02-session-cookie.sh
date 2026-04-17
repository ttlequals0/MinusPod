#!/usr/bin/env bash
# T02: session cookie flags. Smoke env sets MINUSPOD_SESSION_COOKIE_SECURE=false,
# so Set-Cookie should NOT include 'Secure'. HttpOnly should be present.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TEST_NAME="T02-session-cookie" source "$SCRIPT_DIR/../lib/common.sh"

resp=$(curl -s -i -X POST "$LOCAL_BASE/api/v1/auth/login" \
    -H "Content-Type: application/json" \
    -d "{\"password\":\"$LOCAL_PASSWORD\"}")

status=$(printf '%s' "$resp" | head -1 | awk '{print $2}')
assert_eq "$status" "200" 'login returns HTTP 200'

# Flask's default session cookie name is "session"; the CSRF cookie is a
# separate Set-Cookie line. Grab the session-specific one so assertions
# describe the session cookie rather than accidentally the CSRF one.
session_cookie=$(printf '%s' "$resp" | grep -iE '^set-cookie: *session=' | head -1 || true)
csrf_cookie=$(printf '%s' "$resp" | grep -iE '^set-cookie: *minuspod_csrf=' | head -1 || true)

if [ -z "$session_cookie" ]; then
    fail_step 'no session Set-Cookie header on login response'
else
    pass_step 'session Set-Cookie header present'
    note "session cookie: $session_cookie"
    # With SESSION_COOKIE_SECURE=false, Secure flag should be absent
    assert_no_match "$session_cookie" ';\s*[Ss]ecure' 'Secure flag absent on session cookie (smoke override)'
    assert_match "$session_cookie" '[Hh]ttp[Oo]nly' 'HttpOnly flag present on session cookie'
    assert_match "$session_cookie" '[Ss]ame[Ss]ite' 'SameSite attribute present on session cookie'
fi

if [ -n "$csrf_cookie" ]; then
    note "csrf cookie: $csrf_cookie"
    assert_match "$csrf_cookie" '[Ss]ame[Ss]ite=Strict' 'CSRF cookie SameSite=Strict'
fi

finish_test "T02-session-cookie"
