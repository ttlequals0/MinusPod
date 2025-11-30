"""Ad detection using Claude API with configurable prompts and model."""
import logging
import json
import os
import re
import time
import random
from typing import List, Dict, Optional
from anthropic import Anthropic, APIError, APIConnectionError, RateLimitError, InternalServerError

logger = logging.getLogger('podcast.claude')

# Default model - Claude Sonnet 4.5
DEFAULT_MODEL = "claude-sonnet-4-5-20250929"

# User prompt template (not configurable via UI - just formats the transcript)
USER_PROMPT_TEMPLATE = """Podcast: {podcast_name}
Episode: {episode_title}

Transcript:
{transcript}"""

# Valid model IDs - used to validate saved settings
VALID_MODELS = [
    'claude-sonnet-4-5-20250929',
    'claude-opus-4-5-20251101',
    'claude-sonnet-4-20250514',
    'claude-opus-4-1-20250414',
    'claude-3-5-sonnet-20241022',
]

# Retry configuration for transient API errors
RETRY_CONFIG = {
    'max_retries': 3,
    'base_delay': 2.0,      # seconds
    'max_delay': 60.0,      # seconds
    'exponential_base': 2,
    'jitter': True          # Add random jitter to prevent thundering herd
}

# Second pass system prompt for multi-pass detection
SECOND_PASS_SYSTEM_PROMPT = """You are reviewing podcast audio that has ALREADY been processed once for ad detection.

CONTEXT: A first-pass analysis detected and removed these ads (replaced with beeps):
{first_pass_ads}

The transcript you are analyzing is from the PROCESSED audio where those ads are now beeps.

YOUR TASK: Find any ADDITIONAL ads that were MISSED in the first pass.

WHAT TO LOOK FOR:
- Ads with similar patterns to those already detected (same sponsors, similar language)
- Short ad segments (15-30 seconds) that may have been overlooked
- Network-inserted ads without typical transition phrases
- Post-roll ads at the end of the episode
- Any promotional content that sounds like an advertisement

IMPORTANT:
- These sections were initially marked as content - scrutinize carefully
- Look for SIMILAR patterns to the ads already found
- The timestamps in this transcript correspond to the PROCESSED audio, not the original
- If you find an ad, it means the first pass missed it

OUTPUT FORMAT:
Return ONLY a valid JSON array with the same format as first pass:
[{{"start": 0.0, "end": 60.0, "confidence": 0.95, "reason": "Missed ad description", "end_text": "last words"}}]

If no additional ads found: []"""


class AdDetector:
    """Detect advertisements in podcast transcripts using Claude API."""

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or os.environ.get('ANTHROPIC_API_KEY')
        if not self.api_key:
            logger.warning("No Anthropic API key found")
        self.client = None
        self._db = None

    @property
    def db(self):
        """Lazy load database connection."""
        if self._db is None:
            from database import Database
            self._db = Database()
        return self._db

    def initialize_client(self):
        """Initialize Anthropic client."""
        if self.client is None and self.api_key:
            try:
                self.client = Anthropic(api_key=self.api_key)
                logger.info("Anthropic client initialized")
            except Exception as e:
                logger.error(f"Failed to initialize Anthropic client: {e}")
                raise

    def get_available_models(self) -> List[Dict]:
        """Get list of available Claude models from API."""
        try:
            self.initialize_client()
            if not self.client:
                return []

            # Anthropic API models endpoint
            response = self.client.models.list()
            models = []
            for model in response.data:
                # Filter to only include claude models suitable for this task
                if 'claude' in model.id.lower():
                    models.append({
                        'id': model.id,
                        'name': model.display_name if hasattr(model, 'display_name') else model.id,
                        'created': model.created if hasattr(model, 'created') else None
                    })
            return models
        except Exception as e:
            logger.warning(f"Could not fetch models from API: {e}")
            # Return known models as fallback
            return [
                {'id': 'claude-sonnet-4-5-20250929', 'name': 'Claude Sonnet 4.5'},
                {'id': 'claude-opus-4-5-20251101', 'name': 'Claude Opus 4.5'},
                {'id': 'claude-sonnet-4-20250514', 'name': 'Claude Sonnet 4'},
                {'id': 'claude-opus-4-1-20250414', 'name': 'Claude Opus 4.1'},
                {'id': 'claude-3-5-sonnet-20241022', 'name': 'Claude 3.5 Sonnet'},
            ]

    def get_model(self) -> str:
        """Get configured model from database or default."""
        try:
            model = self.db.get_setting('claude_model')
            if model:
                # Validate that model is in the list of known valid models
                if model in VALID_MODELS:
                    return model
                else:
                    logger.warning(f"Invalid model '{model}' in database, clearing and using default")
                    # Clear invalid model from database
                    try:
                        self.db.save_setting('claude_model', DEFAULT_MODEL)
                        logger.info(f"Saved default model '{DEFAULT_MODEL}' to database")
                    except Exception as clear_err:
                        logger.warning(f"Could not clear invalid model from DB: {clear_err}")
        except Exception as e:
            logger.warning(f"Could not load model from DB: {e}")

        return DEFAULT_MODEL

    def get_system_prompt(self) -> str:
        """Get system prompt from database or default."""
        try:
            prompt = self.db.get_setting('system_prompt')
            if prompt:
                return prompt
        except Exception as e:
            logger.warning(f"Could not load system prompt from DB: {e}")

        # Default fallback
        from database import DEFAULT_SYSTEM_PROMPT
        return DEFAULT_SYSTEM_PROMPT

    def get_user_prompt_template(self) -> str:
        """Get user prompt template (hardcoded, not configurable)."""
        return USER_PROMPT_TEMPLATE

    def _is_retryable_error(self, error: Exception) -> bool:
        """Check if an error is transient and should be retried."""
        # Rate limit and connection errors are retryable
        if isinstance(error, (APIConnectionError, RateLimitError)):
            return True
        # Internal server errors (500, 503, 529 overloaded) are retryable
        if isinstance(error, InternalServerError):
            return True
        # Check for specific status codes in generic APIError
        if isinstance(error, APIError):
            status = getattr(error, 'status_code', None)
            if status in (429, 500, 502, 503, 529):
                return True
        return False

    def _calculate_backoff(self, attempt: int) -> float:
        """Calculate exponential backoff delay with optional jitter."""
        delay = min(
            RETRY_CONFIG['base_delay'] * (RETRY_CONFIG['exponential_base'] ** attempt),
            RETRY_CONFIG['max_delay']
        )
        if RETRY_CONFIG['jitter']:
            delay = delay * (0.5 + random.random())  # 50-150% of delay
        return delay

    def detect_ads(self, segments: List[Dict], podcast_name: str = "Unknown",
                   episode_title: str = "Unknown", slug: str = None,
                   episode_id: str = None) -> Optional[Dict]:
        """Detect ad segments using Claude API with retry logic for transient errors."""
        if not self.api_key:
            logger.warning("Skipping ad detection - no API key")
            return {"ads": [], "status": "failed", "error": "No API key", "retryable": False}

        try:
            self.initialize_client()

            # Prepare transcript with timestamps
            transcript_lines = []
            for segment in segments:
                start = segment['start']
                end = segment['end']
                text = segment['text']
                transcript_lines.append(f"[{start:.1f}s - {end:.1f}s] {text}")

            transcript = "\n".join(transcript_lines)

            # Get prompts from database
            system_prompt = self.get_system_prompt()
            user_prompt_template = self.get_user_prompt_template()

            logger.info(f"[{slug}:{episode_id}] Using system prompt ({len(system_prompt)} chars)")
            logger.debug(f"[{slug}:{episode_id}] System prompt first 200 chars: {system_prompt[:200]}...")

            # Format user prompt
            prompt = user_prompt_template.format(
                podcast_name=podcast_name,
                episode_title=episode_title,
                transcript=transcript
            )

            logger.info(f"[{slug}:{episode_id}] Sending transcript to Claude "
                       f"({len(segments)} segments, {len(transcript)} chars)")

            # Call Claude API with configured model and retry logic
            model = self.get_model()
            logger.debug(f"[{slug}:{episode_id}] Using model: {model}")

            response = None
            last_error = None
            max_retries = RETRY_CONFIG['max_retries']

            for attempt in range(max_retries + 1):
                try:
                    response = self.client.messages.create(
                        model=model,
                        max_tokens=2000,
                        temperature=0.0,
                        system=system_prompt,
                        messages=[{
                            "role": "user",
                            "content": prompt
                        }]
                    )
                    break  # Success - exit retry loop
                except Exception as e:
                    last_error = e
                    if self._is_retryable_error(e) and attempt < max_retries:
                        delay = self._calculate_backoff(attempt)
                        logger.warning(
                            f"[{slug}:{episode_id}] API error (attempt {attempt + 1}/{max_retries + 1}): "
                            f"{type(e).__name__}: {e}. Retrying in {delay:.1f}s"
                        )
                        time.sleep(delay)
                        continue
                    else:
                        # Non-retryable error or exhausted retries
                        logger.error(f"[{slug}:{episode_id}] Ad detection failed after {attempt + 1} attempts: {e}")
                        return {
                            "ads": [],
                            "status": "failed",
                            "error": str(e),
                            "error_type": type(e).__name__,
                            "retryable": self._is_retryable_error(e),
                            "prompt": prompt
                        }

            if response is None:
                # Should not reach here, but safety net
                logger.error(f"[{slug}:{episode_id}] Ad detection failed - no response after retries")
                return {
                    "ads": [],
                    "status": "failed",
                    "error": str(last_error) if last_error else "Unknown error",
                    "retryable": True,
                    "prompt": prompt
                }

            # Extract response
            response_text = response.content[0].text if response.content else ""
            logger.info(f"[{slug}:{episode_id}] Claude response: {len(response_text)} chars")

            # Parse JSON from response
            try:
                ads = None

                # Strategy 1: Try to extract from markdown code block first
                code_block_match = re.search(r'```(?:json)?\s*(\[[\s\S]*?\])\s*```', response_text)
                if code_block_match:
                    try:
                        ads = json.loads(code_block_match.group(1))
                        logger.debug(f"[{slug}:{episode_id}] Extracted JSON from code block")
                    except json.JSONDecodeError:
                        pass

                # Strategy 2: Find all potential JSON arrays and use the last valid one
                if ads is None:
                    last_valid_ads = None
                    # Match JSON arrays - use non-greedy to get individual arrays
                    for match in re.finditer(r'\[(?:[^\[\]]*|\[(?:[^\[\]]*|\[[^\[\]]*\])*\])*\]', response_text):
                        try:
                            potential_ads = json.loads(match.group())
                            if isinstance(potential_ads, list):
                                # Check if it looks like ad data (has start/end keys)
                                if not potential_ads or (potential_ads and isinstance(potential_ads[0], dict) and 'start' in potential_ads[0]):
                                    last_valid_ads = potential_ads
                        except json.JSONDecodeError:
                            continue

                    if last_valid_ads is not None:
                        ads = last_valid_ads
                        logger.debug(f"[{slug}:{episode_id}] Found valid JSON array in response")

                # Strategy 3: Fallback to original first-to-last bracket logic
                if ads is None:
                    clean_response = re.sub(r'```json\s*', '', response_text)
                    clean_response = re.sub(r'```\s*', '', clean_response)

                    start_idx = clean_response.find('[')
                    end_idx = clean_response.rfind(']') + 1

                    if start_idx >= 0 and end_idx > start_idx:
                        json_str = clean_response[start_idx:end_idx]
                        ads = json.loads(json_str)

                if ads is None:
                    logger.warning(f"[{slug}:{episode_id}] No JSON array found in response")
                    return {"ads": [], "status": "success", "raw_response": response_text, "prompt": prompt, "error": "No JSON found"}

                if isinstance(ads, list):
                    valid_ads = []
                    for ad in ads:
                        if isinstance(ad, dict) and 'start' in ad and 'end' in ad:
                            valid_ads.append({
                                'start': float(ad['start']),
                                'end': float(ad['end']),
                                'confidence': float(ad.get('confidence', 1.0)),
                                'reason': ad.get('reason', 'Advertisement detected'),
                                'end_text': ad.get('end_text', '')
                            })

                    total_ad_time = sum(ad['end'] - ad['start'] for ad in valid_ads)
                    logger.info(f"[{slug}:{episode_id}] Detected {len(valid_ads)} ad segments "
                               f"({total_ad_time/60:.1f} min total)")

                    return {
                        "ads": valid_ads,
                        "status": "success",
                        "raw_response": response_text,
                        "prompt": prompt,
                        "model": model
                    }
                else:
                    logger.warning(f"[{slug}:{episode_id}] Response was not a JSON array")
                    return {"ads": [], "status": "success", "raw_response": response_text, "prompt": prompt, "error": "Response not an array"}

            except json.JSONDecodeError as e:
                logger.error(f"[{slug}:{episode_id}] Failed to parse JSON: {e}")
                logger.error(f"[{slug}:{episode_id}] Raw response (first 500 chars): {response_text[:500]}")
                return {"ads": [], "status": "success", "raw_response": response_text, "prompt": prompt, "error": str(e)}

        except Exception as e:
            logger.error(f"[{slug}:{episode_id}] Ad detection failed: {e}")
            return {"ads": [], "status": "failed", "error": str(e), "retryable": self._is_retryable_error(e)}

    def process_transcript(self, segments: List[Dict], podcast_name: str = "Unknown",
                          episode_title: str = "Unknown", slug: str = None,
                          episode_id: str = None) -> Dict:
        """Process transcript for ad detection."""
        result = self.detect_ads(segments, podcast_name, episode_title, slug, episode_id)
        if result is None:
            return {"ads": [], "status": "failed", "error": "Detection failed", "retryable": True}
        return result

    def is_multi_pass_enabled(self) -> bool:
        """Check if multi-pass detection is enabled in settings."""
        try:
            setting = self.db.get_setting('multi_pass_enabled')
            return setting and setting.lower() in ('true', '1', 'yes')
        except Exception as e:
            logger.warning(f"Could not check multi_pass_enabled setting: {e}")
            return False

    def detect_ads_second_pass(self, segments: List[Dict], first_pass_ads: List[Dict],
                               podcast_name: str = "Unknown", episode_title: str = "Unknown",
                               slug: str = None, episode_id: str = None) -> Optional[Dict]:
        """Second pass ad detection focusing on what was missed in first pass."""
        if not self.api_key:
            logger.warning("Skipping second pass - no API key")
            return {"ads": [], "status": "failed", "error": "No API key", "retryable": False}

        try:
            self.initialize_client()

            # Prepare transcript with timestamps
            transcript_lines = []
            for segment in segments:
                start = segment['start']
                end = segment['end']
                text = segment['text']
                transcript_lines.append(f"[{start:.1f}s - {end:.1f}s] {text}")

            transcript = "\n".join(transcript_lines)

            # Format first pass ads for context
            first_pass_summary = []
            for ad in first_pass_ads:
                first_pass_summary.append(
                    f"- {ad.get('start', 0):.1f}s to {ad.get('end', 0):.1f}s: {ad.get('reason', 'Ad detected')}"
                )
            first_pass_context = "\n".join(first_pass_summary) if first_pass_summary else "No ads detected in first pass"

            # Build second pass system prompt with first pass context
            system_prompt = SECOND_PASS_SYSTEM_PROMPT.format(first_pass_ads=first_pass_context)

            # Format user prompt
            prompt = USER_PROMPT_TEMPLATE.format(
                podcast_name=podcast_name,
                episode_title=episode_title,
                transcript=transcript
            )

            logger.info(f"[{slug}:{episode_id}] Second pass: Sending transcript to Claude "
                       f"({len(segments)} segments, {len(transcript)} chars)")

            # Call Claude API with retry logic
            model = self.get_model()
            response = None
            last_error = None
            max_retries = RETRY_CONFIG['max_retries']

            for attempt in range(max_retries + 1):
                try:
                    response = self.client.messages.create(
                        model=model,
                        max_tokens=2000,
                        temperature=0.0,
                        system=system_prompt,
                        messages=[{"role": "user", "content": prompt}]
                    )
                    break
                except Exception as e:
                    last_error = e
                    if self._is_retryable_error(e) and attempt < max_retries:
                        delay = self._calculate_backoff(attempt)
                        logger.warning(
                            f"[{slug}:{episode_id}] Second pass API error (attempt {attempt + 1}): {e}. Retrying in {delay:.1f}s"
                        )
                        time.sleep(delay)
                        continue
                    else:
                        logger.error(f"[{slug}:{episode_id}] Second pass failed: {e}")
                        return {
                            "ads": [],
                            "status": "failed",
                            "error": str(e),
                            "retryable": self._is_retryable_error(e)
                        }

            if response is None:
                return {"ads": [], "status": "failed", "error": str(last_error), "retryable": True}

            # Extract and parse response
            response_text = response.content[0].text if response.content else ""
            logger.info(f"[{slug}:{episode_id}] Second pass response: {len(response_text)} chars")

            # Parse JSON - same logic as first pass
            try:
                ads = None
                code_block_match = re.search(r'```(?:json)?\s*(\[[\s\S]*?\])\s*```', response_text)
                if code_block_match:
                    try:
                        ads = json.loads(code_block_match.group(1))
                    except json.JSONDecodeError:
                        pass

                if ads is None:
                    for match in re.finditer(r'\[(?:[^\[\]]*|\[(?:[^\[\]]*|\[[^\[\]]*\])*\])*\]', response_text):
                        try:
                            potential_ads = json.loads(match.group())
                            if isinstance(potential_ads, list):
                                if not potential_ads or (isinstance(potential_ads[0], dict) and 'start' in potential_ads[0]):
                                    ads = potential_ads
                        except json.JSONDecodeError:
                            continue

                if ads is None:
                    clean_response = re.sub(r'```json\s*', '', response_text)
                    clean_response = re.sub(r'```\s*', '', clean_response)
                    start_idx = clean_response.find('[')
                    end_idx = clean_response.rfind(']') + 1
                    if start_idx >= 0 and end_idx > start_idx:
                        ads = json.loads(clean_response[start_idx:end_idx])

                if ads is None:
                    logger.warning(f"[{slug}:{episode_id}] Second pass: No JSON array found")
                    return {"ads": [], "status": "success", "raw_response": response_text}

                if isinstance(ads, list):
                    valid_ads = []
                    for ad in ads:
                        if isinstance(ad, dict) and 'start' in ad and 'end' in ad:
                            valid_ads.append({
                                'start': float(ad['start']),
                                'end': float(ad['end']),
                                'confidence': float(ad.get('confidence', 1.0)),
                                'reason': ad.get('reason', 'Second pass detection'),
                                'end_text': ad.get('end_text', ''),
                                'pass': 2  # Mark as second pass detection
                            })

                    if valid_ads:
                        total_ad_time = sum(ad['end'] - ad['start'] for ad in valid_ads)
                        logger.info(f"[{slug}:{episode_id}] Second pass found {len(valid_ads)} additional ads "
                                   f"({total_ad_time/60:.1f} min)")
                    else:
                        logger.info(f"[{slug}:{episode_id}] Second pass: No additional ads found")

                    return {
                        "ads": valid_ads,
                        "status": "success",
                        "raw_response": response_text,
                        "model": model
                    }
                else:
                    return {"ads": [], "status": "success", "raw_response": response_text}

            except json.JSONDecodeError as e:
                logger.error(f"[{slug}:{episode_id}] Second pass JSON parse error: {e}")
                return {"ads": [], "status": "success", "raw_response": response_text, "error": str(e)}

        except Exception as e:
            logger.error(f"[{slug}:{episode_id}] Second pass failed: {e}")
            return {"ads": [], "status": "failed", "error": str(e), "retryable": self._is_retryable_error(e)}
