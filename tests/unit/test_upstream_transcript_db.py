"""Store upstream transcripts and replace URL/type metadata when newer values arrive."""


class TestSchemaColumnsExist:
    def test_fresh_db_has_episode_columns(self, temp_db):
        cols = temp_db._get_table_columns(temp_db.get_connection(), 'episodes')
        assert 'upstream_transcript_url' in cols
        assert 'upstream_transcript_type' in cols

    def test_fresh_db_has_detail_column(self, temp_db):
        cols = temp_db._get_table_columns(temp_db.get_connection(), 'episode_details')
        assert 'upstream_transcript_json' in cols

    def test_fresh_db_has_podcast_column(self, temp_db):
        cols = temp_db._get_table_columns(temp_db.get_connection(), 'podcasts')
        assert 'transcript_differential' in cols

    def test_migrated_db_gains_episode_columns_matching_fresh_shape(self, temp_db):
        conn = temp_db.get_connection()
        fresh_cols = set(temp_db._get_table_columns(conn, 'episodes'))
        conn.execute("ALTER TABLE episodes DROP COLUMN upstream_transcript_url")
        conn.execute("ALTER TABLE episodes DROP COLUMN upstream_transcript_type")
        conn.commit()
        temp_db._run_schema_migrations()
        cols = set(temp_db._get_table_columns(conn, 'episodes'))
        assert 'upstream_transcript_url' in cols
        assert 'upstream_transcript_type' in cols
        assert cols == fresh_cols

    def test_migrated_db_gains_detail_column_matching_fresh_shape(self, temp_db):
        conn = temp_db.get_connection()
        fresh_cols = set(temp_db._get_table_columns(conn, 'episode_details'))
        conn.execute("ALTER TABLE episode_details DROP COLUMN upstream_transcript_json")
        conn.commit()
        temp_db._run_schema_migrations()
        cols = set(temp_db._get_table_columns(conn, 'episode_details'))
        assert 'upstream_transcript_json' in cols
        assert cols == fresh_cols

    def test_migrated_db_gains_podcast_column_matching_fresh_shape(self, temp_db):
        conn = temp_db.get_connection()
        fresh_cols = set(temp_db._get_table_columns(conn, 'podcasts'))
        conn.execute("ALTER TABLE podcasts DROP COLUMN transcript_differential")
        conn.commit()
        temp_db._run_schema_migrations()
        cols = set(temp_db._get_table_columns(conn, 'podcasts'))
        assert 'transcript_differential' in cols
        assert cols == fresh_cols


class TestDiscoveryUpsert:
    def test_upsert_stores_url_and_type(self, temp_db, mock_podcast):
        slug = mock_podcast['slug']
        temp_db.bulk_upsert_discovered_episodes(slug, [{
            'id': 'ep-1',
            'url': 'https://example.com/ep1.mp3',
            'title': 'Ep 1',
            'upstream_transcript_url': 'https://upstream.example.com/ep1.vtt',
            'upstream_transcript_type': 'text/vtt',
        }])
        episode = temp_db.get_episode(slug, 'ep-1')
        assert episode['upstream_transcript_url'] == 'https://upstream.example.com/ep1.vtt'
        assert episode['upstream_transcript_type'] == 'text/vtt'

    def test_coalesce_keeps_existing_when_new_is_absent(self, temp_db, mock_podcast):
        slug = mock_podcast['slug']
        temp_db.bulk_upsert_discovered_episodes(slug, [{
            'id': 'ep-1', 'url': 'https://example.com/ep1.mp3', 'title': 'Ep 1',
            'upstream_transcript_url': 'https://upstream.example.com/ep1.vtt',
            'upstream_transcript_type': 'text/vtt',
        }])
        temp_db.bulk_upsert_discovered_episodes(slug, [{
            'id': 'ep-1', 'url': 'https://example.com/ep1.mp3', 'title': 'Ep 1',
            'upstream_transcript_url': None,
            'upstream_transcript_type': None,
        }])
        episode = temp_db.get_episode(slug, 'ep-1')
        assert episode['upstream_transcript_url'] == 'https://upstream.example.com/ep1.vtt'
        assert episode['upstream_transcript_type'] == 'text/vtt'

    def test_coalesce_fills_in_a_previously_null_value(self, temp_db, mock_podcast):
        slug = mock_podcast['slug']
        temp_db.bulk_upsert_discovered_episodes(slug, [{
            'id': 'ep-1', 'url': 'https://example.com/ep1.mp3', 'title': 'Ep 1',
            'upstream_transcript_url': None,
            'upstream_transcript_type': None,
        }])
        temp_db.bulk_upsert_discovered_episodes(slug, [{
            'id': 'ep-1', 'url': 'https://example.com/ep1.mp3', 'title': 'Ep 1',
            'upstream_transcript_url': 'https://upstream.example.com/ep1.vtt',
            'upstream_transcript_type': 'text/vtt',
        }])
        episode = temp_db.get_episode(slug, 'ep-1')
        assert episode['upstream_transcript_url'] == 'https://upstream.example.com/ep1.vtt'
        assert episode['upstream_transcript_type'] == 'text/vtt'

    def test_a_newer_non_null_value_replaces_the_stored_one(self, temp_db, mock_podcast):
        """A refresh with a different non-null URL/type replaces the stored value."""
        slug = mock_podcast['slug']
        temp_db.bulk_upsert_discovered_episodes(slug, [{
            'id': 'ep-1', 'url': 'https://example.com/ep1.mp3', 'title': 'Ep 1',
            'upstream_transcript_url': 'https://upstream.example.com/ep1.vtt',
            'upstream_transcript_type': 'text/vtt',
        }])
        temp_db.bulk_upsert_discovered_episodes(slug, [{
            'id': 'ep-1', 'url': 'https://example.com/ep1.mp3', 'title': 'Ep 1',
            'upstream_transcript_url': 'https://new-host.example.com/ep1-v2.srt',
            'upstream_transcript_type': 'application/srt',
        }])
        episode = temp_db.get_episode(slug, 'ep-1')
        assert episode['upstream_transcript_url'] == 'https://new-host.example.com/ep1-v2.srt'
        assert episode['upstream_transcript_type'] == 'application/srt'

    def test_new_url_with_null_type_clears_the_stale_type(self, temp_db, mock_podcast):
        slug = mock_podcast['slug']
        temp_db.bulk_upsert_discovered_episodes(slug, [{
            'id': 'ep-1', 'url': 'https://example.com/ep1.mp3', 'title': 'Ep 1',
            'upstream_transcript_url': 'https://upstream.example.com/ep1.vtt',
            'upstream_transcript_type': 'text/vtt',
        }])
        temp_db.bulk_upsert_discovered_episodes(slug, [{
            'id': 'ep-1', 'url': 'https://example.com/ep1.mp3', 'title': 'Ep 1',
            'upstream_transcript_url': 'https://upstream.example.com/ep1.txt',
            'upstream_transcript_type': None,
        }])
        episode = temp_db.get_episode(slug, 'ep-1')
        assert episode['upstream_transcript_url'] == 'https://upstream.example.com/ep1.txt'
        assert episode['upstream_transcript_type'] is None


class TestDetailJsonStorage:
    def test_save_and_get_round_trip(self, temp_db, mock_episode, mock_podcast):
        slug = mock_podcast['slug']
        episode_id = mock_episode['episode_id']
        payload = {
            'status': 'ok', 'source_url': 'https://upstream.example.com/ep1.vtt',
            'mime': 'text/vtt', 'timed': True, 'coverage': 0.92,
            'spans': [{'start': 10.0, 'end': 55.0, 'words': 120,
                       'offset_confirmed': True, 'text_preview': 'a b c'}],
            'fetched_at': '2026-10-05T00:00:00Z', 'error': None,
        }
        temp_db.save_episode_upstream_transcript(slug, episode_id, payload)
        assert temp_db.get_episode_upstream_transcript(slug, episode_id) == payload

    def test_get_returns_none_when_unset(self, temp_db, mock_episode, mock_podcast):
        slug = mock_podcast['slug']
        episode_id = mock_episode['episode_id']
        assert temp_db.get_episode_upstream_transcript(slug, episode_id) is None

    def test_save_overwrites_previous_value(self, temp_db, mock_episode, mock_podcast):
        slug = mock_podcast['slug']
        episode_id = mock_episode['episode_id']
        temp_db.save_episode_upstream_transcript(slug, episode_id, {'status': 'error'})
        temp_db.save_episode_upstream_transcript(slug, episode_id, {'status': 'ok'})
        assert temp_db.get_episode_upstream_transcript(slug, episode_id) == {'status': 'ok'}
