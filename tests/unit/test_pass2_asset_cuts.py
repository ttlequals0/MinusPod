"""Asset generation must see the cuts the recut actually rendered (one beep
each), mapped to original coordinates, not the pre-merge UI ad list."""
import os
import sys
import tempfile

_test_data_dir = tempfile.mkdtemp(prefix='pass2_assets_test_')
os.environ.setdefault('SECRET_KEY', 'test-secret')
os.environ.setdefault('MINUSPOD_DATA_DIR', _test_data_dir)

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))

from main_app import processing


def test_pass2_rendered_cuts_map_to_original(monkeypatch):
    monkeypatch.setattr(processing, 'get_replacement_duration', lambda: 1.0)
    pass1 = [{'start': 100.0, 'end': 200.0}]
    recut = [{'start': 150.0, 'end': 180.0}]  # processed-audio coordinates
    out = processing._pass2_cuts_in_original(recut, pass1)
    # Processed 150s sits after the pass-1 beep: original = 150 + (100 - 1).
    assert out == [{'start': 249.0, 'end': 279.0, 'detection_stage': 'verification'}]


def test_pass2_cuts_empty_when_no_recut(monkeypatch):
    monkeypatch.setattr(processing, 'get_replacement_duration', lambda: 1.0)
    assert processing._pass2_cuts_in_original(None, [{'start': 0.0, 'end': 10.0}]) == []
    assert processing._pass2_cuts_in_original([], []) == []
