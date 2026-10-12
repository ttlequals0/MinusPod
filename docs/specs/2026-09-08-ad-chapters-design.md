# Ad chapters: design

Segments left in the audio can be published as their own Podcasting 2.0 chapters so a chapter-aware player can skip a break. Adapted from the `ad-chapters` branch of mendelsimon/MinusPod (commits 9ecc9161..d6d1abf6); credit mendelsimon as git co-author on the porting commit. Their `mark` segment action is not ported; per-category eligibility lives in the chapter settings instead.

## Settings

All in `SETTINGS_REGISTRY`, no env vars, all `in_ad_reset=True`, surfaced by `GET /settings`, saved through the ad-detection PUT's phase helpers, documented in `openapi.yaml`, and rendered under Settings > Podcasting 2.0 > Transcripts & Chapters.

| Key | Payload key | Type | Default |
|---|---|---|---|
| `ad_chapters_enabled` | `adChaptersEnabled` | bool | false |
| `ad_chapter_categories` | `adChapterCategories` | JSON map category -> bool | sponsor and cross_promo true, all others false |
| `ad_chapters_include_held` | `adChaptersIncludeHeld` | bool | false |
| `ad_chapter_title_format` | `adChapterTitleFormat` | string | `[mp:{category}]` |
| `ad_chapter_held_title_format` | `adChapterHeldTitleFormat` | string | `[mp:{category}?]` |
| `ad_chapter_resume_title` | `adChapterResumeTitle` | string | `Show` |
| `ad_chapter_min_confidence` | `adChapterMinConfidence` | float 0..1 | 0.9 |

Per-feed overrides, tri-state (inherit / on / off) like `chapters_mode`: `ad_chapters_enabled_override` and `ad_chapter_categories_override` as new nullable columns on `podcasts`, added with `_add_column_if_missing`, editable through the feed PATCH and shown in the feed settings panel next to the existing chapters controls.

Title format validation at save time: the string must `.format(category=...)` without raising; `{category}` may be absent. Reject with 400 on failure. At run time a failure still falls back to the default, never fails the run.

Defaults change nothing on upgrade: feature off, held off.

## Eligibility

A marker gets an ad chapter when all of:
- ad chapters are on for the feed (override, else global),
- the feed's chapters mode is not `off` (off means no chapter JSON at all),
- the marker's audio is still in the served file: `action_applied == 'keep'`, or `is_pending_review(marker)` when include-held is on. Never `remove`, never `beep` (beep cuts the audio and inserts a tone),
- the marker's category is checked in the resolved category map,
- for a kept marker, `adjusted_confidence` (else `confidence`, else 1.0) is at or above `ad_chapter_min_confidence`. Held markers skip the floor; their title already says unconfirmed.

Each eligible marker yields an ad chapter at its start and a resume chapter at its end, unless a generated chapter already starts within 2.0 s of the end, in which case that chapter is the terminator. Generated chapters strictly inside the span are dropped. Timestamps use the same integer-second minimum-1 convention as generated chapters, mapped through the applied cuts.

Ad chapters carry `"kind": "ad"` and `"category": "<category>"` and, when held, `"held": true` in the chapter JSON. Players ignore unknown keys.

Held marker title uses `ad_chapter_held_title_format`; kept uses `ad_chapter_title_format`.

## Filters that must not eat ad chapters

- `chapter_min_duration_seconds` (generation minimum) does not apply to `kind: ad` entries.
- `_remap_chapters_for_recut`'s sliver filter (`MIN_CHAPTER_SECONDS`) does not apply to `kind: ad` entries.

## Rebuild, not remap

Ad chapters are derived from markers plus cuts, no LLM. On every path that produces a chapter list they are rebuilt from the current markers:
- pass 1 generation: `_generate_assets` merges them after the topic chapters are produced (any mode: generated, upstream, or an empty list when the feed has no topic chapters but ad chapters are on).
- recut (`_recut_episode` -> `_generate_assets(regenerate_chapters=False)` -> `_remap_stored_chapters`): strip `kind: ad` entries, remap the topic chapters as today, rebuild ad chapters from the current markers, then embed and save together as today.
- manual `POST .../regenerate-chapters`: after the LLM pass, merge ad chapters from the current markers.
- correction that changes chapters but not audio (reject on a held marker; recategorize between kept categories): `submit_correction` runs a chapter-only rebuild immediately, in a new `_correction_changes_chapters(...)` sibling of `_correction_changes_audio`: strip ad entries, rebuild from markers, re-embed ID3 into the existing processed MP3, save the JSON, invalidate the served feed. No ffmpeg cut, no LLM.

Confirm on a held marker already stamps `pending_recut`; the recut rebuild covers it.

## Rendering

- Episode descriptions (`chapters_in_notes`): ad chapter lines get a readable suffix from the category label: ` (Sponsor)` for kept, ` (Cross-promo, awaiting review)` for held. Suffix is added by the notes renderer; the JSON title is untouched.
- Episode page chapter list: `kind: ad` entries show the existing `SegmentCategoryBadge` for kept and a warning-tint `Review` pill for held, same `px-1.5 py-0.5 text-xs rounded` shape.
- Stats: a kept-with-chapter segment saves no time; nothing changes in the stats fixes from 2.96.3.

## Not in scope

No bulk backfill; Regenerate Chapters per episode is the path. Beep segments never chapter. Feed-level chapters mode off suppresses ad chapters too.

## Release

On PR #722 as 2.96.8. Changelog under a new section. Docs: `docs/configuration.md` gets an "Ad chapters" subsection under Transcripts & Chapters, and `docs/glossary.md` a one-line entry.
