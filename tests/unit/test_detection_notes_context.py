"""Feed detection notes (#709) ride the podcast description into the prompts."""
from tests.app_bootstrap import bootstrap

_test_data_dir = bootstrap('detection_notes_test_')
from main_app.processing import build_podcast_description  # noqa: E402


def test_notes_appended_after_description():
    out = build_podcast_description({'description': 'A show.', 'detection_notes': 'Intro is 45s.'})
    assert out == 'A show.\n\nOperator notes for this show:\nIntro is 45s.'


def test_notes_alone_when_no_description():
    out = build_podcast_description({'description': None, 'detection_notes': 'Intro is 45s.'})
    assert out == 'None\n\nOperator notes for this show:\nIntro is 45s.'


def test_no_notes_returns_description():
    out = build_podcast_description({'description': 'A show.', 'detection_notes': None})
    assert out == 'A show.'


def test_when_no_description_and_no_notes_returns_none():
    assert build_podcast_description({'description': None, 'detection_notes': None}) == None
    assert build_podcast_description({'description': "", 'detection_notes': ""}) == None
    assert build_podcast_description(None) is None


def test_description_truncated_when_longer_than_max_length():
    out = build_podcast_description({'description': 'abcdefghijk', 'detection_notes': 'foo'}, max_desc_length=10)
    assert out == 'abcde...jk\n\nOperator notes for this show:\nfoo'
