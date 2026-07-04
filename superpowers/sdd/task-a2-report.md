# Task A2 Implementation Report

## Status: DONE

## Changes per file

### src/database/schema/tables.py
Added 7 nullable columns to podcasts CREATE TABLE DDL (after cue_template_score_override):
- cue_create_from_pairs_override INTEGER (tri-state NULL/0/1)
- cue_pair_min_break_override REAL
- cue_pair_max_break_override REAL
- cue_pair_max_break_fraction_override REAL
- cue_snap_confidence_override REAL
- cue_snap_lead_override REAL
- cue_snap_lag_override REAL

### src/database/schema/__init__.py
Added the same 7 columns to the podcasts_migrations list (after cue_template_score_override entry),
applying via the existing _add_column_if_missing loop. Idempotent on existing DBs.

### src/database/podcasts.py
- Added get_podcast_cue_settings_overrides(podcast_id) returning all 7 columns in one query.
- Added all 7 DB column names to the update_podcast whitelist.

### src/config.py
Added resolve_feed_cue_settings(db, podcast_id) -> dict immediately after resolve_cue_template_score.
- One DB call to get_podcast_cue_settings_overrides, then falls back to global get_setting_bool/
  get_setting_float, then to code defaults.
- Imports DEFAULT_SNAP_LEAD_SECONDS / DEFAULT_SNAP_LAG_SECONDS from cue_boundary_snap inside
  the function to avoid a circular import at module level.
- Exception in any DB call returns defaults dict (same as no-db behavior).

### src/main_app/processing.py
- Added resolve_feed_cue_settings to the config import block.
- Hoisted podcast_id = getattr(ctx, 'podcast_id', None) ABOVE the cue settings block.
- Replaced 3 direct db.get_setting_float calls (snap_confidence, snap_lead, snap_lag) and the
  db.get_setting_bool('audio_cue_create_from_pairs') gate with one resolve_feed_cue_settings call.
- pair_min_break, pair_max_break, pair_max_break_fraction now come from the resolver.
- orient_window_s and pair_confidence remain global-only (not in the 7 override knobs per brief).
- Behavior with all overrides NULL is byte-identical to the previous direct reads.

### src/api/feeds.py
- Added _normalize_cue_bool_override(value, field_name) for tri-state boolean.
- Added _normalize_cue_float_override(value, field_name, lo, hi) for nullable float with range check.
- Validation ranges mirror api/settings.py exactly:
  - cuePairMinBreak [1.0, 600.0], cuePairMaxBreak [1.0, 3600.0], cuePairMaxBreakFraction [0.0, 1.0]
  - cueSnapConfidence [0.0, 1.0], cueSnapLead [0.5, 30.0], cueSnapLag [0.5, 30.0]
- PATCH handler: 7 new if-blocks after cueTemplateScoreOverride.
- GET /feeds list payload: 7 new fields (cueCreateFromPairsOverride deserialized via _deserialize_nullable_bool).
- GET /feeds/<slug> payload: same 7 fields.
- PATCH response payload: same 7 fields.

### openapi.yaml
7 fields added in both:
- PATCH /feeds/{slug} request body schema (after cueTemplateScoreOverride, before maxEpisodes)
- Feed component schema (after cueTemplateScoreOverride, before maxEpisodes)

### frontend/src/api/types.ts
Added 7 optional nullable fields to Feed interface after cueTemplateScoreOverride.

### frontend/src/api/feeds.ts
Added 7 fields to UpdateFeedPayload interface after cueTemplateScoreOverride.

### frontend/src/pages/feeds/FeedSettingsPanel.tsx
- Added string-state vars for 6 number inputs (pairMin, pairMax, pairFrac, snapConf, snapLead, snapLag)
  with render-time reset pattern (same as cueScoreInput).
- Added commitFloat() helper to DRY the blur-commit pattern across the 6 number inputs.
- Added CollapsibleSection "Cue tuning overrides" (defaultOpen=false) containing all 7 controls:
  - cueCreateFromPairsOverride: TriStateSelect (null/false/true)
  - 6 number inputs with range constraints matching API validation
  - Each shows "Override: <value>" badge when set; placeholder text shows "global (unit)"
  - Collapsed by default so the panel does not balloon for the common case.

## Decision: FeedSettingsPanel grouping
Used a single CollapsibleSection "Cue tuning overrides" for all 7 knobs, collapsed by default.
The brief explicitly suggested this approach for advanced knobs. The cue threshold (cueTemplateScoreOverride)
stays outside the collapsible since it was already there and is the most commonly tuned knob.

## Test files and commands

### New files
- tests/unit/test_feed_cue_settings_resolver.py - 16 tests for resolve_feed_cue_settings
- tests/unit/test_feed_cue_settings_plumbing.py - 3 tests for processing.py plumbing
- tests/integration/test_feed_cue_settings_endpoint.py - 20 tests for API endpoints

### Modified existing test
- tests/integration/test_cue_processing_wiring.py: added get_podcast_cue_settings_overrides to
  _StubDB (no overrides, resolves to global). Required because resolver now calls this method on
  any db with a non-None podcast_id; the stub didn't implement it, causing the exception path to
  return defaults (create_from_pairs=False) and the existing cue-pair test to fail.

### Commands
- Unit: PYTHONPATH=src .venv/bin/python -m pytest tests/ -q
- TypeScript: cd frontend && npx tsc --noEmit

### Counts
- 2376 passed, 0 failed (full suite including pre-existing tests)
- TypeScript: clean (no output)
- New tests: 39 across 3 new files

## Concerns
None. The implementation is straightforward. One note: orient_window_s (audio_cue_pair_orient_window_seconds)
is NOT in the 7 overrides because the brief does not list it. It remains global-only, consistent
with the brief's explicit table of the 7 knobs.
