#!/usr/bin/env bash
# T27: read-only settings endpoints return well-shaped JSON. Covers the
# GET surface of the settings system; mutation paths are exercised by
# T28 (round-trip) and T30 (webhooks).
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TEST_NAME="T27-settings-readonly" source "$SCRIPT_DIR/../lib/common.sh"

setup_authed_jar 27

assert_get_dict() {
    local endpoint="$1" desc="$2"
    if curl -s -b "$JAR" "$LOCAL_BASE$endpoint" | python3 -c "
import json, sys
d = json.load(sys.stdin)
assert isinstance(d, (dict, list)), 'expected dict or list'
" 2>/dev/null; then
        pass_step "$desc"
    else
        fail_step "$desc (bad shape)"
    fi
}

for ep in \
    '/api/v1/settings|GET /settings' \
    '/api/v1/settings/models|GET /settings/models' \
    '/api/v1/settings/whisper-models|GET /settings/whisper-models' \
    '/api/v1/settings/retention|GET /settings/retention' \
    '/api/v1/settings/audio|GET /settings/audio' \
    '/api/v1/settings/processing-timeouts|GET /settings/processing-timeouts' \
    '/api/v1/settings/reviewer|GET /settings/reviewer' \
    '/api/v1/settings/community-sync|GET /settings/community-sync' \
    '/api/v1/settings/webhooks|GET /settings/webhooks' \
    '/api/v1/networks|GET /networks'; do
    path="${ep%%|*}"
    desc="${ep##*|}"
    assert_get_dict "$path" "$desc"
done

finish_test "T27-settings-readonly"
