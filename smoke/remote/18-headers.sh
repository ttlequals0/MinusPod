#!/usr/bin/env bash
# T18 (remote): security headers on /ui/, request-id round-trip on /health.
# HSTS expected on HTTPS (remote is HTTPS).
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TEST_NAME="R-T18-headers" source "$SCRIPT_DIR/../lib/common.sh"

resp=$(curl -s -i "$REMOTE_BASE/ui/")
get_h() { printf '%s' "$resp" | awk -F': ' "tolower(\$1)==\"$1\"{print \$2}" | tr -d '\r' | head -1; }

assert_eq "$(get_h x-frame-options)" "DENY" 'X-Frame-Options=DENY'
assert_eq "$(get_h x-content-type-options)" "nosniff" 'X-Content-Type-Options=nosniff'
assert_eq "$(get_h referrer-policy)" "strict-origin-when-cross-origin" 'Referrer-Policy'

csp=$(get_h content-security-policy)
[ -n "$csp" ] && pass_step 'CSP present' || fail_step 'CSP missing'

hsts=$(get_h strict-transport-security)
if [ -n "$hsts" ]; then
    pass_step "HSTS present: $hsts"
else
    # HSTS is opt-in via MINUSPOD_ENABLE_HSTS=true. Flag as SKIP so the
    # operator sees the surface area without failing the run when it's
    # intentionally off.
    skip_step 'HSTS not set on production; opt in with MINUSPOD_ENABLE_HSTS=true'
fi

# Request-ID round-trip
rid=$(curl -s -i "$REMOTE_BASE/api/v1/health" \
    | awk -F': ' 'tolower($1)=="x-request-id"{print $2}' | tr -d '\r' | head -1)
assert_match "$rid" '^[a-f0-9]{16}$' 'X-Request-Id is 16-char hex on /health'

finish_test "R-T18-headers"
