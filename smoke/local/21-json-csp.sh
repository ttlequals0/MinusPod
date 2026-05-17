#!/usr/bin/env bash
# T21: JSON responses ship a locked-down Content-Security-Policy (2.4.9 fix).
#
# Prior to 2.4.9 the CSP header was only attached to text/html responses, so
# any JSON endpoint that accidentally returned text/html (misconfiguration,
# proxy rewrite) would have no CSP applied. 2.4.9 attaches
#   Content-Security-Policy: default-src 'none'; frame-ancestors 'none'
# to all application/json responses, matching the lockdown already applied to
# /feed/* RSS.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TEST_NAME="T21-json-csp" source "$SCRIPT_DIR/../lib/common.sh"

# Pick a JSON endpoint that's always reachable (no auth required, no body needed).
HEADERS=$(curl -s -i "$LOCAL_BASE/api/v1/auth/status")
csp=$(printf '%s' "$HEADERS" | awk -F': ' 'tolower($1)=="content-security-policy"{print $2}' \
    | tr -d '\r' | head -1)

if [ -z "$csp" ]; then
    fail_step 'no Content-Security-Policy header on JSON response'
    finish_test "T21-json-csp"
    exit 1
fi
note "JSON CSP: $csp"

assert_match "$csp" "default-src 'none'" "CSP includes default-src 'none'"
assert_match "$csp" "frame-ancestors 'none'" "CSP includes frame-ancestors 'none'"

# Also verify the html /ui/ CSP is still intact (not regressed by the JSON change).
HTML_HEADERS=$(curl -s -i "$LOCAL_BASE/ui/")
html_csp=$(printf '%s' "$HTML_HEADERS" | awk -F': ' 'tolower($1)=="content-security-policy"{print $2}' \
    | tr -d '\r' | head -1)
if [ -n "$html_csp" ]; then
    pass_step 'HTML /ui/ still has its CSP'
else
    fail_step 'HTML /ui/ CSP missing (json change regressed text/html branch)'
fi

# A JSON endpoint with a body (POST returning JSON) should also get the lockdown.
# /api/v1/auth/login returning 400 on missing body is a quick JSON-emitting path
# that exercises a different response shape than /auth/status.
ERR_HEADERS=$(curl -s -i -X POST "$LOCAL_BASE/api/v1/auth/login" \
    -H "Content-Type: application/json" -d '{}')
err_csp=$(printf '%s' "$ERR_HEADERS" | awk -F': ' 'tolower($1)=="content-security-policy"{print $2}' \
    | tr -d '\r' | head -1)
if [ -n "$err_csp" ] && printf '%s' "$err_csp" | grep -q "default-src 'none'"; then
    pass_step 'JSON error response also locked down'
else
    fail_step "JSON error response missing or weak CSP (got: '$err_csp')"
fi

finish_test "T21-json-csp"
