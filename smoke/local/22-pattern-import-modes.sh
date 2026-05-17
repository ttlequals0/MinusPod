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

setup_authed_jar 22

SPONSOR="smoke-T22-$(date +%s)"
PAYLOAD=$(python3 -c "
import json
print(json.dumps({
    'patterns': [{
        'scope': 'global',
        'text_template': 'This episode is brought to you by ${SPONSOR}. Visit ${SPONSOR}.com/podcast for 10%% off your first order.',
        'intro_variants': ['This episode is brought to you by'],
        'outro_variants': ['10% off your first order'],
        'sponsor': '${SPONSOR}',
    }],
    'mode': 'merge',
}))
")

# 1. mode=merge inserts the new pattern (imported=1).
imported=$(auth_json POST /api/v1/patterns/import 22 "$PAYLOAD" | json_get imported)
assert_eq "$imported" "1" 'merge mode inserts new pattern (imported=1)'

# 2. mode=supplement on the same payload skips (existing match).
SUPP=$(python3 -c "import json,sys; d=json.loads(sys.stdin.read()); d['mode']='supplement'; print(json.dumps(d))" <<<"$PAYLOAD")
skipped=$(auth_json POST /api/v1/patterns/import 22 "$SUPP" | json_get skipped)
assert_eq "$skipped" "1" 'supplement mode skips existing pattern (skipped=1)'

# 3. mode=replace with EMPTY patterns array -> 400 (fat-finger guard).
code=$(auth_code POST /api/v1/patterns/import 22 '{"patterns":[],"mode":"replace"}')
assert_eq "$code" "400" 'replace with empty array rejected (400)'

# 4. mode=invalid -> 400.
code=$(auth_code POST /api/v1/patterns/import 22 \
    '{"patterns":[{"scope":"global","text_template":"x"}],"mode":"badmode"}')
assert_eq "$code" "400" 'invalid mode rejected (400)'

# Cleanup
pattern_id=$(find_pattern_id_by_sponsor "$SPONSOR")
[ -n "$pattern_id" ] && bulk_delete_pattern 22 "$pattern_id"

finish_test "T22-pattern-import-modes"
