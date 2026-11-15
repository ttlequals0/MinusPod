"""create_windows() windowing on real transcript fixtures."""
import json
import os
import pytest

# Load fixtures
FIXTURES_DIR = os.path.join(os.path.dirname(__file__), '..', 'fixtures')


def load_fixture(name):
    path = os.path.join(FIXTURES_DIR, name)
    if not os.path.exists(path):
        pytest.skip(f"Fixture {name} not found")
    with open(path) as f:
        return json.load(f)


class TestWindowCreation:
    """Test that create_windows produces correct overlapping windows from real transcripts."""

    def test_long_window_count(self):
        """A ~169min episode should produce ~25 windows with 10min size, 3min overlap."""
        from ad_detector import create_windows
        fixture = load_fixture('example_long_transcript.json')
        windows = create_windows(fixture['segments'])
        # Each window covers 10 min with 3 min overlap = 7 min advance
        # 169 min / 7 min ~= 24 windows, plus partial
        assert 20 <= len(windows) <= 30, f"Expected ~24 windows, got {len(windows)}"

    def test_short_window_count(self):
        """A ~37min episode should produce ~6 windows."""
        from ad_detector import create_windows
        fixture = load_fixture('example_transcript.json')
        windows = create_windows(fixture['segments'])
        assert 4 <= len(windows) <= 10, f"Expected ~6 windows, got {len(windows)}"

    def test_windows_cover_full_episode(self):
        """Windows should cover from start to end of the episode."""
        from ad_detector import create_windows
        fixture = load_fixture('example_long_transcript.json')
        windows = create_windows(fixture['segments'])

        # create_windows returns dicts with 'start', 'end', 'segments'
        first_start = windows[0]['start']
        last_end = windows[-1]['end']

        assert first_start < 30, "First window should start near beginning"
        assert last_end > fixture['duration'] - 60, \
            "Last window should reach near end of episode"

    def test_each_ad_covered_by_at_least_one_window(self):
        """Every known ad should fall within at least one processing window."""
        from ad_detector import create_windows
        fixture = load_fixture('example_long_transcript.json')
        windows = create_windows(fixture['segments'])

        for ad in fixture['expected_ads']:
            ad_mid = (ad['start'] + ad['end']) / 2
            covered = any(
                w['start'] <= ad_mid <= w['end']
                for w in windows
            )
            assert covered, (
                f"Ad {ad['sponsor']} at {ad['start']:.0f}-{ad['end']:.0f}s "
                f"not covered by any window"
            )
