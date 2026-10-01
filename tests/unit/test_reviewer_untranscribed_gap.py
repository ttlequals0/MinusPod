"""Reviewer trims stay out of untranscribed audio.

Production shape: a 728.77s-752.55s transcript hole let the reviewer move the start to 752.55s.
"""
from tests.app_bootstrap import bootstrap

bootstrap('reviewer_untranscribed_gap_test_')

import pytest

import ad_reviewer
from ad_reviewer import TranscriptIndex
from main_app import processing
from utils.markers import DAI_PROBE_SPANS
from tests.unit.pipeline_test_utils import _run_pipeline
from tests.unit.reviewer_test_utils import _LLMResp, _mock_episode_meta, _resp, _reviewer, _worded



HOLE_SEGMENTS = [
    _worded(700.0, 728.77, 'and that wraps up the listener mail. Great!'),
    _worded(752.55, 800.0, 'And the nicest thing? It works with Acme.'),
    _worded(800.2, 937.8, 'Try Acme free at acme.example today.'),
    _worded(940.5, 1000.0, 'Okay so back to the show.'),
]


def test_index_records_the_hole_and_not_short_pauses():
    assert TranscriptIndex(HOLE_SEGMENTS).gaps == [(728.77, 752.55)]


def _marker():
    return {'start': 728.8, 'end': 939.9, 'confidence': 0.95,
            'detection_stage': 'claude', 'category': 'sponsor', 'sponsor': 'Acme',
            'reason': 'Acme sponsor read'}


def test_reviewer_trim_across_the_hole_is_floored(monkeypatch):
    reviewer = _reviewer()
    monkeypatch.setattr(processing, '_build_reviewer', lambda db, detector: reviewer)
    body = '[{"start": 752.5, "end": 937.8, "is_ad": true, "confidence": 0.94}]'
    monkeypatch.setattr('ad_reviewer.call_llm_for_window',
                        lambda **kwargs: (_LLMResp(body), None))
    run = _run_pipeline([_marker()], {'sponsor': 'remove'}, segments=HOLE_SEGMENTS,
                        real_refine_reviewer=True, duration=1200.0)
    assert run['result'] is True
    cuts = run['local_ap'].process_episode.call_args.args[1]
    assert len(cuts) == 1
    assert cuts[0]['start'] == 728.8
    assert cuts[0]['end'] <= 939.9


def test_non_dai_marker_edge_stops_at_the_hole():
    got = _reviewer()._clamp_proposed_bounds(
        _marker(), 752.55, 937.8, 728.8, 939.9, 60, 'show', 'ep1', segments=HOLE_SEGMENTS)
    assert got == (728.8, 937.8)


def test_short_pause_does_not_stop_a_trim():
    segments = [HOLE_SEGMENTS[0], _worded(729.0, 752.0, 'more show talk here today'),
                *HOLE_SEGMENTS[1:]]
    got = _reviewer()._clamp_proposed_bounds(
        _marker(), 752.55, 937.8, 728.8, 939.9, 60, 'show', 'ep1', segments=segments)
    assert got == (752.55, 937.8)


def _dai_marker(**extra):
    marker = {'start': 728.8, 'end': 939.9, 'confidence': 0.95,
              'detection_stage': 'dai_differential', 'category': 'sponsor',
              'dai_core_spans': [{'start': 728.8, 'end': 939.9}],
              DAI_PROBE_SPANS: [{'start': 900.0, 'end': 904.0}]}
    marker.update(extra)
    return marker


def _clamp(marker, start, end, barriers=None):
    return _reviewer()._clamp_proposed_bounds(
        marker, start, end, 728.8, 939.9, 60, 'show', 'ep1',
        segments=HOLE_SEGMENTS, hard_barriers=barriers)


def test_eight_second_rule_floors_a_trim_past_the_dai_core_start(monkeypatch):
    assert _clamp(_dai_marker(), 752.55, 939.9) == (728.8, 939.9)
    # Without the rule the edge is transcript-supported and crosses the unmeasured region.
    monkeypatch.setattr(ad_reviewer, 'UNREVIEWABLE_GAP_SECONDS', 1000.0)
    assert _clamp(_dai_marker(), 752.55, 939.9) == (752.55, 939.9)


def test_gap_and_dai_clamp_log_one_consistent_line(caplog):
    marker = _dai_marker()
    with caplog.at_level('INFO', logger='ad_reviewer'):
        assert _clamp(marker, 752.55, 937.8) == (728.8, 937.8)
    lines = [r.getMessage() for r in caplog.records if 'Reviewer trim' in r.getMessage()]
    assert len(lines) == 1
    assert '752.5-937.8 -> 728.8-937.8' in lines[0]
    assert 'start floored by untranscribed audio' in lines[0]


BARRIER_SEGMENTS = [
    _worded(650.0, 700.0, 'show talk before the break'),
    _worded(720.0, 900.0, 'Try Acme free at acme.example today.'),
    _worded(900.2, 937.8, 'Acme makes great things for you.'),
    _worded(980.0, 1100.0, 'Okay so back to the show.'),
]


@pytest.mark.parametrize('edge', ['start', 'end'])
def test_gap_rule_stops_at_a_keep_between_proposal_and_gap(edge):
    marker = dict(_marker(), start=700.0, end=1000.0)
    if edge == 'start':
        proposal, keep, expected = (752.0, 1000.0), {'start': 725.0, 'end': 730.0}, (730.0, 1000.0)
    else:
        proposal, keep, expected = (700.0, 937.8), {'start': 950.0, 'end': 955.0}, (700.0, 950.0)
    got = _reviewer()._clamp_proposed_bounds(
        marker, *proposal, 700.0, 1000.0, 60, 'show', 'ep1',
        segments=BARRIER_SEGMENTS, hard_barriers=[keep])
    assert got == expected


def test_contradiction_hold_recovered_trim_cannot_cross_the_hole():
    reviewer = _reviewer({'review_max_boundary_shift': '60'})
    reason = ('The ad content ends at 800.0s; the rest is show content, '
              'is not an ad, and must be trimmed off the end')
    reviewer._llm_client.messages_create.side_effect = [
        _resp(f'[{{"start": 728.8, "end": 939.9, "confidence": 0.9, "reason": "{reason}"}}]'),
        _resp('{"ad_start": 752.55, "ad_end": 800.0}'),
    ]
    ad = dict(_marker(), confidence=0.9)
    result = reviewer.review(
        accepted_ads=[ad], resurrection_eligible=[], segments=HOLE_SEGMENTS,
        episode_meta=_mock_episode_meta(), pass_num=1, pass_model='claude-test')
    held = result.held_by_contradiction[0]
    assert held['reviewer_proposed_start'] == 728.8
    assert held['reviewer_proposed_end'] == 800.0


AFFIRMED_TRIM_REASON = (
    'The candidate is a genuine ad break containing one sponsor read for Acme. '
    'The original start of 728.8s '
    'is early: the read begins at 752.55s, so the start is trimmed forward to 752.55s '
    'where the sponsor read starts.'
)


def test_affirmed_trim_recovery_cannot_cross_the_hole():
    reviewer = _reviewer({'review_max_boundary_shift': '60'})
    ad = dict(_marker(), confidence=0.95, vad_gap_extended=True)
    reviewer._llm_client.messages_create.side_effect = [
        _resp(f'[{{"start": 728.8, "end": 939.9, "confidence": 0.95, '
              f'"reason": "{AFFIRMED_TRIM_REASON}"}}]'),
        _resp('{"ad_start": 752.55, "ad_end": 939.9}'),
    ]
    result = reviewer.review(
        accepted_ads=[ad], resurrection_eligible=[], segments=HOLE_SEGMENTS,
        episode_meta=_mock_episode_meta(), pass_num=1, pass_model='claude-test')
    assert reviewer._llm_client.messages_create.call_count == 2
    accepted = result.accepted_after_review[0]
    assert accepted['start'] == 728.8
    assert result.verdicts[0].verdict != 'adjust'


def test_end_cannot_be_trimmed_into_an_untranscribed_tail():
    segments = [_worded(900.0, 990.0, 'Try Acme free at acme.example today.')]
    got = _reviewer()._clamp_proposed_bounds(
        dict(_marker(), start=900.0, end=1000.0), 900.0, 990.0, 900.0, 1000.0, 60,
        'show', 'ep1', segments=segments)
    assert got == (900.0, 1000.0)


def test_word_gap_inside_one_segment_is_shown_in_the_prompt():
    words = [{'word': 'Acme', 'start': 100.0, 'end': 100.5},
             {'word': 'deal', 'start': 110.5, 'end': 111.0}]
    segments = [{'start': 100.0, 'end': 111.0, 'text': 'Acme deal', 'words': words}]
    prompt = _reviewer()._build_user_prompt(
        ad={'start': 100.0, 'end': 111.0}, segments=segments,
        episode_meta=_mock_episode_meta(), pool='accepted')
    assert '[100.50s-110.50s] (10.0 s of audio with no transcript)' in prompt


@pytest.mark.parametrize('proposal, barriers, expected', [
    # Barrier ends at 100.02, before the span's own start: it does not stop the floor.
    (105.0, [{'start': 90.0, 'end': 100.02}], 100.0),
    # A proposal inside (original start, span start] never triggers the silence floor.
    (100.02, [], 100.02),
])
def test_silence_floor_triggers_on_the_span_edge(proposal, barriers, expected):
    marker = {'start': 100.0, 'end': 200.0, 'confidence': 0.95, 'detection_stage': 'claude',
              'silent_absorbed_spans': [{'start': 100.03, 'end': 110.0}]}
    got = _reviewer()._clamp_proposed_bounds(
        marker, proposal, 200.0, 100.0, 200.0, 60, 'show', 'ep1', segments=[],
        hard_barriers=barriers)
    assert got == (expected, 200.0)


def test_gap_notes_tolerate_a_line_without_an_end():
    lines = [{'start': 50.0, 'text': 'no end'},
             {'start': 100.0, 'end': 105.0, 'text': 'one'},
             {'start': 120.0, 'end': 125.0, 'text': 'two'}]
    text = ad_reviewer._timestamped_with_gaps(lines, [(105.0, 120.0)], 100.0, 130.0)
    assert text.split('\n') == [
        '[100.0s-105.0s] one',
        '[105.00s-120.00s] (15.0 s of audio with no transcript)',
        '[120.0s-125.0s] two',
    ]
