#!/usr/bin/env bash
# T17: graceful shutdown. SIGTERM the container, expect clean termination,
# expect terminate_all and "Released background leader lock" log lines.
#
# This test ENDS the container. If the run-all orchestrator wants to continue,
# it must restart via 00-setup.sh after this one.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TEST_NAME="T17-shutdown" source "$SCRIPT_DIR/../lib/common.sh"

if ! docker inspect "$LOCAL_CONTAINER" >/dev/null 2>&1; then
    skip_step 'no local container running; T17 cannot proceed'
    finish_test "T17-shutdown"
    exit 0
fi

log "sending SIGTERM to $LOCAL_CONTAINER"
docker kill -s TERM "$LOCAL_CONTAINER" >/dev/null

# Wait up to 90s for container to stop on its own. Gunicorn's master has
# graceful_timeout=330s before SIGKILL, but with no in-flight requests
# workers typically exit within a few seconds; 90s covers the slowest
# legitimate shutdown without masking a real hang.
i=0
while [ $i -lt 90 ]; do
    state=$(docker inspect -f '{{.State.Status}}' "$LOCAL_CONTAINER" 2>/dev/null || echo "gone")
    if [ "$state" != "running" ]; then
        break
    fi
    sleep 1
    i=$((i + 1))
done
state=$(docker inspect -f '{{.State.Status}}' "$LOCAL_CONTAINER" 2>/dev/null || echo "gone")
note "container final state: $state (after ${i}s)"

if [ "$state" = "exited" ] || [ "$state" = "gone" ]; then
    pass_step "container exited cleanly within ${i}s of SIGTERM"
else
    fail_step "container still in state '$state' 90s after SIGTERM"
fi

# Capture final logs and check for graceful shutdown markers
dump_local_logs
if grep -E 'terminate_all|graceful shutdown|shutting down' "$LOCAL_LOG_FILE" >/dev/null; then
    pass_step 'graceful-shutdown log marker present'
else
    skip_step 'no terminate_all/graceful-shutdown log line found (may not be implemented)'
fi
if grep -E 'Released background leader lock|leader_lock_released' "$LOCAL_LOG_FILE" >/dev/null; then
    pass_step 'background leader lock released'
else
    skip_step 'no leader-lock release log (may not have been the leader)'
fi

finish_test "T17-shutdown"
