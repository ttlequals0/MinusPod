"""Reviewer user prompt carries the effective policy and evidence provenance."""
from unittest.mock import MagicMock

from tests.app_bootstrap import bootstrap

bootstrap('reviewer_policy_section_test_')

from ad_reviewer import (POLICY_LINE_CAP, AdReviewer, _capped_line, _edge_item,
                         _format_policy_section)
from database import DEFAULT_REVIEW_PROMPT
from main_app import processing
from utils.markers import hard_members, note_merged_members

ACTIONS = {'sponsor': 'remove', 'cross_promo': 'remove', 'self_promo': 'keep'}
SEGMENTS = [
    {'start': 3430.0, 'end': 3492.9, 'text': 'Show discussion.'},
    {'start': 3492.9, 'end': 3573.2, 'text': 'This episode is brought to you by Acme.'},
    {'start': 3573.2, 'end': 3680.7, 'text': 'Back to the show.'},
    {'start': 3680.7, 'end': 3740.0, 'text': 'More show discussion.'},
]


def _ad():
    return {
        'start': 3492.9, 'end': 3680.7, 'confidence': 0.95,
        'detection_stage': 'claude', 'category': 'sponsor',
        'merged_protected_start': 3492.9, 'merged_protected_end': 3680.7,
        'merged_member_spans': [
            {'start': 3492.9, 'end': 3573.2, 'stage': 'claude',
             'confidence': 0.95, 'precise_start': True, 'precise_end': True},
            {'start': 3544.2, 'end': 3680.7, 'stage': 'fingerprint',
             'fingerprint_match_start': 3544.2,
             'fingerprint_match_end': 3600.0, 'pattern_id': 12},
        ],
    }


def _meta(**extra):
    return {
        'podcast_name': 'Example Podcast', 'episode_title': 'Episode',
        'episode_description': '', 'podcast_description': '',
        'slug': 'example-podcast', 'episode_id': 'a1b2c3d4e5f6',
        'effective_category_actions': ACTIONS, 'min_cut_confidence': 0.8,
        'protected_spans': [
            {'start': 3450.0, 'end': 3480.0, 'kind': 'keep',
             'category': 'self_promo'},
            {'start': 3700.0, 'end': 3710.0, 'kind': 'user_reject'},
            {'start': 5000.0, 'end': 5100.0, 'kind': 'keep',
             'category': 'intro'},
        ],
        **extra,
    }


def _reviewer(settings=None):
    db = MagicMock()
    db.get_setting.side_effect = lambda key: (settings or {}).get(key)
    return AdReviewer(db=db, llm_client=MagicMock(), sponsor_service=None)


def _prompt(ad=None, meta=None, settings=None):
    return _reviewer(settings)._build_user_prompt(
        ad=ad or _ad(), segments=SEGMENTS, episode_meta=meta or _meta(),
        pool='accepted', max_shift=60)


def test_prompt_carries_policy_barriers_members_and_measured_edges():
    prompt = _prompt()
    assert ('Effective category actions: sponsor=remove, cross_promo=remove, '
            'self_promo=keep') in prompt
    assert ('Protected audio (never cut, do not cross): keep 3450.0-3480.0s '
            '(self_promo); user rejected 3700.0-3710.0s') in prompt
    assert '5000.0' not in prompt
    assert 'Evidence envelope: 3492.9-3680.7s' in prompt
    assert 'Member: claude 3492.9-3573.2s conf 0.95 precise start,end\n' in prompt
    assert ('Member: fingerprint pattern #12 3544.2-3680.7s (projected length, '
            'matched 3544.2-3600.0s)\n') in prompt
    assert ('Measured edges: start 3492.9s (transcript, precise), '
            'end 3573.2s (transcript, precise)') in prompt
    assert prompt.index('Effective category actions') < prompt.index('Transcript (60s')


def test_custom_review_prompt_still_gets_the_section():
    reviewer = _reviewer({'review_prompt': 'custom', 'resurrect_prompt': 'resurrect',
                          'review_max_boundary_shift': '60'})
    reviewer._llm_client.messages_create.return_value = MagicMock(
        content='[]', model='test-model')
    reviewer.review(accepted_ads=[_ad()], resurrection_eligible=[], segments=SEGMENTS,
                    episode_meta=_meta(), pass_num=1, pass_model='test-model')
    call = reviewer._llm_client.messages_create.call_args_list[0].kwargs
    assert call['system'].startswith('custom')
    assert 'Effective category actions' in call['messages'][0]['content']


def test_barriers_and_members_are_capped():
    near = [{'start': 3440.0 + i, 'end': 3440.5 + i, 'kind': 'user_reject'}
            for i in range(10)]
    ad = _ad()
    ad['merged_member_spans'] = [
        {'start': 3492.9 + 10 * i, 'end': 3500.0 + 10 * i, 'stage': 'fingerprint',
         'fingerprint_match_start': 3492.9 + 10 * i,
         'fingerprint_match_end': 3500.0 + 10 * i, 'pattern_id': i}
        for i in range(10)]
    section = _format_policy_section(ad, _meta(protected_spans=near), 60)
    lines = section.strip().splitlines()
    protected = next(line for line in lines if line.startswith('Protected audio'))
    members = [line for line in lines if line.startswith('Member:')]
    assert protected.count('user rejected') == 4
    # Nearest to the ad win.
    assert '3449.0-3449.5s' in protected and '3440.0-3440.5s' not in protected
    assert len(members) == 6
    assert all('fingerprint pattern' in line for line in members)
    assert all(len(line) <= 200 for line in lines)
    assert len(section) <= 1500


def test_long_action_map_stays_within_the_line_cap():
    actions = {f'category_{i}': 'remove' for i in range(40)}
    section = _format_policy_section(
        _ad(), _meta(effective_category_actions=actions), 60)
    assert all(len(line) <= 200 for line in section.splitlines())
    assert len(section) <= 1500


def test_holds_are_not_listed_and_plain_ad_has_no_provenance():
    meta = _meta(protected_spans=[], effective_category_actions=None,
                 hard_barriers=[{'start': 3450.0, 'end': 3480.0,
                                 'held_for_review': True}])
    assert _format_policy_section({'start': 3492.9, 'end': 3680.7}, meta, 60) == ''


def test_default_review_prompt_states_the_hard_limit():
    assert ('Protected audio lines are hard limits your boundaries must not '
            'cross, and fingerprint spans are projected lengths, so prefer '
            "the transcript's precise edges.") in DEFAULT_REVIEW_PROMPT


def test_fingerprint_members_record_their_pattern_id():
    target = {'start': 3492.9, 'end': 3573.2, 'detection_stage': 'claude',
              'confidence': 0.95}
    other = {'start': 3544.2, 'end': 3680.7, 'detection_stage': 'fingerprint',
             'fingerprint_match_start': 3544.2, 'fingerprint_match_end': 3600.0,
             'pattern_id': 12}
    note_merged_members(target, other)
    fingerprint = [m for m in target['merged_member_spans']
                   if m['stage'] == 'fingerprint']
    assert fingerprint[0]['pattern_id'] == 12


def test_pass1_reviewer_gets_keeps_and_user_rejections(monkeypatch):
    captured = {}
    reviewer = MagicMock()

    def review(**kwargs):
        captured.update(kwargs['episode_meta'])
        return MagicMock(accepted_after_review=[], verdicts=[], resurrected=[])

    reviewer.review.side_effect = review
    fake_db = MagicMock()
    fake_db.get_false_positive_corrections.return_value = [
        {'start': 3700.0, 'end': 3710.0}]
    fake_db.get_confirmed_corrections.return_value = []
    monkeypatch.setattr(processing, 'db', fake_db)
    monkeypatch.setattr(processing, '_ad_review_enabled', lambda db: True)
    monkeypatch.setattr(processing, '_build_reviewer', lambda db, det: reviewer)
    monkeypatch.setattr(processing, 'clear_fallback', lambda *args: None)
    monkeypatch.setattr(processing, '_publish_status', lambda *args: None)
    monkeypatch.setattr(processing, '_merge_reviewer_result', lambda *args: None)
    monkeypatch.setattr(processing.storage, 'save_combined_ads', lambda *args: None)
    keep = {'start': 3450.0, 'end': 3480.0, 'category': 'self_promo'}
    processing._run_ad_reviewer(
        'example-podcast', 'a1b2c3d4e5f6', 1, [_ad()], [_ad()], SEGMENTS,
        'Example Podcast', 'Episode', '', '', 0.8, 1, 'test-model',
        segment_actions=ACTIONS, hard_barriers=[keep])
    assert captured['protected_spans'] == [
        {'start': 3450.0, 'end': 3480.0, 'kind': 'keep', 'category': 'self_promo'},
        {'start': 3700.0, 'end': 3710.0, 'kind': 'user_reject'},
    ]


def test_pass1_reviewer_widen_stops_at_a_user_rejection(monkeypatch):
    reviewer = _reviewer({'review_prompt': 'review', 'resurrect_prompt': 'resurrect',
                          'review_max_boundary_shift': '60'})
    reviewer._llm_client.messages_create.return_value = MagicMock(
        content='[{"start": 3492.9, "end": 3620.0, "confidence": 0.95}]',
        model='test-model')
    fake_db = MagicMock()
    fake_db.get_false_positive_corrections.return_value = [
        {'start': 3590.0, 'end': 3610.0}]
    fake_db.get_confirmed_corrections.return_value = []
    monkeypatch.setattr(processing, 'db', fake_db)
    monkeypatch.setattr(processing, '_ad_review_enabled', lambda db: True)
    monkeypatch.setattr(processing, '_build_reviewer', lambda db, det: reviewer)
    monkeypatch.setattr(processing, 'clear_fallback', lambda *args: None)
    monkeypatch.setattr(processing, '_publish_status', lambda *args: None)
    monkeypatch.setattr(processing.storage, 'save_combined_ads', lambda *args: None)
    ad = {'start': 3492.9, 'end': 3573.2, 'confidence': 0.95,
          'detection_stage': 'claude', 'category': 'sponsor'}

    cuts, _ = processing._run_ad_reviewer(
        'example-podcast', 'a1b2c3d4e5f6', 1, [ad], [ad], SEGMENTS,
        'Example Podcast', 'Episode', '', '', 0.8, 1, 'test-model',
        segment_actions=ACTIONS, hard_barriers=[])

    assert [(c['start'], c['end']) for c in cuts] == [(3492.9, 3590.0)]


def test_capped_line_truncates_an_oversized_first_item_instead_of_dropping_it():
    item = 'x' * 250
    line = _capped_line('Member', [item])
    assert line != ''
    assert len(line) == POLICY_LINE_CAP
    assert line.startswith('Member: ')
    assert line.endswith('...')


def test_coarse_transcript_edges_are_labelled_unmeasured():
    ad = _ad()
    ad['merged_member_spans'][0].update(precise_start=False, precise_end=False)
    ad['merged_member_spans'].pop()

    assert 'Measured edges: start unmeasured, end unmeasured' in _prompt(ad=ad)


def test_measured_inner_member_labels_the_edge_under_a_coarse_outer_one():
    ad = {'start': 100.0, 'end': 200.0, 'merged_protected_start': 100.0,
          'merged_protected_end': 200.0, 'merged_member_spans': [
        {'start': 100.0, 'end': 200.0, 'stage': 'claude', 'confidence': 0.95,
         'precise_start': False, 'precise_end': False},
        {'start': 110.0, 'end': 190.0, 'stage': 'fingerprint',
         'fingerprint_match_start': 110.0, 'fingerprint_match_end': 190.0}]}
    hard = hard_members(ad, 0.8)

    assert _edge_item(ad, 'start', 0.8, hard) == 'start 110.0s (fingerprint)'
    assert _edge_item(ad, 'end', 0.8, hard) == 'end 190.0s (fingerprint)'
