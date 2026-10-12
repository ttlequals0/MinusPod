# Cue play UX, content-transition prompt fix, pairing parity, threshold auto-suggest

Issue #350 follow-ups from user feedback on 2026-07-01, plus fine-tuning of
the cue detection path found while reading the code. Five items, shipped on one
branch (`feature/issue-350-cue-play-ux`) with one commit per item.

## Background

The reporter raised two UI points and attached a content-transition
cue. Testing that cue against three real episodes of the same show with the shipped
matcher (default `AUDIO_CUE_TEMPLATE_SCORE = 0.75`, voiceover attenuation off)
gave peak scores 0.957-0.968 and 5-7 matches per episode, matching the
reporter's own report. Detection defaults are adequate for this clean cue; the global
default threshold stays 0.75 (item 5 adds a suggest tool, not a default change).

Reading the consuming code surfaced three fine-tuning gaps in how cues are
consumed: a reviewer-prompt inconsistency for `content_transition` (item 3), a
cue-pairing phantom on single both-ends cues (item 4), and a match threshold
whose real lever is masked by a separate effect floor (item 5).

## Item 1 - Make "Play selection" discoverable (cue mark dialog)

Problem: the play-selection control renders as plain muted text ("Play
selection") on the transport's secondary row with no icon, using `ctrlBtn`.
Two users in the thread missed it entirely. It reads as a label, not an action,
and the eye goes to the round primary Play button above it.

Change (all in `frontend/src/components/ad-editor/`):
- `controlStyles.ts`: add one amber-accented icon-button recipe.
- `TransportBar.tsx`: move the play-selection control from the secondary row
  into the primary transport cluster, immediately right of the main Play/Pause
  button. Render it as a compact bracketed play control `[ (>) ]` - a lucide
  `Play` glyph flanked by bracket marks, amber border + text so it ties to the
  amber selection region and the "IN SELECTION" badge. Keep
  `title="Play the bracketed selection only"` and add an `aria-label`.
- The secondary row keeps only the selection readout.
- The control still renders only when `onPlaySelection` is passed. Today that
  is the cue modal only; the ad editor does not pass it, so its transport is
  unchanged.

## Item 2 - Per-cue play button (audio cue templates list)

Problem: to hear a saved cue the user must export the zip, extract it, and open
the file. When picking among several cues this loses track of which is which.

Backend (`src/api/cue_templates.py`):
- New route `GET /api/v1/cue-templates/<int:template_id>/audio`.
- Returns the stored `pcm_blob` as an inline `audio/wav` response
  (`Content-Disposition: inline`), built with the stdlib `wave` module from
  `pcm_blob` + `pcm_sample_rate` (fall back to `SAMPLE_RATE_HZ`). No ffmpeg;
  cues are short. `422` when a template has no `pcm_blob`, mirroring `export`.
  `404` when the template id is unknown.

Frontend:
- `src/api/cueTemplates.ts`: add `cueTemplateAudioUrl(id)` returning
  `/api/v1/cue-templates/${id}/audio` (cookie auth carries automatically, same
  as `cueTemplateExportUrl`).
- `src/pages/feeds/CueTemplatesPanel.tsx`: add a small Play/Stop icon button on
  each template row, before the "Export" link. One shared `<audio>` element per
  panel plays a single cue at a time; the active row shows a Stop (Square)
  icon, the rest show Play. Clicking another row switches to it; clicking the
  active row stops. Non-playable templates (no raw audio) do not show the
  button.

## Item 3 - content_transition reviewer prompt fix (fine-tune)

Problem: the two LLM stages describe a `content_transition` cue differently.

- `src/audio_enforcer.py:138-141` (ad detector prompt) already special-cases
  `cue_type == content_transition`: "a recurring content/segment transition,
  NOT necessarily an ad boundary". Correct.
- `src/ad_reviewer.py` `_format_cue_section` (ad reviewer prompt) only checks
  `role == non_ad`, so `content_transition` is bucketed with `show_intro` /
  `show_outro` under "SHOW INTRO/OUTRO MARKERS NEARBY ... the show's
  open/close, NOT an ad boundary" and "Do not anchor this ad's boundary to
  these markers; they are the show's own intro/outro, not break stingers."

For a cue that reliably brackets ad breaks (the reporter's cue), the reviewer
stage tells the model to ignore exactly that evidence, contradicting the
detector stage. The 2.29.0 relabel updated the UI, the save dialog, and the
detector prompt but missed this reviewer path.

Change (`src/ad_reviewer.py` `_format_cue_section`):
- Split `content_transition` out of the intro/outro bucket. Render it in its
  own group described as a recurring segment/ad transition that may or may not
  be an ad boundary - supporting evidence the model may use to place a
  boundary, without forcing a cut. Mirror the `audio_enforcer` wording so the
  two stages agree.
- `show_intro` / `show_outro` keep the existing "show's own open/close, not an
  ad boundary" wording.
- Gate on `details.get('cue_type')`, which the matcher already sets on every
  template signal.
- No role/snap/pair behavior change: `content_transition` stays `non_ad` and
  still never snaps an edge or opens a pair. Only the reviewer prompt text
  changes.

## Item 4 - Cue-pairing parity: LLM-ad-edge cue orientation (fine-tune)

Problem: a single recurring cue typed `ad_break_boundary` (role `boundary`)
that brackets both ends of every ad break except the opening ad (which has no
entry cue) produces an ordered match sequence exit, entry, exit, entry... with a
leading unpaired exit. With cue-pair synthesis on (`audio_cue_create_from_pairs`,
opt-in), the greedy left-to-right pairing in `cue_pair_ads.py:127-193` pairs
cue0(exit)+cue1(entry), bracketing content, which yields a phantom ad, and the unpaired
opening ad offsets the phase for the whole episode. The reporter's real dilemma:
`ad_break_boundary` risks this phantom; `content_transition` is safe but
forfeits all mechanical help.

Root cause (verified): `AUDIO_CUE_START_EDGE_ROLES = ('start','boundary')` and
`AUDIO_CUE_END_EDGE_ROLES = ('end','boundary')` (config.py:393-394), so a
`boundary` cue passes both the opener gate (line 131) and the closer gate
(line 137). A template stamps one role for all occurrences, so start/end cannot
express a single both-ends sound.

Change (opt-in pair path only; snap untouched):
- `src/ad_detector/cue_pair_ads.py`: before the greedy loop, orient each
  `boundary`-role template cue against the first-pass LLM `ads` already passed
  in (raw and un-snapped; synthesis runs before snap). A cue that is the first
  eligible cue after an LLM ad END, within `orient_window_s`, is an EXIT ->
  ephemeral `effective_role = 'end'` (closer-only). A cue that is the last
  eligible cue before an LLM ad START, within the window, is an ENTRY ->
  `effective_role = 'start'` (opener-only). Ambiguous (both or neither) stays
  `boundary`. The two gates read `effective_role`. One detected opening pre-roll
  pins the phase and the existing `consumed[]` cascade produces correct
  entry->exit pairs across the episode. Demotes only on positive LLM evidence,
  so unclassified feeds behave exactly as today.
- Only `boundary` cues are oriented; `start`/`end` two-sound feeds are left
  byte-for-byte unchanged; a zero-LLM-ad episode returns immediately (greedy
  fallback preserved).
- Corrections from review:
  - Default `effective_role` to `role` inside the `_Cue` dataclass, so a dropped
    init loop can never silently disable all synthesis.
  - Compute the first-after / last-before adjacency over boundary/edge-eligible
    cues only. The `cues` list also holds non_ad template cues
    (content_transition, intro, outro), which would otherwise defeat orientation.
  - Demote conservatively: never demote a cue to exit-only when doing so would
    suppress a genuine missed-break pair that has no nearby LLM ad (the one
    recall-regression case the verifier found). Only demote when positive LLM
    evidence orients the cue AND the demotion does not strand a would-be
    missed-break pair.
- New config `AUDIO_CUE_PAIR_ORIENT_WINDOW_SECONDS = 20.0` (0 disables; must
  exceed the teaser gap, the reporter sees 5-10s) + DB setting
  `audio_cue_pair_orient_window_seconds` wired through defaults dict, GET,
  PUT-validate (0.0-120.0), and reset. API-only for now (no frontend settings
  field), matching the `audio_cue_formant_atten_db` precedent. New role literals
  `AUDIO_CUE_ROLE_START = 'start'` and `AUDIO_CUE_ROLE_END = 'end'` in config so
  the demotion values are single-sourced.
- Drive-by: `audio_cue_pair_max_break_fraction` is missing from the
  `database/settings.py` defaults dict (its reset is a silent no-op today). Add
  it while wiring the new key.

Invariants preserved: runs only inside the opt-in pair pass, only on `boundary`
cues, only rewrites an ephemeral pass-local role (persisted `details.role`
untouched, so telemetry and every stored-role consumer are unaffected), can only
remove eligibility (never invent a pair). No schema change, no data loss. The
snap path and the `content_transition` (non_ad) path share no code with this.

Residual (not a regression): if the LLM misses the opening pre-roll entirely,
cue0 gets no anchor and the phantom can recur - same as today; the user retains
`content_transition` as the escape hatch.

## Item 5 - Threshold auto-suggest, effect-floor-aware (fine-tune)

The ZNCC match score is the stored cue confidence
(`cue_template_matcher.py:281`), and a cue must clear a separate 0.80 effect
floor to change anything - MIN_SIGNAL_CONFIDENCE (hardcoded,
`audio_enforcer.py:24/42/109`) for the LLM prompt, `audio_cue_snap_confidence`
(default 0.80) for snap, `audio_cue_pair_confidence` (0.85) for pairs. So
the reporter's soft cues at 0.67-0.74 sit below the effect floor: lowering the
match threshold to 0.65 would surface them in diagnostics but change zero cuts.
The right answer is an honest auto-suggest, not a blind default drop.

Decision: keep `AUDIO_CUE_TEMPLATE_SCORE = 0.75`. No per-feed threshold (user
constraint). No change to the 0.80 effect floor (deferred).

Change:
- New pure helper `src/audio_analysis/cue_threshold_suggest.py`:
  `suggest_cue_threshold(occurrence_scores, per_template_peaks, effect_floor)`
  sorts scores, finds the widest gap within a plausible band between the noise
  cluster (~0.50) and the signal cluster, returns a suggested value at the gap
  midpoint (clamped) with confidence low/partial/high and an effect-floor
  annotation. No IO; unit-testable under `PYTHONPATH=src pytest`.
- New backgrounded route `POST /feeds/<slug>/cue-threshold-suggest` (mirror the
  cue-scan validation preamble): sweep the picked episode + up to N-1 recent
  completed siblings at a low floor (~0.35) via
  `AudioCueTemplateMatcher.detect_with_debug`, collect every match's
  `details['score']` + per-template peaks, run the helper. Multi-episode decode
  times out synchronously (the reason the cue-candidate scan was backgrounded),
  so use the background-thread + claim + poll pattern with a new non-lossy
  result-cache table mirroring the cue-candidate scan table. Raise the matcher's
  `max_matches_per_template` for the sweep so the low tail is not truncated.
- Live effect floor (verifier correction): the route computes
  `effect_floor = min(0.80 hardcoded prompt floor, live audio_cue_snap_confidence,
  and audio_cue_pair_confidence only when audio_cue_create_from_pairs is on)`.
  `effectFloorWarning` is `signal-below-floor` when the signal floor sits below
  the effect floor, null otherwise (two values only).
- Frontend: `src/api/cueTemplates.ts::suggestCueThreshold(slug, episodeId)` +
  response type; `CueScanModal` in `CueTemplatesPanel.tsx` gets a "Suggest
  threshold" button and a result block (noise ceiling / signal floor / gap /
  suggested), plus "Apply as global default" behind a confirm that states it
  affects ALL feeds. Apply writes `audioCueTemplateScore` via the existing
  validated settings path. Disable Apply on low confidence or when the signal
  cluster is below the effect floor; allow-with-warning otherwise.
- Honesty note: append the 0.80-effect-floor note to the `templateScore` field
  help text in `AudioCueDetectionSection.tsx` (the sibling spectral field
  already has it).
- New config constants: `AUDIO_CUE_SUGGEST_FLOOR` (~0.35),
  `AUDIO_CUE_SUGGEST_MAX_EPISODES` (~5), `AUDIO_CUE_SUGGEST_MIN_GAP` (~0.08),
  `AUDIO_CUE_SUGGEST_MIN_SIGNAL` (~3), `AUDIO_CUE_SUGGEST_BAND` ((0.40, 0.95)),
  `AUDIO_CUE_SUGGEST_MARGIN` (~0.02), and `AUDIO_CUE_EFFECT_FLOOR = 0.80` as a
  display/annotation mirror only (do not rewire `audio_enforcer`'s hardcoded
  value here).

## Out of scope

- Per-feed threshold override (user constraint: global-only).
- Lowering the 0.80 effect floor to make soft cues (0.67-0.74) actually affect
  cuts. Deferred; higher precision risk, needs its own cut-level evidence.
- Unifying the hardcoded 0.80 in `audio_enforcer` with the new
  `AUDIO_CUE_EFFECT_FLOOR` constant (behavior-adjacent to a production floor;
  left as an annotation mirror for now).
- Exposing the pairing orientation window in the frontend settings UI (API-only
  for now, per the `formant_atten` precedent).

## Testing

- Frontend: `cd frontend && npx tsc --noEmit`.
- Backend: `PYTHONPATH=src .venv/bin/python -m pytest tests/ -v`.
- New backend tests:
  - `/cue-templates/<id>/audio` route: inline WAV for a template with PCM, 422
    for one without, 404 for unknown id.
  - Reviewer prompt (`ad_reviewer._format_cue_section`): content_transition
    renders in its own group, not the intro/outro group; intro/outro unchanged.
  - `cue_pair_ads` orientation: exit-first single-cue feed with a detected
    opening ad -> pairs shift to entry->exit, no content phantom; entry-first ->
    identical to today; two-sound start/end feed -> unchanged; zero-LLM-ad ->
    greedy fallback; real missed break with no nearby LLM ad -> still
    synthesized (no recall regression); `orient_window_s = 0` -> orientation off.
  - `suggest_cue_threshold` helper: bimodal -> gap midpoint; unimodal -> low
    confidence; signal cluster below effect floor -> warning; thin sample ->
    low; live effect floor picks up a lowered snap/pair confidence.
  - `/cue-threshold-suggest` route: sweep + suggestion shape; global-only apply.
- Version + docs: bump `version.py` and `openapi.yaml` (new `/audio` and
  `/cue-threshold-suggest` routes); update CHANGELOG.md.
- Manual: mark a cue and confirm the bracketed amber play-selection control is
  obvious in the primary row; play a saved cue from the templates list; run
  "Suggest threshold" on a feed with a clean cue.
