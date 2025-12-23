"""
Text Pattern Matcher - TF-IDF and fuzzy matching for ad detection.

Uses TF-IDF vectorization with trigrams for content matching and
RapidFuzz for fuzzy intro/outro phrase detection. This is effective
for host-read ads that follow similar scripts but aren't identical.
"""
import logging
import re
from dataclasses import dataclass
from typing import List, Optional, Dict, Tuple
import json

logger = logging.getLogger('podcast.textmatch')

# TF-IDF similarity threshold for content matching
TFIDF_THRESHOLD = 0.70

# Fuzzy matching threshold for intro/outro phrases
FUZZY_THRESHOLD = 0.75

# Minimum text length for pattern matching (characters)
MIN_TEXT_LENGTH = 50

# Maximum intro/outro phrase length to check
MAX_PHRASE_LENGTH = 200


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
                    scope=p.get('scope', 'podcast')
                ))

            # Build TF-IDF vectors for pattern templates
            if self._patterns:
                templates = [p.text_template for p in self._patterns if p.text_template]
                if templates:
                    self._pattern_vectors = self._vectorizer.fit_transform(templates)
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
        """Filter patterns by scope hierarchy."""
        applicable = []

        for pattern in self._patterns:
            if pattern.scope == 'global':
                applicable.append(pattern)
            elif pattern.scope == 'network' and network_id:
                # Would need to check network_id match
                applicable.append(pattern)
            elif pattern.scope == 'podcast' and podcast_id:
                # Would need to check podcast_id match
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

        if not self._pattern_vectors or self._pattern_vectors.shape[0] == 0:
            return matches

        try:
            from sklearn.metrics.pairwise import cosine_similarity
            import numpy as np

            # Slide through transcript in windows
            window_size = 500  # characters
            step_size = 200

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

                # Log potential matches for debugging
                if best_score >= 0.5:
                    pattern_preview = patterns[best_idx] if best_idx < len(patterns) else None
                    if pattern_preview:
                        logger.debug(
                            f"Pattern match candidate: score={best_score:.2f} "
                            f"pattern_id={pattern_preview.id} "
                            f"sponsor={pattern_preview.sponsor}"
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
        """Convert character positions to timestamps."""
        start_time = 0.0
        end_time = 0.0

        for seg_start, seg_end, seg_idx in segment_map:
            if seg_start <= start_char < seg_end:
                start_time = segments[seg_idx]['start']
            if seg_start <= end_char <= seg_end:
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
        """Get transcript text within a time range."""
        text = ""
        for seg in segments:
            if seg['end'] >= start and seg['start'] <= end:
                text += seg.get('text', '') + " "
        return text.strip()

    def create_pattern_from_ad(
        self,
        segments: List[Dict],
        start: float,
        end: float,
        sponsor: str = None,
        scope: str = "podcast",
        podcast_id: str = None,
        network_id: str = None
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

        Returns:
            Pattern ID if created, None otherwise
        """
        if not self.db:
            return None

        # Extract text for the ad segment
        ad_text = self._get_text_around_time(segments, start, end)

        if len(ad_text) < MIN_TEXT_LENGTH:
            logger.debug("Ad text too short for pattern creation")
            return None

        # Extract intro (first ~50 words)
        words = ad_text.split()
        intro = " ".join(words[:50]) if len(words) > 50 else ""

        # Extract outro (last ~30 words)
        outro = " ".join(words[-30:]) if len(words) > 30 else ""

        try:
            pattern_id = self.db.create_ad_pattern(
                scope=scope,
                text_template=ad_text,
                intro_variants=json.dumps([intro]) if intro else "[]",
                outro_variants=json.dumps([outro]) if outro else "[]",
                sponsor=sponsor,
                podcast_id=podcast_id,
                network_id=network_id
            )

            logger.info(f"Created text pattern {pattern_id} for sponsor: {sponsor}")

            # Reload patterns
            self._load_patterns()

            return pattern_id

        except Exception as e:
            logger.error(f"Failed to create pattern: {e}")
            return None

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
