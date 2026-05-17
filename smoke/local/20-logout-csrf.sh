#!/usr/bin/env bash
# T20: CSRF enforced on /auth/logout (2.4.9 security fix).
#
# /auth/logout is in AUTH_EXEMPT_PATHS so an expired session can still
# call it, which historically meant the blueprint-level CSRF check was
# skipped. 2.4.9 added an explicit csrf.validate() inside the handler so
# a cross-site POST from a malicious origin can't terminate an
# authenticated session via SameSite fallback alone.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TEST_NAME="T20-logout-csrf" source "$SCRIPT_DIR/../lib/common.sh"

setup_authed_jar 20

if [ -z "$CSRF" ]; then
    fail_step 'no CSRF token issued after login'
    finish_test "T20-logout-csrf"
    exit 1
fi
note "csrf token length: ${#CSRF}"

# Logout WITHOUT CSRF header -> 403.
code=$(curl -s -o /dev/null -w '%{http_code}' \
    -b "$JAR" \
    -X POST "$LOCAL_BASE/api/v1/auth/logout")
assert_eq "$code" "403" 'logout without CSRF rejected (403)'

# Session still authenticated after the rejected logout.
auth_after_reject=$(curl -s -b "$JAR" "$LOCAL_BASE/api/v1/auth/status" | json_get authenticated)
assert_eq "$auth_after_reject" "True" 'session still authenticated after rejected logout'

# Logout WITH CSRF header -> 200.
code=$(curl -s -o /dev/null -w '%{http_code}' \
    -b "$JAR" -c "$JAR" \
    -X POST "$LOCAL_BASE/api/v1/auth/logout" \
    -H "X-CSRF-Token: $CSRF")
assert_eq "$code" "200" 'logout with CSRF succeeds (200)'

# Session unauthenticated after the successful logout.
auth_after_logout=$(curl -s -b "$JAR" "$LOCAL_BASE/api/v1/auth/status" | json_get authenticated)
assert_eq "$auth_after_logout" "False" 'session unauthenticated after logout'

# Unauthenticated logout (no session) still bypasses CSRF: the route stays
# "always callable to clear stale state" because csrf.validate returns None
# when session.authenticated is False.
JAR_FRESH="$RESULTS_DIR/T20-fresh.jar"
rm -f "$JAR_FRESH"
code=$(curl -s -o /dev/null -w '%{http_code}' \
    -c "$JAR_FRESH" \
    -X POST "$LOCAL_BASE/api/v1/auth/logout")
assert_eq "$code" "200" 'unauthenticated logout bypasses CSRF (200)'
rm -f "$JAR_FRESH"

finish_test "T20-logout-csrf"
