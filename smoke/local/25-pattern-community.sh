#!/usr/bin/env bash
# T25: community-pattern submission flow. preview-export reports which
# patterns pass the quality gate; submit-bundle hands back the JSON the
# user would commit to the community repo PR.
# /community-patterns/sync-status is the read endpoint reporting whether
# the in-repo manifest was fetched and how many patterns it carries.
# (/community-patterns/all is DELETE-only and destructive; not exercised.)
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TEST_NAME="T25-pattern-community" source "$SCRIPT_DIR/../lib/common.sh"

setup_authed_jar 25

SPONSOR="smoke-T25-$(date +%s)"
TEXT="Today's show is sponsored by ${SPONSOR}. Head over to ${SPONSOR}.com and use promo code SMOKE for a free trial. Cancel anytime."

pattern_id=$(import_test_pattern 25 "$SPONSOR" "$TEXT")
if [ -z "$pattern_id" ]; then
    fail_step 'could not locate imported test pattern'
    finish_test "T25-pattern-community"
    exit 1
fi
note "test pattern id=$pattern_id"

# preview-export returns ready/rejected shape.
body=$(auth_json POST /api/v1/patterns/preview-export 25 "{\"ids\":[$pattern_id]}")
if printf '%s' "$body" | python3 -c '
import json, sys
d=json.load(sys.stdin)
assert isinstance(d.get("ready"), (list, int)) or "ready_count" in d, "no ready/ready_count"
assert isinstance(d.get("rejected"), (list, int)) or "rejected_count" in d, "no rejected/rejected_count"
' 2>/dev/null; then
    pass_step '/patterns/preview-export returns ready/rejected shape'
else
    fail_step "preview-export bad shape: $(printf '%s' "$body" | head -c 200)"
fi

# submit-bundle returns a JSON object describing the bundle.
fmt=$(auth_json POST /api/v1/patterns/submit-bundle 25 "{\"ids\":[$pattern_id]}" \
    | python3 -c '
import json, sys
try:
    d=json.load(sys.stdin)
    print(d.get("format") or d.get("type") or "ok")
except Exception:
    pass
' 2>/dev/null)
if [ -n "$fmt" ]; then
    pass_step "/patterns/submit-bundle returns bundle JSON (format='$fmt')"
else
    fail_step 'submit-bundle returned non-JSON or empty body'
fi

# /community-patterns/sync-status GET.
code=$(curl -s -o /dev/null -w '%{http_code}' -b "$JAR" \
    "$LOCAL_BASE/api/v1/community-patterns/sync-status")
assert_eq "$code" "200" 'GET /community-patterns/sync-status returns 200'

bulk_delete_pattern 25 "$pattern_id"
finish_test "T25-pattern-community"
