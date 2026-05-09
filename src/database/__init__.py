"""SQLite database package for MinusPod."""
import sqlite3
import threading
import logging
from pathlib import Path
from typing import Optional

from database.schema import SchemaMixin
from database.podcasts import PodcastMixin
from database.episodes import EpisodeMixin
from database.settings import SettingsMixin, DEFAULT_MODEL_PRICING
from database.patterns import PatternMixin
from database.sponsors import SponsorMixin
from database.stats import StatsMixin
from database.maintenance import MaintenanceMixin
from database.fingerprints import FingerprintMixin
from database.queue import QueueMixin
from database.search import SearchMixin
from database.auth_lockout import AuthLockoutMixin

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

Each ad segment: {{"start": FLOAT_SECONDS, "end": FLOAT_SECONDS, "confidence": FLOAT_0_TO_1, "reason": "brief description", "end_text": "last 3-5 words"}}

ALL values for "start", "end", and "confidence" MUST be numeric (float). Never use strings like "high", "low", "medium", or percentages like "95%". Examples: "start": 45.0, "end": 82.0, "confidence": 0.95

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
for a brand with product positioning language. It is not editorial content.{sponsor_database}"""

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

Each ad segment: {{"start": FLOAT_SECONDS, "end": FLOAT_SECONDS, "confidence": FLOAT_0_TO_1, "reason": "brief description", "end_text": "last 3-5 words"}}

ALL values for "start", "end", and "confidence" MUST be numeric (float). Never use strings like "high", "low", "medium", or percentages like "95%". Examples: "start": 45.0, "end": 82.0, "confidence": 0.95

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

Output: []{sponsor_database}"""


# Both reviewer prompts use placeholder substitution via _render_prompt;
# never .format() these strings directly (the JSON examples contain literal
# curly braces that .format() would attempt to interpolate).
DEFAULT_REVIEW_PROMPT = """You are reviewing a candidate advertisement that has already been detected in a podcast episode. Your job is to make one of three decisions: confirm the detection is correct, adjust the boundaries if they include show content or miss part of the ad, or reject it as a false positive.

You will be given the podcast and episode context, the text of the detected ad with its timestamps, transcript context for 60 seconds before and after the ad, and the original detector's reasoning for flagging this segment.

DECISION CRITERIA:

CONFIRMED: The detected segment is a real-world advertisement and the boundaries are correct. The ad starts at or near the first promotional word or transition phrase, and ends at or near the last call to action. Removing this segment would leave clean show content on either side.

ADJUST: The detected segment is a real-world advertisement, but the boundaries are wrong. Adjust when:
- The start cuts off the beginning of the ad (transition phrases like "and now a word from our sponsor" or the opening of the sponsor read appear before the detected start)
- The start includes show content that flows into the ad (the sentence before the detected start is part of the show, not the ad)
- The end cuts off the closing of the ad (URL, promo code, or sign-off appears after the detected end)
- The end includes show content (host transitions back to the topic before the detected end)

When adjusting, the new boundaries must be within {max_boundary_shift_seconds} seconds of the original boundaries in either direction. Do not propose larger shifts.

REJECT: The detected segment is not a real-world advertisement. Reject cases:
- A guest discussing their own work, book, or project in the context of the interview
- The host organically mentioning their own other shows, social media, or Patreon as part of conversational flow (not a produced segment)
- Brand names mentioned in passing as part of genuine topic discussion, news coverage, or product reviews
- A comedic bit or fictional sponsor read that is part of the show's creative content (no real product behind it)
- Silence, pauses, or audio production artifacts with no promotional transcript content
- Topic transitions or content gaps without promotional language

DO NOT REJECT (these ARE real ads):
- Host-read sponsor segments, including ones without promo codes
- Hosting platform pre/post-rolls (Acast, Spotify for Podcasters, iHeart Radio, etc.)
- Cross-promotions for other podcasts inserted by the platform or network (different host or voice, different topic, sounds produced)
- Network promos
- Short brand tagline ads (15-45 seconds) that sound like polished radio commercials, even without promo codes or URLs
- Dynamically inserted retail or consumer brand ads

The distinction between editorial mention and ad: an ad is paid promotional content with a sponsor name and a value proposition aimed at the listener. Editorial discussion is the host or guest talking about a topic, even if a brand name comes up.

WHEN IN DOUBT: Confirm with original boundaries unchanged. Do not reject unless you have clear evidence from the transcript that the segment is not a real-world advertisement. Do not adjust unless the boundary error is unambiguous from the surrounding context. The cost of leaving a real ad in the audio (false negative) is higher than the cost of confirming a borderline detection.

OUTPUT FORMAT:

Respond with a single JSON object. No preamble, no markdown fences, no explanation outside the JSON.

Schema: verdict is one of "confirmed", "adjust", or "reject". adjusted_start and adjusted_end are numbers in seconds, included only when verdict is "adjust". reasoning is a short one or two sentence explanation. confidence is a number from 0 to 1.

Example confirmed response: {{"verdict": "confirmed", "reasoning": "Standard host-read BetterHelp ad with clear sponsor name, pitch, and promo code. Boundaries align with transition phrase at start and final URL at end.", "confidence": 0.95}}

Example adjust response: {{"verdict": "adjust", "adjusted_start": 1245.0, "adjusted_end": 1320.5, "reasoning": "Detected start was 8 seconds late, missing the 'and now a word from our sponsor' transition. End was 4 seconds early, cutting off the final URL repetition.", "confidence": 0.85}}

Example reject response: {{"verdict": "reject", "reasoning": "Host is discussing the company as part of the episode's news coverage, not promoting it. No promo code, no value proposition aimed at the listener, surrounding context is editorial commentary on recent events.", "confidence": 0.80}}{sponsor_database}"""


DEFAULT_RESURRECT_PROMPT = """You are reviewing a candidate advertisement that the validator already rejected. Your job is to either agree with the rejection (the segment is not a real-world advertisement) or disagree (the validator was wrong and the segment should be cut as an ad).

You will be given the podcast and episode context, the text of the rejected segment with its timestamps, transcript context for 60 seconds before and after, the original detector's reasoning for flagging this segment, and the validator's confidence value (which fell below the user's cut threshold).

DECISION CRITERIA:

RESURRECT: The validator was wrong. The segment is a real-world advertisement and should be cut from the audio. Resurrect when:
- The transcript clearly contains promotional language: sponsor name + value proposition + call to action, or polished marketing copy with concentrated brand messaging
- The segment matches the structure of an ad (transition in, sponsor read or platform promo, return to content) even if the validator marked confidence as low
- It is a short brand tagline ad (15-45 seconds) without promo codes, but with concentrated marketing language
- It is a hosting-platform pre/post-roll or cross-promo for another podcast in the network

REJECT: Agree with the validator. The segment is not a real-world advertisement. Reject cases match the rejection criteria a normal reviewer would use:
- A guest discussing their own work in the context of the interview
- Host organically mentioning their own other shows or social media in conversation
- Brand names mentioned in passing as part of editorial topic discussion
- Comedic bits or fictional sponsor reads in the show's creative content
- Silence, pauses, or topic transitions with no promotional transcript content

WHEN IN DOUBT: Agree with the validator and reject. The validator already had reason to mark this low-confidence; only resurrect when you have clear evidence from the transcript that this is a real ad.

OUTPUT FORMAT:

Respond with a single JSON object. No preamble, no markdown fences, no explanation outside the JSON.

Schema: verdict is one of "resurrect" or "reject". reasoning is a short one or two sentence explanation. confidence is a number from 0 to 1.

Example resurrect response: {{"verdict": "resurrect", "reasoning": "Clear platform-inserted Acast post-roll with promotional structure. Validator likely flagged low confidence due to short duration, but transcript content is unambiguous.", "confidence": 0.90}}

Example reject response: {{"verdict": "reject", "reasoning": "Host is mentioning their Patreon in passing during a conversation about audience support, not running a produced ad break. No promo code, no value proposition, no transition cues.", "confidence": 0.85}}{sponsor_database}"""


class Database(SchemaMixin, PodcastMixin, EpisodeMixin, SettingsMixin,
               PatternMixin, SponsorMixin, StatsMixin, MaintenanceMixin,
               FingerprintMixin, QueueMixin, SearchMixin, AuthLockoutMixin):
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
        """Get thread-local database connection.

        WAL mode is preferred but not required: a stale or permissioned
        WAL file on the mounted volume occasionally makes the first
        ``PRAGMA journal_mode = WAL`` fail with "disk I/O error". When
        that happens, reset the journal via ``PRAGMA journal_mode = DELETE``
        and try WAL once more. If WAL is still refused, stay on DELETE
        mode -- less concurrent, still correct -- rather than crash the
        worker on boot.
        """
        if not hasattr(self._local, 'connection') or self._local.connection is None:
            self._local.connection = sqlite3.connect(
                str(self.db_path),
                check_same_thread=False,
                timeout=30.0
            )
            self._local.connection.row_factory = sqlite3.Row
            self._local.connection.execute("PRAGMA busy_timeout = 30000")
            self._local.connection.execute("PRAGMA foreign_keys = ON")
            try:
                self._local.connection.execute("PRAGMA journal_mode = WAL")
            except sqlite3.OperationalError as exc:
                logger.warning(
                    "PRAGMA journal_mode = WAL failed (%s); resetting WAL "
                    "state and retrying.",
                    exc,
                )
                try:
                    self._local.connection.execute("PRAGMA journal_mode = DELETE")
                    self._local.connection.execute("PRAGMA journal_mode = WAL")
                except sqlite3.OperationalError:
                    logger.warning(
                        "WAL mode still refused after reset; falling back "
                        "to DELETE journal. Concurrency is reduced until "
                        "the volume state is repaired.",
                    )
            # NORMAL sync gives WAL its durability contract (fsync on
            # checkpoint and WAL commit) without the fsync-every-write
            # penalty of FULL. Harmless in DELETE mode too.
            try:
                self._local.connection.execute("PRAGMA synchronous = NORMAL")
                self._local.connection.execute("PRAGMA wal_autocheckpoint = 1000")
            except sqlite3.OperationalError:
                pass
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
