"""SQLite database module for podcast server."""
import sqlite3
import threading
import logging
import json
import os
from pathlib import Path
from datetime import datetime, timedelta
from typing import Optional, Dict, List, Any, Tuple

logger = logging.getLogger(__name__)

# Default ad detection prompts
DEFAULT_SYSTEM_PROMPT = """Analyze this podcast transcript and identify ALL advertisement segments.

DETECTION RULES:
- Host-read sponsor segments ARE ads. Any product promotion for compensation is an ad.
- When in doubt, mark it as an ad. False positives are preferred over missing ads.
- Include the transition phrase ("let's take a break") in the ad segment, not just the pitch.
- Ad breaks typically last 60-120 seconds. Shorter segments may indicate incomplete detection.

WHAT TO LOOK FOR:
- Transitions: "This episode is brought to you by...", "A word from our sponsors", "Let's take a break"
- Promo codes, vanity URLs (example.com/podcast), calls to action
- Product endorsements, sponsored content, promotional messages
- Network-inserted retail ads (may sound like radio commercials)

COMMON PODCAST SPONSORS (high confidence if mentioned):
BetterHelp, Athletic Greens, AG1, Shopify, Amazon, Audible, Squarespace, HelloFresh, Factor, NordVPN, ExpressVPN, Mint Mobile, MasterClass, Calm, Headspace, ZipRecruiter, Indeed, LinkedIn Jobs, LinkedIn, Stamps.com, SimpliSafe, Ring, ADT, Casper, Helix Sleep, Purple, Brooklinen, Bombas, Manscaped, Dollar Shave Club, Harry's, Quip, Hims, Hers, Roman, Keeps, Function of Beauty, Native, Liquid IV, Athletic Brewing, Magic Spoon, Thrive Market, Butcher Box, Blue Apron, DoorDash, Uber Eats, Grubhub, Instacart, Rocket Money, Credit Karma, SoFi, Acorns, Betterment, Wealthfront, PolicyGenius, Lemonade, State Farm, Progressive, Geico, Liberty Mutual, T-Mobile, Visible, FanDuel, DraftKings, BetMGM, Toyota, Hyundai, CarMax, Carvana, eBay Motors, ZocDoc, GoodRx, Care/of, Ritual, Seed, HubSpot, NetSuite, Monday.com, Notion, Canva, Grammarly, Babbel, Rosetta Stone, Blinkist, Raycon, Bose, MacPaw, CleanMyMac, Green Chef, Magic Mind, Honeylove, Cozy Earth, Quince, LMNT, Nutrafol, Aura, OneSkin, Incogni, Gametime, 1Password, Bitwarden, CacheFly, Deel, DeleteMe, Framer, Miro, Monarch Money, OutSystems, Spaceship, Thinkst Canary, ThreatLocker, Vanta, Veeam, Zapier, Zscaler, Capital One, Ford, WhatsApp

RETAIL/CONSUMER BRANDS (network-inserted ads):
Nordstrom, Macy's, Target, Walmart, Kohl's, Bloomingdale's, JCPenney, TJ Maxx, Home Depot, Lowe's, Best Buy, Costco, Gap, Old Navy, H&M, Zara, Nike, Adidas, Lululemon, Coach, Kate Spade, Michael Kors, Sephora, Ulta, Bath & Body Works, CVS, Walgreens, AutoZone, O'Reilly Auto Parts, Jiffy Lube, Midas, Gold Belly, Farmer's Dog, Caldera Lab, Monster Energy, Red Bull, Whole Foods, Trader Joe's, Kroger

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

Output: [{{"start": 45.0, "end": 82.0, "confidence": 0.98, "reason": "Athletic Greens sponsor read", "end_text": "athleticgreens.com/podcast"}}]"""

# Default second pass system prompt - BLIND analysis for subtle/baked-in ads
DEFAULT_SECOND_PASS_PROMPT = """Detect SUBTLE and BAKED-IN advertisements that don't use traditional ad transitions.

YOUR FOCUS (ignore obvious "brought to you by" ads):
1. Host endorsements woven into conversation ("I've been using X...")
2. Casual product mentions with promo codes or URLs
3. "Oh by the way" style plugs
4. Quick mid-roll sponsor mentions without transitions
5. Post-signoff promotional content

DO NOT MARK AS ADS:
- Cross-promotion of host's other shows or network podcasts
- Guest plugging their own work/projects
- Genuine personal recommendations without commercial signals

DETECTION SIGNALS:
- Promo codes, vanity URLs, "link in show notes"
- Pricing, discounts, availability info
- Product tangents unrelated to episode topic
- Tonal shift to scripted delivery

AD BOUNDARIES:
- START: First promotional statement
- END: When regular content resumes (not when pitch ends)
- Typical length: 45-120 seconds. Under 30s = verify you found the full segment.

WINDOW CONTEXT:
This transcript may be a segment of a longer episode.
- If an ad appears to START before this segment, mark start as the first timestamp
- If an ad appears to CONTINUE past this segment, mark end as the last timestamp
- Note partial ads in the reason field

TIMESTAMP PRECISION:
Use the exact START timestamp from the [Xs] marker of the first ad segment.
Use the exact END timestamp from the [Xs] marker of the last ad segment.
Do not interpolate or estimate times between segments.

BE THOROUGH: If it sounds promotional, mark it. False positives are acceptable.
BE ACCURATE: Don't invent ads. Some segments have no subtle ads, and [] is valid.

OUTPUT: JSON array only, no explanation.
Format: [{{"start": 0.0, "end": 60.0, "confidence": 0.95, "reason": "description", "end_text": "last words"}}]"""


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
    status TEXT DEFAULT 'pending' CHECK(status IN ('pending','processing','processed','failed')),
    processed_file TEXT,
    processed_at TEXT,
    original_duration REAL,
    new_duration REAL,
    ads_removed INTEGER DEFAULT 0,
    ads_removed_firstpass INTEGER DEFAULT 0,
    ads_removed_secondpass INTEGER DEFAULT 0,
    error_message TEXT,
    ad_detection_status TEXT DEFAULT NULL CHECK(ad_detection_status IN (NULL, 'success', 'failed')),
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

CREATE INDEX IF NOT EXISTS idx_podcasts_slug ON podcasts(slug);
CREATE INDEX IF NOT EXISTS idx_episodes_podcast_id ON episodes(podcast_id);
CREATE INDEX IF NOT EXISTS idx_episodes_episode_id ON episodes(episode_id);
CREATE INDEX IF NOT EXISTS idx_episodes_status ON episodes(status);
CREATE INDEX IF NOT EXISTS idx_episodes_created_at ON episodes(created_at);
CREATE INDEX IF NOT EXISTS idx_episode_details_episode_id ON episode_details(episode_id);
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
            self._local.connection.execute("PRAGMA foreign_keys = ON")
        return self._local.connection

    def _init_schema(self):
        """Initialize database schema."""
        conn = self.get_connection()
        conn.executescript(SCHEMA_SQL)
        conn.commit()
        logger.info(f"Database schema initialized at {self.db_path}")

        # Run schema migrations for existing databases
        self._run_schema_migrations()

    def _run_schema_migrations(self):
        """Run schema migrations for existing databases."""
        conn = self.get_connection()

        # Get existing columns in episodes table
        cursor = conn.execute("PRAGMA table_info(episodes)")
        columns = [row['name'] for row in cursor.fetchall()]

        # Migration: Add ad_detection_status column if missing
        if 'ad_detection_status' not in columns:
            try:
                conn.execute("""
                    ALTER TABLE episodes
                    ADD COLUMN ad_detection_status TEXT DEFAULT NULL
                """)
                conn.commit()
                logger.info("Migration: Added ad_detection_status column to episodes table")
            except Exception as e:
                logger.error(f"Migration failed for ad_detection_status: {e}")

        # Get existing columns in episode_details table
        cursor = conn.execute("PRAGMA table_info(episode_details)")
        details_columns = [row['name'] for row in cursor.fetchall()]

        # Migration: Rename claude_prompt to first_pass_prompt
        if 'claude_prompt' in details_columns and 'first_pass_prompt' not in details_columns:
            try:
                conn.execute("""
                    ALTER TABLE episode_details
                    RENAME COLUMN claude_prompt TO first_pass_prompt
                """)
                conn.commit()
                logger.info("Migration: Renamed claude_prompt to first_pass_prompt")
            except Exception as e:
                logger.error(f"Migration failed for claude_prompt rename: {e}")

        # Migration: Rename claude_raw_response to first_pass_response
        if 'claude_raw_response' in details_columns and 'first_pass_response' not in details_columns:
            try:
                conn.execute("""
                    ALTER TABLE episode_details
                    RENAME COLUMN claude_raw_response TO first_pass_response
                """)
                conn.commit()
                logger.info("Migration: Renamed claude_raw_response to first_pass_response")
            except Exception as e:
                logger.error(f"Migration failed for claude_raw_response rename: {e}")

        # Refresh column list after renames
        cursor = conn.execute("PRAGMA table_info(episode_details)")
        details_columns = [row['name'] for row in cursor.fetchall()]

        # Migration: Add second_pass_prompt column if missing
        if 'second_pass_prompt' not in details_columns:
            try:
                conn.execute("""
                    ALTER TABLE episode_details
                    ADD COLUMN second_pass_prompt TEXT
                """)
                conn.commit()
                logger.info("Migration: Added second_pass_prompt column to episode_details table")
            except Exception as e:
                logger.error(f"Migration failed for second_pass_prompt: {e}")

        # Migration: Add second_pass_response column if missing
        if 'second_pass_response' not in details_columns:
            try:
                conn.execute("""
                    ALTER TABLE episode_details
                    ADD COLUMN second_pass_response TEXT
                """)
                conn.commit()
                logger.info("Migration: Added second_pass_response column to episode_details table")
            except Exception as e:
                logger.error(f"Migration failed for second_pass_response: {e}")

        # Migration: Add ads_removed_firstpass column if missing
        if 'ads_removed_firstpass' not in columns:
            try:
                conn.execute("""
                    ALTER TABLE episodes
                    ADD COLUMN ads_removed_firstpass INTEGER DEFAULT 0
                """)
                conn.commit()
                logger.info("Migration: Added ads_removed_firstpass column to episodes table")
            except Exception as e:
                logger.error(f"Migration failed for ads_removed_firstpass: {e}")

        # Migration: Add ads_removed_secondpass column if missing
        if 'ads_removed_secondpass' not in columns:
            try:
                conn.execute("""
                    ALTER TABLE episodes
                    ADD COLUMN ads_removed_secondpass INTEGER DEFAULT 0
                """)
                conn.commit()
                logger.info("Migration: Added ads_removed_secondpass column to episodes table")
            except Exception as e:
                logger.error(f"Migration failed for ads_removed_secondpass: {e}")

        # Migration: Add description column if missing
        if 'description' not in columns:
            try:
                conn.execute("""
                    ALTER TABLE episodes
                    ADD COLUMN description TEXT
                """)
                conn.commit()
                logger.info("Migration: Added description column to episodes table")
            except Exception as e:
                logger.error(f"Migration failed for description: {e}")

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

        # Multi-pass ad detection (opt-in, default disabled)
        conn.execute(
            """INSERT INTO settings (key, value, is_default) VALUES (?, ?, 1)
               ON CONFLICT(key) DO NOTHING""",
            ('multi_pass_enabled', 'false')
        )

        # Second pass system prompt
        conn.execute(
            """INSERT INTO settings (key, value, is_default) VALUES (?, ?, 1)
               ON CONFLICT(key) DO NOTHING""",
            ('second_pass_prompt', DEFAULT_SECOND_PASS_PROMPT)
        )

        # Second pass model (defaults to Sonnet 4.5)
        from ad_detector import DEFAULT_MODEL
        conn.execute(
            """INSERT INTO settings (key, value, is_default) VALUES (?, ?, 1)
               ON CONFLICT(key) DO NOTHING""",
            ('second_pass_model', DEFAULT_MODEL)
        )

        # Whisper model (defaults to env var or 'small')
        whisper_model = os.environ.get('WHISPER_MODEL', 'small')
        conn.execute(
            """INSERT INTO settings (key, value, is_default) VALUES (?, ?, 1)
               ON CONFLICT(key) DO NOTHING""",
            ('whisper_model', whisper_model)
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
                   SUM(CASE WHEN e.status = 'processed' THEN 1 ELSE 0 END) as processed_count
            FROM podcasts p
            LEFT JOIN episodes e ON p.id = e.podcast_id
            GROUP BY p.id
            ORDER BY p.created_at DESC
        """)
        return [dict(row) for row in cursor.fetchall()]

    def get_podcast_by_slug(self, slug: str) -> Optional[Dict]:
        """Get podcast by slug."""
        conn = self.get_connection()
        cursor = conn.execute(
            "SELECT * FROM podcasts WHERE slug = ?", (slug,)
        )
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
                       'last_checked_at', 'source_url'):
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
            """SELECT e.*, p.slug, ed.transcript_text, ed.ad_markers_json,
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
                               'error_message', 'ad_detection_status'):
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
                    error_message, ad_detection_status)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
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
                    kwargs.get('ad_detection_status')
                )
            )
            db_id = cursor.lastrowid
            conn.commit()

        return db_id

    def save_episode_details(self, slug: str, episode_id: str,
                            transcript_text: str = None,
                            ad_markers: List[Dict] = None,
                            first_pass_response: str = None,
                            first_pass_prompt: str = None,
                            second_pass_prompt: str = None,
                            second_pass_response: str = None):
        """Save or update episode details (transcript, ad markers, pass data)."""
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

        ad_markers_json = json.dumps(ad_markers) if ad_markers is not None else None

        if row:
            # Update existing
            updates = []
            values = []
            if transcript_text is not None:
                updates.append("transcript_text = ?")
                values.append(transcript_text)
            if ad_markers_json is not None:
                updates.append("ad_markers_json = ?")
                values.append(ad_markers_json)
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
                   (episode_id, transcript_text, ad_markers_json,
                    first_pass_response, first_pass_prompt,
                    second_pass_prompt, second_pass_response)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (db_episode_id, transcript_text, ad_markers_json,
                 first_pass_response, first_pass_prompt,
                 second_pass_prompt, second_pass_response)
            )

        conn.commit()

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
                   updated_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now')
               WHERE podcast_id = ? AND episode_id = ?""",
            (podcast['id'], episode_id)
        )
        conn.commit()
        logger.debug(f"[{slug}:{episode_id}] Reset episode status to pending")

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
            'second_pass_prompt': DEFAULT_SECOND_PASS_PROMPT,
            'retention_period_minutes': os.environ.get('RETENTION_PERIOD', '1440'),
            'claude_model': DEFAULT_MODEL,
            'second_pass_model': DEFAULT_MODEL,
            'multi_pass_enabled': 'false',
            'whisper_model': os.environ.get('WHISPER_MODEL', 'small')
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

            cutoff = datetime.utcnow() - timedelta(minutes=retention_minutes)
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
