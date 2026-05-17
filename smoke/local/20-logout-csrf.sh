#!/usr/bin/env bash
# T20: CSRF enforced on /auth/logout (2.4.9 security fix).
#
# /auth/logout is in AUTH_EXEMPT_PATHS so an expired session can still call it,
# which historically meant the blueprint-level CSRF check was skipped. 2.4.9
# added an explicit csrf.validate() inside the handler so a cross-site POST
# from a malicious origin can't terminate an authenticated session via SameSite
# fallback alone.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TEST_NAME="T20-logout-csrf" source "$SCRIPT_DIR/../lib/common.sh"

JAR="$RESULTS_DIR/T20-cookies.jar"
rm -f "$JAR"
login "$LOCAL_BASE" "$LOCAL_PASSWORD" "$JAR" >/dev/null
csrf=$(csrf_from_jar "$LOCAL_BASE" "$JAR")
if [ -z "$csrf" ]; then
    fail_step 'no CSRF token issued after login'
    finish_test "T20-logout-csrf"
    exit 1
fi
note "csrf token length: ${#csrf}"

# 1) Logout WITHOUT CSRF header -- must be rejected (403).
code=$(curl -s -o /dev/null -w '%{http_code}' \
    -b "$JAR" \
    -X POST "$LOCAL_BASE/api/v1/auth/logout")
assert_eq "$code" "403" 'logout without CSRF rejected (403)'

# 2) Session must still be authenticated after the rejected logout.
auth_after_reject=$(curl -s -b "$JAR" "$LOCAL_BASE/api/v1/auth/status" \
    | python3 -c 'import json,sys; print(json.load(sys.stdin).get("authenticated"))' \
    2>/dev/null || echo "")
assert_eq "$auth_after_reject" "True" 'session still authenticated after rejected logout'

# 3) Logout WITH CSRF header -- must succeed (200).
code=$(curl -s -o /dev/null -w '%{http_code}' \
    -b "$JAR" -c "$JAR" \
    -X POST "$LOCAL_BASE/api/v1/auth/logout" \
    -H "X-CSRF-Token: $csrf")
assert_eq "$code" "200" 'logout with CSRF succeeds (200)'

# 4) Session is now unauthenticated.
auth_after_logout=$(curl -s -b "$JAR" "$LOCAL_BASE/api/v1/auth/status" \
    | python3 -c 'import json,sys; print(json.load(sys.stdin).get("authenticated"))' \
    2>/dev/null || echo "")
# When no password is set, /auth/status returns authenticated=True for everyone;
# the smoke env always has a password configured (00-setup sets one), so this
# must be False.
assert_eq "$auth_after_logout" "False" 'session unauthenticated after logout'

# 5) Unauthenticated logout (no session) should still bypass CSRF -- the
# "always callable to clear stale state" property the route preserves.
JAR_FRESH="$RESULTS_DIR/T20-fresh.jar"
rm -f "$JAR_FRESH"
code=$(curl -s -o /dev/null -w '%{http_code}' \
    -c "$JAR_FRESH" \
    -X POST "$LOCAL_BASE/api/v1/auth/logout")
assert_eq "$code" "200" 'unauthenticated logout bypasses CSRF (200)'

rm -f "$JAR" "$JAR_FRESH"
finish_test "T20-logout-csrf"
