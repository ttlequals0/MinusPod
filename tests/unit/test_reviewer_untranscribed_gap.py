"""Reviewer trims stay out of untranscribed audio.

Production shape: a merged 728.77s-752.55s hole let the reviewer move the start to 752.55s.
"""
from tests.app_bootstrap import bootstrap

bootstrap('reviewer_untranscribed_gap_test_')

import pytest

import ad_reviewer
from ad_reviewer import TranscriptIndex, _edge_transcript_supported
from main_app import processing
from utils.markers import (DAI_PROBE_SPANS, VAD_GAP_SPANS, carve_fragment, clip_dai_core_spans,
                           note_merged_members, reviewer_independent_spans)
from tests.unit.pipeline_test_utils import _run_pipeline
from tests.unit.reviewer_test_utils import _LLMResp, _mock_episode_meta, _resp, _reviewer


def _worded(start, end, text):
    tokens = text.split()
    step = (end - start) / len(tokens)
    words = [{'word': w, 'start': round(start + i * step, 2),
              'end': round(start + (i + 1) * step, 2)} for i, w in enumerate(tokens)]
    words[-1]['end'] = end
    return {'start': start, 'end': end, 'text': text, 'words': words}


HOLE_SEGMENTS = [
    _worded(700.0, 728.77, 'and thank you to our patrons. Yay!'),
    _worded(752.55, 800.0, 'And the best part? It integrates seamlessly with Acme.'),
    _worded(800.2, 937.8, 'Try Acme free at acme.example today.'),
    _worded(940.5, 1000.0, 'Okay so back to the show.'),
]


def _filled(pause):
    """The hole filled with speech ending `pause` seconds before 752.55."""
    return [HOLE_SEGMENTS[0],
            _worded(729.0, 752.55 - pause, 'I used to be the person who hunched over my laptop.'),
            *HOLE_SEGMENTS[1:]]


def test_start_edge_across_untranscribed_hole_is_unsupported():
    index = TranscriptIndex(HOLE_SEGMENTS)
    assert not _edge_transcript_supported(index, 'start', 752.55, 728.8)


def test_start_edge_after_a_short_pause_stays_supported():
    for pause in (0.5, 2.0):
        index = TranscriptIndex(_filled(pause))
        assert _edge_transcript_supported(index, 'start', 752.55, 728.8)


def _end_mirror(pause=None):
    head = _worded(0.0, 100.0, 'Try Acme free at acme.example today.')
    if pause is None:
        return [head, _worded(123.8, 150.0, 'Okay so back to the show.')]
    return [head, _worded(100.0 + pause, 123.0, 'more of the read here'),
            _worded(123.8, 150.0, 'Okay so back to the show.')]


def test_end_edge_mirror():
    assert not _edge_transcript_supported(
        TranscriptIndex(_end_mirror()), 'end', 100.0, 123.7)
    assert _edge_transcript_supported(
        TranscriptIndex(_end_mirror(pause=0.5)), 'end', 100.0, 123.7)
    assert _edge_transcript_supported(
        TranscriptIndex(_end_mirror(pause=2.0)), 'end', 100.0, 123.7)


def _marker():
    return {'start': 728.8, 'end': 939.9, 'confidence': 0.95,
            'detection_stage': 'claude', 'category': 'sponsor', 'sponsor': 'Acme',
            'reason': 'Acme sponsor read', 'vad_gap_extended': True,
            VAD_GAP_SPANS: [{'start': 728.77, 'end': 752.55}]}


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


def test_vad_gap_spans_survive_carve_clip_and_merge():
    marker = _marker()
    fragment = carve_fragment(marker, 740.0, 900.0)
    assert fragment[VAD_GAP_SPANS] == [{'start': 740.0, 'end': 752.55}]
    clip_dai_core_spans(fragment, 760.0, 900.0)
    assert VAD_GAP_SPANS not in fragment
    target = {'start': 600.0, 'end': 700.0, 'detection_stage': 'claude'}
    note_merged_members(target, _marker())
    assert target[VAD_GAP_SPANS] == [{'start': 728.77, 'end': 752.55}]
    assert (728.77, 752.55) in reviewer_independent_spans(_marker(), 0.8)


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
    marker = _dai_marker(**{VAD_GAP_SPANS: [{'start': 728.77, 'end': 752.55}]})
    with caplog.at_level('INFO', logger='ad_reviewer'):
        assert _clamp(marker, 752.55, 937.8) == (728.8, 937.8)
    lines = [r.getMessage() for r in caplog.records if 'Reviewer trim' in r.getMessage()]
    assert len(lines) == 1
    assert '752.5-937.8 -> 728.8-937.8' in lines[0]
    assert 'start floored by untranscribed audio' in lines[0]


@pytest.mark.parametrize('edge', ['start', 'end'])
def test_gap_rule_stops_at_a_keep_between_proposal_and_gap(edge):
    if edge == 'start':
        marker = dict(_marker(), start=700.0, end=939.9,
                      **{VAD_GAP_SPANS: [{'start': 700.0, 'end': 720.0}]})
        keep = {'start': 725.0, 'end': 730.0}
        got = _reviewer()._clamp_proposed_bounds(
            marker, 752.55, 939.9, 700.0, 939.9, 60, 'show', 'ep1',
            segments=HOLE_SEGMENTS, hard_barriers=[keep])
        assert got == (730.0, 939.9)
    else:
        marker = dict(_marker(), start=728.8, end=1000.0,
                      **{VAD_GAP_SPANS: [{'start': 980.0, 'end': 1000.0}]})
        keep = {'start': 950.0, 'end': 955.0}
        got = _reviewer()._clamp_proposed_bounds(
            marker, 728.8, 937.8, 728.8, 1000.0, 60, 'show', 'ep1',
            segments=HOLE_SEGMENTS, hard_barriers=[keep])
        assert got == (728.8, 950.0)


def test_contradiction_hold_recovered_trim_cannot_enter_a_merged_gap():
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
