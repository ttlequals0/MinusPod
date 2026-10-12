# Detection tuning: actionable pass-2 misses, scoped rejections, label hygiene (2.76.0)

Date: 2026-07-23
Status: implemented in 2.76.0

## Problem

Evidence from the 2.75.0 investigation (2026-07-23):

1. Pass-2 verification detects missed ads but discards them unless they
   overlap an accepted ad (a sponsor read at confidence 0.80: seen,
   logged, dropped). The overlap path works (a 3963-4131s span
   was caught and cut); the standalone path does not exist.
2. Rejecting a held differential span creates cross-episode
   false-positive texts that suppress future matching feed-wide (12
   regions + 17 texts on one feed).
3. LLM reasoning leaks into sponsor labels ("Product segment", "Regular
   discussion about an app resumes"), producing junk stats and
   duplicate overlapping markers for one ad read.
4. Tiny uncorroborated differential slices can still become holds on
   cold-pattern feeds (a 7.7s hold).
5. learn_from_detections has the same silent-skip shape as the fixed
   cold-start bug (no live cold caller today).
6. One reviewer prose/number mismatch observed (one marker end 1350.9s
   in marker vs 1332.8s emitted); unverified whether adjusts apply.

All thresholds introduced here are tunable via the existing settings
API and the Settings UI. Deferred to future work: dense differential
probing with a measured-difference threshold; second refetch.

## 1. Actionable pass-2 misses

Where: the pass-2 result handling in src/main_app/processing.py (the
consumer of verification_pass run results) plus
pattern_service.record_verification_misses.

Behavior for each pass-2 miss (original-timeline bounds, as already
returned by verification_pass):

- If it overlaps an accepted ad or qualifies for the existing hold
  auto-approve: unchanged (current behavior).
- Else if confidence >= verification_miss_autocut_min_confidence AND
  that setting is enabled: treat as a confirmed correction and recut,
  reusing the existing auto-approve guard preconditions (retained
  original audio, saved segments). Disabled by default.
- Else if confidence >= verification_miss_hold_min_confidence (default
  0.60): create a held-for-review marker with new hold reason
  HOLD_REASON_VERIFICATION_MISS = 'verification_miss' (config.py,
  alongside the other hold-reason constants; is_pending_review in
  src/config.py remains the single source of truth and must include
  it). The marker carries sponsor, confidence, and bounds; it appears
  in the existing Held for Review UI rows, where the waveform editor,
  one-tap confirm, and reject flows already work (2.72.0 machinery).
- Else: log-only, as today.

Learning thresholds LEARNING_MIN_CONFIDENCE (0.85) and
LEARNING_MIN_CONFIDENCE_LONG (0.92) move from constants to DB-backed
settings with those defaults (constants stay as the registry defaults).

New settings (ad-detection settings payload, stage-tunable style):

| key | default | range |
|---|---|---|
| verification_miss_hold_min_confidence | 0.60 | 0.0-1.0 |
| verification_miss_autocut_min_confidence | disabled ('' or 0 = off) | 0.5-1.0 or off |
| learning_min_confidence | 0.85 | 0.5-1.0 |
| learning_min_confidence_long | 0.92 | 0.5-1.0 |

## 2. Scoped rejection fallout + cleanup

- Rejecting a marker whose hold reason is differential_uncorroborated
  (or whose detection method is dai_differential) still records the
  same-episode false-positive region, but no longer creates a
  cross-episode false-positive text.
- One-shot version-gated migration (startup backfill pattern,
  _BACKFILL_VERSION mechanism in src/main_app/__init__.py):
  deactivate, never delete, existing cross-episode false-positive
  texts that are traceable to differential-hold rejections. The
  implementer must verify provenance exists on those rows (correction
  id or type); if provenance is absent, the migration deactivates
  nothing, provenance is added going forward, and the cleanup of the affected feed is
  done via a documented one-off maintenance endpoint call instead.
  No data is deleted in any path (CLAUDE.md: never lose data).

## 3. Sponsor label hygiene

- At detection-merge/marker-persistence time, run sponsor labels
  through the existing is_sponsor_reasoning_rationale filter plus a
  narrow segment-name rule (label ending in " segment", case
  insensitive). Matching labels are replaced with None/'unknown';
  the ad itself is unaffected.
- When two accepted markers overlap by >= 80 percent of the smaller
  one, merge them into one marker spanning the union; keep the
  non-junk sponsor label (if both are real and differ, keep the one
  from the higher-confidence detection).

## 4. Smarter differential stage

The differ stops meaning "not proven identical" and starts meaning
"measured different", with cue templates as a first-class signal.
All changes live in src/differential_fetcher.py, the dai_differential
candidate builder in src/ad_detector/, and the validator's
corroboration rules.

### 4a. Dense probing and measured difference

- Probe every silence-delimited block against the refetch (today: one
  probe per group). Every region in the stored map carries a measured
  peak correlation; the hard-coded corr 0.0 for unprobed gaps goes
  away. Cost: a few hundred FFT correlations per episode.
- A region only becomes an ad candidate when its measured correlation
  is at or below differential_measured_corr_max (default 0.60,
  tunable). Unmeasurable regions (probe window unusable) stay in the
  region map but never become candidates.
- Drift re-probe: before declaring a block different, retry once with
  a widened search window, so cumulative timing drift after a
  different-length ad swap does not cascade into false differentials.

### 4b. Cue fusion (feeds with cue templates)

Cue positions for the primary audio are already computed; add one
template scan over the refetched audio for anchor matching.

- Cue-corroborated candidates: an otherwise-uncorroborated
  differential candidate whose start or end lands within the existing
  cue snap tolerance of a detected cue counts as corroborated
  (structural + acoustic evidence) and cuts instead of holding.
- Cue-pair-bounded candidates: a measured-different region bracketed
  by a break-start/break-end cue pair is a first-class high-confidence
  candidate even when text patterns and Claude are silent.
- Cue anchor alignment: when the same cue is found in both fetches,
  the offset between the two positions re-anchors the correlation
  timeline, absorbing drift at its source.

### 4c. Hold minimum duration

- An uncorroborated differential candidate shorter than
  differential_hold_min_seconds (default 10, 0 disables) is dropped
  from marker creation entirely (still present in the stored region
  map). Corroborated candidates (pattern, cue, or overlap) are
  unaffected.

| key | default | range |
|---|---|---|
| differential_measured_corr_max | 0.60 | 0.0-1.0 |
| differential_hold_min_seconds | 10 | 0-120 |

Remaining known limit (documented, not fixed here): a single refetch
cannot see DAI when both fetches receive identical ad fill; the
second-refetch idea stays in future work.

## 5. learn_from_detections parity

- Add self.initialize_client() at the top of
  AdDetector.learn_from_detections, matching its three siblings.

## 6. Reviewer adjust verification (investigation first)

- Reproduce or trace the mismatch case: reviewer emitted end
  1332.8s (delta -18.14s applied as adjust) but the final marker reads
  1350.9s. Determine whether reviewer adjusts are applied to the cut
  and the stored marker consistently. If a bug is found, fix within
  this release; if behavior is correct (e.g. marker intentionally
  stores pre-adjust detection bounds), document it in
  docs/how-it-works.md instead.

## API and UI

- Settings API: the four item-1 settings plus item-4's setting join
  the existing ad-detection settings GET/PUT payload (same _sv shape,
  defaults block, reset behavior).
- Settings UI: new controls in the Ad Detection settings area
  (verification subsection for item 1, differential subsection for
  item 4), following the
  existing Tailwind/CollapsibleSection conventions. Number inputs or
  sliders with the ranges above; autocut shows as an opt-in toggle
  plus threshold.
- Held for Review rows: hold reason chip must render
  'verification_miss' with a human label ("Verification catch").
- openapi.yaml updated for the settings payload additions.

## Docs

- docs/configuration.md: new tunables with defaults and guidance.
- docs/how-it-works.md: pass-2 miss flow (hold / autocut / learn),
  scoped rejection behavior, and the reworked differential stage.
- docs/audio-cues.md and docs/how-it-works.md both get an explicit
  note: cross-fetch differential detection is significantly more
  accurate on feeds with cue templates configured (cues corroborate
  differential regions, bound DAI slots, and anchor the comparison
  timeline), so setting up cue templates is the recommended first
  step for feeds with heavy dynamic ad insertion.
- docs/glossary.md: entries for verification miss, corroboration,
  differential hold, false-positive text.
- CHANGELOG.md 2.76.0 entry; version.py and openapi info.version.

## Testing

- Backend: unit tests per item (miss -> hold / autocut-gated / log-only
  by confidence; rejection creates no cross-episode text for
  differential holds; migration provenance path both ways; label
  sanitizer; overlap merge; min-duration gate; learn_from_detections
  cold call). Differential: dense probing produces measured corr for
  every region on synthetic PCM fixtures; candidates require measured
  difference; drift re-probe recovers a shifted-identical block; cue
  corroboration flips a hold to a cut; cue-pair-bounded candidate is
  emitted; cue anchor correction absorbs a synthetic offset. Existing
  suites stay green.
- Frontend: settings controls render/save; hold-reason chip label.
- Gates: pytest, ruff, tsc, eslint, full frontend suite.

## Out of scope (recorded for later)

- Second refetch on suspiciously-identical comparisons (the
  sticky-ad-fill blind spot).
- Automatic cue-template discovery for feeds without templates.
