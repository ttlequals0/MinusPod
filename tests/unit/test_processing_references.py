"""Every processing.<name> reference in tests resolves to a real attribute of main_app.processing."""
import pathlib
import re

from tests.app_bootstrap import bootstrap

bootstrap('refs_test_')

from main_app import processing  # noqa: E402

TESTS = pathlib.Path(__file__).resolve().parents[1]
PATTERNS = (re.compile(r"main_app\.processing\.([A-Za-z_]\w*)"),
            re.compile(r"\bprocessing\.([A-Za-z_]\w*)"),
            re.compile(r"setattr\(processing, '([A-Za-z_]\w*)'"),
            re.compile(r"patch\.object\(processing, '([A-Za-z_]\w*)'"))


def test_no_stale_processing_attribute_references():
    stale = set()
    for path in TESTS.rglob('*.py'):
        text = path.read_text()
        if 'processing' not in text:
            continue
        for pattern in PATTERNS:
            for name in pattern.findall(text):
                if name != 'py' and not hasattr(processing, name):
                    stale.add(f'{path.relative_to(TESTS)}: processing.{name}')
    assert not stale, '\n'.join(sorted(stale))
