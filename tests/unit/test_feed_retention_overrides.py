"""Per-feed retention overrides: archive mode and keep-original.

Covers:
- `retention_days_override` beats the global `retention_days` setting.
- An override of 0 archives the feed: the scheduled sweep never touches it,
  and neither does the operator's force_all wipe.
- Feeds on different windows are swept correctly in the same run.
- `keep_original_audio_override` beats the global `keep_original_audio`.
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


def _days_ago(n):
    return _iso(datetime.now(timezone.utc) - timedelta(days=n))


def _seed_processed_episode(db, slug, ep_id, processed_at):
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
        "UPDATE episodes SET processed_file = ?, processed_at = ?, "
        "status = 'processed' WHERE episode_id = ?",
        (f'{ep_id}.mp3', processed_at, ep_id),
    )
    conn.commit()


@pytest.fixture
def db(tmp_path):
    Database._instance = None
    if hasattr(Database, '_initialized'):
        Database._initialized = False
    instance = Database(data_dir=str(tmp_path))
    instance.set_setting('retention_days', '30', is_default=False)
    yield instance
    Database._instance = None


@pytest.fixture
def storage():
    s = MagicMock()
    s.delete_original_only.return_value = (True, 0)
    return s


def _reset_slugs(db, storage, force_all=False):
    """Run the sweep and report which feeds had episodes reset."""
    db.delete_episodes = MagicMock(side_effect=lambda slug, ids, st: (len(ids), 0.0))
    db.cleanup_old_episodes(force_all=force_all, storage=storage)
    return {call.args[0] for call in db.delete_episodes.call_args_list}


# --- resolver ---------------------------------------------------------------

def test_override_wins_over_the_global_setting(db):
    db.create_podcast('show-a', 'https://example.com/a.xml', 'show-a')
    db.update_podcast('show-a', retention_days_override=7)
    assert db.resolve_retention_days('show-a') == 7


def test_no_override_falls_back_to_the_global_setting(db):
    db.create_podcast('show-a', 'https://example.com/a.xml', 'show-a')
    assert db.resolve_retention_days('show-a') == 30


def test_zero_override_marks_the_feed_archived(db):
    db.create_podcast('show-a', 'https://example.com/a.xml', 'show-a')
    db.update_podcast('show-a', retention_days_override=0)
    assert db.resolve_retention_days('show-a') == 0
    assert db.is_archived('show-a') is True


def test_a_feed_on_the_global_window_is_not_archived(db):
    db.create_podcast('show-a', 'https://example.com/a.xml', 'show-a')
    assert db.is_archived('show-a') is False


# --- scheduled sweep --------------------------------------------------------

def test_archived_feed_survives_the_scheduled_sweep(db, storage):
    _seed_processed_episode(db, 'keeper', 'ep-ancient', _days_ago(400))
    db.update_podcast('keeper', retention_days_override=0)

    assert _reset_slugs(db, storage) == set()
    assert db.get_episode('keeper', 'ep-ancient')['status'] == 'processed'


def test_a_shorter_override_expires_sooner_than_the_global(db, storage):
    _seed_processed_episode(db, 'short', 'ep-10d', _days_ago(10))
    db.update_podcast('short', retention_days_override=7)

    assert _reset_slugs(db, storage) == {'short'}


def test_a_longer_override_outlives_the_global(db, storage):
    _seed_processed_episode(db, 'long', 'ep-60d', _days_ago(60))
    db.update_podcast('long', retention_days_override=365)

    assert _reset_slugs(db, storage) == set()


def test_feeds_on_different_windows_are_swept_in_one_run(db, storage):
    _seed_processed_episode(db, 'archived', 'ep-a', _days_ago(400))
    db.update_podcast('archived', retention_days_override=0)
    _seed_processed_episode(db, 'short', 'ep-b', _days_ago(10))
    db.update_podcast('short', retention_days_override=7)
    _seed_processed_episode(db, 'global', 'ep-c', _days_ago(45))
    _seed_processed_episode(db, 'fresh', 'ep-d', _days_ago(2))

    assert _reset_slugs(db, storage) == {'short', 'global'}


def test_an_override_still_applies_when_the_global_is_disabled(db, storage):
    # retention_days 0 used to short-circuit the whole sweep.
    db.set_setting('retention_days', '0', is_default=False)
    _seed_processed_episode(db, 'short', 'ep-10d', _days_ago(10))
    db.update_podcast('short', retention_days_override=7)

    assert _reset_slugs(db, storage) == {'short'}


# --- force_all wipe ---------------------------------------------------------

def test_archived_feed_survives_the_force_all_wipe(db, storage):
    _seed_processed_episode(db, 'keeper', 'ep-a', _days_ago(1))
    db.update_podcast('keeper', retention_days_override=0)
    _seed_processed_episode(db, 'normal', 'ep-b', _days_ago(1))

    assert _reset_slugs(db, storage, force_all=True) == {'normal'}


def test_force_all_still_wipes_a_feed_inheriting_a_disabled_global(db, storage):
    # Global 0 means "retention off", not "archive everything". An explicit
    # operator wipe outranks it; only a per-feed 0 is a deliberate archive.
    db.set_setting('retention_days', '0', is_default=False)
    _seed_processed_episode(db, 'normal', 'ep-a', _days_ago(1))

    assert _reset_slugs(db, storage, force_all=True) == {'normal'}


# --- keep-original override -------------------------------------------------

def test_keep_original_override_wins_over_the_global(db):
    db.create_podcast('show-a', 'https://example.com/a.xml', 'show-a')
    db.set_setting('keep_original_audio', 'true', is_default=False)
    db.update_podcast('show-a', keep_original_audio_override=0)
    assert db.resolve_keep_original_audio('show-a') is False


def test_keep_original_override_can_opt_in_against_a_global_off(db):
    db.create_podcast('show-a', 'https://example.com/a.xml', 'show-a')
    db.set_setting('keep_original_audio', 'false', is_default=False)
    db.update_podcast('show-a', keep_original_audio_override=1)
    assert db.resolve_keep_original_audio('show-a') is True


def test_keep_original_falls_back_to_the_global(db):
    db.create_podcast('show-a', 'https://example.com/a.xml', 'show-a')
    db.set_setting('keep_original_audio', 'false', is_default=False)
    assert db.resolve_keep_original_audio('show-a') is False


def test_keep_original_defaults_on_when_nothing_is_set(db):
    db.create_podcast('show-a', 'https://example.com/a.xml', 'show-a')
    assert db.resolve_keep_original_audio('show-a') is True


def test_originals_sweep_skips_a_feed_with_keep_original_off(db, storage):
    db.set_setting('original_retention_days', '7', is_default=False)
    db.set_setting('keep_original_audio', 'true', is_default=False)
    _seed_processed_episode(db, 'no-originals', 'ep-a', _days_ago(10))
    db.update_podcast('no-originals', keep_original_audio_override=0)

    db.cleanup_old_episodes(storage=storage)
    storage.delete_original_only.assert_not_called()


def test_originals_sweep_runs_for_a_feed_that_opts_in(db, storage):
    db.set_setting('original_retention_days', '7', is_default=False)
    db.set_setting('keep_original_audio', 'false', is_default=False)
    _seed_processed_episode(db, 'has-originals', 'ep-a', _days_ago(10))
    db.update_podcast('has-originals', keep_original_audio_override=1)

    db.cleanup_old_episodes(storage=storage)
    storage.delete_original_only.assert_called_once_with('has-originals', 'ep-a')


def test_archived_feed_keeps_its_originals_too(db, storage):
    # Archive means "keep this show", not "keep only the cut version".
    # Operators who want the space back turn keep-original off for the feed.
    db.set_setting('original_retention_days', '7', is_default=False)
    _seed_processed_episode(db, 'keeper', 'ep-a', _days_ago(400))
    db.update_podcast('keeper', retention_days_override=0)

    db.cleanup_old_episodes(storage=storage)
    storage.delete_original_only.assert_not_called()
