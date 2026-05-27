"""Tests for the v2 backfill of processing_history.ads_detected.

v1 (shipped in 2.5.29) compared history.ads_detected against
episodes.ads_removed_firstpass. firstpass is pass-1 DETECTION count,
not post-reviewer cuts. v1 only corrected episodes where the reviewer
rejected zero ads.

v2 uses (ads_removed - ads_removed_secondpass) which equals post-reviewer
pass-1 cuts, regardless of how many the reviewer rejected.

The canonical case v2 fixes that v1 missed: macbreak-weekly-audio
2d9ccd57b93b. firstpass detection=10, reviewer kept 6, verification
cuts=2, total=8. v1 saw 6 != 10 and skipped. v2 sees 6 == 8 - 2 and
corrects to 8.
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
    d = tempfile.mkdtemp(prefix="minuspod_backfill_v2_test_")
    Database._instance = None
    yield d
    Database._instance = None
    shutil.rmtree(d, ignore_errors=True)


def _migration_marker_present(db_path, name):
    with closing(sqlite3.connect(db_path)) as conn:
        row = conn.execute(
            "SELECT 1 FROM schema_migrations WHERE name = ?", (name,)
        ).fetchone()
        return row is not None


def _insert_podcast(conn, podcast_id, slug):
    conn.execute(
        "INSERT INTO podcasts (id, slug, source_url, title) VALUES (?, ?, ?, ?)",
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


def _clear_v2_marker(db_path):
    with closing(sqlite3.connect(db_path)) as conn:
        conn.execute(
            "DELETE FROM schema_migrations "
            "WHERE name = 'backfill_history_ads_detected_v2_postreviewer_cuts'"
        )
        conn.commit()


def _reload_db(d):
    Database._instance = None
    return Database(data_dir=d)


class TestV2CorrectsReviewerRejectionCases:
    """v2's headline case: episodes where the reviewer rejected some
    ads, so detection > cuts. v1's predicate missed these because it
    compared against detection count."""

    def test_macbreak_style_case(self, fresh_db_dir):
        """The exact pattern from macbreak-weekly-audio:2d9ccd57b93b:
        firstpass detection=10, total cuts=8 (so reviewer-kept-pass-1=6
        and verification=2). Buggy writer captured 6 in history."""
        _reload_db(fresh_db_dir)
        db_path = os.path.join(fresh_db_dir, 'podcast.db')

        _seed_state(db_path, episode={
            'podcast_id': 1, 'podcast_slug': 'macbreak',
            'episode_id': '2d9ccd57b93b',
            'ads_removed': 8, 'firstpass': 10, 'secondpass': 2,
        }, history_rows=[{
            'episode_id': '2d9ccd57b93b',
            'processed_at': '2026-05-27T00:55:56Z',
            'ads_detected': 6,
        }])
        _clear_v2_marker(db_path)
        _reload_db(fresh_db_dir)

        with closing(sqlite3.connect(db_path)) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT ads_detected FROM processing_history WHERE episode_id = ?",
                ('2d9ccd57b93b',),
            ).fetchone()
        assert row['ads_detected'] == 8, (
            'macbreak-style row should be corrected to ads_removed (8). '
            f"got {row['ads_detected']}"
        )
        assert _migration_marker_present(
            db_path, 'backfill_history_ads_detected_v2_postreviewer_cuts'
        )


class TestV2DoesNotDoubleCorrectV1Rows:
    """When v1 already corrected a row (ads_detected was bumped to
    ads_removed), v2's predicate must not re-touch it. With v1-correct
    rows, ads_detected == ads_removed; v2 requires
    ads_detected == ads_removed - secondpass. Since secondpass > 0,
    these are never equal, so v2 naturally skips."""

    def test_v1_corrected_row_left_alone(self, fresh_db_dir):
        _reload_db(fresh_db_dir)
        db_path = os.path.join(fresh_db_dir, 'podcast.db')

        # Simulate the post-v1 state: ads_detected was set to the total
        # (the row that v1 corrected). v2 should NOT touch this.
        _seed_state(db_path, episode={
            'podcast_id': 1, 'podcast_slug': 'show', 'episode_id': 'ep1',
            'ads_removed': 5, 'firstpass': 2, 'secondpass': 3,
        }, history_rows=[{
            'episode_id': 'ep1',
            'processed_at': '2026-05-26T00:00:00Z',
            'ads_detected': 5,
        }])
        _clear_v2_marker(db_path)
        _reload_db(fresh_db_dir)

        with closing(sqlite3.connect(db_path)) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT ads_detected FROM processing_history WHERE episode_id = 'ep1'"
            ).fetchone()
        assert row['ads_detected'] == 5


class TestV2SkipsNoVerificationEpisodes:
    """Episodes with secondpass=0 had no verification cuts, so the
    pre-2.5.28 bug did not apply. v2 should leave them alone."""

    def test_no_change_when_secondpass_zero(self, fresh_db_dir):
        _reload_db(fresh_db_dir)
        db_path = os.path.join(fresh_db_dir, 'podcast.db')
        _seed_state(db_path, episode={
            'podcast_id': 1, 'podcast_slug': 'show', 'episode_id': 'ep1',
            'ads_removed': 3, 'firstpass': 5, 'secondpass': 0,
        }, history_rows=[{
            'episode_id': 'ep1',
            'processed_at': '2026-05-26T00:00:00Z',
            'ads_detected': 3,
        }])
        _clear_v2_marker(db_path)
        _reload_db(fresh_db_dir)

        with closing(sqlite3.connect(db_path)) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT ads_detected FROM processing_history WHERE episode_id = 'ep1'"
            ).fetchone()
        assert row['ads_detected'] == 3


class TestV2SkipsOlderReprocessRows:
    """Only the LATEST history row per (podcast_id, episode_id) is
    candidate for correction. Older reprocess rows cannot be verified
    against episodes (which retains only latest state)."""

    def test_old_row_preserved_latest_corrected(self, fresh_db_dir):
        _reload_db(fresh_db_dir)
        db_path = os.path.join(fresh_db_dir, 'podcast.db')

        # Episode latest state: detection=10, total=8, verification=2.
        # Pass-1 cuts after reviewer = 8 - 2 = 6.
        _seed_state(db_path, episode={
            'podcast_id': 1, 'podcast_slug': 'show', 'episode_id': 'ep1',
            'ads_removed': 8, 'firstpass': 10, 'secondpass': 2,
        }, history_rows=[
            {'episode_id': 'ep1',
             'processed_at': '2026-05-20T00:00:00Z',
             'ads_detected': 5},
            {'episode_id': 'ep1',
             'processed_at': '2026-05-26T00:00:00Z',
             'ads_detected': 6},
        ])
        _clear_v2_marker(db_path)
        _reload_db(fresh_db_dir)

        with closing(sqlite3.connect(db_path)) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT ads_detected, processed_at FROM processing_history "
                "WHERE episode_id = 'ep1' ORDER BY processed_at"
            ).fetchall()
        assert rows[0]['ads_detected'] == 5, 'older reprocess untouched'
        assert rows[1]['ads_detected'] == 8, 'latest row corrected to total'


class TestV2Idempotency:
    """Gate prevents re-execution. Second boot is a no-op even if data
    once again matches the predicate."""

    def test_second_boot_is_a_no_op(self, fresh_db_dir):
        _reload_db(fresh_db_dir)
        db_path = os.path.join(fresh_db_dir, 'podcast.db')

        _seed_state(db_path, episode={
            'podcast_id': 1, 'podcast_slug': 'show', 'episode_id': 'ep1',
            'ads_removed': 8, 'firstpass': 10, 'secondpass': 2,
        }, history_rows=[{
            'episode_id': 'ep1',
            'processed_at': '2026-05-26T00:00:00Z',
            'ads_detected': 6,
        }])
        _clear_v2_marker(db_path)
        _reload_db(fresh_db_dir)  # v2 corrects 6 -> 8 here

        # Plant a row that matches the v2 predicate again. Gate must
        # prevent the second boot from touching it.
        with closing(sqlite3.connect(db_path)) as conn:
            conn.execute(
                "UPDATE processing_history SET ads_detected = 6 "
                "WHERE episode_id = 'ep1'"
            )
            conn.commit()
        _reload_db(fresh_db_dir)

        with closing(sqlite3.connect(db_path)) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT ads_detected FROM processing_history WHERE episode_id = 'ep1'"
            ).fetchone()
        assert row['ads_detected'] == 6, (
            'gate must prevent v2 from running again; planted row stays at 6'
        )


class TestV2SkipsFailedRows:
    """Failed processing rows have ads_detected=0 by design; the
    predicate must not match them."""

    def test_failed_row_not_corrected(self, fresh_db_dir):
        _reload_db(fresh_db_dir)
        db_path = os.path.join(fresh_db_dir, 'podcast.db')
        _seed_state(db_path, episode={
            'podcast_id': 1, 'podcast_slug': 'show', 'episode_id': 'ep1',
            'ads_removed': 8, 'firstpass': 10, 'secondpass': 2,
        }, history_rows=[{
            'episode_id': 'ep1',
            'processed_at': '2026-05-26T00:00:00Z',
            'ads_detected': 0,
            'status': 'failed',
        }])
        _clear_v2_marker(db_path)
        _reload_db(fresh_db_dir)

        with closing(sqlite3.connect(db_path)) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT ads_detected FROM processing_history WHERE episode_id = 'ep1'"
            ).fetchone()
        assert row['ads_detected'] == 0


class TestV1AndV2CoexistInSingleBoot:
    """For a deployer upgrading from <=2.5.28 directly to 2.5.30, both
    migrations run in the same boot. v1 fixes the easy-case rows (no
    reviewer rejection), v2 fixes the harder-case rows. The two
    predicates are mutually exclusive at any moment, so they don't
    fight over the same row."""

    def test_single_boot_corrects_both_pattern_types(self, fresh_db_dir):
        _reload_db(fresh_db_dir)
        db_path = os.path.join(fresh_db_dir, 'podcast.db')

        with closing(sqlite3.connect(db_path)) as conn:
            _insert_podcast(conn, 1, 'show')
            # Episode A: no reviewer rejection (firstpass detection
            # equals pass-1 cuts). v1 should correct.
            _insert_episode(conn, 1, 'epA',
                            ads_removed=5, firstpass=2, secondpass=3)
            _insert_history(conn, podcast_id=1, podcast_slug='show',
                            episode_id='epA',
                            processed_at='2026-05-26T00:00:00Z',
                            ads_detected=2)
            # Episode B: reviewer rejected some (firstpass detection 10
            # but only 6 cut). v2 should correct.
            _insert_episode(conn, 1, 'epB',
                            ads_removed=8, firstpass=10, secondpass=2)
            _insert_history(conn, podcast_id=1, podcast_slug='show',
                            episode_id='epB',
                            processed_at='2026-05-26T00:00:00Z',
                            ads_detected=6)
            conn.commit()

        # Clear both gate rows so both migrations run on next boot.
        with closing(sqlite3.connect(db_path)) as conn:
            conn.execute(
                "DELETE FROM schema_migrations WHERE name IN "
                "('backfill_history_ads_detected_for_verification', "
                "'backfill_history_ads_detected_v2_postreviewer_cuts')"
            )
            conn.commit()

        _reload_db(fresh_db_dir)

        with closing(sqlite3.connect(db_path)) as conn:
            conn.row_factory = sqlite3.Row
            rows = {r['episode_id']: r['ads_detected'] for r in conn.execute(
                "SELECT episode_id, ads_detected FROM processing_history"
            )}
        assert rows['epA'] == 5, 'v1 should correct no-rejection case to total'
        assert rows['epB'] == 8, 'v2 should correct reviewer-rejected case to total'
