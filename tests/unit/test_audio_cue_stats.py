"""The audio-cue count flows into processing_history and the dashboard (#350)."""


def test_audio_cues_recorded_and_aggregated(temp_db, mock_podcast, mock_episode):
    slug = mock_podcast['slug']
    pid = mock_podcast['id']
    episode_id = mock_episode['episode_id']

    temp_db.record_processing_history(
        podcast_id=pid, podcast_slug=slug, podcast_title='Test Podcast',
        episode_id=episode_id, episode_title='Test Episode', status='completed',
        processing_duration_seconds=10.0, ads_detected=2, audio_cues_detected=3,
    )
    temp_db.record_processing_history(
        podcast_id=pid, podcast_slug=slug, podcast_title='Test Podcast',
        episode_id=episode_id, episode_title='Test Episode', status='completed',
        processing_duration_seconds=12.0, ads_detected=1, audio_cues_detected=5,
    )

    stats = temp_db.get_dashboard_stats()
    assert stats['totalAudioCuesDetected'] == 8
    assert stats['avgAudioCuesDetected'] == 4.0
    assert stats['minAudioCuesDetected'] == 3
    assert stats['maxAudioCuesDetected'] == 5


def test_audio_cues_default_zero_when_omitted(temp_db, mock_podcast, mock_episode):
    # Older call sites that do not pass audio_cues_detected record 0, not NULL.
    slug = mock_podcast['slug']
    temp_db.record_processing_history(
        podcast_id=mock_podcast['id'], podcast_slug=slug, podcast_title='Test Podcast',
        episode_id=mock_episode['episode_id'], episode_title='Test Episode',
        status='completed', processing_duration_seconds=10.0, ads_detected=2,
    )
    stats = temp_db.get_dashboard_stats()
    assert stats['totalAudioCuesDetected'] == 0
