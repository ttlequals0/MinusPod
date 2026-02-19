"""VTT transcript generator for Podcasting 2.0 support."""
import logging
from typing import List, Dict, Optional

from utils.time import parse_timestamp, format_vtt_timestamp, adjust_timestamp

logger = logging.getLogger(__name__)


class TranscriptGenerator:
    """Generate WebVTT transcripts from Whisper segments with timestamp adjustment."""

    def is_segment_in_ad(self, segment: Dict, ads_removed: List[Dict]) -> bool:
        """Check if a segment falls entirely within a removed ad.

        Returns True if the segment is entirely contained in an ad or
        has >80% overlap with a removed ad.
        """
        if not ads_removed:
            return False

        seg_start = segment.get('start', 0)
        seg_end = segment.get('end', 0)

        for ad in ads_removed:
            ad_start = ad.get('start', 0)
            ad_end = ad.get('end', 0)

            # Segment is entirely within ad
            if seg_start >= ad_start and seg_end <= ad_end:
                return True

            # Segment significantly overlaps with ad (>80% overlap)
            overlap_start = max(seg_start, ad_start)
            overlap_end = min(seg_end, ad_end)
            if overlap_end > overlap_start:
                overlap = overlap_end - overlap_start
                segment_duration = seg_end - seg_start
                if segment_duration > 0 and (overlap / segment_duration) > 0.8:
                    return True

        return False

    def generate_vtt(
        self,
        segments: List[Dict],
        ads_removed: List[Dict]
    ) -> str:
        """Generate VTT transcript with adjusted timestamps.

        Args:
            segments: Whisper transcript segments with start, end, text
            ads_removed: Removed ad markers with start, end

        Returns:
            VTT formatted transcript string
        """
        if not segments:
            logger.warning("No segments provided for VTT generation")
            return "WEBVTT\n"

        lines = ["WEBVTT", ""]

        cue_count = 0
        for segment in segments:
            # Skip segments that fall within removed ads
            if self.is_segment_in_ad(segment, ads_removed):
                continue

            text = segment.get('text', '').strip()
            if not text:
                continue

            original_start = segment.get('start', 0)
            original_end = segment.get('end', 0)

            # Adjust timestamps for removed ads
            adjusted_start = adjust_timestamp(original_start, ads_removed)
            adjusted_end = adjust_timestamp(original_end, ads_removed)

            # Skip if timestamps are invalid after adjustment
            if adjusted_end <= adjusted_start:
                continue

            # Format cue
            start_ts = format_vtt_timestamp(adjusted_start)
            end_ts = format_vtt_timestamp(adjusted_end)

            lines.append(f"{start_ts} --> {end_ts}")
            lines.append(text)
            lines.append("")  # Blank line between cues

            cue_count += 1

        logger.info(f"Generated VTT with {cue_count} cues from {len(segments)} segments")

        return "\n".join(lines)

    def generate_text(
        self,
        segments: List[Dict],
        ads_removed: List[Dict]
    ) -> str:
        """Generate plain text transcript with adjusted timestamps.

        Output format: [HH:MM:SS.sss --> HH:MM:SS.sss] text
        """
        if not segments:
            return ""

        lines = []
        for segment in segments:
            if self.is_segment_in_ad(segment, ads_removed):
                continue

            text = segment.get('text', '').strip()
            if not text:
                continue

            original_start = segment.get('start', 0)
            original_end = segment.get('end', 0)

            adjusted_start = adjust_timestamp(original_start, ads_removed)
            adjusted_end = adjust_timestamp(original_end, ads_removed)

            if adjusted_end <= adjusted_start:
                continue

            start_ts = format_vtt_timestamp(adjusted_start)
            end_ts = format_vtt_timestamp(adjusted_end)

            lines.append(f"[{start_ts} --> {end_ts}] {text}")

        logger.info(f"Generated text transcript with {len(lines)} segments")
        return "\n".join(lines)

    def generate_vtt_from_text(
        self,
        transcript_text: str,
        ads_removed: List[Dict]
    ) -> Optional[str]:
        """Generate VTT from stored transcript text format.

        The stored format is:
        [HH:MM:SS.sss --> HH:MM:SS.sss] Text content
        """
        if not transcript_text:
            return None

        segments = []
        for line in transcript_text.split('\n'):
            line = line.strip()
            if not line or not line.startswith('['):
                continue

            try:
                time_part, text_part = line.split('] ', 1)
                time_range = time_part.strip('[')
                start_str, end_str = time_range.split(' --> ')

                segments.append({
                    'start': parse_timestamp(start_str),
                    'end': parse_timestamp(end_str),
                    'text': text_part
                })
            except (ValueError, IndexError):
                continue

        if not segments:
            logger.warning("VTT parsing returned empty segments from transcript text")
            return None

        return self.generate_vtt(segments, ads_removed)
