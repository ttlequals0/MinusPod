"""Cross-pass render consolidation regressions."""
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from tests.app_bootstrap import bootstrap

bootstrap('crosspass_consolidation_test_')

from main_app import processing
from audio_processor import AudioProcessor


def _cut(start, end, action='remove', **extra):
    return {'start': start, 'end': end, 'action_applied': action, **extra}


def test_crosspass_plan_joins_touching_and_filler_gap_cuts():
    pass1 = [_cut(100.0, 150.0, replacement_duration=1.0)]
    markers = [_cut(100.0, 150.0)]
    pass2 = [_cut(150.0, 180.0), _cut(185.0, 220.0)]
    plan = processing._crosspass_cut_plan(
        pass1, markers, pass2,
        [{'start': 0.0, 'end': 90.0, 'text': 'content'}], [], 12.0)

    assert [(cut['start'], cut['end']) for cut in plan] == [(100.0, 220.0)]
    assert plan[0]['action_applied'] == 'remove'


def test_crosspass_plan_joins_overlapping_cuts():
    plan = processing._crosspass_cut_plan(
        [_cut(100.0, 160.0, replacement_duration=1.0)],
        [_cut(100.0, 160.0)], [_cut(150.0, 190.0)],
        [{'start': 0.0, 'end': 90.0, 'text': 'content'}], [], 12.0)

    assert [(cut['start'], cut['end']) for cut in plan] == [(100.0, 190.0)]


def test_crosspass_plan_does_not_cross_protected_or_mixed_action_audio():
    pass1 = [_cut(100.0, 150.0, replacement_duration=1.0)]
    markers = [_cut(100.0, 150.0)]
    pass2 = [_cut(150.0, 180.0)]

    assert processing._crosspass_cut_plan(
        pass1, markers, pass2, [{'start': 0.0, 'end': 90.0, 'text': 'content'}],
        [{'start': 145.0, 'end': 155.0}], 12.0) is None
    assert processing._crosspass_cut_plan(
        pass1, markers, [_cut(150.0, 180.0, 'beep')],
        [{'start': 0.0, 'end': 90.0, 'text': 'content'}], [], 12.0) is None


def test_crosspass_plan_falls_back_for_mixed_overlap_even_with_other_join():
    assert processing._crosspass_cut_plan(
        [_cut(100.0, 150.0, replacement_duration=1.0),
         _cut(300.0, 350.0, replacement_duration=1.0)],
        [_cut(100.0, 150.0), _cut(300.0, 350.0)],
        [_cut(120.0, 180.0, 'beep'), _cut(350.0, 390.0)],
        [{'start': 0.0, 'end': 90.0, 'text': 'content'}], [], 12.0) is None


def test_crosspass_plan_respects_speech_threshold_and_duration_cap():
    pass1 = [_cut(100.0, 150.0, replacement_duration=1.0)]
    markers = [_cut(100.0, 150.0)]
    pass2 = [_cut(160.0, 220.0)]

    assert processing._crosspass_cut_plan(pass1, markers, pass2, [], [], 12.0) is None
    assert processing._crosspass_cut_plan(
        pass1, markers, pass2, [{'start': 150.0, 'end': 160.0, 'text': 'speech'}],
        [], 0.0) is None
    assert processing._crosspass_cut_plan(
        pass1, markers, pass2, [{'start': 150.0, 'end': 160.0, 'text': 'speech'}],
        [], 10.0) is None
    assert processing._crosspass_cut_plan(
        [_cut(0.0, 200.0, replacement_duration=1.0)], [_cut(0.0, 200.0)],
        [_cut(201.0, 320.0)], [{'start': 200.0, 'end': 201.0, 'text': ''}], [], 12.0) is None


def test_touching_cuts_merge_without_transcript_or_enabled_gap_setting():
    pass1 = [_cut(100.0, 150.0, replacement_duration=1.0)]
    markers = [_cut(100.0, 150.0)]
    pass2 = [_cut(150.0, 180.0)]

    assert processing._crosspass_cut_plan(pass1, markers, pass2, [], [], 0.0)
    assert processing._crosspass_cut_plan(pass1, markers, pass2, [], [], 12.0)


def test_crosspass_union_keeps_trusted_pass1_short_cut(monkeypatch):
    plan = processing._crosspass_cut_plan(
        [_cut(105.0, 108.0, replacement_duration=1.0, beep=False)], [],
        [_cut(100.0, 105.0, confidence=0.1)], [], [], 0.0)
    processor = AudioProcessor()
    monkeypatch.setattr(processor, 'get_beep_duration', lambda: 1.0)

    applied = processor.compute_applied_cuts(
        [dict(cut, beep=(cut['action_applied'] == 'beep')) for cut in plan], 300.0)

    assert [(cut['start'], cut['end']) for cut in applied] == [(100.0, 108.0)]


def test_mapped_protection_blocks_processed_merge_and_tail_extension(monkeypatch):
    processor = AudioProcessor()
    monkeypatch.setattr(processor, 'get_beep_duration', lambda: 1.0)
    barrier = processing._protected_ranges_in_processed_audio(
        [{'start': 110.1, 'end': 110.4}], [])
    merged = processor.compute_applied_cuts(
        [_cut(100.0, 110.0), _cut(110.5, 121.0)], 300.0, barrier)
    tail_barrier = processing._protected_ranges_in_processed_audio(
        [{'start': 285.0, 'end': 290.0}], [])
    tail = processor.compute_applied_cuts([_cut(250.0, 280.0)], 300.0, tail_barrier)

    assert [(cut['start'], cut['end']) for cut in merged] == [(100.0, 110.0), (110.5, 121.0)]
    assert [(cut['start'], cut['end']) for cut in tail] == [(250.0, 280.0)]


def test_crosspass_plan_joins_when_pass2_precedes_pass1():
    plan = processing._crosspass_cut_plan(
        [_cut(150.0, 200.0, replacement_duration=1.0)], [_cut(150.0, 200.0)],
        [_cut(100.0, 150.0)], [{'start': 0.0, 'end': 90.0, 'text': 'content'}], [], 12.0)

    assert [(cut['start'], cut['end']) for cut in plan] == [(100.0, 200.0)]


def test_failed_crosspass_rerender_preserves_pass1_output(monkeypatch):
    audio = MagicMock()
    audio.process_episode.return_value = None
    unlink = MagicMock()
    monkeypatch.setattr(processing.os.path, 'exists', lambda path: True)
    monkeypatch.setattr(processing.os, 'unlink', unlink)
    cuts = [_cut(100.0, 200.0)]

    output, applied, ok = processing._rerender_crosspass_from_original(
        'test-feed', 'test-episode', '/tmp/original.mp3', '/tmp/pass1.mp3',
        cuts, audio, [])

    assert (output, applied, ok) == ('/tmp/pass1.mp3', None, False)
    assert cuts == [_cut(100.0, 200.0)]
    unlink.assert_not_called()


def test_verification_rerenders_original_and_replaces_cut_authority():
    ctx = SimpleNamespace(
        slug='test-feed', episode_id='test-episode', podcast_id=1,
        podcast_name='Test Podcast', episode_title='Test Episode',
        episode_description='', podcast_description='',
    )
    pass1_cuts = [_cut(100.0, 200.0, replacement_duration=1.0)]
    pass1_markers = [_cut(100.0, 200.0)]
    original = _cut(205.0, 250.0)
    processed = _cut(106.0, 151.0)
    final_cuts = [_cut(100.0, 250.0, replacement_duration=1.0)]
    audio = MagicMock()
    audio.get_audio_duration.side_effect = [400.0, 500.0]
    audio.process_episode.return_value = ('/tmp/crosspass-final.mp3', final_cuts)
    result = {
        'ads': [original], 'ads_processed': [processed],
        'segments': [{'start': 0.0, 'end': 90.0, 'text': 'show content'}],
    }
    fake_db = MagicMock()
    fake_db.get_setting_float.return_value = 0.8
    fake_db.get_false_positive_corrections.return_value = []
    fake_db.get_setting.return_value = 'false'

    with patch.object(processing, 'db', fake_db), \
         patch.object(processing, 'storage'), \
         patch('verification_pass.VerificationPass') as verifier_cls, \
         patch.object(processing, '_apply_pass2_heuristic_rolls'), \
         patch.object(processing, '_validate_verification_ads',
                      side_effect=lambda *args, **kwargs: (args[2], args[3])), \
         patch.object(processing, '_gate_verification_ads_by_confidence',
                      return_value=([processed], [original], [], 0)):
        verifier_cls.return_value.verify.return_value = result
        output = processing._run_verification_pass(
            ctx, '/tmp/pass1-output.mp3', pass1_cuts, False, 0.8,
            audio, None, original_segments=result['segments'],
            segment_actions={'sponsor': 'remove'},
            original_audio_path='/tmp/original-working.mp3',
            pass1_markers=pass1_markers,
        )

    assert audio.process_episode.call_args.args[0] == '/tmp/original-working.mp3'
    assert output[4] == '/tmp/crosspass-final.mp3'
    assert output[2] == []
    assert output[0] == 1
    assert pass1_cuts == final_cuts


def test_verification_marks_failed_crosspass_rerender_incomplete():
    ctx = SimpleNamespace(
        slug='test-feed', episode_id='test-episode', podcast_id=1,
        podcast_name='Test Podcast', episode_title='Test Episode',
        episode_description='', podcast_description='',
    )
    pass1_cuts = [_cut(100.0, 200.0, replacement_duration=1.0)]
    pass1_markers = [_cut(100.0, 200.0)]
    original, processed = _cut(205.0, 250.0), _cut(106.0, 151.0)
    audio = MagicMock()
    audio.get_audio_duration.side_effect = [400.0, 500.0]
    audio.process_episode.return_value = None
    result = {
        'ads': [original], 'ads_processed': [processed],
        'segments': [{'start': 0.0, 'end': 90.0, 'text': 'show content'}],
    }
    fake_db = MagicMock()
    fake_db.get_setting_float.return_value = 0.8
    fake_db.get_false_positive_corrections.return_value = []
    fake_db.get_setting.return_value = 'false'

    with patch.object(processing, 'db', fake_db), \
         patch.object(processing, 'storage'), \
         patch('verification_pass.VerificationPass') as verifier_cls, \
         patch.object(processing, '_apply_pass2_heuristic_rolls'), \
         patch.object(processing, '_validate_verification_ads',
                      side_effect=lambda *args, **kwargs: (args[2], args[3])), \
         patch.object(processing, '_gate_verification_ads_by_confidence',
                      return_value=([processed], [original], [], 0)):
        verifier_cls.return_value.verify.return_value = result
        output = processing._run_verification_pass(
            ctx, '/tmp/pass1-output.mp3', pass1_cuts, False, 0.8,
            audio, None, original_segments=result['segments'],
            segment_actions={'sponsor': 'remove'},
            original_audio_path='/tmp/original-working.mp3',
            pass1_markers=pass1_markers,
        )

    assert output[4] == '/tmp/pass1-output.mp3'
    assert output[6] is False
    assert output[1] == []
    assert pass1_cuts == [_cut(100.0, 200.0, replacement_duration=1.0)]
