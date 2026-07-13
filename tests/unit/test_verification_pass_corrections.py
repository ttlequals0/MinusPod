"""Unit tests for verification_pass timestamp mapping helpers (issue #183)."""

from verification_pass import _build_timestamp_map, _map_correction_to_processed


def test_map_correction_no_cuts():
    assert _map_correction_to_processed(100.0, 200.0, []) == (100.0, 200.0)


def test_map_correction_cut_before():
    cuts = _build_timestamp_map([{'start': 0.0, 'end': 50.0}])
    assert _map_correction_to_processed(100.0, 150.0, cuts) == (50.0, 100.0)


def test_map_correction_cut_inside():
    cuts = _build_timestamp_map([{'start': 100.0, 'end': 200.0}])
    assert _map_correction_to_processed(120.0, 180.0, cuts) is None


def test_map_correction_cut_overlaps_start():
    cuts = _build_timestamp_map([{'start': 100.0, 'end': 150.0}])
    # Visible portion is [150, 200) in original; pass-1 removed 50s before that
    # endpoint, so processed coordinates are (100.0, 150.0).
    assert _map_correction_to_processed(120.0, 200.0, cuts) == (100.0, 150.0)


def test_map_correction_cut_overlaps_end():
    cuts = _build_timestamp_map([{'start': 150.0, 'end': 200.0}])
    # Visible portion is [100, 150) in original; no cut precedes -- processed
    # coordinates equal original coordinates.
    assert _map_correction_to_processed(100.0, 180.0, cuts) == (100.0, 150.0)


def test_map_correction_two_cuts_before():
    cuts = _build_timestamp_map([
        {'start': 0.0, 'end': 30.0},
        {'start': 60.0, 'end': 90.0},
    ])
    # Total removed before correction = 60s
    assert _map_correction_to_processed(200.0, 250.0, cuts) == (140.0, 190.0)


def test_map_correction_issue_183_case():
    """Regression for issue #183: pass 1 cut [0, 275.7], reject (451.65, 551.05)."""
    cuts = _build_timestamp_map([{'start': 0.0, 'end': 275.7}])
    proc = _map_correction_to_processed(451.65, 551.05, cuts)
    assert proc is not None
    assert abs(proc[0] - 175.95) < 1e-6
    assert abs(proc[1] - 275.35) < 1e-6


def test_map_correction_empty_range():
    assert _map_correction_to_processed(100.0, 100.0, []) is None
    assert _map_correction_to_processed(150.0, 100.0, []) is None


def test_map_to_original_accounts_for_replacement_audio():
    from verification_pass import _map_to_original
    cuts = [(50.0, 20.0)]  # cut 50-70s, replaced by a 2s beep
    # Content originally at 100s sits at 100 - (20 - 2) = 82s in beeped audio.
    assert _map_to_original(82.0, cuts, replacement_duration=2.0) == 100.0


def test_map_to_original_inside_replacement_maps_into_cut():
    from verification_pass import _map_to_original
    cuts = [(50.0, 20.0)]
    # 1s into the beep maps just inside the removed span, keeping the
    # mapping monotonic.
    assert _map_to_original(51.0, cuts, replacement_duration=2.0) == 51.0


def test_map_to_original_zero_replacement_unchanged():
    from verification_pass import _map_to_original
    cuts = [(50.0, 20.0)]
    assert _map_to_original(80.0, cuts) == 100.0


def test_map_correction_accounts_for_replacement_audio():
    cuts = [(50.0, 20.0)]
    # Original 100-150s sits at 82-132s in beeped audio (18s shift, not 20s).
    assert _map_correction_to_processed(
        100.0, 150.0, cuts, replacement_duration=2.0) == (82.0, 132.0)


def test_map_correction_start_in_cut_lands_after_beep():
    cuts = [(50.0, 20.0)]
    # Visible portion starts at the cut end (70s original), which sits right
    # after the beep in processed audio: 70 - 18 = 52 = beep end.
    proc = _map_correction_to_processed(60.0, 100.0, cuts, replacement_duration=2.0)
    assert proc == (52.0, 82.0)
