"""Cue fusion into the differential stage (Task 7).

Three seams:
1. dai_differential_ads consumes cue_marks: an uncorroborated merged
   candidate with an edge inside the snap window of a primary template cue
   cuts (cue-backed) instead of holding.
2. Stage 2.5 derives cue_marks and cue-pair corroborating spans from the
   audio_analysis object process_transcript already receives.
3. The differential fetch scans the refetch for template cues (persisted as
   refetch_cues) and align_and_diff interpolates the probe offset between
   cue anchor pairs so a shifted identical block scores identical without
   the doubled-window drift retry.
"""
import os
import sys
import tempfile

os.environ.setdefault('MINUSPOD_DATA_DIR', tempfile.mkdtemp(prefix='cue_fusion_test_'))
os.environ.setdefault('SECRET_KEY', 'test-secret')

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))

import json
from unittest.mock import MagicMock, patch

import numpy as np

import differential_fetcher as df
from ad_detector import AdDetector, dai_differential_ads
from audio_analysis.base import AudioAnalysisResult, AudioSegmentSignal
from config import is_cue_backed


def _diff(*regions):
    return {'status': 'ok', 'regions': list(regions)}


def _region(start, end, corr, kind='differential'):
    return {'start_s': start, 'end_s': end, 'kind': kind, 'corr': corr}


def _cue(start, confidence=0.9, source='template', template_id=1,
         role='boundary', end=None):
    details = {'template_id': template_id, 'role': role, 'label': f't{template_id}'}
    if source is not None:
        details['source'] = source
    return AudioSegmentSignal(
        start=start, end=end if end is not None else start + 1.0,
        signal_type='audio_cue', confidence=confidence, details=details)


def _analysis(*signals):
    result = AudioAnalysisResult()
    result.signals = list(signals)
    return result


# --- 1. Cue corroboration in dai_differential_ads ---------------------------


def test_cue_near_candidate_end_upgrades_hold_to_cut():
    ads = dai_differential_ads(_diff(_region(100.0, 140.0, 0.2)), [],
                               cue_marks=[138.0])
    assert len(ads) == 1
    ad = ads[0]
    assert ad.get('held_for_review') is not True
    assert ad['confidence'] == 0.95
    assert ad['detection_stage'] == 'dai_differential'
    assert ad['cue_snap'] == {'source': 'differential_cue_fusion'}
    assert is_cue_backed(ad) is True
    assert 'differential_uncorroborated' not in ad


def test_cue_far_from_both_edges_keeps_hold():
    ads = dai_differential_ads(_diff(_region(100.0, 140.0, 0.2)), [],
                               cue_marks=[160.0])
    assert len(ads) == 1
    assert ads[0]['held_for_review'] is True
    assert 'cue_snap' not in ads[0]


def test_cue_window_is_minus_lead_plus_lag():
    # A cue at t corroborates an edge e when t - 10.0 <= e <= t + 4.0.
    # end edge 140.0: cue 150.0 -> 140 in [140, 154] (lead side, inclusive).
    ads = dai_differential_ads(_diff(_region(100.0, 140.0, 0.2)), [],
                               cue_marks=[150.0])
    assert ads[0].get('held_for_review') is not True
    # cue 150.5 -> window [140.5, 154.5] misses both edges.
    ads = dai_differential_ads(_diff(_region(100.0, 140.0, 0.2)), [],
                               cue_marks=[150.5])
    assert ads[0]['held_for_review'] is True
    # start edge 100.0: cue 96.0 -> 100 in [86, 100] (lag side, inclusive).
    ads = dai_differential_ads(_diff(_region(100.0, 140.0, 0.2)), [],
                               cue_marks=[96.0])
    assert ads[0].get('held_for_review') is not True
    # cue 95.5 -> window [85.5, 99.5] misses the start edge.
    ads = dai_differential_ads(_diff(_region(100.0, 140.0, 0.2)), [],
                               cue_marks=[95.5])
    assert ads[0]['held_for_review'] is True


def test_cue_backed_candidate_cuts_regardless_of_hold_floor():
    # Corroboration semantics: like a stage overlap, a cue-backed candidate
    # is not subject to the uncorroborated hold floor.
    ads = dai_differential_ads(_diff(_region(100.0, 105.0, 0.2)), [],
                               hold_min_seconds=10.0, cue_marks=[104.0])
    assert len(ads) == 1
    assert ads[0].get('held_for_review') is not True
    assert ads[0]['cue_snap'] == {'source': 'differential_cue_fusion'}


def test_cue_does_not_rescue_high_corr_region():
    assert dai_differential_ads(_diff(_region(100.0, 140.0, 0.9)), [],
                                cue_marks=[138.0]) == []


def test_stage_corroborated_region_takes_stage_path_not_cue_path():
    ads = dai_differential_ads(_diff(_region(100.0, 140.0, 0.2)), [],
                               corroborating_spans=[(110.0, 130.0)],
                               cue_marks=[138.0])
    assert len(ads) == 1
    assert ads[0].get('held_for_review') is not True
    assert 'cue_snap' not in ads[0]


def test_fp_region_still_excluded_despite_cue():
    assert dai_differential_ads(_diff(_region(100.0, 140.0, 0.2)),
                                [(95.0, 145.0)], cue_marks=[138.0]) == []


# --- 2. Stage 2.5 call-site wiring -------------------------------------------


def _capture_stage25(audio_analysis, dai_differential):
    """Run process_transcript to stage 2.5 and capture dai_differential_ads kwargs."""
    detector = AdDetector(api_key='test-key')
    detector.db = MagicMock()
    detector.db.get_setting_float.side_effect = (
        lambda key, default=0.0: default)
    captured = {}

    def fake_dd(dai_diff, fp_pairs, corroborating_spans=None, **kw):
        captured['corroborating_spans'] = corroborating_spans
        captured.update(kw)
        return []

    with patch.object(detector, 'initialize_client'), \
         patch('ad_detector.dai_differential_ads', side_effect=fake_dd), \
         patch.object(detector, 'detect_ads',
                      return_value={'ads': [], 'status': 'success',
                                    'raw_response': '', 'model': 'm'}):
        detector.process_transcript(
            [{'start': 0.0, 'end': 300.0, 'text': 'hello'}],
            slug='s', episode_id='e1', skip_patterns=True,
            audio_analysis=audio_analysis,
            dai_differential=dai_differential,
            keep_content=False)
    return captured


def test_stage25_passes_template_cue_marks():
    analysis = _analysis(
        _cue(101.5, confidence=0.9),                  # template, confident
        _cue(200.0, confidence=0.5),                  # below 0.80 floor
        _cue(250.0, confidence=0.95, source=None),    # spectral fallback
    )
    captured = _capture_stage25(analysis, _diff(_region(10.0, 40.0, 0.2)))
    assert captured['cue_marks'] == [101.5]


def test_stage25_includes_cue_pair_spans_in_corroborating_spans():
    # Two boundary cues bracketing 196s-262s (66s gap, inside the pair band).
    analysis = _analysis(
        _cue(190.0, confidence=0.9, template_id=1, end=196.0),
        _cue(262.0, confidence=0.9, template_id=1, end=263.0),
    )
    captured = _capture_stage25(analysis, _diff(_region(200.0, 260.0, 0.2)))
    spans = captured['corroborating_spans']
    assert any(cs <= 200.0 and ce >= 260.0 for cs, ce in spans)


def test_stage25_none_analysis_yields_empty_cue_marks():
    captured = _capture_stage25(None, _diff(_region(10.0, 40.0, 0.2)))
    assert captured['cue_marks'] == []


def test_stage25_dict_analysis_is_tolerated():
    # Defensive: a serialized dict (no get_signals_by_type) must not crash
    # and contributes no cue marks.
    captured = _capture_stage25({'signals': []},
                                _diff(_region(10.0, 40.0, 0.2)))
    assert captured['cue_marks'] == []


def test_stage25_non_ad_role_cue_excluded_from_marks():
    # A non_ad-role cue (show_intro/show_outro/content_transition) must
    # never corroborate a differential edge -- same role gate
    # cue_boundary_snap and cue_pair_ads honor. Only the role differs from
    # test_stage25_passes_template_cue_marks's confident template cue.
    analysis = _analysis(
        _cue(101.5, confidence=0.9, role='non_ad'),
    )
    captured = _capture_stage25(analysis, _diff(_region(10.0, 40.0, 0.2)))
    assert captured['cue_marks'] == []


def test_stage25_start_and_end_role_cues_are_included():
    # Ad-edge roles ('start', 'end', and by extension 'boundary') still
    # corroborate -- pins existing behavior alongside the non_ad exclusion.
    analysis = _analysis(
        _cue(101.5, confidence=0.9, role='start', template_id=1),
        _cue(200.0, confidence=0.9, role='end', template_id=2),
    )
    captured = _capture_stage25(analysis, _diff(_region(10.0, 40.0, 0.2)))
    assert captured['cue_marks'] == [101.5, 200.0]


def test_stage25_non_ad_cue_at_edge_leaves_candidate_held():
    # End-to-end: a non_ad-role cue sitting right at the differential
    # region's edge must not corroborate it -- the candidate stays held
    # for review, unlike an ad-role cue at the same position (contrast
    # test_differential_inside_cue_pair_is_cut_not_held).
    analysis = _analysis(_cue(138.0, confidence=0.9, role='non_ad'))
    detector = AdDetector(api_key='test-key')
    detector.db = MagicMock()
    detector.db.get_setting_float.side_effect = (
        lambda key, default=0.0: default)
    with patch.object(detector, 'initialize_client'), \
         patch.object(detector, 'detect_ads',
                      return_value={'ads': [], 'status': 'success',
                                    'raw_response': '', 'model': 'm'}):
        result = detector.process_transcript(
            [{'start': 0.0, 'end': 300.0, 'text': 'hello'}],
            slug='s', episode_id='e1', skip_patterns=True,
            audio_analysis=analysis,
            dai_differential=_diff(_region(100.0, 140.0, 0.2)),
            keep_content=False)
    dd = [a for a in result['ads']
          if a.get('detection_stage') == 'dai_differential']
    assert len(dd) == 1
    assert dd[0].get('held_for_review') is True


def test_differential_inside_cue_pair_is_cut_not_held():
    # End-to-end through the real dai_differential_ads: a measured-different
    # candidate bracketed by a cue pair is corroborated -> cut, not held.
    # The pair itself must NOT mint a synthetic cue_pair ad at this stage.
    analysis = _analysis(
        _cue(190.0, confidence=0.9, template_id=1, end=196.0),
        _cue(262.0, confidence=0.9, template_id=1, end=263.0),
    )
    detector = AdDetector(api_key='test-key')
    detector.db = MagicMock()
    detector.db.get_setting_float.side_effect = (
        lambda key, default=0.0: default)
    with patch.object(detector, 'initialize_client'), \
         patch.object(detector, 'detect_ads',
                      return_value={'ads': [], 'status': 'success',
                                    'raw_response': '', 'model': 'm'}):
        result = detector.process_transcript(
            [{'start': 0.0, 'end': 300.0, 'text': 'hello'}],
            slug='s', episode_id='e1', skip_patterns=True,
            audio_analysis=analysis,
            dai_differential=_diff(_region(200.0, 260.0, 0.2)),
            keep_content=False)
    dd = [a for a in result['ads']
          if a.get('detection_stage') == 'dai_differential']
    assert len(dd) == 1
    assert dd[0].get('held_for_review') is not True
    assert not any(a.get('detection_stage') == 'cue_pair'
                   for a in result['ads'])


# --- 3a. Refetch cue scan plumbing -------------------------------------------


class _FakeResponse:
    def __init__(self, chunks):
        self._chunks = chunks

    def raise_for_status(self):
        pass

    def iter_content(self, chunk_size=8192):
        yield from self._chunks

    def close(self):
        pass


def _run_file(tmp_path, size=1000):
    path = tmp_path / 'run.mp3'
    path.write_bytes(b'\x00' * size)
    return str(path)


_ALIGNED = {'status': 'ok', 'regions': [
    {'start_s': 1.0, 'end_s': 5.0, 'kind': 'differential', 'corr': 0.0}]}


def test_fetch_and_diff_scans_refetch_and_builds_anchor_pairs(tmp_path):
    run_file = _run_file(tmp_path)
    refetch_cues = [{'time': 12.5, 'template_id': 5}]
    primary_cues = [{'time': 10.0, 'template_id': 5}]
    scanned = {}

    def cue_scan(path):
        scanned['path'] = path
        return refetch_cues

    with patch('differential_fetcher.safe_get',
               return_value=_FakeResponse([b'x' * 100])), \
         patch('differential_fetcher.get_audio_duration', return_value=42.0), \
         patch('differential_fetcher.align_and_diff',
               return_value=_ALIGNED) as mock_align:
        result = df.fetch_and_diff('https://traffic.megaphone.fm/e.mp3',
                                   run_file, str(tmp_path),
                                   cue_scan=cue_scan,
                                   primary_cues=primary_cues)

    assert result['status'] == 'ok'
    assert result['refetch_cues'] == refetch_cues
    assert scanned['path'] == os.path.join(str(tmp_path), 'refetch_audio')
    assert mock_align.call_args.kwargs['anchor_pairs'] == [(10.0, 12.5)]
    # Refetch file still cleaned up by fetch_and_diff itself.
    assert not os.path.exists(os.path.join(str(tmp_path), 'refetch_audio'))


def test_fetch_and_diff_cue_scan_failure_is_nonfatal(tmp_path):
    run_file = _run_file(tmp_path)

    def cue_scan(path):
        raise RuntimeError('mfcc exploded')

    with patch('differential_fetcher.safe_get',
               return_value=_FakeResponse([b'x' * 100])), \
         patch('differential_fetcher.get_audio_duration', return_value=42.0), \
         patch('differential_fetcher.align_and_diff',
               return_value=_ALIGNED) as mock_align:
        result = df.fetch_and_diff('https://traffic.megaphone.fm/e.mp3',
                                   run_file, str(tmp_path),
                                   cue_scan=cue_scan,
                                   primary_cues=[{'time': 1.0, 'template_id': 5}])

    assert result['status'] == 'ok'
    assert result['refetch_cues'] == []
    assert mock_align.call_args.kwargs['anchor_pairs'] == []


def test_fetch_and_diff_cue_scan_malformed_result_is_nonfatal(tmp_path):
    run_file = _run_file(tmp_path)

    def cue_scan(path):
        # Malformed: missing the required 'time' key that
        # match_cue_anchor_pairs sorts on.
        return [{'template_id': 5}]

    with patch('differential_fetcher.safe_get',
               return_value=_FakeResponse([b'x' * 100])), \
         patch('differential_fetcher.get_audio_duration', return_value=42.0), \
         patch('differential_fetcher.align_and_diff',
               return_value=_ALIGNED) as mock_align:
        result = df.fetch_and_diff('https://traffic.megaphone.fm/e.mp3',
                                   run_file, str(tmp_path),
                                   cue_scan=cue_scan,
                                   primary_cues=[{'time': 1.0, 'template_id': 5}])

    assert result['status'] == 'ok'
    assert result.get('refetch_cues', []) == []
    assert mock_align.call_args.kwargs['anchor_pairs'] == []


def test_fetch_and_diff_without_scan_keeps_legacy_shape(tmp_path):
    run_file = _run_file(tmp_path)
    with patch('differential_fetcher.safe_get',
               return_value=_FakeResponse([b'x' * 100])), \
         patch('differential_fetcher.get_audio_duration', return_value=42.0), \
         patch('differential_fetcher.align_and_diff',
               return_value=_ALIGNED):
        result = df.fetch_and_diff('https://traffic.megaphone.fm/e.mp3',
                                   run_file, str(tmp_path))
    assert 'refetch_cues' not in result


def test_match_cue_anchor_pairs_greedy_by_template_and_order():
    primary = [{'time': 10.0, 'template_id': 1},
               {'time': 50.0, 'template_id': 2},
               {'time': 90.0, 'template_id': 1}]
    refetch = [{'time': 12.0, 'template_id': 1},
               {'time': 53.0, 'template_id': 2},
               {'time': 95.0, 'template_id': 1}]
    assert df.match_cue_anchor_pairs(primary, refetch) == [
        (10.0, 12.0), (50.0, 53.0), (90.0, 95.0)]
    # Template mismatch never pairs; refetch times stay monotonic.
    assert df.match_cue_anchor_pairs(
        [{'time': 10.0, 'template_id': 1}],
        [{'time': 12.0, 'template_id': 9}]) == []
    # Pairs keep the refetch sequence monotonic: once primary t1@10 pairs
    # with refetch t1@45, primary t2@50 cannot reach back to refetch t2@40.
    assert df.match_cue_anchor_pairs(
        [{'time': 10.0, 'template_id': 1}, {'time': 50.0, 'template_id': 2}],
        [{'time': 40.0, 'template_id': 2}, {'time': 45.0, 'template_id': 1}]
    ) == [(10.0, 45.0)]
    assert df.match_cue_anchor_pairs([], []) == []


# --- 3b. Anchor-corrected probe offsets --------------------------------------

RATE = 8000


def _burst(seconds, seed, freq=None):
    n = int(seconds * RATE)
    rng = np.random.default_rng(seed)
    sig = 0.4 * rng.standard_normal(n)
    if freq is not None:
        t = np.arange(n) / RATE
        sig = sig * 0.5 + 0.4 * np.sin(2 * np.pi * freq * t)
    return sig.astype(np.float32)


def _assemble(parts):
    pcm = []
    marks = [0.0]
    t = 0.0
    for part in parts:
        if isinstance(part, tuple) and part[0] == 'sil':
            dur = part[1]
            pcm.append(np.zeros(int(dur * RATE), dtype=np.float32))
            marks.append(t + dur / 2.0)
            t += dur
        else:
            pcm.append(part)
            t += len(part) / RATE
    marks.append(t)
    return np.concatenate(pcm), marks


def test_anchor_offset_interpolates_between_anchors():
    anchors = [(100.0, 100.0), (200.0, 203.0)]
    assert df._anchor_offset(anchors, 100.0) == 0.0
    assert df._anchor_offset(anchors, 150.0) == 1.5
    assert df._anchor_offset(anchors, 200.0) == 3.0
    # Outside the anchor range: clamp to the nearest anchor's delta.
    assert df._anchor_offset(anchors, 50.0) == 0.0
    assert df._anchor_offset(anchors, 250.0) == 3.0
    # Single anchor: constant delta everywhere.
    assert df._anchor_offset([(20.0, 23.0)], 5.0) == 3.0
    assert df._anchor_offset([(20.0, 23.0)], 90.0) == 3.0


def _shifted_pair():
    """Run A|sil|C; refetch inserts 2.5s unmarked pad before C (its silence
    mark went undetected on the refetch), so C is chain-unmatched and its
    inherited offset is stale by 2.5s (outside the base +-2s search window).
    """
    a = _burst(8, 11, 220.0)
    c = _burst(8, 12, 440.0)
    pad = _burst(2.5, 77, None)
    run_pcm, run_marks = _assemble([a, ('sil', 0.4), c])
    ref_pcm, ref_marks = _assemble(
        [a, ('sil', 0.4), np.concatenate([pad, c])])
    return run_pcm, run_marks, ref_pcm, ref_marks


def test_anchor_pair_avoids_widened_reprobe(monkeypatch):
    # A template cue at the head of C in both fetches (primary 8.4s, refetch
    # 10.9s) anchors the probe offset at +2.5s, so C scores identical on the
    # FIRST probe: the doubled-window drift retry never runs.
    run_pcm, run_marks, ref_pcm, ref_marks = _shifted_pair()

    searches = []
    real = df._block_correlation

    def spy(*args, **kwargs):
        searches.append(kwargs.get('search_s', df.XCORR_SEARCH_S))
        return real(*args, **kwargs)

    monkeypatch.setattr(df, '_block_correlation', spy)
    result = df._align_and_diff_pcm(run_pcm, ref_pcm, run_marks, ref_marks,
                                    anchor_pairs=[(8.4, 10.9)])

    assert df.XCORR_SEARCH_S * 2 not in searches
    assert result['status'] == 'no_differential'
    assert all(r['kind'] == 'identical' for r in result['regions'])


def test_without_anchor_the_same_fixture_needs_the_widened_reprobe(monkeypatch):
    # Sanity for the spy above: absent anchors, the stale inherited offset
    # forces the doubled-window retry (existing 2.76.0 behavior).
    run_pcm, run_marks, ref_pcm, ref_marks = _shifted_pair()

    searches = []
    real = df._block_correlation

    def spy(*args, **kwargs):
        searches.append(kwargs.get('search_s', df.XCORR_SEARCH_S))
        return real(*args, **kwargs)

    monkeypatch.setattr(df, '_block_correlation', spy)
    result = df._align_and_diff_pcm(run_pcm, ref_pcm, run_marks, ref_marks)

    assert df.XCORR_SEARCH_S * 2 in searches
    assert result['status'] == 'no_differential'


# --- 3a. Pipeline plumbing (_run_differential_fetch) --------------------------

import main_app.processing as processing  # noqa: E402
from audio_analysis.cue_template_matcher import AudioCueTemplateMatcher  # noqa: E402


def _stub_matcher(signals):
    matcher = AudioCueTemplateMatcher(templates=[])
    matcher.detect = lambda path: signals
    return matcher


def test_pipeline_scans_refetch_persists_cues_and_removes_work_dir(tmp_path):
    work_dir = tmp_path / 'dai_diff_test'
    work_dir.mkdir()
    matcher = _stub_matcher([_cue(12.5, confidence=0.9, template_id=5)])
    fetch_result = {'status': 'ok', 'regions': [],
                    'refetch_meta': {'ua': 'AntennaPod/3.4.0'}, 'error': None,
                    'refetch_cues': [{'time': 12.5, 'template_id': 5}]}
    mock_fetch = MagicMock(return_value=fetch_result)
    with patch('main_app.processing.resolve_differential_fetch_setting',
               return_value=True), \
         patch('main_app.processing._feed_cue_matcher', return_value=matcher), \
         patch('main_app.processing.fetch_and_diff', mock_fetch), \
         patch.object(processing.tempfile, 'mkdtemp',
                      return_value=str(work_dir)), \
         patch.object(processing.status_service, 'update_job_stage'), \
         patch.object(processing.db, 'save_episode_dai_differential') as mock_save:
        result = processing._run_differential_fetch(
            'feed', 'ep1', 'https://example.com/e.mp3', '/tmp/a.mp3', 7)

    assert result == fetch_result
    saved = json.loads(mock_save.call_args.args[2])
    assert saved['refetch_cues'] == [{'time': 12.5, 'template_id': 5}]
    # The worker scanned the primary audio for anchor cues and handed the
    # scan hook plus primary cues to fetch_and_diff.
    kwargs = mock_fetch.call_args.kwargs
    assert kwargs['primary_cues'] == [{'time': 12.5, 'template_id': 5}]
    assert callable(kwargs['cue_scan'])
    assert kwargs['cue_scan']('/x/refetch') == [{'time': 12.5, 'template_id': 5}]
    assert not os.path.exists(str(work_dir))


def test_pipeline_primary_scan_failure_never_fails_differential(tmp_path):
    work_dir = tmp_path / 'dai_diff_boom'
    work_dir.mkdir()
    matcher = AudioCueTemplateMatcher(templates=[])
    matcher.detect = MagicMock(side_effect=RuntimeError('scan exploded'))
    fetch_result = {'status': 'ok', 'regions': [],
                    'refetch_meta': {}, 'error': None, 'refetch_cues': []}
    mock_fetch = MagicMock(return_value=fetch_result)
    with patch('main_app.processing.resolve_differential_fetch_setting',
               return_value=True), \
         patch('main_app.processing._feed_cue_matcher', return_value=matcher), \
         patch('main_app.processing.fetch_and_diff', mock_fetch), \
         patch.object(processing.tempfile, 'mkdtemp',
                      return_value=str(work_dir)), \
         patch.object(processing.status_service, 'update_job_stage'), \
         patch.object(processing.db, 'save_episode_dai_differential'):
        result = processing._run_differential_fetch(
            'feed', 'ep1', 'https://example.com/e.mp3', '/tmp/a.mp3', 7)

    assert result == fetch_result
    assert mock_fetch.call_args.kwargs['primary_cues'] == []
    assert not os.path.exists(str(work_dir))


def test_pipeline_without_matcher_passes_no_cue_hooks():
    mock_fetch = MagicMock(return_value={'status': 'no_differential',
                                         'regions': [], 'refetch_meta': {},
                                         'error': None})
    with patch('main_app.processing.resolve_differential_fetch_setting',
               return_value=True), \
         patch('main_app.processing._feed_cue_matcher', return_value=None), \
         patch('main_app.processing.fetch_and_diff', mock_fetch), \
         patch.object(processing.status_service, 'update_job_stage'), \
         patch.object(processing.db, 'save_episode_dai_differential'):
        processing._run_differential_fetch(
            'feed', 'ep1', 'https://example.com/e.mp3', '/tmp/a.mp3', 7)
    kwargs = mock_fetch.call_args.kwargs
    assert kwargs.get('cue_scan') is None
    assert kwargs.get('primary_cues') in (None, [])


def test_feed_cue_matcher_only_returns_template_matchers():
    matcher = AudioCueTemplateMatcher(templates=[])
    with patch.object(processing.audio_analyzer, '_load_cue_config',
                      return_value=(True, matcher)):
        assert processing._feed_cue_matcher(7) is matcher
    with patch.object(processing.audio_analyzer, '_load_cue_config',
                      return_value=(True, object())):
        assert processing._feed_cue_matcher(7) is None
    with patch.object(processing.audio_analyzer, '_load_cue_config',
                      side_effect=RuntimeError('db gone')):
        assert processing._feed_cue_matcher(7) is None
    assert processing._feed_cue_matcher(None) is None
