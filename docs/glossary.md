# Glossary

Every term the app uses, in plain words, with a link to the part of the docs that covers it. If you hit a word in the UI that isn't here, open an issue.

## A

**Ad Review** - The Patterns page tab that lists every detection across all feeds so you can confirm or reject them in one place, with a play button for the flagged audio. [Web Interface > Ad Review tab](web-interface.md#ad-review-tab)

**Ad Reviewer** - An optional second LLM that double-checks each planned cut before it happens and can confirm, adjust, reject, or resurrect a detection. Off by default. [Configuration > Ad Reviewer](configuration.md#ad-reviewer)

**Audio analysis** - A pre-detection pass over the audio itself (volume shifts, transitions, silence) whose signals feed the detector and validator. [How It Works > Audio Analysis](how-it-works.md#audio-analysis)

**Audio cue** - A short, repeated sound a show plays around its ad breaks (a sting, a jingle). MinusPod can learn one as a template and use matches as hard evidence for ad boundaries. [Audio Cue Detection](audio-cues.md)

**Authenticated feeds** - An optional key appended to feed URLs so only clients holding it can pull your processed feeds. [Security > Authenticated feeds](security-and-storage.md#authenticated-feeds-optional)

## C

**Chapters** - Podcasting 2.0 chapter markers MinusPod generates for the processed audio. [How It Works > Chapter Generation](how-it-works.md#chapter-generation)

**Community patterns** - Opt-in sharing of learned ad patterns with other MinusPod users, and pulling theirs in return. [Configuration > Community Patterns](configuration.md#community-patterns-optional)

**Confidence** - The detector's certainty (0-100%) that a flagged region is an ad. Only detections at or above the cut threshold are removed; the rest stay in the audio for review. [Configuration > Ad Detection Settings](configuration.md#ad-detection-settings)

**Correction** - Your verdict on a detection: "Confirm ad" or "Not an ad". Corrections train future detection for that feed and can trigger a recut. [Web Interface > Ad Review tab](web-interface.md#ad-review-tab)

**Cover art badge** - The small mark MinusPod adds to a feed's artwork so you can tell the processed feed apart from the original in a podcast app. [Configuration > Cover art badge](configuration.md#cover-art-badge)

**Cross-fetch differential** - Downloading an episode twice and comparing the copies; audio that differs between fetches was inserted by an ad server, not part of the show. Runs automatically on feeds that look DAI-served. [How It Works > Cross-Fetch Differential](how-it-works.md#cross-fetch-differential)

**Cue match** - A place in an episode where a learned audio cue template was found, shown with its score and how detection used it. [Audio Cues > Cue matches on an episode](audio-cues.md#cue-matches-on-an-episode)

## D

**DAI (dynamic ad insertion)** - Ads spliced into the audio by the publisher's server at download time, so every download can carry a different ad load. This is why the same episode can be 46 minutes one fetch and 55 the next. [How It Works > Cross-Fetch Differential](how-it-works.md#cross-fetch-differential)

**Detections Not Cut** - The episode page section listing regions the detector flagged but left in the audio: rejected by validation, below the confidence threshold, or vetoed by the reviewer. [How It Works > Post-Detection Validation](how-it-works.md#post-detection-validation)

**Deferred** - An episode parked because the LLM or transcription endpoint was unreachable. It retries automatically when the endpoint comes back instead of burning through its retry budget. [Configuration > Offline Queue](configuration.md#offline-queue)

## E

**Episode statuses** - *Discovered* (seen in the feed, not processed), *Pending* (queued), *Processing* (running now), *Completed* (processed and in your feed), *Failed* (will retry), *Permanently failed* (out of retries), *Deferred* (waiting on an offline endpoint). [How It Works > Processing Queue](how-it-works.md#processing-queue)

## F

**Fingerprint** - An acoustic signature of a known ad, matched against new episodes without any transcript. One of the pattern types MinusPod learns from confirmed cuts. [How It Works > Pattern Learning](how-it-works.md#pattern-learning)

**First pass (Pass 1)** - The main detection run over the freshly transcribed episode: patterns, cross-fetch, and the LLM reading the transcript in windows. [How It Works](how-it-works.md#how-it-works)

## H

**Held for Review** - An ad that detection wanted to cut but a per-feed guard (max ad duration, cue-gated approval, or a reviewer contradiction) stopped. The audio stays intact until you approve or dismiss it. [Web Interface > Held for Review](web-interface.md#held-for-review)

## L

**Low ad yield** - The amber episode badge shown when a run removed far less ad time than the feed's recent average. Usually a lightly-filled DAI download, occasionally a missed ad worth a look. [Web Interface > Processing stats](web-interface.md#processing-stats)

## N

**Normalization** - A rule that maps sponsor name variants ("betterhelp.com slash pod", "Better Help") onto one sponsor so patterns and history stay tidy. [Web Interface > Sponsors and Normalizations](web-interface.md#sponsors-and-normalizations)

## P

**Pattern** - Anything MinusPod has learned from confirmed ads and reapplies to new episodes: text patterns from transcripts and audio fingerprints. Patterns catch repeat ads without spending LLM tokens. [How It Works > Pattern Learning](how-it-works.md#pattern-learning)

**Processing queue** - The line episodes wait in; one episode processes at a time. [How It Works > Processing Queue](how-it-works.md#processing-queue)

**Processing stats** - The per-run table at the bottom of the episode page: what each run downloaded, detected, cut, held, and verified. [Web Interface > Processing stats](web-interface.md#processing-stats)

## R

**Recut** - Re-cutting the retained original audio using the current ad markers, with no download, transcription, or LLM involved. What "Approve & Recut" does. [How It Works > Reprocessing Modes](how-it-works.md#reprocessing-modes)

**Reprocess modes** - *Patterns + AI* (the default, everything), *AI Only* (skip the learned-pattern DB), *Re-detect Ads* (reuse the saved transcript, rerun detection), and *Recut*. [How It Works > Reprocessing Modes](how-it-works.md#reprocessing-modes)

**Resurrected** - A detection the validator rejected that the Ad Reviewer overruled and put back in the cut list. [Configuration > Ad Reviewer](configuration.md#ad-reviewer)

**Retention** - How long processed audio is kept before the episode resets to Discovered. The pre-cut original can have its own shorter window. [Web Interface > Overview](web-interface.md#overview)

## S

**Second scan** - See Verification pass.

**Silence snap** - Nudging a cut boundary to the nearest silence so the edit lands between words instead of inside one. [Audio Cues > Silence snap](audio-cues.md#silence-snap)

**Sliding windows** - Long transcripts are fed to the LLM in overlapping chunks so nothing is missed at chunk edges; the Windows column in Processing stats counts these. [How It Works > Sliding Window Processing](how-it-works.md#sliding-window-processing)

**Sponsor** - The advertiser behind a detection. Sponsors accumulate history per feed, which gets fed back into detection as a hint. [Web Interface > Sponsors and Normalizations](web-interface.md#sponsors-and-normalizations)

## T

**Text pattern** - A learned chunk of ad transcript matched against new episodes by similarity. Deterministic: if the same ad copy appears, it hits. [How It Works > Pattern Learning](how-it-works.md#pattern-learning)

**Transcript (VTT)** - The Podcasting 2.0 transcript MinusPod generates for the processed audio, with cut regions accounted for. [Podcasting 2.0](podcasting-2.0.md)

## V

**Validation** - The rule-based gate every detection passes before cutting: duration limits, confidence, overlap with your corrections, cue evidence, splice checks. [How It Works > Post-Detection Validation](how-it-works.md#post-detection-validation)

**Verification pass (Pass 2)** - After cutting, MinusPod re-transcribes the output audio and runs detection again to catch anything the first pass missed. "Clean" means the second scan found nothing left. [How It Works > Verification Pass](how-it-works.md#verification-pass)

## W

**Waveform Ad Editor** - The visual editor for a single detection: waveform, transcript context, and draggable boundaries, with the original audio for reference. [Web Interface > Waveform Ad Editor](web-interface.md#waveform-ad-editor)

**Webhook events** - Notifications MinusPod can send: Episode Processed, Episode Failed, Auth Failure, Limit Exceeded, Rate Limit Structural, Feed Refresh Failed. Each can also go out by email. [API & Webhooks > Events](api-and-webhooks.md#events)

---

[< Docs index](README.md) | [Project README](../README.md)
