"""Tests for pattern_service.compute_pattern_trust (staleness-based trust tiers)."""
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))

from pattern_service import compute_pattern_trust  # noqa: E402

NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def test_recent_local_match_is_active():
    row = {'source': 'local', 'last_matched_at': '2025-12-15T00:00:00Z'}
    assert compute_pattern_trust(row, NOW) == 'active'


def test_community_confirmed_two_years_ago_never_matched_is_stale():
    row = {
        'source': 'community',
        'last_matched_at': None,
        'community_last_confirmed_at': '2024-01-01T00:00:00Z',
        'created_at': '2023-01-01T00:00:00Z',
    }
    assert compute_pattern_trust(row, NOW) == 'stale'


def test_local_pattern_never_matched_is_unproven_not_stale():
    row = {
        'source': 'local',
        'last_matched_at': None,
        'created_at': '2020-01-01T00:00:00Z',
    }
    assert compute_pattern_trust(row, NOW) == 'unproven'


def test_community_confirmed_last_month_never_matched_is_unproven():
    row = {
        'source': 'community',
        'last_matched_at': None,
        'community_last_confirmed_at': '2025-12-01T00:00:00Z',
        'created_at': '2020-01-01T00:00:00Z',
    }
    assert compute_pattern_trust(row, NOW) == 'unproven'


def test_community_pattern_with_no_timestamps_is_unproven():
    row = {'source': 'community'}
    assert compute_pattern_trust(row, NOW) == 'unproven'


def test_community_stale_by_updated_at_when_confirmed_at_absent():
    row = {
        'source': 'community',
        'last_matched_at': None,
        'community_last_confirmed_at': None,
        'updated_at': '2024-01-01T00:00:00Z',
        'created_at': '2019-01-01T00:00:00Z',
    }
    assert compute_pattern_trust(row, NOW) == 'stale'


def test_old_local_match_outside_active_window_is_unproven():
    row = {'source': 'local', 'last_matched_at': '2025-01-01T00:00:00Z'}
    assert compute_pattern_trust(row, NOW) == 'unproven'
