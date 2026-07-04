"""Unit tests for the per-feed boundary-snap flag resolvers (Phase B, task B1).

resolve_silence_snap_enabled / resolve_transition_snap_enabled:
- 1 -> True, 0 -> False, NULL -> False (simple opt-in, no global inherit).
- No db / no podcast_id -> False.
- DB error -> False with a warning log (fail-open, never raises).
"""
import logging
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))

from config import resolve_silence_snap_enabled, resolve_transition_snap_enabled


class _DB:
    """Minimal DB stub returning the extended overrides dict."""

    def __init__(self, silence=None, transition=None):
        self._silence = silence
        self._transition = transition

    def get_podcast_cue_settings_overrides(self, podcast_id):
        return {
            'cue_create_from_pairs_override': None,
            'cue_pair_min_break_override': None,
            'cue_pair_max_break_override': None,
            'cue_pair_max_break_fraction_override': None,
            'cue_snap_confidence_override': None,
            'cue_snap_lead_override': None,
            'cue_snap_lag_override': None,
            'silence_snap_enabled': self._silence,
            'transition_snap_enabled': self._transition,
        }


class _ErrorDB:
    def get_podcast_cue_settings_overrides(self, podcast_id):
        raise RuntimeError('db error')


# -- Flag set --

def test_silence_snap_enabled_when_set():
    assert resolve_silence_snap_enabled(_DB(silence=1), 1) is True


def test_transition_snap_enabled_when_set():
    assert resolve_transition_snap_enabled(_DB(transition=1), 1) is True


# -- Explicit 0 reads as off --

def test_silence_snap_zero_is_false():
    assert resolve_silence_snap_enabled(_DB(silence=0), 1) is False


def test_transition_snap_zero_is_false():
    assert resolve_transition_snap_enabled(_DB(transition=0), 1) is False


# -- NULL reads as off --

def test_silence_snap_null_is_false():
    assert resolve_silence_snap_enabled(_DB(), 1) is False


def test_transition_snap_null_is_false():
    assert resolve_transition_snap_enabled(_DB(), 1) is False


# -- Flags are independent --

def test_flags_are_independent():
    db = _DB(silence=1, transition=0)
    assert resolve_silence_snap_enabled(db, 1) is True
    assert resolve_transition_snap_enabled(db, 1) is False


# -- Missing db / podcast_id --

def test_no_db_is_false():
    assert resolve_silence_snap_enabled(None, 1) is False
    assert resolve_transition_snap_enabled(None, 1) is False


def test_no_podcast_id_is_false():
    db = _DB(silence=1, transition=1)
    assert resolve_silence_snap_enabled(db, None) is False
    assert resolve_transition_snap_enabled(db, None) is False


# -- DB error fails open to False with a warning --

def test_silence_snap_error_is_false():
    assert resolve_silence_snap_enabled(_ErrorDB(), 1) is False


def test_transition_snap_error_is_false():
    assert resolve_transition_snap_enabled(_ErrorDB(), 1) is False


def test_error_logs_warning(caplog):
    with caplog.at_level(logging.WARNING, logger='config'):
        resolve_silence_snap_enabled(_ErrorDB(), 1)
    assert any('silence_snap_enabled' in r.message for r in caplog.records), (
        f"Expected a warning naming the flag; got: {[r.message for r in caplog.records]}"
    )
