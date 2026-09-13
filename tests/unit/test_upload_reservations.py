"""Cross-process local upload reservation tests."""
import multiprocessing
import os
from contextlib import contextmanager
from pathlib import Path

import pytest

_TIMEOUT = 5


def _child_reserve(data_dir, episode_id, ready, release):
    os.environ['DATA_DIR'] = data_dir
    from database import Database
    Database._instance = None
    db = Database(data_dir)
    if episode_id is None:
        result = db.reserve_next_episode_upload('local', 1)
    else:
        result = db.reserve_episode_uploads(
            'local', [episode_id], operation='individual')
    ready.put(result)
    release.wait(timeout=_TIMEOUT)


@pytest.fixture
def upload_db(temp_dir, monkeypatch):
    monkeypatch.setenv('DATA_DIR', temp_dir)
    from database import Database
    Database._instance = None
    db = Database(temp_dir)
    db.create_podcast('local', 'local://local', 'Local', feed_type='local')
    yield db
    Database._instance = None


def test_simultaneous_implicit_uploads_get_distinct_numbers(upload_db, temp_dir):
    ctx = multiprocessing.get_context('fork')
    ready = ctx.Queue()
    release = ctx.Event()
    children = [
        ctx.Process(target=_child_reserve, args=(temp_dir, None, ready, release))
        for _ in range(2)
    ]
    for child in children:
        child.start()
    try:
        results = [ready.get(timeout=_TIMEOUT) for _ in children]
        assert {result['episode_id'] for result in results} == {'s01e01', 's01e02'}
    finally:
        release.set()
        for child in children:
            child.join(timeout=_TIMEOUT)
            if child.is_alive():
                child.kill()
                child.join(timeout=_TIMEOUT)


def test_explicit_collision_has_one_winner(upload_db, temp_dir):
    ctx = multiprocessing.get_context('fork')
    ready = ctx.Queue()
    release = ctx.Event()
    children = [
        ctx.Process(target=_child_reserve, args=(temp_dir, 's01e07', ready, release))
        for _ in range(2)
    ]
    for child in children:
        child.start()
    try:
        results = [ready.get(timeout=_TIMEOUT) for _ in children]
        assert sum(not result['conflicts'] for result in results) == 1
        assert sum(result['conflicts'] == ['s01e07'] for result in results) == 1
    finally:
        release.set()
        for child in children:
            child.join(timeout=_TIMEOUT)
            if child.is_alive():
                child.kill()
                child.join(timeout=_TIMEOUT)


def test_dead_owner_reservation_is_reclaimed(upload_db, temp_dir):
    ctx = multiprocessing.get_context('fork')
    ready = ctx.Queue()
    leave = ctx.Event()
    child = ctx.Process(
        target=_child_reserve, args=(temp_dir, 's01e09', ready, leave))
    child.start()
    first = ready.get(timeout=_TIMEOUT)
    assert not first['conflicts']
    child.kill()
    child.join(timeout=_TIMEOUT)

    second = upload_db.reserve_episode_uploads(
        'local', ['s01e09'], operation='individual')
    assert not second['conflicts']
    states = upload_db.get_connection().execute(
        "SELECT state FROM upload_reservations WHERE target_key = 's01e09' "
        "ORDER BY created_at, rowid"
    ).fetchall()
    assert [row['state'] for row in states] == ['failed', 'reserved']


def test_dead_prepared_upload_removes_its_temp_file(upload_db, temp_dir):
    root = Path(temp_dir).resolve()
    reservation = upload_db.reserve_next_episode_upload('local', 1)
    temp_path = root / 'podcasts' / 'local' / 'episodes' / 'owned.part'
    temp_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path.write_bytes(b'partial')
    assert upload_db.prepare_upload_reservation(
        reservation['id'], str(temp_path.relative_to(root)))
    upload_db.get_connection().execute(
        "UPDATE upload_reservations SET owner_pid = 2147483647 WHERE id = ?",
        (reservation['id'],),
    )
    upload_db.get_connection().commit()

    retry = upload_db.reserve_next_episode_upload('local', 1)

    assert retry['episode_id'] == 's01e01'
    assert not temp_path.exists()


def test_feed_deletion_marker_blocks_new_reservations(upload_db):
    podcast = upload_db.get_podcast_by_slug('local')
    upload_db.get_connection().execute(
        "UPDATE podcasts SET deletion_requested_at = '2026-01-01T00:00:00Z' WHERE id = ?",
        (podcast['id'],),
    )
    upload_db.get_connection().commit()

    assert upload_db.reserve_next_episode_upload('local', 1) is None
    result = upload_db.reserve_episode_uploads(
        'local', ['s01e01'], operation='import')
    assert result['conflicts'] == ['s01e01']


def test_dead_import_publication_restores_winner_files(upload_db, temp_dir):
    upload_db.upsert_episode(
        'local', 's01e01', original_url='local://s01e01', status='discovered')
    root = Path(temp_dir).resolve()
    episode_dir = root / 'podcasts' / 'local' / 'episodes'
    episode_dir.mkdir(parents=True, exist_ok=True)
    final_path = episode_dir / 's01e01-original.mp3'
    backup_path = episode_dir / '.upload-crash.backup'
    source_path = root / 'import' / 'local' / 's01e01.mp3'
    source_path.parent.mkdir(parents=True, exist_ok=True)
    final_path.write_bytes(b'winner')
    source_path.write_bytes(b'incomplete')

    result = upload_db.reserve_episode_uploads(
        'local', ['s01e01'], operation='import', overwrite=True)
    reservation_id = result['reserved']['s01e01']
    assert upload_db.prepare_upload_reservation(
        reservation_id, str(source_path.relative_to(root)))
    assert upload_db.begin_upload_publication(
        reservation_id, str(source_path.relative_to(root)),
        str(backup_path.relative_to(root)))
    os.replace(final_path, backup_path)
    os.replace(source_path, final_path)
    upload_db.get_connection().execute(
        "UPDATE upload_reservations SET owner_pid = 2147483647 WHERE id = ?",
        (reservation_id,),
    )
    upload_db.get_connection().commit()

    retry = upload_db.reserve_episode_uploads(
        'local', ['s01e01'], operation='import', overwrite=True)

    assert not retry['conflicts']
    assert final_path.read_bytes() == b'winner'
    assert source_path.read_bytes() == b'incomplete'
    assert not backup_path.exists()


def test_dead_import_publication_restores_legacy_target(upload_db, temp_dir):
    upload_db.upsert_episode(
        'local', 's01e0001', original_url='local://s01e0001', status='discovered')
    root = Path(temp_dir).resolve()
    episode_dir = root / 'podcasts' / 'local' / 'episodes'
    episode_dir.mkdir(parents=True, exist_ok=True)
    legacy_path = episode_dir / 's01e0001-original.mp3'
    final_path = episode_dir / 's01e01-original.mp3'
    backup_path = episode_dir / '.upload-legacy-crash.backup'
    source_path = root / 'import' / 'local' / 's01e01.mp3'
    source_path.parent.mkdir(parents=True, exist_ok=True)
    legacy_path.write_bytes(b'legacy-winner')
    source_path.write_bytes(b'incomplete')

    result = upload_db.reserve_episode_uploads(
        'local', ['s01e01'], operation='import', overwrite=True)
    reservation_id = result['reserved']['s01e01']
    assert upload_db.prepare_upload_reservation(
        reservation_id, str(source_path.relative_to(root)))
    assert upload_db.begin_upload_publication(
        reservation_id, str(source_path.relative_to(root)),
        str(backup_path.relative_to(root)), str(legacy_path.relative_to(root)))
    os.replace(legacy_path, backup_path)
    os.replace(source_path, final_path)
    upload_db.get_connection().execute(
        "UPDATE upload_reservations SET owner_pid = 2147483647 WHERE id = ?",
        (reservation_id,),
    )
    upload_db.get_connection().commit()

    upload_db.reserve_episode_uploads(
        'local', ['s01e01'], operation='import', overwrite=True)

    assert legacy_path.read_bytes() == b'legacy-winner'
    assert source_path.read_bytes() == b'incomplete'
    assert not final_path.exists()


def test_dead_individual_publication_removes_only_owner_file(upload_db, temp_dir):
    root = Path(temp_dir).resolve()
    reservation = upload_db.reserve_next_episode_upload('local', 1)
    episode_dir = root / 'podcasts' / 'local' / 'episodes'
    episode_dir.mkdir(parents=True, exist_ok=True)
    temp_path = episode_dir / 'owned.part'
    final_path = episode_dir / 's01e01-original.mp3'
    backup_path = episode_dir / '.upload-crash.backup'
    temp_path.write_bytes(b'incomplete')
    assert upload_db.prepare_upload_reservation(
        reservation['id'], str(temp_path.relative_to(root)))
    assert upload_db.begin_upload_publication(
        reservation['id'], str(temp_path.relative_to(root)),
        str(backup_path.relative_to(root)))
    os.replace(temp_path, final_path)
    upload_db.get_connection().execute(
        "UPDATE upload_reservations SET owner_pid = 2147483647 WHERE id = ?",
        (reservation['id'],),
    )
    upload_db.get_connection().commit()

    retry = upload_db.reserve_next_episode_upload('local', 1)

    assert retry['episode_id'] == 's01e01'
    assert not final_path.exists()
    assert not temp_path.exists()


def test_crash_before_individual_move_preserves_unowned_file(upload_db, temp_dir):
    root = Path(temp_dir).resolve()
    reservation = upload_db.reserve_next_episode_upload('local', 1)
    episode_dir = root / 'podcasts' / 'local' / 'episodes'
    episode_dir.mkdir(parents=True, exist_ok=True)
    temp_path = episode_dir / 'owned.part'
    final_path = episode_dir / 's01e01-original.mp3'
    backup_path = episode_dir / '.upload-crash.backup'
    temp_path.write_bytes(b'incomplete')
    final_path.write_bytes(b'unowned')
    assert upload_db.prepare_upload_reservation(
        reservation['id'], str(temp_path.relative_to(root)))
    assert upload_db.begin_upload_publication(
        reservation['id'], str(temp_path.relative_to(root)),
        str(backup_path.relative_to(root)))
    upload_db.get_connection().execute(
        "UPDATE upload_reservations SET owner_pid = 2147483647 WHERE id = ?",
        (reservation['id'],),
    )
    upload_db.get_connection().commit()

    upload_db.reserve_next_episode_upload('local', 1)

    assert final_path.read_bytes() == b'unowned'
    assert not temp_path.exists()


def test_recovery_is_durable_before_later_admission_failure(
        upload_db, temp_dir, monkeypatch):
    upload_db.upsert_episode(
        'local', 's01e01', original_url='local://s01e01', status='discovered')
    root = Path(temp_dir).resolve()
    episode_dir = root / 'podcasts' / 'local' / 'episodes'
    episode_dir.mkdir(parents=True, exist_ok=True)
    final_path = episode_dir / 's01e01-original.mp3'
    backup_path = episode_dir / '.upload-recovery.backup'
    source_path = root / 'import' / 'local' / 's01e01.mp3'
    source_path.parent.mkdir(parents=True, exist_ok=True)
    final_path.write_bytes(b'winner')
    source_path.write_bytes(b'incomplete')

    result = upload_db.reserve_episode_uploads(
        'local', ['s01e01'], operation='import', overwrite=True)
    reservation_id = result['reserved']['s01e01']
    assert upload_db.prepare_upload_reservation(
        reservation_id, str(source_path.relative_to(root)))
    assert upload_db.begin_upload_publication(
        reservation_id, str(source_path.relative_to(root)),
        str(backup_path.relative_to(root)))
    os.replace(final_path, backup_path)
    os.replace(source_path, final_path)
    conn = upload_db.get_connection()
    conn.execute(
        "UPDATE upload_reservations SET owner_pid = 2147483647 WHERE id = ?",
        (reservation_id,),
    )
    conn.commit()

    original_transaction = upload_db.transaction
    calls = 0

    @contextmanager
    def fail_admission(immediate=False):
        nonlocal calls
        calls += 1
        if calls == 3:
            raise RuntimeError('admission failed after recovery')
        with original_transaction(immediate=immediate) as tx:
            yield tx

    monkeypatch.setattr(upload_db, 'transaction', fail_admission)
    with pytest.raises(RuntimeError, match='admission failed'):
        upload_db.reserve_episode_uploads(
            'local', ['s01e01'], operation='import', overwrite=True)

    monkeypatch.setattr(upload_db, 'transaction', original_transaction)
    upload_db.reserve_episode_uploads(
        'local', ['s01e01'], operation='import', overwrite=True)
    assert final_path.read_bytes() == b'winner'
    assert source_path.read_bytes() == b'incomplete'
    assert not backup_path.exists()
    row = conn.execute(
        "SELECT state, recovery_state FROM upload_reservations WHERE id = ?",
        (reservation_id,),
    ).fetchone()
    assert (row['state'], row['recovery_state']) == ('failed', None)


def test_dead_published_reservation_cleans_backup_only(upload_db, temp_dir):
    root = Path(temp_dir).resolve()
    episode_dir = root / 'podcasts' / 'local' / 'episodes'
    episode_dir.mkdir(parents=True, exist_ok=True)
    final_path = episode_dir / 's01e01-original.mp3'
    backup_path = episode_dir / '.upload-published.backup'
    final_path.write_bytes(b'published')
    backup_path.write_bytes(b'previous')

    reservation = upload_db.reserve_next_episode_upload('local', 1)
    conn = upload_db.get_connection()
    conn.execute(
        "UPDATE upload_reservations SET state = 'published', owner_pid = ?, "
        "backup_name = ?, finished_at = updated_at WHERE id = ?",
        (2147483647, str(backup_path.relative_to(root)), reservation['id']),
    )
    conn.commit()

    retry = upload_db.reserve_next_episode_upload('local', 1)

    assert retry['episode_id'] == 's01e01'
    assert final_path.read_bytes() == b'published'
    assert not backup_path.exists()
    row = conn.execute(
        "SELECT backup_name FROM upload_reservations WHERE id = ?",
        (reservation['id'],),
    ).fetchone()
    assert row['backup_name'] is None


def test_individual_recovery_retries_after_state_commit_failure(
        upload_db, temp_dir, monkeypatch):
    upload_db.upsert_episode(
        'local', 's01e01', original_url='local://s01e01', status='discovered')
    root = Path(temp_dir).resolve()
    episode_dir = root / 'podcasts' / 'local' / 'episodes'
    episode_dir.mkdir(parents=True, exist_ok=True)
    final_path = episode_dir / 's01e01-original.mp3'
    backup_path = episode_dir / '.upload-retry.backup'
    temp_path = episode_dir / 'owned.part'
    final_path.write_bytes(b'previous')
    temp_path.write_bytes(b'replacement')

    result = upload_db.reserve_episode_uploads(
        'local', ['s01e01'], operation='individual', overwrite=True)
    reservation_id = result['reserved']['s01e01']
    assert upload_db.prepare_upload_reservation(
        reservation_id, str(temp_path.relative_to(root)))
    assert upload_db.begin_upload_publication(
        reservation_id, str(temp_path.relative_to(root)),
        str(backup_path.relative_to(root)))
    os.replace(final_path, backup_path)
    os.replace(temp_path, final_path)
    conn = upload_db.get_connection()
    conn.execute(
        "UPDATE upload_reservations SET owner_pid = 2147483647 WHERE id = ?",
        (reservation_id,),
    )
    conn.commit()

    original_transaction = upload_db.transaction
    calls = 0

    @contextmanager
    def fail_recovery_commit(immediate=False):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError('recovery state commit failed')
        with original_transaction(immediate=immediate) as tx:
            yield tx

    monkeypatch.setattr(upload_db, 'transaction', fail_recovery_commit)
    with pytest.raises(RuntimeError, match='recovery state commit failed'):
        upload_db._reap_upload_reservations()
    assert final_path.read_bytes() == b'previous'
    assert temp_path.read_bytes() == b'replacement'

    monkeypatch.setattr(upload_db, 'transaction', original_transaction)
    upload_db._reap_upload_reservations()

    assert final_path.read_bytes() == b'previous'
    assert not temp_path.exists()
    assert not backup_path.exists()
    row = conn.execute(
        "SELECT state, recovery_state, temp_name FROM upload_reservations WHERE id = ?",
        (reservation_id,),
    ).fetchone()
    assert (row['state'], row['recovery_state'], row['temp_name']) == (
        'failed', None, None)
