#!/usr/bin/env bash
# T18: with workers=2 and memory:// rate limiter, the effective limit is 2x
# the declared per-worker limit. Fire 40 wrong-password logins, expect at
# least some 429s (rate limit eventually engages even at 2x).
#
# Run before T17 since T17 stops the container.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TEST_NAME="T18-multi-worker" source "$SCRIPT_DIR/../lib/common.sh"

count_429=0
count_other=0
codes_seen=""
for i in $(seq 1 40); do
    # Single source IP so rate limiter keys the bucket consistently.
    # MINUSPOD_TRUSTED_PROXY_COUNT=1 in smoke, so ProxyFix honors this header.
    code=$(curl -s -o /dev/null -w '%{http_code}' \
        -X POST "$LOCAL_BASE/api/v1/auth/login" \
        -H "Content-Type: application/json" \
        -H "X-Forwarded-For: 198.51.100.42" \
        -d '{"password":"wrong"}')
    if [ "$code" = "429" ]; then
        count_429=$((count_429 + 1))
    else
        count_other=$((count_other + 1))
    fi
    codes_seen="$codes_seen $code"
done
note "codes seen: $codes_seen"
note "summary: 429=$count_429 other=$count_other"

if [ "$count_429" -gt 0 ]; then
    pass_step "rate limit engaged across worker pool ($count_429 / 40 were 429)"
else
    fail_step '0 / 40 requests rate-limited; rate limit may not engage with multi-worker memory:// backend'
fi

finish_test "T18-multi-worker"
