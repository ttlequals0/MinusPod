# Ad Review tab design

Date: 2026-07-12
Issue: #417 (part 2 - dedicated review area for unresolved rejected detections)
Target version: 2.48.0

## Problem

Rejected and held-for-review ad detections can only be found by opening each
episode page one at a time. Issue #417 asks for a single place to work through
them and mark each one "Ad" or "no ad". The broader goal is one view of every
detection across all podcasts.

## Solution overview

Add an "Ad Review" tab to the Patterns page. It lists every ad detection
across all podcasts in a paginated, sortable, filterable table, defaulting to
the detections that need a decision. Row actions reuse the existing
corrections API, so pattern learning behaves exactly as it does on the
episode page.

## UI

### Tabs on the Patterns page

- Tab bar with two tabs: "Patterns" and "Ad Review".
- Active tab persisted in the URL as `?tab=ad-review`; default is Patterns.
- The existing `?id=<pattern_id>` deep link keeps working on the Patterns tab.
- The current pattern list moves under the Patterns tab unchanged.

### Ad Review table

One row per detection. Columns:

| Column     | Content                                            |
|------------|----------------------------------------------------|
| Podcast    | Feed title                                         |
| Episode    | Episode title, links to the episode page           |
| Time       | start-end plus duration in seconds                 |
| Sponsor    | Sponsor name when known                            |
| Confidence | Detection confidence (0-1)                         |
| Stage      | detection_stage value                              |
| Status     | Accepted, Rejected, or Pending review badge        |
| Resolution | Unresolved, Confirmed, or Dismissed badge          |
| Actions    | Play, Approve, Dismiss, Edit                       |

- Pagination: server-side, 20 per page, existing `Pagination` component.
- Filters:
  - Status: Needs review (default), Pending review, Rejected, Accepted, All.
    "Needs review" means pending-review or rejected detections with no
    matching correction.
  - Podcast: dropdown of feeds.
  - Search: free text over sponsor and reason.
- Sorting: episode publish date (default, newest first), confidence, podcast.
  Click a column header to sort; click again to flip direction.
- The UI implementation follows the design guide.

### Row actions

Semantics mirror the episode page exactly:

- Approve ("Ad"): POST a `confirm` correction. When the episode still has
  original audio, trigger the same recut reprocess the episode page uses.
- Dismiss ("No ad"): POST a `reject` correction. The server already demotes a
  held marker to rejected in this path.
- Edit: open the existing `AdReviewModal` for boundary adjustment.
- Play: audition the detection audio, reusing the rejected-detection play
  pattern already on the episode page.
- Rows with a matching correction show the correction state (Confirmed or
  Dismissed) instead of Approve and Dismiss buttons.

## Backend

### New endpoint: GET /detections

Query parameters:

| Param  | Values                                                | Default      |
|--------|-------------------------------------------------------|--------------|
| page   | 1-indexed page number                                 | 1            |
| limit  | items per page, capped                                | 20           |
| status | needs_review, pending, rejected, accepted, all        | needs_review |
| feed   | feed slug                                             | none         |
| q      | free text matched against sponsor and reason          | none         |
| sort   | date, confidence, podcast                             | date         |
| order  | asc, desc                                             | desc         |

Response:

```json
{
  "detections": [
    {
      "feedSlug": "...",
      "feedTitle": "...",
      "episodeId": "...",
      "episodeTitle": "...",
      "publishDate": "...",
      "hasOriginalAudio": true,
      "start": 45.5,
      "end": 120.0,
      "confidence": 0.92,
      "sponsor": "...",
      "reason": "...",
      "patternId": 42,
      "detectionStage": "...",
      "status": "rejected",
      "resolution": "unresolved"
    }
  ],
  "total": 123,
  "page": 1,
  "totalPages": 7
}
```

### Aggregation strategy

Python-side aggregation, no schema change:

1. One query loads `episode_details` rows that have ad markers, joined with
   podcast and episode metadata.
2. One query loads all `confirm` and `false_positive` rows from
   `pattern_corrections`.
3. Markers are flattened in Python. Status uses the same three-bucket logic
   as the episode detail endpoint (pending review first, then rejected, then
   accepted). Resolution tolerance-matches marker bounds against correction
   bounds at 0.5 s, the same tolerance the reject path uses to clear held
   markers.
4. Filter, sort, and paginate the flattened list in Python.

At a typical scale (around 1,000 processed episodes, under 10,000
markers) this stays in the tens of milliseconds. If the library grows an
order of magnitude, move the aggregation into SQL or a denormalized table;
the endpoint contract does not change.

### Writes

No new write paths. Approve and Dismiss call the existing
`POST /episodes/<slug>/<episode_id>/corrections` endpoint, so
false-positive counts, pattern confirmation, and cross-episode suppression
all behave as they do today.

## Testing

- Backend unit tests: status bucketing, resolution matching (tolerance edges),
  filtering, sorting, pagination of the aggregation logic.
- Flask API tests: endpoint parameter validation, each filter, sort orders,
  pagination math, empty library.
- Frontend vitest: tab switching and URL persistence, table rendering,
  filter and sort interactions, Approve and Dismiss submitting the right
  correction payloads, resolved rows hiding action buttons.
- `cd frontend && npx tsc --noEmit` passes.

## Versioning and docs

- `version.py` and `openapi.yaml` bumped to 2.48.0; new endpoint documented
  in `openapi.yaml`.
- CHANGELOG.md entry under 2.48.0.
- `docs/web-interface.md` updated with the Ad Review tab.

## Out of scope

- Bulk multi-select actions (approve or dismiss many rows at once).
- Changing detection, validation, or recut behavior.
- Cue detection verdicts (separate telemetry system with its own review UI).
