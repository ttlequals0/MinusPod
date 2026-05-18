# Configuration & Experiments

[< Docs index](README.md) | [Project README](../README.md)

---

## Configuration

All configuration is managed through the web UI or REST API. No config files needed.

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
- **LLM Tunables (per stage)** - See below

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

#### Env-var overrides

Every tunable has a matching env var (`DETECTION_TEMPERATURE`, `VERIFICATION_MAX_TOKENS`, `REVIEWER_REASONING_LEVEL`, etc.). When the env var is set, Settings renders the control read-only with a note pointing at the variable. Remove the env var to get the stored DB value back. Full list in `.env.example`.

#### Ollama context window

Ollama truncates prompts that exceed its context window without telling you. The default is often 2048 tokens, too small for a full-transcript pass, and detection fails silently. When the active provider is Ollama, Settings exposes a **Context window (num_ctx)** field; set it to your model's trained context (8192 or higher on most modern models). Env-var alias: `OLLAMA_NUM_CTX`.

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

Settings live under Experiments → Ad Reviewer:

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

If you customized your system or verification prompt before this release, the upgrade automatically appends `{sponsor_database}` to your prompt so behavior is preserved. The migration is idempotent and runs once.

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

---

[< Docs index](README.md) | [Project README](../README.md)
