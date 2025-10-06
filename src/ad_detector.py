"""Ad detection using Claude API."""
import logging
import json
import os
from typing import List, Dict, Optional
from anthropic import Anthropic

logger = logging.getLogger(__name__)

class AdDetector:
    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or os.environ.get('ANTHROPIC_API_KEY')
        if not self.api_key:
            logger.warning("No Anthropic API key found")
        self.client = None

    def initialize_client(self):
        """Initialize Anthropic client."""
        if self.client is None and self.api_key:
            try:
                from anthropic import Anthropic
                self.client = Anthropic(api_key=self.api_key)
                logger.info("Anthropic client initialized")
            except Exception as e:
                logger.error(f"Failed to initialize Anthropic client: {e}")
                raise

    def detect_ads(self, segments: List[Dict]) -> Optional[List[Dict]]:
        """Detect ad segments using Claude API."""
        if not self.api_key:
            logger.warning("Skipping ad detection - no API key")
            return []

        try:
            self.initialize_client()

            # Prepare transcript with timestamps for Claude
            transcript_lines = []
            for segment in segments:
                start = segment['start']
                end = segment['end']
                text = segment['text']
                transcript_lines.append(f"[{start:.1f}s - {end:.1f}s] {text}")

            transcript = "\n".join(transcript_lines)

            # Call Claude API
            logger.info("Sending transcript to Claude for ad detection")

            prompt = """Analyze this podcast transcript and identify advertisement segments. Look for:
- Product endorsements, sponsored content, or promotional messages
- Promo codes, special offers, or calls to action
- Clear transitions to/from ads (e.g., "This episode is brought to you by...")
- Host-read advertisements
- Pre-roll, mid-roll, or post-roll ads
- Long intro sections filled with multiple ads before actual content begins
- Mentions of other podcasts/shows from the network (cross-promotion)
- Sponsor messages about credit cards, apps, products, or services

IMPORTANT: When detecting multi-part ad blocks (e.g., 4 back-to-back ads with minimal gaps), return ONE continuous segment from the start of the first ad to the end of the last ad. Do NOT split continuous ad blocks into multiple segments.

Pay special attention to the beginning of the podcast - if the first few minutes contain multiple back-to-back advertisements before the actual show content starts, mark the entire intro ad block as ONE SINGLE SEGMENT.

Return ONLY a JSON array of ad segments with start/end times in seconds. Be aggressive in detecting ads - it's better to remove too much than too little.

Format:
[{"start": 0.0, "end": 240.0, "reason": "4-minute intro ad block with multiple sponsors"}, ...]

Example of what to do:
- If ads run from 0-60s, 61-120s, 121-180s, 181-240s → Return ONE segment: {"start": 0.0, "end": 240.0}
- Do NOT return multiple segments for continuous ad blocks

If no ads are found, return an empty array: []

Transcript:
""" + transcript

            response = self.client.messages.create(
                model="claude-sonnet-4-5-20250929",  # Use Claude Sonnet 4.5 for better ad detection
                max_tokens=1000,
                temperature=0,
                messages=[{
                    "role": "user",
                    "content": prompt
                }]
            )

            # Extract JSON from response
            response_text = response.content[0].text if response.content else ""
            logger.info(f"Claude response received: {len(response_text)} chars")

            # Try to parse JSON from response
            try:
                # Look for JSON array in response
                start_idx = response_text.find('[')
                end_idx = response_text.rfind(']') + 1
                if start_idx >= 0 and end_idx > start_idx:
                    json_str = response_text[start_idx:end_idx]
                    ads = json.loads(json_str)

                    # Validate structure
                    if isinstance(ads, list):
                        valid_ads = []
                        for ad in ads:
                            if isinstance(ad, dict) and 'start' in ad and 'end' in ad:
                                valid_ads.append({
                                    'start': float(ad['start']),
                                    'end': float(ad['end']),
                                    'reason': ad.get('reason', 'Advertisement detected')
                                })

                        total_ad_time = sum(ad['end'] - ad['start'] for ad in valid_ads)
                        logger.info(f"Claude detected {len(valid_ads)} ad segments (total {total_ad_time/60:.1f} minutes)")

                        # Store full response for debugging
                        return {
                            "ads": valid_ads,
                            "raw_response": response_text,
                            "model": "claude-sonnet-4-5-20250929"
                        }
                else:
                    logger.warning("No JSON array found in Claude response")
                    return {"ads": [], "raw_response": response_text, "error": "No JSON found"}

            except json.JSONDecodeError as e:
                logger.error(f"Failed to parse JSON from Claude response: {e}")
                return {"ads": [], "raw_response": response_text, "error": str(e)}

        except Exception as e:
            logger.error(f"Ad detection failed: {e}")
            return {"ads": [], "error": str(e)}

    def process_transcript(self, segments: List[Dict]) -> Dict:
        """Process transcript for ad detection."""
        result = self.detect_ads(segments)
        if result is None:
            return {"ads": [], "error": "Detection failed"}
        return result