"""Unit tests for config.resolve_cue_template_score (#350 Phase 5).

Tests verify:
- Per-feed override wins over global setting.
- None override falls back to global setting.
- None override + no global falls back to the hard-coded constant.
- Exception in DB layer falls back gracefully.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))

from config import resolve_cue_template_score, AUDIO_CUE_TEMPLATE_SCORE


class _DB:
    """Minimal DB stub for resolver tests."""

    def __init__(self, per_feed=None, global_val=None):
        self._per_feed = per_feed
        self._global = global_val

    def get_podcast_cue_score_override(self, podcast_id):
        return self._per_feed

    def get_setting_float(self, key, default=0.0):
        if self._global is None:
            return default
        return self._global


class _ErrorDB:
    def get_podcast_cue_score_override(self, podcast_id):
        raise RuntimeError('db error')

    def get_setting_float(self, key, default=0.0):
        raise RuntimeError('db error')


def test_override_wins_over_global():
    db = _DB(per_feed=0.65, global_val=0.75)
    assert resolve_cue_template_score(db, 1) == 0.65


def test_none_override_uses_global():
    db = _DB(per_feed=None, global_val=0.80)
    assert resolve_cue_template_score(db, 1) == 0.80


def test_none_override_no_global_uses_constant():
    db = _DB(per_feed=None, global_val=None)
    result = resolve_cue_template_score(db, 1)
    assert result == AUDIO_CUE_TEMPLATE_SCORE


def test_no_db_returns_constant():
    assert resolve_cue_template_score(None, None) == AUDIO_CUE_TEMPLATE_SCORE


def test_db_exception_returns_constant():
    assert resolve_cue_template_score(_ErrorDB(), 1) == AUDIO_CUE_TEMPLATE_SCORE


def test_no_podcast_id_skips_override_uses_global():
    db = _DB(per_feed=0.65, global_val=0.82)
    # podcast_id=None means no per-feed lookup possible
    result = resolve_cue_template_score(db, None)
    # should use global (not the per_feed stub -- but our stub returns per_feed
    # regardless of id; so what matters is the resolver skips the call when
    # podcast_id is None and falls back to global)
    assert result == 0.82
