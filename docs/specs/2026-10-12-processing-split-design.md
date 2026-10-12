# Design: split src/main_app/processing.py by pipeline stage

Date: 2026-10-12
Status: draft for the PR after 2.100.11. Behavior-neutral refactor, own PR and version.

## Facts (measured on main at 539923fe)

| Metric | Value |
|---|---|
| Lines | 7,370 |
| Top-level defs | 154 (152 functions, 2 classes) |
| `process_episode` | 913 lines, 57 local callees |
| `_run_verification_pass` | 400 lines, 18 callees |
| `_recut_episode` | 205 lines, 19 callees |
| Test files importing `main_app.processing` | 81 |
| Distinct `patch('main_app.processing.X')` targets | 37 (most-hit: `start_background_processing` 45, `resolve_differential_fetch_mode` 14, `status_service` 13, `fetch_and_diff` 13) |
| External importers | `main_app/__init__.py`, `main_app/background.py`, `api/episodes.py`, `api/feeds.py`, `api/providers.py`, `main_app/routes.py` |

## Module map (contiguous line ranges, so each move is one cut-and-paste)

| New module `src/main_app/pipeline/` | Source lines | Approx lines | Contents |
|---|---|---|---|
| `common.py` | 230-405 | 175 | `_require_publication_owner`, `_publish_status`, `_measure_run_stage`, `get_min_cut_confidence`, `resolve_audio_encode_settings`, `is_transient_error` |
| `transcribe.py` | 653-935 | 285 | `_retranscribe_*`, `_repair_transcript`, `_apply_transcript_corrections`, `_download_episode_audio`, `_download_and_transcribe`, `CDN_BLOCKED_MESSAGE` |
| `signals.py` | 938-1246 | 310 | audio analysis, feed cue matcher, primary cue publish, `_run_differential_fetch`, `_run_transcript_diff` |
| `detect.py` | 1249-1764, 2213-2294 | 600 | `_detect_ads_first_pass`, cue threshold suggestions, `_refine_boundaries`, `_apply_heuristic_rolls`, corrections load, confidence gate, `_build_validator`, `_refine_and_validate` |
| `keep_rules.py` | 1767-2029 | 265 | `KeepDifferentialOverride`, `_keep_overridden*`, `_partition_keep_ads`, `_apply_late_keep_safety_net`, `_restore_confirmed_spans`, `_partition_cut_actions`, `_stamp_and_carve_cuts`, `_learn_from_*` |
| `review.py` | 2297-2944 | 650 | `_build_reviewer`, `_apply_pass2_reviewer`, `_review_hold_release_candidates`, `_merge_reviewer_result`, `_run_ad_reviewer` |
| `cuts.py` | 2947-3419 | 475 | cut groups and carving, `_finalize_cut_state`, `_snap_terminal_starts`, `_complete_cut_tails`, `_finalize_user_confirmed_bounds` |
| `verify.py` | 2032-2210, 3422-4346 | 1,100 | pass2 marker merge, `_validate_verification_ads`, `_file_corroborated_hold_approvals`, crosspass plan, `Protection`, `_run_verification_pass` |
| `publish.py` | 4349-4905 | 560 | chapter remap and publish, embed lock, `rebuild_ad_chapters`, `_generate_assets` |
| `finalize.py` | 4908-5332 | 425 | `_persist_episode_state`, degraded redetect, low ad yield, run log, history row and event, `_finalize_episode` |
| `recut.py` | 5335-5924 | 590 | `_apply_boundary_adjustments`, `_build_recut_ad_list`, `_passthrough_episode`, `_recut_episode`, `_requeue_episode_after_hold` |
| `failure.py` | 5927-6091 | 165 | `_handle_processing_failure` |
| `admission.py` | 6094-6455 | 360 | route snapshot load/persist/resolve, admission gates and phases, `admission_explanation`, `runs_for_account`, `clear_route_snapshot` |
| `processing.py` (stays) | 1-229, 408-650, 6458-7370 | 1,400 | imports, `start_background_processing`, `_process_episode_background`, `_cleanup_cancelled_processing_output`, `process_episode`, public re-exports |

Singletons: each new module does `from main_app import db, storage, ...` exactly as
processing.py does today. The import is safe for the same reason: `main_app/__init__.py`
imports `main_app.processing` after the singletons exist, and processing.py imports the
pipeline modules at that moment.

## Public surface kept on `main_app.processing`

Re-exported unchanged, since other packages import them: `start_background_processing`,
`process_episode`, `rebuild_ad_chapters`, `admission_explanation`, `runs_for_account`,
`clear_route_snapshot`, `is_transient_error`, `build_podcast_context`,
`get_min_cut_confidence`, `resolve_audio_encode_settings`. Private helpers that
`api/episodes.py` and `main_app/background.py` import (`_keep_overridden`,
`_load_stored_dai_differential`, `_make_keep_differential_override`, `_admission_gates`,
`_resolve_route_snapshot`, `_active_phases_for_admission`,
`_required_providers_for_admission`, `_start_run_log`, `_end_run_log`,
`_record_history_row`, `_resolve_or_load_route_snapshot`, `_refresh_rss_for_slug`) get
their import lines repointed at the new module in the same PR.

## The patch-target problem and the decision

`patch('main_app.processing.status_service')` works today because `_publish_status`
looks `status_service` up in the `main_app.processing` globals. Once `_publish_status`
lives in `pipeline/common.py`, patching the old name has no effect on it and the test
either runs real code or passes vacuously. A re-export cannot fix this; only the module
that owns the function can be patched.

Decision: repoint every patch target in the same PR, and do NOT keep re-exports of
third-party names (`fetch_and_diff`, `snap_ad_boundaries_to_cues`, `get_pool`,
`transcriber`, `status_service`, `threading`, ...) on `main_app.processing`. A stale
`patch('main_app.processing.fetch_and_diff')` then raises `AttributeError` at test time
instead of silently doing nothing. Re-exports of the pipeline's own public functions
(table above) stay because they are real API, not patch conveniences.

Mechanics: a script maps each patched name to the module that now defines or imports it
and rewrites the string literals in tests/. Names imported by more than one new module
(for example `status_service` if both `common.py` and `finalize.py` need it) are
resolved per test file by which function the test exercises; the script prints those
for a human to settle. Expected: about 200 patch sites across 81 files.

## Risks

- Import cycles between pipeline modules: `verify.py` calls into `cuts.py`, `review.py`
  and `keep_rules.py`; `recut.py` calls `cuts.py`, `keep_rules.py`, `publish.py`,
  `finalize.py`, `failure.py`. The dependency direction is one-way in the table order
  (later rows import earlier rows), except `failure.py` -> `admission.py`
  (`clear_route_snapshot`, `_stale_snapshot_phases`); place `admission.py` before
  `failure.py` in import order or move those two functions' use behind a lazy import.
- `_embed_locks` module state moves with `_episode_embed_lock` to `publish.py`; nothing
  else touches it.
- Diff size: about 6,000 moved lines. Review by module: `git diff --color-moved=dimmed-zebra`
  shows moved blocks dimmed so only real edits (imports, re-exports) stand out.

## Verification

`pytest tests/` green, `ruff` clean, `tests/unit/test_container_import_paths.py` green,
app boots (`PYTHONPATH=src python -c "import main_app"`), and a grep proving no test still
patches a name that `main_app.processing` no longer defines:
`grep -rhoE "main_app\.processing\.[A-Za-z_]+" tests/ | sort -u` must be a subset of the
public surface list above plus `process_episode`.
