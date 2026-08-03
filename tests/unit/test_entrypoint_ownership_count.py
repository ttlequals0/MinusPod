"""Entrypoint ownership-migration count stays a single integer (issue #604)."""
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ENTRYPOINT = Path(__file__).resolve().parents[2] / 'entrypoint.sh'
pytestmark = pytest.mark.skipif(
    shutil.which('bash') is None, reason='bash not available')


def _unowned_count_block() -> str:
    """The real count block from entrypoint.sh, so the test tracks shipped code."""
    lines = ENTRYPOINT.read_text().splitlines()
    start = next((i for i, line in enumerate(lines) if 'unowned_count=$(' in line), None)
    assert start is not None, 'unowned_count assignment not found in entrypoint.sh'
    end = next((i for i in range(start, len(lines)) if lines[i].strip() == 'fi'), None)
    assert end is not None, 'unterminated unowned_count block in entrypoint.sh'
    return '\n'.join(line.strip() for line in lines[start:end + 1])


def _run(find_body: str) -> subprocess.CompletedProcess:
    script = (
        'set -euo pipefail\n'
        'DATA_DIR=/tmp\n'
        'APP_UID=1000\n'
        f'find() {{ {find_body} }}\n'
        f'{_unowned_count_block()}\n'
        'if [[ "$unowned_count" -gt 0 ]]; then :; fi\n'
        'printf "COUNT=[%s]" "$unowned_count"\n'
    )
    return subprocess.run(['bash', '-c', script], capture_output=True, text=True)


def test_find_failure_yields_plain_zero():
    # pipefail turns a find error into a pipeline failure; the fallback must
    # replace the count, not append to what wc already printed.
    result = _run('return 1;')
    assert result.returncode == 0, result.stderr
    assert 'COUNT=[0]' in result.stdout
    assert 'syntax error' not in result.stderr


def test_find_failure_warns_instead_of_reporting_a_silent_zero():
    # A container without CAP_DAC_OVERRIDE cannot read every entry, and an
    # unexplained zero reads as "nothing to migrate" (issue #604).
    result = _run('return 1;')
    assert 'could not scan all of' in result.stdout + result.stderr


def test_find_success_counts_entries():
    result = _run(R"printf 'a\nb\nc\n'; return 0;")
    assert result.returncode == 0, result.stderr
    assert 'COUNT=[3]' in result.stdout


def test_chown_failure_is_reported():
    # A container without CAP_CHOWN fails every entry; `|| true` used to hide
    # that, leaving the app unable to write with no explanation (issue #604).
    text = ENTRYPOINT.read_text()
    chown_line = next(ln for ln in text.splitlines()
                      if '-exec chown' in ln and not ln.strip().startswith('#'))
    assert '|| true' not in chown_line, 'chown failure must not be swallowed'
    assert 'could not change ownership' in text


def test_no_inline_echo_fallback_on_a_pipeline():
    # The construct that caused #604: `| wc -l || echo 0` inside $( ).
    assert not re.search(r'\|\s*wc -l\s*\|\|\s*echo', ENTRYPOINT.read_text())
