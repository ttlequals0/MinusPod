"""Local feeds are imported archives: the retained original is the only
copy (no upstream to re-download). The retention sweep and the
processing-cancellation cleanup must never delete it."""
import database as database_mod
import storage as storage_mod
import pytest

from database import Database


@pytest.fixture(autouse=True)
def _reset_singletons():
    database_mod.Database._instance = None
    storage_mod.Storage._instance = None
    yield
    database_mod.Database._instance = None
    storage_mod.Storage._instance = None


@pytest.fixture
def db(tmp_path):
    return Database(str(tmp_path))


def _make_local_with_old_episode(db, storage):
    db.create_podcast('arc', 'local://arc', feed_type='local')
    db.update_podcast('arc', retention_days_override=1)
    db.upsert_episode('arc', 's01e01', original_url='local://s01e01',
                      status='processed', title='old',
                      processed_file='s01e01.mp3')
    # Backdate so any sweep would catch it
    conn = db.get_connection()
    conn.execute("UPDATE episodes SET processed_at='2020-01-01T00:00:00Z', "
                 "created_at='2020-01-01T00:00:00Z' WHERE episode_id='s01e01'")
    conn.commit()
    orig = storage.get_original_path('arc', 's01e01')
    orig.parent.mkdir(parents=True, exist_ok=True)
    orig.write_bytes(b'AUDIO')
    return orig


def test_local_original_survives_sweep_with_retention(db, tmp_path):
    from storage import Storage
    storage = Storage(str(tmp_path))
    orig = _make_local_with_old_episode(db, storage)
    db.cleanup_old_episodes(storage=storage)
    assert orig.exists(), "retention sweep must never touch local originals"


def test_delete_processed_file_keep_original(db, tmp_path):
    from storage import Storage
    storage = Storage(str(tmp_path))
    db.create_podcast('arc', 'local://arc', feed_type='local')
    db.upsert_episode('arc', 's01e01', original_url='local://s01e01',
                      status='processed', title='old')
    orig = storage.get_original_path('arc', 's01e01')
    orig.parent.mkdir(parents=True, exist_ok=True)
    orig.write_bytes(b'AUDIO')
    storage.delete_processed_file('arc', 's01e01', keep_original=True)
    assert orig.exists()
