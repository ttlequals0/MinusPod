"""A transcript-supported reviewer trim may cross an unmeasured DAI region edge."""
from dataclasses import dataclass

import pytest

from tests.app_bootstrap import bootstrap

bootstrap('reviewer_dai_core_supported_trim_test_')

from ad_detector import dai_differential_ads
from ad_reviewer import AdReviewer, _edge_transcript_supported
from ad_validator import AdValidator, ValidationResult
from main_app import processing
from utils.markers import (DAI_PROBE_SPANS, carve_fragment, clip_dai_core_spans,
                           dai_probe_spans, merge_dai_core_spans,
                           reviewer_independent_spans)
from tests.unit.test_keep_bypass import _run_pipeline
from tests.unit.test_processing_boundary_safety import _reviewer


@dataclass
class _LLMResp:
    content: str
    model: str = 'test-model'


SEGMENTS = [
    {'start': 0.68, 'end': 20.42, 'text': 'This episode is brought to you by Acme.'},
    {'start': 20.68, 'end': 49.0, 'text': 'Acme makes the best widgets around.'},
    {'start': 49.28, 'end': 58.2, 'text': 'Visit acme.example, Acme delivers.'},
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


def test_fingerprint_match_over_region_floors_supported_end(monkeypatch):
    member = {'start': 0.0, 'end': 73.2, 'stage': 'fingerprint',
              'fingerprint_match_start': 0.0, 'fingerprint_match_end': 73.2}
    _, cuts = _run(monkeypatch, _marker(
        merged_member_spans=[member], merged_protected_start=0.0,
        merged_protected_end=73.2))
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


def test_unsupported_end_keeps_core_clamp(monkeypatch):
    _, cuts = _run(monkeypatch, _marker(), end=60.0)
    assert cuts == [(0.0, 73.2)]


def test_end_edge_supported_by_segment_end_and_gap():
    assert _edge_transcript_supported(SEGMENTS, 'end', 58.2, 73.2)
    # 3.2 is no segment edge; 20.42 is followed 0.26 s later by speech.
    assert not _edge_transcript_supported(SEGMENTS, 'start', 3.2, 0.0)
    assert not _edge_transcript_supported(SEGMENTS, 'end', 20.42, 73.2)
    # Not inward, or no speech between the new and old edge.
    assert not _edge_transcript_supported(SEGMENTS, 'end', 58.2, 58.2)
    assert not _edge_transcript_supported(SEGMENTS, 'end', 58.2, 60.0)
    # Rounded to one decimal, as the prompt shows it.
    rounded = [dict(SEGMENTS[2], end=58.23), SEGMENTS[3]]
    assert _edge_transcript_supported(rounded, 'end', 58.2, 73.2)


def test_start_edge_supported_mirrors_end():
    segments = [{'start': 0.0, 'end': 10.0, 'text': 'Show talk.'},
                {'start': 11.0, 'end': 40.0, 'text': 'Brought to you by Acme.'}]
    assert _edge_transcript_supported(segments, 'start', 11.0, 0.0)
    assert not _edge_transcript_supported(segments, 'start', 11.0, 10.5)
    close = [dict(segments[0], end=10.9), segments[1]]
    assert not _edge_transcript_supported(close, 'start', 11.0, 0.0)


def test_word_edges_support_a_trim_inside_a_segment():
    segments = [{'start': 0.0, 'end': 30.0, 'text': 'Acme rocks. Welcome back.',
                 'words': [{'word': 'Acme', 'start': 0.0, 'end': 5.0},
                           {'word': 'rocks.', 'start': 5.0, 'end': 12.0},
                           {'word': 'Welcome', 'start': 14.0, 'end': 20.0},
                           {'word': 'back.', 'start': 20.0, 'end': 30.0}]}]
    assert _edge_transcript_supported(segments, 'end', 12.0, 30.0)


def test_independent_spans_cover_measured_evidence():
    marker = _marker(cue_pair={'start': {'cue_end': 1.0},
                               'end': {'cue_start': 70.0}})
    spans = reviewer_independent_spans(marker)
    assert (23.6, 29.82) in spans
    assert (0.5, 4.5) in spans
    assert (1.05, 69.95) in spans
    assert (0.0, 73.2) not in spans
    confirmed = _marker(validation={'user_confirmed': True})
    assert (0.0, 73.2) in reviewer_independent_spans(confirmed)


def test_differential_ads_record_each_block_probe_window():
    regions = [
        {'start_s': 0.0, 'end_s': 30.0, 'kind': 'differential', 'corr': 0.1},
        {'start_s': 30.0, 'end_s': 32.0, 'kind': 'differential', 'corr': 0.1},
    ]
    ads = dai_differential_ads({'regions': regions}, [], [(0.0, 32.0)])
    assert ads[0][DAI_PROBE_SPANS] == [{'start': 0.5, 'end': 4.5},
                                       {'start': 30.0, 'end': 32.0}]


def test_legacy_marker_falls_back_to_leading_probe_window():
    marker = _marker()
    del marker[DAI_PROBE_SPANS]
    assert dai_probe_spans(marker) == [(0.0, 4.5)]
    short = {'dai_core_spans': [{'start': 10.0, 'end': 12.0}]}
    assert dai_probe_spans(short) == [(10.0, 12.0)]


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
