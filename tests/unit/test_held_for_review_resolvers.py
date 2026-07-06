"""Unit tests for resolve_max_ad_duration_override and resolve_cue_gated_approval (Phase C, C1).

Patterns mirror test_snap_flag_resolvers.py (B1) and test_cue_score_resolver.py (A).
"""
import logging
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))

from config import resolve_max_ad_duration_override, resolve_cue_gated_approval


class _DB:
    """Minimal DB stub for held-for-review resolvers."""

    def __init__(self, max_ad_dur=None, cue_gated=None):
        self._max_ad_dur = max_ad_dur
        self._cue_gated = cue_gated

    def get_podcast_cue_settings_overrides(self, podcast_id):
        return {
            'max_ad_duration_override': self._max_ad_dur,
            'cue_gated_approval': self._cue_gated,
        }


class _ErrorDB:
    def get_podcast_cue_settings_overrides(self, podcast_id):
        raise RuntimeError('db error')


# ---- resolve_max_ad_duration_override ----

def test_max_ad_duration_returns_set_value():
    db = _DB(max_ad_dur=240.0)
    result = resolve_max_ad_duration_override(db, 1)
    assert result == 240.0


def test_max_ad_duration_null_returns_none():
    db = _DB(max_ad_dur=None)
    assert resolve_max_ad_duration_override(db, 1) is None


def test_max_ad_duration_no_db_returns_none():
    assert resolve_max_ad_duration_override(None, 1) is None


def test_max_ad_duration_no_podcast_id_returns_none():
    db = _DB(max_ad_dur=120.0)
    assert resolve_max_ad_duration_override(db, None) is None


def test_max_ad_duration_db_error_returns_none():
    assert resolve_max_ad_duration_override(_ErrorDB(), 1) is None


def test_max_ad_duration_db_error_logs_warning(caplog):
    with caplog.at_level(logging.WARNING, logger='config'):
        resolve_max_ad_duration_override(_ErrorDB(), 1)
    assert any('max_ad_duration_override' in r.message for r in caplog.records), (
        f"Expected a warning naming the override; got: {[r.message for r in caplog.records]}"
    )


def test_max_ad_duration_zero_returns_zero():
    # Resolver does not range-check; 0 is API-invalid (range 1-3600) and can
    # only arrive via direct DB writes. Documents raw pass-through behavior.
    db = _DB(max_ad_dur=0.0)
    assert resolve_max_ad_duration_override(db, 1) == 0.0


def test_max_ad_duration_integer_coerced_to_float():
    db = _DB(max_ad_dur=300)
    result = resolve_max_ad_duration_override(db, 1)
    assert result == 300.0
    assert isinstance(result, float)


# ---- resolve_cue_gated_approval ----

def test_cue_gated_approval_true_when_set():
    db = _DB(cue_gated=1)
    assert resolve_cue_gated_approval(db, 1) is True


def test_cue_gated_approval_false_when_zero():
    db = _DB(cue_gated=0)
    assert resolve_cue_gated_approval(db, 1) is False


def test_cue_gated_approval_false_when_null():
    db = _DB(cue_gated=None)
    assert resolve_cue_gated_approval(db, 1) is False


def test_cue_gated_approval_no_db_is_false():
    assert resolve_cue_gated_approval(None, 1) is False


def test_cue_gated_approval_no_podcast_id_is_false():
    db = _DB(cue_gated=1)
    assert resolve_cue_gated_approval(db, None) is False


def test_cue_gated_approval_db_error_is_false():
    assert resolve_cue_gated_approval(_ErrorDB(), 1) is False


def test_cue_gated_approval_db_error_logs_warning(caplog):
    with caplog.at_level(logging.WARNING, logger='config'):
        resolve_cue_gated_approval(_ErrorDB(), 1)
    assert any('cue_gated_approval' in r.message for r in caplog.records), (
        f"Expected a warning naming the flag; got: {[r.message for r in caplog.records]}"
    )


def test_flags_are_independent():
    db = _DB(max_ad_dur=180.0, cue_gated=0)
    assert resolve_max_ad_duration_override(db, 1) == 180.0
    assert resolve_cue_gated_approval(db, 1) is False

    db2 = _DB(max_ad_dur=None, cue_gated=1)
    assert resolve_max_ad_duration_override(db2, 1) is None
    assert resolve_cue_gated_approval(db2, 1) is True
