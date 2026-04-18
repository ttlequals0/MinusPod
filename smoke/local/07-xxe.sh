#!/usr/bin/env bash
# T07: XXE defense. Spin up a tiny local HTTP server hosting a malicious RSS
# with an external entity, attempt to add it as a feed, expect rejection at
# parse time and an xml_forbidden_construct (or similar) log event.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TEST_NAME="T07-xxe" source "$SCRIPT_DIR/../lib/common.sh"

XXE_DIR="$RESULTS_DIR/T07-xxe-host"
mkdir -p "$XXE_DIR"
cat > "$XXE_DIR/feed.xml" <<'EOF'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE rss [
<!ENTITY xxe SYSTEM "file:///etc/passwd">
]>
<rss version="2.0">
<channel>
<title>&xxe;</title>
<link>http://example.invalid/</link>
<description>xxe test</description>
</channel>
</rss>
EOF

# Note: container is on the host network bridge by default; from inside the
# container, the host is reachable via the gateway IP. We discover it.
GATEWAY=$(docker inspect "$LOCAL_CONTAINER" \
    --format '{{range .NetworkSettings.Networks}}{{.Gateway}}{{end}}' 2>/dev/null \
    || echo "172.17.0.1")
note "container gateway: $GATEWAY"

# Pick an unused high port
HOST_PORT=18074
( cd "$XXE_DIR" && python3 -m http.server "$HOST_PORT" >/dev/null 2>&1 ) &
SERVER_PID=$!
trap 'kill "$SERVER_PID" 2>/dev/null || true' EXIT

# Wait briefly for server
sleep 1

JAR="$RESULTS_DIR/T07-cookies.jar"
rm -f "$JAR"
login "$LOCAL_BASE" "$LOCAL_PASSWORD" "$JAR" >/dev/null
csrf=$(csrf_from_jar "$LOCAL_BASE" "$JAR")

XXE_URL="http://${GATEWAY}:${HOST_PORT}/feed.xml"
note "submitting XXE feed: $XXE_URL"

code=$(curl -s -o /dev/null -w '%{http_code}' \
    -b "$JAR" \
    -X POST "$LOCAL_BASE/api/v1/feeds" \
    -H "Content-Type: application/json" \
    -H "X-CSRF-Token: $csrf" \
    -d "{\"sourceUrl\":\"$XXE_URL\"}")

# Expect 400/422 (rejected at parse). 201 would mean XXE was processed.
assert_in "$code" "400 422" "XXE feed rejected (got $code)"

dump_local_logs
xxe_log=$(grep -iE 'xml_forbidden_construct|xxe|external entity|forbidden_?dtd|entities_forbidden|dtdforbidden|defusedxml|OPML parse error|Invalid OPML|Invalid feed URL|Failed to add feed' "$LOCAL_LOG_FILE" || true)
if [ -n "$xxe_log" ]; then
    pass_step 'XXE rejection logged (defusedxml or API-level parse-error line)'
    note "log: $(printf '%s' "$xxe_log" | head -1)"
else
    fail_step 'no XXE-rejection log event found'
fi

# /etc/passwd content must NOT appear anywhere in container logs
if grep -q 'root:x:0:0:' "$LOCAL_LOG_FILE"; then
    fail_step 'CRITICAL: /etc/passwd content appears in container logs (XXE succeeded)'
else
    pass_step '/etc/passwd content absent from logs'
fi

kill "$SERVER_PID" 2>/dev/null || true
trap - EXIT
rm -f "$JAR"
finish_test "T07-xxe"
