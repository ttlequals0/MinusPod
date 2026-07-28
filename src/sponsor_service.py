"""Sponsor and normalization service - single source of truth for sponsor data."""
import re
import json
import logging
import threading
from typing import List, Dict, Optional

from utils.constants import (
    INVALID_SPONSOR_VALUES,
    INVALID_SPONSOR_CAPTURE_WORDS,
    NON_BRAND_WORDS,
    REASON_DESCRIPTION_WORDS,
    REASON_DESCRIPTION_MAX,
    SPONSOR_DOMAIN_TLDS,
    MAX_BRAND_WORDS,
    MAX_SPAN_WORDS,
    squash_brand,
    is_sponsor_reasoning_rationale,
    mentions_advertising,
    SEED_SPONSORS,
    SEED_NORMALIZATIONS,
)
from utils.ttl_cache import TTLCache

logger = logging.getLogger(__name__)

# Re-export for back-compat: callers may still do `from sponsor_service import SEED_SPONSORS`.
__all__ = ['SponsorService', 'SEED_SPONSORS', 'SEED_NORMALIZATIONS']

# A brand is a capitalized run. Dot and slash are outside the character class,
# so "Patreon.com/Show" breaks into "Patreon" and "Show" rather than one run.
_BRAND_RUN_RE = re.compile(
    r"[A-Z][A-Za-z0-9&'\u2019-]*(?:\s+[A-Z][A-Za-z0-9&'\u2019-]*)*")
# Bounded quantifier: an unbounded run of [A-Za-z0-9-] here is the
# py/polynomial-redos shape fixed in 1.1.1. 63 is the DNS label limit.
_DOMAIN_RE = re.compile(
    r'\b([A-Za-z0-9][A-Za-z0-9-]{0,62})\.(?:%s)\b' % '|'.join(sorted(SPONSOR_DOMAIN_TLDS)),
    re.IGNORECASE)
_RUN_SPLIT_RE = re.compile(r"[\s'\u2019-]+")
_LABELER_STOPWORDS = NON_BRAND_WORDS | REASON_DESCRIPTION_WORDS


def _brand_run_words(run: str) -> Optional[List[str]]:
    """Words of `run` with leading filler dropped, or None if it names nothing.

    Only INVALID_SPONSOR_CAPTURE_WORDS come off the front; trimming ad
    vocabulary here cost the first word of real names ("Full Circle").
    """
    words = run.split()
    while words and words[0].lower() in INVALID_SPONSOR_CAPTURE_WORDS:
        words.pop(0)
    if not words:
        return None
    run = ' '.join(words)
    if len(run) < 3 or run.lower() in INVALID_SPONSOR_VALUES:
        return None
    parts = [p for p in _RUN_SPLIT_RE.split(run.lower()) if p]
    if all(p in _LABELER_STOPWORDS for p in parts):
        return None
    return None if is_sponsor_reasoning_rationale(run) else words


def _starts_any(domains):
    """Predicate: some domain begins with the given brand head."""
    return lambda head: any(d.startswith(head) for d in domains)


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
        self._cache_lock = threading.Lock()  # guards the cache rebuild
        self._compiled_patterns = {}  # {canonical_name: compiled_regex}
        # Pre-compiled transcript display corrections. Convention: a
        # normalization is treated as a transcript display correction if its
        # replacement contains at least one uppercase character (e.g.
        # "Wegovy"). Lowercase-only replacements (e.g. "ag1") are matcher
        # canonicalizations and are skipped here.
        self._cache_transcript_corrections = []

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
        """Cache for 5 minutes to avoid constant DB hits.

        The rebuild is guarded by a lock and the freshness flag is flipped LAST
        so parallel ad-detection threads can't race two concurrent rebuilds or
        read a half-built cache (concurrency-sweep-1).
        """
        if self._freshness.get('_loaded') is not None:
            return

        with self._cache_lock:
            # Another thread may have rebuilt while we blocked on the lock.
            if self._freshness.get('_loaded') is not None:
                return

            cache_normalizations = self.db.get_sponsor_normalizations(active_only=True)
            cache_sponsors = self.db.get_known_sponsors(active_only=True)

            # Build the transcript-correction list from the normalizations cache.
            # Convention: any active normalization whose replacement contains an
            # uppercase character is a display correction.
            transcript_corrections = []
            for norm in cache_normalizations or []:
                replacement = norm.get('replacement', '')
                if not any(c.isupper() for c in replacement):
                    continue
                try:
                    compiled = re.compile(norm['pattern'], re.IGNORECASE)
                except re.error as e:
                    logger.warning(
                        f"Skipping invalid transcript-correction regex "
                        f"'{norm['pattern']}': {e}"
                    )
                    continue
                transcript_corrections.append((compiled, replacement))

            # Precompile word-boundary regex patterns for sponsor matching
            compiled_patterns = {}
            for sponsor in cache_sponsors:
                name = sponsor['name']
                if len(name) < 3:
                    continue
                # Build pattern matching canonical name + all aliases
                alternatives = [re.escape(name)]
                for alias in self._parse_aliases(sponsor.get('aliases', '[]')):
                    if len(alias) >= 3:
                        alternatives.append(re.escape(alias))
                pattern_str = r'\b(?:' + '|'.join(alternatives) + r')\b'
                compiled_patterns[name] = re.compile(pattern_str, re.IGNORECASE)

            # Publish all caches, then flip the freshness flag last so no reader
            # ever observes a partially-built cache.
            self._cache_normalizations = cache_normalizations
            self._cache_sponsors = cache_sponsors
            self._cache_transcript_corrections = transcript_corrections
            self._compiled_patterns = compiled_patterns
            self._freshness.set('_loaded', True)

            logger.debug(f"Refreshed sponsor cache: {len(cache_sponsors)} sponsors, "
                        f"{len(cache_normalizations)} normalizations")

    def invalidate_cache(self):
        """Call after any updates."""
        self._freshness.clear()
        self._cache_normalizations = None
        self._cache_sponsors = None
        self._cache_transcript_corrections = []

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

    def apply_transcript_corrections(self, text: str) -> str:
        """Apply display-preserving corrections to transcript text.

        Returns the input unchanged when no correction rule matches.
        Casing and whitespace outside the matched span are preserved;
        only entries whose replacement contains uppercase characters are
        applied (see _refresh_cache_if_needed for the convention).
        """
        if not text:
            return text
        self._refresh_cache_if_needed()
        for pattern, replacement in self._cache_transcript_corrections:
            text = pattern.sub(replacement, text)
        return text

    # ========== Sponsors ==========

    def get_sponsors(self) -> List[Dict]:
        """Get all active sponsors."""
        self._refresh_cache_if_needed()
        return self._cache_sponsors or []

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

    def count_sponsor_mentions(self, text: str) -> int:
        """Total registry brand mentions in text, summed over every sponsor.

        Two mentions is the same evidence bar pattern learning uses; one
        passing mention of one brand is not a sponsor read.
        """
        if not text:
            return 0
        self._refresh_cache_if_needed()
        return sum(len(pattern.findall(text))
                   for pattern in self._compiled_patterns.values())

    # ========== Export for Claude prompt / Whisper ==========

    def get_claude_sponsor_list(self) -> str:
        """Format sponsors for Claude prompt."""
        sponsors = self.get_sponsors()
        return ', '.join(s['name'] for s in sponsors)

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
        """Extract a sponsor name from an LLM ad-reason string, else None.

        A brand is a capitalized run narrowed to the span a domain in the same
        text agrees with; the model rewords the reason on every run, so a
        pattern keyed to a phrasing only covers the sample it was written for.
        """
        if not text:
            return None
        # A reason that is entirely the model explaining itself names no
        # advertiser, and a capitalized run inside it is just its first word.
        if is_sponsor_reasoning_rationale(text):
            return None
        # Prose that never mentions advertising names no advertiser either.
        # Without this the first capitalized word of any sentence becomes a
        # brand: "Discussion of the guest's new book" gave "Discussion".
        if not mentions_advertising(text):
            return None
        # Input cap, same reason as the bounded quantifiers above: this runs on
        # whatever string the model put in the field.
        text = text[:REASON_DESCRIPTION_MAX]

        # The first advertiser named labels the break, so stop at the first
        # usable run: a later one with a URL must not win the label.
        words = next(
            (w for w in (_brand_run_words(m.group(0))
                         for m in _BRAND_RUN_RE.finditer(text)) if w),
            None)
        if not words:
            return None

        domains = {squash_brand(m.group(1)) for m in _DOMAIN_RE.finditer(text)}
        # A domain names where the brand both starts and ends, so search spans
        # rather than prefixes: that narrows "Full ZipRecruiter" without a
        # blind leading trim. Leftmost and longest first, so "Jack Archer" is
        # not cut to "Jack" nor "Belmont Park" to "Park".
        span_words = words[:MAX_SPAN_WORDS]
        spans = [(squash_brand(' '.join(span_words[i:j])), i, j)
                 for i in range(len(span_words))
                 for j in range(len(span_words), i, -1)]
        for match_domain in (domains.__contains__, _starts_any(domains)):
            for head, i, j in spans:
                if head and match_domain(head):
                    return ' '.join(span_words[i:j])
        # Nothing agrees, so there is no signal for where the brand ends. Cap
        # it: past this a run is the model describing the product, not naming
        # a brand ("LEGO Land Discovery Center Westchester Ninjago event").
        return ' '.join(words[:MAX_BRAND_WORDS])

    @staticmethod
    def own_site_tokens(podcast_name: str) -> set:
        """Domain labels formed from the show's own name, not advertisers.

        Runs of two or more words plus the whole name; a single title word
        can be a real brand."""
        words = re.findall(r'[a-z0-9]+', (podcast_name or '').lower())
        runs = [''.join(words[i:j])
                for i in range(len(words))
                for j in range(i + 2, len(words) + 1)]
        runs.append(''.join(words))
        return {token for token in runs if len(token) >= 4}

    @staticmethod
    def extract_sponsors_from_transcript(text: str, ad_reason: str = None,
                                         exclude: set = None) -> set:
        """Extract potential sponsor names from transcript text and optional ad reason.

        Returns a set of lowercase brand tokens harvested from:
        - URL/domain mentions (e.g., "vention" from "ventionteams.com")
        - "dot com" speech transcriptions
        - The ad_reason field (e.g., "Vention sponsor read")

        ``exclude`` drops known non-advertiser tokens (see own_site_tokens),
        which also stops the host's own site reading as ad content at a
        boundary.

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

        # Brand named in the ad reason. Shares the labeler rather than keeping
        # a second pair of phrase patterns, which only matched the two
        # phrasings they were written for; merging then missed a brand the
        # marker was already labeled with.
        brand = SponsorService.extract_sponsor_from_reason(ad_reason)
        if brand:
            squashed = squash_brand(brand)
            if len(squashed) > 2 and squashed not in NON_BRAND_WORDS:
                sponsors.add(squashed)

        return sponsors - (exclude or set())

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

    def delete_sponsor(self, sponsor_id: int) -> tuple:
        """Permanently delete a sponsor. Linked patterns are unlinked, not
        deleted. Returns (deleted, unlinked_patterns)."""
        deleted, unlinked = self.db.hard_delete_known_sponsor(sponsor_id)
        if deleted:
            self.invalidate_cache()
        return deleted, unlinked

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
