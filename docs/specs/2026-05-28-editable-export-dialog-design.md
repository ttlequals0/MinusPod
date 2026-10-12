# Editable pattern export dialog (per-export sponsor, aliases and tags override)

## Context

PR #292 surfaced 7 community pattern filename mismatches because contributors
legitimately refine the `sponsor` field after exporting from the in-app dialog:

- The in-app classifier writes the broadest brand the seed knows (`Cascade`,
  `Hims`, `Nordstrom`, `TD Bank`, `Merck`).
- The contributor refines to the product or campaign that the ad actually
  promotes (`Cascade Platinum`, `Hims.com`, `Nordstrom Rack`, `TD card`,
  `badcholesterol.com`).
- The exporter wrote the filename from the original sponsor at download time;
  hand edits to the JSON drifted the file name out of sync.

#294 introduced a validator check that hard-rejects these mismatches with an
exact rename suggestion. That helps after the fact. The editable Export dialog
closes the loop earlier: let the contributor refine `sponsor`,
`sponsor_aliases`, and `sponsor_tags` inside the dialog before the bundle
serializes, so the downloaded file and its JSON agree by construction.

## Goal

A contributor opening **Submit to community** can edit, per pattern, the three
identity fields the validator checks against and see the filename the validator
will expect, before any bytes leave the browser.

## Non-goals

- Editing `text_template` or intro/outro variants in the dialog. The actual ad
  text is already correct from the source episode.
- Persisting overrides to the local DB or to `known_sponsors`. v1 is per-export
  override only. The next bundle a contributor builds starts from the
  classifier's view again.
- A merge UI for community + local pattern overlap (separate concern).
- Sponsor autocomplete or fuzzy "did you mean ...?" suggestions. Free text in
  v1.

## User flow

1. Contributor selects patterns on the Patterns page and opens **Export**.
2. The dialog shows the existing destination radio (`download` / `community`),
   pattern list, and toggles.
3. Each row in the pattern list gains a small `Edit` affordance. Clicking it
   expands an inline panel below the row containing:
   - `Sponsor` text field (required, mirrors the validator's word-boundary
     check against `text_template`)
   - `Aliases` chip input (comma-separated; reuses existing chip pattern from
     `SponsorAliasesEditor` if available)
   - `Tags` chip multi-select backed by `/tags/vocabulary` (only canonical tags
     selectable)
   - A read-only **Filename preview** line: `slugify(sponsor)-<short_uuid>.json`
     and updates as the contributor types
4. Edits are local React state, keyed by `pattern_id`. The contributor can edit
   any number of patterns before downloading; closing and reopening the dialog
   discards edits (matches current dialog behavior on `key=` remount).
5. On **Preview** or **Download**, the dialog sends the override map alongside
   the existing `ids` payload. The backend applies the overrides while building
   the bundle and runs the same quality gates against the overridden fields.
6. The download button reflects current state: if any pattern's overrides
   would cause a gate failure (e.g. sponsor name no longer appears in
   `text_template`), the **Preview** badge counts climb just as they do today.

## Architecture

### Frontend

`frontend/src/components/PatternExportDialog.tsx`:

- Add a `patternOverrides: Record<number, PatternOverride>` state slot
  alongside the existing `selected` set.
- Each `PatternOverride` is
  ```
  { sponsor?: string; sponsor_aliases?: string[]; sponsor_tags?: string[] }
  ```
  Partial overrides only; absent fields keep the server-side default.
- Add a row-level `expandedRowId: number | null` state. Only one row is
  expanded at a time to keep the dialog height bounded on mobile.
- Reuse the destination radio, selected-set, and toggles; the existing flow is
  the no-edits default. A contributor who never expands a row gets today's
  behavior exactly.
- New component `PatternExportEditRow` rendered conditionally under the
  parent row when `expandedRowId === pattern.id`. Three fields + filename
  preview + tag-validity hint.
- Filename preview reuses a small TypeScript port of the validator's
  `slugify` to match the backend byte-for-byte. Function lives in
  `frontend/src/api/communitySlug.ts` so the scaffold form (future) can reuse
  it.

### Frontend types

```ts
// frontend/src/api/patterns.ts (additions)
export interface PatternOverride {
  sponsor?: string;
  sponsor_aliases?: string[];
  sponsor_tags?: string[];
}

// previewExportBundle and downloadCommunityBundle gain an optional
// `overrides?: Record<number, PatternOverride>` argument that they pass
// through unchanged to the backend.
```

### Backend

`src/api/patterns.py`:

- `/patterns/preview-export` and `/patterns/submit-bundle` accept an optional
  `overrides` object: `{ "<pattern_id>": { sponsor, sponsor_aliases,
  sponsor_tags } }`. Validated as a dict of int->dict with string/list values;
  unknown keys ignored.
- The route hands the overrides through to `build_bundle` /
  `_quality_gates` / `_strip_metadata`.

`src/community_export.py`:

- `build_export_payload(pattern, sponsors, overrides=None)` accepts a
  per-pattern override dict. After building the payload from sponsor_row, the
  override fields replace the corresponding payload fields when present.
- `_quality_gates(pattern, sponsors, overrides=None)` re-runs the
  sponsor-in-text check against the overridden sponsor name (or any
  overridden alias) so an edit that strips the sponsor out of the text fails
  the gate the same way the original would. Tag-not-in-vocab also re-runs.
- `build_bundle(pattern_ids, db, overrides=None)` threads `overrides` down to
  each `build_export_payload` call.

### Override application order

For each pattern:

1. Start from `_strip_metadata(pattern, sponsor_row)`, the current payload shape.
2. If `overrides[pattern_id].sponsor` is present and non-empty, replace
   `payload['sponsor']`.
3. If `overrides[pattern_id].sponsor_aliases` is a list, replace
   `payload['sponsor_aliases']` (empty list is a legitimate "no aliases" value).
4. If `overrides[pattern_id].sponsor_tags` is a list, replace
   `payload['sponsor_tags']`.
5. Re-classify `sponsor_match` against the seed using the overridden sponsor.
6. Re-run quality gates against the overridden payload, not the original.

## Validation

- **Sponsor required**: empty sponsor override is rejected at the API layer
  (treated as "no override" rather than "blank"). Submit gate stops empty
  sponsor at the existing `_quality_gates` "sponsor missing" check.
- **Sponsor in text**: existing `_quality_gates` re-runs on the override
  values. If the contributor edits sponsor to something not in
  `text_template` and provides no alias that appears either, preview returns
  the existing rejection ("sponsor or any alias does not appear in
  text_template").
- **Tag vocabulary**: backend uses `valid_tags()` against the overridden tag
  list. Frontend shows live invalid-tag hints by filtering against the
  vocabulary endpoint (already fetched for the existing dialog).
- **Filename preview**: cosmetic on the frontend. The validator-side check
  ships in #294 and is the actual gate when the PR opens.

## Error handling

- API rejects override payloads that are not `dict[str, dict]` shape with a
  400 + body explaining the shape requirement.
- Override applied to a pattern not in `ids` is dropped with a single-line
  log warning, no error.
- Pre-existing rejection paths (text length, duration, FP > CC,
  multi-sponsor) are unchanged and run on the overridden payload.

## Testing

Backend (`tests/unit/test_community_export.py`):

- `build_export_payload` with no overrides produces the same payload as today
  (regression).
- Override sponsor replaces the field and re-runs sponsor-in-text against the
  new value. Pattern whose text contains "Shopify" but original sponsor was
  "Spotify" passes after override to "Shopify".
- Override aliases replace, including empty list as "clear all aliases".
- Override tags drop unknown tags via `_validate_tags`.
- Override on a pattern id not in the input list is ignored without error.
- Filename in the per-pattern submit endpoint reflects the override:
  `build_pr_url(payload_with_override)` returns
  `shopify-<short>.json`, not `spotify-<short>.json`.

Frontend (no jest infra in repo, covered by tsc + manual + Playwright if
already wired):

- TypeScript build clean (`cd frontend && npx tsc --noEmit`).
- Manual: edit a pattern's sponsor in the dialog, confirm filename preview
  updates and the bundle's filename matches on download. Edit tags to include
  an unknown tag, confirm Preview surfaces a rejection.
- Playwright smoke if the repo's existing harness covers PatternsPage.

Integration (`tests/integration/test_community_pattern_flow.py` if present):

- End-to-end: post override -> preview -> submit-bundle -> unzip bundle JSON ->
  assert each pattern's `sponsor` matches the override.

## What ships

- 1 component file added (`PatternExportEditRow.tsx`) + 1 helper module
  (`communitySlug.ts`).
- 1 component file modified (`PatternExportDialog.tsx`).
- 1 API type file modified (`patterns.ts`).
- `community_export.py` + 1-2 API route handlers modified.
- New unit tests in `test_community_export.py` (~6 cases).
- CHANGELOG entry under `[Unreleased] / Added`.
- No version bump (frontend + backend ships in the same image, and behavior is
  additive without breaking existing callers).

## Out of scope (deferred)

- Persisting overrides to local DB.
- Sponsor autocomplete.
- Merge UX for community vs local overlap.
- Editing `text_template` / intro / outro inside the dialog.
- Mobile layout tuning beyond "fields stack vertically below 640px".
