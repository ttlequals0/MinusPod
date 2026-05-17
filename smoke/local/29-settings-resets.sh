#!/usr/bin/env bash
# T29: settings reset endpoints. /settings/ad-detection/reset and
# /settings/prompts/reset restore defaults; both should be idempotent.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TEST_NAME="T29-settings-resets" source "$SCRIPT_DIR/../lib/common.sh"

setup_authed_jar 29

# Reset ad-detection settings -> 200, idempotent on a clean DB.
code=$(auth_code POST /api/v1/settings/ad-detection/reset 29)
assert_eq "$code" "200" 'POST /settings/ad-detection/reset returns 200'

code=$(auth_code POST /api/v1/settings/ad-detection/reset 29)
assert_eq "$code" "200" 'reset is idempotent on second call'

# Reset prompts -> 200.
code=$(auth_code POST /api/v1/settings/prompts/reset 29)
assert_eq "$code" "200" 'POST /settings/prompts/reset returns 200'

# Verify the reset restored the read endpoint to a valid shape.
code=$(auth_code GET /api/v1/settings 29)
assert_eq "$code" "200" 'GET /settings returns 200 after reset'

finish_test "T29-settings-resets"
