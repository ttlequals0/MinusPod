"""A merged marker records every stage that contributed, so audio-only reads are exact."""
import json

import pytest

from tests.app_bootstrap import bootstrap
bootstrap('merged_stages_test_')

from ad_detector import AdDetector  # noqa: E402
from utils.markers import carve_fragment, clip_merge_spans, marker_stages, note_merged_members  # noqa: E402
from tests.fingerprint_match_helpers import get_fingerprint_matches

SLUG = 'example-podcast'
EP = 'a1b2c3d4e5f6'
SEGMENTS = [{'start': s, 'end': s + 5.0, 'text': 'words ' * 12} for s in range(0, 200, 5)]


def _ad(stage, start, end, **extra):
    ad = {'start': start, 'end': end, 'confidence': 0.9, 'detection_stage': stage,
          'reason': 'Acme read', 'sponsor': 'Acme', 'category': 'sponsor'}
    ad.update(extra)
    return ad


def _merge(ads):
    return AdDetector.__new__(AdDetector)._merge_detection_results(ads, SEGMENTS, action_map=None)


@pytest.fixture
def world(temp_db):
    temp_db.create_podcast(SLUG, 'https://example.com/feed.xml', 'Example')
    pid = temp_db.create_ad_pattern(scope='podcast', podcast_id=SLUG, text_template='acme read ' * 12)
    return temp_db, pid


def _audio_only(db, marker):
    db.record_fingerprint_matches_from_markers(SLUG, EP, [marker])
    rows = get_fingerprint_matches(db, marker['pattern_id'], 10, 0)
    assert rows
    return all(row['audio_only'] for row in rows)


def _fingerprint(pid, start=10.0, end=60.0, **extra):
    return _ad('fingerprint', start, end, pattern_id=pid, fingerprint_match_start=start,
               fingerprint_match_end=end, fingerprint_similarity=0.9, fingerprint_coverage=0.8, **extra)


def test_an_unmerged_marker_has_only_its_own_stage():
    assert marker_stages(_ad('fingerprint', 1.0, 5.0)) == ['fingerprint']


def test_note_merged_members_accumulates_both_sides():
    target = _ad('fingerprint', 10.0, 60.0)
    note_merged_members(target, _ad('dai_differential', 30.0, 70.0))
    note_merged_members(target, _ad('vad_gap', 55.0, 80.0))
    assert target['merged_stages'] == ['fingerprint', 'dai_differential', 'vad_gap']


def test_a_merged_other_brings_its_own_stage_list():
    inner = _ad('claude', 30.0, 70.0)
    note_merged_members(inner, _ad('first_pass', 35.0, 65.0))
    target = _ad('fingerprint', 10.0, 60.0)
    note_merged_members(target, inner)
    assert target['merged_stages'] == ['fingerprint', 'claude', 'first_pass']


def test_stages_recorded_in_member_spans_of_older_markers_still_count():
    older = _ad('fingerprint', 10.0, 60.0, merged_protected_start=10.0, merged_protected_end=70.0,
                merged_member_spans=[{'start': 20.0, 'end': 70.0, 'stage': 'claude'}])
    assert set(marker_stages(older)) == {'fingerprint', 'claude'}


@pytest.mark.parametrize('other', ['dai_differential', 'vad_gap', 'first_pass', 'text_pattern', 'claude'])
def test_a_fingerprint_merged_with_another_stage_is_not_audio_only(world, other):
    db, pid = world
    marker = _fingerprint(pid)
    note_merged_members(marker, _ad(other, 40.0, 80.0))
    assert _audio_only(db, json.loads(json.dumps(marker))) is False


def test_a_fingerprint_alone_is_audio_only(world):
    db, pid = world
    assert _audio_only(db, _fingerprint(pid)) is True


def test_a_fingerprint_merged_with_itself_is_still_audio_only(world):
    db, pid = world
    marker = _fingerprint(pid)
    note_merged_members(marker, _fingerprint(pid, 40.0, 90.0))
    assert _audio_only(db, marker) is True


def test_markers_saved_before_the_field_fall_back_to_member_spans(world):
    db, pid = world
    older = _fingerprint(pid, merged_protected_start=10.0, merged_protected_end=70.0,
                         merged_member_spans=[
                             {'start': 10.0, 'end': 60.0, 'stage': 'fingerprint', 'pattern_id': pid},
                             {'start': 20.0, 'end': 70.0, 'stage': 'claude'}])
    assert 'merged_stages' not in older
    assert _audio_only(db, older) is False


@pytest.mark.parametrize('other', ['dai_differential', 'vad_gap'])
def test_the_detection_merge_keeps_stages_that_leave_no_member_span(other):
    merged = _merge([_ad('fingerprint', 10.0, 60.0, pattern_id=1), _ad(other, 40.0, 80.0)])
    assert len(merged) == 1
    assert set(marker_stages(merged[0])) == {'fingerprint', other}
    assert 'merged_stages' in merged[0]


def test_the_field_survives_clips_copies_and_a_json_round_trip():
    marker = _ad('fingerprint', 10.0, 60.0)
    note_merged_members(marker, _ad('vad_gap', 40.0, 80.0))
    clip_merge_spans(marker, 20.0, 50.0)
    copied = json.loads(json.dumps(dict(marker, start=20.0, end=50.0)))
    assert set(marker_stages(copied)) == {'fingerprint', 'vad_gap'}


def test_a_carved_fragment_keeps_the_stage_evidence_but_not_the_member_spans():
    marker = _ad('fingerprint', 10.0, 60.0)
    note_merged_members(marker, _ad('vad_gap', 40.0, 80.0))
    fragment = carve_fragment(marker, 10.0, 30.0)
    assert 'merged_member_spans' not in fragment
    assert set(marker_stages(fragment)) == {'fingerprint', 'vad_gap'}


def test_a_carved_fragment_of_a_fingerprint_and_an_llm_detection_is_not_audio_only(world):
    db, pid = world
    marker = _fingerprint(pid)
    note_merged_members(marker, _ad('claude', 40.0, 80.0))
    fragment = carve_fragment(json.loads(json.dumps(marker)), 10.0, 30.0)
    fragment['pattern_id'] = pid
    assert _audio_only(db, fragment) is False
