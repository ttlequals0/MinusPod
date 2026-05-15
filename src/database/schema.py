"""Schema initialization and migration mixin for MinusPod database."""
import sqlite3
import logging
import json
import os
import re
import time
from pathlib import Path
from typing import Dict, List

logger = logging.getLogger(__name__)


SCHEMA_SQL = """
PRAGMA foreign_keys = ON;
PRAGMA journal_mode = WAL;

-- podcasts table (replaces config/feeds.json)
CREATE TABLE IF NOT EXISTS podcasts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    slug TEXT UNIQUE NOT NULL,
    source_url TEXT NOT NULL,
    title TEXT,
    description TEXT,
    artwork_url TEXT,
    artwork_cached INTEGER DEFAULT 0,
    last_checked_at TEXT,
    network_id TEXT,
    dai_platform TEXT,
    network_id_override TEXT,
    audio_analysis_override TEXT,
    auto_process_override TEXT,
    skip_second_pass INTEGER DEFAULT 0,
    max_episodes INTEGER,
    only_expose_processed_episodes INTEGER,
    tags TEXT NOT NULL DEFAULT '[]',
    user_tags TEXT NOT NULL DEFAULT '[]',
    created_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    updated_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

-- episodes table (replaces data/{slug}/data.json)
CREATE TABLE IF NOT EXISTS episodes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    podcast_id INTEGER NOT NULL,
    episode_id TEXT NOT NULL,
    original_url TEXT NOT NULL,
    title TEXT,
    description TEXT,
    status TEXT DEFAULT 'pending' CHECK(status IN ('discovered','pending','processing','processed','failed','permanently_failed')),
    retry_count INTEGER DEFAULT 0,
    processed_file TEXT,
    original_file TEXT,
    processed_at TEXT,
    processed_version INTEGER DEFAULT 0,
    original_duration REAL,
    new_duration REAL,
    ads_removed INTEGER DEFAULT 0,
    ads_removed_firstpass INTEGER DEFAULT 0,
    ads_removed_secondpass INTEGER DEFAULT 0,
    error_message TEXT,
    ad_detection_status TEXT DEFAULT NULL CHECK(ad_detection_status IN (NULL, 'success', 'failed')),
    artwork_url TEXT,
    episode_number INTEGER,
    tags TEXT NOT NULL DEFAULT '[]',
    created_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    updated_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    FOREIGN KEY (podcast_id) REFERENCES podcasts(id) ON DELETE CASCADE,
    UNIQUE(podcast_id, episode_id)
);

-- episode_details table (transcript and ad data)
CREATE TABLE IF NOT EXISTS episode_details (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    episode_id INTEGER UNIQUE NOT NULL,
    transcript_text TEXT,
    original_transcript_text TEXT,
    transcript_vtt TEXT,
    chapters_json TEXT,
    ad_markers_json TEXT,
    first_pass_response TEXT,
    first_pass_prompt TEXT,
    second_pass_prompt TEXT,
    second_pass_response TEXT,
    original_segments_json TEXT,
    final_segments_json TEXT,
    created_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    FOREIGN KEY (episode_id) REFERENCES episodes(id) ON DELETE CASCADE
);

-- settings table (ad detection config, retention)
CREATE TABLE IF NOT EXISTS settings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    key TEXT UNIQUE NOT NULL,
    value TEXT NOT NULL,
    is_default INTEGER DEFAULT 1,
    updated_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

-- cumulative stats table (persists even after episodes are deleted)
CREATE TABLE IF NOT EXISTS stats (
    key TEXT PRIMARY KEY,
    value REAL NOT NULL DEFAULT 0,
    updated_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

-- system_settings table (for schema versioning and configurable settings)
CREATE TABLE IF NOT EXISTS system_settings (
    key TEXT PRIMARY KEY,
    value TEXT,
    updated_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

-- ad_patterns table (learned ad patterns - NO FK to podcasts, survives content deletion)
CREATE TABLE IF NOT EXISTS ad_patterns (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    scope TEXT NOT NULL CHECK(scope IN ('global', 'network', 'podcast')),
    network_id TEXT,
    podcast_id TEXT,
    dai_platform TEXT,
    text_template TEXT,
    intro_variants TEXT DEFAULT '[]',
    outro_variants TEXT DEFAULT '[]',
    sponsor_id INTEGER REFERENCES known_sponsors(id),
    confirmation_count INTEGER DEFAULT 0,
    false_positive_count INTEGER DEFAULT 0,
    last_matched_at TEXT,
    created_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    created_from_episode_id TEXT,
    is_active INTEGER DEFAULT 1,
    disabled_at TEXT,
    disabled_reason TEXT,
    avg_duration REAL,
    duration_samples INTEGER DEFAULT 0,
    created_by TEXT DEFAULT 'auto',
    source TEXT NOT NULL DEFAULT 'local' CHECK(source IN ('local', 'community', 'imported')),
    community_id TEXT,
    version INTEGER NOT NULL DEFAULT 1,
    submitted_app_version TEXT,
    protected_from_sync INTEGER NOT NULL DEFAULT 0
);

-- pattern_corrections table (user corrections; conflicting entries cleaned up on reversal)
CREATE TABLE IF NOT EXISTS pattern_corrections (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    pattern_id INTEGER,
    episode_id TEXT,
    podcast_title TEXT,
    episode_title TEXT,
    correction_type TEXT NOT NULL CHECK(correction_type IN (
        'false_positive', 'boundary_adjustment', 'confirm',
        'promotion', 'auto_promotion', 'create'
    )),
    original_bounds TEXT,
    corrected_bounds TEXT,
    text_snippet TEXT,
    created_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    sponsor_id INTEGER REFERENCES known_sponsors(id)
);

-- audio_fingerprints table (Chromaprint hashes for DAI-inserted ads)
CREATE TABLE IF NOT EXISTS audio_fingerprints (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    pattern_id INTEGER UNIQUE,
    fingerprint BLOB,
    duration REAL,
    created_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

-- known_sponsors table (master sponsor list - single source of truth)
CREATE TABLE IF NOT EXISTS known_sponsors (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT UNIQUE NOT NULL,
    aliases TEXT DEFAULT '[]',
    category TEXT,
    common_ctas TEXT DEFAULT '[]',
    is_active INTEGER DEFAULT 1,
    tags TEXT NOT NULL DEFAULT '[]',
    created_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

-- sponsor_normalizations table (Whisper transcription fixes)
CREATE TABLE IF NOT EXISTS sponsor_normalizations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    pattern TEXT UNIQUE NOT NULL,
    replacement TEXT NOT NULL,
    category TEXT CHECK(category IN ('sponsor', 'url', 'number', 'phrase')),
    is_active INTEGER DEFAULT 1,
    created_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

-- processing_history table (audit log of all processing attempts)
CREATE TABLE IF NOT EXISTS processing_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    podcast_id INTEGER NOT NULL,
    podcast_slug TEXT NOT NULL,
    podcast_title TEXT,
    episode_id TEXT NOT NULL,
    episode_title TEXT,
    processed_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    processing_duration_seconds REAL,
    status TEXT NOT NULL CHECK(status IN ('completed', 'failed')),
    ads_detected INTEGER DEFAULT 0,
    error_message TEXT,
    reprocess_number INTEGER DEFAULT 1,
    input_tokens INTEGER DEFAULT 0,
    output_tokens INTEGER DEFAULT 0,
    llm_cost REAL DEFAULT 0.0,
    created_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    FOREIGN KEY (podcast_id) REFERENCES podcasts(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_history_processed_at ON processing_history(processed_at DESC);
CREATE INDEX IF NOT EXISTS idx_history_podcast_episode ON processing_history(podcast_id, episode_id);
CREATE INDEX IF NOT EXISTS idx_history_status ON processing_history(status);

-- auto_process_queue table (queue for automatic episode processing)
CREATE TABLE IF NOT EXISTS auto_process_queue (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    podcast_id INTEGER NOT NULL,
    episode_id TEXT NOT NULL,
    original_url TEXT NOT NULL,
    title TEXT,
    status TEXT DEFAULT 'pending' CHECK(status IN ('pending','processing','completed','failed')),
    attempts INTEGER DEFAULT 0,
    error_message TEXT,
    created_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    updated_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    FOREIGN KEY (podcast_id) REFERENCES podcasts(id) ON DELETE CASCADE,
    UNIQUE(podcast_id, episode_id)
);

CREATE INDEX IF NOT EXISTS idx_queue_status ON auto_process_queue(status);
CREATE INDEX IF NOT EXISTS idx_queue_created ON auto_process_queue(created_at);
CREATE INDEX IF NOT EXISTS idx_queue_status_created ON auto_process_queue(status, created_at);
CREATE INDEX IF NOT EXISTS idx_queue_podcast_episode ON auto_process_queue(podcast_id, episode_id);

CREATE INDEX IF NOT EXISTS idx_podcasts_slug ON podcasts(slug);
CREATE INDEX IF NOT EXISTS idx_episodes_podcast_id ON episodes(podcast_id);
CREATE INDEX IF NOT EXISTS idx_episodes_episode_id ON episodes(episode_id);
CREATE INDEX IF NOT EXISTS idx_episodes_podcast_episode ON episodes(podcast_id, episode_id);
CREATE INDEX IF NOT EXISTS idx_episodes_status ON episodes(status);
CREATE INDEX IF NOT EXISTS idx_episodes_created_at ON episodes(created_at);
CREATE INDEX IF NOT EXISTS idx_episode_details_episode_id ON episode_details(episode_id);

-- Cross-episode training indexes (indexes on new columns created in migrations)
CREATE INDEX IF NOT EXISTS idx_patterns_sponsor_id ON ad_patterns(sponsor_id) WHERE is_active = 1;
CREATE INDEX IF NOT EXISTS idx_patterns_source ON ad_patterns(source, is_active);
CREATE INDEX IF NOT EXISTS idx_patterns_community_id ON ad_patterns(community_id) WHERE community_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_fingerprints_pattern ON audio_fingerprints(pattern_id);
CREATE INDEX IF NOT EXISTS idx_corrections_pattern ON pattern_corrections(pattern_id);
CREATE INDEX IF NOT EXISTS idx_sponsors_name ON known_sponsors(name) WHERE is_active = 1;
CREATE INDEX IF NOT EXISTS idx_normalizations_pattern ON sponsor_normalizations(pattern) WHERE is_active = 1;

-- model_pricing table (LLM model cost rates)
CREATE TABLE IF NOT EXISTS model_pricing (
    model_id TEXT PRIMARY KEY,
    match_key TEXT,
    raw_model_id TEXT,
    display_name TEXT NOT NULL,
    input_cost_per_mtok REAL NOT NULL,
    output_cost_per_mtok REAL NOT NULL,
    source TEXT DEFAULT 'legacy',
    updated_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_model_pricing_match_key ON model_pricing(match_key);

-- token_usage table (per-model cumulative LLM token usage)
CREATE TABLE IF NOT EXISTS token_usage (
    model_id TEXT PRIMARY KEY,
    match_key TEXT,
    total_input_tokens INTEGER NOT NULL DEFAULT 0,
    total_output_tokens INTEGER NOT NULL DEFAULT 0,
    total_cost REAL NOT NULL DEFAULT 0.0,
    call_count INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

CREATE TABLE IF NOT EXISTS ad_reviewer_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    episode_id TEXT NOT NULL,
    podcast_id TEXT,
    pass INTEGER NOT NULL,
    pool TEXT NOT NULL,
    original_start REAL NOT NULL,
    original_end REAL NOT NULL,
    verdict TEXT NOT NULL,
    adjusted_start REAL,
    adjusted_end REAL,
    reasoning TEXT,
    confidence REAL,
    model_used TEXT NOT NULL,
    latency_ms INTEGER,
    success INTEGER NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_ad_reviewer_log_episode ON ad_reviewer_log(episode_id);
CREATE INDEX IF NOT EXISTS idx_ad_reviewer_log_podcast ON ad_reviewer_log(podcast_id);
"""

# Indexes that depend on columns added by migrations - created separately
MIGRATION_INDEXES_SQL = """
CREATE INDEX IF NOT EXISTS idx_podcasts_network_id ON podcasts(network_id);
CREATE INDEX IF NOT EXISTS idx_podcasts_dai_platform ON podcasts(dai_platform);
CREATE INDEX IF NOT EXISTS idx_patterns_scope ON ad_patterns(scope, network_id, podcast_id) WHERE is_active = 1;
"""


class SchemaMixin:
    """Schema initialization and migration methods."""

    def _init_schema(self):
        """Initialize database schema with retry logic for concurrent workers."""
        max_retries = 5
        base_delay = 0.5  # seconds

        for attempt in range(max_retries):
            try:
                self._init_schema_inner()
                return
            except sqlite3.OperationalError as e:
                if "database is locked" in str(e) and attempt < max_retries - 1:
                    delay = base_delay * (2 ** attempt)  # Exponential backoff
                    logger.warning(
                        f"Database locked during schema init, retrying in {delay:.1f}s "
                        f"(attempt {attempt + 1}/{max_retries})"
                    )
                    time.sleep(delay)
                else:
                    raise

    def _init_schema_inner(self):
        """Initialize database schema (inner method called with retry wrapper)."""
        conn = self.get_connection()

        # Check if database already has tables (existing database)
        cursor = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='podcasts'"
        )
        is_existing_db = cursor.fetchone() is not None

        if is_existing_db:
            # For existing databases, only create new tables and run migrations
            # Don't run full SCHEMA_SQL as indexes may reference columns that don't exist yet
            logger.info(f"Existing database found at {self.db_path}, running migrations...")
            self._create_new_tables_only(conn)
            self._run_schema_migrations()
        else:
            # Fresh database - run full schema
            conn.executescript(SCHEMA_SQL)
            conn.commit()
            logger.info(f"Database schema initialized at {self.db_path}")
            # Still run migrations to ensure all columns exist
            self._run_schema_migrations()

    def _create_new_tables_only(self, conn):
        """Create new tables for existing databases without running indexes."""
        # Create ad_patterns table if not exists (must match SCHEMA_SQL exactly)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS ad_patterns (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                scope TEXT NOT NULL CHECK(scope IN ('global', 'network', 'podcast')),
                network_id TEXT,
                podcast_id TEXT,
                dai_platform TEXT,
                text_template TEXT,
                intro_variants TEXT DEFAULT '[]',
                outro_variants TEXT DEFAULT '[]',
                sponsor_id INTEGER REFERENCES known_sponsors(id),
                confirmation_count INTEGER DEFAULT 0,
                false_positive_count INTEGER DEFAULT 0,
                last_matched_at TEXT,
                created_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
                created_from_episode_id TEXT,
                is_active INTEGER DEFAULT 1,
                disabled_at TEXT,
                disabled_reason TEXT,
                created_by TEXT DEFAULT 'auto',
                source TEXT NOT NULL DEFAULT 'local' CHECK(source IN ('local', 'community', 'imported')),
                community_id TEXT,
                version INTEGER NOT NULL DEFAULT 1,
                submitted_app_version TEXT,
                protected_from_sync INTEGER NOT NULL DEFAULT 0
            )
        """)

        # Create audio_fingerprints table if not exists (must match SCHEMA_SQL exactly)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS audio_fingerprints (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                pattern_id INTEGER UNIQUE,
                fingerprint BLOB,
                duration REAL,
                created_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
            )
        """)

        # Create pattern_corrections table if not exists (must match SCHEMA_SQL exactly)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS pattern_corrections (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                pattern_id INTEGER,
                episode_id TEXT,
                podcast_title TEXT,
                episode_title TEXT,
                correction_type TEXT NOT NULL CHECK(correction_type IN (
                    'false_positive', 'boundary_adjustment', 'confirm',
                    'promotion', 'auto_promotion', 'create'
                )),
                original_bounds TEXT,
                corrected_bounds TEXT,
                text_snippet TEXT,
                created_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
                sponsor_id INTEGER REFERENCES known_sponsors(id)
            )
        """)

        # Create known_sponsors table if not exists (must match SCHEMA_SQL exactly)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS known_sponsors (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT UNIQUE NOT NULL,
                aliases TEXT DEFAULT '[]',
                category TEXT,
                common_ctas TEXT DEFAULT '[]',
                is_active INTEGER DEFAULT 1,
                tags TEXT NOT NULL DEFAULT '[]',
                created_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
            )
        """)

        # Create sponsor_normalizations table if not exists
        conn.execute("""
            CREATE TABLE IF NOT EXISTS sponsor_normalizations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                pattern TEXT UNIQUE NOT NULL,
                replacement TEXT NOT NULL,
                category TEXT CHECK(category IN ('sponsor', 'url', 'number', 'phrase')),
                is_active INTEGER DEFAULT 1,
                created_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
            )
        """)

        # Create processing_history table if not exists (audit log of processing attempts)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS processing_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                podcast_id INTEGER NOT NULL,
                podcast_slug TEXT NOT NULL,
                podcast_title TEXT,
                episode_id TEXT NOT NULL,
                episode_title TEXT,
                processed_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
                processing_duration_seconds REAL,
                status TEXT NOT NULL CHECK(status IN ('completed', 'failed')),
                ads_detected INTEGER DEFAULT 0,
                error_message TEXT,
                reprocess_number INTEGER DEFAULT 1,
                input_tokens INTEGER DEFAULT 0,
                output_tokens INTEGER DEFAULT 0,
                llm_cost REAL DEFAULT 0.0,
                created_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
                FOREIGN KEY (podcast_id) REFERENCES podcasts(id) ON DELETE CASCADE
            )
        """)

        # Create indexes for processing_history
        conn.execute("CREATE INDEX IF NOT EXISTS idx_history_processed_at ON processing_history(processed_at DESC)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_history_podcast_episode ON processing_history(podcast_id, episode_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_history_status ON processing_history(status)")

        # Create model_pricing table if not exists
        conn.execute("""
            CREATE TABLE IF NOT EXISTS model_pricing (
                model_id TEXT PRIMARY KEY,
                match_key TEXT,
                raw_model_id TEXT,
                display_name TEXT NOT NULL,
                input_cost_per_mtok REAL NOT NULL,
                output_cost_per_mtok REAL NOT NULL,
                source TEXT DEFAULT 'legacy',
                updated_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
            )
        """)

        # Create token_usage table if not exists
        conn.execute("""
            CREATE TABLE IF NOT EXISTS token_usage (
                model_id TEXT PRIMARY KEY,
                match_key TEXT,
                total_input_tokens INTEGER NOT NULL DEFAULT 0,
                total_output_tokens INTEGER NOT NULL DEFAULT 0,
                total_cost REAL NOT NULL DEFAULT 0.0,
                call_count INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS ad_reviewer_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                episode_id TEXT NOT NULL,
                podcast_id TEXT,
                pass INTEGER NOT NULL,
                pool TEXT NOT NULL,
                original_start REAL NOT NULL,
                original_end REAL NOT NULL,
                verdict TEXT NOT NULL,
                adjusted_start REAL,
                adjusted_end REAL,
                reasoning TEXT,
                confidence REAL,
                model_used TEXT NOT NULL,
                latency_ms INTEGER,
                success INTEGER NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_ad_reviewer_log_episode "
            "ON ad_reviewer_log(episode_id)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_ad_reviewer_log_podcast "
            "ON ad_reviewer_log(podcast_id)"
        )

        conn.commit()
        logger.info("Created new tables for cross-episode training and processing history")

    def _add_column_if_missing(self, conn, table: str, column: str,
                               definition: str, existing_columns: set) -> bool:
        """Add a column to a table if it doesn't already exist.

        Returns True if the column was added, False if it already existed.
        """
        if column in existing_columns:
            return False
        try:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
            conn.commit()
            logger.info(f"Migration: Added {column} column to {table} table")
            return True
        except Exception as e:
            logger.warning(f"Migration failed for {table}.{column}: {e}")
            return False

    def _rename_column_if_needed(self, conn, table: str, old_name: str,
                                  new_name: str, existing_columns: set) -> bool:
        """Rename a column if the old name exists and new name doesn't."""
        if old_name in existing_columns and new_name not in existing_columns:
            try:
                conn.execute(f"ALTER TABLE {table} RENAME COLUMN {old_name} TO {new_name}")
                conn.commit()
                logger.info(f"Migration: Renamed {table}.{old_name} to {new_name}")
                return True
            except Exception as e:
                logger.warning(f"Migration failed for {table} rename {old_name}: {e}")
        return False

    def _get_table_columns(self, conn, table: str) -> set:
        """Get the set of column names for a table."""
        cursor = conn.execute(f"PRAGMA table_info({table})")
        return {row['name'] for row in cursor.fetchall()}

    def _run_schema_migrations(self):
        """Run schema migrations for existing databases."""
        # Import here to avoid circular imports at module level
        from database import DEFAULT_SYSTEM_PROMPT, DEFAULT_VERIFICATION_PROMPT
        from database.settings import DEFAULT_MODEL_PRICING

        conn = self.get_connection()

        # -- Episodes table columns --
        ep_cols = self._get_table_columns(conn, 'episodes')
        episodes_migrations = [
            ('ad_detection_status', 'TEXT DEFAULT NULL'),
            ('created_at', "TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))"),
            ('artwork_url', 'TEXT'),
            ('processed_file', 'TEXT'),
            ('original_file', 'TEXT'),
            ('processed_at', 'TEXT'),
            ('processed_version', 'INTEGER DEFAULT 0'),
            ('original_duration', 'REAL'),
            ('ads_removed_firstpass', 'INTEGER DEFAULT 0'),
            ('ads_removed_secondpass', 'INTEGER DEFAULT 0'),
            ('description', 'TEXT'),
            ('reprocess_mode', 'TEXT'),
            ('reprocess_requested_at', 'TEXT'),
            ('published_at', 'TEXT'),
            ('retry_count', 'INTEGER DEFAULT 0'),
            ('episode_number', 'INTEGER'),
        ]
        for col, definition in episodes_migrations:
            self._add_column_if_missing(conn, 'episodes', col, definition, ep_cols)

        # -- Episode details table columns --
        det_cols = self._get_table_columns(conn, 'episode_details')

        # Renames (legacy column names)
        self._rename_column_if_needed(conn, 'episode_details', 'claude_prompt', 'first_pass_prompt', det_cols)
        self._rename_column_if_needed(conn, 'episode_details', 'claude_raw_response', 'first_pass_response', det_cols)

        # Refresh after renames
        det_cols = self._get_table_columns(conn, 'episode_details')
        details_migrations = [
            ('second_pass_prompt', 'TEXT'),
            ('second_pass_response', 'TEXT'),
            ('audio_analysis_json', 'TEXT'),
            ('transcript_vtt', 'TEXT'),
            ('chapters_json', 'TEXT'),
            ('original_transcript_text', 'TEXT'),
            ('original_segments_json', 'TEXT'),
            ('final_segments_json', 'TEXT'),
        ]
        for col, definition in details_migrations:
            self._add_column_if_missing(conn, 'episode_details', col, definition, det_cols)

        # -- Podcasts table columns --
        pod_cols = self._get_table_columns(conn, 'podcasts')
        podcasts_migrations = [
            ('network_id', 'TEXT'),
            ('dai_platform', 'TEXT'),
            ('network_id_override', 'TEXT'),
            ('audio_analysis_override', 'TEXT'),
            ('created_at', "TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))"),
            ('auto_process_override', 'TEXT'),
            ('skip_second_pass', 'INTEGER DEFAULT 0'),
            ('max_episodes', 'INTEGER'),
            ('etag', 'TEXT'),
            ('last_modified_header', 'TEXT'),
            # Plain INTEGER (nullable, no DEFAULT) so NULL means "use the
            # only_expose_processed_default global setting" (2.0.20+). On
            # databases created at 2.0.19 the column was INTEGER DEFAULT 0;
            # the conversion step below rewrites that to match.
            ('only_expose_processed_episodes', 'INTEGER'),
        ]
        for col, definition in podcasts_migrations:
            self._add_column_if_missing(conn, 'podcasts', col, definition, pod_cols)

        # 2.0.19 -> 2.0.20: convert only_expose_processed_episodes from
        # INTEGER DEFAULT 0 to plain nullable INTEGER, treating the previous
        # 0 default as "use global default" (NULL). Explicit per-feed 1
        # values (override-ON) are preserved verbatim. Idempotent: the
        # PRAGMA check below short-circuits once the column has no default.
        col_info = conn.execute("PRAGMA table_info(podcasts)").fetchall()
        oepe_col = next((row for row in col_info
                         if row['name'] == 'only_expose_processed_episodes'), None)
        if oepe_col is not None and oepe_col['dflt_value'] is not None:
            logger.info(
                "Converting podcasts.only_expose_processed_episodes "
                "from INTEGER DEFAULT 0 to plain nullable INTEGER"
            )
            conn.execute(
                "ALTER TABLE podcasts ADD COLUMN "
                "only_expose_processed_episodes_v2 INTEGER"
            )
            conn.execute(
                "UPDATE podcasts SET only_expose_processed_episodes_v2 = "
                "CASE WHEN only_expose_processed_episodes = 1 THEN 1 ELSE NULL END"
            )
            conn.execute(
                "ALTER TABLE podcasts DROP COLUMN only_expose_processed_episodes"
            )
            conn.execute(
                "ALTER TABLE podcasts RENAME COLUMN "
                "only_expose_processed_episodes_v2 TO only_expose_processed_episodes"
            )
            conn.commit()

        # Backfill: pre-v1.0.41 rows may store RFC 2822 dates which break
        # SQLite lexicographic sorting.  After first run this is a no-op.
        try:
            from database.episodes import normalize_published_at
            cursor = conn.execute(
                "SELECT id, published_at FROM episodes "
                "WHERE published_at IS NOT NULL "
                "AND SUBSTR(published_at, 1, 1) NOT BETWEEN '0' AND '9'"
            )
            fixed = 0
            for row in cursor:
                normalized = normalize_published_at(row['published_at'])
                if normalized != row['published_at']:
                    conn.execute(
                        "UPDATE episodes SET published_at = ? WHERE id = ?",
                        (normalized, row['id'])
                    )
                    fixed += 1
            if fixed:
                conn.commit()
                logger.info(f"Migration: Normalized {fixed} RFC 2822 published_at dates to ISO 8601")
        except Exception as e:
            logger.warning(f"published_at normalization migration: {e}")

        # -- Ad patterns table columns --
        ap_cols = self._get_table_columns(conn, 'ad_patterns')
        self._add_column_if_missing(conn, 'ad_patterns', 'avg_duration', 'REAL', ap_cols)
        self._add_column_if_missing(conn, 'ad_patterns', 'duration_samples', 'INTEGER DEFAULT 0', ap_cols)

        # Community-pattern columns (2.4.0). source is a CHECK column but
        # SQLite allows ADD COLUMN with DEFAULT — the CHECK is enforced via
        # the SCHEMA_SQL CREATE TABLE; existing rows default to 'local'.
        ap_cols = self._get_table_columns(conn, 'ad_patterns')
        self._add_column_if_missing(conn, 'ad_patterns', 'source', "TEXT NOT NULL DEFAULT 'local'", ap_cols)
        self._add_column_if_missing(conn, 'ad_patterns', 'community_id', 'TEXT', ap_cols)
        self._add_column_if_missing(conn, 'ad_patterns', 'version', 'INTEGER NOT NULL DEFAULT 1', ap_cols)
        self._add_column_if_missing(conn, 'ad_patterns', 'submitted_app_version', 'TEXT', ap_cols)
        self._add_column_if_missing(
            conn, 'ad_patterns', 'protected_from_sync',
            'INTEGER NOT NULL DEFAULT 0', ap_cols,
        )

        # Indexes for source filtering and community_id lookup (idempotent)
        try:
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_patterns_source "
                "ON ad_patterns(source, is_active)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_patterns_community_id "
                "ON ad_patterns(community_id) WHERE community_id IS NOT NULL"
            )
            conn.commit()
        except Exception as e:
            logger.warning(f"Community pattern index creation: {e}")

        # known_sponsors.tags (2.4.0)
        ks_cols = self._get_table_columns(conn, 'known_sponsors')
        self._add_column_if_missing(conn, 'known_sponsors', 'tags', "TEXT NOT NULL DEFAULT '[]'", ks_cols)

        # podcasts.tags and podcasts.user_tags (2.4.0)
        pod_cols = self._get_table_columns(conn, 'podcasts')
        self._add_column_if_missing(conn, 'podcasts', 'tags', "TEXT NOT NULL DEFAULT '[]'", pod_cols)
        self._add_column_if_missing(conn, 'podcasts', 'user_tags', "TEXT NOT NULL DEFAULT '[]'", pod_cols)

        # episodes.tags (2.4.0)
        ep_cols = self._get_table_columns(conn, 'episodes')
        self._add_column_if_missing(conn, 'episodes', 'tags', "TEXT NOT NULL DEFAULT '[]'", ep_cols)

        # Sponsor reseed runs at the END of this migration (see below), after
        # `_migrate_sponsor_fk` so it operates on dedup'd rows.

        # Migration: Update episodes status CHECK constraint to include 'permanently_failed'
        # SQLite doesn't support ALTER TABLE to modify constraints, so we recreate the table
        try:
            cursor = conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='episodes'")
            create_sql = cursor.fetchone()
            if create_sql and 'permanently_failed' not in create_sql[0]:
                logger.info("Migration: Updating episodes table CHECK constraint for permanently_failed status...")

                # Get current column list from old table
                cursor = conn.execute("PRAGMA table_info(episodes)")
                old_columns = [row['name'] for row in cursor.fetchall()]

                # 1. Create new table with correct constraint (matches current SCHEMA_SQL)
                conn.execute("""
                    CREATE TABLE episodes_new (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        podcast_id INTEGER NOT NULL,
                        episode_id TEXT NOT NULL,
                        original_url TEXT NOT NULL,
                        title TEXT,
                        description TEXT,
                        status TEXT DEFAULT 'pending' CHECK(status IN ('pending','processing','processed','failed','permanently_failed')),
                        retry_count INTEGER DEFAULT 0,
                        processed_file TEXT,
                        processed_at TEXT,
                        original_duration REAL,
                        new_duration REAL,
                        ads_removed INTEGER DEFAULT 0,
                        ads_removed_firstpass INTEGER DEFAULT 0,
                        ads_removed_secondpass INTEGER DEFAULT 0,
                        error_message TEXT,
                        ad_detection_status TEXT DEFAULT NULL CHECK(ad_detection_status IN (NULL, 'success', 'failed')),
                        artwork_url TEXT,
                        reprocess_mode TEXT,
                        reprocess_requested_at TEXT,
                        published_at TEXT,
                        created_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
                        updated_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
                        FOREIGN KEY (podcast_id) REFERENCES podcasts(id) ON DELETE CASCADE,
                        UNIQUE(podcast_id, episode_id)
                    )
                """)

                # Get new table columns
                cursor = conn.execute("PRAGMA table_info(episodes_new)")
                new_columns = [row['name'] for row in cursor.fetchall()]

                # Find common columns (exist in both tables)
                common_columns = [c for c in old_columns if c in new_columns]
                columns_str = ', '.join(common_columns)

                # Disable FK to prevent CASCADE deleting episode_details during DROP
                conn.execute("PRAGMA foreign_keys = OFF")

                # 2. Copy data (only common columns, defaults fill the rest)
                conn.execute(f"""
                    INSERT INTO episodes_new ({columns_str})
                    SELECT {columns_str} FROM episodes
                """)

                # 3. Drop old table
                conn.execute("DROP TABLE episodes")

                # 4. Rename new table
                conn.execute("ALTER TABLE episodes_new RENAME TO episodes")

                # 5. Recreate indexes
                conn.execute("CREATE INDEX IF NOT EXISTS idx_episodes_podcast ON episodes(podcast_id)")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_episodes_status ON episodes(status)")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_episodes_processed_at ON episodes(processed_at)")

                conn.commit()

                # Re-enable FK enforcement
                conn.execute("PRAGMA foreign_keys = ON")
                logger.info("Migration: Successfully updated episodes table CHECK constraint")
        except Exception as e:
            logger.error(f"Migration failed for episodes CHECK constraint: {e}")
            raise  # This is critical - app cannot function without this migration

        # Migration: Update episodes status CHECK constraint to include 'discovered'
        try:
            cursor = conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='episodes'")
            create_sql = cursor.fetchone()
            if create_sql and 'discovered' not in create_sql[0]:
                logger.info("Migration: Updating episodes table CHECK constraint for discovered status...")

                cursor = conn.execute("PRAGMA table_info(episodes)")
                old_columns = [row['name'] for row in cursor.fetchall()]

                conn.execute("""
                    CREATE TABLE episodes_new (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        podcast_id INTEGER NOT NULL,
                        episode_id TEXT NOT NULL,
                        original_url TEXT NOT NULL,
                        title TEXT,
                        description TEXT,
                        status TEXT DEFAULT 'pending' CHECK(status IN ('discovered','pending','processing','processed','failed','permanently_failed')),
                        retry_count INTEGER DEFAULT 0,
                        processed_file TEXT,
                        processed_at TEXT,
                        original_duration REAL,
                        new_duration REAL,
                        ads_removed INTEGER DEFAULT 0,
                        ads_removed_firstpass INTEGER DEFAULT 0,
                        ads_removed_secondpass INTEGER DEFAULT 0,
                        error_message TEXT,
                        ad_detection_status TEXT DEFAULT NULL CHECK(ad_detection_status IN (NULL, 'success', 'failed')),
                        artwork_url TEXT,
                        reprocess_mode TEXT,
                        reprocess_requested_at TEXT,
                        published_at TEXT,
                        created_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
                        updated_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
                        FOREIGN KEY (podcast_id) REFERENCES podcasts(id) ON DELETE CASCADE,
                        UNIQUE(podcast_id, episode_id)
                    )
                """)

                cursor = conn.execute("PRAGMA table_info(episodes_new)")
                new_columns = [row['name'] for row in cursor.fetchall()]
                common_columns = [c for c in old_columns if c in new_columns]
                columns_str = ', '.join(common_columns)

                # Disable FK to prevent CASCADE deleting episode_details during DROP
                conn.execute("PRAGMA foreign_keys = OFF")

                conn.execute(f"""
                    INSERT INTO episodes_new ({columns_str})
                    SELECT {columns_str} FROM episodes
                """)

                conn.execute("DROP TABLE episodes")
                conn.execute("ALTER TABLE episodes_new RENAME TO episodes")

                conn.execute("CREATE INDEX IF NOT EXISTS idx_episodes_podcast ON episodes(podcast_id)")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_episodes_status ON episodes(status)")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_episodes_processed_at ON episodes(processed_at)")

                conn.commit()

                # Re-enable FK enforcement
                conn.execute("PRAGMA foreign_keys = ON")
                logger.info("Migration: Successfully updated episodes table CHECK constraint for discovered status")
        except Exception as e:
            logger.error(f"Migration failed for episodes discovered CHECK constraint: {e}")
            raise

        # Migration: Create auto_process_queue table if not exists
        try:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS auto_process_queue (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    podcast_id INTEGER NOT NULL,
                    episode_id TEXT NOT NULL,
                    original_url TEXT NOT NULL,
                    title TEXT,
                    status TEXT DEFAULT 'pending' CHECK(status IN ('pending','processing','completed','failed')),
                    attempts INTEGER DEFAULT 0,
                    error_message TEXT,
                    created_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
                    updated_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
                    FOREIGN KEY (podcast_id) REFERENCES podcasts(id) ON DELETE CASCADE,
                    UNIQUE(podcast_id, episode_id)
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_queue_status ON auto_process_queue(status)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_queue_created ON auto_process_queue(created_at)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_queue_podcast_episode ON auto_process_queue(podcast_id, episode_id)")
            conn.commit()
            logger.info("Migration: Created auto_process_queue table")
        except Exception as e:
            logger.debug(f"auto_process_queue table creation (may already exist): {e}")

        # Migration: Add published_at to auto_process_queue if missing
        try:
            cursor = conn.execute("PRAGMA table_info(auto_process_queue)")
            queue_columns = [row['name'] for row in cursor.fetchall()]
            if 'published_at' not in queue_columns:
                conn.execute("""
                    ALTER TABLE auto_process_queue
                    ADD COLUMN published_at TEXT
                """)
                conn.commit()
                logger.info("Migration: Added published_at column to auto_process_queue table")
        except Exception as e:
            logger.debug(f"auto_process_queue published_at migration: {e}")

        # Migration: Add description to auto_process_queue if missing
        try:
            cursor = conn.execute("PRAGMA table_info(auto_process_queue)")
            queue_columns = [row['name'] for row in cursor.fetchall()]
            if 'description' not in queue_columns:
                conn.execute("""
                    ALTER TABLE auto_process_queue
                    ADD COLUMN description TEXT
                """)
                conn.commit()
                logger.info("Migration: Added description column to auto_process_queue table")
        except Exception as e:
            logger.debug(f"auto_process_queue description migration: {e}")

        # Create new indexes for podcasts table (will fail silently if already exist)
        try:
            conn.execute("CREATE INDEX IF NOT EXISTS idx_podcasts_network_id ON podcasts(network_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_podcasts_dai_platform ON podcasts(dai_platform)")
            conn.commit()
        except Exception as e:
            logger.debug(f"Index creation (may already exist): {e}")

        # Performance indexes for Phase 3 optimization
        performance_indexes = [
            # Compound index for episode queries by podcast + status
            'CREATE INDEX IF NOT EXISTS idx_episodes_podcast_status ON episodes(podcast_id, status)',
            # Published date for sorting recent episodes
            'CREATE INDEX IF NOT EXISTS idx_episodes_published ON episodes(published_at DESC)',
            # Pattern corrections queries
            'CREATE INDEX IF NOT EXISTS idx_corrections_episode ON pattern_corrections(episode_id)',
            'CREATE INDEX IF NOT EXISTS idx_corrections_type ON pattern_corrections(correction_type)',
            # Ad patterns by podcast
            'CREATE INDEX IF NOT EXISTS idx_patterns_podcast ON ad_patterns(podcast_id)',
        ]
        for idx_sql in performance_indexes:
            try:
                conn.execute(idx_sql)
            except Exception as e:
                logger.debug(f"Index creation (may already exist): {e}")
        conn.commit()

        # Migration: Create FTS5 search index table
        try:
            conn.execute("""
                CREATE VIRTUAL TABLE IF NOT EXISTS search_index USING fts5(
                    content_type,
                    content_id,
                    podcast_slug,
                    title,
                    body,
                    metadata,
                    tokenize='porter unicode61'
                )
            """)
            conn.commit()
            logger.info("Migration: Created FTS5 search_index table")
        except Exception as e:
            logger.debug(f"FTS5 search_index creation (may already exist): {e}")

        # Auto-populate search index if empty
        try:
            cursor = conn.execute("SELECT COUNT(*) FROM search_index")
            if cursor.fetchone()[0] == 0:
                logger.info("Search index is empty, rebuilding...")
                count = self.rebuild_search_index()
                logger.info(f"Search index populated with {count} items")
        except Exception as e:
            logger.warning(f"Failed to auto-populate search index: {e}")

        # Migration: Create auth_failures table for login-lockout tracking
        try:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS auth_failures (
                    ip TEXT PRIMARY KEY,
                    failed_count INTEGER NOT NULL DEFAULT 0,
                    first_failed_at TEXT NOT NULL,
                    last_failed_at TEXT NOT NULL,
                    locked_until TEXT
                )
            """)
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_auth_failures_last ON auth_failures(last_failed_at)"
            )
            conn.commit()
            logger.info("Migration: Created auth_failures table")
        except Exception as e:
            logger.debug(f"auth_failures table creation (may already exist): {e}")

        # Migration: Convert numeric podcast_ids to slugs in ad_patterns table
        # This fixes a bug where auto-created patterns stored numeric IDs instead of slugs
        self._migrate_pattern_podcast_ids()

        # Migration: Clean up contaminated patterns (>3500 chars)
        # These are patterns created from merged multi-ad spans and will never match
        self._cleanup_contaminated_patterns()

        # Migration: Update default prompts to v1.0.2 (DAI tagline guidance)
        try:
            cursor = conn.execute(
                "SELECT value, is_default FROM settings WHERE key = 'system_prompt'"
            )
            row = cursor.fetchone()
            if row and row['is_default'] and 'TAGLINE' not in (row['value'] or ''):
                conn.execute(
                    "UPDATE settings SET value = ? WHERE key = 'system_prompt'",
                    (DEFAULT_SYSTEM_PROMPT,)
                )
                conn.commit()
                logger.info("Migration: Updated default system_prompt to v1.0.2 (DAI tagline guidance)")
        except Exception as e:
            logger.warning(f"Migration failed for system_prompt v1.0.2: {e}")

        try:
            cursor = conn.execute(
                "SELECT value, is_default FROM settings WHERE key = 'verification_prompt'"
            )
            row = cursor.fetchone()
            if row and row['is_default'] and 'brand tagline ads' not in (row['value'] or ''):
                conn.execute(
                    "UPDATE settings SET value = ? WHERE key = 'verification_prompt'",
                    (DEFAULT_VERIFICATION_PROMPT,)
                )
                conn.commit()
                logger.info("Migration: Updated default verification_prompt to v1.0.2 (DAI tagline guidance)")
        except Exception as e:
            logger.warning(f"Migration failed for verification_prompt v1.0.2: {e}")

        # Migration: Update default prompts to v1.0.8 (platform-inserted ads guidance)
        try:
            cursor = conn.execute(
                "SELECT value, is_default FROM settings WHERE key = 'system_prompt'"
            )
            row = cursor.fetchone()
            if row and row['is_default'] and 'PLATFORM-INSERTED ADS' not in (row['value'] or ''):
                conn.execute(
                    "UPDATE settings SET value = ? WHERE key = 'system_prompt'",
                    (DEFAULT_SYSTEM_PROMPT,)
                )
                conn.commit()
                logger.info("Migration: Updated default system_prompt to v1.0.8 (platform-inserted ads)")
        except Exception as e:
            logger.warning(f"Migration failed for system_prompt v1.0.8: {e}")

        try:
            cursor = conn.execute(
                "SELECT value, is_default FROM settings WHERE key = 'verification_prompt'"
            )
            row = cursor.fetchone()
            if row and row['is_default'] and 'PLATFORM-INSERTED ADS' not in (row['value'] or ''):
                conn.execute(
                    "UPDATE settings SET value = ? WHERE key = 'verification_prompt'",
                    (DEFAULT_VERIFICATION_PROMPT,)
                )
                conn.commit()
                logger.info("Migration: Updated default verification_prompt to v1.0.8 (platform-inserted ads)")
        except Exception as e:
            logger.warning(f"Migration failed for verification_prompt v1.0.8: {e}")

        # Migration: refresh default reviewer prompts. The marker phrases
        # below are unique to v2.1.2's array-output prompt and absent from
        # earlier reviewer prompts. Only touches is_default=1 rows.
        try:
            from database import DEFAULT_REVIEW_PROMPT, DEFAULT_RESURRECT_PROMPT
            for key, value, marker in (
                ('review_prompt', DEFAULT_REVIEW_PROMPT, 'KEEP THE AD (return one segment)'),
                ('resurrect_prompt', DEFAULT_RESURRECT_PROMPT, 'RESURRECT (return one segment)'),
            ):
                row = conn.execute(
                    "SELECT value, is_default FROM settings WHERE key = ?",
                    (key,)
                ).fetchone()
                if row and row['is_default'] and marker not in (row['value'] or ''):
                    conn.execute(
                        "UPDATE settings SET value = ? WHERE key = ?",
                        (value, key)
                    )
                    conn.commit()
                    logger.info(f"Migration: Updated default {key} to v2.1.2 (array output)")
        except Exception as e:
            logger.warning(f"Migration failed for reviewer prompts v2.1.2: {e}")

        # Migration: Create token usage tables and seed default model pricing
        try:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS model_pricing (
                    model_id TEXT PRIMARY KEY,
                    match_key TEXT,
                    raw_model_id TEXT,
                    display_name TEXT NOT NULL,
                    input_cost_per_mtok REAL NOT NULL,
                    output_cost_per_mtok REAL NOT NULL,
                    source TEXT DEFAULT 'legacy',
                    updated_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS token_usage (
                    model_id TEXT PRIMARY KEY,
                    match_key TEXT,
                    total_input_tokens INTEGER NOT NULL DEFAULT 0,
                    total_output_tokens INTEGER NOT NULL DEFAULT 0,
                    total_cost REAL NOT NULL DEFAULT 0.0,
                    call_count INTEGER NOT NULL DEFAULT 0,
                    updated_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
                )
            """)
            # Seed default pricing (ON CONFLICT DO NOTHING preserves manual edits)
            # Use old column format -- new columns (match_key, raw_model_id, source)
            # are added by the ALTER TABLE migration block that follows, then backfilled.
            for model_id, info in DEFAULT_MODEL_PRICING.items():
                conn.execute(
                    """INSERT INTO model_pricing
                           (model_id, display_name,
                            input_cost_per_mtok, output_cost_per_mtok)
                       VALUES (?, ?, ?, ?)
                       ON CONFLICT(model_id) DO NOTHING""",
                    (model_id, info['name'], info['input'], info['output'])
                )
            conn.commit()
            logger.info("Migration: Created token usage tables and seeded model pricing")
        except Exception as e:
            logger.warning(f"Migration failed for token usage tables: {e}")

        # Migration: Add match_key, raw_model_id, source columns to model_pricing
        try:
            from config import normalize_model_key
            mp_cols = self._get_table_columns(conn, 'model_pricing')
            self._add_column_if_missing(conn, 'model_pricing', 'match_key', 'TEXT', mp_cols)
            self._add_column_if_missing(conn, 'model_pricing', 'raw_model_id', 'TEXT', mp_cols)
            self._add_column_if_missing(conn, 'model_pricing', 'source', "TEXT DEFAULT 'legacy'", mp_cols)

            # Backfill match_key for existing rows
            rows = conn.execute(
                "SELECT model_id FROM model_pricing WHERE match_key IS NULL"
            ).fetchall()
            if rows:
                for row in rows:
                    key = normalize_model_key(row['model_id'])
                    conn.execute(
                        "UPDATE model_pricing SET match_key = ?, raw_model_id = ? WHERE model_id = ?",
                        (key, row['model_id'], row['model_id'])
                    )

                # Deduplicate: if multiple model_ids map to the same match_key,
                # keep the row with the highest rowid per match_key
                dupes = conn.execute("""
                    SELECT model_id, match_key FROM model_pricing
                    WHERE rowid NOT IN (
                        SELECT MAX(rowid) FROM model_pricing
                        GROUP BY match_key
                    )
                """).fetchall()
                if dupes:
                    for dupe in dupes:
                        logger.info(f"Migration: Removing duplicate model_pricing row: "
                                    f"model_id={dupe['model_id']} match_key={dupe['match_key']}")
                    conn.execute("""
                        DELETE FROM model_pricing
                        WHERE rowid NOT IN (
                            SELECT MAX(rowid) FROM model_pricing
                            GROUP BY match_key
                        )
                    """)
                conn.commit()
                logger.info(f"Migration: Backfilled match_key for {len(rows)} model_pricing rows")

            # Create UNIQUE index on match_key (after backfill + dedup)
            conn.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_model_pricing_match_key ON model_pricing(match_key)"
            )
            conn.commit()

            # Add match_key to token_usage
            tu_cols = self._get_table_columns(conn, 'token_usage')
            self._add_column_if_missing(conn, 'token_usage', 'match_key', 'TEXT', tu_cols)

            # Backfill match_key for existing token_usage rows
            tu_rows = conn.execute(
                "SELECT model_id FROM token_usage WHERE match_key IS NULL"
            ).fetchall()
            if tu_rows:
                for row in tu_rows:
                    key = normalize_model_key(row['model_id'])
                    conn.execute(
                        "UPDATE token_usage SET match_key = ? WHERE model_id = ?",
                        (key, row['model_id'])
                    )
                conn.commit()
                logger.info(f"Migration: Backfilled match_key for {len(tu_rows)} token_usage rows")
        except Exception as e:
            logger.warning(f"Migration failed for match_key backfill: {e}")

        # Migration: Add token tracking columns to processing_history
        hist_cols = self._get_table_columns(conn, 'processing_history')
        for col, definition in [
            ('input_tokens', 'INTEGER DEFAULT 0'),
            ('output_tokens', 'INTEGER DEFAULT 0'),
            ('llm_cost', 'REAL DEFAULT 0.0'),
        ]:
            self._add_column_if_missing(conn, 'processing_history', col, definition, hist_cols)

        # Migration: retention_period_minutes -> retention_days
        try:
            retention_days_exists = conn.execute(
                "SELECT COUNT(*) FROM settings WHERE key = 'retention_days'"
            ).fetchone()[0]

            if not retention_days_exists:
                env_minutes = os.environ.get('RETENTION_PERIOD')
                if env_minutes:
                    days = max(1, round(int(env_minutes) / 1440))
                else:
                    existing = conn.execute(
                        "SELECT value, is_default FROM settings WHERE key = 'retention_period_minutes'"
                    ).fetchone()
                    if existing and not existing['is_default']:
                        days = max(1, round(int(existing['value']) / 1440))
                    else:
                        days = 30
                conn.execute(
                    "INSERT INTO settings (key, value, is_default) VALUES ('retention_days', ?, 1)",
                    (str(days),)
                )
                conn.commit()
                logger.info(f"Migration: Created retention_days setting = {days}")
        except Exception as e:
            logger.warning(f"Migration failed for retention_days: {e}")

        try:
            from database import DEFAULT_REVIEW_PROMPT, DEFAULT_RESURRECT_PROMPT
            ad_reviewer_seeds = [
                ('enable_ad_review', 'false'),
                ('review_model', 'same_as_pass'),
                ('review_max_boundary_shift', '60'),
                ('review_prompt', DEFAULT_REVIEW_PROMPT),
                ('resurrect_prompt', DEFAULT_RESURRECT_PROMPT),
            ]
            for key, value in ad_reviewer_seeds:
                conn.execute(
                    """INSERT INTO settings (key, value, is_default) VALUES (?, ?, 1)
                       ON CONFLICT(key) DO NOTHING""",
                    (key, value)
                )
            conn.commit()
            self._migrate_user_prompts_to_placeholders(conn)
        except Exception as e:
            logger.warning(f"Migration failed for ad reviewer settings: {e}")

        # v2.2.0: Migrate ad_patterns.sponsor TEXT to sponsor_id FK against
        # known_sponsors; add ad_patterns.created_by; add
        # pattern_corrections.sponsor_id; extend pattern_corrections CHECK
        # constraint to include 'auto_promotion' and 'create'.
        try:
            self._migrate_sponsor_fk(conn)
        except Exception as e:
            logger.error(f"Sponsor FK migration failed: {e}")

        # 2.2.10: clear sponsor_id on patterns the 2.2.7 alias backfill mislabeled as Zyn.
        self._cleanup_zyn_cascade(conn)

        # Per-stage LLM tunables: rename ad_detection_max_tokens -> detection_max_tokens.
        try:
            self._migrate_ad_detection_max_tokens(conn)
        except Exception as e:
            logger.warning(f"ad_detection_max_tokens migration failed: {e}")

        # 2.2.11: clear sponsor='Zyn' on ad markers (stored as JSON in
        # episode_details.ad_markers_json) whose detected transcript window
        # does not contain the canonical brand. The per-marker sponsor was
        # frozen at detection time, so the 2.2.10 pattern cleanup alone
        # doesn't update what the editor displays for already-detected ads.
        self._cleanup_zyn_ad_markers(conn)

        # Sponsor seed reseed (2.4.0): CSV is authoritative. Runs LAST so
        # `_migrate_sponsor_fk` has already deduped case-variants from
        # legacy v2.1.x rows; the reseed then operates on the canonical
        # post-FK-migration state. UPDATE on name match preserves `id` for
        # any existing `ad_patterns.sponsor_id` foreign keys; orphans are
        # soft-deleted (is_active=0) rather than dropped.
        try:
            self._reseed_known_sponsors(conn)
        except Exception as e:
            logger.error(f"Sponsor reseed failed: {e}")

        # One-shot repair: patterns created before 2.4.6 by
        # text_pattern_matcher have `intro_variants` / `outro_variants`
        # double-JSON-encoded (caller json.dumps'd, then create_ad_pattern
        # json.dumps'd again). The community export pipeline exploded the
        # result into a list of single characters. Idempotent: rows that
        # parse to a list on the first decode are skipped.
        try:
            self._repair_double_encoded_variants(conn)
        except Exception as e:
            logger.error(f"Variant re-encode repair failed: {e}")

        # Pre-2.4.7 community imports preserved the source pattern's scope
        # (usually 'podcast') without a podcast_id, so they never matched.
        # Re-stamp every source=community row to scope='global'.
        try:
            self._normalize_community_scope(conn)
        except Exception as e:
            logger.error(f"Community scope normalize failed: {e}")

    def _normalize_community_scope(self, conn):
        """Set scope='global' on every source=community pattern; clear
        podcast_id / network_id since they were stripped on export. Stamped
        via `community_scope_revision` so this runs once per database."""
        row = conn.execute(
            "SELECT value FROM settings WHERE key = 'community_scope_revision'"
        ).fetchone()
        if row and row['value'] == '1':
            return
        cursor = conn.execute(
            "UPDATE ad_patterns SET scope = 'global', podcast_id = NULL, "
            "network_id = NULL WHERE source = 'community' AND "
            "(scope != 'global' OR podcast_id IS NOT NULL OR network_id IS NOT NULL)"
        )
        repaired = cursor.rowcount
        conn.execute(
            "INSERT OR REPLACE INTO settings (key, value, updated_at) "
            "VALUES ('community_scope_revision', '1', strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))"
        )
        conn.commit()
        if repaired:
            logger.info(f"Normalized scope=global on {repaired} community pattern rows")

    def _repair_double_encoded_variants(self, conn):
        """Re-encode any ad_patterns.intro_variants / outro_variants column
        whose stored value parses (via json.loads) to a string rather than
        a list. Stamps `variant_reencode_revision` so this only runs once
        per database."""
        import json
        cursor = conn.execute(
            "SELECT value FROM settings WHERE key = 'variant_reencode_revision'"
        )
        row = cursor.fetchone()
        if row and row['value'] == '1':
            return

        repaired = 0
        rows = conn.execute(
            "SELECT id, intro_variants, outro_variants FROM ad_patterns"
        ).fetchall()
        for r in rows:
            updates = {}
            for col in ('intro_variants', 'outro_variants'):
                raw = r[col]
                if not raw:
                    continue
                try:
                    parsed = json.loads(raw)
                except (TypeError, ValueError):
                    continue
                if not isinstance(parsed, str):
                    continue  # already a list, nothing to do
                try:
                    inner = json.loads(parsed)
                except (TypeError, ValueError):
                    continue
                if not isinstance(inner, list):
                    continue
                updates[col] = json.dumps(inner)
            if updates:
                fields = ', '.join(f'{k} = ?' for k in updates)
                conn.execute(
                    f"UPDATE ad_patterns SET {fields} WHERE id = ?",
                    list(updates.values()) + [r['id']],
                )
                repaired += 1

        conn.execute(
            "INSERT OR REPLACE INTO settings (key, value, updated_at) "
            "VALUES ('variant_reencode_revision', '1', strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))"
        )
        conn.commit()
        if repaired:
            logger.info(f"Re-encoded intro/outro_variants on {repaired} ad_patterns rows")

    def _reseed_known_sponsors(self, conn):
        """Apply the authoritative sponsor seed list (src/seed_data/sponsors_final.csv).

        UPDATE on name match (case-insensitive) to preserve `id` for any
        existing ad_patterns.sponsor_id foreign keys. INSERT new rows.
        Soft-delete (is_active=0) any existing sponsor whose name is not in
        the CSV. Idempotent: re-running yields the same end state.

        Stamps a settings flag (`sponsor_seed_revision`) on success so we
        only do meaningful work once per app version that ships a new seed.
        """
        from utils.community_tags import sponsor_seed

        # Bump this when the seed CSV is replaced so the migration re-runs.
        SEED_REVISION = '2.4.0'
        try:
            current = conn.execute(
                "SELECT value FROM settings WHERE key = 'sponsor_seed_revision'"
            ).fetchone()
            if current and current['value'] == SEED_REVISION:
                return
        except Exception:
            # Settings table may not exist yet on a fresh-create path; carry on.
            pass

        seed = sponsor_seed()
        seed_names_lower = {row['name'].lower() for row in seed}

        # Build existing-name -> id map (case-insensitive)
        existing = conn.execute(
            "SELECT id, name FROM known_sponsors"
        ).fetchall()
        existing_by_lower = {row['name'].lower(): row['id'] for row in existing}

        updated = 0
        inserted = 0
        for row in seed:
            name = row['name']
            aliases_json = json.dumps(row['aliases'])
            tags_json = json.dumps(row['tags'])
            existing_id = existing_by_lower.get(name.lower())
            if existing_id is not None:
                conn.execute(
                    "UPDATE known_sponsors SET aliases = ?, tags = ?, is_active = 1 "
                    "WHERE id = ?",
                    (aliases_json, tags_json, existing_id),
                )
                updated += 1
            else:
                conn.execute(
                    "INSERT INTO known_sponsors (name, aliases, tags, is_active) "
                    "VALUES (?, ?, ?, 1)",
                    (name, aliases_json, tags_json),
                )
                inserted += 1

        # Soft-delete orphans: existing sponsors not present in the seed.
        orphans = [
            row_id for lower, row_id in existing_by_lower.items()
            if lower not in seed_names_lower
        ]
        deactivated = 0
        for row_id in orphans:
            conn.execute(
                "UPDATE known_sponsors SET is_active = 0 WHERE id = ?",
                (row_id,),
            )
            deactivated += 1

        # Record the revision so this migration is a no-op next boot.
        conn.execute(
            "INSERT INTO settings (key, value, is_default) VALUES (?, ?, 0) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            ('sponsor_seed_revision', SEED_REVISION),
        )
        conn.commit()
        logger.info(
            f"Migration: sponsor seed v{SEED_REVISION} applied "
            f"({inserted} inserted, {updated} updated, {deactivated} deactivated)"
        )

    def _cleanup_zyn_cascade(self, conn):
        try:
            zyn_row = conn.execute(
                "SELECT id FROM known_sponsors WHERE LOWER(name) = 'zyn'"
            ).fetchone()
            if not zyn_row:
                return
            zyn_id = zyn_row['id']
            rows = conn.execute(
                "SELECT id, text_template FROM ad_patterns "
                "WHERE sponsor_id = ? AND text_template IS NOT NULL",
                (zyn_id,)
            ).fetchall()
            ids_to_clear = [
                row['id'] for row in rows
                if not re.search(r'\bZyn\b', row['text_template'] or '', re.IGNORECASE)
            ]
            if ids_to_clear:
                placeholders = ','.join('?' * len(ids_to_clear))
                conn.execute(
                    f"UPDATE ad_patterns SET sponsor_id = NULL WHERE id IN ({placeholders})",
                    ids_to_clear
                )
                conn.commit()
                logger.info(
                    f"Migration: cleared sponsor_id on {len(ids_to_clear)} "
                    f"patterns whose text does not contain 'Zyn'"
                )
        except Exception as e:
            logger.warning(f"Migration: Zyn cascade cleanup failed: {e}")

    def _migrate_ad_detection_max_tokens(self, conn):
        """Rename ad_detection_max_tokens -> detection_max_tokens.

        Idempotent: if the new key already exists, the old value is dropped
        rather than overwriting the new one. If only the old exists, its value
        is copied first. Either way, the old key is cleaned up.
        """
        cursor = conn.execute(
            "SELECT value, is_default FROM settings WHERE key = ?",
            ('ad_detection_max_tokens',),
        )
        row = cursor.fetchone()
        if row is None:
            return
        old_value, old_is_default = row[0], row[1]
        inserted = conn.execute(
            "INSERT INTO settings (key, value, is_default) VALUES (?, ?, ?) "
            "ON CONFLICT(key) DO NOTHING",
            ('detection_max_tokens', old_value, old_is_default),
        )
        conn.execute(
            "DELETE FROM settings WHERE key = ?",
            ('ad_detection_max_tokens',),
        )
        conn.commit()
        if inserted.rowcount:
            logger.info(
                "Migrated settings key ad_detection_max_tokens -> detection_max_tokens"
            )
        else:
            logger.info(
                "Dropped legacy settings key ad_detection_max_tokens "
                "(detection_max_tokens already present)"
            )

    def _cleanup_zyn_ad_markers(self, conn):
        try:
            from utils.text import extract_text_in_range
        except Exception as e:
            logger.warning(f"Migration: ad-marker Zyn cleanup skipped (import failed): {e}")
            return
        try:
            rows = conn.execute(
                "SELECT episode_id, ad_markers_json, original_transcript_text "
                "FROM episode_details "
                "WHERE ad_markers_json IS NOT NULL AND ad_markers_json LIKE '%Zyn%'"
            ).fetchall()
            markers_cleared = 0
            episodes_touched = 0
            for row in rows:
                try:
                    markers = json.loads(row['ad_markers_json'])
                except (json.JSONDecodeError, TypeError):
                    continue
                if not isinstance(markers, list):
                    continue
                transcript = row['original_transcript_text'] or ''
                changed = False
                for marker in markers:
                    if not isinstance(marker, dict):
                        continue
                    sponsor = (marker.get('sponsor') or '').strip()
                    if sponsor.lower() != 'zyn':
                        continue
                    start = marker.get('start')
                    end = marker.get('end')
                    if not (isinstance(start, (int, float)) and isinstance(end, (int, float))):
                        continue
                    window_text = extract_text_in_range(transcript, float(start), float(end))
                    if re.search(r'\bZyn\b', window_text, re.IGNORECASE):
                        continue
                    marker['sponsor'] = None
                    reason = marker.get('reason') or ''
                    if 'Zyn' in reason:
                        marker['reason'] = (
                            re.sub(r'^\s*Zyn[:\s]*', '', reason).strip() or None
                        )
                    markers_cleared += 1
                    changed = True
                if changed:
                    conn.execute(
                        "UPDATE episode_details SET ad_markers_json = ? WHERE episode_id = ?",
                        (json.dumps(markers), row['episode_id'])
                    )
                    episodes_touched += 1
            if markers_cleared:
                conn.commit()
                logger.info(
                    f"Migration: cleared sponsor='Zyn' on {markers_cleared} ad markers "
                    f"across {episodes_touched} episodes whose detected text does not contain 'Zyn'"
                )
        except Exception as e:
            logger.warning(f"Migration: ad-marker Zyn cleanup failed: {e}")

    def _migrate_sponsor_fk(self, conn):
        """v2.2.0: Migrate ad_patterns.sponsor TEXT to sponsor_id FK.

        Steps, each idempotent:
          1. Add `ad_patterns.sponsor_id`, `ad_patterns.created_by`,
             `pattern_corrections.sponsor_id`.
          2. Dedup `known_sponsors` rows whose names differ only by case
             (keep lowest id).
          3. Snapshot `ad_patterns.sponsor` to a backup table.
          4. Backfill `ad_patterns.sponsor_id` via sponsor_normalize.
          5. Backfill `pattern_corrections.sponsor_id` from the joined
             ad_pattern row.
          6. Verify: `PRAGMA foreign_key_check` empty, and backfill row
             count matches the snapshot.
          7. Drop `ad_patterns.sponsor` via table-recreation.
          8. Recreate `pattern_corrections` with extended CHECK constraint.
          9. Drop the backup table.

        If step 6 fails, destructive steps 7-9 are skipped. The new columns
        and the backup table remain in place so the migration can be retried
        on next startup with no data loss.
        """
        from sponsor_normalize import get_or_create_known_sponsor

        # 1. Add new columns (idempotent)
        ap_cols = self._get_table_columns(conn, 'ad_patterns')
        self._add_column_if_missing(
            conn, 'ad_patterns', 'sponsor_id',
            'INTEGER REFERENCES known_sponsors(id)', ap_cols
        )
        self._add_column_if_missing(
            conn, 'ad_patterns', 'created_by',
            "TEXT DEFAULT 'auto'", ap_cols
        )
        pc_cols = self._get_table_columns(conn, 'pattern_corrections')
        self._add_column_if_missing(
            conn, 'pattern_corrections', 'sponsor_id',
            'INTEGER REFERENCES known_sponsors(id)', pc_cols
        )

        # If the old text column is already gone, the destructive part of
        # the migration ran on a previous startup. Nothing to do.
        ap_cols = self._get_table_columns(conn, 'ad_patterns')
        if 'sponsor' not in ap_cols:
            return

        # 2. Dedup case-variant known_sponsors rows (lowest id wins)
        dupe_groups = conn.execute(
            """SELECT LOWER(name) AS lname, MIN(id) AS keep_id, COUNT(*) AS n
               FROM known_sponsors GROUP BY LOWER(name) HAVING n > 1"""
        ).fetchall()
        for row in dupe_groups:
            conn.execute(
                "DELETE FROM known_sponsors WHERE LOWER(name) = ? AND id <> ?",
                (row['lname'], row['keep_id'])
            )
        if dupe_groups:
            logger.info(
                f"Sponsor FK migration: deduped {len(dupe_groups)} "
                f"case-variant sponsor groups in known_sponsors"
            )

        # 3. Snapshot ad_patterns.sponsor before any destructive op
        backup_exists = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
            ('_migration_backup_ad_patterns_sponsor',)
        ).fetchone() is not None
        current_nonnull = conn.execute(
            "SELECT COUNT(*) AS n FROM ad_patterns WHERE sponsor IS NOT NULL"
        ).fetchone()['n']
        if backup_exists:
            backup_n = conn.execute(
                "SELECT COUNT(*) AS n FROM _migration_backup_ad_patterns_sponsor"
            ).fetchone()['n']
            if backup_n != current_nonnull:
                logger.warning(
                    f"Sponsor FK migration: stale backup table "
                    f"(rows={backup_n}, live={current_nonnull}); recreating"
                )
                conn.execute("DROP TABLE _migration_backup_ad_patterns_sponsor")
                backup_exists = False
        if not backup_exists:
            conn.execute(
                """CREATE TABLE _migration_backup_ad_patterns_sponsor AS
                   SELECT id, sponsor FROM ad_patterns WHERE sponsor IS NOT NULL"""
            )
        snapshot_n = conn.execute(
            "SELECT COUNT(*) AS n FROM _migration_backup_ad_patterns_sponsor"
        ).fetchone()['n']

        # 4. Backfill ad_patterns.sponsor_id
        rows = conn.execute(
            """SELECT id, sponsor FROM ad_patterns
               WHERE sponsor IS NOT NULL AND sponsor_id IS NULL"""
        ).fetchall()
        for row in rows:
            sid = get_or_create_known_sponsor(self, row['sponsor'])
            if sid is None:
                logger.warning(
                    f"Sponsor FK migration: could not resolve sponsor "
                    f"{row['sponsor']!r} for ad_patterns.id={row['id']}; "
                    f"leaving sponsor_id NULL"
                )
                continue
            conn.execute(
                "UPDATE ad_patterns SET sponsor_id = ? WHERE id = ?",
                (sid, row['id'])
            )

        # 5. Backfill pattern_corrections.sponsor_id from the joined pattern row
        conn.execute(
            """UPDATE pattern_corrections SET sponsor_id = (
                   SELECT sponsor_id FROM ad_patterns
                   WHERE ad_patterns.id = pattern_corrections.pattern_id
               )
               WHERE pattern_id IS NOT NULL AND sponsor_id IS NULL"""
        )
        conn.commit()

        # 6. Verify
        fk_violations = conn.execute(
            "PRAGMA foreign_key_check(ad_patterns)"
        ).fetchall()
        fk_violations_pc = conn.execute(
            "PRAGMA foreign_key_check(pattern_corrections)"
        ).fetchall()
        if fk_violations or fk_violations_pc:
            logger.error(
                f"Sponsor FK migration: foreign_key_check failed; "
                f"ad_patterns violations={[dict(r) for r in fk_violations][:10]}, "
                f"pattern_corrections violations={[dict(r) for r in fk_violations_pc][:10]}; "
                f"aborting destructive steps. Re-run on next startup."
            )
            return
        backfilled_n = conn.execute(
            "SELECT COUNT(*) AS n FROM ad_patterns WHERE sponsor_id IS NOT NULL"
        ).fetchone()['n']
        if backfilled_n != snapshot_n:
            unresolved = conn.execute(
                """SELECT b.id, b.sponsor FROM _migration_backup_ad_patterns_sponsor b
                   LEFT JOIN ad_patterns ap ON ap.id = b.id
                   WHERE ap.sponsor_id IS NULL LIMIT 10"""
            ).fetchall()
            logger.error(
                f"Sponsor FK migration: backfill parity failed "
                f"(expected {snapshot_n}, got {backfilled_n}); "
                f"first unresolved rows: {[dict(r) for r in unresolved]}; "
                f"aborting destructive steps. Re-run on next startup."
            )
            return

        # 7-9. Destructive: recreate both tables, drop backup
        try:
            conn.execute("PRAGMA foreign_keys = OFF")

            # Drop the now-stale text-sponsor index before rebuilding the table
            conn.execute("DROP INDEX IF EXISTS idx_patterns_sponsor")

            # 7. Recreate ad_patterns without `sponsor` text column
            old_ap_cols = [
                r['name'] for r in conn.execute("PRAGMA table_info(ad_patterns)").fetchall()
            ]
            conn.execute("""
                CREATE TABLE ad_patterns_new (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    scope TEXT NOT NULL CHECK(scope IN ('global', 'network', 'podcast')),
                    network_id TEXT,
                    podcast_id TEXT,
                    dai_platform TEXT,
                    text_template TEXT,
                    intro_variants TEXT DEFAULT '[]',
                    outro_variants TEXT DEFAULT '[]',
                    sponsor_id INTEGER REFERENCES known_sponsors(id),
                    confirmation_count INTEGER DEFAULT 0,
                    false_positive_count INTEGER DEFAULT 0,
                    last_matched_at TEXT,
                    created_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
                    created_from_episode_id TEXT,
                    is_active INTEGER DEFAULT 1,
                    disabled_at TEXT,
                    disabled_reason TEXT,
                    avg_duration REAL,
                    duration_samples INTEGER DEFAULT 0,
                    created_by TEXT DEFAULT 'auto'
                )
            """)
            new_ap_cols = [
                r['name'] for r in conn.execute("PRAGMA table_info(ad_patterns_new)").fetchall()
            ]
            common_ap = [c for c in old_ap_cols if c in new_ap_cols]
            cols_str = ', '.join(common_ap)
            conn.execute(
                f"INSERT INTO ad_patterns_new ({cols_str}) SELECT {cols_str} FROM ad_patterns"
            )
            conn.execute("DROP TABLE ad_patterns")
            conn.execute("ALTER TABLE ad_patterns_new RENAME TO ad_patterns")
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_patterns_sponsor_id "
                "ON ad_patterns(sponsor_id) WHERE is_active = 1"
            )

            # 8. Recreate pattern_corrections with extended CHECK + sponsor_id FK
            old_pc_cols = [
                r['name'] for r in conn.execute("PRAGMA table_info(pattern_corrections)").fetchall()
            ]
            conn.execute("""
                CREATE TABLE pattern_corrections_new (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    pattern_id INTEGER,
                    episode_id TEXT,
                    podcast_title TEXT,
                    episode_title TEXT,
                    correction_type TEXT NOT NULL CHECK(correction_type IN (
                        'false_positive', 'boundary_adjustment', 'confirm',
                        'promotion', 'auto_promotion', 'create'
                    )),
                    original_bounds TEXT,
                    corrected_bounds TEXT,
                    text_snippet TEXT,
                    created_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
                    sponsor_id INTEGER REFERENCES known_sponsors(id)
                )
            """)
            new_pc_cols = [
                r['name'] for r in conn.execute("PRAGMA table_info(pattern_corrections_new)").fetchall()
            ]
            common_pc = [c for c in old_pc_cols if c in new_pc_cols]
            cols_str = ', '.join(common_pc)
            conn.execute(
                f"INSERT INTO pattern_corrections_new ({cols_str}) "
                f"SELECT {cols_str} FROM pattern_corrections"
            )
            conn.execute("DROP TABLE pattern_corrections")
            conn.execute("ALTER TABLE pattern_corrections_new RENAME TO pattern_corrections")
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_corrections_type "
                "ON pattern_corrections(correction_type)"
            )

            # 9. Drop the backup table; we're done
            conn.execute("DROP TABLE _migration_backup_ad_patterns_sponsor")

            conn.commit()
            logger.info(
                f"Sponsor FK migration: completed (migrated {snapshot_n} rows; "
                f"dropped ad_patterns.sponsor; extended pattern_corrections CHECK)"
            )
        finally:
            conn.execute("PRAGMA foreign_keys = ON")

    def _cleanup_contaminated_patterns(self):
        """Delete patterns with text_template > 3500 chars (contaminated).

        These patterns were created from merged multi-ad spans where adjacent ads
        within 3 seconds were combined. The resulting patterns are too long to
        ever match the TF-IDF window and pollute the pattern database.
        """
        conn = self.get_connection()
        MAX_PATTERN_CHARS = 3500

        try:
            # Get count first
            cursor = conn.execute(
                "SELECT COUNT(*) FROM ad_patterns WHERE length(text_template) > ?",
                (MAX_PATTERN_CHARS,)
            )
            count = cursor.fetchone()[0]

            if count > 0:
                logger.info(
                    f"Migration: Cleaning up {count} contaminated patterns "
                    f"(>{MAX_PATTERN_CHARS} chars)"
                )
                conn.execute(
                    "DELETE FROM ad_patterns WHERE length(text_template) > ?",
                    (MAX_PATTERN_CHARS,)
                )
                conn.commit()
                logger.info(f"Migration: Deleted {count} contaminated patterns")

        except Exception as e:
            logger.error(f"Migration failed for contaminated pattern cleanup: {e}")

    def _migrate_pattern_podcast_ids(self):
        """Convert numeric podcast_ids to slugs in ad_patterns table for consistency.

        This fixes a bug where auto-created patterns stored numeric podcast IDs,
        but the pattern matching code compares against slug strings.
        """
        conn = self.get_connection()

        try:
            # Get mapping of numeric IDs to slugs
            podcasts = conn.execute("SELECT id, slug FROM podcasts").fetchall()
            id_to_slug = {str(p['id']): p['slug'] for p in podcasts}

            if not id_to_slug:
                return  # No podcasts yet

            # Find patterns with numeric podcast_ids that need migration
            patterns = conn.execute(
                "SELECT id, podcast_id FROM ad_patterns WHERE podcast_id IS NOT NULL"
            ).fetchall()

            migrated_count = 0
            for pattern in patterns:
                pid = pattern['podcast_id']
                # Check if this looks like a numeric ID (and we have a mapping for it)
                if pid in id_to_slug:
                    conn.execute(
                        "UPDATE ad_patterns SET podcast_id = ? WHERE id = ?",
                        (id_to_slug[pid], pattern['id'])
                    )
                    migrated_count += 1

            if migrated_count > 0:
                conn.commit()
                logger.info(f"Migration: Converted {migrated_count} pattern podcast_ids from numeric to slug")

        except Exception as e:
            logger.error(f"Migration failed for pattern podcast_ids: {e}")

    def _migrate_from_json(self):
        """Migrate data from JSON files to SQLite."""
        from database import DEFAULT_SYSTEM_PROMPT, DEFAULT_VERIFICATION_PROMPT

        conn = self.get_connection()

        # Check if migration already done
        cursor = conn.execute("SELECT COUNT(*) FROM podcasts")
        if cursor.fetchone()[0] > 0:
            logger.debug("Database already contains data, skipping migration")
            return

        # Check for settings - if empty, seed defaults
        cursor = conn.execute("SELECT COUNT(*) FROM settings")
        if cursor.fetchone()[0] == 0:
            self._seed_default_settings(conn)

        # Migrate feeds.json
        feeds_path = Path("./config/feeds.json")
        if not feeds_path.exists():
            feeds_path = self.data_dir.parent / "config" / "feeds.json"

        if feeds_path.exists():
            try:
                with open(feeds_path) as f:
                    feeds = json.load(f)

                for feed in feeds:
                    slug = feed['out'].strip('/').replace('/', '-')
                    source_url = feed['in']

                    conn.execute(
                        """INSERT INTO podcasts (slug, source_url) VALUES (?, ?)
                           ON CONFLICT(slug) DO NOTHING""",
                        (slug, source_url)
                    )

                logger.info(f"Migrated {len(feeds)} feeds from feeds.json")
            except Exception as e:
                logger.error(f"Failed to migrate feeds.json: {e}")

        # Migrate per-podcast data.json files
        for podcast_dir in self.data_dir.iterdir():
            if not podcast_dir.is_dir():
                continue

            data_file = podcast_dir / "data.json"
            if not data_file.exists():
                continue

            slug = podcast_dir.name

            try:
                # Ensure podcast exists
                cursor = conn.execute(
                    "SELECT id FROM podcasts WHERE slug = ?", (slug,)
                )
                row = cursor.fetchone()

                if not row:
                    # Create podcast entry with empty source URL
                    conn.execute(
                        "INSERT INTO podcasts (slug, source_url) VALUES (?, ?)",
                        (slug, "")
                    )
                    cursor = conn.execute(
                        "SELECT id FROM podcasts WHERE slug = ?", (slug,)
                    )
                    row = cursor.fetchone()

                podcast_id = row['id']

                # Load and migrate episodes
                with open(data_file) as f:
                    data = json.load(f)

                # Update last_checked
                if data.get('last_checked'):
                    conn.execute(
                        "UPDATE podcasts SET last_checked_at = ? WHERE id = ?",
                        (data['last_checked'], podcast_id)
                    )

                # Migrate episodes
                for episode_id, ep_data in data.get('episodes', {}).items():
                    conn.execute(
                        """INSERT INTO episodes
                           (podcast_id, episode_id, original_url, title, status,
                            processed_file, processed_at, original_duration,
                            new_duration, ads_removed, error_message)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                           ON CONFLICT(podcast_id, episode_id) DO NOTHING""",
                        (
                            podcast_id,
                            episode_id,
                            ep_data.get('original_url', ''),
                            ep_data.get('title'),
                            ep_data.get('status', 'pending'),
                            ep_data.get('processed_file'),
                            ep_data.get('processed_at') or ep_data.get('failed_at'),
                            ep_data.get('original_duration'),
                            ep_data.get('new_duration'),
                            ep_data.get('ads_removed', 0),
                            ep_data.get('error')
                        )
                    )

                logger.info(f"Migrated data for podcast: {slug}")

            except Exception as e:
                logger.error(f"Failed to migrate data for {slug}: {e}")

        conn.commit()
        logger.info("JSON to SQLite migration completed")

    def _seed_default_settings(self, conn: 'sqlite3.Connection'):
        """Seed default settings."""
        from database import (
            DEFAULT_SYSTEM_PROMPT,
            DEFAULT_VERIFICATION_PROMPT,
            DEFAULT_REVIEW_PROMPT,
            DEFAULT_RESURRECT_PROMPT,
        )

        conn.execute(
            """INSERT INTO settings (key, value, is_default) VALUES (?, ?, 1)
               ON CONFLICT(key) DO NOTHING""",
            ('system_prompt', DEFAULT_SYSTEM_PROMPT)
        )

        # Retention period from env or default 24 hours
        retention_minutes = os.environ.get('RETENTION_PERIOD', '1440')
        conn.execute(
            """INSERT INTO settings (key, value, is_default) VALUES (?, ?, 1)
               ON CONFLICT(key) DO NOTHING""",
            ('retention_period_minutes', retention_minutes)
        )

        # Keep original (pre-cut) audio alongside processed output so the ad
        # editor can play the untouched track for boundary review. Roughly
        # doubles per-episode audio storage; user can opt out in Settings.
        conn.execute(
            """INSERT INTO settings (key, value, is_default) VALUES (?, ?, 1)
               ON CONFLICT(key) DO NOTHING""",
            ('keep_original_audio', 'true')
        )

        # Processing timeouts (soft = auto-clear stuck jobs; hard = force-release).
        # Env var overrides are only used here for seeding; runtime changes live in DB.
        soft_default = os.environ.get('PROCESSING_SOFT_TIMEOUT', '3600')
        hard_default = os.environ.get('PROCESSING_HARD_TIMEOUT', '7200')
        conn.execute(
            """INSERT INTO settings (key, value, is_default) VALUES (?, ?, 1)
               ON CONFLICT(key) DO NOTHING""",
            ('processing_soft_timeout_seconds', soft_default)
        )
        conn.execute(
            """INSERT INTO settings (key, value, is_default) VALUES (?, ?, 1)
               ON CONFLICT(key) DO NOTHING""",
            ('processing_hard_timeout_seconds', hard_default)
        )

        # Verification pass prompt
        conn.execute(
            """INSERT INTO settings (key, value, is_default) VALUES (?, ?, 1)
               ON CONFLICT(key) DO NOTHING""",
            ('verification_prompt', DEFAULT_VERIFICATION_PROMPT)
        )

        # Verification pass model (defaults to same as first pass)
        from config import DEFAULT_AD_DETECTION_MODEL as DEFAULT_MODEL
        provider = os.environ.get('LLM_PROVIDER', 'anthropic').lower()
        env_model = os.environ.get('OPENAI_MODEL') if provider != 'anthropic' else None
        conn.execute(
            """INSERT INTO settings (key, value, is_default) VALUES (?, ?, 1)
               ON CONFLICT(key) DO NOTHING""",
            ('verification_model', env_model or DEFAULT_MODEL)
        )

        # Migrate old second_pass settings to verification settings
        try:
            old_prompt = None
            old_model = None
            cursor = conn.execute("SELECT key, value FROM settings WHERE key IN ('second_pass_prompt', 'second_pass_model')")
            for row in cursor:
                if row[0] == 'second_pass_prompt':
                    old_prompt = row[1]
                elif row[0] == 'second_pass_model':
                    old_model = row[1]

            if old_prompt:
                conn.execute(
                    "INSERT INTO settings (key, value, is_default) VALUES (?, ?, 0) ON CONFLICT(key) DO NOTHING",
                    ('verification_prompt', old_prompt)
                )
            if old_model:
                conn.execute(
                    "INSERT INTO settings (key, value, is_default) VALUES (?, ?, 0) ON CONFLICT(key) DO NOTHING",
                    ('verification_model', old_model)
                )
        except Exception as e:
            logger.warning(f"Settings migration (second_pass -> verification): {e}")

        # Whisper model (defaults to env var or 'small')
        whisper_model = os.environ.get('WHISPER_MODEL', 'small')
        conn.execute(
            """INSERT INTO settings (key, value, is_default) VALUES (?, ?, 1)
               ON CONFLICT(key) DO NOTHING""",
            ('whisper_model', whisper_model)
        )

        # Whisper language. ISO 639-1 code (e.g. 'en', 'fi', 'es') or 'auto'
        # to let Whisper detect. Default English preserves prior behavior.
        whisper_language = os.environ.get('WHISPER_LANGUAGE') or 'en'
        conn.execute(
            """INSERT INTO settings (key, value, is_default) VALUES (?, ?, 1)
               ON CONFLICT(key) DO NOTHING""",
            ('whisper_language', whisper_language)
        )

        # Audio analysis settings
        audio_analysis_settings = [
            ('volume_threshold_db', '3.0'),
            ('transition_threshold_db', '3.5'),
        ]
        for key, value in audio_analysis_settings:
            conn.execute(
                """INSERT INTO settings (key, value, is_default) VALUES (?, ?, 1)
                   ON CONFLICT(key) DO NOTHING""",
                (key, value)
            )

        # Ad detection aggressiveness (minimum confidence to cut from audio)
        # Lower = more aggressive (removes more potential ads)
        # Higher = more conservative (removes only high-confidence ads)
        # Range: 0.50 to 0.95, default 0.80
        conn.execute(
            """INSERT INTO settings (key, value, is_default) VALUES (?, ?, 1)
               ON CONFLICT(key) DO NOTHING""",
            ('min_cut_confidence', '0.80')
        )

        # Auto-process new episodes (enabled by default)
        conn.execute(
            """INSERT INTO settings (key, value, is_default) VALUES (?, ?, 1)
               ON CONFLICT(key) DO NOTHING""",
            ('auto_process_enabled', 'true')
        )

        # Default cap on episodes returned in served RSS feeds (per-feed
        # max_episodes overrides this when set).
        conn.execute(
            """INSERT INTO settings (key, value, is_default) VALUES (?, ?, 1)
               ON CONFLICT(key) DO NOTHING""",
            ('max_feed_episodes', '300')
        )

        # Global default for hiding unprocessed episodes from served RSS
        # feeds (per-feed only_expose_processed_episodes overrides when set).
        conn.execute(
            """INSERT INTO settings (key, value, is_default) VALUES (?, ?, 1)
               ON CONFLICT(key) DO NOTHING""",
            ('only_expose_processed_default', 'false')
        )

        # Audio output bitrate (defaults to 128k)
        conn.execute(
            """INSERT INTO settings (key, value, is_default) VALUES (?, ?, 1)
               ON CONFLICT(key) DO NOTHING""",
            ('audio_bitrate', '128k')
        )

        # VTT transcripts enabled (Podcasting 2.0)
        conn.execute(
            """INSERT INTO settings (key, value, is_default) VALUES (?, ?, 1)
               ON CONFLICT(key) DO NOTHING""",
            ('vtt_transcripts_enabled', 'true')
        )

        # Chapters enabled (Podcasting 2.0)
        conn.execute(
            """INSERT INTO settings (key, value, is_default) VALUES (?, ?, 1)
               ON CONFLICT(key) DO NOTHING""",
            ('chapters_enabled', 'true')
        )

        # Chapters model (Podcasting 2.0) - provider-aware default
        from chapters_generator import CHAPTERS_MODEL
        conn.execute(
            """INSERT INTO settings (key, value, is_default) VALUES (?, ?, 1)
               ON CONFLICT(key) DO NOTHING""",
            ('chapters_model', env_model or CHAPTERS_MODEL)
        )

        # LLM provider (seeded from env; runtime changes go via settings API)
        conn.execute(
            """INSERT INTO settings (key, value, is_default) VALUES (?, ?, 1)
               ON CONFLICT(key) DO NOTHING""",
            ('llm_provider', os.environ.get('LLM_PROVIDER', 'anthropic'))
        )

        # OpenAI base URL (seeded from env; runtime changes go via settings API)
        conn.execute(
            """INSERT INTO settings (key, value, is_default) VALUES (?, ?, 1)
               ON CONFLICT(key) DO NOTHING""",
            ('openai_base_url', os.environ.get('OPENAI_BASE_URL', 'http://localhost:8000/v1'))
        )

        ad_reviewer_seeds = [
            ('enable_ad_review', 'false'),
            ('review_model', 'same_as_pass'),
            ('review_max_boundary_shift', '60'),
            ('review_prompt', DEFAULT_REVIEW_PROMPT),
            ('resurrect_prompt', DEFAULT_RESURRECT_PROMPT),
        ]
        for key, value in ad_reviewer_seeds:
            conn.execute(
                """INSERT INTO settings (key, value, is_default) VALUES (?, ?, 1)
                   ON CONFLICT(key) DO NOTHING""",
                (key, value)
            )

        conn.commit()
        logger.info("Default settings seeded")

        self._migrate_user_prompts_to_placeholders(conn)

    def _migrate_user_prompts_to_placeholders(self, conn: 'sqlite3.Connection'):
        """One-time backfill: append ``{sponsor_database}`` to user-customized
        system / verification prompts.

        Before this change, ad_detector.py unconditionally appended a sponsor
        block to every prompt at runtime. After the placeholder switch, prompts
        without a ``{sponsor_database}`` placeholder get no sponsor content -
        which would silently strip the dynamic sponsor list from any
        user-customized prompt that pre-dates this release. This migration
        adds the placeholder so behavior is preserved.

        Idempotent via _review_prompt_migrated flag. Touches only is_default=0
        prompts (user-customized), since defaults are reseeded fresh on every
        startup.
        """
        try:
            row = conn.execute(
                "SELECT value FROM settings WHERE key = ?",
                ('_review_prompt_migrated',)
            ).fetchone()
            if row is not None:
                return
        except Exception:
            return

        for key in ('system_prompt', 'verification_prompt'):
            try:
                row = conn.execute(
                    "SELECT value, is_default FROM settings WHERE key = ?",
                    (key,)
                ).fetchone()
                if not row:
                    continue
                value = row[0] if not isinstance(row, dict) else row['value']
                is_default = row[1] if not isinstance(row, dict) else row['is_default']
                if is_default:
                    continue
                if not value or '{sponsor_database}' in value:
                    continue
                conn.execute(
                    "UPDATE settings SET value = ? WHERE key = ?",
                    (value + '{sponsor_database}', key)
                )
                logger.info(
                    f"Migration: appended {{sponsor_database}} placeholder to "
                    f"customized {key}"
                )
            except Exception as e:
                logger.warning(f"Migration: failed to backfill {key}: {e}")

        conn.execute(
            """INSERT INTO settings (key, value, is_default) VALUES (?, ?, 1)
               ON CONFLICT(key) DO NOTHING""",
            ('_review_prompt_migrated', 'true')
        )
        conn.commit()
