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

# Validate it's an SQLite file (header bytes "SQLite format 3\0")
header=$(head -c 16 "$OUT" 2>/dev/null | tr -d '\0' || true)
if printf '%s' "$header" | grep -q 'SQLite format 3'; then
    pass_step 'backup file is a valid SQLite database'
else
    fail_step "backup file not SQLite (header: $header)"
fi

dump_local_logs
if grep -E 'Database backup downloaded|backup_downloaded' "$LOCAL_LOG_FILE" >/dev/null; then
    pass_step 'WARN audit log present for backup download'
else
    fail_step 'WARN audit log missing for backup download'
fi

rm -f "$OUT" "$JAR"
finish_test "T13-backup"
