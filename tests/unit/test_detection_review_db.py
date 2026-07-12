"""Tests for the cross-episode detection review DB methods."""
import json


def _seed_episode_with_markers(db, slug, episode_id, markers, title='Ep',
                               published_at=None):
    db.upsert_episode(slug, episode_id,
                      original_url=f'https://example.com/{episode_id}.mp3',
                      title=title, status='processed')
    db.save_episode_details(slug, episode_id, ad_markers=markers)
    if published_at:
        conn = db.get_connection()
        conn.execute(
            'UPDATE episodes SET published_at = ? WHERE episode_id = ?',
            (published_at, episode_id))
        conn.commit()


class TestGetDetectionRows:
    def test_returns_rows_with_feed_metadata(self, temp_db, mock_podcast):
        markers = [{'start': 10.0, 'end': 40.0, 'confidence': 0.9}]
        _seed_episode_with_markers(temp_db, mock_podcast['slug'], 'ep-1',
                                   markers, published_at='2026-07-01T00:00:00Z')
        rows = temp_db.get_detection_rows()
        assert len(rows) == 1
        row = rows[0]
        assert row['feed_slug'] == mock_podcast['slug']
        assert row['feed_title'] == mock_podcast['title']
        assert row['episode_id'] == 'ep-1'
        assert row['published_at'] == '2026-07-01T00:00:00Z'
        assert json.loads(row['ad_markers_json']) == markers

    def test_skips_episodes_without_markers(self, temp_db, mock_podcast):
        temp_db.upsert_episode(mock_podcast['slug'], 'ep-none',
                               original_url='https://example.com/x.mp3',
                               title='No markers', status='processed')
        assert temp_db.get_detection_rows() == []

    def test_skips_empty_marker_arrays(self, temp_db, mock_podcast):
        _seed_episode_with_markers(temp_db, mock_podcast['slug'], 'ep-empty', [])
        assert temp_db.get_detection_rows() == []


class TestGetReviewCorrections:
    def test_returns_resolving_correction_types_with_parsed_bounds(
            self, temp_db, mock_podcast):
        temp_db.create_pattern_correction(
            correction_type='confirm', pattern_id=None, episode_id='ep-1',
            original_bounds={'start': 10.0, 'end': 40.0})
        temp_db.create_pattern_correction(
            correction_type='false_positive', pattern_id=None, episode_id='ep-2',
            original_bounds={'start': 5.0, 'end': 25.0})
        temp_db.create_pattern_correction(
            correction_type='boundary_adjustment', pattern_id=None,
            episode_id='ep-3', original_bounds={'start': 1.0, 'end': 2.0})
        temp_db.create_pattern_correction(
            correction_type='create', pattern_id=None,
            episode_id='ep-4', original_bounds={'start': 3.0, 'end': 4.0})
        rows = temp_db.get_review_corrections()
        types = sorted(r['correction_type'] for r in rows)
        assert types == ['boundary_adjustment', 'confirm', 'false_positive']
        confirm = next(r for r in rows if r['correction_type'] == 'confirm')
        assert confirm == {'episode_id': 'ep-1', 'correction_type': 'confirm',
                           'start': 10.0, 'end': 40.0}

    def test_skips_rows_with_missing_bounds(self, temp_db):
        temp_db.create_pattern_correction(
            correction_type='confirm', pattern_id=None, episode_id='ep-x',
            original_bounds=None)
        assert temp_db.get_review_corrections() == []
