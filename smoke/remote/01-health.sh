#!/usr/bin/env bash
# T01 (remote): /health endpoint sanity. No log assertions on remote (handled
# in 15-log-hygiene.sh via Grafana MCP).
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TEST_NAME="R-T01-health" source "$SCRIPT_DIR/../lib/common.sh"

body=$(curl -s "$REMOTE_BASE/api/v1/health")
assert_match "$body" '"status"\s*:\s*"healthy"' '/health body status=healthy'
code=$(http_code "$REMOTE_BASE/api/v1/health")
assert_eq "$code" "200" '/health HTTP 200'

# Version check via /system/info or /system/status if reachable
ver=$(curl -s -b "$REMOTE_COOKIES" "$REMOTE_BASE/api/v1/system/status" \
    | python3 -c 'import json,sys
try:
    d=json.load(sys.stdin)
    print(d.get("version") or d.get("appVersion") or "")
except Exception:
    print("")' 2>/dev/null || true)
note "remote version: $ver"
# Accept any non-empty SemVer. Hardcoding a specific value here made the
# remote smoke fail on every release. Tie this to a specific version only
# when smoke is run as a post-deploy gate (set SMOKE_EXPECT_VERSION).
if [ -n "${SMOKE_EXPECT_VERSION:-}" ]; then
    if [ "$ver" = "${SMOKE_EXPECT_VERSION}" ]; then
        pass_step "remote reports version ${SMOKE_EXPECT_VERSION}"
    else
        fail_step "remote version mismatch: got '$ver', expected '${SMOKE_EXPECT_VERSION}'"
    fi
elif [ -n "$ver" ]; then
    if printf '%s' "$ver" | grep -qE '^[0-9]+\.[0-9]+\.[0-9]+'; then
        pass_step "remote reports SemVer version: $ver"
    else
        fail_step "remote version is not SemVer: '$ver'"
    fi
else
    skip_step 'could not determine remote version (auth or endpoint shape)'
fi

finish_test "R-T01-health"
