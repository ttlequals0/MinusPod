#!/usr/bin/env bash
# T06: SSRF block on private/loopback IPs at feed-add. Authenticated.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TEST_NAME="T06-ssrf" source "$SCRIPT_DIR/../lib/common.sh"

JAR="$RESULTS_DIR/T06-cookies.jar"
rm -f "$JAR"
login_code=$(login "$LOCAL_BASE" "$LOCAL_PASSWORD" "$JAR")
assert_eq "$login_code" "200" 'login for SSRF test'

# Capture CSRF token from /auth/status (token is exposed via cookie + endpoint)
csrf=$(csrf_from_jar "$LOCAL_BASE" "$JAR")
note "csrf token length: ${#csrf}"

urls=(
    "http://127.0.0.1/feed.xml"
    "http://169.254.169.254/latest/meta-data/"
    "http://10.0.0.1/rss"
    "http://[::1]/rss"
    "http://localhost/rss"
    "http://192.168.1.1/rss"
)

ssrf_rejected=0
rate_limited=0
for u in "${urls[@]}"; do
    code=$(curl -s -o /dev/null -w '%{http_code}' \
        -b "$JAR" \
        -X POST "$LOCAL_BASE/api/v1/feeds" \
        -H "Content-Type: application/json" \
        -H "X-CSRF-Token: $csrf" \
        -d "{\"sourceUrl\":\"$u\"}")
    if printf '%s' "400 422 403" | grep -qw "$code"; then
        ssrf_rejected=$((ssrf_rejected+1))
        pass_step "SSRF $u rejected at validator (got $code)"
    elif [ "$code" = "429" ]; then
        rate_limited=$((rate_limited+1))
        note "SSRF $u throttled before validator ran (got 429; POST /feeds is 3/min)"
    else
        fail_step "SSRF $u unexpected response (got $code; expected 400/422/403/429)"
    fi
done

# Every URL in the list is internal or loopback; the ones that reached
# the validator must all have been rejected. At least one must reach it.
if [ "$ssrf_rejected" -ge 1 ]; then
    pass_step "SSRF validator engaged on at least one of the internal URLs ($ssrf_rejected rejected, $rate_limited throttled)"
else
    fail_step "no URL reached the SSRF validator (all $rate_limited were rate-limited)"
fi

# Verify ssrf_blocked structured event in logs
dump_local_logs
ssrf_count=$(grep -cE 'SSRF blocked|ssrf_blocked' "$LOCAL_LOG_FILE" || true)
if [ "$ssrf_count" -ge 1 ]; then
    pass_step "ssrf_blocked log events present (count=$ssrf_count)"
else
    fail_step 'ssrf_blocked log events missing'
fi

rm -f "$JAR"
finish_test "T06-ssrf"
