"""episode_feed_details: lazy cache of the feed-rendering fields of episode_details."""
import json
import sqlite3
import time

import pytest

from database import Database

SLUG = 'example-podcast'
SINCE = '2026-01-01'
CHAPTERS = json.dumps({'version': '1.2.0', 'chapters': [{'startTime': 0, 'title': 'Intro'}]})
CUTS = [{'start': 10.0, 'end': 20.0}]
CUTS_RAW = json.dumps(CUTS)
DETAIL_KEYS = {'chapters_json', 'applied_cuts_json', 'has_transcript_vtt'}
NORMAL_BUSY_MS = 60000


def _add_episode(db, eid, day, status='processed', duration=1800.0):
    db.upsert_episode(
        SLUG, eid, original_url=f'https://example.com/{eid}.mp3',
        title=f'The Daily Tech Show {eid}', status=status,
        processed_file='/x.mp3' if status == 'processed' else None,
        original_duration=duration, published_at=f'2026-03-{day:02d}T00:00:00Z')


@pytest.fixture
def seeded(temp_db):
    db = temp_db
    db.create_podcast(SLUG, 'https://example.com/feed.xml', 'Example Podcast')
    _add_episode(db, 'a1b2c3d4e5f6', 3)
    _add_episode(db, 'b1b2c3d4e5f6', 2)
    _add_episode(db, 'c1b2c3d4e5f6', 1)
    db.save_episode_details(SLUG, 'a1b2c3d4e5f6', transcript_vtt='WEBVTT\n\nhi',
                            chapters_json=CHAPTERS)
    db.save_applied_cuts(SLUG, 'a1b2c3d4e5f6', CUTS)
    db.save_episode_details(SLUG, 'b1b2c3d4e5f6', transcript_text='text only')
    return db


def _pk(db, eid):
    return db.get_episode(SLUG, eid)['id']


def _pid(db):
    return db.get_podcast_by_slug(SLUG)['id']


def _cache_ids(db):
    return {r[0] for r in db.get_connection().execute(
        'SELECT episode_id FROM episode_feed_details')}


def _recents(db):
    return {r['episode_id']: r for r in db.get_recent_processed_episodes(SINCE, details=True)}


def _fill(db):
    db.get_recent_processed_episodes(SINCE, details=True)
    assert _pk(db, 'a1b2c3d4e5f6') in _cache_ids(db)


class TestRecentsDetails:
    def test_values_identical_before_and_after_fill(self, seeded):
        first = _recents(seeded)
        second = _recents(seeded)
        assert first == second
        full = first['a1b2c3d4e5f6']
        assert full['chapters_json'] == CHAPTERS
        assert full['applied_cuts_json'] == CUTS_RAW
        assert full['has_transcript_vtt']
        for eid in ('b1b2c3d4e5f6', 'c1b2c3d4e5f6'):
            assert first[eid]['chapters_json'] is None and first[eid]['applied_cuts_json'] is None
            assert not first[eid]['has_transcript_vtt']

    def test_key_set_is_plain_keys_plus_three(self, seeded):
        plain = set(seeded.get_recent_processed_episodes(SINCE)[0])
        for _ in range(2):
            detailed = set(seeded.get_recent_processed_episodes(SINCE, details=True)[0])
            assert detailed == plain | DETAIL_KEYS

    def test_first_call_fills_cache_for_rows_with_details(self, seeded):
        assert _cache_ids(seeded) == set()
        _recents(seeded)
        assert _cache_ids(seeded) == {_pk(seeded, 'a1b2c3d4e5f6'), _pk(seeded, 'b1b2c3d4e5f6')}

    def test_second_call_reads_cache(self, seeded):
        _recents(seeded)
        seeded.get_connection().execute(
            "UPDATE episode_feed_details SET chapters_json = 'sentinel'")
        seeded.get_connection().commit()
        assert _recents(seeded)['a1b2c3d4e5f6']['chapters_json'] == 'sentinel'

    def test_plain_call_does_not_touch_cache(self, seeded):
        seeded.get_recent_processed_episodes(SINCE)
        assert _cache_ids(seeded) == set()


class TestEmptyTranscript:
    def test_empty_vtt_is_not_a_transcript_before_and_after_fill(self, seeded):
        _add_episode(seeded, 'e1b2c3d4e5f6', 5)
        seeded.save_episode_details(SLUG, 'e1b2c3d4e5f6', transcript_vtt='')
        before = _recents(seeded)['e1b2c3d4e5f6']['has_transcript_vtt']
        assert _pk(seeded, 'e1b2c3d4e5f6') in _cache_ids(seeded)
        cached = seeded.get_connection().execute(
            'SELECT has_transcript_vtt FROM episode_feed_details WHERE episode_id = ?',
            (_pk(seeded, 'e1b2c3d4e5f6'),)).fetchone()[0]
        after = _recents(seeded)['e1b2c3d4e5f6']['has_transcript_vtt']
        assert not before and not after and cached == 0


class TestInvalidation:
    @pytest.mark.parametrize('mutate,field,expected', [
        (lambda db: db.save_episode_details(SLUG, 'a1b2c3d4e5f6', chapters_json='{"c": 1}'),
         'chapters_json', '{"c": 1}'),
        (lambda db: db.save_processing_assets(SLUG, 'a1b2c3d4e5f6', {'chapters': {'c': 2}}),
         'chapters_json', '{"c": 2}'),
        (lambda db: db.save_processing_assets(SLUG, 'a1b2c3d4e5f6', {'transcript_vtt': None}),
         'has_transcript_vtt', 0),
        (lambda db: db.save_applied_cuts(SLUG, 'a1b2c3d4e5f6', [{'start': 1.0, 'end': 2.0}]),
         'applied_cuts_json', '[{"start": 1.0, "end": 2.0}]'),
        (lambda db: db.save_chapters_and_applied_cuts(
            SLUG, 'a1b2c3d4e5f6', '{"c": 3}', [{'start': 3.0, 'end': 4.0}]),
         'chapters_json', '{"c": 3}'),
        (lambda db: db.clear_episode_details(SLUG, 'a1b2c3d4e5f6'), 'chapters_json', None),
        (lambda db: db.batch_clear_episode_details(SLUG, ['a1b2c3d4e5f6']), 'applied_cuts_json', None),
        (lambda db: db.clear_episode_ad_data(SLUG, 'a1b2c3d4e5f6'), 'has_transcript_vtt', 0),
        (lambda db: db.batch_clear_episode_ad_data(SLUG, ['a1b2c3d4e5f6']), 'chapters_json', None),
    ])
    def test_write_path_invalidates_and_next_read_is_new(self, seeded, mutate, field, expected):
        _fill(seeded)
        mutate(seeded)
        assert _pk(seeded, 'a1b2c3d4e5f6') not in _cache_ids(seeded)
        assert _recents(seeded)['a1b2c3d4e5f6'][field] == expected

    def test_save_episode_details_update_vtt(self, seeded):
        _fill(seeded)
        seeded.save_episode_details(SLUG, 'b1b2c3d4e5f6', transcript_vtt='WEBVTT\n\nx')
        assert _pk(seeded, 'b1b2c3d4e5f6') not in _cache_ids(seeded)
        assert _recents(seeded)['b1b2c3d4e5f6']['has_transcript_vtt']

    def test_save_episode_details_insert(self, seeded):
        _recents(seeded)
        seeded.get_connection().execute(
            'INSERT INTO episode_feed_details (episode_id) VALUES (?)',
            (_pk(seeded, 'c1b2c3d4e5f6'),))
        seeded.get_connection().commit()
        seeded.save_episode_details(SLUG, 'c1b2c3d4e5f6', chapters_json=CHAPTERS)
        assert _pk(seeded, 'c1b2c3d4e5f6') not in _cache_ids(seeded)
        assert _recents(seeded)['c1b2c3d4e5f6']['chapters_json'] == CHAPTERS

    def test_raw_insert_or_replace(self, seeded):
        pk = _pk(seeded, 'a1b2c3d4e5f6')
        _fill(seeded)
        conn = seeded.get_connection()
        conn.execute(
            'INSERT OR REPLACE INTO episode_details (episode_id, chapters_json) VALUES (?, ?)',
            (pk, 'replaced'))
        conn.commit()
        assert pk not in _cache_ids(seeded)
        assert _recents(seeded)['a1b2c3d4e5f6']['chapters_json'] == 'replaced'

    def test_unrelated_column_keeps_cache(self, seeded):
        _fill(seeded)
        seeded.save_episode_details(SLUG, 'a1b2c3d4e5f6', transcript_text='more text')
        assert _pk(seeded, 'a1b2c3d4e5f6') in _cache_ids(seeded)


class TestDeletion:
    def test_details_row_delete(self, seeded):
        _fill(seeded)
        seeded.get_connection().execute(
            'DELETE FROM episode_details WHERE episode_id = ?', (_pk(seeded, 'a1b2c3d4e5f6'),))
        seeded.get_connection().commit()
        assert _pk(seeded, 'a1b2c3d4e5f6') not in _cache_ids(seeded)

    def test_episode_delete(self, seeded):
        _fill(seeded)
        pk = _pk(seeded, 'a1b2c3d4e5f6')
        seeded.get_connection().execute('DELETE FROM episodes WHERE id = ?', (pk,))
        seeded.get_connection().commit()
        assert pk not in _cache_ids(seeded)

    def test_podcast_delete(self, seeded):
        _fill(seeded)
        assert _cache_ids(seeded)
        seeded.delete_podcast(SLUG)
        assert _cache_ids(seeded) == set()


class TestPerFeedReads:
    def _snapshot(self, db):
        pid = _pid(db)
        return db.get_feed_render_details(pid)[:2]

    def test_same_results_before_and_after_fill_and_filters(self, seeded):
        _add_episode(seeded, 'd1b2c3d4e5f6', 4, status='discovered')
        seeded.save_episode_details(SLUG, 'd1b2c3d4e5f6', chapters_json=CHAPTERS)
        seeded.save_applied_cuts(SLUG, 'd1b2c3d4e5f6', CUTS)
        before = self._snapshot(seeded)
        assert before[0] == {'a1b2c3d4e5f6': CHAPTERS}
        assert before[1] == {'a1b2c3d4e5f6': CUTS_RAW}
        assert _pk(seeded, 'a1b2c3d4e5f6') in _cache_ids(seeded)
        assert self._snapshot(seeded) == before

    def test_reads_use_cache(self, seeded):
        self._snapshot(seeded)
        pid = _pid(seeded)
        conn = seeded.get_connection()
        conn.execute("UPDATE episode_feed_details SET chapters_json = 'sentinel', "
                     "applied_cuts_json = 'sentinel2' WHERE episode_id = ?",
                     (_pk(seeded, 'a1b2c3d4e5f6'),))
        conn.commit()
        chapters, cuts, _ = seeded.get_feed_render_details(pid)
        assert chapters == {'a1b2c3d4e5f6': 'sentinel'}
        assert cuts == {'a1b2c3d4e5f6': 'sentinel2'}

    def test_cached_null_is_skipped(self, seeded):
        self._snapshot(seeded)
        pid = _pid(seeded)
        seeded.save_episode_details(SLUG, 'b1b2c3d4e5f6', chapters_json=CHAPTERS)
        _recents(seeded)
        seeded.clear_episode_ad_data(SLUG, 'b1b2c3d4e5f6')
        chapters, cuts, _ = seeded.get_feed_render_details(pid)
        assert 'b1b2c3d4e5f6' not in chapters
        assert 'b1b2c3d4e5f6' not in cuts

    def test_invalidated_value_is_visible(self, seeded):
        self._snapshot(seeded)
        pid = _pid(seeded)
        seeded.save_applied_cuts(SLUG, 'a1b2c3d4e5f6', [{'start': 1.0, 'end': 2.0}])
        assert json.loads(seeded.get_feed_render_details(pid)[1]['a1b2c3d4e5f6']) == [
            {'start': 1.0, 'end': 2.0}]


class TestMigration:
    def _objects(self, db):
        rows = db.get_connection().execute(
            "SELECT name FROM sqlite_master WHERE name LIKE '%feed_details%'").fetchall()
        return {r[0] for r in rows}

    def test_fresh_database_has_table_and_triggers(self, temp_db):
        names = self._objects(temp_db)
        assert 'episode_feed_details' in names
        assert len(names) == 4

    def test_existing_database_gets_them_back(self, seeded, temp_dir):
        conn = seeded.get_connection()
        for name in [n for n in self._objects(seeded) if n != 'episode_feed_details']:
            conn.execute(f'DROP TRIGGER {name}')
        conn.execute('DROP TABLE episode_feed_details')
        conn.commit()
        assert self._objects(seeded) == set()
        reopened = object.__new__(Database)
        reopened._initialized = False
        Database.__init__(reopened, data_dir=temp_dir)
        assert len(self._objects(reopened)) == 4
        first = _recents(reopened)
        assert first == _recents(reopened)
        assert first['a1b2c3d4e5f6']['chapters_json'] == CHAPTERS
        reopened.save_applied_cuts(SLUG, 'a1b2c3d4e5f6', [])
        assert _pk(reopened, 'a1b2c3d4e5f6') not in _cache_ids(reopened)


class TestRecentJsonColumn:
    def _seed(self, db):
        db.create_podcast(SLUG, 'https://example.com/feed.xml', 'Example Podcast')
        for i in range(5):
            _add_episode(db, f'e{i}b2c3d4e5f6', i + 1)
        for i in (0, 1, 3, 4):
            db.save_episode_audio_analysis(SLUG, f'e{i}b2c3d4e5f6', json.dumps({'n': i}))
        _add_episode(db, 'f0b2c3d4e5f6', 9, duration=30.0)
        db.save_episode_audio_analysis(SLUG, 'f0b2c3d4e5f6', json.dumps({'n': 'short'}))

    def test_order_limit_null_skip_exclusion_and_min_duration(self, temp_db):
        self._seed(temp_db)
        rows = temp_db.get_recent_audio_analyses(SLUG, limit=10)
        assert [r['episode_id'] for r in rows] == [
            'e4b2c3d4e5f6', 'e3b2c3d4e5f6', 'e1b2c3d4e5f6', 'e0b2c3d4e5f6']
        assert rows[0]['original_duration'] == 1800.0
        assert json.loads(rows[0]['audio_analysis_json']) == {'n': 4}
        limited = temp_db.get_recent_audio_analyses(SLUG, limit=2)
        assert [r['episode_id'] for r in limited] == ['e4b2c3d4e5f6', 'e3b2c3d4e5f6']
        excluded = temp_db.get_recent_audio_analyses(
            SLUG, exclude_episode_id='e4b2c3d4e5f6', limit=2)
        assert [r['episode_id'] for r in excluded] == ['e3b2c3d4e5f6', 'e1b2c3d4e5f6']
        short = temp_db.get_recent_audio_analyses(SLUG, limit=10, min_duration=10.0)
        assert short[0]['episode_id'] == 'f0b2c3d4e5f6'


class TestFillIsBestEffort:
    def _busy_ms(self, db):
        return db.get_connection().execute('PRAGMA busy_timeout').fetchone()[0]

    def test_open_caller_transaction_is_kept_and_fill_deferred(self, seeded):
        conn = seeded.get_connection()
        pid = _pid(seeded)
        conn.execute("UPDATE episodes SET title = 'Changed' WHERE episode_id = 'c1b2c3d4e5f6'")
        assert conn.in_transaction
        assert seeded.get_feed_render_details(pid)[0] == {'a1b2c3d4e5f6': CHAPTERS}
        assert _recents(seeded)['a1b2c3d4e5f6']['chapters_json'] == CHAPTERS
        assert conn.in_transaction
        assert _cache_ids(seeded) == set()
        conn.commit()
        assert seeded.get_episode(SLUG, 'c1b2c3d4e5f6')['title'] == 'Changed'
        _recents(seeded)
        assert _pk(seeded, 'a1b2c3d4e5f6') in _cache_ids(seeded)

    def test_busy_timeout_restored_after_fill(self, seeded):
        before = self._busy_ms(seeded)
        assert before == NORMAL_BUSY_MS
        _fill(seeded)
        assert self._busy_ms(seeded) == NORMAL_BUSY_MS

    def test_locked_database_skips_fill_quickly(self, seeded):
        pid = _pid(seeded)
        other = sqlite3.connect(seeded.db_path, timeout=1)
        other.isolation_level = None
        other.execute('BEGIN IMMEDIATE')
        try:
            start = time.monotonic()
            assert seeded.get_feed_render_details(pid)[0] == {'a1b2c3d4e5f6': CHAPTERS}
            assert _recents(seeded)['a1b2c3d4e5f6']['chapters_json'] == CHAPTERS
            assert time.monotonic() - start < 10
            assert self._busy_ms(seeded) == NORMAL_BUSY_MS
        finally:
            other.execute('COMMIT')
            other.close()
        assert _cache_ids(seeded) == set()
        _recents(seeded)
        assert _pk(seeded, 'a1b2c3d4e5f6') in _cache_ids(seeded)


class TestTransactionBusyTimeout:
    def test_failed_begin_is_quick_and_restores_timeout(self, seeded):
        other = sqlite3.connect(seeded.db_path, timeout=1)
        other.isolation_level = None
        other.execute('BEGIN IMMEDIATE')
        try:
            start = time.monotonic()
            with pytest.raises(sqlite3.OperationalError):
                with seeded.transaction(immediate=True, busy_timeout_ms=200):
                    pass
            assert time.monotonic() - start < 5
        finally:
            other.execute('COMMIT')
            other.close()
        busy = seeded.get_connection().execute('PRAGMA busy_timeout').fetchone()[0]
        assert busy == NORMAL_BUSY_MS


class TestBusyTimeoutArgs:
    def test_busy_timeout_without_immediate_is_rejected(self, seeded):
        with pytest.raises(ValueError, match='requires immediate'):
            seeded.transaction(busy_timeout_ms=200)


class TestPodcasting2Flags:
    def _flags(self, db):
        return db.get_feed_render_details(_pid(db))[2]

    def _seed_extra(self, db):
        _add_episode(db, 'd1b2c3d4e5f6', 4, status='discovered')
        db.save_episode_details(SLUG, 'd1b2c3d4e5f6', transcript_vtt='WEBVTT\n\nx')

    def test_contents_and_equal_before_and_after_fill(self, seeded):
        self._seed_extra(seeded)
        before = self._flags(seeded)
        assert before == {
            'a1b2c3d4e5f6': (True, True),
            'b1b2c3d4e5f6': (False, False),
            'c1b2c3d4e5f6': (False, False),
            'd1b2c3d4e5f6': (True, False),
        }
        assert _pk(seeded, 'd1b2c3d4e5f6') in _cache_ids(seeded)
        assert self._flags(seeded) == before

    def test_invalidated_by_vtt_and_chapters_writes(self, seeded):
        self._flags(seeded)
        seeded.save_episode_details(SLUG, 'b1b2c3d4e5f6', transcript_vtt='WEBVTT\n\ny')
        assert self._flags(seeded)['b1b2c3d4e5f6'] == (True, False)
        seeded.save_chapters_and_applied_cuts(SLUG, 'b1b2c3d4e5f6', CHAPTERS, CUTS)
        assert self._flags(seeded)['b1b2c3d4e5f6'] == (True, True)
