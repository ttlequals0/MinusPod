"""Replay saved production episodes through the real recut, validator and
pass-2 reconciliation code. Fixtures live outside the repo; see README.md."""
import copy
import inspect
import json

import pytest

from tests.prod_replay.conftest import FIXTURES_ENV, fixtures_dir, load_episode, load_json

ROOT = fixtures_dir()
if ROOT is None:
    pytest.skip(f'{FIXTURES_ENV} not set', allow_module_level=True)

from tests.app_bootstrap import bootstrap  # noqa: E402

bootstrap('prod_replay_')

from audio_processor import get_replacement_duration  # noqa: E402
from main_app import processing  # noqa: E402
from main_app import verification_reconciliation as vr  # noqa: E402
from utils import markers as marker_utils  # noqa: E402
from utils.time import adjust_timestamp, merge_cut_spans, overlap_seconds  # noqa: E402
from verification_pass import _build_timestamp_map, _map_to_original  # noqa: E402

EXPECT = load_json(ROOT / 'expectations.json')
BASELINE = load_json(ROOT / 'baseline.json')
MIN_CONF = EXPECT['min_cut_confidence']
ACTIONS = EXPECT['segment_category_actions']
EPISODES = list(EXPECT['episodes'])
# Pipeline-derived state stripped before re-running validation.
TRANSIENT_KEYS = ('validation', 'was_cut', 'held_for_review', 'hold_reason',
                  'reviewer_locked_start', 'reviewer_locked_end', 'source',
                  'pass2_corroborated', 'pass2_corroborated_span')

PENDING_17 = pytest.mark.xfail(strict=True, reason='pending Task 17 (audit item 3)')
PENDING_19 = pytest.mark.xfail(strict=True, reason='pending Task 19 (audit item 2)')
PENDING_20 = pytest.mark.xfail(strict=True, reason='pending Task 20 (audit item 4)')


def _findings(item, kind=None):
    params = []
    for n, eid in enumerate(EPISODES):
        for k, f in enumerate(EXPECT['episodes'][eid]['findings']):
            if f['item'] == item and (kind is None or f['kind'] == kind):
                params.append(pytest.param(eid, f, id=f'ep{n:02d}-f{k:02d}'))
    return params


def _ep_params():
    return [pytest.param(eid, id=f'ep{n:02d}') for n, eid in enumerate(EPISODES)]


def _dump(out_dir, name, payload):
    (out_dir / name).write_text(json.dumps(payload, indent=1, default=str))


def _spans(obj):
    """Every dict with numeric start/end found in obj, recursively."""
    if isinstance(obj, dict):
        if isinstance(obj.get('start'), (int, float)) and isinstance(obj.get('end'), (int, float)):
            yield obj
        for v in obj.values():
            if isinstance(v, (dict, list, tuple)):
                yield from _spans(v)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            yield from _spans(v)


def _stub_db(monkeypatch, ep):
    """Serve one saved episode through the db calls the recut path makes."""
    db = processing.db
    corrections = ep['corrections']

    def _bounds(c):
        b = c.get('original_bounds') or {}
        return {'start': float(b['start']), 'end': float(b['end'])} if b else None

    def _confirmed(_pid, _eid):
        out = []
        for c in corrections:
            if c['correction_type'] not in ('confirm', 'boundary_adjustment'):
                continue
            b = _bounds(c)
            span = c.get('corrected_bounds')
            if not b or (c['correction_type'] == 'boundary_adjustment' and not span):
                continue
            if span:
                b['confirmed_span'] = {'start': float(span['start']), 'end': float(span['end'])}
            b['correction_type'] = c['correction_type']
            if c.get('auto_filed'):
                b['auto_filed'] = True
            out.append(b)
        return out

    stored_dd = json.dumps(ep['dai_differential']) if ep.get('dai_differential') else None
    monkeypatch.setattr(db, 'get_episode',
                        lambda s, e: {'ad_markers_json': json.dumps(ep['markers'])})
    monkeypatch.setattr(db, 'get_podcast_by_slug', lambda s: {'id': 1})
    monkeypatch.setattr(db, 'get_episode_corrections', lambda p, e: corrections)
    monkeypatch.setattr(db, 'get_false_positive_corrections',
                        lambda p, e: [b for c in corrections
                                      if c['correction_type'] == 'false_positive'
                                      and (b := _bounds(c))])
    monkeypatch.setattr(db, 'get_confirmed_corrections', _confirmed)
    monkeypatch.setattr(db, 'get_podcast_cue_settings_overrides', lambda p: {})
    monkeypatch.setattr(db, 'get_episode_audio_analysis', lambda s, e: None)
    monkeypatch.setattr(db, 'get_episode_dai_differential', lambda s, e: stored_dd)
    monkeypatch.setattr(db, 'resolve_segment_actions', lambda s: dict(ACTIONS))


def _recut(monkeypatch, ep):
    _stub_db(monkeypatch, ep)
    return processing._build_recut_ad_list(
        'replay', 'replay', ep['segments'], ep['duration'], ep['description'],
        MIN_CONF, podcast_id=1, segment_actions=dict(ACTIONS))


def _pass1_cuts(ep):
    """Pass-1 render spans: saved cuts minus markers only a later recut cut
    (auto-approved holds, restored rejects, released inconclusive holds)."""
    auto = ep['auto_approved_spans']
    recut_only = ('reject', 'inconclusive') if len(ep['ffmpeg_span_counts']) > 1 else ('reject',)
    cuts = []
    for m in ep['markers']:
        if not m.get('was_cut') or m.get('detection_stage') == 'verification':
            continue
        if m.get('reviewer_verdict') in recut_only:
            continue
        if any(abs(m['start'] - a) < 0.1 and abs(m['end'] - b) < 0.1 for a, b in auto):
            continue
        cuts.append({'start': m['start'], 'end': m['end']})
    merged = [{'start': s, 'end': e} for s, e, *_ in merge_cut_spans(cuts)]
    first_render = (ep['ffmpeg_span_counts'] or [None])[0]
    return merged, first_render


def _pass1_holds(ep):
    holds = []
    for m in ep['markers']:
        if m.get('detection_stage') == 'verification':
            continue
        if m.get('hold_reason') == 'verification_kept_conflict':
            continue
        if m.get('hold_reason') or m.get('reviewer_verdict') == 'inconclusive':
            h = copy.deepcopy(m)
            h['hold_reason'] = m.get('hold_reason') or 'reviewer_inconclusive_bounds'
            h['held_for_review'] = True
            h['was_cut'] = False
            holds.append(h)
    return holds


def _call(fn, **kwargs):
    params = inspect.signature(fn).parameters
    return fn(**{k: v for k, v in kwargs.items() if k in params})


# (a) recut replay -----------------------------------------------------------

@pytest.mark.parametrize('eid,finding', _findings(1))
def test_recut_keeps_reviewer_reject_uncut(monkeypatch, eid, finding):
    ep = load_episode(ROOT, eid)
    ads_to_remove, all_ads = _recut(monkeypatch, ep)
    lo, hi = finding['span']
    cut_hits = [a for a in ads_to_remove if overlap_seconds(a['start'], a['end'], lo, hi) > 0.5]
    assert not cut_hits, f'reject span {lo}-{hi} is in ads_to_remove'
    same = [a for a in all_ads if abs(a['start'] - lo) < 0.5 and abs(a['end'] - hi) < 0.5]
    assert same, 'reject marker missing from the recut marker list'
    assert all(not a.get('was_cut') for a in same)


@pytest.mark.parametrize('eid', _ep_params())
def test_recut_replay_records_cut_list(monkeypatch, replay_out, eid):
    ep = load_episode(ROOT, eid)
    ads_to_remove, all_ads = _recut(monkeypatch, ep)
    _dump(replay_out, f'recut_{eid}.json', {
        'ads_to_remove': [[a['start'], a['end']] for a in ads_to_remove],
        'markers': [{'start': a['start'], 'end': a['end'], 'was_cut': a.get('was_cut'),
                     'decision': a.get('validation', {}).get('decision'),
                     'hold_reason': a.get('hold_reason'),
                     'flags': a.get('validation', {}).get('flags')} for a in all_ads],
    })
    # Replaying the saved state must not add cuts over any reviewer reject.
    rejects = [m for m in ep['markers']
               if m.get('reviewer_verdict') == 'reject' and m.get('source') == 'reviewer']
    for m in rejects:
        assert not any(overlap_seconds(a['start'], a['end'], m['start'], m['end']) > 0.5
                       for a in ads_to_remove)


# (b) validator replay ---------------------------------------------------------

def _pre_validation(marker):
    m = {k: v for k, v in marker.items()
         if k not in TRANSIENT_KEYS and not k.startswith('reviewer_')}
    # Undo a reviewer move so validation sees the detector's bounds.
    if marker.get('reviewer_original_start') is not None:
        m['start'] = marker['reviewer_original_start']
        m['end'] = marker['reviewer_original_end']
    return m


def _validate(eid):
    ep = load_episode(ROOT, eid)
    ads = [_pre_validation(m) for m in ep['markers']
           if m.get('detection_stage') != 'verification']
    fp = [{'start': c['original_bounds']['start'], 'end': c['original_bounds']['end']}
          for c in ep['corrections'] if c['correction_type'] == 'false_positive']
    validator = processing._build_validator(
        ep['duration'], ep['segments'], ep['description'],
        false_positive_corrections=fp, min_cut_confidence=MIN_CONF,
        max_ad_duration_override=None, cue_gate_enabled=False, podcast_id=None)
    audio = {'dai_differential': ep['dai_differential']} if ep.get('dai_differential') else None
    return validator.validate(ads, audio_analysis=audio, actions_map=dict(ACTIONS)).ads


@pytest.mark.parametrize('eid', _ep_params())
def test_validator_replay_records_decisions(replay_out, eid):
    result = _validate(eid)
    _dump(replay_out, f'validator_{eid}.json', [{
        'start': a['start'], 'end': a['end'],
        'decision': a.get('validation', {}).get('decision'),
        'hold_reason': a.get('hold_reason'),
        'category': a.get('category'),
        'measured_member_spans': marker_utils.measured_member_spans(a, MIN_CONF),
    } for a in result])
    assert result


@PENDING_20
@pytest.mark.parametrize('eid,finding', _findings(4))
def test_validator_exposes_measured_end_not_envelope(eid, finding):
    result = _validate(eid)
    lo, hi = finding['span']
    ad = max(result, key=lambda a: overlap_seconds(a['start'], a['end'], lo, hi))
    assert overlap_seconds(ad['start'], ad['end'], lo, hi) > 0
    support = marker_utils.edge_support(ad, 'end', MIN_CONF)
    assert support['measured'] == pytest.approx(finding['measured_end'], abs=0.5)
    assert support['envelope'] >= support['measured']


# (c) pass-2 reconciliation replay -------------------------------------------

def _processed_twin(orig, cuts):
    beep = get_replacement_duration()
    return dict(orig, start=adjust_timestamp(orig['start'], cuts, beep),
                end=adjust_timestamp(orig['end'], cuts, beep))


def _pass1_or_skip(ep):
    cuts, first_render = _pass1_cuts(ep)
    if first_render is None or len(cuts) != first_render:
        pytest.skip(f'pass-1 cut list not reconstructable ({len(cuts)} spans vs '
                    f'{first_render} rendered)')
    return cuts


@pytest.mark.parametrize('eid,finding', _findings(2))
def test_gate_replay_records_hold_overlap(replay_out, eid, finding):
    ep = load_episode(ROOT, eid)
    cuts = _pass1_or_skip(ep)
    holds = _pass1_holds(ep)
    lo, hi = finding['finding_original']
    if not any(h['start'] < hi and h['end'] > lo for h in holds):
        pytest.skip('no saved pass-1 hold overlaps the logged finding')
    # Confidence is not logged for dropped findings; the drop branch ignores it.
    orig = {'start': lo, 'end': hi, 'confidence': 0.95, 'category': 'sponsor',
            'detection_stage': 'verification', 'validation': {'decision': 'ACCEPT'}}
    before = copy.deepcopy(holds)
    result = _call(vr._gate_verification_ads_by_confidence,
                   verification_ads_processed=[_processed_twin(orig, cuts)],
                   verification_ads_original=[orig], min_cut_confidence=MIN_CONF,
                   pass1_held_markers=holds, pass1_cuts=cuts)
    _dump(replay_out, f"gate_{eid}_{int(lo)}.json",
          {'result': result, 'holds_after': holds, 'holds_before': before})


@PENDING_19
@pytest.mark.parametrize('eid,finding', _findings(2))
def test_gate_reviews_pass2_span_inside_hold(eid, finding):
    ep = load_episode(ROOT, eid)
    cuts = _pass1_or_skip(ep)
    holds = _pass1_holds(ep)
    lo, hi = finding['finding_original']
    orig = {'start': lo, 'end': hi, 'confidence': 0.95, 'category': 'sponsor',
            'detection_stage': 'verification', 'validation': {'decision': 'ACCEPT'}}
    hold_bounds = {(round(h['start'], 2), round(h['end'], 2)) for h in holds}
    result = _call(vr._gate_verification_ads_by_confidence,
                   verification_ads_processed=[_processed_twin(orig, cuts)],
                   verification_ads_original=[copy.deepcopy(orig)], min_cut_confidence=MIN_CONF,
                   pass1_held_markers=holds, pass1_cuts=cuts)
    outputs = [s for s in _spans(result)
               if (round(s['start'], 2), round(s['end'], 2)) not in hold_bounds
               and s.get('held_for_review') is not False]
    reviewed = [s for s in outputs if overlap_seconds(s['start'], s['end'], lo, hi) > 0]
    assert reviewed, 'pass-2 finding over a hold was dropped without reaching review'


@pytest.mark.parametrize('eid,finding', _findings(3))
def test_kept_exclusion_replay_records(replay_out, eid, finding):
    ep = load_episode(ROOT, eid)
    cuts = _pass1_or_skip(ep)
    ps, pe = finding['finding_processed']
    if finding['finding_original'] is None:
        # A later recut replaced the saved hold; map the logged processed span back.
        ts_map = _build_timestamp_map(cuts)
        beep = get_replacement_duration()
        lo, hi = _map_to_original(ps, ts_map, beep), _map_to_original(pe, ts_map, beep)
    else:
        lo, hi = finding['finding_original']
    orig = {'start': lo, 'end': hi, 'confidence': 0.95, 'category': 'sponsor',
            'detection_stage': 'verification'}
    proc = _processed_twin(orig, cuts)
    if abs(proc['start'] - ps) > 1.5 or abs(proc['end'] - pe) > 1.5:
        pytest.skip(f'processed mapping {proc["start"]:.1f}-{proc["end"]:.1f} does not '
                    f'match the logged {ps}-{pe}')
    kept = [m for m in ep['markers'] if m.get('action_applied') == 'keep'
            and m.get('detection_stage') != 'verification']
    result = vr._exclude_kept_spans_from_verification([proc], [orig], kept, cuts, [])
    _dump(replay_out, f'kept_{eid}_{int(lo)}.json', {'result': result})


def _kept_input(eid, finding):
    ep = load_episode(ROOT, eid)
    cuts = _pass1_or_skip(ep)
    lo, hi = finding['finding_original']
    orig = {'start': lo, 'end': hi, 'confidence': 0.95, 'category': 'sponsor',
            'detection_stage': 'verification'}
    kept = [m for m in ep['markers'] if m.get('action_applied') == 'keep'
            and m.get('detection_stage') != 'verification']
    return [_processed_twin(orig, cuts)], [orig], kept, cuts


@PENDING_17
@pytest.mark.parametrize('eid,finding', [p for p in _findings(3) if p.values[1].get('audited')])
def test_kept_split_preserves_outside_residual(eid, finding):
    proc, orig, kept, cuts = _kept_input(eid, finding)
    _p, surviving, _conflicts = vr._exclude_kept_spans_from_verification(
        proc, orig, kept, cuts, [])
    keeps = [(k['start'], k['end']) for k in kept]
    for s in surviving:
        for k_lo, k_hi in keeps:
            assert overlap_seconds(s['start'], s['end'], k_lo, k_hi) <= 0.5
    lo, hi = finding['finding_original']
    residual = hi - lo - sum(overlap_seconds(lo, hi, a, b) for a, b in keeps)
    covered = sum(overlap_seconds(s['start'], s['end'], lo, hi) for s in surviving)
    assert covered >= 0.9 * residual
    paid = finding['expected_good'].get('paid_span_original')
    if paid:
        assert sum(overlap_seconds(s['start'], s['end'], *paid) for s in surviving) \
            >= 0.9 * (paid[1] - paid[0])


# Summary -----------------------------------------------------------------------

def test_cluster_summary_report(summary_rows, replay_out):
    """Report only: flags episodes outside 3-5 rendered ad clusters."""
    rows = []
    for eid in EPISODES:
        base = BASELINE['episodes'][eid]
        cuts = base['cut_clusters']
        flag = 'LOW' if cuts < 3 else 'HIGH' if cuts > 5 else ''
        rows.append({'episode': eid, 'cuts': cuts, 'holds': base['pending_holds'],
                     'restored_rejects': base['restored_reject_clusters'],
                     'expected': EXPECT['episodes'][eid]['expected_ad_clusters'],
                     'flag': flag})
    summary_rows.extend(rows)
    _dump(replay_out, 'cluster_summary.json', rows)
