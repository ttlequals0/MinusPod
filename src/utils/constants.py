"""Shared constants for ad detection and pattern matching.

Centralizes field name sets and classification values that were previously
duplicated across ad_detector.py and text_pattern_matcher.py.
"""

import re
from enum import Enum



class EpisodeStatus(str, Enum):
    """Episode lifecycle statuses.

    DISCOVERED..PERMANENTLY_FAILED mirror the schema CHECK constraint on
    episodes.status (src/database/schema.py:50). COMPLETED is the API-facing
    alias the frontend sees; src/api/episodes/core.py maps PROCESSED -> COMPLETED
    in responses. Inherits from str so existing == comparisons against bare
    literals keep working without a wide-scope refactor.
    """
    DISCOVERED = "discovered"
    PENDING = "pending"
    PROCESSING = "processing"
    PROCESSED = "processed"
    FAILED = "failed"
    PERMANENTLY_FAILED = "permanently_failed"
    # Offline queue (#482): waiting for the LLM/Whisper endpoint to be
    # reachable again. Re-driven or TTL-expired by offline_queue_tick.
    DEFERRED = "deferred"
    COMPLETED = "completed"

    @classmethod
    def to_api(cls, status):
        """DB status -> API status: 'processed' is exposed as 'completed'.
        Every other status passes through unchanged."""
        return cls.COMPLETED.value if status == cls.PROCESSED else status

    @classmethod
    def from_api(cls, status):
        """API status -> DB status: accept the 'completed' alias for
        'processed'. Every other status passes through unchanged."""
        return cls.PROCESSED.value if status == cls.COMPLETED else status


# Invalid sponsor values that indicate extraction failure or garbage data.
# Used by ad_detector (validate_ads_from_response, _extract_sponsor_from_reason)
# and text_pattern_matcher (create_pattern_from_ad).
INVALID_SPONSOR_VALUES = frozenset({
    'none', 'unknown', 'null', 'n/a', 'na', '', 'no', 'yes',
    'ad', 'ads', 'sponsor', 'sponsors', 'advertisement', 'advertisements',
    'multiple', 'various', 'detected', 'advertisement detected',
    'host read', 'host-read', 'mid-roll', 'pre-roll', 'post-roll',
    # Window-continuation notes the prompt itself asks for. 'note' is a
    # sponsor-candidate key, so a short one became the brand name and was
    # offered to pattern learning as a sponsor.
    'continues in next', 'continues from previous', 'continued',
    'continues', 'continuation',
    # Quantity words that open a reason ("Two consecutive cross-promotion
    # ads") are the first capitalized run, and became the sponsor. Only a
    # whole run is rejected, so "Five Guys" still labels. A stored registry
    # row of one bare count word also stops matching, which is intended.
    'one', 'two', 'three', 'four', 'five', 'six', 'seven', 'eight',
    'nine', 'ten', 'several', 'both',
})

# Claude occasionally returns a reasoning sentence in the `sponsor` slot
# (e.g. "Inferred from ~26 second gap in transcript with no spoken content").
# Reject any value that starts with one of these prefixes (case-insensitive)
# or that contains an unambiguously meta substring. Real sponsor names never
# do. The text_pattern_matcher rejects these later, but catching them at
# parse time keeps junk out of the ad dict in the first place.
SPONSOR_REASONING_PREFIXES = (
    'inferred from', 'inferred', 'based on', 'according to',
    'likely ', 'possibly ', 'may be ', 'appears to ', 'seems to ',
    'detected as ', 'classified as ', 'regular discussion',
)
SPONSOR_REASONING_SUBSTRINGS = (
    ' in transcript', 'audio signal', 'no spoken content',
    'gap in transcript', 'volume anomaly',
)

# The substrings above only decide for text short enough to be nothing but a
# rationale; a full description can mention the transcript in passing. A
# rationale-shaped prefix still decides at any length.
SPONSOR_RATIONALE_SUBSTRING_MAX_CHARS = 200
CANCELED_ERROR_MESSAGE = 'Canceled by user'

SPONSOR_MAX_NAME_CHARS = 60

# Backstop on the detector's free-text reason. Generous on purpose: the old
# 300/150 caps put a literal "..." in the UI with nothing behind it (#591).
REASON_DESCRIPTION_MAX = 2000

# Max chars of quoted transcript text carried into a pattern match reason.
# Above the phrase-variant cap, so only a pathological alignment trips it.
PATTERN_EVIDENCE_MAX_CHARS = 220

_SQUASH_RE = re.compile(r'[^a-z0-9]')

# Longest a brand name is taken to be when no domain confirms where it ends;
# beyond this the model is describing rather than naming.
MAX_BRAND_WORDS = 4
# The labeler's span search is quadratic in a run's word count, so bound it.
# A brand a domain agrees with is never this long; past here it is prose.
MAX_SPAN_WORDS = 12


def squash_brand(text) -> str:
    """Brand text reduced to comparable characters, so a slug-style rendering
    still matches: "Jack Archer" and "jackarcher.com" both give 'jackarcher'."""
    return _SQUASH_RE.sub('', str(text).lower())


def is_sponsor_reasoning_rationale(text) -> bool:
    """True if `text` looks like an LLM reasoning sentence stored in a slot
    that should hold a brand name or ad description.

    Single source of truth for the 2.5.11 sponsor-field guard
    (ad_detector/prompts.py:_get_valid_sponsor_value), the 2.5.13 verification-miss
    `reason` filter (pattern_service.record_verification_misses), and the
    `_cleanup_low_mention_patterns` migration.
    """
    if not text:
        return False
    lowered = str(text).strip().lower()
    if lowered.startswith(SPONSOR_REASONING_PREFIXES):
        return True
    if len(lowered) <= SPONSOR_RATIONALE_SUBSTRING_MAX_CHARS:
        return any(s in lowered for s in SPONSOR_REASONING_SUBSTRINGS)
    return False


# Host lead-in before the advertiser ("our friends and sponsors at Acme").
# The brand is what follows; rejecting the whole string loses it.
_SPONSOR_LEAD_IN_RE = re.compile(
    r'^(?:(?:our|their|the|his|her|its|my|your)\s+)?'
    r'(?:friends?|partners?|sponsors?|supporters?)'
    r'(?:\s+and\s+(?:friends?|partners?|sponsors?|supporters?))*'
    r'\s+(?:at|from)\s+(\S.*)$', re.I)

# Credit lead-in before the advertiser ("Sponsored by Acme"). The whole
# phrase goes, preposition included, or the brand is stored as "by Acme".
_CREDIT_LEAD_IN_RE = re.compile(
    r'^(?:brought\s+to\s+you|sponsored|produced|presented|powered|hosted|'
    r'edited|written)\s+by\b(?:\s+(\S.*))?$', re.I)

# Possessives that precede either a brand ("Our Place") or junk ("our
# sponsor"). Kept in the label; what follows decides.
_POSSESSIVE_LEAD_WORDS = frozenset({
    'our', 'their', 'his', 'her', 'its', 'my', 'your',
})


def sanitize_sponsor_label(text, show_name: str | None = None) -> str | None:
    """The advertiser in an LLM sponsor slot, or None when it names none.
    A host lead-in ("their friends and sponsors at Acme") and a credit lead-in
    ("Sponsored by Acme") are stripped; prose, segment/structure labels and the
    show's own name are rejected."""
    if not text:
        return None
    label = str(text).strip()
    if is_sponsor_reasoning_rationale(label) or AUDIO_ECHO_RE.search(label):
        return None
    credit = _CREDIT_LEAD_IN_RE.match(label)
    if credit:
        label = (credit.group(1) or '').strip()
        if not label:
            return None
    lead_in = _SPONSOR_LEAD_IN_RE.match(label)
    if lead_in:
        label = lead_in.group(1).strip()
    if re.search(r'\bsegment$', label, re.I):
        return None
    # Imported lazily: this module is a leaf that most of src imports, and
    # config is the heavier one. A module-scope import here would make any
    # future utils import in config a cycle.
    from config import repair_segment_category
    if (repair_segment_category(label) or is_non_brand_name(label)
            or is_hosting_platform_name(label)):
        return None
    head, _, rest = label.partition(' ')
    # A possessive in front of junk is junk; in front of a real name it is
    # filler the model added, and the name stays whole.
    if head.lower() in _POSSESSIVE_LEAD_WORDS and (
            not rest.strip() or is_non_brand_name(rest.strip())
            or names_the_show(rest, show_name)):
        return None
    if names_the_show(label, show_name):
        return None
    return label


def names_the_show(text, show_name: str | None) -> bool:
    """Whether a sponsor label is just the show's own name.

    A self-promo or listener-support read has no advertiser, so the model
    puts the show there ("Dailytechnewsshow" for a Patreon thank-you). That
    is not a sponsor, and it pollutes pattern learning and same-sponsor
    merging. Compared with separators stripped, so a slug-style rendering
    still matches.
    """
    if not text or not show_name:
        return False
    label, show = squash_brand(text), squash_brand(show_name)
    if not label or not show:
        return False
    return label == show


# First-pass-learning and verification-miss confidence floors. Single source
# of truth so the two auto-pattern paths share one trust model
# (ad_detector._ad_passes_learning_filters, pattern_service.record_verification_misses).
LEARNING_MIN_CONFIDENCE = 0.85
LEARNING_MIN_CONFIDENCE_LONG = 0.92
LEARNING_LONG_DURATION_THRESHOLD = 90.0

# Duration window a learned pattern's source span must fall in. Over the
# ceiling the span usually holds several ads, so it is split before it is
# dropped.
LEARNING_MIN_PATTERN_DURATION = 15
LEARNING_MAX_PATTERN_DURATION = 120

# How far past the ceiling a piece cut at its own ad transitions may run. The
# ceiling screens for contamination, which a cut piece has already passed, but
# without a bound one undetected transition would store a pattern of any length.
LEARNING_SPLIT_DURATION_FACTOR = 2

# How far into a span the labeled brand must first appear. A read names its
# advertiser early; a brand that only turns up in the back half usually means
# the opening read belongs to someone else and the label is misattributed.
LEARNING_BRAND_ONSET_FRACTION = 0.6

# Structural fields in LLM ad response objects that never contain sponsor info.
# Everything NOT in this set is a candidate for dynamic field scanning.
STRUCTURAL_FIELDS = frozenset({
    'start', 'end', 'start_time', 'end_time', 'start_timestamp', 'end_timestamp',
    'ad_start_timestamp', 'ad_end_timestamp', 'start_time_seconds', 'end_time_seconds',
    'confidence', 'start_text', 'end_text', 'is_ad', 'type', 'classification',
    'start_seconds', 'end_seconds', 'duration', 'duration_seconds',
    'music_bed', 'music_bed_confidence',
    # 'category' and its aliases: the sponsor scan falls back to any short
    # string field, so "self_promo" was being returned as the sponsor name.
    'category', 'segment_type',
})

# Ordered list of field names to check for sponsor/advertiser name (priority order).
SPONSOR_PRIORITY_FIELDS = [
    'sponsor_name', 'advertiser', 'sponsor', 'brand', 'company', 'product', 'name'
]

# Known brand names that would otherwise be blocked by Gate B in
# ad_detector.learn_from_detections (single-word sponsors shorter than 6 chars
# that aren't in the sponsor registry). Lowercase for lookup.
KNOWN_SHORT_BRANDS = frozenset({
    'xero', 'venmo', 'kayak', 'meter', 'pura', 'opal', 'waymo', 'plaid',
    'deel', 'ramp', 'brex', 'lyft', 'uber', 'slack', 'zoom', 'asana',
    'figma', 'canva', 'miro', 'hinge', 'tonal', 'whoop',
    'noom', 'ipsy', 'lume',
    'lmnt', 'ag1',
})

# Sponsor name aliases for common Whisper mishearings / spelling variants.
# Lookup is lowercase. The value is the canonical sponsor name stored on
# created patterns. Applied in ad_detector.learn_from_detections and
# pattern_service.record_verification_misses before sponsor-based gating so
# the variants merge into one pattern family instead of splitting across
# parallel misspelled entries.
SPONSOR_ALIASES = {
    # Xero
    'zero': 'Xero',
    'xerox': 'Xero',
    # 1Password
    '1 password': '1Password',
    'one password': '1Password',
    'one-password': '1Password',
    # Affirm
    'a firm': 'Affirm',
    # AG1 / Athletic Greens (SEED canonical is "Athletic Greens"; AG1 is an alias)
    'ag one': 'Athletic Greens',
    'ag 1': 'Athletic Greens',
    'a g one': 'Athletic Greens',
    'ag1': 'Athletic Greens',
    'athletic greens one': 'Athletic Greens',
    'athleticgreens': 'Athletic Greens',
    # Athlean-X
    'athlean x': 'Athlean-X',
    'athlean-x': 'Athlean-X',
    # BetMGM
    'bet mgm': 'BetMGM',
    'bet-mgm': 'BetMGM',
    # BetterHelp
    'better help': 'BetterHelp',
    'better-help': 'BetterHelp',
    # Birchbox
    'birch box': 'Birchbox',
    'birch-box': 'Birchbox',
    # Bitwarden
    'bit warden': 'Bitwarden',
    'bit-warden': 'Bitwarden',
    # Blue Apron
    'blueapron': 'Blue Apron',
    # Brex (skip 'brexit' - distinct noun)
    'brecks': 'Brex',
    # Butcher Box (SEED canonical is two-word form)
    'butcher box': 'Butcher Box',
    'butcher-box': 'Butcher Box',
    'butcherbox': 'Butcher Box',
    # CarMax
    'car max': 'CarMax',
    'car-max': 'CarMax',
    # Cloudflare
    'cloud flare': 'Cloudflare',
    'cloud-flare': 'Cloudflare',
    # Credit Karma
    'creditkarma': 'Credit Karma',
    # DeleteMe
    'delete me': 'DeleteMe',
    'delete-me': 'DeleteMe',
    # Dollar Shave Club
    'dollarshaveclub': 'Dollar Shave Club',
    # DoorDash
    'door dash': 'DoorDash',
    'door-dash': 'DoorDash',
    # DraftKings
    'draft kings': 'DraftKings',
    'draft-kings': 'DraftKings',
    # Eight Sleep
    'eight-sleep': 'Eight Sleep',
    '8 sleep': 'Eight Sleep',
    '8-sleep': 'Eight Sleep',
    'eightsleep': 'Eight Sleep',
    # EveryPlate
    'every plate': 'EveryPlate',
    'every-plate': 'EveryPlate',
    # ExpressVPN
    'express vpn': 'ExpressVPN',
    'express-vpn': 'ExpressVPN',
    # FabFitFun
    'fab fit fun': 'FabFitFun',
    'fab-fit-fun': 'FabFitFun',
    # FanDuel
    'fan duel': 'FanDuel',
    'fan-duel': 'FanDuel',
    # Gametime (SEED canonical)
    'game time': 'Gametime',
    'game-time': 'Gametime',
    'gametime': 'Gametime',
    # GitHub Copilot
    'co pilot': 'GitHub Copilot',
    'co-pilot': 'GitHub Copilot',
    'copilot': 'GitHub Copilot',
    'github-copilot': 'GitHub Copilot',
    # Gopuff
    'go puff': 'Gopuff',
    'go-puff': 'Gopuff',
    # GoodRx
    'good rx': 'GoodRx',
    'good-rx': 'GoodRx',
    # Green Chef
    'green chef': 'Green Chef',
    'green-chef': 'Green Chef',
    'greenchef': 'Green Chef',
    # Grubhub
    'grub hub': 'Grubhub',
    'grub-hub': 'Grubhub',
    # Harry's
    'harrys': "Harry's",
    # Headspace
    'head space': 'Headspace',
    'head-space': 'Headspace',
    # HelloFresh
    'hello fresh': 'HelloFresh',
    'hello-fresh': 'HelloFresh',
    # Hims / Hims & Hers
    "him's": 'Hims',
    'hims and hers': 'Hims & Hers',
    'hims & hers': 'Hims & Hers',
    # Honeylove (SEED canonical)
    'honey love': 'Honeylove',
    'honey-love': 'Honeylove',
    'honeylove': 'Honeylove',
    # HubSpot
    'hub spot': 'HubSpot',
    'hub-spot': 'HubSpot',
    'hubs pot': 'HubSpot',
    # Imperfect Foods
    'imperfect foods': 'Imperfect Foods',
    'imperfectfoods': 'Imperfect Foods',
    # Instacart
    'insta cart': 'Instacart',
    'insta-cart': 'Instacart',
    # LegalZoom
    'legal zoom': 'LegalZoom',
    'legal-zoom': 'LegalZoom',
    'legalzoom': 'LegalZoom',
    # Liquid IV (SEED canonical; "Liquid I.V." is the alias form)
    'liquid iv': 'Liquid IV',
    'liquid i v': 'Liquid IV',
    'liquid i.v.': 'Liquid IV',
    'liquidiv': 'Liquid IV',
    # LMNT (canonical matches existing SEED entry)
    'l m n t': 'LMNT',
    'element': 'LMNT',
    # Magic Mind
    'magic mind': 'Magic Mind',
    'magicmind': 'Magic Mind',
    # Magic Spoon
    'magic spoon': 'Magic Spoon',
    'magicspoon': 'Magic Spoon',
    # MasterClass
    'master class': 'MasterClass',
    'master-class': 'MasterClass',
    # Mercury
    'mercury bank': 'Mercury',
    'mercury-bank': 'Mercury',
    # Mint Mobile
    'mint mobile': 'Mint Mobile',
    'mint-mobile': 'Mint Mobile',
    'mintmobile': 'Mint Mobile',
    # Miro (skip 'mirror' - common word)
    'my ro': 'Miro',
    # Monarch Money
    'monarch money': 'Monarch Money',
    'monarch-money': 'Monarch Money',
    'monarchmoney': 'Monarch Money',
    # Myprotein
    'my protein': 'Myprotein',
    'myprotein': 'Myprotein',
    # NetSuite
    'net suite': 'NetSuite',
    'net-suite': 'NetSuite',
    # NordVPN
    'nord vpn': 'NordVPN',
    'nord-vpn': 'NordVPN',
    # OneSkin
    'one skin': 'OneSkin',
    'one-skin': 'OneSkin',
    # P90X
    'p ninety x': 'P90X',
    # Patreon
    'pay tree on': 'Patreon',
    'patron': 'Patreon',
    # Perplexity
    'perplexity ai': 'Perplexity',
    'perplexity-ai': 'Perplexity',
    # PolicyGenius
    'policy genius': 'PolicyGenius',
    'policy-genius': 'PolicyGenius',
    # Pura
    'pyura': 'Pura',
    # Raycon
    'ray con': 'Raycon',
    'ray-con': 'Raycon',
    # Retool
    're tool': 'Retool',
    # Rocket Lawyer / Money / Mortgage
    'rocketlawyer': 'Rocket Lawyer',
    'rocket money': 'Rocket Money',
    'rocket-money': 'Rocket Money',
    'rocketmoney': 'Rocket Money',
    'rocketmortgage': 'Rocket Mortgage',
    # Rogaine
    'ro gain': 'Rogaine',
    'ro-gaine': 'Rogaine',
    # SeatGeek
    'seat geek': 'SeatGeek',
    'seat-geek': 'SeatGeek',
    # Shopify
    'shop ify': 'Shopify',
    'shop a fly': 'Shopify',
    'shop fly': 'Shopify',
    # SimpliSafe
    'simpli safe': 'SimpliSafe',
    'simpli-safe': 'SimpliSafe',
    'simply safe': 'SimpliSafe',
    # Skyscanner
    'sky scanner': 'Skyscanner',
    'sky-scanner': 'Skyscanner',
    # SoFi (skip 'Sophie' - common name)
    'so fi': 'SoFi',
    'so-fi': 'SoFi',
    # Squarespace
    'square space': 'Squarespace',
    'square-space': 'Squarespace',
    # Stamps.com
    'stamp dot com': 'Stamps.com',
    # Stitch Fix
    'stitch fix': 'Stitch Fix',
    'stitch-fix': 'Stitch Fix',
    'stitchfix': 'Stitch Fix',
    # StubHub
    'stub hub': 'StubHub',
    'stub-hub': 'StubHub',
    # Substack
    'sub stack': 'Substack',
    'sub-stack': 'Substack',
    # Thrive Market
    'thrive market': 'Thrive Market',
    'thrivemarket': 'Thrive Market',
    # Transparent Labs
    'transparent labs': 'Transparent Labs',
    'transparentlabs': 'Transparent Labs',
    # Uber Eats
    'uber eats': 'Uber Eats',
    'uber-eats': 'Uber Eats',
    'ubereats': 'Uber Eats',
    # Vercel
    'ver sel': 'Vercel',
    'ver cell': 'Vercel',
    # Wealthfront
    'wealth front': 'Wealthfront',
    'wealth-front': 'Wealthfront',
    # Whoop
    'woop': 'Whoop',
    # ZipRecruiter
    'zip recruiter': 'ZipRecruiter',
    'zip-recruiter': 'ZipRecruiter',
    # ZocDoc
    'zoc doc': 'ZocDoc',
    'zoc-doc': 'ZocDoc',
    'zock doc': 'ZocDoc',
}


def canonical_sponsor(sponsor):
    """Return ``SPONSOR_ALIASES[sponsor.lower()]`` if present, else ``sponsor`` unchanged.

    Keeps the original casing when there is no alias match so unrelated sponsors
    are not touched; only known mishearings collapse onto the canonical name.
    """
    if not sponsor or not isinstance(sponsor, str):
        return sponsor
    return SPONSOR_ALIASES.get(sponsor.strip().lower(), sponsor)

# Keywords to match against any JSON key for fuzzy sponsor field detection.
SPONSOR_PATTERN_KEYWORDS = [
    'sponsor', 'brand', 'advertiser', 'company', 'product', 'ad_name', 'note'
]

# Invalid capture words - common English words that indicate regex captured garbage
# e.g., "not an advertisement" -> regex captures "not an" as sponsor.
# Distinct from NON_BRAND_WORDS below: this set targets English filler/
# grammatical words that appear at the START of a captured sponsor name
# (validate_extracted_sponsor in ad_detector). NON_BRAND_WORDS targets
# ad-domain vocabulary that follows or surrounds a sponsor mention.
INVALID_SPONSOR_CAPTURE_WORDS = frozenset({
    'not', 'no', 'this', 'that', 'the', 'a', 'an', 'another',
    'consistent', 'possible', 'potential', 'likely', 'seems',
    'is', 'was', 'are', 'were', 'with', 'from', 'for', 'by',
    'clear', 'any', 'some', 'host', 'their', 'its', 'our',
})

# Ad-domain vocabulary that appears in ad reasons / Claude output but is
# never a brand name. Used by ad_detector to filter spurious "sponsor"
# captures pulled from reason strings like "sponsor read" or "ad segment".
# This set is a strict superset of the inline excluded_words previously
# defined in extract_sponsor_names (the latter targeted the same domain
# but was narrower).
NON_BRAND_WORDS = frozenset({
    'ad', 'ads', 'sponsor', 'sponsored', 'advertisement', 'commercial',
    'host', 'read', 'segment', 'content', 'break', 'detected', 'detection',
    'network', 'inserted', 'dynamically', 'transition', 'promotional',
    'promo', 'promotion', 'mention', 'mentioned', 'plug', 'spot',
    'the', 'and', 'for', 'with', 'from', 'this', 'that', 'into',
    'brand', 'tagline', 'product', 'pitch', 'marketing', 'copy',
    'complete', 'partial', 'full', 'brief', 'short', 'long',
    'message', 'insert', 'mid', 'roll', 'pre', 'post',
})

# Sponsor-slot junk the model reaches for when it has no advertiser to name.
# Separate from NON_BRAND_WORDS, which also drives keyword extraction where
# dropping these would cost real matches.
SEGMENT_STRUCTURE_WORDS = frozenset({
    'show', 'episode', 'podcast', 'segment', 'section', 'chapter',
})

# Single common English words sometimes emitted as a standalone "sponsor";
# learned as a one-word brand they match normal speech and force-confirm a
# false positive. Only whole single-token names are checked (see below).
COMMON_SPEECH_WORDS = frozenset({
    'all', 'anyway', 'out', 'live', 'couch', 'comment', 'fuck', 'well',
    'okay', 'yeah', 'right', 'now', 'here', 'there', 'then', 'also', 'just',
    'only', 'about', 'anything', 'everything', 'nothing', 'someone',
    'anyone', 'everyone', 'actually', 'really', 'maybe', 'today', 'stuff',
})


# Pronouns and auxiliaries that reach the sponsor slot only as the stem of a
# quoted contraction ("Let's", "You're"), never as a brand on their own.
CONTRACTION_STEM_WORDS = frozenset({
    'let', 'you', 'we', 'they', 'it', 'that', 'this', 'there', 'here',
    'what', 'who', 'i', 'he', 'she', 'do', 'does', 'did', 'can', 'will',
    'is', 'are', 'was', 'were', 'has', 'have', 'had', 'would', 'could',
    'should',
})

# Endings split off a single token before its stem is checked. Both "n't" and
# "'t" are tried so "don't" and "can't" each reach a real stem.
_CONTRACTION_SUFFIXES = ("'s", "'re", "'ll", "'ve", "'d", "'m", "n't", "'t")


def strip_apostrophe_suffixes(name: str, suffixes) -> list[str]:
    """Bases left by stripping one trailing apostrophe suffix from `name`.

    A curly U+2019 matches the ASCII form, so a suffix list only has to spell
    each ending once. Bases keep the original spelling of what is left.
    """
    text = str(name)
    lowered = text.lower().replace('\u2019', "'")
    return [text[:-len(suffix)] for suffix in suffixes
            if lowered.endswith(suffix) and len(text) > len(suffix)]

_SINGLE_WORD_NON_BRAND = (COMMON_SPEECH_WORDS | SEGMENT_STRUCTURE_WORDS
                          | CONTRACTION_STEM_WORDS)

# Audio-analysis labels a weak verification model echoes as the "sponsor"
# ("volume_decrease", "splice evidence: digital silence"). A name made only
# of these words is a signal name, not an advertiser.
AUDIO_SIGNAL_WORDS = frozenset({
    'silence', 'volume', 'loudness', 'splice', 'transition', 'anomaly',
    'vad', 'dai', 'evidence', 'step', 'decrease', 'increase', 'gap',
    'digital', 'deep', 'pair', 'cue', 'signal',
})

# Role, credit, and structural labels the model emits when the span has no
# advertiser to name ("Produced", "Post-signoff", "Non-English"). A name made
# only of these describes the segment, not a brand. Ad-shape words are listed
# here rather than read from NON_BRAND_WORDS, which also holds ordinary words
# real brands are built from ("The Gap", "Content Network", "Full Spot").
STRUCTURAL_LABEL_WORDS = frozenset({
    'produced', 'producer', 'producers', 'production', 'presented',
    'presents', 'hosted', 'edited', 'written', 'narrated',
    'material', 'final', 'signoff', 'sign', 'off', 'intro', 'outro',
    'recap', 'credits', 'bumper', 'teaser', 'preview',
    'non', 'english', 'language', 'self', 'cross',
    'mid', 'pre', 'post', 'roll', 'promo',
})

# Podcast hosting, CDN, and DAI platforms. A domain label naming one of these
# names the delivery platform, never the advertiser. pattern_service's
# DAI_PLATFORMS holds the feed-signature domains and is not interchangeable: a
# platform that also buys ads (Spotify) belongs there and not here.
PODCAST_HOSTING_NAMES = frozenset({
    'acast', 'megaphone', 'art19', 'omny', 'omnycontent', 'simplecast',
    'spreaker', 'podbean', 'anchor', 'libsyn', 'buzzsprout', 'captivate',
    'transistor', 'redcircle', 'blubrry', 'fireside', 'pinecast',
    'tritondigital', 'podtrac', 'chartable', 'podsights', 'podscribe',
    'audioboom', 'backtracks', 'soundcloud',
})

# Listening apps, storefronts, and social networks an episode description
# links to alongside its sponsors. Harvest-only: several of these do buy ads,
# so they are not junk sponsor names the way a hosting platform is.
NON_SPONSOR_LINK_DOMAINS = PODCAST_HOSTING_NAMES | frozenset({
    'apple', 'itunes', 'spotify', 'google', 'youtube', 'amazon', 'pandora',
    'iheart', 'stitcher', 'overcast', 'pocketcasts', 'castbox', 'deezer',
    'tunein', 'twitter', 'instagram', 'facebook', 'tiktok', 'threads',
    'mastodon', 'bsky', 'bluesky', 'reddit', 'linkedin', 'discord', 'twitch',
    'patreon', 'paypal', 'substack', 'github', 'linktr',
})

# Generic web words a URL or "dot com" harvest picks up as though they were
# brands ("info" out of "example info dot com").
GENERIC_WEB_WORDS = frozenset({
    'info', 'www', 'web', 'website', 'site', 'online', 'home', 'index',
    'page', 'pages', 'link', 'links', 'email', 'mail', 'blog', 'help',
    'support', 'about', 'contact', 'privacy', 'terms', 'login', 'signin',
    'signup', 'subscribe', 'download', 'store', 'shop', 'news', 'search',
})

# Shortest a domain-derived brand token may be; below this it matches ordinary
# speech. KNOWN_SHORT_BRANDS is the exemption.
MIN_BRAND_TOKEN_CHARS = 4

# Shortest registry name or alias compiled into a brand matcher. Below it a
# name matches ordinary speech wherever a span is scanned for advertisers.
MIN_BRAND_MATCH_CHARS = 3

# Articles and conjunctions that are no evidence either way; the words around
# them decide.
_LABEL_STOP_WORDS = frozenset({'the', 'a', 'an', 'of', 'and'})

_SIGNAL_LABEL_WORDS = AUDIO_SIGNAL_WORDS | STRUCTURAL_LABEL_WORDS


# Lead-in and trailing words a platform label carries ("Hosted on Acast",
# "Acast ads", "Acast.com", "Anchor FM"); the platform is what is left.
_HOSTING_LEAD_IN_RE = re.compile(r'^hosted\s+(?:on|at|by)\s+(\S.*)$', re.I)
_HOSTING_TRAILING_WORDS = frozenset({'fm', 'ads', 'com', 'net', 'io'})


def is_hosting_platform_name(name) -> bool:
    """A podcast hosting, CDN, or DAI platform: it delivers the ad rather than
    buying it. Screened when a new label is minted, never when a stored
    registry row is compiled, where dropping it stops it matching at all."""
    if not name:
        return False
    key = ' '.join(str(name).split()).lower()
    lead_in = _HOSTING_LEAD_IN_RE.match(key)
    if lead_in:
        key = lead_in.group(1).strip()
    words = key.replace('.', ' ').split()
    while len(words) > 1 and words[-1] in _HOSTING_TRAILING_WORDS:
        words.pop()
    return ' '.join(words) in PODCAST_HOSTING_NAMES


def is_non_brand_name(name: str) -> bool:
    """A sanitized name that is never a real advertiser: a known junk value, a
    single common/structure/signal word or contraction of one, or a name made
    only of audio-signal and structural labels."""
    if not name:
        return True
    key = ' '.join(str(name).split()).lower().replace('\u2019', "'")
    if key in INVALID_SPONSOR_VALUES:
        return True
    words = [word for word
             in key.replace('_', ' ').replace(':', ' ').replace('-', ' ').split()
             if word not in _LABEL_STOP_WORDS]
    # Two signal words describe the segment; one behind an article reads as a
    # name ("The Gap"), which the single-word check below rules on instead.
    if len(words) > 1 and all(word in _SIGNAL_LABEL_WORDS for word in words):
        return True
    if ' ' in key:
        return False
    if key in _SINGLE_WORD_NON_BRAND or key in _SIGNAL_LABEL_WORDS:
        return True
    stems = strip_apostrophe_suffixes(key, _CONTRACTION_SUFFIXES)
    return any(stem in _SINGLE_WORD_NON_BRAND for stem in stems)


def is_brand_token(token) -> bool:
    """Whether a domain- or URL-derived token can stand as a brand name.
    Harvested tokens reach boundary extension and description confirmation as
    advertisers, where a generic web word matches ordinary speech and moves a cut."""
    key = str(token or '').strip().lower()
    if not key or key in GENERIC_WEB_WORDS or key in NON_BRAND_WORDS:
        return False
    if len(key) < MIN_BRAND_TOKEN_CHARS and key not in KNOWN_SHORT_BRANDS:
        return False
    return not (is_non_brand_name(key) or is_hosting_platform_name(key))

# Vocabulary the model reaches for when describing an ad's shape or evidence,
# plus the pronouns it quotes ("We'll be right back"). Read only by the
# sponsor labeler. Kept out of NON_BRAND_WORDS because that set also filters
# boundary-relocation keywords, where losing "back" or "block" costs hits.
REASON_DESCRIPTION_WORDS = frozenset({
    'orphaned', 'contiguous', 'dai', 'url', 'back', 'block', 'lead',
    'fragment', 'leftover', 'confirmed', 'merged', 'missed', 'spots',
    'we', 'll', 'i', 'you', 'they', 'he', 'she', 'it', 'to',
})

NEGATION_WORDS = frozenset({
    'not', 'no', 'non', 'never', 'isnt', 'arent', 'wasnt', 'without',
})

# Words that only appear in a reason when the model is describing advertising.
AD_LANGUAGE_WORDS = frozenset({
    'ad', 'ads', 'advert', 'adverts', 'advertisement', 'advertisements',
    'advertiser', 'advertisers', 'advertising', 'sponsor', 'sponsors',
    'sponsored', 'sponsorship', 'commercial', 'commercials', 'promo',
    'promos', 'promotion', 'promotional', 'preroll', 'midroll', 'postroll',
    'dai', 'endorsement', 'infomercial', 'spot', 'spots',
})


# Audio telemetry the detection prompt shows the model; a reason quoting it describes the audio, not an ad.
AUDIO_ECHO_RE = re.compile(
    r'\bsplice_\d+(?:\.\d+)?\b|\bdai transition pair\b|\bvolume (?:anomaly|increase|decrease)\b'
    r'|\b\d+(?:\.\d+)?\s*db\b|\bsplice evidence\b|\bloudness step\b|\bspectral step\b'
    r'|\bdigital silence\b|\bdai transitions?\b|\bdynamic ad insertion\b|\bpotential ad content\b',
    re.IGNORECASE)
# Words an echo couples to the audio: "ad boundary", "ad transition", and "DAI" beside an echo token.
_ECHO_MARK = '\x00'
_ECHO_COUPLED_RE = re.compile(
    r'\bad\s+(?:break\s+)?(?:boundar(?:y|ies)|transitions?)\b'
    rf'|\bdai\W*(?={_ECHO_MARK})|(?<={_ECHO_MARK})\W*dai\b', re.IGNORECASE)


def mentions_advertising(text) -> bool:
    """True if `text` calls the span an ad, the positive evidence the detection
    gate needs. Separate from the sponsor labeler, which answers what the
    advertiser is called and names the first capitalized word of any sentence.
    """
    if not text:
        return False
    text = str(text)
    if AUDIO_ECHO_RE.search(text):
        # The echo describes the splice; a standalone "ad" or "ads" beside it still counts.
        text = _ECHO_COUPLED_RE.sub(' ', AUDIO_ECHO_RE.sub(_ECHO_MARK, text))
    words = re.findall(r'[a-z]+', text.lower())
    # A negated mention is the model saying the span is not an ad, so it is not
    # evidence that it is. Two tokens back covers "not a sponsor read".
    return any(w in AD_LANGUAGE_WORDS
               and NEGATION_WORDS.isdisjoint(words[max(0, i - 2):i])
               for i, w in enumerate(words))


# TLDs recognized in spoken "X dot com" transcript prose.
DOMAIN_TLDS = frozenset({'com', 'org', 'net', 'io', 'co'})

# TLDs a sponsor URL in an ad reason is written with. Wider than the spoken
# set: a written URL carries TLDs a host would not say aloud.
SPONSOR_DOMAIN_TLDS = DOMAIN_TLDS | frozenset({
    'tv', 'fm', 'us', 'app', 'shop', 'store', 'ai', 'edu',
})

# Classifications from LLM that indicate non-ad content
NOT_AD_CLASSIFICATIONS = frozenset({
    'content', 'not_ad', 'editorial', 'organic',
    'show_content', 'regular_content', 'interview',
    'conversation', 'segment', 'topic'
})

# SSRF protection: allowed URL schemes for outbound requests
ALLOWED_URL_SCHEMES = frozenset({'http', 'https'})

# SSRF protection: allowed ports for outbound requests (empty = allow all)
ALLOWED_URL_PORTS = frozenset({80, 443, 8080, 8443})


# Seed data for known sponsors. Consumed by SponsorService at startup and by
# the offline LLM benchmark for a deterministic prompt. Each entry feeds the
# `sponsors` table on first run and the prompt's static sponsor list.
SEED_SPONSORS = [
    {"name": "Athletic Greens", "aliases": ["AG1", "AG One"], "category": "health"},
    {"name": "BetterHelp", "aliases": ["Better Help"], "category": "health"},
    {"name": "Squarespace", "aliases": ["Square Space"], "category": "tech"},
    {"name": "Shopify", "aliases": [], "category": "tech"},
    {"name": "HelloFresh", "aliases": ["Hello Fresh"], "category": "food"},
    {"name": "NordVPN", "aliases": ["Nord VPN"], "category": "vpn"},
    {"name": "ExpressVPN", "aliases": ["Express VPN"], "category": "vpn"},
    {"name": "ZipRecruiter", "aliases": ["Zip Recruiter"], "category": "jobs"},
    {"name": "SimpliSafe", "aliases": ["Simpli Safe"], "category": "home"},
    {"name": "Mint Mobile", "aliases": ["MintMobile"], "category": "telecom"},
    {"name": "MasterClass", "aliases": ["Master Class"], "category": "education"},
    {"name": "Rocket Money", "aliases": ["RocketMoney", "Truebill"], "category": "finance"},
    {"name": "DoorDash", "aliases": ["Door Dash"], "category": "food"},
    {"name": "HubSpot", "aliases": ["Hub Spot"], "category": "tech"},
    {"name": "NetSuite", "aliases": ["Net Suite"], "category": "tech"},
    {"name": "Amazon", "aliases": [], "category": "retail"},
    {"name": "Audible", "aliases": [], "category": "entertainment"},
    {"name": "Factor", "aliases": [], "category": "food"},
    {"name": "Calm", "aliases": [], "category": "health"},
    {"name": "Headspace", "aliases": ["Head Space"], "category": "health"},
    {"name": "Indeed", "aliases": [], "category": "jobs"},
    {"name": "LinkedIn", "aliases": ["LinkedIn Jobs"], "category": "jobs"},
    {"name": "Stamps.com", "aliases": ["Stamps"], "category": "business"},
    {"name": "Ring", "aliases": [], "category": "home"},
    {"name": "ADT", "aliases": [], "category": "home"},
    {"name": "Casper", "aliases": [], "category": "home"},
    {"name": "Helix Sleep", "aliases": ["Helix"], "category": "home"},
    {"name": "Purple", "aliases": [], "category": "home"},
    {"name": "Brooklinen", "aliases": [], "category": "home"},
    {"name": "Bombas", "aliases": [], "category": "apparel"},
    {"name": "Manscaped", "aliases": [], "category": "personal"},
    {"name": "Dollar Shave Club", "aliases": ["DSC"], "category": "personal"},
    {"name": "Harry's", "aliases": ["Harrys"], "category": "personal"},
    {"name": "Quip", "aliases": [], "category": "personal"},
    {"name": "Hims", "aliases": [], "category": "health"},
    {"name": "Hers", "aliases": [], "category": "health"},
    {"name": "Roman", "aliases": [], "category": "health"},
    {"name": "Function of Beauty", "aliases": [], "category": "personal"},
    {"name": "Native", "aliases": [], "category": "personal"},
    {"name": "Liquid IV", "aliases": ["Liquid I.V."], "category": "health"},
    {"name": "Athletic Brewing", "aliases": [], "category": "beverage"},
    {"name": "Magic Spoon", "aliases": [], "category": "food"},
    {"name": "Thrive Market", "aliases": [], "category": "food"},
    {"name": "Butcher Box", "aliases": ["ButcherBox"], "category": "food"},
    {"name": "Blue Apron", "aliases": [], "category": "food"},
    {"name": "Uber Eats", "aliases": ["UberEats"], "category": "food"},
    {"name": "Grubhub", "aliases": ["Grub Hub"], "category": "food"},
    {"name": "Instacart", "aliases": [], "category": "food"},
    {"name": "Credit Karma", "aliases": [], "category": "finance"},
    {"name": "SoFi", "aliases": [], "category": "finance"},
    {"name": "Acorns", "aliases": [], "category": "finance"},
    {"name": "Betterment", "aliases": [], "category": "finance"},
    {"name": "Wealthfront", "aliases": [], "category": "finance"},
    {"name": "PolicyGenius", "aliases": ["Policy Genius"], "category": "finance"},
    {"name": "Lemonade", "aliases": [], "category": "finance"},
    {"name": "State Farm", "aliases": [], "category": "finance"},
    {"name": "Progressive", "aliases": [], "category": "finance"},
    {"name": "Geico", "aliases": [], "category": "finance"},
    {"name": "Liberty Mutual", "aliases": [], "category": "finance"},
    {"name": "T-Mobile", "aliases": ["TMobile"], "category": "telecom"},
    {"name": "Visible", "aliases": [], "category": "telecom"},
    {"name": "FanDuel", "aliases": ["Fan Duel"], "category": "gambling"},
    {"name": "DraftKings", "aliases": ["Draft Kings"], "category": "gambling"},
    {"name": "BetMGM", "aliases": ["Bet MGM"], "category": "gambling"},
    {"name": "Toyota", "aliases": [], "category": "auto"},
    {"name": "Hyundai", "aliases": [], "category": "auto"},
    {"name": "CarMax", "aliases": ["Car Max"], "category": "auto"},
    {"name": "Carvana", "aliases": [], "category": "auto"},
    {"name": "eBay Motors", "aliases": [], "category": "auto"},
    {"name": "ZocDoc", "aliases": ["Zoc Doc"], "category": "health"},
    {"name": "GoodRx", "aliases": ["Good Rx"], "category": "health"},
    {"name": "Care/of", "aliases": ["Care of", "Careof"], "category": "health"},
    {"name": "Ritual", "aliases": [], "category": "health"},
    {"name": "Seed", "aliases": [], "category": "health"},
    {"name": "Monday.com", "aliases": ["Monday"], "category": "tech"},
    {"name": "Notion", "aliases": [], "category": "tech"},
    {"name": "Canva", "aliases": [], "category": "tech"},
    {"name": "Grammarly", "aliases": [], "category": "tech"},
    {"name": "Babbel", "aliases": [], "category": "education"},
    {"name": "Rosetta Stone", "aliases": [], "category": "education"},
    {"name": "Blinkist", "aliases": [], "category": "education"},
    {"name": "Raycon", "aliases": [], "category": "electronics"},
    {"name": "Bose", "aliases": [], "category": "electronics"},
    {"name": "MacPaw", "aliases": ["CleanMyMac"], "category": "tech"},
    {"name": "Green Chef", "aliases": ["GreenChef"], "category": "food"},
    {"name": "Magic Mind", "aliases": [], "category": "beverage"},
    {"name": "Honeylove", "aliases": ["Honey Love"], "category": "apparel"},
    {"name": "Cozy Earth", "aliases": [], "category": "home"},
    {"name": "Quince", "aliases": [], "category": "apparel"},
    {"name": "LMNT", "aliases": ["Element"], "category": "health"},
    {"name": "Nutrafol", "aliases": [], "category": "health"},
    {"name": "Aura", "aliases": [], "category": "tech"},
    {"name": "OneSkin", "aliases": ["One Skin"], "category": "personal"},
    {"name": "Incogni", "aliases": [], "category": "tech"},
    {"name": "Gametime", "aliases": ["Game Time"], "category": "entertainment"},
    {"name": "1Password", "aliases": ["One Password"], "category": "tech"},
    {"name": "Bitwarden", "aliases": ["Bit Warden"], "category": "tech"},
    {"name": "CacheFly", "aliases": [], "category": "tech"},
    {"name": "Deel", "aliases": [], "category": "business"},
    {"name": "DeleteMe", "aliases": ["Delete Me"], "category": "tech"},
    {"name": "Framer", "aliases": [], "category": "tech"},
    {"name": "Miro", "aliases": [], "category": "tech"},
    {"name": "Monarch Money", "aliases": [], "category": "finance"},
    {"name": "OutSystems", "aliases": [], "category": "tech"},
    {"name": "Spaceship", "aliases": [], "category": "tech"},
    {"name": "Thinkst Canary", "aliases": [], "category": "tech"},
    {"name": "ThreatLocker", "aliases": [], "category": "tech"},
    {"name": "Vanta", "aliases": [], "category": "tech"},
    {"name": "Veeam", "aliases": [], "category": "tech"},
    {"name": "Zapier", "aliases": [], "category": "tech"},
    {"name": "Zscaler", "aliases": [], "category": "tech"},
    {"name": "Capital One", "aliases": [], "category": "finance"},
    {"name": "Ford", "aliases": [], "category": "auto"},
    {"name": "WhatsApp", "aliases": [], "category": "tech"},

    # 2.0.13 expansion: pb.json brands not previously in SEED (139 entries from Magellan AI / Podchaser / SponsorUnited)
    # automotive_transport
    {"name": "Lime", "aliases": [], "category": "automotive_transport"},
    {"name": "Lyft", "aliases": [], "category": "automotive_transport"},
    {"name": "Turo", "aliases": [], "category": "automotive_transport"},
    {"name": "Uber", "aliases": [], "category": "automotive_transport"},
    {"name": "Waymo", "aliases": [], "category": "automotive_transport"},

    # b2b_startup
    {"name": "Gusto", "aliases": [], "category": "b2b_startup"},
    {"name": "Meter", "aliases": [], "category": "b2b_startup"},
    {"name": "PagerDuty", "aliases": [], "category": "b2b_startup"},
    {"name": "Rippling", "aliases": [], "category": "b2b_startup"},
    {"name": "Splunk", "aliases": [], "category": "b2b_startup"},
    {"name": "Webflow", "aliases": [], "category": "b2b_startup"},

    # ecommerce_retail_dtc
    {"name": "Allbirds", "aliases": [], "category": "ecommerce_retail_dtc"},
    {"name": "Alo Yoga", "aliases": [], "category": "ecommerce_retail_dtc"},
    {"name": "Birchbox", "aliases": [], "category": "ecommerce_retail_dtc"},
    {"name": "Everlane", "aliases": [], "category": "ecommerce_retail_dtc"},
    {"name": "FabFitFun", "aliases": [], "category": "ecommerce_retail_dtc"},
    {"name": "GOAT", "aliases": [], "category": "ecommerce_retail_dtc"},
    {"name": "Gopuff", "aliases": [], "category": "ecommerce_retail_dtc"},
    {"name": "Lululemon", "aliases": [], "category": "ecommerce_retail_dtc"},
    {"name": "Outdoor Voices", "aliases": [], "category": "ecommerce_retail_dtc"},
    {"name": "Poshmark", "aliases": [], "category": "ecommerce_retail_dtc"},
    {"name": "Rothy's", "aliases": [], "category": "ecommerce_retail_dtc"},
    {"name": "Saatva", "aliases": [], "category": "ecommerce_retail_dtc"},
    {"name": "Shein", "aliases": [], "category": "ecommerce_retail_dtc"},
    {"name": "SKIMS", "aliases": [], "category": "ecommerce_retail_dtc"},
    {"name": "Stitch Fix", "aliases": [], "category": "ecommerce_retail_dtc"},
    {"name": "StockX", "aliases": [], "category": "ecommerce_retail_dtc"},
    {"name": "Temu", "aliases": [], "category": "ecommerce_retail_dtc"},
    {"name": "Ten Thousand", "aliases": [], "category": "ecommerce_retail_dtc"},
    {"name": "ThredUp", "aliases": [], "category": "ecommerce_retail_dtc"},
    {"name": "Vuori", "aliases": [], "category": "ecommerce_retail_dtc"},
    {"name": "Warby Parker", "aliases": [], "category": "ecommerce_retail_dtc"},
    {"name": "Wayfair", "aliases": [], "category": "ecommerce_retail_dtc"},

    # finance_fintech
    {"name": "Affirm", "aliases": [], "category": "finance_fintech"},
    {"name": "Bill.com", "aliases": [], "category": "finance_fintech"},
    {"name": "Brex", "aliases": [], "category": "finance_fintech"},
    {"name": "Chime", "aliases": [], "category": "finance_fintech"},
    {"name": "Coinbase", "aliases": [], "category": "finance_fintech"},
    {"name": "FreshBooks", "aliases": [], "category": "finance_fintech"},
    {"name": "Intuit", "aliases": [], "category": "finance_fintech"},
    {"name": "Klarna", "aliases": [], "category": "finance_fintech"},
    {"name": "Mercury", "aliases": [], "category": "finance_fintech"},
    {"name": "NerdWallet", "aliases": [], "category": "finance_fintech"},
    {"name": "Plaid", "aliases": [], "category": "finance_fintech"},
    {"name": "Public.com", "aliases": [], "category": "finance_fintech"},
    {"name": "QuickBooks", "aliases": [], "category": "finance_fintech"},
    {"name": "Ramp", "aliases": [], "category": "finance_fintech"},
    {"name": "Robinhood", "aliases": [], "category": "finance_fintech"},
    {"name": "Stripe", "aliases": [], "category": "finance_fintech"},
    {"name": "UnitedHealth Group", "aliases": [], "category": "finance_fintech"},
    {"name": "WebBank", "aliases": [], "category": "finance_fintech"},
    {"name": "Xero", "aliases": [], "category": "finance_fintech"},

    # food_beverage_nutrition
    {"name": "Alani Nu", "aliases": [], "category": "food_beverage_nutrition"},
    {"name": "Bloom Nutrition", "aliases": [], "category": "food_beverage_nutrition"},
    {"name": "EveryPlate", "aliases": [], "category": "food_beverage_nutrition"},
    {"name": "Huel", "aliases": [], "category": "food_beverage_nutrition"},
    {"name": "Imperfect Foods", "aliases": [], "category": "food_beverage_nutrition"},
    {"name": "McDonald's", "aliases": [], "category": "food_beverage_nutrition"},
    {"name": "OLIPOP", "aliases": [], "category": "food_beverage_nutrition"},
    {"name": "Poppi", "aliases": [], "category": "food_beverage_nutrition"},
    {"name": "Starbucks", "aliases": [], "category": "food_beverage_nutrition"},
    {"name": "Transparent Labs", "aliases": [], "category": "food_beverage_nutrition"},

    # gaming_sports_betting
    {"name": "Caesars Sportsbook", "aliases": [], "category": "gaming_sports_betting"},
    {"name": "ESPN Bet", "aliases": [], "category": "gaming_sports_betting"},
    {"name": "SeatGeek", "aliases": [], "category": "gaming_sports_betting"},
    {"name": "StubHub", "aliases": [], "category": "gaming_sports_betting"},

    # home_security
    {"name": "Pura", "aliases": [], "category": "home_security"},

    # insurance_legal
    {"name": "LegalZoom", "aliases": [], "category": "insurance_legal"},
    {"name": "Rocket Lawyer", "aliases": [], "category": "insurance_legal"},

    # media_streaming
    {"name": "Apple TV+", "aliases": [], "category": "media_streaming"},
    {"name": "Disney+", "aliases": [], "category": "media_streaming"},
    {"name": "HBO Max", "aliases": [], "category": "media_streaming"},
    {"name": "iHeartRadio", "aliases": [], "category": "media_streaming"},
    {"name": "Netflix", "aliases": [], "category": "media_streaming"},
    {"name": "Paramount+", "aliases": [], "category": "media_streaming"},
    {"name": "SiriusXM", "aliases": [], "category": "media_streaming"},
    {"name": "Spotify", "aliases": [], "category": "media_streaming"},
    {"name": "YouTube", "aliases": [], "category": "media_streaming"},
    {"name": "YouTube TV", "aliases": [], "category": "media_streaming"},

    # mental_health_wellness
    {"name": "Cerebral", "aliases": [], "category": "mental_health_wellness"},
    {"name": "Eight Sleep", "aliases": [], "category": "mental_health_wellness"},
    {"name": "Function Health", "aliases": [], "category": "mental_health_wellness"},
    {"name": "Inside Tracker", "aliases": [], "category": "mental_health_wellness"},
    {"name": "Joovv", "aliases": [], "category": "mental_health_wellness"},
    {"name": "Levels", "aliases": [], "category": "mental_health_wellness"},
    {"name": "Momentous", "aliases": [], "category": "mental_health_wellness"},
    {"name": "Noom", "aliases": [], "category": "mental_health_wellness"},
    {"name": "Ro", "aliases": [], "category": "mental_health_wellness"},
    {"name": "Talkspace", "aliases": [], "category": "mental_health_wellness"},
    {"name": "Thorne", "aliases": [], "category": "mental_health_wellness"},
    {"name": "WHOOP", "aliases": [], "category": "mental_health_wellness"},

    # tech_software_saas
    {"name": "Airtable", "aliases": [], "category": "tech_software_saas"},
    {"name": "Anthropic", "aliases": [], "category": "tech_software_saas"},
    {"name": "Asana", "aliases": [], "category": "tech_software_saas"},
    {"name": "Brilliant", "aliases": [], "category": "tech_software_saas"},
    {"name": "ClickUp", "aliases": [], "category": "tech_software_saas"},
    {"name": "Cloudflare", "aliases": [], "category": "tech_software_saas"},
    {"name": "CrowdStrike", "aliases": [], "category": "tech_software_saas"},
    {"name": "Cursor", "aliases": [], "category": "tech_software_saas"},
    {"name": "Databricks", "aliases": [], "category": "tech_software_saas"},
    {"name": "Datadog", "aliases": [], "category": "tech_software_saas"},
    {"name": "DocuSign", "aliases": [], "category": "tech_software_saas"},
    {"name": "Duolingo", "aliases": [], "category": "tech_software_saas"},
    {"name": "ElevenLabs", "aliases": [], "category": "tech_software_saas"},
    {"name": "Figma", "aliases": [], "category": "tech_software_saas"},
    {"name": "GitHub", "aliases": [], "category": "tech_software_saas"},
    {"name": "GitHub Copilot", "aliases": [], "category": "tech_software_saas"},
    {"name": "Klaviyo", "aliases": [], "category": "tech_software_saas"},
    {"name": "Linear", "aliases": [], "category": "tech_software_saas"},
    {"name": "Loom", "aliases": [], "category": "tech_software_saas"},
    {"name": "Mailchimp", "aliases": [], "category": "tech_software_saas"},
    {"name": "Midjourney", "aliases": [], "category": "tech_software_saas"},
    {"name": "Okta", "aliases": [], "category": "tech_software_saas"},
    {"name": "OpenAI", "aliases": [], "category": "tech_software_saas"},
    {"name": "Patreon", "aliases": [], "category": "tech_software_saas"},
    {"name": "Perplexity", "aliases": [], "category": "tech_software_saas"},
    {"name": "Retool", "aliases": [], "category": "tech_software_saas"},
    {"name": "Salesforce", "aliases": [], "category": "tech_software_saas"},
    {"name": "SendGrid", "aliases": [], "category": "tech_software_saas"},
    {"name": "ServiceNow", "aliases": [], "category": "tech_software_saas"},
    {"name": "Skillshare", "aliases": [], "category": "tech_software_saas"},
    {"name": "Slack", "aliases": [], "category": "tech_software_saas"},
    {"name": "Snowflake", "aliases": [], "category": "tech_software_saas"},
    {"name": "Substack", "aliases": [], "category": "tech_software_saas"},
    {"name": "Twilio", "aliases": [], "category": "tech_software_saas"},
    {"name": "Vercel", "aliases": [], "category": "tech_software_saas"},
    {"name": "Workday", "aliases": [], "category": "tech_software_saas"},
    {"name": "Zendesk", "aliases": [], "category": "tech_software_saas"},
    {"name": "Zoom", "aliases": [], "category": "tech_software_saas"},

    # telecom
    {"name": "AT&T", "aliases": [], "category": "telecom"},
    {"name": "Comcast", "aliases": [], "category": "telecom"},
    {"name": "Verizon", "aliases": [], "category": "telecom"},

    # travel_hospitality
    {"name": "Airbnb", "aliases": [], "category": "travel_hospitality"},
    {"name": "Booking.com", "aliases": [], "category": "travel_hospitality"},
    {"name": "Expedia", "aliases": [], "category": "travel_hospitality"},
    {"name": "Hopper", "aliases": [], "category": "travel_hospitality"},
    {"name": "Kayak", "aliases": [], "category": "travel_hospitality"},
    {"name": "Skyscanner", "aliases": [], "category": "travel_hospitality"},
    {"name": "Vrbo", "aliases": [], "category": "travel_hospitality"},
    {"name": "Zyn", "aliases": ["ZYN", "Zinn"], "category": "tobacco_nicotine"},
]

# Seed data for normalizations (Whisper transcription fixes)
SEED_NORMALIZATIONS = [
    # Sponsor name fixes
    {"pattern": r"\bag\s*one\b", "replacement": "ag1", "category": "sponsor"},
    {"pattern": r"\bag\s*1\b", "replacement": "ag1", "category": "sponsor"},
    {"pattern": r"\bbetter\s*help\b", "replacement": "betterhelp", "category": "sponsor"},
    {"pattern": r"\bsquare\s*space\b", "replacement": "squarespace", "category": "sponsor"},
    {"pattern": r"\bzip\s*recruiter\b", "replacement": "ziprecruiter", "category": "sponsor"},
    {"pattern": r"\bsimpli\s*safe\b", "replacement": "simplisafe", "category": "sponsor"},
    {"pattern": r"\bmint\s*mobile\b", "replacement": "mintmobile", "category": "sponsor"},
    {"pattern": r"\bmaster\s*class\b", "replacement": "masterclass", "category": "sponsor"},
    {"pattern": r"\brocket\s*money\b", "replacement": "rocketmoney", "category": "sponsor"},
    {"pattern": r"\bdoor\s*dash\b", "replacement": "doordash", "category": "sponsor"},
    {"pattern": r"\bhub\s*spot\b", "replacement": "hubspot", "category": "sponsor"},
    {"pattern": r"\bnet\s*suite\b", "replacement": "netsuite", "category": "sponsor"},
    {"pattern": r"\bhello\s*fresh\b", "replacement": "hellofresh", "category": "sponsor"},
    {"pattern": r"\bnord\s*vpn\b", "replacement": "nordvpn", "category": "sponsor"},
    {"pattern": r"\bexpress\s*vpn\b", "replacement": "expressvpn", "category": "sponsor"},
    {"pattern": r"\bhead\s*space\b", "replacement": "headspace", "category": "sponsor"},
    {"pattern": r"\bpolicy\s*genius\b", "replacement": "policygenius", "category": "sponsor"},
    {"pattern": r"\bfan\s*duel\b", "replacement": "fanduel", "category": "sponsor"},
    {"pattern": r"\bdraft\s*kings\b", "replacement": "draftkings", "category": "sponsor"},
    {"pattern": r"\bbet\s*mgm\b", "replacement": "betmgm", "category": "sponsor"},
    {"pattern": r"\bcar\s*max\b", "replacement": "carmax", "category": "sponsor"},
    {"pattern": r"\bzoc\s*doc\b", "replacement": "zocdoc", "category": "sponsor"},
    {"pattern": r"\bgood\s*rx\b", "replacement": "goodrx", "category": "sponsor"},
    {"pattern": r"\bgreen\s*chef\b", "replacement": "greenchef", "category": "sponsor"},
    {"pattern": r"\bhoney\s*love\b", "replacement": "honeylove", "category": "sponsor"},
    {"pattern": r"\bone\s*skin\b", "replacement": "oneskin", "category": "sponsor"},
    {"pattern": r"\bgame\s*time\b", "replacement": "gametime", "category": "sponsor"},
    {"pattern": r"\bone\s*password\b", "replacement": "1password", "category": "sponsor"},
    {"pattern": r"\bbit\s*warden\b", "replacement": "bitwarden", "category": "sponsor"},
    {"pattern": r"\bdelete\s*me\b", "replacement": "deleteme", "category": "sponsor"},
    {"pattern": r"\bmonarch\s*money\b", "replacement": "monarchmoney", "category": "sponsor"},
    {"pattern": r"\bliquid\s*i\.?v\.?\b", "replacement": "liquidiv", "category": "sponsor"},
    {"pattern": r"\bbutcher\s*box\b", "replacement": "butcherbox", "category": "sponsor"},
    {"pattern": r"\bgrub\s*hub\b", "replacement": "grubhub", "category": "sponsor"},
    {"pattern": r"\buber\s*eats\b", "replacement": "ubereats", "category": "sponsor"},

    # URL patterns
    {"pattern": r"\bdot\s+com\b", "replacement": ".com", "category": "url"},
    {"pattern": r"\bdot\s+co\b", "replacement": ".co", "category": "url"},
    {"pattern": r"\bdot\s+org\b", "replacement": ".org", "category": "url"},
    {"pattern": r"\bdot\s+io\b", "replacement": ".io", "category": "url"},
    {"pattern": r"\bforward\s+slash\b", "replacement": "/", "category": "url"},
    {"pattern": r"(?<!\w)slash(?!\w)", "replacement": "/", "category": "url"},

    # Number words to digits (for promo codes)
    {"pattern": r"\bpercent\s+off\b", "replacement": "% off", "category": "number"},
    {"pattern": r"\bfifty\s+percent\b", "replacement": "50%", "category": "number"},
    {"pattern": r"\btwenty\s+percent\b", "replacement": "20%", "category": "number"},
    {"pattern": r"\bfifteen\s+percent\b", "replacement": "15%", "category": "number"},
    {"pattern": r"\bten\s+percent\b", "replacement": "10%", "category": "number"},

    # Common phrase fixes
    {"pattern": r"\bpromo\s+code\b", "replacement": "promo code", "category": "phrase"},
    {"pattern": r"\bdiscount\s+code\b", "replacement": "discount code", "category": "phrase"},
    {"pattern": r"\bspecial\s+offer\b", "replacement": "special offer", "category": "phrase"},
    {"pattern": r"\bfree\s+shipping\b", "replacement": "free shipping", "category": "phrase"},
    {"pattern": r"\bfree\s+trial\b", "replacement": "free trial", "category": "phrase"},
    {"pattern": r"\bmoney\s+back\s+guarantee\b", "replacement": "money back guarantee", "category": "phrase"},

    # Transcript display corrections. Mixed-case replacement opts in to the
    # transcript-correction code path; see SponsorService.apply_transcript_corrections.
    {"pattern": r"\bWeGoV\b", "replacement": "Wegovy", "category": "phrase"},
    {"pattern": r"\bwe\s+go\s+v\b", "replacement": "Wegovy", "category": "phrase"},
]


# Default ad-detection system prompt. Lives here (a stdlib-only module) so the
# offline benchmark in benchmarks/llm/ can import it without pulling in the
# database package's transitive secrets_crypto -> cryptography chain.
DEFAULT_SYSTEM_PROMPT = """
You are a JSON API for segmenting podcast transcripts.

Your job is to identify every segment in a given transcript according to the rules defined below. The transcript may contain incorrect words (especially company and brand names), misheard phrases, and incomplete sentences. Use the podcast metadata and surrounding context to infer the correct meaning.

The transcript is presented in one of two formats:
- Timestamped: Each cue contains its start and end time offset, in seconds, e.g. `[12.3s - 14.5s] Text`.
- Indexed: Each cue contains its zero-based index, e.g. `[123] Text`.

SEGMENTATION RULES:
- Build a continuous chain of topical segments from the first cue to the last. No gaps.
- Start a new segment immediately when the topic changes.
- Each segment must be categorized according to the rules defined below.

CATEGORIES:
- `main_content`: The core value proposition of the episode, including cold open, narrative, story telling, commentary, interview, Q&A, etc.
- `intro`: Opening theme, host welcome and introductions. (Note: cold open / story intro is `main_content`.)
- `outro`: Sign-off, credits, closing theme. (Note: a closing conversation or storyteller bio is `main_content`.)
- `interaction`: Calls to action (like, review, comment).
- `teaser`: Previews of upcoming content; hook for the audience to stay tuned.
- `recap`: Summary of prior episodes, e.g. "previously on…" (Note: an end of episode summary is `main_content`.)
- `transition`: Musical or narrative interlude bridging segments. Prefer one of the other content categories when possible.
- `sponsor`: Ads or promotions unrelated to the podcast or its network, including trailing disclaimers.
- `cross_promo`: Promos for sister podcasts or the podcast network (Acast, Spotify, etc).
- `self_promo`: Promos for the podcast itself, including Patreon, merch, tours, etc.

The `sponsor`, `cross_promo`, and `self_promo` categories are collectively referred to as "promotional" categories. The others are non-promotional or "content" categories.

CONSTRAINTS:
- DO NOT output `main_content` segments. The caller will infer them from the gaps between other segments.
- Promotional segments: Create one segment per promotion per instance.
- Cues may occasionally contain content from two distinct segments. For example, an ad may start or end mid cue. When this happens, include the cue in BOTH segments.

FALSE POSITIVE PREVENTION GUIDELINES:
- Organic brand mentions or product discussions stay in `main_content` unless explicit and prolonged promotional language is used.
- Passing mentions of sponsors, URLs, or social handles stay in `main_content` unless sustained and directed at the audience.
- Guest plugs stay in `main_content`.
- When unsure if a segment is `main_content` or not, assume that it is and adjust confidence accordingly.

<!--
COMMON PODCAST SPONSORS (high confidence if mentioned):
BetterHelp, Athletic Greens, AG1, Shopify, Amazon, Audible, Squarespace, HelloFresh, Factor, NordVPN, ExpressVPN, Mint Mobile, MasterClass, Calm, Headspace, ZipRecruiter, Indeed, LinkedIn Jobs, LinkedIn, Stamps.com, SimpliSafe, Ring, ADT, Casper, Helix Sleep, Purple, Brooklinen, Bombas, Manscaped, Dollar Shave Club, Harry's, Quip, Hims, Hers, Roman, Function of Beauty, Native, Liquid IV, Athletic Brewing, Magic Spoon, Thrive Market, Butcher Box, Blue Apron, DoorDash, Uber Eats, Grubhub, Instacart, Rocket Money, Credit Karma, SoFi, Acorns, Betterment, Wealthfront, PolicyGenius, Lemonade, State Farm, Progressive, Geico, Liberty Mutual, T-Mobile, Visible, FanDuel, DraftKings, BetMGM, Toyota, Hyundai, CarMax, Carvana, eBay Motors, ZocDoc, GoodRx, Care/of, Ritual, Seed, HubSpot, NetSuite, Monday.com, Notion, Canva, Grammarly, Babbel, Rosetta Stone, Blinkist, Raycon, Bose, MacPaw, CleanMyMac, Green Chef, Magic Mind, Honeylove, Cozy Earth, Quince, LMNT, Nutrafol, Aura, OneSkin, Incogni, Gametime, 1Password, Bitwarden, CacheFly, Deel, DeleteMe, Framer, Miro, Monarch Money, OutSystems, Spaceship, Thinkst Canary, ThreatLocker, Vanta, Veeam, Zapier, Zscaler, Capital One, Ford, WhatsApp

RETAIL/CONSUMER BRANDS (network-inserted ads):
Nordstrom, Macy's, Target, Walmart, Kohl's, Bloomingdale's, JCPenney, TJ Maxx, Home Depot, Lowe's, Best Buy, Costco, Gap, Old Navy, H&M, Zara, Nike, Adidas, Lululemon, Coach, Kate Spade, Michael Kors, Sephora, Ulta, Bath & Body Works, CVS, Walgreens, AutoZone, O'Reilly Auto Parts, Jiffy Lube, Midas, Gold Belly, Farmer's Dog, Caldera Lab, Monster Energy, Red Bull, Whole Foods, Trader Joe's, Kroger, GNC
-->

{sponsor_database}

OUTPUT FORMAT:
As a JSON API, you must return exactly one JSON object that conforms to the following schema.

Response shape:
{
  "segments": [ segment1, segment2, … ]
}

Segment shape:
{
  "start": number,
  "end": number,
  "category": enum,
  "confidence": number,
  "reason": string or null,
  "sponsor_name": string or null,
  "start_text": string or null
  "end_text": string or null
}

Field rules:
- `segments`: An ordered array of detected segments, excluding `main_content` segments.
- `start`: The index or start time (depending on cue format) of the first cue in the segment.
- `end`: The index or end time of the last cue in the segment.
- `category`: The segment category as defined above.
- `confidence`: A number between 0.0 and 1.0 indicating how confident you are that start, end, and category are accurate.
- `reason`: A *short* explanation for why the segment was categorized as such.
- `sponsor_name`: The named sponsor (advertiser/brand/company) or product in a promotional segment, null otherwise.
- `start_text`: The first 5 words of ad copy in a promotional segment, null otherwise. If the ad copy is missing or unclear, use an empty string.
- `end_text`: Similar to `start_text` except the final 5 words of ad copy.

<!--
EXAMPLE: Partial transcript (with timestamps) and response:
[1.6s - 47.0s] Thanks for joining us today… Let's take a quick break.
[48.5s - 52.0s] Ready to start a healthier routine?
[52.5s - 78.0s] AG1 is the daily foundational nutrition supplement… Go to athleticgreens.com/podcast.
[78.5s - 82.0s] That's athleticgreens.com/podcast.
[82.5s - 86.0s] Now, back to our conversation.
…
[512.0s - 514.5s] Before we get back to it, a quick note.
[514.5s - 528.0s] Hey, it's Jamie from Tech Weekly. If you like this show, check out our other podcast Startup Stories for interviews with founders every Tuesday.
[528.0s - 531.0s] Now, back to today's episode.

RESPONSE: 
{ "segments": [
    {"start": 1.6, "end": 47.0, "confidence": 0.97, "category": "intro", "reason": "Welcome and throw to break", "sponsor_name": null, "start_text": null, "end_text": null},
    {"start": 48.5, "end": 86.0, "confidence": 0.95, "category": "sponsor", "reason": "Athletic Greens sponsor read, out cue", "sponsor_name": "Athletic Greens", "start_text": "Ready to start a healthier routine?", "end_text": "That's athleticgreens.com/podcast"},
    …
    {"start": 512.0, "end": 531.0, "confidence": 0.92, "category": "cross_promo", "reason": "Cross-promotion for sister podcast and in/out cues", "sponsor_name": "Startup Stories", "start_text": "Hey, it's Jamie from Tech Weekly.", "end_text": "interviews with founders every Tuesday."}
  ]
}
-->

REMINDERS:
- Merge consecutive segments with the same category and topic.
- DO NOT include `main_content` segments.
- Output compact JSON only. No Markdown, code fences, or explanatory text. Only JSON.
- Use only those fields described above.
- Omit (or assign null) to fields which are not applicable, such as `sponsor_name`, `start_text`, and `end_text` for non-promotional segments.
- Ensure the output is valid, parseable JSON.
""".strip()

# Opt-in addition to DEFAULT_SYSTEM_PROMPT (issue #565): appended only when
# detect_show_segments is enabled. ad_detector.AdDetector appends it after
# any operator override of system_prompt, so it applies even when customized.
SHOW_SEGMENTS_PROMPT_SECTION = """SHOW SEGMENTS:
This podcast has also asked for its show-structure segments to be identified. In addition to ads, look for these and return them in the same JSON array, each with its own category:
- intro: the show's opening theme music and/or host introduction, before the actual episode content starts
- outro: the show's closing credits, sign-off, or theme music, after the episode content ends
- recap: a produced "coming up" preview, a headline bumper, or a "listen to this next" segment: something that previews or summarizes content rather than being the content itself

"category" is REQUIRED on these objects too, set to exactly "intro", "outro", or "recap". A show segment reported without "category" is invalid, the same as an ad reported without one.

RULES FOR SHOW SEGMENTS:
- A cold open is content, not intro. If the host starts the episode with a quote, a story, or a hook before the theme music, that is content. Do not flag it.
- Only flag a span that is clearly theme music, closing credits, or housekeeping. If you are unsure whether something is a show segment, do not flag it.
- Use the same timestamp discipline as ads: use the exact [Xs] marker timestamps from the transcript, do not interpolate or estimate.

OUTRO EXAMPLE:
[2324.5s - 2360.0s] That's the show for today, thanks for listening, and we'll see you next time.
[2360.0s - 2381.1s] [closing theme music]

Output: [{"start": 2324.5, "end": 2381.1, "confidence": 0.85, "category": "outro", "reason": "Show sign-off and closing theme music", "end_text": "[closing theme music]"}]
"""


# Provenance of a reprocess_requested_at stamp. The stamp itself only says
# "may bypass the auto-process gate", which is not the same as a person asking.
REPROCESS_SOURCE_JIT = 'jit'
REPROCESS_SOURCE_DEGRADED = 'degraded'
REPROCESS_SOURCE_POLICY = 'policy'
# Sources the pipeline wrote for itself; a NULL source means a person.
PIPELINE_REPROCESS_SOURCES = (REPROCESS_SOURCE_JIT, REPROCESS_SOURCE_DEGRADED)
