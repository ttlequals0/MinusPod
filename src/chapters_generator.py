"""JSON chapters generator for Podcasting 2.0 support."""
import logging
import math
import re
from itertools import pairwise

from config import (
    ModelNotConfiguredError,
    is_keep_like, normalize_segment_category, resolve_chapter_geometry,
    resolve_stage_tunables,
)
from database import Database, DEFAULT_CHAPTER_PROMPT
from utils.prompt import (
    render_prompt_once, apply_override, strip_html, strip_comments_from_prompt
)
from utils.time import (
    parse_timestamp, adjust_timestamp, merge_cut_spans, shift_for_merged_cuts,
    span_inside_any_cut,
)
from description_chapters import CLOCK_TIME_SUFFIX
from utils.text import extract_text_from_segments
from llm_capabilities import PASS_CHAPTER_GENERATION
from llm_client import (
    get_llm_client, get_api_key, LLMClient,
    LLMDisabledError, OllamaContextOverflowError, ProviderAccountChangedError, ProviderRateLimitedError,
)
from cancel import ProcessingCancelled, ProcessingOwnershipLost
from llm_route import client_for_route, live_route_params
from run_context import route_for_phase
from utils.llm_call import call_llm

logger = logging.getLogger(__name__)

# Episodes shorter than this skip AI topic detection entirely.
MIN_DURATION_FOR_AI = 900.0
CHAPTERS_AI_UNAVAILABLE = (
    'Chapter AI is unavailable. Check Settings > AI Models, then try again.')

# Two chapters whose start times are closer than this are merged during dedupe.
MIN_DEDUP_WINDOW = 60.0

# Show-notes gate: description timestamps replace LLM topic detection when at
# least this many fall inside the episode, the first is listed within the
# first-seconds limit (original audio) and consecutive ones are at least the
# spacing apart.
MIN_SHOW_NOTES_CHAPTERS = 3
SHOW_NOTES_MAX_FIRST_SECONDS = 600.0
SHOW_NOTES_MIN_SPACING_SECONDS = 60.0
# Internal source of a show-notes chapter; such chapters never trigger a chapter trim.
SHOW_NOTES_SOURCE = 'show_notes'
# The transcript is searched this far either side of a listed time, and a
# snapped time further away than this is rejected.
SNAP_RADIUS_SECONDS = 300.0
# A first show-notes chapter starting at or after this gets an "Intro" before it.
SHOW_NOTES_INTRO_MIN_SECONDS = 5.0

SHOW_NOTES_ALIGN_PROMPT = (
    "These topics are listed in the episode's show notes with approximate "
    "start times. For each topic, find the transcript line where the "
    "discussion of that topic begins. Reply with one line per topic, exactly "
    "`<number> MM:SS`, using a timestamp from the [MM:SS] markers. If a topic "
    "cannot be found, reply `<number> -`.\n\n"
    "Topics:\n{topics}\n\nTranscript:\n{transcript}"
)

_CLOCK_TITLE = re.compile(CLOCK_TIME_SUFFIX)

_ALIGN_LINE = re.compile(
    r'^\W*(\d{1,3})\s*[.):\-]?\s+(\d{1,3}:\d{2}(?::\d{2})?|-)(?:\s|$)')

# Patterns for MM:SS timestamps embedded in episode descriptions.
_TIMESTAMP_PATTERNS = (
    re.compile(r'(?:^|\n)\s*(\d{1,2}:\d{2}(?::\d{2})?)\s*[-:]*\s*(.+?)(?=\n|$)'),
    re.compile(r'\[(\d{1,2}:\d{2}(?::\d{2})?)\]\s*(.+?)(?=\n|$)'),
    re.compile(r'\((\d{1,2}:\d{2}(?::\d{2})?)\)\s*(.+?)(?=\n|$)'),
)


def _parse_description_anchors(description: str) -> list[tuple[str, str]]:
    """Extract (timestamp, title) pairs from an episode description.

    Returns deduplicated, time-sorted anchors used as soft hints in the topic-detection prompt;
    the LLM still chooses whether to honor them.
    """
    if not description:
        return []
    text = strip_html(description)
    seen: dict[str, str] = {}
    for pattern in _TIMESTAMP_PATTERNS:
        for ts, title in pattern.findall(text):
            title = title.strip().rstrip('-:').strip()
            if not title or len(title) < 2 or title.isdigit():
                continue
            seen.setdefault(ts, title)
    return sorted(seen.items(), key=lambda kv: parse_timestamp(kv[0]))


def _show_notes_anchors(description: str | None, cuts: list[dict] | None,
                        replacement_duration: float,
                        episode_duration: float) -> list[tuple[float, str]]:
    """Listed (original-audio) times shifted by the cuts like the served description, or [] off the gate."""
    listed = [(parse_timestamp(ts), title)
              for ts, title in _parse_description_anchors(description or '')
              if not _CLOCK_TITLE.match(title)]
    merged = merge_cut_spans(cuts or [], default_replacement=replacement_duration)
    inside = [(t, shift_for_merged_cuts(t, merged), title) for t, title in listed]
    inside = [a for a in inside if a[1] < episode_duration]
    if len(inside) < MIN_SHOW_NOTES_CHAPTERS:
        return []
    if inside[0][0] > SHOW_NOTES_MAX_FIRST_SECONDS:
        return []
    if any(b[0] - a[0] < SHOW_NOTES_MIN_SPACING_SECONDS for a, b in pairwise(inside)):
        return []
    return [(shifted, title) for _, shifted, title in inside]


def _hint_time(hint: dict) -> float:
    """A hint's anchor time: seam hints carry 'time', range hints 'start'."""
    return hint.get('time', hint.get('start', 0.0))


def _format_mmss(seconds: float) -> str:
    """Format seconds as zero-padded MM:SS, matching the prompt's own
    [MM:SS] transcript markers and example lines."""
    total = max(0, int(round(seconds)))
    return f"{total // 60:02d}:{total % 60:02d}"


def build_segment_hints(markers: list[dict] | None, cuts: list[dict] | None,
                         replacement_duration: float = 0.0) -> list[dict]:
    """Map applied ad/segment markers onto the processed timeline as topic-boundary hints.

    The processed transcript has no trace of a cut ad, so the topic detector loses the signal that a
    topic change likely happened there. This rebuilds it from the markers and applied-cut list via
    utils.time.adjust_timestamp, keeping positions in lockstep with the transcript mapping.
    A 'remove' marker becomes a single seam position; 'keep'/'mark'/'beep' markers become a start/end
    range, since that time still exists in the processed audio. Other actions (including markers
    pending review) are skipped. Returns [] without markers so the prompt stays unchanged.
    """
    if not markers:
        return []
    cuts = cuts or []
    hints: list[dict] = []
    for marker in markers:
        action = marker.get('action_applied')
        if action != 'remove' and action != 'beep' and not is_keep_like(action):
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
    hints.sort(key=_hint_time)
    return hints


def _format_hints_block(hints: list[dict]) -> str:
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


def chapter_language_instruction(language: str | None) -> str:
    """Prompt paragraph naming the chapter title language; empty for none or English."""
    code = (language or '').strip().lower()
    # The prompts are English already.
    if not code or code == 'en':
        return ''
    if code != 'auto':
        return (f"Write every chapter title in the language with ISO 639 code '{code}', "
                "even where the transcript or these instructions use another language.")
    return ("Write every chapter title in the language the transcript is spoken in, "
            "even though these instructions are in English.")


def get_chapters_model() -> str:
    """This run's chapters model, else the configured value outside a run."""
    route = route_for_phase('chapters')
    if route:
        return route['configured_model']
    try:
        db = Database()

        model = db.get_setting('chapters_model')
        if model:
            return model

        # Reuse the configured detection model: correct for every provider.
        primary_model = db.get_setting('claude_model')
        if primary_model:
            return primary_model
    except Exception as e:
        logger.warning(f"Could not load chapters model from DB: {e}")

    raise ModelNotConfiguredError('chapters_model')


class ChaptersGenerator:
    """Generate JSON chapters from episode content."""

    def __init__(self, api_key: str = None):
        """Initialize the chapters generator; api_key defaults to the environment configuration."""
        self._api_key_override: str | None = api_key
        self._llm_client_override: LLMClient | None = None
        self._episode_id: str | None = None
        self._slug: str | None = None
        # (template, override), read once per run rather than per window.
        self._chapter_prompt: tuple[str, str] | None = None
        self._language_instruction = ''
        # Per-feed notes are set per generate_chapters call, never baked into the cached template.
        self._chapter_notes: str | None = None
        # Set when topic detection or title generation fails and the run
        # degrades to a fallback; read after generate_chapters() so a
        # degraded run doesn't look like a normal short episode.
        self._topic_detection_failed: bool = False
        self._title_generation_failed: bool = False
        self._missing_client_for_titles: bool = False
        self._show_notes_alignment_failed: bool = False
        # Set when get_chapters_model() raises; appended to the degradation
        # reason below instead of just the generic "... failed" text.
        self._model_not_configured_message: str | None = None
        # Set when Ollama truncated a chapter prompt to fit num_ctx (#893).
        self._context_overflow_message: str | None = None
        self.chapters_degraded: bool = False
        self.chapters_degradation_reason: str | None = None
        # Output start times of show-notes chapters, kept out of the chapter trim.
        self.show_notes_starts: frozenset[int] = frozenset()

    @property
    def api_key(self) -> str | None:
        """Active API key. Resolves dynamically via get_api_key() unless overridden."""
        if self._api_key_override is not None:
            return self._api_key_override
        return get_api_key()

    @api_key.setter
    def api_key(self, value: str | None) -> None:
        self._api_key_override = value

    @property
    def _llm_client(self) -> LLMClient | None:
        """Current LLM client: this run's chapters-route client, or the
        global client outside a run. Reads through on every access so a
        settings change takes effect without restarting the worker."""
        return self._client_for('chapters')

    @_llm_client.setter
    def _llm_client(self, value: LLMClient | None) -> None:
        self._llm_client_override = value

    def _client_for(self, route: str | dict | None) -> LLMClient | None:
        return client_for_route(
            route, override=self._llm_client_override,
            fallback=lambda: get_llm_client() if self.api_key else None)

    def _initialize_client(self):
        """Surface LLM client init errors before a generation run."""
        if not self.api_key:
            return
        try:
            client = get_llm_client()
            logger.debug(f"LLM client initialized for chapters generator: {client.get_provider_name()}")
        except LLMDisabledError:
            return
        except Exception as e:
            logger.error(f"Failed to initialize LLM client: {e}")

    def _get_full_transcript_range(
        self,
        segments: list[dict],
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

    def _load_chapter_prompt(self) -> tuple[str, str]:
        """The (template, override) settings pair, cached for the run."""
        if self._chapter_prompt is None:
            try:
                db = Database()
                template = db.get_setting('chapter_prompt') or DEFAULT_CHAPTER_PROMPT
                override = db.get_setting('chapter_prompt_override') or ''
            except Exception as e:
                logger.warning(f"Could not load chapter prompt from DB: {e}")
                template, override = DEFAULT_CHAPTER_PROMPT, ''
            self._chapter_prompt = (
                strip_comments_from_prompt(template),
                strip_comments_from_prompt(override)
            )
        return self._chapter_prompt

    def _detect_topic_boundaries(
        self,
        transcript: str,
        start_time: float,
        end_time: float,
        num_splits: int,
        episode_description: str = None,
        anchors: list[tuple[str, str]] | None = None,
        hints: list[dict] | None = None,
        previous_title: str | None = None,
    ) -> list[dict] | None:
        """Use the LLM to detect topic boundaries in a transcript range.

        hints: optional candidate boundaries from ad/segment markers (see build_segment_hints); empty/None
        leaves the prompt unchanged. previous_title: the chapter this window opens inside, so a windowed
        run does not reopen a topic the previous window just closed.

        Returns a list of {'original_time': float, 'title': str}, or None when the LLM call itself failed
        (as opposed to a legitimately empty range).
        """
        hints_block = _format_hints_block(hints or [])
        continuation_block = (
            f"\n\nThis segment continues the chapter \"{previous_title}\". Do not "
            "emit a boundary for that same topic; only for a change away from it."
            if previous_title else ""
        )
        description_block = ""
        # anchors, when given, are pre-parsed (and window-filtered) show-notes
        # candidates; otherwise fall back to parsing the raw description.
        if anchors is None and episode_description and episode_description.strip():
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
        elif episode_description and episode_description.strip():
            description_block = (
                "\n\nIf the episode description below contains explicit "
                "timestamp markers in MM:SS or H:MM:SS form, prefer those "
                "timestamps and titles over inferring your own. Otherwise "
                "identify topic transitions from the transcript.\n\n"
                f"Episode description:\n{episode_description}"
            )

        # Override lands on the template BEFORE variables render, and
        # substitution is single-pass: transcript, description, and title
        # text can never inject or reposition a placeholder.
        base_prompt, override = self._load_chapter_prompt()
        prompt = render_prompt_once(
            apply_override(base_prompt, override),
            num_splits=str(num_splits),
            segment_start=f"{int(start_time/60)}:{int(start_time%60):02d}",
            segment_end=f"{int(end_time//60)}:{int(end_time%60):02d}",
            continuation_block=continuation_block,
            description_block=description_block,
            hints_block=hints_block,
            transcript=transcript,
        )
        if self._language_instruction:
            prompt += f"\n\n{self._language_instruction}"
        if self._chapter_notes:
            prompt += f"\n\nFeed notes:\n{self._chapter_notes}"

        try:
            live = live_route_params('chapters')
            model = live.model if live.route else get_chapters_model()
            max_tokens, temperature, reasoning = resolve_stage_tunables(
                'chapter_boundary', provider=live.provider, model=model)
            response, last_error = call_llm(
                llm_client=self._client_for(live.route),
                model=model,
                system_prompt="",
                prompt=prompt,
                llm_timeout=live.timeout,
                max_retries=live.max_retries,
                max_tokens=max_tokens,
                temperature=temperature,
                reasoning_effort=reasoning,
                slug=self._slug,
                episode_id=self._episode_id,
                call_label="chapter topic detection",
                pass_name=PASS_CHAPTER_GENERATION,
                phase_key='chapters',
                provider=live.provider,
                credential_slot=live.credential_slot,
                stage_tunable_prefix='chapter_boundary',
            )
            if response is None:
                # A rate-limit hold is queue-wide state, not a degraded run.
                if isinstance(last_error, ProviderRateLimitedError):
                    raise last_error
                if isinstance(last_error, OllamaContextOverflowError):
                    self._context_overflow_message = (
                        f"prompt exceeded Ollama num_ctx={last_error.num_ctx} "
                        f"(about {last_error.estimated_tokens} tokens); raise num_ctx")
                logger.error(f"Failed to detect topic boundaries: {last_error}")
                return None

            result_text = response.content.strip()
            logger.debug(f"LLM topic detection response ({len(result_text)} chars):\n{result_text}")
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

                # Minutes are unbounded MM:SS in prompts, so allow 3 digits
                # (windows past 100 minutes emit e.g. "135:42").
                match = re.match(r'^(\d{1,3}:\d{2}(?::\d{2})?)\s*[-:]?\s*(.+)$', cleaned)
                if match:
                    timestamp_str, title = match.groups()
                    try:
                        seconds = parse_timestamp(timestamp_str)
                        if start_time <= seconds < end_time:
                            chapters.append({
                                'original_time': seconds,
                                'title': title.strip()
                            })
                            logger.debug(f"Accepted topic: {timestamp_str} ({seconds}s) - {title.strip()}")
                        else:
                            logger.debug(f"Rejected outside range: {timestamp_str} ({seconds}s) not in {start_time}-{end_time}")
                    except (ValueError, IndexError) as e:
                        logger.warning(f"Failed to parse timestamp {timestamp_str}: {e}")
                else:
                    logger.debug(f"Line didn't match pattern: {cleaned[:80]}")

            logger.info(f"AI detected {len(chapters)} topic boundaries")
            return chapters

        except ModelNotConfiguredError as e:
            logger.warning(f"Chapter topic detection skipped: {e}")
            self._model_not_configured_message = CHAPTERS_AI_UNAVAILABLE
            return None
        except (ProviderRateLimitedError, ProviderAccountChangedError, LLMDisabledError,
                ProcessingCancelled, ProcessingOwnershipLost):
            raise
        except Exception as e:
            logger.error(f"Failed to detect topic boundaries: {e}")
            return None

    def get_transcript_excerpt(
        self,
        segments: list[dict],
        start_time: float,
        end_time: float,
        max_words: int = 300
    ) -> str:
        """Get transcript excerpt for a time range."""
        return extract_text_from_segments(segments, start_time, end_time, max_words)

    def generate_chapter_titles(
        self,
        chapters: list[dict],
        segments: list[dict],
        podcast_name: str,
        episode_title: str,
    ) -> list[dict]:
        """Generate titles for chapters that need them.

        Chapters and segments share the post-ad-removal timeline, so chapter startTime indexes the transcript directly.
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
            self._missing_client_for_titles = True
            self._model_not_configured_message = CHAPTERS_AI_UNAVAILABLE
            self._title_generation_failed = True
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

            for req, title in zip(chapter_requests, titles, strict=True):
                chapters[req['index']]['title'] = title
                chapters[req['index']]['needs_title'] = False

        except ModelNotConfiguredError as e:
            logger.warning(f"Chapter title generation skipped: {e}")
            self._model_not_configured_message = CHAPTERS_AI_UNAVAILABLE
            self._title_generation_failed = True
            return self._apply_generic_titles(chapters)
        except (ProviderRateLimitedError, ProviderAccountChangedError, LLMDisabledError,
                ProcessingCancelled, ProcessingOwnershipLost):
            raise
        except Exception as e:
            logger.error(f"Failed to generate chapter titles: {e}")
            self._title_generation_failed = True
            return self._apply_generic_titles(chapters)

        return chapters

    def _call_claude_for_titles(
        self,
        chapter_requests: list[dict],
        podcast_name: str,
        episode_title: str
    ) -> list[str]:
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
        if self._language_instruction:
            prompt += f"\n\n{self._language_instruction}"
        if self._chapter_notes:
            prompt += f"\n\nFeed notes:\n{self._chapter_notes}"

        live = live_route_params('chapters')
        model = live.model if live.route else get_chapters_model()
        max_tokens, temperature, reasoning = resolve_stage_tunables(
            'chapter_title', provider=live.provider, model=model)
        response, last_error = call_llm(
            llm_client=self._client_for(live.route),
            model=model,
            system_prompt="",
            prompt=prompt,
            llm_timeout=live.timeout,
            max_retries=live.max_retries,
            max_tokens=max_tokens,
            temperature=temperature,
            reasoning_effort=reasoning,
            slug=self._slug,
            episode_id=self._episode_id,
            call_label="chapter title generation",
            pass_name=PASS_CHAPTER_GENERATION,
            phase_key='chapters',
            provider=live.provider,
            credential_slot=live.credential_slot,
            stage_tunable_prefix='chapter_title',
        )
        if response is None:
            # Caller (generate_chapter_titles) catches this and degrades to
            # generic titles, except a rate-limit hold, which propagates.
            raise last_error

        response_text = response.content.strip()
        titles = [line.strip() for line in response_text.split('\n') if line.strip()]

        if len(titles) < len(chapter_requests):
            self._title_generation_failed = True
        while len(titles) < len(chapter_requests):
            titles.append(f"Part {len(titles) + 1}")

        return titles[:len(chapter_requests)]

    def _apply_generic_titles(self, chapters: list[dict]) -> list[dict]:
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
        chapters: list[dict],
        episode_duration: float,
        min_duration: float,
    ) -> list[dict]:
        """Drop chapters shorter than min_duration by absorbing into the previous.

        Assumes the post-ad-removal timeline; the first chapter is always retained.
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
        segments: list[dict],
        ads_removed: list[dict],
        replacement_duration: float = 0.0,
    ) -> list[dict]:
        """Project raw segments onto the post-ad-removal timeline.

        Drops segments entirely inside an ad span and shifts the rest back by the cumulative duration
        of earlier ads via utils.time.adjust_timestamp.
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
        segments: list[dict],
        episode_duration: float,
        target: float,
        window_span: float,
        max_boundaries: int,
        episode_description: str = None,
        hints: list[dict] | None = None,
    ) -> list[dict]:
        """Detect topic boundaries a window at a time, earliest first.

        An empty window is legitimate (a stretch can be one topic); only a failed call or every window
        coming back empty degrades the run.
        """
        window_count = max(1, math.ceil(episode_duration / window_span))
        found: list[dict] = []
        calls = 0
        previous_title = None
        # Parse show-notes anchors once; each window gets only its own range.
        # With no parseable anchors the raw description goes to window 0 only,
        # since repeating multi-KB prose per window is real token cost.
        all_anchors = _parse_description_anchors(episode_description or '')

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
            # Only hints overlapping this window; out-of-window lines just
            # bloat the prompt. A range hint straddling a seam reaches both.
            window_hints = [
                h for h in (hints or [])
                if (_hint_time(h) < win_end
                    and h.get('end', _hint_time(h)) >= win_start)
            ]
            window_anchors = [
                a for a in all_anchors
                if win_start <= parse_timestamp(a[0]) < win_end
            ]
            remaining = max_boundaries - len(found)
            num_splits = max(
                1, min(remaining, int((win_end - win_start) / target)))
            calls += 1
            window_chapters = self._detect_topic_boundaries(
                transcript_text, win_start, win_end, num_splits,
                episode_description=(episode_description
                                     if index == 0 and not all_anchors
                                     else None),
                anchors=window_anchors if all_anchors else None,
                hints=window_hints,
                previous_title=previous_title,
            )
            if window_chapters is None:
                logger.warning(
                    f"Failed to detect topic boundaries in window "
                    f"{win_start:.0f}s-{win_end:.0f}s")
                self._topic_detection_failed = True
                continue
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
        if calls and not found:
            self._topic_detection_failed = True
        return found[:max_boundaries]

    def _align_show_notes_group(
        self,
        segments: list[dict],
        group: list[tuple[int, float, str]],
        episode_duration: float,
    ) -> dict[int, float] | None:
        """{index: seconds} for the (index, seconds, title) topics the LLM located, or None when the call failed."""
        start = max(0.0, group[0][1] - SNAP_RADIUS_SECONDS)
        end = min(episode_duration, group[-1][1] + SNAP_RADIUS_SECONDS)
        transcript = self._get_full_transcript_range(segments, start, end)
        if not transcript:
            return {}
        topics = '\n'.join(
            f"{n}. (about {_format_mmss(t)}) {title}"
            for n, (_, t, title) in enumerate(group, 1))
        prompt = SHOW_NOTES_ALIGN_PROMPT.format(
            topics=topics, transcript=transcript)
        if self._chapter_notes:
            prompt += f"\n\nFeed notes:\n{self._chapter_notes}"
        try:
            live = live_route_params('chapters')
            model = live.model if live.route else get_chapters_model()
            max_tokens, temperature, reasoning = resolve_stage_tunables(
                'chapter_boundary', provider=live.provider, model=model)
            response, last_error = call_llm(
                llm_client=self._client_for(live.route),
                model=model,
                system_prompt="",
                prompt=prompt,
                llm_timeout=live.timeout,
                max_retries=live.max_retries,
                max_tokens=max_tokens,
                temperature=temperature,
                reasoning_effort=reasoning,
                slug=self._slug,
                episode_id=self._episode_id,
                call_label="chapter show-notes alignment",
                pass_name=PASS_CHAPTER_GENERATION,
                phase_key='chapters',
                provider=live.provider,
                credential_slot=live.credential_slot,
                stage_tunable_prefix='chapter_boundary',
            )
            if response is None:
                if isinstance(last_error, ProviderRateLimitedError):
                    raise last_error
                if isinstance(last_error, OllamaContextOverflowError):
                    self._context_overflow_message = (
                        f"prompt exceeded Ollama num_ctx={last_error.num_ctx} "
                        f"(about {last_error.estimated_tokens} tokens); raise num_ctx")
                logger.warning(f"Show-notes alignment failed: {last_error}")
                return None
            found: dict[int, float] = {}
            for line in response.content.strip().split('\n'):
                match = _ALIGN_LINE.match(line.strip())
                if not match or match.group(2) == '-':
                    continue
                number = int(match.group(1))
                if 1 <= number <= len(group):
                    try:
                        found.setdefault(
                            group[number - 1][0], parse_timestamp(match.group(2)))
                    except (ValueError, IndexError):
                        continue
            return found
        except ModelNotConfiguredError as e:
            logger.warning(f"Show-notes alignment skipped: {e}")
            self._model_not_configured_message = CHAPTERS_AI_UNAVAILABLE
            return None
        except (ProviderRateLimitedError, ProviderAccountChangedError, LLMDisabledError,
                ProcessingCancelled, ProcessingOwnershipLost):
            raise
        except Exception as e:
            logger.warning(f"Show-notes alignment failed: {e}")
            return None

    def _extra_llm_chapters(
        self,
        segments: list[dict],
        episode_duration: float,
        listed: list[dict],
        window_span: float,
        episode_description: str | None,
        hints: list[dict] | None,
        target: float,
        max_boundaries: int,
        min_duration: float,
    ) -> list[dict]:
        """Titled LLM topic boundaries at least min_duration from listed chapters and each other; failure adds none."""
        if episode_duration <= MIN_DURATION_FOR_AI:
            return []
        self._initialize_client()
        if not self._llm_client:
            return []
        saved = (self._topic_detection_failed, self._model_not_configured_message,
                 self._context_overflow_message)
        try:
            found = self._detect_boundaries_windowed(
                segments, episode_duration, target, window_span, max_boundaries,
                episode_description=episode_description, hints=hints)
        finally:
            (self._topic_detection_failed, self._model_not_configured_message,
             self._context_overflow_message) = saved
        starts = [c['startTime'] for c in listed]
        extras: list[dict] = []
        for ch in sorted(found, key=lambda c: c['original_time']):
            t, title = ch['original_time'], (ch.get('title') or '').strip()
            if not title:
                continue
            if any(abs(t - s) < min_duration for s in starts):
                continue
            # Like the generate path, no chapter shorter than min_duration at the end.
            if episode_duration - t < min_duration:
                continue
            if extras and t - extras[-1]['startTime'] < min_duration:
                continue
            extras.append({'startTime': t, 'title': title, 'source': 'ai'})
        return extras

    def _chapters_from_show_notes(
        self,
        segments: list[dict],
        episode_duration: float,
        anchors: list[tuple[float, str]],
        window_span: float,
        episode_description: str | None = None,
        hints: list[dict] | None = None,
        target: float = 600.0,
        max_boundaries: int = 20,
        min_duration: float = 180.0,
    ) -> list[dict]:
        """One chapter per (processed seconds, title) anchor, titles verbatim, each time aligned to the transcript."""
        # Anchors at the very start stay at 0; the rest are grouped so each
        # group's transcript span fits one chapter window.
        movable = [(i, t, title) for i, (t, title) in enumerate(anchors)
                   if t >= SHOW_NOTES_INTRO_MIN_SECONDS]
        groups: list[list[tuple[int, float, str]]] = []
        for item in movable:
            if groups:
                span = (min(episode_duration, item[1] + SNAP_RADIUS_SECONDS)
                        - max(0.0, groups[-1][0][1] - SNAP_RADIUS_SECONDS))
                if span <= window_span:
                    groups[-1].append(item)
                    continue
            groups.append([item])

        # Unaligned listed times stand; a failed or impossible call degrades the run.
        snapped: dict[int, float] = {}
        if groups:
            self._initialize_client()
        if groups and self._llm_client:
            for group in groups:
                found = self._align_show_notes_group(segments, group, episode_duration)
                if found is None:
                    self._show_notes_alignment_failed = True
                else:
                    snapped.update(found)
        elif groups:
            logger.warning("No LLM client; show-notes chapters keep their listed times")
            self._show_notes_alignment_failed = True
            self._model_not_configured_message = CHAPTERS_AI_UNAVAILABLE

        times: list[float] = []
        aligned = 0
        for i, (raw, _title) in enumerate(anchors):
            candidate = snapped.get(i)
            upper = anchors[i + 1][0] if i + 1 < len(anchors) else episode_duration
            if (candidate is not None
                    and abs(candidate - raw) <= SNAP_RADIUS_SECONDS
                    and (not times or candidate > times[-1])
                    and candidate < upper):
                times.append(candidate)
                aligned += 1
            else:
                times.append(raw)
        if times[0] < SHOW_NOTES_INTRO_MIN_SECONDS:
            times[0] = 0.0

        chapters = [{'startTime': t, 'title': title, 'source': SHOW_NOTES_SOURCE}
                    for t, (_, title) in zip(times, anchors, strict=True)]
        if chapters[0]['startTime'] >= SHOW_NOTES_INTRO_MIN_SECONDS:
            chapters.insert(0, {'startTime': 0, 'title': 'Intro', 'source': SHOW_NOTES_SOURCE})

        extras = self._extra_llm_chapters(
            segments, episode_duration, chapters, window_span, episode_description,
            hints, target, max_boundaries, min_duration)
        if extras:
            logger.info(f"Added {len(extras)} LLM chapter(s) the show notes do not list")
            chapters = sorted(chapters + extras, key=lambda c: c['startTime'])

        # Whole seconds, strictly increasing, first at 1 or later (the output
        # convention for chapter start times).
        previous = 0
        for ch in chapters:
            previous = max(previous + 1, int(round(ch['startTime'])))
            ch['startTime'] = previous

        logger.info(
            f"Built {len(anchors)} chapter(s) from show notes "
            f"({aligned} time(s) aligned to the transcript)")
        return chapters

    def generate_chapters(
        self,
        segments: list[dict],
        episode_description: str = None,
        ads_removed: list[dict] = None,
        podcast_name: str = "Unknown",
        episode_title: str = "Unknown",
        episode_id: str | None = None,
        replacement_duration: float = 0.0,
        segment_markers: list[dict] | None = None,
        marker_cuts: list[dict] | None = None,
        slug: str | None = None,
        chapter_notes: str | None = None,
        language: str | None = None,
        show_notes_chapters: bool = True,
    ) -> dict:
        """Generate Podcasting 2.0 chapters from transcript segments.

        Shared entry point for the main pipeline and the manual /regenerate-chapters endpoint.

        segments: pipeline callers pass raw segments plus ads_removed; regen callers pass pre-adjusted
            VTT segments and omit ads_removed.
        episode_description: optional RSS description, injected into the topic-detection prompt so the
            model can honor curated timestamp markers.
        ads_removed: optional list of {'start', 'end'} ad spans; segments are projected onto the
            post-ad-removal timeline before detection.
        podcast_name, episode_title: used for title generation.
        segment_markers: optional detected markers (each with 'start', 'end', 'category',
            'action_applied') used for topic-boundary hints (see build_segment_hints); None or empty
            leaves the prompt unchanged.
        marker_cuts: applied cut list mapping segment_markers and description timestamps onto the
            processed timeline; defaults to `ads_removed`. regenerate-chapters must pass it explicitly: its
            segments are already on the processed timeline but hints need the original applied-cut list to
            map from marker coordinates.
        chapter_notes: per-feed notes appended to the topic-detection prompt as a final "Feed notes:"
            paragraph; None or blank adds nothing.
        language: ISO 639 code for the chapter titles, or 'auto'; None or 'en' leaves the prompts as is.
        slug: podcast slug for the llm_call_usage ledger's podcast linkage; omitted call sites still
            ledger the attempt, without a resolved podcast_id.
        show_notes_chapters: the feed's switch for building chapters from description timestamps
            (see _show_notes_anchors); False always uses topic detection.

        Returns {'version': '1.2.0', 'chapters': [{'startTime', 'title'}, ...]}.
        """
        logger.info(f"Generating chapters for '{episode_title}'")
        self._episode_id = episode_id
        self._slug = slug
        self._chapter_notes = (chapter_notes or '').strip() or None
        self._language_instruction = chapter_language_instruction(language)
        self._topic_detection_failed = False
        self._title_generation_failed = False
        self._missing_client_for_titles = False
        self._show_notes_alignment_failed = False
        self._model_not_configured_message = None
        self._context_overflow_message = None
        self.chapters_degraded = False
        self.chapters_degradation_reason = None
        self.show_notes_starts = frozenset()

        if not segments:
            return {'version': '1.2.0', 'chapters': []}

        if ads_removed:
            segments = self._adjust_segments_for_ads(segments, ads_removed, replacement_duration)
            if not segments:
                return {'version': '1.2.0', 'chapters': []}

        applied_cuts = marker_cuts if marker_cuts is not None else ads_removed
        hints = build_segment_hints(segment_markers, applied_cuts, replacement_duration)

        episode_duration = segments[-1].get('end', 0)

        chapters = [{
            'startTime': 0,
            'title': None,
            'source': 'auto',
            'needs_title': True,
        }]

        # Resolve once so detection and min-duration enforcement can never
        # see different geometry mid-run.
        target, window_span, max_boundaries, min_duration = resolve_chapter_geometry()

        # Timestamps listed in the show notes are the chapters when they pass the gate.
        show_notes_anchors = _show_notes_anchors(
            episode_description, applied_cuts, replacement_duration,
            episode_duration) if show_notes_chapters else []
        listed_chapters = None
        if show_notes_anchors:
            listed_chapters = self._chapters_from_show_notes(
                segments, episode_duration, show_notes_anchors, window_span,
                episode_description=episode_description, hints=hints, target=target,
                max_boundaries=max_boundaries, min_duration=min_duration)

        if listed_chapters is not None:
            chapters = listed_chapters
        elif episode_duration > MIN_DURATION_FOR_AI:
            self._initialize_client()
            if self._llm_client:
                for ch in self._detect_boundaries_windowed(
                        segments, episode_duration,
                        target, window_span, max_boundaries,
                        episode_description=episode_description, hints=hints):
                    chapters.append({
                        'startTime': ch['original_time'],
                        'title': ch.get('title'),
                        'source': 'ai',
                        'needs_title': not ch.get('title'),
                    })
            else:
                self._topic_detection_failed = True
                self._model_not_configured_message = CHAPTERS_AI_UNAVAILABLE

        if listed_chapters is None:
            chapters.sort(key=lambda x: x['startTime'])

            deduplicated = []
            for ch in chapters:
                if not deduplicated or ch['startTime'] - deduplicated[-1]['startTime'] >= MIN_DEDUP_WINDOW:
                    deduplicated.append(ch)
                elif len(deduplicated) > 1:
                    # A sub-minute chapter is usually a promo or transition; keep the topic after it.
                    deduplicated[-1] = ch
            chapters = deduplicated

            chapters = self._enforce_min_duration(
                chapters, episode_duration, min_duration)

            chapters = self.generate_chapter_titles(
                chapters, segments, podcast_name, episode_title
            )
        if (listed_chapters is None and episode_duration <= MIN_DURATION_FOR_AI and len(chapters) == 1
                and self._missing_client_for_titles and not self._topic_detection_failed):
            self._title_generation_failed = False
            self._model_not_configured_message = None

        output_chapters = []
        for chapter in chapters:
            output_chapters.append({
                'startTime': max(1, int(round(chapter['startTime']))),
                'title': chapter.get('title', 'Untitled'),
            })
        self.show_notes_starts = frozenset(
            out['startTime'] for out, chapter in zip(output_chapters, chapters, strict=True)
            if chapter.get('source') == SHOW_NOTES_SOURCE)

        logger.info(f"Generated {len(output_chapters)} chapters")

        if (self._topic_detection_failed or self._title_generation_failed
                or self._show_notes_alignment_failed):
            reasons = []
            if self._topic_detection_failed:
                reasons.append('chapter topic detection failed')
            if self._show_notes_alignment_failed:
                reasons.append('chapter show-notes alignment failed')
            if self._title_generation_failed:
                reasons.append('chapter title generation failed')
            if self._model_not_configured_message:
                reasons.append(self._model_not_configured_message)
            if self._context_overflow_message:
                reasons.append(self._context_overflow_message)
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
