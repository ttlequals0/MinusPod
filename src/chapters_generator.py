"""JSON chapters generator for Podcasting 2.0 support."""
import json
import logging
import os
import re
from typing import List, Dict, Optional, Tuple

from utils.time import parse_timestamp
from utils.text import extract_text_from_segments

logger = logging.getLogger(__name__)

# Minimum chapter duration in seconds (3 minutes)
MIN_CHAPTER_DURATION = 180.0

# Patterns to match timestamps in episode descriptions
TIMESTAMP_PATTERNS = [
    # "0:00 - Intro" or "0:00 Intro" or "0:00: Intro"
    r'(?:^|\n)\s*(\d{1,2}:\d{2}(?::\d{2})?)\s*[-:]*\s*(.+?)(?=\n|$)',
    # "[00:15:00] Segment Name"
    r'\[(\d{1,2}:\d{2}(?::\d{2})?)\]\s*(.+?)(?=\n|$)',
    # "(1:30:45) Topic"
    r'\((\d{1,2}:\d{2}(?::\d{2})?)\)\s*(.+?)(?=\n|$)',
]


class ChaptersGenerator:
    """Generate JSON chapters from episode content."""

    def __init__(self, api_key: str = None):
        """Initialize the chapters generator.

        Args:
            api_key: Anthropic API key (defaults to ANTHROPIC_API_KEY env var)
        """
        self.api_key = api_key or os.environ.get('ANTHROPIC_API_KEY')
        self.client = None

    def _initialize_client(self):
        """Initialize Anthropic client if not already done."""
        if self.client is None and self.api_key:
            try:
                from anthropic import Anthropic
                self.client = Anthropic(api_key=self.api_key)
                logger.debug("Anthropic client initialized for chapters generator")
            except Exception as e:
                logger.error(f"Failed to initialize Anthropic client: {e}")

    def parse_timestamp_to_seconds(self, timestamp: str) -> float:
        """
        Parse a timestamp string to seconds.

        Supports formats:
        - M:SS (e.g., "1:30")
        - MM:SS (e.g., "01:30")
        - H:MM:SS (e.g., "1:30:45")
        - HH:MM:SS (e.g., "01:30:45")

        Args:
            timestamp: Timestamp string

        Returns:
            Time in seconds
        """
        return parse_timestamp(timestamp)

    def _html_to_text(self, html: str) -> str:
        """
        Convert HTML to plain text for parsing.

        Converts <br>, <p>, <li> tags to newlines and strips other HTML.
        """
        if not html:
            return ""

        text = html
        # Convert block elements and breaks to newlines
        text = re.sub(r'<br\s*/?>', '\n', text, flags=re.IGNORECASE)
        text = re.sub(r'</p>', '\n', text, flags=re.IGNORECASE)
        text = re.sub(r'</li>', '\n', text, flags=re.IGNORECASE)
        text = re.sub(r'</div>', '\n', text, flags=re.IGNORECASE)
        # Strip remaining HTML tags
        text = re.sub(r'<[^>]+>', '', text)
        # Decode common HTML entities
        text = text.replace('&amp;', '&')
        text = text.replace('&lt;', '<')
        text = text.replace('&gt;', '>')
        text = text.replace('&quot;', '"')
        text = text.replace('&#39;', "'")
        text = text.replace('&nbsp;', ' ')
        # Normalize whitespace but preserve newlines
        text = re.sub(r'[ \t]+', ' ', text)
        text = re.sub(r'\n\s*\n', '\n\n', text)

        return text.strip()

    def parse_description_timestamps(self, description: str) -> List[Dict]:
        """
        Parse timestamps from episode description.

        Args:
            description: Episode description text (can be HTML)

        Returns:
            List of {'original_time': float, 'title': str}
        """
        if not description:
            return []

        # Convert HTML to plain text first
        text = self._html_to_text(description)

        chapters = []
        seen_times = set()

        for pattern in TIMESTAMP_PATTERNS:
            for match in re.finditer(pattern, text, re.MULTILINE | re.IGNORECASE):
                timestamp_str, title = match.groups()
                title = title.strip()

                # Skip empty titles or very short ones
                if not title or len(title) < 2:
                    continue

                # Skip titles that look like more timestamps or numbers
                if re.match(r'^[\d:]+$', title):
                    continue

                try:
                    seconds = self.parse_timestamp_to_seconds(timestamp_str)

                    # Avoid duplicates (within 5 seconds)
                    time_key = round(seconds / 5) * 5
                    if time_key in seen_times:
                        continue
                    seen_times.add(time_key)

                    chapters.append({
                        'original_time': seconds,
                        'title': title,
                        'source': 'description'
                    })
                except (ValueError, IndexError):
                    continue

        # Sort by time
        chapters.sort(key=lambda x: x['original_time'])

        logger.info(f"Parsed {len(chapters)} timestamps from description")
        return chapters

    def adjust_timestamp(self, original_time: float, ads_removed: List[Dict]) -> float:
        """
        Adjust a timestamp to account for removed ads.

        Args:
            original_time: Original timestamp in seconds
            ads_removed: List of removed ads

        Returns:
            Adjusted timestamp
        """
        if not ads_removed:
            return original_time

        adjustment = 0.0
        sorted_ads = sorted(ads_removed, key=lambda x: x['start'])

        for ad in sorted_ads:
            ad_start = ad.get('start', 0)
            ad_end = ad.get('end', 0)

            if ad_end <= original_time:
                adjustment += (ad_end - ad_start)
            else:
                break

        return max(0.0, original_time - adjustment)

    def detect_ad_gap_chapters(
        self,
        segments: List[Dict],
        ads_removed: List[Dict],
        episode_duration: float = None
    ) -> List[Dict]:
        """
        Create chapters from content segments between removed ads.

        Args:
            segments: Transcript segments
            ads_removed: List of removed ads
            episode_duration: Total episode duration (optional)

        Returns:
            List of auto-generated chapters
        """
        if not segments:
            return []

        # Get episode duration from segments if not provided
        if episode_duration is None:
            episode_duration = segments[-1].get('end', 0) if segments else 0

        if not ads_removed:
            # No ads removed - just create intro and outro chapters
            return [
                {'original_time': 0, 'title': None, 'source': 'auto', 'needs_title': True}
            ]

        chapters = []
        sorted_ads = sorted(ads_removed, key=lambda x: x['start'])

        # Content before first ad
        first_ad_start = sorted_ads[0].get('start', 0)
        if first_ad_start >= MIN_CHAPTER_DURATION:
            chapters.append({
                'original_time': 0,
                'title': None,
                'source': 'auto',
                'needs_title': True
            })

        # Content between ads
        for i in range(len(sorted_ads)):
            ad_end = sorted_ads[i].get('end', 0)

            # Find next ad start or episode end
            if i < len(sorted_ads) - 1:
                next_ad_start = sorted_ads[i + 1].get('start', 0)
            else:
                next_ad_start = episode_duration

            # Only create chapter if gap is long enough
            gap_duration = next_ad_start - ad_end
            if gap_duration >= MIN_CHAPTER_DURATION:
                chapters.append({
                    'original_time': ad_end,
                    'title': None,
                    'source': 'auto',
                    'needs_title': True
                })

        logger.info(f"Detected {len(chapters)} chapters from ad boundaries")
        return chapters

    def merge_chapters(
        self,
        description_chapters: List[Dict],
        ad_gap_chapters: List[Dict],
        ads_removed: List[Dict]
    ) -> List[Dict]:
        """
        Merge chapters from description and ad gaps.

        Description chapters take priority. Ad gap chapters fill in gaps.

        Args:
            description_chapters: Chapters parsed from description
            ad_gap_chapters: Auto-generated chapters from ad boundaries
            ads_removed: List of removed ads

        Returns:
            Merged and adjusted chapter list
        """
        # Start with description chapters (they have titles)
        merged = []

        for chapter in description_chapters:
            adjusted_time = self.adjust_timestamp(chapter['original_time'], ads_removed)
            merged.append({
                'startTime': adjusted_time,
                'title': chapter['title'],
                'source': 'description',
                'needs_title': False
            })

        # Add ad gap chapters that don't overlap with description chapters
        for chapter in ad_gap_chapters:
            adjusted_time = self.adjust_timestamp(chapter['original_time'], ads_removed)

            # Check if this time is close to an existing chapter (within 60 seconds)
            is_duplicate = False
            for existing in merged:
                if abs(existing['startTime'] - adjusted_time) < 60:
                    is_duplicate = True
                    break

            if not is_duplicate:
                merged.append({
                    'startTime': adjusted_time,
                    'title': chapter.get('title'),
                    'source': 'auto',
                    'needs_title': chapter.get('needs_title', True)
                })

        # Sort by time
        merged.sort(key=lambda x: x['startTime'])

        # Ensure first chapter starts at 0
        if merged and merged[0]['startTime'] > 10:
            merged.insert(0, {
                'startTime': 0,
                'title': 'Introduction',
                'source': 'auto',
                'needs_title': False
            })
        elif merged and merged[0]['startTime'] <= 10:
            merged[0]['startTime'] = 0

        return merged

    def split_long_segments(
        self,
        chapters: List[Dict],
        segments: List[Dict],
        ads_removed: List[Dict],
        episode_duration: float,
        max_segment_duration: float = 900.0  # 15 minutes
    ) -> List[Dict]:
        """
        Split long segments using AI to detect topic changes.

        Args:
            chapters: Current chapter list
            segments: Transcript segments
            ads_removed: List of removed ads
            episode_duration: Total episode duration
            max_segment_duration: Max segment length before splitting (default 15 min)

        Returns:
            Updated chapter list with long segments split
        """
        if not segments or not chapters:
            return chapters

        self._initialize_client()
        if not self.client:
            logger.warning("No Anthropic client available for segment splitting")
            return chapters

        result = []
        for i, chapter in enumerate(chapters):
            result.append(chapter)

            # Get end time of this segment
            if i + 1 < len(chapters):
                segment_end = chapters[i + 1]['startTime']
            else:
                # Last chapter - use episode duration adjusted for removed ads
                total_ad_duration = sum(
                    ad.get('end', 0) - ad.get('start', 0)
                    for ad in ads_removed
                )
                segment_end = episode_duration - total_ad_duration

            segment_duration = segment_end - chapter['startTime']

            # Skip if segment is short enough
            if segment_duration <= max_segment_duration:
                logger.debug(f"Chapter {i}: {segment_duration:.0f}s <= {max_segment_duration}s, skipping split")
                continue

            logger.info(f"Chapter {i}: {segment_duration:.0f}s > {max_segment_duration}s, attempting AI split")

            # Get transcript for this long segment
            # Convert adjusted times back to original times for transcript lookup
            original_start = self._reverse_adjust_timestamp(chapter['startTime'], ads_removed)
            original_end = self._reverse_adjust_timestamp(segment_end, ads_removed)

            transcript_text = self._get_full_transcript_range(segments, original_start, original_end)
            if not transcript_text or len(transcript_text) < 500:
                continue

            # Ask Claude to find topic boundaries
            num_splits = min(int(segment_duration / 600), 4)  # ~10 min chunks, max 4 splits
            if num_splits < 1:
                logger.debug(f"Chapter {i}: num_splits < 1, skipping")
                continue

            logger.info(f"Chapter {i}: Requesting {num_splits} topic boundaries from AI (original {original_start:.0f}-{original_end:.0f}, transcript {len(transcript_text)} chars)")

            try:
                new_chapters = self._detect_topic_boundaries(
                    transcript_text, original_start, original_end, num_splits
                )

                # Adjust times and add to result
                for new_chapter in new_chapters:
                    adjusted_time = self.adjust_timestamp(new_chapter['original_time'], ads_removed)
                    # Skip if too close to existing chapter
                    if any(abs(ch['startTime'] - adjusted_time) < 60 for ch in result):
                        continue
                    result.append({
                        'startTime': adjusted_time,
                        'title': new_chapter.get('title'),
                        'source': 'ai_split',
                        'needs_title': not new_chapter.get('title')
                    })
            except Exception as e:
                logger.warning(f"Failed to split long segment: {e}")
                continue

        # Re-sort after adding new chapters
        result.sort(key=lambda x: x['startTime'])
        return result

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

    def detect_topics_from_description(
        self,
        description: str,
        segments: List[Dict],
        ads_removed: List[Dict],
        episode_duration: float
    ) -> List[Dict]:
        """
        Detect chapters from topic sections in description (no timestamps).

        Handles descriptions like:
        <p>Windows 11</p><ul><li>Feature 1</li>...</ul>
        <p>AI</p><ul><li>Item 1</li>...</ul>

        Args:
            description: Episode description HTML
            segments: Transcript segments
            ads_removed: Removed ads list
            episode_duration: Total duration

        Returns:
            List of chapters with matched timestamps
        """
        if not description or not segments:
            return []

        # Extract topic headers from HTML structure
        topics = self._extract_topic_headers(description)
        if len(topics) < 2:
            return []

        logger.info(f"Found {len(topics)} topic headers in description: {topics}")

        self._initialize_client()
        if not self.client:
            return []

        # Get transcript summary for matching
        transcript_summary = self._get_transcript_summary(segments, max_chars=6000)

        # Ask Claude to match topics to transcript positions
        return self._match_topics_to_transcript(
            topics, transcript_summary, segments, ads_removed
        )

    def _extract_topic_headers(self, description: str) -> List[str]:
        """
        Extract topic headers from HTML description.

        Looks for patterns like:
        - <p>Topic Name</p> followed by <ul>
        - <strong>Topic Name</strong>
        - <h2>Topic Name</h2>, <h3>Topic Name</h3>
        """
        topics = []

        # Pattern 1: <p>Short text</p> followed by <ul> (common podcast show notes format)
        # Match: <p>Windows 11</p><ul> or <p>AI</p><ul>
        pattern1 = r'<p>([^<]{2,40})</p>\s*<ul'
        for match in re.finditer(pattern1, description, re.IGNORECASE):
            header = match.group(1).strip()
            # Skip if it looks like a sentence (has lowercase after first word)
            if header and not re.search(r'\.\s|,\s', header):
                topics.append(header)

        # Pattern 2: <strong>Topic</strong> or <b>Topic</b> as standalone
        pattern2 = r'<(?:strong|b)>([^<]{2,40})</(?:strong|b)>'
        for match in re.finditer(pattern2, description, re.IGNORECASE):
            header = match.group(1).strip()
            if header and len(header) < 40:
                topics.append(header)

        # Pattern 3: <h2> or <h3> headers
        pattern3 = r'<h[23][^>]*>([^<]{2,40})</h[23]>'
        for match in re.finditer(pattern3, description, re.IGNORECASE):
            header = match.group(1).strip()
            if header:
                topics.append(header)

        # Pattern 4: Look in plain text for short lines followed by list items
        text = self._html_to_text(description)
        lines = text.split('\n')
        for i, line in enumerate(lines):
            line = line.strip()

            # Skip empty, very short, or very long lines
            if not line or len(line) < 2 or len(line) > 40:
                continue

            # Skip lines that look like list items or timestamps
            if line.startswith('-') or line.startswith('*') or re.match(r'^\d', line):
                continue

            # Check if this is a short standalone line (potential header)
            # followed by content (non-empty next line or end of list)
            if i + 1 < len(lines):
                next_line = lines[i + 1].strip()
                # Short line followed by content = likely a header
                if next_line and len(line) < 30 and not re.search(r'[.!?]$', line):
                    topics.append(line)

        # Deduplicate while preserving order
        seen = set()
        unique_topics = []
        for topic in topics:
            topic_lower = topic.lower().strip()
            if topic_lower not in seen and len(topic_lower) > 1:
                seen.add(topic_lower)
                unique_topics.append(topic)

        return unique_topics[:10]  # Max 10 topics

    def _get_transcript_summary(self, segments: List[Dict], max_chars: int = 6000) -> str:
        """Get transcript with timestamps, sampled if too long."""
        lines = []
        total_chars = 0

        # Sample every Nth segment if needed
        step = max(1, len(segments) // 200)

        for i, segment in enumerate(segments):
            if i % step != 0:
                continue

            text = segment.get('text', '').strip()
            if not text:
                continue

            start = segment.get('start', 0)
            mins = int(start // 60)
            secs = int(start % 60)
            line = f"[{mins:02d}:{secs:02d}] {text}"

            if total_chars + len(line) > max_chars:
                break

            lines.append(line)
            total_chars += len(line)

        return '\n'.join(lines)

    def _match_topics_to_transcript(
        self,
        topics: List[str],
        transcript: str,
        segments: List[Dict],
        ads_removed: List[Dict]
    ) -> List[Dict]:
        """Use Claude to match topic headers to transcript timestamps."""
        topics_list = '\n'.join(f"- {t}" for t in topics)

        prompt = f"""Match these podcast episode topics to their approximate start times in the transcript.

Topics from episode description:
{topics_list}

For each topic, find where it's discussed in the transcript and provide the timestamp.
Format your response as one line per topic:
MM:SS Topic Name

Only include topics you can clearly identify in the transcript. Skip topics you can't find.

Transcript:
{transcript}"""

        try:
            response = self.client.messages.create(
                model="claude-3-5-haiku-20241022",
                max_tokens=400,
                temperature=0.3,
                messages=[{"role": "user", "content": prompt}]
            )

            result_text = response.content[0].text.strip()
            chapters = []

            for line in result_text.split('\n'):
                line = line.strip()
                if not line:
                    continue

                match = re.match(r'^(\d{1,2}:\d{2})\s+(.+)$', line)
                if match:
                    timestamp_str, title = match.groups()
                    try:
                        original_time = self.parse_timestamp_to_seconds(timestamp_str)
                        adjusted_time = self.adjust_timestamp(original_time, ads_removed)
                        chapters.append({
                            'startTime': adjusted_time,
                            'title': title.strip(),
                            'source': 'topic_match',
                            'needs_title': False
                        })
                    except (ValueError, IndexError):
                        continue

            logger.info(f"Matched {len(chapters)} topics to transcript positions")
            return chapters

        except Exception as e:
            logger.error(f"Failed to match topics to transcript: {e}")
            return []

    def _detect_topic_boundaries(
        self,
        transcript: str,
        start_time: float,
        end_time: float,
        num_splits: int
    ) -> List[Dict]:
        """
        Use Claude to detect topic boundaries in transcript.

        Returns list of {'original_time': float, 'title': str}
        """
        prompt = f"""Analyze this podcast transcript segment and identify {num_splits} major topic changes.

The segment runs from {int(start_time//60)}:{int(start_time%60):02d} to {int(end_time//60)}:{int(end_time%60):02d}.

For each topic change, provide the timestamp (from the [MM:SS] markers) and a short title (3-7 words).

OUTPUT FORMAT:
Return ONLY topic lines, one per line. No introduction, no explanation, no numbering.
Each line must be exactly: MM:SS Topic Title Here

Example:
05:30 Discussion of AI Trends
12:45 New Product Announcements

Only include clear topic transitions, not minor tangents. Skip the very beginning since that's already a chapter.

Transcript:
{transcript[:8000]}"""

        try:
            response = self.client.messages.create(
                model="claude-3-5-haiku-20241022",
                max_tokens=300,
                temperature=0.3,
                messages=[{"role": "user", "content": prompt}]
            )

            result_text = response.content[0].text.strip()
            logger.info(f"Claude topic detection response ({len(result_text)} chars):\n{result_text}")
            chapters = []

            for line in result_text.split('\n'):
                line = line.strip()
                if not line:
                    continue

                # Skip any preamble (Claude sometimes ignores instructions)
                if line.lower().startswith(('here', 'based', 'the ', 'i ', 'these')):
                    logger.debug(f"Skipping preamble: {line[:50]}")
                    continue

                # Strip any leading markers Claude might add despite instructions
                cleaned = re.sub(r'^[\d]+[.)]\s*', '', line)  # "1." or "1)"
                cleaned = re.sub(r'^[-*]+\s*', '', cleaned)   # "-" or "*"
                cleaned = cleaned.strip()

                # Parse "MM:SS Title" format (what we asked for)
                # Also handle minor variations Claude might produce
                match = re.match(r'^(\d{1,2}:\d{2}(?::\d{2})?)\s*[-:]?\s*(.+)$', cleaned)
                if match:
                    timestamp_str, title = match.groups()
                    try:
                        seconds = self.parse_timestamp_to_seconds(timestamp_str)
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

            logger.info(f"AI detected {len(chapters)} topic boundaries in long segment")
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
        """
        Get transcript excerpt for a time range.

        Delegates to utils.text.extract_text_from_segments.

        Args:
            segments: Transcript segments
            start_time: Start of range (in original/unadjusted time)
            end_time: End of range (in original/unadjusted time)
            max_words: Maximum words to include

        Returns:
            Transcript excerpt text
        """
        return extract_text_from_segments(segments, start_time, end_time, max_words)

    def generate_chapter_titles(
        self,
        chapters: List[Dict],
        segments: List[Dict],
        podcast_name: str,
        episode_title: str,
        ads_removed: List[Dict]
    ) -> List[Dict]:
        """
        Generate titles for chapters that need them using Claude.

        Args:
            chapters: Chapters with some needing titles
            segments: Transcript segments
            podcast_name: Name of the podcast
            episode_title: Episode title
            ads_removed: Removed ads (for reverse timestamp lookup)

        Returns:
            Chapters with titles generated
        """
        # Find chapters that need titles
        chapters_needing_titles = [
            (i, ch) for i, ch in enumerate(chapters)
            if ch.get('needs_title', False) and ch.get('title') is None
        ]

        if not chapters_needing_titles:
            return chapters

        # Initialize client
        self._initialize_client()
        if not self.client:
            logger.warning("Claude client not available, using generic titles")
            return self._apply_generic_titles(chapters)

        # Prepare batch request for all chapters
        chapter_requests = []
        for idx, chapter in chapters_needing_titles:
            # Find the time range for this chapter (until next chapter or end)
            start_time = chapter['startTime']
            if idx + 1 < len(chapters):
                end_time = chapters[idx + 1]['startTime']
            else:
                end_time = start_time + 600  # 10 minutes max

            # Need to reverse the timestamp adjustment to get original times
            # for transcript lookup
            original_start = self._reverse_adjust_timestamp(start_time, ads_removed)
            original_end = self._reverse_adjust_timestamp(end_time, ads_removed)

            excerpt = self.get_transcript_excerpt(segments, original_start, original_end)

            chapter_requests.append({
                'index': idx,
                'excerpt': excerpt,
                'position': 'start' if idx == 0 else ('end' if idx == len(chapters) - 1 else 'middle')
            })

        # Generate titles using Claude
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

    def _reverse_adjust_timestamp(self, adjusted_time: float, ads_removed: List[Dict]) -> float:
        """
        Reverse the timestamp adjustment to get original time.

        Maps adjusted (post-ad-removal) time back to original time.

        Args:
            adjusted_time: Time after ad removal
            ads_removed: List of removed ads

        Returns:
            Original timestamp
        """
        if not ads_removed:
            return adjusted_time

        sorted_ads = sorted(ads_removed, key=lambda x: x['start'])

        # Calculate cumulative ad duration up to each point
        # Original time = Adjusted time + total ad duration before that point
        total_ad_duration = 0.0
        last_ad_end = 0.0

        for ad in sorted_ads:
            ad_start = ad.get('start', 0)
            ad_end = ad.get('end', 0)
            ad_duration = ad_end - ad_start

            # Calculate what original time this adjusted_time would be at
            # if we stopped here
            original_candidate = adjusted_time + total_ad_duration

            # If the candidate falls before this ad starts, we found it
            if original_candidate < ad_start:
                return original_candidate

            # Add this ad's duration
            total_ad_duration += ad_duration
            last_ad_end = ad_end

        # After all ads, just add total duration
        return adjusted_time + total_ad_duration

    def _call_claude_for_titles(
        self,
        chapter_requests: List[Dict],
        podcast_name: str,
        episode_title: str
    ) -> List[str]:
        """
        Call Claude API to generate chapter titles.

        Args:
            chapter_requests: List of chapter info with excerpts
            podcast_name: Podcast name
            episode_title: Episode title

        Returns:
            List of generated titles
        """
        # Build prompt
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
            from anthropic import APIError, RateLimitError

            response = self.client.messages.create(
                model="claude-3-5-haiku-20241022",  # Use Haiku for cost efficiency
                max_tokens=500,
                temperature=0.3,
                messages=[{"role": "user", "content": prompt}]
            )

            # Parse response
            response_text = response.content[0].text.strip()
            titles = [line.strip() for line in response_text.split('\n') if line.strip()]

            # Ensure we have the right number of titles
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
        """
        Apply generic titles to chapters that need them.

        Args:
            chapters: Chapter list

        Returns:
            Chapters with generic titles applied
        """
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
        ads_removed: List[Dict]
    ) -> List[Dict]:
        """Enforce minimum chapter duration across all sources.

        Removes chapters that are too short by merging them into the
        previous chapter (the previous chapter absorbs the short one).
        The first chapter is never removed.

        Args:
            chapters: Sorted list of chapters with 'startTime'
            episode_duration: Total episode duration
            ads_removed: Removed ads for duration calculation

        Returns:
            Filtered chapter list with short chapters removed
        """
        if len(chapters) <= 1:
            return chapters

        # Calculate adjusted episode duration
        total_ad_duration = sum(
            ad.get('end', 0) - ad.get('start', 0)
            for ad in ads_removed
        )
        adjusted_duration = episode_duration - total_ad_duration

        result = [chapters[0]]  # Always keep first chapter

        for i in range(1, len(chapters)):
            chapter = chapters[i]
            prev = result[-1]

            # Calculate this chapter's duration
            if i + 1 < len(chapters):
                chapter_duration = chapters[i + 1]['startTime'] - chapter['startTime']
            else:
                chapter_duration = adjusted_duration - chapter['startTime']

            if chapter_duration < MIN_CHAPTER_DURATION:
                # Too short - absorb into previous chapter
                # If the short chapter has a better title, keep it on the previous
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

    def format_chapters_json(self, chapters: List[Dict]) -> str:
        """
        Format chapters as Podcasting 2.0 JSON.

        Args:
            chapters: List of chapters

        Returns:
            JSON string
        """
        # Clean up chapters for output
        # Use integers for startTime (some podcast apps don't handle floats)
        # Use min value of 1 (some apps expect chapters to start at 1, not 0)
        output_chapters = []
        for chapter in chapters:
            output_chapter = {
                'startTime': max(1, int(round(chapter['startTime']))),
                'title': chapter.get('title', 'Untitled')
            }
            output_chapters.append(output_chapter)

        output = {
            'version': '1.2.0',
            'chapters': output_chapters
        }

        return json.dumps(output, indent=2)

    def generate_chapters(
        self,
        segments: List[Dict],
        ads_removed: List[Dict],
        episode_description: str = None,
        podcast_name: str = "Unknown",
        episode_title: str = "Unknown"
    ) -> Dict:
        """
        Generate complete chapters for an episode.

        Args:
            segments: Transcript segments
            ads_removed: List of removed ads
            episode_description: Episode description (optional)
            podcast_name: Podcast name
            episode_title: Episode title

        Returns:
            Chapters dict ready for JSON serialization
        """
        logger.info(f"Generating chapters for '{episode_title}'")

        # Step 1: Parse description timestamps
        description_chapters = self.parse_description_timestamps(episode_description)

        # Step 2: Detect ad gap chapters
        episode_duration = segments[-1].get('end', 0) if segments else 0
        ad_gap_chapters = self.detect_ad_gap_chapters(segments, ads_removed, episode_duration)

        # Step 3: Merge chapters
        merged_chapters = self.merge_chapters(
            description_chapters, ad_gap_chapters, ads_removed
        )

        # Step 3b: If no description chapters found, try topic-based detection
        if not description_chapters and episode_description:
            topic_chapters = self.detect_topics_from_description(
                episode_description, segments, ads_removed, episode_duration
            )
            if topic_chapters:
                for ch in topic_chapters:
                    # Skip if too close to existing chapter
                    if any(abs(existing['startTime'] - ch['startTime']) < 60 for existing in merged_chapters):
                        continue
                    merged_chapters.append(ch)
                merged_chapters.sort(key=lambda x: x['startTime'])

        # Step 3c: Split long segments using AI topic detection
        if segments:
            merged_chapters = self.split_long_segments(
                merged_chapters, segments, ads_removed, episode_duration
            )

        # Step 3d: Enforce minimum chapter duration across all sources
        merged_chapters = self._enforce_min_duration(merged_chapters, episode_duration, ads_removed)

        # Step 4: Generate titles for chapters that need them
        if segments:
            merged_chapters = self.generate_chapter_titles(
                merged_chapters, segments, podcast_name, episode_title, ads_removed
            )
        else:
            merged_chapters = self._apply_generic_titles(merged_chapters)

        # Step 5: Build output
        # Use integers for startTime (some podcast apps don't handle floats)
        # Use min value of 1 (some apps expect chapters to start at 1, not 0)
        output_chapters = []
        for chapter in merged_chapters:
            output_chapters.append({
                'startTime': max(1, int(round(chapter['startTime']))),
                'title': chapter.get('title', 'Untitled')
            })

        logger.info(f"Generated {len(output_chapters)} chapters")

        return {
            'version': '1.2.0',
            'chapters': output_chapters
        }

    def generate_chapters_from_vtt(
        self,
        segments: List[Dict],
        episode_description: str = None,
        podcast_name: str = "Unknown",
        episode_title: str = "Unknown"
    ) -> Dict:
        """
        Generate chapters from VTT segments (already adjusted for ad removal).

        This is used for regenerating chapters without full reprocessing.
        VTT timestamps are already adjusted, so no ad-based adjustment is done.
        Uses AI to detect topic changes in the content.

        Args:
            segments: VTT transcript segments (already adjusted)
            episode_description: Episode description (optional)
            podcast_name: Podcast name
            episode_title: Episode title

        Returns:
            Chapters dict ready for JSON serialization
        """
        logger.info(f"Generating chapters from VTT for '{episode_title}'")

        if not segments:
            return {'version': '1.2.0', 'chapters': []}

        episode_duration = segments[-1].get('end', 0)

        # Start with intro chapter
        chapters = [{
            'startTime': 0,
            'title': None,
            'source': 'auto',
            'needs_title': True
        }]

        # Use AI to detect topic changes if episode is long enough
        if episode_duration > 900:  # > 15 minutes
            self._initialize_client()
            if self.client:
                # Get full transcript with timestamps
                transcript_text = self._get_full_transcript_range(segments, 0, episode_duration)

                if transcript_text and len(transcript_text) > 500:
                    # Calculate how many topic splits we want
                    num_splits = min(int(episode_duration / 600), 6)  # ~10 min chunks, max 6

                    logger.info(f"VTT regeneration: Requesting {num_splits} topic boundaries from AI for {episode_duration:.0f}s episode")

                    try:
                        new_chapters = self._detect_topic_boundaries(
                            transcript_text, 0, episode_duration, num_splits
                        )

                        for ch in new_chapters:
                            # VTT times are already adjusted, use original_time directly
                            chapters.append({
                                'startTime': ch['original_time'],
                                'title': ch.get('title'),
                                'source': 'ai_vtt',
                                'needs_title': not ch.get('title')
                            })
                    except Exception as e:
                        logger.warning(f"Failed to detect topic boundaries: {e}")

        # Sort and deduplicate
        chapters.sort(key=lambda x: x['startTime'])

        # Remove chapters too close together (< 60 seconds)
        deduplicated = []
        for ch in chapters:
            if not deduplicated or ch['startTime'] - deduplicated[-1]['startTime'] >= 60:
                deduplicated.append(ch)
        chapters = deduplicated

        # Enforce minimum chapter duration
        chapters = self._enforce_min_duration(chapters, episode_duration, [])

        # Generate titles for chapters that need them
        chapters = self.generate_chapter_titles(
            chapters, segments, podcast_name, episode_title, []  # No ads for VTT
        )

        # Build output
        output_chapters = []
        for chapter in chapters:
            output_chapters.append({
                'startTime': max(1, int(round(chapter['startTime']))),
                'title': chapter.get('title', 'Untitled')
            })

        logger.info(f"Generated {len(output_chapters)} chapters from VTT")

        return {
            'version': '1.2.0',
            'chapters': output_chapters
        }
