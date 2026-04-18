# shellcheck shell=bash
# Shared helpers for smoke tests. Source this from each script.

set -u

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
RESULTS_DIR="${RESULTS_DIR:-$REPO_ROOT/smoke/results}"
mkdir -p "$RESULTS_DIR"

LOCAL_BASE="${LOCAL_BASE:-http://localhost:8001}"
LOCAL_PASSWORD="${LOCAL_PASSWORD:-SmokeTestPass123!}"
LOCAL_CONTAINER="${LOCAL_CONTAINER:-minuspod-smoke}"
LOCAL_VOLUME="${LOCAL_VOLUME:-minuspod-smoke-data}"
LOCAL_LOG_FILE="${LOCAL_LOG_FILE:-$RESULTS_DIR/local-container.log}"

REMOTE_BASE="${REMOTE_BASE:-https://your-server.example.com}"
REMOTE_COOKIES="${REMOTE_COOKIES:-$REPO_ROOT/cookies.txt}"

# Per-test result tracking. Each test script appends to its result file with
# pass_step / fail_step. The SUMMARY.md generator reads these files.
TEST_NAME="${TEST_NAME:-unknown}"
TEST_RESULT_FILE="${TEST_RESULT_FILE:-$RESULTS_DIR/${TEST_NAME}.txt}"

# Counters per script invocation
PASS_COUNT=0
FAIL_COUNT=0

log()  { printf '[%s] %s\n' "$(date -u +%H:%M:%S)" "$*" | tee -a "$TEST_RESULT_FILE" >&2; }
note() { printf '    %s\n' "$*" | tee -a "$TEST_RESULT_FILE" >&2; }

pass_step() {
    PASS_COUNT=$((PASS_COUNT + 1))
    printf 'PASS %s\n' "$*" | tee -a "$TEST_RESULT_FILE" >&2
}

fail_step() {
    FAIL_COUNT=$((FAIL_COUNT + 1))
    printf 'FAIL %s\n' "$*" | tee -a "$TEST_RESULT_FILE" >&2
}

skip_step() {
    printf 'SKIP %s\n' "$*" | tee -a "$TEST_RESULT_FILE" >&2
}

assert_eq() {
    local actual="$1" expected="$2" desc="$3"
    if [ "$actual" = "$expected" ]; then
        pass_step "$desc (got=$actual)"
    else
        fail_step "$desc (expected=$expected got=$actual)"
    fi
}

assert_in() {
    # assert_in <actual> <space-separated-set> <desc>
    local actual="$1" set="$2" desc="$3"
    local v
    for v in $set; do
        if [ "$actual" = "$v" ]; then
            pass_step "$desc (got=$actual in {$set})"
            return
        fi
    done
    fail_step "$desc (got=$actual expected one of {$set})"
}

assert_match() {
    # assert_match <haystack> <regex> <desc>
    local haystack="$1" regex="$2" desc="$3"
    if printf '%s' "$haystack" | grep -Eq -- "$regex"; then
        pass_step "$desc"
    else
        fail_step "$desc (no match for /$regex/)"
        note "haystack head: $(printf '%s' "$haystack" | head -c 200)"
    fi
}

assert_no_match() {
    local haystack="$1" regex="$2" desc="$3"
    if printf '%s' "$haystack" | grep -Eq -- "$regex"; then
        fail_step "$desc (unexpected match for /$regex/)"
    else
        pass_step "$desc"
    fi
}

# Quiet curl with status code only. Capped at 10s so SSE and other
# slow-streaming endpoints don't hang the harness.
http_code() {
    curl -s -o /dev/null --max-time 10 -w '%{http_code}' "$@"
}

# Curl returning headers and body to stdout (HTTP/1.1 style).
http_full() {
    curl -s -i --max-time 10 "$@"
}

# Login against $1 base URL with $2 password, write cookies to $3 jar.
# Echoes HTTP code.
login() {
    local base="$1" password="$2" jar="$3"
    curl -s -o /dev/null -w '%{http_code}' \
        -c "$jar" \
        -X POST "$base/api/v1/auth/login" \
        -H "Content-Type: application/json" \
        -d "{\"password\":\"$password\"}"
}

# Print a structured result footer; called at end of each test script.
finish_test() {
    local name="${1:-$TEST_NAME}"
    local total=$((PASS_COUNT + FAIL_COUNT))
    if [ "$FAIL_COUNT" -eq 0 ] && [ "$total" -gt 0 ]; then
        printf 'RESULT %s PASS (%d/%d)\n' "$name" "$PASS_COUNT" "$total" \
            | tee -a "$TEST_RESULT_FILE" >&2
        return 0
    elif [ "$total" -eq 0 ]; then
        printf 'RESULT %s SKIP (no assertions)\n' "$name" \
            | tee -a "$TEST_RESULT_FILE" >&2
        return 0
    else
        printf 'RESULT %s FAIL (%d/%d passed)\n' "$name" "$PASS_COUNT" "$total" \
            | tee -a "$TEST_RESULT_FILE" >&2
        return 1
    fi
}

# Wait for a base URL's /api/v1/health to return 200, up to N seconds.
wait_for_health() {
    local base="$1" timeout="${2:-60}"
    local i=0
    while [ $i -lt "$timeout" ]; do
        if [ "$(http_code "$base/api/v1/health")" = "200" ]; then
            return 0
        fi
        sleep 1
        i=$((i + 1))
    done
    return 1
}

# Capture the local container's logs into LOCAL_LOG_FILE so log-hygiene tests
# can grep them.
dump_local_logs() {
    if command -v docker >/dev/null 2>&1 \
       && docker inspect "$LOCAL_CONTAINER" >/dev/null 2>&1; then
        docker logs "$LOCAL_CONTAINER" > "$LOCAL_LOG_FILE" 2>&1 || true
    fi
}

# Extract the minuspod_csrf token from a Netscape-format cookie jar.
# Refreshes the jar by hitting /auth/status first so the server
# re-issues the cookie if the existing one has rolled. Prints the
# token to stdout (empty string if absent).
#
# Usage: csrf_from_jar "$BASE_URL" "$JAR_PATH"
csrf_from_jar() {
    local base="$1" jar="$2"
    # -c writes updated cookies back; -b loads existing if present
    if [ -f "$jar" ]; then
        curl -s -b "$jar" -c "$jar" -o /dev/null --max-time 10 "$base/api/v1/auth/status"
    else
        curl -s -c "$jar" -o /dev/null --max-time 10 "$base/api/v1/auth/status"
    fi
    awk '/minuspod_csrf/{print $7}' "$jar" | head -1
}
