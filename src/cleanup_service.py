"""
Cleanup Service - Pattern retention and database maintenance.

Handles:
- Disabling stale patterns not matched recently
- Purging disabled patterns after retention period
- Confidence decay for unused patterns
- Database VACUUM for space reclamation
- Database backup automation
"""
import logging
import os
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Dict, Optional

from secrets_crypto import (
    encrypt_bytes as _encrypt_bytes,
    is_available as crypto_available,
)
from utils.time import utc_now_iso
from utils.ttl_cache import TTLCache

logger = logging.getLogger('podcast.cleanup')

# Default settings (can be overridden in database)
DEFAULT_SETTINGS = {
    'episode_days': 30,           # Delete episodes older than this
    'pattern_stale_days': 180,    # Disable patterns not matched in this many days
    'pattern_purge_days': 90,     # Delete patterns disabled longer than this
    'auto_vacuum': True,          # Run VACUUM after purge
    'confidence_decay_percent': 10,  # Max decay per run
    'min_confirmations_to_decay': 5,  # Don't decay patterns with few confirmations
    'backup_enabled': True,       # Enable automatic backups
    'backup_keep_count': 7,       # Number of backups to retain
}


class CleanupService:
    """
    Service for database maintenance and pattern lifecycle management.

    Manages pattern lifecycle:
    1. Active patterns are used for detection
    2. Stale patterns (not matched recently) are disabled
    3. Disabled patterns are purged after retention period

    Also handles:
    - Episode cleanup based on retention period
    - Confidence decay for promoting pattern turnover
    - Database optimization via VACUUM
    """

    def __init__(self, db=None):
        """
        Initialize the cleanup service.

        Args:
            db: Database instance
        """
        self.db = db
        # 5 minute TTL cache; single bucket keyed by '_settings'
        self._settings_cache = TTLCache(ttl_seconds=300.0)

    def _get_setting(self, key: str) -> any:
        """Get a setting value from database or default."""
        settings = self._settings_cache.get('_settings')
        if settings is None:
            settings = self._load_settings()
        return settings.get(key, DEFAULT_SETTINGS.get(key))

    def _load_settings(self):
        """Load settings from database and cache them."""
        settings = DEFAULT_SETTINGS.copy()

        if self.db:
            try:
                for key in DEFAULT_SETTINGS:
                    value = self.db.get_setting(f'cleanup_{key}')
                    if value is not None:
                        # Convert to appropriate type
                        if key in ('auto_vacuum',):
                            settings[key] = value.lower() in ('true', '1', 'yes')
                        elif key in ('episode_days', 'pattern_stale_days', 'pattern_purge_days',
                                     'confidence_decay_percent', 'min_confirmations_to_decay'):
                            settings[key] = int(value)
                        else:
                            settings[key] = value
            except Exception as e:
                logger.warning(f"Failed to load cleanup settings: {e}")

        self._settings_cache.set('_settings', settings)
        return settings

    def run_all(self) -> Dict[str, int]:
        """
        Run all cleanup tasks.

        Returns:
            Dict with counts of affected items per task
        """
        results = {
            'stale_patterns_disabled': 0,
            'patterns_purged': 0,
            'episodes_deleted': 0,
            'patterns_decayed': 0,
            'auth_failures_pruned': 0,
            'vacuum_run': False,
            'backup_created': False
        }

        # Run each task
        results['stale_patterns_disabled'] = self.run_disable_stale()
        results['patterns_purged'] = self.run_purge_disabled()
        results['episodes_deleted'] = self.run_episode_cleanup()
        results['patterns_decayed'] = self.run_confidence_decay()
        try:
            results['auth_failures_pruned'] = self.db.cleanup_auth_failures()
        except Exception:
            logger.exception("auth_failures cleanup failed")

        if self._get_setting('auto_vacuum'):
            self._vacuum()
            results['vacuum_run'] = True

        # Create database backup
        if self._get_setting('backup_enabled'):
            backup_path = self.backup_database()
            results['backup_created'] = backup_path is not None

        logger.info(f"Cleanup complete: {results}")
        return results

    def run_disable_stale(self) -> int:
        """
        Disable patterns that haven't been matched recently.

        Returns:
            Number of patterns disabled
        """
        if not self.db:
            return 0

        stale_days = self._get_setting('pattern_stale_days')
        cutoff_date = datetime.now(timezone.utc) - timedelta(days=stale_days)
        cutoff_str = cutoff_date.strftime('%Y-%m-%dT%H:%M:%SZ')

        try:
            # Get active patterns not matched since cutoff
            patterns = self.db.get_ad_patterns(active_only=True)

            disabled_count = 0
            for pattern in patterns:
                last_matched = pattern.get('last_matched_at')

                # Skip if never matched (new pattern)
                if not last_matched:
                    # Check creation date instead
                    created = pattern.get('created_at', '')
                    if created < cutoff_str:
                        self._disable_pattern(pattern['id'], 'stale_never_matched')
                        disabled_count += 1
                    continue

                # Disable if not matched recently
                if last_matched < cutoff_str:
                    self._disable_pattern(pattern['id'], 'stale')
                    disabled_count += 1

            if disabled_count:
                logger.info(f"Disabled {disabled_count} stale patterns")

            return disabled_count

        except Exception as e:
            logger.error(f"Failed to disable stale patterns: {e}")
            return 0

    def _disable_pattern(self, pattern_id: int, reason: str):
        """Disable a pattern with reason."""
        try:
            self.db.update_ad_pattern(
                pattern_id,
                is_active=False,
                disabled_at=utc_now_iso(),
                disabled_reason=reason
            )
        except Exception as e:
            logger.error(f"Failed to disable pattern {pattern_id}: {e}")

    def run_purge_disabled(self) -> int:
        """
        Delete patterns that have been disabled beyond retention period.

        Returns:
            Number of patterns deleted
        """
        if not self.db:
            return 0

        purge_days = self._get_setting('pattern_purge_days')
        cutoff_date = datetime.now(timezone.utc) - timedelta(days=purge_days)
        cutoff_str = cutoff_date.strftime('%Y-%m-%dT%H:%M:%SZ')

        try:
            # Get disabled patterns
            patterns = self.db.get_ad_patterns(active_only=False)

            purged_count = 0
            for pattern in patterns:
                if pattern.get('is_active'):
                    continue

                disabled_at = pattern.get('disabled_at')
                if disabled_at and disabled_at < cutoff_str:
                    self._purge_pattern(pattern['id'])
                    purged_count += 1

            if purged_count:
                logger.info(f"Purged {purged_count} disabled patterns")

            return purged_count

        except Exception as e:
            logger.error(f"Failed to purge disabled patterns: {e}")
            return 0

    def _purge_pattern(self, pattern_id: int):
        """Delete a pattern and its related data."""
        try:
            # Delete fingerprints first (foreign key)
            self.db.delete_audio_fingerprint(pattern_id)

            # Delete the pattern
            self.db.delete_ad_pattern(pattern_id)

        except Exception as e:
            logger.error(f"Failed to purge pattern {pattern_id}: {e}")

    def run_episode_cleanup(self) -> int:
        """
        Delete episodes older than retention period.

        Note: This only deletes episode records, not audio files.
        Audio files are managed separately by the storage module.

        Returns:
            Number of episodes deleted
        """
        if not self.db:
            return 0

        episode_days = self._get_setting('episode_days')
        cutoff_date = datetime.now(timezone.utc) - timedelta(days=episode_days)
        cutoff_str = cutoff_date.strftime('%Y-%m-%dT%H:%M:%SZ')

        try:
            # Get all episodes
            # Note: This would need to be implemented as a batch operation
            # for large databases
            deleted_count = self.db.delete_old_episodes(cutoff_str)

            if deleted_count:
                logger.info(f"Deleted {deleted_count} old episodes")

            return deleted_count

        except Exception as e:
            logger.error(f"Failed to delete old episodes: {e}")
            return 0

    def run_confidence_decay(self) -> int:
        """
        Apply confidence decay to patterns not recently matched.

        This prevents patterns from accumulating high confirmation counts
        and never being replaced by better patterns.

        Decay rules:
        - Only patterns not matched in 30+ days
        - Max decay per run is configurable (default 10%)
        - Patterns with few confirmations are not decayed

        Returns:
            Number of patterns with decayed confidence
        """
        if not self.db:
            return 0

        decay_percent = self._get_setting('confidence_decay_percent')
        min_confirmations = self._get_setting('min_confirmations_to_decay')

        # Only decay patterns not matched in 30 days
        cutoff_date = datetime.now(timezone.utc) - timedelta(days=30)
        cutoff_str = cutoff_date.strftime('%Y-%m-%dT%H:%M:%SZ')

        try:
            patterns = self.db.get_ad_patterns(active_only=True)

            decayed_count = 0
            for pattern in patterns:
                confirmations = pattern.get('confirmation_count', 0)

                # Skip patterns with low confirmations
                if confirmations < min_confirmations:
                    continue

                last_matched = pattern.get('last_matched_at')
                if last_matched and last_matched >= cutoff_str:
                    continue

                # Apply decay
                decay_amount = max(1, int(confirmations * decay_percent / 100))
                new_confirmations = confirmations - decay_amount

                self.db.update_ad_pattern(
                    pattern['id'],
                    confirmation_count=max(0, new_confirmations)
                )
                decayed_count += 1

            if decayed_count:
                logger.info(f"Applied confidence decay to {decayed_count} patterns")

            return decayed_count

        except Exception as e:
            logger.error(f"Failed to apply confidence decay: {e}")
            return 0

    def _vacuum(self):
        """Run VACUUM to reclaim space.

        Also runs a full WAL checkpoint with TRUNCATE so the write-ahead
        log file is returned to zero bytes instead of recycled. This
        matters on volumes where readers keep the WAL pinned and the
        periodic ``wal_autocheckpoint`` cannot reclaim it.
        """
        if not self.db:
            return

        try:
            conn = self.db.get_connection()
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            # VACUUM must be run outside a transaction
            conn.execute("VACUUM")
            logger.info("Database VACUUM + WAL checkpoint completed")
        except Exception as e:
            logger.error(f"VACUUM failed: {e}")

    def backup_database(self) -> Optional[str]:
        """Create a timestamped backup of the SQLite database.

        Uses SQLite's backup API for consistency (safe during writes).
        When ``MINUSPOD_MASTER_PASSPHRASE`` is set, the backup is AES-GCM
        wrapped under the same KEK as provider secrets and written as
        ``*.db.enc``. Without the passphrase, the plaintext ``*.db`` is
        kept and a WARN logged so operators know scheduled backups are
        not protected. Retention matches either extension.

        Returns the path of the final (possibly encrypted) file, or None.
        """
        if not self.db:
            return None

        tmp_path = None
        try:
            db_path = self.db.db_path
            if not db_path or not os.path.exists(db_path):
                logger.warning("Database path not found, skipping backup")
                return None

            db_dir = os.path.dirname(db_path)
            backup_dir = os.path.join(db_dir, 'backups')
            os.makedirs(backup_dir, exist_ok=True)

            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            # Atomic rename within the same filesystem -- write snapshot
            # to a .tmp, wrap (or not), then rename to the final name so
            # readers never see a partial file.
            tmp_path = os.path.join(backup_dir, f'podcast_{timestamp}.tmp')

            source_conn = self.db.get_connection()
            backup_conn = sqlite3.connect(tmp_path)
            try:
                source_conn.backup(backup_conn)
            finally:
                backup_conn.close()

            if crypto_available():
                try:
                    with open(tmp_path, 'rb') as f:
                        blob = f.read()
                    enc = _encrypt_bytes(self.db, blob)
                    final_path = os.path.join(backup_dir, f'podcast_{timestamp}.db.enc')
                    with open(final_path, 'wb') as f:
                        f.write(enc)
                    os.unlink(tmp_path)
                    tmp_path = None
                    logger.info(f"Encrypted database backup created: {final_path}")
                except Exception:
                    logger.exception("Scheduled backup encryption failed; keeping unencrypted copy")
                    final_path = os.path.join(backup_dir, f'podcast_{timestamp}.db')
                    os.rename(tmp_path, final_path)
                    tmp_path = None
            else:
                final_path = os.path.join(backup_dir, f'podcast_{timestamp}.db')
                os.rename(tmp_path, final_path)
                tmp_path = None
                logger.warning(
                    "Scheduled DB backup written UNENCRYPTED: set "
                    "MINUSPOD_MASTER_PASSPHRASE to enable AES-GCM wrap"
                )

            self._cleanup_old_backups(backup_dir)
            return final_path

        except Exception as e:
            logger.error(f"Database backup failed: {e}")
            return None
        finally:
            if tmp_path and os.path.exists(tmp_path):
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass

    def _cleanup_old_backups(self, backup_dir: str):
        """Remove old backups, keeping only the configured number."""
        keep_count = self._get_setting('backup_keep_count')

        try:
            # Get all backup files sorted by modification time (newest
            # first). Match both .db (legacy / no passphrase) and .db.enc
            # (AES-GCM-wrapped) so retention works across the transition.
            backups = []
            for f in os.listdir(backup_dir):
                if f.startswith('podcast_') and (f.endswith('.db') or f.endswith('.db.enc')):
                    path = os.path.join(backup_dir, f)
                    backups.append((path, os.path.getmtime(path)))

            backups.sort(key=lambda x: x[1], reverse=True)

            # Remove backups beyond keep_count
            for path, _ in backups[keep_count:]:
                try:
                    os.remove(path)
                    logger.debug(f"Removed old backup: {path}")
                except OSError as e:
                    logger.warning(f"Failed to remove old backup {path}: {e}")

            removed = max(0, len(backups) - keep_count)
            if removed:
                logger.info(f"Cleaned up {removed} old backup(s), keeping {keep_count}")

        except Exception as e:
            logger.error(f"Failed to cleanup old backups: {e}")

    def get_statistics(self) -> Dict:
        """
        Get cleanup-related statistics.

        Returns:
            Dict with pattern/episode counts and ages
        """
        if not self.db:
            return {}

        try:
            stats = {
                'total_patterns': 0,
                'active_patterns': 0,
                'disabled_patterns': 0,
                'stale_patterns': 0,
                'total_episodes': 0,
                'settings': {}
            }

            # Count patterns
            all_patterns = self.db.get_ad_patterns(active_only=False)
            stats['total_patterns'] = len(all_patterns)
            stats['active_patterns'] = len([p for p in all_patterns if p.get('is_active')])
            stats['disabled_patterns'] = stats['total_patterns'] - stats['active_patterns']

            # Count stale patterns
            stale_days = self._get_setting('pattern_stale_days')
            cutoff = (datetime.now(timezone.utc) - timedelta(days=stale_days)).strftime('%Y-%m-%dT%H:%M:%SZ')
            stats['stale_patterns'] = len([
                p for p in all_patterns
                if p.get('is_active') and (
                    not p.get('last_matched_at') or p['last_matched_at'] < cutoff
                )
            ])

            # Current settings
            stats['settings'] = {
                'episode_days': self._get_setting('episode_days'),
                'pattern_stale_days': self._get_setting('pattern_stale_days'),
                'pattern_purge_days': self._get_setting('pattern_purge_days'),
                'auto_vacuum': self._get_setting('auto_vacuum'),
            }

            return stats

        except Exception as e:
            logger.error(f"Failed to get cleanup statistics: {e}")
            return {}
