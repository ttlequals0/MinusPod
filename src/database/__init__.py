"""SQLite database package for MinusPod."""
import os
import sqlite3
import threading
import time
import logging
from pathlib import Path

from database.schema import SchemaMixin
from database.podcasts import PodcastMixin
from database.episodes import EpisodeMixin
from database.settings import SettingsMixin
from database.patterns import PatternMixin
from database.sponsors import SponsorMixin
from database.stats import StatsMixin
from database.maintenance import MaintenanceMixin
from database.fingerprints import FingerprintMixin
from database.cue_templates import CueTemplateMixin
from database.cue_detections import CueDetectionMixin
from database.queue import QueueMixin
from database.search import SearchMixin
from database.auth_lockout import AuthLockoutMixin
from database.podping_hosts import PodpingHostMixin
from database.feed_subscribers import FeedSubscriberMixin
from database.downloads import DownloadHistoryMixin
from database.provider_admission import ProviderAdmissionMixin
from database.upload_reservations import UploadReservationMixin
from database.failover_events import FailoverEventsMixin
from database.pattern_cleanup import PatternCleanupMixin
from utils.paths import resolve_data_dir

logger = logging.getLogger(__name__)

# Slow statements and transactions are logged at this threshold.
# Implicit transaction timing includes the opening statement's lock wait.
SLOW_SQLITE_SECONDS = 5.0

# How long a writer waits for the lock. 30s left almost no margin: ordinary
# feed-refresh writes were observed holding it 28.4s under contention.
BUSY_TIMEOUT_MS = 60000
_sqlite_metrics_lock = threading.Lock()
_sqlite_metrics = {
    'slowStatements': 0,
    'slowCommits': 0,
    'failedCommits': 0,
    'longTransactions': 0,
    'lastCommitMs': 0.0,
    'maxCommitMs': 0.0,
}


def sqlite_metrics_snapshot() -> dict:
    with _sqlite_metrics_lock:
        return {'scope': 'worker_process', 'processId': os.getpid(), **_sqlite_metrics}


def _record_sqlite_metric(name: str, value: float | None = None) -> None:
    with _sqlite_metrics_lock:
        if value is None:
            _sqlite_metrics[name] += 1
        else:
            _sqlite_metrics['lastCommitMs'] = value
            _sqlite_metrics['maxCommitMs'] = max(_sqlite_metrics['maxCommitMs'], value)
            if value >= SLOW_SQLITE_SECONDS * 1000:
                _sqlite_metrics['slowCommits'] += 1


class TracedConnection(sqlite3.Connection):
    """sqlite3.Connection that logs slow statements and long transactions."""

    _tx_started = None
    _tx_opener = None
    _tx_start_includes_wait = False

    def execute(self, sql, *args):
        return self._traced(super().execute, sql, *args)

    def executemany(self, sql, *args):
        return self._traced(super().executemany, sql, *args)

    def _traced(self, run, sql, *args):
        was_in_tx = self.in_transaction
        started = time.monotonic()
        try:
            return run(sql, *args)
        except Exception:
            # A statement that opened the transaction and then failed would
            # leave it open on this thread with nothing to roll it back (#566).
            if not was_in_tx and self.in_transaction:
                super().rollback()
            raise
        finally:
            self._note_statement(sql, started, was_in_tx)

    def commit(self):
        tx_started = self._tx_started
        tx_opener = self._tx_opener
        started = time.monotonic()
        try:
            super().commit()
        except Exception:
            _record_sqlite_metric('failedCommits')
            raise
        else:
            _record_sqlite_metric('commit', (time.monotonic() - started) * 1000)
            self._note_transaction_end('commit', tx_started, tx_opener)

    def rollback(self):
        tx_started = self._tx_started
        tx_opener = self._tx_opener
        try:
            super().rollback()
        finally:
            self._note_transaction_end('rollback', tx_started, tx_opener)

    def __exit__(self, exc_type, exc_value, traceback):
        if exc_type is None:
            try:
                self.commit()
            except BaseException:
                try:
                    self.rollback()
                except Exception:
                    pass
                raise
        else:
            self.rollback()
        return False

    def _note_statement(self, sql, started, was_in_tx):
        elapsed = time.monotonic() - started
        if elapsed >= SLOW_SQLITE_SECONDS:
            _record_sqlite_metric('slowStatements')
            logger.warning(
                "SQLite statement took %.1fs on thread %s: %s",
                elapsed, threading.current_thread().name, _sql_head(sql))
        if not self.in_transaction:
            # Autocommit, or a C-level commit (`with conn:`, executescript) ended it.
            self._tx_started = self._tx_opener = None
            self._tx_start_includes_wait = False
        elif not was_in_tx:
            # BEGIN IMMEDIATE may spend most of its time waiting for the lock.
            normalized = str(sql).lstrip().upper()
            immediate = normalized.startswith('BEGIN IMMEDIATE')
            self._tx_started = time.monotonic() if immediate else started
            self._tx_start_includes_wait = not immediate
            self._tx_opener = _sql_head(sql)
        elif self._tx_opener and self._tx_opener.split(None, 1)[0].upper() == 'BEGIN':
            # Name the first real statement instead of the BEGIN that opened it.
            self._tx_opener = _sql_head(sql)

    def _note_transaction_end(self, how, tx_started=None, tx_opener=None):
        if tx_started is None:
            return
        held = time.monotonic() - tx_started
        if held >= SLOW_SQLITE_SECONDS:
            _record_sqlite_metric('longTransactions')
            if self._tx_start_includes_wait:
                logger.warning(
                    "SQLite transaction elapsed %.1fs (opening statement and possible lock wait included) before %s on thread %s; opened by: %s",
                    held, how, threading.current_thread().name, tx_opener)
            else:
                logger.warning(
                    "SQLite write transaction held %.1fs before %s on thread %s; opened by: %s",
                    held, how, threading.current_thread().name, tx_opener)
        self._tx_started = None
        self._tx_opener = None
        self._tx_start_includes_wait = False


def _sql_head(sql):
    return truncate(' '.join(str(sql).split()), 120)

from utils.constants import DEFAULT_SYSTEM_PROMPT  # re-exported for backward compat
from utils.text import truncate

# Verification pass prompt - runs on processed audio to catch missed ads
DEFAULT_VERIFICATION_PROMPT = """
You are a JSON API for segmenting podcast transcripts.

Your job is to identify every segment in a given transcript according to the rules defined below. The transcript may contain incorrect words (especially company and brand names), misheard phrases, and incomplete sentences. Use the podcast metadata and surrounding context to infer the correct meaning.

IMPORTANT: This is the SECOND-PASS over the transcript. Ads and other unwanted segments found in the first-pass have already been removed. However, the first-pass may have missed some segments, in-whole or in-part. Be especially vigilant for AD FRAGMENTS, such as:
- Orphaned or out-of-place URLs
- Orphaned promo codes: "use code X for", "code X at checkout"
- Orphaned calls to action: "link in the show notes", "check it out at", "sign up at"
- Trailing sponsor mentions: "that's [brand].com", "thanks to [sponsor]"
- Ad bumpers and in/out cues that survived the first pass.

The transcript is presented in one of two formats:
- Timestamped: Each cue contains its start and end time offset, in seconds, e.g. `[12.3s - 14.5s] Text`.
- Indexed: Each cue contains its zero-based index, e.g. `[123] Text`.

SEGMENTATION RULES:
- Build a continuous chain of topical segments from the first cue to the last. No gaps.
- Start a new segment immediately when the topic changes.
- Each segment must be categorized according to the rules defined below.

CATEGORIES:
- `main_content`: The core value proposition of the episode, including cold open, narrative, story telling, commentary, interview, Q&A, etc.
- `intro`: Opening theme, host welcome and introductions. (Note: cold open / story intro is `main_content`.)
- `outro`: Sign-off, credits, closing theme. (Note: a closing conversation or storyteller bio is `main_content`.)
- `interaction`: Calls to action (like, review, comment).
- `teaser`: Previews of upcoming content; hook for the audience to stay tuned.
- `recap`: Summary of prior episodes, e.g. "previously on…" (Note: an end of episode summary is `main_content`.)
- `transition`: Musical or narrative interlude bridging segments. Prefer one of the other content categories when possible.
- `sponsor`: Ads or promotions unrelated to the podcast or its network, including trailing disclaimers.
- `cross_promo`: Promos for sister podcasts or the podcast network (Acast, Spotify, etc).
- `self_promo`: Promos for the podcast itself, including Patreon, merch, tours, etc.

The `sponsor`, `cross_promo`, and `self_promo` categories are collectively referred to as "promotional" categories. The others are non-promotional or "content" categories.

CONSTRAINTS:
- DO NOT output `main_content` segments. The caller will infer them from the gaps between other segments.
- Promotional segments: Create one segment per promotion per instance.
- Cues may occasionally contain content from two distinct segments. For example, an ad may start or end mid cue. When this happens, include the cue in BOTH segments.

FALSE POSITIVE PREVENTION GUIDELINES:
- Organic brand mentions or product discussions stay in `main_content` unless explicit and prolonged promotional language is used.
- Passing mentions of sponsors, URLs, or social handles stay in `main_content` unless sustained and directed at the audience.
- Guest plugs stay in `main_content`.
- When unsure if a segment is `main_content` or not, assume that it is and adjust confidence accordingly.

ORPHANED FRAGMENTS:
Partial segments leftover from the first pass cut may be difficult to categorize accurately. Do your best and know that orphaned fragments will be merged into their parent segment, making the categorization of these fragments less critical.

{sponsor_database}

OUTPUT FORMAT:
As a JSON API, you must return exactly one JSON object that conforms to the following schema.

Response shape:
{
  "segments": [ segment1, segment2, … ]
}

Segment shape:
{
  "start": number,
  "end": number,
  "category": enum,
  "confidence": number,
  "reason": string or null,
  "sponsor_name": string or null,
  "start_text": string or null
  "end_text": string or null
}

Field rules:
- `segments`: An ordered array of detected segments, excluding `main_content` segments.
- `start`: The index or start time (depending on cue format) of the first cue in the segment.
- `end`: The index or end time of the last cue in the segment.
- `category`: The segment category as defined above.
- `confidence`: A number between 0.0 and 1.0 indicating how confident you are that start, end, and category are accurate.
- `reason`: A *short* explanation for why the segment was categorized as such.
- `sponsor_name`: The named sponsor (advertiser/brand/company) or product in a promotional segment, null otherwise.
- `start_text`: The first 5 words of ad copy in a promotional segment, null otherwise. If the ad copy is missing or unclear, use an empty string.
- `end_text`: Similar to `start_text` except the final 5 words of ad copy.

<!--
EXAMPLE: Partial transcript (with timestamps) and response:
[1.6s - 47.0s] Thanks for joining us today… Let's take a quick break.
[48.5s - 52.0s] Ready to start a healthier routine?
[52.5s - 78.0s] AG1 is the daily foundational nutrition supplement… Go to athleticgreens.com/podcast.
[78.5s - 82.0s] That's athleticgreens.com/podcast.
[82.5s - 86.0s] Now, back to our conversation.
…
[512.0s - 514.5s] Before we get back to it, a quick note.
[514.5s - 528.0s] Hey, it's Jamie from Tech Weekly. If you like this show, check out our other podcast Startup Stories for interviews with founders every Tuesday.
[528.0s - 531.0s] Now, back to today's episode.

RESPONSE: 
{ "segments": [
    {"start": 1.6, "end": 47.0, "confidence": 0.97, "category": "intro", "reason": "Welcome and throw to break", "sponsor_name": null, "start_text": null, "end_text": null},
    {"start": 48.5, "end": 86.0, "confidence": 0.95, "category": "sponsor", "reason": "Athletic Greens sponsor read, out cue", "sponsor_name": "Athletic Greens", "start_text": "Ready to start a healthier routine?", "end_text": "That's athleticgreens.com/podcast"},
    …
    {"start": 512.0, "end": 531.0, "confidence": 0.92, "category": "cross_promo", "reason": "Cross-promotion for sister podcast and in/out cues", "sponsor_name": "Startup Stories", "start_text": "Hey, it's Jamie from Tech Weekly.", "end_text": "interviews with founders every Tuesday."}
  ]
}
-->

REMINDERS:
- Merge consecutive segments with the same category and topic.
- DO NOT include `main_content` segments.
- Output compact JSON only. No Markdown, code fences, or explanatory text. Only JSON.
- Use only those fields described above.
- Omit (or assign null) to fields which are not applicable, such as `sponsor_name`, `start_text`, and `end_text` for non-promotional segments.
- Ensure the output is valid, parseable JSON.
""".strip()


# Both reviewer prompts use placeholder substitution via _render_prompt;
# never .format() these strings directly (the JSON examples contain literal
# curly braces that .format() would attempt to interpolate).
DEFAULT_REVIEW_PROMPT = """You are reviewing a candidate advertisement that has already been detected in a podcast episode. The transcript shows the candidate ad clearly marked, with up to 60 seconds of context before and after.

Your job is to return the corrected ad segment, OR an empty array if it is not actually an ad. Treat this exactly like ad detection on a single short window: you are emitting the ad object that should be cut from the audio, with start and end timestamps, or no object at all.

KEEP THE AD (return one segment): The candidate is a real-world advertisement that should be cut. Use the original start and end if they are already correct. Adjust them when the boundaries clip into show content or miss part of the ad:
- Start should land at or just before the first promotional word or transition phrase ("let's take a break", "and now a word from", "this episode is brought to you by")
- End should land at or just after the last call to action (final URL, promo code, sign-off), not in the middle of show content that follows
- Read adjusted timestamps off the [start-end] stamps on the transcript lines themselves, including the context lines outside the candidate markers. Never interpolate a boundary
- Adjusted boundaries must stay within {max_boundary_shift_seconds} seconds of the original boundaries in either direction

PARTIAL SPAN: if any part of the candidate span is not ad content (show content before the ad starts or after it ends), you MUST return adjusted start and end timestamps covering only the ad portion. Never return the original boundaries and describe the trim only in the "reason" text: the reason is prose for a human, and only the start and end numbers control the cut.

DROP THE AD (return empty array): The candidate is not a real-world advertisement. Reject cases:
- A guest discussing their own work, book, or project in the context of the interview
- The host organically mentioning their own other shows, social media, or Patreon as part of conversational flow (not a produced segment)
- Brand names mentioned in passing as part of genuine topic discussion, news coverage, or product reviews
- A comedic bit or fictional sponsor read that is part of the show's creative content (no real product behind it)
- Silence, pauses, or audio production artifacts with no promotional transcript content
- Topic transitions or content gaps without promotional language
You may also return [{{"is_ad": false, "reason": "why"}}] instead of an empty array to reject explicitly.

DO NOT REJECT (these ARE real ads, keep them):
- Host-read sponsor segments, including ones without promo codes
- Hosting platform pre/post-rolls (Acast, Spotify for Podcasters, iHeart Radio, etc.)
- Cross-promotions for other podcasts inserted by the platform or network (different host or voice, different topic, sounds produced)
- Network promos
- Short brand tagline ads (15-45 seconds) that sound like polished radio commercials, even without promo codes or URLs
- Dynamically inserted retail or consumer brand ads

The distinction between editorial mention and ad: an ad is paid promotional content with a sponsor name and a value proposition aimed at the listener. Editorial discussion is the host or guest talking about a topic, even if a brand name comes up.

AUDIO CUE SIGNALS: if the prompt notes a labelled audio cue next to the candidate boundary, treat it as ground truth for that side and do not move the boundary across it. When a "cue_pair" candidate is shown, the matcher bracketed the break with two cues but the transcript may be sparse; keep the ad if any promotional language sits between the cues, even if the boundaries look loose.

Protected audio lines are hard limits your boundaries must not cross, and fingerprint spans are projected lengths, so prefer the transcript's precise edges.

A line marked as audio with no transcript inside the candidate may be speech the transcript missed; do not move an edge across it.

WHEN IN DOUBT: Keep the ad with original boundaries unchanged. Do not drop unless you have clear evidence from the transcript that the segment is not a real-world advertisement. Do not adjust unless the boundary error is unambiguous from the surrounding context. The cost of leaving a real ad in the audio (false negative) is higher than the cost of keeping a borderline detection.

OUTPUT FORMAT:
Return ONLY a valid JSON array. No explanation, no markdown.

Each kept ad: {{"is_ad": BOOLEAN_TRUE_ONLY_IF_REAL_AD, "start": FLOAT_SECONDS, "end": FLOAT_SECONDS, "confidence": FLOAT_0_TO_1, "reason": "brief description"}}

Set "is_ad" true only when the span is a real advertisement to cut. Set it false, or return an empty array, when it is not.

ALL values for "start", "end", and "confidence" MUST be numeric (float). Never use strings like "high", "low", "medium", or percentages like "95%". Examples: "start": 45.0, "end": 82.0, "confidence": 0.95

EXAMPLE - KEEP UNCHANGED (boundaries are correct):
Original boundaries: 1245.00s - 1320.50s.
[1240.0s-1245.0s] We will pick that thread back up in a minute.
>>> CANDIDATE AD START [1245.0s] >>>
[1245.0s-1248.0s] This episode is brought to you by BetterHelp.
[1248.0s-1315.0s] BetterHelp is the largest online therapy platform...
[1315.0s-1320.5s] Visit betterhelp.com slash podcast. That's betterhelp.com slash podcast.
<<< CANDIDATE AD END [1320.5s] <<<
[1320.5s-1325.0s] Anyway, back to what we were talking about.

Output: [{{"is_ad": true, "start": 1245.0, "end": 1320.5, "confidence": 0.95, "reason": "Confirmed BetterHelp host-read sponsor with clean boundaries"}}]

EXAMPLE - ADJUST BOUNDARIES (start was late, end was early):
Original boundaries: 100.00s - 130.00s.
[92.0s-95.0s] So that wraps up our discussion. Let's take a quick break.
[95.0s-100.0s] This episode is brought to you by Athletic Greens.
>>> CANDIDATE AD START [100.0s] >>>
[100.0s-128.0s] AG1 is the daily foundational nutrition supplement...
[128.0s-130.0s] Go to athleticgreens.com slash podcast.
<<< CANDIDATE AD END [130.0s] <<<
[130.0s-132.0s] That's athleticgreens.com slash podcast.
[132.0s-135.0s] Now, back to our conversation.

Both adjusted timestamps are read straight off context lines: 95.0 starts the line before the marker, 132.0 ends the line after it.

Output: [{{"is_ad": true, "start": 95.0, "end": 132.0, "confidence": 0.92, "reason": "Started at the transition line at 95.0s; ended at 132.0s after the repeated URL"}}]

EXAMPLE - DROP (host mentioning a brand editorially, not an ad):
Original boundaries: 50.00s - 70.00s.
[45.0s-50.0s] Have you been following the Apple antitrust case?
>>> CANDIDATE AD START [50.0s] >>>
[50.0s-62.0s] The DOJ argued that Apple's app store policies harm developers.
[62.0s-70.0s] They want the court to force a change to the commission structure.
<<< CANDIDATE AD END [70.0s] <<<
[70.0s-74.0s] What's your take on the proposed remedies?

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
You may also return [{{"is_ad": false, "reason": "why"}}] instead of an empty array to reject explicitly.

AUDIO CUE SIGNALS: if a labelled audio cue brackets or sits next to the rejected segment, treat it as strong evidence a break happened there; a cue-bracketed segment with even a single sponsor mention is usually a real ad the validator was too cautious about -- resurrect it.

WHEN IN DOUBT: Agree with the validator and return empty. Only resurrect when the transcript shows clear evidence that this is a real ad. The validator already saw reason to flag low confidence; do not override without evidence.

OUTPUT FORMAT:
Return ONLY a valid JSON array. No explanation, no markdown.

Each resurrected ad: {{"is_ad": BOOLEAN_TRUE_ONLY_IF_REAL_AD, "start": FLOAT_SECONDS, "end": FLOAT_SECONDS, "confidence": FLOAT_0_TO_1, "reason": "brief description"}}

Set "is_ad" true only when the span is a real advertisement to cut. Set it false, or return an empty array, when it is not.

ALL values for "start", "end", and "confidence" MUST be numeric (float). Never use strings like "high", "low", "medium", or percentages like "95%". Examples: "start": 45.0, "end": 82.0, "confidence": 0.95

EXAMPLE - RESURRECT (validator missed a real ad):
Validator-rejected segment: 666.7s - 674.3s (validator confidence 0.71)
[660.0s] So that's our take on the antitrust case.
[666.7s] Hosted on Acast. See acast dot com slash privacy for more information.
[674.5s] Welcome back, today we're talking about the Switch 2 launch.

Output: [{{"is_ad": true, "start": 666.7, "end": 674.3, "confidence": 0.92, "reason": "Acast hosting platform post-roll, clearly promotional and not editorial"}}]

EXAMPLE - KEEP REJECTED (validator was right):
Validator-rejected segment: 200.0s - 215.0s (validator confidence 0.65)
[195.0s] We've been talking about Apple's new privacy framework.
[200.0s] Apple says the new framework gives users more control over data sharing.
[215.0s] But critics argue Apple still has too much power over the app store.
[220.0s] Let's get into the developer reaction next.

Output: []{sponsor_database}"""


# Chapter topic-detection prompt. Rendered via utils.prompt.render_prompt with
# the block placeholders below; must stay byte-identical to the pre-setting
# f-string output when unset.
DEFAULT_CHAPTER_PROMPT = """Analyze this podcast transcript segment and identify {num_splits} major topic changes.

The segment runs from {segment_start} to {segment_end}.

For each topic change, provide the timestamp (from the [MM:SS] markers) and a short title (3-7 words).

OUTPUT FORMAT:
Return ONLY topic lines, one per line. No introduction, no explanation, no numbering.
Each line must be exactly: MM:SS Topic Title Here

Example:
05:30 Discussion of AI Trends
12:45 New Product Announcements

Only include clear topic transitions, not minor tangents. Skip the very beginning since that's already a chapter.{continuation_block}{description_block}{hints_block}

Transcript:
{transcript}"""


# Pattern cleanup system prompt (Experiments > Pattern cleanup). The user
# message carries pattern text, sponsor, category, and optional transcript context.
DEFAULT_PATTERN_CLEANUP_PROMPT = """You review one learned podcast segment pattern. Its text may include unrelated words before or after the intended segment.

Classify the intended segment first, then check its content.
- Preserve content that belongs to an intro, outro, recap, self-promotion, or listener interaction. Those categories are not contamination by themselves.
- For non-sponsor segments, use action "keep" with a category correction when needed. Do not invent a sponsor or force the segment into sponsor-copy trimming rules.
- For sponsor reads, retain only the exact sponsor copy. Drop host banter, unrelated show content, and transitions into or out of the break.
- If two or more sponsors are read back to back, return each read as a separate piece.
- Name the sponsor as it is spoken in the read. For a trim, also return sponsor when the recorded sponsor is wrong and the corrected name appears in the kept text.
- Return category when the recorded category is wrong. Use only sponsor, cross_promo, self_promo, interaction, intro, outro, or recap.
- Do not invent, reword, or reorder words. Every text you return must be copied from the pattern text.
- If the pattern is already clean, return action "keep".
- Set contaminated to true when the pattern holds unrelated main-show discussion that a trim cannot fix, and say why in contamination_reason.

Transcript context, when given, shows the audio around the pattern with the pattern marked between [[ and ]]. Use it only to judge where the intended segment starts and ends; never copy words from outside the pattern text.

Return one JSON object:
{"action": "keep" | "trim" | "split" | "rename", "text": string or null (trim only), "sponsor": string or null (rename, or optional with trim), "category": string or null (optional category correction), "pieces": [{"text": string, "sponsor": string, "category": string or null}] (split only; category optional per piece), "contaminated": boolean, "contamination_reason": string or null, "confidence": number from 0 to 1, "reasons": [short strings]}"""


class Database(SchemaMixin, PodcastMixin, EpisodeMixin, SettingsMixin,
               PatternMixin, SponsorMixin, StatsMixin, MaintenanceMixin,
               FingerprintMixin, CueTemplateMixin, CueDetectionMixin,
               QueueMixin, SearchMixin, AuthLockoutMixin, PodpingHostMixin,
               FeedSubscriberMixin, ProviderAdmissionMixin,
               UploadReservationMixin, FailoverEventsMixin, PatternCleanupMixin,
               DownloadHistoryMixin):
    """SQLite database manager with thread-safe connections."""

    _instance = None
    _lock = threading.Lock()

    def __new__(cls, data_dir: str = None):
        """Singleton pattern for database instance."""
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance

    def __init__(self, data_dir: str = None):
        if self._initialized:
            return

        # Resolved at call time, not from an import-time constant: a bare
        # Database() after the singleton is reset must land in the configured
        # data dir, not the packaged default.
        self.data_dir = Path(data_dir or resolve_data_dir())
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.data_dir / "podcast.db"
        self._local = threading.local()
        self._initialized = True

        # Initialize schema
        self._init_schema()

        # Run migration if needed
        self._migrate_from_json()

        try:
            conn = self.get_connection()
            ap_count = conn.execute(
                "SELECT COUNT(*) FROM ad_patterns WHERE is_active = 1"
            ).fetchone()[0]
            ks_count = conn.execute(
                "SELECT COUNT(*) FROM known_sponsors WHERE is_active = 1"
            ).fetchone()[0]
            logger.debug(
                f"Pattern catalog: ad_patterns active={ap_count}, "
                f"known_sponsors active={ks_count}"
            )
        except Exception as e:
            logger.warning(f"Pattern catalog count failed: {e}")

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
                timeout=BUSY_TIMEOUT_MS / 1000,
                factory=TracedConnection,
            )
            self._local.connection.row_factory = sqlite3.Row
            self._local.connection.execute(f"PRAGMA busy_timeout = {BUSY_TIMEOUT_MS}")
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
        def __init__(self, conn, immediate=False):
            self.conn = conn
            self.immediate = immediate
        def __enter__(self):
            if self.immediate:
                if self.conn.in_transaction:
                    # BEGIN IMMEDIATE cannot nest; a transaction open here is a leak.
                    logger.warning("Rolled back a leaked transaction before BEGIN IMMEDIATE on thread %s",
                                   threading.current_thread().name)
                    self.conn.rollback()
                self.conn.execute("BEGIN IMMEDIATE")
            return self.conn
        def __exit__(self, exc_type, exc_val, exc_tb):
            if exc_type is None:
                self.conn.commit()
            else:
                self.conn.rollback()
            return False

    def transaction(self, immediate: bool = False):
        """Context manager for database transactions.

        Usage:
            with db.transaction() as conn:
                conn.execute("INSERT ...")
                conn.execute("UPDATE ...")
            # Auto-commits on success, auto-rolls back on exception

        immediate=True issues BEGIN IMMEDIATE so the write lock is taken
        up front, where busy_timeout applies. The default deferred begin
        upgrades to a write lock at the first DML statement, and that
        upgrade fails instantly with "database is locked" when the
        snapshot is stale (SQLITE_BUSY_SNAPSHOT is not retried by
        busy_timeout). Use it for multi-statement writes that can run
        alongside other writers (issue #566).

        Do not hold one across per-row Python work: a transaction spanning a
        whole feed's episodes held the write lock past every other writer's
        busy_timeout. Chunk the rows and take a transaction per chunk.
        """
        return self._TransactionContext(self.get_connection(), immediate=immediate)

    def rollback_open_transaction(self) -> bool:
        """Roll back a transaction left open on this thread's connection.

        Returns True when there was one. Connections are thread-local and
        live for the worker thread's lifetime, so a write path that raises
        between BEGIN and commit without rolling back would otherwise hold
        its lock forever and fail every later write on the thread with
        "database is locked" (issue #566). Does not create a connection.
        """
        conn = getattr(self._local, 'connection', None)
        if conn is not None and conn.in_transaction:
            conn.rollback()
            return True
        return False

    def clear_leaked_transaction(self, log, where: str) -> None:
        """Logged, never-raising rollback_open_transaction. Called from the
        guard points that bound a leaked transaction's lifetime: request
        teardown, background loop iterations, and the episode processing
        thread."""
        try:
            if self.rollback_open_transaction():
                log.warning(f"Rolled back a leaked transaction ({where})")
        except Exception as e:
            log.error(f"Leaked-transaction rollback failed ({where}): {e}")
