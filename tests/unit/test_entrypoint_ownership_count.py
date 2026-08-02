"""Entrypoint ownership-migration count stays a single integer (issue #604)."""
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ENTRYPOINT = Path(__file__).resolve().parents[2] / 'entrypoint.sh'
pytestmark = pytest.mark.skipif(
    shutil.which('bash') is None, reason='bash not available')


def _unowned_count_line() -> str:
    """The real assignment from entrypoint.sh, so the test tracks the shipped code."""
    for line in ENTRYPOINT.read_text().splitlines():
        if re.match(r'\s*unowned_count=', line):
            return line.strip()
    raise AssertionError('unowned_count assignment not found in entrypoint.sh')


def _run(find_body: str) -> subprocess.CompletedProcess:
    script = (
        'set -euo pipefail\n'
        'DATA_DIR=/tmp\n'
        'APP_UID=1000\n'
        f'find() {{ {find_body} }}\n'
        f'{_unowned_count_line()}\n'
        'if [[ "$unowned_count" -gt 0 ]]; then :; fi\n'
        'printf %s "$unowned_count"\n'
    )
    return subprocess.run(['bash', '-c', script], capture_output=True, text=True)


def test_find_failure_yields_plain_zero():
    # pipefail turns a find error into a pipeline failure; the fallback must
    # replace the count, not append to what wc already printed.
    result = _run('return 1;')
    assert result.returncode == 0, result.stderr
    assert result.stdout == '0'
    assert 'syntax error' not in result.stderr


def test_find_success_counts_entries():
    result = _run(R"printf 'a\nb\nc\n'; return 0;")
    assert result.returncode == 0, result.stderr
    assert result.stdout == '3'


def test_no_inline_echo_fallback_on_a_pipeline():
    # The construct that caused #604: `| wc -l || echo 0` inside $( ).
    assert not re.search(r'\|\s*wc -l\s*\|\|\s*echo', ENTRYPOINT.read_text())
