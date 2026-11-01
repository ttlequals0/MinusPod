"""The settings API package registers every route and its group modules form an acyclic import graph."""
import pathlib

from tests.app_bootstrap import bootstrap
from tests.import_graph import assert_package_acyclic

bootstrap('settings_pkg_')

from main_app import app  # noqa: E402

PKG = pathlib.Path(__file__).resolve().parents[2] / 'src/api/settings'

# (rule, methods) of every view function under api.settings, recorded before the split.
EXPECTED_RULES = [
    ('/api/v1/community-patterns/all', ('DELETE',)),
    ('/api/v1/community-patterns/sync', ('POST',)),
    ('/api/v1/community-patterns/sync-status', ('GET',)),
    ('/api/v1/networks', ('GET',)),
    ('/api/v1/settings', ('GET',)),
    ('/api/v1/settings/ad-detection', ('PUT',)),
    ('/api/v1/settings/ad-detection/reset', ('POST',)),
    ('/api/v1/settings/audio', ('GET',)),
    ('/api/v1/settings/audio', ('PUT',)),
    ('/api/v1/settings/community-sync', ('GET',)),
    ('/api/v1/settings/community-sync', ('PUT',)),
    ('/api/v1/settings/db-backup', ('GET',)),
    ('/api/v1/settings/db-backup', ('PUT',)),
    ('/api/v1/settings/feed-auth/regenerate-key', ('POST',)),
    ('/api/v1/settings/models', ('GET',)),
    ('/api/v1/settings/models/refresh', ('POST',)),
    ('/api/v1/settings/notifications/email', ('GET',)),
    ('/api/v1/settings/notifications/email', ('PUT',)),
    ('/api/v1/settings/notifications/email/test', ('POST',)),
    ('/api/v1/settings/notifications/timezone', ('GET',)),
    ('/api/v1/settings/notifications/timezone', ('PUT',)),
    ('/api/v1/settings/offline-queue', ('GET',)),
    ('/api/v1/settings/offline-queue', ('PUT',)),
    ('/api/v1/settings/pattern-cleanup', ('PUT',)),
    ('/api/v1/settings/processing-timeouts', ('GET',)),
    ('/api/v1/settings/processing-timeouts', ('PUT',)),
    ('/api/v1/settings/prompts/<name>/reset', ('POST',)),
    ('/api/v1/settings/prompts/reset', ('POST',)),
    ('/api/v1/settings/provider-budget', ('GET',)),
    ('/api/v1/settings/provider-budget', ('PUT',)),
    ('/api/v1/settings/provider-budget/currencies', ('GET',)),
    ('/api/v1/settings/provider-budget/rate/<currency>', ('GET',)),
    ('/api/v1/settings/rate-limit-hold', ('GET',)),
    ('/api/v1/settings/rate-limit-hold', ('PUT',)),
    ('/api/v1/settings/rate-limit-hold/reset', ('POST',)),
    ('/api/v1/settings/replacement-audio', ('DELETE',)),
    ('/api/v1/settings/replacement-audio', ('GET',)),
    ('/api/v1/settings/replacement-audio', ('POST',)),
    ('/api/v1/settings/replacement-audio/file', ('GET',)),
    ('/api/v1/settings/retention', ('GET',)),
    ('/api/v1/settings/retention', ('PUT',)),
    ('/api/v1/settings/reviewer', ('GET',)),
    ('/api/v1/settings/reviewer', ('PUT',)),
    ('/api/v1/settings/update-check', ('GET',)),
    ('/api/v1/settings/update-check', ('PUT',)),
    ('/api/v1/settings/webhooks', ('GET',)),
    ('/api/v1/settings/webhooks', ('POST',)),
    ('/api/v1/settings/webhooks/<webhook_id>', ('DELETE',)),
    ('/api/v1/settings/webhooks/<webhook_id>', ('PUT',)),
    ('/api/v1/settings/webhooks/<webhook_id>/test', ('POST',)),
    ('/api/v1/settings/webhooks/validate-template', ('POST',)),
    ('/api/v1/settings/whisper-models', ('GET',)),
    ('/api/v1/settings/whisper/capacity', ('GET',)),
    ('/api/v1/settings/youtube', ('GET',)),
    ('/api/v1/settings/youtube', ('PUT',)),
    ('/api/v1/settings/youtube/reset', ('POST',)),
    ('/api/v1/settings/youtube/sb-user-id', ('GET',)),
    ('/api/v1/settings/youtube/sb-user-id/generate', ('POST',)),
]


def test_api_settings_routes_registered():
    actual = sorted(
        (r.rule, tuple(sorted(m for m in r.methods if m not in ('HEAD', 'OPTIONS'))))
        for r in app.url_map.iter_rules()
        if app.view_functions[r.endpoint].__module__.startswith('api.settings'))
    assert actual == sorted(EXPECTED_RULES)


def test_api_settings_import_graph_is_acyclic_and_init_imports_every_module():
    assert_package_acyclic(PKG, 'api.settings')
