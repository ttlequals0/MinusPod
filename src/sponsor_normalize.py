"""Sponsor name sanitization and known_sponsors FK resolution.

All sponsor writes from the rest of the codebase flow through
`get_or_create_known_sponsor()` so the canonical row in
`known_sponsors` is the only place sponsor names live.
"""
import string


_STRIP_CHARS = string.whitespace + '\'"`.,;:!?-'
_MAX_LENGTH = 100


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
    existing = db.get_known_sponsor_by_name(s)
    if existing:
        return existing['id']
    return db.create_known_sponsor(name=s)
