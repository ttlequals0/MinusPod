"""Tests for the one-shot backfill of processing_history.ads_detected.

Pre-2.5.28 the writer at _record_history_and_event recorded
pass-1-after-reviewer cuts only. This migration repairs the LATEST
history row per episode where the bug signature is unambiguous:

  ads_detected == episode.ads_removed_firstpass
  AND episode.ads_removed_secondpass > 0

Older reprocess rows can't be safely corrected (episodes table only
retains latest state) and are deliberately left alone.
"""
import os
import shutil
import sqlite3
import tempfile
from contextlib import closing

import pytest

from database import Database


@pytest.fixture
def fresh_db_dir():
    d = tempfile.mkdtemp(prefix="minuspod_backfill_test_")
    Database._instance = None
    yield d
    Database._instance = None
    shutil.rmtree(d, ignore_errors=True)


def _migration_marker_present(db_path):
    with closing(sqlite3.connect(db_path)) as conn:
        row = conn.execute(
            "SELECT 1 FROM schema_migrations "
            "WHERE name = 'backfill_history_ads_detected_for_verification'"
        ).fetchone()
        return row is not None


def _insert_podcast(conn, podcast_id, slug):
    conn.execute(
        "INSERT INTO podcasts (id, slug, source_url, title) "
        "VALUES (?, ?, ?, ?)",
        (podcast_id, slug, f'https://example.test/{slug}.xml', slug),
    )


def _insert_episode(conn, podcast_id, episode_id, *, ads_removed,
                    firstpass, secondpass):
    conn.execute(
        "INSERT INTO episodes (podcast_id, episode_id, original_url, status, "
        "ads_removed, ads_removed_firstpass, ads_removed_secondpass) "
        "VALUES (?, ?, ?, 'processed', ?, ?, ?)",
        (podcast_id, episode_id, f'https://example.test/{episode_id}.mp3',
         ads_removed, firstpass, secondpass),
    )


def _insert_history(conn, *, podcast_id, podcast_slug, episode_id,
                    processed_at, ads_detected, status='completed'):
    conn.execute(
        "INSERT INTO processing_history "
        "(podcast_id, podcast_slug, episode_id, processed_at, status, ads_detected) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (podcast_id, podcast_slug, episode_id, processed_at, status, ads_detected),
    )


def _seed_state(db_path, *, episode, history_rows):
    """Insert podcast, episode, and history rows into a fresh DB.

    The schema-init pass for a brand-new DB runs _seed_default_settings
    but no episode/history seeds, so the tables are empty for us.
    """
    podcast_id = episode['podcast_id']
    podcast_slug = episode['podcast_slug']
    with closing(sqlite3.connect(db_path)) as conn:
        _insert_podcast(conn, podcast_id, podcast_slug)
        _insert_episode(
            conn, podcast_id, episode['episode_id'],
            ads_removed=episode['ads_removed'],
            firstpass=episode['firstpass'],
            secondpass=episode['secondpass'],
        )
        for h in history_rows:
            _insert_history(conn, podcast_id=podcast_id,
                            podcast_slug=podcast_slug, **h)
        conn.commit()


def _clear_migration_marker(db_path):
    """Drop the schema_migrations marker so the backfill runs again on
    next Database init. Lets tests stage state and trigger the migration
    explicitly."""
    with closing(sqlite3.connect(db_path)) as conn:
        conn.execute(
            "DELETE FROM schema_migrations "
            "WHERE name = 'backfill_history_ads_detected_for_verification'"
        )
        conn.commit()


def _reload_db(d):
    Database._instance = None
    return Database(data_dir=d)


class TestBackfillLatestHistoryRow:
    """The latest history row of an episode with verification cuts gets
    its ads_detected corrected to the true total."""

    def test_corrects_latest_row_when_bug_signature_matches(self, fresh_db_dir):
        _reload_db(fresh_db_dir)
        db_path = os.path.join(fresh_db_dir, 'podcast.db')

        # Episode had pass-1=2 + verification=3 = 5 total. History was
        # written by the pre-2.5.28 buggy writer so it captured only 2.
        _seed_state(db_path, episode={
            'podcast_id': 1, 'podcast_slug': 'show', 'episode_id': 'ep1',
            'ads_removed': 5, 'firstpass': 2, 'secondpass': 3,
        }, history_rows=[{
            'episode_id': 'ep1',
            'processed_at': '2026-05-26T00:00:00Z',
            'ads_detected': 2,
        }])
        _clear_migration_marker(db_path)
        _reload_db(fresh_db_dir)

        with closing(sqlite3.connect(db_path)) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT ads_detected FROM processing_history WHERE episode_id = 'ep1'"
            ).fetchone()
        assert row['ads_detected'] == 5
        assert _migration_marker_present(db_path)


class TestBackfillSkipsAlreadyCorrect:
    """Rows where ads_detected already matches the true total are left
    alone."""

    def test_no_change_when_already_correct(self, fresh_db_dir):
        _reload_db(fresh_db_dir)
        db_path = os.path.join(fresh_db_dir, 'podcast.db')
        _seed_state(db_path, episode={
            'podcast_id': 1, 'podcast_slug': 'show', 'episode_id': 'ep1',
            'ads_removed': 5, 'firstpass': 2, 'secondpass': 3,
        }, history_rows=[{
            'episode_id': 'ep1',
            'processed_at': '2026-05-26T00:00:00Z',
            'ads_detected': 5,
        }])
        _clear_migration_marker(db_path)
        _reload_db(fresh_db_dir)

        with closing(sqlite3.connect(db_path)) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT ads_detected FROM processing_history WHERE episode_id = 'ep1'"
            ).fetchone()
        assert row['ads_detected'] == 5


class TestBackfillSkipsNoVerificationCuts:
    """Rows where the episode had zero verification cuts are left alone.
    Even if ads_detected differs from ads_removed for some other reason,
    we don't touch them because the bug only manifested when
    secondpass > 0.
    """

    def test_no_change_when_episode_had_no_secondpass(self, fresh_db_dir):
        _reload_db(fresh_db_dir)
        db_path = os.path.join(fresh_db_dir, 'podcast.db')
        _seed_state(db_path, episode={
            'podcast_id': 1, 'podcast_slug': 'show', 'episode_id': 'ep1',
            'ads_removed': 3, 'firstpass': 3, 'secondpass': 0,
        }, history_rows=[{
            'episode_id': 'ep1',
            'processed_at': '2026-05-26T00:00:00Z',
            'ads_detected': 3,
        }])
        _clear_migration_marker(db_path)
        _reload_db(fresh_db_dir)

        with closing(sqlite3.connect(db_path)) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT ads_detected FROM processing_history WHERE episode_id = 'ep1'"
            ).fetchone()
        assert row['ads_detected'] == 3


class TestBackfillSkipsOldReprocessRows:
    """When an episode was reprocessed, only the LATEST history row is
    corrected. Older rows are left alone because the episodes table
    only retains the latest state and we cannot recover the true
    pass-2 count for prior runs.
    """

    def test_old_row_preserved_latest_corrected(self, fresh_db_dir):
        _reload_db(fresh_db_dir)
        db_path = os.path.join(fresh_db_dir, 'podcast.db')

        # Episode's latest state: 5 ads total (2 pass-1 + 3 pass-2).
        # History has two rows: an older reprocess (ads_detected=4) and
        # the latest reprocess (ads_detected=2 from the bug).
        _seed_state(db_path, episode={
            'podcast_id': 1, 'podcast_slug': 'show', 'episode_id': 'ep1',
            'ads_removed': 5, 'firstpass': 2, 'secondpass': 3,
        }, history_rows=[
            {'episode_id': 'ep1',
             'processed_at': '2026-05-20T00:00:00Z',
             'ads_detected': 4},
            {'episode_id': 'ep1',
             'processed_at': '2026-05-26T00:00:00Z',
             'ads_detected': 2},
        ])
        _clear_migration_marker(db_path)
        _reload_db(fresh_db_dir)

        with closing(sqlite3.connect(db_path)) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT ads_detected, processed_at FROM processing_history "
                "WHERE episode_id = 'ep1' ORDER BY processed_at"
            ).fetchall()

        assert rows[0]['ads_detected'] == 4, 'older reprocess untouched'
        assert rows[1]['ads_detected'] == 5, 'latest row corrected to total'


class TestBackfillIdempotency:
    """Running the migration twice does nothing on the second run; the
    gate row prevents repeat execution even if the data would still
    match the bug signature.
    """

    def test_second_boot_is_a_no_op(self, fresh_db_dir):
        _reload_db(fresh_db_dir)
        db_path = os.path.join(fresh_db_dir, 'podcast.db')

        _seed_state(db_path, episode={
            'podcast_id': 1, 'podcast_slug': 'show', 'episode_id': 'ep1',
            'ads_removed': 5, 'firstpass': 2, 'secondpass': 3,
        }, history_rows=[{
            'episode_id': 'ep1',
            'processed_at': '2026-05-26T00:00:00Z',
            'ads_detected': 2,
        }])
        _clear_migration_marker(db_path)
        _reload_db(fresh_db_dir)  # first run: backfills 2 -> 5

        # Now plant a fresh divergent row that would match the bug
        # signature, but the gate row is set so the migration must NOT
        # re-run.
        with closing(sqlite3.connect(db_path)) as conn:
            conn.execute(
                "UPDATE processing_history SET ads_detected = 2 "
                "WHERE episode_id = 'ep1'"
            )
            conn.commit()
        _reload_db(fresh_db_dir)  # second run: should NOT touch the row

        with closing(sqlite3.connect(db_path)) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT ads_detected FROM processing_history WHERE episode_id = 'ep1'"
            ).fetchone()
        assert row['ads_detected'] == 2, (
            'gated migration must not re-run; planted row should remain at 2'
        )


class TestBackfillSkipsFailedRow:
    """Failed processing rows are recorded with ads_detected=0 and must
    not be touched."""

    def test_failed_row_not_corrected(self, fresh_db_dir):
        _reload_db(fresh_db_dir)
        db_path = os.path.join(fresh_db_dir, 'podcast.db')
        _seed_state(db_path, episode={
            'podcast_id': 1, 'podcast_slug': 'show', 'episode_id': 'ep1',
            'ads_removed': 5, 'firstpass': 2, 'secondpass': 3,
        }, history_rows=[{
            'episode_id': 'ep1',
            'processed_at': '2026-05-26T00:00:00Z',
            'ads_detected': 0,
            'status': 'failed',
        }])
        _clear_migration_marker(db_path)
        _reload_db(fresh_db_dir)

        with closing(sqlite3.connect(db_path)) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT ads_detected FROM processing_history WHERE episode_id = 'ep1'"
            ).fetchone()
        assert row['ads_detected'] == 0
