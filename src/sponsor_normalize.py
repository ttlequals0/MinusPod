"""Sponsor name sanitization and known_sponsors FK resolution.

All sponsor writes from the rest of the codebase flow through
`get_or_create_known_sponsor()` so the canonical row in
`known_sponsors` is the only place sponsor names live.
"""
import re
import string

from utils.constants import (
    is_hosting_platform_name, is_non_brand_name, strip_apostrophe_suffixes,
)


_STRIP_CHARS = string.whitespace + '\'"`.,;:!?-'
_MAX_LENGTH = 100
# The curly apostrophe is normalized by the shared helper, so one spelling.
_POSSESSIVE_SUFFIXES = ("'s",)
# Names are stored as typed, so a lookup has to try each spelling.
_POSSESSIVE_SPELLINGS = ("'s", "\u2019s")


def segment_category_for(label, overrides):
    """Segment category for a sponsor label: exact name or alias match, else
    the longest known name appearing in the label as a whole word."""
    if not label or not overrides:
        return None
    key = ' '.join(str(label).split()).lower()
    if key in overrides:
        return overrides[key]
    best = None
    for name, category in overrides.items():
        if len(name) >= 3 and re.search(rf'(?<!\w){re.escape(name)}(?!\w)', key):
            if best is None or len(name) > len(best[0]):
                best = (name, category)
    return best[1] if best else None


def _possessive_base(name):
    """Base name of a trailing possessive, else None."""
    bases = strip_apostrophe_suffixes(name, _POSSESSIVE_SUFFIXES)
    return bases[0] if bases else None


def get_or_create_known_sponsor(db, name):
    """Resolve a free-text sponsor name to a `known_sponsors.id`.

    Sanitization, in order:
      1. Must be a string.
      2. Strip leading/trailing whitespace and outer quotes/punctuation.
      3. Reject if any character is a non-whitespace control char
         (0x00-0x1F minus \\t \\n \\r, plus 0x7F).
      4. Collapse runs of internal whitespace to single spaces.
      5. Reject if empty after sanitization.
      6. Reject if longer than 100 characters.

    Returns:
      Existing row id on case-insensitive match, or a new row id if
      inserted, or `None` for any rejected input.
    """
    if not isinstance(name, str):
        return None
    s = name.strip(_STRIP_CHARS)
    if any(
        (ord(c) < 0x20 and c not in '\t\n\r') or ord(c) == 0x7F
        for c in s
    ):
        return None
    s = ' '.join(s.split())
    if not s:
        return None
    if len(s) > _MAX_LENGTH:
        return None
    if is_non_brand_name(s) or is_hosting_platform_name(s):
        return None
    existing = db.get_known_sponsor_by_name(s)
    if existing:
        return existing['id']
    base = _possessive_base(s)
    if base:
        base_row = db.get_known_sponsor_by_name(base)
        if base_row:
            return base_row['id']
    else:
        # Symmetric: a possessive brand keeps its own spelling, so the row it
        # created has to be found when the base name arrives later.
        for spelling in _POSSESSIVE_SPELLINGS:
            possessive_row = db.get_known_sponsor_by_name(s + spelling)
            if possessive_row:
                return possessive_row['id']
    return db.create_known_sponsor(name=s)
