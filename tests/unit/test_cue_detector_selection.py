"""Reconciliation tests for AudioAnalyzer._load_cue_config (#350).

Verifies the gating decision: the master ``audio_cue_detection_enabled`` toggle
controls whether any cue detector runs, per-feed templates take precedence when
present, and the spectral detector is the fallback otherwise.
"""
import numpy as np

from audio_analysis.audio_analyzer import AudioAnalyzer
from audio_analysis.cue_detector import AudioCueDetector
from audio_analysis.cue_template_matcher import AudioCueTemplateMatcher
from audio_analysis.cue_features import N_COEFFS, serialize_mfcc, pcm_to_int16_bytes


def _add_template(db, podcast_id):
    rng = np.random.default_rng(0)
    mfcc = rng.standard_normal((10, N_COEFFS)).astype(np.float32)
    pcm = np.clip(rng.standard_normal(1600), -1, 1).astype(np.float32)
    return db.create_cue_template(
        podcast_id=podcast_id, label='ding', source_episode_id='ep-1',
        source_offset_s=1.0, duration_s=0.6, sample_rate=16000,
        n_coeffs=N_COEFFS, mfcc_blob=serialize_mfcc(mfcc),
        pcm_blob=pcm_to_int16_bytes(pcm), pcm_sample_rate=16000,
    )


def test_toggle_off_runs_no_detector(temp_db):
    pid = temp_db.create_podcast('show-a', 'http://x/a.xml', 'Show A')
    _add_template(temp_db, pid)  # templates present but toggle is off
    analyzer = AudioAnalyzer(db=temp_db)
    enabled, detector = analyzer._load_cue_config(feed_id=pid)
    assert enabled is False
    assert detector is None


def test_toggle_on_no_templates_uses_spectral(temp_db):
    pid = temp_db.create_podcast('show-b', 'http://x/b.xml', 'Show B')
    temp_db.set_setting('audio_cue_detection_enabled', 'true')
    analyzer = AudioAnalyzer(db=temp_db)
    enabled, detector = analyzer._load_cue_config(feed_id=pid)
    assert enabled is True
    assert isinstance(detector, AudioCueDetector)


def test_toggle_on_with_templates_uses_matcher(temp_db):
    pid = temp_db.create_podcast('show-c', 'http://x/c.xml', 'Show C')
    temp_db.set_setting('audio_cue_detection_enabled', 'true')
    _add_template(temp_db, pid)
    analyzer = AudioAnalyzer(db=temp_db)
    enabled, detector = analyzer._load_cue_config(feed_id=pid)
    assert enabled is True
    assert isinstance(detector, AudioCueTemplateMatcher)


def test_no_feed_id_falls_back_to_spectral(temp_db):
    temp_db.set_setting('audio_cue_detection_enabled', 'true')
    analyzer = AudioAnalyzer(db=temp_db)
    enabled, detector = analyzer._load_cue_config(feed_id=None)
    assert enabled is True
    assert isinstance(detector, AudioCueDetector)
