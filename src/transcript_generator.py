"""VTT transcript generator for Podcasting 2.0 support."""
import logging
from typing import List, Dict, Optional

from config import SEGMENT_AD_COVERAGE_THRESHOLD
from utils.time import parse_timestamp, format_vtt_timestamp, adjust_timestamp

logger = logging.getLogger(__name__)


class TranscriptGenerator:
    """Generate WebVTT transcripts from Whisper segments with timestamp adjustment."""

    def is_segment_in_ad(self, segment: Dict, ads_removed: List[Dict]) -> bool:
        """Check if a removed ad (or several together) cover the segment.

        Returns True when the union of all removed-ad overlaps covers >80% of
        the segment. Measuring against the union, not each ad alone, is what
        catches a segment split across two adjacent cuts: a pass-1 cut and a
        pass-2 re-cut can each take ~half of a straddling segment without
        either reaching 80% on its own, yet together remove almost all of it.
        Checking ads individually left that segment (and its ad text) in the
        transcript even though the audio for it was gone.
        """
        return self._ad_coverage(segment, ads_removed) > SEGMENT_AD_COVERAGE_THRESHOLD

    def _ad_coverage(self, segment: Dict, ads_removed: List[Dict]) -> float:
        """Fraction of the segment covered by the union of removed-ad overlaps."""
        if not ads_removed:
            return 0.0

        seg_start = segment.get('start', 0)
        seg_end = segment.get('end', 0)
        segment_duration = seg_end - seg_start
        if segment_duration <= 0:
            return 0.0

        # Clip each ad to the segment, then merge the clipped intervals so two
        # cuts overlapping the same slice are not double-counted.
        clipped = []
        for ad in ads_removed:
            s = max(seg_start, ad.get('start', 0))
            e = min(seg_end, ad.get('end', 0))
            if e > s:
                clipped.append((s, e))
        if not clipped:
            return 0.0

        clipped.sort()
        covered = 0.0
        cur_start, cur_end = clipped[0]
        for s, e in clipped[1:]:
            if s <= cur_end:
                cur_end = max(cur_end, e)
            else:
                covered += cur_end - cur_start
                cur_start, cur_end = s, e
        covered += cur_end - cur_start

        return covered / segment_duration

    def _trim_ad_words(self, segment: Dict, ads_removed: List[Dict]) -> Optional[Dict]:
        """Drop the words of a partially-covered segment that sit inside a cut.

        A segment under the coverage threshold is kept, but without trimming
        its in-cut words the removed ad's text bleeds into the transcript.
        Uses each word's midpoint against the raw cut intervals (midpoint in
        any interval == midpoint in their union, so no merge is needed).

        Returns the segment unchanged when there is nothing to trim or no
        usable word timing, a rebuilt segment when words were dropped, or
        None when no words survive.
        """
        words = segment.get('words') or []
        # Key-presence is not enough: the API transcription path can carry
        # JSON nulls through as None values.
        if not words or any(not isinstance(w.get('start'), (int, float))
                            or not isinstance(w.get('end'), (int, float))
                            for w in words):
            return segment

        kept = [
            w for w in words
            if not any(ad.get('start', 0) < (w['start'] + w['end']) / 2 < ad.get('end', 0)
                       for ad in ads_removed)
        ]
        if len(kept) == len(words):
            # Keep the original text byte-for-byte when nothing was dropped.
            return segment
        if not kept:
            return None

        return {
            'start': kept[0]['start'],
            'end': kept[-1]['end'],
            'text': ''.join(w.get('word', '') for w in kept).strip(),
        }

    def compute_final_segments(
        self,
        segments: List[Dict],
        ads_removed: List[Dict]
    ) -> List[Dict]:
        """Apply ad-removal filter + timestamp adjustment, return surviving segments.

        Mirrors the filter/adjust pass used by generate_vtt and generate_text,
        producing the post-cut segment list as plain dicts so it can be persisted
        as final_segments_json alongside the VTT.
        """
        out = []
        for segment in segments:
            coverage = self._ad_coverage(segment, ads_removed)
            if coverage > SEGMENT_AD_COVERAGE_THRESHOLD:
                continue
            if coverage > 0:
                # Partially covered: trim the in-cut words so the removed
                # ad's text does not bleed into the transcript.
                segment = self._trim_ad_words(segment, ads_removed)
                if segment is None:
                    continue
            text = segment.get('text', '').strip()
            if not text:
                continue
            adjusted_start = adjust_timestamp(segment.get('start', 0), ads_removed)
            adjusted_end = adjust_timestamp(segment.get('end', 0), ads_removed)
            if adjusted_end <= adjusted_start:
                continue
            out.append({'start': adjusted_start, 'end': adjusted_end, 'text': text})
        return out

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

        final = self.compute_final_segments(segments, ads_removed)
        for seg in final:
            start_ts = format_vtt_timestamp(seg['start'])
            end_ts = format_vtt_timestamp(seg['end'])
            lines.append(f"{start_ts} --> {end_ts}")
            lines.append(seg['text'])
            lines.append("")

        logger.info(f"Generated VTT with {len(final)} cues from {len(segments)} segments")

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

        final = self.compute_final_segments(segments, ads_removed)
        lines = [
            f"[{format_vtt_timestamp(seg['start'])} --> {format_vtt_timestamp(seg['end'])}] {seg['text']}"
            for seg in final
        ]

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
