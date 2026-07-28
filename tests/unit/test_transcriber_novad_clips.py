"""No-VAD transcription must supply its own clip_timestamps.

BatchedInferencePipeline builds its chunks from VAD speech timestamps. With
vad_filter=False and no clip_timestamps it raises "No clip timestamps found",
which transcribe() swallowed into a None return. faster-whisper has its own
single-clip fallback below the 30s chunk length, so the tail re-transcription
(spec 1.2) failed on the local backend for any span of 30 seconds or more.
"""
from unittest.mock import MagicMock, patch

import pytest

import transcriber as transcriber_mod
from transcriber import Transcriber, _full_span_clips


def test_full_span_clips_splits_at_chunk_length():
    assert _full_span_clips(72.5) == [
        {'start': 0.0, 'end': 30.0},
        {'start': 30.0, 'end': 60.0},
        {'start': 60.0, 'end': 72.5},
    ]


def test_full_span_clips_exact_multiple_has_no_empty_tail_clip():
    assert _full_span_clips(60.0) == [
        {'start': 0.0, 'end': 30.0},
        {'start': 30.0, 'end': 60.0},
    ]


def test_full_span_clips_shorter_than_chunk_is_single_clip():
    assert _full_span_clips(12.0) == [{'start': 0.0, 'end': 12.0}]


def test_duration_a_hair_past_a_boundary_drops_the_degenerate_clip():
    # ffprobe durations are floats; a sub-sample trailing clip reaches the
    # pipeline as an empty feature array and raises IndexError there.
    assert _full_span_clips(60.00000000000001) == [
        {'start': 0.0, 'end': 30.0},
        {'start': 30.0, 'end': 60.0},
    ]


def test_duration_below_the_minimum_clip_yields_no_clips():
    # None lets the pipeline take its own under-30s path.
    assert _full_span_clips(0.02) is None


@pytest.mark.parametrize('duration', [None, 0, -1.0])
def test_full_span_clips_unknown_duration(duration):
    assert _full_span_clips(duration) is None


def _run_transcribe(vad_filter, duration=72.5):
    model = MagicMock()
    info = MagicMock(language='en', language_probability=0.99)
    model.transcribe.return_value = (iter([]), info)
    with patch.object(transcriber_mod, '_get_whisper_settings',
                      return_value={'backend': 'local', 'language': 'en'}), \
         patch.object(transcriber_mod.WhisperModelSingleton,
                      'get_batched_pipeline', return_value=model), \
         patch.object(transcriber_mod.WhisperModelSingleton,
                      'get_current_model_name', return_value='small'), \
         patch.object(Transcriber, 'get_audio_duration', return_value=duration), \
         patch.object(Transcriber, 'get_initial_prompt', return_value=''):
        Transcriber().transcribe('/tail.wav', preprocessed=True,
                                 vad_filter=vad_filter)
    return model.transcribe.call_args.kwargs


def test_novad_transcription_passes_full_span_clips():
    kwargs = _run_transcribe(vad_filter=False)
    assert kwargs['vad_filter'] is False
    assert kwargs['vad_parameters'] is None
    assert kwargs['clip_timestamps'] == _full_span_clips(72.5)


def test_vad_transcription_passes_no_clips():
    kwargs = _run_transcribe(vad_filter=True)
    assert kwargs['vad_filter'] is True
    assert kwargs['vad_parameters'] is not None
    assert kwargs['clip_timestamps'] is None


def test_novad_with_unknown_duration_falls_back_to_no_clips():
    assert _run_transcribe(vad_filter=False, duration=None)['clip_timestamps'] is None


def test_clips_cover_the_preprocessed_file_not_the_raw_chunk():
    """loudnorm can shift the duration by tens of milliseconds, and the sliver
    past the raw chunk's duration would go untranscribed."""
    model = MagicMock()
    info = MagicMock(language='en', language_probability=0.99)
    model.transcribe.return_value = (iter([]), info)
    durations = {'/tail.wav': 60.0, '/tail.preprocessed.wav': 60.4}

    with patch.object(transcriber_mod, '_get_whisper_settings',
                      return_value={'backend': 'local', 'language': 'en'}), \
         patch.object(transcriber_mod.WhisperModelSingleton,
                      'get_batched_pipeline', return_value=model), \
         patch.object(transcriber_mod.WhisperModelSingleton,
                      'get_current_model_name', return_value='small'), \
         patch.object(Transcriber, 'get_audio_duration',
                      side_effect=lambda p: durations[p]), \
         patch.object(Transcriber, 'preprocess_audio',
                      return_value='/tail.preprocessed.wav'), \
         patch.object(Transcriber, 'get_initial_prompt', return_value=''):
        Transcriber().transcribe('/tail.wav', vad_filter=False)

    clips = model.transcribe.call_args.kwargs['clip_timestamps']
    assert clips[-1]['end'] == 60.4
