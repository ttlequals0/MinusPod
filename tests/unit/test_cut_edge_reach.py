"""A cut edge reaches the lead-in and tail of the same read (real-excerpt fixtures, cases 1-3)."""
import json
import pathlib
from types import SimpleNamespace

from tests.app_bootstrap import bootstrap

bootstrap('cut_edge_reach_test_')

import pytest

from ad_detector import boundaries, edge_reach
from audio_enforcer import AUDIO_CUE_TYPE_SHOW_INTRO
from tests.unit.reviewer_test_utils import _build_reviewer, _mock_episode_meta, _resp, _worded

FIXTURES = pathlib.Path(__file__).resolve().parents[1] / 'fixtures' / 'cut_edges'
SETTINGS = {'review_prompt': 'review', 'resurrect_prompt': 'resurrect',
            'review_max_boundary_shift': '60'}


def _case(name):
    return json.loads((FIXTURES / name).read_text())


def _audio(case, signals=()):
    return SimpleNamespace(
        splice_evidence={'events': case['splice_events'], 'calibration': {'status': 'cold_start'}},
        signals=list(signals), get_signals_by_type=lambda kind: [
            s for s in signals if kind == 'audio_cue'])


def _review(case, body, *, podcast='Test Podcast', spans=(), signals=(), ad_extra=None,
            segments=None, enabled=True):
    reviewer = _build_reviewer(SETTINGS)
    reviewer._llm_client.messages_create.return_value = _resp(body)
    ad = {**case['marker'], 'confidence': 0.95, **(ad_extra or {})}
    meta = {**_mock_episode_meta(), 'podcast_name': podcast, 'edge_check': enabled,
            'marker_spans': [(ad['start'], ad['end']), *spans],
            'audio_analysis': _audio(case, signals)}
    result = reviewer.review(
        accepted_ads=[ad], resurrection_eligible=[],
        segments=segments or case['segments'], episode_meta=meta,
        pass_num=1, pass_model='claude-test')
    assert len(result.accepted_after_review) == 1
    return result.accepted_after_review[0], result


def _body(ad, **extra):
    fields = {'is_ad': True, 'start': ad['start'], 'end': ad['end'], 'confidence': 0.95, **extra}
    return json.dumps([fields])


def test_case1_lead_in_extends_to_splice():
    case = _case('case1_acme_health_leadin.json')
    lo, hi = case['expected']['expected_start_tolerance']
    out, result = _review(case, _body(case['marker'], lead_in_from='B5'))
    assert lo <= out['start'] <= hi
    assert out['end'] == pytest.approx(case['expected']['expected_end'], abs=0.1)
    assert out['reviewer_locked_start'] == out['start']
    assert out['edge_splice_anchor'] == ['start']
    assert result.verdicts[0].verdict == 'adjust'


def test_lead_in_null_keeps_start():
    case = _case('case1_acme_health_leadin.json')
    out, result = _review(case, _body(case['marker'], lead_in_from=None))
    assert out['start'] == case['marker']['start']
    assert result.verdicts[0].verdict == 'confirmed'


def test_unknown_or_malformed_label_keeps_start():
    case = _case('case1_acme_health_leadin.json')
    for label in ('B9', 'A1', 'five', 7, ''):
        out, _ = _review(case, _body(case['marker'], lead_in_from=label))
        assert out['start'] == case['marker']['start'], label


def test_edge_check_off_ignores_the_answer():
    case = _case('case1_acme_health_leadin.json')
    out, _ = _review(case, _body(case['marker'], lead_in_from='B5'), enabled=False)
    assert out['start'] == case['marker']['start']


def test_user_confirmed_marker_is_not_extended():
    case = _case('case1_acme_health_leadin.json')
    out, _ = _review(case, _body(case['marker'], lead_in_from='B5'),
                     ad_extra={'validation': {'user_confirmed': True}})
    assert out['start'] == case['marker']['start']


def test_lead_in_capped_at_25s():
    # Sentences end where the ad starts (100.0); the label names one 27 s away, the cap allows 25 s.
    def sentence(label, start, end):
        return {'label': label, 'start': start, 'end': end, 'text': 'x', 'words': 1, 'sparse': False}
    before = [sentence('B1', 90.0, 100.0), sentence('B2', 80.0, 90.0), sentence('B3', 73.0, 80.0)]
    start, end, _ = boundaries.resolve_edge_extension(
        start=100.0, end=160.0, before=before, after=[], lead_in_from='B3', tail_to=None,
        splices=[], start_floor=0.0, end_ceiling=1e9)
    assert (start, end) == (80.0, 160.0)
    assert 100.0 - start <= boundaries.LEAD_IN_MAX_MOVE_S


def test_lead_in_stops_at_previous_marker():
    case = _case('case1_acme_health_leadin.json')
    out, _ = _review(case, _body(case['marker'], lead_in_from='B5'), spans=[(2380.0, 2425.0)])
    assert out['start'] == pytest.approx(2425.08)
    assert 'edge_splice_anchor' not in out


def test_lead_in_skipped_in_episode_head_zone():
    case = _case('case1_acme_health_leadin.json')
    intro = SimpleNamespace(confidence=1.0, start=2500.0, end=2502.0,
                            details={'cue_type': AUDIO_CUE_TYPE_SHOW_INTRO})
    out, _ = _review(case, _body(case['marker'], lead_in_from='B5'), signals=[intro])
    assert out['start'] == case['marker']['start']


def test_edge_check_labels_map_to_sentence_times():
    case = _case('case1_acme_health_leadin.json')
    before, _after = edge_reach.edge_check_sentences(
        case['segments'], case['marker']['start'], case['marker']['end'])
    assert [s['label'] for s in before] == ['B1', 'B2', 'B3', 'B4', 'B5', 'B6']
    assert before[4]['text'].startswith('I gate to stone')
    for label, expected in (('B1', 2429.74), ('B3', 2425.08), ('B5', 2418.78)):
        start, _end, anchored = boundaries.resolve_edge_extension(
            start=2433.25, end=2540.52, before=before, after=[], lead_in_from=label,
            tail_to=None, splices=[], start_floor=0.0, end_ceiling=1e9)
        assert start == pytest.approx(expected), label
        assert anchored == []


def test_splice_inside_the_lead_in_sentence_does_not_anchor():
    before = [{'label': 'B1', 'start': 90.0, 'end': 100.0, 'sparse': False}]
    kw = dict(start=100.0, end=160.0, before=before, after=[], lead_in_from='B1', tail_to=None,
              start_floor=50.0, end_ceiling=200.0)
    assert boundaries.resolve_edge_extension(splices=[(92.0, 93.0)], **kw)[0] == 90.0
    assert boundaries.resolve_edge_extension(splices=[(88.0, 89.0)], **kw) == (89.0, 160.0, ['start'])
    after = [{'label': 'A1', 'start': 160.0, 'end': 170.0, 'sparse': False}]
    kw = dict(start=100.0, end=160.0, before=[], after=after, lead_in_from=None, tail_to='A1',
              start_floor=50.0, end_ceiling=200.0)
    assert boundaries.resolve_edge_extension(splices=[(167.0, 168.0)], **kw)[1] == 170.0
    assert boundaries.resolve_edge_extension(splices=[(171.0, 172.0)], **kw) == (100.0, 171.0, ['end'])


def test_tail_to_extends_end():
    segments = [_worded(0, 40, 'show talk. ' * 40),
                _worded(100.2, 101.5, 'Go to acme.com today.'),
                _worded(101.8, 103.5, "That's acme.com slash show."),
                _worded(103.8, 120.0, 'Welcome back to the conversation about gardens and soil ' * 3)]
    ad = {'start': 40.0, 'end': 100.0}
    case = {'marker': ad, 'segments': segments, 'splice_events': []}
    out, _ = _review(case, _body(ad, tail_to='A2'))
    assert out['end'] == pytest.approx(103.5)
    assert out['reviewer_locked_end'] == out['end']


def test_case2_sparse_line_fallback_moves_start():
    case = _case('case2_example_northguard_hole.json')
    lo, hi = case['expected']['expected_start_fallback_range']
    out, _ = _review(case, _body(case['marker'], lead_in_from=None), podcast='Example Show')
    assert lo <= out['start'] <= hi
    assert out['start'] >= case['marker']['start'] - boundaries.LEAD_IN_MAX_MOVE_S
    assert out['end'] == pytest.approx(case['expected']['expected_end'], abs=0.1)


def test_case2_repaired_segments_land_the_start_in_the_speech_window():
    case = _case('case2_example_northguard_hole.json')
    lo, hi = case['expected']['expected_start_tolerance']

    def line(start, end, text):
        step = (end - start) / len(text.split())
        words = [{'word': w, 'start': start + i * step, 'end': start + (i + 1) * step}
                 for i, w in enumerate(text.split())]
        return {'start': start, 'end': end, 'text': text, 'words': words}

    # What the sparse repair stores: the 4-word line is replaced by the real speech from 124.13.
    segments = [s for s in case['segments'] if s['start'] != 114.78]
    segments += [line(124.13, 133.0, 'This episode is sponsored by NorthGuard.'),
                 line(133.0, 144.5, 'The quiet part about small garden stories is that they sound calm.')]
    segments.sort(key=lambda s: s['start'])
    ad = case['marker']
    before, _ = edge_reach.edge_check_sentences(segments, ad['start'], ad['end'])
    label = next(s['label'] for s in before if s['text'].startswith('This episode'))
    out, _ = _review(case, _body(ad, lead_in_from=label), podcast='Example Show',
                     segments=segments)
    assert lo <= out['start'] <= hi
    assert out['reviewer_locked_start'] == out['start']


def test_sparse_fallback_needs_an_independent_member():
    case = _case('case2_example_northguard_hole.json')
    bare = {k: v for k, v in case['marker'].items()
            if k not in ('merged_member_spans', 'transcript_span')}
    out, _ = _review(case, _body(bare, lead_in_from=None), podcast='Example Show',
                     ad_extra={'merged_member_spans': [], 'transcript_span': None})
    assert out['start'] == case['marker']['start']


def _edge_lines(caplog):
    return [r.getMessage() for r in caplog.records if 'Edge check @' in r.getMessage()]


def test_edge_check_logs_the_answer_limits_and_move(caplog):
    caplog.set_level('INFO')
    case = _case('case1_acme_health_leadin.json')
    _review(case, _body(case['marker'], lead_in_from='B5'))
    (line,) = _edge_lines(caplog)
    assert 'lead_in_from=B5' in line and 'tail_to=none' in line
    assert 'floor ' in line and 'ceiling ' in line and 'floor n/a' not in line
    assert 'start moved to ' in line and 'end declined: ' in line
    assert 'splice anchor' in line


def test_edge_check_logs_when_nothing_moves(caplog):
    caplog.set_level('INFO')
    case = _case('case1_acme_health_leadin.json')
    out, _ = _review(case, _body(case['marker'], lead_in_from=None))
    assert out['start'] == case['marker']['start']
    (line,) = _edge_lines(caplog)
    assert 'lead_in_from=none' in line
    assert 'start declined: no answer from the reviewer' in line


def test_edge_check_logs_an_unknown_label_as_declined(caplog):
    caplog.set_level('INFO')
    case = _case('case1_acme_health_leadin.json')
    _review(case, _body(case['marker'], lead_in_from='B99'))
    (line,) = _edge_lines(caplog)
    assert 'lead_in_from=B99' in line and 'start declined: answer B99' in line


def test_edge_check_off_logs_nothing(caplog):
    caplog.set_level('INFO')
    case = _case('case1_acme_health_leadin.json')
    _review(case, _body(case['marker'], lead_in_from='B5'), enabled=False)
    assert _edge_lines(caplog) == []


def test_edge_check_explanation_request_uses_plain_words():
    import re
    from config import SEGMENT_CATEGORIES
    sentence = {'start': 1.0, 'end': 3.0, 'text': 'hello', 'sparse': False, 'label': 'B1'}
    block = edge_reach.format_edge_check([sentence], [dict(sentence, label='A1')], [], 10.0, 40.0)
    explain = next(s for s in block.split('. ') if 'written explanation' in s)
    for code in (c for c in SEGMENT_CATEGORIES if "_" in c):
        assert code not in explain
    assert not re.search(r'\b[BA]\d\b|pass|stage|_', explain.lower().replace('written', ''))
    assert "the host's own promotion" in explain and 'sponsor read' in explain


def test_sentences_close_at_terminals_pauses_length_and_non_speech():
    segs = [{'start': 0.0, 'end': 2.0, 'text': ' Try it today.'},
            {'start': 2.1, 'end': 4.0, 'text': ' no punctuation here'},
            {'start': 7.0, 'end': 9.0, 'text': ' after a pause'},
            {'start': 9.0, 'end': 10.0, 'text': ' ♪'},
            {'start': 10.0, 'end': 26.0, 'text': ' one long unpunctuated line'},
            {'start': 26.0, 'end': 27.0, 'text': ' next'}]
    texts = [s['text'] for s in edge_reach.build_sentences(segs)]
    assert texts == ['Try it today.', 'no punctuation here', 'after a pause',
                     'one long unpunctuated line', 'next']
