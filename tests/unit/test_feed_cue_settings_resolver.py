"""Unit tests for config.resolve_feed_cue_settings (Task A2).

Tests verify:
- Per-feed override wins over global for each of the 7 knobs.
- NULL override falls back to global setting.
- NULL override + no global falls back to code default.
- All 7 values returned from ONE db getter call (not seven).
- Exception in DB falls back gracefully to defaults.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))

from config import (
    resolve_feed_cue_settings,
    AUDIO_CUE_SNAP_CONFIDENCE,
    AUDIO_CUE_PAIR_MIN_BREAK_SECONDS,
    AUDIO_CUE_PAIR_MAX_BREAK_SECONDS,
    AUDIO_CUE_PAIR_MAX_BREAK_FRACTION,
)
from ad_detector.cue_boundary_snap import DEFAULT_SNAP_LEAD_SECONDS, DEFAULT_SNAP_LAG_SECONDS

_CREATE_FROM_PAIRS_DEFAULT = False


class _DB:
    """Minimal DB stub for resolver tests."""

    def __init__(self, overrides=None, global_vals=None, getter_call_count=None):
        # overrides: dict of column_name -> value (None means NULL / not set)
        self._overrides = overrides or {}
        self._global = global_vals or {}
        self._call_count = getter_call_count  # mutable list for tracking

    def get_podcast_cue_settings_overrides(self, podcast_id):
        if self._call_count is not None:
            self._call_count.append(1)
        return {
            'cue_create_from_pairs_override': self._overrides.get('cue_create_from_pairs_override'),
            'cue_pair_min_break_override': self._overrides.get('cue_pair_min_break_override'),
            'cue_pair_max_break_override': self._overrides.get('cue_pair_max_break_override'),
            'cue_pair_max_break_fraction_override': self._overrides.get('cue_pair_max_break_fraction_override'),
            'cue_snap_confidence_override': self._overrides.get('cue_snap_confidence_override'),
            'cue_snap_lead_override': self._overrides.get('cue_snap_lead_override'),
            'cue_snap_lag_override': self._overrides.get('cue_snap_lag_override'),
        }

    def get_setting_bool(self, key, default=False):
        return self._global.get(key, default)

    def get_setting_float(self, key, default=0.0):
        return self._global.get(key, default)


class _ErrorDB:
    def get_podcast_cue_settings_overrides(self, podcast_id):
        raise RuntimeError('db error')

    def get_setting_bool(self, key, default=False):
        raise RuntimeError('db error')

    def get_setting_float(self, key, default=0.0):
        raise RuntimeError('db error')


# -- Override wins --

def test_create_from_pairs_override_wins():
    db = _DB(overrides={'cue_create_from_pairs_override': 1},
             global_vals={'audio_cue_create_from_pairs': False})
    result = resolve_feed_cue_settings(db, 1)
    assert result['create_from_pairs'] is True


def test_create_from_pairs_override_force_off():
    db = _DB(overrides={'cue_create_from_pairs_override': 0},
             global_vals={'audio_cue_create_from_pairs': True})
    result = resolve_feed_cue_settings(db, 1)
    assert result['create_from_pairs'] is False


def test_pair_min_break_override_wins():
    db = _DB(overrides={'cue_pair_min_break_override': 15.0},
             global_vals={'audio_cue_pair_min_break_seconds': 30.0})
    assert resolve_feed_cue_settings(db, 1)['pair_min_break'] == 15.0


def test_pair_max_break_override_wins():
    db = _DB(overrides={'cue_pair_max_break_override': 120.0},
             global_vals={'audio_cue_pair_max_break_seconds': 480.0})
    assert resolve_feed_cue_settings(db, 1)['pair_max_break'] == 120.0


def test_pair_max_break_fraction_override_wins():
    db = _DB(overrides={'cue_pair_max_break_fraction_override': 0.3},
             global_vals={'audio_cue_pair_max_break_fraction': 0.5})
    assert resolve_feed_cue_settings(db, 1)['pair_max_break_fraction'] == 0.3


def test_snap_confidence_override_wins():
    db = _DB(overrides={'cue_snap_confidence_override': 0.70},
             global_vals={'audio_cue_snap_confidence': 0.80})
    assert resolve_feed_cue_settings(db, 1)['snap_confidence'] == 0.70


def test_snap_lead_override_wins():
    db = _DB(overrides={'cue_snap_lead_override': 5.0},
             global_vals={'audio_cue_snap_lead_seconds': 10.0})
    assert resolve_feed_cue_settings(db, 1)['snap_lead'] == 5.0


def test_snap_lag_override_wins():
    db = _DB(overrides={'cue_snap_lag_override': 2.0},
             global_vals={'audio_cue_snap_lag_seconds': 4.0})
    assert resolve_feed_cue_settings(db, 1)['snap_lag'] == 2.0


# -- NULL override falls back to global --

def test_null_override_uses_global_create_from_pairs():
    db = _DB(overrides={'cue_create_from_pairs_override': None},
             global_vals={'audio_cue_create_from_pairs': True})
    assert resolve_feed_cue_settings(db, 1)['create_from_pairs'] is True


def test_null_override_uses_global_snap_confidence():
    db = _DB(overrides={'cue_snap_confidence_override': None},
             global_vals={'audio_cue_snap_confidence': 0.90})
    assert resolve_feed_cue_settings(db, 1)['snap_confidence'] == 0.90


def test_null_override_uses_global_pair_min_break():
    db = _DB(overrides={'cue_pair_min_break_override': None},
             global_vals={'audio_cue_pair_min_break_seconds': 45.0})
    assert resolve_feed_cue_settings(db, 1)['pair_min_break'] == 45.0


# -- NULL override + no global falls back to code default --

def test_all_null_uses_code_defaults():
    db = _DB()  # no overrides, no globals
    result = resolve_feed_cue_settings(db, 1)
    assert result['create_from_pairs'] is _CREATE_FROM_PAIRS_DEFAULT
    assert result['pair_min_break'] == AUDIO_CUE_PAIR_MIN_BREAK_SECONDS
    assert result['pair_max_break'] == AUDIO_CUE_PAIR_MAX_BREAK_SECONDS
    assert result['pair_max_break_fraction'] == AUDIO_CUE_PAIR_MAX_BREAK_FRACTION
    assert result['snap_confidence'] == AUDIO_CUE_SNAP_CONFIDENCE
    assert result['snap_lead'] == DEFAULT_SNAP_LEAD_SECONDS
    assert result['snap_lag'] == DEFAULT_SNAP_LAG_SECONDS


# -- Single getter call --

def test_one_db_read_for_all_seven():
    """Resolver must fetch all 7 overrides in one db call."""
    call_count = []
    db = _DB(getter_call_count=call_count)
    resolve_feed_cue_settings(db, 1)
    assert len(call_count) == 1, (
        f"Expected 1 db getter call, got {len(call_count)}"
    )


# -- podcast_id=None skips override lookup --

def test_no_podcast_id_skips_override():
    db = _DB(overrides={'cue_snap_confidence_override': 0.50},
             global_vals={'audio_cue_snap_confidence': 0.88})
    result = resolve_feed_cue_settings(db, None)
    assert result['snap_confidence'] == 0.88


# -- Exception falls back to defaults --

def test_exception_falls_back_to_defaults():
    result = resolve_feed_cue_settings(_ErrorDB(), 1)
    assert result['create_from_pairs'] is _CREATE_FROM_PAIRS_DEFAULT
    assert result['snap_confidence'] == AUDIO_CUE_SNAP_CONFIDENCE
    assert result['snap_lead'] == DEFAULT_SNAP_LEAD_SECONDS


def test_no_db_returns_defaults():
    result = resolve_feed_cue_settings(None, None)
    assert result['create_from_pairs'] is _CREATE_FROM_PAIRS_DEFAULT
    assert result['pair_min_break'] == AUDIO_CUE_PAIR_MIN_BREAK_SECONDS
