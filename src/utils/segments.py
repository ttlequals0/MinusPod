import logging
import math

from typing import TypedDict, NotRequired, TypeGuard, Any
from collections.abc import Sequence

logger = logging.getLogger(__name__)


class WordSegment(TypedDict):
    """A word with timing; `word` is space-prefixed and non-empty, `end` >= `start`."""
    word: str
    start: float|int
    end: float|int

# Segments are plain dicts at runtime; the Like aliases keep type conversions easy.
type WordSegmentLike = WordSegment|dict

class TextSegment(TypedDict):
    """A text segment with optional word timings; `words` are chronological and inside [start, end]."""
    text: str
    start: float|int
    end: float|int
    words: NotRequired[list[WordSegmentLike]]
    terminal: NotRequired[bool]
    word_timed_line: NotRequired[bool]

type TextSegmentLike = TextSegment|dict

def word_spans(word_segments: Sequence[WordSegmentLike],
               ) -> tuple[str, list[tuple[int, int]]]:
    """Join words into one utterance and return each word's (start, stop) span in it.

    If any word is space-prefixed, words without a leading space continue the
    previous word (e.g. "&T" after " AT"); otherwise every word is space-separated.
    """
    prefixed = any(w['word'].startswith(' ') for w in word_segments)
    text = ''
    spans: list[tuple[int, int]] = []
    for i, w in enumerate(word_segments):
        raw = w['word']
        if i and not (prefixed and not raw.startswith(' ')):
            text += ' '
        token = raw.strip()
        spans.append((len(text), len(text) + len(token)))
        text += token
    return text, spans

def join_word_segments(word_segments: Sequence[WordSegmentLike]) -> str:
    """Join word segments into one utterance."""
    return word_spans(word_segments)[0]

def is_text_segment(obj: Any) -> TypeGuard[TextSegmentLike]:
    """Check that an object is shaped like a `TextSegment`."""
    return (
        isinstance(obj, dict)
        and isinstance(obj.get("text"), str)
        and isinstance(obj.get("start"), (float, int))
        and isinstance(obj.get("end"), (float, int))
        and ((x := obj.get("words")) is None or isinstance(x, list))
        and ((x := obj.get("terminal")) is None or isinstance(x, bool))
        and ((x := obj.get("word_timed_line")) is None or isinstance(x, bool))
    )

def validate_text_segment(segment: TextSegmentLike,
                          strict: bool = True,
                          require_space_prefixed_words: bool|None = None,
                          tolerance: float = 0.001,
                          ) -> None:
    """Validate a text segment; strict also requires start <= end and words ordered inside [start, end]."""
    if not is_text_segment(segment):
        raise TypeError(f"Invalid text segment structure: {segment}")
    # structure is correct. validate values...
    if not (math.isfinite(segment["start"]) and math.isfinite(segment["end"])):
        raise ValueError(f"Invalid segment timing: {segment}")
    if strict and segment["end"] < segment["start"]:
        raise ValueError(f"Segment end time is before start time: {segment}")
    if (words := segment.get("words")):
        validate_word_segments(words, 
                               strict=strict,
                               require_space_prefix=require_space_prefixed_words,
                               tolerance=tolerance)
        if strict:
            # word[0].start and word[-1].end should equal the segment's start
            # and end, but we only enforce that they are within bounds.
            if (segment["start"] - words[0]["start"] > tolerance
                or words[-1]["end"] - segment["end"] > tolerance):
                raise ValueError(f"Word segments are not contained within the parent segment: {segment}")
    
def is_word_segment(obj: Any) -> TypeGuard[WordSegmentLike]:
    """Check that an object is shaped like a `WordSegment`."""
    return (
        isinstance(obj, dict)
        and isinstance(obj.get("word"), str)
        and isinstance(obj.get("start"), (float, int))
        and isinstance(obj.get("end"), (float, int))
    )

def validate_word_segment(word: WordSegmentLike,
                          strict: bool = True,
                          require_space_prefix: bool|None = None,
                          ) -> None:
    """Validate a word segment; strict requires start <= end, non-empty text and a space prefix."""
    if require_space_prefix is None:
        require_space_prefix = strict
    if not is_word_segment(word):
        raise TypeError(f"Invalid word segment structure: {word}")
    # structure is correct. validate values...
    start, end = word['start'], word['end']
    if not (math.isfinite(start) and math.isfinite(end)):
        raise ValueError(f"Invalid word segment timing: {word}")
    if strict:
        if end < start:
            raise ValueError(f"Word segment end time is before start time: {start} -> {end}")
        if not word["word"]:
            raise ValueError(f"Word segment is empty text: {word}")
    if require_space_prefix and not word["word"].startswith(' '):
        raise ValueError(f"Word segment is not space-prefixed: {word['word']}")

def validate_word_segments(words: Sequence[WordSegmentLike],
                           strict: bool = True,
                           require_space_prefix: bool|None = None,
                           tolerance: float = 0.001,
                           ) -> None:
    """Validate word segments; strict also requires them ordered by start (overlap allowed)."""
    if not isinstance(words, Sequence):
        raise TypeError(f"Invalid list of word segments: {words}")
    prev = None
    for word in words:
        validate_word_segment(word,
                              strict=strict,
                              require_space_prefix=require_space_prefix)
        if strict and prev is not None:
            if prev["start"] - word["start"] > tolerance:
                raise ValueError(f"Word segments are out of order: {words}")
            # Dont worry about overlap. It should not exist but if it does, 
            # we'll assume there is a reason for it.
        prev = word


def normalize_segments(segments: Sequence[TextSegmentLike]) -> list[TextSegmentLike]:
    """Normalize segments in place and sort them by start and end.

    Segments that fail `validate_text_segment(strict=False)` are dropped with one warning.
    Text and segment timings are never rewritten; only the word lists are normalized.
    """
    if not isinstance(segments, Sequence):
        raise TypeError(f"Invalid sequence of text segments: {segments}")
    valid: list[TextSegmentLike] = []
    dropped = 0
    first_error = ''
    for s in segments:
        try:
            validate_text_segment(s, strict=False)
        except (TypeError, ValueError) as e:
            dropped += 1
            first_error = first_error or str(e)
        else:
            valid.append(s)
    if dropped:
        logger.warning(f"Dropped {dropped} invalid transcript segment(s); first error: {first_error}")
    valid = [_normalize_segment(s) for s in valid]
    valid.sort(key=lambda s: (s['start'], s['end']))
    return valid

def _normalize_segment(segment: TextSegmentLike) -> TextSegmentLike:
    segment['text'] = segment['text'].strip()
    start = float(segment['start'])
    segment['start'] = start
    segment['end'] = max(start, float(segment['end']))
    if (words := segment.get('words')):
        segment['words'] = _normalize_word_segments(words)
    return segment

def _normalize_word_segments(input: list[WordSegmentLike]) -> list[WordSegmentLike]:
    """Sort, merge split tokens, space-prefix and trim overlap; never drops a word."""
    if not input:
        return []
    words = [_normalize_word_segment(w) for w in input]
    words.sort(key=lambda w: w['start'])  # stable

    # Whisper emits unprefixed continuation tokens: " U|.S.", " AT|&T", " example|.com",
    # " 100|,000", " y|'all", " built|-in". If any word is space-prefixed, merge the rest
    # into the previous word.
    space_prefixed = any(w['word'].startswith(' ') for w in words)
    out: list[WordSegmentLike] = []
    for word in words:
        last = out[-1] if out else None
        if last is not None and space_prefixed and not word['word'].startswith(' '):
            last['word'] += word['word']
            last['end'] = max(last['end'], word['end'])
            continue
        if not word['word'].startswith(' '):
            word['word'] = ' ' + word['word'].strip()
        out.append(word)

    # trim overlap by shifting the end time (preserves sort order)
    for cur, nxt in zip(out, out[1:], strict=False):
        if cur['end'] > nxt['start']:
            cur['end'] = nxt['start']
    return out

def _normalize_word_segment(word: WordSegmentLike) -> WordSegmentLike:
    start = float(word['start'])
    word['start'] = start
    word['end'] = max(start, float(word['end']))
    return word
