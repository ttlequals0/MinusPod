"""JSON chapters generator for Podcasting 2.0 support."""
import json
import logging
import os
import re
from typing import List, Dict, Optional, Tuple

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
        parts = timestamp.split(':')
        if len(parts) == 2:
            # M:SS or MM:SS
            return int(parts[0]) * 60 + float(parts[1])
        elif len(parts) == 3:
            # H:MM:SS or HH:MM:SS
            return int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])
        return 0.0

    def parse_description_timestamps(self, description: str) -> List[Dict]:
        """
        Parse timestamps from episode description.

        Args:
            description: Episode description text

        Returns:
            List of {'original_time': float, 'title': str}
        """
        if not description:
            return []

        chapters = []
        seen_times = set()

        for pattern in TIMESTAMP_PATTERNS:
            for match in re.finditer(pattern, description, re.MULTILINE | re.IGNORECASE):
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

    def get_transcript_excerpt(
        self,
        segments: List[Dict],
        start_time: float,
        end_time: float,
        max_words: int = 300
    ) -> str:
        """
        Get transcript excerpt for a time range.

        Args:
            segments: Transcript segments
            start_time: Start of range (in original/unadjusted time)
            end_time: End of range (in original/unadjusted time)
            max_words: Maximum words to include

        Returns:
            Transcript excerpt text
        """
        words = []
        for segment in segments:
            seg_start = segment.get('start', 0)
            seg_end = segment.get('end', 0)

            # Include segments that overlap with range
            if seg_end > start_time and seg_start < end_time:
                text = segment.get('text', '').strip()
                if text:
                    words.extend(text.split())

                if len(words) >= max_words:
                    break

        return ' '.join(words[:max_words])

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

        This is an approximation since we add back ad durations.

        Args:
            adjusted_time: Time after ad removal
            ads_removed: List of removed ads

        Returns:
            Approximate original timestamp
        """
        if not ads_removed:
            return adjusted_time

        sorted_ads = sorted(ads_removed, key=lambda x: x['start'])
        current_adjusted = 0.0
        current_original = 0.0

        for ad in sorted_ads:
            ad_start = ad.get('start', 0)
            ad_end = ad.get('end', 0)
            ad_duration = ad_end - ad_start

            # Calculate adjusted position of ad start
            ad_start_adjusted = ad_start - current_original + current_adjusted

            if adjusted_time <= ad_start_adjusted:
                # Target is before this ad
                return current_original + (adjusted_time - current_adjusted)

            # Move past this ad
            current_original = ad_end
            current_adjusted = ad_start_adjusted

        # Target is after all ads
        return current_original + (adjusted_time - current_adjusted)

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
