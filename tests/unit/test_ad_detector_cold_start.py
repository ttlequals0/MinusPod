"""process_transcript must build detector dependencies before the stage
1/2 gates.

Regression: the first run to reach ad detection in a fresh process
silently skipped fingerprint and text-pattern matching because the
matcher attributes were still None when the stage gates evaluated
(deps were only built mid-run, at stage 3). See the 2.75.0 changelog.
"""

from unittest.mock import patch

from ad_detector import AdDetector

SEGMENTS = [
    {'start': 0.0, 'end': 30.0,
     'text': 'welcome to the show this episode is brought to you by capital one'},
    {'start': 30.0, 'end': 60.0,
     'text': 'now back to the news of the day with more content for you'},
]


class _RecordingMatcher:
    def __init__(self):
        self.calls = 0

    def is_available(self):
        return True

    def find_matches(self, *args, **kwargs):
        self.calls += 1
        return []


class _FakeDb:
    def get_false_positive_corrections(self, episode_id):
        return []

    def get_podcast_false_positive_texts(self, slug):
        return []

    def get_setting(self, key):
        return None

    def get_setting_float(self, key, default):
        return default

    def get_active_pattern_sponsors(self):
        return set()


def test_pattern_stage_runs_on_cold_detector():
    """A detector that has never been initialized must still run stage 2."""
    det = AdDetector()
    assert det.text_pattern_matcher is None, "expected a cold detector"

    matcher = _RecordingMatcher()

    def build_deps():
        # Stand-in for the real initialize_client(): installs deps.
        det.db = _FakeDb()
        det.text_pattern_matcher = matcher

    with patch.object(det, 'initialize_client', side_effect=build_deps), \
         patch.object(det, 'detect_ads',
                      return_value={'ads': [], 'status': 'success'}):
        det.process_transcript(
            SEGMENTS, 'Test Podcast', 'Test Episode',
            slug='test-slug', episode_id='ep1', podcast_id='test-slug',
            keep_content=False,
        )

    assert matcher.calls == 1, (
        "text pattern stage did not run on a cold detector: "
        "dependencies were not initialized before the stage gates"
    )


def test_learn_from_detections_initializes_deps_on_cold_detector():
    """A detector that has never been initialized must still be able to
    learn patterns: initialize_client() must run before the
    text_pattern_matcher gate, not after it.
    """
    det = AdDetector()
    assert det.text_pattern_matcher is None, "expected a cold detector"

    matcher = _RecordingMatcher()
    init_calls = []

    def build_deps():
        init_calls.append(1)
        det.db = _FakeDb()
        det.text_pattern_matcher = matcher

    with patch.object(det, 'initialize_client', side_effect=build_deps):
        result = det.learn_from_detections(
            ads=[], segments=SEGMENTS, podcast_id='test-slug',
            episode_id='ep1',
        )

    assert init_calls == [1], (
        "learn_from_detections did not call initialize_client(): "
        "a cold detector would silently no-op via the text_pattern_matcher "
        "guard instead of reaching the learning path"
    )
    assert result == 0
