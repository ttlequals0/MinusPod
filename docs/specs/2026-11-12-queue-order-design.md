# Processing order for queued episodes: design

Issue #997.

## Context

The auto-process queue runs pending episodes by priority, highest first, and
then by the time each one was queued (`ORDER BY priority DESC, created_at ASC`
in `src/database/queue.py`). An episode's priority is its feed's queue
priority plus boosts for a fresh release, a manual request or a reprocess.

A bulk add of a back catalog queues every episode at about the same moment
with the same priority, so they run in the order the bulk action inserted
them. That order suits a news show. A listener who starts a show at episode 1
wants the oldest episodes ready first. Today there is no way to ask for that
or to re-sort what is already queued.

## Goal

An operator can choose the order in which a feed's queued episodes are
processed: as queued (today's behavior), oldest published first, or newest
published first. There is a global default and a per-feed override, and a
change applies at once to episodes already waiting.

## Non-goals

- Changing the order between feeds. Which feed gets the next turn is decided
  as it is today.
- Changing priorities or boosts. A fresh release, a manual request, a
  reprocess, a feed's queue priority and a hand-set episode priority all
  still run first.
- Drag-and-drop ordering of single queue rows. The per-episode priority route
  already covers moving one episode.

## Decision: the setting

| Where | Name | Values | Default |
|---|---|---|---|
| Global setting | `queue_order` (API `queueOrder`) | `queued`, `oldest_first`, `newest_first` | `queued` |
| Per feed | `queue_order` column (API `queueOrder`) | the same three, or null | null: use the global setting |

`queued` is what happens today, so an upgrade changes nothing until an
operator picks another value.

The global setting is read through `GET /api/v1/settings` and saved through
`PUT /api/v1/settings/ad-detection`, with a default, validation and reset. It
is included in config export and import. The per-feed value is read and saved
with the feed (`GET` and `PATCH /api/v1/feeds/{slug}`), beside
`queuePriority`. The column is added with `_add_column_if_missing`; no
existing data changes.

## Decision: what the order applies to

The order applies among pending episodes of one feed that have the same
priority. Those episodes hold a set of places in the queue. The places stay
where they are; the order decides which of the feed's episodes takes each
place.

- `oldest_first`: the earliest publish date takes the feed's first place.
- `newest_first`: the latest publish date takes it.
- `queued`: episodes take the feed's remaining places in the order they were queued.

An episode with no publish date goes after the dated ones. Ties fall back to
the time queued.

The order is worked out when the queue is read, so changing the setting,
globally or for one feed, re-sorts the waiting episodes on the next read. One
query fragment gives the order to the claim, the "next episode" read, the
pending list and the queue status. The Processing Queue panel therefore shows
the order the worker will follow.

A claim consumes the place assigned to the selected episode. If another row
held that place, the two rows exchange their stored place keys in the same
write transaction as the claim. Removing the claimed row then leaves every
other feed in its displayed position. Changing an order setting still writes
no queue rows. Original queue timestamps remain available for ties and for
returning to As queued.

Deferred episodes, pending recuts and the approval queue keep their own
orders.

## Decision: UI

Follows `design-guide/UI-CONTRACT.md`.

- Queue page, in the Queue control section after the priority boosts:
  "Processing order" with the three choices. It saves on change like the
  boosts, with one line of help saying that it applies within a feed and that
  priorities still come first.
- Feed settings, beside "Queue priority": "Processing order" with "Use
  default", "As queued", "Oldest first" and "Newest first". It saves on change
  like Queue priority.
- The Processing Queue panel needs no new control. Its rows come back in the
  new order.

## Docs

- `openapi.yaml` and `docs/api-and-webhooks.md`: `queueOrder` on settings and
  on the feed.
- The queue section of the user docs explains the three orders and how they
  combine with priority.
- `CHANGELOG.md`.

## Tests

- Setting: validation, default, reset, export and import; per-feed value
  saved, cleared, and rejected when invalid.
- Order: one feed, same priority, each of the three values; a feed override
  against a different global value; a fresh, manual or hand-set episode still
  runs first; two feeds interleaved keep their places; missing publish dates;
  equal publish dates.
- A changed setting re-sorts waiting episodes with no other write.
- The claim, the pending list and the queue status agree on the order,
  including across a page boundary of the pending list.
- Two workers claiming at once never take the same episode.
- An upgrade from a database without the column keeps today's order.
- OpenAPI conformance for the changed routes.
- Frontend: vitest for both controls, plus Playwright screenshots at the
  UI-CONTRACT widths with a coarse pointer at phone widths.

## Alternatives considered

- Sorting the bulk action's insert order. It would not help episodes already
  queued, and a second bulk add would land behind the first.
- Re-stamping priorities by publish date. It would mix order into priority
  and fight the boosts and hand-set priorities.
- A one-time "Sort queue" button. It leaves the next bulk add unsorted.
