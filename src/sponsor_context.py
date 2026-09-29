"""Transcript checks for whether a named sponsor is being advertised."""
import re

from sponsor_normalize import extract_description_sponsors
from utils.constants import is_brand_token, squash_brand
from utils.text import most_mentioned, word_boundary_re

# A sponsor named once can be a passing mention.
SPONSOR_MIN_MENTIONS = 2

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
# A written domain ("acme.com", "acme .com") or a spoken "acme dot com/org/net/co".
DOMAIN_LABEL_RE = re.compile(r'\b([a-z0-9-]{3,})(?:\.[a-z]{2,6}|\s+dot\s+(?:com|org|net|co))\b')


def domain_labels(text: str) -> set[str]:
    """Lowercase domain labels a text names."""
    return set(DOMAIN_LABEL_RE.findall(re.sub(r'\s+\.', '.', text.lower())))


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


def local_commercial_context(texts: list[str], sponsor: str, *, names_sponsor,
                             matches_expected) -> bool:
    """Whether one of texts names sponsor in commercial context, with the next text in reach."""
    return any(text_has_commercial_context(
        text, sponsor, names_sponsor=names_sponsor, matches_expected=matches_expected,
        following=texts[index + 1] if index + 1 < len(texts) else '')
        for index, text in enumerate(texts))


def registry_sponsor(sponsor_service, texts: list[str], *, names_sponsor, matches_expected,
                     expected: str | None = None) -> tuple[str | None, int, bool]:
    """(brand, mentions, confirmed) for the registry sponsor texts name most; expected filters brands."""
    offsets = sponsor_service.brand_mention_offsets(' '.join(texts))
    if expected:
        offsets = {name: found for name, found in offsets.items()
                   if matches_expected(name, expected)}
    brand, mentions = most_mentioned(offsets)
    confirmed = mentions >= SPONSOR_MIN_MENTIONS and local_commercial_context(
        texts, brand, names_sponsor=names_sponsor, matches_expected=matches_expected)
    return brand, mentions, confirmed


def description_sponsor_re(description: str | None) -> re.Pattern | None:
    """Matcher for the brand-like sponsors a description names, or None."""
    return word_boundary_re(
        {name for name in extract_description_sponsors(description) if is_brand_token(name)})
