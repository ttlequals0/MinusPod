"""Stage modules import only earlier stage modules, so the package has no cycles."""
import ast
import pathlib

from tests.app_bootstrap import bootstrap

bootstrap('pipeline_order_')

from main_app.pipeline import STAGE_MODULES  # noqa: E402

PKG = pathlib.Path(__file__).resolve().parents[2] / 'src/main_app/pipeline'


def _stage_imports(name):
    tree = ast.parse((PKG / f'{name}.py').read_text())
    out = set()
    for node in tree.body:
        if isinstance(node, ast.ImportFrom) and node.module == 'main_app.pipeline':
            out.update(alias.name for alias in node.names)
    return out


def test_pipeline_import_order():
    for i, name in enumerate(STAGE_MODULES):
        later = set(STAGE_MODULES[i:])
        assert not (_stage_imports(name) & later), f'{name} imports a later stage'


def test_processing_keeps_only_the_orchestrator():
    tree = ast.parse((PKG.parent / 'processing.py').read_text())
    defs = {n.name for n in tree.body if isinstance(n, ast.FunctionDef)}
    assert defs == {'_cleanup_cancelled_processing_output', '_process_episode_background',
                    'start_background_processing', 'process_episode'}
