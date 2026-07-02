"""Tests for ad-affinity typing of recurring cue candidates (#350 Phase 4).

Covers the pure annotator (annotate_recurring_with_ad_affinity) and the
sibling-fallback path (_sibling_affinity_fallback + its trigger condition in
_run_cue_candidate_scan) with stubbed matcher/DB.
"""
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from audio_analysis.cue_candidates import annotate_recurring_with_ad_affinity
from config import (
    AUDIO_CUE_AD_AFFINITY_TOLERANCE_SECONDS,
    AUDIO_CUE_AD_AFFINITY_MIN_FRACTION,
    AUDIO_CUE_AD_AFFINITY_PHASE_FRACTION,
    AUDIO_CUE_TYPE_CONTENT_TRANSITION,
)

TOLS = dict(
    tolerance_s=AUDIO_CUE_AD_AFFINITY_TOLERANCE_SECONDS,
    min_fraction=AUDIO_CUE_AD_AFFINITY_MIN_FRACTION,
    phase_fraction=AUDIO_CUE_AD_AFFINITY_PHASE_FRACTION,
)

AD_SPANS = [
    {'start': 100.0, 'end': 160.0},
    {'start': 600.0, 'end': 660.0},
]


class TestAnnotateRecurringWithAdAffinity:
    def _cand(self, count=4, occurrences=None):
        return {
            'start': 99.0, 'end': 102.0, 'count': count,
            'occurrences': occurrences or [99.5, 599.5, 200.0, 400.0],
        }

    def test_no_ad_history_leaves_suggested_type_none(self):
        cands = [self._cand()]
        result = annotate_recurring_with_ad_affinity(cands, [], **TOLS)
        assert result[0]['suggestedType'] is None
        assert result[0]['adBoundaryHits'] is None
        assert result[0]['boundaryAffinity'] is None

    def test_occurrences_stripped_after_annotation(self):
        cands = [self._cand()]
        result = annotate_recurring_with_ad_affinity(cands, AD_SPANS, **TOLS)
        assert 'occurrences' not in result[0]

    def test_high_affinity_typing_boundary(self):
        # 3 of 4 occurrences near ad boundaries; mix of near-start and near-end
        # -> boundary typed (neither start_fraction nor end_fraction >= phase_fraction)
        ad_spans = [{'start': 100.0, 'end': 160.0}, {'start': 600.0, 'end': 660.0}]
        cands = [{
            'start': 99.0, 'end': 102.0, 'count': 4,
            # 99.5 near start 100 (start_hit), 659.5 near end 660 (end_hit),
            # 598.0 near start 600 (start_hit), 400 not near anything
            'occurrences': [99.5, 659.5, 598.0, 400.0],
        }]
        result = annotate_recurring_with_ad_affinity(cands, ad_spans, **TOLS)
        assert result[0]['suggestedType'] == 'ad_break_boundary'
        assert result[0]['adBoundaryHits'] == 3
        assert result[0]['boundaryAffinity'] == pytest.approx(0.75)

    def test_start_only_typing(self):
        # All near start, none near end -> ad_break_start
        ad_spans = [{'start': 100.0, 'end': 200.0}]
        cands = [{
            'start': 99.0, 'end': 102.0, 'count': 3,
            'occurrences': [100.0, 101.0, 102.0],
        }]
        result = annotate_recurring_with_ad_affinity(
            cands, ad_spans,
            tolerance_s=5.0, min_fraction=0.6, phase_fraction=0.8,
        )
        assert result[0]['suggestedType'] == 'ad_break_start'

    def test_end_only_typing(self):
        ad_spans = [{'start': 50.0, 'end': 100.0}]
        cands = [{
            'start': 99.0, 'end': 102.0, 'count': 3,
            'occurrences': [100.0, 101.0, 99.5],
        }]
        result = annotate_recurring_with_ad_affinity(
            cands, ad_spans,
            tolerance_s=5.0, min_fraction=0.6, phase_fraction=0.8,
        )
        assert result[0]['suggestedType'] == 'ad_break_end'

    def test_low_affinity_gets_content_transition(self):
        # Only 1 of 4 near a boundary -> below min_fraction
        cands = [{
            'start': 99.0, 'end': 102.0, 'count': 4,
            'occurrences': [99.5, 300.0, 400.0, 500.0],
        }]
        result = annotate_recurring_with_ad_affinity(cands, AD_SPANS, **TOLS)
        assert result[0]['suggestedType'] == AUDIO_CUE_TYPE_CONTENT_TRANSITION

    def test_hits_below_2_gets_content_transition(self):
        # 1 hit / 2 count = 50% - below min_fraction AND hits < 2
        cands = [{
            'start': 99.0, 'end': 102.0, 'count': 2,
            'occurrences': [99.5, 300.0],
        }]
        result = annotate_recurring_with_ad_affinity(cands, AD_SPANS, **TOLS)
        assert result[0]['suggestedType'] == AUDIO_CUE_TYPE_CONTENT_TRANSITION
        assert result[0]['adBoundaryHits'] == 1

    def test_tolerance_exact_boundary(self):
        # Occurrence at exactly tolerance distance should count as a hit
        ad_spans = [{'start': 100.0, 'end': 200.0}]
        cands = [{
            'start': 99.0, 'end': 102.0, 'count': 3,
            'occurrences': [95.0, 96.0, 97.0],  # exactly 5.0s before start
        }]
        result = annotate_recurring_with_ad_affinity(
            cands, ad_spans, tolerance_s=5.0, min_fraction=0.6, phase_fraction=0.8)
        assert result[0]['adBoundaryHits'] == 3

    def test_tolerance_just_beyond_boundary(self):
        # 5.1s beyond start should NOT count as a hit
        ad_spans = [{'start': 100.0, 'end': 200.0}]
        cands = [{
            'start': 99.0, 'end': 102.0, 'count': 3,
            'occurrences': [94.9, 94.8, 94.7],  # 5.1s before start
        }]
        result = annotate_recurring_with_ad_affinity(
            cands, ad_spans, tolerance_s=5.0, min_fraction=0.6, phase_fraction=0.8)
        assert result[0]['adBoundaryHits'] == 0
        assert result[0]['suggestedType'] == AUDIO_CUE_TYPE_CONTENT_TRANSITION

    def test_near_both_edges_counts_once(self):
        # Occurrence near both start and end of a span counts as one hit (not two)
        ad_spans = [{'start': 100.0, 'end': 103.0}]  # only 3s gap, occurrence at 101 is near both
        cands = [{
            'start': 99.0, 'end': 102.0, 'count': 2,
            'occurrences': [101.0, 50.0],
        }]
        result = annotate_recurring_with_ad_affinity(
            cands, ad_spans, tolerance_s=5.0, min_fraction=0.4, phase_fraction=0.8)
        assert result[0]['adBoundaryHits'] == 1

    def test_resort_by_affinity_descending(self):
        # Higher affinity should rank first
        c1 = {'start': 99.0, 'end': 102.0, 'count': 4,
               'occurrences': [99.5, 599.5, 200.0, 400.0]}  # 2/4 = 0.5
        c2 = {'start': 200.0, 'end': 203.0, 'count': 3,
               'occurrences': [99.5, 599.5, 603.0]}  # 3/3 = 1.0
        result = annotate_recurring_with_ad_affinity([c1, c2], AD_SPANS, **TOLS)
        assert result[0]['start'] == 200.0  # c2 has higher affinity
        assert result[1]['start'] == 99.0

    def test_empty_recurring_list(self):
        result = annotate_recurring_with_ad_affinity([], AD_SPANS, **TOLS)
        assert result == []

    def test_occurrences_missing_treated_as_no_occurrences(self):
        # A candidate without occurrences key should get null affinity fields
        cands = [{'start': 99.0, 'end': 102.0, 'count': 4}]
        result = annotate_recurring_with_ad_affinity(cands, AD_SPANS, **TOLS)
        assert result[0]['adBoundaryHits'] is None
        assert result[0]['boundaryAffinity'] is None


class _FakeSignal:
    def __init__(self, start, template_id):
        self.start = start
        self.details = {'template_id': template_id}


class _FakeMatcher:
    """Stands in for AudioCueTemplateMatcher; records rows and detect() calls."""
    instances = []
    signals_by_path = {}

    def __init__(self, rows, score_threshold=None):
        type(self).instances.append(self)
        self.rows = rows
        self.score_threshold = score_threshold
        self.detect_paths = []
        self.is_usable = True

    def detect(self, path):
        self.detect_paths.append(path)
        return type(self).signals_by_path.get(path, [])


class TestSiblingAffinityFallback:
    """_sibling_affinity_fallback with stubbed matcher, DB, and decode."""

    def setup_method(self):
        _FakeMatcher.instances = []
        _FakeMatcher.signals_by_path = {}

    def _db(self, sibling_rows, has_audio=True):
        db = MagicMock()
        db.get_recent_episode_ad_history.return_value = sibling_rows
        db.get_episode.side_effect = lambda slug, eid: (
            {'episode_id': eid, 'original_file': 'original.mp3'} if has_audio else
            {'episode_id': eid, 'original_file': None})
        db.get_setting_float.return_value = 0.75
        return db

    def _storage(self):
        storage = MagicMock()
        storage.get_original_path.side_effect = (
            lambda slug, eid: Path(f'/fake/{slug}/{eid}.mp3'))
        return storage

    def _sibling_row(self, eid, spans):
        marked = [{**s, 'was_cut': True} for s in spans]
        return {'episode_id': eid, 'original_duration': 3600,
                'ad_markers_json': json.dumps(marked)}

    def _run(self, recurring, db, storage):
        from api.cue_templates import _sibling_affinity_fallback
        with patch('api.cue_templates.AudioCueTemplateMatcher', _FakeMatcher), \
             patch('api.cue_templates.os.path.exists', return_value=True), \
             patch('api.cue_templates.decode_pcm_window',
                   return_value=np.zeros(16000, dtype=np.float32)), \
             patch('api.cue_templates.compute_mfcc',
                   return_value=np.zeros((10, 13), dtype=np.float32)), \
             patch('api.cue_templates.serialize_mfcc', return_value=b'blob'):
            return _sibling_affinity_fallback(
                recurring, 'feed', 'target-ep', db, storage, '/fake/target.mp3')

    def test_caps_at_5_candidates_and_2_siblings(self):
        # 7 candidates and 3 history siblings must be capped to 5 templates x
        # 2 detect() calls.
        recurring = [
            {'start': float(i * 100), 'end': float(i * 100 + 3), 'count': 9 - i}
            for i in range(7)
        ]
        rows = [self._sibling_row(f'sib{i}', [{'start': 100.0, 'end': 160.0}])
                for i in range(3)]
        self._run(recurring, self._db(rows), self._storage())
        assert len(_FakeMatcher.instances) == 1
        matcher = _FakeMatcher.instances[0]
        assert len(matcher.rows) == 5
        assert len(matcher.detect_paths) == 2

    def test_pools_hits_across_siblings(self):
        # One hit per sibling: only POOLED hits reach the >=2 gate, so this is
        # typed ad_break_boundary iff pooling works across siblings.
        recurring = [{'start': 50.0, 'end': 53.0, 'count': 4}]
        rows = [
            self._sibling_row('sib0', [{'start': 100.0, 'end': 160.0}]),
            self._sibling_row('sib1', [{'start': 600.0, 'end': 660.0}]),
        ]
        _FakeMatcher.signals_by_path = {
            '/fake/feed/sib0.mp3': [_FakeSignal(101.0, 0)],
            '/fake/feed/sib1.mp3': [_FakeSignal(659.0, 0)],
        }
        out = self._run(recurring, self._db(rows), self._storage())
        assert out[0]['adBoundaryHits'] == 2
        assert out[0]['boundaryAffinity'] == pytest.approx(1.0)
        assert out[0]['suggestedType'] == 'ad_break_boundary'
        assert out[0]['affinitySource'] == 'siblings'

    def test_low_pooled_affinity_gets_content_transition(self):
        # 1 boundary hit out of 4 sibling matches -> below the 0.6 gate.
        recurring = [{'start': 50.0, 'end': 53.0, 'count': 4}]
        rows = [self._sibling_row('sib0', [{'start': 100.0, 'end': 160.0}])]
        _FakeMatcher.signals_by_path = {
            '/fake/feed/sib0.mp3': [
                _FakeSignal(101.0, 0), _FakeSignal(300.0, 0),
                _FakeSignal(400.0, 0), _FakeSignal(500.0, 0),
            ],
        }
        out = self._run(recurring, self._db(rows), self._storage())
        assert out[0]['suggestedType'] == AUDIO_CUE_TYPE_CONTENT_TRANSITION
        assert out[0]['affinitySource'] == 'siblings'

    def test_skips_cleanly_when_no_sibling_has_audio(self):
        recurring = [{'start': 50.0, 'end': 53.0, 'count': 4,
                      'occurrences': [50.0, 200.0]}]
        rows = [self._sibling_row('sib0', [{'start': 100.0, 'end': 160.0}])]
        out = self._run(recurring, self._db(rows, has_audio=False), self._storage())
        assert len(_FakeMatcher.instances) == 0
        assert out[0]['adBoundaryHits'] is None
        assert out[0]['boundaryAffinity'] is None
        assert out[0]['affinitySource'] is None
        assert 'occurrences' not in out[0]

    def test_skips_cleanly_when_no_ad_history(self):
        recurring = [{'start': 50.0, 'end': 53.0, 'count': 4}]
        out = self._run(recurring, self._db([]), self._storage())
        assert len(_FakeMatcher.instances) == 0
        assert out[0]['adBoundaryHits'] is None


class TestScanWiring:
    """_run_cue_candidate_scan chooses episode history over sibling fallback."""

    def _scan(self, ad_markers_json, recurring):
        from api.cue_templates import _run_cue_candidate_scan
        db = MagicMock()
        db.get_episode.return_value = {
            'episode_id': 'ep1', 'ad_markers_json': ad_markers_json}
        fallback = MagicMock(side_effect=lambda recurring, *a, **k: recurring)
        with patch('api.cue_templates.get_database', return_value=db), \
             patch('api.cue_templates.get_storage', return_value=MagicMock()), \
             patch('api.cue_templates.AudioFingerprinter') as fp_cls, \
             patch('api.cue_templates._drop_speechlike_recurring',
                   side_effect=lambda r, p: r), \
             patch('api.cue_templates._templated_cue_spans', return_value=[]), \
             patch('api.cue_templates._sibling_affinity_fallback', fallback):
            fp = fp_cls.return_value
            fp.is_available.return_value = False
            fp.discover_recurring_spots.return_value = recurring
            fp.discover_cross_episode_cues.return_value = []
            _run_cue_candidate_scan(1, 'ep1', 'feed', '/fake/a.mp3',
                                    similarity=0.9, min_count=2)
        assert not db.save_cue_candidate_scan_error.called
        saved = db.save_cue_candidate_scan_result.call_args[0][2]
        return fallback, saved

    def test_episode_history_skips_sibling_fallback_and_types(self):
        recurring = [{'start': 99.0, 'end': 102.0, 'count': 2,
                      'occurrences': [99.5, 159.0]}]
        history = json.dumps([{'start': 100.0, 'end': 160.0, 'was_cut': True}])
        fallback, saved = self._scan(history, recurring)
        assert not fallback.called
        assert saved[0]['suggestedType'] == 'ad_break_boundary'
        assert saved[0]['affinitySource'] == 'episode'
        assert saved[0]['adBoundaryHits'] == 2

    def test_rejected_marker_is_not_a_boundary(self):
        # A was_cut=False (reviewer-rejected) marker must not count: with no cut
        # marker there is no ad history, so the sibling fallback runs instead.
        recurring = [{'start': 99.0, 'end': 102.0, 'count': 2,
                      'occurrences': [99.5, 159.0]}]
        history = json.dumps([{'start': 100.0, 'end': 160.0, 'was_cut': False}])
        fallback, _ = self._scan(history, recurring)
        assert fallback.called

    def test_missing_was_cut_key_set_is_untrusted(self):
        # Raw pass-1 markers (no was_cut on any) are untrusted -> no boundaries,
        # so the sibling fallback runs (mirrors positional_prior's set defense).
        recurring = [{'start': 99.0, 'end': 102.0, 'count': 2,
                      'occurrences': [99.5, 159.0]}]
        history = json.dumps([{'start': 100.0, 'end': 160.0}])
        fallback, _ = self._scan(history, recurring)
        assert fallback.called

    def test_no_episode_history_triggers_sibling_fallback(self):
        recurring = [{'start': 99.0, 'end': 102.0, 'count': 2,
                      'occurrences': [99.5, 159.0]}]
        fallback, _ = self._scan(None, recurring)
        assert fallback.called

    def test_unparseable_history_triggers_sibling_fallback(self):
        recurring = [{'start': 99.0, 'end': 102.0, 'count': 2,
                      'occurrences': [99.5, 159.0]}]
        fallback, _ = self._scan('{not json', recurring)
        assert fallback.called
