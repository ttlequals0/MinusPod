#!/usr/bin/env bash
# Pull ttlequals0/minuspod:2.0.0 and run isolated container on port 8001.
# Sets MINUSPOD_PASSWORD via -e so login works for tests.
#
# Idempotent: if a container with the same name already exists, it's removed.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../lib/common.sh
TEST_NAME="00-setup" source "$SCRIPT_DIR/../lib/common.sh"

IMAGE="${IMAGE:-ttlequals0/minuspod:2.0.0}"
PORT="${PORT:-8001}"

log "pulling $IMAGE"
docker pull "$IMAGE" >/dev/null

if docker inspect "$LOCAL_CONTAINER" >/dev/null 2>&1; then
    log "removing existing container $LOCAL_CONTAINER"
    docker rm -f "$LOCAL_CONTAINER" >/dev/null
fi

if docker volume inspect "$LOCAL_VOLUME" >/dev/null 2>&1; then
    log "removing existing volume $LOCAL_VOLUME"
    docker volume rm -f "$LOCAL_VOLUME" >/dev/null
fi

log "creating volume $LOCAL_VOLUME"
docker volume create "$LOCAL_VOLUME" >/dev/null

log "starting $LOCAL_CONTAINER on port $PORT"
# Env var names match the app's actual config (not MINUSPOD_* everywhere):
# SESSION_COOKIE_SECURE and LOG_LEVEL are plain; MINUSPOD_TRUSTED_PROXY_COUNT
# and MINUSPOD_MASTER_PASSPHRASE are MINUSPOD-prefixed.
docker run -d \
    --name "$LOCAL_CONTAINER" \
    --platform linux/amd64 \
    -p "${PORT}:8000" \
    -v "${LOCAL_VOLUME}:/app/data" \
    -e SESSION_COOKIE_SECURE=false \
    -e SESSION_COOKIE_SAMESITE=Lax \
    -e MINUSPOD_TRUSTED_PROXY_COUNT=1 \
    -e MINUSPOD_MASTER_PASSPHRASE=smoke-test-passphrase \
    -e ANTHROPIC_API_KEY=dummy-key-no-llm-calls-in-smoke \
    -e LOG_LEVEL=INFO \
    "$IMAGE" >/dev/null

log "waiting for /api/v1/health on $LOCAL_BASE"
if wait_for_health "$LOCAL_BASE" 90; then
    pass_step "container healthy at $LOCAL_BASE"
else
    fail_step "container did not become healthy in 90s"
    log "last 80 lines of container logs:"
    docker logs --tail 80 "$LOCAL_CONTAINER" 2>&1 | tee -a "$TEST_RESULT_FILE" >&2 || true
fi

# Provision the admin password via the first-boot PUT (no current password
# required while app_password is empty). All subsequent login-based tests
# assume LOCAL_PASSWORD is active.
log "setting initial admin password via PUT /auth/password"
pw_code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 \
    -X PUT "$LOCAL_BASE/api/v1/auth/password" \
    -H "Content-Type: application/json" \
    -d "{\"newPassword\":\"$LOCAL_PASSWORD\"}")
if [ "$pw_code" = "200" ] || [ "$pw_code" = "204" ]; then
    pass_step "initial admin password set (HTTP $pw_code)"
else
    fail_step "initial password PUT returned HTTP $pw_code (expected 200/204)"
fi

dump_local_logs
finish_test "00-setup"
