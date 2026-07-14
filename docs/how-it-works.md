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
| **Verification Pass** | Post-cut re-detection catches missed ads by re-transcribing processed audio | Automatic |
| **Audio Enforcement** | Volume and transition signals programmatically validate and extend ad detections | Automatic |
| **Pattern Learning** | System learns from corrections, patterns promote from podcast to network to global scope | Automatic |
| **Confidence Thresholds** | >=80% confidence: cut (configurable); 50-79%: kept for review; <50%: rejected | Automatic |

See detailed sections below for configuration and usage.

### Verification Pass

After the first pass detects and removes ads, a verification pipeline runs on the processed audio:

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

A fourth outcome is **held for review**. An ad is held when a per-feed rule blocks the automatic cut (both rules are off by default, set on the feed settings page):

- **Max ad duration** - the detection exceeds the feed's cap, even if the model was highly confident.
- **Cue gating** - the feed has cue-gated approval on and the detection has no audio-cue evidence. Manual markers are exempt from cue gating (the duration cap still applies to them). On cue-gated feeds, verification-pass (pass 2) proposals are always held because they cannot carry cue evidence.

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

**Pattern Management:**
Access the Patterns page from the navigation bar to:
- View all patterns with their scope, sponsor, and statistics
- Filter by scope (Global, Network, Podcast) or search by sponsor name
- Toggle patterns active/inactive
- View confirmation and false positive counts

### Real-Time Processing Status

A global status bar shows real-time processing progress via Server-Sent Events. It displays the current episode title, processing stage (Transcribing, Detecting Ads, Processing Audio), a progress bar, and queue depth. Click it to navigate to the processing episode.

### Chapter Generation

When enabled (Settings > Transcripts & Chapters > Generate Chapters, on by default), MinusPod writes Podcasting 2.0 chapters for each episode after the ads are cut. An LLM finds topic transitions in the transcript, anchors them to any timestamps the show lists in its description, and titles each chapter, keeping them at least three minutes apart. Chapters are served as a `podcast:chapters` JSON file and can be re-run from the episode page. The Chapters Model in Settings picks the model; a small model like Haiku works well.

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

### Cross-Fetch Differential

Dynamically inserted ads (DAI) are spliced into the audio by the publisher's ad server at download time, so two downloads of the same episode can carry different ads -- or different amounts of them. The cross-fetch differential exploits that: MinusPod downloads the episode a second time with a different client signature and compares the two copies. Audio that differs between the fetches cannot be part of the show, so each differing region becomes an ad candidate with hard evidence behind it, no transcript reading required.

The per-feed setting (Feed page > Settings > Cross-fetch diff) has three positions. **Auto** (the default since 2.53.0) runs the stage when the feed looks DAI-served -- a detected ad platform, or an episode audio URL that routes through a known DAI prefix domain. **On** always runs it; **Off** never does. The settings panel shows whether the stage currently runs on the feed. The trade-off is bandwidth: every new episode is downloaded twice, which also doubles the feed's download count in the publisher's stats.

Each detection found this way is tagged with the cross-fetch stage in the ad list, and the episode header shows a "Cross-fetch: N inserted" badge when the comparison found differing regions.

---

[< Docs index](README.md) | [Project README](../README.md)
