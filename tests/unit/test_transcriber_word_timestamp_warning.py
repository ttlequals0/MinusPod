"""A 200 that carries no word timestamps must say so."""
import logging

from tests.app_bootstrap import bootstrap

_test_data_dir = bootstrap('transcriber_word_ts_')
from transcriber import _warn_if_word_timestamps_missing  # noqa: E402


SETTINGS = {'api_base_url': 'https://whisper.example.com/v1',
            'api_model': 'whisper-1'}


def _warnings(caplog, segments, settings=None):
    with caplog.at_level(logging.WARNING, logger='transcriber'):
        _warn_if_word_timestamps_missing(segments, settings or SETTINGS)
    return [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]


def test_segments_without_words_warn_once_naming_provider_and_model(caplog):
    messages = _warnings(caplog, [{'start': 0, 'end': 1, 'text': 'hi', 'words': []},
                                  {'start': 1, 'end': 2, 'text': 'there'}])

    assert len(messages) == 1
    assert 'whisper.example.com' in messages[0]
    assert 'whisper-1' in messages[0]


def test_any_segment_with_words_is_silent(caplog):
    messages = _warnings(caplog, [
        {'start': 0, 'end': 1, 'text': 'hi', 'words': []},
        {'start': 1, 'end': 2, 'text': 'there',
         'words': [{'word': 'there', 'start': 1, 'end': 2}]},
    ])

    assert messages == []


def test_an_empty_transcript_is_reported_elsewhere(caplog):
    assert _warnings(caplog, []) == []


def test_the_warning_carries_no_credentials(caplog):
    settings = {'api_base_url': 'https://whisper.example.com/v1?token=secret',
                'api_model': 'whisper-1'}
    messages = _warnings(caplog, [{'start': 0, 'end': 1, 'text': 'hi'}], settings)

    assert len(messages) == 1
    assert 'secret' not in messages[0]
