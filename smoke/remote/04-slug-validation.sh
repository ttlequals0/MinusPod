#!/usr/bin/env bash
# T04 (remote): traversal-style slugs rejected.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TEST_NAME="R-T04-slug-validation" source "$SCRIPT_DIR/../lib/common.sh"

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
    code=$(http_code "$REMOTE_BASE/$slug")
    assert_in "$code" "400 404 301 302 308" "slug '$slug' rejected (got $code)"
done

finish_test "R-T04-slug-validation"
