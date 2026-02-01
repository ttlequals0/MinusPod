"""
Text Pattern Matcher - TF-IDF and fuzzy matching for ad detection.

Uses TF-IDF vectorization with trigrams for content matching and
RapidFuzz for fuzzy intro/outro phrase detection. This is effective
for host-read ads that follow similar scripts but aren't identical.
"""
import logging
from dataclasses import dataclass
from typing import List, Optional, Dict, Tuple
import json

from utils.text import extract_text_from_segments

logger = logging.getLogger('podcast.textmatch')

# TF-IDF similarity threshold for content matching
TFIDF_THRESHOLD = 0.70

# Fuzzy matching threshold for intro/outro phrases
FUZZY_THRESHOLD = 0.75

# Minimum text length for pattern matching (characters)
MIN_TEXT_LENGTH = 50

# Maximum intro/outro phrase length to check
MAX_PHRASE_LENGTH = 200

# Common ad transition phrases (for detecting multi-sponsor contamination)
AD_TRANSITION_PHRASES = [
    "this episode is brought to you by",
    "this podcast is sponsored by",
    "support for this podcast comes from",
    "and now a word from",
    "brought to you by",
    "this episode is sponsored by",
    "today's episode is brought to you by",
    "today's sponsor is",
    "thanks to",
]

# Base vocabulary for TF-IDF - common terms in podcast ads
# These ensure the vectorizer recognizes ad-related words even without patterns
BASE_AD_VOCABULARY = [
    # Ad transition phrases
    "sponsor", "sponsored", "sponsorship", "brought", "thanks",
    "word", "break", "quick", "moment", "support", "supporters",
    # Call to action
    "promo", "code", "discount", "percent", "off", "free",
    "visit", "go", "check", "try", "sign", "offer", "deal",
    # Common ad phrases
    "mentioned", "today", "show", "episode", "podcast",
    # URLs and domains
    "dot", "com", "org", "net", "slash", "link", "click",
    # Money and value
    "money", "save", "savings", "price", "cost", "value",
    # Product types
    "service", "product", "app", "subscription", "trial",
]


@dataclass
class TextMatch:
    """Represents a text pattern match."""
    pattern_id: int
    start: float
    end: float
    confidence: float
    sponsor: Optional[str] = None
    match_type: str = "content"  # "content", "intro", "outro", "both"


@dataclass
class AdPattern:
    """Represents a learned ad pattern."""
    id: int
    text_template: str
    intro_variants: List[str]
    outro_variants: List[str]
    sponsor: Optional[str]
    scope: str  # "global", "network", "podcast"
    podcast_id: Optional[str] = None
    network_id: Optional[str] = None


class TextPatternMatcher:
    """
    Text-based pattern matching for identifying repeated ad reads.

    Uses multiple strategies:
    1. TF-IDF cosine similarity for overall content matching
    2. RapidFuzz for fuzzy intro/outro phrase detection
    3. Keyword spotting for sponsor names
    """

    def __init__(self, db=None, sponsor_service=None):
        """
        Initialize the text pattern matcher.

        Args:
            db: Database instance for loading patterns
            sponsor_service: SponsorService for sponsor name lookups
        """
        self.db = db
        self.sponsor_service = sponsor_service
        self._vectorizer = None
        self._pattern_vectors = None
        self._patterns: List[AdPattern] = []
        self._initialized = False

    def _ensure_initialized(self):
        """Lazy initialization of TF-IDF vectorizer."""
        if self._initialized:
            return

        try:
            from sklearn.feature_extraction.text import TfidfVectorizer

            # Initialize vectorizer with trigrams for better matching
            self._vectorizer = TfidfVectorizer(
                ngram_range=(1, 3),
                min_df=1,
                stop_words='english',
                lowercase=True
            )
            self._initialized = True

            # Load patterns if database available
            if self.db:
                self._load_patterns()

        except ImportError:
            logger.warning("scikit-learn not available - text pattern matching disabled")
            self._initialized = False

    def is_available(self) -> bool:
        """Check if text pattern matching is available."""
        self._ensure_initialized()
        return self._initialized and self._vectorizer is not None

    def _load_patterns(self):
        """Load ad patterns from database."""
        if not self.db:
            return

        try:
            patterns = self.db.get_ad_patterns(active_only=True)
            self._patterns = []

            for p in patterns:
                # Parse JSON fields
                intro_variants = p.get('intro_variants', '[]')
                if isinstance(intro_variants, str):
                    intro_variants = json.loads(intro_variants)

                outro_variants = p.get('outro_variants', '[]')
                if isinstance(outro_variants, str):
                    outro_variants = json.loads(outro_variants)

                self._patterns.append(AdPattern(
                    id=p['id'],
                    text_template=p.get('text_template', ''),
                    intro_variants=intro_variants or [],
                    outro_variants=outro_variants or [],
                    sponsor=p.get('sponsor'),
                    scope=p.get('scope', 'podcast'),
                    podcast_id=p.get('podcast_id'),
                    network_id=p.get('network_id')
                ))

            # Build TF-IDF vectors for pattern templates
            if self._patterns:
                templates = [p.text_template for p in self._patterns if p.text_template]
                if templates:
                    # Include base vocabulary terms to ensure ad-related words are recognized
                    # even if they don't appear in existing patterns
                    base_text = ' '.join(BASE_AD_VOCABULARY)
                    all_texts = templates + [base_text]
                    self._vectorizer.fit(all_texts)
                    # Now transform only the patterns (not the base vocabulary)
                    self._pattern_vectors = self._vectorizer.transform(templates)
                    logger.info(f"Loaded {len(self._patterns)} text patterns")

        except Exception as e:
            logger.error(f"Failed to load patterns: {e}")

    def reload_patterns(self):
        """Reload patterns from database."""
        self._load_patterns()

    def find_matches(
        self,
        segments: List[Dict],
        podcast_id: str = None,
        network_id: str = None
    ) -> List[TextMatch]:
        """
        Search transcript segments for known ad patterns.

        Args:
            segments: List of transcript segments with 'start', 'end', 'text'
            podcast_id: Optional podcast ID for scope filtering
            network_id: Optional network ID for scope filtering

        Returns:
            List of TextMatch objects for found ads
        """
        if not self.is_available() or not self._patterns:
            return []

        matches = []

        # Build full transcript with segment mapping
        segment_map = []  # [(start_char, end_char, segment_index)]
        full_text = ""

        for i, seg in enumerate(segments):
            start_char = len(full_text)
            text = seg.get('text', '')
            full_text += text + " "
            end_char = len(full_text)
            segment_map.append((start_char, end_char, i))

        if len(full_text.strip()) < MIN_TEXT_LENGTH:
            return []

        # Filter patterns by scope
        applicable_patterns = self._filter_patterns_by_scope(
            podcast_id, network_id
        )

        if not applicable_patterns:
            return []

        # Strategy 1: TF-IDF content matching on sliding windows
        content_matches = self._find_content_matches(
            full_text, segments, segment_map, applicable_patterns
        )
        matches.extend(content_matches)

        # Strategy 2: Fuzzy intro/outro phrase matching
        phrase_matches = self._find_phrase_matches(
            full_text, segments, segment_map, applicable_patterns
        )
        matches.extend(phrase_matches)

        # Merge overlapping matches
        matches = self._merge_matches(matches)

        # Refine boundaries using intro/outro phrases
        matches = self._refine_boundaries(matches, segments, applicable_patterns)

        return matches

    def _filter_patterns_by_scope(
        self,
        podcast_id: str = None,
        network_id: str = None
    ) -> List[AdPattern]:
        """Filter patterns by scope hierarchy.

        Global patterns apply to all podcasts.
        Network patterns apply to podcasts in the same network.
        Podcast patterns apply only to the specific podcast.
        """
        applicable = []

        for pattern in self._patterns:
            if pattern.scope == 'global':
                # Global patterns always apply
                applicable.append(pattern)
            elif pattern.scope == 'network':
                # Network patterns require matching network_id
                if network_id and pattern.network_id == network_id:
                    applicable.append(pattern)
            elif pattern.scope == 'podcast':
                # Podcast patterns require matching podcast_id
                if podcast_id and pattern.podcast_id == podcast_id:
                    applicable.append(pattern)

        return applicable

    def _find_content_matches(
        self,
        full_text: str,
        segments: List[Dict],
        segment_map: List[Tuple],
        patterns: List[AdPattern]
    ) -> List[TextMatch]:
        """Find matches using TF-IDF content similarity."""
        matches = []

        if self._pattern_vectors is None or self._pattern_vectors.shape[0] == 0:
            return matches

        try:
            from sklearn.metrics.pairwise import cosine_similarity
            import numpy as np

            # Slide through transcript in windows
            # ~1500 chars = ~60 seconds of speech at typical podcast pace
            # This captures full-length ads (typically 60-120s)
            window_size = 1500  # characters
            step_size = 500    # ~33% overlap

            for start_pos in range(0, len(full_text) - MIN_TEXT_LENGTH, step_size):
                end_pos = min(start_pos + window_size, len(full_text))
                window_text = full_text[start_pos:end_pos]

                if len(window_text.strip()) < MIN_TEXT_LENGTH:
                    continue

                # Vectorize window
                try:
                    window_vec = self._vectorizer.transform([window_text])
                except Exception:
                    continue

                # Compare against pattern vectors
                similarities = cosine_similarity(window_vec, self._pattern_vectors)[0]

                # Find best match above threshold
                best_idx = np.argmax(similarities)
                best_score = similarities[best_idx]

                # Log potential matches for debugging - helps diagnose why patterns fail
                if best_score >= 0.4:  # Lower threshold to catch near-misses
                    pattern_preview = patterns[best_idx] if best_idx < len(patterns) else None
                    if pattern_preview:
                        pattern_len = len(pattern_preview.text_template) if pattern_preview.text_template else 0
                        logger.debug(
                            f"Pattern match attempt: score={best_score:.3f} "
                            f"threshold={TFIDF_THRESHOLD} pattern_id={pattern_preview.id} "
                            f"sponsor={pattern_preview.sponsor} "
                            f"pattern_len={pattern_len} window_len={len(window_text)}"
                        )

                if best_score >= TFIDF_THRESHOLD:
                    pattern = patterns[best_idx] if best_idx < len(patterns) else None
                    if pattern:
                        logger.info(
                            f"Pattern match found: score={best_score:.2f} "
                            f"pattern_id={pattern.id} sponsor={pattern.sponsor} "
                            f"scope={pattern.scope}"
                        )
                        # Map character positions to timestamps
                        start_time, end_time = self._char_pos_to_time(
                            start_pos, end_pos, segment_map, segments
                        )

                        matches.append(TextMatch(
                            pattern_id=pattern.id,
                            start=start_time,
                            end=end_time,
                            confidence=float(best_score),
                            sponsor=pattern.sponsor,
                            match_type="content"
                        ))

        except ImportError:
            logger.warning("sklearn not available for content matching")
        except Exception as e:
            logger.error(f"Content matching failed: {e}")

        return matches

    def _find_phrase_matches(
        self,
        full_text: str,
        segments: List[Dict],
        segment_map: List[Tuple],
        patterns: List[AdPattern]
    ) -> List[TextMatch]:
        """Find matches using fuzzy intro/outro phrase detection."""
        matches = []

        try:
            from rapidfuzz import fuzz

            full_text_lower = full_text.lower()

            for pattern in patterns:
                # Check intro phrases
                for intro in pattern.intro_variants:
                    if len(intro) < 10:
                        continue

                    intro_lower = intro.lower()

                    # Search for fuzzy matches
                    best_pos, best_score = self._fuzzy_find(
                        full_text_lower, intro_lower
                    )

                    if best_score >= FUZZY_THRESHOLD * 100:
                        # Found intro - estimate ad end based on typical length
                        start_time, _ = self._char_pos_to_time(
                            best_pos, best_pos + len(intro),
                            segment_map, segments
                        )
                        # Estimate 60 second ad length by default
                        end_time = start_time + 60

                        matches.append(TextMatch(
                            pattern_id=pattern.id,
                            start=start_time,
                            end=end_time,
                            confidence=best_score / 100,
                            sponsor=pattern.sponsor,
                            match_type="intro"
                        ))

                # Check outro phrases
                for outro in pattern.outro_variants:
                    if len(outro) < 10:
                        continue

                    outro_lower = outro.lower()

                    best_pos, best_score = self._fuzzy_find(
                        full_text_lower, outro_lower
                    )

                    if best_score >= FUZZY_THRESHOLD * 100:
                        _, end_time = self._char_pos_to_time(
                            best_pos, best_pos + len(outro),
                            segment_map, segments
                        )
                        # Estimate ad started 60 seconds before outro
                        start_time = max(0, end_time - 60)

                        matches.append(TextMatch(
                            pattern_id=pattern.id,
                            start=start_time,
                            end=end_time,
                            confidence=best_score / 100,
                            sponsor=pattern.sponsor,
                            match_type="outro"
                        ))

        except ImportError:
            logger.warning("rapidfuzz not available for phrase matching")
        except Exception as e:
            logger.error(f"Phrase matching failed: {e}")

        return matches

    def _fuzzy_find(self, text: str, pattern: str) -> Tuple[int, float]:
        """
        Find best fuzzy match position for pattern in text.

        Returns:
            Tuple of (position, score)
        """
        try:
            from rapidfuzz import fuzz

            best_pos = 0
            best_score = 0

            # Slide through text looking for best match
            pattern_len = len(pattern)
            for i in range(0, len(text) - pattern_len + 1, 50):  # Step by 50 chars
                window = text[i:i + pattern_len + 50]  # Slight overshoot
                score = fuzz.partial_ratio(pattern, window)
                if score > best_score:
                    best_score = score
                    best_pos = i

            return best_pos, best_score

        except Exception:
            return 0, 0

    def _char_pos_to_time(
        self,
        start_char: int,
        end_char: int,
        segment_map: List[Tuple],
        segments: List[Dict]
    ) -> Tuple[float, float]:
        """Convert character positions to timestamps.

        Maps character positions in concatenated text back to segment timestamps.
        Uses consistent boundary comparison (< for exclusive upper bound).
        """
        start_time = 0.0
        end_time = 0.0

        for seg_start, seg_end, seg_idx in segment_map:
            # Start time: find segment containing start_char
            if seg_start <= start_char < seg_end:
                start_time = segments[seg_idx]['start']

            # End time: find segment containing end_char
            # Use < for consistency with start_char boundary handling
            if seg_start <= end_char < seg_end:
                end_time = segments[seg_idx]['end']
                break
            # Handle case where end_char is exactly at the end of the last segment
            elif end_char == seg_end and seg_idx == len(segments) - 1:
                end_time = segments[seg_idx]['end']
                break

        # Fallback if not found
        if end_time <= start_time and segments:
            end_time = segments[-1]['end']

        return start_time, end_time

    def _merge_matches(self, matches: List[TextMatch]) -> List[TextMatch]:
        """Merge overlapping matches."""
        if not matches:
            return []

        # Sort by start time
        matches.sort(key=lambda m: m.start)

        merged = []
        current = matches[0]

        for match in matches[1:]:
            # Check for overlap (within 5 seconds)
            if match.start <= current.end + 5.0:
                # Merge - keep higher confidence
                current = TextMatch(
                    pattern_id=current.pattern_id if current.confidence >= match.confidence else match.pattern_id,
                    start=min(current.start, match.start),
                    end=max(current.end, match.end),
                    confidence=max(current.confidence, match.confidence),
                    sponsor=current.sponsor or match.sponsor,
                    match_type="both" if current.match_type != match.match_type else current.match_type
                )
            else:
                merged.append(current)
                current = match

        merged.append(current)
        return merged

    def _refine_boundaries(
        self,
        matches: List[TextMatch],
        segments: List[Dict],
        patterns: List[AdPattern]
    ) -> List[TextMatch]:
        """Refine match boundaries using intro/outro phrases."""
        refined = []

        try:
            from rapidfuzz import fuzz

            for match in matches:
                # Find the pattern
                pattern = next(
                    (p for p in patterns if p.id == match.pattern_id),
                    None
                )

                if not pattern:
                    refined.append(match)
                    continue

                new_start = match.start
                new_end = match.end

                # Look for intro phrase near start
                if pattern.intro_variants:
                    # Get text around start
                    start_text = self._get_text_around_time(
                        segments, match.start - 10, match.start + 30
                    ).lower()

                    for intro in pattern.intro_variants:
                        score = fuzz.partial_ratio(intro.lower(), start_text)
                        if score >= FUZZY_THRESHOLD * 100:
                            # Find exact position
                            for seg in segments:
                                if seg['start'] >= match.start - 10 and seg['start'] <= match.start + 30:
                                    if fuzz.partial_ratio(intro.lower(), seg['text'].lower()) >= 70:
                                        new_start = seg['start']
                                        break
                            break

                # Look for outro phrase near end
                if pattern.outro_variants:
                    end_text = self._get_text_around_time(
                        segments, match.end - 30, match.end + 10
                    ).lower()

                    for outro in pattern.outro_variants:
                        score = fuzz.partial_ratio(outro.lower(), end_text)
                        if score >= FUZZY_THRESHOLD * 100:
                            for seg in segments:
                                if seg['end'] >= match.end - 30 and seg['end'] <= match.end + 10:
                                    if fuzz.partial_ratio(outro.lower(), seg['text'].lower()) >= 70:
                                        new_end = seg['end']
                                        break
                            break

                refined.append(TextMatch(
                    pattern_id=match.pattern_id,
                    start=new_start,
                    end=new_end,
                    confidence=match.confidence,
                    sponsor=match.sponsor,
                    match_type=match.match_type
                ))

        except ImportError:
            return matches
        except Exception as e:
            logger.error(f"Boundary refinement failed: {e}")
            return matches

        return refined

    def _get_text_around_time(
        self,
        segments: List[Dict],
        start: float,
        end: float
    ) -> str:
        """Get transcript text within a time range.

        Delegates to utils.text.extract_text_from_segments.
        """
        return extract_text_from_segments(segments, start, end)

    def create_pattern_from_ad(
        self,
        segments: List[Dict],
        start: float,
        end: float,
        sponsor: str = None,
        scope: str = "podcast",
        podcast_id: str = None,
        network_id: str = None,
        episode_id: str = None
    ) -> Optional[int]:
        """
        Create a new ad pattern from a detected ad segment.

        Args:
            segments: Transcript segments
            start: Ad start time
            end: Ad end time
            sponsor: Sponsor name (optional)
            scope: Pattern scope ("global", "network", "podcast")
            podcast_id: Podcast ID for podcast-scoped patterns
            network_id: Network ID for network-scoped patterns
            episode_id: Episode ID for tracking pattern origin

        Returns:
            Pattern ID if created, None otherwise
        """
        if not self.db:
            return None

        # Validate ad duration - reject contaminated multi-ad spans
        MAX_PATTERN_DURATION = 120  # 2 minutes - longest reasonable single ad read
        duration = end - start
        if duration > MAX_PATTERN_DURATION:
            logger.warning(
                f"Skipping pattern creation: duration {duration:.0f}s exceeds "
                f"max {MAX_PATTERN_DURATION}s (likely multi-ad contamination)"
            )
            return None

        # Extract text for the ad segment
        ad_text = self._get_text_around_time(segments, start, end)

        if len(ad_text) < MIN_TEXT_LENGTH:
            logger.debug("Ad text too short for pattern creation")
            return None

        # Sanity check on extracted text length to catch contaminated patterns
        MAX_PATTERN_CHARS = 3500  # ~230 seconds at 15 chars/sec
        if len(ad_text) > MAX_PATTERN_CHARS:
            logger.warning(
                f"Skipping pattern creation: text length {len(ad_text)} exceeds "
                f"max {MAX_PATTERN_CHARS} chars (likely contaminated with multiple ads)"
            )
            return None

        # Extract intro (first ~50 words)
        words = ad_text.split()
        intro = " ".join(words[:50]) if len(words) > 50 else ""

        # Extract outro (last ~30 words)
        outro = " ".join(words[-30:]) if len(words) > 30 else ""

        # Check for multiple ad transitions (contamination indicator)
        ad_text_lower = ad_text.lower()
        transition_count = sum(1 for phrase in AD_TRANSITION_PHRASES
                               if phrase in ad_text_lower)
        if transition_count > 1:
            logger.warning(
                f"Skipping pattern creation: found {transition_count} ad transitions - "
                f"likely multi-ad contamination"
            )
            return None

        # Validate sponsor appears in intro (if provided)
        if sponsor and intro:
            if sponsor.lower() not in intro.lower():
                logger.warning(
                    f"Skipping pattern creation: sponsor '{sponsor}' not in intro - "
                    f"may be contaminated or misattributed"
                )
                return None

        try:
            pattern_id = self.db.create_ad_pattern(
                scope=scope,
                text_template=ad_text,
                intro_variants=json.dumps([intro]) if intro else "[]",
                outro_variants=json.dumps([outro]) if outro else "[]",
                sponsor=sponsor,
                podcast_id=podcast_id,
                network_id=network_id,
                created_from_episode_id=episode_id
            )

            logger.info(f"Created text pattern {pattern_id} for sponsor: {sponsor}")

            # Reload patterns
            self._load_patterns()

            return pattern_id

        except Exception as e:
            logger.error(f"Failed to create pattern: {e}")
            return None

    def detect_multi_sponsor_pattern(self, pattern: Dict) -> List[str]:
        """Detect if pattern text contains multiple sponsors.

        Scans the text_template for common ad transition phrases that indicate
        sponsor reads. If more than one is found, the pattern is contaminated
        with multiple ads that were incorrectly merged.

        Args:
            pattern: Pattern dict with 'text_template' field

        Returns:
            List of sponsor names found if multiple detected, empty list if single/none
        """
        text = pattern.get('text_template', '').lower()
        if not text:
            return []

        # Find all sponsor transition phrases
        sponsors = []
        for phrase in AD_TRANSITION_PHRASES:
            start = 0
            while True:
                idx = text.find(phrase, start)
                if idx == -1:
                    break
                # Extract ~50 chars after phrase for sponsor name
                after = text[idx + len(phrase):idx + len(phrase) + 50]
                # First word(s) after phrase is likely sponsor
                words = after.strip().split()
                if words:
                    sponsor_candidate = words[0].strip('.,!?:')
                    # Skip common words that aren't sponsors
                    skip_words = {'the', 'our', 'a', 'an', 'and', 'today', 'this'}
                    if sponsor_candidate and sponsor_candidate not in skip_words:
                        if sponsor_candidate not in sponsors:
                            sponsors.append(sponsor_candidate)
                start = idx + 1

        return sponsors if len(sponsors) > 1 else []

    def split_pattern(self, pattern_id: int) -> List[int]:
        """Split a multi-sponsor pattern into separate patterns.

        Detects ad transition phrases in the pattern text and splits at each
        transition point to create individual single-sponsor patterns.
        The original pattern is disabled after successful split.

        Args:
            pattern_id: ID of the pattern to split

        Returns:
            List of new pattern IDs created, empty if no split needed/possible
        """
        if not self.db:
            logger.error("Cannot split pattern: no database connection")
            return []

        pattern = self.db.get_ad_pattern_by_id(pattern_id)
        if not pattern:
            logger.error(f"Pattern {pattern_id} not found")
            return []

        text = pattern.get('text_template', '')
        if not text:
            logger.warning(f"Pattern {pattern_id} has no text_template")
            return []

        text_lower = text.lower()
        new_ids = []

        # Find split points at ad transitions
        split_points = []
        for phrase in AD_TRANSITION_PHRASES:
            idx = text_lower.find(phrase)
            while idx != -1:
                split_points.append(idx)
                idx = text_lower.find(phrase, idx + 1)

        split_points = sorted(set(split_points))

        if len(split_points) < 2:
            logger.info(f"Pattern {pattern_id} doesn't need splitting "
                       f"(only {len(split_points)} transition phrase found)")
            return []

        logger.info(f"Pattern {pattern_id}: found {len(split_points)} ad transitions, "
                   f"splitting into separate patterns")

        # Create new patterns for each segment
        for i, start in enumerate(split_points):
            end = split_points[i + 1] if i + 1 < len(split_points) else len(text)
            segment = text[start:end].strip()

            if len(segment) < MIN_TEXT_LENGTH:
                logger.debug(f"Skipping segment {i}: too short ({len(segment)} chars)")
                continue

            # Extract sponsor from segment
            segment_lower = segment.lower()
            sponsor = None
            for phrase in AD_TRANSITION_PHRASES:
                if phrase in segment_lower:
                    idx = segment_lower.find(phrase)
                    after = segment[idx + len(phrase):idx + len(phrase) + 30]
                    words = after.strip().split()
                    if words:
                        candidate = words[0].strip('.,!?:')
                        skip_words = {'the', 'our', 'a', 'an', 'and', 'today', 'this'}
                        if candidate and candidate not in skip_words:
                            sponsor = candidate.title()
                            break

            # Create intro/outro for new pattern
            words = segment.split()
            intro = " ".join(words[:50]) if len(words) > 50 else ""
            outro = " ".join(words[-30:]) if len(words) > 30 else ""

            try:
                new_id = self.db.create_ad_pattern(
                    scope=pattern.get('scope', 'podcast'),
                    text_template=segment,
                    intro_variants=json.dumps([intro]) if intro else "[]",
                    outro_variants=json.dumps([outro]) if outro else "[]",
                    sponsor=sponsor,
                    podcast_id=pattern.get('podcast_id'),
                    network_id=pattern.get('network_id'),
                    created_from_episode_id=pattern.get('created_from_episode_id')
                )
                if new_id:
                    new_ids.append(new_id)
                    logger.info(f"Created split pattern {new_id} with sponsor '{sponsor}' "
                               f"({len(segment)} chars)")
            except Exception as e:
                logger.error(f"Failed to create split pattern: {e}")

        # Disable original pattern if we created new ones
        if new_ids:
            from datetime import datetime
            self.db.update_ad_pattern(
                pattern_id,
                is_active=0,
                disabled_at=datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ'),
                disabled_reason=f"Split into patterns: {new_ids}"
            )
            logger.info(f"Disabled original pattern {pattern_id}, "
                       f"replaced with {len(new_ids)} split patterns: {new_ids}")

            # Reload patterns
            self._load_patterns()

        return new_ids

    def matches_false_positive(
        self,
        text: str,
        false_positive_texts: List[str],
        threshold: float = 0.75
    ) -> Tuple[bool, float]:
        """Check if text is similar to any false positive.

        Uses TF-IDF cosine similarity to compare candidate text against
        previously rejected segments.

        Args:
            text: Candidate text to check
            false_positive_texts: List of previously rejected segment texts
            threshold: Minimum similarity score to consider a match

        Returns:
            Tuple of (is_match, highest_similarity_score)
        """
        if not text or not false_positive_texts or len(text) < MIN_TEXT_LENGTH:
            return False, 0.0

        try:
            from sklearn.feature_extraction.text import TfidfVectorizer
            from sklearn.metrics.pairwise import cosine_similarity

            # Fit on false positive texts + the candidate
            all_texts = false_positive_texts + [text]
            vectorizer = TfidfVectorizer(ngram_range=(1, 3), stop_words='english')
            vectors = vectorizer.fit_transform(all_texts)

            # Compare candidate (last) against all false positives
            candidate_vec = vectors[-1]
            fp_vectors = vectors[:-1]

            similarities = cosine_similarity(candidate_vec, fp_vectors)[0]
            max_similarity = float(max(similarities)) if len(similarities) > 0 else 0.0

            return max_similarity >= threshold, max_similarity

        except ImportError:
            logger.warning("scikit-learn not available for false positive matching")
            return False, 0.0
        except Exception as e:
            logger.warning(f"False positive matching failed: {e}")
            return False, 0.0
