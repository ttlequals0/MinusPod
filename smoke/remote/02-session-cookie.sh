#!/usr/bin/env bash
# T02 (remote): On HTTPS production, Set-Cookie MUST include Secure + HttpOnly
# + SameSite. We use a deliberately wrong password so we don't churn an
# active session, and inspect the Set-Cookie that WOULD be issued.
#
# NOTE: a wrong-password login will NOT issue Set-Cookie. To actually inspect
# cookie flags on remote, we hit a non-mutating authenticated endpoint with
# the existing cookies.txt and look at the Set-Cookie on the response (Flask
# refreshes session cookies on access).
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TEST_NAME="R-T02-session-cookie" source "$SCRIPT_DIR/../lib/common.sh"

resp=$(curl -s -i -b "$REMOTE_COOKIES" "$REMOTE_BASE/api/v1/system/status")
status=$(printf '%s' "$resp" | head -1 | awk '{print $2}')
note "system/status HTTP $status"

# The session cookie uses Flask's default name "session"; the CSRF cookie
# is minuspod_csrf. We inspect the SESSION cookie specifically because it
# carries HttpOnly whereas CSRF cookie must be JS-readable.
session_cookie=$(printf '%s' "$resp" | grep -iE '^set-cookie: *session=' | head -1 || true)
csrf_cookie=$(printf '%s' "$resp" | grep -iE '^set-cookie: *minuspod_csrf=' | head -1 || true)

if [ -z "$session_cookie" ]; then
    skip_step 'no session cookie refresh on /system/status (permanent session may not tick); cannot inspect flags this way'
else
    pass_step 'session Set-Cookie refresh observed'
    note "session cookie: $session_cookie"
    assert_match "$session_cookie" ';\s*[Ss]ecure' 'Secure flag PRESENT on production session cookie'
    assert_match "$session_cookie" '[Hh]ttp[Oo]nly' 'HttpOnly flag present on session cookie'
    assert_match "$session_cookie" '[Ss]ame[Ss]ite' 'SameSite attribute present on session cookie'
fi

if [ -n "$csrf_cookie" ]; then
    note "csrf cookie: $csrf_cookie"
    assert_match "$csrf_cookie" '[Ss]ame[Ss]ite=Strict' 'CSRF cookie SameSite=Strict on production'
    assert_match "$csrf_cookie" ';\s*[Ss]ecure' 'CSRF cookie has Secure flag on production HTTPS'
fi

finish_test "R-T02-session-cookie"
