"""
Text Pattern Matcher - TF-IDF and fuzzy matching for ad detection.

Uses TF-IDF vectorization with trigrams for content matching and
RapidFuzz for fuzzy intro/outro phrase detection. This is effective
for host-read ads that follow similar scripts but aren't identical.
"""
import logging
import re
from dataclasses import dataclass, field, replace
import json

from config import (
    DEFAULT_AD_DURATION_ESTIMATE, LONG_AD_WARN,
    TFIDF_MATCH_THRESHOLD as TFIDF_THRESHOLD,
    FUZZY_MATCH_THRESHOLD as FUZZY_THRESHOLD,
    SEGMENT_CATEGORIES,
)
from community_export import (
    count_brand_occurrences, brand_match_candidates, first_brand_occurrence,
    get_sponsor_row_or_stub)
from utils.text import extract_text_from_segments, timed_spans_from_segments
from sponsor_normalize import get_or_create_known_sponsor
from utils.constants import (
    canonical_sponsor,
    INVALID_SPONSOR_VALUES,
    LEARNING_BRAND_ONSET_FRACTION,
    LEARNING_MAX_PATTERN_DURATION,
    LEARNING_MIN_PATTERN_DURATION,
    LEARNING_SPLIT_DURATION_FACTOR,
    sanitize_sponsor_label,
)
from utils.community_tags import UNIVERSAL_TAG
from utils.language import get_pattern_language
from utils.pattern_similarity import similarity, canonicalize_for_dedupe

logger = logging.getLogger('podcast.textmatch')


def is_defined_pattern(pattern: dict) -> bool:
    """Tier-1 trust: user-created or community patterns; auto-learned are not."""
    return (pattern.get('created_by') == 'user'
            or pattern.get('source') == 'community')


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

# Sanity check on ad text / segment length; longer implies contamination with
# multiple ads. ~230s at 15 chars/sec for the single-ad guard in
# create_pattern_from_ad. Also used as the per-segment cap for the manual
# correction paths that call split_template_text.
MAX_PATTERN_CHARS = 3500

# Near-duplicate threshold for learning dedupe in create_pattern_from_ad.
# Applies to canonicalized text (see canonicalize_for_dedupe), not raw text.
LEARNING_DEDUPE_SIMILARITY_THRESHOLD = 0.9

# Words a sponsor-name guess after a transition phrase should never be (the
# first word is usually an article or filler, not the brand).
# Compared case-folded (see _guess_sponsor_from_segment). Credit verbs are
# here because show credits follow the sponsor read, so the word after a
# transition phrase is often "produced" rather than a brand.
_SPONSOR_GUESS_SKIP_WORDS = {
    'the', 'our', 'a', 'an', 'and', 'today', 'this',
    'produced', 'hosted', 'edited', 'written', 'presented',
}

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

# Below this length a phrase is not discriminative on its own: partial_ratio
# scores the best substring alignment, so 20-odd common characters clear the
# base threshold somewhere in any long transcript.
FUZZY_DISCRIMINATIVE_LENGTH = 60


# Shortest variant eligible for fuzzy matching. An exact substring scores 100,
# above every scaled threshold, so a short verbatim-common phrase matched
# anywhere it appeared; local extraction already floors well above this.
MIN_FUZZY_VARIANT_CHARS = 20


def required_fuzzy_score(phrase_len: int) -> float:
    """Score a phrase of this length must reach to count as a match."""
    base = FUZZY_THRESHOLD * 100
    return min(98.0, base + max(0, FUZZY_DISCRIMINATIVE_LENGTH - phrase_len) * 0.5)

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


def _guess_sponsor_from_segment(segment: str) -> str | None:
    """Guess a sponsor name from the first word following a matched ad
    transition phrase in `segment`. Returns None if no phrase matches or the
    following word is filler (an article, "today", etc)."""
    segment_lower = segment.lower()
    for phrase in AD_TRANSITION_PHRASES:
        if phrase in segment_lower:
            idx = segment_lower.find(phrase)
            after = segment[idx + len(phrase):idx + len(phrase) + 30]
            words = after.strip().split()
            if words:
                candidate = words[0].strip('.,!?:')
                # Compare folded: the transcript capitalizes sentence-initial
                # words, so a raw compare let "The" and "Today" through as
                # sponsor names. INVALID_SPONSOR_VALUES is the same vocabulary
                # the detector and pattern creation already reject.
                folded = candidate.lower()
                if (candidate
                        and folded not in _SPONSOR_GUESS_SKIP_WORDS
                        and folded not in INVALID_SPONSOR_VALUES):
                    return candidate.title()
    return None


def find_transition_offsets(text: str) -> list[int]:
    """Character offsets of AD_TRANSITION_PHRASES matches in `text`.

    Overlapping and nested matches (e.g. "brought to you by" inside "this
    episode is brought to you by") collapse to one offset: the earliest, with
    the cluster extended to the longest phrase found there so a nested hit does
    not register as a second boundary.

    Shared by split_template_text and the split-candidates endpoint, so the ad
    editor proposes dividers at exactly the points a split would use.
    """
    text_lower = text.lower()

    hits = []
    for phrase in AD_TRANSITION_PHRASES:
        idx = text_lower.find(phrase)
        while idx != -1:
            hits.append((idx, len(phrase)))
            idx = text_lower.find(phrase, idx + 1)

    # Earliest offset first; longest phrase first among ties, so the first
    # kept hit at a given start position is the one whose span is used to
    # absorb any nested/overlapping hits that follow.
    hits.sort(key=lambda h: (h[0], -h[1]))

    clusters: list[tuple[int, int]] = []
    for offset, length in hits:
        if clusters and offset < clusters[-1][1]:
            start, end = clusters[-1]
            clusters[-1] = (start, max(end, offset + length))
        else:
            clusters.append((offset, offset + length))

    return [start for start, _ in clusters]


def split_template_text(text: str) -> list[dict]:
    """Segment ad template text at AD_TRANSITION_PHRASES boundaries.

    Returns a list of {'text': str, 'sponsor': Optional[str]} segments, one
    per detected sponsor block. Overlapping/nested phrase matches (e.g.
    "brought to you by" inside "this episode is brought to you by") are
    deduped to a single split point: the earliest offset, extending the
    matched span to cover the longest phrase found there so a nested hit
    doesn't register as a second split point.

    Fewer than 2 distinct split points means no split is needed: the whole
    text comes back as a single segment with sponsor=None. Segments below
    MIN_TEXT_LENGTH are dropped (falling back to a single whole-text segment
    if that drops everything).
    """
    split_offsets = find_transition_offsets(text)

    if len(split_offsets) < 2:
        return [{'text': text, 'sponsor': None}]

    segments = []
    for i, offset in enumerate(split_offsets):
        start = 0 if i == 0 else offset
        end = split_offsets[i + 1] if i + 1 < len(split_offsets) else len(text)
        segment_text = text[start:end].strip()

        if len(segment_text) < MIN_TEXT_LENGTH:
            continue

        segments.append({
            'text': segment_text,
            'sponsor': _guess_sponsor_from_segment(segment_text),
        })

    if not segments:
        return [{'text': text, 'sponsor': None}]

    return segments


@dataclass
class TextMatch:
    """Represents a text pattern match."""
    pattern_id: int
    start: float
    end: float
    confidence: float
    sponsor: str | None = None
    match_type: str = "content"  # "content", "intro", "outro", "both"
    # Transcript text the phrase aligned to, quoted in the marker reason.
    matched_text: str | None = None
    # Segment category (#565) inherited from the matched pattern, so a
    # re-match of a pattern learned from e.g. a cross_promo marker carries
    # that category into the detection instead of falling back to 'sponsor'.
    category: str | None = None
    # Tier-1 trust (user-created or community pattern); see is_defined_pattern.
    defined: bool = False
    # pattern_ids of matches merged into this one, for match-credit recording.
    absorbed_ids: list = field(default_factory=list)
    # True when an edge came from duration estimation, not matched text
    # (see _estimate_start/end_from_duration); makes the span advisory.
    span_estimated: bool = False
    # Real matched-text time bounds, for capping label reach when
    # span_estimated is True. None when not applicable.
    text_start: float | None = None
    text_end: float | None = None


@dataclass
class AdPattern:
    """Represents a learned ad pattern."""
    id: int
    text_template: str
    intro_variants: list[str]
    outro_variants: list[str]
    sponsor: str | None
    scope: str  # "global", "network", "podcast"
    podcast_id: str | None = None
    network_id: str | None = None
    avg_duration: float | None = None
    sponsor_id: int | None = None
    source: str = 'local'  # "local", "community", "imported"
    source_language: str | None = None  # ISO 639-1 code of the transcript the pattern was learned from (#252)
    category: str | None = None  # Segment category (#565); None on a legacy/unmigrated row
    created_by: str | None = None  # 'user', 'auto', 'community'; feeds is_defined_pattern

    @property
    def is_defined(self) -> bool:
        return is_defined_pattern({'created_by': self.created_by, 'source': self.source})


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
        self._patterns: list[AdPattern] = []
        self._pattern_buckets = {}
        self._initialized = False
        # sponsor_id -> set of tags; populated alongside _load_patterns.
        self._sponsor_tags: dict[int, set] = {}

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
                    category=p.get('category'),
                    created_by=p.get('created_by'),
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
        segments: list[dict],
        podcast_id: str = None,
        network_id: str = None,
        podcast_tags: set | None = None,
        language: str | None = None,
    ) -> list[TextMatch]:
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
        podcast_tags: set | None = None,
    ) -> list[AdPattern]:
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
        applicable: list[AdPattern] = []
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
        segments: list[dict],
        segment_map: list[tuple],
        patterns: list[AdPattern]
    ) -> list[TextMatch]:
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
        """Score sliding windows against a set of pattern vectors.

        All windows are vectorized in a single batched transform (one call
        instead of one per window). Scores are numerically identical to
        per-window transforms: TfidfVectorizer.transform treats each
        document independently.
        """
        from sklearn.metrics.pairwise import cosine_similarity
        import numpy as np

        def score_row(similarities, start_pos, end_pos, window_len):
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
                        f"pattern_len={pattern_len} window_len={window_len}"
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
                        match_type="content",
                        category=pattern.category,
                        defined=pattern.is_defined,
                    ))

        window_bounds = []
        window_texts = []
        for start_pos in range(0, len(full_text) - MIN_TEXT_LENGTH, step_size):
            end_pos = min(start_pos + window_size, len(full_text))
            window_text = full_text[start_pos:end_pos]

            if len(window_text.strip()) < MIN_TEXT_LENGTH:
                continue

            window_bounds.append((start_pos, end_pos))
            window_texts.append(window_text)

        if not window_texts:
            return

        try:
            window_matrix = self._vectorizer.transform(window_texts)
        except Exception as e:
            # A fitted TfidfVectorizer cannot fail per-document, so a batch
            # failure would hit every window individually too; skip scoring.
            logger.warning(f"Content window vectorization failed: {e}")
            return

        all_similarities = cosine_similarity(window_matrix, target_vectors)
        for i, (start_pos, end_pos) in enumerate(window_bounds):
            score_row(all_similarities[i], start_pos, end_pos,
                      len(window_texts[i]))

    def _find_phrase_matches(
        self,
        full_text: str,
        segments: list[dict],
        segment_map: list[tuple],
        patterns: list[AdPattern]
    ) -> list[TextMatch]:
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
                    if len(intro) < MIN_FUZZY_VARIANT_CHARS:
                        continue

                    intro_lower = intro.lower()

                    # Search for fuzzy matches
                    best_pos, best_score, matched = self._fuzzy_find(
                        full_text_lower, intro_lower
                    )

                    if best_score >= required_fuzzy_score(len(intro_lower)):
                        # Found intro - scan for paired outro or estimate from duration
                        start_time, intro_text_end = self._char_pos_to_time(
                            best_pos, best_pos + len(matched),
                            segment_map, segments
                        )
                        intro_end_pos = best_pos + len(matched)
                        scanned_end = self._scan_for_outro(
                            full_text_lower, segment_map, segments, pattern, intro_end_pos
                        )
                        span_estimated = scanned_end is None
                        end_time = (self._estimate_end_from_duration(pattern, start_time)
                                    if span_estimated else scanned_end)

                        matches.append(TextMatch(
                            pattern_id=pattern.id,
                            start=start_time,
                            end=end_time,
                            confidence=best_score / 100,
                            sponsor=pattern.sponsor,
                            match_type="intro",
                            category=pattern.category,
                            matched_text=matched,
                            defined=pattern.is_defined,
                            span_estimated=span_estimated,
                            text_start=start_time,
                            text_end=intro_text_end if span_estimated else end_time,
                        ))

                # Check outro phrases
                for outro in pattern.outro_variants:
                    if len(outro) < MIN_FUZZY_VARIANT_CHARS:
                        continue

                    outro_lower = outro.lower()

                    best_pos, best_score, matched = self._fuzzy_find(
                        full_text_lower, outro_lower
                    )

                    if best_score >= required_fuzzy_score(len(outro_lower)):
                        outro_text_start, end_time = self._char_pos_to_time(
                            best_pos, best_pos + len(matched),
                            segment_map, segments
                        )
                        outro_start_pos = best_pos
                        scanned_start = self._scan_for_intro(
                            full_text_lower, segment_map, segments, pattern, outro_start_pos
                        )
                        span_estimated = scanned_start is None
                        start_time = (self._estimate_start_from_duration(pattern, end_time)
                                      if span_estimated else scanned_start)

                        matches.append(TextMatch(
                            pattern_id=pattern.id,
                            start=start_time,
                            end=end_time,
                            confidence=best_score / 100,
                            sponsor=pattern.sponsor,
                            match_type="outro",
                            category=pattern.category,
                            matched_text=matched,
                            defined=pattern.is_defined,
                            span_estimated=span_estimated,
                            text_start=outro_text_start if span_estimated else start_time,
                            text_end=end_time,
                        ))

        except ImportError:
            logger.warning("rapidfuzz not available for phrase matching")
        except Exception as e:
            logger.error(f"Phrase matching failed: {e}")

        return matches

    def _fuzzy_find(self, text: str, pattern: str) -> tuple[int, float, str]:
        """
        Find best fuzzy match position for pattern in text.

        Returns:
            Tuple of (aligned position, score, matched text).
        """
        try:
            from rapidfuzz import fuzz

            best_pos = 0
            best_score = 0
            best_window = ''

            # Slide through text looking for best match
            pattern_len = len(pattern)
            for i in range(0, len(text) - pattern_len + 1, 50):  # Step by 50 chars
                window = text[i:i + pattern_len + 50]  # Slight overshoot
                score = fuzz.partial_ratio(pattern, window)
                if score > best_score:
                    best_score = score
                    best_pos = i
                    best_window = window

            if best_window:
                align = fuzz.partial_ratio_alignment(pattern, best_window)
                if align:
                    return (best_pos + align.dest_start, best_score,
                            best_window[align.dest_start:align.dest_end])
                return best_pos, best_score, best_window[:pattern_len]

            return best_pos, best_score, ''

        except Exception:
            return 0, 0, ''

    def _scan_for_boundary(self, full_text, segment_map, segments, variants,
                           search_start, search_end, extract_time):
        """Scan a text region for a known phrase variant using fuzzy matching."""
        if not variants:
            return None

        best_time = None
        best_score = 0
        search_region = full_text[search_start:search_end]

        for phrase in variants:
            if len(phrase) < MIN_FUZZY_VARIANT_CHARS:
                continue
            phrase_lower = phrase.lower()
            pos, score, matched = self._fuzzy_find(search_region, phrase_lower)
            if score >= required_fuzzy_score(len(phrase_lower)) and score > best_score:
                time = extract_time(search_start, pos, matched, segment_map, segments)
                if time is not None:
                    best_time = time
                    best_score = score

        return best_time

    def _scan_for_outro(self, full_text, segment_map, segments, pattern, search_from_pos):
        """Scan forward from intro match for a known outro variant."""
        search_end = min(search_from_pos + MAX_SCAN_CHARS, len(full_text))

        def extract_end_time(region_start, pos, matched, seg_map, segs):
            abs_pos = region_start + pos + len(matched)
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

        def extract_start_time(region_start, pos, matched, seg_map, segs):
            abs_pos = region_start + pos
            start_time, _ = self._char_pos_to_time(
                abs_pos, abs_pos + len(matched), seg_map, segs
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
        segment_map: list[tuple],
        segments: list[dict]
    ) -> tuple[float, float]:
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

    def _merge_matches(self, matches: list[TextMatch]) -> list[TextMatch]:
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
                # Merge - a defined (tier-1) match wins ownership regardless
                # of confidence; otherwise keep higher confidence.
                if current.defined != match.defined:
                    best = current if current.defined else match
                    loser = match if current.defined else current
                else:
                    best = current if current.confidence >= match.confidence else match
                    loser = match if best is current else current
                # Any estimated edge on either side keeps the merged span
                # advisory; label reach is capped to the union of grounded
                # text only (None-safe: an estimated edge has no text bound).
                text_starts = [t for t in (current.text_start, match.text_start) if t is not None]
                text_ends = [t for t in (current.text_end, match.text_end) if t is not None]
                current = replace(
                    best,
                    start=min(current.start, match.start),
                    end=max(current.end, match.end),
                    confidence=max(current.confidence, match.confidence),
                    sponsor=best.sponsor or loser.sponsor,
                    match_type="both" if current.match_type != match.match_type else current.match_type,
                    category=best.category if best.defined else (current.category or match.category),
                    absorbed_ids=current.absorbed_ids + match.absorbed_ids + [loser.pattern_id],
                    span_estimated=current.span_estimated or match.span_estimated,
                    text_start=min(text_starts) if text_starts else None,
                    text_end=max(text_ends) if text_ends else None,
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
        matches: list[TextMatch],
        segments: list[dict],
        patterns: list[AdPattern]
    ) -> list[TextMatch]:
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
                    # Flat threshold on purpose: this searches ~40s around a
                    # match that already cleared required_fuzzy_score, so the
                    # long-transcript alignment problem it guards is absent.
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

                refined.append(replace(match, start=new_start, end=new_end))

        except ImportError:
            return matches
        except Exception as e:
            logger.error(f"Boundary refinement failed: {e}")
            return matches

        return refined

    def _get_text_around_time(
        self,
        segments: list[dict],
        start: float,
        end: float
    ) -> str:
        """Get transcript text within a time range.

        Delegates to utils.text.extract_text_from_segments.
        """
        return extract_text_from_segments(segments, start, end)

    # Reuse centralized constant (superset of the old local set)
    INVALID_SPONSORS = INVALID_SPONSOR_VALUES

    def _pattern_duration_bounds(self) -> tuple[int, int]:
        """Configured [min, max] source-span duration for a learned pattern.

        isinstance rather than int(): a MagicMock db converts to 1 and would
        cap every span at one second, which several fixtures would hit.
        """
        def read(key: str, fallback: int) -> int:
            value = self.db.get_setting_int(key, fallback) if self.db else None
            return int(value) if isinstance(value, int) and not isinstance(value, bool) else fallback

        return (read('learning_min_pattern_duration', LEARNING_MIN_PATTERN_DURATION),
                read('learning_max_pattern_duration', LEARNING_MAX_PATTERN_DURATION))

    def create_patterns_from_ad(
        self,
        segments: list[dict],
        start: float,
        end: float,
        sponsor: str = None,
        scope: str = "podcast",
        podcast_id: str = None,
        network_id: str = None,
        episode_id: str = None,
        category: str = None,
    ) -> list[dict]:
        """Learn from a detected span, splitting it when it holds several ads.

        A span over the ceiling, or with more than one transition phrase, is
        usually back-to-back reads, and dropping it whole taught nothing. Each
        piece goes through create_pattern_from_ad, so every gate still runs.
        Returns one {'id', 'start', 'end'} per pattern, so a caller storing an
        audio fingerprint can key it to the piece the pattern actually covers.
        """
        if not self.db:
            return []

        def create(piece_start, piece_end, piece_sponsor,
                   from_split=False, piece_text=None):
            pattern_id = self.create_pattern_from_ad(
                segments, piece_start, piece_end, sponsor=piece_sponsor,
                scope=scope, podcast_id=podcast_id, network_id=network_id,
                episode_id=episode_id, category=category,
                from_split=from_split, ad_text=piece_text)
            return ([{'id': pattern_id, 'start': piece_start, 'end': piece_end}]
                    if pattern_id else [])

        _, max_duration = self._pattern_duration_bounds()
        ad_text = self._get_text_around_time(segments, start, end)
        if (end - start <= max_duration
                and len(find_transition_offsets(ad_text)) <= 1):
            return create(start, end, sponsor)

        # Local import: split_planning imports this module for its phrase list.
        from split_planning import build_split_candidates, build_split_pieces

        spans = timed_spans_from_segments(segments, start, end)
        times = [c['time'] for c in build_split_candidates(spans, start, end)]
        if not times:
            # Nothing to split on; create_pattern_from_ad logs why it declines.
            return create(start, end, sponsor)

        pieces = build_split_pieces(spans, start, end, times)
        logger.info(
            f"Splitting {end - start:.0f}s span into {len(pieces)} pieces "
            f"for pattern learning"
        )
        created = []
        for index, piece in enumerate(pieces):
            # No divider sits near the span start, so the caller's sponsor names
            # the opening read; giving it to a later piece would mislabel it.
            piece_sponsor = piece['sponsor'] or (sponsor if index == 0 else None)
            if not piece_sponsor:
                logger.debug(
                    f"Split piece {piece['start']:.0f}-{piece['end']:.0f}s has no "
                    f"sponsor of its own; not learned")
                continue
            created.extend(
                create(piece['start'], piece['end'],
                       canonical_sponsor(piece_sponsor),
                       from_split=True, piece_text=piece['text']))
        return created

    def create_pattern_from_ad(
        self,
        segments: list[dict],
        start: float,
        end: float,
        sponsor: str = None,
        scope: str = "podcast",
        podcast_id: str = None,
        network_id: str = None,
        episode_id: str = None,
        category: str = None,
        from_split: bool = False,
        ad_text: str = None
    ) -> int | None:
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
            category: Segment category (#565) the source marker carried.
                Normalized before storage; None stores NULL, which reads
                back as 'sponsor'.

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
        # Segment and show-structure words arrive here as sponsors when the
        # model has no advertiser to name.
        if sanitize_sponsor_label(sponsor) is None:
            logger.warning(
                f"Rejecting pattern: '{sponsor}' is a segment or structure "
                f"name, not an advertiser")
            return None

        # Validate ad duration - reject contaminated multi-ad spans on the
        # upper end, and short spans on the lower end. Pattern #356 (Patreon,
        # 8 s) is the canonical floor false-positive: real sponsor reads almost
        # never fit in under 15 seconds.
        MIN_PATTERN_DURATION, MAX_PATTERN_DURATION = self._pattern_duration_bounds()
        duration = end - start
        if duration < MIN_PATTERN_DURATION:
            logger.warning(
                f"Skipping pattern creation: duration {duration:.0f}s below "
                f"min {MIN_PATTERN_DURATION}s (likely a fragment or host mention, "
                f"not a sponsor read)"
            )
            return None
        # A piece cut at its own ad transitions has passed the contamination
        # screen, so it may run past the ceiling, but only so far: an undetected
        # transition would otherwise store a pattern of any length.
        ceiling = (MAX_PATTERN_DURATION * LEARNING_SPLIT_DURATION_FACTOR
                   if from_split else MAX_PATTERN_DURATION)
        if duration > ceiling:
            logger.warning(
                f"Skipping pattern creation: duration {duration:.0f}s exceeds "
                f"max {ceiling:.0f}s (likely multi-ad contamination)"
            )
            return None

        # A split piece supplies its own text: build_split_pieces cuts on strict
        # overlap and this extractor includes a segment that merely touches the
        # boundary, so re-extracting would pull in the next piece's opening line.
        if ad_text is None:
            ad_text = self._get_text_around_time(segments, start, end)

        if len(ad_text) < MIN_TEXT_LENGTH:
            logger.debug("Ad text too short for pattern creation")
            return None

        # Sanity check on extracted text length to catch contaminated patterns
        if len(ad_text) > MAX_PATTERN_CHARS:
            logger.warning(
                f"Skipping pattern creation: text length {len(ad_text)} exceeds "
                f"max {MAX_PATTERN_CHARS} chars (likely contaminated with multiple ads)"
            )
            return None

        # Extract intro and outro at sentence boundaries
        intro = _extract_intro_phrase(ad_text)
        outro = _extract_outro_phrase(ad_text)

        # Counts positions, not phrase-list entries: "this episode is brought
        # to you by" contains "brought to you by", so the commonest opener in
        # podcasting scored 2 and every read using it was rejected.
        transition_count = len(find_transition_offsets(ad_text))
        if transition_count > 1:
            logger.warning(
                f"Skipping pattern creation: found {transition_count} ad transitions - "
                f"likely multi-ad contamination"
            )
            return None

        # Also covers "never appears at all", which the raw intro substring test
        # used to catch, less reliably: it missed alternate spellings.
        #
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
            # A read names its advertiser early. A brand first appearing in
            # the back half usually means the span opens with a different
            # advertiser's read and the label is misattributed.
            onset = first_brand_occurrence(ad_text, sponsor_row)
            if onset is not None and onset > len(ad_text) * LEARNING_BRAND_ONSET_FRACTION:
                logger.warning(
                    f"Skipping pattern creation: sponsor '{sponsor}' first appears "
                    f"{onset} of {len(ad_text)} chars in - the opening read "
                    f"likely belongs to a different advertiser"
                )
                return None

        # Only a span that passed every validation gate above may credit an
        # existing pattern's confirmation_count, and reuses a near-identical
        # existing pattern instead of inserting a duplicate (#565).
        try:
            existing_patterns = (
                self.db.get_ad_patterns(podcast_id=podcast_id) if podcast_id else []
            )
        except Exception:
            existing_patterns = []
        for existing_pattern in existing_patterns:
            existing_text = existing_pattern.get('text_template') or ''
            if not existing_text:
                continue
            sim = similarity(canonicalize_for_dedupe(ad_text), canonicalize_for_dedupe(existing_text))
            if sim >= LEARNING_DEDUPE_SIMILARITY_THRESHOLD:
                logger.info(
                    f"Near-duplicate of pattern #{existing_pattern['id']} "
                    f"(sim {sim:.2f}); updating stats instead of inserting"
                )
                try:
                    self.db.increment_pattern_match(existing_pattern['id'])
                except Exception as e:
                    logger.warning(f"Failed to record dedupe match: {e}")
                return existing_pattern['id']

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
                category=category if category in SEGMENT_CATEGORIES else None,
            )

            logger.info(f"Created text pattern {pattern_id} for sponsor: {sponsor}")

            # Reload patterns
            self._load_patterns()

            return pattern_id

        except Exception as e:
            logger.error(f"Failed to create pattern: {e}")
            return None

    def split_pattern(self, pattern_id: int) -> list[int]:
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

        new_ids = []
        segments = split_template_text(text)

        if len(segments) < 2:
            logger.info(f"Pattern {pattern_id} doesn't need splitting "
                       f"(only {len(segments)} segment found)")
            return []

        logger.info(f"Pattern {pattern_id}: splitting into {len(segments)} separate patterns")

        # Create new patterns for each segment
        for seg in segments:
            segment = seg['text']
            sponsor = seg['sponsor']

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
        false_positive_texts: list[str],
        threshold: float = 0.75
    ) -> tuple[bool, float]:
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
