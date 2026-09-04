# Glossary

Every term the app uses, in plain words, with a link to the part of the docs that covers it. If you hit a word in the UI that isn't here, open an issue.

## A

**Ad Review** - The Patterns page tab that lists every detection across all feeds so you can confirm or reject them in one place, with a play button for the flagged audio. [Web Interface > Ad Review tab](web-interface.md#ad-review-tab)

**Ad Reviewer** - An optional second LLM that double-checks each planned cut before it happens and can confirm, adjust, reject, or resurrect a detection. Off by default. [Configuration > Ad Reviewer](configuration.md#ad-reviewer)

**Addressing mode** - An experimental detector setting where the LLM names numbered transcript lines (`segment_ids`) instead of inventing start and end timestamps (`timestamps`), or where `random` draws one of the two per run. Timestamps stays the default while results decide whether that changes. The Stats page compares the modes on contract compliance and on ad yield (proposed vs kept, with drop reasons). [Configuration > Ad Addressing Mode](configuration.md#ad-addressing-mode)

**Archive mode** - A per-feed retention setting that keeps every processed episode indefinitely, ignoring the global retention window and the "Clear all processed audio" action. Set it on shows you never want swept, such as ones that have stopped publishing. [Configuration > Per-feed retention](configuration.md#per-feed-retention)

**Audio analysis** - A pre-detection pass over the audio itself (volume shifts, transitions, silence) whose signals feed the detector and validator. [How It Works > Audio Analysis](how-it-works.md#audio-analysis)

**Audio cue** - A short, repeated sound a show plays around its ad breaks (a sting, a jingle). MinusPod can learn one as a template and use matches as hard evidence for ad boundaries. [Audio Cue Detection](audio-cues.md)

**Authenticated feeds** - An optional key appended to feed URLs so only clients holding it can pull your processed feeds. [Security > Authenticated feeds](security-and-storage.md#authenticated-feeds-optional)

## C

**Chapters** - Chapter markers MinusPod generates for the processed audio, served as Podcasting 2.0 JSON and embedded in the MP3 as ID3 frames. [How It Works > Chapter Generation](how-it-works.md#chapter-generation)

**Community patterns** - Opt-in sharing of learned ad patterns with other MinusPod users, and pulling theirs in return. [Configuration > Community Patterns](configuration.md#community-patterns-optional)

**Confidence** - The detector's certainty (0-100%) that a flagged region is an ad. Only detections at or above the cut threshold are removed; the rest stay in the audio for review. [Configuration > Ad Detection Settings](configuration.md#ad-detection-settings)

**Correction** - Your verdict on a detection: "Confirm ad" or "Not an ad". Corrections train future detection for that feed and can trigger a recut. [Web Interface > Ad Review tab](web-interface.md#ad-review-tab)

**Corroboration** - Independent evidence from another detection stage, an overlapping marker, or a matched audio cue that backs an otherwise-uncertain detection, letting it cut instead of hold. Used by the cross-fetch differential stage to decide which candidates cut and which hold. [How It Works > Cross-Fetch Differential](how-it-works.md#cross-fetch-differential)

**Cover art badge** - The small mark MinusPod adds to a feed's artwork so you can tell the processed feed apart from the original in a podcast app. [Configuration > Cover art badge](configuration.md#cover-art-badge)

**Cross-fetch differential** - Downloading an episode twice and comparing the copies; audio that differs between fetches was inserted by an ad server, not part of the show. Runs automatically on feeds that look DAI-served. [How It Works > Cross-Fetch Differential](how-it-works.md#cross-fetch-differential)

**Cue match** - A place in an episode where a learned audio cue template was found, shown with its score and how detection used it. [Audio Cues > Cue matches on an episode](audio-cues.md#cue-matches-on-an-episode)

**Cue-only** - The experimental per-feed Processing mode that cuts from cue pairs and previously learned ad patterns, with no LLM reading the transcript. Requires an enabled ad-break-start and an enabled ad-break-end template on the feed. [Audio Cue Detection > Cue-only preset](audio-cues.md#cue-only-preset)

**Cue pair** - Two bracketing template cues, an ad-break-start and an ad-break-end, that mint an ad span between them without reading the transcript. Cutting from cue pairs is experimental and off by default. [Audio Cue Detection > Ad cutting](audio-cues.md#ad-cutting)

## D

**DAI (dynamic ad insertion)** - Ads spliced into the audio by the publisher's server at download time, so every download can carry a different ad load. This is why the same episode can be 46 minutes one fetch and 55 the next. [How It Works > Cross-Fetch Differential](how-it-works.md#cross-fetch-differential)

**Defined pattern** - A user-created or synced community ad pattern. Its match always cuts the matched segment, overriding whatever action the segment's category resolves to. An auto-learned pattern is not defined and still respects segment actions. [How It Works > Pattern Learning](how-it-works.md#pattern-learning)

**Detections Not Cut** - The episode page section listing regions the detector flagged but left in the audio: rejected by validation, below the confidence threshold, or vetoed by the reviewer. [How It Works > Post-Detection Validation](how-it-works.md#post-detection-validation)

**Deferred** - An episode parked because the LLM or transcription endpoint was unreachable. It retries automatically when the endpoint comes back instead of burning through its retry budget. [Configuration > Offline Queue](configuration.md#offline-queue)

**Differential hold** - An uncorroborated cross-fetch differential candidate: the two fetches measurably differ, but no other stage, overlap, or matched audio cue backs it as an ad. Held for review rather than cut, unless it is shorter than the differential hold-minimum-length setting, in which case it is dropped instead. [How It Works > Cross-Fetch Differential](how-it-works.md#cross-fetch-differential)

**Dry-run import plan** - The preview a bulk archive-import scan returns before anything is written: every file matched to an episode, every rejected file with a reason, and the publish date each episode would get. Committing re-checks the same files and refuses if anything changed underneath the plan. [Local Feeds > Scan, then commit](local-feeds.md#scan-then-commit)

## E

**Episode statuses** - *Discovered* (seen in the feed, not processed), *Pending* (queued), *Processing* (running now), *Completed* (processed and in your feed), *Failed* (will retry), *Permanently failed* (out of retries), *Deferred* (waiting on an offline endpoint). [How It Works > Processing Queue](how-it-works.md#processing-queue)

## F

**False-positive text** - Transcript text stored from a confirmed "Mark as Not Ad" correction, matched against future episodes of the same podcast to suppress similar text automatically. Rejecting a differential detection, held or not, does not create this text (it was only ever a candidate, never a confirmed false positive from a real detector), though the same-episode region is still blocked from resurfacing. [How It Works > Pattern Learning](how-it-works.md#pattern-learning)

**Fingerprint** - An acoustic signature of a known ad, matched against new episodes without any transcript. One of the pattern types MinusPod learns from confirmed cuts. [How It Works > Pattern Learning](how-it-works.md#pattern-learning)

**First pass (Pass 1)** - The main detection run over the freshly transcribed episode: patterns, cross-fetch, and the LLM reading the transcript in windows. [How It Works](how-it-works.md#how-it-works)

## H

**Held for Review** - An ad that detection wanted to cut but a per-feed guard (max ad duration, cue-gated approval, a reviewer contradiction, or a verification conflict) stopped. The audio stays intact until you approve or dismiss it. [Web Interface > Held for Review](web-interface.md#held-for-review)

**Hold (queue)** - Any reason the queue is waiting rather than working: a rate-limit pause that stops new claims outright, or an offline-queue wait that parks specific episodes while the rest keep processing. The status bar names which one is in effect, and `GET /api/v1/status` reports it in a `hold` block. [Configuration > Rate-Limit Hold](configuration.md#rate-limit-hold)

## I

**Import directory** - The user-managed folder for a local feed's archive import, `<data>/import/<slug>/`. You place audio and sidecar files there yourself; on a successful commit MinusPod moves the audio out and deletes that episode's sidecars along with it, leaving a rejected or errored file's sidecars in place to fix and re-scan. [Local Feeds > Bulk import](local-feeds.md#bulk-import)

## K

**Keep action** - A per-category segment action that leaves a detected span in the audio untouched. A kept marker bypasses validator hold rules and reviewer boundary checks, is dropped instead of re-flagged if pass-2 verification finds it again, and never creates a correction or false-positive text, though it still teaches the pattern learner its category. [How It Works > Segment Categories](how-it-works.md#segment-categories)

## L

**Local feed** - A podcast feed MinusPod builds and serves from your own audio files, with no upstream RSS feed behind it. Episodes still run through the same ad-removal pipeline; MinusPod is the publisher rather than a proxy. [Local Feeds](local-feeds.md)

**Low ad yield** - The amber episode badge shown when a run removed far less ad time than the feed's recent average. Usually a lightly-filled DAI download, occasionally a missed ad worth a look. [Web Interface > Processing stats](web-interface.md#processing-stats)

## N

**Normalization** - A rule that maps sponsor name variants ("betterhelp.com slash pod", "Better Help") onto one sponsor so patterns and history stay tidy. [Web Interface > Sponsors and Normalizations](web-interface.md#sponsors-and-normalizations)

## O

**Offline queue** - An opt-in hold that parks an episode when the LLM provider or Whisper endpoint is unreachable, probes the service every few minutes, and re-queues the episode once it answers. It does not stop the queue: everything not waiting on that service keeps processing. [Configuration > Offline Queue](configuration.md#offline-queue)

**Outbound Requests** - The settings section holding the two User-Agent strings MinusPod sends: one for audio, artwork, and chapters, one for RSS. Editable so a host that starts refusing ours can be worked around without a new release. [Configuration > Outbound Requests](configuration.md#outbound-requests)

## P

**Partial detection** - An episode published from pattern and cross-fetch cuts alone after the AI detection pass failed. Shows an amber badge and a Re-run detection banner on the episode page; one automatic low-priority re-detect is also queued. [How It Works > Partial Detection](how-it-works.md#partial-detection)

**Pass-through** - One of the five presets on the per-feed Processing mode select. It turns processing off entirely: episodes are downloaded and served exactly as published, and the feed URL stays the same so switching to another mode resumes processing later without touching your podcast app. [Configuration > Pass-through mode](configuration.md#pass-through-mode)

**Pattern** - Anything MinusPod has learned from confirmed ads and reapplies to new episodes: text patterns from transcripts and audio fingerprints. Patterns catch repeat ads without spending LLM tokens. [How It Works > Pattern Learning](how-it-works.md#pattern-learning)

**Podping** - An opt-in listener that watches the Hive blockchain for publish notifications and refreshes a matching feed immediately instead of waiting for the next scheduled poll. Only some hosts send them; polling continues either way. [Podcasting 2.0 > Podping](podcasting-2.0.md#podping)

**Processing mode** - The per-feed preset that decides what the pipeline does with each episode: standard ad removal, keep-content detection, skip ad detection (transcripts and chapters only), pass-through, or cue-only (cuts from cue pairs and previously learned ad patterns, no LLM call). One select in Feed Settings; the REST API also accepts the underlying per-field flags. [How It Works](how-it-works.md)

**Processing queue** - The line episodes wait in; one episode processes at a time. [How It Works > Processing Queue](how-it-works.md#processing-queue)

**Processing stats** - The per-run table at the bottom of the episode page: what each run downloaded, detected, cut, held, and verified. [Web Interface > Processing stats](web-interface.md#processing-stats)

## Q

**Queue priority** - A per-feed High/Normal/Low processing-order preference, with automatic boosts for episodes published in the last 48 hours and for manual reprocesses. [Configuration > Queue priority](configuration.md#queue-priority)

## R

**Rate-limit hold** - An opt-in hold that parks an episode when the LLM provider answers a 429 carrying a reset time, and stops the queue claiming new work until that time passes. Unlike the offline queue it pauses everything, though anything you ask for by hand still runs. [Configuration > Rate-Limit Hold](configuration.md#rate-limit-hold)

**Recut** - Re-cutting the retained original audio using the current ad markers, with no download, transcription, or LLM involved. What "Approve & Recut" does. [How It Works > Reprocessing Modes](how-it-works.md#reprocessing-modes)

**Reprocess modes** - *Patterns + AI* (the default, everything), *AI Only* (skip the learned-pattern DB), *Re-detect Ads* (reuse the saved transcript, rerun detection), and *Recut*. [How It Works > Reprocessing Modes](how-it-works.md#reprocessing-modes)

**Resurrected** - A detection the validator rejected that the Ad Reviewer overruled and put back in the cut list. [Configuration > Ad Reviewer](configuration.md#ad-reviewer)

**Retention** - How long processed audio is kept before the episode resets to Discovered. The pre-cut original can have its own shorter window, and any feed can override the whole window on its own settings page. [Configuration > Per-feed retention](configuration.md#per-feed-retention)

## S

**Second scan** - See Verification pass.

**Seed sponsors** - Four toggles, one per LLM pass (detection, verification, reviewer, resurrect), that control whether that pass is handed the known-sponsor list. All on by default; turning one off lets that pass judge each candidate without a prior nudge from sponsors seen before. [Configuration > Seed sponsors](configuration.md#seed-sponsors)

**Sidecar file** - An optional file next to an archive-import audio file, sharing its exact basename: a `.txt` description, a `.jpg`/`.jpeg`/`.png` cover, or a `.json` file overriding title, description, publish date, season, and episode. A JSON sidecar overrides everything else, including the filename's sNNeNN token. [Local Feeds > JSON sidecar](local-feeds.md#json-sidecar)

**Segment category** - What kind of content a detected marker spans: sponsor, cross-promo, self-promo, interaction, intro, outro, or recap. Each category resolves to an action (remove, beep, or keep), set per feed or globally on the **Segment actions** card and defaulting to remove until changed. Intro, outro, and recap are only detected on feeds where show-segments detection resolves to on (a per-feed Inherit/On/Off choice, falling back to the global default); the other four categories are always detected. A defined pattern's match always cuts regardless of the resolved action. [How It Works > Segment Categories](how-it-works.md#segment-categories)
  - Sponsor - Paid ads, including dynamically inserted ones
  - Cross-promo - Promos for other shows and the network
  - Self-promo - The show's own Patreon, merch, and subscribe asks
  - Interaction - Follow, rate, and review reminders
  - Intro - Opening theme and welcome
  - Outro - Closing credits and sign-off
  - Recap - Previews and coming-up bumpers

**Silence snap** - Nudging a cut boundary to the nearest silence so the edit lands between words instead of inside one. [Audio Cues > Silence snap](audio-cues.md#silence-snap)

**Sliding windows** - Long transcripts are fed to the LLM in overlapping chunks so nothing is missed at chunk edges; the Windows column in Processing stats counts these. [How It Works > Sliding Window Processing](how-it-works.md#sliding-window-processing)

**Splice check** - The rule that holds a long cut for review unless the audio shows an edit point near its edges. Feeds whose ads are never joined into the audio can turn it off on their own settings page. [Configuration > Splice check](configuration.md#splice-check)

**Splice evidence** - A mark in the audio where something was joined: a transition pair, an ad-break cue, a sharp volume step, or a detected splice event. Server-inserted and edited-in ads leave these; an ad spoken straight through in a single take does not. [How It Works > Held for Review](how-it-works.md#held-for-review)

**sNNeNN naming token** - The `s01e01`-style prefix a local feed's archive-import files must start with to be matched: case-insensitive, zero-padded to at least 2 digits for both season and episode. Mints the episode's id and is what sidecar files are matched against. [Local Feeds > Naming scheme](local-feeds.md#naming-scheme)

**Sponsor** - The advertiser behind a detection. Sponsors accumulate history per feed, which gets fed back into detection as a hint. [Web Interface > Sponsors and Normalizations](web-interface.md#sponsors-and-normalizations)

**Staging area** - The per-feed holding folder for a local feed's uploaded archive-import batch, `<data>/import-staging/<slug>/`. Unlike the import directory, MinusPod fully manages it: it is populated by the upload endpoint and cleared out as the import commits. [Local Feeds > Bulk import](local-feeds.md#bulk-import)

**Synthesized publish date** - The publish date MinusPod assigns a local-feed episode when none was given explicitly: episodes are sorted by season and episode, the newest anchors at import time, and earlier ones step back a day each (or space evenly between two explicit dates). [Local Feeds > Publish dates](local-feeds.md#publish-dates)

## T

**Text pattern** - A learned chunk of ad transcript matched against new episodes by similarity. Deterministic: if the same ad copy appears, it hits. [How It Works > Pattern Learning](how-it-works.md#pattern-learning)

**Text recurrence hint** - An optional signal that flags transcript spans repeating near-verbatim across a show's last two or more episodes (intros, credits, boilerplate) and passes them to pass 1 detection as a hint. Off by default, and never enough by itself to cut anything. [Configuration > Text recurrence hints](configuration.md#text-recurrence-hints)

**Title blacklist** - A per-feed list of case-insensitive glob patterns (e.g. `Bonus Episode *`) that skip processing for matching episode titles. A per-feed choice serves a skipped episode unmodified or hides it from the feed. Manual reprocess overrides it. [Configuration > Title blacklist](configuration.md#title-blacklist)

**Transcript (VTT)** - The Podcasting 2.0 transcript MinusPod generates for the processed audio, with cut regions accounted for. [Podcasting 2.0](podcasting-2.0.md)

## U

**User-Agent** - The string MinusPod sends to identify itself on an outbound request. There are two, because hosts disagree about what they will answer. Bot mitigation on some CDNs refuses browser identifiers below a version floor that moves as new browsers ship, while some feed hosts serve only a declared podcast client. Both are editable. [Configuration > Outbound Requests](configuration.md#outbound-requests)

## V

**Validation** - The rule-based gate every detection passes before cutting: duration limits, confidence, overlap with your corrections, cue evidence, splice checks. [How It Works > Post-Detection Validation](how-it-works.md#post-detection-validation)

**Verification conflict** - A second-pass finding that lands on a span the first pass deliberately kept. Your segment-action map wins, so the audio is left alone, but the finding holds for review under hold reason `verification_kept_conflict` rather than being discarded. [How It Works > Verification Pass](how-it-works.md#verification-pass)

**Verification miss** - A standalone pass-2 detection (an ad the verification pass found but the first pass missed) that overlaps no pass-1 marker. Above a confidence floor it holds for review, or cuts automatically when autocut is enabled; below the floor it is dropped and logged rather than surfacing silently. [Configuration > Detection Tuning](configuration.md#detection-tuning)

**Verification pass (Pass 2)** - After cutting, MinusPod re-transcribes the output audio and runs detection again to catch anything the first pass missed. "Clean" means the second scan found nothing left. [How It Works > Verification Pass](how-it-works.md#verification-pass)

## W

**Waveform Ad Editor** - The visual editor for a single detection: waveform, transcript context, and draggable boundaries, with the original audio for reference. [Web Interface > Waveform Ad Editor](web-interface.md#waveform-ad-editor)

**Webhook events** - Notifications MinusPod can send: Episode Processed, Episode Failed, Auth Failure, Limit Exceeded, Rate Limit Structural, Feed Refresh Failed, Update Available, Cue Template Quiet, Queue Held, Queue Resumed, Service Offline, Service Reachable. Each can also go out by email. [API & Webhooks > Events](api-and-webhooks.md#events)

---

[< Docs index](README.md) | [Project README](../README.md)
