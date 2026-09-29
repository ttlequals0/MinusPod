"""Hard protection vs temporary holds, and one effective category action."""
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from tests.app_bootstrap import bootstrap

bootstrap('protection_contract_test_')

from main_app import processing
from ad_detector import AdDetector, AddressingStats
from ad_detector.boundaries import removal_coverage_regions
from ad_validator import AdValidator, ValidationResult

ACTIONS = {'self_promo': 'keep', 'sponsor': 'remove'}


def _spans(ranges):
    return [(round(r['start'], 3), round(r['end'], 3)) for r in ranges]


def test_hard_orig_and_hard_proc_cover_the_same_audio_under_shifted_cuts():
    pass1_cuts = [
        {'start': 100.0, 'end': 130.0, 'replacement_duration': 0.0},
        {'start': 500.0, 'end': 520.0, 'replacement_duration': 0.0},
    ]
    hold = {'start': 300.0, 'end': 310.0, 'held_for_review': True}

    protection = processing.build_protection(
        kept=[{'start': 200.0, 'end': 220.0}],
        category_kept=[{'start': 215.0, 'end': 240.0}],
        user_trims=[{'start': 120.0, 'end': 140.0}],
        fp_corrections=[{'start': 510.0, 'end': 530.0}],
        holds=[hold],
        pass1_cuts=pass1_cuts,
    )

    assert _spans(protection.hard_orig) == [
        (120.0, 140.0), (200.0, 240.0), (510.0, 530.0)]
    # Only the audio each range still has after pass 1, shifted by earlier cuts.
    assert _spans(protection.hard_proc) == [
        (100.0, 110.0), (170.0, 210.0), (470.0, 480.0)]
    assert protection.holds_orig == [hold]
    assert (300.0, 310.0) not in _spans(protection.hard_orig)
    assert (270.0, 280.0) not in _spans(protection.hard_proc)


def test_barriers_include_holds_except_the_owning_hold():
    owning = {'start': 300.0, 'end': 310.0}
    other = {'start': 400.0, 'end': 410.0}
    protection = processing.build_protection(
        kept=[{'start': 200.0, 'end': 220.0}], category_kept=[], user_trims=[],
        fp_corrections=[], holds=[owning, other],
        pass1_cuts=[{'start': 100.0, 'end': 130.0, 'replacement_duration': 0.0}])

    assert _spans(protection.barriers_orig()) == [
        (200.0, 220.0), (300.0, 310.0), (400.0, 410.0)]
    assert _spans(protection.barriers_orig(exclude=[owning])) == [
        (200.0, 220.0), (400.0, 410.0)]
    assert _spans(protection.barriers_proc(exclude=[owning])) == [
        (170.0, 190.0), (370.0, 380.0)]


def test_category_keeps_are_hard_barriers_not_holds_through_pass2():
    ctx = SimpleNamespace(
        slug='example-podcast', episode_id='a1b2c3d4e5f6', podcast_id=1,
        podcast_name='Example Podcast', episode_title='Episode',
        episode_description='', podcast_description='',
    )
    sponsor = {'start': 100.0, 'end': 150.0, 'confidence': 0.98,
               'category': 'sponsor'}
    promo = {'start': 300.0, 'end': 320.0, 'confidence': 0.98,
             'category': 'self_promo'}
    pass1_hold = {'start': 400.0, 'end': 420.0, 'held_for_review': True,
                  'hold_reason': 'max_duration'}
    audio = MagicMock()
    audio.get_audio_duration.return_value = 600.0
    audio.process_episode.return_value = ('/tmp/recut.mp3', [dict(sponsor)])
    fake_db = MagicMock()
    fake_db.get_setting_float.return_value = 0.8
    fake_db.get_false_positive_corrections.return_value = [
        {'start': 500.0, 'end': 510.0}]
    fake_db.get_setting.return_value = 'false'
    built = []
    real_build = processing.build_protection

    def spy_build(**kwargs):
        protection = real_build(**kwargs)
        built.append(protection)
        return protection

    reviewer = MagicMock()
    crosspass = MagicMock(return_value=None)
    with patch.object(processing, 'db', fake_db), \
         patch.object(processing, 'storage'), \
         patch('verification_pass.VerificationPass') as verifier_cls, \
         patch.object(processing, '_apply_pass2_heuristic_rolls'), \
         patch.object(processing, '_validate_verification_ads',
                      side_effect=lambda *args, **kwargs: (args[2], args[3])), \
         patch.object(processing, '_gate_verification_ads_by_confidence',
                      side_effect=lambda processed, original, *args, **kwargs:
                      (processed, original, [], 0, [])), \
         patch.object(processing, '_apply_pass2_reviewer', reviewer), \
         patch.object(processing, '_crosspass_cut_plan', crosspass), \
         patch.object(processing, 'build_protection', side_effect=spy_build):
        verifier_cls.return_value.verify.return_value = {
            'ads': [dict(sponsor), dict(promo)],
            'ads_processed': [dict(sponsor), dict(promo)],
            'segments': [{'start': 100.0, 'end': 150.0, 'text': 'Sponsor'}],
        }
        output = processing._run_verification_pass(
            ctx, '/tmp/pass1-output.mp3', [], False, 0.8,
            audio, None, original_segments=[],
            pass1_held_markers=[pass1_hold],
            segment_actions=ACTIONS,
        )

    # Hard sources are built once, after the category partition.
    [protection] = built
    assert (300.0, 320.0) in _spans(protection.hard_orig)
    assert (400.0, 420.0) not in _spans(protection.hard_orig)
    reviewer_barriers = reviewer.call_args.kwargs['protected_original_ranges']
    assert {(300.0, 320.0), (400.0, 420.0)} <= set(_spans(reviewer_barriers))
    # The prompt lists hard protection only, labelled; holds stay out.
    labelled = reviewer.call_args.kwargs['protected_spans']
    assert {'start': 300.0, 'end': 320.0, 'kind': 'keep',
            'category': 'self_promo'} in labelled
    assert {'start': 500.0, 'end': 510.0, 'kind': 'user_reject'} in labelled
    assert (400.0, 420.0) not in _spans(labelled)
    assert {(300.0, 320.0), (400.0, 420.0)} <= set(
        _spans(crosspass.call_args.args[4]))
    # The hold blocks gap merges; hard ranges clip every cut.
    assert (400.0, 420.0) in _spans(audio.process_episode.call_args.kwargs['cut_barriers'])
    assert (300.0, 320.0) not in _spans(audio.process_episode.call_args.kwargs['cut_barriers'])
    # A user rejection is a hard render barrier, like a keep.
    assert {(300.0, 320.0), (500.0, 510.0)} <= set(
        _spans(audio.process_episode.call_args.kwargs['hard_barriers']))
    assert (400.0, 420.0) not in _spans(
        audio.process_episode.call_args.kwargs['hard_barriers'])
    # The category keep still persists with the pass-2 markers.
    assert any(m['start'] == 300.0 and m['action_applied'] == 'keep'
               for m in output[3])


def _promo_defined():
    return {'start': 100.0, 'end': 130.0, 'confidence': 0.9,
            'category': 'self_promo', 'pattern_defined': True}


def test_pattern_defined_keep_category_resolves_to_remove_in_every_helper():
    keep_ads, remove_ads = processing._partition_keep_ads(
        [_promo_defined()], ACTIONS)
    assert keep_ads == [] and len(remove_ads) == 1

    assert len(processing._apply_late_keep_safety_net(
        [_promo_defined()], [], ACTIONS)) == 1

    [cut] = processing._partition_cut_actions([_promo_defined()], ACTIONS)
    assert cut['action_applied'] == 'remove'

    remaining_p, _, kept_p, _ = processing._partition_pass2_category_actions(
        [_promo_defined()], [_promo_defined()], ACTIONS)
    assert kept_p == [] and len(remaining_p) == 1

    stamped = _promo_defined()
    processing._stamp_pass2_cut_actions([stamped], [], ACTIONS)
    assert stamped['action_applied'] == 'remove'

    region = {'start': 100.0, 'end': 130.0, 'pattern_id': 7,
              'category': 'self_promo', 'pattern_defined': True}
    assert removal_coverage_regions([region], ACTIONS) == [region]

    merged = AdValidator.__new__(AdValidator)._merge_close_ads(
        [_promo_defined(),
         {'start': 131.0, 'end': 160.0, 'confidence': 0.9, 'category': 'sponsor'}],
        ValidationResult(ads=[]), actions_map=ACTIONS)
    assert len(merged) == 1

    folded = AdDetector.__new__(AdDetector)._merge_overlapping_accepted_duplicates(
        [_promo_defined(),
         {'start': 101.0, 'end': 131.0, 'confidence': 0.95, 'category': 'sponsor'}],
        action_map=ACTIONS)
    assert [m['category'] for m in folded] == ['sponsor']


def test_pattern_regions_carry_the_defined_flag():
    detector = AdDetector.__new__(AdDetector)
    detector.pattern_service = None
    match = SimpleNamespace(
        start=10.0, end=40.0, confidence=0.9, sponsor='Acme', pattern_id=3,
        category='self_promo', defined=True, span_estimated=False,
        text_start=None, text_end=None)
    all_ads, regions = [], []
    with patch('ad_detector._pattern_match_evidence', return_value='text'):
        detector._add_pattern_match(match, 'text_pattern', 'text', all_ads,
                                    regions, 'a1b2c3d4e5f6')
    assert regions[0]['pattern_defined'] is True


def test_process_transcript_uses_the_caller_action_map_once():
    detector = AdDetector(api_key='test-key')
    detector.db = None
    detector.audio_fingerprinter = None
    detector.pattern_service = None
    detector.text_pattern_matcher = None
    resolve = MagicMock(return_value={'sponsor': 'remove'})
    detect = MagicMock(return_value={'ads': [], 'status': 'success'})
    with (patch.object(detector, 'initialize_client'),
          patch.object(detector, '_resolve_segment_action_map', resolve),
          patch.object(detector, 'detect_ads', detect)):
        detector.process_transcript(
            [{'start': 0.0, 'end': 10.0, 'text': 'hello'}],
            podcast_name='Example Podcast', episode_title='Episode',
            slug='example-podcast', episode_id='a1b2c3d4e5f6',
            keep_content=False, action_map=ACTIONS)

    resolve.assert_not_called()
    assert detect.call_args.kwargs['action_map'] is ACTIONS


def test_verification_detection_uses_the_caller_action_map():
    detector = AdDetector(api_key='test-key')
    run_pass = MagicMock(return_value=([], [], 0, None, 0, 0, 0, {},
                                       AddressingStats()))
    resolve = MagicMock(return_value=None)
    with (patch.object(detector, 'initialize_client'),
          patch.object(detector, '_effective_addressing_mode',
                       return_value=('timestamps', 'timestamps')),
          patch.object(detector, 'get_verification_prompt', return_value='v'),
          patch.object(detector, 'get_verification_model', return_value='m'),
          patch.object(detector, '_build_known_pattern_hint', return_value=''),
          patch.object(detector, '_resolve_segment_action_map', resolve),
          patch.object(detector, '_run_detection_pass', run_pass),
          patch('ad_detector.create_windows', return_value=[{}])):
        result = detector.run_verification_detection(
            [{'start': 0.0, 'end': 10.0, 'text': 'hello'}],
            slug='example-podcast', episode_id='a1b2c3d4e5f6',
            action_map=ACTIONS)

    resolve.assert_not_called()
    assert run_pass.call_args.kwargs['action_map'] is ACTIONS
    assert result['segment_actions'] is ACTIONS


def test_verification_pass_forwards_the_resolved_actions():
    ctx = SimpleNamespace(
        slug='example-podcast', episode_id='a1b2c3d4e5f6', podcast_id=1,
        podcast_name='Example Podcast', episode_title='Episode',
        episode_description='', podcast_description='')
    fake_db = MagicMock()
    fake_db.get_setting_float.return_value = 0.8
    fake_db.get_false_positive_corrections.return_value = []
    with patch.object(processing, 'db', fake_db), \
         patch.object(processing, 'storage'), \
         patch('verification_pass.VerificationPass') as verifier_cls:
        verifier_cls.return_value.verify.return_value = {
            'ads': [], 'ads_processed': [], 'segments': [],
            'status': 'no_segments'}
        processing._run_verification_pass(
            ctx, '/tmp/pass1-output.mp3', [], False, 0.8, MagicMock(), None,
            segment_actions=ACTIONS)

    assert verifier_cls.return_value.verify.call_args.kwargs[
        'action_map'] is ACTIONS
    fake_db.resolve_segment_actions.assert_not_called()


def test_first_pass_detection_forwards_the_resolved_actions():
    ctx = SimpleNamespace(slug='example-podcast', episode_id='a1b2c3d4e5f6',
                          podcast_id=1)
    detector = MagicMock()
    detector.process_transcript.return_value = {'ads': [], 'status': 'success'}
    with patch.object(processing, 'ad_detector', detector), \
         patch.object(processing, 'db'), \
         patch.object(processing, 'storage'), \
         patch.object(processing, '_publish_status'):
        processing._detect_ads_first_pass(
            ctx, [], '/tmp/original.mp3', False, None, None,
            action_map=ACTIONS)

    assert detector.process_transcript.call_args.kwargs['action_map'] is ACTIONS
