"""Transcript checks for whether a named sponsor is being advertised."""
import re

from utils.constants import squash_brand

COMMERCIAL_CONTEXT_RE = re.compile(
    r'\b(?:use\s+(?:promo\s+)?code\s+\w+|promo\s+code|'
    r'(?:\d+\s*%|(?:\d+|ten|fifteen|twenty|thirty|forty|fifty)\s+percent)'
    r'\s+off|free\s+(?:trial|shipping)|'
    r'book\s+a\s+call|request\s+a\s+demo)\b', re.IGNORECASE)
# Group 1 of each framing pattern is the text that must name the sponsor.
SPONSOR_FRAMING_RE = re.compile(
    r'\b(?:sponsored\s+by|brought\s+to\s+you\s+by|our\s+sponsors?\s+at)\s+'
    r'([^.!?]{1,80})', re.IGNORECASE)
SPONSOR_THANKS_RE = re.compile(
    r'\bthanks?\s+(?:you\s+)?(?:to\s+)?([^.!?]{1,80}?)\s+'
    r'for\s+(?:supporting|sponsoring)\b', re.IGNORECASE)
SPONSOR_IS_SPONSOR_RE = re.compile(
    r'([^.!?,;]{1,80})\s+is\s+(?:a|our)\s+sponsor(?=\s*(?:[.!?,;]|$))',
    re.IGNORECASE)
# Group 1 of each link pattern is the domain label; squash_brand drops
# the hyphens and spaces of a spelled-out one ("A-C-M-E.com").
BRAND_LINK_RE = re.compile(
    r'\b(?:visit|go\s+to|head\s+to|shop\s+at|learn\s+more\s+at|'
    r'check\s+(?:them|it)\s+out\s+at|find\s+out\s+more\s+at)\s+'
    r'([a-z0-9-]+)\.(?:com|io|org|net)\b', re.IGNORECASE)
# Read-aloud only: a spelled-out label or a spoken "dot com".
BARE_LINK_RE = re.compile(
    r"(?<![\w'])((?:[a-z0-9][\s-]){2,}[a-z0-9](?=\.|\s+dot\s)|"
    r"[a-z0-9-]+(?=\s+dot\s))(?:\.|\s+dot\s+)(?:com|io|org|net)\b",
    re.IGNORECASE)
# (pattern, exact): an exact pattern's group 1 must be the brand alone.
FRAMING_PATTERNS = ((SPONSOR_FRAMING_RE, False), (SPONSOR_THANKS_RE, True),
                    (SPONSOR_IS_SPONSOR_RE, True))
LINK_PATTERNS = (BRAND_LINK_RE, BARE_LINK_RE)


def text_has_commercial_context(text: str, sponsor: str, *, names_sponsor, matches_expected,
                                following: str = '') -> bool:
    """Whether text names sponsor with an offer, or with a framing or link in reach of following."""
    if not names_sponsor(text, sponsor):
        return False
    if COMMERCIAL_CONTEXT_RE.search(text):
        return True
    nearby = text + ' ' + following
    for pattern, exact in FRAMING_PATTERNS:
        for match in pattern.finditer(nearby):
            framing = match.group(1)
            if (matches_expected(framing.strip(), sponsor) if exact
                    else names_sponsor(framing, sponsor)):
                return True
    return any(squash_brand(link.group(1)) == squash_brand(sponsor)
               for pattern in LINK_PATTERNS for link in pattern.finditer(nearby))
