#!/usr/bin/env bash
# T15: log hygiene. No raw secrets, API keys, tokens or passwords in logs.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TEST_NAME="T15-log-hygiene" source "$SCRIPT_DIR/../lib/common.sh"

dump_local_logs

# Check for known secret prefixes/markers
if grep -E 'sk-ant-[A-Za-z0-9]|sk-proj-[A-Za-z0-9]|xoxb-[A-Za-z0-9]|Bearer [A-Za-z0-9._-]{16,}' "$LOCAL_LOG_FILE" \
    > "$RESULTS_DIR/T15-secret-hits.txt" 2>/dev/null
then
    fail_step 'CRITICAL: secret-like tokens found in container logs'
    head -5 "$RESULTS_DIR/T15-secret-hits.txt" | tee -a "$TEST_RESULT_FILE" >&2
else
    pass_step 'no secret-like tokens in container logs'
    rm -f "$RESULTS_DIR/T15-secret-hits.txt"
fi

# Check for credential-bearing query strings
if grep -E '(\?|&)(api[_-]?key|token|secret|password)=' "$LOCAL_LOG_FILE" \
    > "$RESULTS_DIR/T15-querystring-hits.txt" 2>/dev/null
then
    fail_step 'CRITICAL: credential-bearing query strings in logs'
    head -5 "$RESULTS_DIR/T15-querystring-hits.txt" | tee -a "$TEST_RESULT_FILE" >&2
else
    pass_step 'no credential-bearing query strings in logs'
    rm -f "$RESULTS_DIR/T15-querystring-hits.txt"
fi

# Check for the literal smoke password
if grep -F "$LOCAL_PASSWORD" "$LOCAL_LOG_FILE" >/dev/null 2>&1; then
    fail_step 'CRITICAL: smoke password text appears in container logs'
else
    pass_step 'smoke password text absent from container logs'
fi

# Tracebacks: any unhandled exceptions during the run?
tb_count=$(grep -cE '^Traceback \(most recent call last\)' "$LOCAL_LOG_FILE" || true)
if [ "$tb_count" -gt 0 ]; then
    fail_step "$tb_count Traceback(s) in container logs - investigate"
    grep -A2 'Traceback' "$LOCAL_LOG_FILE" | head -20 | tee -a "$TEST_RESULT_FILE" >&2
else
    pass_step 'no Tracebacks in container logs'
fi

finish_test "T15-log-hygiene"
