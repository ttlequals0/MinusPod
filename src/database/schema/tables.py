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
    language_override TEXT,
    title_override TEXT,
    detection_mode TEXT,
    cue_template_score_override REAL,
    cue_create_from_pairs_override INTEGER,
    cue_pair_min_break_override REAL,
    cue_pair_max_break_override REAL,
    cue_pair_max_break_fraction_override REAL,
    cue_snap_confidence_override REAL,
    cue_snap_lead_override REAL,
    cue_snap_lag_override REAL,
    -- Boundary-snap opt-in flags (NULL/0 = off, 1 = on; no global to inherit)
    silence_snap_enabled INTEGER,
    transition_snap_enabled INTEGER,
    -- Layer 3 cross-fetch differential opt-in (NULL/0 = off, 1 = on)
    differential_fetch_enabled INTEGER,
    -- Phase C held-for-review per-feed settings
    max_ad_duration_override REAL,
    cue_gated_approval INTEGER DEFAULT 0,
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
    status TEXT DEFAULT 'pending' CHECK(status IN ('discovered','pending','processing','processed','failed','permanently_failed','deferred')),
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
    pending_review_count INTEGER NOT NULL DEFAULT 0,
    error_message TEXT,
    -- Offline queue (#482): when the episode FIRST entered the offline queue
    -- and which service ('llm' or 'whisper') was unreachable. deferred_at
    -- survives re-drive cycles so the TTL bounds total time in the deferred
    -- lifecycle; cleared on success, manual reprocess, and TTL expiry.
    deferred_at TEXT,
    deferred_service TEXT,
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
    protected_from_sync INTEGER NOT NULL DEFAULT 0,
    source_language TEXT,
    content_hash TEXT
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

-- audio_cue_templates (per-feed user-defined ding/stinger templates, #350)
-- mfcc_blob: float32 little-endian, shape (n_frames, n_coeffs) row-major.
--   Frames are 25 ms / 10 ms hop @ sample_rate Hz. n_coeffs stored separately.
-- pcm_blob: raw captured window, int16 little-endian mono @ pcm_sample_rate.
--   Source of truth so a template can be re-derived if MFCC params change or
--   exported as a lossless WAV. Nullable for rows imported without raw PCM.
-- scope: 'podcast' (this feed) or 'network' (all feeds sharing network_id).
--   No 'global' tier -- a cue only matches a show using the exact same sound.
CREATE TABLE IF NOT EXISTS audio_cue_templates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    podcast_id INTEGER NOT NULL,
    label TEXT NOT NULL,
    source_episode_id TEXT,
    source_offset_s REAL NOT NULL,
    duration_s REAL NOT NULL,
    sample_rate INTEGER NOT NULL,
    n_coeffs INTEGER NOT NULL,
    mfcc_blob BLOB NOT NULL,
    pcm_blob BLOB,
    pcm_sample_rate INTEGER,
    scope TEXT NOT NULL DEFAULT 'podcast' CHECK(scope IN ('network', 'podcast')),
    network_id TEXT,
    -- No CHECK: config.AUDIO_CUE_TYPES + the API are the source of truth for the
    -- allowed set, so a new type (#350 content_transition) needs no table rebuild.
    cue_type TEXT NOT NULL DEFAULT 'ad_break_boundary',
    enabled INTEGER NOT NULL DEFAULT 1,
    score_threshold REAL,
    created_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    created_by TEXT DEFAULT 'user',
    FOREIGN KEY (podcast_id) REFERENCES podcasts(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_cue_templates_feed ON audio_cue_templates(podcast_id, enabled);
CREATE INDEX IF NOT EXISTS idx_cue_templates_scope ON audio_cue_templates(scope, network_id, podcast_id) WHERE enabled = 1;

-- cue_detections (per-cue telemetry, #350 follow-up). One row per template cue
-- the matcher surfaced for an episode, with the match score and how detection
-- used it (snap / pair / none) plus the user's review verdict. Advisory only:
-- nothing here changes the cut list; it lets the user judge a feed's cues and
-- tune thresholds. Rows for an episode are replaced on reprocess.
CREATE TABLE IF NOT EXISTS cue_detections (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    podcast_id INTEGER NOT NULL,
    episode_id TEXT NOT NULL,
    template_id INTEGER,
    label TEXT,
    cue_type TEXT,
    role TEXT,
    source TEXT NOT NULL DEFAULT 'template',
    start_s REAL NOT NULL,
    end_s REAL NOT NULL,
    match_score REAL,
    confidence REAL,
    -- no CHECK: outcomes validated in app code (_VALID_OUTCOMES).
    outcome TEXT NOT NULL DEFAULT 'none',
    verdict TEXT NOT NULL DEFAULT 'pending' CHECK(verdict IN ('pending', 'confirmed', 'rejected')),
    -- Signed distance from an above-threshold cue to the nearest pre-snap LLM
    -- ad edge on its eligible side; NULL for advisory (non_ad) cues (#350 Ph6).
    edge_distance_s REAL,
    -- Taxonomy explaining an outcome='none' (covered / out_of_reach /
    -- below_snap_confidence / advisory_role / unpaired / pair-skip reason).
    unused_reason TEXT,
    created_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    FOREIGN KEY (podcast_id) REFERENCES podcasts(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_cue_detections_episode ON cue_detections(episode_id);
CREATE INDEX IF NOT EXISTS idx_cue_detections_feed ON cue_detections(podcast_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_cue_detections_template ON cue_detections(template_id);

-- Cached result of the on-demand "find recurring sounds" scan (#350 follow-up).
-- The scan decodes the whole episode and is slow (90s+ on a long show), so it
-- runs in a background thread and the API polls this row instead of blocking
-- the request past the proxy timeout. One row per (feed, episode).
CREATE TABLE IF NOT EXISTS cue_candidate_scans (
    podcast_id INTEGER NOT NULL,
    episode_id TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'scanning' CHECK(status IN ('scanning', 'ready', 'error')),
    candidates_json TEXT,
    error TEXT,
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    claim_epoch INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (podcast_id, episode_id),
    FOREIGN KEY (podcast_id) REFERENCES podcasts(id) ON DELETE CASCADE
);

-- cue_threshold_scans: cached result of the on-demand threshold-suggest sweep.
-- Mirrors cue_candidate_scans but stores a suggestion dict instead of candidates.
-- The sweep runs in a background thread; the API polls this row.
CREATE TABLE IF NOT EXISTS cue_threshold_scans (
    podcast_id INTEGER NOT NULL,
    episode_id TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'scanning' CHECK(status IN ('scanning', 'ready', 'error')),
    result_json TEXT,
    error TEXT,
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    claim_epoch INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (podcast_id, episode_id),
    FOREIGN KEY (podcast_id) REFERENCES podcasts(id) ON DELETE CASCADE
);

-- cue_cross_episode_scans: cached result of the cross-episode body scan (D1b, #350).
-- Keyed by (podcast_id, episode_set_hash) where episode_set_hash is the sha256
-- of the sorted episode-id list (hex). Stores candidates in target-episode
-- coordinates plus an echo of the episode set so the UI can seed the template flow.
CREATE TABLE IF NOT EXISTS cue_cross_episode_scans (
    podcast_id INTEGER NOT NULL,
    episode_set_hash TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'scanning' CHECK(status IN ('scanning', 'ready', 'error')),
    result_json TEXT,
    error TEXT,
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    claim_epoch INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (podcast_id, episode_set_hash),
    FOREIGN KEY (podcast_id) REFERENCES podcasts(id) ON DELETE CASCADE
);

-- cue_window_optimize_scans: cached result of the per-template window optimizer (D2a, #350).
-- Keyed by template_id alone (the optimizer is per-template, not per-episode). Stores the
-- proposed window and per-episode peak scores so the UI can offer a one-click apply.
CREATE TABLE IF NOT EXISTS cue_window_optimize_scans (
    template_id INTEGER NOT NULL PRIMARY KEY,
    status TEXT NOT NULL DEFAULT 'scanning' CHECK(status IN ('scanning', 'ready', 'error')),
    result_json TEXT,
    error TEXT,
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    claim_epoch INTEGER NOT NULL DEFAULT 0,
    FOREIGN KEY (template_id) REFERENCES audio_cue_templates(id) ON DELETE CASCADE
);
"""

# Indexes that depend on columns added by migrations - created separately
MIGRATION_INDEXES_SQL = """
CREATE INDEX IF NOT EXISTS idx_podcasts_network_id ON podcasts(network_id);
CREATE INDEX IF NOT EXISTS idx_podcasts_dai_platform ON podcasts(dai_platform);
CREATE INDEX IF NOT EXISTS idx_patterns_scope ON ad_patterns(scope, network_id, podcast_id) WHERE is_active = 1;
"""
