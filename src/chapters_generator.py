"""JSON chapters generator for Podcasting 2.0 support."""
import logging
import math
import re
from typing import List, Dict, Optional, Tuple

from config import DEFAULT_CHAPTERS_MODEL as _DEFAULT_CHAPTERS_MODEL
from config import (
    normalize_segment_category, resolve_chapter_geometry,
    resolve_stage_tunables,
)
from utils.time import parse_timestamp, adjust_timestamp, span_inside_any_cut
from utils.text import extract_text_from_segments
from llm_capabilities import PASS_CHAPTER_GENERATION
from llm_client import (
    get_llm_client, get_api_key, LLMClient,
    get_llm_timeout, get_llm_max_retries, get_effective_provider,
    PROVIDER_ANTHROPIC,
)
from utils.llm_call import call_llm

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


def _format_mmss(seconds: float) -> str:
    """Format seconds as zero-padded MM:SS, matching the prompt's own
    [MM:SS] transcript markers and example lines."""
    total = max(0, int(round(seconds)))
    return f"{total // 60:02d}:{total % 60:02d}"


def build_segment_hints(markers: Optional[List[Dict]], cuts: Optional[List[Dict]],
                         replacement_duration: float = 0.0) -> List[Dict]:
    """Map applied ad/segment markers onto the processed timeline as
    candidate topic-boundary hints for the topic-detection prompt.

    The processed transcript has no trace of a cut ad, so the topic
    detector loses the signal that a topic change likely happened there;
    this reconstructs it from the same markers and applied-cut list the
    rest of the pipeline uses (via utils.time.adjust_timestamp, so
    positions stay in lockstep with the transcript's own mapping).

    A 'remove' marker becomes a single seam position (where the
    surrounding content now joins); 'keep'/'beep' markers become a
    start/end range, since that time still exists in the processed audio.
    Any other action, including markers still pending review, is skipped.
    Returns [] when there are no markers, so callers can skip the hints
    block entirely and leave the prompt unchanged.
    """
    if not markers:
        return []
    cuts = cuts or []
    hints: List[Dict] = []
    for marker in markers:
        action = marker.get('action_applied')
        if action not in ('remove', 'beep', 'keep'):
            continue
        start = marker.get('start')
        end = marker.get('end')
        if start is None or end is None:
            continue
        category = normalize_segment_category(marker.get('category'))
        if action == 'remove':
            seam = adjust_timestamp(start, cuts, replacement_duration)
            hints.append({'type': 'seam', 'time': seam, 'category': category})
        else:
            mapped_start = adjust_timestamp(start, cuts, replacement_duration)
            mapped_end = adjust_timestamp(end, cuts, replacement_duration)
            if mapped_end <= mapped_start:
                continue
            hints.append({
                'type': 'range', 'start': mapped_start, 'end': mapped_end,
                'category': category,
            })
    hints.sort(key=lambda h: h.get('time', h.get('start', 0.0)))
    return hints


def _format_hints_block(hints: List[Dict]) -> str:
    """Render segment hints into the prompt block explaining what they mean.

    Returns "" when hints is empty, leaving the caller's prompt unchanged."""
    if not hints:
        return ""
    lines = []
    for hint in hints:
        if hint['type'] == 'seam':
            lines.append(f"{_format_mmss(hint['time'])} ad/segment break ({hint['category']})")
        else:
            lines.append(
                f"{_format_mmss(hint['start'])}-{_format_mmss(hint['end'])} "
                f"ad/segment ({hint['category']})"
            )
    hint_lines = '\n'.join(lines)
    return (
        "\n\nThese timestamps mark where an ad break or show segment was "
        "detected in this episode. Podcast ads are usually inserted between "
        "content segments, so a real topic change often falls at one of "
        "these points. Treat them as CANDIDATE boundaries only: use one "
        "only where the transcript itself shows a genuine topic change "
        "there. Do not output a boundary just because it is listed here.\n\n"
        f"Detected ad/segment positions:\n{hint_lines}"
    )


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
        self._episode_id: Optional[str] = None
        # Set when topic detection or title generation fails and the run
        # degrades to a fallback; read after generate_chapters() so a
        # degraded run doesn't look like a normal short episode.
        self._topic_detection_failed: bool = False
        self._title_generation_failed: bool = False
        self.chapters_degraded: bool = False
        self.chapters_degradation_reason: Optional[str] = None

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
        hints: Optional[List[Dict]] = None,
        previous_title: Optional[str] = None,
    ) -> List[Dict]:
        """Use the LLM to detect topic boundaries in a transcript range.

        hints: optional candidate boundaries derived from detected ad/segment
        markers (see build_segment_hints). Empty/None leaves the prompt unchanged.

        previous_title: the title of the chapter this window opens inside, so a
        windowed run does not reopen a topic the previous window just closed.

        Returns list of {'original_time': float, 'title': str}.
        """
        hints_block = _format_hints_block(hints or [])
        continuation_block = (
            f"\n\nThis segment continues the chapter \"{previous_title}\". Do not "
            "emit a boundary for that same topic; only for a change away from it."
            if previous_title else ""
        )
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

Only include clear topic transitions, not minor tangents. Skip the very beginning since that's already a chapter.{continuation_block}{description_block}{hints_block}

Transcript:
{transcript}"""

        try:
            max_tokens, temperature, reasoning = resolve_stage_tunables('chapter_boundary')
            response, last_error = call_llm(
                llm_client=self._llm_client,
                model=get_chapters_model(),
                system_prompt="",
                prompt=prompt,
                llm_timeout=get_llm_timeout(),
                max_retries=get_llm_max_retries(),
                max_tokens=max_tokens,
                temperature=temperature,
                reasoning_effort=reasoning,
                slug=None,
                episode_id=self._episode_id,
                call_label="chapter topic detection",
                pass_name=PASS_CHAPTER_GENERATION,
            )
            if response is None:
                logger.error(f"Failed to detect topic boundaries: {last_error}")
                self._topic_detection_failed = True
                return []

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
            self._topic_detection_failed = True
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
            self._title_generation_failed = True
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
            "Generate short, descriptive chapter titles (3-8 words each) for a podcast episode.",
            "",
            f"Podcast: {podcast_name}",
            f"Episode: {episode_title}",
            "",
            "For each chapter below, provide ONLY the title on a single line.",
            "Use active voice when possible.",
            "No punctuation at end of titles.",
            "If it's clearly an introduction, 'Introduction' is fine.",
            "If it's clearly a conclusion, 'Closing Thoughts' or similar is fine.",
            "",
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

        max_tokens, temperature, reasoning = resolve_stage_tunables('chapter_title')
        response, last_error = call_llm(
            llm_client=self._llm_client,
            model=get_chapters_model(),
            system_prompt="",
            prompt=prompt,
            llm_timeout=get_llm_timeout(),
            max_retries=get_llm_max_retries(),
            max_tokens=max_tokens,
            temperature=temperature,
            reasoning_effort=reasoning,
            slug=None,
            episode_id=self._episode_id,
            call_label="chapter title generation",
            pass_name=PASS_CHAPTER_GENERATION,
        )
        if response is None:
            # Caller (generate_chapter_titles) catches this and degrades to
            # generic titles; retries/classification/webhooks already ran
            # inside call_llm.
            raise last_error

        response_text = response.content.strip()
        titles = [line.strip() for line in response_text.split('\n') if line.strip()]

        while len(titles) < len(chapter_requests):
            titles.append(f"Part {len(titles) + 1}")

        return titles[:len(chapter_requests)]

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
        min_duration: float = MIN_CHAPTER_DURATION,
    ) -> List[Dict]:
        """Drop chapters shorter than min_duration by absorbing into the previous.

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

            if chapter_duration < min_duration:
                if chapter.get('title') and not prev.get('title'):
                    prev['title'] = chapter['title']
                    prev['needs_title'] = False
                logger.info(
                    f"Removing short chapter at {chapter['startTime']:.0f}s "
                    f"({chapter_duration:.0f}s < {min_duration:.0f}s min): "
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
        replacement_duration: float = 0.0,
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
            if span_inside_any_cut(start, end, sorted_ads):
                continue
            adjusted.append({
                **seg,
                'start': adjust_timestamp(start, sorted_ads, replacement_duration),
                'end': adjust_timestamp(end, sorted_ads, replacement_duration),
            })
        return adjusted

    def _detect_boundaries_windowed(
        self,
        segments: List[Dict],
        episode_duration: float,
        episode_description: str = None,
        hints: Optional[List[Dict]] = None,
    ) -> List[Dict]:
        """Detect topic boundaries a window at a time, earliest first.

        One call over a multi-hour transcript both caps the count and clusters
        the boundaries it does return, leaving long untouched stretches at the
        end. Windowing asks per stretch instead, and passes the previous
        window's last title forward so the model does not reopen a topic it
        just closed.

        Degradation is per window: a failed call marks the run degraded but
        keeps the other windows' boundaries. An empty window is legitimate,
        since a stretch can genuinely be one topic, so only every window coming
        back empty counts as a failure.
        """
        target, window_span, max_boundaries, _ = resolve_chapter_geometry()
        window_count = max(1, math.ceil(episode_duration / window_span))
        found: List[Dict] = []
        calls = 0
        any_success = False
        previous_title = None

        for index in range(window_count):
            if len(found) >= max_boundaries:
                logger.info(
                    f"Chapter boundary cap reached ({max_boundaries}); "
                    f"skipping {window_count - index} remaining window(s)"
                )
                break
            win_start = index * window_span
            win_end = min(episode_duration, win_start + window_span)
            if win_end - win_start < target:
                continue
            transcript_text = self._get_full_transcript_range(
                segments, win_start, win_end)
            if not transcript_text or len(transcript_text) <= 500:
                continue
            remaining = max_boundaries - len(found)
            num_splits = max(
                1, min(remaining, int((win_end - win_start) / target)))
            calls += 1
            try:
                window_chapters = self._detect_topic_boundaries(
                    transcript_text, win_start, win_end, num_splits,
                    episode_description=episode_description if index == 0 else None,
                    hints=hints,
                    previous_title=previous_title,
                )
            except Exception as e:
                logger.warning(
                    f"Failed to detect topic boundaries in window "
                    f"{win_start:.0f}s-{win_end:.0f}s: {e}")
                self._topic_detection_failed = True
                continue
            any_success = True
            if window_chapters:
                found.extend(window_chapters)
                previous_title = window_chapters[-1].get('title') or previous_title

        logger.info(
            f"Chapter boundary detection: {calls} call(s) over {window_count} "
            f"window(s), {len(found)} boundaries for a "
            f"{episode_duration:.0f}s episode"
        )
        # Every window empty on an episode that qualified for AI boundaries is a
        # failure, not a normal short episode.
        if calls and not found and any_success:
            self._topic_detection_failed = True
        return found[:max_boundaries]

    def generate_chapters(
        self,
        segments: List[Dict],
        episode_description: str = None,
        ads_removed: List[Dict] = None,
        podcast_name: str = "Unknown",
        episode_title: str = "Unknown",
        episode_id: Optional[str] = None,
        replacement_duration: float = 0.0,
        segment_markers: Optional[List[Dict]] = None,
        marker_cuts: Optional[List[Dict]] = None,
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
            segment_markers: Optional list of detected ad/segment markers
                (each with 'start', 'end', 'category', 'action_applied') used
                to build topic-boundary hints (see build_segment_hints). None
                or empty leaves the topic-detection prompt unchanged.
            marker_cuts: Applied cut list used to map segment_markers onto
                the processed timeline; defaults to `ads_removed`. The
                regenerate-chapters endpoint must pass this explicitly: its
                segments are already on the processed timeline, but hints
                still need the original applied-cut list to map from marker
                (original-time) coordinates.

        Returns:
            {'version': '1.2.0', 'chapters': [{'startTime', 'title'}, ...]}
        """
        logger.info(f"Generating chapters for '{episode_title}'")
        self._episode_id = episode_id
        self._topic_detection_failed = False
        self._title_generation_failed = False
        self.chapters_degraded = False
        self.chapters_degradation_reason = None

        if not segments:
            return {'version': '1.2.0', 'chapters': []}

        if ads_removed:
            segments = self._adjust_segments_for_ads(segments, ads_removed, replacement_duration)
            if not segments:
                return {'version': '1.2.0', 'chapters': []}

        hint_cuts = marker_cuts if marker_cuts is not None else ads_removed
        hints = build_segment_hints(segment_markers, hint_cuts, replacement_duration)

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
                for ch in self._detect_boundaries_windowed(
                        segments, episode_duration,
                        episode_description=episode_description, hints=hints):
                    chapters.append({
                        'startTime': ch['original_time'],
                        'title': ch.get('title'),
                        'source': 'ai',
                        'needs_title': not ch.get('title'),
                    })

        chapters.sort(key=lambda x: x['startTime'])

        deduplicated = []
        for ch in chapters:
            if not deduplicated or ch['startTime'] - deduplicated[-1]['startTime'] >= MIN_DEDUP_WINDOW:
                deduplicated.append(ch)
        chapters = deduplicated

        chapters = self._enforce_min_duration(
            chapters, episode_duration,
            resolve_chapter_geometry()[3])

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

        if self._topic_detection_failed or self._title_generation_failed:
            reasons = []
            if self._topic_detection_failed:
                reasons.append('chapter topic detection failed')
            if self._title_generation_failed:
                reasons.append('chapter title generation failed')
            reason = '; '.join(reasons)
            self.chapters_degraded = True
            self.chapters_degradation_reason = reason
            if len(output_chapters) <= 1:
                logger.warning(
                    f"[{episode_id}] chapters degraded to a single chapter "
                    f"({reason}); operator-visible fallback, not a normal short episode"
                )
            else:
                logger.warning(
                    f"[{episode_id}] chapter generation degraded ({reason}); "
                    f"some chapters may have generic titles or missing boundaries"
                )

        return {
            'version': '1.2.0',
            'chapters': output_chapters,
        }
