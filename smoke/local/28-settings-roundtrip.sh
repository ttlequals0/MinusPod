#!/usr/bin/env bash
# T28: settings round-trip. PUT a value, GET it back, verify it stuck.
# Covers /settings/retention, /settings/audio, /settings/processing-timeouts.
#
# poll_for handles the 2-worker memory:// cache staleness: a GET right
# after a PUT can land on the worker whose cache the PUT didn't
# invalidate. True/False here are json_get's Python `print(bool)` output;
# don't switch json_get to json.dumps without updating these comparisons.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TEST_NAME="T28-settings-roundtrip" source "$SCRIPT_DIR/../lib/common.sh"

setup_authed_jar 28

# /settings/retention round-trip + invalid rejection.
auth_json PUT /api/v1/settings/retention 28 '{"retentionDays":42}' >/dev/null
got=$(poll_for /api/v1/settings/retention retentionDays 42)
assert_eq "$got" "42" '/settings/retention PUT round-trips'

code=$(auth_code PUT /api/v1/settings/retention 28 '{"retentionDays":-1}')
assert_eq "$code" "400" '/settings/retention rejects negative value (400)'

# /settings/audio: toggle keepOriginalAudio.
auth_json PUT /api/v1/settings/audio 28 '{"keepOriginalAudio":false}' >/dev/null
got=$(poll_for /api/v1/settings/audio keepOriginalAudio False)
assert_eq "$got" "False" '/settings/audio PUT keepOriginalAudio=false round-trips'

auth_json PUT /api/v1/settings/audio 28 '{"keepOriginalAudio":true}' >/dev/null
got=$(poll_for /api/v1/settings/audio keepOriginalAudio True)
assert_eq "$got" "True" '/settings/audio PUT keepOriginalAudio=true round-trips'

code=$(auth_code PUT /api/v1/settings/audio 28 '{}')
assert_eq "$code" "400" '/settings/audio rejects missing keepOriginalAudio (400)'

# /settings/processing-timeouts: PUT both, GET back.
auth_json PUT /api/v1/settings/processing-timeouts 28 \
    '{"softTimeoutSeconds":900,"hardTimeoutSeconds":1200}' >/dev/null
soft=$(poll_for /api/v1/settings/processing-timeouts softTimeoutSeconds 900)
hard=$(poll_for /api/v1/settings/processing-timeouts hardTimeoutSeconds 1200)
assert_eq "$soft" "900" '/settings/processing-timeouts soft round-trips'
assert_eq "$hard" "1200" '/settings/processing-timeouts hard round-trips'

code=$(auth_code PUT /api/v1/settings/processing-timeouts 28 '{"softTimeoutSeconds":900}')
assert_eq "$code" "400" '/settings/processing-timeouts rejects missing hard (400)'

# F6: detection-window geometry as stage tunables. The /settings endpoint
# accepts both fields with per-field bounds and a cross-field rule
# (overlap < size). GET surfaces them under stageTunables.*.value.

# Helper: pull a nested stageTunables value via a small python one-shot.
nested_tunable() {
    local key="$1"
    curl -s -b "$JAR" "$LOCAL_BASE/api/v1/settings" | python3 -c "
import json, sys
try:
    d = json.load(sys.stdin)
    print(d.get('stageTunables', {}).get('$key', {}).get('value', ''))
except Exception:
    print('')
" 2>/dev/null
}

auth_json PUT /api/v1/settings/ad-detection 28 '{"windowSizeSeconds":300,"windowOverlapSeconds":60}' >/dev/null
sleep 0.3
size=$(nested_tunable windowSizeSeconds)
overlap=$(nested_tunable windowOverlapSeconds)
assert_eq "$size" "300" '/settings windowSizeSeconds=300 round-trips'
assert_eq "$overlap" "60" '/settings windowOverlapSeconds=60 round-trips'

# Reset to defaults so other tests don't inherit the small window.
auth_json PUT /api/v1/settings/ad-detection 28 '{"windowSizeSeconds":600,"windowOverlapSeconds":180}' >/dev/null

# Cross-field: overlap >= size is rejected.
code=$(auth_code PUT /api/v1/settings/ad-detection 28 '{"windowSizeSeconds":300,"windowOverlapSeconds":300}')
assert_eq "$code" "400" '/settings rejects overlap >= size (400)'

# Per-field bounds.
code=$(auth_code PUT /api/v1/settings/ad-detection 28 '{"windowSizeSeconds":50}')
assert_eq "$code" "400" '/settings rejects windowSizeSeconds below floor (400)'

code=$(auth_code PUT /api/v1/settings/ad-detection 28 '{"windowSizeSeconds":2000}')
assert_eq "$code" "400" '/settings rejects windowSizeSeconds above ceiling (400)'

code=$(auth_code PUT /api/v1/settings/ad-detection 28 '{"windowOverlapSeconds":-5}')
assert_eq "$code" "400" '/settings rejects negative windowOverlapSeconds (400)'

finish_test "T28-settings-roundtrip"
