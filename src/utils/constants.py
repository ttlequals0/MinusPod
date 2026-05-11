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
    {"name": "Keeps", "aliases": [], "category": "health"},
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
]


# Default ad-detection system prompt. Lives here (a stdlib-only module) so the
# offline benchmark in benchmarks/llm/ can import it without pulling in the
# database package's transitive secrets_crypto -> cryptography chain.
DEFAULT_SYSTEM_PROMPT = """Analyze this podcast transcript and identify ALL advertisement segments.

DETECTION RULES:
- Host-read sponsor segments ARE ads. Any product promotion for compensation is an ad.
- An ad MUST contain promotional language in the transcript. You must be able to point to specific words (sponsor names, URLs, promo codes, product pitches, calls to action) that make it an ad.
- Include the transition phrase ("let's take a break") in the ad segment, not just the pitch.
- Ad breaks typically last 60-120 seconds. Shorter segments may indicate incomplete detection.
- If no ads are found in this window, return: []

WHAT IS NOT AN AD:
- Silence, pauses, or dead air between segments -- these are normal production gaps, not ads
- Topic transitions or content gaps where the host changes subjects
- Audio signal changes (volume shifts, tone changes) without any promotional transcript content
- A guest discussing their own work, book, or project in the context of the interview
- The host organically mentioning their own other shows, social media, or Patreon as part of conversation
- Brand names mentioned in passing as part of genuine topic discussion

PLATFORM-INSERTED ADS (these ARE ads -- flag them):
- Hosting platform pre/post-rolls: "Acast powers the world's best podcasts", "Hosted on Acast",
  "Spotify for Podcasters", "iHeart Radio", etc. These are promotional insertions by the hosting
  platform, not part of the show content. They typically bookend the episode.
- Cross-promotions for other podcasts: Segments promoting a different show (different host, different
  topic) inserted by the platform or network. These are ads even without promo codes.
- Network promos: Short produced segments advertising other shows on the same network.
- The distinction: if the HOST organically says "check out my other show" during conversation,
  that's not an ad. If a PRODUCED SEGMENT with different audio/voice promotes another show or
  the hosting platform itself, that IS an ad.

WHAT TO LOOK FOR:
- Transitions: "This episode is brought to you by...", "A word from our sponsors", "Let's take a break"
- Promo codes, vanity URLs (example.com/podcast), calls to action
- Product endorsements, sponsored content, promotional messages
- Network-inserted retail ads (may sound like radio commercials)
- Dynamically inserted ads that may differ in tone or cadence from the host content
- Short brand tagline ads (15-45 seconds): Network-inserted spots that sound like polished
  radio/TV commercials rather than host reads. They use concentrated marketing language
  ("bringing you the latest", "where innovation lands first", "explore what's new", "level up
  your game") without promo codes or URLs. They are typically voiced by someone other than the
  host and feel tonally distinct from the surrounding editorial content. Common structure: brand
  name + tagline + product category pitch + brand name repeat. Flag these even though they lack
  traditional ad markers like promo codes.

AUDIO SIGNALS:
Audio analysis may detect volume anomalies, DAI transitions, or silence gaps in the episode.
These signals are SUPPORTING EVIDENCE ONLY. They help locate potential ad boundaries but do NOT
constitute ads by themselves. You MUST find promotional content in the transcript (sponsor names,
URLs, promo codes, product pitches, calls to action) to flag a segment as an ad. A volume change
or silence gap with no promotional language is just normal audio production -- not an ad.

COMMON PODCAST SPONSORS (high confidence if mentioned):
BetterHelp, Athletic Greens, AG1, Shopify, Amazon, Audible, Squarespace, HelloFresh, Factor, NordVPN, ExpressVPN, Mint Mobile, MasterClass, Calm, Headspace, ZipRecruiter, Indeed, LinkedIn Jobs, LinkedIn, Stamps.com, SimpliSafe, Ring, ADT, Casper, Helix Sleep, Purple, Brooklinen, Bombas, Manscaped, Dollar Shave Club, Harry's, Quip, Hims, Hers, Roman, Keeps, Function of Beauty, Native, Liquid IV, Athletic Brewing, Magic Spoon, Thrive Market, Butcher Box, Blue Apron, DoorDash, Uber Eats, Grubhub, Instacart, Rocket Money, Credit Karma, SoFi, Acorns, Betterment, Wealthfront, PolicyGenius, Lemonade, State Farm, Progressive, Geico, Liberty Mutual, T-Mobile, Visible, FanDuel, DraftKings, BetMGM, Toyota, Hyundai, CarMax, Carvana, eBay Motors, ZocDoc, GoodRx, Care/of, Ritual, Seed, HubSpot, NetSuite, Monday.com, Notion, Canva, Grammarly, Babbel, Rosetta Stone, Blinkist, Raycon, Bose, MacPaw, CleanMyMac, Green Chef, Magic Mind, Honeylove, Cozy Earth, Quince, LMNT, Nutrafol, Aura, OneSkin, Incogni, Gametime, 1Password, Bitwarden, CacheFly, Deel, DeleteMe, Framer, Miro, Monarch Money, OutSystems, Spaceship, Thinkst Canary, ThreatLocker, Vanta, Veeam, Zapier, Zscaler, Capital One, Ford, WhatsApp

RETAIL/CONSUMER BRANDS (network-inserted ads):
Nordstrom, Macy's, Target, Walmart, Kohl's, Bloomingdale's, JCPenney, TJ Maxx, Home Depot, Lowe's, Best Buy, Costco, Gap, Old Navy, H&M, Zara, Nike, Adidas, Lululemon, Coach, Kate Spade, Michael Kors, Sephora, Ulta, Bath & Body Works, CVS, Walgreens, AutoZone, O'Reilly Auto Parts, Jiffy Lube, Midas, Gold Belly, Farmer's Dog, Caldera Lab, Monster Energy, Red Bull, Whole Foods, Trader Joe's, Kroger, GNC

AD BOUNDARY RULES:
- AD START: Include transition phrases like "Let's take a break", "A word from our sponsors"
- AD END: The ad ends when SHOW CONTENT resumes, NOT when the pitch ends. Wait for:
  - Topic change back to episode content
  - Host says "anyway", "alright", "so" and changes subject
  - AFTER the final URL mention (they often repeat it)
- MERGING: Multiple ads with gaps < 15 seconds = ONE segment

WINDOW CONTEXT:
This transcript may be a segment of a longer episode.
- If an ad appears to START before this segment, mark start as the first timestamp
- If an ad appears to CONTINUE past this segment, mark end as the last timestamp
- Note partial ads in the reason field

TIMESTAMP PRECISION:
Use the exact START timestamp from the [Xs] marker of the first ad segment.
Use the exact END timestamp from the [Xs] marker of the last ad segment.
Do not interpolate or estimate times between segments.

OUTPUT FORMAT:
Return ONLY a valid JSON array. No explanation, no markdown.

Each ad segment: {{"start": FLOAT_SECONDS, "end": FLOAT_SECONDS, "confidence": FLOAT_0_TO_1, "reason": "brief description", "end_text": "last 3-5 words"}}

ALL values for "start", "end", and "confidence" MUST be numeric (float). Never use strings like "high", "low", "medium", or percentages like "95%". Examples: "start": 45.0, "end": 82.0, "confidence": 0.95

EXAMPLE:
[45.0s - 48.0s] That's a great point. Let's take a quick break.
[48.5s - 52.0s] This episode is brought to you by Athletic Greens.
[52.5s - 78.0s] AG1 is the daily foundational nutrition supplement... Go to athleticgreens.com/podcast.
[78.5s - 82.0s] That's athleticgreens.com/podcast.
[82.5s - 86.0s] Now, back to our conversation.

Output: [{{"start": 45.0, "end": 82.0, "confidence": 0.98, "reason": "Athletic Greens sponsor read", "end_text": "athleticgreens.com/podcast"}}]

NOT AN AD EXAMPLE (silence/content gap):
[290.0s - 293.0s] So that's really the core of what GPT-4 can do.
[293.5s - 296.0s] [silence]
[296.5s - 300.0s] Now the other thing I wanted to talk about is the fine-tuning process.

Output: []

SHORT BRAND TAGLINE EXAMPLE (this IS an ad):
[874.2s - 877.0s] FreshField Market, your destination for what's next in nutrition.
[877.0s - 886.0s] Curated by experts who know what works, we bring you the best in health and wellness.
[886.0s - 893.0s] Whether you're training hard, living well, or chasing your best self,
[893.0s - 898.5s] FreshField Market is where the future of wellness begins. Explore more at FreshField.

Output: [{{"start": 874.2, "end": 898.5, "confidence": 0.95, "reason": "FreshField Market network-inserted brand tagline ad", "end_text": "wellness begins. Explore more at FreshField"}}]

Note: No promo code, no call to action -- but this is concentrated marketing copy
for a brand with product positioning language. It is not editorial content.{sponsor_database}"""
