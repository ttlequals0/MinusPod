"""A transcript-supported reviewer trim may cross an unmeasured DAI region edge."""
import math
import random

import pytest

from tests.app_bootstrap import bootstrap

bootstrap('reviewer_dai_core_supported_trim_test_')

from ad_detector import AdDetector, dai_differential_ads, transcript_differential_ads
from ad_detector.boundaries import _merge_ad_pair, deduplicate_window_ads
from ad_reviewer import (AdReviewer, TranscriptIndex,
                         _SUPPORTED_EDGE_GAP_S, _edge_transcript_supported, _negated,
                         _speech_capped_floor, speech_units)
from ad_validator import AdValidator, ValidationResult
from audio_analysis.base import AudioAnalysisResult
from audio_processor import AudioProcessor
from main_app.pipeline import review, cuts
from main_app.pipeline import detect as detect_stage
from utils.markers import (DAI_PROBE_SPANS, EDGE_TOLERANCE, carve_fragment,
                           clip_dai_core_spans, drop_stale_reviewer_locks, merge_dai_core_spans,
                           reviewer_edge_locked, reviewer_independent_spans, TimedWords)
from tests.unit.marker_test_utils import _ad
from tests.unit.pipeline_test_utils import _run_pipeline
from tests.unit.reviewer_test_utils import _LLMResp, _reviewer, _worded



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
    monkeypatch.setattr(review, '_build_reviewer', lambda db, detector: reviewer)
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
    assert 'start floored by untranscribed audio, end floored by independent span' in lines[0]


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
    monkeypatch.setattr(review, '_build_reviewer', lambda db, detector: reviewer)
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
    monkeypatch.setattr(review, '_build_reviewer', lambda db, detector: reviewer)
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


def test_wordless_core_end_holds_against_a_word_supported_trim(monkeypatch):
    # The wordless stretch inside a measured core is ad audio, so the core end holds.
    run, cuts = _run_outro(monkeypatch, [{'start': 2821.31, 'end': 2825.31}])
    saved = _assert_bounds(run, cuts, 2802.09, 2961.04)
    assert saved['dai_core_spans'][-1]['end'] == pytest.approx(2961.04)
    AdValidator(episode_duration=3600.0, segments=[])._clamp_boundaries(
        [saved], ValidationResult(ads=[]))
    assert saved['end'] == pytest.approx(2961.04)


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


def test_wordless_core_start_holds_against_a_word_supported_trim(monkeypatch):
    run, cuts = _run_reviewed(
        monkeypatch, _midroll_marker([{'start': 3773.89, 'end': 3777.89}]),
        MIDROLL_SEGMENTS, (3773.63, 3849.02), 3950.0,
        events=[{'time': 3857.0, 'type': 'deep_silence', 'depth_dbfs': -70.0}])
    saved = _assert_bounds(run, cuts, 3773.39, 3849.02)
    assert 'reviewer_locked_start' not in saved
    assert saved['reviewer_locked_end'] == pytest.approx(3849.02)
    assert not saved.get('tail_completed')


def test_wordless_core_edges_hold_against_a_terminal_snap(monkeypatch):
    run, cuts = _run_reviewed(
        monkeypatch, _midroll_marker([{'start': 3773.89, 'end': 3777.89}], end=3850.5),
        MIDROLL_SEGMENTS[:2], (3773.63, 3849.02), 3850.5,
        events=[{'time': 3772.94, 'type': 'deep_silence', 'depth_dbfs': -70.0}])
    saved = _assert_bounds(run, cuts, 3772.94, 3850.5)
    assert 'reviewer_locked_start' not in saved


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
    snapped = cuts._snap_completed_cut_tails_to_splice(
        's', 'e', [ad], [ad], [], _splice(events))
    assert snapped[0]['end'] == 40.0
    unlocked = dict(ad, reviewer_locked_end=None)
    snapped = cuts._snap_completed_cut_tails_to_splice(
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
    return not crossed and gap >= _SUPPORTED_EDGE_GAP_S


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
    units, words = speech_units(segments), set(TimedWords(segments).spans)
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
    units, words = speech_units(segments), set(TimedWords(segments).spans)
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


def _dense_words(lo, hi, step=0.5):
    """Contiguous words with 0.05 s gaps, as in fast produced speech."""
    count = int((hi - lo) / step)
    return [{'word': 'w', 'start': lo + i * step, 'end': lo + i * step + step - 0.05}
            for i in range(count)]


def _intro_seam_segments():
    # The promo's last word ends 121.29 and the show intro follows with no pause.
    tail = [{'word': 'podcast', 'start': 120.62, 'end': 121.29},
            {'word': 'welcome', 'start': 121.29, 'end': 122.14}]
    return [_worded_words(0.0, 120.62, _dense_words(0.0, 120.62)),
            _worded_words(120.62, 122.14, tail),
            _worded_words(122.14, 160.0, _dense_words(122.14, 160.0))]


def _worded_words(start, end, words):
    return {'start': start, 'end': end, 'text': 'text', 'words': words}


def _seam_marker():
    return {
        'start': 0.05, 'end': 157.85, 'confidence': 0.95,
        'detection_stage': 'dai_differential', 'category': 'cross_promo',
        'dai_core_spans': [{'start': 4.07, 'end': 157.85}],
        DAI_PROBE_SPANS: [{'start': 4.57, 'end': 8.57}, {'start': 91.08, 'end': 95.08}],
        'transcript_span': {'start': 0.05, 'end': 121.29, 'offset_confirmed': True},
        'merged_distinct_ads': True,
        'merged_member_spans': [
            {'start': 0.05, 'end': 121.29, 'stage': 'transcript_differential'},
            {'start': 0.05, 'end': 122.38, 'stage': 'claude', 'precise_start': True,
             'precise_end': True}],
        'merged_protected_start': 0.05, 'merged_protected_end': 122.38,
    }


def test_trim_to_transcript_differential_edge_crosses_unprobed_block_tail():
    # The audio block ends 157.85 but no probe sits past 95.08; the word-aligned
    # transcript edge, agreeing with the reviewer, outranks the block's tail.
    reviewer = AdReviewer.__new__(AdReviewer)
    bounds = reviewer._clamp_proposed_bounds(
        _seam_marker(), 0.05, 121.29, 0.05, 157.85, 600, 'slug', 'ep',
        segments=_intro_seam_segments())
    assert bounds == pytest.approx((0.05, 121.29))


def test_trim_off_transcript_differential_edge_keeps_block_floor():
    reviewer = AdReviewer.__new__(AdReviewer)
    bounds = reviewer._clamp_proposed_bounds(
        _seam_marker(), 0.05, 121.6, 0.05, 157.85, 600, 'slug', 'ep',
        segments=_intro_seam_segments())
    assert bounds[1] > 150.0


def _clamp(marker, start, end, segments):
    reviewer = AdReviewer.__new__(AdReviewer)
    return reviewer._clamp_proposed_bounds(
        marker, start, end, 0.05, 157.85, 600, 'slug', 'ep', segments=segments)


def test_trim_off_unconfirmed_differential_edge_keeps_block_floor():
    marker = _seam_marker()
    marker['transcript_span']['offset_confirmed'] = False
    bounds = _clamp(marker, 0.05, 121.29, _intro_seam_segments())
    assert bounds[1] > 150.0


def test_outward_move_to_differential_edge_is_not_accepted():
    index = TranscriptIndex(_intro_seam_segments())
    marker = _seam_marker()
    # end edge: 121.29 is outward of an original end at 100.0; start edge likewise mirrored
    assert not _edge_transcript_supported(index, 'end', 121.29, 100.0, marker)
    marker['transcript_span']['start'] = 100.0
    assert not _edge_transcript_supported(index, 'start', 100.0, 130.0, marker)
    assert _edge_transcript_supported(index, 'end', 121.29, 157.85, marker)


def test_inward_start_trim_aligned_with_differential_start_is_supported():
    # mirror of the end case, mid-episode: speech before the span start matched the publisher text.
    marker = {
        'start': 200.0, 'end': 300.0, 'confidence': 0.95,
        'detection_stage': 'dai_differential', 'category': 'sponsor',
        'dai_core_spans': [{'start': 200.0, 'end': 300.0}],
        DAI_PROBE_SPANS: [{'start': 280.0, 'end': 284.0}, {'start': 290.0, 'end': 294.0}],
        'transcript_span': {'start': 236.0, 'end': 300.0, 'offset_confirmed': True},
    }
    segs = [_worded_words(0.0, 400.0, _dense_words(0.0, 400.0))]

    def start(m, new):
        reviewer = AdReviewer.__new__(AdReviewer)
        return reviewer._clamp_proposed_bounds(
            m, new, 300.0, 200.0, 300.0, 600, 'slug', 'ep', segments=segs)[0]

    assert start(marker, 236.0) == pytest.approx(236.0)
    marker['transcript_span']['offset_confirmed'] = False
    assert start(marker, 236.0) < 230.0


def _speech(lo, hi):
    return _worded_words(lo, hi, _dense_words(lo, hi))


def _preroll_segments():
    # Ad words end 61.58, a 9.9 s wordless stretch, then the show's own speech.
    return [_speech(0.24, 61.58), _speech(71.5, 95.4)]


def _preroll_marker(offset_confirmed=True):
    return {
        'start': 0.0, 'end': 90.82, 'confidence': 0.95,
        'detection_stage': 'dai_differential', 'category': 'sponsor',
        'dai_core_spans': [{'start': 0.0, 'end': 90.4}],
        DAI_PROBE_SPANS: [{'start': 0.5, 'end': 4.5}],
        'transcript_span': {'start': 0.0, 'end': 61.58, 'offset_confirmed': offset_confirmed},
        'merged_distinct_ads': True,
        'merged_member_spans': [
            {'start': 0.0, 'end': 61.58, 'stage': 'transcript_differential'},
            {'start': 0.24, 'end': 61.58, 'stage': 'claude', 'confidence': 0.98,
             'precise_start': True, 'precise_end': True}],
        'merged_protected_start': 0.24, 'merged_protected_end': 61.58,
    }


def _preroll_end(marker, segments=None):
    reviewer = AdReviewer.__new__(AdReviewer)
    return reviewer._clamp_proposed_bounds(
        marker, 0.0, 71.5, 0.0, 90.82, 600, 'slug', 'ep',
        segments=segments or _preroll_segments())[1]


def test_end_trim_past_wordless_gap_keeps_proposal_when_two_sources_pin_aligned_edge():
    assert _preroll_end(_preroll_marker()) == pytest.approx(71.5)


def test_end_trim_without_precise_member_keeps_core_floor():
    marker = _preroll_marker()
    marker['merged_member_spans'][1].pop('precise_end')
    assert _preroll_end(marker) > 85.0


def test_end_trim_with_disagreeing_precise_member_keeps_core_floor():
    marker = _preroll_marker()
    marker['merged_member_spans'][1]['end'] = 62.0
    assert _preroll_end(marker) > 85.0


def test_end_trim_with_words_in_gap_keeps_core_floor():
    segments = [_speech(0.24, 61.58), _speech(64.0, 66.0), _speech(71.5, 95.4)]
    assert _preroll_end(_preroll_marker(), segments) > 85.0


def test_end_trim_with_unconfirmed_offset_keeps_core_floor():
    assert _preroll_end(_preroll_marker(offset_confirmed=False)) > 85.0


def test_start_trim_past_wordless_gap_keeps_proposal_when_two_sources_pin_aligned_edge():
    marker = {
        'start': 0.0, 'end': 100.0, 'confidence': 0.95,
        'detection_stage': 'dai_differential', 'category': 'sponsor',
        'dai_core_spans': [{'start': 0.0, 'end': 100.0}],
        DAI_PROBE_SPANS: [{'start': 95.0, 'end': 99.0}],
        'transcript_span': {'start': 38.0, 'end': 100.0, 'offset_confirmed': True},
        'merged_distinct_ads': True,
        'merged_member_spans': [
            {'start': 38.0, 'end': 100.0, 'stage': 'transcript_differential'},
            {'start': 38.0, 'end': 99.8, 'stage': 'claude', 'confidence': 0.98,
             'precise_start': True, 'precise_end': True}],
        'merged_protected_start': 38.0, 'merged_protected_end': 99.8,
    }
    segments = [_speech(0.0, 28.0), _speech(38.0, 105.0)]
    reviewer = AdReviewer.__new__(AdReviewer)
    bounds = reviewer._clamp_proposed_bounds(
        marker, 28.0, 100.0, 0.0, 100.0, 600, 'slug', 'ep', segments=segments)
    assert bounds[0] == pytest.approx(27.95)


def _gap_marker():
    return {'start': 0.0, 'end': 90.4, 'confidence': 0.95, 'detection_stage': 'dai_differential',
            'category': 'sponsor', 'sponsor': None, 'reason': 'Dynamically inserted',
            'dai_core_spans': [{'start': 0.0, 'end': 90.4}],
            DAI_PROBE_SPANS: [{'start': 0.5, 'end': 4.5}],
            'differential_uncorroborated': True, 'held_for_review': True,
            'hold_reason': 'differential_uncorroborated'}


def test_merge_of_released_gap_marker_into_dai_marker_keeps_transcript_span():
    # Two-step merge (released gap first), not the production order; covers the detector fold only.
    detector = AdDetector.__new__(AdDetector)
    _, gap, llm, _ = _preroll_detections()
    segs = [_speech(0.24, 61.58)]
    released = detector._merge_detection_results([gap[0], llm], segments=segs)
    (merged,) = detector._merge_detection_results([_gap_marker(), *released], segments=segs)
    assert merged['detection_stage'] == 'dai_differential'
    assert merged['transcript_span']['offset_confirmed'] is True
    # The LLM member's edges are quote-aligned in a real run.
    merged['merged_member_spans'][1].update(precise_start=True, precise_end=True)
    assert _preroll_end(merged) == pytest.approx(71.5)


_SPAN = {'start': 1.0, 'end': 60.0, 'offset_confirmed': True}


def _fold_pair():
    first = {'start': 0.0, 'end': 60.0, 'confidence': 0.9, 'detection_stage': 'claude',
             'category': 'sponsor', 'sponsor': 'Acme', 'reason': 'Acme read'}
    return first, dict(first, start=1.0, transcript_span=dict(_SPAN))


def _fold_merge_ad_pair():
    first, second = _fold_pair()
    _merge_ad_pair(first, second)
    return first


def _fold_validator():
    first, second = _fold_pair()
    second.update(start=60.5, end=100.0)
    (merged,) = AdValidator(episode_duration=600.0, segments=[])._merge_close_ads(
        [first, second], ValidationResult(ads=[]))
    return merged


def _fold_detector_main_loop():
    (merged,) = AdDetector.__new__(AdDetector)._merge_detection_results(list(_fold_pair()))
    return merged


def _fold_detector_duplicates():
    (merged,) = AdDetector.__new__(AdDetector)._merge_overlapping_accepted_duplicates(
        list(_fold_pair()))
    return merged


@pytest.mark.parametrize('fold', [_fold_merge_ad_pair, _fold_validator,
                                  _fold_detector_main_loop, _fold_detector_duplicates])
def test_fold_keeps_later_marker_transcript_span(fold):
    assert fold()['transcript_span']['offset_confirmed'] is True


@pytest.mark.parametrize('stage', ['text_pattern', 'claude'])
def test_trim_to_carried_span_edge_keeps_later_folded_ad(stage):
    marker = _preroll_marker()
    marker['merged_member_spans'].append(
        {'start': 75.0, 'end': 85.0, 'stage': stage, 'confidence': 0.95,
         'precise_start': True, 'precise_end': True})
    marker['merged_protected_end'] = 85.0
    segments = [_speech(0.24, 61.58), _speech(75.0, 85.0), _speech(86.0, 95.4)]
    reviewer = AdReviewer.__new__(AdReviewer)
    end = reviewer._clamp_proposed_bounds(
        marker, 0.0, 61.58, 0.0, 90.82, 600, 'slug', 'ep', segments=segments)[1]
    assert end >= 85.0


def test_window_dedup_and_twin_merge_do_not_carry_transcript_span():
    span = {'start': 1.0, 'end': 60.0, 'offset_confirmed': True}
    first = {'start': 0.0, 'end': 60.0, 'confidence': 0.9, 'detection_stage': 'claude',
             'category': 'sponsor', 'sponsor': 'Acme'}
    (deduped,) = deduplicate_window_ads([first, dict(first, start=1.0, transcript_span=span)])
    assert 'transcript_span' not in deduped
    twin = {'start': 20.0, 'end': 80.0}
    ad = {'start': 12.0, 'end': 80.0, '_orig_twin': twin}
    later = {'start': 80.0, 'end': 85.0, '_orig_twin': {'start': 10.0, 'end': 85.0,
                                                        'transcript_span': span}}
    AdValidator(episode_duration=100.0, segments=[])._merge_close_ads(
        [ad, later], ValidationResult(ads=[]))
    assert 'transcript_span' not in twin


def _preroll_detections():
    """Differential region, offset-confirmed transcript gap and aligned LLM ad for the pre-roll."""
    dd = dai_differential_ads(
        {'regions': [{'kind': 'differential', 'corr': 0.04, 'start_s': 0.0, 'end_s': 90.4}]}, [])
    gap = transcript_differential_ads([{'start': 0.24, 'end': 61.58, 'offset_confirmed': True}])
    llm = {'start': 0.24, 'end': 61.58, 'confidence': 0.98, 'detection_stage': 'claude',
           'category': 'sponsor', 'sponsor': None,
           'reason': 'Based on transcript: Visit orderlyacme.com for details.'}
    segments = _preroll_segments()
    segments[0] = dict(segments[0], text='Visit orderlyacme.com for 50% off.')
    return dd, gap, llm, segments


def _production_preroll_marker():
    """The opening marker as the detector merge then boundary refinement build it."""
    dd, gap, llm, segments = _preroll_detections()
    merged = AdDetector.__new__(AdDetector)._merge_detection_results(
        sorted(dd + gap + [llm], key=lambda a: a['start']), segments=segments)
    (marker,) = detect_stage._refine_boundaries(merged, segments)
    for member in marker['merged_member_spans']:
        if member['stage'] == 'claude':
            member.update(precise_start=True, precise_end=True)
    return marker


def test_refined_preroll_marker_keeps_transcript_span():
    marker = _production_preroll_marker()
    assert (marker.get('transcript_span') or {}).get('offset_confirmed') is True


# 61.6 lands at 71.5: the 9.9 s wordless gap past the ad is held in the cut by the untranscribed-audio rule.
@pytest.mark.parametrize('proposal', [61.6, 71.5])
def test_production_preroll_trim_is_not_floored_to_core_end(proposal):
    marker = _production_preroll_marker()
    reviewer = AdReviewer.__new__(AdReviewer)
    end = reviewer._clamp_proposed_bounds(
        marker, 0.0, proposal, 0.0, 90.4, 600, 'slug', 'ep',
        segments=_preroll_segments())[1]
    assert end == pytest.approx(71.5)
