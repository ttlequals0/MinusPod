"""Unit tests for the recut cut-list helpers (issue #422)."""
import json
import os
import sys
import tempfile

import pytest

# Bind a temp data dir via env (Storage reads MINUSPOD_DATA_DIR natively) so
# importing main_app does not mkdir /app/data. Using the env var instead of
# rebinding Database.__init__.__defaults__ at import time keeps this module from
# poisoning sibling test modules' singleton state (finding: import poisoning).
_test_data_dir = tempfile.mkdtemp(prefix='recut_test_')
os.environ.setdefault('SECRET_KEY', 'test-secret')
os.environ.setdefault('MINUSPOD_DATA_DIR', _test_data_dir)

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))

from main_app import processing


@pytest.fixture(autouse=True)
def _isolate_db():
    """Pin the Database singleton to this module's dir per test so collection
    order cannot leave it bound to a sibling module's dir."""
    import database
    database.Database._instance = None
    database.Database.__init__.__defaults__ = (_test_data_dir,)
    database.Database.__new__.__defaults__ = (_test_data_dir,)
    yield


def test_best_overlap_ad_picks_max_overlap():
    ads = [{'start': 0, 'end': 10}, {'start': 50, 'end': 70}, {'start': 100, 'end': 110}]
    assert processing._best_overlap_ad(ads, 55, 65) is ads[1]


def test_best_overlap_ad_none_when_no_overlap():
    ads = [{'start': 0, 'end': 10}]
    assert processing._best_overlap_ad(ads, 50, 60) is None


def test_best_overlap_ad_excludes_ids():
    ads = [{'start': 0, 'end': 10}, {'start': 0, 'end': 10}]
    first = processing._best_overlap_ad(ads, 1, 5)
    second = processing._best_overlap_ad(ads, 1, 5, exclude_ids={id(first)})
    assert second is not first


def test_apply_boundary_adjustments_overrides_bounds(monkeypatch):
    ads = [{'start': 100.0, 'end': 160.0, 'confidence': 0.9}]
    corrections = [{
        'correction_type': 'boundary_adjustment',
        'original_bounds': {'start': 100.0, 'end': 160.0},
        'corrected_bounds': {'start': 105.0, 'end': 150.0},
    }]
    monkeypatch.setattr(processing.db, 'get_episode_corrections', lambda eid: corrections)
    processing._apply_boundary_adjustments('slug', 'ep', ads)
    assert ads[0]['start'] == 105.0
    assert ads[0]['end'] == 150.0


def test_apply_boundary_adjustments_skips_unmatched(monkeypatch):
    ads = [{'start': 100.0, 'end': 160.0}]
    corrections = [{
        'correction_type': 'boundary_adjustment',
        'original_bounds': {'start': 900.0, 'end': 950.0},
        'corrected_bounds': {'start': 905.0, 'end': 940.0},
    }]
    monkeypatch.setattr(processing.db, 'get_episode_corrections', lambda eid: corrections)
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
    monkeypatch.setattr(processing.db, 'get_episode_corrections', lambda eid: corrections)
    processing._apply_boundary_adjustments('slug', 'ep', ads)
    assert ads[0]['start'] == 110.0
    assert ads[0]['end'] == 150.0


def test_build_recut_ad_list_drops_rejected(monkeypatch):
    ads = [
        {'start': 30.0, 'end': 90.0, 'confidence': 0.98, 'sponsor': 'A', 'reason': 'sponsor read for A'},
        {'start': 300.0, 'end': 360.0, 'confidence': 0.98, 'sponsor': 'B', 'reason': 'sponsor read for B'},
    ]
    monkeypatch.setattr(processing.db, 'get_episode', lambda s, e: {'ad_markers_json': json.dumps(ads)})
    monkeypatch.setattr(processing.db, 'get_episode_corrections', lambda eid: [])
    monkeypatch.setattr(processing.db, 'get_false_positive_corrections',
                        lambda eid: [{'start': 300.0, 'end': 360.0}])
    monkeypatch.setattr(processing.db, 'get_confirmed_corrections', lambda eid: [])
    segments = [
        {'start': 30.0, 'end': 90.0, 'text': 'sponsor a'},
        {'start': 300.0, 'end': 360.0, 'text': 'sponsor b'},
    ]
    ads_to_remove, all_ads = processing._build_recut_ad_list(
        'slug', 'ep', segments, 600.0, '', 0.80
    )
    starts = {a['start'] for a in ads_to_remove}
    assert 30.0 in starts
    assert 300.0 not in starts
    assert len(all_ads) == 2  # rejected ad stays in the list, just not cut


def test_build_recut_ad_list_keeps_confirmed(monkeypatch):
    # A low-confidence ad the user confirmed must still be cut.
    ads = [{'start': 30.0, 'end': 90.0, 'confidence': 0.40, 'sponsor': 'A', 'reason': 'maybe an ad for A'}]
    monkeypatch.setattr(processing.db, 'get_episode', lambda s, e: {'ad_markers_json': json.dumps(ads)})
    monkeypatch.setattr(processing.db, 'get_episode_corrections', lambda eid: [])
    monkeypatch.setattr(processing.db, 'get_false_positive_corrections', lambda eid: [])
    monkeypatch.setattr(processing.db, 'get_confirmed_corrections', lambda eid: [{'start': 30.0, 'end': 90.0}])
    segments = [{'start': 30.0, 'end': 90.0, 'text': 'maybe an ad'}]
    ads_to_remove, _ = processing._build_recut_ad_list('slug', 'ep', segments, 600.0, '', 0.80)
    assert {a['start'] for a in ads_to_remove} == {30.0}


def test_build_recut_ad_list_keeps_manual_add(monkeypatch):
    # A manually-added marker (confidence 1.0, detection_stage 'manual') must be cut.
    ads = [{'start': 120.0, 'end': 180.0, 'confidence': 1.0, 'detection_stage': 'manual',
            'sponsor': 'Manual Co', 'reason': 'Manual Co: manually added ad'}]
    monkeypatch.setattr(processing.db, 'get_episode', lambda s, e: {'ad_markers_json': json.dumps(ads)})
    monkeypatch.setattr(processing.db, 'get_episode_corrections', lambda eid: [])
    monkeypatch.setattr(processing.db, 'get_false_positive_corrections', lambda eid: [])
    monkeypatch.setattr(processing.db, 'get_confirmed_corrections', lambda eid: [])
    segments = [{'start': 120.0, 'end': 180.0, 'text': 'manual'}]
    ads_to_remove, _ = processing._build_recut_ad_list('slug', 'ep', segments, 600.0, '', 0.80)
    assert {a['start'] for a in ads_to_remove} == {120.0}


def test_build_recut_ad_list_empty_when_no_markers(monkeypatch):
    monkeypatch.setattr(processing.db, 'get_episode', lambda s, e: {'ad_markers_json': None})
    assert processing._build_recut_ad_list('slug', 'ep', [], 600.0, '', 0.80) == ([], [])


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
    monkeypatch.setattr(processing.db, 'get_episode_corrections', lambda eid: [])
    monkeypatch.setattr(processing.db, 'get_false_positive_corrections',
                        lambda eid: fp or [])
    monkeypatch.setattr(processing.db, 'get_confirmed_corrections',
                        lambda eid: confirmed or [])
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
    ads_to_remove, _ = processing._build_recut_ad_list(
        'slug', 'ep', [], 3600.0, '', 0.80
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
    ads_to_remove, all_ads = processing._build_recut_ad_list(
        'slug', 'ep', [], 3600.0, '', 0.80
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
    ads_to_remove, all_ads = processing._build_recut_ad_list(
        'slug', 'ep', segments, 3600.0, '', 0.80
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
    ads_to_remove, all_ads = processing._build_recut_ad_list(
        'slug', 'ep', [], 3600.0, '', 0.80
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
    ads_to_remove, _ = processing._build_recut_ad_list(
        'slug', 'ep', [], 3600.0, '', 0.80
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
    ads_to_remove, all_ads = processing._build_recut_ad_list(
        'slug', 'ep', [], 3600.0, '', 0.80
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
    ads_to_remove, all_ads = processing._build_recut_ad_list(
        'slug', 'ep', [], 3600.0, '', 0.80
    )
    assert ads_to_remove, "Previously-cut ad must still be cut after clamp"
    assert not all_ads[0].get('held_for_review'), (
        "Clamped previously-cut ad must not resurrect as held"
    )


def test_build_recut_merge_survivor_inherits_saved_cut_stamp(monkeypatch):
    # The merge survivor is the FIRST ad. When the first was NOT previously cut
    # but the absorbed second WAS, the saved-cut stamp must propagate to the
    # survivor so the merged span (containing previously-cut audio) stays cut.
    ads = [
        {'start': 100.0, 'end': 160.0, 'confidence': 0.95,
         'reason': 'promo one'},  # not previously cut
        {'start': 162.0, 'end': 200.0, 'confidence': 0.95,
         'reason': 'promo two', 'was_cut': True},  # previously cut
    ]
    _stub_recut_db(monkeypatch, ads, overrides={'cue_gated_approval': 1})
    ads_to_remove, all_ads = processing._build_recut_ad_list(
        'slug', 'ep', [], 3600.0, '', 0.80
    )
    assert ads_to_remove, "Merged span containing previously-cut audio must be cut"
    assert not any(a.get('held_for_review') for a in all_ads), (
        "Merged span with previously-cut audio must not resurrect as held"
    )


def test_build_recut_previously_cut_review_not_held_by_cue_gate(monkeypatch):
    # A previously-cut ad that re-validates to REVIEW (below threshold) must not
    # be newly held by the cue-gate fall-through -- it was already published.
    ads = [{'start': 500.0, 'end': 560.0, 'confidence': 0.60,
            'reason': 'possible sponsor mention', 'was_cut': True}]
    _stub_recut_db(monkeypatch, ads, overrides={'cue_gated_approval': 1})
    ads_to_remove, all_ads = processing._build_recut_ad_list(
        'slug', 'ep', [], 3600.0, '', 0.80
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
    ads_to_remove, all_ads = processing._build_recut_ad_list(
        'slug', 'ep', [], 3600.0, '', 0.80
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

    def spy(self, ads_arg, audio_analysis=None):
        captured['called'] = True
        captured['audio_analysis'] = audio_analysis
        return orig(self, ads_arg, audio_analysis=audio_analysis)

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
    processing._build_recut_ad_list('slug', 'ep', [], 3600.0, '', 0.80)
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
    processing._build_recut_ad_list('slug', 'ep', [], 3600.0, '', 0.80)
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
    processing._build_recut_ad_list('slug', 'ep', [], 3600.0, '', 0.80)
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
    processing._build_recut_ad_list('slug', 'ep', [], 3600.0, '', 0.80)
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
