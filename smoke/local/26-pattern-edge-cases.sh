#!/usr/bin/env bash
# T26: pattern endpoints reject malformed/inapplicable requests cleanly.
# Covers /patterns/<id>/split, /patterns/merge, /patterns/<id>/protect,
# /patterns/<id>/submit-to-community on inputs that don't qualify so we
# exercise the route handlers' error paths without needing a contaminated
# or community-sourced fixture pattern.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TEST_NAME="T26-pattern-edge-cases" source "$SCRIPT_DIR/../lib/common.sh"

setup_authed_jar 26

SPONSOR="smoke-T26-$(date +%s)"
TEXT="Today's sponsor is ${SPONSOR}. Visit ${SPONSOR}.com."

pattern_id=$(import_test_pattern 26 "$SPONSOR" "$TEXT")
if [ -z "$pattern_id" ]; then
    fail_step 'could not locate imported test pattern'
    finish_test "T26-pattern-edge-cases"
    exit 1
fi
note "test pattern id=$pattern_id (source=local, single sponsor)"

# split on a non-contaminated pattern -> 400. The handler returns
# error_response with the "does not need splitting" message; 404 would
# only fire on a missing id, which we just imported, so don't accept it.
code=$(auth_code POST "/api/v1/patterns/$pattern_id/split" 26)
assert_eq "$code" "400" 'split on non-contaminated pattern returns 400'

# merge with empty source_ids -> 400.
code=$(auth_code POST /api/v1/patterns/merge 26 '{"source_ids":[]}')
assert_eq "$code" "400" 'merge with empty source_ids rejected (400)'

# protect on a local (non-community) pattern -> 400.
code=$(auth_code POST "/api/v1/patterns/$pattern_id/protect" 26)
assert_eq "$code" "400" 'protect on local pattern rejected (community-only)'

# protect on a non-existent id -> 404.
code=$(auth_code POST /api/v1/patterns/999999999/protect 26)
assert_eq "$code" "404" 'protect on missing id returns 404'

# unprotect on a local pattern -> 200 (no-op; the DELETE path deliberately
# does NOT enforce the community-only source guard that POST does).
code=$(auth_code DELETE "/api/v1/patterns/$pattern_id/protect" 26)
assert_eq "$code" "200" 'unprotect on local pattern is idempotent (200)'

# submit-to-community on a missing pattern -> 400. The export pipeline
# raises ExportError before any 404 lookup.
code=$(auth_code POST /api/v1/patterns/999999999/submit-to-community 26)
assert_eq "$code" "400" 'submit-to-community on missing id returns 400 (ExportError, not 404)'

bulk_delete_pattern 26 "$pattern_id"
finish_test "T26-pattern-edge-cases"
