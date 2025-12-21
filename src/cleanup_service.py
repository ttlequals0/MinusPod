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
from datetime import datetime, timedelta
from typing import Dict, Optional

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
        self._settings_cache = None
        self._settings_loaded_at = None

    def _get_setting(self, key: str) -> any:
        """Get a setting value from database or default."""
        # Reload settings every 5 minutes
        if (self._settings_cache is None or
            self._settings_loaded_at is None or
            datetime.now() - self._settings_loaded_at > timedelta(minutes=5)):
            self._load_settings()

        return self._settings_cache.get(key, DEFAULT_SETTINGS.get(key))

    def _load_settings(self):
        """Load settings from database."""
        self._settings_cache = DEFAULT_SETTINGS.copy()
        self._settings_loaded_at = datetime.now()

        if not self.db:
            return

        try:
            for key in DEFAULT_SETTINGS:
                value = self.db.get_setting(f'cleanup_{key}')
                if value is not None:
                    # Convert to appropriate type
                    if key in ('auto_vacuum',):
                        self._settings_cache[key] = value.lower() in ('true', '1', 'yes')
                    elif key in ('episode_days', 'pattern_stale_days', 'pattern_purge_days',
                                 'confidence_decay_percent', 'min_confirmations_to_decay'):
                        self._settings_cache[key] = int(value)
                    else:
                        self._settings_cache[key] = value
        except Exception as e:
            logger.warning(f"Failed to load cleanup settings: {e}")

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
            'vacuum_run': False,
            'backup_created': False
        }

        # Run each task
        results['stale_patterns_disabled'] = self.run_disable_stale()
        results['patterns_purged'] = self.run_purge_disabled()
        results['episodes_deleted'] = self.run_episode_cleanup()
        results['patterns_decayed'] = self.run_confidence_decay()

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
        cutoff_date = datetime.utcnow() - timedelta(days=stale_days)
        cutoff_str = cutoff_date.isoformat()

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
                disabled_at=datetime.utcnow().isoformat(),
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
        cutoff_date = datetime.utcnow() - timedelta(days=purge_days)
        cutoff_str = cutoff_date.isoformat()

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
        cutoff_date = datetime.utcnow() - timedelta(days=episode_days)
        cutoff_str = cutoff_date.isoformat()

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
        cutoff_date = datetime.utcnow() - timedelta(days=30)
        cutoff_str = cutoff_date.isoformat()

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
        """Run VACUUM to reclaim space."""
        if not self.db:
            return

        try:
            conn = self.db.get_connection()
            # VACUUM must be run outside a transaction
            conn.execute("VACUUM")
            logger.info("Database VACUUM completed")
        except Exception as e:
            logger.error(f"VACUUM failed: {e}")

    def backup_database(self) -> Optional[str]:
        """
        Create a timestamped backup of the SQLite database.

        Uses SQLite's backup API for consistency (safe during writes).
        Cleans up old backups, keeping only the configured number.

        Returns:
            Path to backup file, or None if backup failed
        """
        if not self.db:
            return None

        try:
            # Get database path
            db_path = self.db.db_path
            if not db_path or not os.path.exists(db_path):
                logger.warning("Database path not found, skipping backup")
                return None

            # Create backup directory next to database
            db_dir = os.path.dirname(db_path)
            backup_dir = os.path.join(db_dir, 'backups')
            os.makedirs(backup_dir, exist_ok=True)

            # Generate timestamped backup filename
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            backup_filename = f'podcast_{timestamp}.db'
            backup_path = os.path.join(backup_dir, backup_filename)

            # Use SQLite backup API for safe, consistent backup
            source_conn = self.db.get_connection()
            backup_conn = sqlite3.connect(backup_path)

            try:
                source_conn.backup(backup_conn)
                backup_conn.close()
                logger.info(f"Database backup created: {backup_path}")
            except Exception as e:
                backup_conn.close()
                # Clean up failed backup
                if os.path.exists(backup_path):
                    os.remove(backup_path)
                raise e

            # Clean up old backups
            self._cleanup_old_backups(backup_dir)

            return backup_path

        except Exception as e:
            logger.error(f"Database backup failed: {e}")
            return None

    def _cleanup_old_backups(self, backup_dir: str):
        """Remove old backups, keeping only the configured number."""
        keep_count = self._get_setting('backup_keep_count')

        try:
            # Get all backup files sorted by modification time (newest first)
            backups = []
            for f in os.listdir(backup_dir):
                if f.startswith('podcast_') and f.endswith('.db'):
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
            cutoff = (datetime.utcnow() - timedelta(days=stale_days)).isoformat()
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
