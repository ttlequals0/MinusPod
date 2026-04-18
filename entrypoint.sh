#!/bin/bash
#
# MinusPod container entrypoint.
#
# The container is started as root because the data volume may be mounted
# from a host path owned by a different UID (common on first run after a
# docker compose pull, or after an operator recreates the volume). We fix
# ownership, then drop privileges to the unprivileged minuspod user via
# gosu. Only root needs to live long enough to run this script; gunicorn
# runs as UID 1000 (or APP_UID / APP_GID if overridden).
#
set -euo pipefail

APP_UID=${APP_UID:-1000}
APP_GID=${APP_GID:-1000}
DATA_DIR=${DATA_DIR:-/app/data}

mkdir -p "$DATA_DIR/.cache"
mkdir -p "$DATA_DIR/podcasts"
mkdir -p "$DATA_DIR/backups"

# If the runtime UID differs from the baked-in user (e.g. operator ran
# ``docker run --user 2000``), skip the chown/drop steps; gunicorn runs
# as whatever UID the caller requested.
if [[ "$(id -u)" == "0" ]]; then
    if [[ "$(id -u minuspod)" != "$APP_UID" ]] || [[ "$(id -g minuspod)" != "$APP_GID" ]]; then
        echo "entrypoint: updating minuspod UID/GID -> ${APP_UID}/${APP_GID}"
        groupmod -o -g "$APP_GID" minuspod
        usermod -o -u "$APP_UID" -g "$APP_GID" minuspod
    fi

    # Incremental chown: only touch files that aren't already owned by
    # APP_UID. A first boot on a pre-existing volume pays a one-time scan;
    # every subsequent boot is ~milliseconds because the count of
    # unowned files is zero. A running count is logged so an operator
    # can see the migration is finishing instead of hanging.
    if command -v find >/dev/null 2>&1; then
        unowned_count=$(find "$DATA_DIR" \! -user "$APP_UID" -print 2>/dev/null | wc -l || echo 0)
        if [[ "$unowned_count" -gt 0 ]]; then
            echo "entrypoint: migrating ownership of $unowned_count entries under $DATA_DIR to ${APP_UID}:${APP_GID}"
            find "$DATA_DIR" \! -user "$APP_UID" -exec chown -h "${APP_UID}:${APP_GID}" {} + 2>/dev/null || true
        fi
    fi

    # Ensure gunicorn.conf.py is readable by the app user even if it was
    # shipped read-only by the image build.
    chown "${APP_UID}:${APP_GID}" /app/gunicorn.conf.py 2>/dev/null || true

    cd /app/src
    exec gosu minuspod gunicorn -c /app/gunicorn.conf.py main_app:app
fi

# Non-root invocation path (operator used --user). Run directly.
cd /app/src
exec gunicorn -c /app/gunicorn.conf.py main_app:app
