"""Guards patch targets: no test or source file may reference an attribute a split module no longer has."""
import importlib
import pathlib
import re

import pytest

from tests.app_bootstrap import bootstrap

bootstrap('refs_test_')

ROOT = pathlib.Path(__file__).resolve().parents[2]
SCAN_DIRS = (ROOT / 'tests', ROOT / 'src')
SELF = pathlib.Path(__file__).resolve()
CHAIN = r"((?:\.\w+)*)"

# import name -> regex that finds local aliases of the module in a file
MODULES = {
    'main_app.processing': r"from main_app import processing(?: as (\w+))?",
    'api.settings': r"(?:from api import settings(?: as (\w+))?|import api\.settings as (\w+))",
}


def _resolve(root, chain, name):
    """Return the 'mod.attr' that is missing, or None when the dotted reference resolves."""
    obj = root
    for part in [p for p in chain.split('.') if p] + [name]:
        if not hasattr(obj, part):
            return part
        obj = getattr(obj, part)
    return None


def _stale_in(text, import_name, root, alias_pattern):
    prefix = re.escape(import_name)
    found = [(m.group(1), m.group(2)) for m in re.finditer(rf"{prefix}{CHAIN}\.(\w+)", text)]
    short = import_name.rsplit('.', 1)[-1]
    aliases = {next((g for g in m.groups() if g), short) for m in re.finditer(alias_pattern, text)}
    for alias in aliases:
        for form in (r"setattr\(", r"patch\.object\("):
            found += [(m.group(1), m.group(2))
                      for m in re.finditer(rf"{form}{re.escape(alias)}{CHAIN}, '(\w+)'", text)]
    return [f'{import_name}{chain}.{name}' for chain, name in found if _resolve(root, chain, name)]


@pytest.mark.parametrize('import_name', sorted(MODULES))
def test_no_stale_attribute_references(import_name):
    root = importlib.import_module(import_name)
    stale = set()
    short = import_name.rsplit('.', 1)[-1]
    for base in SCAN_DIRS:
        for path in base.rglob('*.py'):
            if path == SELF:
                continue
            text = path.read_text()
            if short not in text:
                continue
            for ref in _stale_in(text, import_name, root, MODULES[import_name]):
                stale.add(f'{path.relative_to(ROOT)}: {ref}')
    assert not stale, '\n'.join(sorted(stale))
