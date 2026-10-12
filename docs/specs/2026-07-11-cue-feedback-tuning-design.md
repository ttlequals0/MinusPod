# Cue feedback tuning: candidate dismissals and verdict-fed threshold suggestion

Date: 2026-07-11
Status: implemented in 2.44.0
Target version: 2.44.0

## Problem

The cue suggestion machinery has no memory of user feedback:

- The candidate scan (`_run_cue_candidate_scan`) re-derives candidates from
  fingerprint recurrence on every scan. Junk candidates (music beds, stingers,
  repeated phrases that are not ad cues) reappear on every scan of every
  episode. Noisy feeds flood the candidate list. The only suppression today is
  spans already covered by a created template (`_templated_cue_spans`).
- The threshold suggestion (`suggest_cue_threshold`) is unsupervised: it sweeps
  templates at a low floor and hunts for a bimodal gap in raw scores. When
  noise and signal clusters overlap it gives up ("no clear separation").
  Meanwhile every user verdict on a cue detection is a labeled
  (template_id, match_score) pair sitting unused in `cue_detections`.

## Decisions (from brainstorming)

| Decision | Choice |
|----------|--------|
| Main pain | Junk candidates flood the list and return every scan |
| Dismissal scope | Feed-wide by sound (audio fingerprint match), not per-episode span |
| Dismissed candidate UI | Collapsed "Dismissed" group with undo; never silently hidden |
| Scope | Candidate dismissal and verdict-fed threshold suggestion, one feature |
| Threshold behavior | Suggestion only; user applies manually (matches existing UX) |
| Approach | A: dismissal table + scan-time fingerprint matching |

## 1. Data model

New table `cue_candidate_dismissals`:

```sql
CREATE TABLE IF NOT EXISTS cue_candidate_dismissals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    podcast_id INTEGER NOT NULL,
    source_episode_id TEXT NOT NULL,
    start_s REAL NOT NULL,
    end_s REAL NOT NULL,
    label TEXT,
    fingerprint TEXT NOT NULL,
    created_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    FOREIGN KEY (podcast_id) REFERENCES podcasts(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_cue_dismissals_podcast
    ON cue_candidate_dismissals(podcast_id);
```

- `fingerprint` stores the chromaprint of the dismissed span, extracted at
  dismiss time from the episode's retained original audio. Same encoding the
  `ad_patterns` fingerprint path uses, so decode helpers are shared.
- Candidates in the cached scan JSON (`cue_candidate_scans`) gain two optional
  fields: `dismissed: true` and `dismissalId: <int>`. Absent means active.
- `CUE_CANDIDATE_SCHEMA_VERSION` bumps by 1 so caches produced before this
  feature read as stale and rescan once (same retroactive pattern as the
  speech filter, issue #350 4A). No data migration needed.

## 2. Dismiss / undo flow

New endpoints in `src/api/cue_templates.py` (same blueprint as the other
cue-candidate routes):

- `POST /feeds/<slug>/episodes/<episode_id>/cue-candidates/dismiss`
  Body: `{ "start_s": float, "end_s": float, "label": str|null }`.
  Steps: validate span; require retained original audio (409 with a clear
  message if gone, fingerprinting needs the file); extract the span
  fingerprint via `AudioFingerprinter`; insert the dismissal row; stamp the
  matching candidate in this episode's cached scan result
  (`dismissed: true, dismissalId: id`), the UI sends the candidate's exact
  start/end as returned by the scan, so the match is exact equality on the
  round-tripped JSON floats; return the dismissal.
- `DELETE /cue-candidate-dismissals/<id>`
  Removes the row and un-stamps any candidate carrying that `dismissalId` in
  the source episode's cached scan. Other episodes' caches self-correct on
  their next rescan.
- `GET /feeds/<slug>/cue-candidate-dismissals`
  Lists the feed's dismissals (id, label, source episode, span, created_at).
  Used by the UI to render undo targets that are not in the current scan.

Cache-consistency trade-off (accepted): a dismissal stamps the episode it was
made from immediately; other episodes' cached scan results still show the
sound until their next rescan. All future scans suppress it feed-wide.

## 3. Scan integration

In `_run_cue_candidate_scan`, after `merge_cue_candidates`:

1. Load the feed's dismissals; decode each stored fingerprint. A corrupt or
   undecodable fingerprint is skipped with a warning log; it never fails the
   scan.
2. For each dismissal, enumerate its occurrences in the already-computed
   `target_fp` using the existing `_enumerate_window_occurrences` machinery
   (the same primitive `discover_recurring_spots` builds on).
3. Any candidate whose span overlaps an occurrence is marked
   `dismissed: true, dismissalId: id`. Candidates are kept in the result, not
   dropped, the UI's collapsed group renders from the same cached scan.
4. Match similarity: new config constant `AUDIO_CUE_DISMISSAL_SIMILARITY`,
   default equal to `AUDIO_CUE_RECURRENCE_SIMILARITY`.

## 4. Verdict-fed threshold suggestion

`suggest_cue_threshold(occurrence_scores, effect_floor=..., labeled_scores=None)`
in `src/audio_analysis/cue_threshold_suggest.py` stays a pure function.

- `labeled_scores`: list of `(score: float, verdict: 'confirmed'|'rejected')`.
- Supervised rule: with at least `CUE_SUGGEST_MIN_LABELED` (default 3) labels
  total and both classes present, if `max(rejected) < min(confirmed)`, propose
  the midpoint of the labeled gap (clamped to the effect floor), confidence
  `high`, reason citing counts (e.g. "5 rejected matches score at or below
  0.71, 4 confirmed at or above 0.84").
- One-sided labels: rejected-only labels raise the proposed floor to
  `max(rejected) + margin` if the unsupervised suggestion sits below it;
  confirmed-only labels cap the suggestion at `min(confirmed) - margin`.
  `margin` is the existing `AUDIO_CUE_SUGGEST_MARGIN` constant.
- Overlap (`max(rejected) >= min(confirmed)`): fall back to the existing
  unsupervised gap logic and set `labeledOverlap: true` in the result (the
  hint layer, section 5, turns this into a re-capture suggestion).
- `_run_cue_threshold_scan` pulls `(template_id, match_score, verdict)` rows
  for the feed from `cue_detections` (verdict != 'pending', match_score not
  null) and passes scores in. The stored scan result gains
  `labeledCounts: {confirmed: n, rejected: n}`.
- Suggestion-only: the apply step is unchanged; nothing auto-applies.

## 5. Per-template hints (pure DB, no audio)

New pure module `src/audio_analysis/cue_verdict_hints.py` (no Flask/DB
imports, mirroring `cue_threshold_suggest.py`); the DB layer feeds it rows
and merges its output into the health payload:

- Input: a template's rejected/confirmed match scores plus the feed's current
  resolved threshold.
- Output: `raise_threshold` when rejections cluster just above the threshold
  (all rejected scores within `CUE_HINT_NEAR_BAND` (default 0.10) above it and
  below every confirmed score); `recapture` when rejections spread across the
  score range or overlap confirmed scores; `None` below
  `CUE_HINT_MIN_REJECTIONS` (default 3) rejections.
- Surfaced through the existing per-feed cue health stats payload
  (`src/database/cue_detections.py` health function + its API route), as a
  `hint` field per template.

## 6. Frontend

- `CueCandidatesSection.tsx`: per-candidate Dismiss button; dismissed
  candidates render in a collapsed "Dismissed" group (CollapsibleSection,
  default closed) with an Undo button per entry. Feed-level dismissals not
  present in the current scan are listed in the same group from the GET
  endpoint so undo is always reachable.
- Cue health panel (feed settings): per-template hint chip ("Raise threshold"
  / "Re-capture cue") with a tooltip explaining the evidence; threshold scan
  result surfaces the verdict-informed reason string and labeled counts.
- API client additions in `frontend/src/api/` following the existing
  cueDetections/cueTemplates module pattern; types in `api/types.ts`.

## 7. Error handling

- Dismiss with original audio missing: 409, message says the original file is
  required to fingerprint the sound.
- fpcalc unavailable: dismiss endpoint returns 503-style error (same
  dependency story as the candidate scan itself).
- Corrupt stored dismissal fingerprint: skipped at scan time with a warning;
  scan completes.
- Sparse verdicts: below minimum counts the threshold suggestion ignores
  labels and hints stay `None`; no behavior change for feeds with no review
  activity.

## 8. Testing

- Unit (pure, no IO): labeled threshold suggestion, clean separation,
  overlap, rejected-only, confirmed-only, below-minimum, margin clamping to
  effect floor; hint classifier, near-band clustering, spread, overlap,
  minimum-count gate; dismissal occurrence-overlap matching.
- API: dismiss happy path (row inserted, cache stamped), missing original
  audio (409), undo (row gone, cache un-stamped), list endpoint, scan worker
  marks matching candidates across episodes (fixture fingerprints).
- Frontend: CueCandidatesSection dismiss/undo rendering and collapsed group;
  `npx tsc --noEmit`; full vitest suite.
- Python: `PYTHONPATH=src .venv/bin/python -m pytest tests/ -v`.

## Out of scope

- Auto-applying thresholds (explicitly rejected in brainstorming).
- Downranking near-miss candidates below the dismissal match cutoff
  ("feed-wide plus downrank-similar" option was not chosen).
- Migrating candidate scan caches to first-class candidate rows (approach C).
- Any change to episode processing or cutting: dismissals and verdicts remain
  advisory to suggestion surfaces only.
- The Rejected Detections play button (separate change, 2.43.1, already on
  branch `feature/rejected-detections-play-button`).

## Notes for the implementation plan

- Reuse `_enumerate_window_occurrences` and the fingerprint decode helpers;
  do not add a second matching implementation.
- The verdict pull must exclude `pending` verdicts and null match scores.
- `CUE_CANDIDATE_SCHEMA_VERSION` bump forces a one-time rescan per cached
  episode; mention in CHANGELOG.
- CHANGELOG entry under 2.44.0; bump `version.py` and `openapi.yaml`
  (openapi also needs the three new routes documented).
