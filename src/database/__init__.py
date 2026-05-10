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
DEFAULT_REVIEW_PROMPT = """You are reviewing a candidate advertisement that has already been detected in a podcast episode. The transcript shows the candidate ad clearly marked, with up to 60 seconds of context before and after.

Your job is to return the corrected ad segment, OR an empty array if it is not actually an ad. Treat this exactly like ad detection on a single short window: you are emitting the ad object that should be cut from the audio, with start and end timestamps, or no object at all.

KEEP THE AD (return one segment): The candidate is a real-world advertisement that should be cut. Use the original start and end if they are already correct. Adjust them when the boundaries clip into show content or miss part of the ad:
- Start should land at or just before the first promotional word or transition phrase ("let's take a break", "and now a word from", "this episode is brought to you by")
- End should land at or just after the last call to action (final URL, promo code, sign-off), not in the middle of show content that follows
- Adjusted boundaries must stay within {max_boundary_shift_seconds} seconds of the original boundaries in either direction

DROP THE AD (return empty array): The candidate is not a real-world advertisement. Reject cases:
- A guest discussing their own work, book, or project in the context of the interview
- The host organically mentioning their own other shows, social media, or Patreon as part of conversational flow (not a produced segment)
- Brand names mentioned in passing as part of genuine topic discussion, news coverage, or product reviews
- A comedic bit or fictional sponsor read that is part of the show's creative content (no real product behind it)
- Silence, pauses, or audio production artifacts with no promotional transcript content
- Topic transitions or content gaps without promotional language

DO NOT REJECT (these ARE real ads, keep them):
- Host-read sponsor segments, including ones without promo codes
- Hosting platform pre/post-rolls (Acast, Spotify for Podcasters, iHeart Radio, etc.)
- Cross-promotions for other podcasts inserted by the platform or network (different host or voice, different topic, sounds produced)
- Network promos
- Short brand tagline ads (15-45 seconds) that sound like polished radio commercials, even without promo codes or URLs
- Dynamically inserted retail or consumer brand ads

The distinction between editorial mention and ad: an ad is paid promotional content with a sponsor name and a value proposition aimed at the listener. Editorial discussion is the host or guest talking about a topic, even if a brand name comes up.

WHEN IN DOUBT: Keep the ad with original boundaries unchanged. Do not drop unless you have clear evidence from the transcript that the segment is not a real-world advertisement. Do not adjust unless the boundary error is unambiguous from the surrounding context. The cost of leaving a real ad in the audio (false negative) is higher than the cost of keeping a borderline detection.

OUTPUT FORMAT:
Return ONLY a valid JSON array. No explanation, no markdown.

Each kept ad: {{"start": FLOAT_SECONDS, "end": FLOAT_SECONDS, "confidence": FLOAT_0_TO_1, "reason": "brief description"}}

ALL values for "start", "end", and "confidence" MUST be numeric (float). Never use strings like "high", "low", "medium", or percentages like "95%". Examples: "start": 45.0, "end": 82.0, "confidence": 0.95

EXAMPLE - KEEP UNCHANGED (boundaries are correct):
Original detection: 1245.0s - 1320.5s, sponsor: BetterHelp
[1245.5s] This episode is brought to you by BetterHelp.
[1248.0s] BetterHelp is the largest online therapy platform...
[1315.0s] Visit betterhelp.com slash podcast.
[1318.0s] That's betterhelp.com slash podcast.
[1322.0s] Anyway, back to what we were talking about.

Output: [{{"start": 1245.0, "end": 1320.5, "confidence": 0.95, "reason": "Confirmed BetterHelp host-read sponsor with clean boundaries"}}]

EXAMPLE - ADJUST BOUNDARIES (start was late, end was early):
Original detection: 100.0s - 130.0s, sponsor: AG1
[92.0s] So that wraps up our discussion. Let's take a quick break.
[95.0s] This episode is brought to you by Athletic Greens.
[100.0s] AG1 is the daily foundational nutrition supplement...
[128.0s] Go to athleticgreens.com slash podcast.
[130.5s] That's athleticgreens.com slash podcast.
[133.0s] Now, back to our conversation.

Output: [{{"start": 95.0, "end": 132.0, "confidence": 0.92, "reason": "Adjusted start back to capture transition; extended end past final URL repetition"}}]

EXAMPLE - DROP (host mentioning a brand editorially, not an ad):
Original detection: 50.0s - 70.0s, sponsor: Apple
[48.0s] Have you been following the Apple antitrust case?
[55.0s] The DOJ argued that Apple's app store policies harm developers.
[68.0s] What's your take on the proposed remedies?

Output: []{sponsor_database}"""


DEFAULT_RESURRECT_PROMPT = """You are taking a second look at a segment that the validator already rejected for low confidence. The transcript shows the candidate clearly marked, with up to 60 seconds of context before and after.

Your job: if the transcript shows this is actually an ad that should be cut, return the ad segment. If the validator was right and it is not an ad, return an empty array.

RESURRECT (return one segment): The validator was wrong. The segment is a real-world advertisement and should be cut. Resurrect when:
- The transcript clearly contains promotional language: sponsor name + value proposition + call to action, or polished marketing copy with concentrated brand messaging
- The segment matches the structure of an ad (transition in, sponsor read or platform promo, return to content) even if the validator marked confidence as low
- It is a short brand tagline ad (15-45 seconds) without promo codes, but with concentrated marketing language
- It is a hosting-platform pre/post-roll or cross-promo for another podcast in the network

KEEP REJECTED (return empty array): Agree with the validator. The segment is not a real-world advertisement. Match the same not-an-ad criteria the main reviewer uses:
- A guest discussing their own work in the context of the interview
- Host organically mentioning their own other shows or social media in conversation
- Brand names mentioned in passing as part of editorial topic discussion
- Comedic bits or fictional sponsor reads in the show's creative content
- Silence, pauses, or topic transitions with no promotional transcript content

WHEN IN DOUBT: Agree with the validator and return empty. Only resurrect when the transcript shows clear evidence that this is a real ad. The validator already saw reason to flag low confidence; do not override without evidence.

OUTPUT FORMAT:
Return ONLY a valid JSON array. No explanation, no markdown.

Each resurrected ad: {{"start": FLOAT_SECONDS, "end": FLOAT_SECONDS, "confidence": FLOAT_0_TO_1, "reason": "brief description"}}

ALL values for "start", "end", and "confidence" MUST be numeric (float). Never use strings like "high", "low", "medium", or percentages like "95%". Examples: "start": 45.0, "end": 82.0, "confidence": 0.95

EXAMPLE - RESURRECT (validator missed a real ad):
Validator-rejected segment: 666.7s - 674.3s (validator confidence 0.71)
[660.0s] So that's our take on the antitrust case.
[666.7s] Hosted on Acast. See acast dot com slash privacy for more information.
[674.5s] Welcome back, today we're talking about the Switch 2 launch.

Output: [{{"start": 666.7, "end": 674.3, "confidence": 0.92, "reason": "Acast hosting platform post-roll, clearly promotional and not editorial"}}]

EXAMPLE - KEEP REJECTED (validator was right):
Validator-rejected segment: 200.0s - 215.0s (validator confidence 0.65)
[195.0s] We've been talking about Apple's new privacy framework.
[200.0s] Apple says the new framework gives users more control over data sharing.
[215.0s] But critics argue Apple still has too much power over the app store.
[220.0s] Let's get into the developer reaction next.

Output: []{sponsor_database}"""


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
