"""Tests for the foreign-language DAI detector gate.

The gate prevents the heuristic non-English-as-ad detector from running on
non-English podcasts, where it would false-positive every segment.
"""
import pytest

from transcriber import Transcriber


@pytest.mark.parametrize(
    "transcribe_language,detected_lang,expected",
    [
        # Configured English: always run; the detector's whole purpose is to
        # catch non-English DAI ads inside English content.
        ('en', 'en', True),
        ('en', 'es', True),
        ('en', 'unknown', True),
        ('en', None, True),
        # Auto mode: trust Whisper's detection. Run only on English variants.
        (None, 'en', True),
        (None, 'english', True),
        (None, 'es', False),
        (None, 'pt', False),
        (None, 'unknown', False),
        (None, '', False),
        (None, None, False),
        # Configured non-English: never run, even if detection disagrees.
        ('es', 'es', False),
        ('es', 'en', False),
        ('pt-br', 'pt', False),
        ('fr', 'unknown', False),
    ],
)
def test_should_detect_foreign_language(transcribe_language, detected_lang, expected):
    assert Transcriber._should_detect_foreign_language(
        transcribe_language, detected_lang
    ) is expected
