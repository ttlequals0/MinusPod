# How It Works & Detection Pipeline

[< Docs index](README.md) | [Project README](../README.md)

---

## Contents

- [How It Works](#how-it-works)
- [Advanced Features (Quick Reference)](#advanced-features-quick-reference)

One episode across every pipeline stage, then the learning loop and what happens when a stage fails. Portrait cut and the file itself in [Videos](videos/README.md).

https://github.com/user-attachments/assets/cf3d38d7-3df6-4e8d-bac1-c5dd720dc4b6

## How It Works

1. **Transcription** - audio is converted to text with timestamps by faster-whisper or Parakeet (local, GPU or CPU), or by a remote OpenAI-compatible API
2. **Ad Detection** - An LLM analyzes the transcript to identify ad segments, with an automatic verification pass. With the provider set to None, learned patterns and audio fingerprints do this instead; see [Running without an LLM](llm-providers.md#running-without-an-llm)
3. **Audio Processing** - FFmpeg removes detected ads and inserts short audio markers
4. **Serving** - Flask serves modified RSS feeds and processed audio files

Processing happens on-demand when you play an episode, or automatically when new episodes appear. An episode is processed once; processing time depends on episode length, hardware, and chosen models. After processing, the output is stored on disk and served directly on subsequent plays.

## Advanced Features (Quick Reference)

| Feature | Description | Enable In |
|---------|-------------|-----------|
| **Verification Pass** | Post-cut re-detection catches missed ads by re-transcribing processed audio | Automatic, per-feed opt-out |
| **Audio Enforcement** | Volume and transition signals programmatically validate and extend ad detections | Automatic |
| **Pattern Learning** | System learns from corrections, patterns promote from podcast to network to global scope | Automatic |
| **Confidence Thresholds** | >=80% confidence: cut (configurable); 50-79%: kept for review; <50%: rejected | Automatic |
| **Keep Content Only** | Inverted detection: the model marks show content and the rest is removed, guarded by safety gates | Feed page > Feed Settings > Processing mode |
| **Skip Ad Detection** | Transcripts and chapters only; no detection LLM calls, nothing cut | Feed page > Feed Settings > Processing mode |
| **Skip Verification Pass** | First pass still detects and cuts; the post-cut second sweep does not run | Feed page > Feed Settings > Advanced |
| **Transcript Diff** | Diffs the Whisper transcript against the publisher's ad-free transcript; gaps corroborate or hold, never auto-cut | Automatic, per-feed opt-out |

See detailed sections below for configuration and usage.

### Verification Pass

After the first pass detects and removes ads, a verification pipeline runs on the processed audio, unless the feed has "Skip verification pass" set (see [Skip Verification Pass](#skip-verification-pass)):

1. **Re-transcribe** - The processed audio is re-transcribed with the active transcription backend
2. **Audio Analysis** - Volume analysis and transition detection run on the processed audio
3. **LLM Detection** - A "what doesn't belong" prompt detects any remaining ad content
4. **Audio Enforcement** - Programmatic signal matching validates and extends detections
5. **Re-cut** - If missed ads are found, the pass 1 output is re-cut directly

Each detected ad shows a badge indicating which stage found it:
- **First Pass** (blue) - Found during first pass detection
- **Audio Enforced** (orange) - Found by programmatic audio signal matching
- **Verification** (purple) - Found by the post-cut verification pass

The verification model can be configured separately from the first pass model in Settings.

A pass-2 detection that overlaps an already-accepted ad is folded into the recut at step 5 above. A **standalone miss** (one that overlaps no pass-1 marker) goes through a separate confidence gate instead of being silently discarded:

- Above the autocut floor (Settings > Ad Detection, off by default): cuts automatically, the same as any other gated cut.
- Otherwise, once it clears the hold floor (default 0.60): holds for review with hold reason `verification_miss`, shown as a "Verification catch" chip in [Held for Review](#held-for-review).
- Below the hold floor: dropped, with a log line naming what was dropped and why.

A third case is a pass-2 finding that lands on a span pass 1 deliberately kept. The keep reflects your segment-action map, so pass 2 never cuts through it, but the finding is not discarded: it holds for review with hold reason `verification_kept_conflict`.

See [Configuration > Detection Tuning](configuration.md#detection-tuning) for both floors.

### Sliding Window Processing

For long episodes, transcripts are processed in overlapping windows:

- **Window Size** - how much transcript each detection request covers (default 10 minutes)
- **Overlap** - trailing overlap between windows so ads at boundaries aren't missed (default 3 minutes)
- **Deduplication** - Ads detected in multiple windows are automatically merged

At the defaults a 60-minute episode is processed as 9 overlapping windows, with duplicate detections merged. The window size and overlap are both configurable; see [detection window geometry](configuration.md#detection-window-geometry) for the ranges and when to lower them.

### Processing Queue

Processing admission protects memory and provider capacity:

- One episode processes at a time by default. A remote Whisper pool can raise the episode limit.
- Within an episode, chunk preparation overlaps transcription: ffmpeg cuts
  and normalizes upcoming chunks while the GPU transcribes the current one
  (see [Chunked transcription](transcription.md#chunked-transcription))
- Processing runs in a background thread, so the UI stays responsive
- Each run has an immutable ID, owner process identity, and heartbeat in SQLite
- Only the owner of that run can publish results, update its status, or release its slot
- A dead owner is recovered at startup or before new work is admitted; its episode and queue row return to pending without a retry penalty
- An optional [processing schedule](configuration.md#processing-schedule) limits new starts to a weekly window. Outside it the dispatcher claims nothing and queued episodes wait; runs already started finish
- View the active episode and the full waiting queue in Settings > Processing
  Queue, listed in the order the worker will claim them, and cancel any of them
  there

When you request an episode that needs processing:
1. If nothing is processing, it starts in the background; if another episode is currently processing, it does not. Either way it returns HTTP 503 with `Retry-After: 30`
2. If the queue is busy and the episode gets queued behind another, it returns HTTP 503 with `Retry-After: 60`
3. Once processed, subsequent requests serve the stored file directly from disk

HEAD requests (sent by podcast apps like Pocket Casts during feed refresh) proxy headers from the upstream audio source without triggering processing. This prevents feed refreshes from flooding the processing queue.

Cancellation is stored in SQLite, so any gunicorn worker can request it. The owner checks that state between steps and before publishing. The API returns HTTP 202 if the owner is still stopping after 2 seconds. Feed and episode deletion use the same ownership checks, and an older run cannot publish over a successor.

Separately from episode processing, MinusPod polls every feed's upstream RSS on a schedule (default every 15 minutes, configurable 5-1440 minutes) to discover new episodes. An opt-in Podping listener can refresh a feed ahead of the next poll. See [Configuration > Feed Refresh and Podping](configuration.md#feed-refresh-and-podping) and [Podcasting 2.0 > Podping](podcasting-2.0.md#podping).

### Post-Detection Validation

After ad detection, a validation layer reviews each detection before audio processing:

- **Duration checks** - Rejects ads outside configurable duration limits
- **Confidence thresholds** - Rejects very low confidence detections; only cuts ads above the minimum confidence threshold (adjustable in Settings)
- **Position heuristics** - Boosts confidence for typical ad positions (pre-roll, mid-roll, post-roll)
- **Transcript verification** - Checks for sponsor names and ad signals in the transcript
- **Auto-correction** - Merges ads with tiny gaps, clamps boundaries to valid range

Ads are classified as:
- **ACCEPT** - High confidence, removed from audio
- **REVIEW** - Medium confidence, removed but flagged for review
- **REJECT** - Too short/long, low confidence, or missing ad signals; kept in audio

Rejected ads appear in a separate "Rejected Detections" section in the UI so you can verify the validator's decisions.

#### Held for Review

A fourth outcome is **held for review**. An ad is held when one of these rules blocks the automatic cut:

- **Max ad duration** - per-feed rule, off by default (feed settings page). The detection exceeds the feed's cap, even if the model was highly confident.
- **Cue gating** - per-feed rule, off by default. The feed has cue-gated approval on and the detection has no audio-cue evidence. Manual markers are exempt from cue gating (the duration cap still applies to them). On cue-gated feeds, verification-pass (pass 2) proposals are always held because they cannot carry cue evidence.
- **Standalone verification-pass miss** - global tunable (Settings > Ad Detection, default 0.60 confidence). A pass-2 detection overlapping no pass-1 marker clears the verification-miss hold floor but not the (off-by-default) autocut floor. Shown with a "Verification catch" chip. See [Verification Pass](#verification-pass).
- **No splice evidence** - global rule with a per-feed override. A cut of 60 seconds or more from the detector or a learned pattern is held unless the audio shows an edit point near one of its edges. That can be a DAI transition pair, an ad-break cue template, a volume step of 12 dB or more, a splice event, or an overlapping differential region. Intro and outro cues never count, since they mark the show rather than a break. The rule applies only once the feed's splice calibration says `calibrated`, which takes five episodes of stored history. What matters is whether the ad was joined into the audio. An ad recorded separately and edited in leaves an edit point; one spoken straight through in a single take does not, so a feed whose ads are never joined in has every long cut held. Turn the check off for that feed on its settings page. See [Splice check](configuration.md#splice-check).
- **Uncorroborated cross-fetch differential region** - global tunable. The two fetches measurably differ, but no other stage, overlap, or matched audio cue backs the region as an ad. See [Cross-Fetch Differential](#cross-fetch-differential).
- **Uncorroborated transcript differential gap** - global tunable, per-feed override. A block of speech is missing from the publisher's transcript, but no other stage backs the region as an ad. See [Transcript Differential](#transcript-differential).
- **Reviewer boundary conflict** - a reviewer proposed moving inside protected evidence from a merged candidate. The original span stays in the audio and the proposed boundaries appear for manual review.

Held ads stay in the audio and the episode publishes with them intact. Each such episode also fires the Episode Pending Review notification event. Approve & Recut re-cuts via the Recut Audio mode (no LLM re-run) when the original audio is retained; Dismiss leaves the audio unchanged. The buttons and the "N held" chip are described in [Web Interface > Held for Review](web-interface.md#held-for-review).

The API returns held ads as `pendingReviewMarkers` on the episode detail response; episode entries carry a `pendingReviewCount` (see `openapi.yaml`).

### Partial Detection

If the first-pass LLM call fails with a transient, non-auth error, but pattern matching and cross-fetch differential evidence already produced cuts, the episode does not fail outright: it publishes with those cuts alone. The episode page shows an amber "Partial detection" badge (hover for the failure reason) and a warning banner with a **Re-run detection** button. One automatic low-priority re-detect is also queued for the episode, so most partial episodes correct themselves without you doing anything; it runs once, and does not re-queue itself if it degrades again. Verification (pass 2) still runs afterward when the feed's settings call for it, since only pass 1 failed. The API exposes this as `partialDetection` on the episode detail response, with the failure reason and the detection window counts (`windowsFailed`/`windowsTotal`) from the run that produced the served audio.

An episode fails normally, rather than publishing partial results, when pattern and cross-fetch evidence produced no cuts at all, or the failure was auth-related (invalid key, forbidden, unauthorized).

### Pattern Learning

When an ad is detected and validated, text patterns are extracted and stored for future matching.

**Pattern Hierarchy:**
- **Global Patterns** - Match across all podcasts (e.g., common sponsors like Squarespace, BetterHelp)
- **Network Patterns** - Match within a podcast network (TWiT, Relay FM, Gimlet, etc.)
- **Podcast Patterns** - Match only for a specific podcast

When processing new episodes, the system first checks for known patterns before sending to the LLM. Patterns with high confirmation counts and low false positive rates are matched with high confidence.

**Pattern Sources:**
- **Audio Fingerprinting** - Identifies DAI-inserted ads using Chromaprint acoustic fingerprints
- **Text Pattern Matching** - TF-IDF similarity and fuzzy matching against learned patterns
- **LLM Analysis** - Falls back to AI analysis for uncovered segments

**Fingerprint matching limits:**
A fingerprint match is measured at one alignment. Where the audio stops agreeing for longer than the gap tolerance setting, the match is split, and each piece must pass the coverage and evidence checks on its own. A short dropout inside an ad is bridged. An ad with a damaged stretch longer than the gap tolerance is found as shorter pieces, or not found at all. When two stored ads overlap in an episode, the stronger match takes the overlap (a full match always beats a partial one) and the other keeps what is left if that is at least the minimum ad length. These limits were measured in testing:
- On heavily compressed audio, a match edge can stop a few seconds before the ad's real edge. MinusPod does not stretch it to a nearby LLM edge, because that would bring back cuts that run into show audio. Leaving a little ad in is the safer mistake.
- When only part of a stored ad is in the episode, a match edge rarely runs into show audio, and then only by a few seconds. This was rare in synthetic tests.
- When an ad and the show share a music bed or other steady audio, the ad's pattern can also match show audio elsewhere in the episode. A match must agree clearly better than the same stretch shifted a few seconds either way. With that check, a false cut of this kind was still seen in a small share of synthetic test ad breaks.
- A pattern taken from near-uniform audio can be stored and then missed. In synthetic tests, a few stored patterns were not found in a copy with some bits flipped.
- A false match on unrelated audio is rare. These limits were observed in synthetic tests. A real library has not been measured yet.
- The match strictness setting starts at 0.65. Raising it to about 0.70 changes little.

**Defined vs. Auto-Learned Patterns:**
Patterns come in two trust tiers. A pattern is **defined** when a person created it (manual "create", confirm, or adjust) or it came from a synced community pattern; a pattern the system learned on its own from a detection is **auto-learned**. A defined pattern always cuts its matched segment, overriding whatever action the matched category resolves to under [Segment Categories](#segment-categories) below, including a feed's late keep safety net. An auto-learned pattern still respects segment actions for its own category and can be kept or beeped like any other detection. A remove-category marker with real evidence (a fingerprint match, a sponsor text pattern, a sponsor with an active pattern, a confirmation or a reviewer verdict) owns any audio it shares with a keep-category marker. The keep is trimmed around it, kept audio never carves the cut, and a verification catch for a known sponsor is not dropped for sitting inside a kept span. The distinction also governs merges: when a defined pattern's match overlaps an auto-learned one, the defined pattern owns the merged span (the absorbed pattern keeps credit for its own matches), and the pass-1 sponsor hint sent to the detection LLM includes a category and an opening snippet for defined patterns but only a name for auto-learned ones.

To keep the same text from being learned twice, learning a near-identical match updates the existing pattern instead of inserting a duplicate.

**User Corrections:**
In the ad editor, you can confirm, reject, or adjust detected ads:
- **Confirm** - Creates/updates patterns in the database, incrementing confirmation count
- **Adjust Boundaries** - Corrects start/end times for an ad; also creates patterns from adjusted boundaries (like confirm), so the learned pattern text matches the corrected range
- **Mark as Not Ad** - Flags as false positive and stores the transcript text. Similar text is automatically excluded in future episodes of the same podcast using TF-IDF similarity matching (cross-episode false positive learning)

Confirm, Adjust, and the manual "create" flow all check the marked text for
multiple ad-transition phrases (e.g. two sponsors read back to back) and split
it into one pattern per sponsor instead of a single contaminated pattern.

**Pattern Management:**
Access the Patterns page from the navigation bar to:
- View all patterns with their scope, sponsor, and statistics
- Filter by scope (Global, Network, Podcast) or search by sponsor name
- Toggle patterns active/inactive
- View confirmation and false positive counts
- Split a pattern that already covers multiple sponsors into one pattern per
  sponsor from its detail view

### Audio fingerprints

An audio fingerprint is a Chromaprint signature of a known ad. It lives on a pattern, next to the pattern's text, and finds the same ad in a new episode by sound alone, with no transcript and no LLM call. This is the main detector when the LLM provider is [None](llm-providers.md#running-without-an-llm).

**Where a fingerprint comes from.** One is stored when a person or a trusted detector sets an ad's edges on an episode whose original audio is still retained (the global or per-feed "Keep original audio" setting):

| Source | Result |
|--------|--------|
| LLM or SponsorBlock detection that creates a pattern | A fingerprint of the detected span |
| Add ad in the ad editor | A fingerprint of the marked span, one per sponsor if the text splits |
| Split of a pattern, by hand or in Pattern Cleanup | Each new pattern gets a fingerprint of its own span of the original's source episode |
| Trim in Pattern Cleanup | The fingerprint is rebuilt from the trimmed text's span |
| Confirm | A fingerprint, only if the pattern has none |
| Adjust | The fingerprint is rebuilt from the new edges |
| Add audio to pattern, on a text-pattern detection | A fingerprint of the detection's span as shown |
| Community or imported pattern | None until one of the rows above adds it |

Storing is best effort. The pattern or correction is saved either way, and the response says whether audio was stored and, if not, why (no retained original, span shorter than the minimum ad length, too little distinctive audio to ever match, an existing fingerprint, or a failure). Once you delete a pattern's audio, the automatic rows above do not create it again. Adjust and Add audio to pattern do.

**How a match is measured.** For each active fingerprint in scope, the matcher finds where it lines up with the episode and measures the whole run of agreeing audio. The match reports its measured start and end, similarity (how closely the audio agrees, 0 to 100%) and coverage (the share of the stored ad that was found). It counts only when similarity and coverage reach their settings and enough close agreement backs it. A run with at least 20 seconds of agreeing audio also counts below the coverage setting, as a partial match. Ads air in groups, so a stored fingerprint often holds two or three ads of one break, and another episode may air only one of them. Short shared openings, such as a tagline or a disclaimer, stay under 20 seconds and are still held to the coverage setting. Unrelated real audio can agree for a few seconds, so a very short or low-detail fingerprint that could not pass those checks is refused when it is stored rather than left unable to match. The marker then spans the measured edges, not the stored length.

**Scope.** Audio follows the pattern's scope exactly as text does: a podcast pattern matches only that podcast, a network pattern only podcasts on that network, and a global pattern everywhere. A network pattern uses the feed's network override when one is set. Matches an older version made outside a pattern's scope are flagged in its history, so you can widen the scope on purpose if you want them.

**Controls on a pattern.** Open a pattern on the Patterns page and use its Audio section:

- **Audio on or off.** Stops or resumes matching for the fingerprint. Text matching and the pattern's active state are not affected, and disabling the whole pattern still silences both.
- **Play.** Plays the source audio. It needs a retained original of the episode the fingerprint was built from or of a recent match. When none is left, the section says "Source audio expired". The fingerprint keeps matching and becomes playable again after its next match.
- **Edit edges.** Opens the source episode with the fingerprint's span as the only region. Moving the edges and saving rebuilds the fingerprint. The episode's own markers and cuts are not changed.
- **Delete audio.** Removes the fingerprint and its match history and keeps the pattern.
- **Add audio to pattern.** For a pattern with no fingerprint whose marker still has a retained original, builds one from that marker.
- **Recent matches.** Each match with its episode, position, similarity, how much of the ad it covered, and whether only audio found it. **Not this ad** on a row sends the existing reject correction for that episode and marks the match, so it stops counting toward the pattern's matches and mean similarity. A reject does not turn audio off by itself.

If you raise the minimum coverage setting so far that a stored fingerprint could no longer match, the section says "This audio cannot match under the current settings".

The Patterns table has an Audio column ("91% match", "Audio off", "No source audio" or "-"), the Stats page has an Audio matching section (patterns with audio, audio matches, found by audio only, marked Not this ad), and the settings are under Settings > AI and processing > Audio fingerprinting, described in [Configuration](configuration.md#audio-fingerprinting). Merging or deduplicating patterns moves a fingerprint and its history to the pattern that is kept. Deleting a pattern or feed deletes them.

"Found by audio only" means no other detection stage contributed to that marker. Match history is written from an episode's final markers, so an ad the validator rejected is not a match. Reprocessing replaces an episode's rows and keeps those marked Not this ad. A match row is deleted with its episode, so a pattern's match count, mean similarity and last match reflect only episodes that still exist. An episode whose audio merely expired keeps its rows.

The routes behind all of this are listed in [API and Webhooks](api-and-webhooks.md).

### Real-Time Processing Status

A global status bar polls `GET /api/v1/status` every 2 seconds and backs off to 30 seconds after failures. The response comes from shared status and SQLite state, so any gunicorn worker can answer it. The finite 30-second SSE route remains for compatible clients. The bar shows active jobs, stage, progress, queue depth, provider holds, offline waits, and the processing schedule when it is holding queued episodes.

### Chapter Generation

When enabled (Settings > Transcripts & Chapters > Generate Chapters, on by default), MinusPod writes Podcasting 2.0 chapters for each episode after the ads are cut. What it writes depends on the feed's chapter mode (Feed Settings > Chapters). Auto, the default, keeps surviving ID3 publisher chapters, including a single chapter, and checks feed-provided chapter JSON before falling back to generation. Always generate produces AI chapters regardless. Off writes no chapters for episodes processed after the switch. On the generate path, an LLM finds topic transitions in the transcript, anchors them to any timestamps the show lists in its description, and titles each chapter, keeping them at least three minutes apart. Chapters are served two ways: as a `podcast:chapters` JSON file, and embedded in the MP3 itself as ID3 frames for players like Castro that only read embedded chapters. Re-running chapters from the episode page updates both. Timestamps written in the episode description, such as "12:34 Topic" lines, are shifted by the same cuts in the served feed, so they still point at the right moment; clock times like "10:00 AM" are left alone. The Chapters Model in Settings picks the model; a small model like Haiku works well.

Regenerate chapters starts on demand in a background thread, outside the processing queue. Each attempt records its own result, usage, and episode log when log storage is enabled. It respects provider rate-limit holds and refuses to start while the episode is already processing or regenerating chapters.

### Reprocessing Modes

You can re-run detection on an episode without re-fetching it, in any of four modes: Reprocess, Full Analysis, Recut Audio, and Re-detect Ads. See [Reprocessing](configuration.md#reprocessing) for what each one does and which are available as bulk feed actions.

### Audio Analysis

Audio analysis runs automatically on every episode (lightweight, uses only ffmpeg):

- **Volume Analysis** - Detects loudness anomalies using EBU R128 measurement. Identifies sections mastered at different levels than the content baseline.
- **Transition Detection** - Finds abrupt frame-to-frame loudness jumps that indicate dynamically inserted ad (DAI) boundaries. Pairs up/down transitions into candidate ad regions.
- **Audio Enforcement** - After LLM detection, uncovered audio signals with ad language in the transcript are promoted to ads. DAI transitions with high confidence (>=0.8) or sponsor matches are also promoted. Existing ad boundaries are extended when signals partially overlap.
- **Audio Cue Templates** - When a feed has a learned cue template (a marked ding or stinger), an MFCC matcher finds that exact sound across the episode and snaps a detected ad's edges to the nearest high-confidence cue, capped by the reviewer's max boundary shift. The cue never cuts on its own. See [Audio Cue Detection](audio-cues.md) for setup, cue types, and the opt-in cue-pair option.

### Nearby-Ad Merge

Within a single ad break, individual spots are sometimes separated by brief transition music or silence rather than actual show content. The nearby-ad merge pass collapses those filler gaps so the whole break is cut as one span.

The gap is measured in speech content from the transcript, not wall-clock time. Two ads merge when the speech between them falls below the **Ad break filler gap threshold** (Settings > Ad Detection; default 12 seconds). Music, silence, and untranscribed regions count for nothing. Set the threshold to 0 to disable.

A 5-minute safety cap prevents merging when the resulting span would exceed it, regardless of how little speech is in the gap. A merge is also skipped when either ad or the merged span overlaps a user false-positive correction, so a marked "not an ad" range keeps its say in the validator. Audio-cue evidence on the merged ads is carried onto the combined span.

### Boundary Sweeps After the Reviewer

Two more passes can run on pass 1's ads after the reviewer step, both before the audio is cut. The terminal-start snap runs whenever there are cuts with splice evidence, whether or not the [ad reviewer](configuration.md#ad-reviewer) is enabled, while tail completion needs it. A reviewer "adjust" log line is therefore not always an ad's final boundary: either sweep can still move it before the cut happens.

A terminal-start snap pulls a terminal cut's start to the nearest deep-silence splice point. A tail completion sweep pushes a cut's end forward again when the transcript right after the reviewer-adjusted end still reads as ad content: a sponsor name, a URL, or a call-to-action. Tail completion exists specifically to counteract the reviewer pulling an end back to, or inside, the detector's original boundary and stranding a trailing CTA in the cut audio. It only runs when the reviewer is enabled; without a reviewer, the boundary-extension pass earlier in the pipeline already does this work.

Both sweeps update the persisted marker and the active cut list together, so the two never drift apart. An adjusted marker's `reviewer_original_start` / `reviewer_original_end` fields hold the pre-adjust detector bounds, and its live `start` / `end` reflect whichever sweep touched it last: the `terminal_snap` and `tail_completed` flags note which ones ran.

### Segment Categories

**Opt-in, two ways:**

- Every category defaults to remove. A feed cuts every category until someone edits that feed's action map, or the global default map, to set one to keep, mark, or beep.
- Intro, outro, and recap are only detected when show-segments detection is on for a feed: a per-feed Inherit/On/Off choice that falls back to the global default when set to Inherit (the feed's default). Sponsor, cross-promo, self-promo, and interaction are always detected regardless of that setting. Setting intro to keep on a feed where show-segments detection resolves to off has no effect, since no intro marker is ever produced.

Every detected marker carries a category: what kind of content the span is, not just whether it is an ad. The categories and what each covers are in [Configuration > Segment categories](configuration.md#segment-categories). Boundaries on music-heavy intro/outro segments are approximate, since the detector reads the transcript and music rarely produces one. Fingerprint and text-pattern detections inherit the matched pattern's stored category; cross-fetch differential detections carry none. Uncategorized detections default to sponsor. A pattern's category can be set when it is created (manual ad creation or import) and edited later on the pattern detail modal or via `PUT /api/v1/patterns/{id}`, which matters because a pattern categorized as keep-resolving (say, cross-promo on a feed that keeps cross-promos) protects every span it matches.

Each category resolves to one of four actions: remove, beep, keep, or mark. Mark requires chapters to be enabled; see [Ad chapters](podcasting-2.0.md#ad-chapters). The actions, resolution order and the **Segment actions** cards are in [Configuration > Segment categories](configuration.md#segment-categories). The episode page shows each marker's category as a chip.

When LLM detections are trimmed against pattern-matched regions, only remove-resolving regions count as coverage, so a keep-resolving pattern match never shadows an overlapping remove detection.

A kept or marked marker is saved with `was_cut = false` and skips the parts of the pipeline that assume something is being cut: it bypasses validator hold rules and reviewer boundary checks (there is nothing to hold or adjust), is dropped from any pass-2 verification finding that overlaps it rather than being re-flagged as a miss, and is excluded from the "Detections Not Cut" count, since choosing to keep a segment is not a miss. Kept and marked markers never create corrections or cross-episode false-positive text, so they cannot poison pattern learning with a "not an ad" signal, but they still feed the pattern learner as detections and carry their category onto the learned pattern, so future matches on the same text keep improving regardless of the action applied to them.

Changing an action map does not retroactively touch already-processed episodes. The **Re-render episodes with current segment actions** button (see [Configuration > Segment categories](configuration.md#segment-categories)) uses the same recut path as a single-episode recut, which also re-resolves actions.

### Cross-Fetch Differential

Dynamically inserted ads (DAI) are spliced into the audio by the publisher's ad server at download time, so two downloads of the same episode can carry different ads, or different amounts of them. The cross-fetch differential exploits that: MinusPod downloads the episode a second time with a different client signature and compares the two copies. Audio that differs between the fetches cannot be part of the show, so each differing region becomes an ad candidate with hard evidence behind it, without reading the transcript.

Every silence-delimited block in the run file is probed against the refetch and carries its own measured correlation. A region only becomes an ad candidate when that correlation is at or below the **Correlation ceiling** setting (default 0.60): a high correlation means the two fetches matched too closely to be a real ad swap, not proof the region is untouched. Qualifying blocks that touch are merged into one candidate span, and a candidate too stale to score cleanly (e.g. after a different-length ad swap shifted the rest of the file) gets one retry with a widened search window before it is judged.

A candidate cuts outright when it overlaps a marker another stage already found. Otherwise it holds for review instead of cutting on differential evidence alone, unless it is shorter than the **Hold minimum length** setting (default 10 seconds, 0 disables the floor), in which case it is dropped instead of surfacing a sub-floor, likely re-roll-noise slice. See [Configuration > Detection Tuning](configuration.md#detection-tuning) for both controls.

On a feed with cue templates configured, a matched audio cue also corroborates a candidate: one whose start or end lands within the normal cue-snap window of a template cue cuts instead of holding, and a candidate bracketed by a break-start/break-end cue pair corroborates the same way. The refetch is scanned for the same template cues as the primary download, and when the same cue is found in both, the offset between the two positions re-anchors the comparison timeline, absorbing fetch-to-fetch timing drift at its source. **Cross-fetch differential detection is significantly more accurate on feeds with cue templates configured**: cues also help bound DAI slots. Set one up first (see [Audio Cue Detection](audio-cues.md)) on any feed with heavy dynamic ad insertion, before tuning the differential thresholds.

The global setting (Settings > Global Defaults > Cross-fetch diff) has three positions. **Auto**, the default, runs when MinusPod detects an ad platform or an audio URL on a known DAI prefix domain. **On** always runs it; **Off** never does. New feeds and feeds set to Inherit use that choice; an explicit feed setting overrides it. The panel shows whether the stage currently runs on the feed. The trade-off is bandwidth: every new episode is downloaded twice, which also doubles the feed's download count in the publisher's stats.

Each detection found this way is tagged with the cross-fetch stage in the ad list, and the episode header shows a "Cross-fetch: N inserted" badge when the comparison found differing regions.

Rejecting a differential detection as not an ad, held or not, still blocks that same episode-region from re-surfacing, but does not seed cross-episode false-positive text: it was only ever a candidate, never a confirmed false positive from a real detector, so it does not suppress future matching on other episodes of the feed.

### Transcript Differential

When a feed's `podcast:transcript` tag points at a transcript made before the
ads were spliced in, MinusPod diffs its own Whisper transcript against it:
any block of speech the publisher's copy omits is a candidate ad, with no
audio refetch required. A found gap releases its own hold when another
stage's detection covers at least half of the gap. A detection that a gap
covers for at least half of its own length is also marked corroborated and
gains confidence. A gap nothing releases holds for review on its own, tagged
"Upstream transcript omits this span". It never cuts by itself. See [Upstream Transcript Differential](transcript-differential.md)
for the full mechanism, thresholds, and settings.

### Keep Content Only

Normal detection asks the model to find the ads. Keep content only flips the question: the model marks what is show content, and everything it does not mark is removed. This helps on feeds whose inserted ads never read like ads: cross-promos, host-read spots without sponsor language, or network filler that blends into the show.

Enable it per feed by setting **Processing mode** to Keep content only, at Feed page > Feed Settings. Cuts found this way get a teal "Keep-content" badge in the episode ad list, and they are excluded from text-pattern learning.

Because the mode removes everything unmarked, a labeling mistake cuts real audio. Safety gates check each episode: the marked content must cover at least 55% of the runtime, no more than 45% may be removed in total, and no single cut may exceed 25% of the episode or 7 minutes. If any gate fails, or any transcript window comes back unlabeled, that episode falls back to normal ad removal. The fallback is silent and per-episode, so a feed on this mode can still produce normally-detected episodes.

The mode is experimental: spot-check the first few episodes after enabling it.

### Skip Ad Detection

For shows that run no ads, detection is wasted LLM spend. Setting the per-feed **Processing mode** to Skip ad detection (Feed page > Feed Settings) keeps transcription, transcripts, and chapters but skips every detection stage: no first-pass detection, no verification pass, no audio-cue analysis, no cross-fetch second download. Nothing is cut, so the served audio matches the original. Chapters still make their own LLM call.

This differs from the Pass-through option on the same select, which skips processing entirely: no transcription or ad removal, so no transcript and no chapters. Pass-through still serves through MinusPod, and audio may be transcoded for serving (see [Configuration > Pass-through mode](configuration.md#pass-through-mode)).

### Cue-Only Mode

Cue-only is experimental, since it can cut audio no language model has read.
It cuts from cue pairs and previously learned ad patterns, with no LLM
reading the transcript. Fingerprint, text-pattern, and cross-fetch
differential detection still run and can cut on their own; the LLM
detection pass, the LLM boundary reviewer, the verification pass, and LLM
redetection are all off. It requires at least one enabled ad-break-start and
one enabled ad-break-end audio cue template on the feed. See
[Audio Cue Detection > Cue-only preset](audio-cues.md#cue-only-preset) for
the template requirement, the per-feed safety policy, the bootstrap
workflow, and the transcription toggle.

### Skip Verification Pass

The verification pass is a second detection sweep over the already-cut audio, so an episode that runs it pays for ad detection twice. On a feed whose first pass is already reliable that is spend for nothing. Settings > Global Defaults decides whether verification runs. New feeds and feeds set to Inherit use that choice; Feed Settings > Advanced can set an explicit override. Skipping verification leaves the first pass untouched and declines the second sweep, which roughly halves the ad-detection LLM spend per episode. Chapters and the boundary reviewer make their own LLM calls either way, so the total episode cost drops by less than half.

Two things change beyond the saving. Ads the first pass missed stay in the audio, since nothing re-scans the output. And a held differential detection that pass 2 would have corroborated and auto-approved waits for you on the review queue instead, so a feed on this setting can accumulate holds that pass 2 would have cleared.

Runs that skipped the pass are labelled "(no verification)" in the episode's run list, and they record no verification result rather than a zero, which would have read as a clean second scan.

The five processing modes are one **Processing mode** select; see [Episode Processing Workflows](workflows.md#processing-modes). When the REST API's underlying flags (`passthroughEnabled`, `skipAdDetection`, `detectionMode`) are set independently, pass-through beats skip ad detection, skip ad detection beats keep-content, and keep-content beats cue-only (a feed set to keep-content with skip on detects nothing).

---

[< Docs index](README.md) | [Project README](../README.md)
