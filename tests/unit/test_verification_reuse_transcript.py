"""LLM-only pass 2 maps the saved transcript through cuts instead of
re-transcribing the processed audio (issue #349)."""
import os
import sys
from copy import deepcopy
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


def test_llm_only_maps_transcript_without_re_transcribing(monkeypatch):
    # The processed audio contains a beep where each cut was, so the mapped
    # transcript must land on the beeped timeline or pass-2 coordinates
    # drift against the audio it re-examines.
    monkeypatch.setattr('verification_pass.get_replacement_duration', lambda: 2.0)
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
    # Detector ran on the mapped, post-cut transcript: the cut segment is
    # gone and a later segment shifts left by the 70s cut minus the 2s beep.
    passed = v.ad_detector.run_verification_detection.call_args[0][0]
    texts = [s['text'] for s in passed]
    assert 'sponsor read' not in texts
    assert any(s['text'] == 'back to the show' and abs(s['start'] - 132.0) < 0.1
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


def test_category_kept_detection_is_learned_as_a_miss():
    v = _verifier()
    v.pattern_service = MagicMock()
    v.ad_detector.run_verification_detection.return_value = {
        'ads': [
            {'start': 10.0, 'end': 30.0, 'category': 'self_promo',
             'sponsor': 'Own show'},
            {'start': 40.0, 'end': 70.0, 'category': 'sponsor',
             'sponsor': 'Acme'},
        ],
        'segment_actions': {
            'self_promo': 'keep',
            'sponsor': 'remove',
        },
    }
    original = [_seg(0.0, 90.0, 'intro own show acme sponsor')]

    v.verify(**_kwargs(pass1_cuts=[], original_segments=original))

    learned = v.pattern_service.record_verification_misses.call_args.args[2]
    assert [ad['category'] for ad in learned] == ['self_promo', 'sponsor']


def test_original_mapping_covers_nested_timestamp_metadata(monkeypatch):
    monkeypatch.setattr('verification_pass.get_replacement_duration', lambda: 2.0)
    v = _verifier()
    v.transcriber.transcribe_chunked.return_value = [_seg(0.0, 90.0, 'content')]
    processed = {
        'start': 5.0,
        'end': 42.0,
        'text_start': 25.0,
        'text_end': 42.0,
        'merged_protected_start': 5.0,
        'merged_protected_end': 25.0,
        'merged_member_spans': [
            {'start': 5.0, 'end': 25.0, 'stage': 'text_pattern'},
        ],
        'dai_core_spans': [{'start': 25.0, 'end': 42.0}],
        'tuple_span': (44.0, 52.0),
        'detection_metadata': '{"start": 44.0, "end": 52.0}',
    }
    before = deepcopy(processed)
    v.ad_detector.run_verification_detection.return_value = {'ads': [processed]}
    cuts = [
        {'start': 10.0, 'end': 20.0, 'replacement_duration': 2.0},
        {'start': 40.0, 'end': 60.0, 'replacement_duration': 5.0},
    ]

    result = v.verify(**_kwargs(pass1_cuts=cuts))

    mapped = result['ads'][0]
    assert mapped['start'] == 5.0
    assert mapped['end'] == 65.0
    assert (mapped['text_start'], mapped['text_end']) == (33.0, 65.0)
    assert (mapped['merged_protected_start'],
            mapped['merged_protected_end']) == (5.0, 33.0)
    assert mapped['merged_member_spans'] == [
        {'start': 5.0, 'end': 33.0, 'stage': 'text_pattern'},
    ]
    assert mapped['dai_core_spans'] == [{'start': 33.0, 'end': 65.0}]
    assert mapped['tuple_span'] == (44.0, 52.0)
    assert mapped['detection_metadata'] == '{"start": 44.0, "end": 52.0}'
    assert result['ads_processed'][0] == {**before, 'detection_stage': 'verification'}
    assert processed == {**before, 'detection_stage': 'verification'}
    assert mapped['merged_member_spans'] is not processed['merged_member_spans']


def test_no_cuts_deep_copies_nested_timestamp_metadata():
    v = _verifier()
    v.transcriber.transcribe_chunked.return_value = [_seg(0.0, 30.0, 'content')]
    processed = {
        'start': 5.0,
        'end': 15.0,
        'merged_member_spans': [
            {'start': 6.0, 'end': 14.0, 'stage': 'verification'},
        ],
    }
    v.ad_detector.run_verification_detection.return_value = {'ads': [processed]}

    result = v.verify(**_kwargs(pass1_cuts=[]))

    mapped = result['ads'][0]
    processed_copy = result['ads_processed'][0]
    assert mapped == processed_copy
    assert mapped is not processed_copy
    assert mapped['merged_member_spans'] is not processed_copy['merged_member_spans']
    mapped['merged_member_spans'][0]['start'] = 1.0
    assert processed_copy['merged_member_spans'][0]['start'] == 6.0
