"""Legacy or out-of-window corrections must not reach the validator or the recut replay."""
import json
import logging
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))

import pytest

from main_app.pipeline import detect, recut
from main_app.pipeline.shared import db as shared_db
from utils.correction_guard import correction_skip_reason, usable_confirmed_corrections
from ad_validator import AdValidator


pytestmark = pytest.mark.usefixtures('held_widen_sixty')


HELD = {'start': 437.0, 'end': 440.2, 'confidence': 0.9, 'sponsor': 'Acme',
        'reason': 'span', 'category': 'sponsor', 'held_for_review': True,
        'hold_reason': 'transcript_loop'}
# A cut neighbour closes the held window at 497.42.
NEIGHBOUR = {'start': 497.42, 'end': 520.0, 'was_cut': True, 'category': 'sponsor'}


class FakeDb:
    def __init__(self, confirmed, markers):
        self._confirmed = confirmed
        self._markers = markers

    def get_setting(self, key):
        return '60' if key == 'held_widen_seconds' else None

    def get_podcast_row(self, slug):
        return {'id': 1}

    def get_false_positive_corrections(self, podcast_id, episode_id):
        return []

    def get_confirmed_corrections(self, podcast_id, episode_id):
        return list(self._confirmed)

    def get_episode(self, slug, episode_id):
        return {'ad_markers_json': json.dumps(self._markers),
                'original_duration': 3600.0, 'status': 'processed'}


def _adjust(orig, new):
    return {'start': orig[0], 'end': orig[1], 'correction_type': 'boundary_adjustment',
            'confirmed_span': {'start': new[0], 'end': new[1]}}


def test_adjustment_outside_held_window_is_dropped_before_the_validator(caplog):
    markers = [dict(HELD), dict(NEIGHBOUR)]
    db = FakeDb([_adjust((437.0, 440.2), (100.0, 500.0))], markers)
    caplog.set_level(logging.INFO)
    _fp, confirmed = detect._load_user_corrections('s', 'e', db)
    assert confirmed == []
    assert 'skipped legacy correction 100.0-500.0: outside the allowed window' in caplog.text
    result = AdValidator(episode_duration=3600.0, confirmed_corrections=confirmed).validate(
        [dict(HELD)])
    assert (result.ads[0]['start'], result.ads[0]['end']) == (437.0, 440.2)


def test_recut_replay_skips_the_same_adjustment(monkeypatch):
    ads = [dict(HELD), dict(NEIGHBOUR)]
    monkeypatch.setattr(shared_db, 'get_podcast_row', lambda slug: {'id': 1})
    monkeypatch.setattr(shared_db, 'get_episode_corrections', lambda pid, eid: [
        {'correction_type': 'boundary_adjustment',
         'original_bounds': {'start': 437.0, 'end': 440.2},
         'corrected_bounds': {'start': 100.0, 'end': 500.0}}])
    recut._apply_boundary_adjustments(
        's', 'e', ads, episode={'original_duration': 3600.0, 'status': 'processed'})
    assert (ads[0]['start'], ads[0]['end']) == (437.0, 440.2)


def test_half_second_confirm_is_skipped_in_both_paths(caplog):
    row = {'start': 620.0, 'end': 620.2, 'correction_type': 'confirm'}
    assert correction_skip_reason(row) == 'shorter than 1s'
    caplog.set_level(logging.INFO)
    _fp, confirmed = detect._load_user_corrections(
        's', 'e', FakeDb([row], [{'start': 600.0, 'end': 630.0}]))
    assert confirmed == []
    assert 'skipped legacy correction 620.0-620.2: shorter than 1s' in caplog.text
    reason = recut._legacy_skip_reason(
        {'start': 600.0, 'end': 630.0}, 620.0, 620.2, None, [], [], 0.0, 0)
    assert reason == 'shorter than 1s'


def test_valid_confirm_still_clamps_to_the_approved_span():
    row = {'start': 120.0, 'end': 437.0, 'correction_type': 'confirm',
           'confirmed_span': {'start': 150.0, 'end': 390.0}}
    _fp, confirmed = detect._load_user_corrections(
        's', 'e', FakeDb([row], [{'start': 120.0, 'end': 437.0}]))
    assert confirmed == [row]
    result = AdValidator(episode_duration=3600.0, confirmed_corrections=confirmed).validate(
        [{'start': 120.0, 'end': 437.0, 'confidence': 0.5, 'reason': 'x'}])
    assert (result.ads[0]['start'], result.ads[0]['end']) == (150.0, 390.0)


def test_newer_confirm_supersedes_an_older_adjustment():
    confirm = {'start': 120.0, 'end': 437.0, 'correction_type': 'confirm',
               'confirmed_span': {'start': 150.0, 'end': 390.0}}
    adjust = _adjust((120.0, 437.0), (100.0, 500.0))
    assert correction_skip_reason(adjust, newer=[confirm]) == 'superseded by a newer confirm'
    assert correction_skip_reason(confirm, newer=[adjust]) is None


def test_newer_confirm_on_an_adjacent_marker_does_not_supersede_an_adjustment():
    confirm = {'start': 629.0, 'end': 660.0, 'correction_type': 'confirm'}
    adjust = _adjust((600.0, 630.0), (600.0, 625.0))
    assert correction_skip_reason(adjust, newer=[confirm]) is None
    assert usable_confirmed_corrections([confirm, adjust], []) == [confirm, adjust]


def test_candidates_clamped_to_one_approved_span_yield_one_marker():
    span = {'start': 150.0, 'end': 390.0}
    confirmed = [
        {'start': 120.0, 'end': 437.0, 'correction_type': 'confirm',
         'confirmed_span': dict(span)},
        {'start': 300.0, 'end': 437.0, 'correction_type': 'confirm',
         'confirmed_span': dict(span)}]
    ads = [{'start': 120.0, 'end': 437.0, 'confidence': 0.5, 'reason': 'a',
            'detection_stage': 'claude'},
           {'start': 120.0, 'end': 437.0, 'confidence': 0.5, 'reason': 'b',
            'detection_stage': 'fingerprint'},
           {'start': 100.0, 'end': 450.0, 'confidence': 0.7, 'reason': 'c',
            'detection_stage': 'text_pattern'}]
    result = AdValidator(episode_duration=3600.0, confirmed_corrections=confirmed).validate(ads)
    accepted = [(a['start'], a['end']) for a in result.ads
                if a['validation']['decision'] == 'ACCEPT']
    assert accepted == [(150.0, 390.0)]
    assert result.accepted == 1
