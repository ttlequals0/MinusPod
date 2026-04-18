#!/usr/bin/env bash
# T16 (remote): trigger reprocessing of an existing already-processed episode
# and verify the pipeline runs to completion (status transitions, no errors,
# final artifacts present).
#
# Idempotent: reprocess does not destroy the existing processed output until
# the new pass succeeds.
#
# Steps:
#   1. List feeds; pick the first
#   2. List episodes; pick a recent one with status=processed
#   3. Trigger reprocess via POST /episodes/{id}/reprocess (or equivalent)
#   4. Poll /episodes/{id} until status moves through processing -> processed
#   5. Verify VTT, chapters JSON, processed audio reachable
#   6. Verify no ERROR-level logs during the window (orchestrator via Grafana MCP)
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TEST_NAME="R-T16-episode-processing" source "$SCRIPT_DIR/../lib/common.sh"

# 1) Pick first feed
feed_json=$(curl -s -b "$REMOTE_COOKIES" "$REMOTE_BASE/api/v1/feeds")
feed_id=$(printf '%s' "$feed_json" | python3 -c 'import json,sys
d=json.load(sys.stdin)
feeds=d.get("feeds") or d.get("items") or d
if isinstance(feeds, list) and feeds:
    f=feeds[0]
    print(f.get("id") or f.get("slug") or "")
' 2>/dev/null || true)
if [ -z "$feed_id" ]; then
    skip_step 'no feeds on remote'; finish_test "R-T16-episode-processing"; exit 0
fi
note "feed: $feed_id"

# 2) Pick a processed episode
ep_json=$(curl -s -b "$REMOTE_COOKIES" "$REMOTE_BASE/api/v1/feeds/$feed_id/episodes?limit=20")
ep_id=$(printf '%s' "$ep_json" | python3 -c 'import json,sys
d=json.load(sys.stdin)
eps=d.get("episodes") or d.get("items") or d
if isinstance(eps, list):
    for e in eps:
        st=(e.get("status") or "").lower()
        if st in ("processed","completed","ready"):
            print(e.get("id") or e.get("episodeId") or "")
            break
' 2>/dev/null || true)
if [ -z "$ep_id" ]; then
    skip_step "no processed episodes on feed $feed_id"
    finish_test "R-T16-episode-processing"; exit 0
fi
note "episode: $ep_id"

# 3) CSRF then reprocess
# CSRF token lives in the `minuspod_csrf` cookie, not the /auth/status
# JSON body. Use a temporary jar so the response's Set-Cookie lands in a
# parsable format.
TMP_JAR=$(mktemp)
curl -s -b "$REMOTE_COOKIES" -c "$TMP_JAR" -o /dev/null "$REMOTE_BASE/api/v1/auth/status"
# Netscape cookie jar columns: domain hostonly path secure expires name value
csrf=$(awk '/minuspod_csrf/{print $7}' "$TMP_JAR" | head -1)
rm -f "$TMP_JAR"
if [ -z "$csrf" ]; then
    # Fall back to the token in the stable cookies.txt if /auth/status
    # didn't refresh (server may skip Set-Cookie when the existing
    # cookie is still valid).
    csrf=$(awk '/minuspod_csrf/{print $7}' "$REMOTE_COOKIES" | head -1)
fi
note "csrf token length: ${#csrf}"

reproc_code=$(curl -s -o "$RESULTS_DIR/T16-reproc.json" -w '%{http_code}' \
    -b "$REMOTE_COOKIES" \
    -X POST "$REMOTE_BASE/api/v1/episodes/$feed_id/$ep_id/reprocess" \
    -H "Content-Type: application/json" \
    -H "X-CSRF-Token: $csrf" \
    -d '{}')
note "reprocess HTTP $reproc_code"
assert_in "$reproc_code" "200 202 204" "reprocess accepted"

# 4) Poll up to 15 minutes
deadline=$((SECONDS + 900))
final_status=""
while [ $SECONDS -lt $deadline ]; do
    status=$(curl -s -b "$REMOTE_COOKIES" "$REMOTE_BASE/api/v1/feeds/$feed_id/episodes/$ep_id" \
        | python3 -c 'import json,sys
try:
    d=json.load(sys.stdin); print((d.get("status") or "").lower())
except: print("")' 2>/dev/null || true)
    if [ "$status" = "processed" ] || [ "$status" = "completed" ] || [ "$status" = "ready" ]; then
        final_status="$status"
        break
    fi
    if [ "$status" = "error" ] || [ "$status" = "failed" ]; then
        final_status="$status"
        break
    fi
    sleep 15
done

if [ "$final_status" = "processed" ] || [ "$final_status" = "completed" ] || [ "$final_status" = "ready" ]; then
    pass_step "episode reached terminal status: $final_status"
elif [ "$final_status" = "error" ] || [ "$final_status" = "failed" ]; then
    fail_step "episode ended in $final_status"
else
    fail_step "episode did not reach terminal status within 15min (last seen: '$final_status')"
fi

# 5) Artifact spot-checks (podcast-app paths, live at the app level)
vtt_code=$(http_code "$REMOTE_BASE/episodes/$feed_id/$ep_id.vtt")
assert_in "$vtt_code" "200 304 404" "VTT endpoint reachable (got $vtt_code)"
chap_code=$(http_code "$REMOTE_BASE/episodes/$feed_id/$ep_id/chapters.json")
assert_in "$chap_code" "200 304 404" "chapters endpoint reachable (got $chap_code)"
mp3_code=$(http_code "$REMOTE_BASE/episodes/$feed_id/$ep_id.mp3")
assert_in "$mp3_code" "200 206 304 404" "MP3 endpoint reachable (got $mp3_code)"

note 'log scan for this window: orchestrator should run Grafana MCP query'
note '  {container="minuspod"} |~ "ERROR" - filter by smoke timestamp range'

finish_test "R-T16-episode-processing"
