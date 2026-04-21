"""Settings mixin for MinusPod database."""
import os
import logging
from typing import Optional, Dict, Any, List

from config import normalize_model_key
from secrets_crypto import CryptoUnavailableError, decrypt, encrypt, is_ciphertext

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
        """Reset a setting to its default value.

        Secret keys (provider api keys) get DELETEd so the env-var
        fallback in ``llm_client`` takes over. Writing an empty-string
        row would leave a residue that reads as "configured but empty"
        and also trips the plaintext-secret read warning.
        """
        # Import here to avoid circular import
        from database import DEFAULT_SYSTEM_PROMPT, DEFAULT_VERIFICATION_PROMPT
        from config import DEFAULT_AD_DETECTION_MODEL as DEFAULT_MODEL
        from chapters_generator import CHAPTERS_MODEL
        from config import PROVIDER_ANTHROPIC
        from llm_client import get_effective_provider
        from secrets_crypto import SECRET_SETTING_KEYS

        if key in SECRET_SETTING_KEYS:
            self.clear_secret(key)
            return True

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
            'min_cut_confidence': '0.80',
            'auto_process_enabled': 'true',
            'whisper_backend': os.environ.get('WHISPER_BACKEND', 'local'),
            'whisper_api_base_url': os.environ.get('WHISPER_API_BASE_URL', ''),
            'whisper_api_model': os.environ.get('WHISPER_API_MODEL', 'whisper-1'),
            'whisper_compute_type': os.environ.get('WHISPER_COMPUTE_TYPE', 'auto'),
        }

        if key in defaults:
            self.set_setting(key, defaults[key], is_default=True)
            return True
        return False

    def get_secret(self, key: str) -> Optional[str]:
        """Return a decrypted secret, or None if unset.

        Transparently handles legacy plaintext rows (no envelope prefix) so
        pre-v1.2.0 stored keys keep working until re-saved.
        """
        raw = self.get_setting(key)
        if not raw:
            return None
        if not is_ciphertext(raw):
            return raw
        try:
            return decrypt(self, raw)
        except CryptoUnavailableError:
            logger.warning("secrets_crypto unavailable; skipping decrypt")
            return None
        except Exception:
            logger.exception("secrets_crypto decrypt failed")
            return None

    def set_secret(self, key: str, plaintext: str):
        """Encrypt and store a secret. Requires provider crypto to be available.

        No-ops through ``is_ciphertext`` so a request body that replays a
        previously-emitted ciphertext (e.g. a UI round-trip with a masked
        field) does not get double-wrapped.
        """
        if is_ciphertext(plaintext):
            self.set_setting(key, plaintext)
            return
        self.set_setting(key, encrypt(self, plaintext))

    def clear_secret(self, key: str):
        """Remove a stored secret so env-var fallback takes over.

        Deletes the row outright rather than writing an empty string. An empty
        string still occupies a row that happens to be readable; a deleted
        row leaves no residue for an inquisitive attacker who somehow got
        read access to the ``settings`` table but not the ciphertext.
        """
        conn = self.get_connection()
        conn.execute("DELETE FROM settings WHERE key = ?", (key,))
        conn.commit()

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

    def get_pricing_last_updated(self) -> Optional[str]:
        """Get the most recent updated_at from model_pricing table."""
        conn = self.get_connection()
        cursor = conn.execute("SELECT MAX(updated_at) as last_updated FROM model_pricing")
        row = cursor.fetchone()
        return row['last_updated'] if row else None

    def get_model_pricing(self, source: str = None) -> List[Dict]:
        """Get model pricing entries, optionally filtered by source."""
        conn = self.get_connection()
        if source:
            cursor = conn.execute(
                """SELECT match_key, raw_model_id, display_name,
                          input_cost_per_mtok, output_cost_per_mtok,
                          source, updated_at
                   FROM model_pricing WHERE source = ?
                   ORDER BY display_name""",
                (source,)
            )
        else:
            cursor = conn.execute(
                """SELECT match_key, raw_model_id, display_name,
                          input_cost_per_mtok, output_cost_per_mtok,
                          source, updated_at
                   FROM model_pricing ORDER BY display_name"""
            )
        return [
            {
                'matchKey': row['match_key'],
                'rawModelId': row['raw_model_id'],
                'displayName': row['display_name'],
                'inputCostPerMtok': row['input_cost_per_mtok'],
                'outputCostPerMtok': row['output_cost_per_mtok'],
                'source': row['source'],
                'updatedAt': row['updated_at'],
            }
            for row in cursor
        ]

    def seed_default_pricing(self):
        """Seed model_pricing from DEFAULT_MODEL_PRICING as fallback.

        Called only when live fetch fails and table is empty.
        Marks rows with source='default' so they get overwritten on next live fetch.
        """
        conn = self.get_connection()
        inserted = 0
        for model_id, info in DEFAULT_MODEL_PRICING.items():
            key = normalize_model_key(model_id)
            cursor = conn.execute(
                """INSERT INTO model_pricing
                       (model_id, match_key, raw_model_id, display_name,
                        input_cost_per_mtok, output_cost_per_mtok, source)
                   VALUES (?, ?, ?, ?, ?, ?, 'default')
                   ON CONFLICT(match_key) DO NOTHING""",
                (model_id, key, model_id, info['name'], info['input'], info['output'])
            )
            if cursor.rowcount > 0:
                inserted += 1
        conn.commit()
        if inserted > 0:
            logger.info(f"Seeded {inserted} default model pricing entries")

    def upsert_fetched_pricing(self, models: List[Dict], source: str):
        """Bulk upsert pricing fetched from an external source."""
        conn = self.get_connection()
        # Deduplicate by match_key (last entry wins) to avoid PK/UNIQUE conflict
        seen = {}
        for m in models:
            seen[m['match_key']] = m
        models = list(seen.values())
        for m in models:
            conn.execute(
                """INSERT INTO model_pricing
                       (model_id, match_key, raw_model_id, display_name,
                        input_cost_per_mtok, output_cost_per_mtok, source)
                   VALUES (?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(match_key) DO UPDATE SET
                     raw_model_id = excluded.raw_model_id,
                     display_name = excluded.display_name,
                     input_cost_per_mtok = excluded.input_cost_per_mtok,
                     output_cost_per_mtok = excluded.output_cost_per_mtok,
                     source = excluded.source,
                     updated_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now')""",
                (m['raw_model_id'], m['match_key'], m['raw_model_id'], m['display_name'],
                 m['input_cost_per_mtok'], m['output_cost_per_mtok'], source)
            )
        conn.commit()
