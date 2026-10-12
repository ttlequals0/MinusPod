"""The settings API package registers every route and its group modules form an acyclic import graph."""
import ast
import pathlib

from tests.app_bootstrap import bootstrap

bootstrap('settings_pkg_')

from main_app import app  # noqa: E402

PKG = pathlib.Path(__file__).resolve().parents[2] / 'src/api/settings'
GROUPS = sorted(p.stem for p in PKG.glob('*.py') if p.stem != '__init__')

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
]


def _tree(name):
    return ast.parse((PKG / f'{name}.py').read_text())


def _group_imports(name):
    out = set()
    for node in _tree(name).body:
        if isinstance(node, ast.ImportFrom) and node.module == 'api.settings':
            out.update(alias.name for alias in node.names if alias.name in GROUPS)
    return out


def test_api_settings_routes_registered():
    actual = sorted(
        (r.rule, tuple(sorted(m for m in r.methods if m not in ('HEAD', 'OPTIONS'))))
        for r in app.url_map.iter_rules()
        if app.view_functions[r.endpoint].__module__.startswith('api.settings'))
    assert actual == sorted(EXPECTED_RULES)


def test_api_settings_import_graph_is_acyclic():
    graph = {name: _group_imports(name) for name in GROUPS}
    done, active = set(), []

    def visit(node):
        assert node not in active, f'import cycle: {" -> ".join(active[active.index(node):] + [node])}'
        if node in done:
            return
        active.append(node)
        for dep in graph[node]:
            visit(dep)
        active.pop()
        done.add(node)

    for name in graph:
        visit(name)


def test_init_imports_every_group_module():
    imported = set()
    for node in _tree('__init__').body:
        if isinstance(node, ast.ImportFrom) and node.module == 'api.settings':
            imported.update(alias.name for alias in node.names)
    assert imported == set(GROUPS)
