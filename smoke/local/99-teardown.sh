#!/usr/bin/env bash
# Stop and remove the local container and its volume.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TEST_NAME="99-teardown" source "$SCRIPT_DIR/../lib/common.sh"

# Final logs first
dump_local_logs

if docker inspect "$LOCAL_CONTAINER" >/dev/null 2>&1; then
    log "stopping $LOCAL_CONTAINER"
    docker stop "$LOCAL_CONTAINER" >/dev/null 2>&1 || true
    log "removing $LOCAL_CONTAINER"
    docker rm -f "$LOCAL_CONTAINER" >/dev/null 2>&1 || true
    pass_step "container $LOCAL_CONTAINER removed"
else
    skip_step "container $LOCAL_CONTAINER not present"
fi

if docker volume inspect "$LOCAL_VOLUME" >/dev/null 2>&1; then
    log "removing volume $LOCAL_VOLUME"
    docker volume rm -f "$LOCAL_VOLUME" >/dev/null 2>&1 || true
    pass_step "volume $LOCAL_VOLUME removed"
else
    skip_step "volume $LOCAL_VOLUME not present"
fi

finish_test "99-teardown"
