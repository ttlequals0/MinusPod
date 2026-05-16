"""Sponsor and normalization service - single source of truth for sponsor data."""
import re
import json
import logging
from typing import List, Dict, Optional

from utils.constants import (
    INVALID_SPONSOR_VALUES,
    INVALID_SPONSOR_CAPTURE_WORDS,
    NON_BRAND_WORDS,
    SEED_SPONSORS,
    SEED_NORMALIZATIONS,
)
from utils.ttl_cache import TTLCache

logger = logging.getLogger(__name__)

# Re-export for back-compat: callers may still do `from sponsor_service import SEED_SPONSORS`.
__all__ = ['SponsorService', 'SEED_SPONSORS', 'SEED_NORMALIZATIONS']


class SponsorService:
    """Single source of truth for sponsors and normalizations."""

    def __init__(self, db):
        """Initialize with database instance."""
        self.db = db
        self._cache_normalizations = None
        self._cache_sponsors = None
        # Cache freshness gate; payload lives on instance attrs above and
        # _compiled_patterns below. Single key '_loaded'.
        self._freshness = TTLCache(ttl_seconds=300.0)  # 5 minutes
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
        if self._freshness.get('_loaded') is not None:
            return

        self._cache_normalizations = self.db.get_sponsor_normalizations(active_only=True)
        self._cache_sponsors = self.db.get_known_sponsors(active_only=True)
        self._freshness.set('_loaded', True)

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
        self._freshness.clear()
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

    @staticmethod
    def extract_sponsor_from_reason(text: str) -> Optional[str]:
        """Extract sponsor name from descriptive reason / ad-classification text.

        Distinct from extract_sponsor_from_text: this targets short descriptive
        strings produced by the LLM ("Acme sponsor read", "ad for Acme",
        "promoting Acme") rather than full transcript text. Returns the raw
        captured token (case preserved) when valid, else None.
        """
        if not text:
            return None
        patterns = [
            r'^(\w+(?:\s+\w+)?)\s+(?:sponsor|ad)\s+read',
            r'(?:this is (?:a|an) )?(\w+(?:\s+\w+)?)\s+(?:ad|advertisement|sponsor)',
            r'(?:ad|advertisement|sponsor)(?:ship)?\s+(?:for|by|from)\s+(\w+(?:\s+\w+)?)',
            r'promoting\s+(\w+(?:\s+\w+)?)',
            r'brought to you by\s+(\w+(?:\s+\w+)?)',
        ]
        for pattern in patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                sponsor = match.group(1).strip()
                if len(sponsor) < 2:
                    continue
                if sponsor.lower() in INVALID_SPONSOR_VALUES:
                    continue
                if sponsor.lower() in ('a', 'an', 'the', 'this', 'that', 'another', 'host'):
                    continue
                first_word = sponsor.split()[0].lower() if sponsor.split() else ''
                if first_word in INVALID_SPONSOR_CAPTURE_WORDS:
                    continue
                if ' ' in sponsor and sponsor == sponsor.lower():
                    continue
                return sponsor
        return None

    @staticmethod
    def extract_sponsors_from_transcript(text: str, ad_reason: str = None) -> set:
        """Extract potential sponsor names from transcript text and optional ad reason.

        Returns a set of lowercase brand tokens harvested from:
        - URL/domain mentions (e.g., "vention" from "ventionteams.com")
        - "dot com" speech transcriptions
        - The ad_reason field (e.g., "Vention sponsor read")

        This is the multi-sponsor counterpart used by merge_same_sponsor_ads
        to test whether adjacent ad regions share a brand.
        """
        sponsors = set()
        if not text:
            text = ''
        text_lower = text.lower()

        # Extract domain names from URLs (e.g., "vention" from "ventionteams.com")
        url_pattern = r'(?:https?://)?(?:www\.)?([a-z0-9]+)(?:teams|\.com|\.tv|\.io|\.co|\.org)'
        for match in re.finditer(url_pattern, text_lower):
            sponsor = match.group(1)
            if len(sponsor) > 2:  # Skip very short matches
                sponsors.add(sponsor)

        # Also look for explicit "dot com" mentions
        dotcom_pattern = r'([a-z]+)\s*(?:dot\s*com|\.com)'
        for match in re.finditer(dotcom_pattern, text_lower):
            sponsor = match.group(1)
            if len(sponsor) > 2:
                sponsors.add(sponsor)

        # Extract brand name from ad reason (e.g., "Vention sponsor read" -> "vention")
        if ad_reason:
            reason_lower = ad_reason.lower()
            # Look for patterns like "X sponsor read", "X ad", "ad for X"
            reason_patterns = [
                r'^([a-z]+)\s+(?:sponsor|ad\b)',  # "Vention sponsor read"
                r'(?:ad for|sponsor(?:ed by)?)\s+([a-z]+)',  # "ad for Vention"
            ]
            for pattern in reason_patterns:
                match = re.search(pattern, reason_lower)
                if match:
                    brand = match.group(1)
                    # Filter common non-brand vocabulary (e.g. "sponsor read",
                    # "ad segment", "complete ad segment"). NON_BRAND_WORDS is
                    # a superset of the original inline excluded_words list.
                    if len(brand) > 2 and brand not in NON_BRAND_WORDS:
                        sponsors.add(brand)

        return sponsors

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
