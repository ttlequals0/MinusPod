"""VTT transcript generator for Podcasting 2.0 support."""
import logging
from typing import List, Dict, Optional

from utils.time import parse_timestamp

logger = logging.getLogger(__name__)


class TranscriptGenerator:
    """Generate WebVTT transcripts from Whisper segments with timestamp adjustment."""

    def adjust_timestamp(self, original_time: float, ads_removed: List[Dict]) -> float:
        """
        Adjust a timestamp to account for removed ads.

        For each ad that ends BEFORE the original timestamp,
        subtract the ad duration from the timestamp.

        Args:
            original_time: Original timestamp in seconds
            ads_removed: List of {'start': float, 'end': float} for REMOVED ads

        Returns:
            Adjusted timestamp reflecting position in processed audio
        """
        if not ads_removed:
            return original_time

        adjustment = 0.0
        sorted_ads = sorted(ads_removed, key=lambda x: x['start'])

        for ad in sorted_ads:
            ad_start = ad.get('start', 0)
            ad_end = ad.get('end', 0)

            if ad_end <= original_time:
                # This entire ad was before our timestamp
                adjustment += (ad_end - ad_start)
            elif ad_start < original_time < ad_end:
                # Our timestamp falls WITHIN an ad - this shouldn't happen
                # for content timestamps, but handle gracefully
                adjustment += (original_time - ad_start)
                break
            else:
                # This ad is after our timestamp
                break

        return max(0.0, original_time - adjustment)

    def is_segment_in_ad(self, segment: Dict, ads_removed: List[Dict]) -> bool:
        """
        Check if a segment falls entirely within a removed ad.

        Args:
            segment: Transcript segment with 'start' and 'end'
            ads_removed: List of removed ads

        Returns:
            True if segment should be excluded from transcript
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

    def format_vtt_timestamp(self, seconds: float) -> str:
        """
        Format seconds as VTT timestamp (HH:MM:SS.mmm).

        Args:
            seconds: Time in seconds

        Returns:
            Formatted timestamp string
        """
        hours = int(seconds // 3600)
        minutes = int((seconds % 3600) // 60)
        secs = seconds % 60

        return f"{hours:02d}:{minutes:02d}:{secs:06.3f}"

    def generate_vtt(
        self,
        segments: List[Dict],
        ads_removed: List[Dict]
    ) -> str:
        """
        Generate VTT transcript with adjusted timestamps.

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
            adjusted_start = self.adjust_timestamp(original_start, ads_removed)
            adjusted_end = self.adjust_timestamp(original_end, ads_removed)

            # Skip if timestamps are invalid after adjustment
            if adjusted_end <= adjusted_start:
                continue

            # Format cue
            start_ts = self.format_vtt_timestamp(adjusted_start)
            end_ts = self.format_vtt_timestamp(adjusted_end)

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
        """
        Generate plain text transcript with adjusted timestamps.

        Output format: [HH:MM:SS.sss --> HH:MM:SS.sss] text

        Args:
            segments: Whisper transcript segments with start, end, text
            ads_removed: Removed ad markers with start, end

        Returns:
            Text transcript with timestamps
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

            adjusted_start = self.adjust_timestamp(original_start, ads_removed)
            adjusted_end = self.adjust_timestamp(original_end, ads_removed)

            if adjusted_end <= adjusted_start:
                continue

            start_ts = self.format_vtt_timestamp(adjusted_start)
            end_ts = self.format_vtt_timestamp(adjusted_end)

            lines.append(f"[{start_ts} --> {end_ts}] {text}")

        logger.info(f"Generated text transcript with {len(lines)} segments")
        return "\n".join(lines)

    def generate_vtt_from_text(
        self,
        transcript_text: str,
        ads_removed: List[Dict]
    ) -> Optional[str]:
        """
        Generate VTT from stored transcript text format.

        The stored format is:
        [HH:MM:SS.sss --> HH:MM:SS.sss] Text content

        Args:
            transcript_text: Transcript in stored text format
            ads_removed: Removed ad markers

        Returns:
            VTT formatted transcript or None if parsing fails
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
            return None

        return self.generate_vtt(segments, ads_removed)
