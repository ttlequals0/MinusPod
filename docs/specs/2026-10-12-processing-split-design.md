# Design: split src/main_app/processing.py by pipeline stage

Date: 2026-10-12
Status: implemented in 2.100.11. Behavior-neutral refactor.

## Facts (measured on main before the split)

| Metric | Value |
|---|---|
| Lines | 7,370 |
| Top-level defs | 154 (152 functions, 2 classes) |
| `process_episode` | 913 lines, 57 local callees |
| `_run_verification_pass` | 400 lines, 18 callees |
| `_recut_episode` | 205 lines, 19 callees |
| Test files importing `main_app.processing` | 81 |
| External importers | `main_app/__init__.py`, `main_app/background.py`, `api/episodes.py`, `api/feeds.py`, `api/providers.py`, `main_app/routes.py` |

## Module map

Functions moved by contiguous line range into stage modules under `src/main_app/pipeline/`.
The modules form a dependency DAG: each imports only modules earlier in this order.
`processing.py` may import any of them.

| Order | Module | Contents |
|---|---|---|
| 1 | `common` | Status publishing, stage timing, settings reads, transient-error classification, `_copy_retained_original_to_temp`, `_refresh_rss_for_slug` |
| 2 | `transcribe` | Download and transcription, including tail and hole re-transcription |
| 3 | `signals` | Audio analysis, cue matching, cross-fetch differential, transcript differential |
| 4 | `detect` | Pass 1 detection, boundary refinement, validation, confidence gating |
| 5 | `review` | Ad reviewer runs for pass 1 and pass 2, hold release |
| 6 | `cuts` | Cut carving, hold carving, terminal snaps, final cut state |
| 7 | `keep_rules` | Keep overrides, keep partitioning, late keep safety net, learning from kept or cut ads |
| 8 | `verify` | Pass 2 verification over the processed audio, cross-pass cut planning |
| 9 | `publish` | Chapter remapping and publishing, asset generation |
| 10 | `finalize` | Episode state persistence, run log, history rows, completion events |
| 11 | `admission` | Route snapshots and provider admission gates |
| 12 | `failure` | Processing failure handling, `_requeue_episode_after_hold` |
| 13 | `recut` | Recut and pass-through modes from the retained original |

`processing.py` keeps `process_episode`, `start_background_processing`,
`_process_episode_background`, `_cleanup_cancelled_processing_output` and the real
`from main_app import (db, ...)` singleton binding.

Four helpers moved to a module other than the one their line range implies:
`_setting_float`, `_copy_retained_original_to_temp` and `_refresh_rss_for_slug` to
`common` (several stages use them), and `_requeue_episode_after_hold` to `failure`
(its only caller, and `recut` imports `failure`).

## The patch-target problem and the decision

`patch('main_app.processing.status_service')` worked when `_publish_status` looked
`status_service` up in the `main_app.processing` globals. Once the function lives in
another module, patching the old name has no effect on it and the test either runs real
code or passes vacuously. Two mechanisms keep the 81 test modules meaningful:

1. Cross-module calls use `module.function(...)`. A call written `recut._recut_episode(...)`
   reads the attribute at call time, so one patch target (`main_app.pipeline.recut`)
   works from every caller. A plain `from recut import _recut_episode` would bind the
   function at import time and make patches on the defining module invisible to callers.
2. App singletons (`db`, `storage`, `transcriber`, `ad_detector`, `audio_processor`,
   `audio_analyzer`, `sponsor_service`, `status_service`, `pattern_service`,
   `shutdown_event`) are read through lazy proxies in `main_app.pipeline.shared`. Each
   proxy resolves `main_app.processing.<name>` on every access, so a test that rebinds
   `processing.db` reaches every stage, as it did when the stages were one module.
   The proxies forward attribute reads, writes and truthiness; stage code calls methods
   on them and never tests identity or type.

Nothing is re-exported from `main_app.processing`. A stale
`patch('main_app.processing.fetch_and_diff')` raises `AttributeError` at test time
instead of silently doing nothing. A script rewrote the test references to the module
that now owns each name. Names used by several stages (for example `clear_fallback`)
are patched in each stage that calls them. `tests/unit/test_processing_references.py`
fails on any `processing.X` reference whose `X` is not an attribute of the module.

## Risks

- Import cycles at boot: pinned by the stage order and
  `tests/unit/test_pipeline_import_order.py`.
- A local variable that shares a name with a stage module (for example `admission`,
  `cuts`) hides the module inside that function. The two such locals in stage code were
  renamed; tests keep a `_stage` alias import where a local still shares the name.
- `_embed_locks` module state moved with `_episode_embed_lock` to `publish`; nothing
  else touches it.
- Diff size: about 6,000 moved lines. `git diff --color-moved=dimmed-zebra` dims moved
  blocks so only real edits (imports, module-attribute call sites) stand out.

## Verification

`pytest tests/` green, `ruff` clean, `tests/unit/test_container_import_paths.py` green,
and the app boots with `PYTHONPATH=src python -c "import main_app"`.
