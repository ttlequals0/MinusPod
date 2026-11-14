"""The autouse model guard fails a test that reaches a model boundary, even when product code swallows the error."""
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]

CASES = {
    'whisper_model': ("from faster_whisper import WhisperModel", "WhisperModel('tiny')", 'faster_whisper.WhisperModel'),
    'download_model': ("from faster_whisper.utils import download_model", "download_model('tiny')",
                       'faster_whisper.utils.download_model'),
    'snapshot_download': ("from huggingface_hub import snapshot_download", "snapshot_download('x/y')",
                          'huggingface_hub.snapshot_download'),
    'hf_hub_download': ("import huggingface_hub", "huggingface_hub.hf_hub_download('x/y', 'f')",
                        'huggingface_hub.hf_hub_download'),
    'hub_probe': ("from huggingface_hub import HfApi", "HfApi().model_info('x/y')",
                  'huggingface_hub.HfApi.model_info'),
    'parakeet_load': ("import onnx_asr", "onnx_asr.load_model('x', path='/nonexistent')", 'onnx_asr.load_model'),
    'parakeet_download': ("import parakeet_transcriber as p",
                          "p._fetch_model_files('/nonexistent/model', 'int8')", 'huggingface_hub.snapshot_download'),
}


def _module_bound_names():
    # A name bound at import time in a project module, as `from x import y` does.
    return textwrap.dedent('''
        import parakeet_transcriber
        from huggingface_hub import snapshot_download as bound_download
        def test_bound_name():
            try:
                bound_download('x/y')
            except AssertionError:
                pass
    ''')


@pytest.fixture(scope='module')
def guard_run(tmp_path_factory):
    root = tmp_path_factory.mktemp('guard')
    (root / 'pytest.ini').write_text('[pytest]\n')
    (root / 'conftest.py').write_text(
        f"import sys\nsys.path[:0] = [{str(REPO)!r}, {str(REPO / 'src')!r}]\n"
        "from tests.model_guard import _no_real_model_loads  # noqa: F401\n")
    body = ''
    for name, (imp, call, _boundary) in CASES.items():
        body += f"\ndef test_{name}():\n    {imp}\n    try:\n        {call}\n    except Exception:\n        pass\n"
    body += "\ndef test_clean():\n    pass\n"
    (root / 'test_cases.py').write_text(body)
    (root / 'test_bound.py').write_text(_module_bound_names())
    proc = subprocess.run(
        [sys.executable, '-m', 'pytest', str(root), '-q', '-p', 'no:cacheprovider', '-rA'],
        capture_output=True, text=True, cwd=root, timeout=180,
        env={**os.environ, 'COLUMNS': '400'})
    return proc.stdout


@pytest.mark.parametrize('name', list(CASES))
def test_guard_fails_a_test_that_reaches_the_boundary(guard_run, name):
    boundary = CASES[name][2]
    lines = [ln for ln in guard_run.splitlines() if ln.startswith('ERROR') and f'test_cases.py::test_{name}' in ln]
    assert lines, guard_run
    assert boundary in lines[0]


def test_guard_fails_a_name_bound_in_a_module(guard_run):
    assert any(ln.startswith('ERROR') and 'test_bound.py::test_bound_name' in ln for ln in guard_run.splitlines())


def test_guard_leaves_a_clean_test_alone(guard_run):
    assert 'PASSED test_cases.py::test_clean' in guard_run
    assert not any('test_cases.py::test_clean' in ln for ln in guard_run.splitlines() if ln.startswith('ERROR'))
