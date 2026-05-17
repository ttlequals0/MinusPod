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
# Optional $4 = X-Forwarded-For value. Pass a unique IP per test to avoid
# starving the per-IP /auth/login rate limit (3/min, 10/hour) when many
# tests in the same suite log in back-to-back. Echoes HTTP code.
login() {
    local base="$1" password="$2" jar="$3" xff="${4:-}"
    local hdr=()
    if [ -n "$xff" ]; then
        hdr+=(-H "X-Forwarded-For: $xff")
    fi
    curl -s -o /dev/null -w '%{http_code}' \
        -c "$jar" \
        -X POST "$base/api/v1/auth/login" \
        -H "Content-Type: application/json" \
        "${hdr[@]}" \
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


# Per-test, per-run unique private IPv4 to spoof in X-Forwarded-For.
# /patterns/import and other write endpoints rate-limit at 3/hour per IP
# under the memory:// backend, so reusing the same IP across runs (or
# across multiple tests in one run) starves the quota and the assertion
# fails for the wrong reason. We derive the third+fourth octets from a
# nonce so each test in each run gets fresh quota; second octet stays
# per-test so the IP also tells you which test issued the request.
#
# Usage: ip=$(smoke_ip 22)   # -> 10.22.137.42 (or similar)
smoke_ip() {
    local test_octet="${1:-99}"
    local nonce_pid=$$
    local nonce_t=$(date +%N | sed 's/^0*//')
    local third=$(( (nonce_pid + nonce_t / 1000) % 254 + 1 ))
    local fourth=$(( (nonce_pid * 7 + nonce_t / 100) % 254 + 1 ))
    printf '10.%d.%d.%d' "$test_octet" "$third" "$fourth"
}


# Per-test setup: create a cookie jar at $RESULTS_DIR/T<n>-cookies.jar,
# login from a per-run unique X-Forwarded-For IP, grab the CSRF token,
# and register an EXIT trap so the jar is removed even if the test bails
# on an assertion failure. Exports globals JAR and CSRF for the caller.
#
# Usage: setup_authed_jar <test_num>       # e.g. setup_authed_jar 22
setup_authed_jar() {
    local test_num="$1"
    JAR="$RESULTS_DIR/T${test_num}-cookies.jar"
    rm -f "$JAR"
    login "$LOCAL_BASE" "$LOCAL_PASSWORD" "$JAR" "$(smoke_ip "$test_num")" >/dev/null
    CSRF=$(csrf_from_jar "$LOCAL_BASE" "$JAR")
    # shellcheck disable=SC2064  # we WANT $JAR expanded at trap-set time
    trap "rm -f \"$JAR\"" EXIT
}


# Authenticated JSON-body POST/PUT/DELETE. Returns the response body on
# stdout. Use json_get to extract fields. Test number controls the X-FF
# IP so each test gets its own per-IP rate-limit quota.
#
# Usage: auth_json <method> <path> <test_num> <json-body>
# Example: body=$(auth_json POST /api/v1/patterns/import 22 "$PAYLOAD")
auth_json() {
    local method="$1" path="$2" test_num="$3" body="$4"
    curl -s -b "$JAR" \
        -X "$method" "$LOCAL_BASE$path" \
        -H "Content-Type: application/json" \
        -H "X-Forwarded-For: $(smoke_ip "$test_num")" \
        -H "X-CSRF-Token: $CSRF" \
        -d "$body"
}


# Same shape as auth_json but returns the HTTP code instead of the body.
# Use when the assertion is "expect this status code".
auth_code() {
    local method="$1" path="$2" test_num="$3" body="${4:-}"
    local data_flag=()
    if [ -n "$body" ]; then
        data_flag=(-H "Content-Type: application/json" -d "$body")
    fi
    curl -s -o /dev/null -w '%{http_code}' \
        -b "$JAR" \
        -X "$method" "$LOCAL_BASE$path" \
        -H "X-Forwarded-For: $(smoke_ip "$test_num")" \
        -H "X-CSRF-Token: $CSRF" \
        "${data_flag[@]}"
}


# Extract a top-level JSON field from stdin. Returns the value as a string
# (Python `print(value)`), or empty if the key is missing / parse fails.
#
# Usage: name=$(curl ... | json_get name)
json_get() {
    local key="$1"
    python3 -c "
import json, sys
try:
    d = json.load(sys.stdin)
    v = d.get('$key', '') if isinstance(d, dict) else ''
    print(v if v is not None else '')
except Exception:
    print('')
" 2>/dev/null
}


# Return the id of the first ad_pattern matching the given sponsor name,
# or empty if not found. Used by pattern-CRUD tests to look up the row
# they just imported (the import endpoint returns counts, not ids).
find_pattern_id_by_sponsor() {
    local sponsor="$1"
    curl -s -b "$JAR" "$LOCAL_BASE/api/v1/patterns" | python3 -c "
import json, sys
sponsor = '$sponsor'
try:
    d = json.load(sys.stdin)
    for p in d.get('patterns', []):
        if p.get('sponsor') == sponsor:
            print(p['id'])
            break
except Exception:
    pass
" 2>/dev/null
}


# Import a single test pattern with the given sponsor name + ad text.
# Idempotent on conflict (mode=merge); returns the assigned id by looking
# it up after the import lands. Test number controls the X-FF IP so the
# import doesn't share the 3/hour quota with other tests in the same run.
#
# Usage: id=$(import_test_pattern 22 "$SPONSOR" "$AD_TEXT")
import_test_pattern() {
    local test_num="$1" sponsor="$2" text="$3"
    local payload
    payload=$(python3 -c "
import json
print(json.dumps({
    'patterns': [{
        'scope': 'global',
        'text_template': '''$text''',
        'intro_variants': ['This episode is brought to you by'],
        'sponsor': '$sponsor',
    }],
    'mode': 'merge',
}))
")
    auth_json POST /api/v1/patterns/import "$test_num" "$payload" >/dev/null
    find_pattern_id_by_sponsor "$sponsor"
}


# Hard-delete a single pattern by id, satisfying the bulk-delete
# expected_count guard. Silent on failure so cleanup paths don't mask the
# actual test result. Test number controls the X-FF IP.
bulk_delete_pattern() {
    local test_num="$1" id="$2"
    auth_code POST /api/v1/patterns/bulk-delete "$test_num" \
        "{\"ids\":[$id],\"confirm\":true,\"expected_count\":1}" >/dev/null
}


# Extract a response header value from a full `curl -i` dump. Case-
# insensitive header name; returns empty if absent.
#
# Usage: csp=$(printf '%s' "$response" | header_value content-security-policy)
header_value() {
    local name="$1"
    awk -F': ' -v h="$name" 'tolower($1)==tolower(h){print $2}' | tr -d '\r' | head -1
}


# Poll a GET endpoint until the JSON field equals the expected value (or
# tries are exhausted). Useful for cache-backed endpoints (e.g. settings
# under the memory:// limiter where a PUT can invalidate one worker's
# cache but a subsequent GET lands on the other worker). Prints the last
# observed value to stdout.
#
# Usage: got=$(poll_for /api/v1/settings/retention retentionDays 42)
poll_for() {
    local endpoint="$1" key="$2" expected="$3" tries="${4:-10}"
    local got=""
    for _ in $(seq 1 "$tries"); do
        got=$(curl -s -b "$JAR" "$LOCAL_BASE$endpoint" | json_get "$key")
        [ "$got" = "$expected" ] && break
    done
    printf '%s' "$got"
}
