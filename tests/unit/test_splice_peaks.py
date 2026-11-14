import subprocess
from unittest.mock import patch

import numpy as np

from audio_analysis.audio_analyzer import AudioAnalyzer
from audio_analysis.base import AudioAnalysisResult
from audio_analysis.splice_detector import SpliceDetector
import audio_peaks as ap


def _gen_wav(path, seconds=3.0):
    subprocess.run(['ffmpeg', '-y', '-f', 'lavfi', '-i',
                    f'sine=frequency=440:duration={seconds}',
                    '-ac', '1', '-ar', '44100', str(path)], check=True,
                   capture_output=True)


def test_detect_with_peaks_payload_matches_detect(tmp_path):
    src = tmp_path / 'ep.wav'
    _gen_wav(src)
    det = SpliceDetector()
    payload_only = det.detect(str(src), 3.0, [])
    payload, peaks = det.detect_with_peaks(str(src), 3.0, [])
    assert payload == payload_only
    assert peaks is not None and peaks.dtype == np.dtype('<u2')
    assert len(peaks) == 300


def test_detect_with_peaks_maxima_match_direct_decode(tmp_path):
    src = tmp_path / 'ep.wav'
    _gen_wav(src)
    _, peaks = SpliceDetector().detect_with_peaks(str(src), 3.0, [])
    direct = ap.build_peaks_from_decode(src, 3.0)
    n = min(len(peaks), len(direct))
    assert n > 250
    assert np.array_equal(peaks[:n], direct[:n])


def test_detect_with_peaks_returns_peaks_for_short_audio(tmp_path):
    src = tmp_path / 'short.wav'
    _gen_wav(src, seconds=0.5)
    det = SpliceDetector()
    payload, peaks = det.detect_with_peaks(str(src), 0.5, [])
    assert payload == det.detect(str(src), 0.5, [])
    assert peaks is not None and len(peaks) == 50


def test_detect_with_peaks_none_on_missing_file():
    payload, peaks = SpliceDetector().detect_with_peaks('/no/such.wav', 1.0, [])
    assert peaks is None and payload['events'] == []


def test_detect_with_peaks_none_on_decode_failure(tmp_path):
    bad = tmp_path / 'bad.mp3'
    bad.write_bytes(b'not audio')
    payload, peaks = SpliceDetector().detect_with_peaks(str(bad), 1.0, [])
    assert peaks is None and payload['events'] == []


def test_peaks_10ms_not_serialized():
    r = AudioAnalysisResult()
    assert r.peaks_10ms is None
    r.peaks_10ms = np.array([1, 2, 3], dtype='<u2')
    assert 'peaks_10ms' not in r.to_dict()


class _SpliceDB:
    def get_setting_bool(self, key, default=None):
        return True if key == 'splice_evidence_enabled' else default

    def get_setting_float(self, key, default=None):
        return default

    def get_podcast_cue_settings_overrides(self, podcast_id):
        return {}


def _analyze_with(detect_result):
    analyzer = AudioAnalyzer(db=_SpliceDB())
    with patch.object(analyzer.volume_analyzer, 'analyze',
                      return_value=([], -20.0, [])), \
         patch.object(analyzer.splice_detector, 'detect_with_peaks',
                      **detect_result), \
         patch('audio_analysis.audio_analyzer.get_audio_duration',
               return_value=600.0), \
         patch('os.path.exists', return_value=True):
        return analyzer.analyze('/fake/episode.mp3')


def test_analyzer_carries_peaks_and_keeps_them_out_of_dict():
    payload = {'version': 1, 'events': [], 'calibration': {'status': 'cold_start'}}
    peaks = np.array([4, 5], dtype='<u2')
    result = _analyze_with({'return_value': (payload, peaks)})
    assert result.splice_evidence == payload
    assert result.peaks_10ms is peaks
    assert 'peaks_10ms' not in result.to_dict()


def test_analyzer_peaks_none_when_detector_raises():
    result = _analyze_with({'side_effect': RuntimeError('boom')})
    assert result.peaks_10ms is None and result.splice_evidence is None
    assert any('splice' in e for e in result.errors)


def test_peaks_maxima_include_full_scale_negative(tmp_path):
    import wave
    pcm = np.zeros(8000, dtype='<i2')
    pcm[100] = -32768
    src = tmp_path / 'neg.wav'
    with wave.open(str(src), 'wb') as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(8000)
        w.writeframes(pcm.tobytes())
    _, peaks = SpliceDetector().detect_with_peaks(str(src), 1.0, [])
    assert peaks[1] == 32768


def test_analyzer_handles_malformed_detector_result():
    result = _analyze_with({'return_value': ('only-one-value',)})
    assert result.peaks_10ms is None and result.splice_evidence is None
    assert any('splice' in e for e in result.errors)
