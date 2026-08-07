"""Tests for two-tier pattern trust (defined vs auto-learned patterns)."""
from tests.app_bootstrap import bootstrap
bootstrap('pattern_tiers_test_')

from text_pattern_matcher import is_defined_pattern


def test_user_created_pattern_is_defined():
    assert is_defined_pattern({'created_by': 'user', 'source': 'local'})


def test_community_pattern_is_defined():
    assert is_defined_pattern({'created_by': 'auto', 'source': 'community'})


def test_auto_local_pattern_is_not_defined():
    assert not is_defined_pattern({'created_by': 'auto', 'source': 'local'})


def test_missing_fields_not_defined():
    assert not is_defined_pattern({})
