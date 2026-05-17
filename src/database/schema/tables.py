"""SQL DDL constants for MinusPod database schema.

SCHEMA_SQL: Full initial schema (CREATE TABLE / CREATE INDEX) executed on
fresh databases via conn.executescript(SCHEMA_SQL).

MIGRATION_INDEXES_SQL: Indexes that depend on columns added by ALTER TABLE
migrations; created separately after _run_schema_migrations completes.

This module is import-only (no behavior). Both constants are re-exported by
database.schema for backward compatibility with all existing call sites.
"""

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
