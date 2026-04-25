"""Shared constants for ad detection and pattern matching.

Centralizes field name sets and classification values that were previously
duplicated across ad_detector.py and text_pattern_matcher.py.
"""

# Invalid sponsor values that indicate extraction failure or garbage data.
# Used by ad_detector (validate_ads_from_response, _extract_sponsor_from_reason)
# and text_pattern_matcher (create_pattern_from_ad).
INVALID_SPONSOR_VALUES = frozenset({
    'none', 'unknown', 'null', 'n/a', 'na', '', 'no', 'yes',
    'ad', 'ads', 'sponsor', 'sponsors', 'advertisement', 'advertisements',
    'multiple', 'various', 'detected', 'advertisement detected',
    'host read', 'host-read', 'mid-roll', 'pre-roll', 'post-roll'
})

# Structural fields in LLM ad response objects that never contain sponsor info.
# Everything NOT in this set is a candidate for dynamic field scanning.
STRUCTURAL_FIELDS = frozenset({
    'start', 'end', 'start_time', 'end_time', 'start_timestamp', 'end_timestamp',
    'ad_start_timestamp', 'ad_end_timestamp', 'start_time_seconds', 'end_time_seconds',
    'confidence', 'end_text', 'is_ad', 'type', 'classification',
    'start_seconds', 'end_seconds', 'duration', 'duration_seconds',
    'music_bed', 'music_bed_confidence',
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
    'lmnt', 'acast',
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
# e.g., "not an advertisement" -> regex captures "not an" as sponsor
INVALID_SPONSOR_CAPTURE_WORDS = frozenset({
    'not', 'no', 'this', 'that', 'the', 'a', 'an', 'another',
    'consistent', 'possible', 'potential', 'likely', 'seems',
    'is', 'was', 'are', 'were', 'with', 'from', 'for', 'by',
    'clear', 'any', 'some', 'host', 'their', 'its', 'our',
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
