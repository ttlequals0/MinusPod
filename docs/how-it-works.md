# How It Works & Detection Pipeline

[< Docs index](README.md) | [Project README](../README.md)

---

## How It Works

1. **Transcription** - Whisper converts audio to text with timestamps (local GPU via faster-whisper, or remote API via OpenAI-compatible endpoint)
2. **Ad Detection** - An LLM analyzes the transcript to identify ad segments, with an automatic verification pass
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

See detailed sections below for configuration and usage.

### Verification Pass

After the first pass detects and removes ads, a verification pipeline runs on the processed audio, unless the feed has "Skip verification pass" set (see [Skip Verification Pass](#skip-verification-pass)):

1. **Re-transcribe** - The processed audio is re-transcribed on CPU using Whisper
2. **Audio Analysis** - Volume analysis and transition detection run on the processed audio
3. **LLM Detection** - A "what doesn't belong" prompt detects any remaining ad content
4. **Audio Enforcement** - Programmatic signal matching validates and extends detections
5. **Re-cut** - If missed ads are found, the pass 1 output is re-cut directly

Each detected ad shows a badge indicating which stage found it:
- **First Pass** (blue) - Found during first pass detection
- **Audio Enforced** (orange) - Found by programmatic audio signal matching
- **Verification** (purple) - Found by the post-cut verification pass

The verification model can be configured separately from the first pass model in Settings.

A pass-2 detection that overlaps an already-accepted ad is folded into the recut at step 5 above. A **standalone miss** - one that overlaps no pass-1 marker - goes through a separate confidence gate instead of being silently discarded:

- Above the autocut floor (Settings > Ad Detection, off by default): cuts automatically, the same as any other gated cut.
- Otherwise, once it clears the hold floor (default 0.60): holds for review with hold reason `verification_miss`, shown as a "Verification catch" chip in [Held for Review](#held-for-review).
- Below the hold floor: dropped, with a log line naming what was dropped and why. Never silent.

See [Configuration > Detection Tuning](configuration.md#detection-tuning) for both floors.

### Sliding Window Processing

For long episodes, transcripts are processed in overlapping windows:

- **Window Size** - how much transcript each detection request covers (default 10 minutes)
- **Overlap** - trailing overlap between windows so ads at boundaries aren't missed (default 3 minutes)
- **Deduplication** - Ads detected in multiple windows are automatically merged

At the defaults a 60-minute episode is processed as 9 overlapping windows, with duplicate detections merged. The window size and overlap are both configurable; see [detection window geometry](configuration.md#detection-window-geometry) for the ranges and when to lower them.

### Processing Queue

To prevent memory issues from concurrent processing, episodes are processed one at a time:

- Only one episode processes at a time (Whisper + FFmpeg are memory-intensive)
- Processing runs in a background thread, so the UI stays responsive
- Episodes stuck in "processing" status reset automatically on server restart
- View and cancel processing episodes in Settings

When you request an episode that needs processing:
1. If nothing is processing, it starts in the background and returns HTTP 503 with `Retry-After: 30`
2. If another episode is currently processing, it returns HTTP 503 with `Retry-After: 30`
3. If the queue is busy and the episode gets queued behind another, it returns HTTP 503 with `Retry-After: 60`
4. Once processed, subsequent requests serve the stored file directly from disk

HEAD requests (sent by podcast apps like Pocket Casts during feed refresh) proxy headers from the upstream audio source without triggering processing. This prevents feed refreshes from flooding the processing queue.

Separately from episode processing, MinusPod polls every feed's upstream RSS on a schedule (default every 15 minutes, configurable 5-1440 minutes) to discover new episodes. An opt-in Podping listener can refresh a single feed immediately when its host announces a new episode over the Podping notification bus, ahead of the next scheduled poll; the schedule keeps running either way, so it stays the fallback for feeds whose host doesn't send Podping. See [Podcasting 2.0 > Podping](podcasting-2.0.md#podping) and [Configuration > Feed Refresh and Podping](configuration.md#feed-refresh-and-podping).

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
- **REJECT** - Too short/long, low confidence, or missing ad signals - kept in audio

Rejected ads appear in a separate "Rejected Detections" section in the UI so you can verify the validator's decisions.

#### Held for Review

A fourth outcome is **held for review**. An ad is held when one of these rules blocks the automatic cut:

- **Max ad duration** - per-feed rule, off by default (feed settings page). The detection exceeds the feed's cap, even if the model was highly confident.
- **Cue gating** - per-feed rule, off by default. The feed has cue-gated approval on and the detection has no audio-cue evidence. Manual markers are exempt from cue gating (the duration cap still applies to them). On cue-gated feeds, verification-pass (pass 2) proposals are always held because they cannot carry cue evidence.
- **Standalone verification-pass miss** - global tunable (Settings > Ad Detection, default 0.60 confidence). A pass-2 detection overlapping no pass-1 marker clears the verification-miss hold floor but not the (off-by-default) autocut floor. Shown with a "Verification catch" chip. See [Verification Pass](#verification-pass).
- **Uncorroborated cross-fetch differential region** - global tunable. The two fetches measurably differ, but no other stage, overlap, or matched audio cue backs the region as an ad. See [Cross-Fetch Differential](#cross-fetch-differential).

Held ads stay in the audio. The episode publishes with them intact. The episode page shows held ads in an amber "Held for Review (N)" section with Approve & Recut and Dismiss buttons. Approve & Recut stores a confirm correction and immediately re-cuts via the Recut Audio mode (no LLM re-run) if the original audio is still retained; without it, the button reads Approve and the cut applies on the next reprocess. Dismiss records a rejection and leaves the audio unchanged. The episode list shows an "N held" chip on any episode with held ads.

The API returns held ads as `pendingReviewMarkers` on the episode detail response; episode entries carry a `pendingReviewCount` (see `openapi.yaml`).

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

### Real-Time Processing Status

A global status bar shows real-time processing progress via Server-Sent Events. It displays the current episode title, processing stage (Transcribing, Detecting Ads, Processing Audio), a progress bar, and queue depth. Click it to navigate to the processing episode.

### Chapter Generation

When enabled (Settings > Transcripts & Chapters > Generate Chapters, on by default), MinusPod writes Podcasting 2.0 chapters for each episode after the ads are cut. What it writes depends on the feed's chapter mode (Feed Settings > Chapters). Auto, the default, keeps the podcast's own embedded chapters, remapped onto the cut audio, and only generates when fewer than two of them survive the cut. Always generate produces AI chapters regardless. Off writes no chapters for episodes processed after the switch. On the generate path, an LLM finds topic transitions in the transcript, anchors them to any timestamps the show lists in its description, and titles each chapter, keeping them at least three minutes apart. Chapters are served two ways: as a `podcast:chapters` JSON file, and embedded in the MP3 itself as ID3 frames for players like Castro that only read embedded chapters. Re-running chapters from the episode page updates both. The Chapters Model in Settings picks the model; a small model like Haiku works well.

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

Two more passes can run on pass 1's ads after the reviewer step, both before the audio is cut. The terminal-start snap runs whenever there are cuts with splice evidence, whether or not the [ad reviewer](configuration.md#ad-reviewer) is enabled; tail completion only runs when the reviewer is enabled. A reviewer "adjust" log line is therefore not always an ad's final boundary: either sweep can still move it before the cut happens.

A terminal-start snap pulls a terminal cut's start to the nearest deep-silence splice point. A tail completion sweep pushes a cut's end forward again when the transcript right after the reviewer-adjusted end still reads as ad content: a sponsor name, a URL, or a call-to-action. Tail completion exists specifically to counteract the reviewer pulling an end back to, or inside, the detector's original boundary and stranding a trailing CTA in the cut audio. It only runs when the reviewer is enabled; without a reviewer, the boundary-extension pass earlier in the pipeline already does this work.

Both sweeps update the persisted marker and the active cut list together, so the two never drift apart. An adjusted marker's `reviewer_original_start` / `reviewer_original_end` fields hold the pre-adjust detector bounds, and its live `start` / `end` reflect whichever sweep touched it last: the `terminal_snap` and `tail_completed` flags note which ones ran.

### Segment Categories

**Opt-in, two ways. Upgrading changes nothing by itself:**

- Every category defaults to remove. A feed cuts exactly as it did before this feature until someone edits that feed's action map, or the global default map, to set a category to keep or beep.
- Intro, outro, and recap are only detected when a feed turns on the **Detect intro, outro, and housekeeping segments** toggle on its settings page (per feed, off by default). Sponsor, cross-promo, self-promo, and interaction are always detected regardless of that toggle. Setting intro to keep on a feed that has not enabled the toggle has no effect, since no intro marker is ever produced.

If you previously worked around the lack of intro/outro handling by editing the global first-pass prompt override to force intro or outro segments to be treated as removable ads, remove that override now. The override is global and applies to every feed, so it keeps forcing intro/outro out even on a feed whose category actions say keep, fighting the per-feed system described below.

Every detected marker carries a category: what kind of content the span is, not just whether it is an ad. Sponsor covers paid host-read or produced reads, dynamic ad insertion, and platform pre/post-rolls; cross-promo covers other-show and network promos; self-promo covers Patreon, merch, and subscribe/donate asks for the show itself; interaction covers follow/rate/review prompts. These four are always detected. Intro, outro, and recap (show intro or theme, outro and credits, "coming up" or "listen next" housekeeping) are detected only when a feed opts in via the **Detect intro, outro, and housekeeping segments** toggle on its settings page, off by default; boundaries on music-heavy intro/outro segments are approximate, since the detector reads the transcript and music rarely produces one. Fingerprint, text-pattern, and cross-fetch differential detections are not categorized by an LLM and default to sponsor.

Each category resolves to one of three actions: remove (cut, the long-standing behavior), beep (replace the span with a tone, keeping the episode's original duration), or keep (leave the audio untouched). Resolution checks the feed's per-category override first, falls back to the global default map, and falls back to remove if neither sets the category. Configure the global map under Settings > Global Defaults > **Segment actions**, and override it per feed on the feed settings page under the same **Segment actions** heading, where each category starts in an inherit state until you touch it. Every category defaults to remove, so upgrading to this feature changes nothing on its own; the episode page shows each marker's category as a chip, plus a muted "Kept" badge on any marker whose resolved action is keep.

A kept marker is saved with `was_cut = false` and skips the parts of the pipeline that assume something is being cut: it bypasses validator hold rules and reviewer boundary checks (there is nothing to hold or adjust), is dropped from any pass-2 verification finding that overlaps it rather than being re-flagged as a miss, and is excluded from the "Detections Not Cut" count, since choosing to keep a segment is not a miss. Kept markers never create corrections or cross-episode false-positive text, so they cannot poison pattern learning with a "not an ad" signal, but they still feed the pattern learner as detections and carry their category onto the learned pattern, so future matches on the same text keep improving regardless of the action applied to them.

Changing an action map does not retroactively touch already-processed episodes. The feed settings page has a **Re-render episodes with current segment actions** button that re-cuts every processed episode with a retained original against the current per-feed and global maps, using the same recut path as a single-episode recut; a category's action can also be changed after the fact through a normal per-episode recut, which re-resolves actions the same way.

### Cross-Fetch Differential

Dynamically inserted ads (DAI) are spliced into the audio by the publisher's ad server at download time, so two downloads of the same episode can carry different ads, or different amounts of them. The cross-fetch differential exploits that: MinusPod downloads the episode a second time with a different client signature and compares the two copies. Audio that differs between the fetches cannot be part of the show, so each differing region becomes an ad candidate with hard evidence behind it, no transcript reading required.

Every silence-delimited block in the run file is probed against the refetch and carries its own measured correlation. A region only becomes an ad candidate when that correlation is at or below the **Correlation ceiling** setting (default 0.60): a high correlation means the two fetches matched too closely to be a real ad swap, not proof the region is untouched. Qualifying blocks that touch are merged into one candidate span, and a candidate too stale to score cleanly (e.g. after a different-length ad swap shifted the rest of the file) gets one retry with a widened search window before it is judged.

A candidate cuts outright when it overlaps a marker another stage already found. Otherwise it holds for review instead of cutting on differential evidence alone, unless it is shorter than the **Hold minimum length** setting (default 10 seconds, 0 disables the floor), in which case it is dropped instead of surfacing a sub-floor, likely re-roll-noise slice. See [Configuration > Detection Tuning](configuration.md#detection-tuning) for both controls.

On a feed with cue templates configured, a matched audio cue also corroborates a candidate: one whose start or end lands within the normal cue-snap window of a template cue cuts instead of holding, and a candidate bracketed by a break-start/break-end cue pair corroborates the same way. The refetch is scanned for the same template cues as the primary download, and when the same cue is found in both, the offset between the two positions re-anchors the comparison timeline, absorbing fetch-to-fetch timing drift at its source. **Cross-fetch differential detection is significantly more accurate on feeds with cue templates configured**: cues corroborate differential regions, help bound DAI slots, and anchor the comparison timeline. Setting up a cue template (see [Audio Cue Detection](audio-cues.md)) is the recommended first step on any feed with heavy dynamic ad insertion, before tuning the differential thresholds themselves.

The per-feed setting (Feed page > Settings > Cross-fetch diff) has three positions. **Auto** (the default since 2.53.0) runs the stage when the feed looks DAI-served: a detected ad platform, or an episode audio URL that routes through a known DAI prefix domain. **On** always runs it; **Off** never does. The settings panel shows whether the stage currently runs on the feed. The trade-off is bandwidth: every new episode is downloaded twice, which also doubles the feed's download count in the publisher's stats.

Each detection found this way is tagged with the cross-fetch stage in the ad list, and the episode header shows a "Cross-fetch: N inserted" badge when the comparison found differing regions.

Rejecting a differential detection as not an ad, held or not, still blocks that same episode-region from re-surfacing, but no longer seeds cross-episode false-positive text: it was only ever a candidate, never a confirmed false positive from a real detector, so it does not suppress future matching on other episodes of the feed.

### Keep Content Only

Normal detection asks the model to find the ads. Keep content only flips the question: the model marks what is show content, and everything it does not mark is removed. This helps on feeds whose inserted ads never read like ads: cross-promos, host-read spots without sponsor language, or network filler that blends into the show.

Enable it per feed by setting **Processing mode** to Keep content only, at Feed page > Feed Settings. Cuts found this way get a teal "Keep-content" badge in the episode ad list, and they are excluded from text-pattern learning.

Because the mode removes everything unmarked, a labeling mistake cuts real audio. Safety gates check each episode: the marked content must cover at least 55% of the runtime, no more than 45% may be removed in total, and no single cut may exceed 25% of the episode or 7 minutes. If any gate fails, or any transcript window comes back unlabeled, that episode falls back to normal ad removal. The fallback is silent and per-episode, so a feed on this mode can still produce normally-detected episodes.

The mode is experimental: spot-check the first few episodes after enabling it.

### Skip Ad Detection

For shows that run no ads, detection is wasted LLM spend. Setting the per-feed **Processing mode** to Skip ad detection (Feed page > Feed Settings) keeps transcription, transcripts, and chapters but skips every detection stage: no first-pass detection, no verification pass, no audio-cue analysis, no cross-fetch second download. Nothing is cut, so the served audio matches the original. Chapters still make their own LLM call.

This differs from the Pass-through option on the same select, which serves episodes untouched and skips processing entirely: no transcript, no chapters.

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

The verification pass is a second detection sweep over the already-cut audio, so an episode that runs it pays for ad detection twice. On a feed whose first pass is already reliable that is spend for nothing. The per-feed "Skip verification pass" toggle (Feed page > Feed Settings > Advanced) leaves the first pass untouched and declines the second sweep, which roughly halves the ad-detection LLM spend per episode. Chapters and the boundary reviewer make their own LLM calls either way, so the total episode cost drops by less than half.

Two things change beyond the saving. Ads the first pass missed stay in the audio, since nothing re-scans the output. And a held differential detection that pass 2 would have corroborated and auto-approved now waits for you on the review queue instead, so a feed on this setting can accumulate holds that used to clear themselves.

Runs that skipped the pass are labelled "(no verification)" in the episode's run list, and they record no verification result rather than a zero, which would have read as a clean second scan.

The feed settings page exposes standard, keep-content, skip ad detection, pass-through, and cue-only as one **Processing mode** select, so a feed only ever runs one at a time, and each option's hint explains what it changes. The REST API still accepts the underlying per-field flags (`passthroughEnabled`, `skipAdDetection`, `detectionMode`) for external callers; when those are set independently of the select, the same precedence applies as before: pass-through beats skip ad detection, skip ad detection beats keep-content, and keep-content beats cue-only (a feed set to keep-content with skip on detects nothing).

---

[< Docs index](README.md) | [Project README](../README.md)
