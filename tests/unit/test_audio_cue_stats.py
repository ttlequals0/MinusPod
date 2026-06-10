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


def test_verification_surfaces_processed_audio_cue_count():
    """verify() reports cues found on the processed audio so the stat counts them (#350).

    The pass-1 counter in processing never sees the verification analysis, so the
    count has to come back through the result dict.
    """
    from types import SimpleNamespace
    from audio_analysis.base import AudioAnalysisResult
    from verification_pass import VerificationPass

    cue = SimpleNamespace(signal_type='audio_cue', start=10.0, end=10.5)
    vol = SimpleNamespace(signal_type='volume_increase', start=20.0, end=21.0)
    analysis = AudioAnalysisResult(signals=[cue, vol, cue])  # two cues, one non-cue

    class FakeAnalyzer:
        def analyze(self, _path):
            return analysis

    class FakeDetector:
        def run_verification_detection(self, *_a, **_k):
            return {'ads': []}  # clean -> no missed ads

    vp = VerificationPass(
        ad_detector=FakeDetector(), transcriber=None,
        audio_analyzer=FakeAnalyzer(), pattern_service=None, db=None,
    )
    # pass1_cuts=None + original_segments reuses the transcript (no transcription).
    result = vp.verify(
        processed_audio_path='/nonexistent.mp3', podcast_name='p',
        episode_title='t', slug='s', episode_id='e',
        pass1_cuts=None, original_segments=[{'start': 0.0, 'end': 30.0, 'text': 'hi'}],
    )
    assert result['status'] == 'clean'
    assert result['audio_cue_count'] == 2
