#!/usr/bin/env bash
# T04: slug traversal/edge inputs at /<slug> RSS endpoint must NEVER return 200
# with sensitive content; expect 400/404. Empty 200 (default index) doesn't count
# as a fail unless body looks like sensitive data.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TEST_NAME="T04-slug-validation" source "$SCRIPT_DIR/../lib/common.sh"

slugs=(
    "../etc"
    "..%2Fetc"
    ".hidden"
    "foo/bar"
    "foo\\bar"
    "null%00byte"
    "%2e%2e%2fpasswd"
)

for slug in "${slugs[@]}"; do
    code=$(http_code "$LOCAL_BASE/$slug")
    assert_in "$code" "400 404 301 302 308" "slug '$slug' rejected (got $code)"
done

finish_test "T04-slug-validation"
