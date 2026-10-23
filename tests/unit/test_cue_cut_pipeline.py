"""cue_match marker handling in the cut, telemetry and eligibility layers (#874)."""
from ad_detector.cue_telemetry import build_cue_detection_records
from audio_analysis.base import AudioAnalysisResult, AudioSegmentSignal
from audio_processor import AudioProcessor
from config import cue_only_missing_roles, is_cue_backed
from utils.markers import MEASURED_EVIDENCE_STAGES


def _marker(start, end, stage='cue_match', conf=0.8):
    return {'start': start, 'end': end, 'confidence': conf, 'detection_stage': stage}


def test_short_cue_match_cut_is_kept_but_short_llm_cut_dropped():
    cuts, _ = AudioProcessor()._applied_cuts_with_locks(
        [_marker(10.0, 14.0), _marker(100.0, 104.0, stage='claude')], 600.0)
    assert [(c['start'], c['end']) for c in cuts] == [(10.0, 14.0)]


def test_cue_match_is_cue_backed_and_measured():
    assert is_cue_backed(_marker(1.0, 3.0))
    assert 'cue_match' in MEASURED_EVIDENCE_STAGES


def test_cue_match_outcome_is_cut():
    sig = AudioSegmentSignal(
        start=10.0, end=14.0, signal_type='audio_cue', confidence=0.9,
        details={'source': 'template', 'template_id': 3, 'label': 'x',
                 'cue_type': 'show_intro', 'role': 'non_ad', 'score': 0.9})
    r = AudioAnalysisResult()
    r.signals = [sig]
    ad = {'start': 10.5, 'end': 14.0, 'cue_match': {'template_id': 3, 'cue_start': 10.0}}
    assert build_cue_detection_records([ad], r)[0]['outcome'] == 'cut'
    assert build_cue_detection_records([], r)[0]['outcome'] == 'none'


def _row(cue_type, cut=None, enabled=True):
    return {'cue_type': cue_type, 'enabled': enabled, 'cut_matches': cut}


def test_cue_only_missing_roles_cut_matches():
    def missing(rows, default=False):
        return cue_only_missing_roles(rows, cut_matches_default=default)
    assert missing([_row('show_intro', cut=True)]) == set()
    assert missing([_row('show_intro')]) == {'start', 'end'}
    assert missing([_row('show_intro')], True) == set()
    assert missing([_row('show_intro', cut=False)], True) == {'start', 'end'}
    assert missing([_row('content_transition', cut=True)]) == {'start', 'end'}
    assert missing([_row('show_intro', cut=True, enabled=False)]) == {'start', 'end'}


def test_reviewer_skips_cue_match_and_confirmed_only():
    from ad_reviewer import _skips_reviewer
    assert _skips_reviewer(_marker(1.0, 3.0))
    assert _skips_reviewer({'validation': {'user_confirmed': True}})
    assert not _skips_reviewer(_marker(1.0, 30.0, stage='claude'))
