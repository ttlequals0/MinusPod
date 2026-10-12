# Recents feed (#721)

One opt-in combined feed that carries every episode processed on the instance
from the day the feed was created onward, so a subscriber adds it once and new
podcasts appear without re-importing OPML.

## Data model

- One `podcasts` row, `feed_type = 'recents'`, fixed slug `recents`,
  `source_url = 'recents://'`. At most one may exist; creating a second is a
  409.
- Editable: `title` (default "Recents"), `description`, artwork (upload via
  the existing `POST /feeds/<slug>/artwork`; default is the bundled MinusPod
  logo served by the artwork routes when no upload exists).
- `created_at` is the cutoff. No episodes of its own, no processing or
  override settings, no upstream refresh.

## Membership

An episode is in the feed when all of the following hold:

- it belongs to a subscribed or local feed (never another recents row);
- `status = 'processed'` and it has a processed file;
- `published_at` is set and `published_at >= recents.created_at`.

`processed_at` is never consulted, so a backlog episode processed after the
feed exists stays out because its publish date is older. An episode with no
publish date is excluded rather than falling back to discovery or processing
time. Ordering is `published_at` descending, capped by the global
`maxFeedEpisodes`.

## Served RSS

- `GET /recents` (and the `?key=` form under authenticated feeds) renders from
  the DB only. Channel: title, description, artwork of the recents row.
- Each item is the source episode's item as the source feed would emit it:
  enclosure `/episodes/<source-slug>/<id>-vN.mp3`, transcript, chapters,
  episode artwork, GUID, all under the source slug, so nothing is copied and
  the feed key works unchanged. The item title is prefixed with the source
  podcast title.
- The chapter list in descriptions follows the global `chaptersInNotes`
  setting (the recents row has no override).
- Cached like other feeds. Rebuilt when any episode finishes processing (the
  same seam that rebuilds the source feed), when the recents row is edited, and
  by the existing "Regenerate feeds" action.

## API

- `POST /feeds` with `{"feedType": "recents", "title"?, "description"?}`.
- `GET /feeds/recents` returns `feedType: 'recents'` plus the usual fields;
  `episodes` for it lists the member episodes with their source slug.
- `PATCH /feeds/recents` accepts `title` and `description` only; any other
  override field is a 400.
- `DELETE /feeds/recents` removes the row and cached RSS; source episodes are
  untouched.
- OPML exports (both modes) exclude it.

## UI

- Add Feed offers a third type, Recents, only while none exists.
- Dashboard shows it as a normal card with the logo; its episode counts come
  from membership.
- Feed page: episode rows link to the source feed's episode page (no episode
  actions on the recents page). The settings panel is replaced by title,
  description, and artwork.
- Feed detail hides refresh, reprocess-all, processing mode, cue templates,
  and every per-feed override.

## Tests

- Migration: `feed_type` accepts `recents`; single-row constraint.
- Render: cutoff by publish date (including an old episode reprocessed after
  creation, and a NULL publish date), cross-feed items with source URLs, feed
  key, title prefix, cap, chapters block gating.
- API round-trips, 409 on a second row, 400 on override fields, OPML
  exclusion, rebuild on episode completion and on edit.
- vitest: Add Feed type, feed page link targets, hidden settings.
