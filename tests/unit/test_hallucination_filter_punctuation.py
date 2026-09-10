"""Bare closing-phrase hallucinations are scrubbed whatever punctuation trails them."""
from transcriber import Transcriber


def _scrub(text):
    return Transcriber().filter_hallucinations([{'text': text}])


def test_closing_phrase_with_trailing_punctuation_is_scrubbed():
    for text in ('Thanks for watching!', 'Thank you for watching.', 'Bye?', 'you.', '!!',
                 'Thanks for watching\u2026', 'See you next time,', '...'):
        assert _scrub(text) == [], text


def test_closing_phrase_inside_real_speech_is_kept():
    assert len(_scrub('Thanks for watching the keynote with us, now the news.')) == 1
