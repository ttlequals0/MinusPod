"""Per-device batch-size ceiling for transcription.

BATCH_SIZE_TIERS picks a batch size from audio duration alone, so on a card
where the top tier never fits, every short episode paid two CUDA OOM failures
before settling. The ceiling records the post-OOM candidate so later episodes
start at a size that fits.
"""

from tests.app_bootstrap import bootstrap

_test_data_dir = bootstrap('batch_size_test_')

import database  # noqa: E402
from transcriber import Transcriber  # noqa: E402

def _db():
    """Resolve the singleton per call, the same way the code under test does.

    A module-level instance captured at import can point at an earlier test
    module's data dir once another module re-bootstraps, so writes here would
    land somewhere the transcriber never reads.
    """
    return database.Database()


def _fresh():
    # Empty string is the "unset" form here: _batch_size_ceiling treats any
    # falsy stored value as no ceiling.
    _db().set_setting(Transcriber.BATCH_CEILING_SETTING, '')
    return Transcriber()


def test_tier_used_when_no_ceiling_stored():
    t = _fresh()
    assert t.get_batch_size_for_duration(30 * 60) == 16


def test_ceiling_clamps_a_higher_tier():
    t = _fresh()
    t.record_batch_size_ceiling(4)
    assert t.get_batch_size_for_duration(30 * 60) == 4


def test_lower_tier_still_wins_over_a_higher_ceiling():
    """A long episode must keep its smaller tier; the ceiling is an upper bound,
    not a floor."""
    t = _fresh()
    t.record_batch_size_ceiling(16)
    assert t.get_batch_size_for_duration(150 * 60) == 4


def test_ceiling_never_drops_below_one():
    t = _fresh()
    t.record_batch_size_ceiling(0)
    assert t.get_batch_size_for_duration(30 * 60) >= 1


def test_ceiling_only_ratchets_down():
    """Evidence that a size does not fit must not be undone by a later write of
    a larger candidate."""
    t = _fresh()
    t.record_batch_size_ceiling(4)
    t.record_batch_size_ceiling(16)
    assert t.get_batch_size_for_duration(30 * 60) == 4


def test_unknown_duration_keeps_the_conservative_default():
    t = _fresh()
    assert t.get_batch_size_for_duration(None) == 8


def test_unknown_duration_is_still_clamped():
    t = _fresh()
    t.record_batch_size_ceiling(2)
    assert t.get_batch_size_for_duration(None) == 2


def test_malformed_stored_ceiling_is_ignored():
    t = _fresh()
    _db().set_setting(Transcriber.BATCH_CEILING_SETTING, 'banana')
    assert t.get_batch_size_for_duration(30 * 60) == 16
