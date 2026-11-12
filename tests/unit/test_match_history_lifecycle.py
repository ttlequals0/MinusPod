"""Match history goes with its episode row and stays when only the audio is removed."""

import pytest

from tests.unit.test_peaks_deletion import (  # noqa: F401
    NOW_ISO, NOW_ISO_2, _commit_synchronously, db_storage, local_feed, real_mp3_bytes,
)
from local_import import build_import_plan

OTHER = 'a1b2c3d4e5f6'
KEPT = 'b1b2c3d4e5f6'


def _pattern(db):
    return db.create_ad_pattern(text_template='read ' * 12, scope='global')


def _match(db, pid, slug, episode_id):
    db.record_fingerprint_match(pid, episode_id, slug, 5.0, 35.0, 0.9, 0.8, True, False)


def _count(db, slug, episode_id):
    return db.get_connection().execute(
        "SELECT COUNT(*) FROM audio_fingerprint_matches WHERE podcast_slug = ? AND episode_id = ?",
        (slug, episode_id)).fetchone()[0]


@pytest.fixture
def world(db_storage, local_feed):  # noqa: F811
    db, storage = db_storage
    pid = _pattern(db)
    for ep in (OTHER, KEPT):
        db.upsert_episode(local_feed, ep, original_url=f'local://{ep}', title=ep, status='discovered')
        _match(db, pid, local_feed, ep)
    return db, storage, local_feed, pid


def test_deleting_a_local_episode_row_removes_only_its_matches(world):
    db, storage, slug, _pid = world
    assert db.delete_episode_rows(slug, [OTHER], storage) == 1
    assert _count(db, slug, OTHER) == 0
    assert _count(db, slug, KEPT) == 1


def test_bulk_delete_removes_every_listed_episodes_matches(world):
    db, storage, slug, _pid = world
    db.delete_episode_rows(slug, [OTHER, KEPT], storage)
    assert _count(db, slug, OTHER) == _count(db, slug, KEPT) == 0


def test_matches_of_another_feed_with_the_same_episode_id_survive(world):
    db, storage, slug, pid = world
    db.create_podcast('other-feed', 'local://other-feed', 'Other', feed_type='local')
    _match(db, pid, 'other-feed', OTHER)
    db.delete_episode_rows(slug, [OTHER], storage)
    assert _count(db, 'other-feed', OTHER) == 1


def test_resetting_an_episode_to_discovered_keeps_its_matches(world):
    db, storage, slug, _pid = world
    db.delete_episodes(slug, [OTHER], storage)
    assert _count(db, slug, OTHER) == 1


def test_deleting_a_feed_removes_its_matches(world):
    db, _storage, slug, _pid = world
    assert db.delete_podcast(slug)
    assert _count(db, slug, OTHER) == _count(db, slug, KEPT) == 0


def test_local_import_replacing_a_wide_id_row_removes_its_matches(db_storage, local_feed, real_mp3_bytes):  # noqa: F811
    db, storage = db_storage
    slug = local_feed
    pid = _pattern(db)
    wide_id = 's01e0006'
    db.upsert_episode(slug, wide_id, original_url=f'local://{wide_id}', status='discovered', title='Old',
                      season_number=1, episode_number=6, published_at=NOW_ISO, original_duration=1.0)
    _match(db, pid, slug, wide_id)
    src_dir = storage.import_source_dir(slug)
    src_dir.mkdir(parents=True)
    audio = src_dir / 'S01E06 - Pilot.mp3'
    audio.write_bytes(real_mp3_bytes)
    plan = build_import_plan(slug, [audio], existing_ids={wide_id}, overwrite=True, now_iso=NOW_ISO_2)
    _commit_synchronously(slug, plan, db, storage)
    assert _count(db, slug, wide_id) == 0


def test_migration_deletes_matches_of_vanished_episodes_once(world):
    db, _storage, slug, pid = world
    conn = db.get_connection()
    _match(db, pid, slug, 'c1c2c3c4c5c6')
    _match(db, pid, 'gone-feed', OTHER)
    conn.execute("DELETE FROM schema_migrations WHERE name = 'fingerprint_matches_drop_vanished_episodes'")
    db._run_fingerprint_matches_drop_vanished_episodes(conn)
    assert _count(db, slug, 'c1c2c3c4c5c6') == 0
    assert _count(db, 'gone-feed', OTHER) == 0
    assert _count(db, slug, OTHER) == _count(db, slug, KEPT) == 1
    _match(db, pid, slug, 'd1d2d3d4d5d6')
    db._run_fingerprint_matches_drop_vanished_episodes(conn)
    assert _count(db, slug, 'd1d2d3d4d5d6') == 1
