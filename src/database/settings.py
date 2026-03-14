"""Settings mixin for MinusPod database."""
import os
import logging
from typing import Optional, Dict, Any, List

logger = logging.getLogger(__name__)

# Default pricing for known Anthropic models (USD per 1M tokens)
DEFAULT_MODEL_PRICING = {
    'claude-opus-4-6':            {'name': 'Claude Opus 4.6',   'input': 5.0,  'output': 25.0},
    'claude-opus-4-5-20251101':   {'name': 'Claude Opus 4.5',   'input': 5.0,  'output': 25.0},
    'claude-opus-4-1-20250805':   {'name': 'Claude Opus 4.1',   'input': 15.0, 'output': 75.0},
    'claude-opus-4-20250514':     {'name': 'Claude Opus 4',     'input': 15.0, 'output': 75.0},
    'claude-sonnet-4-6':          {'name': 'Claude Sonnet 4.6', 'input': 3.0,  'output': 15.0},
    'claude-sonnet-4-5-20250929': {'name': 'Claude Sonnet 4.5', 'input': 3.0,  'output': 15.0},
    'claude-sonnet-4-20250514':   {'name': 'Claude Sonnet 4',   'input': 3.0,  'output': 15.0},
    'claude-haiku-4-5-20251001':  {'name': 'Claude Haiku 4.5',  'input': 1.0,  'output': 5.0},
}


class SettingsMixin:
    """Settings management methods."""

    def get_setting(self, key: str) -> Optional[str]:
        """Get a setting value."""
        conn = self.get_connection()
        cursor = conn.execute(
            "SELECT value FROM settings WHERE key = ?", (key,)
        )
        row = cursor.fetchone()
        return row['value'] if row else None

    def get_all_settings(self) -> Dict[str, Any]:
        """Get all settings as a dictionary."""
        conn = self.get_connection()
        cursor = conn.execute("SELECT key, value, is_default FROM settings")
        settings = {}
        for row in cursor:
            settings[row['key']] = {
                'value': row['value'],
                'is_default': bool(row['is_default'])
            }
        return settings

    def set_setting(self, key: str, value: str, is_default: bool = False):
        """Set a setting value."""
        conn = self.get_connection()
        conn.execute(
            """INSERT INTO settings (key, value, is_default, updated_at)
               VALUES (?, ?, ?, strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
               ON CONFLICT(key) DO UPDATE SET
                 value = excluded.value,
                 is_default = excluded.is_default,
                 updated_at = excluded.updated_at""",
            (key, value, 1 if is_default else 0)
        )
        conn.commit()

    def reset_setting(self, key: str):
        """Reset a setting to its default value."""
        # Import here to avoid circular import
        from database import DEFAULT_SYSTEM_PROMPT, DEFAULT_VERIFICATION_PROMPT
        from ad_detector import DEFAULT_MODEL
        from chapters_generator import CHAPTERS_MODEL
        from llm_client import get_effective_provider, PROVIDER_ANTHROPIC

        # Provider-aware defaults for model settings
        provider = get_effective_provider()
        if provider != PROVIDER_ANTHROPIC:
            env_model = os.environ.get('OPENAI_MODEL')
            model_default = env_model or DEFAULT_MODEL
            chapters_default = env_model or CHAPTERS_MODEL
        else:
            model_default = DEFAULT_MODEL
            chapters_default = CHAPTERS_MODEL

        defaults = {
            'system_prompt': DEFAULT_SYSTEM_PROMPT,
            'verification_prompt': DEFAULT_VERIFICATION_PROMPT,
            'retention_period_minutes': os.environ.get('RETENTION_PERIOD', '1440'),
            'claude_model': model_default,
            'verification_model': model_default,
            'whisper_model': os.environ.get('WHISPER_MODEL', 'small'),
            'vtt_transcripts_enabled': 'true',
            'chapters_enabled': 'true',
            'chapters_model': chapters_default,
            'llm_provider': os.environ.get('LLM_PROVIDER', 'anthropic'),
            'openai_base_url': os.environ.get('OPENAI_BASE_URL', 'http://localhost:8000/v1'),
            'openrouter_api_key': '',  # Reset clears DB value; env var is read at runtime
            'min_cut_confidence': '0.80',
            'auto_process_enabled': 'true',
            'whisper_backend': os.environ.get('WHISPER_BACKEND', 'local'),
            'whisper_api_base_url': os.environ.get('WHISPER_API_BASE_URL', ''),
            'whisper_api_key': '',  # Reset clears DB value; env var is read at runtime
            'whisper_api_model': os.environ.get('WHISPER_API_MODEL', 'whisper-1'),
        }

        if key in defaults:
            self.set_setting(key, defaults[key], is_default=True)
            return True
        return False

    # ========== System Settings Methods (for schema versioning) ==========

    def get_system_setting(self, key: str) -> Optional[str]:
        """Get a system setting value."""
        conn = self.get_connection()
        cursor = conn.execute(
            "SELECT value FROM system_settings WHERE key = ?", (key,)
        )
        row = cursor.fetchone()
        return row['value'] if row else None

    def set_system_setting(self, key: str, value: str):
        """Set a system setting value."""
        conn = self.get_connection()
        conn.execute(
            """INSERT INTO system_settings (key, value, updated_at)
               VALUES (?, ?, strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
               ON CONFLICT(key) DO UPDATE SET
                 value = excluded.value,
                 updated_at = excluded.updated_at""",
            (key, value)
        )
        conn.commit()

    def get_model_pricing(self) -> List[Dict]:
        """Get all model pricing entries."""
        conn = self.get_connection()
        cursor = conn.execute(
            """SELECT model_id, display_name, input_cost_per_mtok, output_cost_per_mtok, updated_at
               FROM model_pricing ORDER BY display_name"""
        )
        return [
            {
                'modelId': row['model_id'],
                'displayName': row['display_name'],
                'inputCostPerMtok': row['input_cost_per_mtok'],
                'outputCostPerMtok': row['output_cost_per_mtok'],
                'updatedAt': row['updated_at'],
            }
            for row in cursor
        ]

    def refresh_model_pricing(self, available_models: List[Dict]):
        """Insert pricing for newly discovered models from DEFAULT_MODEL_PRICING.

        Called when the model list is refreshed via GET /settings/models.
        Uses ON CONFLICT DO NOTHING to preserve any manual price overrides.
        """
        conn = self.get_connection()
        inserted = 0
        for model in available_models:
            model_id = model.get('id', '')
            if model_id in DEFAULT_MODEL_PRICING:
                info = DEFAULT_MODEL_PRICING[model_id]
                cursor = conn.execute(
                    """INSERT INTO model_pricing (model_id, display_name, input_cost_per_mtok, output_cost_per_mtok)
                       VALUES (?, ?, ?, ?)
                       ON CONFLICT(model_id) DO NOTHING""",
                    (model_id, info['name'], info['input'], info['output'])
                )
                if cursor.rowcount > 0:
                    inserted += 1
        conn.commit()
        if inserted > 0:
            logger.info(f"Refreshed model pricing: {inserted} new models added")
