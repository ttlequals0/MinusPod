"""Tests for config.resolve_transcript_differential (2.98.0 transcript
differential, Task 1). Mirrors TestResolveSkipSecondPass in
test_skip_second_pass.py.
"""
from types import SimpleNamespace

from config import resolve_transcript_differential


class TestResolveTranscriptDifferential:
    def test_feed_one_overrides_the_global(self):
        db = SimpleNamespace(get_setting_bool=lambda key, default: False)
        assert resolve_transcript_differential({'transcript_differential': 1}, db=db) is True

    def test_feed_zero_overrides_the_global(self):
        db = SimpleNamespace(get_setting_bool=lambda key, default: True)
        assert resolve_transcript_differential({'transcript_differential': 0}, db=db) is False

    def test_feed_none_falls_back_to_the_global_setting(self):
        db = SimpleNamespace(get_setting_bool=lambda key, default: True)
        assert resolve_transcript_differential({'transcript_differential': None}, db=db) is True
        db = SimpleNamespace(get_setting_bool=lambda key, default: False)
        assert resolve_transcript_differential({'transcript_differential': None}, db=db) is False
        assert resolve_transcript_differential(None, db=db) is False
        assert resolve_transcript_differential({}, db=db) is False

    def test_no_db_defaults_true(self):
        assert resolve_transcript_differential(None) is True
        assert resolve_transcript_differential({}) is True
        assert resolve_transcript_differential({'transcript_differential': None}) is True
