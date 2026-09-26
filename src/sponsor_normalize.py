"""Sponsor name sanitization and known_sponsors FK resolution.

All sponsor writes from the rest of the codebase flow through
`get_or_create_known_sponsor()` so the canonical row in
`known_sponsors` is the only place sponsor names live.
"""
import logging
import re
import string
from functools import lru_cache

from utils.constants import (
    NON_SPONSOR_LINK_DOMAINS, is_brand_token,
    is_hosting_platform_name, is_non_brand_name, strip_apostrophe_suffixes,
)

logger = logging.getLogger(__name__)


_STRIP_CHARS = string.whitespace + '\'"`.,;:!?-'
_MAX_LENGTH = 100
# The curly apostrophe is normalized by the shared helper, so one spelling.
_POSSESSIVE_SUFFIXES = ("'s",)
# Names are stored as typed, so a lookup has to try each spelling.
_POSSESSIVE_SPELLINGS = ("'s", "\u2019s")


# Whole words only, so "keeps" and "romance" do not yield sponsors.
DESCRIPTION_SPONSOR_PATTERNS = re.compile(
    r'(?<!\w)(?:betterhelp|athletic\s*greens|ag1|squarespace|nordvpn|'
    r'expressvpn|hellofresh|audible|masterclass|ziprecruiter|'
    r'raycon|manscaped|stamps\.com|indeed|linkedin|'
    r'casper|helix|brooklinen|bombas|calm|headspace|'
    r'better\s*help|honey|simplisafe|wix|shopify|'
    r'bluechew|roman|hims|keeps|factor|noom|'
    r'magic\s*spoon|athletic\s*brewing|liquid\s*iv)(?!\w)',
    re.IGNORECASE
)

# Domains from href URLs (e.g., "bitwarden.com/twit" -> "bitwarden").
_DESCRIPTION_HREF_RE = re.compile(
    r'href=["\']?(?:https?://)?(?:www\.)?([a-z0-9-]+)\.(?:com|io|co|net|org)',
    re.IGNORECASE)


@lru_cache(maxsize=64)
def extract_description_sponsors(episode_description: str | None) -> frozenset:
    """Lowercase sponsor names from a description; cached and shared by detector and validator."""
    sponsors = set()
    if not episode_description:
        return frozenset()

    for match in _DESCRIPTION_HREF_RE.finditer(episode_description):
        domain = match.group(1).lower()
        # A description links to its host, its apps, and its socials next
        # to its sponsors, and a short outlet token matches normal speech.
        if domain in NON_SPONSOR_LINK_DOMAINS or not is_brand_token(domain):
            continue
        sponsors.add(domain)

    # Both the spoken form and the squashed one are kept, so "liquid iv"
    # confirms against a transcript however the brand is written.
    for match in DESCRIPTION_SPONSOR_PATTERNS.finditer(episode_description.lower()):
        sponsor = match.group(0).lower()
        sponsors.add(sponsor)
        sponsors.add(sponsor.replace(' ', ''))

    if sponsors:
        logger.info(f"Extracted sponsors from description: {sponsors}")

    return frozenset(sponsors)


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


def get_or_create_known_sponsor(db, name, conn=None):
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
    existing = db.get_known_sponsor_by_name(s, conn=conn)
    if existing:
        return existing['id']
    base = _possessive_base(s)
    if base:
        base_row = db.get_known_sponsor_by_name(base, conn=conn)
        if base_row:
            return base_row['id']
    else:
        # Symmetric: a possessive brand keeps its own spelling, so the row it
        # created has to be found when the base name arrives later.
        for spelling in _POSSESSIVE_SPELLINGS:
            possessive_row = db.get_known_sponsor_by_name(s + spelling, conn=conn)
            if possessive_row:
                return possessive_row['id']
    return db.create_known_sponsor(name=s, conn=conn)
