"""Sponsor and normalization service - single source of truth for sponsor data."""
import re
import json
import logging
from datetime import datetime, timedelta
from typing import List, Dict, Optional

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

    def _refresh_cache_if_needed(self):
        """Cache for 5 minutes to avoid constant DB hits."""
        if self._cache_time and (datetime.utcnow() - self._cache_time) < self._cache_ttl:
            return

        self._cache_normalizations = self.db.get_sponsor_normalizations(active_only=True)
        self._cache_sponsors = self.db.get_known_sponsors(active_only=True)
        self._cache_time = datetime.utcnow()
        logger.debug(f"Refreshed sponsor cache: {len(self._cache_sponsors)} sponsors, "
                    f"{len(self._cache_normalizations)} normalizations")

    def invalidate_cache(self):
        """Call after any updates."""
        self._cache_time = None
        self._cache_normalizations = None
        self._cache_sponsors = None

    # ========== Initialization ==========

    def seed_initial_data(self):
        """Seed sponsors and normalizations if tables are empty."""
        # Check if already seeded
        existing_sponsors = self.db.get_known_sponsors(active_only=False)
        if existing_sponsors:
            logger.debug("Sponsors already seeded, skipping")
            return

        logger.info("Seeding initial sponsor and normalization data...")

        # Seed sponsors
        for sponsor in SEED_SPONSORS:
            try:
                self.db.create_known_sponsor(
                    name=sponsor["name"],
                    aliases=sponsor.get("aliases", []),
                    category=sponsor.get("category")
                )
            except Exception as e:
                logger.warning(f"Failed to seed sponsor {sponsor['name']}: {e}")

        # Seed normalizations
        for norm in SEED_NORMALIZATIONS:
            try:
                self.db.create_sponsor_normalization(
                    pattern=norm["pattern"],
                    replacement=norm["replacement"],
                    category=norm["category"]
                )
            except Exception as e:
                logger.warning(f"Failed to seed normalization {norm['pattern']}: {e}")

        self.invalidate_cache()
        logger.info(f"Seeded {len(SEED_SPONSORS)} sponsors and {len(SEED_NORMALIZATIONS)} normalizations")

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
            # Parse aliases from JSON string
            aliases = sponsor.get('aliases', '[]')
            if isinstance(aliases, str):
                try:
                    aliases = json.loads(aliases)
                except json.JSONDecodeError:
                    aliases = []
            names.extend(aliases)
        return names

    def find_sponsor_in_text(self, text: str) -> Optional[str]:
        """Identify sponsor mentioned in text. Returns canonical sponsor name or None."""
        if not text:
            return None

        text_lower = text.lower()

        for sponsor in self.get_sponsors():
            # Check main name
            if sponsor['name'].lower() in text_lower:
                return sponsor['name']

            # Check aliases
            aliases = sponsor.get('aliases', '[]')
            if isinstance(aliases, str):
                try:
                    aliases = json.loads(aliases)
                except json.JSONDecodeError:
                    aliases = []

            for alias in aliases:
                if alias.lower() in text_lower:
                    return sponsor['name']

        return None

    def get_sponsors_in_text(self, text: str) -> List[str]:
        """Find all sponsors mentioned in text. Returns list of canonical names."""
        if not text:
            return []

        text_lower = text.lower()
        found = []

        for sponsor in self.get_sponsors():
            # Check main name
            if sponsor['name'].lower() in text_lower:
                found.append(sponsor['name'])
                continue

            # Check aliases
            aliases = sponsor.get('aliases', '[]')
            if isinstance(aliases, str):
                try:
                    aliases = json.loads(aliases)
                except json.JSONDecodeError:
                    aliases = []

            for alias in aliases:
                if alias.lower() in text_lower:
                    found.append(sponsor['name'])
                    break

        return found

    # ========== Export for Claude prompt / Whisper ==========

    def get_claude_sponsor_list(self) -> str:
        """Format sponsors for Claude prompt."""
        sponsors = self.get_sponsors()
        return ', '.join(s['name'] for s in sponsors)

    def get_normalization_dict(self) -> Dict[str, str]:
        """For Whisper post-processing. Returns {pattern: replacement}."""
        return {n['pattern']: n['replacement'] for n in self.get_normalizations()}

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
