# Design specs

Design documents behind larger features. Each spec records the problem, the
decision, and the alternatives at the time it was written; the code and
CHANGELOG are authoritative for current behavior. Tests cite a spec by path
and heading anchor when a test exists to pin a decision made here.

| Spec | Topic | Shipped in |
|---|---|---|
| [2026-05-28 Editable export dialog](2026-05-28-editable-export-dialog-design.md) | Per-export sponsor, alias, and tag overrides in the community pattern export dialog | 2.4.4 |
| [2026-07-01 Cue play UX](2026-07-01-cue-play-ux-design.md) | Play-selection control, content-transition prompt fix, cue pairing parity, threshold suggestion | 2.31.0 |
| [2026-07-08 Ad splice detection](2026-07-08-ad-splice-detection.md) | End-of-episode ad fixes, audio splice-evidence signals, cross-fetch differential | 2.40.x |
| [2026-07-10 Cross-episode scan UI](2026-07-10-cross-episode-scan-ui-design.md) | Play buttons and per-episode match breakdown in the cue scan | 2.42.0 |
| [2026-07-11 Cue feedback tuning](2026-07-11-cue-feedback-tuning-design.md) | Candidate dismissals and verdict-fed threshold suggestion | 2.44.0 |
| [2026-07-12 Ad Review tab](2026-07-12-ad-review-tab-design.md) | Cross-podcast review queue for held and rejected detections | 2.48.0 |
| [2026-07-22 Release process](2026-07-22-release-process-design.md) | Stable channel, GitHub Releases, in-app update checks | 2.73.0 |
| [2026-07-23 Detection tuning](2026-07-23-detection-tuning-design.md) | Pass-2 misses, scoped rejections, sponsor label hygiene | 2.76.0 |
| [2026-07-23 Podping listener](2026-07-23-podping-listener-design.md) | Optional Hive podping subscription as a refresh accelerator | 2.77.0 |
| [2026-07-24 Segment categories](2026-07-24-segment-categories-design.md) | Typed segments with a per-category remove, beep, or keep action | 2.78.0 |
| [2026-07-29 Detected Ads and split](2026-07-29-detected-ads-split-chapters-design.md) | Detected Ads tab, category filters, marker split, chapter density, bug fixes | 2.82.0 |
| [2026-09-06 Parallel Whisper](2026-09-06-parallel-whisper-design.md) | Concurrent episodes and requests against a remote Whisper backend | 2.96.2 |
| [2026-09-06 Recents feed](2026-09-06-recents-feed-design.md) | Opt-in combined feed of every processed episode | 2.96.1 |
| [2026-09-08 Ad chapters](2026-09-08-ad-chapters-design.md) | Publishing kept segments as Podcasting 2.0 chapters | 2.96.8 |
| [2026-10-11 Parakeet backend](2026-10-11-parakeet-backend-design.md) | In-process Parakeet transcription backend | 2.100.0 |
