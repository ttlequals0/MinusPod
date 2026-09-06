"""Feed detection notes (#709) ride the podcast description into the prompts."""
from tests.app_bootstrap import bootstrap

_test_data_dir = bootstrap('detection_notes_test_')
from main_app.processing import build_podcast_context  # noqa: E402


def test_notes_appended_after_description():
    out = build_podcast_context({'description': 'A show.', 'detection_notes': 'Intro is 45s.'})
    assert out == 'A show.\n\nOperator notes for this show:\nIntro is 45s.'


def test_notes_alone_when_no_description():
    out = build_podcast_context({'description': None, 'detection_notes': 'Intro is 45s.'})
    assert out == 'Operator notes for this show:\nIntro is 45s.'


def test_no_notes_returns_description():
    assert build_podcast_context({'description': 'A show.', 'detection_notes': None}) == 'A show.'
    assert build_podcast_context(None) is None
