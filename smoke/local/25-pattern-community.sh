#!/usr/bin/env bash
# T25: community-pattern submission flow. preview-export reports which
# patterns pass the quality gate; submit-bundle hands back the JSON the
# user would commit to the community repo PR. /community-patterns/all
# is the read endpoint for the in-repo manifest.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TEST_NAME="T25-pattern-community" source "$SCRIPT_DIR/../lib/common.sh"

JAR="$RESULTS_DIR/T25-cookies.jar"
rm -f "$JAR"
login "$LOCAL_BASE" "$LOCAL_PASSWORD" "$JAR" "$(smoke_ip 25)" >/dev/null
csrf=$(csrf_from_jar "$LOCAL_BASE" "$JAR")

SPONSOR="smoke-T25-$(date +%s)"

# 1. Import a well-shaped pattern (sponsor, intro/outro, enough text) so
# preview-export has something concrete to chew on.
PAYLOAD='{"patterns":[{"scope":"global","text_template":"Today'"'"'s show is sponsored by '"$SPONSOR"'. Head over to '"$SPONSOR"'.com and use promo code SMOKE for a free trial. Cancel anytime.","intro_variants":["Today'"'"'s show is sponsored by"],"outro_variants":["Cancel anytime"],"sponsor":"'"$SPONSOR"'"}],"mode":"merge"}'
curl -s -o /dev/null -b "$JAR" \
    -X POST "$LOCAL_BASE/api/v1/patterns/import" \
    -H "Content-Type: application/json" \
    -H "X-Forwarded-For: $(smoke_ip 25)" \
    -H "X-CSRF-Token: $csrf" \
    -d "$PAYLOAD"

pattern_id=$(curl -s -b "$JAR" "$LOCAL_BASE/api/v1/patterns" \
    | python3 -c "
import json, sys
d=json.load(sys.stdin)
for p in d.get('patterns', []):
    if p.get('sponsor') == '$SPONSOR':
        print(p['id']); break
")
if [ -z "$pattern_id" ]; then
    fail_step 'could not locate imported test pattern'
    rm -f "$JAR"
    finish_test "T25-pattern-community"
    exit 1
fi
note "test pattern id=$pattern_id"

# 2. preview-export should return ready/rejected counts + per-id reasons.
body=$(curl -s -b "$JAR" \
    -X POST "$LOCAL_BASE/api/v1/patterns/preview-export" \
    -H "Content-Type: application/json" \
    -H "X-Forwarded-For: $(smoke_ip 25)" \
    -H "X-CSRF-Token: $csrf" \
    -d "{\"ids\":[$pattern_id]}")
if printf '%s' "$body" | python3 -c '
import json, sys
d=json.load(sys.stdin)
assert isinstance(d.get("ready"), (list, int)) or "ready_count" in d, "no ready/ready_count"
assert isinstance(d.get("rejected"), (list, int)) or "rejected_count" in d, "no rejected/rejected_count"
print("ok")
' >/dev/null 2>&1; then
    pass_step '/patterns/preview-export returns ready/rejected shape'
else
    fail_step "preview-export bad shape: $(printf '%s' "$body" | head -c 200)"
fi

# 3. submit-bundle should return a JSON object describing the bundle.
body=$(curl -s -b "$JAR" \
    -X POST "$LOCAL_BASE/api/v1/patterns/submit-bundle" \
    -H "Content-Type: application/json" \
    -H "X-Forwarded-For: $(smoke_ip 25)" \
    -H "X-CSRF-Token: $csrf" \
    -d "{\"ids\":[$pattern_id]}")
fmt=$(printf '%s' "$body" | python3 -c '
import json, sys
try:
    d=json.load(sys.stdin)
    # bundle envelope shape
    print(d.get("format") or d.get("type") or "ok")
except Exception:
    print("")
' 2>/dev/null)
if [ -n "$fmt" ]; then
    pass_step "/patterns/submit-bundle returns bundle JSON (format='$fmt')"
else
    fail_step "submit-bundle returned non-JSON or empty body: $(printf '%s' "$body" | head -c 200)"
fi

# 4. /community-patterns/sync-status is the GET read endpoint reporting whether
# the in-repo manifest was fetched and how many patterns it carries.
# (/community-patterns/all is DELETE-only and destructive; not exercised here.)
code=$(curl -s -o /dev/null -w '%{http_code}' \
    -b "$JAR" "$LOCAL_BASE/api/v1/community-patterns/sync-status")
assert_eq "$code" "200" 'GET /community-patterns/sync-status returns 200'

# Cleanup
curl -s -o /dev/null -b "$JAR" \
    -X POST "$LOCAL_BASE/api/v1/patterns/bulk-delete" \
    -H "Content-Type: application/json" \
    -H "X-Forwarded-For: $(smoke_ip 25)" \
    -H "X-CSRF-Token: $csrf" \
    -d "{\"ids\":[$pattern_id],\"confirm\":true,\"expected_count\":1}"

rm -f "$JAR"
finish_test "T25-pattern-community"
