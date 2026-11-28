"""audio_analysis_enabled=false skips the analysis stage in pass 1 and in verification."""
from unittest.mock import MagicMock

from tests.app_bootstrap import bootstrap

bootstrap('audio_analysis_switch_test_')

from ad_validator import AdValidator, Decision  # noqa: E402
from config import HOLD_REASON_NO_SPLICE  # noqa: E402
from main_app import processing  # noqa: E402
from main_app.pipeline import signals  # noqa: E402
from verification_pass import VerificationPass  # noqa: E402


def _pass1(monkeypatch, enabled, **kwargs):
    analyzer = MagicMock()
    monkeypatch.setattr(processing, 'audio_analyzer', analyzer)
    monkeypatch.setattr(processing.db, 'get_setting_bool', lambda key, default=False: enabled)
    monkeypatch.setattr(processing.db, 'get_podcast_row', lambda slug: {'id': 1})
    monkeypatch.setattr(processing.db, 'save_episode_audio_analysis', lambda *a: None)
    monkeypatch.setattr(processing.status_service, 'update_job_stage', lambda *a, **k: None)
    result = signals._run_audio_analysis('example-podcast', 'a1b2c3d4e5f6', '/x.mp3', **kwargs)
    return result, analyzer


def test_pass1_skips_the_analyzer_when_off(monkeypatch):
    result, analyzer = _pass1(monkeypatch, enabled=False)
    assert result is None
    analyzer.analyze.assert_not_called()


def test_pass1_runs_the_analyzer_when_on(monkeypatch):
    _result, analyzer = _pass1(monkeypatch, enabled=True)
    analyzer.analyze.assert_called_once()


def test_cue_only_feeds_still_analyse_when_off(monkeypatch):
    _result, analyzer = _pass1(monkeypatch, enabled=False, force_cue_detection=True)
    analyzer.analyze.assert_called_once()


def _verify(enabled):
    detector = MagicMock()
    detector.run_verification_detection.return_value = {'ads': []}
    analyzer = MagicMock()
    db = MagicMock()
    db.get_setting_bool.return_value = enabled
    verifier = VerificationPass(ad_detector=detector, transcriber=MagicMock(),
                                audio_analyzer=analyzer, db=db)
    verifier.verify(processed_audio_path='/x.mp3', podcast_name='Show', episode_title='Ep',
                    slug='example-podcast', episode_id='a1b2c3d4e5f6',
                    original_segments=[{'start': 0.0, 'end': 30.0, 'text': 'intro'}])
    return detector, analyzer


def test_verification_skips_the_analyzer_and_detects_without_signals_when_off():
    detector, analyzer = _verify(enabled=False)
    analyzer.analyze.assert_not_called()
    assert detector.run_verification_detection.call_args.kwargs['audio_analysis'] is None


def test_verification_runs_the_analyzer_when_on():
    _detector, analyzer = _verify(enabled=True)
    analyzer.analyze.assert_called_once()


def test_validator_accepts_a_long_cut_without_analysis_and_never_splice_holds():
    words = ('alpha bravo charlie delta echo foxtrot golf hotel india juliet kilo lima mike '
             'november oscar papa quebec romeo sierra tango uniform victor whiskey').split()
    segments = [{'start': float(i), 'end': float(i + 1),
                 'text': f'{words[i % 23]} {words[(i * 5 + 3) % 23]} {words[(i * 11 + 7) % 23]} {i}'}
                for i in range(1200)]
    for seg in segments[100:220]:
        seg['text'] = (f"brought to you by Acme, use promo code {words[int(seg['start']) % 23]}"
                       f"{int(seg['start'])} at acme.com for {words[int(seg['start']) * 3 % 23]} savings")
    validator = AdValidator(1200.0, segments, episode_description=None, min_cut_confidence=0.7)
    ad = {'start': 100.0, 'end': 220.0, 'confidence': 0.95, 'detection_stage': 'claude',
          'reason': 'Acme sponsor read'}
    result = validator.validate([ad], signals._make_validator_audio_analysis(None, None))
    assert result.ads[0]['validation']['decision'] == Decision.ACCEPT.value
    assert result.ads[0].get('hold_reason') != HOLD_REASON_NO_SPLICE
