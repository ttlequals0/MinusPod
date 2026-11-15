"""Segment-level filters: Whisper hallucination removal and foreign-language detection."""

import logging
import re
from difflib import SequenceMatcher

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


def _seg_text(seg: dict) -> str:
    return (seg.get('text') or '').strip()


def filter_hallucinations(segments: list[dict]) -> list[dict]:
    """Filter out common Whisper hallucinations and artifacts."""
    filtered = []
    for seg in segments:
        text = _seg_text(seg)
        if not text:
            continue
        if HALLUCINATION_PATTERNS.match(text.rstrip(TRAILING_PUNCTUATION)):
            logger.debug("Filtered hallucination segment (%d chars)", len(text))
            continue
        # Skip repeated segments (Whisper loop artifacts)
        if filtered and text == _seg_text(filtered[-1]):
            logger.debug("Filtered repeated segment (%d chars)", len(text))
            continue
        filtered.append(seg)
    return filtered


# Whisper repetition loops, including near-matches and A,B,A,B runs that filter_hallucinations keeps.
# Stored transcripts keep every cue, so callers compute the runs once per detection run.
LOOP_MIN_CUES = 8            # shortest run that counts as a loop
LOOP_MAX_CUE_SECONDS = 4.0   # a loop cue is shorter than this
LOOP_SIMILARITY = 0.9        # normalised-text ratio that counts as a repeat
LOOP_PERIOD = 2              # a cue repeats one of the previous two (A,B,A,B loops)
LOOP_MIN_COVERAGE = 0.5      # share of a marker inside loops that makes its text unprovable

_NON_WORD_RE = re.compile(r'[^\w ]')


def _norm_text(text: str) -> str:
    return _NON_WORD_RE.sub('', text.lower()).strip()


def _short(seg: dict) -> bool:
    return 0 <= seg['end'] - seg['start'] < LOOP_MAX_CUE_SECONDS


def find_loop_runs(segments: list[dict]) -> list[tuple[float, float, int]]:
    """Return (start, end, cue_count) per run of >= LOOP_MIN_CUES repeating short cues."""
    norms = [_norm_text(_seg_text(s)) for s in segments]
    flags = [False] * len(segments)
    for i, seg in enumerate(segments):
        for k in range(1, LOOP_PERIOD + 1):
            if (norms[i] and i - k >= 0 and _short(seg) and _short(segments[i - k])
                    and SequenceMatcher(None, norms[i], norms[i - k]).ratio() >= LOOP_SIMILARITY):
                flags[i] = flags[i - k] = True   # the repeated cue seeds the run too
                break
    runs, i = [], 0
    while i < len(segments):
        if not flags[i]:
            i += 1
            continue
        j = i
        while j < len(segments) and flags[j]:
            j += 1
        if j - i >= LOOP_MIN_CUES:
            runs.append((segments[i]['start'], segments[j - 1]['end'], j - i))
        i = j
    return runs


def loop_coverage(runs: list[tuple[float, float, int]], start: float, end: float) -> float:
    """Fraction of [start, end] inside loop runs."""
    if end <= start:
        return 0.0
    return sum(max(0.0, min(b, end) - max(a, start)) for a, b, _ in runs) / (end - start)


def _in_loop(seg: dict, runs: list[tuple[float, float, int]]) -> bool:
    return any(a <= seg['start'] and seg['end'] <= b for a, b, _ in runs)


def drop_loop_cues(segments: list[dict], runs: list[tuple[float, float, int]]) -> list[dict]:
    """Segments without cues inside a loop run: they are no evidence either way."""
    if not runs:
        return segments
    return [s for s in segments if not _in_loop(s, runs)]


def loop_cues(segments: list[dict], runs: list[tuple[float, float, int]]) -> list[dict]:
    """The cues inside a loop run: the complement of drop_loop_cues."""
    return [s for s in segments if _in_loop(s, runs)]


def should_detect_foreign_language(transcribe_language, detected_lang) -> bool:
    """Run the foreign-language DAI detector only when the audio is English.

    The detector flags non-English ad insertions in English podcasts; on a
    non-English one it matches every segment. Runs only for ``whisper_language='en'``
    or ``'auto'`` with a detected English variant.
    """
    if transcribe_language == 'en':
        return True
    if transcribe_language is None:
        return (detected_lang or '').lower().startswith('en')
    return False


def detect_non_english_segment(text: str, primary_language: str) -> bool:
    """Detect if a segment is likely non-English (potential DAI ad).

    Heuristics: high non-ASCII ratio, common Spanish/other-language patterns, and
    markers when the primary detected language (from Whisper) is not English.
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
