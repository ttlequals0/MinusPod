#!/usr/bin/env bash
# T13: backup endpoint produces a valid SQLite file and emits WARN audit log.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TEST_NAME="T13-backup" source "$SCRIPT_DIR/../lib/common.sh"

JAR="$RESULTS_DIR/T13-cookies.jar"
rm -f "$JAR"
login "$LOCAL_BASE" "$LOCAL_PASSWORD" "$JAR" >/dev/null

OUT="$RESULTS_DIR/T13-backup.db"
rm -f "$OUT"

code=$(curl -s -o "$OUT" -w '%{http_code}' \
    -b "$JAR" "$LOCAL_BASE/api/v1/system/backup")
assert_eq "$code" "200" 'backup download HTTP 200'

if [ -s "$OUT" ]; then
    pass_step "backup file non-empty ($(stat -c%s "$OUT") bytes)"
else
    fail_step 'backup file empty'
fi

# Validate the backup format. With MINUSPOD_MASTER_PASSPHRASE set in the
# smoke env, the backup endpoint emits an encrypted envelope starting with
# magic bytes "MPBK01\x00". Without the passphrase it ships raw SQLite
# (header "SQLite format 3\x00"). Accept either, and if encrypted, exercise
# the standalone decrypter end-to-end so the test catches a broken envelope.
magic=$(head -c 7 "$OUT" 2>/dev/null | od -An -c | tr -d ' \n')
if printf '%s' "$magic" | grep -q '^SQLitef'; then
    pass_step 'backup file is a plaintext SQLite database (no MASTER_PASSPHRASE)'
elif printf '%s' "$magic" | grep -q '^MPBK01'; then
    pass_step 'backup file is a MPBK01 encrypted envelope (MASTER_PASSPHRASE set)'

    # Decrypt round-trip. Needs the container's per-instance salt; the
    # standalone decrypter pulls it from any SQLite file from the same
    # instance, including a copy of the live DB.
    SALT_DB="$RESULTS_DIR/T13-salt.db"
    DECRYPTED="$RESULTS_DIR/T13-backup-decrypted.db"
    rm -f "$SALT_DB" "$DECRYPTED"
    # SQLite in WAL mode buffers writes in podcast.db-wal until checkpointed.
    # The salt persisted by the backup endpoint above may still be in the WAL
    # at this point, so checkpoint via the container's python (sqlite3 CLI
    # isn't installed in the runtime image) before docker cp.
    docker exec "$LOCAL_CONTAINER" python3 -c \
        "import sqlite3; c=sqlite3.connect('/app/data/podcast.db'); c.execute('PRAGMA wal_checkpoint(FULL)'); c.close()" \
        >/dev/null 2>&1 || true
    if docker cp "$LOCAL_CONTAINER:/app/data/podcast.db" "$SALT_DB" 2>/dev/null; then
        if MINUSPOD_MASTER_PASSPHRASE="${MINUSPOD_MASTER_PASSPHRASE:-smoke-test-passphrase}" \
                python3 "$REPO_ROOT/scripts/decrypt_backup_standalone.py" \
                    --salt-db "$SALT_DB" "$OUT" "$DECRYPTED" >/dev/null 2>&1; then
            d_header=$(head -c 16 "$DECRYPTED" 2>/dev/null | tr -d '\0' || true)
            if printf '%s' "$d_header" | grep -q 'SQLite format 3'; then
                pass_step 'MPBK01 envelope decrypts to a valid SQLite database'
            else
                fail_step "decrypted file is not SQLite (header: $d_header)"
            fi
        else
            fail_step 'standalone decrypter failed'
        fi
    else
        skip_step 'docker cp of /app/data/podcast.db failed; cannot exercise decrypt round-trip'
    fi
    rm -f "$SALT_DB" "$DECRYPTED"
else
    fail_step "backup file has unknown magic (first 7 bytes: $magic)"
fi

dump_local_logs
if grep -E 'Database backup downloaded|backup_downloaded' "$LOCAL_LOG_FILE" >/dev/null; then
    pass_step 'WARN audit log present for backup download'
else
    fail_step 'WARN audit log missing for backup download'
fi

rm -f "$OUT" "$JAR"
finish_test "T13-backup"
