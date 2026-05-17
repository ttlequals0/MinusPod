#!/usr/bin/env bash
# T35: docs, openapi spec, and /health/live (k8s-style liveness probe
# distinct from /api/v1/health which checks DB + storage).
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TEST_NAME="T35-docs-and-liveness" source "$SCRIPT_DIR/../lib/common.sh"

setup_authed_jar 35

# Swagger UI at /api/v1/docs (trailing slash variant too).
for path in /api/v1/docs /api/v1/docs/; do
    code=$(curl -s -o /dev/null -w '%{http_code}' -b "$JAR" "$LOCAL_BASE$path")
    assert_in "$code" "200 301 302 308" "GET $path returns success-ish (got $code)"
done

# openapi.yaml served as text/yaml. Use parameter expansion to peek the
# first line and extract the `version:` field; piping the full 170+ KB
# body through head/grep races head's pipe-close vs printf's write,
# triggering SIGPIPE 141 which set -o pipefail then propagates as a
# false negative.
SPEC_FILE="$RESULTS_DIR/T35-openapi.yaml"
trap "rm -f \"$SPEC_FILE\"" EXIT
curl -s -b "$JAR" "$LOCAL_BASE/api/v1/openapi.yaml" -o "$SPEC_FILE"

first_line=$(head -1 "$SPEC_FILE")
if [[ "$first_line" == openapi:* ]]; then
    pass_step '/openapi.yaml returns valid YAML starting with `openapi:`'
else
    fail_step "/openapi.yaml bad shape: first line='$first_line'"
fi

# Pull info.version from the indented `  version: X.Y.Z` line.
ver=$(awk '/^  version:/ {gsub(/^[ \t]+|[ \t]+$/, "", $2); print $2; exit}' "$SPEC_FILE")
assert_match "$ver" '^[0-9]+\.[0-9]+\.[0-9]+' "openapi info.version is SemVer (got '$ver')"

# /health/live is the cheap liveness probe (always 200 if process is up).
code=$(curl -s -o /dev/null -w '%{http_code}' "$LOCAL_BASE/api/v1/health/live")
assert_eq "$code" "200" 'GET /health/live returns 200'

# /api/v1/health is the deeper readiness probe (DB + storage).
status=$(curl -s "$LOCAL_BASE/api/v1/health" | json_get status)
assert_eq "$status" "healthy" 'GET /api/v1/health reports status=healthy'

finish_test "T35-docs-and-liveness"
