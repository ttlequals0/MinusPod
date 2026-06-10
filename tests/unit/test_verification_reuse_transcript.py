"""LLM-only pass 2 maps the saved transcript through cuts instead of
re-transcribing the processed audio (issue #349)."""
import os
import sys
from unittest.mock import MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))

from verification_pass import VerificationPass


def _seg(start, end, text):
    return {'start': start, 'end': end, 'text': text}


def _verifier():
    ad_detector = MagicMock()
    ad_detector.run_verification_detection.return_value = {'ads': []}
    transcriber = MagicMock()
    analyzer = MagicMock()
    analysis = MagicMock()
    analysis.signals = []
    analysis.get_signals_by_type.return_value = []
    analyzer.analyze.return_value = analysis
    return VerificationPass(ad_detector=ad_detector, transcriber=transcriber,
                            audio_analyzer=analyzer)


def _kwargs(**over):
    base = dict(
        processed_audio_path='/nonexistent.mp3',
        podcast_name='Show', episode_title='Ep',
        slug='show', episode_id='e1',
    )
    base.update(over)
    return base


def test_llm_only_maps_transcript_without_re_transcribing():
    v = _verifier()
    original = [
        _seg(0.0, 30.0, 'intro'),
        _seg(100.0, 160.0, 'sponsor read'),    # inside the cut -> dropped
        _seg(200.0, 230.0, 'back to the show'),
    ]
    cuts = [{'start': 95.0, 'end': 165.0}]      # removes 70s over the sponsor seg
    result = v.verify(**_kwargs(
        pass1_cuts=cuts, original_segments=original, reuse_transcript=True))

    # No Whisper pass.
    v.transcriber.transcribe_chunked.assert_not_called()
    # Detector ran on the mapped, post-cut transcript: the cut segment is gone
    # and a later segment shifts left by the 70s cut.
    passed = v.ad_detector.run_verification_detection.call_args[0][0]
    texts = [s['text'] for s in passed]
    assert 'sponsor read' not in texts
    assert any(s['text'] == 'back to the show' and abs(s['start'] - 130.0) < 0.1
               for s in passed)
    assert result['status'] == 'clean'


def test_re_transcribes_when_not_llm_only():
    v = _verifier()
    v.transcriber.transcribe_chunked.return_value = [_seg(0.0, 10.0, 'x')]
    v.verify(**_kwargs(
        pass1_cuts=[{'start': 95.0, 'end': 165.0}],
        original_segments=[_seg(0.0, 30.0, 'intro')],
        reuse_transcript=False))
    v.transcriber.transcribe_chunked.assert_called_once()


def test_no_cuts_reuses_transcript_directly_regardless_of_flag():
    v = _verifier()
    original = [_seg(0.0, 30.0, 'intro')]
    v.verify(**_kwargs(
        pass1_cuts=[], original_segments=original, reuse_transcript=False))
    v.transcriber.transcribe_chunked.assert_not_called()
    passed = v.ad_detector.run_verification_detection.call_args[0][0]
    assert [s['text'] for s in passed] == ['intro']
