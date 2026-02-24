"""SQLite database module for MinusPod."""
import sqlite3
import threading
import logging
import json
import os
import time
from pathlib import Path
from datetime import datetime, timedelta, timezone
from typing import Optional, Dict, List, Any, Tuple

import nh3

from utils.time import parse_timestamp
from utils.text import extract_text_in_range

logger = logging.getLogger(__name__)

# Default ad detection prompts
DEFAULT_SYSTEM_PROMPT = """Analyze this podcast transcript and identify ALL advertisement segments.

DETECTION RULES:
- Host-read sponsor segments ARE ads. Any product promotion for compensation is an ad.
- An ad MUST contain promotional language in the transcript. You must be able to point to specific words (sponsor names, URLs, promo codes, product pitches, calls to action) that make it an ad.
- Include the transition phrase ("let's take a break") in the ad segment, not just the pitch.
- Ad breaks typically last 60-120 seconds. Shorter segments may indicate incomplete detection.
- If no ads are found in this window, return: []

WHAT IS NOT AN AD:
- Silence, pauses, or dead air between segments -- these are normal production gaps, not ads
- Topic transitions or content gaps where the host changes subjects
- Audio signal changes (volume shifts, tone changes) without any promotional transcript content
- A guest discussing their own work, book, or project in the context of the interview
- The host organically mentioning their own other shows, social media, or Patreon as part of conversation
- Brand names mentioned in passing as part of genuine topic discussion

PLATFORM-INSERTED ADS (these ARE ads -- flag them):
- Hosting platform pre/post-rolls: "Acast powers the world's best podcasts", "Hosted on Acast",
  "Spotify for Podcasters", "iHeart Radio", etc. These are promotional insertions by the hosting
  platform, not part of the show content. They typically bookend the episode.
- Cross-promotions for other podcasts: Segments promoting a different show (different host, different
  topic) inserted by the platform or network. These are ads even without promo codes.
- Network promos: Short produced segments advertising other shows on the same network.
- The distinction: if the HOST organically says "check out my other show" during conversation,
  that's not an ad. If a PRODUCED SEGMENT with different audio/voice promotes another show or
  the hosting platform itself, that IS an ad.

WHAT TO LOOK FOR:
- Transitions: "This episode is brought to you by...", "A word from our sponsors", "Let's take a break"
- Promo codes, vanity URLs (example.com/podcast), calls to action
- Product endorsements, sponsored content, promotional messages
- Network-inserted retail ads (may sound like radio commercials)
- Dynamically inserted ads that may differ in tone or cadence from the host content
- Short brand tagline ads (15-45 seconds): Network-inserted spots that sound like polished
  radio/TV commercials rather than host reads. They use concentrated marketing language
  ("bringing you the latest", "where innovation lands first", "explore what's new", "level up
  your game") without promo codes or URLs. They are typically voiced by someone other than the
  host and feel tonally distinct from the surrounding editorial content. Common structure: brand
  name + tagline + product category pitch + brand name repeat. Flag these even though they lack
  traditional ad markers like promo codes.

AUDIO SIGNALS:
Audio analysis may detect volume anomalies, DAI transitions, or silence gaps in the episode.
These signals are SUPPORTING EVIDENCE ONLY. They help locate potential ad boundaries but do NOT
constitute ads by themselves. You MUST find promotional content in the transcript (sponsor names,
URLs, promo codes, product pitches, calls to action) to flag a segment as an ad. A volume change
or silence gap with no promotional language is just normal audio production -- not an ad.

COMMON PODCAST SPONSORS (high confidence if mentioned):
BetterHelp, Athletic Greens, AG1, Shopify, Amazon, Audible, Squarespace, HelloFresh, Factor, NordVPN, ExpressVPN, Mint Mobile, MasterClass, Calm, Headspace, ZipRecruiter, Indeed, LinkedIn Jobs, LinkedIn, Stamps.com, SimpliSafe, Ring, ADT, Casper, Helix Sleep, Purple, Brooklinen, Bombas, Manscaped, Dollar Shave Club, Harry's, Quip, Hims, Hers, Roman, Keeps, Function of Beauty, Native, Liquid IV, Athletic Brewing, Magic Spoon, Thrive Market, Butcher Box, Blue Apron, DoorDash, Uber Eats, Grubhub, Instacart, Rocket Money, Credit Karma, SoFi, Acorns, Betterment, Wealthfront, PolicyGenius, Lemonade, State Farm, Progressive, Geico, Liberty Mutual, T-Mobile, Visible, FanDuel, DraftKings, BetMGM, Toyota, Hyundai, CarMax, Carvana, eBay Motors, ZocDoc, GoodRx, Care/of, Ritual, Seed, HubSpot, NetSuite, Monday.com, Notion, Canva, Grammarly, Babbel, Rosetta Stone, Blinkist, Raycon, Bose, MacPaw, CleanMyMac, Green Chef, Magic Mind, Honeylove, Cozy Earth, Quince, LMNT, Nutrafol, Aura, OneSkin, Incogni, Gametime, 1Password, Bitwarden, CacheFly, Deel, DeleteMe, Framer, Miro, Monarch Money, OutSystems, Spaceship, Thinkst Canary, ThreatLocker, Vanta, Veeam, Zapier, Zscaler, Capital One, Ford, WhatsApp

RETAIL/CONSUMER BRANDS (network-inserted ads):
Nordstrom, Macy's, Target, Walmart, Kohl's, Bloomingdale's, JCPenney, TJ Maxx, Home Depot, Lowe's, Best Buy, Costco, Gap, Old Navy, H&M, Zara, Nike, Adidas, Lululemon, Coach, Kate Spade, Michael Kors, Sephora, Ulta, Bath & Body Works, CVS, Walgreens, AutoZone, O'Reilly Auto Parts, Jiffy Lube, Midas, Gold Belly, Farmer's Dog, Caldera Lab, Monster Energy, Red Bull, Whole Foods, Trader Joe's, Kroger, GNC

AD BOUNDARY RULES:
- AD START: Include transition phrases like "Let's take a break", "A word from our sponsors"
- AD END: The ad ends when SHOW CONTENT resumes, NOT when the pitch ends. Wait for:
  - Topic change back to episode content
  - Host says "anyway", "alright", "so" and changes subject
  - AFTER the final URL mention (they often repeat it)
- MERGING: Multiple ads with gaps < 15 seconds = ONE segment

WINDOW CONTEXT:
This transcript may be a segment of a longer episode.
- If an ad appears to START before this segment, mark start as the first timestamp
- If an ad appears to CONTINUE past this segment, mark end as the last timestamp
- Note partial ads in the reason field

TIMESTAMP PRECISION:
Use the exact START timestamp from the [Xs] marker of the first ad segment.
Use the exact END timestamp from the [Xs] marker of the last ad segment.
Do not interpolate or estimate times between segments.

OUTPUT FORMAT:
Return ONLY a valid JSON array. No explanation, no markdown.

Each ad segment: {{"start": seconds, "end": seconds, "confidence": 0.0-1.0, "reason": "brief description", "end_text": "last 3-5 words"}}

EXAMPLE:
[45.0s - 48.0s] That's a great point. Let's take a quick break.
[48.5s - 52.0s] This episode is brought to you by Athletic Greens.
[52.5s - 78.0s] AG1 is the daily foundational nutrition supplement... Go to athleticgreens.com/podcast.
[78.5s - 82.0s] That's athleticgreens.com/podcast.
[82.5s - 86.0s] Now, back to our conversation.

Output: [{{"start": 45.0, "end": 82.0, "confidence": 0.98, "reason": "Athletic Greens sponsor read", "end_text": "athleticgreens.com/podcast"}}]

NOT AN AD EXAMPLE (silence/content gap):
[290.0s - 293.0s] So that's really the core of what GPT-4 can do.
[293.5s - 296.0s] [silence]
[296.5s - 300.0s] Now the other thing I wanted to talk about is the fine-tuning process.

Output: []

SHORT BRAND TAGLINE EXAMPLE (this IS an ad):
[874.2s - 877.0s] FreshField Market, your destination for what's next in nutrition.
[877.0s - 886.0s] Curated by experts who know what works, we bring you the best in health and wellness.
[886.0s - 893.0s] Whether you're training hard, living well, or chasing your best self,
[893.0s - 898.5s] FreshField Market is where the future of wellness begins. Explore more at FreshField.

Output: [{{"start": 874.2, "end": 898.5, "confidence": 0.95, "reason": "FreshField Market network-inserted brand tagline ad", "end_text": "wellness begins. Explore more at FreshField"}}]

Note: No promo code, no call to action -- but this is concentrated marketing copy
for a brand with product positioning language. It is not editorial content."""

# Verification pass prompt - runs on processed audio to catch missed ads
DEFAULT_VERIFICATION_PROMPT = """You are reviewing a podcast episode that has ALREADY had advertisements removed. The audio has been processed — detected ads were cut and replaced with a brief transition tone. Your job is to find anything that was MISSED or only partially removed.

CONTEXT:
This is a second pass over processed audio. The first pass already detected and removed obvious ads. What remains should be clean episode content. Anything promotional that is still present was either:
1. An ad that was completely missed
2. A fragment of an ad that was partially cut (boundary was off by a few seconds)
3. A subtle baked-in ad that blended with the conversation

WHAT TO LOOK FOR:

AD FRAGMENTS (highest priority):
- Orphaned URLs: "dot com slash podcast", "dot com slash [code]"
- Orphaned promo codes: "use code [X] for", "code [X] at checkout"
- Orphaned calls to action: "link in the show notes", "check it out at", "sign up at"
- Trailing sponsor mentions: "that's [brand].com", "thanks to [sponsor]"
- Leading transitions that survived the cut: "and now a word from", "this episode is brought to you"
These fragments appear near transition points where the previous cut boundary was slightly off.

MISSED ADS:
- Full sponsor reads that the first pass missed entirely
- Mid-roll ads without obvious transition phrases ("I've been using [product]...")
- Dynamically inserted ads that may differ in tone from the host content
- Short brand tagline ads (15-45 seconds): Network-inserted spots with concentrated marketing
  language but no promo codes or URLs. These sound like polished radio commercials -- a brand
  name, tagline, product pitch, and brand repeat. They are NOT host reads and feel tonally
  distinct from surrounding content. Flag these even without traditional ad markers.
- Quick mid-roll mentions with URLs or promo codes
- Post-signoff promotional content after the episode's natural ending

WHAT IS NOT AN AD:
- A guest discussing their own work, book, or project in the context of the interview
- The host organically mentioning their own other shows, social media, or Patreon during conversation
- Genuine topic discussion that happens to mention a brand name in passing
- Episode content that sounds slightly awkward due to surrounding ad removal
- Silence, pauses, or dead air -- these are normal, not missed ads
- Content gaps or topic transitions between segments
- Audio artifacts from the first pass ad removal (slight volume changes near cut points are expected)

PLATFORM-INSERTED ADS (these ARE ads -- flag them if still present):
- Hosting platform pre/post-rolls: "Acast powers the world's best podcasts", "Hosted on Acast",
  "Spotify for Podcasters", "iHeart Radio", etc. These are promotional insertions, not show content.
- Cross-promotions for other podcasts: Produced segments promoting a different show (different host,
  different topic) inserted by the platform or network. These are ads even without promo codes.
- Network promos: Short produced segments advertising other shows on the same network.
- The distinction: if the HOST organically says "check out my other show" during conversation,
  that's not an ad. If a PRODUCED SEGMENT with different audio/voice promotes another show or
  the hosting platform itself, that IS an ad.

NOTE: A short, polished segment with marketing language for a brand IS still an ad even if
it lacks promo codes or URLs. The distinction is: editorial content discusses a brand in
context of a story; a tagline ad is pure promotional copy with no informational value.

CRITICAL: Every ad you flag must contain identifiable promotional language in the transcript -- a sponsor name, URL, promo code, product pitch, or call to action. If the transcript text in a region is just normal conversation, silence, or a topic change, it is NOT an ad regardless of any audio signal changes.

HOW TO IDENTIFY FRAGMENTS:
A fragment is promotional language that appears abruptly at the start or end of a content section. In the processed audio, the flow should be: natural conversation → transition tone → natural conversation. If instead you see: natural conversation → transition tone → "...dot com slash podcast. Anyway, back to..." → natural conversation, that trailing "dot com slash podcast" is a fragment from an incompletely removed ad.

AD BOUNDARY RULES:
- AD START: First promotional word or transition phrase
- AD END: Where clean episode content resumes (after the last URL, promo code, or call to action)
- For fragments: mark the ENTIRE fragment including any surrounding promotional context
- MERGING: Multiple fragments or ads with gaps < 15 seconds = ONE segment

WINDOW CONTEXT:
This transcript may be a segment of a longer episode.
- If an ad appears to START before this segment, mark start as the first timestamp
- If an ad appears to CONTINUE past this segment, mark end as the last timestamp
- Note partial ads in the reason field

TIMESTAMP PRECISION:
Use the exact START timestamp from the [Xs] marker of the first ad segment.
Use the exact END timestamp from the [Xs] marker of the last ad segment.
Do not interpolate or estimate times between segments.

BE ACCURATE: Don't invent ads. Many episodes will be completely clean after the first pass. An empty result [] is expected and valid for well-processed episodes.

OUTPUT FORMAT:
Return ONLY a valid JSON array. No explanation, no markdown.

Each ad segment: {{"start": seconds, "end": seconds, "confidence": 0.0-1.0, "reason": "brief description", "end_text": "last 3-5 words"}}

FRAGMENT EXAMPLE:
[120.0s - 122.0s] So yeah, that's really interesting.
[122.5s - 124.0s] [transition tone]
[124.5s - 128.0s] at athleticgreens.com slash podcast. Anyway, moving on to
[128.5s - 132.0s] the next topic I wanted to discuss was the new research.

Output: [{{"start": 124.5, "end": 128.0, "confidence": 0.95, "reason": "Athletic Greens ad fragment — orphaned URL after cut boundary", "end_text": "moving on to"}}]

MISSED AD EXAMPLE:
[340.0s - 342.0s] You know what I've been really into lately?
[342.5s - 348.0s] I've been using this app called Calm and it's been amazing for my sleep.
[348.5s - 365.0s] They have these sleep stories and meditations... You can try it free for 30 days at calm.com/podcast.
[365.5s - 368.0s] But anyway, getting back to what we were saying about

Output: [{{"start": 340.0, "end": 365.0, "confidence": 0.92, "reason": "Calm app sponsor read — missed baked-in ad with free trial URL", "end_text": "calm.com/podcast"}}]

CLEAN EPISODE EXAMPLE:
[no promotional content found in transcript]

Output: []"""


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
    transcript_vtt TEXT,
    chapters_json TEXT,
    ad_markers_json TEXT,
    first_pass_response TEXT,
    first_pass_prompt TEXT,
    second_pass_prompt TEXT,
    second_pass_response TEXT,
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
    sponsor TEXT,
    confirmation_count INTEGER DEFAULT 0,
    false_positive_count INTEGER DEFAULT 0,
    last_matched_at TEXT,
    created_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    created_from_episode_id TEXT,
    is_active INTEGER DEFAULT 1,
    disabled_at TEXT,
    disabled_reason TEXT
);

-- pattern_corrections table (user corrections; conflicting entries cleaned up on reversal)
CREATE TABLE IF NOT EXISTS pattern_corrections (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    pattern_id INTEGER,
    episode_id TEXT,
    podcast_title TEXT,
    episode_title TEXT,
    correction_type TEXT NOT NULL CHECK(correction_type IN ('false_positive', 'boundary_adjustment', 'confirm', 'promotion')),
    original_bounds TEXT,
    corrected_bounds TEXT,
    text_snippet TEXT,
    created_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
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

CREATE INDEX IF NOT EXISTS idx_podcasts_slug ON podcasts(slug);
CREATE INDEX IF NOT EXISTS idx_episodes_podcast_id ON episodes(podcast_id);
CREATE INDEX IF NOT EXISTS idx_episodes_episode_id ON episodes(episode_id);
CREATE INDEX IF NOT EXISTS idx_episodes_status ON episodes(status);
CREATE INDEX IF NOT EXISTS idx_episodes_created_at ON episodes(created_at);
CREATE INDEX IF NOT EXISTS idx_episode_details_episode_id ON episode_details(episode_id);

-- Cross-episode training indexes (indexes on new columns created in migrations)
CREATE INDEX IF NOT EXISTS idx_patterns_sponsor ON ad_patterns(sponsor) WHERE is_active = 1;
CREATE INDEX IF NOT EXISTS idx_fingerprints_pattern ON audio_fingerprints(pattern_id);
CREATE INDEX IF NOT EXISTS idx_corrections_pattern ON pattern_corrections(pattern_id);
CREATE INDEX IF NOT EXISTS idx_sponsors_name ON known_sponsors(name) WHERE is_active = 1;
CREATE INDEX IF NOT EXISTS idx_normalizations_pattern ON sponsor_normalizations(pattern) WHERE is_active = 1;
"""

# Indexes that depend on columns added by migrations - created separately
MIGRATION_INDEXES_SQL = """
CREATE INDEX IF NOT EXISTS idx_podcasts_network_id ON podcasts(network_id);
CREATE INDEX IF NOT EXISTS idx_podcasts_dai_platform ON podcasts(dai_platform);
CREATE INDEX IF NOT EXISTS idx_patterns_scope ON ad_patterns(scope, network_id, podcast_id) WHERE is_active = 1;
"""


class Database:
    """SQLite database manager with thread-safe connections."""

    _instance = None
    _lock = threading.Lock()

    def __new__(cls, data_dir: str = "/app/data"):
        """Singleton pattern for database instance."""
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance

    def __init__(self, data_dir: str = "/app/data"):
        if self._initialized:
            return

        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.data_dir / "podcast.db"
        self._local = threading.local()
        self._initialized = True

        # Initialize schema
        self._init_schema()

        # Run migration if needed
        self._migrate_from_json()

    def get_connection(self) -> sqlite3.Connection:
        """Get thread-local database connection."""
        if not hasattr(self._local, 'connection') or self._local.connection is None:
            self._local.connection = sqlite3.connect(
                str(self.db_path),
                check_same_thread=False,
                timeout=30.0
            )
            self._local.connection.row_factory = sqlite3.Row
            # Enable WAL mode for better concurrent access (reads don't block writes)
            self._local.connection.execute("PRAGMA journal_mode = WAL")
            # Set busy timeout to 30 seconds (SQLite will retry instead of failing immediately)
            self._local.connection.execute("PRAGMA busy_timeout = 30000")
            self._local.connection.execute("PRAGMA foreign_keys = ON")
        return self._local.connection

    class _TransactionContext:
        """Context manager for database transactions with automatic commit/rollback."""
        def __init__(self, conn):
            self.conn = conn
        def __enter__(self):
            return self.conn
        def __exit__(self, exc_type, exc_val, exc_tb):
            if exc_type is None:
                self.conn.commit()
            else:
                self.conn.rollback()
            return False

    def transaction(self):
        """Context manager for database transactions.

        Usage:
            with db.transaction() as conn:
                conn.execute("INSERT ...")
                conn.execute("UPDATE ...")
            # Auto-commits on success, auto-rolls back on exception
        """
        return self._TransactionContext(self.get_connection())

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
                sponsor TEXT,
                confirmation_count INTEGER DEFAULT 0,
                false_positive_count INTEGER DEFAULT 0,
                last_matched_at TEXT,
                created_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
                created_from_episode_id TEXT,
                is_active INTEGER DEFAULT 1,
                disabled_at TEXT,
                disabled_reason TEXT
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
                correction_type TEXT NOT NULL CHECK(correction_type IN ('false_positive', 'boundary_adjustment', 'confirm', 'promotion')),
                original_bounds TEXT,
                corrected_bounds TEXT,
                text_snippet TEXT,
                created_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
            )
        """)

        # Create known_sponsors table if not exists
        conn.execute("""
            CREATE TABLE IF NOT EXISTS known_sponsors (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT UNIQUE NOT NULL,
                aliases TEXT DEFAULT '[]',
                category TEXT,
                common_ctas TEXT DEFAULT '[]',
                is_active INTEGER DEFAULT 1,
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
                created_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
                FOREIGN KEY (podcast_id) REFERENCES podcasts(id) ON DELETE CASCADE
            )
        """)

        # Create indexes for processing_history
        conn.execute("CREATE INDEX IF NOT EXISTS idx_history_processed_at ON processing_history(processed_at DESC)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_history_podcast_episode ON processing_history(podcast_id, episode_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_history_status ON processing_history(status)")

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
            logger.error(f"Migration failed for {table}.{column}: {e}")
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
                logger.error(f"Migration failed for {table} rename {old_name}: {e}")
        return False

    def _get_table_columns(self, conn, table: str) -> set:
        """Get the set of column names for a table."""
        cursor = conn.execute(f"PRAGMA table_info({table})")
        return {row['name'] for row in cursor.fetchall()}

    def _run_schema_migrations(self):
        """Run schema migrations for existing databases."""
        conn = self.get_connection()

        # -- Episodes table columns --
        ep_cols = self._get_table_columns(conn, 'episodes')
        episodes_migrations = [
            ('ad_detection_status', 'TEXT DEFAULT NULL'),
            ('created_at', "TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))"),
            ('artwork_url', 'TEXT'),
            ('processed_file', 'TEXT'),
            ('processed_at', 'TEXT'),
            ('original_duration', 'REAL'),
            ('ads_removed_firstpass', 'INTEGER DEFAULT 0'),
            ('ads_removed_secondpass', 'INTEGER DEFAULT 0'),
            ('description', 'TEXT'),
            ('reprocess_mode', 'TEXT'),
            ('reprocess_requested_at', 'TEXT'),
            ('published_at', 'TEXT'),
            ('retry_count', 'INTEGER DEFAULT 0'),
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
            ('etag', 'TEXT'),
            ('last_modified_header', 'TEXT'),
        ]
        for col, definition in podcasts_migrations:
            self._add_column_if_missing(conn, 'podcasts', col, definition, pod_cols)

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
                logger.info("Migration: Successfully updated episodes table CHECK constraint")
        except Exception as e:
            logger.error(f"Migration failed for episodes CHECK constraint: {e}")
            raise  # This is critical - app cannot function without this migration

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

    def _seed_default_settings(self, conn: sqlite3.Connection):
        """Seed default settings."""
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

        # Verification pass prompt
        conn.execute(
            """INSERT INTO settings (key, value, is_default) VALUES (?, ?, 1)
               ON CONFLICT(key) DO NOTHING""",
            ('verification_prompt', DEFAULT_VERIFICATION_PROMPT)
        )

        # Verification pass model (defaults to same as first pass)
        from ad_detector import DEFAULT_MODEL
        conn.execute(
            """INSERT INTO settings (key, value, is_default) VALUES (?, ?, 1)
               ON CONFLICT(key) DO NOTHING""",
            ('verification_model', DEFAULT_MODEL)
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

        conn.commit()
        logger.info("Default settings seeded")

    # ========== Podcast Methods ==========

    def get_all_podcasts(self) -> List[Dict]:
        """Get all podcasts with episode counts."""
        conn = self.get_connection()
        cursor = conn.execute("""
            SELECT p.*,
                   COUNT(e.id) as episode_count,
                   SUM(CASE WHEN e.status = 'processed' THEN 1 ELSE 0 END) as processed_count,
                   MAX(e.created_at) as last_episode_date
            FROM podcasts p
            LEFT JOIN episodes e ON p.id = e.podcast_id
            GROUP BY p.id
            ORDER BY p.created_at DESC
        """)
        return [dict(row) for row in cursor.fetchall()]

    def get_podcast_by_slug(self, slug: str) -> Optional[Dict]:
        """Get podcast by slug with episode counts."""
        conn = self.get_connection()
        cursor = conn.execute("""
            SELECT p.*,
                   COUNT(e.id) as episode_count,
                   SUM(CASE WHEN e.status = 'processed' THEN 1 ELSE 0 END) as processed_count,
                   MAX(e.created_at) as last_episode_date
            FROM podcasts p
            LEFT JOIN episodes e ON p.id = e.podcast_id
            WHERE p.slug = ?
            GROUP BY p.id
        """, (slug,))
        row = cursor.fetchone()
        return dict(row) if row else None

    def create_podcast(self, slug: str, source_url: str, title: str = None) -> int:
        """Create a new podcast. Returns podcast ID."""
        conn = self.get_connection()
        cursor = conn.execute(
            """INSERT INTO podcasts (slug, source_url, title) VALUES (?, ?, ?)""",
            (slug, source_url, title)
        )
        conn.commit()
        return cursor.lastrowid

    def update_podcast(self, slug: str, **kwargs) -> bool:
        """Update podcast fields."""
        if not kwargs:
            return False

        conn = self.get_connection()

        # Build update query
        fields = []
        values = []
        for key, value in kwargs.items():
            if key in ('title', 'description', 'artwork_url', 'artwork_cached',
                       'last_checked_at', 'source_url', 'network_id', 'dai_platform',
                       'network_id_override', 'audio_analysis_override', 'auto_process_override',
                       'etag', 'last_modified_header'):
                fields.append(f"{key} = ?")
                values.append(value)

        if not fields:
            return False

        fields.append("updated_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now')")
        values.append(slug)

        conn.execute(
            f"UPDATE podcasts SET {', '.join(fields)} WHERE slug = ?",
            values
        )
        conn.commit()
        return True

    def delete_podcast(self, slug: str) -> bool:
        """Delete podcast and all associated data."""
        conn = self.get_connection()
        cursor = conn.execute(
            "DELETE FROM podcasts WHERE slug = ?", (slug,)
        )
        conn.commit()
        return cursor.rowcount > 0

    def update_podcast_etag(self, slug: str, etag: str, last_modified: str) -> bool:
        """Update ETag and Last-Modified header for conditional GET support.

        Args:
            slug: Podcast slug
            etag: ETag header value from RSS server
            last_modified: Last-Modified header value from RSS server

        Returns:
            True if update succeeded
        """
        return self.update_podcast(slug, etag=etag, last_modified_header=last_modified)

    # ========== Episode Methods ==========

    def get_episodes(self, slug: str, status: str = None,
                     limit: int = 50, offset: int = 0) -> Tuple[List[Dict], int]:
        """Get episodes for a podcast with pagination."""
        conn = self.get_connection()

        # Get podcast ID
        podcast = self.get_podcast_by_slug(slug)
        if not podcast:
            return [], 0

        podcast_id = podcast['id']

        # Build query
        where_clause = "WHERE e.podcast_id = ?"
        params = [podcast_id]

        if status and status != 'all':
            where_clause += " AND e.status = ?"
            params.append(status)

        # Get total count
        cursor = conn.execute(
            f"SELECT COUNT(*) FROM episodes e {where_clause}",
            params
        )
        total = cursor.fetchone()[0]

        # Get episodes
        params.extend([limit, offset])
        cursor = conn.execute(
            f"""SELECT e.* FROM episodes e
                {where_clause}
                ORDER BY e.created_at DESC
                LIMIT ? OFFSET ?""",
            params
        )

        episodes = [dict(row) for row in cursor.fetchall()]
        return episodes, total

    def get_episode(self, slug: str, episode_id: str) -> Optional[Dict]:
        """Get episode by slug and episode_id."""
        conn = self.get_connection()
        cursor = conn.execute(
            """SELECT e.*, p.slug, ed.transcript_text, ed.transcript_vtt,
                      ed.chapters_json, ed.ad_markers_json,
                      ed.first_pass_response, ed.first_pass_prompt,
                      ed.second_pass_prompt, ed.second_pass_response
               FROM episodes e
               JOIN podcasts p ON e.podcast_id = p.id
               LEFT JOIN episode_details ed ON e.id = ed.episode_id
               WHERE p.slug = ? AND e.episode_id = ?""",
            (slug, episode_id)
        )
        row = cursor.fetchone()
        return dict(row) if row else None

    def get_episode_by_id(self, db_id: int) -> Optional[Dict]:
        """Get episode by database ID."""
        conn = self.get_connection()
        cursor = conn.execute(
            """SELECT e.*, p.slug FROM episodes e
               JOIN podcasts p ON e.podcast_id = p.id
               WHERE e.id = ?""",
            (db_id,)
        )
        row = cursor.fetchone()
        return dict(row) if row else None

    def get_episode_by_title_and_date(self, slug: str, title: str, published_at: str) -> Optional[Dict]:
        """Get episode by title and publish date (for deduplication).

        This catches cases where the same episode has different IDs due to
        changing RSS GUIDs or dynamic URL parameters.

        Args:
            slug: Podcast slug
            title: Episode title (exact match)
            published_at: Publish date in ISO format

        Returns:
            Episode dict if found, None otherwise
        """
        if not title or not published_at:
            return None

        conn = self.get_connection()
        podcast = self.get_podcast_by_slug(slug)
        if not podcast:
            return None

        cursor = conn.execute(
            """SELECT e.*, p.slug FROM episodes e
               JOIN podcasts p ON e.podcast_id = p.id
               WHERE p.slug = ? AND e.title = ? AND e.published_at = ?""",
            (slug, title, published_at)
        )
        row = cursor.fetchone()
        return dict(row) if row else None

    def upsert_episode(self, slug: str, episode_id: str, **kwargs) -> int:
        """Insert or update an episode. Returns episode database ID."""
        conn = self.get_connection()

        # Get podcast ID
        podcast = self.get_podcast_by_slug(slug)
        if not podcast:
            raise ValueError(f"Podcast not found: {slug}")

        podcast_id = podcast['id']

        # Check if episode exists
        cursor = conn.execute(
            "SELECT id FROM episodes WHERE podcast_id = ? AND episode_id = ?",
            (podcast_id, episode_id)
        )
        row = cursor.fetchone()

        if row:
            # Update existing episode
            db_id = row['id']
            if kwargs:
                fields = []
                values = []
                for key, value in kwargs.items():
                    if key in ('original_url', 'title', 'description', 'status', 'processed_file',
                               'processed_at', 'original_duration', 'new_duration',
                               'ads_removed', 'ads_removed_firstpass', 'ads_removed_secondpass',
                               'error_message', 'ad_detection_status', 'artwork_url',
                               'reprocess_mode', 'reprocess_requested_at', 'retry_count', 'published_at'):
                        fields.append(f"{key} = ?")
                        values.append(value)

                if fields:
                    fields.append("updated_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now')")
                    values.append(db_id)
                    conn.execute(
                        f"UPDATE episodes SET {', '.join(fields)} WHERE id = ?",
                        values
                    )
                    conn.commit()
        else:
            # Insert new episode
            cursor = conn.execute(
                """INSERT INTO episodes
                   (podcast_id, episode_id, original_url, title, description, status,
                    processed_file, processed_at, original_duration,
                    new_duration, ads_removed, ads_removed_firstpass, ads_removed_secondpass,
                    error_message, ad_detection_status, artwork_url, retry_count, published_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    podcast_id,
                    episode_id,
                    kwargs.get('original_url', ''),
                    kwargs.get('title'),
                    kwargs.get('description'),
                    kwargs.get('status', 'pending'),
                    kwargs.get('processed_file'),
                    kwargs.get('processed_at'),
                    kwargs.get('original_duration'),
                    kwargs.get('new_duration'),
                    kwargs.get('ads_removed', 0),
                    kwargs.get('ads_removed_firstpass', 0),
                    kwargs.get('ads_removed_secondpass', 0),
                    kwargs.get('error_message'),
                    kwargs.get('ad_detection_status'),
                    kwargs.get('artwork_url'),
                    kwargs.get('retry_count', 0),
                    kwargs.get('published_at')
                )
            )
            db_id = cursor.lastrowid
            conn.commit()

        return db_id

    def save_episode_details(self, slug: str, episode_id: str,
                            transcript_text: str = None,
                            transcript_vtt: str = None,
                            chapters_json: str = None,
                            ad_markers: List[Dict] = None,
                            first_pass_response: str = None,
                            first_pass_prompt: str = None,
                            second_pass_prompt: str = None,
                            second_pass_response: str = None):
        """Save or update episode details (transcript, VTT, chapters, ad markers, pass data)."""
        conn = self.get_connection()

        # Get episode database ID
        episode = self.get_episode(slug, episode_id)
        if not episode:
            raise ValueError(f"Episode not found: {slug}/{episode_id}")

        db_episode_id = episode['id']

        # Check if details exist
        cursor = conn.execute(
            "SELECT id FROM episode_details WHERE episode_id = ?",
            (db_episode_id,)
        )
        row = cursor.fetchone()

        ad_markers_json_str = json.dumps(ad_markers) if ad_markers is not None else None

        if row:
            # Update existing
            updates = []
            values = []
            if transcript_text is not None:
                updates.append("transcript_text = ?")
                values.append(transcript_text)
            if transcript_vtt is not None:
                updates.append("transcript_vtt = ?")
                values.append(transcript_vtt)
            if chapters_json is not None:
                updates.append("chapters_json = ?")
                values.append(chapters_json)
            if ad_markers_json_str is not None:
                updates.append("ad_markers_json = ?")
                values.append(ad_markers_json_str)
            if first_pass_response is not None:
                updates.append("first_pass_response = ?")
                values.append(first_pass_response)
            if first_pass_prompt is not None:
                updates.append("first_pass_prompt = ?")
                values.append(first_pass_prompt)
            if second_pass_prompt is not None:
                updates.append("second_pass_prompt = ?")
                values.append(second_pass_prompt)
            if second_pass_response is not None:
                updates.append("second_pass_response = ?")
                values.append(second_pass_response)

            if updates:
                values.append(row['id'])
                conn.execute(
                    f"UPDATE episode_details SET {', '.join(updates)} WHERE id = ?",
                    values
                )
        else:
            # Insert new
            conn.execute(
                """INSERT INTO episode_details
                   (episode_id, transcript_text, transcript_vtt, chapters_json,
                    ad_markers_json, first_pass_response, first_pass_prompt,
                    second_pass_prompt, second_pass_response)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (db_episode_id, transcript_text, transcript_vtt, chapters_json,
                 ad_markers_json_str, first_pass_response, first_pass_prompt,
                 second_pass_prompt, second_pass_response)
            )

        conn.commit()

    def save_episode_audio_analysis(self, slug: str, episode_id: str, audio_analysis_json: str):
        """Save audio analysis results for an episode."""
        conn = self.get_connection()

        # Get episode database ID
        episode = self.get_episode(slug, episode_id)
        if not episode:
            logger.warning(f"Episode not found for audio analysis: {slug}/{episode_id}")
            return

        db_episode_id = episode['id']

        # Check if details exist
        cursor = conn.execute(
            "SELECT id FROM episode_details WHERE episode_id = ?",
            (db_episode_id,)
        )
        row = cursor.fetchone()

        if row:
            # Update existing
            conn.execute(
                "UPDATE episode_details SET audio_analysis_json = ? WHERE id = ?",
                (audio_analysis_json, row['id'])
            )
        else:
            # Insert new
            conn.execute(
                """INSERT INTO episode_details (episode_id, audio_analysis_json)
                   VALUES (?, ?)""",
                (db_episode_id, audio_analysis_json)
            )

        conn.commit()
        logger.debug(f"[{slug}:{episode_id}] Saved audio analysis to database")

    def clear_episode_details(self, slug: str, episode_id: str):
        """Clear transcript and ad markers for an episode."""
        conn = self.get_connection()

        # Get episode database ID
        episode = self.get_episode(slug, episode_id)
        if not episode:
            return

        db_episode_id = episode['id']

        conn.execute(
            "DELETE FROM episode_details WHERE episode_id = ?",
            (db_episode_id,)
        )
        conn.commit()
        logger.debug(f"[{slug}:{episode_id}] Cleared episode details from database")

    def reset_episode_status(self, slug: str, episode_id: str):
        """Reset episode status to pending for reprocessing."""
        conn = self.get_connection()

        podcast = self.get_podcast_by_slug(slug)
        if not podcast:
            return

        conn.execute(
            """UPDATE episodes
               SET status = 'pending',
                   processed_file = NULL,
                   processed_at = NULL,
                   original_duration = NULL,
                   new_duration = NULL,
                   ads_removed = NULL,
                   error_message = NULL,
                   retry_count = 0,
                   updated_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now')
               WHERE podcast_id = ? AND episode_id = ?""",
            (podcast['id'], episode_id)
        )
        conn.commit()
        logger.debug(f"[{slug}:{episode_id}] Reset episode status to pending (retry_count reset)")

    # ========== Settings Methods ==========

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
        from ad_detector import DEFAULT_MODEL

        defaults = {
            'system_prompt': DEFAULT_SYSTEM_PROMPT,
            'verification_prompt': DEFAULT_VERIFICATION_PROMPT,
            'retention_period_minutes': os.environ.get('RETENTION_PERIOD', '1440'),
            'claude_model': DEFAULT_MODEL,
            'verification_model': DEFAULT_MODEL,
            'whisper_model': os.environ.get('WHISPER_MODEL', 'small'),
            'vtt_transcripts_enabled': 'true',
            'chapters_enabled': 'true'
        }

        if key in defaults:
            self.set_setting(key, defaults[key], is_default=True)
            return True
        return False

    # ========== Cleanup Methods ==========

    def cleanup_old_episodes(self, force_all: bool = False) -> Tuple[int, float]:
        """
        Delete episodes older than retention period, or all episodes if force_all=True.
        Returns (count deleted, MB freed estimate).
        """
        conn = self.get_connection()

        if force_all:
            # Delete ALL episodes immediately
            cursor = conn.execute(
                """SELECT e.id, e.episode_id, e.processed_file, p.slug
                   FROM episodes e
                   JOIN podcasts p ON e.podcast_id = p.id"""
            )
        else:
            # Get retention period - env var takes precedence over database setting
            retention_minutes = int(os.environ.get('RETENTION_PERIOD') or
                                   self.get_setting('retention_period_minutes') or '1440')

            if retention_minutes <= 0:
                return 0, 0.0

            cutoff = datetime.now(timezone.utc) - timedelta(minutes=retention_minutes)
            cutoff_str = cutoff.strftime('%Y-%m-%dT%H:%M:%SZ')

            # Get episodes to delete
            cursor = conn.execute(
                """SELECT e.id, e.episode_id, e.processed_file, p.slug
                   FROM episodes e
                   JOIN podcasts p ON e.podcast_id = p.id
                   WHERE e.created_at < ?""",
                (cutoff_str,)
            )

        episodes_to_delete = cursor.fetchall()
        deleted_count = 0
        freed_bytes = 0

        for row in episodes_to_delete:
            slug = row['slug']
            episode_id = row['episode_id']

            # Delete files
            podcast_dir = self.data_dir / "podcasts" / slug / "episodes"

            # Only delete audio file - transcript/ads/prompt stored in database
            # Database cascade delete handles episode_details table
            file_path = podcast_dir / f"{episode_id}.mp3"
            if file_path.exists():
                try:
                    freed_bytes += file_path.stat().st_size
                    file_path.unlink()
                except Exception as e:
                    logger.warning(f"Failed to delete {file_path}: {e}")

            deleted_count += 1

        # Delete from database (cascade deletes episode_details)
        if force_all:
            conn.execute("DELETE FROM episodes")
        else:
            conn.execute(
                "DELETE FROM episodes WHERE created_at < ?",
                (cutoff_str,)
            )
        conn.commit()

        freed_mb = freed_bytes / (1024 * 1024)

        if deleted_count > 0:
            logger.info(f"Cleaned up {deleted_count} old episodes, freed {freed_mb:.1f} MB")

        return deleted_count, freed_mb

    # ========== Stats Methods ==========

    def get_stats(self) -> Dict:
        """Get database statistics."""
        conn = self.get_connection()

        # Podcast count
        cursor = conn.execute("SELECT COUNT(*) FROM podcasts")
        podcast_count = cursor.fetchone()[0]

        # Episode counts by status
        cursor = conn.execute("""
            SELECT status, COUNT(*) as count
            FROM episodes
            GROUP BY status
        """)
        status_counts = {row['status']: row['count'] for row in cursor}

        # Total episodes
        total_episodes = sum(status_counts.values())

        # Storage estimate (processed files)
        total_size = 0
        for podcast_dir in self.data_dir.iterdir():
            if podcast_dir.is_dir():
                episodes_dir = podcast_dir / "episodes"
                if episodes_dir.exists():
                    for f in episodes_dir.glob("*.mp3"):
                        total_size += f.stat().st_size

        return {
            'podcast_count': podcast_count,
            'episode_count': total_episodes,
            'episodes_by_status': status_counts,
            'storage_mb': total_size / (1024 * 1024)
        }

    def get_feeds_config(self) -> List[Dict]:
        """Get feed configuration in feeds.json format for compatibility."""
        conn = self.get_connection()
        cursor = conn.execute(
            "SELECT slug, source_url FROM podcasts WHERE source_url != ''"
        )
        return [
            {'in': row['source_url'], 'out': f"/{row['slug']}"}
            for row in cursor
        ]

    # ========== Cumulative Stats Methods ==========

    def increment_total_time_saved(self, seconds: float):
        """Add to the cumulative total time saved. Called when episode processing completes."""
        if seconds <= 0:
            return

        conn = self.get_connection()
        conn.execute(
            """INSERT INTO stats (key, value, updated_at)
               VALUES ('total_time_saved', ?, strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
               ON CONFLICT(key) DO UPDATE SET
                 value = value + excluded.value,
                 updated_at = excluded.updated_at""",
            (seconds,)
        )
        conn.commit()
        logger.debug(f"Incremented total time saved by {seconds:.1f} seconds")

    def get_total_time_saved(self) -> float:
        """Get the cumulative total time saved across all processed episodes."""
        conn = self.get_connection()
        cursor = conn.execute(
            "SELECT value FROM stats WHERE key = 'total_time_saved'"
        )
        row = cursor.fetchone()
        return row['value'] if row else 0.0

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

    # ========== Ad Patterns Methods ==========

    def get_ad_patterns(self, scope: str = None, podcast_id: str = None,
                        network_id: str = None, active_only: bool = True) -> List[Dict]:
        """Get ad patterns with optional filtering. Includes podcast_name when available."""
        conn = self.get_connection()

        # Join with podcasts to get podcast name (podcast_id stores slugs since v0.1.194)
        query = """
            SELECT ap.*, p.title as podcast_name, p.slug as podcast_slug
            FROM ad_patterns ap
            LEFT JOIN podcasts p ON ap.podcast_id = p.slug
            WHERE 1=1
        """
        params = []

        if active_only:
            query += " AND ap.is_active = 1"
        if scope:
            query += " AND ap.scope = ?"
            params.append(scope)
        if podcast_id:
            query += " AND ap.podcast_id = ?"
            params.append(podcast_id)
        if network_id:
            query += " AND ap.network_id = ?"
            params.append(network_id)

        query += " ORDER BY ap.created_at DESC"

        cursor = conn.execute(query, params)
        return [dict(row) for row in cursor.fetchall()]

    def get_ad_pattern_by_id(self, pattern_id: int) -> Optional[Dict]:
        """Get a single ad pattern by ID with podcast info."""
        conn = self.get_connection()
        cursor = conn.execute(
            """SELECT ap.*, p.title as podcast_name, p.slug as podcast_slug
               FROM ad_patterns ap
               LEFT JOIN podcasts p ON ap.podcast_id = p.slug
               WHERE ap.id = ?""",
            (pattern_id,)
        )
        row = cursor.fetchone()
        return dict(row) if row else None

    def find_pattern_by_text(self, text_template: str, podcast_id: str = None) -> Optional[Dict]:
        """Find an existing pattern with the same text_template (for deduplication)."""
        conn = self.get_connection()
        if podcast_id:
            cursor = conn.execute(
                "SELECT * FROM ad_patterns WHERE text_template = ? AND podcast_id = ?",
                (text_template, podcast_id)
            )
        else:
            cursor = conn.execute(
                "SELECT * FROM ad_patterns WHERE text_template = ? AND podcast_id IS NULL",
                (text_template,)
            )
        row = cursor.fetchone()
        return dict(row) if row else None

    def create_ad_pattern(self, scope: str, text_template: str = None,
                          sponsor: str = None, podcast_id: str = None,
                          network_id: str = None, dai_platform: str = None,
                          intro_variants: List[str] = None,
                          outro_variants: List[str] = None,
                          created_from_episode_id: str = None) -> int:
        """Create a new ad pattern. Returns pattern ID."""
        conn = self.get_connection()
        cursor = conn.execute(
            """INSERT INTO ad_patterns
               (scope, text_template, sponsor, podcast_id, network_id, dai_platform,
                intro_variants, outro_variants, created_from_episode_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (scope, text_template, sponsor, podcast_id, network_id, dai_platform,
             json.dumps(intro_variants or []), json.dumps(outro_variants or []),
             created_from_episode_id)
        )
        conn.commit()
        return cursor.lastrowid

    def update_ad_pattern(self, pattern_id: int, **kwargs) -> bool:
        """Update an ad pattern."""
        conn = self.get_connection()

        fields = []
        values = []
        for key, value in kwargs.items():
            if key in ('scope', 'text_template', 'sponsor', 'podcast_id', 'network_id',
                       'dai_platform', 'confirmation_count', 'false_positive_count',
                       'last_matched_at', 'is_active', 'disabled_at', 'disabled_reason'):
                fields.append(f"{key} = ?")
                values.append(value)
            elif key in ('intro_variants', 'outro_variants'):
                fields.append(f"{key} = ?")
                values.append(json.dumps(value) if isinstance(value, list) else value)

        if not fields:
            return False

        values.append(pattern_id)
        conn.execute(
            f"UPDATE ad_patterns SET {', '.join(fields)} WHERE id = ?",
            values
        )
        conn.commit()
        return True

    def increment_pattern_match(self, pattern_id: int):
        """Increment pattern confirmation count and update last_matched_at."""
        conn = self.get_connection()
        conn.execute(
            """UPDATE ad_patterns SET
               confirmation_count = confirmation_count + 1,
               last_matched_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now')
               WHERE id = ?""",
            (pattern_id,)
        )
        conn.commit()

    def increment_pattern_false_positive(self, pattern_id: int):
        """Increment pattern false positive count."""
        conn = self.get_connection()
        conn.execute(
            "UPDATE ad_patterns SET false_positive_count = false_positive_count + 1 WHERE id = ?",
            (pattern_id,)
        )
        conn.commit()

    def delete_ad_pattern(self, pattern_id: int) -> bool:
        """Delete an ad pattern. Returns True if deleted."""
        conn = self.get_connection()
        cursor = conn.execute(
            "DELETE FROM ad_patterns WHERE id = ?", (pattern_id,)
        )
        conn.commit()
        return cursor.rowcount > 0

    def delete_old_episodes(self, cutoff_date: str) -> int:
        """Delete episodes older than cutoff date. Returns count deleted."""
        conn = self.get_connection()
        cursor = conn.execute(
            "DELETE FROM episodes WHERE created_at < ?", (cutoff_date,)
        )
        conn.commit()
        return cursor.rowcount

    def cleanup_duplicate_episodes(self, slug: str) -> int:
        """
        Remove duplicate episodes from a feed, keeping only the latest version.

        Duplicates are identified by matching title (case-insensitive) and
        created_at date. When duplicates exist, keeps the one with the most
        recent created_at timestamp.

        Args:
            slug: The podcast feed slug

        Returns:
            Number of duplicate episodes removed
        """
        podcast_id = self.get_podcast_id(slug)
        if not podcast_id:
            return 0

        conn = self.get_connection()

        # Find duplicate groups by title + date
        cursor = conn.execute("""
            SELECT LOWER(TRIM(title)) as norm_title,
                   DATE(created_at) as created_date,
                   GROUP_CONCAT(episode_id) as episode_ids,
                   COUNT(*) as cnt
            FROM episodes
            WHERE podcast_id = ?
            GROUP BY norm_title, created_date
            HAVING cnt > 1
        """, (podcast_id,))

        duplicates = cursor.fetchall()
        removed = 0

        for row in duplicates:
            episode_ids = row['episode_ids'].split(',')

            # Get full details to find the latest one
            placeholders = ','.join(['?'] * len(episode_ids))
            detail_cursor = conn.execute(f"""
                SELECT episode_id, created_at
                FROM episodes
                WHERE podcast_id = ? AND episode_id IN ({placeholders})
                ORDER BY created_at DESC
            """, [podcast_id] + episode_ids)

            details = detail_cursor.fetchall()

            # Keep the first (most recent), delete the rest
            for old_ep in details[1:]:
                old_id = old_ep['episode_id']
                conn.execute(
                    "DELETE FROM episodes WHERE podcast_id = ? AND episode_id = ?",
                    (podcast_id, old_id)
                )
                removed += 1
                logger.info(f"Removed duplicate episode {old_id} from {slug}")

        if removed > 0:
            conn.commit()
            logger.info(f"Cleaned up {removed} duplicate episodes from {slug}")

        return removed

    # ========== Pattern Corrections Methods ==========

    def create_pattern_correction(self, correction_type: str, pattern_id: int = None,
                                   episode_id: str = None, podcast_title: str = None,
                                   episode_title: str = None, original_bounds: Dict = None,
                                   corrected_bounds: Dict = None, text_snippet: str = None) -> int:
        """Create a pattern correction record. Returns correction ID."""
        conn = self.get_connection()
        cursor = conn.execute(
            """INSERT INTO pattern_corrections
               (pattern_id, episode_id, podcast_title, episode_title, correction_type,
                original_bounds, corrected_bounds, text_snippet)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (pattern_id, episode_id, podcast_title, episode_title, correction_type,
             json.dumps(original_bounds) if original_bounds else None,
             json.dumps(corrected_bounds) if corrected_bounds else None,
             text_snippet)
        )
        conn.commit()
        return cursor.lastrowid

    def delete_conflicting_corrections(self, episode_id: str, correction_type: str,
                                        bounds_start: float, bounds_end: float) -> int:
        """Delete corrections that conflict with a new correction being submitted.

        When user confirms an ad, delete false_positive corrections for same bounds.
        When user rejects an ad, delete confirm corrections for same bounds.

        Returns number of deleted rows.
        """
        # Determine the conflicting type
        if correction_type == 'confirm':
            conflicting_type = 'false_positive'
        elif correction_type == 'false_positive':
            conflicting_type = 'confirm'
        else:
            return 0  # adjust doesn't conflict with either

        conn = self.get_connection()
        cursor = conn.execute(
            """SELECT id, original_bounds FROM pattern_corrections
               WHERE episode_id = ? AND correction_type = ?""",
            (episode_id, conflicting_type)
        )

        deleted = 0
        for row in cursor.fetchall():
            if row['original_bounds']:
                try:
                    parsed = json.loads(row['original_bounds'])
                    fp_start = float(parsed.get('start', 0))
                    fp_end = float(parsed.get('end', 0))
                    # Check overlap (same 50% threshold as validator)
                    overlap_start = max(bounds_start, fp_start)
                    overlap_end = min(bounds_end, fp_end)
                    overlap = max(0, overlap_end - overlap_start)
                    segment_duration = bounds_end - bounds_start
                    if segment_duration > 0 and overlap / segment_duration >= 0.5:
                        conn.execute("DELETE FROM pattern_corrections WHERE id = ?", (row['id'],))
                        deleted += 1
                except (json.JSONDecodeError, KeyError, ValueError):
                    pass

        if deleted:
            conn.commit()
        return deleted

    def get_pattern_corrections(self, pattern_id: int = None, limit: int = 100) -> List[Dict]:
        """Get pattern corrections, optionally filtered by pattern_id."""
        conn = self.get_connection()

        if pattern_id:
            cursor = conn.execute(
                """SELECT * FROM pattern_corrections
                   WHERE pattern_id = ?
                   ORDER BY created_at DESC LIMIT ?""",
                (pattern_id, limit)
            )
        else:
            cursor = conn.execute(
                "SELECT * FROM pattern_corrections ORDER BY created_at DESC LIMIT ?",
                (limit,)
            )

        return [dict(row) for row in cursor.fetchall()]

    def get_episode_corrections(self, episode_id: str) -> List[Dict]:
        """Get all corrections for a specific episode."""
        conn = self.get_connection()
        cursor = conn.execute(
            """SELECT id, correction_type, original_bounds, corrected_bounds, created_at
               FROM pattern_corrections
               WHERE episode_id = ?
               ORDER BY created_at DESC""",
            (episode_id,)
        )
        results = []
        for row in cursor.fetchall():
            item = dict(row)
            if item.get('original_bounds'):
                item['original_bounds'] = json.loads(item['original_bounds'])
            if item.get('corrected_bounds'):
                item['corrected_bounds'] = json.loads(item['corrected_bounds'])
            results.append(item)
        return results

    def get_false_positive_corrections(self, episode_id: str) -> List[Dict]:
        """Get false_positive corrections for an episode with parsed bounds.

        Returns list of dicts with 'start' and 'end' keys for easy overlap checking.
        """
        conn = self.get_connection()
        cursor = conn.execute(
            """SELECT original_bounds FROM pattern_corrections
               WHERE episode_id = ? AND correction_type = 'false_positive'""",
            (episode_id,)
        )
        results = []
        for row in cursor.fetchall():
            bounds = row['original_bounds']
            if bounds:
                try:
                    parsed = json.loads(bounds)
                    if 'start' in parsed and 'end' in parsed:
                        results.append({
                            'start': float(parsed['start']),
                            'end': float(parsed['end'])
                        })
                except (json.JSONDecodeError, KeyError, ValueError):
                    pass
        return results

    def get_confirmed_corrections(self, episode_id: str) -> List[Dict]:
        """Get confirmed corrections for an episode with parsed bounds.

        Returns list of dicts with 'start' and 'end' keys for easy overlap checking.
        """
        conn = self.get_connection()
        cursor = conn.execute(
            """SELECT original_bounds FROM pattern_corrections
               WHERE episode_id = ? AND correction_type = 'confirm'""",
            (episode_id,)
        )
        results = []
        for row in cursor.fetchall():
            bounds = row['original_bounds']
            if bounds:
                try:
                    parsed = json.loads(bounds)
                    if 'start' in parsed and 'end' in parsed:
                        results.append({
                            'start': float(parsed['start']),
                            'end': float(parsed['end'])
                        })
                except (json.JSONDecodeError, KeyError, ValueError):
                    pass
        return results

    def get_podcast_false_positive_texts(self, podcast_slug: str, limit: int = 100) -> List[Dict]:
        """Get all false positive texts for a podcast for cross-episode matching.

        Returns list of dicts with:
        - text: The rejected segment text
        - episode_id: Which episode it came from
        - start, end: Original time bounds
        """
        conn = self.get_connection()
        cursor = conn.execute('''
            SELECT pc.text_snippet, pc.episode_id, pc.original_bounds, pc.created_at
            FROM pattern_corrections pc
            JOIN episodes e ON pc.episode_id = e.episode_id
            JOIN podcasts p ON e.podcast_id = p.id
            WHERE p.slug = ?
            AND pc.correction_type = 'false_positive'
            AND pc.text_snippet IS NOT NULL
            AND length(pc.text_snippet) >= 50
            ORDER BY pc.created_at DESC
            LIMIT ?
        ''', (podcast_slug, limit))

        results = []
        for row in cursor.fetchall():
            bounds = {}
            if row['original_bounds']:
                try:
                    bounds = json.loads(row['original_bounds'])
                except (json.JSONDecodeError, ValueError):
                    pass
            results.append({
                'text': row['text_snippet'],
                'episode_id': row['episode_id'],
                'start': bounds.get('start'),
                'end': bounds.get('end'),
                'created_at': row['created_at']
            })
        return results

    # ========== Audio Fingerprints Methods ==========

    def get_audio_fingerprint(self, pattern_id: int) -> Optional[Dict]:
        """Get audio fingerprint for a pattern."""
        conn = self.get_connection()
        cursor = conn.execute(
            "SELECT * FROM audio_fingerprints WHERE pattern_id = ?", (pattern_id,)
        )
        row = cursor.fetchone()
        return dict(row) if row else None

    def get_all_audio_fingerprints(self) -> List[Dict]:
        """Get all audio fingerprints."""
        conn = self.get_connection()
        cursor = conn.execute("SELECT * FROM audio_fingerprints")
        return [dict(row) for row in cursor.fetchall()]

    def create_audio_fingerprint(self, pattern_id: int, fingerprint: bytes,
                                  duration: float) -> int:
        """Create an audio fingerprint. Returns fingerprint ID."""
        conn = self.get_connection()
        cursor = conn.execute(
            """INSERT INTO audio_fingerprints (pattern_id, fingerprint, duration)
               VALUES (?, ?, ?)
               ON CONFLICT(pattern_id) DO UPDATE SET
                 fingerprint = excluded.fingerprint,
                 duration = excluded.duration""",
            (pattern_id, fingerprint, duration)
        )
        conn.commit()
        return cursor.lastrowid

    def delete_audio_fingerprint(self, pattern_id: int) -> bool:
        """Delete an audio fingerprint."""
        conn = self.get_connection()
        cursor = conn.execute(
            "DELETE FROM audio_fingerprints WHERE pattern_id = ?", (pattern_id,)
        )
        conn.commit()
        return cursor.rowcount > 0

    # ========== Known Sponsors Methods ==========

    def get_known_sponsors(self, active_only: bool = True) -> List[Dict]:
        """Get all known sponsors."""
        conn = self.get_connection()
        query = "SELECT * FROM known_sponsors"
        if active_only:
            query += " WHERE is_active = 1"
        query += " ORDER BY name"
        cursor = conn.execute(query)
        return [dict(row) for row in cursor.fetchall()]

    def get_known_sponsor_by_id(self, sponsor_id: int) -> Optional[Dict]:
        """Get a single sponsor by ID."""
        conn = self.get_connection()
        cursor = conn.execute(
            "SELECT * FROM known_sponsors WHERE id = ?", (sponsor_id,)
        )
        row = cursor.fetchone()
        return dict(row) if row else None

    def get_known_sponsor_by_name(self, name: str) -> Optional[Dict]:
        """Get a sponsor by name."""
        conn = self.get_connection()
        cursor = conn.execute(
            "SELECT * FROM known_sponsors WHERE LOWER(name) = LOWER(?)", (name,)
        )
        row = cursor.fetchone()
        return dict(row) if row else None

    def create_known_sponsor(self, name: str, aliases: List[str] = None,
                              category: str = None, common_ctas: List[str] = None) -> int:
        """Create a known sponsor. Returns sponsor ID."""
        conn = self.get_connection()
        cursor = conn.execute(
            """INSERT INTO known_sponsors (name, aliases, category, common_ctas)
               VALUES (?, ?, ?, ?)""",
            (name, json.dumps(aliases or []), category, json.dumps(common_ctas or []))
        )
        conn.commit()
        return cursor.lastrowid

    def update_known_sponsor(self, sponsor_id: int, **kwargs) -> bool:
        """Update a known sponsor."""
        conn = self.get_connection()

        fields = []
        values = []
        for key, value in kwargs.items():
            if key in ('name', 'category', 'is_active'):
                fields.append(f"{key} = ?")
                values.append(value)
            elif key in ('aliases', 'common_ctas'):
                fields.append(f"{key} = ?")
                values.append(json.dumps(value) if isinstance(value, list) else value)

        if not fields:
            return False

        values.append(sponsor_id)
        conn.execute(
            f"UPDATE known_sponsors SET {', '.join(fields)} WHERE id = ?",
            values
        )
        conn.commit()
        return True

    def delete_known_sponsor(self, sponsor_id: int) -> bool:
        """Delete a known sponsor (or set inactive)."""
        conn = self.get_connection()
        cursor = conn.execute(
            "UPDATE known_sponsors SET is_active = 0 WHERE id = ?", (sponsor_id,)
        )
        conn.commit()
        return cursor.rowcount > 0

    # ========== Sponsor Normalizations Methods ==========

    def get_sponsor_normalizations(self, category: str = None,
                                    active_only: bool = True) -> List[Dict]:
        """Get sponsor normalizations."""
        conn = self.get_connection()

        query = "SELECT * FROM sponsor_normalizations WHERE 1=1"
        params = []

        if active_only:
            query += " AND is_active = 1"
        if category:
            query += " AND category = ?"
            params.append(category)

        query += " ORDER BY category, pattern"

        cursor = conn.execute(query, params)
        return [dict(row) for row in cursor.fetchall()]

    def create_sponsor_normalization(self, pattern: str, replacement: str,
                                      category: str) -> int:
        """Create a sponsor normalization. Returns normalization ID."""
        conn = self.get_connection()
        cursor = conn.execute(
            """INSERT INTO sponsor_normalizations (pattern, replacement, category)
               VALUES (?, ?, ?)""",
            (pattern, replacement, category)
        )
        conn.commit()
        return cursor.lastrowid

    def update_sponsor_normalization(self, norm_id: int, **kwargs) -> bool:
        """Update a sponsor normalization."""
        conn = self.get_connection()

        fields = []
        values = []
        for key, value in kwargs.items():
            if key in ('pattern', 'replacement', 'category', 'is_active'):
                fields.append(f"{key} = ?")
                values.append(value)

        if not fields:
            return False

        values.append(norm_id)
        conn.execute(
            f"UPDATE sponsor_normalizations SET {', '.join(fields)} WHERE id = ?",
            values
        )
        conn.commit()
        return True

    def delete_sponsor_normalization(self, norm_id: int) -> bool:
        """Delete a sponsor normalization (or set inactive)."""
        conn = self.get_connection()
        cursor = conn.execute(
            "UPDATE sponsor_normalizations SET is_active = 0 WHERE id = ?", (norm_id,)
        )
        conn.commit()
        return cursor.rowcount > 0

    # ========== Processing History Methods ==========

    def record_processing_history(self, podcast_id: int, podcast_slug: str,
                                   podcast_title: str, episode_id: str,
                                   episode_title: str, status: str,
                                   processing_duration_seconds: float = None,
                                   ads_detected: int = 0,
                                   error_message: str = None) -> int:
        """Record a processing attempt in history. Returns history entry ID."""
        conn = self.get_connection()

        # Calculate reprocess number (count existing entries + 1)
        cursor = conn.execute(
            """SELECT COUNT(*) FROM processing_history
               WHERE podcast_id = ? AND episode_id = ?""",
            (podcast_id, episode_id)
        )
        existing_count = cursor.fetchone()[0]
        reprocess_number = existing_count + 1

        cursor = conn.execute(
            """INSERT INTO processing_history
               (podcast_id, podcast_slug, podcast_title, episode_id, episode_title,
                processed_at, processing_duration_seconds, status, ads_detected,
                error_message, reprocess_number)
               VALUES (?, ?, ?, ?, ?, strftime('%Y-%m-%dT%H:%M:%SZ', 'now'), ?, ?, ?, ?, ?)""",
            (podcast_id, podcast_slug, podcast_title, episode_id, episode_title,
             processing_duration_seconds, status, ads_detected, error_message,
             reprocess_number)
        )
        conn.commit()
        logger.info(f"Recorded processing history: {podcast_slug}/{episode_id} - {status} (reprocess #{reprocess_number})")
        return cursor.lastrowid

    def backfill_processing_history(self) -> int:
        """Migrate existing processed episodes to processing_history table.
        Only backfills episodes that don't already have history entries.
        Returns count of records created."""
        conn = self.get_connection()

        # Only backfill episodes not already in history
        # Note: processed_at is often NULL in older records, so use updated_at as fallback
        cursor = conn.execute('''
            INSERT INTO processing_history
                (podcast_id, podcast_slug, podcast_title, episode_id, episode_title,
                 processed_at, processing_duration_seconds, status, ads_detected,
                 error_message, reprocess_number)
            SELECT
                e.podcast_id,
                p.slug,
                p.title,
                e.episode_id,
                e.title,
                COALESCE(e.processed_at, e.updated_at),
                NULL,
                CASE
                    WHEN e.status = 'failed' THEN 'failed'
                    ELSE 'completed'
                END,
                COALESCE(e.ads_removed, 0),
                e.error_message,
                1
            FROM episodes e
            JOIN podcasts p ON e.podcast_id = p.id
            WHERE e.status IN ('processed', 'failed')
              AND NOT EXISTS (
                  SELECT 1 FROM processing_history h
                  WHERE h.podcast_id = e.podcast_id
                    AND h.episode_id = e.episode_id
              )
        ''')

        count = cursor.rowcount
        conn.commit()
        if count > 0:
            logger.info(f"Backfilled {count} records to processing_history")
        return count

    def backfill_patterns_from_corrections(self) -> int:
        """Create patterns from existing 'confirm' corrections that have no pattern_id.

        This retroactively learns from user confirmations that were submitted
        before the pattern learning feature existed.
        Returns count of patterns created.

        Uses utils.time.parse_timestamp and utils.text.extract_text_in_range.
        """
        conn = self.get_connection()
        created_count = 0

        # Find all 'confirm' corrections without a pattern_id
        cursor = conn.execute('''
            SELECT pc.id, pc.episode_id, pc.original_bounds, pc.podcast_title
            FROM pattern_corrections pc
            WHERE pc.correction_type = 'confirm'
              AND pc.pattern_id IS NULL
        ''')
        corrections = cursor.fetchall()

        for correction in corrections:
            correction_id = correction['id']
            episode_id = correction['episode_id']
            original_bounds = correction['original_bounds']

            if not episode_id or not original_bounds:
                continue

            try:
                bounds = json.loads(original_bounds)
                start = bounds.get('start')
                end = bounds.get('end')
                if start is None or end is None:
                    continue

                # Get episode with transcript - need to find by episode_id
                # episode_id in corrections is the episode GUID, not slug
                cursor2 = conn.execute('''
                    SELECT e.*, p.id as podcast_db_id, p.slug, ed.transcript_text
                    FROM episodes e
                    JOIN podcasts p ON e.podcast_id = p.id
                    LEFT JOIN episode_details ed ON e.id = ed.episode_id
                    WHERE e.episode_id = ?
                ''', (episode_id,))
                episode = cursor2.fetchone()

                if not episode:
                    continue

                transcript = episode['transcript_text'] or ''
                podcast_id = episode['podcast_db_id']

                # Extract ad text from transcript
                ad_text = extract_text_in_range(transcript, start, end)

                if ad_text and len(ad_text) >= 50:
                    # Check for existing pattern with same text (deduplication)
                    existing = conn.execute(
                        '''SELECT id FROM ad_patterns
                           WHERE text_template = ? AND podcast_id = ?''',
                        (ad_text, str(podcast_id))
                    ).fetchone()

                    if existing:
                        # Link correction to existing pattern instead of creating duplicate
                        conn.execute(
                            'UPDATE pattern_corrections SET pattern_id = ? WHERE id = ?',
                            (existing['id'], correction_id)
                        )
                        logger.info(f"Linked correction {correction_id} to existing pattern {existing['id']}")
                    else:
                        # Create new pattern
                        cursor3 = conn.execute(
                            '''INSERT INTO ad_patterns
                               (scope, text_template, podcast_id, intro_variants, outro_variants,
                                created_from_episode_id)
                               VALUES (?, ?, ?, ?, ?, ?)''',
                            ('podcast', ad_text, str(podcast_id),
                             json.dumps([ad_text[:200]] if len(ad_text) > 200 else [ad_text]),
                             json.dumps([ad_text[-150:]] if len(ad_text) > 150 else []),
                             episode_id)
                        )
                        new_pattern_id = cursor3.lastrowid

                        # Update correction to link to new pattern
                        conn.execute(
                            'UPDATE pattern_corrections SET pattern_id = ? WHERE id = ?',
                            (new_pattern_id, correction_id)
                        )
                        created_count += 1
                        logger.info(f"Created pattern {new_pattern_id} from correction {correction_id}")

            except (json.JSONDecodeError, KeyError, TypeError) as e:
                logger.warning(f"Failed to process correction {correction_id}: {e}")
                continue

        conn.commit()
        if created_count > 0:
            logger.info(f"Backfilled {created_count} patterns from corrections")
        return created_count

    def deduplicate_patterns(self) -> int:
        """Remove duplicate patterns, merging stats into the pattern with most confirmations.

        Duplicates are patterns with the same text_template and podcast_id,
        regardless of sponsor (sponsor variations are merged together).

        Returns count of duplicates removed."""
        conn = self.get_connection()

        # Find duplicates - patterns with same text_template and podcast_id
        # This includes patterns with same text but different sponsors
        cursor = conn.execute('''
            SELECT text_template, podcast_id, GROUP_CONCAT(id) as all_ids
            FROM ad_patterns
            WHERE text_template IS NOT NULL
            GROUP BY text_template, podcast_id
            HAVING COUNT(*) > 1
        ''')
        duplicates = cursor.fetchall()

        removed_count = 0
        for dup in duplicates:
            all_ids = [int(x) for x in dup['all_ids'].split(',')]

            # Find the pattern with most confirmations to keep
            patterns_cursor = conn.execute(
                f'''SELECT id, sponsor, confirmation_count, false_positive_count
                    FROM ad_patterns
                    WHERE id IN ({','.join('?' * len(all_ids))})
                    ORDER BY confirmation_count DESC, id ASC''',
                all_ids
            )
            patterns = patterns_cursor.fetchall()

            if len(patterns) < 2:
                continue

            # Keep the pattern with most confirmations (first one after sort)
            keep_pattern = patterns[0]
            keep_id = keep_pattern['id']
            remove_ids = [p['id'] for p in patterns[1:]]

            # Sum up all confirmation and false positive counts
            total_confirmations = sum(p['confirmation_count'] for p in patterns)
            total_false_positives = sum(p['false_positive_count'] for p in patterns)

            # If the keeper has no sponsor, try to use one from duplicates
            final_sponsor = keep_pattern['sponsor']
            if not final_sponsor:
                for p in patterns[1:]:
                    if p['sponsor']:
                        final_sponsor = p['sponsor']
                        break

            # Update the kept pattern with merged stats
            conn.execute(
                '''UPDATE ad_patterns
                   SET confirmation_count = ?, false_positive_count = ?, sponsor = ?
                   WHERE id = ?''',
                [total_confirmations, total_false_positives, final_sponsor, keep_id]
            )

            # Update corrections to point to the kept pattern
            placeholders = ','.join('?' * len(remove_ids))
            conn.execute(
                f'''UPDATE pattern_corrections
                    SET pattern_id = ?
                    WHERE pattern_id IN ({placeholders})''',
                [keep_id] + remove_ids
            )

            # Delete duplicate patterns
            conn.execute(
                f'''DELETE FROM ad_patterns WHERE id IN ({placeholders})''',
                remove_ids
            )
            removed_count += len(remove_ids)
            logger.info(f"Merged {len(remove_ids)} duplicate patterns into pattern {keep_id} "
                       f"(confirmations: {total_confirmations}, fps: {total_false_positives})")

        conn.commit()
        if removed_count > 0:
            logger.info(f"Deduplicated {removed_count} patterns total")
        return removed_count

    def extract_sponsors_for_patterns(self) -> int:
        """Extract sponsor names for patterns that have text_template but no sponsor.

        Returns count of patterns updated."""
        from sponsor_service import SponsorService

        conn = self.get_connection()
        updated_count = 0

        # Find patterns without sponsors
        cursor = conn.execute('''
            SELECT id, text_template FROM ad_patterns
            WHERE sponsor IS NULL AND text_template IS NOT NULL
        ''')
        patterns = cursor.fetchall()

        for pattern in patterns:
            sponsor = SponsorService.extract_sponsor_from_text(pattern['text_template'])
            if sponsor:
                conn.execute(
                    'UPDATE ad_patterns SET sponsor = ? WHERE id = ?',
                    (sponsor, pattern['id'])
                )
                updated_count += 1
                logger.info(f"Extracted sponsor '{sponsor}' for pattern {pattern['id']}")

        conn.commit()
        if updated_count > 0:
            logger.info(f"Extracted sponsors for {updated_count} patterns")
        return updated_count

    def get_processing_history(self, limit: int = 50, offset: int = 0,
                                status_filter: str = None,
                                podcast_slug: str = None,
                                sort_by: str = 'processed_at',
                                sort_dir: str = 'desc') -> Tuple[List[Dict], int]:
        """Get processing history with pagination. Returns (entries, total_count)."""
        conn = self.get_connection()

        # Build WHERE clause
        where_clauses = []
        params = []

        if status_filter and status_filter in ('completed', 'failed'):
            where_clauses.append("status = ?")
            params.append(status_filter)

        if podcast_slug:
            where_clauses.append("podcast_slug = ?")
            params.append(podcast_slug)

        where_sql = " AND ".join(where_clauses) if where_clauses else "1=1"

        # Validate sort column
        valid_sort_cols = ['processed_at', 'podcast_title', 'episode_title',
                          'processing_duration_seconds', 'ads_detected',
                          'reprocess_number', 'status']
        if sort_by not in valid_sort_cols:
            sort_by = 'processed_at'
        sort_dir = 'DESC' if sort_dir.lower() == 'desc' else 'ASC'

        # Get total count
        cursor = conn.execute(
            f"SELECT COUNT(*) FROM processing_history WHERE {where_sql}",
            params
        )
        total_count = cursor.fetchone()[0]

        # Get paginated results
        query_params = params + [limit, offset]
        cursor = conn.execute(
            f"""SELECT * FROM processing_history
                WHERE {where_sql}
                ORDER BY {sort_by} {sort_dir}
                LIMIT ? OFFSET ?""",
            query_params
        )

        entries = [dict(row) for row in cursor.fetchall()]
        return entries, total_count

    def get_processing_history_stats(self) -> Dict:
        """Get aggregate statistics from processing history."""
        conn = self.get_connection()

        # Total processed
        cursor = conn.execute("SELECT COUNT(*) FROM processing_history")
        total_processed = cursor.fetchone()[0]

        # Completed count
        cursor = conn.execute("SELECT COUNT(*) FROM processing_history WHERE status = 'completed'")
        completed_count = cursor.fetchone()[0]

        # Failed count
        cursor = conn.execute("SELECT COUNT(*) FROM processing_history WHERE status = 'failed'")
        failed_count = cursor.fetchone()[0]

        # Average processing time (for completed only)
        cursor = conn.execute(
            """SELECT AVG(processing_duration_seconds)
               FROM processing_history
               WHERE status = 'completed' AND processing_duration_seconds IS NOT NULL"""
        )
        avg_time = cursor.fetchone()[0] or 0

        # Total ads detected
        cursor = conn.execute("SELECT SUM(ads_detected) FROM processing_history WHERE status = 'completed'")
        total_ads = cursor.fetchone()[0] or 0

        # Reprocess count (entries with reprocess_number > 1)
        cursor = conn.execute("SELECT COUNT(*) FROM processing_history WHERE reprocess_number > 1")
        reprocess_count = cursor.fetchone()[0]

        # Unique episodes processed
        cursor = conn.execute("SELECT COUNT(DISTINCT podcast_slug || '/' || episode_id) FROM processing_history")
        unique_episodes = cursor.fetchone()[0]

        return {
            'total_processed': total_processed,
            'completed_count': completed_count,
            'failed_count': failed_count,
            'avg_processing_time_seconds': round(avg_time, 2),
            'total_ads_detected': total_ads,
            'reprocess_count': reprocess_count,
            'unique_episodes': unique_episodes
        }

    def get_episode_reprocess_count(self, podcast_id: int, episode_id: str) -> int:
        """Get the number of times an episode has been processed."""
        conn = self.get_connection()
        cursor = conn.execute(
            """SELECT COUNT(*) FROM processing_history
               WHERE podcast_id = ? AND episode_id = ?""",
            (podcast_id, episode_id)
        )
        return cursor.fetchone()[0]

    def export_processing_history(self, status_filter: str = None,
                                   podcast_slug: str = None) -> List[Dict]:
        """Export all processing history (no pagination) for export."""
        conn = self.get_connection()

        # Build WHERE clause
        where_clauses = []
        params = []

        if status_filter and status_filter in ('completed', 'failed'):
            where_clauses.append("status = ?")
            params.append(status_filter)

        if podcast_slug:
            where_clauses.append("podcast_slug = ?")
            params.append(podcast_slug)

        where_sql = " AND ".join(where_clauses) if where_clauses else "1=1"

        cursor = conn.execute(
            f"""SELECT * FROM processing_history
                WHERE {where_sql}
                ORDER BY processed_at DESC""",
            params
        )

        return [dict(row) for row in cursor.fetchall()]

    # ========== Auto-Process Queue Methods ==========

    def is_auto_process_enabled(self) -> bool:
        """Check if auto-process is enabled globally."""
        setting = self.get_setting('auto_process_enabled')
        return setting == 'true' if setting else True  # Default to enabled

    def is_auto_process_enabled_for_podcast(self, slug: str) -> bool:
        """Check if auto-process is enabled for a specific podcast.

        Returns: True if enabled (considering both global and podcast-level settings)
        """
        # Check global setting first
        global_enabled = self.is_auto_process_enabled()

        # Get podcast-level override
        podcast = self.get_podcast_by_slug(slug)
        if not podcast:
            return global_enabled

        override = podcast.get('auto_process_override')
        if override == 'true':
            return True
        elif override == 'false':
            return False
        else:
            # No override, use global setting
            return global_enabled

    def queue_episode_for_processing(self, slug: str, episode_id: str,
                                      original_url: str, title: str = None,
                                      published_at: str = None,
                                      description: str = None) -> Optional[int]:
        """Add an episode to the auto-process queue. Returns queue ID or None if already queued."""
        conn = self.get_connection()

        # Get podcast ID
        podcast = self.get_podcast_by_slug(slug)
        if not podcast:
            logger.error(f"Cannot queue episode: podcast not found: {slug}")
            return None

        podcast_id = podcast['id']

        try:
            cursor = conn.execute(
                """INSERT INTO auto_process_queue
                   (podcast_id, episode_id, original_url, title, published_at, description)
                   VALUES (?, ?, ?, ?, ?, ?)
                   ON CONFLICT(podcast_id, episode_id) DO NOTHING""",
                (podcast_id, episode_id, original_url, title, published_at, description)
            )
            conn.commit()
            return cursor.lastrowid if cursor.rowcount > 0 else None
        except Exception as e:
            logger.error(f"Failed to queue episode for processing: {e}")
            return None

    def get_next_queued_episode(self) -> Optional[Dict]:
        """Get the next pending episode from the queue (FIFO order)."""
        conn = self.get_connection()
        cursor = conn.execute(
            """SELECT q.*, p.slug as podcast_slug, p.title as podcast_title
               FROM auto_process_queue q
               JOIN podcasts p ON q.podcast_id = p.id
               WHERE q.status = 'pending'
               ORDER BY q.created_at ASC
               LIMIT 1"""
        )
        row = cursor.fetchone()
        return dict(row) if row else None

    def update_queue_status(self, queue_id: int, status: str,
                            error_message: str = None) -> bool:
        """Update the status of a queued episode."""
        conn = self.get_connection()
        if error_message:
            conn.execute(
                """UPDATE auto_process_queue SET
                   status = ?,
                   error_message = ?,
                   attempts = attempts + 1,
                   updated_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now')
                   WHERE id = ?""",
                (status, error_message, queue_id)
            )
        else:
            conn.execute(
                """UPDATE auto_process_queue SET
                   status = ?,
                   updated_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now')
                   WHERE id = ?""",
                (status, queue_id)
            )
        conn.commit()
        return True

    def get_queue_status(self) -> Dict:
        """Get auto-process queue status summary."""
        conn = self.get_connection()
        cursor = conn.execute(
            """SELECT
               COUNT(*) FILTER (WHERE status = 'pending') as pending,
               COUNT(*) FILTER (WHERE status = 'processing') as processing,
               COUNT(*) FILTER (WHERE status = 'completed') as completed,
               COUNT(*) FILTER (WHERE status = 'failed') as failed,
               COUNT(*) as total
               FROM auto_process_queue"""
        )
        row = cursor.fetchone()
        return dict(row) if row else {'pending': 0, 'processing': 0, 'completed': 0, 'failed': 0, 'total': 0}

    def clear_completed_queue_items(self, older_than_hours: int = 24) -> int:
        """Clear completed queue items older than specified hours. Returns count deleted."""
        conn = self.get_connection()
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=older_than_hours)).strftime('%Y-%m-%dT%H:%M:%SZ')
        cursor = conn.execute(
            """DELETE FROM auto_process_queue
               WHERE status = 'completed' AND updated_at < ?""",
            (cutoff,)
        )
        conn.commit()
        return cursor.rowcount

    def clear_pending_queue_items(self) -> int:
        """Clear all pending items from the auto-process queue. Returns count deleted."""
        conn = self.get_connection()
        cursor = conn.execute(
            """DELETE FROM auto_process_queue WHERE status = 'pending'"""
        )
        conn.commit()
        return cursor.rowcount

    def reset_orphaned_queue_items(self, stuck_minutes: int = 35, max_attempts: int = 3) -> Tuple[int, int]:
        """Reset queue items stuck in 'processing' for too long.

        This catches orphaned queue items where the worker crashed or was killed
        without properly updating the status. Items exceeding max_attempts are
        marked as 'failed' permanently. Items under max_attempts are reset to
        'pending' with incremented attempts counter.

        Args:
            stuck_minutes: Minutes after which a 'processing' item is considered orphaned
            max_attempts: Maximum retry attempts before marking as permanently failed

        Returns:
            Tuple of (reset_count, failed_count)
        """
        conn = self.get_connection()

        # First: Mark items that exceeded max attempts as permanently failed
        cursor = conn.execute(
            """UPDATE auto_process_queue
               SET status = 'failed',
                   updated_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now'),
                   error_message = 'Exceeded max retry attempts'
               WHERE status = 'processing'
               AND attempts >= ?
               AND datetime(updated_at) < datetime('now', ? || ' minutes')
               RETURNING id, episode_id""",
            (max_attempts, f'-{stuck_minutes}')
        )
        failed_items = cursor.fetchall()

        # Second: Reset items under max attempts, incrementing counter
        cursor = conn.execute(
            """UPDATE auto_process_queue
               SET status = 'pending',
                   attempts = attempts + 1,
                   updated_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now'),
                   error_message = 'Reset after processing timeout'
               WHERE status = 'processing'
               AND attempts < ?
               AND datetime(updated_at) < datetime('now', ? || ' minutes')
               RETURNING id, episode_id""",
            (max_attempts, f'-{stuck_minutes}')
        )
        reset_items = cursor.fetchall()
        conn.commit()

        for row in failed_items:
            logger.warning(f"Queue item exceeded max attempts, marking failed: id={row['id']}, episode_id={row['episode_id']}")
        for row in reset_items:
            logger.info(f"Reset orphaned queue item: id={row['id']}, episode_id={row['episode_id']}")

        return len(reset_items), len(failed_items)

    def reset_failed_queue_items(self, max_retries: int = 3, max_age_hours: int = 48) -> int:
        """Reset failed queue items eligible for automatic retry with backoff.

        Backoff: attempt 1 → 5 min, attempt 2 → 15 min, attempt 3+ → 45 min.
        Only resets where episode status is 'failed' (not 'permanently_failed'),
        retry_count < max_retries, and the item failed within max_age_hours.
        """
        conn = self.get_connection()
        cursor = conn.execute(
            """UPDATE auto_process_queue
               SET status = 'pending',
                   updated_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now')
               WHERE id IN (
                   SELECT q.id
                   FROM auto_process_queue q
                   JOIN episodes e ON q.podcast_id = e.podcast_id
                                    AND q.episode_id = e.episode_id
                   WHERE q.status = 'failed'
                     AND e.status = 'failed'
                     AND e.retry_count < ?
                     AND datetime(q.updated_at) > datetime('now', '-' || ? || ' hours')
                     AND datetime(q.updated_at) < datetime('now',
                         CASE
                             WHEN q.attempts <= 1 THEN '-5 minutes'
                             WHEN q.attempts = 2 THEN '-15 minutes'
                             ELSE '-45 minutes'
                         END
                     )
               )
               RETURNING id, episode_id""",
            (max_retries, max_age_hours)
        )
        reset_items = cursor.fetchall()
        conn.commit()
        for row in reset_items:
            logger.info(f"Reset failed queue item for retry: id={row['id']}, episode_id={row['episode_id']}")
        return len(reset_items)

    # ========== Full-Text Search Methods ==========

    def rebuild_search_index(self) -> int:
        """Rebuild the FTS5 search index from scratch.

        Indexes:
        - Episodes: title, description, transcript
        - Podcasts: title, description
        - Patterns: text, sponsor
        - Sponsors: name, aliases

        Returns count of indexed items.
        """
        conn = self.get_connection()
        count = 0

        # Clear existing index
        conn.execute("DELETE FROM search_index")

        # Index podcasts
        cursor = conn.execute("""
            SELECT slug, title, description
            FROM podcasts
        """)
        for row in cursor:
            conn.execute("""
                INSERT INTO search_index (content_type, content_id, podcast_slug, title, body, metadata)
                VALUES (?, ?, ?, ?, ?, ?)
            """, ('podcast', row['slug'], row['slug'], row['title'],
                  row['description'] or '', ''))
            count += 1

        # Index episodes with transcripts
        cursor = conn.execute("""
            SELECT e.episode_id, e.title, e.description, p.slug, ed.transcript_text
            FROM episodes e
            JOIN podcasts p ON e.podcast_id = p.id
            LEFT JOIN episode_details ed ON e.id = ed.episode_id
            WHERE e.status = 'processed'
        """)
        for row in cursor:
            # Limit transcript size to avoid huge index entries
            transcript = (row['transcript_text'] or '')[:100000]  # ~100k chars max
            conn.execute("""
                INSERT INTO search_index (content_type, content_id, podcast_slug, title, body, metadata)
                VALUES (?, ?, ?, ?, ?, ?)
            """, ('episode', row['episode_id'], row['slug'], row['title'],
                  transcript, row['description'] or ''))
            count += 1

        # Index patterns
        cursor = conn.execute("""
            SELECT id, text_template, sponsor, scope
            FROM ad_patterns
            WHERE is_active = 1
        """)
        for row in cursor:
            conn.execute("""
                INSERT INTO search_index (content_type, content_id, podcast_slug, title, body, metadata)
                VALUES (?, ?, ?, ?, ?, ?)
            """, ('pattern', str(row['id']), row['scope'] or 'global',
                  row['sponsor'] or 'Unknown', row['text_template'] or '', ''))
            count += 1

        # Index sponsors
        cursor = conn.execute("""
            SELECT id, name, aliases
            FROM known_sponsors
            WHERE is_active = 1
        """)
        for row in cursor:
            conn.execute("""
                INSERT INTO search_index (content_type, content_id, podcast_slug, title, body, metadata)
                VALUES (?, ?, ?, ?, ?, ?)
            """, ('sponsor', str(row['id']), 'global', row['name'],
                  row['aliases'] or '', ''))
            count += 1

        conn.commit()
        logger.info(f"Search index rebuilt with {count} items")
        return count

    def index_episode(self, episode_id: str, slug: str) -> bool:
        """Index or re-index a single episode in the search index."""
        conn = self.get_connection()
        try:
            row = conn.execute("""
                SELECT e.episode_id, e.title, e.description, p.slug, ed.transcript_text
                FROM episodes e
                JOIN podcasts p ON e.podcast_id = p.id
                LEFT JOIN episode_details ed ON e.id = ed.episode_id
                WHERE e.episode_id = ? AND p.slug = ?
            """, (episode_id, slug)).fetchone()
            if not row:
                return False
            conn.execute(
                "DELETE FROM search_index WHERE content_type = 'episode' AND content_id = ?",
                (episode_id,))
            transcript = (row['transcript_text'] or '')[:100000]
            conn.execute("""
                INSERT INTO search_index (content_type, content_id, podcast_slug, title, body, metadata)
                VALUES (?, ?, ?, ?, ?, ?)
            """, ('episode', row['episode_id'], row['slug'], row['title'],
                  transcript, row['description'] or ''))
            conn.commit()
            return True
        except Exception as e:
            logger.error(f"Failed to index episode {episode_id}: {e}")
            return False

    @staticmethod
    def _sanitize_snippet(snippet):
        """Sanitize FTS5 snippet HTML, allowing only <mark> highlight tags."""
        if not snippet:
            return snippet
        return nh3.clean(snippet, tags={"mark"}, attributes={})

    def search(self, query: str, content_type: Optional[str] = None, limit: int = 50) -> List[Dict]:
        """Full-text search across indexed content.

        Args:
            query: Search query (supports FTS5 query syntax)
            content_type: Filter by type ('episode', 'podcast', 'pattern', 'sponsor')
            limit: Maximum results to return

        Returns:
            List of search results with type, id, slug, title, snippet, and score
        """
        conn = self.get_connection()

        # Clean query for FTS5 (escape special characters)
        clean_query = query.replace('"', '""').strip()
        if not clean_query:
            return []

        # Add wildcards for partial matching
        search_query = f'"{clean_query}"* OR {clean_query}*'

        try:
            if content_type:
                cursor = conn.execute("""
                    SELECT
                        content_type,
                        content_id,
                        podcast_slug,
                        title,
                        snippet(search_index, 4, '<mark>', '</mark>', '...', 64) as snippet,
                        bm25(search_index) as score
                    FROM search_index
                    WHERE search_index MATCH ?
                    AND content_type = ?
                    ORDER BY bm25(search_index)
                    LIMIT ?
                """, (search_query, content_type, limit))
            else:
                cursor = conn.execute("""
                    SELECT
                        content_type,
                        content_id,
                        podcast_slug,
                        title,
                        snippet(search_index, 4, '<mark>', '</mark>', '...', 64) as snippet,
                        bm25(search_index) as score
                    FROM search_index
                    WHERE search_index MATCH ?
                    ORDER BY bm25(search_index)
                    LIMIT ?
                """, (search_query, limit))

            results = []
            for row in cursor:
                results.append({
                    'type': row['content_type'],
                    'id': row['content_id'],
                    'podcastSlug': row['podcast_slug'],
                    'title': row['title'],
                    'snippet': self._sanitize_snippet(row['snippet']),
                    'score': abs(row['score'])  # BM25 returns negative scores
                })

            return results

        except Exception as e:
            logger.error(f"Search error for query '{query}': {e}")
            return []

    def get_search_index_stats(self) -> Dict[str, int]:
        """Get statistics about the search index."""
        conn = self.get_connection()

        stats = {}
        cursor = conn.execute("""
            SELECT content_type, COUNT(*) as count
            FROM search_index
            GROUP BY content_type
        """)
        for row in cursor:
            stats[row['content_type']] = row['count']

        stats['total'] = sum(stats.values())
        return stats
