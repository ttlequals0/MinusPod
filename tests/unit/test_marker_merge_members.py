import os
import sys
import tempfile

os.environ.setdefault('MINUSPOD_DATA_DIR', tempfile.mkdtemp(prefix='mergemem_test_'))
os.environ.setdefault('SECRET_KEY', 'test-secret')
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))

from utils.markers import note_merged_members


def _ad(start, end, stage):
    return {'start': start, 'end': end, 'detection_stage': stage}


def test_claude_members_are_protected():
    base = _ad(100.0, 130.0, 'claude')
    note_merged_members(base, _ad(131.0, 160.0, 'claude'))
    assert base['merged_protected_start'] == 100.0
    assert base['merged_protected_end'] == 160.0


def test_all_differential_members_yield_null_protection():
    base = _ad(837.2, 1040.0, 'dai_differential')
    note_merged_members(base, _ad(1041.0, 1068.5, 'dai_differential'))
    assert base['merged_protected_start'] is None
    assert base['merged_protected_end'] is None


def test_mixed_members_protect_only_anchored_span():
    base = _ad(830.0, 980.0, 'dai_differential')
    note_merged_members(base, _ad(891.3, 1007.9, 'claude'))
    assert base['merged_protected_start'] == 891.3
    assert base['merged_protected_end'] == 1007.9


def test_chained_merges_do_not_promote_extended_span():
    # claude base absorbs a differential tail, span is extended by the
    # caller, then a second merge folds another differential region. The
    # protected union must stay the original claude span.
    base = _ad(100.0, 130.0, 'claude')
    note_merged_members(base, _ad(131.0, 170.0, 'dai_differential'))
    base['end'] = 170.0
    note_merged_members(base, _ad(171.0, 200.0, 'dai_differential'))
    base['end'] = 200.0
    assert base['merged_protected_start'] == 100.0
    assert base['merged_protected_end'] == 130.0


def test_folding_a_previously_merged_marker_carries_its_protection():
    other = _ad(300.0, 400.0, 'dai_differential')
    other['merged_protected_start'] = 320.0
    other['merged_protected_end'] = 360.0
    base = _ad(100.0, 290.0, 'text_pattern')
    note_merged_members(base, other)
    assert base['merged_protected_start'] == 100.0
    assert base['merged_protected_end'] == 360.0


def test_keep_content_and_cue_pair_members_are_protected():
    base = _ad(100.0, 130.0, 'keep_content')
    note_merged_members(base, _ad(131.0, 160.0, 'cue_pair'))
    assert base['merged_protected_start'] == 100.0
    assert base['merged_protected_end'] == 160.0


def test_unknown_stage_fails_protected():
    base = _ad(100.0, 130.0, 'dai_differential')
    note_merged_members(base, _ad(131.0, 160.0, 'some_future_stage'))
    assert base['merged_protected_start'] == 131.0
    assert base['merged_protected_end'] == 160.0
