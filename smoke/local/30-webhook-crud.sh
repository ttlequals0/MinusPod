#!/usr/bin/env bash
# T30: webhook lifecycle. validate-template -> create -> update ->
# test (will fail to deliver to example.invalid, that's expected) ->
# delete -> confirm gone.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TEST_NAME="T30-webhook-crud" source "$SCRIPT_DIR/../lib/common.sh"

setup_authed_jar 30

# validate-template happy path.
body=$(auth_json POST /api/v1/settings/webhooks/validate-template 30 \
    '{"template":"Episode {{episode_id}} processed at {{timestamp}}"}')
valid=$(printf '%s' "$body" | json_get valid)
assert_eq "$valid" "True" 'validate-template accepts simple template'

# validate-template rejects bad template.
body=$(auth_json POST /api/v1/settings/webhooks/validate-template 30 \
    '{"template":"{% if no_endif %}"}')
valid=$(printf '%s' "$body" | json_get valid)
assert_eq "$valid" "False" 'validate-template rejects malformed template'

# Create. example.invalid never resolves so the eventual /test below will
# fail safely -- we only check call shape, not delivery success.
body=$(auth_json POST /api/v1/settings/webhooks 30 \
    '{"url":"https://example.invalid/hook","events":["Episode Processed"]}')
webhook_id=$(printf '%s' "$body" | json_get id)
if [ -z "$webhook_id" ]; then
    fail_step "create returned no id (body: $(printf '%s' "$body" | head -c 200))"
    finish_test "T30-webhook-crud"
    exit 1
fi
pass_step "POST /settings/webhooks creates webhook (id=$webhook_id)"

# Update events.
code=$(auth_code PUT "/api/v1/settings/webhooks/$webhook_id" 30 \
    '{"events":["Episode Processed","Auth Failure"]}')
assert_eq "$code" "200" 'PUT /settings/webhooks/<id> updates events'

# GET list contains our webhook by URL.
got_url=$(curl -s -b "$JAR" "$LOCAL_BASE/api/v1/settings/webhooks" | python3 -c "
import json, sys
d = json.load(sys.stdin)
hooks = d.get('webhooks', d if isinstance(d, list) else [])
for h in hooks:
    if h.get('id') == '$webhook_id':
        print(h.get('url', '')); break
")
assert_eq "$got_url" "https://example.invalid/hook" 'GET /settings/webhooks lists created hook'

# /test endpoint. example.invalid is unresolvable so delivery fails;
# we just verify the API returns a structured {success: bool}.
body=$(auth_json POST "/api/v1/settings/webhooks/$webhook_id/test" 30 '{}')
success=$(printf '%s' "$body" | json_get success)
if [ "$success" = "True" ] || [ "$success" = "False" ]; then
    pass_step "POST /settings/webhooks/<id>/test returns success boolean (got '$success')"
else
    fail_step "/test bad shape: $(printf '%s' "$body" | head -c 200)"
fi

# DELETE and confirm gone.
code=$(auth_code DELETE "/api/v1/settings/webhooks/$webhook_id" 30)
assert_eq "$code" "200" 'DELETE /settings/webhooks/<id> returns 200'

still_there=$(curl -s -b "$JAR" "$LOCAL_BASE/api/v1/settings/webhooks" | python3 -c "
import json, sys
d = json.load(sys.stdin)
hooks = d.get('webhooks', d if isinstance(d, list) else [])
print(any(h.get('id') == '$webhook_id' for h in hooks))
")
assert_eq "$still_there" "False" 'webhook is gone after DELETE'

finish_test "T30-webhook-crud"
