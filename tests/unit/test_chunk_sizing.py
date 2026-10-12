"""Chunk sizing counts a resident model's memory once."""
from unittest.mock import patch

from tests.app_bootstrap import bootstrap

_test_data_dir = bootstrap('chunk_sizing_test_')

import transcriber as transcriber_mod  # noqa: E402


def _chunk_seconds(loaded):
    with patch('transcriber.chunking.get_available_memory_gb', return_value=(10.0, 'gpu')), \
            patch.object(transcriber_mod.local.WhisperModelSingleton, 'is_loaded', return_value=loaded):
        seconds, _reason = transcriber_mod.chunking.calculate_optimal_chunk_duration('large-v3', 'cuda')
    return seconds


def test_resident_model_is_not_subtracted_again():
    # 10 GB free, 70% usable: 7.0 GB minus the 5.5 GB base leaves 1.5 GB at 0.25 GB/min.
    assert _chunk_seconds(loaded=False) == 360
    assert _chunk_seconds(loaded=True) == 1680
