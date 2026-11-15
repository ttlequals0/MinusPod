"""Hole repair under precise_cut_edges: the sparse-line re-decode follows the flag."""
from tests.app_bootstrap import bootstrap

bootstrap('hole_repair_precise_edges_test_')

import pytest

from main_app.pipeline import transcribe


@pytest.mark.parametrize('flag', [False, True])
def test_sparse_repair_follows_the_flag(monkeypatch, flag):
    seen = {}

    def repair_gaps(audio_path, segments, min_s, language, skip=(), sparse=False):
        seen['sparse'] = sparse
        return [], []
    monkeypatch.setattr(transcribe, 'resolve_precise_cut_edges', lambda db: flag)
    monkeypatch.setattr(transcribe.transcriber, 'repair_gaps', repair_gaps)
    transcribe._retranscribe_holes_no_vad('/a.mp3', [{'start': 0, 'end': 1}, {'start': 30, 'end': 31}], None)
    assert seen['sparse'] is flag
