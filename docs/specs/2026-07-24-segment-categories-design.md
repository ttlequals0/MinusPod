# Segment categories design (2.78.0, issue #565)

Date: 2026-07-24
Status: implemented in 2.78.0

## Goal

SponsorBlock-style typed segments: every detected marker carries a
structured category, and each feed resolves a per-category action
(remove, beep, keep) at cut time. Default behavior is byte-identical
to today (everything removes). Closes the "no clean way to keep a
flagged segment" gap and retires the prompt-override workaround.

User decisions (brainstormed):
- Scope: phase 1 (ad-side categories, already detected) plus per-feed
  opt-in detection of intro/outro/housekeeping segments.
- Defaults: every category defaults to remove; zero behavior change on
  upgrade until a user changes an action.
- Feasibility corrections vs the issue text: intro/outro/recap are not
  in the default detection prompt today (new detection, opt-in only);
  organic self-promo is currently excluded by the prompt and stays
  excluded unless the feed opts into segment detection.

## Categories

Enum (single source of truth in src/config.py):

| category | covers | produced by |
|---|---|---|
| sponsor | paid host-read or produced reads, DAI, platform-inserted pre/post rolls | all stages; default when unlabeled |
| cross_promo | other-show and network promos | LLM stages |
| self_promo | Patreon, merch, subscribe, donate for the show itself | LLM stages |
| interaction | follow/rate/review prompts | LLM stages |
| intro | show intro/theme | LLM stage, only when feed opted in |
| outro | outro and credits | LLM stage, only when feed opted in |
| recap | "coming up", headlines bumpers, "listen next" housekeeping | LLM stage, only when feed opted in |

Provenance rules:
- Detection prompt gains a required `category` field on each emitted
  ad object, restricted to the enum. Verification and reviewer prompts
  echo the category through unchanged.
- Fingerprint, text-pattern, and differential detections default to
  `sponsor` (differential also `sponsor`; DAI is a sponsor delivery
  mechanism, not a category).
- Any missing or unknown category value falls back to `sponsor` at
  marker-save time, so action resolution can never dangle.

## Actions and resolution

Action enum: `remove` (cut, today's behavior), `beep` (replace with
the existing beep-replace path in audio_processor), `keep` (do not
alter audio; marker persisted with `was_cut = 0` and
`action_applied = 'keep'`).

Resolution: per-feed override map, else global default map, else
`remove`. Mirrors the existing max-episodes per-feed-override pattern.

Storage:
- Global: settings key `segment_category_actions`, JSON object
  category -> action, seeded with every category = `remove`.
- Per feed: `podcasts.segment_category_actions` TEXT NULL (JSON, only
  overridden categories present; NULL = inherit global).
- Opt-in detection: `podcasts.detect_show_segments` INTEGER NULL
  (1 = detect intro/outro/recap; NULL/0 = off, default).

All schema changes are additive (ALTER TABLE ADD COLUMN via
`_add_column_if_missing`; new `episode_ad_markers.category` TEXT NULL,
read as `sponsor` when NULL). No data loss, no backfill.

## Pipeline changes

1. Detection (pass 1): unchanged flow; markers now carry category.
   When `detect_show_segments` is on, the LLM window prompt appends a
   section instructing detection of intro/outro/recap segments with
   the same timestamp discipline as ads.
2. Validation/review: markers whose RESOLVED action is `keep` bypass
   hold rules and boundary review (nothing will be cut, so there is
   nothing to hold or adjust); they are saved as informational
   markers. Remove/beep markers flow through validator, reviewer,
   held-for-review, and corrections exactly as today.
3. Cut: markers partition by resolved action. `remove` spans cut,
   `beep` spans beep-replace (existing machinery), `keep` spans left
   intact. Boundary sweeps (`_complete_cut_tails`,
   `_snap_terminal_starts`) operate only on remove/beep spans.
4. Pass-2 verification: before acting on verification findings, any
   found span overlapping a kept marker (mapped to the processed
   timeline) is dropped with a debug log. This prevents kept segments
   from being re-cut or held as verification misses.
5. Notifications: `ads_not_cut` excludes keep-action markers (a kept
   segment is a decision, not a miss). `ads_held` unchanged.
6. Learning: kept markers never create corrections or cross-episode
   false-positive texts. Pattern learning still learns from them as
   detections and stores category on `ad_patterns.category`
   (additive), so detection keeps improving regardless of action.
7. Recut: category on markers persists through recut-from-retained-
   original. Changing an action map does not auto-recut; the feed
   settings page gains a "Re-render episodes with current segment
   actions" action that runs the existing recut path per episode
   (markers re-resolved against the current maps). Episode detail's
   existing recut also re-resolves.
8. Community patterns: shared patterns gain optional `category`
   (default `sponsor` on import/absent). Sync format stays backward
   compatible both directions.

## API and UI

- Markers in episode payloads gain `category` and `actionApplied`.
- Feed settings API: `segmentCategoryActions` (partial map) and
  `detectShowSegments` (bool) on the existing per-feed settings
  endpoints; global `segmentCategoryActions` via the settings
  registry/PUT phases.
- UI:
  - Global Defaults: compact 7-row matrix (category label + three-way
    segmented control remove/beep/keep), collapsed under a disclosure
    like the update panel's "Update settings".
  - Feed settings: same matrix as an override (tri-state: inherit
    marker until touched) + the "Detect intro, outro, and housekeeping
    segments" toggle + the re-render action button.
  - Episode detail: marker chips show the category (e.g. "Cross-promo")
    and a muted "kept" badge for keep-action markers.
- openapi.yaml: schema updates for all of the above.

## Non-goals

- No per-listener actions (MinusPod serves one rendered cut per feed).
- No auto mass-recut on settings change.
- No new detection stages; intro/outro/recap ride the existing LLM
  windows.
- No category editing UI for individual markers (approve/reject flow
  unchanged).
- No SponsorBlock database integration.

## Testing

- Category provenance: LLM-emitted enum honored; unknown/missing ->
  sponsor; non-LLM stages -> sponsor.
- Resolution: per-feed overrides beat global; unset category ->
  remove.
- Cut partition: remove cuts, beep beeps, keep leaves audio and stamps
  was_cut=0/action keep (duration assertions on a synthetic file).
- Pass-2 exclusion: verification finding overlapping a kept span is
  dropped; non-overlapping findings still act.
- Validator/reviewer bypass for keep-resolved markers; hold rules
  unchanged for remove/beep.
- Learning guards: no corrections/FP texts from kept markers; pattern
  learning still records category.
- Notification counts exclude kept markers from ads_not_cut.
- Migration: additive columns, existing rows read as sponsor/inherit.
- Frontend: matrix PUT payloads, tri-state inherit behavior, chips,
  re-render action call.
- Recut: action-map change + re-render produces the new cut from the
  retained original (existing recut tests extended).

## Also in this release: OCI version label (#576)

Both images inherit `org.opencontainers.image.version="26.04"` from
the ubuntu:26.04 base. Fix in both Dockerfiles (CPU mirror checklist
applies): final stage gains

    ARG MINUSPOD_VERSION=dev
    LABEL org.opencontainers.image.version="${MINUSPOD_VERSION}"

- GPU local build: the build command passes
  `--build-arg MINUSPOD_VERSION=<version>` (the user-level
  /build-and-push flow is updated to include it; a build without the
  arg labels the image `dev`, never the misleading OS version).
- CPU workflow (`cpu-image.yml`): pass the same build-arg from the
  workflow's version input on both arch legs.
- Verify step in the release flow: `docker inspect` the built image
  and assert the label equals version.py before push.
- Test: none automatable in pytest; verification lives in the build
  gates above.

## Version and process

2.78.0, one PR, one release. Docs: how-it-works (new "Segment categories"
section), configuration.md, glossary.md, podcasting-2.0.md untouched,
README bullet, CHANGELOG.

Known risks called out during brainstorming:
- Beep on intro/outro sounds odd (theme music beeped); allowed for
  consistency, expected real-world choices are keep/remove.
- Opt-in segment detection adds LLM output volume per window on opted
  feeds and its boundaries (music without speech) are harder for a
  transcript-based detector; documentation must set expectations that
  intro/outro boundaries are approximate on music-heavy segments.
