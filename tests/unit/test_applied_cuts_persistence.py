"""Persistence and migration tests for episode_details.applied_cuts_json.

The recut chapter remap loads this authoritative applied cut list instead of
reconstructing it from was_cut markers. None (never persisted) and [] (nothing
cut) are distinct: the recut treats None as a skip, [] as an authoritative
empty list.
"""
from database import Database


CUTS_A = [{'start': 100.0, 'end': 200.0}, {'start': 500.0, 'end': 560.0}]
CUTS_B = [{'start': 30.0, 'end': 95.0}]


class TestAppliedCutsRoundTrip:
    def test_round_trip(self, temp_db, mock_episode):
        slug = mock_episode['slug']
        ep_id = mock_episode['episode_id']

        temp_db.save_applied_cuts(slug, ep_id, CUTS_A)
        assert temp_db.get_applied_cuts(slug, ep_id) == CUTS_A

    def test_get_returns_none_when_unset(self, temp_db, mock_episode):
        # Never persisted -> None (the recut fallback signal), NOT [].
        assert temp_db.get_applied_cuts(
            mock_episode['slug'], mock_episode['episode_id']) is None

    def test_empty_list_is_persisted_distinct_from_none(self, temp_db, mock_episode):
        slug = mock_episode['slug']
        ep_id = mock_episode['episode_id']

        temp_db.save_applied_cuts(slug, ep_id, [])
        # An authoritative empty list round-trips as [], not None.
        assert temp_db.get_applied_cuts(slug, ep_id) == []

    def test_overwrite_on_recut(self, temp_db, mock_episode):
        slug = mock_episode['slug']
        ep_id = mock_episode['episode_id']

        temp_db.save_applied_cuts(slug, ep_id, CUTS_A)
        temp_db.save_applied_cuts(slug, ep_id, CUTS_B)
        assert temp_db.get_applied_cuts(slug, ep_id) == CUTS_B

    def test_only_start_end_persisted(self, temp_db, mock_episode):
        slug = mock_episode['slug']
        ep_id = mock_episode['episode_id']

        temp_db.save_applied_cuts(
            slug, ep_id,
            [{'start': 10.0, 'end': 20.0, 'confidence': 0.9, 'reason': 'ad'}])
        assert temp_db.get_applied_cuts(slug, ep_id) == [
            {'start': 10.0, 'end': 20.0}]

    def test_unknown_episode_no_op(self, temp_db, mock_podcast):
        temp_db.save_applied_cuts(mock_podcast['slug'], 'nonexistent', CUTS_A)
        assert temp_db.get_applied_cuts(mock_podcast['slug'], 'nonexistent') is None


class TestAppliedCutsMigration:
    def test_column_added_default_null_rows_intact_idempotent(self, temp_dir):
        # Faithful pre-deploy state: build the full current schema, seed an
        # episode with details, then DROP applied_cuts_json to simulate a DB
        # from before this column existed. Reopening must re-add the column
        # (NULL default), preserve the existing row, and be idempotent -- no
        # data loss (CLAUDE.md migration rule).
        Database._instance = None
        seed = Database(data_dir=temp_dir)
        seed.create_podcast('p', 'u', 'P')
        seed.upsert_episode('p', 'e1', original_url='http://x/e.mp3',
                            title='E1', status='processed')
        seed.save_episode_details('p', 'e1', transcript_text='legacy transcript',
                                  chapters_json='{"chapters": []}')
        conn = seed.get_connection()
        assert 'applied_cuts_json' in {
            r['name'] for r in conn.execute("PRAGMA table_info(episode_details)")}
        conn.execute("ALTER TABLE episode_details DROP COLUMN applied_cuts_json")
        conn.commit()
        assert 'applied_cuts_json' not in {
            r['name'] for r in conn.execute("PRAGMA table_info(episode_details)")}
        Database._instance = None

        # Reopen: _run_schema_migrations re-adds the missing column.
        db = Database(data_dir=temp_dir)
        try:
            vconn = db.get_connection()
            cols_after = {r['name'] for r in vconn.execute(
                "PRAGMA table_info(episode_details)")}
            assert 'applied_cuts_json' in cols_after

            row = vconn.execute(
                "SELECT transcript_text, chapters_json, applied_cuts_json "
                "FROM episode_details WHERE episode_id = ("
                "  SELECT id FROM episodes WHERE episode_id = 'e1')").fetchone()
            # Pre-existing data intact; new column defaults NULL (no data loss).
            assert row['transcript_text'] == 'legacy transcript'
            assert row['chapters_json'] == '{"chapters": []}'
            assert row['applied_cuts_json'] is None
            # The recut getter reads None for the un-backfilled row.
            assert db.get_applied_cuts('p', 'e1') is None
        finally:
            Database._instance = None

        # Idempotent: re-running migrations does not error or duplicate.
        Database._instance = None
        db2 = Database(data_dir=temp_dir)
        try:
            vconn2 = db2.get_connection()
            count = sum(1 for r in vconn2.execute(
                "PRAGMA table_info(episode_details)")
                if r['name'] == 'applied_cuts_json')
            assert count == 1
            # Round-trip still works post-migration (write then read).
            db2.save_applied_cuts('p', 'e1', CUTS_A)
            assert db2.get_applied_cuts('p', 'e1') == CUTS_A
        finally:
            Database._instance = None
