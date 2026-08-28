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


def test_local_original_and_processed_survive_force_all(db, tmp_path):
    """The manual 'Clear all processed audio' path (force_all=True) must
    exempt local feeds exactly like the scheduled sweep: it holds the only
    copy of the audio, so neither the original nor the processed file may
    be deleted, and the episode row must not be reset."""
    from storage import Storage
    storage = Storage(str(tmp_path))
    orig = _make_local_with_old_episode(db, storage)
    processed = storage.get_episode_path('arc', 's01e01')
    processed.parent.mkdir(parents=True, exist_ok=True)
    processed.write_bytes(b'PROCESSED')

    reset, freed = db.cleanup_old_episodes(force_all=True, storage=storage)

    assert orig.exists(), "force_all must never delete a local feed's original"
    assert processed.exists(), "force_all must never delete a local feed's processed file"
    episode = db.get_episode('arc', 's01e01')
    assert episode['status'] == 'processed', "local feed episode must not be reset"
    assert episode['processed_file'] == 's01e01.mp3'


def test_delete_episodes_keep_original_preserves_original(db, tmp_path):
    """The bulk 'delete' action on a local feed must keep the retained
    original (it is the only copy) and only wipe the processed output,
    resetting the row to discovered like a normal delete."""
    from storage import Storage
    storage = Storage(str(tmp_path))
    db.create_podcast('arc', 'local://arc', feed_type='local')
    db.upsert_episode('arc', 's01e01', original_url='local://s01e01',
                      status='processed', title='old',
                      processed_file='s01e01.mp3')
    orig = storage.get_original_path('arc', 's01e01')
    orig.parent.mkdir(parents=True, exist_ok=True)
    orig.write_bytes(b'ORIGINAL')
    processed = storage.get_episode_path('arc', 's01e01')
    processed.write_bytes(b'PROCESSED')

    count, freed = db.delete_episodes('arc', ['s01e01'], storage, keep_original=True)

    assert count == 1
    assert orig.exists(), "keep_original=True must never delete the local original"
    assert not processed.exists(), "processed output must still be removed"
    episode = db.get_episode('arc', 's01e01')
    assert episode['status'] == 'discovered'
    assert freed > 0


def test_delete_episodes_default_still_wipes_original(db, tmp_path):
    """No regression: keep_original defaults to False, matching the
    pre-existing behavior a subscribed feed's delete action relies on."""
    from storage import Storage
    storage = Storage(str(tmp_path))
    db.create_podcast('show', 'https://example.com/show.xml')
    db.upsert_episode('show', 'abcdef012345',
                      original_url='https://example.com/abcdef012345.mp3',
                      status='processed', title='ep',
                      processed_file='abcdef012345.mp3')
    orig = storage.get_original_path('show', 'abcdef012345')
    orig.parent.mkdir(parents=True, exist_ok=True)
    orig.write_bytes(b'ORIGINAL')
    processed = storage.get_episode_path('show', 'abcdef012345')
    processed.write_bytes(b'PROCESSED')

    count, freed = db.delete_episodes('show', ['abcdef012345'], storage)

    assert count == 1
    assert not orig.exists()
    assert not processed.exists()


def test_subscribed_feed_still_cleaned_under_force_all(db, tmp_path):
    """No regression: force_all must still wipe a normal (non-local, non-
    archived) feed's processed episodes."""
    from storage import Storage
    storage = Storage(str(tmp_path))
    ep_id = 'abcdef012345'
    db.create_podcast('show', 'https://example.com/show.xml')
    db.upsert_episode('show', ep_id, original_url=f'https://example.com/{ep_id}.mp3',
                      status='processed', title='ep', processed_file=f'{ep_id}.mp3')
    processed = storage.get_episode_path('show', ep_id)
    processed.parent.mkdir(parents=True, exist_ok=True)
    processed.write_bytes(b'PROCESSED')

    reset, freed = db.cleanup_old_episodes(force_all=True, storage=storage)

    assert reset == 1
    assert not processed.exists(), "force_all must still clean subscribed feeds"
    episode = db.get_episode('show', ep_id)
    assert episode['status'] == 'discovered'
