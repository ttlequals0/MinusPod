"""Reconciliation tests for AudioAnalyzer._load_cue_config (#350).

Verifies the gating decision: the master ``audio_cue_detection_enabled`` toggle
controls whether any cue detector runs, per-feed templates take precedence when
present, and the spectral detector is the fallback otherwise.
"""
import logging
from unittest.mock import PropertyMock, patch

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
        podcast_id=podcast_id, cue_type='ad_break_boundary', source_episode_id='ep-1',
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


def test_force_bypasses_toggle_with_templates(temp_db):
    pid = temp_db.create_podcast('show-d', 'http://x/d.xml', 'Show D')
    _add_template(temp_db, pid)  # toggle stays off
    analyzer = AudioAnalyzer(db=temp_db)
    enabled, detector = analyzer._load_cue_config(feed_id=pid, force=True)
    assert enabled is True
    assert isinstance(detector, AudioCueTemplateMatcher)


def test_force_without_templates_does_not_enable_spectral(temp_db):
    pid = temp_db.create_podcast('show-e', 'http://x/e.xml', 'Show E')
    analyzer = AudioAnalyzer(db=temp_db)
    enabled, detector = analyzer._load_cue_config(feed_id=pid, force=True)
    assert enabled is False
    assert detector is None


def test_non_forced_matcher_construction_error_returns_disabled(temp_db):
    # Construction exceptions must not fall through to the spectral
    # fallback even though the global toggle is on (main parity).
    pid = temp_db.create_podcast('show-i', 'http://x/i.xml', 'Show I')
    _add_template(temp_db, pid)
    temp_db.set_setting('audio_cue_detection_enabled', 'true')
    analyzer = AudioAnalyzer(db=temp_db)
    with patch.object(AudioCueTemplateMatcher, '__init__',
                       side_effect=RuntimeError('boom')):
        enabled, detector = analyzer._load_cue_config(feed_id=pid)
    assert enabled is False
    assert detector is None


def test_force_with_unusable_matcher_logs_and_reports_error(temp_db, caplog):
    # Templates present but the matcher can't use them (e.g. every template's
    # mfcc blob failed to parse): a cue-only run would otherwise cut nothing
    # from templates with no signal that anything went wrong.
    pid = temp_db.create_podcast('show-f', 'http://x/f.xml', 'Show F')
    _add_template(temp_db, pid)
    analyzer = AudioAnalyzer(db=temp_db)
    errors = []
    with patch.object(AudioCueTemplateMatcher, 'is_usable',
                      new_callable=PropertyMock, return_value=False):
        with caplog.at_level(logging.ERROR, logger='podcast.audio_analysis'):
            enabled, detector = analyzer._load_cue_config(
                feed_id=pid, force=True, errors=errors)
    assert enabled is False
    assert detector is None
    assert any('no usable matcher' in r.message for r in caplog.records)
    assert any('no usable matcher' in e for e in errors)


def test_analyze_force_with_unusable_matcher_surfaces_error_on_result(temp_db):
    # End-to-end through analyze(): the force-path failure message must
    # reach AudioAnalysisResult.errors, not just the log (a local `errors`
    # list inside analyze() previously shadowed the one _load_cue_config
    # appended to, so result.errors never carried it).
    pid = temp_db.create_podcast('show-g', 'http://x/g.xml', 'Show G')
    _add_template(temp_db, pid)
    analyzer = AudioAnalyzer(db=temp_db)
    with patch('os.path.exists', return_value=True), \
         patch('audio_analysis.audio_analyzer.get_audio_duration', return_value=600.0), \
         patch.object(analyzer.volume_analyzer, 'analyze', return_value=([], None, [])), \
         patch.object(analyzer.splice_detector, 'detect', return_value=None), \
         patch.object(AudioCueTemplateMatcher, 'is_usable',
                      new_callable=PropertyMock, return_value=False):
        result = analyzer.analyze('/fake/ep.mp3', feed_id=pid, force_cue_detection=True)
    assert any('no usable matcher' in e for e in result.errors)
