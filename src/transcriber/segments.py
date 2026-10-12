"""Segment-level filters: Whisper hallucination removal and foreign-language detection."""

import logging
import re

logger = logging.getLogger('transcriber')

# Whisper artifacts on silence and music. Matched after trailing punctuation
# is stripped, so a bare phrase or bare punctuation is an artifact.
HALLUCINATION_PATTERNS = re.compile(
    r'^(?:thanks for watching|thank you for watching|please subscribe|'
    r'like and subscribe|see you next time|bye|'
    r'subtitles by the amara\.org community|'
    r'\[music\]|\[applause\]|\[laughter\]|\[silence\]|you)?$',
    re.IGNORECASE
)
TRAILING_PUNCTUATION = '.!?,;:\u2026'


def filter_hallucinations(segments: list[dict]) -> list[dict]:
    """Filter out common Whisper hallucinations and artifacts."""
    filtered = []
    for seg in segments:
        text = seg.get('text', '').strip()
        if not text:
            continue
        if HALLUCINATION_PATTERNS.match(text.rstrip(TRAILING_PUNCTUATION)):
            logger.debug("Filtered hallucination segment (%d chars)", len(text))
            continue
        # Skip repeated segments (Whisper loop artifacts)
        if filtered and text == filtered[-1].get('text', '').strip():
            logger.debug("Filtered repeated segment (%d chars)", len(text))
            continue
        filtered.append(seg)
    return filtered


def should_detect_foreign_language(transcribe_language, detected_lang) -> bool:
    """Run the foreign-language DAI detector only when the audio is English.

    The detector flags non-English ad insertions inside English podcasts.
    On a non-English podcast its heuristics match every segment, so we
    skip it unless the user pinned ``whisper_language='en'`` or chose
    ``'auto'`` and Whisper detected an English variant.
    """
    if transcribe_language == 'en':
        return True
    if transcribe_language is None:
        return (detected_lang or '').lower().startswith('en')
    return False


def detect_non_english_segment(text: str, primary_language: str) -> bool:
    """Detect if a segment is likely non-English (potential DAI ad).

    Uses multiple heuristics:
    1. High ratio of non-ASCII characters (Spanish, etc.)
    2. Common Spanish/other language patterns
    3. If primary detected language is not English and segment has markers

    Args:
        text: The segment text
        primary_language: The overall detected language from Whisper

    Returns:
        True if segment appears to be non-English
    """
    if not text or len(text) < 10:
        return False

    # Check for high ratio of accented/non-ASCII characters
    non_ascii_chars = sum(1 for c in text if ord(c) > 127)
    non_ascii_ratio = non_ascii_chars / len(text)

    # Spanish and other language indicators
    spanish_patterns = [
        'usted', 'puede', 'para', 'como', 'ahora', 'llame', 'gratis',
        'oferta', 'hoy', 'desde', 'hasta', 'numero', 'telefono',
        'visite', 'compre', 'ahorre', 'descuento', 'promocion'
    ]

    text_lower = text.lower()

    # Check for Spanish ad patterns
    spanish_word_count = sum(1 for word in spanish_patterns if word in text_lower)

    # Heuristics for non-English detection
    is_likely_foreign = (
        # High non-ASCII ratio suggests accented language
        non_ascii_ratio > 0.05 or
        # Multiple Spanish words detected
        spanish_word_count >= 2 or
        # Primary language is not English and segment has some markers
        (primary_language not in ['en', 'english', 'unknown'] and
         (non_ascii_ratio > 0.02 or spanish_word_count >= 1))
    )

    if is_likely_foreign:
        logger.debug(f"Non-English segment detected: non_ascii={non_ascii_ratio:.2f}, "
                    f"spanish_words={spanish_word_count}, primary_lang={primary_language}")

    return is_likely_foreign
