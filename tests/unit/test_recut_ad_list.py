"""Unit tests for the recut cut-list helpers (issue #422)."""
import json
import shutil
import subprocess
import time
from contextlib import ExitStack
from unittest.mock import patch

import pytest

from tests.app_bootstrap import bootstrap

# bootstrap seeds OPENAI_MODEL so an app DB first created here has a model.
_test_data_dir = bootstrap('recut_test_')

from ad_chapters import AdChapterConfig
from config import PASS2_REVIEWED_RELEASE_HOLD_REASONS
from main_app import processing
from utils.markers import explicit_override

def _user_corrections(slug, episode_id):
    """The (fp, confirmed) corrections the test's db holds."""
    return processing._load_user_corrections(slug, episode_id, processing.db)



@pytest.fixture(autouse=True)
def _isolate_db(monkeypatch):
    """Pin the Database singleton to this module's dir per test so collection
    order cannot leave it bound to a sibling module's dir."""
    import database
    monkeypatch.setattr(database.Database, '_instance', None)
    monkeypatch.setenv('DATA_DIR', _test_data_dir)
    yield


def test_apply_boundary_adjustments_overrides_bounds(monkeypatch):
    ads = [{
        'start': 100.0,
        'end': 160.0,
        'confidence': 0.9,
        'dai_core_spans': [{'start': 100.0, 'end': 160.0}],
    }]
    corrections = [{
        'correction_type': 'boundary_adjustment',
        'original_bounds': {'start': 100.0, 'end': 160.0},
        'corrected_bounds': {'start': 105.0, 'end': 150.0},
    }]
    monkeypatch.setattr(processing.db, 'get_podcast_by_slug', lambda slug: {'id': 42})
    monkeypatch.setattr(
        processing.db, 'get_episode_corrections', lambda podcast_id, eid: corrections)
    processing._apply_boundary_adjustments('slug', 'ep', ads)
    assert ads[0]['start'] == 105.0
    assert ads[0]['end'] == 150.0
    assert ads[0]['dai_core_spans'] == [{'start': 105.0, 'end': 150.0}]


def test_apply_boundary_adjustments_narrows_the_merge_records(monkeypatch):
    ads = [{
        'start': 100.0,
        'end': 160.0,
        'merged_distinct_ads': True,
        'merged_protected_start': 100.0,
        'merged_protected_end': 160.0,
        'merged_member_spans': [
            {'start': 100.0, 'end': 120.0, 'stage': 'claude'},
            {'start': 140.0, 'end': 160.0, 'stage': 'claude'},
        ],
    }]
    corrections = [{
        'correction_type': 'boundary_adjustment',
        'original_bounds': {'start': 100.0, 'end': 160.0},
        'corrected_bounds': {'start': 105.0, 'end': 130.0},
    }]
    monkeypatch.setattr(processing.db, 'get_podcast_by_slug', lambda slug: {'id': 42})
    monkeypatch.setattr(
        processing.db, 'get_episode_corrections', lambda podcast_id, eid: corrections)

    processing._apply_boundary_adjustments('slug', 'ep', ads)

    assert ads[0]['merged_member_spans'] == [
        {'start': 105.0, 'end': 120.0, 'stage': 'claude'}]
    assert (ads[0]['merged_protected_start'],
            ads[0]['merged_protected_end']) == (105.0, 130.0)


def test_apply_boundary_adjustments_skips_unmatched(monkeypatch):
    ads = [{'start': 100.0, 'end': 160.0}]
    corrections = [{
        'correction_type': 'boundary_adjustment',
        'original_bounds': {'start': 900.0, 'end': 950.0},
        'corrected_bounds': {'start': 905.0, 'end': 940.0},
    }]
    monkeypatch.setattr(processing.db, 'get_podcast_by_slug', lambda slug: {'id': 42})
    monkeypatch.setattr(
        processing.db, 'get_episode_corrections', lambda podcast_id, eid: corrections)
    processing._apply_boundary_adjustments('slug', 'ep', ads)
    assert ads[0]['start'] == 100.0
    assert ads[0]['end'] == 160.0


def test_apply_boundary_adjustments_newest_wins(monkeypatch):
    ads = [{'start': 100.0, 'end': 160.0}]
    # get_episode_corrections returns newest first (ORDER BY created_at DESC).
    corrections = [
        {'correction_type': 'boundary_adjustment',
         'original_bounds': {'start': 100.0, 'end': 160.0},
         'corrected_bounds': {'start': 110.0, 'end': 150.0}},
        {'correction_type': 'boundary_adjustment',
         'original_bounds': {'start': 100.0, 'end': 160.0},
         'corrected_bounds': {'start': 101.0, 'end': 159.0}},
    ]
    monkeypatch.setattr(processing.db, 'get_podcast_by_slug', lambda slug: {'id': 42})
    monkeypatch.setattr(
        processing.db, 'get_episode_corrections', lambda podcast_id, eid: corrections)
    processing._apply_boundary_adjustments('slug', 'ep', ads)
    assert ads[0]['start'] == 110.0
    assert ads[0]['end'] == 150.0


def _boundary_adjustment(orig_start, orig_end, piece_start, piece_end):
    """A boundary_adjustment correction row."""
    return {'correction_type': 'boundary_adjustment',
            'original_bounds': {'start': orig_start, 'end': orig_end},
            'corrected_bounds': {'start': piece_start, 'end': piece_end}}


def _pin_corrections(monkeypatch, corrections):
    monkeypatch.setattr(processing.db, 'get_podcast_by_slug', lambda slug: {'id': 42})
    monkeypatch.setattr(
        processing.db, 'get_episode_corrections', lambda podcast_id, eid: corrections)


def test_apply_boundary_adjustments_leaves_split_pieces_alone(monkeypatch):
    # Issue #794: the longer second piece must not be remapped onto piece 0.
    ads = [{'start': 1000.0, 'end': 1150.0, 'sponsor': 'Acme'},
           {'start': 1150.0, 'end': 1400.0, 'sponsor': 'Acme'}]
    _pin_corrections(monkeypatch, [_boundary_adjustment(1000.0, 1400.0, 1000.0, 1150.0)])
    for _ in range(2):
        processing._apply_boundary_adjustments('slug', 'ep', ads)
        assert [(a['start'], a['end']) for a in ads] == [(1000.0, 1150.0), (1150.0, 1400.0)]


def test_apply_boundary_adjustments_leaves_three_split_pieces_alone(monkeypatch):
    ads = [{'start': 1000.0, 'end': 1100.0}, {'start': 1100.0, 'end': 1300.0},
           {'start': 1300.0, 'end': 1400.0}]
    _pin_corrections(monkeypatch, [_boundary_adjustment(1000.0, 1400.0, 1000.0, 1100.0)])
    processing._apply_boundary_adjustments('slug', 'ep', ads)
    assert [(a['start'], a['end']) for a in ads] == [
        (1000.0, 1100.0), (1100.0, 1300.0), (1300.0, 1400.0)]


def test_apply_boundary_adjustments_boundless_marker_not_satisfied(monkeypatch):
    ads = [{'start': None, 'end': None}]
    _pin_corrections(monkeypatch, [_boundary_adjustment(0.0, 20.0, 0.0, 10.0)])
    processing._apply_boundary_adjustments('slug', 'ep', ads)
    assert ads == [{'start': None, 'end': None}]


def test_apply_boundary_adjustments_already_trimmed_is_idempotent(monkeypatch):
    # The satisfied newest trim still shields the marker from an older one.
    ads = [{'start': 105.0, 'end': 150.0}]
    _pin_corrections(monkeypatch, [
        _boundary_adjustment(100.0, 160.0, 105.0, 150.0),
        _boundary_adjustment(100.0, 160.0, 101.0, 159.0),
    ])
    processing._apply_boundary_adjustments('slug', 'ep', ads)
    processing._apply_boundary_adjustments('slug', 'ep', ads)
    assert (ads[0]['start'], ads[0]['end']) == (105.0, 150.0)


def test_apply_boundary_adjustments_split_then_adjust_leaves_sibling(monkeypatch):
    ads = [{'start': 1000.0, 'end': 1100.0}, {'start': 1150.0, 'end': 1400.0}]
    _pin_corrections(monkeypatch, [
        _boundary_adjustment(1000.0, 1150.0, 1000.0, 1100.0),
        _boundary_adjustment(1000.0, 1400.0, 1000.0, 1150.0),
    ])
    processing._apply_boundary_adjustments('slug', 'ep', ads)
    assert [(a['start'], a['end']) for a in ads] == [(1000.0, 1100.0), (1150.0, 1400.0)]


def test_apply_boundary_adjustments_coincident_marker_does_not_steal(monkeypatch):
    target = {'start': 100.0, 'end': 160.0, 'sponsor': 'Acme'}
    other = {'start': 100.0, 'end': 130.0, 'sponsor': 'Other'}
    _pin_corrections(monkeypatch, [_boundary_adjustment(100.0, 160.0, 100.0, 130.0)])
    processing._apply_boundary_adjustments('slug', 'ep', [other, target])
    assert (target['start'], target['end']) == (100.0, 130.0)
    assert (other['start'], other['end']) == (100.0, 130.0)


def test_apply_boundary_adjustments_moves_single_marker_to_disjoint_span(monkeypatch):
    ads = [{'start': 100.0, 'end': 160.0}]
    _pin_corrections(monkeypatch, [_boundary_adjustment(100.0, 160.0, 200.0, 240.0)])
    processing._apply_boundary_adjustments('slug', 'ep', ads)
    assert (ads[0]['start'], ads[0]['end']) == (200.0, 240.0)


def test_apply_boundary_adjustments_skips_ambiguous_disjoint_move(monkeypatch, caplog):
    ads = [{'start': 1000.0, 'end': 1150.0}, {'start': 1150.0, 'end': 1400.0}]
    _pin_corrections(monkeypatch, [_boundary_adjustment(1000.0, 1400.0, 2000.0, 2100.0)])
    caplog.set_level('INFO')
    processing._apply_boundary_adjustments('slug', 'ep', ads)
    assert [(a['start'], a['end']) for a in ads] == [(1000.0, 1150.0), (1150.0, 1400.0)]
    assert 'spans several markers and matches none' in caplog.text


def test_build_recut_ad_list_cuts_both_split_pieces(monkeypatch):
    ads = [{'start': 1000.0, 'end': 1150.0, 'confidence': 1.0, 'sponsor': 'Acme',
            'reason': 'Split from 1000.0s-1400.0s block'},
           {'start': 1150.0, 'end': 1400.0, 'confidence': 1.0, 'sponsor': 'Acme',
            'reason': 'Split from 1000.0s-1400.0s block'}]
    _stub_recut_db(monkeypatch, ads, confirmed=[{
        'start': 1000.0, 'end': 1400.0, 'correction_type': 'boundary_adjustment',
        'confirmed_span': {'start': 1000.0, 'end': 1150.0}}])
    monkeypatch.setattr(
        processing.db, 'get_episode_corrections',
        lambda podcast_id, eid: [_boundary_adjustment(1000.0, 1400.0, 1000.0, 1150.0)])
    ads_to_remove, all_ads, *_ = processing._build_recut_ad_list(
        'slug', 'ep', [], 3600.0, '', 0.80,
        corrections=_user_corrections('slug', 'ep'))
    assert sorted((a['start'], a['end']) for a in ads_to_remove) == [
        (1000.0, 1150.0), (1150.0, 1400.0)]
    assert len(all_ads) == 2


def test_explicit_override_recognises_split_piece_zero():
    confirmed = [{'start': 1000.0, 'end': 1400.0, 'correction_type': 'boundary_adjustment',
                  'confirmed_span': {'start': 1000.0, 'end': 1150.0}}]
    assert explicit_override({'start': 1000.0, 'end': 1150.0}, confirmed)


def test_final_confirmed_bounds_sync_cut_and_master(monkeypatch):
    cut = {
        'start': 1361.5,
        'end': 1406.753,
        'confidence': 0.95,
        'dai_core_spans': [{'start': 1361.5, 'end': 1406.4}],
        'end_extended_by_content': True,
        'tail_splice_snap': {'event_time': 1407.5},
        'validation': {
            'decision': 'ACCEPT',
            'user_confirmed': True,
            'flags': ['INFO: User confirmed as ad'],
        },
    }
    master = dict(cut)
    master['validation'] = dict(cut['validation'])
    master['validation']['flags'] = list(cut['validation']['flags'])
    corrections = [{
        'start': 1361.654,
        'end': 1409.104,
        'confirmed_span': {'start': 1361.5, 'end': 1408.93},
    }]
    saves = []
    monkeypatch.setattr(
        processing.storage, 'save_combined_ads',
        lambda *args: saves.append(args))

    result = processing._finalize_user_confirmed_bounds(
        'feed', 'episode', [cut], [master], corrections)

    assert result[0]['start'] == 1361.5
    assert result[0]['end'] == 1408.93
    assert master['start'] == 1361.5
    assert master['end'] == 1408.93
    assert result[0]['dai_core_spans'] == [
        {'start': 1361.5, 'end': 1406.4}]
    assert 'end_extended_by_content' not in result[0]
    assert 'tail_splice_snap' not in result[0]
    assert 'INFO: Finalized to user-approved span' in (
        master['validation']['flags'])
    assert len(saves) == 1


def test_plain_confirmed_fragments_keep_their_own_bounds(monkeypatch):
    first = {
        'start': 100.4,
        'end': 120.0,
        'validation': {
            'decision': 'ACCEPT',
            'user_confirmed': True,
            'confirmed_span': {'start': 100.4, 'end': 120.0},
            'flags': [],
        },
    }
    second = {
        'start': 150.0,
        'end': 170.0,
        'validation': {
            'decision': 'ACCEPT',
            'user_confirmed': True,
            'confirmed_span': {'start': 150.0, 'end': 170.0},
            'flags': [],
        },
    }
    saves = []
    monkeypatch.setattr(
        processing.storage, 'save_combined_ads',
        lambda *args: saves.append(args))

    result = processing._finalize_user_confirmed_bounds(
        'feed', 'episode', [first, second], [first, second], [])

    assert [(ad['start'], ad['end']) for ad in result] == [
        (100.4, 120.0), (150.0, 170.0)]
    assert len(saves) == 1


def test_final_confirmed_bounds_ignores_untrusted_marker(monkeypatch):
    marker = {
        'start': 1361.5,
        'end': 1406.753,
        'validation': {'decision': 'ACCEPT', 'flags': []},
    }
    corrections = [{
        'start': 1361.654,
        'end': 1409.104,
        'confirmed_span': {'start': 1361.5, 'end': 1408.93},
    }]
    monkeypatch.setattr(
        processing.storage, 'save_combined_ads',
        lambda *args: pytest.fail('unchanged markers must not be persisted'))

    processing._finalize_user_confirmed_bounds(
        'feed', 'episode', [marker], [marker], corrections)

    assert marker['end'] == 1406.753


def test_final_confirmed_bounds_uses_carried_span_after_large_extension(monkeypatch):
    marker = {
        'start': 101.0,
        'end': 141.0,
        'validation': {
            'decision': 'ACCEPT',
            'user_confirmed': True,
            'confirmed_span': {'start': 101.0, 'end': 111.0},
            'flags': [],
        },
    }
    corrections = [{
        'start': 100.0,
        'end': 112.0,
        'confirmed_span': {'start': 101.0, 'end': 111.0},
    }]
    monkeypatch.setattr(processing.storage, 'save_combined_ads', lambda *args: None)

    processing._finalize_user_confirmed_bounds(
        'feed', 'episode', [marker], [marker], corrections)

    assert marker['start'] == 101.0
    assert marker['end'] == 111.0


def test_final_confirmed_bounds_narrows_the_merge_records(monkeypatch):
    marker = {
        'start': 100.0,
        'end': 200.0,
        'merged_distinct_ads': True,
        'merged_protected_start': 100.0,
        'merged_protected_end': 200.0,
        'merged_member_spans': [
            {'start': 100.0, 'end': 140.0, 'stage': 'claude'},
            {'start': 160.0, 'end': 200.0, 'stage': 'claude'},
        ],
        'validation': {
            'decision': 'ACCEPT',
            'user_confirmed': True,
            'confirmed_span': {'start': 100.0, 'end': 140.0},
            'flags': [],
        },
    }
    corrections = [{
        'start': 100.0, 'end': 200.0,
        'confirmed_span': {'start': 100.0, 'end': 140.0},
    }]
    monkeypatch.setattr(processing.storage, 'save_combined_ads', lambda *args: None)

    processing._finalize_user_confirmed_bounds(
        'feed', 'episode', [marker], [marker], corrections)

    assert (marker['start'], marker['end']) == (100.0, 140.0)
    assert marker['merged_member_spans'] == [
        {'start': 100.0, 'end': 140.0, 'stage': 'claude'}]
    assert marker['merged_protected_end'] == 140.0


def test_final_confirmed_bounds_does_not_restore_older_trim_after_new_plain_confirm(
        monkeypatch):
    marker = {
        'start': 100.0,
        'end': 200.0,
        'validation': {
            'decision': 'ACCEPT',
            'user_confirmed': True,
            'flags': [],
        },
    }
    corrections = [
        {'start': 100.0, 'end': 200.0},
        {
            'start': 100.0,
            'end': 200.0,
            'confirmed_span': {'start': 120.0, 'end': 180.0},
        },
    ]
    monkeypatch.setattr(
        processing.storage, 'save_combined_ads',
        lambda *args: pytest.fail('newest plain confirm must keep its bounds'))

    processing._finalize_user_confirmed_bounds(
        'feed', 'episode', [marker], [marker], corrections)

    assert marker['start'] == 100.0
    assert marker['end'] == 200.0


def test_final_confirmed_bounds_clamps_carried_span_to_episode(monkeypatch):
    marker = {
        'start': 101.0,
        'end': 111.0,
        'validation': {
            'decision': 'ACCEPT',
            'user_confirmed': True,
            'confirmed_span': {'start': 101.0, 'end': 111.0},
            'flags': [],
        },
    }
    monkeypatch.setattr(processing.storage, 'save_combined_ads', lambda *args: None)

    processing._finalize_user_confirmed_bounds(
        'feed', 'episode', [marker], [marker], [], episode_duration=105.0)

    assert marker['start'] == 101.0
    assert marker['end'] == 105.0


def test_final_confirmed_bounds_persists_metadata_only_cleanup(monkeypatch):
    marker = {
        'start': 101.0,
        'end': 111.0,
        'end_extended_by_content': True,
        'tail_splice_snap': {'event_time': 111.0},
        'dai_core_spans': [{'start': 100.0, 'end': 112.0}],
        'validation': {
            'decision': 'ACCEPT',
            'user_confirmed': True,
            'confirmed_span': {'start': 101.0, 'end': 111.0},
            'flags': [],
        },
    }
    saves = []
    monkeypatch.setattr(
        processing.storage, 'save_combined_ads',
        lambda *args: saves.append(args))

    processing._finalize_user_confirmed_bounds(
        'feed', 'episode', [marker], [marker], [])

    assert 'end_extended_by_content' not in marker
    assert 'tail_splice_snap' not in marker
    assert marker['dai_core_spans'] == [{'start': 101.0, 'end': 111.0}]
    assert len(saves) == 1


def test_final_confirmed_bounds_ignores_collapsed_duration_clamp(monkeypatch):
    marker = {
        'start': 101.0,
        'end': 111.0,
        'validation': {
            'decision': 'ACCEPT',
            'user_confirmed': True,
            'confirmed_span': {'start': 101.0, 'end': 111.0},
            'flags': [],
        },
    }
    monkeypatch.setattr(
        processing.storage, 'save_combined_ads',
        lambda *args: pytest.fail('a collapsed clamp must not be persisted'))

    processing._finalize_user_confirmed_bounds(
        'feed', 'episode', [marker], [marker], [], episode_duration=100.0)

    assert marker['start'] == 101.0
    assert marker['end'] == 111.0


def test_final_confirmed_bounds_master_fallback_requires_overlap(monkeypatch):
    approved = {'start': 101.0, 'end': 111.0}

    def marker(start, end):
        return {
            'start': start,
            'end': end,
            'validation': {
                'decision': 'ACCEPT',
                'user_confirmed': True,
                'confirmed_span': approved,
                'flags': [],
            },
        }

    cut = marker(101.0, 141.0)
    unrelated = marker(500.0, 510.0)
    related_master = marker(100.0, 140.0)
    monkeypatch.setattr(processing.storage, 'save_combined_ads', lambda *args: None)

    processing._finalize_user_confirmed_bounds(
        'feed', 'episode', [cut], [unrelated, related_master], [])

    assert cut['end'] == 111.0
    assert (related_master['start'], related_master['end']) == (101.0, 111.0)
    assert (unrelated['start'], unrelated['end']) == (500.0, 510.0)


def test_build_recut_ad_list_drops_rejected(monkeypatch):
    ads = [
        {'start': 30.0, 'end': 90.0, 'confidence': 0.98, 'sponsor': 'A', 'reason': 'sponsor read for A'},
        {'start': 300.0, 'end': 360.0, 'confidence': 0.98, 'sponsor': 'B', 'reason': 'sponsor read for B'},
    ]
    monkeypatch.setattr(processing.db, 'get_episode', lambda s, e: {'ad_markers_json': json.dumps(ads)})
    monkeypatch.setattr(processing.db, 'get_podcast_by_slug', lambda slug: {'id': 42})
    monkeypatch.setattr(
        processing.db, 'get_episode_corrections', lambda podcast_id, eid: [])
    monkeypatch.setattr(processing.db, 'get_false_positive_corrections',
                        lambda podcast_id, eid: [{'start': 300.0, 'end': 360.0}])
    monkeypatch.setattr(
        processing.db, 'get_confirmed_corrections', lambda podcast_id, eid: [])
    segments = [
        {'start': 30.0, 'end': 90.0, 'text': 'sponsor a'},
        {'start': 300.0, 'end': 360.0, 'text': 'sponsor b'},
    ]
    ads_to_remove, all_ads, *_ = processing._build_recut_ad_list(
        'slug', 'ep', segments, 600.0, '', 0.80,
        corrections=_user_corrections('slug', 'ep')
    )
    starts = {a['start'] for a in ads_to_remove}
    assert 30.0 in starts
    assert 300.0 not in starts
    assert len(all_ads) == 2  # rejected ad stays in the list, just not cut


def test_build_recut_ad_list_keeps_confirmed(monkeypatch):
    # A low-confidence ad the user confirmed must still be cut.
    ads = [{'start': 30.0, 'end': 90.0, 'confidence': 0.40, 'sponsor': 'A', 'reason': 'maybe an ad for A'}]
    monkeypatch.setattr(processing.db, 'get_episode', lambda s, e: {'ad_markers_json': json.dumps(ads)})
    monkeypatch.setattr(processing.db, 'get_podcast_by_slug', lambda slug: {'id': 42})
    monkeypatch.setattr(
        processing.db, 'get_episode_corrections', lambda podcast_id, eid: [])
    monkeypatch.setattr(
        processing.db, 'get_false_positive_corrections', lambda podcast_id, eid: [])
    monkeypatch.setattr(
        processing.db, 'get_confirmed_corrections',
        lambda podcast_id, eid: [{'start': 30.0, 'end': 90.0}])
    segments = [{'start': 30.0, 'end': 90.0, 'text': 'maybe an ad'}]
    ads_to_remove, _, *_ = processing._build_recut_ad_list('slug', 'ep', segments, 600.0, '', 0.80, corrections=_user_corrections('slug', 'ep'))
    assert {a['start'] for a in ads_to_remove} == {30.0}


def test_build_recut_ad_list_keeps_manual_add(monkeypatch):
    # A manually-added marker (confidence 1.0, detection_stage 'manual') must be cut.
    ads = [{'start': 120.0, 'end': 180.0, 'confidence': 1.0, 'detection_stage': 'manual',
            'sponsor': 'Manual Co', 'reason': 'Manual Co: manually added ad'}]
    monkeypatch.setattr(processing.db, 'get_episode', lambda s, e: {'ad_markers_json': json.dumps(ads)})
    monkeypatch.setattr(processing.db, 'get_podcast_by_slug', lambda slug: {'id': 42})
    monkeypatch.setattr(
        processing.db, 'get_episode_corrections', lambda podcast_id, eid: [])
    monkeypatch.setattr(
        processing.db, 'get_false_positive_corrections', lambda podcast_id, eid: [])
    monkeypatch.setattr(
        processing.db, 'get_confirmed_corrections', lambda podcast_id, eid: [])
    segments = [{'start': 120.0, 'end': 180.0, 'text': 'manual'}]
    ads_to_remove, _, *_ = processing._build_recut_ad_list('slug', 'ep', segments, 600.0, '', 0.80, corrections=_user_corrections('slug', 'ep'))
    assert {a['start'] for a in ads_to_remove} == {120.0}


def test_build_recut_ad_list_empty_when_no_markers(monkeypatch):
    monkeypatch.setattr(processing.db, 'get_episode', lambda s, e: {'ad_markers_json': None})
    assert processing._build_recut_ad_list('slug', 'ep', [], 600.0, '', 0.80, corrections=_user_corrections('slug', 'ep')) == ([], [], [], [])


def _stub_assets_io(monkeypatch, counters):
    import chapters_generator
    monkeypatch.setattr(chapters_generator.ChaptersGenerator, 'generate_chapters',
                        lambda self, *a, **k: counters.__setitem__('chapters', counters.get('chapters', 0) + 1) or {'chapters': []})
    monkeypatch.setattr(processing.db, 'get_setting', lambda k: 'true')
    monkeypatch.setattr(processing.storage, 'save_final_segments', lambda *a, **k: None)
    monkeypatch.setattr(processing.storage, 'save_transcript_vtt', lambda *a, **k: None)
    monkeypatch.setattr(processing.storage, 'save_chapters_and_applied_cuts',
                        lambda *a, **k: counters.__setitem__('save_chapters', counters.get('save_chapters', 0) + 1))
    monkeypatch.setattr(processing.db, 'save_episode_details', lambda *a, **k: None)


def _stub_recut_db(monkeypatch, ads, fp=None, confirmed=None, overrides=None):
    """Shared monkeypatch helper for _build_recut_ad_list hold tests."""
    import json as _json
    monkeypatch.setattr(processing.db, 'get_episode',
                        lambda s, e: {'ad_markers_json': _json.dumps(ads)})
    monkeypatch.setattr(
        processing.db, 'get_episode_corrections', lambda podcast_id, eid: [])
    monkeypatch.setattr(processing.db, 'get_false_positive_corrections',
                        lambda podcast_id, eid: fp or [])
    monkeypatch.setattr(processing.db, 'get_confirmed_corrections',
                        lambda podcast_id, eid: confirmed or [])
    # Simulate per-feed settings overrides (or empty = both unset)
    monkeypatch.setattr(processing.db, 'get_podcast_by_slug',
                        lambda s: {'id': 42})
    monkeypatch.setattr(processing.db, 'get_podcast_cue_settings_overrides',
                        lambda pid: overrides or {})


def test_build_recut_held_confirm_is_cut(monkeypatch):
    # held ad + confirm correction -> FP/confirm early-return wins -> ACCEPT -> cut
    ads = [{'start': 100.0, 'end': 400.0, 'confidence': 0.95,
            'reason': 'BetterHelp sponsor', 'held_for_review': True,
            'hold_reason': 'max_duration'}]
    _stub_recut_db(monkeypatch, ads,
                   confirmed=[{'start': 100.0, 'end': 400.0}],
                   overrides={'max_ad_duration_override': 240.0})
    ads_to_remove, _, *_ = processing._build_recut_ad_list(
        'slug', 'ep', [], 3600.0, '', 0.80,
        corrections=_user_corrections('slug', 'ep')
    )
    assert {a['start'] for a in ads_to_remove} == {100.0}, (
        "Confirmed held ad must be cut on recut"
    )


def test_build_recut_held_fp_is_uncut_reject(monkeypatch):
    # held ad + FP correction -> FP early-return wins -> REJECT -> not cut
    ads = [{'start': 100.0, 'end': 400.0, 'confidence': 0.95,
            'reason': 'BetterHelp sponsor', 'held_for_review': True,
            'hold_reason': 'max_duration'}]
    _stub_recut_db(monkeypatch, ads,
                   fp=[{'start': 100.0, 'end': 400.0}],
                   overrides={'max_ad_duration_override': 240.0})
    ads_to_remove, all_ads, *_ = processing._build_recut_ad_list(
        'slug', 'ep', [], 3600.0, '', 0.80,
        corrections=_user_corrections('slug', 'ep')
    )
    assert ads_to_remove == [], "FP-corrected held ad must not be cut"
    assert not all_ads[0].get('held_for_review'), "FP path must clear stale held flag"


def test_build_recut_respects_splice_veto_disabled(monkeypatch):
    """Finding 6: recut must read splice_veto_enabled from DB settings, not
    silently use the code default (True). When the operator disabled the veto,
    an evidence-less long claude cut must be cut on recut, not held."""
    # 90s claude cut: calibrated feed, no splice events -> would be vetoed if
    # splice_veto_enabled defaults to True (the bug). With the setting read as
    # False it must not be held.
    ads = [{'start': 1800.0, 'end': 1890.0, 'confidence': 0.92,
            'detection_stage': 'claude',
            'reason': 'Vrbo vacation rental read with booking details'}]
    analysis = {'splice_evidence': {'version': 1, 'events': [],
                                    'calibration': {'status': 'calibrated'}}}
    _stub_recut_db(monkeypatch, ads)
    monkeypatch.setattr(processing.db, 'get_episode_audio_analysis',
                        lambda s, e: json.dumps(analysis))
    monkeypatch.setattr(processing.db, 'get_setting_bool',
                        lambda k, **kw: (False if k == 'splice_veto_enabled'
                                         else kw.get('default', False)))
    monkeypatch.setattr(processing.db, 'get_setting_float',
                        lambda k, default=None: default)
    try:
        monkeypatch.setattr(processing.db, 'get_episode_dai_differential',
                            lambda s, e: None)
    except AttributeError:
        pass
    segments = [{'start': 1800.0, 'end': 1890.0, 'text': 'vacation rental'}]
    ads_to_remove, all_ads, *_ = processing._build_recut_ad_list(
        'slug', 'ep', segments, 3600.0, '', 0.80,
        corrections=_user_corrections('slug', 'ep')
    )
    assert len(ads_to_remove) == 1, (
        "splice_veto_enabled=False must not veto the cut on recut"
    )
    assert all_ads[0].get('hold_reason') != 'no_splice_evidence'


def test_build_recut_held_nothing_stays_held_uncut(monkeypatch):
    # held ad + no correction -> re-held by validator -> gate keeps it
    ads = [{'start': 100.0, 'end': 400.0, 'confidence': 0.95,
            'reason': 'BetterHelp sponsor'}]
    _stub_recut_db(monkeypatch, ads,
                   overrides={'max_ad_duration_override': 240.0})
    ads_to_remove, all_ads, *_ = processing._build_recut_ad_list(
        'slug', 'ep', [], 3600.0, '', 0.80,
        corrections=_user_corrections('slug', 'ep')
    )
    assert ads_to_remove == [], "Held ad with no correction must not be cut"
    assert all_ads[0].get('held_for_review') is True
    assert all_ads[0].get('was_cut') is False


def test_build_recut_manual_on_cue_gated_feed_is_cut(monkeypatch):
    # detection_stage='manual' is exempt from cue gating -> still cut
    ads = [{'start': 120.0, 'end': 180.0, 'confidence': 1.0,
            'detection_stage': 'manual',
            'reason': 'Manual Co: manually added ad'}]
    _stub_recut_db(monkeypatch, ads,
                   overrides={'cue_gated_approval': 1})
    ads_to_remove, _, *_ = processing._build_recut_ad_list(
        'slug', 'ep', [], 3600.0, '', 0.80,
        corrections=_user_corrections('slug', 'ep')
    )
    assert {a['start'] for a in ads_to_remove} == {120.0}, (
        "Manual ad must be cut even on a cue-gated feed"
    )


def test_gate_held_ad_not_cut_despite_high_confidence():
    # Regression: a held ad with adjusted_confidence >= min_cut_confidence must
    # NOT be cut. Before the gate patch, the REVIEW branch fell through to CUT
    # when confidence was above the threshold.
    ad = {
        'start': 100.0,
        'end': 200.0,
        'confidence': 0.95,
        'held_for_review': True,
        'hold_reason': 'max_duration',
        'validation': {
            'decision': 'REVIEW',
            'adjusted_confidence': 0.95,
        },
    }
    ads_to_remove, _ = processing._gate_validation_by_confidence(
        'slug', 'ep', [ad], 0.80
    )
    assert ads_to_remove == [], (
        "Held ad must not appear in ads_to_remove regardless of confidence"
    )
    assert ad['was_cut'] is False


def test_build_recut_previously_cut_stays_cut_when_cue_gate_enabled(monkeypatch):
    # A marker cut in the saved state must NOT flip to held when cue gating is
    # newly enabled: the ad is already gone from the published audio.
    ads = [{'start': 100.0, 'end': 160.0, 'confidence': 0.95,
            'reason': 'promotional read', 'was_cut': True}]
    _stub_recut_db(monkeypatch, ads, overrides={'cue_gated_approval': 1})
    ads_to_remove, all_ads, *_ = processing._build_recut_ad_list(
        'slug', 'ep', [], 3600.0, '', 0.80,
        corrections=_user_corrections('slug', 'ep')
    )
    assert {a['start'] for a in ads_to_remove} == {100.0}, (
        "Previously-cut ad must still be cut on recut with cue gate on"
    )
    assert not all_ads[0].get('held_for_review'), (
        "Previously-cut ad must not be resurrected as held"
    )


def test_build_recut_previously_cut_stays_cut_after_boundary_clamp(monkeypatch):
    # A previously-cut ad whose end overruns the episode gets clamped by the
    # validator. Keying the resurrection guard by raw span would miss the
    # clamped ad and it would flip to held; it must still be cut.
    ads = [{'start': 3540.0, 'end': 3603.0, 'confidence': 0.95,
            'reason': 'promotional read', 'was_cut': True}]
    _stub_recut_db(monkeypatch, ads, overrides={'cue_gated_approval': 1})
    ads_to_remove, all_ads, *_ = processing._build_recut_ad_list(
        'slug', 'ep', [], 3600.0, '', 0.80,
        corrections=_user_corrections('slug', 'ep')
    )
    assert ads_to_remove, "Previously-cut ad must still be cut after clamp"
    assert not all_ads[0].get('held_for_review'), (
        "Clamped previously-cut ad must not resurrect as held"
    )


def test_build_recut_preserves_trusted_short_cut(monkeypatch):
    ads = [{
        'start': 100.0, 'end': 106.0, 'confidence': 0.85,
        'reason': 'fragment split around beep audio', 'was_cut': True,
        '_measured_split_fragment': True,
    }]
    _stub_recut_db(monkeypatch, ads)

    ads_to_remove, all_ads, *_ = processing._build_recut_ad_list(
        'slug', 'ep', [], 3600.0, '', 0.80,
        corrections=_user_corrections('slug', 'ep')
    )

    assert ads_to_remove == all_ads
    assert all_ads[0]['validation']['decision'] == 'ACCEPT'
    assert all_ads[0]['was_cut'] is True


def test_build_recut_trusted_short_cut_still_honors_fp(monkeypatch):
    ads = [{
        'start': 100.0, 'end': 106.0, 'confidence': 0.85,
        'reason': 'fragment split around beep audio', 'was_cut': True,
        '_measured_split_fragment': True,
    }]
    _stub_recut_db(monkeypatch, ads, fp=[{'start': 100.0, 'end': 106.0}])

    ads_to_remove, all_ads, *_ = processing._build_recut_ad_list(
        'slug', 'ep', [], 3600.0, '', 0.80,
        corrections=_user_corrections('slug', 'ep')
    )

    assert ads_to_remove == []
    assert all_ads[0]['validation']['decision'] == 'REJECT'
    assert all_ads[0]['was_cut'] is False


def test_build_recut_no_merge_across_saved_cut_status_keeps_both_outcomes(monkeypatch):
    # Must NOT merge: folding would let the previously-cut stamp force-accept
    # the whole span. Each keeps its own outcome.
    ads = [
        {'start': 100.0, 'end': 160.0, 'confidence': 0.95,
         'reason': 'promo one'},  # not previously cut
        {'start': 162.0, 'end': 200.0, 'confidence': 0.95,
         'reason': 'promo two', 'was_cut': True},  # previously cut
    ]
    _stub_recut_db(monkeypatch, ads, overrides={'cue_gated_approval': 1})
    ads_to_remove, all_ads, *_ = processing._build_recut_ad_list(
        'slug', 'ep', [], 3600.0, '', 0.80,
        corrections=_user_corrections('slug', 'ep')
    )
    assert {a['start'] for a in ads_to_remove} == {162.0}, (
        "Previously-cut span must still be cut; the never-cut span must not"
    )
    assert len(all_ads) == 2
    first, second = sorted(all_ads, key=lambda a: a['start'])
    assert first.get('held_for_review'), "Never-cut span must be held for review"
    assert not second.get('held_for_review'), (
        "Previously-cut span must not resurrect as held"
    )


def test_build_recut_previously_cut_review_not_held_by_cue_gate(monkeypatch):
    # A previously-cut ad that re-validates to REVIEW (below threshold) must not
    # be newly held by the cue-gate fall-through -- it was already published.
    ads = [{'start': 500.0, 'end': 560.0, 'confidence': 0.60,
            'reason': 'possible sponsor mention', 'was_cut': True}]
    _stub_recut_db(monkeypatch, ads, overrides={'cue_gated_approval': 1})
    ads_to_remove, all_ads, *_ = processing._build_recut_ad_list(
        'slug', 'ep', [], 3600.0, '', 0.80,
        corrections=_user_corrections('slug', 'ep')
    )
    assert not all_ads[0].get('held_for_review'), (
        "Previously-cut REVIEW ad must not be newly held by the cue gate"
    )


def test_build_recut_previously_held_still_re_held(monkeypatch):
    # A previously-held marker keeps full hold-rule re-derivation.
    ads = [{'start': 100.0, 'end': 400.0, 'confidence': 0.95,
            'reason': 'promotional read', 'was_cut': False,
            'held_for_review': True, 'hold_reason': 'max_duration'}]
    _stub_recut_db(monkeypatch, ads,
                   overrides={'max_ad_duration_override': 240.0})
    ads_to_remove, all_ads, *_ = processing._build_recut_ad_list(
        'slug', 'ep', [], 3600.0, '', 0.80,
        corrections=_user_corrections('slug', 'ep')
    )
    assert ads_to_remove == []
    assert all_ads[0].get('held_for_review') is True
    assert all_ads[0].get('was_cut') is False


def test_gate_review_fallthrough_no_cue_is_held_on_cue_gated_feed():
    # A REVIEW ad whose rounded adjusted_confidence >= slider must NOT be cut by
    # the fall-through on a cue-gated feed when it has no cue evidence: hold it.
    ad = {
        'start': 500.0, 'end': 560.0, 'confidence': 0.80,
        'validation': {'decision': 'REVIEW', 'adjusted_confidence': 0.80},
    }
    ads_to_remove, _ = processing._gate_validation_by_confidence(
        'slug', 'ep', [ad], 0.80, cue_gate_enabled=True
    )
    assert ads_to_remove == [], "No-cue REVIEW ad must not be cut on a cue-gated feed"
    assert ad['was_cut'] is False
    assert ad['held_for_review'] is True
    assert ad['hold_reason'] == 'no_cue_evidence'


def test_gate_review_fallthrough_cue_backed_is_cut_on_cue_gated_feed():
    # Cue-backed REVIEW ad at/over threshold is still cut via the fall-through.
    ad = {
        'start': 500.0, 'end': 560.0, 'confidence': 0.80,
        'cue_snap': {'start': 498.0, 'end': 562.0},
        'validation': {'decision': 'REVIEW', 'adjusted_confidence': 0.80},
    }
    ads_to_remove, _ = processing._gate_validation_by_confidence(
        'slug', 'ep', [ad], 0.80, cue_gate_enabled=True
    )
    assert {a['start'] for a in ads_to_remove} == {500.0}
    assert ad['was_cut'] is True


def test_gate_review_fallthrough_no_cue_cut_when_gate_off():
    # Gate disabled -> fall-through cuts as before, no hold.
    ad = {
        'start': 500.0, 'end': 560.0, 'confidence': 0.80,
        'validation': {'decision': 'REVIEW', 'adjusted_confidence': 0.80},
    }
    ads_to_remove, _ = processing._gate_validation_by_confidence(
        'slug', 'ep', [ad], 0.80, cue_gate_enabled=False
    )
    assert {a['start'] for a in ads_to_remove} == {500.0}
    assert not ad.get('held_for_review')


def _spy_validate(monkeypatch, captured):
    """Wrap AdValidator.validate to record the audio_analysis it receives,
    then delegate to the real implementation so downstream code is unchanged."""
    import ad_validator
    orig = ad_validator.AdValidator.validate

    def spy(self, ads_arg, audio_analysis=None, actions_map=None):
        captured['called'] = True
        captured['audio_analysis'] = audio_analysis
        return orig(self, ads_arg, audio_analysis=audio_analysis, actions_map=actions_map)

    monkeypatch.setattr(ad_validator.AdValidator, 'validate', spy)


def test_build_recut_merges_dai_differential_into_audio_analysis(monkeypatch):
    # Happy path: db returns valid dai_differential_json -> the dict passed to
    # validate carries audio_analysis['dai_differential'] with the regions.
    ads = [{'start': 100.0, 'end': 160.0, 'confidence': 0.95,
            'reason': 'promotional read', 'sponsor': 'X'}]
    _stub_recut_db(monkeypatch, ads)
    dd = {'status': 'ok', 'regions': [
        {'start_s': 100.0, 'end_s': 160.0, 'kind': 'differential', 'corr': 0.0}]}
    monkeypatch.setattr(processing.db, 'get_episode_audio_analysis', lambda s, e: None)
    monkeypatch.setattr(processing.db, 'get_episode_dai_differential',
                        lambda s, e: json.dumps(dd))
    captured = {}
    _spy_validate(monkeypatch, captured)
    processing._build_recut_ad_list('slug', 'ep', [], 3600.0, '', 0.80, corrections=_user_corrections('slug', 'ep'))
    assert captured['called']
    assert captured['audio_analysis'] is not None
    assert 'dai_differential' in captured['audio_analysis']
    regions = captured['audio_analysis']['dai_differential']['regions']
    assert regions[0]['kind'] == 'differential'
    assert regions[0]['start_s'] == 100.0


def test_build_recut_dai_differential_none_does_not_crash(monkeypatch):
    # db.get_episode_dai_differential returns None -> recut proceeds, validate
    # still called, no dai_differential key on the merged dict.
    ads = [{'start': 100.0, 'end': 160.0, 'confidence': 0.95,
            'reason': 'promotional read', 'sponsor': 'X'}]
    _stub_recut_db(monkeypatch, ads)
    monkeypatch.setattr(processing.db, 'get_episode_audio_analysis', lambda s, e: None)
    monkeypatch.setattr(processing.db, 'get_episode_dai_differential', lambda s, e: None)
    captured = {}
    _spy_validate(monkeypatch, captured)
    processing._build_recut_ad_list('slug', 'ep', [], 3600.0, '', 0.80, corrections=_user_corrections('slug', 'ep'))
    assert captured['called']
    assert captured['audio_analysis'] is None


def test_build_recut_dai_differential_attribute_error_does_not_crash(monkeypatch):
    # db is None or the method is absent on an older db -> AttributeError is
    # swallowed, recut proceeds, validate still called.
    ads = [{'start': 100.0, 'end': 160.0, 'confidence': 0.95,
            'reason': 'promotional read', 'sponsor': 'X'}]
    _stub_recut_db(monkeypatch, ads)
    monkeypatch.setattr(processing.db, 'get_episode_audio_analysis', lambda s, e: None)

    def _raise(s, e):
        raise AttributeError("'NoneType' object has no attribute 'get_episode_dai_differential'")

    monkeypatch.setattr(processing.db, 'get_episode_dai_differential', _raise)
    captured = {}
    _spy_validate(monkeypatch, captured)
    processing._build_recut_ad_list('slug', 'ep', [], 3600.0, '', 0.80, corrections=_user_corrections('slug', 'ep'))
    assert captured['called']
    assert captured['audio_analysis'] is None


def test_build_recut_dai_differential_malformed_json_does_not_crash(monkeypatch):
    # Malformed JSON string -> ValueError swallowed, recut proceeds, validate
    # still called, no dai_differential key.
    ads = [{'start': 100.0, 'end': 160.0, 'confidence': 0.95,
            'reason': 'promotional read', 'sponsor': 'X'}]
    _stub_recut_db(monkeypatch, ads)
    monkeypatch.setattr(processing.db, 'get_episode_audio_analysis', lambda s, e: None)
    monkeypatch.setattr(processing.db, 'get_episode_dai_differential',
                        lambda s, e: '{not valid json')
    captured = {}
    _spy_validate(monkeypatch, captured)
    processing._build_recut_ad_list('slug', 'ep', [], 3600.0, '', 0.80, corrections=_user_corrections('slug', 'ep'))
    assert captured['called']
    assert captured['audio_analysis'] is None


def test_generate_assets_skips_chapters_when_disabled(monkeypatch):
    # Recut path: no AI chapter call, no chapter write.
    counters = {}
    _stub_assets_io(monkeypatch, counters)
    segments = [{'start': 0.0, 'end': 30.0, 'text': 'hello world'}]
    processing._generate_assets('slug', 'ep', segments, [], '', 'Pod', 'Title',
                                regenerate_chapters=False)
    assert counters.get('chapters', 0) == 0
    assert counters.get('save_chapters', 0) == 0


def test_generate_assets_generates_chapters_by_default(monkeypatch):
    # Main pipeline path: chapters still generated.
    counters = {}
    _stub_assets_io(monkeypatch, counters)
    segments = [{'start': 0.0, 'end': 30.0, 'text': 'hello world'}]
    processing._generate_assets('slug', 'ep', segments, [], '', 'Pod', 'Title')
    assert counters.get('chapters', 0) == 1


def test_generate_assets_embeds_chapters_into_audio(monkeypatch):
    # Main pipeline passes the final MP3 path; generated chapters are also
    # embedded as ID3 frames (issue #523).
    counters = {}
    _stub_assets_io(monkeypatch, counters)
    import chapters_generator
    monkeypatch.setattr(
        chapters_generator.ChaptersGenerator, 'generate_chapters',
        lambda self, *a, **k: {'chapters': [{'startTime': 0, 'title': 'Intro'}]})
    embedded = {}
    monkeypatch.setattr(processing, 'embed_chapters',
                        lambda path, chapters, duration=None: embedded.update(
                            path=path, chapters=chapters, duration=duration) or True)
    segments = [{'start': 0.0, 'end': 30.0, 'text': 'hello world'}]
    # chapters_mode='generate' forces the generator path deterministically:
    # default 'auto' would probe audio_path for publisher chapters first,
    # and this path does not exist on disk so the probe would fail (#560).
    processing._generate_assets('slug', 'ep', segments, [], '', 'Pod', 'Title',
                                audio_path='/data/slug/episodes/ep-v1.mp3',
                                audio_duration=1800.0,
                                podcast_row={'chapters_mode': 'generate'})
    assert embedded == {'path': '/data/slug/episodes/ep-v1.mp3',
                        'chapters': [{'startTime': 0, 'title': 'Intro'}],
                        'duration': 1800.0}


def test_generate_assets_skips_embed_without_audio_path(monkeypatch):
    counters = {}
    _stub_assets_io(monkeypatch, counters)
    import chapters_generator
    monkeypatch.setattr(
        chapters_generator.ChaptersGenerator, 'generate_chapters',
        lambda self, *a, **k: {'chapters': [{'startTime': 0, 'title': 'Intro'}]})
    embedded = {}
    monkeypatch.setattr(processing, 'embed_chapters',
                        lambda path, chapters, duration=None: embedded.update(path=path) or True)
    segments = [{'start': 0.0, 'end': 30.0, 'text': 'hello world'}]
    processing._generate_assets('slug', 'ep', segments, [], '', 'Pod', 'Title')
    assert embedded == {}


R1 = (296.0, 322.0)
R2 = (694.0, 706.0)


def _reject(span, was_cut=False):
    return {'start': span[0], 'end': span[1], 'confidence': 0.97,
            'reason': 'Acme sponsor read', 'detection_stage': 'claude',
            'was_cut': was_cut, 'source': 'reviewer', 'reviewer_verdict': 'reject',
            'reviewer_confidence': 0.9}


def _cut(start, end):
    return {'start': start, 'end': end, 'confidence': 0.95,
            'reason': 'Acme promo', 'detection_stage': 'claude', 'was_cut': True}


def _corroborated_hold(start, end):
    return {'start': start, 'end': end, 'confidence': 0.95,
            'reason': 'Acme promo', 'detection_stage': 'claude', 'was_cut': False,
            'held_for_review': True, 'hold_reason': 'differential_uncorroborated',
            'differential_uncorroborated': True, 'pass2_corroborated': True}


def _auto_confirm(start, end):
    return {'start': start, 'end': end, 'correction_type': 'confirm', 'auto_filed': True}


def _reject_segments():
    return [{'start': float(t), 'end': float(t + 10), 'text': 'Acme promo code'}
            for t in range(0, 1200, 10)]


def _spans(ads):
    return {(round(a['start'], 2), round(a['end'], 2)) for a in ads}


def _find(ads, span):
    return next(a for a in ads if (round(a['start'], 2), round(a['end'], 2)) == span)


def _stub_adjustment(monkeypatch, original, corrected):
    monkeypatch.setattr(
        processing.db, 'get_episode_corrections',
        lambda podcast_id, eid: [{'correction_type': 'boundary_adjustment',
                                  'original_bounds': {'start': original[0], 'end': original[1]},
                                  'corrected_bounds': {'start': corrected[0], 'end': corrected[1]}}])


def test_recut_preserves_reviewer_rejects_among_confirmed_adjusted_and_held(monkeypatch):
    ads = [_cut(100.0, 160.0), _reject(R1), _cut(400.0, 460.0), _reject(R2),
           _corroborated_hold(1000.0, 1100.0)]
    confirmed = [_auto_confirm(1000.0, 1100.0),
                 {'start': 395.0, 'end': 465.0, 'correction_type': 'boundary_adjustment',
                  'confirmed_span': {'start': 400.0, 'end': 460.0}},
                 {'start': 100.0, 'end': 160.0, 'correction_type': 'confirm'}]
    _stub_recut_db(monkeypatch, ads, confirmed=confirmed)
    _stub_adjustment(monkeypatch, (395.0, 465.0), (400.0, 460.0))

    ads_to_remove, all_ads, *_ = processing._build_recut_ad_list(
        'slug', 'ep', _reject_segments(), 3600.0, '', 0.80,
        corrections=_user_corrections('slug', 'ep'))

    assert _spans(ads_to_remove) == {(100.0, 160.0), (400.0, 460.0), (1000.0, 1100.0)}
    for span in (R1, R2):
        marker = _find(all_ads, span)
        assert marker['was_cut'] is False
        assert marker['validation']['decision'] == 'REJECT'
        assert 'reviewer_reject_preserved' in marker['validation']['flags']
        assert not any(k.startswith('_') for k in marker)


def test_recut_repairs_reject_saved_as_cut(monkeypatch):
    _stub_recut_db(monkeypatch, [_reject(R1, was_cut=True)])

    ads_to_remove, all_ads, *_ = processing._build_recut_ad_list(
        'slug', 'ep', _reject_segments(), 3600.0, '', 0.80,
        corrections=_user_corrections('slug', 'ep'))

    assert ads_to_remove == []
    assert all_ads[0]['was_cut'] is False
    assert all_ads[0]['validation']['decision'] == 'REJECT'


def test_recut_auto_filed_confirm_does_not_cut_reject(monkeypatch):
    _stub_recut_db(monkeypatch, [_reject(R1)], confirmed=[_auto_confirm(*R1)])

    ads_to_remove, all_ads, *_ = processing._build_recut_ad_list(
        'slug', 'ep', _reject_segments(), 3600.0, '', 0.80,
        corrections=_user_corrections('slug', 'ep'))

    assert ads_to_remove == []
    assert all_ads[0]['was_cut'] is False
    assert (all_ads[0]['start'], all_ads[0]['end']) == R1


def test_recut_user_confirm_overrides_reject(monkeypatch):
    stale = dict(_reject(R1), validation={'decision': 'REJECT',
                                          'flags': ['reviewer_reject_preserved']})
    _stub_recut_db(monkeypatch, [stale],
                   confirmed=[{'start': R1[0], 'end': R1[1], 'correction_type': 'confirm'}])

    ads_to_remove, all_ads, *_ = processing._build_recut_ad_list(
        'slug', 'ep', _reject_segments(), 3600.0, '', 0.80,
        corrections=_user_corrections('slug', 'ep'))

    assert _spans(ads_to_remove) == {R1}
    assert all_ads[0]['was_cut'] is True
    assert 'reviewer_reject_preserved' not in all_ads[0]['validation']['flags']


def test_recut_boundary_adjustment_overrides_reject(monkeypatch):
    corrected = (300.0, 320.0)
    _stub_recut_db(monkeypatch, [_reject(R1)],
                   confirmed=[{'start': R1[0], 'end': R1[1],
                               'correction_type': 'boundary_adjustment',
                               'confirmed_span': {'start': corrected[0], 'end': corrected[1]}}])
    _stub_adjustment(monkeypatch, R1, corrected)

    ads_to_remove, all_ads, *_ = processing._build_recut_ad_list(
        'slug', 'ep', _reject_segments(), 3600.0, '', 0.80,
        corrections=_user_corrections('slug', 'ep'))

    assert _spans(ads_to_remove) == {corrected}
    assert all_ads[0]['was_cut'] is True


def test_recut_reject_preservation_is_idempotent(monkeypatch):
    ads = [_cut(100.0, 160.0), _reject(R1, was_cut=True), _reject(R2),
           _corroborated_hold(1000.0, 1100.0)]
    confirmed = [_auto_confirm(1000.0, 1100.0)]
    _stub_recut_db(monkeypatch, ads, confirmed=confirmed)
    first_cut, first_all, *_ = processing._build_recut_ad_list(
        'slug', 'ep', _reject_segments(), 3600.0, '', 0.80,
        corrections=_user_corrections('slug', 'ep'))

    _stub_recut_db(monkeypatch, json.loads(json.dumps(first_all)), confirmed=confirmed)
    second_cut, second_all, *_ = processing._build_recut_ad_list(
        'slug', 'ep', _reject_segments(), 3600.0, '', 0.80,
        corrections=_user_corrections('slug', 'ep'))

    assert _spans(first_cut) == _spans(second_cut) == {(100.0, 160.0), (1000.0, 1100.0)}
    assert ([(a['start'], a['end'], a['validation']['flags']) for a in first_all]
            == [(a['start'], a['end'], a['validation']['flags']) for a in second_all])
    for span in (R1, R2):
        assert _find(second_all, span)['was_cut'] is False


@pytest.mark.parametrize('neighbor', [_cut(270.0, 295.5), dict(_cut(270.0, 295.5), was_cut=False)])
@pytest.mark.parametrize('saved_cut', [False, True])
def test_recut_reject_never_merges_into_adjacent_cut(monkeypatch, neighbor, saved_cut):
    _stub_recut_db(monkeypatch, [neighbor, _reject(R1, was_cut=saved_cut)])

    ads_to_remove, all_ads, *_ = processing._build_recut_ad_list(
        'slug', 'ep', _reject_segments(), 3600.0, '', 0.80,
        corrections=_user_corrections('slug', 'ep'))

    assert _spans(ads_to_remove) == {(270.0, 295.5)}
    assert _find(all_ads, R1)['was_cut'] is False


@pytest.mark.skipif(shutil.which('ffmpeg') is None or shutil.which('ffprobe') is None,
                    reason='ffmpeg/ffprobe not available')
def test_recut_episode_keeps_rejects_out_of_saved_markers_and_applied_cuts(tmp_path):
    src = tmp_path / 'retained.mp3'
    subprocess.run(['ffmpeg', '-y', '-f', 'lavfi', '-i', 'sine=frequency=440:duration=60',
                    '-acodec', 'libmp3lame', '-ab', '64k', str(src)],
                   check=True, capture_output=True)
    rejects = [(20.5, 30.0), (40.0, 45.0)]
    ads = [_cut(10.0, 20.0), _reject(rejects[0], was_cut=True), _reject(rejects[1]),
           _corroborated_hold(48.0, 56.0)]
    saved = {}
    with ExitStack() as stack:
        p = lambda *a, **k: stack.enter_context(patch.object(*a, **k))
        db = p(processing, 'db')
        storage = p(processing, 'storage')
        p(processing, 'status_service')
        p(processing, '_finalize_episode')
        p(processing, 'get_min_cut_confidence', return_value=0.80)
        p(processing, 'embed_chapters', return_value=True)
        p(processing, 'get_replacement_duration', return_value=1.0)
        p(processing, 'resolve_ad_chapter_config', return_value=AdChapterConfig.disabled())
        db.get_episode.return_value = {'podcast_id': 1, 'processed_version': 1,
                                       'ad_markers_json': json.dumps(ads)}
        db.get_podcast_by_slug.return_value = {'id': 1}
        db.get_original_segments.return_value = [
            {'start': float(t), 'end': float(t + 5), 'text': 'Acme promo code'}
            for t in range(0, 60, 5)]
        db.get_all_settings.return_value = {}
        db.get_setting.return_value = None
        db.get_episode_corrections.return_value = []
        db.get_false_positive_corrections.return_value = []
        db.get_confirmed_corrections.return_value = [_auto_confirm(48.0, 56.0)]
        db.get_podcast_cue_settings_overrides.return_value = {}
        db.get_episode_audio_analysis.return_value = None
        db.get_episode_dai_differential.return_value = None
        db.resolve_segment_actions.return_value = {}
        storage.get_original_path.return_value = src
        storage.get_applied_cuts.return_value = []
        storage.get_chapters_json.return_value = {
            'version': '1.2.0', 'chapters': [{'startTime': 0, 'title': 'Intro'}]}
        storage.get_episode_path.return_value = str(tmp_path / 'final.mp3')
        storage.save_combined_ads.side_effect = (
            lambda s, e, markers: saved.__setitem__('markers', json.loads(json.dumps(markers))))
        storage.save_chapters_and_applied_cuts.side_effect = (
            lambda s, e, chapters, cuts: saved.__setitem__('cuts', cuts))

        assert processing._recut_episode(
            'example-podcast', 'a1b2c3d4e5f6', 'Episode', 'Podcast', '', time.time())
        # The recut reads the episode row once and builds its ad list from that read.
        assert db.get_episode.call_count == 1

    for lo, hi in rejects:
        marker = _find(saved['markers'], (lo, hi))
        assert marker['was_cut'] is False
        assert marker['validation']['decision'] == 'REJECT'
        assert not any(c['start'] < hi and c['end'] > lo for c in saved['cuts'])
    assert any(c['start'] <= 48.5 and c['end'] >= 55.5 for c in saved['cuts'])


def test_build_recut_ad_list_keeps_kept_markers_out_of_validation(monkeypatch):
    """A kept marker must not be merged into an overlapping cut on recut."""
    kept = {'start': 100.0, 'end': 130.0, 'confidence': 0.98, 'category': 'self_promo',
            'action_applied': 'keep', 'was_cut': False, 'reason': 'show promo'}
    sponsor = {'start': 110.0, 'end': 190.0, 'confidence': 0.98, 'category': 'sponsor',
               'sponsor': 'Acme', 'was_cut': True, 'reason': 'sponsor read for Acme'}
    monkeypatch.setattr(processing.db, 'get_episode',
                        lambda s, e: {'ad_markers_json': json.dumps([kept, sponsor])})
    monkeypatch.setattr(processing.db, 'get_podcast_by_slug', lambda slug: {'id': 42})
    monkeypatch.setattr(processing.db, 'get_episode_corrections', lambda p, e: [])
    monkeypatch.setattr(processing.db, 'get_false_positive_corrections', lambda p, e: [])
    monkeypatch.setattr(processing.db, 'get_confirmed_corrections', lambda p, e: [])
    seen = []
    real_build = processing._build_validator

    def spy_build(*args, **kwargs):
        validator = real_build(*args, **kwargs)
        real_validate = validator.validate
        validator.validate = lambda ads, **kw: (seen.extend(ads), real_validate(ads, **kw))[1]
        return validator

    monkeypatch.setattr(processing, '_build_validator', spy_build)
    segments = [{'start': 100.0, 'end': 190.0, 'text': 'promo then sponsor read'}]

    ads_to_remove, all_ads, *_ = processing._build_recut_ad_list(
        'slug', 'ep', segments, 600.0, '', 0.80,
        segment_actions={'self_promo': 'keep', 'sponsor': 'remove'},
        corrections=_user_corrections('slug', 'ep'))

    assert [a.get('category') for a in seen] == ['sponsor']
    assert (100.0, 130.0, 'keep') in {
        (a['start'], a['end'], a.get('action_applied')) for a in all_ads}
    assert all(a.get('category') == 'sponsor' for a in ads_to_remove)


def test_pass1_carve_saves_trusted_fragments_that_a_recut_keeps_cut(monkeypatch):
    cut = dict(_cut(10.0, 60.0), category='sponsor', sponsor='Acme')
    keep = {'start': 12.0, 'end': 40.0, 'confidence': 0.98, 'category': 'self_promo',
            'action_applied': 'keep', 'was_cut': False, 'reason': 'show promo'}
    all_ads = [cut, keep]
    pieces = processing._carve_cuts_around([cut], all_ads, [keep])
    processing._finalize_cut_state(
        all_ads, pieces, [{'start': 10.0, 'end': 12.0}, {'start': 40.0, 'end': 60.0}], 600.0)
    fragments = [a for a in all_ads if a is not keep]
    assert [(a['start'], a['end'], a['was_cut']) for a in fragments] == [
        (10.0, 12.0, True), (40.0, 60.0, True)]
    assert all(a.get('_measured_split_fragment') for a in fragments)

    _stub_recut_db(monkeypatch, json.loads(json.dumps(all_ads)))
    ads_to_remove, _, *_ = processing._build_recut_ad_list(
        'slug', 'ep', [{'start': 0.0, 'end': 60.0, 'text': 'Acme promo code'}], 600.0,
        '', 0.80, segment_actions={'self_promo': 'keep', 'sponsor': 'remove'},
        corrections=_user_corrections('slug', 'ep'))

    assert {(10.0, 12.0), (40.0, 60.0)} <= _spans(ads_to_remove)


@pytest.mark.skipif(shutil.which('ffmpeg') is None or shutil.which('ffprobe') is None,
                    reason='ffmpeg/ffprobe not available')
def test_manual_approve_reject_and_adjust_recut_twice_is_identical(tmp_path):
    src = tmp_path / 'retained.mp3'
    subprocess.run(['ffmpeg', '-y', '-f', 'lavfi', '-i', 'sine=frequency=440:duration=120',
                    '-acodec', 'libmp3lame', '-ab', '64k', str(src)],
                   check=True, capture_output=True)
    held = dict(_cut(30.0, 38.0), was_cut=False, held_for_review=True,
                hold_reason='max_duration')
    ads = [_cut(10.0, 20.0), held, _cut(40.0, 46.0), _cut(48.0, 58.0)]
    corrections = [{'correction_type': 'boundary_adjustment',
                    'original_bounds': {'start': 48.0, 'end': 58.0},
                    'corrected_bounds': {'start': 49.0, 'end': 57.0}}]

    def run(markers):
        saved = {}
        with ExitStack() as stack:
            p = lambda *a, **k: stack.enter_context(patch.object(*a, **k))
            db = p(processing, 'db')
            storage = p(processing, 'storage')
            p(processing, 'status_service')
            p(processing, '_finalize_episode')
            p(processing, 'get_min_cut_confidence', return_value=0.80)
            p(processing, 'embed_chapters', return_value=True)
            p(processing, 'get_replacement_duration', return_value=1.0)
            p(processing, 'resolve_ad_chapter_config', return_value=AdChapterConfig.disabled())
            db.get_episode.return_value = {'podcast_id': 1, 'processed_version': 1,
                                           'ad_markers_json': json.dumps(markers)}
            db.get_podcast_by_slug.return_value = {'id': 1}
            db.get_original_segments.return_value = [
                {'start': float(t), 'end': float(t + 5), 'text': 'Acme promo code'}
                for t in range(0, 120, 5)]
            db.get_all_settings.return_value = {}
            db.get_setting.return_value = None
            db.get_episode_corrections.return_value = corrections
            db.get_false_positive_corrections.return_value = [{'start': 40.0, 'end': 46.0}]
            db.get_confirmed_corrections.return_value = [
                {'start': 30.0, 'end': 38.0, 'correction_type': 'confirm'}]
            db.get_podcast_cue_settings_overrides.return_value = {}
            db.get_episode_audio_analysis.return_value = None
            db.get_episode_dai_differential.return_value = None
            db.resolve_segment_actions.return_value = {}
            storage.get_original_path.return_value = src
            storage.get_applied_cuts.return_value = []
            storage.get_chapters_json.return_value = {
                'version': '1.2.0', 'chapters': [{'startTime': 0, 'title': 'Intro'}]}
            storage.get_episode_path.return_value = str(tmp_path / 'final.mp3')
            storage.save_combined_ads.side_effect = (
                lambda s, e, m: saved.__setitem__('markers', json.loads(json.dumps(m))))
            storage.save_chapters_and_applied_cuts.side_effect = (
                lambda s, e, chapters, cuts: saved.__setitem__('cuts', cuts))
            assert processing._recut_episode(
                'example-podcast', 'a1b2c3d4e5f6', 'Episode', 'Podcast', '', time.time())
        return saved

    first = run(ads)
    second = run(first['markers'])

    assert second == first
    was_cut = {(round(m['start'], 1), round(m['end'], 1)): m['was_cut']
               for m in first['markers']}
    assert was_cut == {(10.0, 20.0): True, (30.0, 38.0): True, (40.0, 46.0): False,
                       (49.0, 57.0): True}


REVIEWER_REASONS = ('reviewer_reject_conflict', 'reviewer_boundary_conflict',
                    'reviewer_inconclusive_bounds', 'reviewer_failed',
                    'reviewer_contradiction')
HELD = (300.0, 330.0)


def _reviewer_hold(reason, span=HELD):
    return {'start': span[0], 'end': span[1], 'confidence': 0.95,
            'reason': 'Acme promo', 'detection_stage': 'claude', 'was_cut': False,
            'held_for_review': True, 'hold_reason': reason}


def _assert_still_held(marker, reason):
    assert marker['held_for_review'] is True
    assert marker['hold_reason'] == reason
    assert marker['was_cut'] is False
    assert marker['validation']['decision'] == 'REVIEW'
    assert not any(k.startswith('_') for k in marker)


@pytest.mark.parametrize('reason', REVIEWER_REASONS)
def test_unrelated_auto_confirm_recut_leaves_reviewer_hold_held(monkeypatch, reason):
    ads = [_reviewer_hold(reason), _corroborated_hold(1000.0, 1100.0)]
    confirmed = [dict(_auto_confirm(1000.0, 1100.0),
                      hold_reason='differential_uncorroborated')]
    _stub_recut_db(monkeypatch, ads, confirmed=confirmed)

    ads_to_remove, all_ads, *_ = processing._build_recut_ad_list(
        'slug', 'ep', _reject_segments(), 3600.0, '', 0.80,
        corrections=_user_corrections('slug', 'ep'))

    assert _spans(ads_to_remove) == {(1000.0, 1100.0)}
    _assert_still_held(_find(all_ads, HELD), reason)


@pytest.mark.parametrize('reason', REVIEWER_REASONS)
def test_user_confirm_releases_reviewer_hold(monkeypatch, reason):
    _stub_recut_db(monkeypatch, [_reviewer_hold(reason)],
                   confirmed=[{'start': HELD[0], 'end': HELD[1], 'correction_type': 'confirm'}])

    ads_to_remove, all_ads, *_ = processing._build_recut_ad_list(
        'slug', 'ep', _reject_segments(), 3600.0, '', 0.80,
        corrections=_user_corrections('slug', 'ep'))

    assert _spans(ads_to_remove) == {HELD}
    assert not all_ads[0].get('held_for_review')


@pytest.mark.parametrize('reason', REVIEWER_REASONS)
def test_matching_auto_confirm_releases_only_a_releasable_reviewer_hold(monkeypatch, reason):
    _stub_recut_db(monkeypatch, [_reviewer_hold(reason)],
                   confirmed=[dict(_auto_confirm(*HELD), hold_reason=reason)])

    ads_to_remove, all_ads, *_ = processing._build_recut_ad_list(
        'slug', 'ep', _reject_segments(), 3600.0, '', 0.80,
        corrections=_user_corrections('slug', 'ep'))

    if reason in PASS2_REVIEWED_RELEASE_HOLD_REASONS:
        assert _spans(ads_to_remove) == {HELD}
    else:
        assert ads_to_remove == []
        _assert_still_held(all_ads[0], reason)


def test_auto_confirm_filed_for_another_reason_does_not_release_hold(monkeypatch):
    reason = 'reviewer_inconclusive_bounds'
    _stub_recut_db(monkeypatch, [_reviewer_hold(reason)],
                   confirmed=[dict(_auto_confirm(*HELD), hold_reason='no_splice_evidence')])

    ads_to_remove, all_ads, *_ = processing._build_recut_ad_list(
        'slug', 'ep', _reject_segments(), 3600.0, '', 0.80,
        corrections=_user_corrections('slug', 'ep'))

    assert ads_to_remove == []
    _assert_still_held(all_ads[0], reason)


def test_reviewer_hold_recut_is_idempotent(monkeypatch):
    ads = [_reviewer_hold(r, (300.0 + 100 * i, 330.0 + 100 * i))
           for i, r in enumerate(REVIEWER_REASONS)]
    ads.append(_corroborated_hold(1000.0, 1100.0))
    confirmed = [dict(_auto_confirm(1000.0, 1100.0),
                      hold_reason='differential_uncorroborated')]
    _stub_recut_db(monkeypatch, ads, confirmed=confirmed)
    first_cut, first_all, *_ = processing._build_recut_ad_list(
        'slug', 'ep', _reject_segments(), 3600.0, '', 0.80,
        corrections=_user_corrections('slug', 'ep'))

    _stub_recut_db(monkeypatch, json.loads(json.dumps(first_all)), confirmed=confirmed)
    second_cut, second_all, *_ = processing._build_recut_ad_list(
        'slug', 'ep', _reject_segments(), 3600.0, '', 0.80,
        corrections=_user_corrections('slug', 'ep'))

    assert _spans(first_cut) == _spans(second_cut) == {(1000.0, 1100.0)}
    held = [(a['start'], a['end'], a.get('hold_reason'), a['was_cut']) for a in first_all]
    assert held == [(a['start'], a['end'], a.get('hold_reason'), a['was_cut'])
                    for a in second_all]
    assert sum(1 for a in second_all if a.get('held_for_review')) == len(REVIEWER_REASONS)


@pytest.mark.skipif(shutil.which('ffmpeg') is None or shutil.which('ffprobe') is None,
                    reason='ffmpeg/ffprobe not available')
def test_approval_fold_recut_keeps_reviewer_reject_conflict_hold(tmp_path):
    src = tmp_path / 'retained.mp3'
    subprocess.run(['ffmpeg', '-y', '-f', 'lavfi', '-i', 'sine=frequency=440:duration=60',
                    '-acodec', 'libmp3lame', '-ab', '64k', str(src)],
                   check=True, capture_output=True)
    held = (20.0, 30.0)
    ads = [_cut(5.0, 15.0), _reviewer_hold('reviewer_reject_conflict', held),
           _corroborated_hold(30.5, 40.0)]
    saved = {}
    with ExitStack() as stack:
        p = lambda *a, **k: stack.enter_context(patch.object(*a, **k))
        db = p(processing, 'db')
        storage = p(processing, 'storage')
        p(processing, 'status_service')
        p(processing, '_finalize_episode')
        p(processing, 'get_min_cut_confidence', return_value=0.80)
        p(processing, 'embed_chapters', return_value=True)
        p(processing, 'get_replacement_duration', return_value=1.0)
        p(processing, 'resolve_ad_chapter_config', return_value=AdChapterConfig.disabled())
        db.get_episode.return_value = {'podcast_id': 1, 'processed_version': 1,
                                       'ad_markers_json': json.dumps(ads)}
        db.get_podcast_by_slug.return_value = {'id': 1}
        db.get_original_segments.return_value = [
            {'start': float(t), 'end': float(t + 5), 'text': 'Acme promo code'}
            for t in range(0, 60, 5)]
        db.get_all_settings.return_value = {}
        db.get_setting.return_value = None
        db.get_episode_corrections.return_value = []
        db.get_false_positive_corrections.return_value = []
        db.get_confirmed_corrections.return_value = [
            dict(_auto_confirm(30.5, 40.0), hold_reason='differential_uncorroborated')]
        db.get_setting_float.side_effect = lambda k, default=None: default
        db.get_setting_bool.side_effect = lambda k, default=False: default
        db.get_podcast_cue_settings_overrides.return_value = {}
        db.get_episode_audio_analysis.return_value = None
        db.get_episode_dai_differential.return_value = None
        db.resolve_segment_actions.return_value = {}
        storage.get_original_path.return_value = src
        storage.get_applied_cuts.return_value = []
        storage.get_chapters_json.return_value = {
            'version': '1.2.0', 'chapters': [{'startTime': 0, 'title': 'Intro'}]}
        storage.get_episode_path.return_value = str(tmp_path / 'final.mp3')
        storage.save_combined_ads.side_effect = (
            lambda s, e, markers: saved.__setitem__('markers', json.loads(json.dumps(markers))))
        storage.save_chapters_and_applied_cuts.side_effect = (
            lambda s, e, chapters, cuts: saved.__setitem__('cuts', cuts))

        assert processing._recut_episode(
            'example-podcast', 'a1b2c3d4e5f6', 'Episode', 'Podcast', '', time.time(),
            owns_failure=False)

    _assert_still_held(_find(saved['markers'], held), 'reviewer_reject_conflict')
    assert not any(c['start'] < held[1] and c['end'] > held[0] for c in saved['cuts'])
    assert any(c['start'] <= 31.0 and c['end'] >= 39.5 for c in saved['cuts'])


def test_recut_does_not_stamp_reviewer_rejected(monkeypatch):
    import ad_validator
    seen = []
    orig = ad_validator.AdValidator.validate

    def spy(self, ads_arg, **kw):
        seen.extend(dict(a) for a in ads_arg)
        return orig(self, ads_arg, **kw)

    monkeypatch.setattr(ad_validator.AdValidator, 'validate', spy)
    _stub_recut_db(monkeypatch, [_cut(100.0, 160.0), _reject(R1, was_cut=True)])

    ads_to_remove, all_ads, *_ = processing._build_recut_ad_list(
        'slug', 'ep', _reject_segments(), 3600.0, '', 0.80,
        corrections=_user_corrections('slug', 'ep'))

    assert seen and all('_reviewer_rejected' not in a for a in seen)
    assert _spans(ads_to_remove) == {(100.0, 160.0)}
    assert _find(all_ads, R1)['validation']['decision'] == 'REJECT'


def _recut_render_call(tmp_path, markers, confirmed):
    """Drive a real _recut_episode with ffmpeg mocked; return the render call."""
    with ExitStack() as stack:
        p = lambda *a, **k: stack.enter_context(patch.object(*a, **k))
        db = p(processing, 'db')
        storage = p(processing, 'storage')
        p(processing, 'status_service')
        p(processing, '_finalize_episode')
        p(processing, '_generate_assets')
        p(processing, '_copy_retained_original_to_temp', return_value=str(tmp_path / 'w.mp3'))
        p(processing, 'get_min_cut_confidence', return_value=0.80)
        p(processing.os.path, 'exists', return_value=False)
        p(processing.shutil, 'move')
        local_ap = p(processing, 'AudioProcessor').return_value
        local_ap.get_audio_duration.return_value = 600.0
        local_ap.process_episode.side_effect = (
            lambda path, segs, cut_barriers=None, hard_barriers=None: (
                str(tmp_path / 'cut.mp3'), [{'start': s['start'], 'end': s['end']} for s in segs]))
        db.get_episode.return_value = {'podcast_id': 1, 'processed_version': 1,
                                       'ad_markers_json': json.dumps(markers)}
        db.get_podcast_by_slug.return_value = {'id': 1}
        db.get_original_segments.return_value = [
            {'start': float(t), 'end': float(t + 5), 'text': 'Show talk'} for t in range(0, 600, 5)]
        db.get_all_settings.return_value = {}
        db.get_setting.return_value = None
        db.get_episode_corrections.return_value = []
        db.get_false_positive_corrections.return_value = []
        db.get_confirmed_corrections.return_value = confirmed
        db.get_podcast_cue_settings_overrides.return_value = {}
        db.get_episode_audio_analysis.return_value = None
        db.get_episode_dai_differential.return_value = None
        db.resolve_segment_actions.return_value = {}
        storage.get_applied_cuts.return_value = []
        storage.get_episode_path.return_value = str(tmp_path / 'final.mp3')

        assert processing._recut_episode(
            'example-podcast', 'a1b2c3d4e5f6', 'Episode', 'Podcast', '', time.time())
    return local_ap.process_episode.call_args


def test_recut_barriers_use_the_carved_reviewer_hold(tmp_path):
    hold = {'start': 10.0, 'end': 40.0, 'confidence': 0.95, 'reason': 'Acme promo',
            'detection_stage': 'claude', 'was_cut': False, 'held_for_review': True,
            'hold_reason': 'reviewer_contradiction', 'source': 'reviewer',
            'reviewer_verdict': 'confirmed'}
    call = _recut_render_call(
        tmp_path, [hold], [{'start': 10.0, 'end': 22.0, 'correction_type': 'confirm'}])

    assert _spans(call.args[1]) == {(10.0, 22.0)}
    assert _spans(call.kwargs['cut_barriers']) == {(22.0, 40.0)}


def test_recut_cuts_a_user_confirm_inside_a_reviewer_reject(tmp_path):
    call = _recut_render_call(
        tmp_path, [_reject((5.0, 95.0))], [{'start': 20.0, 'end': 60.0, 'correction_type': 'confirm'}])

    assert [(c['start'], c['end']) for c in call.args[1]] == [(20.0, 60.0)]
    hard = _spans(call.kwargs['hard_barriers'])
    assert {(5.0, 20.0), (60.0, 95.0)} <= hard and (5.0, 95.0) not in hard


def test_recut_keeps_an_auto_filed_confirm_inside_a_reviewer_reject_uncut(tmp_path):
    call = _recut_render_call(tmp_path, [_reject((5.0, 95.0))], [_auto_confirm(20.0, 60.0)])

    assert call.args[1] == []
    assert (5.0, 95.0) in _spans(call.kwargs['hard_barriers'])
