"""Refs follow markers only through save_episode_details, so no other code may set ad_markers_json."""
import ast
import re
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / 'src'

# Enclosing functions allowed to write markers: the refs-maintaining writer and two one-shot
# migrations that run before the refs backfill.
ALLOWED = {'save_episode_details', '_cleanup_zyn_ad_markers', '_collapse_duplicate_ad_markers'}

SETS_MARKERS = re.compile(
    r"INSERT\s+(?:OR\s+\w+\s+)?INTO\s+episode_details\s*\([^)]*\bad_markers_json"
    r"|\bad_markers_json\s*=\s*(?!NULL\b)\S", re.IGNORECASE)


def _writers():
    found = set()
    for path in SRC.rglob('*.py'):
        tree = ast.parse(path.read_text())
        owners = {}
        for fn in ast.walk(tree):
            if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                for node in ast.walk(fn):
                    owners[id(node)] = fn.name
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str) and SETS_MARKERS.search(node.value):
                found.add((str(path.relative_to(SRC)), owners.get(id(node), '<module>')))
    return found


def test_only_the_allowed_functions_set_ad_markers_json():
    unexpected = {site for site in _writers() if site[1] not in ALLOWED}
    assert not unexpected, f'new ad_markers_json writers must keep episode_marker_refs in step: {sorted(unexpected)}'


def test_the_scan_sees_the_known_writers():
    assert {name for _, name in _writers()} == ALLOWED
