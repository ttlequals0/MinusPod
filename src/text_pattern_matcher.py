"""
Text Pattern Matcher - TF-IDF and fuzzy matching for ad detection.

Uses TF-IDF vectorization with trigrams for content matching and
RapidFuzz for fuzzy intro/outro phrase detection. This is effective
for host-read ads that follow similar scripts but aren't identical.
"""
import logging
import re
from dataclasses import dataclass, replace
from typing import List, Optional, Dict, Tuple
import json

from config import (
    DEFAULT_AD_DURATION_ESTIMATE, LONG_AD_WARN,
    TFIDF_MATCH_THRESHOLD as TFIDF_THRESHOLD,
    FUZZY_MATCH_THRESHOLD as FUZZY_THRESHOLD,
)
from community_export import count_brand_occurrences, brand_match_candidates, get_sponsor_row_or_stub
from utils.text import extract_text_from_segments
from sponsor_normalize import get_or_create_known_sponsor
from utils.constants import INVALID_SPONSOR_VALUES
from utils.community_tags import UNIVERSAL_TAG
from utils.language import get_pattern_language

logger = logging.getLogger('podcast.textmatch')

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

# Paired boundary scanning
MAX_SCAN_CHARS = 4000                 # ~4 minutes of speech, cap for paired boundary scan

# Emitted matches longer than this (s) are validated against the sponsor brand
# before being kept. The matcher emits the convex hull of every matched
# fragment with no length cap, so a false-early anchor or a chained merge can
# stretch a span minutes past the real ad; a span this long whose audio carries
# no brand mention there is over-cut. Reuses LONG_AD_WARN (longest reasonable
# single read); spans at or under it keep the matcher's existing behavior.
MAX_MATCH_DURATION = LONG_AD_WARN

# Proportional TF-IDF window sizing
WINDOW_SIZES = [500, 1000, 1500, 2500]
WINDOW_SIZE_TOLERANCE = 0.6


def _split_sentences(text: str) -> list:
    """Split text into sentences at sentence-ending punctuation."""
    return [s.strip() for s in re.split(r'(?<=[.!?])\s+', text) if s.strip()]


def _extract_intro_phrase(text: str, min_words: int = 20, max_words: int = 60) -> str:
    """Extract intro phrase ending at a sentence boundary."""
    sentences = _split_sentences(text)
    result_words = 0
    result_sentences = []
    for sentence in sentences:
        words = sentence.split()
        if result_words + len(words) > max_words and result_sentences:
            break
        result_sentences.append(sentence)
        result_words += len(words)
        if result_words >= min_words:
            break
    return " ".join(result_sentences).strip()


def _extract_outro_phrase(text: str, min_words: int = 15, max_words: int = 40) -> str:
    """Extract outro phrase starting at a sentence boundary."""
    sentences = _split_sentences(text)
    result_words = 0
    result_sentences = []
    for sentence in reversed(sentences):
        words = sentence.split()
        if result_words + len(words) > max_words and result_sentences:
            break
        result_sentences.append(sentence)
        result_words += len(words)
        if result_words >= min_words:
            break
    result_sentences.reverse()
    return " ".join(result_sentences).strip()


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
    avg_duration: Optional[float] = None
    sponsor_id: Optional[int] = None
    source: str = 'local'  # "local", "community", "imported"
    source_language: Optional[str] = None  # ISO 639-1 code of the transcript the pattern was learned from (#252)


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
        # id -> row index in _pattern_vectors, so any pattern subset can reuse
        # the load-time vectors without re-running the vectorizer.
        self._pattern_row_index = {}
        self._patterns: List[AdPattern] = []
        self._pattern_buckets = {}
        self._initialized = False
        # sponsor_id -> set of tags; populated alongside _load_patterns.
        self._sponsor_tags: Dict[int, set] = {}

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
                    network_id=p.get('network_id'),
                    avg_duration=p.get('avg_duration'),
                    sponsor_id=p.get('sponsor_id'),
                    source=p.get('source') or 'local',
                    source_language=p.get('source_language'),
                ))

            # Cache sponsor_id -> tags for matcher eligibility checks.
            try:
                tags_map = self.db.get_sponsor_tags_map()
                self._sponsor_tags = {sid: set(tags) for sid, tags in tags_map.items()}
            except Exception as e:
                logger.warning(f"Could not load sponsor tags map: {e}")
                self._sponsor_tags = {}

            # Build TF-IDF vectors for pattern templates
            if self._patterns:
                templates = [p.text_template for p in self._patterns if p.text_template]
                if templates:
                    # Include base vocabulary terms to ensure ad-related words are recognized
                    # even if they don't appear in existing patterns
                    base_text = ' '.join(BASE_AD_VOCABULARY)
                    all_texts = templates + [base_text]
                    if self._vectorizer is None:
                        self._ensure_initialized()
                    if self._vectorizer is not None:
                        self._vectorizer.fit(all_texts)
                        # Now transform only the patterns (not the base vocabulary)
                        self._pattern_vectors = self._vectorizer.transform(templates)
                        # Row i corresponds to the i-th templated pattern; map
                        # id -> row so subsets reuse these vectors (no per-call
                        # re-transform).
                        self._pattern_row_index = {
                            p.id: i for i, p in enumerate(
                                p for p in self._patterns if p.text_template
                            )
                        }
                        logger.info(f"Loaded {len(self._patterns)} text patterns")

                        # Build per-bucket TF-IDF vectors for proportional window matching
                        # Each pattern goes into its single closest bucket only
                        self._pattern_buckets = {}
                        for pattern in self._patterns:
                            if not pattern.text_template:
                                continue
                            tlen = len(pattern.text_template)
                            closest_size = min(WINDOW_SIZES, key=lambda ws: abs(ws - tlen))
                            if abs(tlen - closest_size) <= closest_size * WINDOW_SIZE_TOLERANCE:
                                self._pattern_buckets.setdefault(
                                    closest_size, {'patterns': [], 'vectors': None}
                                )
                                self._pattern_buckets[closest_size]['patterns'].append(pattern)
                        for bucket in self._pattern_buckets.values():
                            bucket_templates = [p.text_template for p in bucket['patterns']]
                            bucket['vectors'] = self._vectorizer.transform(bucket_templates)
                    else:
                        logger.warning("Vectorizer unavailable, patterns loaded without TF-IDF indexing")

        except Exception as e:
            logger.error(f"Failed to load patterns: {e}")

    def find_matches(
        self,
        segments: List[Dict],
        podcast_id: str = None,
        network_id: str = None,
        podcast_tags: Optional[set] = None,
        language: Optional[str] = None,
    ) -> List[TextMatch]:
        """
        Search transcript segments for known ad patterns.

        Args:
            segments: List of transcript segments with 'start', 'end', 'text'
            podcast_id: Optional podcast ID for scope filtering
            network_id: Optional network ID for scope filtering
            podcast_tags: Optional set of tag strings for this podcast.
                Community patterns are filtered out when their sponsor tags
                share no overlap with the podcast tags (unless the sponsor
                or podcast has no tags, or the sponsor carries 'universal').
            language: Optional ISO 639-1 code. Patterns whose source_language
                is set and differs are excluded (#252). Null on the pattern
                is treated as language-agnostic (legacy rows).

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

        # Filter patterns by scope (+ tag eligibility for community patterns)
        applicable_patterns = self._filter_patterns_by_scope(
            podcast_id, network_id, podcast_tags
        )

        # Filter by source_language (#252).
        if language:
            applicable_patterns = [
                p for p in applicable_patterns
                if not getattr(p, 'source_language', None) or p.source_language == language
            ]

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

        # Trim/reject spans that ran past the real ad into show content
        matches = self._constrain_overlong_spans(matches, segments)

        logger.info(
            f"Stage 2 (text pattern) considered {len(applicable_patterns)} patterns "
            f"(of {len(self._patterns)} loaded), matched {len(matches)}"
        )
        return matches

    def _filter_patterns_by_scope(
        self,
        podcast_id: str = None,
        network_id: str = None,
        podcast_tags: Optional[set] = None,
    ) -> List[AdPattern]:
        """Filter patterns by scope hierarchy and (for community) tag eligibility.

        Scope rules:
        - Global patterns apply to all podcasts.
        - Network patterns apply to podcasts in the same network.
        - Podcast patterns apply only to the specific podcast.

        Tag eligibility (community patterns only):
        - Sponsor with 'universal' tag matches everything.
        - Overlap between sponsor tags and podcast tags matches.
        - Either side empty -> match (fallback).
        Local and imported patterns bypass the tag check entirely.
        """
        applicable: List[AdPattern] = []
        podcast_tag_set = set(podcast_tags) if podcast_tags else set()

        for pattern in self._patterns:
            # Scope gate
            if pattern.scope == 'global':
                pass
            elif pattern.scope == 'network':
                if not (network_id and pattern.network_id == network_id):
                    continue
            elif pattern.scope == 'podcast':
                if not (podcast_id and pattern.podcast_id == podcast_id):
                    continue
            else:
                continue

            # Tag eligibility (community patterns only)
            if pattern.source == 'community':
                sponsor_tags = self._sponsor_tags.get(pattern.sponsor_id, set())
                if UNIVERSAL_TAG in sponsor_tags:
                    applicable.append(pattern)
                    continue
                if not sponsor_tags or not podcast_tag_set:
                    applicable.append(pattern)
                    continue
                if sponsor_tags & podcast_tag_set:
                    applicable.append(pattern)
                    continue
                # No overlap -> drop this community pattern
                continue

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

        # Restrict scoring to the scope/tag/language-filtered subset. The
        # buckets and self._pattern_vectors are built from ALL patterns at load
        # time, so scoring against them directly defeats the filter and lets a
        # wrong-scope / wrong-language pattern match (patterns-service-1), and
        # in the fallback the filtered `patterns` list and the all-patterns
        # vector matrix drift out of alignment (patterns-service-2).
        applicable_ids = {p.id for p in patterns}

        try:
            if self._pattern_buckets:
                bucketed_ids = set()
                for window_size, bucket in self._pattern_buckets.items():
                    idxs = [
                        i for i, p in enumerate(bucket['patterns'])
                        if p.id in applicable_ids
                    ]
                    if not idxs:
                        continue
                    sub_patterns = [bucket['patterns'][i] for i in idxs]
                    bucketed_ids.update(p.id for p in sub_patterns)
                    sub_vectors = bucket['vectors'][idxs]
                    step_size = window_size // 3
                    self._score_windows(
                        full_text, segment_map, segments, matches,
                        sub_patterns, sub_vectors,
                        window_size, step_size
                    )
                # Score applicable patterns that fell outside every window bucket
                # (e.g. templates shorter than ~200 chars) so they still get
                # TF-IDF content matching instead of phrase matching only.
                leftover = [
                    p for p in patterns
                    if p.text_template and p.id not in bucketed_ids
                    and p.id in self._pattern_row_index
                ]
                if leftover:
                    # Reuse the vectors computed at load (row-indexed) instead of
                    # re-running the vectorizer on these templates every call.
                    rows = [self._pattern_row_index[p.id] for p in leftover]
                    self._score_windows(
                        full_text, segment_map, segments, matches,
                        leftover, self._pattern_vectors[rows],
                        1500, 500
                    )
            else:
                # Fallback: rebuild aligned vectors for just the applicable
                # patterns so the list and matrix stay in lockstep.
                tmpl_patterns = [p for p in patterns if p.text_template]
                if tmpl_patterns and self._vectorizer is not None:
                    sub_vectors = self._vectorizer.transform(
                        [p.text_template for p in tmpl_patterns]
                    )
                    self._score_windows(
                        full_text, segment_map, segments, matches,
                        tmpl_patterns, sub_vectors,
                        1500, 500
                    )

        except ImportError:
            # ImportError propagates from _score_windows's local sklearn/numpy imports
            logger.warning("sklearn not available for content matching")
        except Exception as e:
            logger.error(f"Content matching failed: {e}")

        return matches

    def _score_windows(self, full_text, segment_map, segments, matches,
                       target_patterns, target_vectors, window_size, step_size):
        """Score sliding windows against a set of pattern vectors."""
        from sklearn.metrics.pairwise import cosine_similarity
        import numpy as np

        for start_pos in range(0, len(full_text) - MIN_TEXT_LENGTH, step_size):
            end_pos = min(start_pos + window_size, len(full_text))
            window_text = full_text[start_pos:end_pos]

            if len(window_text.strip()) < MIN_TEXT_LENGTH:
                continue

            try:
                window_vec = self._vectorizer.transform([window_text])
            except Exception:
                continue

            similarities = cosine_similarity(window_vec, target_vectors)[0]
            best_idx = np.argmax(similarities)
            best_score = similarities[best_idx]

            if best_score >= 0.4:
                pattern_preview = target_patterns[best_idx] if best_idx < len(target_patterns) else None
                if pattern_preview:
                    pattern_len = len(pattern_preview.text_template) if pattern_preview.text_template else 0
                    logger.debug(
                        f"Pattern match attempt: score={best_score:.3f} "
                        f"threshold={TFIDF_THRESHOLD} pattern_id={pattern_preview.id} "
                        f"sponsor={pattern_preview.sponsor} "
                        f"pattern_len={pattern_len} window_len={len(window_text)}"
                    )

            if best_score >= TFIDF_THRESHOLD:
                pattern = target_patterns[best_idx] if best_idx < len(target_patterns) else None
                if pattern:
                    logger.info(
                        f"Pattern match found: score={best_score:.2f} "
                        f"pattern_id={pattern.id} sponsor={pattern.sponsor} "
                        f"scope={pattern.scope}"
                    )
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
            # Optional dependency: importing here lets the except ImportError
            # below degrade gracefully when rapidfuzz is not installed. The
            # actual fuzzy scoring runs inside self._fuzzy_find.
            from rapidfuzz import fuzz  # noqa: F401

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
                        # Found intro - scan for paired outro or estimate from duration
                        start_time, _ = self._char_pos_to_time(
                            best_pos, best_pos + len(intro),
                            segment_map, segments
                        )
                        intro_end_pos = best_pos + len(intro_lower)
                        end_time = self._scan_for_outro(
                            full_text_lower, segment_map, segments, pattern, intro_end_pos
                        ) or self._estimate_end_from_duration(pattern, start_time)

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
                        outro_start_pos = best_pos
                        start_time = self._scan_for_intro(
                            full_text_lower, segment_map, segments, pattern, outro_start_pos
                        ) or self._estimate_start_from_duration(pattern, end_time)

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

    def _scan_for_boundary(self, full_text, segment_map, segments, variants,
                           search_start, search_end, extract_time):
        """Scan a text region for a known phrase variant using fuzzy matching."""
        if not variants:
            return None

        best_time = None
        best_score = 0
        search_region = full_text[search_start:search_end]

        for phrase in variants:
            if len(phrase) < 10:
                continue
            phrase_lower = phrase.lower()
            pos, score = self._fuzzy_find(search_region, phrase_lower)
            if score >= FUZZY_THRESHOLD * 100 and score > best_score:
                time = extract_time(search_start, pos, phrase_lower, segment_map, segments)
                if time is not None:
                    best_time = time
                    best_score = score

        return best_time

    def _scan_for_outro(self, full_text, segment_map, segments, pattern, search_from_pos):
        """Scan forward from intro match for a known outro variant."""
        search_end = min(search_from_pos + MAX_SCAN_CHARS, len(full_text))

        def extract_end_time(region_start, pos, phrase_lower, seg_map, segs):
            abs_pos = region_start + pos + len(phrase_lower)
            _, end_time = self._char_pos_to_time(
                region_start + pos, abs_pos, seg_map, segs
            )
            return end_time

        return self._scan_for_boundary(
            full_text, segment_map, segments, pattern.outro_variants,
            search_from_pos, search_end, extract_end_time
        )

    def _scan_for_intro(self, full_text, segment_map, segments, pattern, search_to_pos):
        """Scan backward from outro match for a known intro variant."""
        search_start = max(0, search_to_pos - MAX_SCAN_CHARS)

        def extract_start_time(region_start, pos, phrase_lower, seg_map, segs):
            abs_pos = region_start + pos
            start_time, _ = self._char_pos_to_time(
                abs_pos, abs_pos + len(phrase_lower), seg_map, segs
            )
            return start_time

        return self._scan_for_boundary(
            full_text, segment_map, segments, pattern.intro_variants,
            search_start, search_to_pos, extract_start_time
        )

    def _estimate_end_from_duration(self, pattern, start_time):
        """Estimate ad end time from pattern's average duration."""
        duration = pattern.avg_duration if pattern.avg_duration is not None else DEFAULT_AD_DURATION_ESTIMATE
        return start_time + duration

    def _estimate_start_from_duration(self, pattern, end_time):
        """Estimate ad start time from pattern's average duration."""
        duration = pattern.avg_duration if pattern.avg_duration is not None else DEFAULT_AD_DURATION_ESTIMATE
        return max(0, end_time - duration)

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
            if seg_start <= end_char < seg_end or end_char == seg_end and seg_idx == len(segments) - 1:
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
            # Merge only matches for the same sponsor within 5s (case-folded;
            # both None counts as same). Merging across sponsors lets one
            # sponsor's bad anchor drag another's span outward and folds a
            # co-located ad behind a single label; merging an unattributed
            # match into a named ad lets brand-free content inherit the sponsor
            # and ride along as ad. Distinct sponsors stay as separate spans.
            same_sponsor = (
                (current.sponsor or '').lower() == (match.sponsor or '').lower()
            )
            if same_sponsor and match.start <= current.end + 5.0:
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

    def _get_sponsor_row(self, sponsor):
        """Look up a known-sponsor row (name + aliases) for brand matching.

        Falls back to a name-only row when there is no DB or no stored sponsor
        so brand matching still works against the bare sponsor string.
        """
        return get_sponsor_row_or_stub(self.db, sponsor)

    def _brand_bearing_bounds(self, segments, start, end, sponsor_row):
        """Return (first_start, last_end) of the segments overlapping
        [start, end] whose text mentions the sponsor brand as a whole word, or
        (None, None) if none do.

        Word-boundary (not substring) matching so a short brand like 'Hims'
        does not false-match content words like 'whims', which would otherwise
        anchor the trim on show content and defeat it. Bounds use min/max so
        the result is correct regardless of segment ordering.
        """
        candidates = brand_match_candidates(sponsor_row)
        if not candidates:
            return None, None
        brand_re = re.compile('|'.join(rf'\b{re.escape(c)}\b' for c in candidates))

        first = None
        last = None
        for seg in segments:
            if seg['end'] <= start or seg['start'] >= end:
                continue
            if brand_re.search(seg.get('text', '').lower()):
                first = seg['start'] if first is None else min(first, seg['start'])
                last = seg['end'] if last is None else max(last, seg['end'])
        return first, last

    def _constrain_overlong_spans(self, matches, segments):
        """Bound spans that ran past the real ad into show content.

        The matcher emits the convex hull of every matched fragment with no
        length cap, so a false-early anchor or a chained merge can stretch a
        span minutes before/after the actual read. A span longer than a single
        ad whose audio carries no brand mention there is over-cut. For each
        match over MAX_MATCH_DURATION:
        - with a sponsor: trim to the brand-bearing region (shrink only); drop
          it entirely if the brand never appears in the span.
        - without a sponsor: drop it. There is no brand to anchor the trim, and
          clamping to a guessed window could cut show content if the real ad is
          not where we guess. A later stage can still catch a real ad here.
        Spans at or under MAX_MATCH_DURATION are returned unchanged.

        Trimming only ever shrinks a span, so it cannot remove more show content
        than the unbounded matcher already did; the residual failure modes all
        err toward leaving ad audio in (the safe direction) rather than cutting
        content:
        - a genuine >MAX_MATCH_DURATION read whose brand is spoken only mid-span
          (not at the edges) loses its brand-free intro/outro from the cut;
        - a real long read whose brand is absent (ASR-garbled, or spoken only
          as an unlisted form) is dropped and left in the episode;
        - an over-long span that chains two same-sponsor reads with show content
          between them keeps that interior content (the trim bounds only the
          edges, it does not split interior gaps).
        """
        constrained = []
        sponsor_rows = {}
        for match in matches:
            if match.end - match.start <= MAX_MATCH_DURATION:
                constrained.append(match)
                continue

            if not match.sponsor:
                logger.info(
                    f"Dropping unattributed text_pattern span "
                    f"{match.start:.1f}-{match.end:.1f}s "
                    f"({match.end - match.start:.0f}s over cap, "
                    f"no sponsor to anchor a trim)"
                )
                continue

            if match.sponsor not in sponsor_rows:
                sponsor_rows[match.sponsor] = self._get_sponsor_row(match.sponsor)
            first, last = self._brand_bearing_bounds(
                segments, match.start, match.end, sponsor_rows[match.sponsor]
            )
            if first is None:
                logger.info(
                    f"Dropping text_pattern span {match.start:.1f}-{match.end:.1f}s: "
                    f"sponsor '{match.sponsor}' absent from "
                    f"{match.end - match.start:.0f}s span (over-cut into content)"
                )
                continue
            new_start = max(match.start, first)
            new_end = min(match.end, last)
            if (new_start, new_end) != (match.start, match.end):
                logger.info(
                    f"Trimming text_pattern span {match.start:.1f}-{match.end:.1f}s -> "
                    f"{new_start:.1f}-{new_end:.1f}s to '{match.sponsor}' brand region"
                )
            constrained.append(replace(match, start=new_start, end=new_end))
        return constrained

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

    # Reuse centralized constant (superset of the old local set)
    INVALID_SPONSORS = INVALID_SPONSOR_VALUES

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

        # Validate sponsor name before creating pattern
        if not sponsor or len(sponsor.strip()) < 2:
            logger.warning(f"Rejecting pattern: invalid sponsor name '{sponsor}'")
            return None

        sponsor_lower = sponsor.lower().strip()
        if sponsor_lower in self.INVALID_SPONSORS:
            logger.warning(f"Rejecting pattern: generic/invalid sponsor '{sponsor}'")
            return None

        # Validate ad duration - reject contaminated multi-ad spans on the
        # upper end, and short spans (< 15 s) on the lower end. Pattern #356
        # (Patreon, 8 s) is the canonical floor false-positive: real sponsor
        # reads almost never fit in under 15 seconds.
        MIN_PATTERN_DURATION = 15  # shortest plausible sponsor read
        MAX_PATTERN_DURATION = 120  # 2 minutes - longest reasonable single ad read
        duration = end - start
        if duration < MIN_PATTERN_DURATION:
            logger.warning(
                f"Skipping pattern creation: duration {duration:.0f}s below "
                f"min {MIN_PATTERN_DURATION}s (likely a fragment or host mention, "
                f"not a sponsor read)"
            )
            return None
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

        # Extract intro and outro at sentence boundaries
        intro = _extract_intro_phrase(ad_text)
        outro = _extract_outro_phrase(ad_text)

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

        # Require the brand to appear at least twice in the ad_text. Real
        # ads repeat the brand (intro + outro at minimum); a single mention
        # is a strong signal of a host name-drop rather than a sponsor
        # read. Pattern #354 (drink-champs Modelo) was the canonical
        # false-positive: host conversation about "the big Modelo?" got
        # passed to record_verification_misses as a missed ad and turned
        # into a podcast-scoped pattern.
        #
        # Counts substring (not word-boundary) so brands that only appear
        # inside a URL still pass ("DeleteMe" inside joindeleteme.com).
        # Counts across name + aliases + whitespace-stripped variants so a
        # sponsor stored as 'statefarm' still scores against a 'State Farm'
        # transcript and vice versa.
        if sponsor:
            sponsor_row = self._get_sponsor_row(sponsor)
            occurrences = count_brand_occurrences(ad_text, sponsor_row)
            if occurrences < 2:
                logger.warning(
                    f"Skipping pattern creation: sponsor '{sponsor}' (with aliases) "
                    f"appears only {occurrences}x in ad_text (need >=2) - likely "
                    f"a host name-drop or verification-pass false positive"
                )
                return None

        try:
            sponsor_id = (
                get_or_create_known_sponsor(self.db, sponsor) if sponsor else None
            )
            pattern_id = self.db.create_ad_pattern(
                scope=scope,
                text_template=ad_text,
                intro_variants=[intro] if intro else [],
                outro_variants=[outro] if outro else [],
                sponsor_id=sponsor_id,
                podcast_id=podcast_id,
                network_id=network_id,
                created_from_episode_id=episode_id,
                duration=duration,
                source_language=get_pattern_language(self.db, slug=podcast_id),
            )

            logger.info(f"Created text pattern {pattern_id} for sponsor: {sponsor}")

            # Reload patterns
            self._load_patterns()

            return pattern_id

        except Exception as e:
            logger.error(f"Failed to create pattern: {e}")
            return None

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
            intro = _extract_intro_phrase(segment)
            outro = _extract_outro_phrase(segment)

            try:
                split_sponsor_id = (
                    get_or_create_known_sponsor(self.db, sponsor) if sponsor else None
                )
                new_id = self.db.create_ad_pattern(
                    scope=pattern.get('scope', 'podcast'),
                    text_template=segment,
                    intro_variants=[intro] if intro else [],
                    outro_variants=[outro] if outro else [],
                    sponsor_id=split_sponsor_id,
                    podcast_id=pattern.get('podcast_id'),
                    network_id=pattern.get('network_id'),
                    created_from_episode_id=pattern.get('created_from_episode_id'),
                    source_language=pattern.get('source_language'),
                )
                if new_id:
                    new_ids.append(new_id)
                    logger.info(f"Created split pattern {new_id} with sponsor '{sponsor}' "
                               f"({len(segment)} chars)")
            except Exception as e:
                logger.error(f"Failed to create split pattern: {e}")

        # Disable original pattern if we created new ones
        if new_ids:
            from utils.time import utc_now_iso
            self.db.update_ad_pattern(
                pattern_id,
                is_active=0,
                disabled_at=utc_now_iso(),
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
