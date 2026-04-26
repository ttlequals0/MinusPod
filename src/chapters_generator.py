"""JSON chapters generator for Podcasting 2.0 support."""
import logging
import re
from typing import List, Dict, Optional, Tuple

from config import DEFAULT_CHAPTERS_MODEL as _DEFAULT_CHAPTERS_MODEL
from utils.time import parse_timestamp, adjust_timestamp
from utils.text import extract_text_from_segments
from llm_client import (
    get_llm_client, get_api_key, LLMClient,
    APIError, RateLimitError,
    get_llm_timeout, get_effective_provider,
    PROVIDER_ANTHROPIC,
)

logger = logging.getLogger(__name__)

# Minimum chapter duration in seconds (3 minutes)
MIN_CHAPTER_DURATION = 180.0

# Episodes shorter than this skip AI topic detection entirely.
MIN_DURATION_FOR_AI = 900.0

# Two chapters whose start times are closer than this are merged during dedupe.
MIN_DEDUP_WINDOW = 60.0

# Topic-detection LLM temperature. Low value keeps boundary choices reproducible
# across reruns of the same transcript (title generation uses its own temperature).
TOPIC_DETECTION_TEMPERATURE = 0.1

# Patterns for MM:SS timestamps embedded in episode descriptions.
_TIMESTAMP_PATTERNS = (
    re.compile(r'(?:^|\n)\s*(\d{1,2}:\d{2}(?::\d{2})?)\s*[-:]*\s*(.+?)(?=\n|$)'),
    re.compile(r'\[(\d{1,2}:\d{2}(?::\d{2})?)\]\s*(.+?)(?=\n|$)'),
    re.compile(r'\((\d{1,2}:\d{2}(?::\d{2})?)\)\s*(.+?)(?=\n|$)'),
)


def _strip_html(text: str) -> str:
    """Convert simple HTML to plain text for show-note timestamp parsing.

    Block-level tags must be turned into newlines (not just stripped) so the
    downstream `_TIMESTAMP_PATTERNS` regex sees each timestamp on its own line.
    A bare tag-stripper like nh3 would collapse `<p>00:00 A</p><p>05:30 B</p>`
    into `00:00 A05:30 B` and miss every anchor after the first.
    """
    if not text:
        return ""
    text = re.sub(r'<br\s*/?>', '\n', text, flags=re.IGNORECASE)
    text = re.sub(r'</(p|li|div)>', '\n', text, flags=re.IGNORECASE)
    text = re.sub(r'<[^>]+>', '', text)
    for entity, char in (('&amp;', '&'), ('&lt;', '<'), ('&gt;', '>'),
                         ('&quot;', '"'), ('&#39;', "'"), ('&nbsp;', ' ')):
        text = text.replace(entity, char)
    text = re.sub(r'[ \t]+', ' ', text)
    return text.strip()


def _parse_description_anchors(description: str) -> List[Tuple[str, str]]:
    """Extract (timestamp, title) pairs from an episode description.

    Returns deduplicated, sorted-by-time anchors. Used as soft hints in the
    topic-detection prompt; the LLM still chooses whether to honor them.
    """
    if not description:
        return []
    text = _strip_html(description)
    seen: Dict[str, str] = {}
    for pattern in _TIMESTAMP_PATTERNS:
        for ts, title in pattern.findall(text):
            title = title.strip().rstrip('-:').strip()
            if not title or len(title) < 2 or title.isdigit():
                continue
            seen.setdefault(ts, title)
    return sorted(seen.items(), key=lambda kv: parse_timestamp(kv[0]))

# Default model for chapter generation tasks (titles, topic detection, splitting).
# Uses Haiku for cost efficiency -- these are simple classification/generation tasks.
CHAPTERS_MODEL = _DEFAULT_CHAPTERS_MODEL


def get_chapters_model() -> str:
    """Get configured chapters model from database or fall back to default."""
    try:
        from database import Database
        db = Database()

        model = db.get_setting('chapters_model')
        if model:
            return model

        # Provider-aware fallback: use the primary detection model for non-Anthropic providers
        # (Ollama doesn't have Anthropic model names like claude-haiku-4-5-20251001)
        provider = get_effective_provider()
        if provider != PROVIDER_ANTHROPIC:
            primary_model = db.get_setting('claude_model')
            if primary_model:
                return primary_model
    except Exception as e:
        logger.warning(f"Could not load chapters model from DB: {e}")

    return CHAPTERS_MODEL


class ChaptersGenerator:
    """Generate JSON chapters from episode content."""

    def __init__(self, api_key: str = None):
        """Initialize the chapters generator.

        Args:
            api_key: LLM API key (defaults to environment configuration)
        """
        self.api_key = api_key or get_api_key()
        self._llm_client_override: Optional[LLMClient] = None

    @property
    def _llm_client(self) -> Optional[LLMClient]:
        """Current LLM client. Reads through ``get_llm_client`` on every access
        so that provider/base-URL changes via the settings API take effect
        immediately without restarting the worker."""
        if self._llm_client_override is not None:
            return self._llm_client_override
        if not self.api_key:
            return None
        return get_llm_client()

    @_llm_client.setter
    def _llm_client(self, value: Optional[LLMClient]) -> None:
        self._llm_client_override = value

    def _initialize_client(self):
        """Surface LLM client init errors before a generation run."""
        if not self.api_key:
            return
        try:
            client = get_llm_client()
            logger.debug(f"LLM client initialized for chapters generator: {client.get_provider_name()}")
        except Exception as e:
            logger.error(f"Failed to initialize LLM client: {e}")

    def _get_full_transcript_range(
        self,
        segments: List[Dict],
        start_time: float,
        end_time: float
    ) -> str:
        """Get full transcript text for a time range with timestamps."""
        lines = []
        for segment in segments:
            seg_start = segment.get('start', 0)
            seg_end = segment.get('end', 0)

            if seg_end < start_time:
                continue
            if seg_start > end_time:
                break

            text = segment.get('text', '').strip()
            if text:
                mins = int(seg_start // 60)
                secs = int(seg_start % 60)
                lines.append(f"[{mins:02d}:{secs:02d}] {text}")

        return '\n'.join(lines)

    def _detect_topic_boundaries(
        self,
        transcript: str,
        start_time: float,
        end_time: float,
        num_splits: int,
        episode_description: str = None,
    ) -> List[Dict]:
        """Use the LLM to detect topic boundaries in a transcript range.

        Returns list of {'original_time': float, 'title': str}.
        """
        description_block = ""
        if episode_description and episode_description.strip():
            anchors = _parse_description_anchors(episode_description)
            if anchors:
                anchor_lines = '\n'.join(f"{ts} {title}" for ts, title in anchors)
                description_block = (
                    "\n\nThese candidate boundaries were extracted from the episode "
                    "show notes. Prefer these timestamps when the transcript "
                    "supports them. Drop any candidate that doesn't match the "
                    "discussion. Add your own boundaries only when a major "
                    "transition is missing from the candidates.\n\n"
                    f"Candidate boundaries from show notes:\n{anchor_lines}"
                )
            else:
                description_block = (
                    "\n\nIf the episode description below contains explicit "
                    "timestamp markers in MM:SS or H:MM:SS form, prefer those "
                    "timestamps and titles over inferring your own. Otherwise "
                    "identify topic transitions from the transcript.\n\n"
                    f"Episode description:\n{episode_description}"
                )

        prompt = f"""Analyze this podcast transcript segment and identify {num_splits} major topic changes.

The segment runs from {int(start_time/60)}:{int(start_time%60):02d} to {int(end_time//60)}:{int(end_time%60):02d}.

For each topic change, provide the timestamp (from the [MM:SS] markers) and a short title (3-7 words).

OUTPUT FORMAT:
Return ONLY topic lines, one per line. No introduction, no explanation, no numbering.
Each line must be exactly: MM:SS Topic Title Here

Example:
05:30 Discussion of AI Trends
12:45 New Product Announcements

Only include clear topic transitions, not minor tangents. Skip the very beginning since that's already a chapter.{description_block}

Transcript:
{transcript}"""

        try:
            response = self._llm_client.messages_create(
                model=get_chapters_model(),
                max_tokens=300,
                system="",
                temperature=TOPIC_DETECTION_TEMPERATURE,
                messages=[{"role": "user", "content": prompt}],
                timeout=get_llm_timeout()
            )

            result_text = response.content.strip()
            logger.info(f"LLM topic detection response ({len(result_text)} chars):\n{result_text}")
            chapters = []

            for line in result_text.split('\n'):
                line = line.strip()
                if not line:
                    continue

                if line.lower().startswith(('here', 'based', 'the ', 'i ', 'these')):
                    logger.debug(f"Skipping preamble: {line[:50]}")
                    continue

                cleaned = re.sub(r'^[\d]+[.)]\s*', '', line)
                cleaned = re.sub(r'^[-*]+\s*', '', cleaned)
                cleaned = cleaned.strip()

                match = re.match(r'^(\d{1,2}:\d{2}(?::\d{2})?)\s*[-:]?\s*(.+)$', cleaned)
                if match:
                    timestamp_str, title = match.groups()
                    try:
                        seconds = parse_timestamp(timestamp_str)
                        if start_time <= seconds < end_time:
                            chapters.append({
                                'original_time': seconds,
                                'title': title.strip()
                            })
                            logger.info(f"Accepted topic: {timestamp_str} ({seconds}s) - {title.strip()}")
                        else:
                            logger.info(f"Rejected outside range: {timestamp_str} ({seconds}s) not in {start_time}-{end_time}")
                    except (ValueError, IndexError) as e:
                        logger.warning(f"Failed to parse timestamp {timestamp_str}: {e}")
                else:
                    logger.info(f"Line didn't match pattern: {cleaned[:80]}")

            logger.info(f"AI detected {len(chapters)} topic boundaries")
            return chapters

        except Exception as e:
            logger.error(f"Failed to detect topic boundaries: {e}")
            return []

    def get_transcript_excerpt(
        self,
        segments: List[Dict],
        start_time: float,
        end_time: float,
        max_words: int = 300
    ) -> str:
        """Get transcript excerpt for a time range."""
        return extract_text_from_segments(segments, start_time, end_time, max_words)

    def generate_chapter_titles(
        self,
        chapters: List[Dict],
        segments: List[Dict],
        podcast_name: str,
        episode_title: str,
    ) -> List[Dict]:
        """Generate titles for chapters that need them.

        Chapters and segments share the same post-ad-removal timeline, so the
        chapter startTime is used directly for transcript lookup.
        """
        chapters_needing_titles = [
            (i, ch) for i, ch in enumerate(chapters)
            if ch.get('needs_title', False) and ch.get('title') is None
        ]

        if not chapters_needing_titles:
            return chapters

        self._initialize_client()
        if not self._llm_client:
            logger.warning("LLM client not available, using generic titles")
            return self._apply_generic_titles(chapters)

        chapter_requests = []
        for idx, chapter in chapters_needing_titles:
            start_time = chapter['startTime']
            if idx + 1 < len(chapters):
                end_time = chapters[idx + 1]['startTime']
            else:
                end_time = start_time + 600

            excerpt = self.get_transcript_excerpt(segments, start_time, end_time)

            chapter_requests.append({
                'index': idx,
                'excerpt': excerpt,
                'position': 'start' if idx == 0 else ('end' if idx == len(chapters) - 1 else 'middle')
            })

        try:
            titles = self._call_claude_for_titles(
                chapter_requests, podcast_name, episode_title
            )

            for req, title in zip(chapter_requests, titles):
                chapters[req['index']]['title'] = title
                chapters[req['index']]['needs_title'] = False

        except Exception as e:
            logger.error(f"Failed to generate chapter titles: {e}")
            return self._apply_generic_titles(chapters)

        return chapters

    def _call_claude_for_titles(
        self,
        chapter_requests: List[Dict],
        podcast_name: str,
        episode_title: str
    ) -> List[str]:
        """Call the LLM to generate chapter titles in one batched request."""
        prompt_parts = [
            f"Generate short, descriptive chapter titles (3-8 words each) for a podcast episode.",
            f"",
            f"Podcast: {podcast_name}",
            f"Episode: {episode_title}",
            f"",
            f"For each chapter below, provide ONLY the title on a single line.",
            f"Use active voice when possible.",
            f"No punctuation at end of titles.",
            f"If it's clearly an introduction, 'Introduction' is fine.",
            f"If it's clearly a conclusion, 'Closing Thoughts' or similar is fine.",
            f"",
        ]

        for i, req in enumerate(chapter_requests):
            position_hint = ""
            if req['position'] == 'start':
                position_hint = " (beginning of episode)"
            elif req['position'] == 'end':
                position_hint = " (end of episode)"

            prompt_parts.append(f"Chapter {i + 1}{position_hint}:")
            prompt_parts.append(f"Transcript excerpt: {req['excerpt'][:500]}...")
            prompt_parts.append("")

        prompt_parts.append(f"Provide exactly {len(chapter_requests)} titles, one per line:")

        prompt = "\n".join(prompt_parts)

        try:
            response = self._llm_client.messages_create(
                model=get_chapters_model(),
                max_tokens=500,
                system="",
                temperature=0.3,
                messages=[{"role": "user", "content": prompt}],
                timeout=get_llm_timeout()
            )

            response_text = response.content.strip()
            titles = [line.strip() for line in response_text.split('\n') if line.strip()]

            while len(titles) < len(chapter_requests):
                titles.append(f"Part {len(titles) + 1}")

            return titles[:len(chapter_requests)]

        except RateLimitError:
            logger.warning("Rate limited generating chapter titles, using generic")
            raise
        except APIError as e:
            logger.error(f"API error generating chapter titles: {e}")
            raise

    def _apply_generic_titles(self, chapters: List[Dict]) -> List[Dict]:
        """Apply generic titles to chapters that need them."""
        part_num = 1
        for chapter in chapters:
            if chapter.get('needs_title', False) and chapter.get('title') is None:
                if chapter['startTime'] < 60:
                    chapter['title'] = 'Introduction'
                else:
                    chapter['title'] = f'Part {part_num}'
                    part_num += 1
                chapter['needs_title'] = False

        return chapters

    def _enforce_min_duration(
        self,
        chapters: List[Dict],
        episode_duration: float,
    ) -> List[Dict]:
        """Drop chapters shorter than MIN_CHAPTER_DURATION by absorbing into the previous.

        Assumes chapters and episode_duration are already on the post-ad-removal
        timeline. First chapter is always retained.
        """
        if len(chapters) <= 1:
            return chapters

        result = [chapters[0]]

        for i in range(1, len(chapters)):
            chapter = chapters[i]
            prev = result[-1]

            if i + 1 < len(chapters):
                chapter_duration = chapters[i + 1]['startTime'] - chapter['startTime']
            else:
                chapter_duration = episode_duration - chapter['startTime']

            if chapter_duration < MIN_CHAPTER_DURATION:
                if chapter.get('title') and not prev.get('title'):
                    prev['title'] = chapter['title']
                    prev['needs_title'] = False
                logger.info(
                    f"Removing short chapter at {chapter['startTime']:.0f}s "
                    f"({chapter_duration:.0f}s < {MIN_CHAPTER_DURATION:.0f}s min): "
                    f"'{chapter.get('title', 'untitled')}'"
                )
            else:
                result.append(chapter)

        if len(result) < len(chapters):
            logger.info(
                f"Chapter duration enforcement: {len(chapters)} -> {len(result)} chapters"
            )

        return result

    def _adjust_segments_for_ads(
        self,
        segments: List[Dict],
        ads_removed: List[Dict],
    ) -> List[Dict]:
        """Project raw segments onto the post-ad-removal timeline.

        Drops segments that fall entirely inside an ad span. Shifts the
        remaining segment start/end times back by the cumulative duration of
        earlier ads via utils.time.adjust_timestamp.
        """
        if not ads_removed:
            return segments

        sorted_ads = sorted(ads_removed, key=lambda a: a.get('start', 0))
        adjusted = []
        for seg in segments:
            start = seg.get('start', 0)
            end = seg.get('end', start)
            if any(ad.get('start', 0) <= start and end <= ad.get('end', 0) for ad in sorted_ads):
                continue
            adjusted.append({
                **seg,
                'start': adjust_timestamp(start, sorted_ads),
                'end': adjust_timestamp(end, sorted_ads),
            })
        return adjusted

    def generate_chapters(
        self,
        segments: List[Dict],
        episode_description: str = None,
        ads_removed: List[Dict] = None,
        podcast_name: str = "Unknown",
        episode_title: str = "Unknown",
    ) -> Dict:
        """Generate Podcasting 2.0 chapters from transcript segments.

        Shared entry point for the main processing pipeline and the manual
        /regenerate-chapters endpoint.

        Args:
            segments: Transcript segments. Pipeline callers pass raw segments
                plus ads_removed; regen callers pass pre-adjusted VTT segments
                and omit ads_removed.
            episode_description: Optional RSS description; when present it is
                injected into the topic-detection prompt so the model can
                honor curated timestamp markers.
            ads_removed: Optional list of {'start', 'end'} ad spans. When
                provided, segments are projected onto the post-ad-removal
                timeline before detection runs.
            podcast_name: Podcast name (used for title generation).
            episode_title: Episode title (used for title generation).

        Returns:
            {'version': '1.2.0', 'chapters': [{'startTime', 'title'}, ...]}
        """
        logger.info(f"Generating chapters for '{episode_title}'")

        if not segments:
            return {'version': '1.2.0', 'chapters': []}

        if ads_removed:
            segments = self._adjust_segments_for_ads(segments, ads_removed)
            if not segments:
                return {'version': '1.2.0', 'chapters': []}

        episode_duration = segments[-1].get('end', 0)

        chapters = [{
            'startTime': 0,
            'title': None,
            'source': 'auto',
            'needs_title': True,
        }]

        if episode_duration > MIN_DURATION_FOR_AI:
            self._initialize_client()
            if self._llm_client:
                transcript_text = self._get_full_transcript_range(segments, 0, episode_duration)

                if transcript_text and len(transcript_text) > 500:
                    num_splits = min(int(episode_duration / 600), 6)

                    logger.info(
                        f"Requesting {num_splits} topic boundaries from AI for "
                        f"{episode_duration:.0f}s episode"
                    )

                    try:
                        new_chapters = self._detect_topic_boundaries(
                            transcript_text, 0, episode_duration, num_splits,
                            episode_description=episode_description,
                        )

                        for ch in new_chapters:
                            chapters.append({
                                'startTime': ch['original_time'],
                                'title': ch.get('title'),
                                'source': 'ai',
                                'needs_title': not ch.get('title'),
                            })
                    except Exception as e:
                        logger.warning(f"Failed to detect topic boundaries: {e}")

        chapters.sort(key=lambda x: x['startTime'])

        deduplicated = []
        for ch in chapters:
            if not deduplicated or ch['startTime'] - deduplicated[-1]['startTime'] >= MIN_DEDUP_WINDOW:
                deduplicated.append(ch)
        chapters = deduplicated

        chapters = self._enforce_min_duration(chapters, episode_duration)

        chapters = self.generate_chapter_titles(
            chapters, segments, podcast_name, episode_title
        )

        output_chapters = []
        for chapter in chapters:
            output_chapters.append({
                'startTime': max(1, int(round(chapter['startTime']))),
                'title': chapter.get('title', 'Untitled'),
            })

        logger.info(f"Generated {len(output_chapters)} chapters")

        return {
            'version': '1.2.0',
            'chapters': output_chapters,
        }
