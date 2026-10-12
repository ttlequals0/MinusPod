"""The pipeline stage modules form an acyclic import graph and processing.py only orchestrates."""
import ast
import pathlib

from tests.app_bootstrap import bootstrap
from tests.import_graph import assert_acyclic, package_import_graph

bootstrap('pipeline_order_')

PKG = pathlib.Path(__file__).resolve().parents[2] / 'src/main_app/pipeline'
STAGES = sorted(p.stem for p in PKG.glob('*.py') if p.stem not in ('__init__', 'shared'))


def _tree(name):
    return ast.parse((PKG / f'{name}.py').read_text())


def test_pipeline_import_graph_is_acyclic():
    graph = {name: deps - {'shared'} for name, deps in package_import_graph(PKG, 'main_app.pipeline').items()
             if name != 'shared'}
    assert_acyclic(graph)


def test_stages_do_not_import_processing():
    for name in STAGES:
        for node in ast.walk(_tree(name)):
            if isinstance(node, ast.ImportFrom):
                imported = {f'{node.module}.{a.name}' for a in node.names} | {node.module}
            elif isinstance(node, ast.Import):
                imported = {a.name for a in node.names}
            else:
                continue
            assert 'main_app.processing' not in imported, f'{name} imports main_app.processing'


def test_processing_defines_nothing_a_stage_defines():
    stage_defs = {n.name for name in STAGES for n in _tree(name).body
                  if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))}
    tree = ast.parse((PKG.parent / 'processing.py').read_text())
    local = {n.name for n in tree.body
             if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))}
    assert not (local & stage_defs), f'duplicated in processing.py: {sorted(local & stage_defs)}'
