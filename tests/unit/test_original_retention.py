"""Tests for the 2.5.14 split retention: original audio retention can be
shorter than processed retention.

Covers:
- `cleanup_old_episodes` two-pass behaviour (original-only pre-pass then the
  existing full-cleanup pass).
- No-op when `keep_original_audio` is off.
- No-op when `original_retention_days` is unset or >= `retention_days`.
- API endpoint clamps `originalRetentionDays > retentionDays`.
"""
import os
import sys
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))

from database import Database


def _iso(dt):
    return dt.strftime('%Y-%m-%dT%H:%M:%SZ')


def _seed_processed_episode(db, slug, ep_id, processed_at):
    """Insert a podcast + episode marked processed with a processed_file path."""
    if not db.get_podcast_by_slug(slug):
        db.create_podcast(slug, f'https://example.com/{slug}.xml', slug)
    db.upsert_episode(
        slug, ep_id,
        original_url=f'https://example.com/{ep_id}.mp3',
        title=f'Episode {ep_id}',
        status='processed',
    )
    conn = db.get_connection()
    conn.execute(
        "UPDATE episodes SET processed_file = ?, processed_at = ?, status = 'processed' "
        "WHERE episode_id = ?",
        (f'{ep_id}.mp3', processed_at, ep_id),
    )
    conn.commit()


@pytest.fixture
def db(tmp_path):
    Database._instance = None
    if hasattr(Database, '_initialized'):
        Database._initialized = False
    instance = Database(data_dir=str(tmp_path))
    yield instance
    Database._instance = None


def test_original_only_pass_drops_original_keeps_processed(db):
    """Episode processed 10 days ago, original retention 7, processed retention 30.
    Pre-pass should call delete_original_only on it; main pass should not touch
    the episode (it's still inside the 30-day window)."""
    db.set_setting('retention_days', '30', is_default=False)
    db.set_setting('original_retention_days', '7', is_default=False)
    db.set_setting('keep_original_audio', 'true', is_default=False)
    _seed_processed_episode(
        db, 'show-a', 'ep-old',
        _iso(datetime.now(timezone.utc) - timedelta(days=10)),
    )

    storage = MagicMock()
    storage.delete_original_only.return_value = (True, 1_500_000)

    reset_count, freed_mb = db.cleanup_old_episodes(storage=storage)

    storage.delete_original_only.assert_called_once_with('show-a', 'ep-old')
    assert reset_count == 0  # main pass did not reset (within processed window)
    assert freed_mb == 0.0  # main-pass return value (originals freed go in log only)
    # Episode still 'processed' afterwards
    ep = db.get_episode('show-a', 'ep-old')
    assert ep['status'] == 'processed'


def test_main_pass_resets_when_processed_retention_elapsed(db):
    """Episode processed 40 days ago, original retention 7, processed retention 30.
    The original-only pre-pass should NOT touch it (it's past the main retention,
    so the main pass owns it). The main pass should reset it."""
    db.set_setting('retention_days', '30', is_default=False)
    db.set_setting('original_retention_days', '7', is_default=False)
    db.set_setting('keep_original_audio', 'true', is_default=False)
    _seed_processed_episode(
        db, 'show-b', 'ep-ancient',
        _iso(datetime.now(timezone.utc) - timedelta(days=40)),
    )

    storage = MagicMock()
    storage.delete_original_only.return_value = (False, 0)
    storage.cleanup_episode_files.return_value = 2_000_000

    reset_count, freed_mb = db.cleanup_old_episodes(storage=storage)

    storage.delete_original_only.assert_not_called()
    assert reset_count == 1
    assert freed_mb > 0


def test_no_op_when_keep_original_audio_off(db):
    """No originals were ever saved; the pre-pass must be a no-op."""
    db.set_setting('retention_days', '30', is_default=False)
    db.set_setting('original_retention_days', '7', is_default=False)
    db.set_setting('keep_original_audio', 'false', is_default=False)
    _seed_processed_episode(
        db, 'show-c', 'ep-x',
        _iso(datetime.now(timezone.utc) - timedelta(days=10)),
    )

    storage = MagicMock()
    db.cleanup_old_episodes(storage=storage)

    storage.delete_original_only.assert_not_called()


def test_no_op_when_original_retention_unset(db):
    """Operator never set original_retention_days; pre-pass must be a no-op
    so the original keeps its existing behaviour (same retention as processed)."""
    db.set_setting('retention_days', '30', is_default=False)
    # original_retention_days intentionally NOT set
    db.set_setting('keep_original_audio', 'true', is_default=False)
    _seed_processed_episode(
        db, 'show-d', 'ep-y',
        _iso(datetime.now(timezone.utc) - timedelta(days=10)),
    )

    storage = MagicMock()
    db.cleanup_old_episodes(storage=storage)

    storage.delete_original_only.assert_not_called()


def test_no_op_when_original_retention_meets_or_exceeds_processed(db):
    """If the operator typed a value >= retention_days, the two windows are
    effectively the same and the main pass already covers the original."""
    db.set_setting('retention_days', '30', is_default=False)
    db.set_setting('original_retention_days', '30', is_default=False)
    db.set_setting('keep_original_audio', 'true', is_default=False)
    _seed_processed_episode(
        db, 'show-e', 'ep-z',
        _iso(datetime.now(timezone.utc) - timedelta(days=10)),
    )

    storage = MagicMock()
    db.cleanup_old_episodes(storage=storage)

    storage.delete_original_only.assert_not_called()


def test_pre_pass_skips_episodes_still_within_original_window(db):
    """Episode processed 3 days ago. Original retention 7 days. Should NOT be
    swept yet (still inside its window)."""
    db.set_setting('retention_days', '30', is_default=False)
    db.set_setting('original_retention_days', '7', is_default=False)
    db.set_setting('keep_original_audio', 'true', is_default=False)
    _seed_processed_episode(
        db, 'show-f', 'ep-fresh',
        _iso(datetime.now(timezone.utc) - timedelta(days=3)),
    )

    storage = MagicMock()
    db.cleanup_old_episodes(storage=storage)

    storage.delete_original_only.assert_not_called()


def test_clamp_caps_original_to_processed():
    """API clamp helper: original > processed => clamp to processed."""
    from api.settings import _clamp_original_retention
    assert _clamp_original_retention(10, 30) == 10
    assert _clamp_original_retention(30, 10) == 10  # under cap untouched
    assert _clamp_original_retention(30, 30) == 30


def test_clamp_passes_through_when_retention_disabled():
    """When retention_days=0 (disabled), original is passed through unchanged
    because there is no processed peer to outlive."""
    from api.settings import _clamp_original_retention
    assert _clamp_original_retention(0, 7) == 7
    assert _clamp_original_retention(0, 365) == 365


def test_pre_pass_handles_storage_failure_gracefully(db):
    """If storage.delete_original_only returns (False, 0) (file already gone,
    permission error, etc), the pre-pass should not crash; the main pass
    should still run."""
    db.set_setting('retention_days', '30', is_default=False)
    db.set_setting('original_retention_days', '7', is_default=False)
    db.set_setting('keep_original_audio', 'true', is_default=False)
    _seed_processed_episode(
        db, 'show-g', 'ep-missing-orig',
        _iso(datetime.now(timezone.utc) - timedelta(days=10)),
    )

    storage = MagicMock()
    storage.delete_original_only.return_value = (False, 0)

    reset_count, freed_mb = db.cleanup_old_episodes(storage=storage)

    storage.delete_original_only.assert_called_once()
    assert reset_count == 0  # main pass did not own this row
    assert freed_mb == 0.0
