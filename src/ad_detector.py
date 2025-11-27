"""Ad detection using Claude API with configurable prompts and model."""
import logging
import json
import os
import re
from typing import List, Dict, Optional
from anthropic import Anthropic

logger = logging.getLogger('podcast.claude')

# Default model - Claude Sonnet 4.5
DEFAULT_MODEL = "claude-sonnet-4-5-20250929"

# Valid model IDs - used to validate saved settings
VALID_MODELS = [
    'claude-sonnet-4-5-20250929',
    'claude-opus-4-5-20251101',
    'claude-sonnet-4-20250514',
    'claude-opus-4-1-20250414',
    'claude-3-5-sonnet-20241022',
]


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
        """Get user prompt template from database or default."""
        try:
            template = self.db.get_setting('user_prompt_template')
            if template:
                return template
        except Exception as e:
            logger.warning(f"Could not load user prompt template from DB: {e}")

        # Default fallback
        from database import DEFAULT_USER_PROMPT_TEMPLATE
        return DEFAULT_USER_PROMPT_TEMPLATE

    def detect_ads(self, segments: List[Dict], podcast_name: str = "Unknown",
                   episode_title: str = "Unknown", slug: str = None,
                   episode_id: str = None) -> Optional[Dict]:
        """Detect ad segments using Claude API."""
        if not self.api_key:
            logger.warning("Skipping ad detection - no API key")
            return {"ads": [], "error": "No API key"}

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

            # Format user prompt
            prompt = user_prompt_template.format(
                podcast_name=podcast_name,
                episode_title=episode_title,
                transcript=transcript
            )

            logger.info(f"[{slug}:{episode_id}] Sending transcript to Claude "
                       f"({len(segments)} segments, {len(transcript)} chars)")

            # Save the prompt for debugging
            if slug and episode_id:
                try:
                    from storage import Storage
                    storage = Storage()
                    storage.save_prompt(slug, episode_id, prompt)
                except Exception as e:
                    logger.warning(f"Could not save prompt: {e}")

            # Call Claude API with configured model
            model = self.get_model()
            logger.debug(f"[{slug}:{episode_id}] Using model: {model}")

            response = self.client.messages.create(
                model=model,
                max_tokens=2000,
                temperature=0.2,
                system=system_prompt,
                messages=[{
                    "role": "user",
                    "content": prompt
                }]
            )

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
                    return {"ads": [], "raw_response": response_text, "error": "No JSON found"}

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
                    logger.info(f"[{slug}:{episode_id}] Detected {len(valid_ads)} ad segments "
                               f"({total_ad_time/60:.1f} min total)")

                    return {
                        "ads": valid_ads,
                        "raw_response": response_text,
                        "model": model
                    }
                else:
                    logger.warning(f"[{slug}:{episode_id}] Response was not a JSON array")
                    return {"ads": [], "raw_response": response_text, "error": "Response not an array"}

            except json.JSONDecodeError as e:
                logger.error(f"[{slug}:{episode_id}] Failed to parse JSON: {e}")
                logger.error(f"[{slug}:{episode_id}] Raw response (first 500 chars): {response_text[:500]}")
                return {"ads": [], "raw_response": response_text, "error": str(e)}

        except Exception as e:
            logger.error(f"[{slug}:{episode_id}] Ad detection failed: {e}")
            return {"ads": [], "error": str(e)}

    def process_transcript(self, segments: List[Dict], podcast_name: str = "Unknown",
                          episode_title: str = "Unknown", slug: str = None,
                          episode_id: str = None) -> Dict:
        """Process transcript for ad detection."""
        result = self.detect_ads(segments, podcast_name, episode_title, slug, episode_id)
        if result is None:
            return {"ads": [], "error": "Detection failed"}
        return result
