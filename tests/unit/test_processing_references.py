"""Guards patch targets that name an attribute the module no longer has."""
import functools
import importlib
import pathlib
import re

from tests.app_bootstrap import bootstrap

bootstrap('refs_test_')

TESTS = pathlib.Path(__file__).resolve().parents[1]
TARGET_RE = re.compile(r"""['"]((?:api\.settings|main_app\.processing)(?:\.\w+)+)['"]""")


@functools.lru_cache(maxsize=None)
def _test_sources():
    return [(path, path.read_text()) for path in sorted(TESTS.rglob('*.py'))]


def _resolves(target):
    parts = target.split('.')
    for i in range(len(parts), 0, -1):
        try:
            obj = importlib.import_module('.'.join(parts[:i]))
        except ImportError:
            continue
        for attr in parts[i:]:
            if not hasattr(obj, attr):
                return False
            obj = getattr(obj, attr)
        return True
    return False


def test_no_stale_patch_targets():
    stale = sorted({f'{path.relative_to(TESTS.parent)}: {target}'
                    for path, text in _test_sources()
                    for target in TARGET_RE.findall(text)
                    if not _resolves(target)})
    assert not stale, '\n'.join(stale)
