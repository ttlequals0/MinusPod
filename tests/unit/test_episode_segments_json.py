"""Tests for original_segments_json and final_segments_json persistence."""


SEGMENTS_A = [
    {'start': 0.0, 'end': 5.5, 'text': 'Hello world.'},
    {'start': 5.5, 'end': 12.3, 'text': 'This is a test.'},
]

SEGMENTS_B = [
    {'start': 0.0, 'end': 4.0, 'text': 'Different segments.'},
]


class TestOriginalSegments:
    def test_round_trip(self, temp_db, mock_episode):
        slug = mock_episode['slug']
        ep_id = mock_episode['episode_id']

        temp_db.save_original_segments(slug, ep_id, SEGMENTS_A)
        result = temp_db.get_original_segments(slug, ep_id)

        assert result == SEGMENTS_A

    def test_get_returns_none_when_unset(self, temp_db, mock_episode):
        result = temp_db.get_original_segments(mock_episode['slug'], mock_episode['episode_id'])
        assert result is None

    def test_write_once_does_not_overwrite(self, temp_db, mock_episode):
        slug = mock_episode['slug']
        ep_id = mock_episode['episode_id']

        temp_db.save_original_segments(slug, ep_id, SEGMENTS_A)
        temp_db.save_original_segments(slug, ep_id, SEGMENTS_B)

        result = temp_db.get_original_segments(slug, ep_id)
        assert result == SEGMENTS_A

    def test_unknown_episode_no_op(self, temp_db, mock_podcast):
        temp_db.save_original_segments(mock_podcast['slug'], 'nonexistent-id', SEGMENTS_A)
        result = temp_db.get_original_segments(mock_podcast['slug'], 'nonexistent-id')
        assert result is None


class TestFinalSegments:
    def test_round_trip(self, temp_db, mock_episode):
        slug = mock_episode['slug']
        ep_id = mock_episode['episode_id']

        temp_db.save_final_segments(slug, ep_id, SEGMENTS_A)
        result = temp_db.get_final_segments(slug, ep_id)

        assert result == SEGMENTS_A

    def test_get_returns_none_when_unset(self, temp_db, mock_episode):
        result = temp_db.get_final_segments(mock_episode['slug'], mock_episode['episode_id'])
        assert result is None

    def test_overwrite_on_reprocess(self, temp_db, mock_episode):
        slug = mock_episode['slug']
        ep_id = mock_episode['episode_id']

        temp_db.save_final_segments(slug, ep_id, SEGMENTS_A)
        temp_db.save_final_segments(slug, ep_id, SEGMENTS_B)

        result = temp_db.get_final_segments(slug, ep_id)
        assert result == SEGMENTS_B

    def test_independent_of_original(self, temp_db, mock_episode):
        slug = mock_episode['slug']
        ep_id = mock_episode['episode_id']

        temp_db.save_original_segments(slug, ep_id, SEGMENTS_A)
        temp_db.save_final_segments(slug, ep_id, SEGMENTS_B)

        assert temp_db.get_original_segments(slug, ep_id) == SEGMENTS_A
        assert temp_db.get_final_segments(slug, ep_id) == SEGMENTS_B
