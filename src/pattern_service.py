"""
Pattern Service - Manages ad pattern hierarchy and automatic promotion.

Handles:
- Three-tier pattern hierarchy: Global -> Network -> Podcast
- Automatic pattern promotion based on match frequency
- RSS metadata extraction for network/DAI platform detection
- Pattern lookup with scope priority
"""
import logging
import json
from dataclasses import dataclass
from typing import List, Optional, Dict, Tuple
from datetime import datetime

from config import (
    PODCAST_TO_NETWORK_THRESHOLD,
    NETWORK_TO_GLOBAL_THRESHOLD,
    PROMOTION_SIMILARITY_THRESHOLD,
    SPONSOR_GLOBAL_THRESHOLD
)

logger = logging.getLogger('podcast.patterns')

# Known DAI platforms and their RSS signatures
DAI_PLATFORMS = {
    'megaphone': [
        'megaphone.fm',
        'megaphone.co',
        'traffic.megaphone.fm',
        'cdn.megaphone.fm'
    ],
    'acast': [
        'acast.com',
        'shows.acast.com',
        'open.acast.com'
    ],
    'art19': [
        'art19.com',
        'rss.art19.com',
        'content.art19.com'
    ],
    'omny': [
        'omny.fm',
        'omnycontent.com',
        'omnyfm.com'
    ],
    'simplecast': [
        'simplecast.com',
        'cdn.simplecast.com'
    ],
    'spreaker': [
        'spreaker.com',
        'www.spreaker.com'
    ],
    'podbean': [
        'podbean.com',
        'www.podbean.com'
    ],
    'anchor': [
        'anchor.fm',
        'd3t3ozftmdmh3i.cloudfront.net'  # Anchor CDN
    ],
    'spotify': [
        'spotify.com',
        'spotifyanchor-web.app.link'
    ],
    'triton': [
        'tritondigital.com',
        'tdsdk.com'
    ]
}

# Known podcast networks and their identifiers
KNOWN_NETWORKS = {
    'twit': ['twit.tv', 'twit.am', 'twit network'],
    'relay_fm': ['relay.fm', 'relay fm'],
    'gimlet': ['gimlet', 'gimletmedia'],
    'the_ringer': ['theringer.com', 'ringer podcast network'],
    'wondery': ['wondery.com', 'wondery'],
    'npr': ['npr.org', 'national public radio'],
    'nyt': ['nytimes.com', 'new york times'],
    'parcast': ['parcast.com', 'parcast network'],
    'pushkin': ['pushkin.fm', 'pushkin industries'],
    'crooked_media': ['crooked.com', 'crooked media'],
    'earwolf': ['earwolf.com', 'earwolf'],
    'maximum_fun': ['maximumfun.org', 'maximum fun'],
    'radiotopia': ['radiotopia.fm', 'radiotopia'],
    'vox': ['vox.com', 'vox media podcast network'],
    'slate': ['slate.com', 'slate podcasts'],
    'iheart': ['iheart.com', 'iheartradio', 'iheartpodcast'],
}


@dataclass
class PatternMatch:
    """Represents a pattern match result."""
    pattern_id: int
    scope: str
    confidence: float
    sponsor: Optional[str]
    text_similarity: float


class PatternService:
    """
    Service for managing ad pattern hierarchy and promotion.

    Pattern Scope Hierarchy (lookup priority):
    1. Podcast - Patterns specific to a single podcast
    2. Network - Patterns shared across podcasts in the same network
    3. Global - Patterns that apply to all podcasts (typically DAI ads)
    """

    def __init__(self, db=None):
        """
        Initialize the pattern service.

        Args:
            db: Database instance
        """
        self.db = db

    def detect_dai_platform(self, feed_url: str, feed_content: str = None) -> Optional[str]:
        """
        Detect the DAI (Dynamic Ad Insertion) platform from feed metadata.

        Args:
            feed_url: The RSS feed URL
            feed_content: Optional raw feed XML content

        Returns:
            Platform identifier string or None
        """
        feed_url_lower = feed_url.lower()

        # Check URL against known platform domains
        for platform, signatures in DAI_PLATFORMS.items():
            for sig in signatures:
                if sig in feed_url_lower:
                    logger.debug(f"Detected DAI platform '{platform}' from URL")
                    return platform

        # Check feed content for platform indicators
        if feed_content:
            content_lower = feed_content.lower()
            for platform, signatures in DAI_PLATFORMS.items():
                for sig in signatures:
                    if sig in content_lower:
                        logger.debug(f"Detected DAI platform '{platform}' from feed content")
                        return platform

        return None

    def detect_network(self, feed_url: str, feed_title: str = None,
                       feed_description: str = None, feed_author: str = None) -> Optional[str]:
        """
        Detect the podcast network from feed metadata.

        Args:
            feed_url: The RSS feed URL
            feed_title: Feed title
            feed_description: Feed description
            feed_author: Feed author/owner

        Returns:
            Network identifier string or None
        """
        # Combine all metadata for searching
        searchable = ' '.join(filter(None, [
            feed_url.lower(),
            (feed_title or '').lower(),
            (feed_description or '').lower()[:500],
            (feed_author or '').lower()
        ]))

        for network, signatures in KNOWN_NETWORKS.items():
            for sig in signatures:
                if sig in searchable:
                    logger.debug(f"Detected network '{network}' from feed metadata")
                    return network

        return None

    def get_patterns_for_podcast(
        self,
        podcast_id: str,
        network_id: str = None
    ) -> List[Dict]:
        """
        Get all applicable patterns for a podcast, ordered by scope priority.

        Args:
            podcast_id: The podcast slug/ID
            network_id: Optional network ID

        Returns:
            List of patterns, podcast-specific first, then network, then global
        """
        if not self.db:
            return []

        patterns = []

        # Priority 1: Podcast-specific patterns
        podcast_patterns = self.db.get_ad_patterns(
            scope='podcast',
            podcast_id=podcast_id,
            active_only=True
        )
        for p in podcast_patterns:
            p['_priority'] = 0
        patterns.extend(podcast_patterns)

        # Priority 2: Network patterns (if network_id provided)
        if network_id:
            network_patterns = self.db.get_ad_patterns(
                scope='network',
                network_id=network_id,
                active_only=True
            )
            for p in network_patterns:
                p['_priority'] = 1
            patterns.extend(network_patterns)

        # Priority 3: Global patterns
        global_patterns = self.db.get_ad_patterns(
            scope='global',
            active_only=True
        )
        for p in global_patterns:
            p['_priority'] = 2
        patterns.extend(global_patterns)

        # Sort by priority, then by confirmation count
        patterns.sort(key=lambda p: (p['_priority'], -p.get('confirmation_count', 0)))

        return patterns

    def check_for_promotion(self, pattern_id: int) -> Optional[str]:
        """
        Check if a pattern should be promoted to a broader scope.

        Args:
            pattern_id: The pattern ID to check

        Returns:
            New scope if promotion is warranted, None otherwise
        """
        if not self.db:
            return None

        pattern = self.db.get_ad_pattern_by_id(pattern_id)
        if not pattern or not pattern.get('is_active'):
            return None

        current_scope = pattern.get('scope')

        if current_scope == 'podcast':
            # Check if pattern matches across multiple podcasts in same network
            similar_count = self._count_similar_patterns_in_network(pattern)
            if similar_count >= PODCAST_TO_NETWORK_THRESHOLD:
                logger.info(
                    f"Pattern {pattern_id} qualifies for network promotion "
                    f"({similar_count} similar patterns in network)"
                )
                return 'network'

        elif current_scope == 'network':
            # Check if pattern matches across multiple networks
            network_count = self._count_networks_with_similar_pattern(pattern)
            if network_count >= NETWORK_TO_GLOBAL_THRESHOLD:
                logger.info(
                    f"Pattern {pattern_id} qualifies for global promotion "
                    f"({network_count} networks with similar patterns)"
                )
                return 'global'

        return None

    def promote_pattern(self, pattern_id: int, new_scope: str) -> bool:
        """
        Promote a pattern to a broader scope.

        Args:
            pattern_id: The pattern to promote
            new_scope: The new scope ('network' or 'global')

        Returns:
            True if promotion succeeded
        """
        if not self.db:
            return False

        try:
            pattern = self.db.get_ad_pattern_by_id(pattern_id)
            if not pattern:
                return False

            # Update pattern scope
            self.db.update_ad_pattern(pattern_id, scope=new_scope)

            # Log the promotion
            self.db.create_pattern_correction(
                pattern_id=pattern_id,
                correction_type='promotion',
                text_snippet=f"Auto-promoted from {pattern['scope']} to {new_scope}"
            )

            logger.info(f"Promoted pattern {pattern_id} to {new_scope} scope")
            return True

        except Exception as e:
            logger.error(f"Failed to promote pattern {pattern_id}: {e}")
            return False

    def merge_similar_patterns(
        self,
        pattern_ids: List[int],
        target_scope: str = 'network'
    ) -> Optional[int]:
        """
        Merge multiple similar patterns into a single pattern.

        Combines text templates, intro/outro variants from all patterns.
        The merged pattern inherits the highest confirmation count.

        Args:
            pattern_ids: List of pattern IDs to merge
            target_scope: Scope for the merged pattern

        Returns:
            ID of the merged pattern, or None if merge failed
        """
        if not self.db or len(pattern_ids) < 2:
            return None

        try:
            patterns = [self.db.get_ad_pattern_by_id(pid) for pid in pattern_ids]
            patterns = [p for p in patterns if p is not None]

            if len(patterns) < 2:
                return None

            # Collect all variants
            all_intros = set()
            all_outros = set()
            sponsors = set()
            max_confirmations = 0
            best_template = None
            best_template_len = 0

            for pattern in patterns:
                # Collect intro variants
                intros = pattern.get('intro_variants', '[]')
                if isinstance(intros, str):
                    intros = json.loads(intros)
                all_intros.update(intros)

                # Collect outro variants
                outros = pattern.get('outro_variants', '[]')
                if isinstance(outros, str):
                    outros = json.loads(outros)
                all_outros.update(outros)

                # Collect sponsors
                if pattern.get('sponsor'):
                    sponsors.add(pattern['sponsor'])

                # Track highest confirmation count
                conf = pattern.get('confirmation_count', 0)
                if conf > max_confirmations:
                    max_confirmations = conf

                # Use longest template as the canonical one
                template = pattern.get('text_template', '')
                if template and len(template) > best_template_len:
                    best_template = template
                    best_template_len = len(template)

            # Create merged pattern
            merged_id = self.db.create_ad_pattern(
                scope=target_scope,
                text_template=best_template,
                sponsor=list(sponsors)[0] if len(sponsors) == 1 else None,
                intro_variants=list(all_intros),
                outro_variants=list(all_outros)
            )

            # Update confirmation count
            self.db.update_ad_pattern(merged_id, confirmation_count=max_confirmations)

            # Disable original patterns
            for pid in pattern_ids:
                self.db.update_ad_pattern(
                    pid,
                    is_active=False,
                    disabled_reason=f"Merged into pattern {merged_id}"
                )

            logger.info(
                f"Merged {len(pattern_ids)} patterns into new {target_scope} "
                f"pattern {merged_id}"
            )
            return merged_id

        except Exception as e:
            logger.error(f"Failed to merge patterns: {e}")
            return None

    def _count_similar_patterns_in_network(self, pattern: Dict) -> int:
        """Count how many podcasts in the same network have similar patterns."""
        if not self.db:
            return 0

        network_id = pattern.get('network_id')
        if not network_id:
            return 0

        # Get all podcast-scoped patterns in the network
        all_patterns = self.db.get_ad_patterns(scope='podcast', network_id=network_id)

        # Count unique podcasts with similar patterns
        similar_podcasts = set()
        template = pattern.get('text_template', '')

        for p in all_patterns:
            if p['id'] == pattern['id']:
                continue
            if self._patterns_similar(template, p.get('text_template', '')):
                podcast_id = p.get('podcast_id')
                if podcast_id:
                    similar_podcasts.add(podcast_id)

        return len(similar_podcasts)

    def _count_networks_with_similar_pattern(self, pattern: Dict) -> int:
        """Count how many networks have similar patterns."""
        if not self.db:
            return 0

        # Get all network-scoped patterns
        all_patterns = self.db.get_ad_patterns(scope='network')

        # Count unique networks with similar patterns
        similar_networks = set()
        template = pattern.get('text_template', '')

        for p in all_patterns:
            if p['id'] == pattern['id']:
                continue
            if self._patterns_similar(template, p.get('text_template', '')):
                network_id = p.get('network_id')
                if network_id:
                    similar_networks.add(network_id)

        return len(similar_networks)

    def _patterns_similar(self, text1: str, text2: str) -> bool:
        """Check if two pattern texts are similar enough to merge."""
        if not text1 or not text2:
            return False

        try:
            from rapidfuzz import fuzz
            similarity = fuzz.ratio(text1.lower(), text2.lower()) / 100
            return similarity >= PROMOTION_SIMILARITY_THRESHOLD
        except ImportError:
            # Fallback to simple comparison
            return text1.lower()[:100] == text2.lower()[:100]

    def record_pattern_match(
        self,
        pattern_id: int,
        episode_id: str = None
    ) -> None:
        """
        Record that a pattern was matched, updating last_matched_at.

        Also triggers promotion check.

        Args:
            pattern_id: The matched pattern ID
            episode_id: Optional episode ID for logging
        """
        if not self.db:
            return

        try:
            # Update last_matched_at
            self.db.update_ad_pattern(
                pattern_id,
                last_matched_at=datetime.utcnow().isoformat()
            )

            # Increment confirmation count
            pattern = self.db.get_ad_pattern_by_id(pattern_id)
            if pattern:
                new_count = pattern.get('confirmation_count', 0) + 1
                self.db.update_ad_pattern(pattern_id, confirmation_count=new_count)

            # Check for promotion
            new_scope = self.check_for_promotion(pattern_id)
            if new_scope:
                self.promote_pattern(pattern_id, new_scope)

        except Exception as e:
            logger.error(f"Failed to record pattern match: {e}")

    def update_podcast_metadata(
        self,
        podcast_id: str,
        feed_url: str,
        feed_content: str = None,
        feed_title: str = None,
        feed_description: str = None,
        feed_author: str = None
    ) -> Dict[str, Optional[str]]:
        """
        Detect and store DAI platform and network for a podcast.

        Args:
            podcast_id: Podcast slug/ID
            feed_url: RSS feed URL
            feed_content: Raw feed XML
            feed_title: Feed title
            feed_description: Feed description
            feed_author: Feed author

        Returns:
            Dict with detected 'dai_platform' and 'network_id'
        """
        result = {
            'dai_platform': None,
            'network_id': None
        }

        # Detect DAI platform
        dai_platform = self.detect_dai_platform(feed_url, feed_content)
        if dai_platform:
            result['dai_platform'] = dai_platform

        # Detect network
        network_id = self.detect_network(
            feed_url, feed_title, feed_description, feed_author
        )
        if network_id:
            result['network_id'] = network_id

        # Update podcast in database
        if self.db and (dai_platform or network_id):
            try:
                self.db.update_podcast(
                    podcast_id,
                    dai_platform=dai_platform,
                    network_id=network_id
                )
                logger.info(
                    f"Updated podcast {podcast_id}: "
                    f"platform={dai_platform}, network={network_id}"
                )
            except Exception as e:
                logger.error(f"Failed to update podcast metadata: {e}")

        return result

    def check_sponsor_global_promotion(self, sponsor: str) -> bool:
        """
        Check if a sponsor appears in 3+ podcasts, warranting global promotion.

        Args:
            sponsor: The sponsor name to check

        Returns:
            True if sponsor qualifies for global promotion
        """
        if not self.db or not sponsor:
            return False

        try:
            # Get all podcast-scoped patterns for this sponsor
            all_patterns = self.db.get_ad_patterns(scope='podcast')
            sponsor_lower = sponsor.lower()

            # Count unique podcasts with this sponsor
            podcasts_with_sponsor = set()
            for pattern in all_patterns:
                pattern_sponsor = pattern.get('sponsor', '')
                if pattern_sponsor and pattern_sponsor.lower() == sponsor_lower:
                    podcast_id = pattern.get('podcast_id')
                    if podcast_id:
                        podcasts_with_sponsor.add(podcast_id)

            count = len(podcasts_with_sponsor)
            if count >= SPONSOR_GLOBAL_THRESHOLD:
                logger.info(
                    f"Sponsor '{sponsor}' found in {count} podcasts, "
                    f"qualifies for global promotion"
                )
                return True

            return False

        except Exception as e:
            logger.error(f"Error checking sponsor global promotion: {e}")
            return False

    def auto_promote_sponsor_patterns(self, sponsor: str) -> int:
        """
        Automatically promote all patterns for a sponsor to global scope.

        Called when sponsor appears in 3+ podcasts.

        Args:
            sponsor: The sponsor name

        Returns:
            Number of patterns promoted
        """
        if not self.db or not sponsor:
            return 0

        try:
            # Check if this sponsor already has global patterns
            global_patterns = self.db.get_ad_patterns(scope='global')
            sponsor_lower = sponsor.lower()

            for pattern in global_patterns:
                pattern_sponsor = pattern.get('sponsor', '')
                if pattern_sponsor and pattern_sponsor.lower() == sponsor_lower:
                    logger.debug(f"Sponsor '{sponsor}' already has global patterns")
                    return 0

            # Get all podcast-scoped patterns for this sponsor
            all_patterns = self.db.get_ad_patterns(scope='podcast')
            patterns_to_promote = []

            for pattern in all_patterns:
                pattern_sponsor = pattern.get('sponsor', '')
                if pattern_sponsor and pattern_sponsor.lower() == sponsor_lower:
                    patterns_to_promote.append(pattern)

            if not patterns_to_promote:
                return 0

            # Find the pattern with highest confirmation count to use as template
            best_pattern = max(
                patterns_to_promote,
                key=lambda p: p.get('confirmation_count', 0)
            )

            # Create new global pattern
            global_id = self.db.create_ad_pattern(
                scope='global',
                text_template=best_pattern.get('text_template'),
                sponsor=sponsor,
                intro_variants=best_pattern.get('intro_variants', []),
                outro_variants=best_pattern.get('outro_variants', [])
            )

            if global_id:
                # Sum all confirmation counts
                total_confirmations = sum(
                    p.get('confirmation_count', 0) for p in patterns_to_promote
                )
                self.db.update_ad_pattern(
                    global_id,
                    confirmation_count=total_confirmations
                )

                logger.info(
                    f"Created global pattern {global_id} for sponsor '{sponsor}' "
                    f"(from {len(patterns_to_promote)} podcast patterns)"
                )

                # Log the promotion
                self.db.create_pattern_correction(
                    pattern_id=global_id,
                    correction_type='auto_promotion',
                    text_snippet=f"Auto-created global pattern for sponsor '{sponsor}' "
                                 f"appearing in {len(patterns_to_promote)} podcasts"
                )

                return 1

            return 0

        except Exception as e:
            logger.error(f"Error promoting sponsor patterns: {e}")
            return 0
