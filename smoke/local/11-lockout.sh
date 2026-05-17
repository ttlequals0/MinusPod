#!/usr/bin/env bash
# T11: account lockout. From a private IP (loopback): 6 fails should NOT lock.
# From a spoofed public IP via X-Forwarded-For (requires TRUSTED_PROXY_COUNT=1):
# 5 fails then a 6th attempt with correct password should be 429 with Retry-After.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TEST_NAME="T11-lockout" source "$SCRIPT_DIR/../lib/common.sh"

# 1) Private-IP exclusion: /auth/login is rate-limited 3/min independent
# of lockout, so we can't fire 6 unauth attempts from 127.0.0.1 without
# hitting the limiter first. Unit test tests/unit/test_auth_lockout.py
# covers the private-IP path directly; smoke only checks the public-IP
# path end-to-end here.
note 'private-IP lockout exclusion covered by tests/unit/test_auth_lockout.py'

# 2) Public IP path: 5 wrong from a real public IP.
#
# IMPORTANT: do NOT use RFC 5737 documentation ranges (192.0.2.0/24,
# 198.51.100.0/24, 203.0.113.0/24) here. Python stdlib `ipaddress`
# classifies all of those as `is_private=True` per RFC 6890, so the
# lockout machinery (`utils.validation.is_public_ip_for_lockout`)
# refuses to count failures against them and this test silently
# never trips the threshold.
#
# Cloudflare 1.1.1.1 is `is_private=False`, well-known, and the
# isolated smoke container never actually contacts it -- the IP is
# only used as a spoofed X-Forwarded-For value.
#
# /auth/login is capped at 3/min by flask-limiter, so we pace the
# attempts 22s apart to stay under the rate limit while still
# accumulating lockout-counter hits.
# Total: 5 * 22s + 22s pause = ~132s. Slow but reliable.
SPOOF="1.1.1.1"
for i in $(seq 1 5); do
    curl -s -o /dev/null -X POST "$LOCAL_BASE/api/v1/auth/login" \
        -H "Content-Type: application/json" \
        -H "X-Forwarded-For: $SPOOF" \
        -d '{"password":"wrong"}'
    # No sleep after the 5th attempt so the next correct request lands
    # inside the same lockout window.
    [ "$i" -lt 5 ] && sleep 22 || true
done

# 6th attempt (even with correct password) should be 429 with Retry-After
# from the LOCKOUT path -- not from flask-limiter. The lockout handler
# sets Retry-After; flask-limiter 429 does not include that header.
#
# Wait > 60s after the 5th failure so flask-limiter's 3/min sliding
# window clears. Without this gap, the limiter check (which runs BEFORE
# the route handler) intercepts the 6th attempt with its own 429 and the
# lockout path never runs, leaving Retry-After empty.
sleep 65

resp=$(curl -s -i -X POST "$LOCAL_BASE/api/v1/auth/login" \
    -H "Content-Type: application/json" \
    -H "X-Forwarded-For: $SPOOF" \
    -d "{\"password\":\"$LOCAL_PASSWORD\"}")
status=$(printf '%s' "$resp" | head -1 | awk '{print $2}')
retry=$(printf '%s' "$resp" | awk -F': ' 'tolower($1)=="retry-after"{print $2}' | tr -d '\r' | head -1)

assert_eq "$status" "429" 'spoofed public IP locked out after 5 failures'
[ -n "$retry" ] && pass_step "Retry-After header present (=$retry)" \
                || fail_step 'Retry-After header missing on lockout response'

finish_test "T11-lockout"
