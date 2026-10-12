"""Guards patch targets that name an attribute the module no longer has."""
import importlib
import pathlib
import re

from tests.app_bootstrap import bootstrap

bootstrap('refs_test_')

TESTS = pathlib.Path(__file__).resolve().parents[1]
TARGET_RE = re.compile(r"""['"]((?:api\.settings|episode_runs|api\.episodes|main_app\.processing|transcriber)(?:\.\w+)+)['"]""")


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
    sources = [(path, path.read_text()) for path in sorted(TESTS.rglob('*.py'))]
    targets = {t for _, text in sources for t in TARGET_RE.findall(text)}
    stale = {t for t in targets if not _resolves(t)}
    report = sorted(f'{path.relative_to(TESTS.parent)}: {t}'
                    for path, text in sources
                    for t in stale if t in TARGET_RE.findall(text))
    assert not report, '\n'.join(report)
