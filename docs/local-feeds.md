# Local Feeds

[< Docs index](README.md) | [Project README](../README.md)

---

A local feed is a podcast feed MinusPod builds and serves from your own audio files instead of an upstream RSS feed. Use it to turn an archive of MP3s into a real, subscribable Podcasting 2.0 feed: upload or import episodes, MinusPod runs them through the same ad-removal pipeline as any subscribed feed (if there's anything to remove), and generates transcripts and chapters for them.

There is no upstream for a local feed. MinusPod is the publisher, not a proxy. That changes a few things covered below: how episodes get added, what happens to your original files, and what parts of a normal feed's settings don't apply.

## Creating a local feed

`POST /api/v1/feeds` with `feedType: "local"` instead of a `sourceUrl`:

```json
{
  "feedType": "local",
  "title": "My Archive Show",
  "slug": "my-archive-show",
  "description": "Everything I recorded before I had an RSS feed",
  "author": "Jane Host",
  "explicit": false,
  "categories": ["Comedy", "Arts"]
}
```

`title` is required; `slug` is derived from the title if you don't supply one. Pick the slug carefully: it's immutable after creation, same as for subscribed feeds, and it's also the name of the feed's import directory on disk (see below). Everything else is optional at creation time and editable afterward with `PATCH /api/v1/feeds/{slug}`.

A local feed has no artwork until you upload one. The feed still works without it, but most podcast apps and directories expect cover art, so upload one as soon as you can:

```
POST /api/v1/feeds/{slug}/artwork
```

Multipart, field `file`, JPEG or PNG. If the image is under 1400x1400 the response carries a `warning` (podcast directories generally want at least that) but the upload still succeeds.

## Editing feed metadata

`PATCH /api/v1/feeds/{slug}` accepts `title`, `author`, `explicit`, `categories`, `description`, and `p20` on local feeds. These same fields 400 on a subscribed feed, since a refresh would just overwrite them from the upstream RSS. See [Podcasting 2.0 fields](#podcasting-20-fields) below for `p20`.

## Adding episodes

There are two ways to get episodes into a local feed: upload one at a time, or hand MinusPod a batch and let it work out the naming.

### Single-episode upload

```
POST /api/v1/feeds/{slug}/episodes
```

Multipart form: `audio` (required, `.mp3` only, up to 1 GB), plus optional `title`, `season`, `episode`, `publishedAt`, `description`, and `artwork`. Season defaults to 1; episode defaults to one past the highest existing episode number in that season. The pair mints an episode id of the form `s01e05`, and MinusPod refuses to overwrite an id that already exists (409); use the bulk import path with `overwrite: true` for that.

Once uploaded, edit an episode with `PATCH /api/v1/feeds/{slug}/episodes/{episodeId}` (title, description, season, episode, publishedAt, `p20`), several at once with `PATCH /api/v1/feeds/{slug}/episodes` (a JSON array of `{episodeId, ...}` edits, up to 500 per request, validated as a batch: one bad entry fails the whole request before anything is written), or delete one with `DELETE /api/v1/feeds/{slug}/episodes/{episodeId}`. Deleting removes the row and its files outright; there's no upstream feed to rediscover it from later, unlike deleting an episode on a subscribed feed. An episode currently processing can't be deleted (409) until it finishes or you cancel it.

### Bulk import

For archives, drop files into the import directory or upload a batch, then run a dry-run scan before committing.

**Where files come from**: two places, and you can use either or both in the same import.

- **Staging area** (`<data>/import-staging/<slug>/`): files you `POST` to `/api/v1/feeds/{slug}/import/upload` (multipart, repeated `files` field). MinusPod owns this directory: it moves each file's audio out on commit and deletes anything left over once the import finishes.
- **Import directory** (`<data>/import/<slug>/`): files you place there yourself, outside MinusPod. This is for archives already sitting on the same host or a mounted volume. MinusPod only ever moves the audio file out of it on commit; sidecar files (`.txt`, `.json`, artwork) are left exactly where you put them. Point this at the same filesystem as your data directory if you can: the commit is then a same-volume move rather than a copy, which matters for a large archive.

Either way, files must follow the naming scheme below before MinusPod will touch them.

#### Naming scheme

```
s01e01 - The Beginning.mp3      audio (required)
s01e01 - The Beginning.txt      description sidecar (optional)
s01e01 - The Beginning.jpg      episode artwork sidecar (optional)
s01e01 - The Beginning.json     metadata sidecar (optional)
```

- The season/episode token (`s01e01`) is case-insensitive and must be zero-padded to at least 2 digits, at the start of the filename.
- A sidecar is matched to its audio file by exact basename, not just the token: `s01e01.mp3` pairs with `s01e01.txt`, not with `s01e01 - Title.txt`.
- Everything after `" - "` becomes the episode title. No title, and no title sidecar override, falls back to `Episode {n}`.
- Audio must be `.mp3`. Anything else with a recognized audio extension (`.m4a`, `.wav`, `.flac`, `.aac`, `.ogg`, `.wma`, `.opus`, `.aiff`, `.aif`) is rejected with a hint to convert it first:

  ```
  ffmpeg -i in.m4a -codec:a libmp3lame -q:a 2 out.mp3
  ```

- Artwork sidecars are `.jpg`, `.jpeg`, or `.png`. Description sidecars are `.txt`.
- Files that don't match the scheme at all are listed in the dry-run report with a reason and never imported.

#### JSON sidecar

The optional `.json` sidecar overrides everything else derived from the filename, including the season/episode token. All fields are optional; any key that isn't one of the five below fails the whole sidecar (fail closed: the episode is skipped, not imported with partial metadata):

```json
{
  "title": "The Beginning",
  "description": "...",
  "published_at": "2019-03-01T12:00:00Z",
  "season": 1,
  "episode": 1
}
```

| Field | Type | Notes |
|---|---|---|
| `title` | string, 1-500 chars | |
| `description` | string | Plain text, or the HTML subset the app already renders elsewhere |
| `published_at` | string | ISO 8601, timezone required |
| `season` | integer >= 0 | |
| `episode` | integer >= 1 | |

A formal JSON Schema for this file is published at [`docs/schemas/episode-sidecar.schema.json`](schemas/episode-sidecar.schema.json), so you can validate a batch of sidecars before importing.

#### Publish dates

If you don't set `published_at` (sidecar or per-episode edit), MinusPod synthesizes one from episode order: sorted by (season, episode), the newest episode in the batch anchors at import time and each earlier one steps back a day. An explicit date anywhere in the batch also anchors the schedule, and MinusPod spaces the episodes between anchors evenly rather than always stepping by exactly a day.

Explicit dates that land out of order relative to the (season, episode) sort are a hard error: the dry-run report names the conflicting files, and the batch can't commit until you fix the sidecars.

#### Duplicates and existing episodes

Two files landing on the same episode id within one batch is a per-file error on both. An id that already exists in the feed is also an error, unless you pass `overwrite: true`, in which case a re-import fully replaces that episode: new audio and metadata come in, and the prior transcripts, chapters, and processing history are discarded as the episode goes back to `discovered`. Existing episodes are never silently overwritten without the flag.

#### Scan, then commit

```
POST /api/v1/feeds/{slug}/import/scan       {"source": "staging" | "directory" | "both", "overwrite": false}
```

Returns a dry-run plan: every file MinusPod is about to import (with resolved season/episode/title and its dates marked `explicit` or `synthesized`), every rejected file with a reason, and a `planHash` covering the exact set of files and their sizes/mtimes. Nothing is written yet.

```
POST /api/v1/feeds/{slug}/import/commit     {"planHash": "...", "source": "both", "overwrite": false}
```

Echo back the `planHash` from the scan. MinusPod re-scans the same source(s) server-side and compares hashes; if anything on disk changed since the scan, commit 409s and asks you to re-scan rather than importing a plan that no longer matches reality. A second commit while one is already running for the same feed also 409s: only one import runs per feed at a time.

Commit runs in the background; poll it with:

```
GET /api/v1/feeds/{slug}/import/status
```

## Processing behavior

Whether an imported or uploaded episode gets queued for ad detection depends on one thing: was the feed empty before this batch landed.

- **Into an empty feed** (the very first import, populating an archive from scratch): nothing is queued, no matter what auto-process is set to. A hundred-episode backfill isn't "new content" in the sense auto-process exists for.
- **Into a feed that already has episodes** (a later single upload, or a later import): each newly added episode enters the normal auto-process gate, same as a newly discovered episode on a subscribed feed, except there's no publish-date recency check. A backdated archive episode you just added is still new content to MinusPod even though its synthesized date is old.

Either way, with auto-process off (or an episode that didn't get queued), the episode still processes on first play, via manual reprocess, or through the bulk episode actions, exactly like a subscribed feed.

Until an episode is processed, its enclosure URL is unversioned, which triggers just-in-time processing the first time a player requests it, the same behavior as an unprocessed episode on a subscribed feed.

## Podcasting 2.0 fields

Local feeds carry a pragmatic subset of the Podcast Namespace, set through the `p20` object on the feed (`PATCH /api/v1/feeds/{slug}`) and on individual episodes (`PATCH /api/v1/feeds/{slug}/episodes/{episodeId}`).

Feed level (`p20`): `funding` (list of `{text, url}`), `person` (list of `{text, role, group, img, href}`), `license` (list of `{text, url}`), `location` (list of `{text, geo, osm}`), `txt` (list of `{text, purpose}`), plus the scalars `medium` (one of `podcast`, `music`, `video`, `film`, `audiobook`, `newsletter`, `blog`), `locked` (`yes` or `no`), and `locked_owner` (an email address; blank clears it). `guid` is minted once at creation from the feed URL and can't be set or changed through this field. Sending `p20: null` clears the five list tags and any locked owner, but leaves `guid`, `medium`, and `locked` alone.

Episode level (`p20`): `person` and `location`, both lists in the same shape as above.

Out of scope for now: value-for-value splits, `liveItem`, `podroll`, and Podping announcements for local feeds (MinusPod's Podping support today only listens for upstream announcements on subscribed feeds; it doesn't publish its own).

## What doesn't apply to local feeds

Because there's no upstream feed, the following don't apply to a local feed: RSS refresh (`POST /api/v1/feeds/{slug}/refresh` returns 400, "Local feed has no upstream to refresh"), Podping, and the cross-fetch differential. The processing pipeline itself (detection mode, cue templates, chapters mode, segment actions, queue priority, retention overrides, the episode title skip list) works exactly as it does on a subscribed feed.

## Retention, backups, and originals

The pre-cut original audio is the *only* copy of a local episode; there's no upstream to re-download it from. The scheduled retention sweep (global or per-feed window, and the keep-original-audio setting) never touches a local feed's originals: local feeds are excluded from that sweep entirely, no matter what the window is set to.

The one exception is the operator-triggered "Clear all processed audio" action (`POST /api/v1/system/cleanup`), which resets every feed's processed episodes regardless of their retention window. It still honors a feed set to Archive (`retentionDaysOverride: 0`), but a local feed that isn't archived is not otherwise exempt from this specific action the way it is from the scheduled sweep. If you keep local feeds and use this action, set them to Archive first, or avoid it.

If you back up MinusPod, make sure your backup covers `<data>/podcasts/<slug>/` for local feeds. The database backup alone does not include audio.

## OPML export

Local feeds are skipped from OPML export in `mode=original` (there's no upstream URL to export; the internal `local://` placeholder would leak into the file). They're included as normal in `mode=modified`, which exports the MinusPod-served feed URLs.

## Reference

- [OpenAPI specification](../openapi.yaml) - full request/response shapes for every endpoint above
- [Episode sidecar JSON Schema](schemas/episode-sidecar.schema.json)
- [Glossary](glossary.md) - local feed, import directory, staging area, sidecar file, sNNeNN naming token, dry-run import plan, synthesized publish date
- [Podcasting 2.0](podcasting-2.0.md) - how MinusPod's namespace support differs for feeds it publishes itself versus feeds it re-serves

---

[< Docs index](README.md) | [Project README](../README.md)
