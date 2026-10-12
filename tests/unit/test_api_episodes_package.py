"""The episodes API package registers every route and its group modules form an acyclic import graph."""
import pathlib

from tests.app_bootstrap import bootstrap
from tests.import_graph import assert_acyclic, module_imports, package_modules

bootstrap('episodes_pkg_')

from main_app import app  # noqa: E402

PKG = pathlib.Path(__file__).resolve().parents[2] / 'src/api/episodes'
GROUPS = package_modules(PKG)

# (rule, methods) of every view function under api.episodes, recorded before the split.
EXPECTED_RULES = [
    ('/api/v1/episodes/<slug>/<episode_id>/reprocess', ('POST',)),
    ('/api/v1/episodes/pending-recuts', ('GET',)),
    ('/api/v1/episodes/pending-recuts/apply', ('POST',)),
    ('/api/v1/episodes/processing', ('GET',)),
    ('/api/v1/feeds/<slug>/episodes', ('GET',)),
    ('/api/v1/feeds/<slug>/episodes/<episode_id>', ('GET',)),
    ('/api/v1/feeds/<slug>/episodes/<episode_id>/artwork', ('GET',)),
    ('/api/v1/feeds/<slug>/episodes/<episode_id>/cancel', ('POST',)),
    ('/api/v1/feeds/<slug>/episodes/<episode_id>/final-segments', ('GET',)),
    ('/api/v1/feeds/<slug>/episodes/<episode_id>/original-segments', ('GET',)),
    ('/api/v1/feeds/<slug>/episodes/<episode_id>/original-transcript', ('GET',)),
    ('/api/v1/feeds/<slug>/episodes/<episode_id>/original.mp3', ('GET',)),
    ('/api/v1/feeds/<slug>/episodes/<episode_id>/peaks', ('GET',)),
    ('/api/v1/feeds/<slug>/episodes/<episode_id>/processed.mp3', ('GET',)),
    ('/api/v1/feeds/<slug>/episodes/<episode_id>/queue-priority', ('POST',)),
    ('/api/v1/feeds/<slug>/episodes/<episode_id>/regenerate-chapters', ('POST',)),
    ('/api/v1/feeds/<slug>/episodes/<episode_id>/reprocess', ('POST',)),
    ('/api/v1/feeds/<slug>/episodes/<episode_id>/retry-ad-detection', ('POST',)),
    ('/api/v1/feeds/<slug>/episodes/<episode_id>/runs/<int:run_number>/log', ('GET',)),
    ('/api/v1/feeds/<slug>/episodes/<episode_id>/split-candidates', ('GET',)),
    ('/api/v1/feeds/<slug>/episodes/<episode_id>/transcript', ('GET',)),
    ('/api/v1/feeds/<slug>/episodes/<episode_id>/transcript-span', ('GET',)),
    ('/api/v1/feeds/<slug>/episodes/bulk', ('POST',)),
    ('/api/v1/feeds/<slug>/episodes/passthrough', ('POST',)),
    ('/api/v1/feeds/<slug>/reprocess-all', ('POST',)),
]


def test_api_episodes_routes_registered():
    actual = sorted(
        (r.rule, tuple(sorted(m for m in r.methods if m not in ('HEAD', 'OPTIONS'))))
        for r in app.url_map.iter_rules()
        if app.view_functions[r.endpoint].__module__.startswith('api.episodes'))
    assert actual == sorted(EXPECTED_RULES)


def test_api_episodes_import_graph_is_acyclic():
    graph = {name: module_imports(PKG / f'{name}.py', 'api.episodes') & set(GROUPS) for name in GROUPS}
    assert_acyclic(graph)


def test_init_imports_every_group_module():
    assert module_imports(PKG / '__init__.py', 'api.episodes') == set(GROUPS)
