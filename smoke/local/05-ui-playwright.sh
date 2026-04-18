#!/usr/bin/env bash
# T05: UI smoke via Playwright. This script is a stub that records that the UI
# tests must be driven by the Playwright MCP from the orchestrator (Claude
# Code session) since playwright is not installed as a CLI here.
#
# The orchestrator (Claude) navigates the local UI at $LOCAL_BASE/ui/ and
# performs the checks listed in the spec, writing to this result file.
#
# Required browser checks (the orchestrator runs these via MCP):
#   1. Login flow with $LOCAL_PASSWORD succeeds, lands on /ui/
#   2. Dashboard heading visible
#   3. Settings page renders, shows version "2.0.0"
#   4. Add-feed modal opens and accepts a URL field
#   5. Delete button uses double-click confirmation pattern
#   6. Security headers on /ui/ : X-Frame-Options=DENY,
#      X-Content-Type-Options=nosniff,
#      Referrer-Policy=strict-origin-when-cross-origin,
#      Content-Security-Policy present
#   7. /api/v1/health response includes X-Request-Id matching ^[a-f0-9]{16}$
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TEST_NAME="T05-ui-playwright" source "$SCRIPT_DIR/../lib/common.sh"

# Header-only checks we CAN do via curl here. Real UI assertions are done by
# the orchestrator via Playwright MCP.

resp=$(curl -s -i "$LOCAL_BASE/ui/")
xfo=$(printf '%s' "$resp" | awk -F': ' 'tolower($1)=="x-frame-options"{print $2}' | tr -d '\r' | head -1)
xcto=$(printf '%s' "$resp" | awk -F': ' 'tolower($1)=="x-content-type-options"{print $2}' | tr -d '\r' | head -1)
ref=$(printf '%s' "$resp" | awk -F': ' 'tolower($1)=="referrer-policy"{print $2}' | tr -d '\r' | head -1)
csp=$(printf '%s' "$resp" | awk -F': ' 'tolower($1)=="content-security-policy"{print $2}' | tr -d '\r' | head -1)

assert_eq "$xfo" "DENY" 'X-Frame-Options=DENY on /ui/'
assert_eq "$xcto" "nosniff" 'X-Content-Type-Options=nosniff on /ui/'
assert_eq "$ref" "strict-origin-when-cross-origin" 'Referrer-Policy on /ui/'
[ -n "$csp" ] && pass_step 'Content-Security-Policy present on /ui/' \
              || fail_step 'Content-Security-Policy missing on /ui/'

# Request ID round-trip on /health
rid=$(curl -s -i "$LOCAL_BASE/api/v1/health" \
    | awk -F': ' 'tolower($1)=="x-request-id"{print $2}' | tr -d '\r' | head -1)
assert_match "$rid" '^[a-f0-9]{16}$' 'X-Request-Id is 16-char hex on /health'

skip_step 'browser-based UI assertions deferred to orchestrator (Playwright MCP)'

finish_test "T05-ui-playwright"
