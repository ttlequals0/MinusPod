# Audio Cue Detection

[< Docs index](README.md) | [Project README](../README.md)

---

Some shows play a short non-spoken cue, a chime or stinger, right before or after
an ad break. The transcript cannot capture it, so detection lands a beat late. A
cue never marks an ad on its own: the model must still find ad content in the
transcript, and the cue only sharpens an ad's boundaries. The one exception is
the opt-in cue-pair setting below, which can propose a missed break for the
reviewer to evaluate.

Audio cue detection is off by default. Turn it on under Settings > Experiments >
Audio Cue Detection. It applies only to episodes processed after you enable it.

**Cross-fetch differential detection is significantly more accurate on feeds
with cue templates configured.** A matched cue corroborates a differential
region so it cuts instead of holding for review, helps bound DAI slots, and
anchors the comparison timeline against fetch-to-fetch timing drift. If a
feed carries heavy dynamic ad insertion, setting up a cue template is the
recommended first step, before tuning the differential thresholds
themselves. See [Cross-Fetch Differential](how-it-works.md#cross-fetch-differential)
for how the two stages combine.

## How it finds the cue

There are two ways to find a cue, both gated by the master toggle.

- **Per-feed templates (recommended).** You mark the exact sound once on a recent
  episode. The server stores an MFCC fingerprint and a normalized
  cross-correlation matcher finds that same sound on every other episode of the
  feed. A template can move an ad's edges. When a feed has at least one enabled
  template, the matcher is used for that feed instead of the spectral fallback.
- **Spectral fallback.** When a feed has no templates and the experiment is on,
  an extra ffmpeg pass band-passes the audio to the cue's frequency band and
  flags brief loudness bursts that stand out from the in-band speech baseline.
  Each burst is handed to the detector as an `audio_cue` signal, the same way
  volume changes and DAI transitions already are. The spectral path is evidence
  only; it never moves an edge.

After detection, a boundary-snap pass moves the start and end of a detected ad to
the nearest high-confidence cue, capped by the reviewer's Max boundary shift, so
the cut lands on the chime rather than a beat into or out of the spoken read. An
ad whose edge was moved this way shows a "Cue snapped" badge in the detected-ads
list on the episode page.

### Silence snap

DAI-inserted ads are usually bracketed by short silences the platform injects at
the splice point. After cue snap runs, a second pass moves any remaining edge to
the midpoint of the nearest qualifying silence span.

Silence snap is per-feed and off by default. Enable it with the **Snap cuts to
silence** toggle in Feed settings on the feed detail page. Any edge already moved
by cue snap is skipped.

Three tunables under Settings > Experiments > Audio Cue Detection > Ad cutting
shape the detector:

- **Silence threshold (dBFS)** - audio quieter than this counts as silence
  (-90 to -20, default -50). Raise it if the feed's ad-break gaps carry room tone
  that never drops below the default; lower it if ordinary speech pauses count as
  silence.
- **Silence minimum duration (s)** - shortest quiet span that qualifies
  (0.1 to 5, default 0.3). Raise it if short natural pauses qualify as splice
  silences.
- **Silence snap max distance (s)** - farthest an ad edge may move to reach a
  detected silence (0.25 to 10, default 2). Widen it if edges consistently land
  farther than 2 s from the real splice; a wider window risks snapping to a
  pause inside the ad.

Two guards are always active: a snap that would shrink an ad below the removal
minimum is reverted, and a snap that would close the gap to a neighboring ad
below the merge threshold is rejected.

Enable it on feeds with dynamically inserted ads and audible silence gaps at the
splice points; the "Silence snapped" badge in the detected-ads list on the
episode page shows it working. Leave it off on feeds where the host pauses
mid-read.

## Cue types

You pick a type from a fixed dropdown rather than typing a label, so the model
always sees a consistent phrase. The type also decides which edge the cue may
move:

- **Ad-break boundary (both ends)** - the same sound plays entering and leaving
  the break. Snaps either edge of a detected ad. This is the default.
- **Ad-break start** - snaps an ad's start only, and opens a span when cue-pair
  gap-filling is on.
- **Ad-break end** - snaps an ad's end only, and closes a span.
- **Show intro / Show outro** - the show's own open or close sound, not an ad.
  The model is told to ignore it as a boundary so it stops mis-reading an intro
  sting as a break. Never moves a boundary.
- **Content transition (may or may not be an ad)** - a recurring segment-break
  sound that may or may not sit next to an ad. Never cut on its own; the model is
  told a transition happens there, not an ad boundary. With the per-feed **Snap
  to content transitions** toggle enabled in Feed settings, a matched transition
  cue may move a detected ad's edges the same way a boundary cue does. Enable it
  only after Test on episode confirms the template matches accurately on this
  feed.

## Marking a cue

Open the feed and expand Audio Cue Templates, then click `+ Mark cue` and pick a
recent episode whose original audio is still retained. The picker only lists
episodes that still have their original, since a cue can sit inside a removed ad.

The mark dialog uses the same waveform as the ad editor. Drag the green and red
pins to bracket the cue (0.2 to 10 seconds by default, up to 60 for a show intro or outro), or play to the sound and use
Set START / Set END at the playhead. The Snap to onset assist nudges an edge to
the nearest sharp amplitude rise so a short ding is easy to bracket tightly; turn
it off for a ramped sound with no clean attack. Pick a cue type, then Save, or
Save and preview to see every place the cue matches on that episode before it
goes live.

## Finding cues automatically

Instead of hunting for the sound by ear, let MinusPod find candidates for you.
On the episode page, expand Audio Cues and click **Find audio cues** (it needs
the episode's retained original audio). The scan decodes the whole episode in the
background, so it can take a minute on a long episode, and returns two kinds of
candidate:

- **Recurring stings** that repeat within the episode (the same short sound
  bracketing each break).
- **Intros and outros** shared with other episodes of the same feed.

The recurring-sting pass drops candidates that read as speech, so it surfaces
musical stings rather than repeated talk; the intro and outro pass keeps spoken
candidates, since a show's open is often spoken. Each candidate shows its
timestamp range and a kind label, with
a Play button to hear just that span and a **Make template** button that opens
the mark dialog seeded with the candidate's bounds and a suggested type. You
review and save it like any hand-marked cue. The scan suggests; it never creates
a template on its own.

A **Dismiss** button on each candidate handles the junk. It stores that sound's
fingerprint, and every later scan in the feed suppresses matching candidates in
any episode. Dismissed sounds move to a collapsed **Dismissed** list with a
per-entry Undo, so nothing is silently hidden. Episodes scanned before a
dismissal keep showing the sound until you hit **Rescan** on their results.

## Finding cues across episodes

The **Find across episodes** button in the Audio Cue Templates panel runs a
full-body cross-episode scan. Pick two to five episodes from the feed; all must
have retained original audio. The first episode in the list sets the coordinate
frame: returned candidate timestamps are in that episode's timeline.

The scan fingerprints every episode in the background. Full-duration decoding is
slow, so expect a wait for long episodes. Once done, it reports segments that
recur across the supplied episodes. Each candidate shows its start/end in the
target episode and how many supplied episodes share it. A **Make template**
button opens the mark dialog seeded with those bounds, the same as the
per-episode find-audio-cues flow.

API: `POST /api/v1/feeds/{slug}/cue-cross-episode-scan`. Supply `episodeIds`
(2-5) and poll with the same body. Returns `status` (scanning / ready / error)
and, when ready, a `candidates` array. Pass `rescan: true` to force a fresh run.

## Managing cues

Saved cues are listed with enable checkboxes. Change type swaps a cue's type in
place. Test on episode runs every enabled cue against any episode and reports
each cue's peak match score, which is the value to tune Template match score
against in Settings. Each template row has a Threshold button that sets a
per-template match score (0.30 to 0.99); leave it empty to inherit the per-feed
or global threshold. Values below 0.30 are rejected because noise commonly
scores in the 0.33-0.50 range and a sub-floor threshold would surface noise hits
as cue matches. Export downloads a cue as a portable file (a lossless audio
clip plus a manifest) to share with another install; Import loads one back. On a
feed that belongs to a network, Promote to network applies a cue to every show on
that network. Saving a non-ad cue type (intro, outro, or content transition)
asks for confirmation, since those types never cut.

### Optimizing the cue window

If a saved template matches inconsistently, the **Optimize window** row action
can help. It sweeps an 11x11 grid of start/end trims (0.1 s steps, up to 0.5 s
each way) and finds the window with the best mean peak-correlation score across
the source episode and up to four siblings. Results are cached per template and
invalidated whenever the window changes.

When the scan finishes, the row shows an inline before/after panel with the
current and proposed bounds, mean scores, and per-episode peaks. **Apply** moves
the window: the server re-extracts the stored audio blobs from the retained
source original. If the current window already scores highest, the panel says so
and skips the Apply button.

If the source episode's original audio has aged out, the optimizer returns a
409. The Apply step also returns 409 if the original disappeared between scan
and apply; the inline panel shows the error.

API: `POST /api/v1/feeds/{slug}/cue-templates/{templateId}/optimize-window`.
Returns `status` (scanning / ready / error) and, when ready, `proposedStartS`,
`proposedEndS`, `meanPeakScore`, `baselineMeanPeakScore`, `baselineWindow`, and
`perEpisode` peak scores. Returns 409 when the source original has aged out.

To apply, send `PATCH /api/v1/cue-templates/{id}` with `sourceOffsetS` and/or
`durationS`. Either field triggers blob re-extraction from the retained
original; returns 409 when the original is gone.

## Cue matches on an episode

When a feed has cue templates, the episode page shows where each enabled cue
matched, so you can confirm the matcher is keying on the right sound before you
rely on it to snap boundaries.

Confirming or rejecting matches also feeds tuning. Reviewed scores sharpen the
`Suggest threshold` sweep: rejections raise the proposed floor, confirmations
cap it, and a clean gap between the two places the suggestion directly. Once a
template collects three or more rejections above the current threshold, the
templates panel shows a hint chip: **Raise threshold** when the rejections sit
just above it, **Re-capture cue** when they spread across the score range.
Verdicts tune suggestions only; they never add or remove ads.

## Settings

Settings live under Settings > Experiments > Audio Cue Detection. They are
database settings configured in the UI and at `GET/PUT /api/v1/settings`; none of
them has an environment variable. The group headings below match the three cards
that appear in the UI when the toggle is on.

- **Enable audio cue detection** - master toggle, off by default. Turns on
  whichever mode applies to the feed.

### Finding cues

Spots candidate cues in the audio and brackets how long a cue may run.

- **Frequency band** - the low and high edges, in Hz, of the band the spectral
  fallback listens in. Chimes and bells usually sit between roughly 1.5 and
  8 kHz. The low edge must be below the high edge.
- **Prominence threshold** - how far above the in-band speech baseline, in dB, a
  sound must rise to count as a cue in the spectral fallback. Lower catches
  quieter cues but adds false positives.
- **Minimum confidence** - drops cues weaker than this. The model is never shown
  a cue below 0.80 confidence regardless of this value.
- **Capture minimum length (s)** - shortest cue you may bracket.
- **Capture maximum length (s)** - longest cue you may bracket.
- **Show-intro capture maximum (s)** - longest show-intro stinger you may bracket.
- **Show-outro capture maximum (s)** - longest show-outro stinger you may bracket.

### Matching templates

Scores saved cue templates against new episodes.

- **Template match score** - the cross-correlation score a marked template must
  reach to register on another episode (0 to 0.99, default 0.75). Lower catches
  more occurrences but risks false matches. Applies only to feeds with templates;
  otherwise the spectral knobs above are used. With the default floors a cue
  must still reach 0.80 confidence to affect a cut, so a lower value here mostly
  surfaces weaker cues in diagnostics.

  **Threshold precedence:** per-template > per-feed > global. A per-template
  value (set on the template row in the Audio Cue Templates panel) overrides the
  per-feed override (`cueTemplateScoreOverride` on the feed settings page), which
  in turn overrides this global setting. An empty value at any level means
  inherit the next level down. Two diagnostics bypass per-template values:
  typing a score threshold into `Test on episode` applies that one value to
  every template for that run, and the `Suggest threshold` sweep strips them so
  it can measure the full score distribution.

- **Voiceover attenuation (dB)** - off by default. When a cue is a music bed
  under a per-episode voiceover (the jingle is constant, the read varies), this
  attenuates the 800-3400 Hz speech band during matching so the cue keys on the
  bed. Only that band is touched, so bass beds and high chimes are unaffected;
  try 9-12 dB if a music-bed cue matches inconsistently.

### Ad cutting

Uses accepted cues to snap ad edges or build ads from cue pairs.

- **Snap confidence floor** - minimum cue confidence before a cue may move an ad
  edge. Higher is stricter.
- **Snap lead window (s)** - how far before an ad edge a cue may sit and still
  snap the boundary.
- **Snap lag window (s)** - how far after an ad edge a cue may sit and still snap
  the boundary.
- **Silence threshold (dBFS)** - audio quieter than this counts as silence for
  silence snap (-90 to -20, default -50). Applies only on feeds with the
  per-feed **Snap cuts to silence** toggle enabled.
- **Silence minimum duration (s)** - shortest quiet span that counts as a
  silence (0.1 to 5, default 0.3).
- **Silence snap max distance (s)** - farthest an ad edge may move to reach a
  detected silence (0.25 to 10, default 2).
- **Cue-pair confidence floor** - minimum cue confidence to synthesize an ad from
  a cue pair. Higher than the snap floor because this creates an ad rather than
  refining one.
- **Cue-pair minimum break (s)** - shortest span between two cues that may form a
  synthesized ad.
- **Cue-pair maximum break (s)** - longest span between two cues that may form a
  synthesized ad.
- **Cue-pair maximum break (fraction of episode)** - reject a cue pair spanning
  more than this fraction of the episode. 0 disables it.
- **Create ads from cue pairs** - off by default. When two high-confidence cues
  bracket a plausible break the model missed, synthesize a cue-only ad for that
  span. The reviewer still evaluates it. This relaxes the "cue is supporting
  evidence only" rule, so leave it off until you trust the matcher on a feed.

## Per-feed overrides

Per-feed controls sit on the feed detail page under Feed settings:

- **Cue threshold** (`cueTemplateScoreOverride`) - per-feed match score override,
  0.30 to 0.99. Empty means use the global Template match score. A per-template
  value takes precedence over this. The `Suggest threshold` action in Test on
  episode can write this override for you via its Apply to this feed button.
- **Cue tuning overrides** (collapsible) - per-feed overrides for seven advanced
  knobs. Empty for any field means inherit the global value:
  - **Pair synthesis** (`cueCreateFromPairsOverride`) - tri-state: on, off, or
    inherit global.
  - **Pair min break** (`cuePairMinBreakOverride`) - seconds.
  - **Pair max break** (`cuePairMaxBreakOverride`) - seconds.
  - **Pair max fraction** (`cuePairMaxBreakFractionOverride`) - 0-1.
  - **Snap confidence** (`cueSnapConfidenceOverride`) - 0-1.
  - **Snap lead** (`cueSnapLeadOverride`) - seconds.
  - **Snap lag** (`cueSnapLagOverride`) - seconds.
- **Snap cuts to silence** (`silenceSnapEnabled`) - off by default. Edges not
  already moved by cue snap are moved to the midpoint of the nearest qualifying
  silence. See Silence snap above.
- **Snap to content transitions** (`transitionSnapEnabled`) - off by default.
  Content-transition cues may snap ad edges the same way boundary cues do.

Overrides are set at `PATCH /api/v1/feeds/<slug>` and apply to episodes
processed after the change.

## Requirements and notes

Marking a cue requires the source episode's retained original audio, because a
cue can sit inside a removed ad. `keep_original_audio` is on by default; there is
no backfill, so only episodes processed after upgrading to 2.9.0 can be used to
mark a cue. If you set a shorter `original_retention_days` than `retention_days`,
originals age out earlier and those episodes drop out of the cue picker even
though the processed audio remains. Each template stores its own raw audio, so a
saved cue keeps working after its source episode's original is gone.

The Stats page shows an Avg Audio Cues card and a Total Audio Cues figure. Both
read zero until the experiment is enabled. Detection quality depends on the show,
so start with one whose cue is clear.

## Screenshots

#### Cue templates panel
| Desktop | Mobile |
|---------|--------|
| <img src="screenshots/cue-templates-desktop.png" width="500"> | <img src="screenshots/cue-templates-mobile.png" width="200"> |

#### Marking a cue
| Desktop |
|---------|
| <img src="screenshots/cue-editor-desktop.png" width="500"> |

#### Cue matches on an episode
| Desktop | Mobile |
|---------|--------|
| <img src="screenshots/cue-matches-desktop.png" width="500"> | <img src="screenshots/cue-matches-mobile.png" width="200"> |

---

[< Docs index](README.md) | [Project README](../README.md)
