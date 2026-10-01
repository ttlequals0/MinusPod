"""A reviewer trim may not pull an edge away from the labelled boundary cue it was snapped to."""
from tests.app_bootstrap import bootstrap

bootstrap('reviewer_boundary_cue_floor_test_')

from main_app import processing
from utils.markers import DAI_PROBE_SPANS
from tests.unit.pipeline_test_utils import _run_pipeline
from tests.unit.reviewer_test_utils import _LLMResp, _reviewer, _worded



SEGMENTS = [
    _worded(1790.0, 1815.5, 'and that is all for the first half of the show'),
    _worded(1816.2, 1895.0, 'This break is brought to you by Acme, the tool shop for every home.'),
    _worded(1899.0, 1950.0, 'Okay we are back and talking about gardens again today'),
]


def _snap(source='template', cue_start=1897.65, cue_end=1898.4):
    return {'original': 1897.68, 'cue_start': cue_start, 'cue_end': cue_end,
            'cue_confidence': 0.9, 'shift_seconds': -0.08, 'template_id': 3,
            'label': 'ad-break boundary', 'source': source, 'cue_type': 'boundary'}


def _marker(**extra):
    marker = {'start': 1816.0, 'end': 1897.6, 'confidence': 0.95, 'detection_stage': 'claude',
              'category': 'sponsor', 'sponsor': 'Acme', 'reason': 'Acme sponsor read',
              'dai_core_spans': [{'start': 1816.0, 'end': 1897.6}],
              DAI_PROBE_SPANS: [{'start': 1816.5, 'end': 1820.5}],
              'cue_snap': {'end': _snap()}}
    marker.update(extra)
    return marker


def _clamp(marker, start, end):
    return _reviewer()._clamp_proposed_bounds(
        marker, start, end, marker['start'], marker['end'], 60, 'show', 'ep1',
        segments=SEGMENTS)


def test_end_stays_at_the_snapped_boundary_cue(caplog):
    with caplog.at_level('INFO', logger='ad_reviewer'):
        assert _clamp(_marker(), 1816.0, 1895.0) == (1816.0, 1897.6)
    lines = [r.getMessage() for r in caplog.records if 'Reviewer trim' in r.getMessage()]
    assert len(lines) == 1
    assert 'stopped at boundary cue: 1816.0-1895.0 -> 1816.0-1897.6' in lines[0]


def test_a_generic_cue_snap_keeps_the_old_behaviour():
    marker = _marker(cue_snap={'end': _snap(source='spectral')})
    assert _clamp(marker, 1816.0, 1895.0) == (1816.0, 1895.0)


def test_start_edge_mirror():
    marker = _marker(cue_snap={'start': _snap(cue_start=1815.2, cue_end=1815.95)})
    assert _clamp(marker, 1816.2, 1897.6) == (1816.0, 1897.6)


def test_pipeline_renders_the_cut_to_the_cue(monkeypatch):
    reviewer = _reviewer()
    monkeypatch.setattr(processing, '_build_reviewer', lambda db, detector: reviewer)
    body = '[{"start": 1816.0, "end": 1895.0, "is_ad": true, "confidence": 0.94}]'
    monkeypatch.setattr('ad_reviewer.call_llm_for_window',
                        lambda **kwargs: (_LLMResp(body), None))
    run = _run_pipeline([_marker()], {'sponsor': 'remove'}, segments=SEGMENTS,
                        real_refine_reviewer=True, duration=2400.0)
    assert run['result'] is True
    cuts = run['local_ap'].process_episode.call_args.args[1]
    assert [round(c['end'], 2) for c in cuts] == [1897.6]


def test_recovered_hold_bounds_respect_the_cue():
    import ad_reviewer
    index = ad_reviewer.TranscriptIndex(SEGMENTS)
    assert ad_reviewer._hold_inward_limits(
        _marker(), 1816.0, 1895.0, 1816.0, 1897.6, index, []) == (1816.0, 1897.6)
