#!/usr/bin/env bash
# T22: /patterns/import covers all three modes + the two 400 paths.
#
# Modes: merge (default; add new, update existing), supplement (only add
# missing), replace (wipe and re-import). The empty-replace 400 is a fat-
# finger guard so a typo doesn't wipe the table; the invalid-mode 400 keeps
# the contract tight.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TEST_NAME="T22-pattern-import-modes" source "$SCRIPT_DIR/../lib/common.sh"

JAR="$RESULTS_DIR/T22-cookies.jar"
rm -f "$JAR"
login "$LOCAL_BASE" "$LOCAL_PASSWORD" "$JAR" "$(smoke_ip 22)" >/dev/null
csrf=$(csrf_from_jar "$LOCAL_BASE" "$JAR")

SPONSOR="smoke-T22-$(date +%s)"
PAYLOAD="$RESULTS_DIR/T22-pattern.json"
cat > "$PAYLOAD" <<EOF
{
  "patterns": [
    {
      "scope": "global",
      "text_template": "This episode is brought to you by ${SPONSOR}. Visit ${SPONSOR}.com/podcast for 10% off your first order.",
      "intro_variants": ["This episode is brought to you by"],
      "outro_variants": ["10% off your first order"],
      "sponsor": "${SPONSOR}"
    }
  ],
  "mode": "merge"
}
EOF

post_import() {
    curl -s -o /dev/null -w '%{http_code}' \
        -b "$JAR" \
        -X POST "$LOCAL_BASE/api/v1/patterns/import" \
        -H "Content-Type: application/json" \
        -H "X-CSRF-Token: $csrf" \
        --data-binary @"$1"
}

post_import_body() {
    curl -s -b "$JAR" \
        -X POST "$LOCAL_BASE/api/v1/patterns/import" \
        -H "Content-Type: application/json" \
        -H "X-CSRF-Token: $csrf" \
        -d "$1"
}

# 1. mode=merge inserts the new pattern (imported=1).
body=$(curl -s -b "$JAR" \
    -X POST "$LOCAL_BASE/api/v1/patterns/import" \
    -H "Content-Type: application/json" \
    -H "X-Forwarded-For: $(smoke_ip 22)" \
    -H "X-CSRF-Token: $csrf" \
    --data-binary @"$PAYLOAD")
imported=$(printf '%s' "$body" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("imported",0))' 2>/dev/null || echo 0)
assert_eq "$imported" "1" 'merge mode inserts new pattern (imported=1)'

# 2. mode=supplement on the same payload skips (existing match).
SUPP="$RESULTS_DIR/T22-supplement.json"
python3 -c "import json; d=json.load(open('$PAYLOAD')); d['mode']='supplement'; json.dump(d, open('$SUPP','w'))"
body=$(curl -s -b "$JAR" \
    -X POST "$LOCAL_BASE/api/v1/patterns/import" \
    -H "Content-Type: application/json" \
    -H "X-Forwarded-For: $(smoke_ip 22)" \
    -H "X-CSRF-Token: $csrf" \
    --data-binary @"$SUPP")
skipped=$(printf '%s' "$body" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("skipped",0))' 2>/dev/null || echo 0)
assert_eq "$skipped" "1" 'supplement mode skips existing pattern (skipped=1)'

# 3. mode=replace with EMPTY patterns array -> 400 (fat-finger guard).
EMPTY_REPLACE='{"patterns":[],"mode":"replace"}'
code=$(post_import_body "$EMPTY_REPLACE" -o /dev/null -w '%{http_code}' 2>/dev/null || echo "?")
# the wrapper above swallows curl args; redo cleanly:
code=$(curl -s -o /dev/null -w '%{http_code}' \
    -b "$JAR" \
    -X POST "$LOCAL_BASE/api/v1/patterns/import" \
    -H "Content-Type: application/json" \
    -H "X-Forwarded-For: $(smoke_ip 22)" \
    -H "X-CSRF-Token: $csrf" \
    -d "$EMPTY_REPLACE")
assert_eq "$code" "400" 'replace with empty array rejected (400)'

# 4. mode=invalid -> 400.
BAD_MODE='{"patterns":[{"scope":"global","text_template":"x"}],"mode":"badmode"}'
code=$(curl -s -o /dev/null -w '%{http_code}' \
    -b "$JAR" \
    -X POST "$LOCAL_BASE/api/v1/patterns/import" \
    -H "Content-Type: application/json" \
    -H "X-Forwarded-For: $(smoke_ip 22)" \
    -H "X-CSRF-Token: $csrf" \
    -d "$BAD_MODE")
assert_eq "$code" "400" 'invalid mode rejected (400)'

# Cleanup: find the test pattern by its unique sponsor and bulk-delete.
pattern_id=$(curl -s -b "$JAR" "$LOCAL_BASE/api/v1/patterns" \
    | python3 -c "
import json, sys
try:
    d=json.load(sys.stdin)
    for p in d.get('patterns', []):
        if p.get('sponsor') == '$SPONSOR':
            print(p['id'])
            break
except Exception:
    pass
" 2>/dev/null || echo "")

if [ -n "$pattern_id" ]; then
    curl -s -o /dev/null -b "$JAR" \
        -X POST "$LOCAL_BASE/api/v1/patterns/bulk-delete" \
        -H "Content-Type: application/json" \
        -H "X-CSRF-Token: $csrf" \
        -d "{\"ids\":[$pattern_id],\"confirm\":true,\"expected_count\":1}"
    note "cleaned up test pattern id=$pattern_id"
fi

rm -f "$JAR" "$PAYLOAD" "$SUPP"
finish_test "T22-pattern-import-modes"
