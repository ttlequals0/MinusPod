"""Test helpers for the processing queue."""


def peek_next_queued(db):
    """The next pending queue row in dequeue order, without claiming it."""
    from database.queue import _ORDERED_BY

    cte, params = db._ordered_pending()
    row = db.get_connection().execute(
        f"""{cte}
           SELECT q.*, p.slug as podcast_slug, p.title as podcast_title
           FROM ordered o
           JOIN auto_process_queue q ON q.id = o.id
           JOIN podcasts p ON q.podcast_id = p.id
           ORDER BY {_ORDERED_BY}
           LIMIT 1""",  # noqa: S608
        params,
    ).fetchone()
    return dict(row) if row else None
