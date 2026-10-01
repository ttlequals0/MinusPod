"""A transcript-supported reviewer trim may cross an unmeasured DAI region edge."""
import math
import random

import pytest

from tests.app_bootstrap import bootstrap

bootstrap('reviewer_dai_core_supported_trim_test_')

from ad_detector import dai_differential_ads
from ad_reviewer import (AdReviewer, TranscriptIndex,
                         _SUPPORTED_EDGE_GAP_S, _edge_transcript_supported, _negated,
                         _speech_capped_floor, _speech_units)
from ad_validator import AdValidator, ValidationResult
from config import UNREVIEWABLE_GAP_SECONDS
from audio_analysis.base import AudioAnalysisResult
from audio_processor import AudioProcessor
from main_app import processing
from utils.markers import (DAI_PROBE_SPANS, EDGE_TOLERANCE, carve_fragment,
                           clip_dai_core_spans, drop_stale_reviewer_locks, merge_dai_core_spans,
                           reviewer_edge_locked, reviewer_independent_spans, TimedWords)
from tests.unit.marker_test_utils import _ad
from tests.unit.pipeline_test_utils import _run_pipeline
from tests.unit.reviewer_test_utils import _LLMResp, _reviewer


def _worded(start, end, text):
    """Segment with its tokens spread evenly over [start, end] as timed words."""
    tokens = text.split()
    step = (end - start) / len(tokens)
    words = [{'word': w, 'start': round(start + i * step, 2),
              'end': round(start + (i + 1) * step, 2)} for i, w in enumerate(tokens)]
    words[-1]['end'] = end
    return {'start': start, 'end': end, 'text': text, 'words': words}


SEGMENTS = [
    _worded(0.68, 20.42, 'This episode is brought to you by Acme.'),
    _worded(20.68, 49.0, 'Acme makes the best widgets around.'),
    _worded(49.28, 58.2, 'Visit acme.example, Acme delivers.'),
    {'start': 61.06, 'end': 87.16, 'text': 'Welcome back to the show, today we talk about gardens.'},
    {'start': 87.83, 'end': 105.89, 'text': 'Our guest has grown tomatoes for decades.'},
]


def _marker(**overrides):
    marker = {
        'start': 0.0, 'end': 73.2, 'confidence': 0.95,
        'detection_stage': 'dai_differential', 'category': 'sponsor',
        'sponsor': 'Acme', 'reason': 'Dynamically inserted: audio differs across fetches',
        'dai_core_spans': [{'start': 0.0, 'end': 73.2}],
        DAI_PROBE_SPANS: [{'start': 0.5, 'end': 4.5}],
        'merged_member_spans': [{
            'start': 23.6, 'end': 29.82, 'stage': 'fingerprint',
            'fingerprint_match_start': 0.0, 'fingerprint_match_end': 63.8}],
        'merged_protected_start': 23.6, 'merged_protected_end': 29.82,
        'merged_distinct_ads': True,
    }
    marker.update(overrides)
    return marker


def _run(monkeypatch, marker, end=58.2):
    reviewer = _reviewer()
    monkeypatch.setattr(processing, '_build_reviewer', lambda db, detector: reviewer)
    body = f'[{{"start": 3.2, "end": {end}, "is_ad": true, "confidence": 0.94}}]'
    monkeypatch.setattr('ad_reviewer.call_llm_for_window',
                        lambda **kwargs: (_LLMResp(body), None))
    run = _run_pipeline([marker], {'sponsor': 'remove'}, segments=SEGMENTS,
                        real_refine_reviewer=True, duration=600.0)
    assert run['result'] is True
    cuts = run['local_ap'].process_episode.call_args.args[1]
    return run, [(c['start'], c['end']) for c in cuts]


def _saved_marker(run):
    saved = run['storage'].save_combined_ads.call_args_list[-1].args[2]
    return next(m for m in saved if m.get('detection_stage') == 'dai_differential')


def test_supported_trim_crosses_unmeasured_region_end(monkeypatch):
    run, cuts = _run(monkeypatch, _marker())
    assert cuts == [(0.0, 58.2)]
    saved = _saved_marker(run)
    assert saved['dai_core_spans'] == [{'start': 0.0, 'end': 58.2}]
    AdValidator(episode_duration=600.0, segments=[])._clamp_boundaries(
        [saved], ValidationResult(ads=[]))
    assert saved['end'] == 58.2


def test_absorbed_silence_floors_supported_end(monkeypatch):
    # Same proposal as the unmeasured-region trim above, but 58.2-73.2 is absorbed silence.
    _, cuts = _run(monkeypatch, _marker(
        silent_absorbed_spans=[{'start': 58.2, 'end': 73.2}]))
    assert cuts == [(0.0, 73.2)]


def test_fingerprint_match_over_region_floors_supported_end(monkeypatch):
    member = {'start': 0.0, 'end': 73.2, 'stage': 'fingerprint',
              'fingerprint_match_start': 0.0, 'fingerprint_match_end': 73.2}
    # Unmerged, so the supported-edge floor runs; merged, the member-conflict hold fires first.
    _, cuts = _run(monkeypatch, _marker(
        merged_member_spans=[member], merged_protected_start=0.0,
        merged_protected_end=73.2, merged_distinct_ads=False))
    assert cuts == [(0.0, 73.2)]


def test_second_probe_block_floors_supported_end(monkeypatch):
    _, cuts = _run(monkeypatch, _marker(**{
        DAI_PROBE_SPANS: [{'start': 0.5, 'end': 4.5},
                          {'start': 61.56, 'end': 65.56}]}))
    assert cuts == [(0.0, 73.2)]


def test_cue_pair_floors_supported_end(monkeypatch):
    _, cuts = _run(monkeypatch, _marker(cue_pair={
        'start': {'cue_end': -0.05}, 'end': {'cue_start': 73.25}}))
    assert cuts == [(0.0, 73.2)]


def test_unsupported_end_keeps_floor_when_segment_straddles(monkeypatch):
    # 61.06-87.16 has no word timings, so it may merge ad and show speech.
    _, cuts = _run(monkeypatch, _marker(), end=60.0)
    assert cuts == [(0.0, 73.2)]


def _supported(segments, edge, new, old):
    return _edge_transcript_supported(TranscriptIndex(segments), edge, new, old)


def test_end_edge_supported_by_word_end_and_gap():
    assert _supported(SEGMENTS, 'end', 58.2, 73.2)
    # 3.2 is no word edge; 20.42 is followed 0.26 s later by speech.
    assert not _supported(SEGMENTS, 'start', 3.2, 0.0)
    assert not _supported(SEGMENTS, 'end', 20.42, 73.2)
    # Not inward.
    assert not _supported(SEGMENTS, 'end', 58.2, 58.2)
    # No speech in the released span is still supported.
    assert _supported(SEGMENTS, 'end', 58.2, 60.0)
    # Rounded to one decimal, as the prompt shows it.
    rounded = [_worded(49.28, 58.23, 'Visit Acme.'), SEGMENTS[3]]
    assert _supported(rounded, 'end', 58.2, 73.2)


def test_start_edge_supported_mirrors_end():
    segments = [_worded(0.0, 10.0, 'Show talk.'),
                _worded(11.0, 40.0, 'Brought to you by Acme.')]
    assert _supported(segments, 'start', 11.0, 0.0)
    assert _supported(segments, 'start', 11.0, 10.5)
    close = [_worded(0.0, 10.9, 'Show talk.'), segments[1]]
    assert not _supported(close, 'start', 11.0, 0.0)
    assert not _supported([{'start': 0.0, 'end': 10.0, 'text': 'Show talk.'},
                           {'start': 11.0, 'end': 40.0, 'text': 'Acme.'}],
                          'start', 11.0, 0.0)


def test_word_end_before_long_pause_is_supported_without_released_speech():
    segments = [_worded(2940.0, 2959.57, 'Visit acme.example today.'),
                _worded(2966.47, 2980.0, 'Welcome back to the show.')]
    assert _supported(segments, 'end', 2959.57, 2961.04)


def test_segment_end_without_word_timings_is_unsupported():
    segments = [{'start': 2940.0, 'end': 2959.57, 'text': 'Visit acme.example today.'},
                {'start': 2966.47, 'end': 2980.0, 'text': 'Welcome back to the show.'}]
    assert not _supported(segments, 'end', 2959.57, 2961.04)


def test_word_end_with_next_word_close_is_unsupported():
    segments = [_worded(2940.0, 2959.57, 'Visit acme.example today.'),
                _worded(2959.77, 2980.0, 'Welcome back to the show.')]
    assert not _supported(segments, 'end', 2959.57, 2961.04)


def test_word_edges_support_a_trim_inside_a_segment():
    segments = [{'start': 0.0, 'end': 30.0, 'text': 'Acme rocks. Welcome back.',
                 'words': [{'word': 'Acme', 'start': 0.0, 'end': 5.0},
                           {'word': 'rocks.', 'start': 5.0, 'end': 12.0},
                           {'word': 'Welcome', 'start': 14.0, 'end': 20.0},
                           {'word': 'back.', 'start': 20.0, 'end': 30.0}]}]
    assert _supported(segments, 'end', 12.0, 30.0)


def test_independent_spans_cover_measured_evidence():
    marker = _marker(cue_pair={'start': {'cue_end': 1.0},
                               'end': {'cue_start': 70.0}})
    spans = reviewer_independent_spans(marker, 0.8)
    assert (23.6, 29.82) in spans
    assert (0.5, 4.5) in spans
    assert (1.05, 69.95) in spans
    assert (0.0, 73.2) not in spans
    confirmed = _marker(validation={'user_confirmed': True})
    assert (0.0, 73.2) in reviewer_independent_spans(confirmed, 0.8)


def test_differential_ads_record_each_block_probe_window():
    regions = [
        {'start_s': 0.0, 'end_s': 30.0, 'kind': 'differential', 'corr': 0.1},
        {'start_s': 30.0, 'end_s': 32.0, 'kind': 'differential', 'corr': 0.1},
    ]
    ads = dai_differential_ads({'regions': regions}, [], [(0.0, 32.0)])
    assert ads[0][DAI_PROBE_SPANS] == [{'start': 0.5, 'end': 4.5},
                                       {'start': 30.0, 'end': 32.0}]


def test_probe_spans_ride_merges_clips_and_fragments():
    target = {'dai_core_spans': [{'start': 0.0, 'end': 30.0}],
              DAI_PROBE_SPANS: [{'start': 0.5, 'end': 4.5}]}
    other = {'dai_core_spans': [{'start': 30.0, 'end': 60.0}],
             DAI_PROBE_SPANS: [{'start': 30.5, 'end': 34.5}]}
    merge_dai_core_spans(target, other)
    assert target[DAI_PROBE_SPANS] == [{'start': 0.5, 'end': 4.5},
                                       {'start': 30.5, 'end': 34.5}]
    fragment = carve_fragment(dict(target, start=0.0, end=60.0), 2.0, 32.0)
    assert fragment[DAI_PROBE_SPANS] == [{'start': 2.0, 'end': 4.5},
                                         {'start': 30.5, 'end': 32.0}]
    clip_dai_core_spans(target, 10.0, 60.0)
    assert target[DAI_PROBE_SPANS] == [{'start': 30.5, 'end': 34.5}]


def test_clamp_without_segments_keeps_core_floor():
    reviewer = AdReviewer.__new__(AdReviewer)
    bounds = reviewer._clamp_proposed_bounds(
        _marker(), 3.2, 58.2, 0.0, 73.2, 60, 'slug', 'ep')
    assert bounds == pytest.approx((0.0, 73.2))


def test_clamp_with_segments_lets_supported_end_cross_core():
    reviewer = AdReviewer.__new__(AdReviewer)
    bounds = reviewer._clamp_proposed_bounds(
        _marker(), 3.2, 58.2, 0.0, 73.2, 60, 'slug', 'ep', segments=SEGMENTS)
    assert bounds == pytest.approx((0.0, 58.2))


def test_clamp_log_names_what_floored_each_edge(caplog):
    marker = _marker(cue_pair={'start': {'cue_end': -0.05}, 'end': {'cue_start': 63.85}})
    reviewer = AdReviewer.__new__(AdReviewer)
    with caplog.at_level('INFO', logger='ad_reviewer'):
        bounds = reviewer._clamp_proposed_bounds(
            marker, 3.2, 58.2, 0.0, 73.2, 60,
            'slug', 'ep', segments=SEGMENTS)
    assert bounds == pytest.approx((0.0, 63.8))
    lines = [r.getMessage() for r in caplog.records if 'DAI core' in r.getMessage()]
    assert len(lines) == 1
    assert 'start floored by DAI core, end floored by independent span' in lines[0]


# "vary." ends 94.2 and "Dare" starts 0.22 s later, too close for a supported edge.
TIGHT_SEGMENTS = SEGMENTS[:3] + [
    {'start': 61.06, 'end': 94.2, 'text': 'Welcome back, results may vary.',
     'words': [{'word': 'Welcome', 'start': 61.06, 'end': 70.0},
               {'word': 'back,', 'start': 70.0, 'end': 80.0},
               {'word': 'results', 'start': 80.0, 'end': 90.0},
               {'word': 'vary.', 'start': 90.0, 'end': 94.2}]},
    {'start': 94.42, 'end': 105.89, 'text': 'Dare to grow tomatoes.',
     'words': [{'word': 'Dare', 'start': 94.42, 'end': 94.98},
               {'word': 'to', 'start': 94.98, 'end': 100.0},
               {'word': 'grow', 'start': 100.0, 'end': 103.0},
               {'word': 'tomatoes.', 'start': 103.0, 'end': 105.89}]},
]


def _tight_marker(**overrides):
    return _marker(**dict({'end': 94.8, 'dai_core_spans': [{'start': 0.0, 'end': 94.8}]},
                          **overrides))


def test_unsupported_end_capped_at_next_spoken_word(caplog):
    reviewer = AdReviewer.__new__(AdReviewer)
    with caplog.at_level('INFO', logger='ad_reviewer'):
        bounds = reviewer._clamp_proposed_bounds(
            _tight_marker(), 0.0, 94.2, 0.0, 94.8, 60, 'slug', 'ep',
            segments=TIGHT_SEGMENTS)
    assert bounds == pytest.approx((0.0, 94.2))
    lines = [r.getMessage() for r in caplog.records if 'DAI core' in r.getMessage()]
    assert len(lines) == 1
    assert 'start floored by none, end floored by spoken word cap' in lines[0]


def test_unsupported_end_off_word_edge_capped_at_word_start():
    reviewer = AdReviewer.__new__(AdReviewer)
    bounds = reviewer._clamp_proposed_bounds(
        _tight_marker(), 0.0, 94.3, 0.0, 94.8, 60, 'slug', 'ep',
        segments=TIGHT_SEGMENTS)
    assert bounds == pytest.approx((0.0, 94.42))


def test_next_word_inside_probe_keeps_core_floor():
    reviewer = AdReviewer.__new__(AdReviewer)
    marker = _tight_marker(**{DAI_PROBE_SPANS: [{'start': 0.5, 'end': 4.5},
                                                {'start': 94.3, 'end': 94.8}]})
    bounds = reviewer._clamp_proposed_bounds(
        marker, 0.0, 94.2, 0.0, 94.8, 60, 'slug', 'ep', segments=TIGHT_SEGMENTS)
    assert bounds == pytest.approx((0.0, 94.8))


def test_unsupported_start_capped_at_previous_spoken_word():
    segments = [{'start': 0.0, 'end': 10.0, 'text': 'Show talk ends.',
                 'words': [{'word': 'Show', 'start': 0.0, 'end': 5.0},
                           {'word': 'ends.', 'start': 5.0, 'end': 10.0}]},
                {'start': 10.2, 'end': 40.0, 'text': 'Brought to you by Acme.'}]
    marker = {'start': 9.5, 'end': 40.0, 'detection_stage': 'dai_differential',
              'dai_core_spans': [{'start': 9.5, 'end': 40.0}],
              DAI_PROBE_SPANS: [{'start': 35.0, 'end': 39.0}]}
    reviewer = AdReviewer.__new__(AdReviewer)
    bounds = reviewer._clamp_proposed_bounds(
        marker, 10.2, 40.0, 9.5, 40.0, 60, 'slug', 'ep', segments=segments)
    assert bounds == pytest.approx((10.2, 40.0))
    probed = dict(marker, **{DAI_PROBE_SPANS: [{'start': 9.5, 'end': 13.5}]})
    bounds = reviewer._clamp_proposed_bounds(
        probed, 10.2, 40.0, 9.5, 40.0, 60, 'slug', 'ep', segments=segments)
    assert bounds == pytest.approx((9.5, 40.0))


def test_unsupported_end_render_keeps_next_spoken_word(monkeypatch):
    reviewer = _reviewer()
    monkeypatch.setattr(processing, '_build_reviewer', lambda db, detector: reviewer)
    body = '[{"start": 0.0, "end": 94.2, "is_ad": true, "confidence": 0.94}]'
    monkeypatch.setattr('ad_reviewer.call_llm_for_window',
                        lambda **kwargs: (_LLMResp(body), None))
    run = _run_pipeline([_tight_marker()], {'sponsor': 'remove'},
                        segments=TIGHT_SEGMENTS, real_refine_reviewer=True,
                        duration=600.0)
    assert run['result'] is True
    cuts = run['local_ap'].process_episode.call_args.args[1]
    assert [(c['start'], c['end']) for c in cuts] == [(0.0, 94.2)]


def test_speech_wholly_inside_core_keeps_core_floor():
    # Speech after 20.42 pauses at 58.2, well before the core end.
    reviewer = AdReviewer.__new__(AdReviewer)
    bounds = reviewer._clamp_proposed_bounds(
        _marker(), 0.0, 20.42, 0.0, 73.2, 60, 'slug', 'ep', segments=SEGMENTS)
    assert bounds == pytest.approx((0.0, 73.2))


def test_contiguous_words_cap_at_word_straddling_floor():
    # 0.05 s gaps everywhere: the cap must not release the unprobed core after 20.45.
    words = [{'word': 'w', 'start': i * 0.5, 'end': i * 0.5 + 0.45} for i in range(212)]
    segments = [{'start': 0.0, 'end': 106.0, 'text': 'x', 'words': words}]
    marker = {'start': 0.0, 'end': 94.8, 'detection_stage': 'dai_differential',
              'dai_core_spans': [{'start': 0.0, 'end': 94.8}],
              DAI_PROBE_SPANS: [{'start': 0.5, 'end': 4.5}]}
    reviewer = AdReviewer.__new__(AdReviewer)
    bounds = reviewer._clamp_proposed_bounds(
        marker, 0.0, 20.45, 0.0, 94.8, 600, 'slug', 'ep', segments=segments)
    assert bounds == pytest.approx((0.0, 94.5))


def test_segment_without_words_straddling_floor_keeps_core_floor():
    reviewer = AdReviewer.__new__(AdReviewer)
    bounds = reviewer._clamp_proposed_bounds(
        _marker(), 0.0, 58.2, 0.0, 73.2, 60, 'slug', 'ep',
        segments=SEGMENTS[:3] + [{'start': 58.4, 'end': 87.16, 'text': 'Welcome back.'}])
    assert bounds == pytest.approx((0.0, 73.2))


def _start_marker():
    return {'start': 9.5, 'end': 40.0, 'detection_stage': 'dai_differential',
            'dai_core_spans': [{'start': 9.5, 'end': 40.0}],
            DAI_PROBE_SPANS: [{'start': 35.0, 'end': 39.0}]}


def test_unsupported_start_after_pause_keeps_core_floor():
    segments = [{'start': 0.0, 'end': 9.0, 'text': 'Show talk ends.',
                 'words': [{'word': 'Show', 'start': 0.0, 'end': 5.0},
                           {'word': 'ends.', 'start': 5.0, 'end': 9.0}]},
                {'start': 10.2, 'end': 40.0, 'text': 'Brought to you by Acme.'}]
    reviewer = AdReviewer.__new__(AdReviewer)
    bounds = reviewer._clamp_proposed_bounds(
        _start_marker(), 10.2, 40.0, 9.5, 40.0, 60, 'slug', 'ep', segments=segments)
    assert bounds == pytest.approx((9.5, 40.0))


def test_unsupported_start_off_word_lands_on_straddling_word_end():
    segments = [{'start': 0.0, 'end': 10.0, 'text': 'Show talk ends.',
                 'words': [{'word': 'Show', 'start': 0.0, 'end': 5.0},
                           {'word': 'ends.', 'start': 5.0, 'end': 10.0}]},
                {'start': 10.2, 'end': 40.0, 'text': 'Brought to you by Acme.'}]
    reviewer = AdReviewer.__new__(AdReviewer)
    bounds = reviewer._clamp_proposed_bounds(
        _start_marker(), 10.1, 40.0, 9.5, 40.0, 60, 'slug', 'ep', segments=segments)
    assert bounds == pytest.approx((10.0, 40.0))


# Released span 2959.57-2961.04 holds no transcribed speech; the show resumes at 2966.47.
OUTRO_SEGMENTS = [
    _worded(2790.0, 2801.8, 'That is all for the first half.'),
    _worded(2802.09, 2900.0, 'This episode is brought to you by Acme.'),
    _worded(2900.2, 2959.57, 'Visit acme.example, Acme delivers.'),
    _worded(2966.47, 2990.0, 'Our guest grows tomatoes in the garden.'),
]


def _dai_marker(start, end, probes):
    return _ad(start, end, stage='dai_differential', confidence=0.95,
               category='sponsor', sponsor='Acme',
               reason='Dynamically inserted: audio differs across fetches',
               dai_core_spans=[{'start': start, 'end': end}], **{DAI_PROBE_SPANS: probes})


def _outro_marker(probes):
    return _dai_marker(2820.81, 2961.04, probes)


def _splice(events):
    analysis = AudioAnalysisResult()
    analysis.splice_evidence = {'events': events, 'calibration': {'status': 'calibrated'}}
    return analysis


def _run_reviewed(monkeypatch, marker, segments, bounds, duration, events=()):
    reviewer = _reviewer()
    monkeypatch.setattr(processing, '_build_reviewer', lambda db, detector: reviewer)
    body = f'[{{"start": {bounds[0]}, "end": {bounds[1]}, "is_ad": true, "confidence": 0.94}}]'
    monkeypatch.setattr('ad_reviewer.call_llm_for_window',
                        lambda **kwargs: (_LLMResp(body), None))
    run = _run_pipeline([marker], {'sponsor': 'remove'}, segments=segments,
                        real_refine_reviewer=True, real_sweeps=True,
                        audio_analysis_result=_splice(list(events)), duration=duration)
    assert run['result'] is True
    cuts = run['local_ap'].process_episode.call_args.args[1]
    return run, [(c['start'], c['end']) for c in cuts]


def _run_outro(monkeypatch, probes):
    return _run_reviewed(monkeypatch, _outro_marker(probes), OUTRO_SEGMENTS,
                         (2802.09, 2959.57), 3600.0)


def _assert_bounds(run, cuts, start, end):
    saved = _saved_marker(run)
    assert (saved['start'], saved['end']) == pytest.approx((start, end), abs=EDGE_TOLERANCE)
    assert cuts == [pytest.approx((start, end), abs=EDGE_TOLERANCE)]
    return saved


def test_word_supported_end_crosses_core_without_released_speech(monkeypatch):
    run, cuts = _run_outro(monkeypatch, [{'start': 2821.31, 'end': 2825.31}])
    saved = _assert_bounds(run, cuts, 2802.09, 2959.57)
    assert saved['dai_core_spans'][-1]['end'] == pytest.approx(2959.57)
    AdValidator(episode_duration=3600.0, segments=[])._clamp_boundaries(
        [saved], ValidationResult(ads=[]))
    assert saved['end'] == pytest.approx(2959.57)


def test_probe_over_released_span_keeps_core_end(monkeypatch):
    run, cuts = _run_outro(monkeypatch, [{'start': 2821.31, 'end': 2825.31},
                                        {'start': 2959.0, 'end': 2961.04}])
    assert len(cuts) == 1 and cuts[0][1] == pytest.approx(2961.04)
    saved = _saved_marker(run)
    assert saved['end'] == pytest.approx(2961.04)
    assert 'reviewer_locked_end' not in saved


MIDROLL_SEGMENTS = [
    _worded(3760.0, 3772.67, 'That wraps the first story.'),
    _worded(3773.63, 3849.02, 'This episode is brought to you by Acme widgets.'),
    _worded(3851.5, 3856.0, 'Visit acme.example for a free trial.'),
    _worded(3858.0, 3890.0, 'Our guest grows tomatoes in the garden.'),
]


def _midroll_marker(probes, end=3851.08):
    return _dai_marker(3773.39, end, probes)


def test_tail_completion_keeps_reviewer_locked_end(monkeypatch):
    run, cuts = _run_reviewed(
        monkeypatch, _midroll_marker([{'start': 3773.89, 'end': 3777.89}]),
        MIDROLL_SEGMENTS, (3773.63, 3849.02), 3950.0,
        events=[{'time': 3857.0, 'type': 'deep_silence', 'depth_dbfs': -70.0}])
    saved = _assert_bounds(run, cuts, 3773.63, 3849.02)
    assert saved['reviewer_locked_start'] == pytest.approx(3773.63)
    assert saved['reviewer_locked_end'] == pytest.approx(3849.02)
    assert not saved.get('tail_completed')


def test_terminal_snap_keeps_reviewer_locked_start(monkeypatch):
    run, cuts = _run_reviewed(
        monkeypatch, _midroll_marker([{'start': 3773.89, 'end': 3777.89}], end=3850.5),
        MIDROLL_SEGMENTS[:2], (3773.63, 3849.02), 3850.5,
        events=[{'time': 3772.94, 'type': 'deep_silence', 'depth_dbfs': -70.0}])
    saved = _assert_bounds(run, cuts, 3773.63, 3849.02)
    assert 'terminal_snap' not in saved


def test_probe_over_released_start_keeps_core_start(monkeypatch):
    run, cuts = _run_reviewed(
        monkeypatch, _midroll_marker([{'start': 3773.39, 'end': 3777.39}]),
        MIDROLL_SEGMENTS, (3773.63, 3849.02), 3950.0)
    saved = _saved_marker(run)
    assert saved['start'] == pytest.approx(3773.39)
    assert 'reviewer_locked_start' not in saved


def test_splice_snap_keeps_reviewer_locked_end():
    ad = {'start': 10.0, 'end': 40.0, 'end_extended_by_content': True,
          'reviewer_locked_end': 40.0}
    events = [{'time': 42.0, 'type': 'deep_silence', 'depth_dbfs': -70.0}]
    snapped = processing._snap_completed_cut_tails_to_splice(
        's', 'e', [ad], [ad], [], _splice(events))
    assert snapped[0]['end'] == 40.0
    unlocked = dict(ad, reviewer_locked_end=None)
    snapped = processing._snap_completed_cut_tails_to_splice(
        's', 'e', [unlocked], [unlocked], [], _splice(events))
    assert snapped[0]['end'] == 42.0


def test_validator_keeps_reviewer_locked_edges():
    validator = AdValidator(episode_duration=100.0, segments=[])
    ad = {'start': 12.0, 'end': 80.0, 'reviewer_locked_start': 12.0,
          'reviewer_locked_end': 80.0,
          'dai_core_spans': [{'start': 10.0, 'end': 82.0}]}
    validator._clamp_boundaries([ad], ValidationResult(ads=[]))
    assert (ad['start'], ad['end']) == (12.0, 80.0)
    validator._extend_trailing_ad([ad], ValidationResult(ads=[]))
    assert ad['end'] == 80.0
    later = {'start': 80.5, 'end': 95.0}
    merged = validator._merge_close_ads([ad, later], ValidationResult(ads=[]))
    assert [(m['start'], m['end']) for m in merged] == [(12.0, 80.0), (80.5, 95.0)]


def test_touching_merge_past_locked_end_drops_end_lock():
    validator = AdValidator(episode_duration=100.0, segments=[])
    ad = {'start': 12.0, 'end': 80.0, 'reviewer_locked_start': 12.0,
          'reviewer_locked_end': 80.0}
    merged = validator._merge_close_ads(
        [ad, {'start': 80.0, 'end': 90.0}], ValidationResult(ads=[]))
    assert [(m['start'], m['end']) for m in merged] == [(12.0, 90.0)]
    assert 'reviewer_locked_end' not in merged[0]
    assert merged[0]['reviewer_locked_start'] == 12.0


def test_drop_stale_reviewer_locks_keeps_unmoved_and_narrowed_edges():
    marker = {'start': 12.0, 'end': 70.0, 'reviewer_locked_start': 12.03,
              'reviewer_locked_end': 80.0}
    drop_stale_reviewer_locks(marker)
    assert (marker['reviewer_locked_start'], marker['reviewer_locked_end']) == (12.03, 80.0)
    marker['start'] = 11.0
    drop_stale_reviewer_locks(marker)
    assert 'reviewer_locked_start' not in marker
    assert marker['reviewer_locked_end'] == 80.0


def test_merge_before_locked_start_drops_twin_start_lock():
    validator = AdValidator(episode_duration=100.0, segments=[])
    twin = {'start': 20.0, 'end': 80.0, 'reviewer_locked_start': 20.0,
            'reviewer_locked_end': 80.0}
    ad = {'start': 12.0, 'end': 80.0, '_orig_twin': twin}
    later = {'start': 80.0, 'end': 85.0, '_orig_twin': {'start': 10.0, 'end': 85.0}}
    validator._merge_close_ads([ad, later], ValidationResult(ads=[]))
    assert (twin['start'], twin['end']) == (10.0, 85.0)
    assert 'reviewer_locked_start' not in twin
    assert 'reviewer_locked_end' not in twin


def test_render_keeps_reviewer_locked_end_before_episode_end():
    ad = {'start': 12.0, 'end': 80.0, 'reviewer_locked_end': 80.0}
    cuts = AudioProcessor().compute_applied_cuts([ad], 100.0)
    assert [(c['start'], c['end']) for c in cuts] == [(12.0, 80.0)]


def test_human_widened_edge_is_no_longer_locked():
    assert reviewer_edge_locked({'end': 80.0, 'reviewer_locked_end': 80.0}, 'end')
    assert reviewer_edge_locked({'end': 70.0, 'reviewer_locked_end': 80.0}, 'end')
    assert not reviewer_edge_locked({'end': 85.0, 'reviewer_locked_end': 80.0}, 'end')
    assert not reviewer_edge_locked({'start': 5.0, 'reviewer_locked_start': 12.0}, 'start')


def _scan_supported(units, words, edge, new, old):
    """The whole-transcript scan the index replaced."""
    if edge == 'start':
        return _scan_supported(_negated(units), set(_negated(words)), 'end', -new, -old)
    matched = [hi for _, hi in words if abs(hi - new) <= EDGE_TOLERANCE]
    if new >= old - EDGE_TOLERANCE or not matched:
        return False
    at = max(matched)
    gap = min((lo for lo, _ in units if lo > at - EDGE_TOLERANCE), default=math.inf) - at
    crossed = any(lo < at - EDGE_TOLERANCE and hi > at + EDGE_TOLERANCE for lo, hi in units)
    cursor, widest = at, 0.0
    for lo, hi in sorted(u for u in units if at - EDGE_TOLERANCE < u[0] < old):
        widest, cursor = max(widest, lo - cursor), max(cursor, hi)
    widest = max(widest, old - cursor)
    return not crossed and gap >= _SUPPORTED_EDGE_GAP_S and widest < UNREVIEWABLE_GAP_SECONDS


def _scan_capped(units, words, independent, edge, proposed, floor):
    if edge == 'start':
        capped = _scan_capped(_negated(units), set(_negated(words)), _negated(independent),
                              'end', -proposed, -floor)
        return None if capped is None else -capped
    after = sorted(u for u in units if u[0] >= proposed - EDGE_TOLERANCE)
    straddling = [u for u in after if u[0] < floor < u[1]]
    if floor <= proposed or not straddling or straddling[0] not in words:
        return None
    word_lo = straddling[0][0]
    snap = (after[0] == straddling[0] and word_lo - proposed < _SUPPORTED_EDGE_GAP_S
            and any(abs(hi - proposed) <= EDGE_TOLERANCE for _, hi in units))
    capped = proposed if snap else max(word_lo, proposed)
    if any(min(hi, floor) - max(lo, capped) > 0 for lo, hi in independent):
        return None
    return capped


@pytest.mark.parametrize('seed', range(20))
def test_indexed_edge_checks_match_the_transcript_scan(seed):
    rng = random.Random(seed)
    segments, t = [], 0.0
    for _ in range(40):
        words = []
        for _ in range(rng.randint(0, 12)):
            t += rng.choice([0.05, 0.2, 0.4, 0.9])
            words.append({'start': round(t, 2), 'end': round(t + rng.uniform(0.1, 0.6), 2),
                          'word': 'w'})
            t = words[-1]['end']
        seg_end = round(t + rng.uniform(0.0, 2.0), 2)
        segments.append({'start': round(t - 5, 2), 'end': seg_end, 'words': words})
        t = seg_end
    index = TranscriptIndex(segments)
    units, words = _speech_units(segments), set(TimedWords(segments).spans)
    edges = [hi for _lo, hi in words] + [lo for lo, _hi in words]
    for _ in range(300):
        new = rng.choice(edges) if rng.random() < 0.6 else rng.uniform(0, t)
        old = new + rng.uniform(-3, 20) * rng.choice([1, -1])
        floor = new + rng.uniform(-2, 15) * rng.choice([1, -1])
        independent = [(x, x + rng.uniform(0.5, 5)) for x in
                       (rng.uniform(0, t) for _ in range(rng.randint(0, 3)))]
        for edge in ('start', 'end'):
            assert (_edge_transcript_supported(index, edge, new, old)
                    == _scan_supported(units, words, edge, new, old))
            assert (_speech_capped_floor(index, independent, edge, new, floor)
                    == _scan_capped(units, words, independent, edge, new, floor))


@pytest.mark.parametrize('seed', range(5))
def test_indexed_edge_checks_match_the_scan_at_the_exact_tolerance(seed):
    # Decimal timestamps put abs(edge - new) a hair over or under 0.05; the index must agree.
    rng = random.Random(seed)
    segments, t = [], 10.0
    for _ in range(30):
        words = []
        for _ in range(rng.randint(1, 10)):
            t = round(t + rng.choice([0.05, 0.1, 0.3, 0.6]), 2)
            end = round(t + rng.choice([0.05, 0.2, 0.4]), 2)
            words.append({'start': t, 'end': end, 'word': 'w'})
            t = end
        segments.append({'start': words[0]['start'], 'end': t, 'words': words})
    index = TranscriptIndex(segments)
    units, words = _speech_units(segments), set(TimedWords(segments).spans)
    edges = sorted({v for span in words for v in span})
    for _ in range(2000):
        new = round(rng.choice(edges) + rng.choice([-0.05, 0.05, -0.04, 0.06, 0.0]), 2)
        old = round(new + rng.choice([-1, 1]) * rng.randint(1, 1500) / 100, 2)
        floor = round(new + rng.choice([-1, 1]) * rng.randint(1, 800) / 100, 2)
        for edge in ('start', 'end'):
            assert (_edge_transcript_supported(index, edge, new, old)
                    == _scan_supported(units, words, edge, new, old))
            assert (_speech_capped_floor(index, [], edge, new, floor)
                    == _scan_capped(units, words, [], edge, new, floor))
