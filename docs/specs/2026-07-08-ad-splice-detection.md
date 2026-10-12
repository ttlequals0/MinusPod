# Spec: End-of-episode ad fixes + audio splice-evidence signals + cross-fetch differential

Date: 2026-07-08
Target version: 2.40.0 (verify against Docker Hub + CHANGELOG before tagging)
Delivery: one PR, one build and release. Commits structured by layer for reviewability.
Status: implemented in 2.40.x

## Problem

Ads at the ends of episodes survive the pipeline. Two verified root causes
(both log- and audio-confirmed on production episodes):

1. Episode A (a weekly tech show on a Megaphone-hosted feed): a Megaphone DAI post-roll
   plays ~15 dB quieter than the show. Whisper vad_filter dropped it, so the
   transcript ends 42.4s before the audio. LLM windows are built only over the
   transcript (prompts.py:66-101), so no LLM saw it. The vad_gap detector
   flagged the exact span at 0.75; the POST_ROLL boost lifted it to 0.80; then
   ad_validator.py:514-515 clamped it to 0.79 because it has "no ad signals in
   transcript" - a condition that is always true for untranscribed audio
   (catch-22). REVIEW -> kept in audio. The resurrection reviewer saw an empty
   transcript span and rejected. The marker never got held_for_review, so it
   shipped silently (pendingReviewCount=0).

2. Episode B (a comedy show on an Audioboom-hosted feed): the terminal DAI block
   (4160.9s-EOF) was detected; first-pass LLM had the correct start at 4160.9s
   (conf 0.95). The merged marker came from a learned text pattern and the boundary
   reviewer's "adjust" verdict moved the start to 4172.0s - 11.1s inside the
   ad. The episode ships ending mid-sentence in a sponsor read.

Collateral defects confirmed during investigation (fixed in this PR too):

3. Reviewer verdict/reasoning contradiction: markers whose reviewer_reasoning
   says "no advertisement content whatsoever" carry verdict "confirmed" and
   get cut (Episode B's 90-second false-positive marker cut 90s of continuous monologue - proven
   content by cross-fetch correlation; 4 similar contradictions on the Episode A
   run). Two Episode A markers with verdict "failure: LLM call failed" were also
   cut - that fallback (reviewer unavailable -> trust pass 1) is intentional
   and stays, but gets documented and tested.

4. Cut-renderer overshoot: shipped audio keeps more than markers specify.
   Episode B: ~18.3s residual measured vs 11.1s marker arithmetic (~7s extra,
   matches the 6.5s timeSaved-vs-marker-sum delta). Episode A: applied cuts total
   ~17.2s less than the marker union.

## Empirical grounding for the audio-signature feature

Measured on both episodes (27 boundary/control points, ffmpeg feature battery,
cross-fetch alignment):

- No single audio feature separates splices from content standalone. Controls
  produced loudness steps up to +8.1 LU (larger than any true splice) and
  centroid steps that beat 4/6 real splices. MP3 bitstream analysis is dead:
  both platforms re-encode the full stream to uniform CBR.
- The discriminating feature is platform-dependent: Episode A/Megaphone splices
  show loudness+flatness steps; Episode B/Audioboom splices show encoded digital
  silence (RMS < -80 dBFS >= 0.5s scored 0 FP in ~1h of content; 10/10 events
  in the file were DAI-related).
- Both missed ads carry strong signatures at the candidate boundary: Episode A tail
  had the strongest signature of all 14 points tested there (-6.6 LU,
  flatness 0.08 -> 0.21); the Episode B onset sits in the only 1.4s-deep silence
  in that file.
- The audio features correctly veto false positives: the 90-second false cut
  contains zero splice evidence across its span; two Episode A transcript-gap
  suspects were baked-in content (bit-identical across fetches) and no audio
  feature fired on them.
- Cross-fetch differencing is near-definitive for DAI: re-fetched enclosures
  re-rolled every fill (Episode A tail 42.4s vs 57.2s; Episode B fills differ per
  break), while baked content stayed bit-identical.

Conclusion embedded in this design: audio evidence acts as a corroborator,
boundary snapper, veto, and LLM prompt signal at candidate boundaries - never
as a standalone segmenter.

## Layer 1: Tail fixes

### 1.1 vad_gap clamp bypass with audio corroboration

File: src/ad_validator.py (_verify_in_transcript, clamp at ~514-515).

- AdValidator gains access to the episode's stored audio analysis (wire the
  existing audio_analysis dict through AdValidator.validate from
  processing.py; it is already computed and saved for every episode).
- New helper _has_audio_corroboration(marker, audio_analysis) -> bool:
  true when a DAI transition pair or a >= 12 dB volume step (existing
  transition_detector/volume_analyzer output) sits within +-5s of the marker
  start or end, or (Layer 2) a splice-evidence event does, or (Layer 3) a
  differential region overlaps the marker.
- Behavior: corroborated vad_gap markers skip the clamp entirely (keep their
  boosted confidence, e.g. 0.80 -> ACCEPT/cut). Uncorroborated markers clamp
  as today.
- Regression test reproduces the Episode A scenario: untranscribed tail marker at
  0.75 + POST_ROLL boost + stored 15 dB transition -> cut.

### 1.2 Tail re-transcription without VAD

File: src/main_app/processing.py (_download_and_transcribe area),
src/transcriber.py.

- After transcription: if audio_duration - last_segment_end >=
  tail_retranscribe_min_seconds (default 10.0) and <=
  tail_retranscribe_max_seconds (default 600.0), extract the tail span and
  re-transcribe it with vad_filter=False (local faster-whisper path; API
  backends send the tail as its own chunk). Offset timestamps, run
  filter_hallucinations, append segments flagged novad_tail=True.
- Effect: LLM windows now cover the tail; detect_postroll can fire; the
  resurrection reviewer sees real text instead of an empty span.
- Guard: hallucination filter unchanged; the LLM still judges the text, so
  music-tail hallucinations do not auto-cut anything.

### 1.3 Review-queue visibility for uncorroborated tail markers

File: src/main_app/processing.py (_gate_validation_by_confidence area),
src/config.py (is_pending_review).

- A vad_gap marker at the episode tail (end within 5s of EOF) that stays
  REVIEW and uncorroborated gets held_for_review=True, so it lands in the
  pending-review UI instead of shipping silently.

### 1.4 Reviewer verdict/reasoning contradiction (bug 3)

File: src/ad_reviewer.py (_review_single parsing ~524-622,
_apply_reviewer_verdict_to_ad in processing.py ~932).

- Root-cause the parser first (implementation task): determine how "confirmed"
  verdicts ship with flatly contradictory reasoning, and whether the model
  output or the parsing is at fault.
- Regardless of parser outcome, add a contradiction guard: verdict in
  (confirmed, adjust) whose reasoning matches negative patterns ("no
  advertisement", "not an ad", "no ad content", "contains no ad") ->
  held_for_review=True instead of auto-cut. Never auto-reject on the guard
  (the reasoning could be the wrong half).
- "failure" verdicts keep the current fallback (trust pass-1 decision) -
  documented and covered by a test so the behavior is explicit.

### 1.5 Cut-renderer overshoot (bug 4)

File: src/audio_processor.py (compute_applied_cuts ~169-257, remove_ads,
filter_complex construction).

- Root-cause task: instrument applied cuts vs final duration; find where ~7s
  per episode leaks (fade handling, gap merging, beep insertion, or seek
  granularity).
- Acceptance: processed duration == original - sum(applied cuts) within 1.0s
  on test fixtures, and a unit test asserts marker arithmetic matches the
  rendered file via silence probes.

## Layer 2: Splice-evidence validator

### 2.1 New module: src/audio_analysis/splice_detector.py

Runs inside AudioAnalyzer.analyze (same decode pass family as existing
analyzers). Computes per episode:

- deep_silences: RMS events (10ms frames, 8kHz mono decode) below -80 dBFS
  for >= 0.5s, and below -70 dBFS for >= 1.4s, with depth + duration.
- loudness_steps: from the ebur128 momentary series volume_analyzer already
  produces: step = mean(M[t+1..t+6]) - mean(M[t-6..t-1]), gated > -70 LUFS,
  evaluated at candidate points (silence events, volume anomalies).
- spectral_steps: energy-weighted spectral centroid + flatness per 1s frame
  (power-weighted aggregation - naive per-frame averaging is silence-
  contaminated and measurably flipped a boundary's sign during investigation).

Stored in the audio_analysis JSON under splice_evidence with a version key.
Feature-flagged: splice_evidence_enabled default true.

### 2.2 Per-feed calibration

- Base rates measured from the feed's stored splice_evidence history (last N
  episodes, N=5 like cross-episode cue discovery): distribution of pause
  depth/duration, step magnitudes in content.
- Thresholds per feed picked so the expected content FP rate <= 1 event/hour;
  cold start uses conservative global defaults (digital silence -80 dBFS /
  0.5s; composite features corroborate only, never veto, until calibrated).
- Same storage/learning shape as positional_prior (per-feed, additive,
  DB-tunable).

### 2.3 Consumers (none cuts on audio alone)

a. Validator corroboration - shares the 1.1 interface: a splice event within
   +-3s of a marker boundary counts as corroboration.
b. Terminal boundary snap - when a marker's end is within 2s of EOF, scan
   backward from its start up to terminal_snap_window_seconds (default 30)
   for the strongest deep-silence event; snap the start to it only if the
   extension span is ad-classified text, untranscribed, or pattern-matched -
   never across content-classified speech. (Episode B: recovers exactly the
   11.1s; the transcript guard prevents eating the outro's final sentence,
   which sits 3s from an adjacent 1.6s pause.)
c. Zero-evidence veto - a proposed cut with duration >= veto_min_cut_seconds
   (default 60) from stages claude/text_pattern with no splice evidence inside
   the span or within +-3s of either edge gets demoted to REVIEW +
   held_for_review=True. It never silently un-cuts: humans see it in the
   queue. splice_veto_enabled default true; per-feed disable available.
   (Would have saved the 90-second false cut.)
d. Prompt signal - AudioEnforcer.format_for_window adds SPLICE EVIDENCE lines
   (deep silences with depth/duration, steps) with guidance; the reviewer
   prompt gets events near the candidate boundaries, which also fixes the
   "empty prompt for untranscribed span" resurrection failure.

Scope: pass 1 only. Pass 2 runs in processed-audio coordinates; mapping splice
evidence through cuts is deferred (same reasoning as positional prior's
deliberate pass-2 exclusion).

## Layer 3: Cross-fetch differential

### 3.1 New module: src/differential_fetcher.py

- Per-feed opt-in: differential_fetch_enabled default false.
- Timing: fetch #2 fires after transcription completes, i.e. naturally 15-60
  min after fetch #1 - no artificial sleep needed. Results are ready before
  first-pass detection so they feed prompts and validation.
- UA pool: fetch #2 uses a rotated User-Agent from a pool of real podcast
  client strings (Apple Podcasts iOS, Overcast, PocketCasts, AntennaPod,
  Castro), always different from fetch #1's UA. Rationale: ad decisioning
  keys on request fingerprint; we cannot vary IP, so UA + time spacing is the
  available variation. Pool lives in constants, DB-overridable.
- Failure handling: any fetch/decode/alignment failure is non-fatal (log,
  skip, status recorded). Size/duration sanity caps and a timeout guard.

### 3.2 Alignment engine

align_and_diff(run_file, refetch_file) -> regions:

- Decode both to 8kHz mono; silencedetect fingerprints; duration-matched
  chaining (DP) builds a coarse piecewise-constant offset map; windowed
  normalized cross-correlation refines offsets and locates jump points.
  (This productionizes the exact method used manually during investigation,
  which reproduced every pipeline cut within 1-2s on both episodes.)
- Output regions in run-file coordinates: kind=differential (audio differs
  across fetches -> dynamically inserted) or identical (content or baked-in
  ad), with per-region correlation confidence.
- Identical files / no offset jumps -> status no_differential. Reported as
  "no differential found", explicitly not "no DAI" (same-fill re-rolls are a
  known false negative).
- Stored per episode: dai_differential JSON {regions, refetch_meta{ua,
  spacing_s, size, duration}, status}.

### 3.3 Consumers

- Validator corroboration (shared interface): differential region overlapping
  a marker is the strongest corroboration class.
- Prompt signal: windows overlapping differential regions get a line stating
  the range is confirmed dynamically inserted (content differs across
  fetches).
- Detection candidates: differential regions become markers with
  detection_stage='dai_differential', confidence 0.95 - the same precedent as
  the fingerprint stage. They still pass validator + reviewer gates. This is
  the only signal that catches host-read DAI spots the LLM misses.
- Feed suggestion heuristic: enclosure URL chain matched against known
  DAI/prefix domains (pdst.fm, pscrb.fm, mgln.ai, megaphone, podtrac,
  chrt.fm, arttrk.com, clrtpod.com, dts.podtrac) -> UI hint on feed settings:
  "this feed likely uses dynamic ad insertion; enable differential fetch".

### 3.4 Frontend

- Feed settings: differential fetch toggle + DAI-likely hint badge.
- Episode view: new detection_stage label renders via existing marker UI;
  differential status shown in episode diagnostics.
- TypeScript check per project rule.

## Data / API changes (additive only - no data loss)

- audio_analysis JSON: + splice_evidence (versioned).
- episode: + dai_differential JSON.
- feed settings: + differential_fetch_enabled.
- markers: + corroborated_by (nullable string: transition_pair |
  splice_evidence | dai_differential) for observability.
- config keys (DB-tunable, following existing pattern):
  tail_retranscribe_min_seconds=10, tail_retranscribe_max_seconds=600,
  splice_evidence_enabled=true, splice_veto_enabled=true,
  veto_min_cut_seconds=60, terminal_snap_window_seconds=30,
  differential_fetch_enabled(per-feed)=false.
- openapi.yaml: new fields + version bump to 2.40.0.

## Testing

- Unit (pytest, PYTHONPATH=src, venv):
  - splice_detector on synthetic ffmpeg fixtures (tones + digital silence +
    level step + centroid shift at known offsets).
  - alignment engine on synthetic stitched files (same base audio, different
    inserted fills) - assert region recovery within 0.5s.
  - Episode A catch-22 regression: tail vad_gap + stored transition -> cut;
    without corroboration -> REVIEW + held_for_review.
  - tail re-transcription merge with mocked transcriber (offsets, flags,
    hallucination filter applied).
  - terminal snap: recovers span; transcript guard blocks snapping across
    content speech.
  - veto: long evidence-less cut demoted to REVIEW+held; corroborated cut
    untouched.
  - reviewer contradiction guard; failure-verdict fallback explicit test.
  - renderer invariant: processed duration == original - sum(cuts) +- 1.0s.
- Integration: pipeline run with mocked LLM asserting splice/differential
  lines reach detection and reviewer prompts.
- Full suite green; frontend npx tsc --noEmit; app builds and starts.
- Post-deploy verification: reprocess both example episodes; acceptance =
  Episode A tail ad cut (structurally: tail is transcribed + corroborated),
  Episode B terminal block cut from its true onset.

## Rollout

- CHANGELOG.md entry; version.py + openapi.yaml -> 2.40.0.
- Full code review on the diff; fix all findings.
- CodeQL clean before merge.
- GPU image build/scan/push first, then dispatch CPU workflow (mirror
  checklist: no Dockerfile base changes expected; requirements.txt additions,
  if any, mirror automatically since both install requirements.txt).
- Deploy via Portainer webhook; verify /api/v1/health; reprocess the two
  example episodes as the acceptance check.
- PR body and issue comments carry no session links and no
  environment-specific domains.

## Risks

- Tail re-transcription hallucinating on music tails: existing hallucination
  filter + LLM judgment gate it; no auto-cut from text alone.
- Snap eating outro content: transcript guard; covered by the Episode B-derived
  test case.
- Veto holding legitimate long cuts on splice-quiet feeds: demote-to-review
  (never silent un-cut) + per-feed disable + calibration cold-start rule
  (uncalibrated feeds corroborate only, never veto).
- Differential same-fill false negatives: reported as no_differential;
  UA pool + natural time spacing mitigate.
- Double download inflates the show's download stats on opted-in feeds:
  inherent to the technique; opt-in per feed keeps it bounded.
- PR size: single PR per user decision; commits ordered Layer 1 -> 2 -> 3 ->
  frontend so review can proceed layer by layer.
