"""Shared text-similarity helpers for ad-pattern dedupe and merge.

Lifted from `tools/community_pattern_validator` so the community-submission
validator (CI + import endpoint) and the runtime merge/clustering paths
(`pattern_service`, `api/patterns`) compute similarity with one implementation.
The validator re-exports these names for backward compatibility.

Bands: `>= DUPLICATE_THRESHOLD` is a duplicate, `>= VARIANT_THRESHOLD` (and
below duplicate) is a variant, below that is distinct.
"""
import re
from difflib import SequenceMatcher

from utils.community_tags import (
    CANONICAL_DAYS,
    CANONICAL_MONTHS,
    CANONICAL_RELATIVE_TIME,
    CANONICAL_STOPWORDS,
    DATE_REGEX,
    YEAR_REGEX,
)

DUPLICATE_THRESHOLD = 0.95
VARIANT_THRESHOLD = 0.75


def canonicalize_for_dedupe(text: str) -> str:
    """Return the canonical form of `text` used for dedupe comparison only.

    Lowercase -> strip date/year tokens (BEFORE punctuation removal, otherwise
    '12/31' becomes '12 31' and slips past) -> punctuation->space -> collapse
    whitespace -> strip stopwords / day / month / relative-time tokens -> trim.
    The original text is not modified.
    """
    if not text:
        return ''
    s = text.lower()
    s = DATE_REGEX.sub(' ', s)
    s = YEAR_REGEX.sub(' ', s)
    s = re.sub(r'[^a-z0-9\s]+', ' ', s)
    s = re.sub(r'\s+', ' ', s).strip()
    if not s:
        return ''
    tokens = s.split(' ')
    drop = CANONICAL_STOPWORDS | CANONICAL_DAYS | CANONICAL_MONTHS | CANONICAL_RELATIVE_TIME
    kept = [t for t in tokens if t and t not in drop]
    return ' '.join(kept)


def similarity(a: str, b: str) -> float:
    """Compute the similarity ratio between two canonicalized strings."""
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a, b).ratio()
