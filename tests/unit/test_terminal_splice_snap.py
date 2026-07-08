"""Terminal boundary snap to splice evidence (spec 2.3b).

Dillon-shaped fixture: the reviewer moved a terminal marker's start 11.1s
inside the DAI block. The snap recovers the extension via the deep-silence
event at the true onset, while a content sentence 3s further back blocks
the deeper-but-wrong candidate.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))

from ad_detector.boundaries import snap_terminal_ad_to_splice

_EOF = 4200.0
_WINDOW = 30.0


def _event(t, depth, etype='digital_silence', dur=1.4):
    return {'time': t, 'end_time': t + dur, 'type': etype,
            'depth_dbfs': depth, 'duration_s': dur, 'loudness_step_lu': None,
            'centroid_step_hz': None, 'flatness_step': None}


def _fixture():
    # Content sentence ends 4158.4s; ad read (promo code) 4161.0-4171.5s.
    segments = [
        {'start': 4155.0, 'end': 4158.4,
         'text': 'and that is the end of the story for this week'},
        {'start': 4161.0, 'end': 4171.5,
         'text': 'Vital Proteins collagen peptides, use promo code DILLON'},
    ]
    marker = {'start': 4172.0, 'end': _EOF, 'confidence': 0.9,
              'reason': 'Vital Proteins ad read',
              'detection_stage': 'text_pattern'}
    events = [
        _event(4157.9, -95.0),  # deeper, but behind the content sentence
        _event(4160.9, -88.0),  # the true DAI onset
    ]
    return segments, marker, events


def test_snap_recovers_extension_and_blocks_over_snap():
    segments, marker, events = _fixture()
    out = snap_terminal_ad_to_splice([marker], segments, events, _EOF, _WINDOW)
    assert len(out) == 1
    snapped = out[0]
    # Deeper 4157.9 candidate is blocked (content sentence in its span);
    # the 4160.9 event wins: 11.1s extension recovered.
    assert snapped['start'] == 4160.9
    assert snapped['terminal_snap']['original_start'] == 4172.0
    assert snapped['terminal_snap']['event_type'] == 'digital_silence'


def test_content_only_candidate_blocks_snap_entirely():
    segments, marker, events = _fixture()
    only_far = [events[0]]  # only the candidate behind the content sentence
    out = snap_terminal_ad_to_splice([marker], segments, only_far, _EOF, _WINDOW)
    assert out[0]['start'] == 4172.0
    assert 'terminal_snap' not in out[0]


def test_non_terminal_marker_untouched():
    segments, marker, events = _fixture()
    marker = dict(marker, end=_EOF - 10.0)  # 10s from EOF > 2.0s tolerance
    out = snap_terminal_ad_to_splice([marker], segments, events, _EOF, _WINDOW)
    assert out[0]['start'] == 4172.0
    assert 'terminal_snap' not in out[0]


def test_marker_coverage_allows_span_without_promo_text():
    # The extension span is plain speech but covered by another detected
    # marker (ad-classified): the snap is allowed.
    segments = [{'start': 4161.0, 'end': 4171.5,
                 'text': 'talking about collagen for your morning routine'}]
    marker = {'start': 4172.0, 'end': _EOF, 'confidence': 0.9,
              'reason': 'terminal block', 'detection_stage': 'text_pattern'}
    coverage = [marker,
                {'start': 4160.9, 'end': 4172.0, 'detection_stage': 'claude',
                 'confidence': 0.95, 'reason': 'first-pass ad'}]
    events = [_event(4160.9, -88.0)]
    out = snap_terminal_ad_to_splice([marker], segments, events, _EOF, _WINDOW,
                                     coverage_ads=coverage)
    assert out[0]['start'] == 4160.9


def test_only_silence_event_types_considered():
    segments, marker, _ = _fixture()
    step_only = [{'time': 4160.9, 'end_time': 4162.3, 'type': 'loudness_step',
                  'depth_dbfs': None, 'duration_s': 1.4,
                  'loudness_step_lu': 8.0, 'centroid_step_hz': None,
                  'flatness_step': None}]
    out = snap_terminal_ad_to_splice([marker], segments, step_only, _EOF, _WINDOW)
    assert out[0]['start'] == 4172.0
