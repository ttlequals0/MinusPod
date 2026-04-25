"""Sponsor and normalization service - single source of truth for sponsor data."""
import re
import json
import logging
from datetime import datetime, timedelta, timezone
from typing import List, Dict, Optional

from utils.constants import INVALID_SPONSOR_VALUES, INVALID_SPONSOR_CAPTURE_WORDS

logger = logging.getLogger(__name__)

# Seed data for known sponsors (extracted from Claude prompt)
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


class SponsorService:
    """Single source of truth for sponsors and normalizations."""

    def __init__(self, db):
        """Initialize with database instance."""
        self.db = db
        self._cache_normalizations = None
        self._cache_sponsors = None
        self._cache_time = None
        self._cache_ttl = timedelta(minutes=5)
        self._compiled_patterns = {}  # {canonical_name: compiled_regex}

    @staticmethod
    def _parse_aliases(aliases) -> list:
        """Parse aliases from DB value (JSON string or list)."""
        if isinstance(aliases, list):
            return aliases
        if isinstance(aliases, str):
            try:
                return json.loads(aliases)
            except json.JSONDecodeError:
                return []
        return []

    def _refresh_cache_if_needed(self):
        """Cache for 5 minutes to avoid constant DB hits."""
        if self._cache_time and (datetime.now(timezone.utc) - self._cache_time) < self._cache_ttl:
            return

        self._cache_normalizations = self.db.get_sponsor_normalizations(active_only=True)
        self._cache_sponsors = self.db.get_known_sponsors(active_only=True)
        self._cache_time = datetime.now(timezone.utc)

        # Precompile word-boundary regex patterns for sponsor matching
        self._compiled_patterns = {}
        for sponsor in self._cache_sponsors:
            name = sponsor['name']
            if len(name) < 3:
                continue
            # Build pattern matching canonical name + all aliases
            alternatives = [re.escape(name)]
            for alias in self._parse_aliases(sponsor.get('aliases', '[]')):
                if len(alias) >= 3:
                    alternatives.append(re.escape(alias))
            pattern_str = r'\b(?:' + '|'.join(alternatives) + r')\b'
            self._compiled_patterns[name] = re.compile(pattern_str, re.IGNORECASE)

        logger.debug(f"Refreshed sponsor cache: {len(self._cache_sponsors)} sponsors, "
                    f"{len(self._cache_normalizations)} normalizations")

    def invalidate_cache(self):
        """Call after any updates."""
        self._cache_time = None
        self._cache_normalizations = None
        self._cache_sponsors = None

    # ========== Initialization ==========

    def seed_initial_data(self):
        """Idempotent. Inserts SEED rows whose names aren't already in the DB; never touches existing rows.

        Runs at app startup. On a fresh DB it seeds everything; on an existing DB it adds only new
        entries from updates to SEED_SPONSORS / SEED_NORMALIZATIONS. User-edited aliases on existing
        rows are preserved because the membership check happens before any insert.
        """
        existing_names = {s['name'].lower() for s in self.db.get_known_sponsors(active_only=False)}
        added = 0
        for sponsor in SEED_SPONSORS:
            if sponsor['name'].lower() in existing_names:
                continue
            try:
                self.db.create_known_sponsor(
                    name=sponsor['name'],
                    aliases=sponsor.get('aliases', []),
                    category=sponsor.get('category'),
                )
                added += 1
            except Exception as e:
                logger.warning(f"Failed to seed sponsor {sponsor['name']}: {e}")

        existing_patterns = {n['pattern'] for n in self.db.get_sponsor_normalizations(active_only=False)}
        norm_added = 0
        for norm in SEED_NORMALIZATIONS:
            if norm['pattern'] in existing_patterns:
                continue
            try:
                self.db.create_sponsor_normalization(
                    pattern=norm['pattern'],
                    replacement=norm['replacement'],
                    category=norm['category'],
                )
                norm_added += 1
            except Exception as e:
                logger.warning(f"Failed to seed normalization {norm['pattern']}: {e}")

        self.invalidate_cache()
        if added or norm_added:
            logger.info(f"Seeded {added} new sponsors and {norm_added} new normalizations (existing rows preserved)")

    # ========== Normalization ==========

    def get_normalizations(self) -> List[Dict]:
        """Get all active normalizations."""
        self._refresh_cache_if_needed()
        return self._cache_normalizations or []

    def normalize_text(self, text: str) -> str:
        """Apply all active normalizations to text."""
        if not text:
            return text

        text = text.lower()

        for norm in self.get_normalizations():
            try:
                text = re.sub(norm['pattern'], norm['replacement'], text, flags=re.IGNORECASE)
            except re.error as e:
                logger.warning(f"Invalid regex pattern '{norm['pattern']}': {e}")

        # Normalize whitespace
        return ' '.join(text.split())

    # ========== Sponsors ==========

    def get_sponsors(self) -> List[Dict]:
        """Get all active sponsors."""
        self._refresh_cache_if_needed()
        return self._cache_sponsors or []

    def get_sponsor_names(self) -> List[str]:
        """Flat list of all sponsor names + aliases."""
        names = []
        for sponsor in self.get_sponsors():
            names.append(sponsor['name'])
            names.extend(self._parse_aliases(sponsor.get('aliases', '[]')))
        return names

    def find_sponsor_in_text(self, text: str) -> Optional[str]:
        """Identify sponsor mentioned in text. Returns canonical sponsor name or None.

        Uses precompiled word-boundary patterns to avoid false positives from short
        names appearing inside longer words. Names/aliases shorter than 3 characters
        are skipped.
        """
        if not text:
            return None

        self._refresh_cache_if_needed()
        for name, pattern in self._compiled_patterns.items():
            if pattern.search(text):
                return name

        return None

    def get_sponsors_in_text(self, text: str) -> List[str]:
        """Find all sponsors mentioned in text. Returns list of canonical names.

        Uses precompiled word-boundary patterns to avoid false positives from short
        names appearing inside longer words. Names/aliases shorter than 3 characters
        are skipped.
        """
        if not text:
            return []

        self._refresh_cache_if_needed()
        found = []
        for name, pattern in self._compiled_patterns.items():
            if pattern.search(text):
                found.append(name)
        return found

    # ========== Export for Claude prompt / Whisper ==========

    def get_claude_sponsor_list(self) -> str:
        """Format sponsors for Claude prompt."""
        sponsors = self.get_sponsors()
        return ', '.join(s['name'] for s in sponsors)

    def get_normalization_dict(self) -> Dict[str, str]:
        """For Whisper post-processing. Returns {pattern: replacement}."""
        return {n['pattern']: n['replacement'] for n in self.get_normalizations()}

    # ========== Sponsor Extraction from Text ==========

    @staticmethod
    def extract_sponsor_from_text(ad_text: str) -> Optional[str]:
        """Extract sponsor name from ad text by looking for URLs and common patterns.

        Looks for:
        - Domain names (e.g., hex.ai, thisisnewjersey.com)
        - Common sponsor phrases (e.g., "brought to you by X", "sponsored by X")
        """
        if not ad_text:
            return None

        # Look for URLs/domains mentioned in the text.
        # Bounded quantifier + input cap prevent polynomial ReDoS on adversarial text.
        domain_pattern = r'(?:visit\s+)?(?:www\.)?([a-zA-Z0-9-]{1,63})\.(?:com|ai|io|org|net|co|gov)(?:/\S{0,200})?'
        domains = re.findall(domain_pattern, ad_text.lower()[:5000])

        ignore_domains = {'example', 'website', 'podcast', 'episode', 'click', 'link'}
        domains = [d for d in domains if d not in ignore_domains]

        if domains:
            sponsor = domains[0].replace('-', ' ').title()
            return sponsor

        # Look for "brought to you by X" or "sponsored by X" patterns
        sponsor_patterns = [
            r'brought to you by\s+([A-Z][a-zA-Z0-9\s]+?)(?:\.|,|!|\s+is|\s+where|\s+the)',
            r'sponsored by\s+([A-Z][a-zA-Z0-9\s]+?)(?:\.|,|!|\s+is|\s+where|\s+the)',
            r'thanks to\s+([A-Z][a-zA-Z0-9\s]+?)(?:\s+for|\.|,|!)',
        ]

        for pattern in sponsor_patterns:
            match = re.search(pattern, ad_text, re.IGNORECASE)
            if match:
                sponsor = match.group(1).strip()
                if len(sponsor) < 50:
                    return sponsor

        return None

    # ========== CRUD Wrappers ==========

    def add_sponsor(self, name: str, aliases: List[str] = None,
                    category: str = None) -> int:
        """Add a new sponsor. Returns sponsor ID."""
        sponsor_id = self.db.create_known_sponsor(name, aliases, category)
        self.invalidate_cache()
        return sponsor_id

    def update_sponsor(self, sponsor_id: int, **kwargs) -> bool:
        """Update a sponsor."""
        result = self.db.update_known_sponsor(sponsor_id, **kwargs)
        if result:
            self.invalidate_cache()
        return result

    def delete_sponsor(self, sponsor_id: int) -> bool:
        """Delete (deactivate) a sponsor."""
        result = self.db.delete_known_sponsor(sponsor_id)
        if result:
            self.invalidate_cache()
        return result

    def add_normalization(self, pattern: str, replacement: str, category: str) -> int:
        """Add a new normalization. Returns normalization ID."""
        norm_id = self.db.create_sponsor_normalization(pattern, replacement, category)
        self.invalidate_cache()
        return norm_id

    def update_normalization(self, norm_id: int, **kwargs) -> bool:
        """Update a normalization."""
        result = self.db.update_sponsor_normalization(norm_id, **kwargs)
        if result:
            self.invalidate_cache()
        return result

    def delete_normalization(self, norm_id: int) -> bool:
        """Delete (deactivate) a normalization."""
        result = self.db.delete_sponsor_normalization(norm_id)
        if result:
            self.invalidate_cache()
        return result
