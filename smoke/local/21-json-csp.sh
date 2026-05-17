#!/usr/bin/env bash
# T21: JSON responses ship a locked-down Content-Security-Policy
# (2.4.9 fix). Prior to 2.4.9 the CSP header was only attached to
# text/html responses, so any JSON endpoint that accidentally
# returned text/html would have no CSP applied.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TEST_NAME="T21-json-csp" source "$SCRIPT_DIR/../lib/common.sh"

# /auth/status: simple GET JSON response.
csp=$(http_full "$LOCAL_BASE/api/v1/auth/status" | header_value content-security-policy)
if [ -z "$csp" ]; then
    fail_step 'no Content-Security-Policy header on JSON response'
    finish_test "T21-json-csp"
    exit 1
fi
note "JSON CSP: $csp"

assert_match "$csp" "default-src 'none'" "CSP includes default-src 'none'"
assert_match "$csp" "frame-ancestors 'none'" "CSP includes frame-ancestors 'none'"

# HTML /ui/ CSP still present (catches regressions in the text/html branch
# caused by the JSON addition).
html_csp=$(http_full "$LOCAL_BASE/ui/" | header_value content-security-policy)
if [ -n "$html_csp" ]; then
    pass_step 'HTML /ui/ still has its CSP'
else
    fail_step 'HTML /ui/ CSP missing (json change regressed text/html branch)'
fi

# JSON error response (POST /auth/login with empty body) also gets the
# lockdown -- different response shape than /auth/status.
err_csp=$(curl -s -i -X POST "$LOCAL_BASE/api/v1/auth/login" \
    -H "Content-Type: application/json" -d '{}' \
    | header_value content-security-policy)
if printf '%s' "$err_csp" | grep -q "default-src 'none'"; then
    pass_step 'JSON error response also locked down'
else
    fail_step "JSON error response missing or weak CSP (got: '$err_csp')"
fi

finish_test "T21-json-csp"
