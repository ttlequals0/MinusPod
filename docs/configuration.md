# Configuration & Experiments

[< Docs index](README.md) | [Project README](../README.md)

---

## Configuration

All configuration is in the web UI or REST API. No config files needed.

### Adding Feeds

1. Open `http://your-server:8000/ui/`
2. Click "Add Feed"
3. Enter the podcast RSS URL
4. Optionally set a custom slug (URL path)

### Ad Detection Settings

Customize ad detection in Settings:
- **LLM Provider** - Switch between Anthropic (direct API), OpenRouter, Ollama (local), or OpenAI-compatible endpoints at runtime without restarting the container
- **AI Model** - Model for first pass ad detection
- **Verification Model** - Separate model for the post-cut verification pass
- **Chapters Model** - Model for chapter generation (a small model like Haiku works well here)
- **Audio Bitrate** - Output bitrate for processed audio (default 128k)
- **System Prompts** - Customizable prompts for first pass and verification detection
- **Ad break filler gap threshold** - ads in the same break separated by less than this many seconds of speech are merged into one cut. Default 12 seconds. Set to 0 to disable. Merges that would exceed 5 minutes total are skipped. See [Nearby-Ad Merge](how-it-works.md#nearby-ad-merge)
- **LLM Tunables** - See below

### Detection Tuning

Settings > Ad Detection has two grouped subsections for tuning how aggressively the verification pass and the cross-fetch differential stage act on what they find. All six controls are database settings; API: `PUT /api/v1/settings/ad-detection` (see `openapi.yaml`).

**Verification pass** - governs standalone catches: ads pass 2 finds that pass 1 missed and that overlap no pass-1 marker.

| Control | Default | Range | Notes |
|---|---|---|---|
| Hold floor | 0.60 | 0.0 - 1.0 | Confidence a standalone verification catch must reach to hold for review. Below it, the catch is dropped and logged instead of surfacing. |
| Autocut | off (0) | 0.5 - 1.0, or off | When enabled, cuts a standalone catch automatically once it reaches this confidence, instead of holding it for review. Off by default, so catches only ever hold or drop. |
| Pattern-learning floor | 0.85 | 0.5 - 1.0 | Minimum confidence before a detection can teach the pattern matcher a new sponsor. Applies to ads up to 90 seconds. |
| Pattern-learning floor, long ads | 0.92 | 0.5 - 1.0 | Same floor for ads longer than 90 seconds. Higher by default, since a long span is costlier to learn wrong. |

A held standalone catch carries a `verification_miss` hold reason and shows a "Verification catch" chip in the Held for Review section; it gets the same waveform editor and approve/dismiss flow as any other held ad. See [Held for Review](how-it-works.md#held-for-review) and [Verification Pass](how-it-works.md#verification-pass).

**Differential detection** - governs the cross-fetch stage's candidate and hold gates.

| Control | Default | Range | Notes |
|---|---|---|---|
| Correlation ceiling | 0.60 | 0.0 - 1.0 | A cross-fetch region becomes a differential candidate only when its measured correlation is at or below this value. A higher correlation means the two fetches matched too closely to be a real ad swap. |
| Hold minimum length | 10s | 0 - 120s | An uncorroborated differential candidate shorter than this is dropped instead of held for review. Set to 0 to hold a candidate of any length. |

Raise the correlation ceiling if genuine ad swaps are being missed as alignment noise, or lower it if identical-content regions are surfacing as false differential candidates. Raise the hold minimum length if short re-roll noise is showing up as holds; lower it (or disable it) if a feed's shortest DAI fills are being dropped before you get a chance to review them. See [Cross-Fetch Differential](how-it-works.md#cross-fetch-differential) for how these gates fit into the stage, including how audio cue templates corroborate candidates independently of both settings.

### Tuning LLM behavior per stage

Each LLM pass can be tuned independently from Settings. The five passes:

1. **Ad Detection (Pass 1)** - first scan of the full transcript
2. **Verification (Pass 2)** - second scan against the processed audio
3. **Reviewer** - optional confirm/reject pass (shared by both reviewer invocations)
4. **Chapter Boundary Detection** - finds topic transitions
5. **Chapter Title Generation** - writes titles for those chapters

Controls available on each:

| Control | Range | Notes |
|---|---|---|
| Temperature | 0.0 - 2.0 | 0.0 is fully reproducible. Keep detection and chapter boundaries low. |
| Max tokens | 128 - 32768 | Response cap. Truncated JSON fails parsing; the salvage helper only recovers single-ad cases. |
| Reasoning | Provider-aware | Anthropic takes a numeric token budget (1024-65536) for the `thinking` block. OpenAI, OpenRouter, and Ollama take an effort level (`none`, `low`, `medium`, `high`). |

Defaults match what the code used before this feature, so existing installs behave identically until you touch a control.

#### Fallback when the provider rejects a value

If the provider returns a 4xx because your tunables don't fit the model, the call is logged at WARNING and retried once with the built-in defaults. The fallback flag is keyed by `(episode_id, pass_name)`, so two episodes processing in parallel won't step on each other's flag. It clears at the start of the next pass, so your values get a fresh attempt there.

#### Env-var defaults

Every tunable has a matching env var (`DETECTION_TEMPERATURE`, `VERIFICATION_MAX_TOKENS`, `REVIEWER_REASONING_LEVEL`, etc.). The env var supplies the default; a value saved in Settings wins over it, like every other env-backed setting. When the env var is set, the control shows a note naming the variable it inherits its default from. Full list in `.env.example`.

#### Ollama context window

Ollama truncates prompts that exceed its context window without telling you. The default is often 2048 tokens, too small for a full-transcript pass, and detection fails silently. When the active provider is Ollama, Settings exposes a **Context window (num_ctx)** field; set it to your model's trained context (8192 or higher on most modern models). Env-var alias: `OLLAMA_NUM_CTX`.

#### Detection window geometry

Long episodes are chunked into overlapping windows before being sent to the detection LLM. These controls are global rather than per-stage, and sit above the per-stage controls:

| Control | Range | Default | Notes |
|---|---|---|---|
| Window size | 120-1800 seconds | 600s | How much audio each detection request covers. Lower values reduce tokens per request and help small local models or low-tier provider plans stay under per-minute caps. |
| Window overlap | 0-1770 seconds | 180s | Trailing overlap between consecutive windows so an ad straddling a boundary is still visible in the next window. Must be strictly less than window size. |

API: `PUT /api/v1/settings` accepts `windowSizeSeconds` and `windowOverlapSeconds`. Cross-field validation rejects `overlap >= size` with a 400. The reset-to-default buttons in the UI clear the stored value so the built-in defaults apply on the next episode; no restart needed.

When the provider returns a 429 because a single window's request exceeds the per-minute token cap, MinusPod flags the episode with a `Rate Limit Structural` error and fires the matching webhook (see [API & Webhooks](api-and-webhooks.md#events)). Lower **Window size** here, or move to a higher provider tier; the retry loop won't eventually succeed because the request itself is too big.

### VAD Gap Detector (advanced)

Whisper uses Voice Activity Detection to skip regions it classifies as silence or non-speech. Sped-up legal disclaimers at the tail of DIA ads, distorted interstitials, and some ad intros fall into that bucket and never make it into the transcript. Since MinusPod's Claude, text-pattern, and roll detectors all run against the transcript, these regions are invisible to them and can leak into the processed output, usually at the very start or end of an episode.

The VAD gap detector (added in 2.0.7) runs after the other stages and treats untranscribed spans as ad candidates:

- **Head gap** at the top of the episode: cut whenever the first transcribed segment starts more than `VAD_GAP_START_MIN_SECONDS` (default 3s) into the audio and nothing already covers it.
- **Mid gap** between segments: if the span is adjacent to a detected ad, the ad's boundary is extended in place. Otherwise, the gap must be at least `VAD_GAP_MID_MIN_SECONDS` (default 8s) AND have ad-signoff language before it or show-resume language after it. Neutral content pauses are left alone.
- **Tail gap** at the bottom: cut when the span is at least `VAD_GAP_TAIL_MIN_SECONDS` (default 3s) and the postroll detector hasn't already marked it.

Disable with `VAD_GAP_DETECTION_ENABLED=false` or via `PUT /api/v1/settings` `{"vadGapDetectionEnabled": false}`. The knob is intentionally not in the UI; operators reach it via env or API.

If the detector is cutting too aggressively on a specific podcast, raise the mid threshold before disabling. `VAD_GAP_MID_MIN_SECONDS=15` or higher restricts the standalone mid path to very long spans; the adjacent-ad-extend path still fires regardless.

### Provider API Keys

You can set the Anthropic, OpenAI-compatible, OpenRouter, Ollama, and remote Whisper keys from the UI (Settings > LLM Provider and Settings > Transcription) or via `PUT /api/v1/settings/providers/<name>`. No container restart needed. Keys are encrypted with AES-256-GCM.

Two things have to be in place first:

1. `MINUSPOD_MASTER_PASSPHRASE` set in the container environment. PBKDF2 derives the encryption key from it, so treat it like any other production secret: back it up, keep it stable, don't commit it. To rotate, use Settings > Security > Provider Key Encryption (or `POST /api/v1/settings/providers/rotate-passphrase`). The call re-encrypts every stored key in one transaction, then you must update the env var to the new value before the next restart, or the next boot won't decrypt anything.
2. An admin password set in the UI, so Settings is reachable. The password gates the surface only; it isn't part of the crypto. Changing it leaves stored keys untouched.

If the passphrase is missing, the key inputs collapse to a "Setup required" note, the API returns `409 provider_crypto_unavailable`, and env-var credentials keep working. GET responses never include key values, only booleans plus a `db`/`env`/`none` source marker.

### Cover art badge

Settings > Cover Art has an **Overlay MinusPod badge on cover art** toggle, off by default. When on, MinusPod adds a small badge to the bottom-right corner of each served feed's cover art, so the filtered version is easy to tell apart from the original in your podcast app. The badged image is served at `/<slug>/cover-minuspod.jpg`. A **Refresh all artwork** button in the same section re-renders every feed's cover art, which you run after toggling the setting or swapping the badge asset.

<img src="screenshots/cover-art-badge.png" width="200">

### Pass-through mode

Each feed's settings page has a **Pass-through** toggle. When it is on, MinusPod stops processing that feed's episodes entirely: each new episode is downloaded and served exactly as published, with no transcription, ad detection, or cutting. Useful for archiving originals, or for pausing ad removal on a feed without touching your podcast app.

The served feed URL does not change, which is the point: your app keeps pulling the same MinusPod feed, and turning the toggle off resumes full processing for new episodes. Two caveats: enclosures that are not MP3 get converted to MP3 (the serving stack requires it), and the download size cap (`MINUSPOD_MAX_AUDIO_DOWNLOAD_MB`, default 500) still applies, so raise it before archiving very large episodes. Episodes that were served untouched keep their original audio until you reprocess them. While the toggle is on, a full or AI reprocess just re-downloads the current copy; the per-episode Recut action still works on episodes that have a retained original and ad markers.

## Experiments

The Experiments section in Settings holds opt-in features that are still being evaluated. Everything here is disabled by default. Turning a feature on does not change behavior on existing processed episodes; it applies only to subsequent processing runs.

### Ad Reviewer

The ad reviewer is an opt-in third LLM stage that sits between detection and audio cutting. After pass 1 detection (and again after pass 2), the reviewer takes each candidate ad along with 60 seconds of transcript on either side and decides one of three things: confirm the detection as is, adjust the start or end timestamps within a configured cap, or reject the segment as a false positive. The reviewer also gets a second look at validator-rejected detections whose confidence sits within 20 percentage points of your `min_cut_confidence` slider, and may resurrect them as real ads.

When to enable it:

- Comedy and fiction podcasts that include in-bit fake sponsor reads (Welcome to Night Vale was the torture test for this feature)
- News shows that read sponsor-adjacent copy editorially without it actually being an ad break
- Hosts who organically mention their own other shows or Patreon, where the detector flags a non-ad as promotional
- Episodes where you have noticed the cut is starting a few seconds late or ending a few seconds early

Cost is one extra LLM call per detected ad (and one extra call per rejected detection in the resurrection band). With the default Claude pass-1 model and a typical episode that produces 4 to 8 ad detections, expect a small percentage increase in per-episode token spend rather than a doubling.

Settings live under Experiments -> Ad Reviewer:

- **Enable ad reviewer** - master toggle, off by default
- **Review model** - `Same as pass model` reuses the pass-1 detection model on pass-1 review and the verification model on pass-2 review. You can override to a single specific model for both reviewer passes (for example, run pass-1 detection on a smaller cheap model and run reviewer on a larger model that is better at boundary work)
- **Max boundary shift** - caps how far the reviewer can move start or end timestamps when it chooses adjust. Default 60 seconds. Enforced in code regardless of what the prompt says
- **Review prompt** - system prompt for the confirm/adjust/reject reviewer
- **Resurrect prompt** - system prompt for the resurrect/reject reviewer over rejected detections

Reviewer activity surfaces in two places:

- The episode detail page shows the original timestamps on top and a `Reviewer: MM:SS - MM:SS` line beneath when the reviewer adjusted boundaries. Reviewer-rejected ads carry a `Source: Reviewer` tag in the rejected detections list.
- The Stats page shows an Ad Reviewer Stats card with verdict counts (confirmed, adjusted, rejected, resurrected, failed), pass-1 and pass-2 adjustment counts, average boundary shift in seconds, and resurrection count. The card hides when the reviewer has not run.

### Prompt placeholders

Detection, verification, and reviewer prompts use explicit placeholder substitution rather than always appending dynamic content. Available placeholders:

- `{sponsor_database}` - substituted at runtime with the dynamic sponsor list (the one that grows as new sponsors are detected). Available in the system, verification, review, and resurrect prompts. If you remove this placeholder from your customized prompt, no sponsor list is injected on that prompt.
- `{max_boundary_shift_seconds}` - review prompt only. Substituted with the current `Max boundary shift` setting. The boundary cap is enforced in code regardless of whether the placeholder is in the prompt.
- `{override}` - replaced with that pass's override text (see below). If a customized prompt omits it, the override is appended instead.

If you customized your system or verification prompt before this release, the upgrade automatically appends `{sponsor_database}` to your prompt so behavior is preserved. The migration is idempotent and runs once.

### Per-pass prompt overrides

Each pass (first, verification, reviewer, resurrect) has an optional **Override** field in Settings, empty by default. Text there is added to that pass at run time, so you can apply a tweak (e.g. "keep this show's news roundup") without editing the built-in prompt, which stays intact. It is inserted at the prompt's `{override}` placeholder if present, otherwise appended under an "additional instructions" header. An empty override changes nothing.

### Audio Cue Detection

Audio cue detection snaps ad cuts to a show's recurring chime or stinger, and is
off by default. Setup, cue types, the find-audio-cues scan, and every tuning
control are documented in [Audio Cue Detection](audio-cues.md).

## Reprocessing

Reprocessing an episode re-runs detection without re-fetching it from the source feed. The episode menu offers four modes; the bulk feed actions offer the same set apart from Recut Audio:

- **Reprocess** (default) - uses the learned pattern database plus the LLM. Fastest option for routine re-detection.
- **Full Analysis** - skips the pattern database for a fresh LLM-only pass.
- **Recut Audio** - re-cuts the retained original from the episode's current ad list and re-times the saved transcript, without re-transcribing or calling the LLM. Use it after editing ads by hand to regenerate the output file. Because no LLM runs, generated chapters are not refreshed: the rebuilt file carries the source feed's own chapters remapped to the new cut, and the podcast:chapters JSON keeps its old timestamps. Run Regenerate Chapters afterward if chapters matter for the episode.
- **Re-detect Ads** - reruns detection and re-cuts using the transcript already saved for the episode, skipping the transcription step that dominates processing time on local hardware. Requires an existing transcript; episodes without one are skipped, and it is also offered for failed episodes that still have a transcript. Use it to iterate on detection settings or models without paying for transcription each time.

## Community Patterns (Optional)

MinusPod can share and receive ad patterns from a community-maintained seed list. Patterns describe recognized ad reads (sponsor scripts, host-read pre-rolls, etc.) so new MinusPod instances skip the LLM detection step for ads that have already been identified elsewhere.

The feature is **opt-in** and **off by default**. When enabled, your MinusPod instance pulls a manifest of community patterns from this repo on a schedule you control. To submit your own patterns back, open the Patterns page Export dialog and pick **Submit to community**: the app runs quality gates over your selection, shows what will pass, and downloads a single bundle file. Drop it into your fork of `patterns/community/` and open one PR.

### What you get when enabled

- Faster ad detection for sponsors other MinusPod users have already identified
- New patterns appear automatically as the community contributes them
- Local patterns you build stay private unless you choose to submit them

### What you control

- **Sync schedule** - cron expression in Settings (default: weekly, Sunday 3am)
- **Manual sync** - "Sync now" button in Settings
- **Per-pattern protection** - pin any community pattern with **Protect from sync** to prevent automatic updates or deletion
- **Disable at any time** - flipping the toggle stops sync; existing community patterns remain unless you delete them
- **Remove all at once** - "Remove all community patterns" in Settings wipes every community pattern (including any you marked Protect from sync). Useful for a clean reset before re-enabling sync.

### What is shared if you submit

Submitting a pattern is a separate action you trigger from the Export dialog and never automatic. Before submission, the app:

- Strips local identifiers (which podcast, which network, your match counts, your timestamps)
- Strips PII from pattern text (consumer email addresses, non-toll-free phone numbers)
- Validates the pattern meets quality thresholds
- Generates a JSON file and opens a prefilled GitHub PR in your browser

You retain everything locally. Submission is a copy, not a move.

### Full details

See [`patterns/README.md`](../patterns/README.md) for the technical reference (sync mechanics, file formats, tag vocabulary) and [`patterns/CONTRIBUTING.md`](../patterns/CONTRIBUTING.md) for what happens when you submit a pattern.

## Offline Queue

If your LLM or Whisper server only runs part of the day (a desktop PC that hosts Ollama, for example), episodes that arrive while it is off normally retry a few times, trip the circuit breaker, and end up permanently failed until you reprocess them by hand. The offline queue changes that: an episode that fails because the endpoint is unreachable is parked with a "queued (offline)" status instead. Every few minutes MinusPod probes the endpoint, and once it answers again the parked episodes go back into the processing queue on their own.

The feature is off by default. Configure it in **Settings > Offline Queue**.

| Setting | Default | Notes |
|---|---|---|
| Enabled | off | Park episodes when the LLM or Whisper endpoint is unreachable. |
| Give up after | 48 hours | Episodes still waiting after this long are marked failed and logged. Range 1-720 hours. |

Only connection-level failures qualify: connection refused, DNS errors, timeouts, and repeated 5xx responses. Auth errors, rate limits, and bad responses still fail normally, so a wrong API key does not sit in the queue looking healthy. Turning the toggle off stops new episodes from being parked, but anything already waiting keeps being probed and expired so nothing is stranded. You can also reprocess a parked episode by hand at any time.

## Scheduled Database Backups

MinusPod can snapshot its SQLite database to a directory on a cron schedule. The feature is off by default. The "Back up now" button runs a snapshot immediately whether or not the schedule is enabled, and is rate-limited to 6 runs per hour. Configure it in **Settings > Data & Security > Scheduled Backups**.

| Setting | Default | Notes |
|---|---|---|
| Enabled | off | Turn on the cron schedule. Back up now works regardless. |
| Schedule | `30 3 * * *` | Cron expression, interpreted as UTC. |
| Destination | `/app/data/backups` | Directory path inside the container. Empty uses the default. |
| Keep last | 1 | 1 overwrites a single file; higher keeps timestamped copies and prunes the oldest. |

Cron examples (all UTC):

- `30 3 * * *` - daily at 03:30
- `0 */6 * * *` - every 6 hours, on the hour
- `0 4 * * 0` - weekly, Sunday at 04:00

The snapshots are plain SQLite files and are never encrypted, even with `MINUSPOD_MASTER_PASSPHRASE` set. For filenames, restore steps, and how destination directory permissions are handled, see [Scheduled database backups](security-and-storage.md#scheduled-database-backups) in the security guide.

## Feed Refresh and Podping

MinusPod polls every feed's upstream RSS on a fixed schedule. Podping is an opt-in accelerator that can trigger an immediate refresh of a single feed when its host announces a new episode; scheduled polling never turns off, so it stays the fallback for hosts that don't send Podping and for any notification the listener misses. See [Podcasting 2.0 > Podping](podcasting-2.0.md#podping) for how the listener works, which hosts send Podping, and the "Last podping" diagnostic on the feed detail page.

| Setting | Default | Notes |
|---|---|---|
| Feed refresh interval | 15 minutes | Minutes between background RSS refresh passes for every feed. Range 5-1440. Settings > Global Defaults. A change applies after the wait already in progress finishes. |
| Podping notifications | off | Opt-in listener that stamps a feed's "last podping" time and refreshes that one feed immediately when its host sends a Podping notification. Settings > Transcripts & Chapters. |

---

[< Docs index](README.md) | [Project README](../README.md)
