"""Validate real transcript fixtures and test windowing logic.

Uses transcript data pulled from the live instance to verify:
- Fixture integrity (segments cover ad regions, sponsor keywords present)
- create_windows() produces correct overlapping windows for real episodes
- Every known ad falls within at least one processing window

These tests do NOT run the LLM or the full detection pipeline.

Fixtures:
    tests/fixtures/sn1071_transcript.json  - Security Now 1071 (8 ads, 169min)
    tests/fixtures/dtns5239_transcript.json - DTNS 5239 (5 ads, 37min)
"""
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


class TestTranscriptFixturesExist:
    """Verify test fixtures are present and well-formed."""

    def test_sn1071_fixture_exists(self):
        fixture = load_fixture('sn1071_transcript.json')
        assert len(fixture['segments']) > 100
        assert len(fixture['expected_ads']) >= 7
        assert fixture['slug'] == 'security-now-audio'

    def test_dtns5239_fixture_exists(self):
        fixture = load_fixture('dtns5239_transcript.json')
        assert len(fixture['segments']) > 30
        assert len(fixture['expected_ads']) >= 4
        assert fixture['slug'] == 'daily-tech-news-show'

    def test_segments_have_required_fields(self):
        fixture = load_fixture('sn1071_transcript.json')
        for seg in fixture['segments'][:5]:
            assert 'start' in seg
            assert 'end' in seg
            assert 'text' in seg
            assert isinstance(seg['start'], (int, float))
            assert isinstance(seg['end'], (int, float))
            assert seg['end'] > seg['start']

    def test_expected_ads_have_required_fields(self):
        fixture = load_fixture('sn1071_transcript.json')
        for ad in fixture['expected_ads']:
            assert 'sponsor' in ad
            assert 'start' in ad
            assert 'end' in ad
            assert 'confidence' in ad


class TestAdTimestampCoverage:
    """Verify that transcript segments cover the time ranges where ads are expected."""

    def test_sn1071_segments_cover_ad_regions(self):
        """Transcript segments should exist in the time ranges of known ads."""
        fixture = load_fixture('sn1071_transcript.json')
        segments = fixture['segments']

        for ad in fixture['expected_ads']:
            # Find segments that overlap with this ad's time range
            overlapping = [
                s for s in segments
                if s['end'] > ad['start'] and s['start'] < ad['end']
            ]
            assert len(overlapping) > 0, (
                f"No transcript segments found for ad "
                f"{ad['sponsor']} at {ad['start']:.0f}-{ad['end']:.0f}s"
            )

    def test_dtns5239_segments_cover_ad_regions(self):
        fixture = load_fixture('dtns5239_transcript.json')
        segments = fixture['segments']

        for ad in fixture['expected_ads']:
            overlapping = [
                s for s in segments
                if s['end'] > ad['start'] and s['start'] < ad['end']
            ]
            assert len(overlapping) > 0, (
                f"No transcript segments found for ad "
                f"{ad['sponsor']} at {ad['start']:.0f}-{ad['end']:.0f}s"
            )


class TestSponsorKeywordsInTranscript:
    """Verify that sponsor names or related keywords appear in the transcript
    near where ads were detected -- confirming the fixtures are valid."""

    def _get_text_in_range(self, segments, start, end, padding=30):
        """Get concatenated text from segments in a time range with padding."""
        return ' '.join(
            s['text'] for s in segments
            if s['end'] > (start - padding) and s['start'] < (end + padding)
        ).lower()

    def test_sn1071_hoxhunt_sponsor_in_transcript(self):
        fixture = load_fixture('sn1071_transcript.json')
        ad = fixture['expected_ads'][0]  # Hoxhunt
        text = self._get_text_in_range(fixture['segments'], ad['start'], ad['end'])
        # Whisper may transcribe "Hoxhunt" differently
        assert any(kw in text for kw in ['hoxhunt', 'hox hunt', 'hawks hunt', 'hawkshunt']), \
            f"Expected Hoxhunt keywords in transcript near {ad['start']:.0f}s"

    def test_sn1071_guardsquare_sponsor_in_transcript(self):
        fixture = load_fixture('sn1071_transcript.json')
        # Find GuardSquare ad
        gs_ads = [a for a in fixture['expected_ads'] if a['sponsor'] == 'GuardSquare']
        assert gs_ads, "GuardSquare ad not found in fixture"
        ad = gs_ads[0]
        text = self._get_text_in_range(fixture['segments'], ad['start'], ad['end'])
        assert any(kw in text for kw in ['guardsquare', 'guard square', 'mobile app security']), \
            f"Expected GuardSquare keywords in transcript near {ad['start']:.0f}s"

    def test_sn1071_zscaler_sponsor_in_transcript(self):
        fixture = load_fixture('sn1071_transcript.json')
        zs_ads = [a for a in fixture['expected_ads'] if a['sponsor'] == 'Zscaler']
        assert zs_ads, "Zscaler ad not found in fixture"
        ad = zs_ads[0]
        text = self._get_text_in_range(fixture['segments'], ad['start'], ad['end'])
        assert any(kw in text for kw in ['zscaler', 'z scaler']), \
            f"Expected Zscaler keywords in transcript near {ad['start']:.0f}s"

    def test_dtns5239_network_inserted_ad_in_transcript(self):
        """Network-inserted ads (Acast) may not contain sponsor name in transcript.
        Verify that the ad region contains promotional/network content markers."""
        fixture = load_fixture('dtns5239_transcript.json')
        babbel_ads = [a for a in fixture['expected_ads'] if a['sponsor'] == 'Babbel']
        assert babbel_ads, "Babbel ad not found in fixture"
        ad = babbel_ads[0]
        text = self._get_text_in_range(fixture['segments'], ad['start'], ad['end'])
        # Network-inserted ads often contain promotional language from the ad network
        assert any(kw in text for kw in [
            'babbel', 'babel', 'babble', 'language',
            'acast', 'recommend', 'podcast', 'listen to',
            'welcome to', 'sponsored'
        ]), f"Expected ad-related keywords in transcript near {ad['start']:.0f}s"


class TestWindowCreation:
    """Test that create_windows produces correct overlapping windows from real transcripts."""

    def test_sn1071_window_count(self):
        """A ~169min episode should produce ~25 windows with 10min size, 3min overlap."""
        from ad_detector import create_windows
        fixture = load_fixture('sn1071_transcript.json')
        windows = create_windows(fixture['segments'])
        # Each window covers 10 min with 3 min overlap = 7 min advance
        # 169 min / 7 min ~= 24 windows, plus partial
        assert 20 <= len(windows) <= 30, f"Expected ~24 windows, got {len(windows)}"

    def test_dtns5239_window_count(self):
        """A ~37min episode should produce ~6 windows."""
        from ad_detector import create_windows
        fixture = load_fixture('dtns5239_transcript.json')
        windows = create_windows(fixture['segments'])
        assert 4 <= len(windows) <= 10, f"Expected ~6 windows, got {len(windows)}"

    def test_windows_cover_full_episode(self):
        """Windows should cover from start to end of the episode."""
        from ad_detector import create_windows
        fixture = load_fixture('sn1071_transcript.json')
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
        fixture = load_fixture('sn1071_transcript.json')
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
