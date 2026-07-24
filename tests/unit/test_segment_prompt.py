"""Tests for segment category emission in the detection prompt (issue #565).

DEFAULT_SYSTEM_PROMPT always asks the LLM for a category on every ad.
SHOW_SEGMENTS_PROMPT_SECTION (intro/outro/recap detection) is opt-in per
podcast and is appended only when the podcast's detect_show_segments column
is truthy. Normalization at the ad_detector merge seam applies regardless
of that flag: it is a defense against any out-of-enum LLM value, not a gate
on show-segment categories.
"""
from ad_detector import AdDetector
from config import SEGMENT_CATEGORIES, normalize_segment_category
from utils.constants import DEFAULT_SYSTEM_PROMPT, SHOW_SEGMENTS_PROMPT_SECTION
from ad_detector.prompts import parse_ads_from_response


class _FakeDb:
    """Minimal db stub: get_setting for no-override, get_podcast_by_slug
    for the detect_show_segments flag."""

    def __init__(self, detect_show_segments=False, system_prompt=None):
        self._detect_show_segments = detect_show_segments
        self._system_prompt = system_prompt

    def get_setting(self, key):
        if key == 'system_prompt':
            return self._system_prompt
        return None

    def get_podcast_by_slug(self, slug):
        if slug is None:
            return None
        return {'slug': slug, 'detect_show_segments': self._detect_show_segments}


def _detector(detect_show_segments=False, system_prompt=None):
    det = AdDetector()
    det.db = _FakeDb(detect_show_segments=detect_show_segments,
                      system_prompt=system_prompt)
    det.sponsor_service = None
    return det


class TestDefaultPromptCategoryInstructions:
    def test_category_field_required_in_output_format(self):
        assert '"category"' in DEFAULT_SYSTEM_PROMPT

    def test_all_four_base_categories_defined(self):
        for cat in ('sponsor', 'cross_promo', 'self_promo', 'interaction'):
            assert cat in DEFAULT_SYSTEM_PROMPT

    def test_intro_outro_recap_gated_on_show_segments_section(self):
        assert 'SHOW SEGMENTS' in DEFAULT_SYSTEM_PROMPT
        assert 'only when this prompt also contains a SHOW SEGMENTS section' \
            in DEFAULT_SYSTEM_PROMPT


class TestShowSegmentsSection:
    def test_section_defines_intro_outro_recap(self):
        for cat in ('intro', 'outro', 'recap'):
            assert cat in SHOW_SEGMENTS_PROMPT_SECTION

    def test_section_flags_cold_open_as_content(self):
        assert 'cold open' in SHOW_SEGMENTS_PROMPT_SECTION.lower()


class TestPromptComposition:
    def test_section_appended_when_flag_on(self):
        det = _detector(detect_show_segments=True)
        prompt = det._build_detection_system_prompt('feed-a')
        assert SHOW_SEGMENTS_PROMPT_SECTION in prompt

    def test_section_absent_when_flag_off(self):
        det = _detector(detect_show_segments=False)
        prompt = det._build_detection_system_prompt('feed-a')
        assert SHOW_SEGMENTS_PROMPT_SECTION not in prompt

    def test_section_absent_when_no_podcast_row(self):
        det = _detector(detect_show_segments=True)
        # slug=None -> get_podcast_by_slug short-circuits to no row.
        prompt = det._build_detection_system_prompt(None)
        assert SHOW_SEGMENTS_PROMPT_SECTION not in prompt

    def test_section_rides_along_on_operator_override(self):
        # Operator has replaced system_prompt entirely; the show-segments
        # section is appended AFTER override resolution, so an opted-in
        # feed still gets it even though it is nowhere in their override.
        override = "Custom instructions with no category talk at all."
        det = _detector(detect_show_segments=True, system_prompt=override)
        prompt = det._build_detection_system_prompt('feed-a')
        assert override in prompt
        assert SHOW_SEGMENTS_PROMPT_SECTION in prompt

    def test_flag_off_leaves_operator_override_untouched_by_section(self):
        override = "Custom instructions with no category talk at all."
        det = _detector(detect_show_segments=False, system_prompt=override)
        prompt = det._build_detection_system_prompt('feed-a')
        assert override in prompt
        assert SHOW_SEGMENTS_PROMPT_SECTION not in prompt


class TestParsedCategorySurvivesMergeSeam:
    def _mock_response(self, category):
        return (
            '[{"start": 10.0, "end": 40.0, "confidence": 0.9, '
            '"category": "%s", "reason": "Theme music and welcome", '
            '"end_text": "welcome back"}]' % category
        )

    def test_intro_category_survives_parse_and_merge(self):
        det = AdDetector()
        raw_ads = parse_ads_from_response(
            self._mock_response('intro'), slug='feed-a', episode_id='ep1')
        assert raw_ads and raw_ads[0]['category'] == 'intro'

        merged = det._merge_detection_results(raw_ads)
        assert len(merged) == 1
        assert merged[0]['category'] == 'intro'

    def test_recap_category_survives_with_flag_off(self):
        # Category normalization is unconditional: it runs at the merge
        # seam regardless of whether detect_show_segments is on, because
        # intro/outro/recap are in SEGMENT_CATEGORIES either way.
        det = AdDetector()
        raw_ads = parse_ads_from_response(
            self._mock_response('recap'), slug='feed-a', episode_id='ep1')
        merged = det._merge_detection_results(raw_ads)
        assert merged[0]['category'] == 'recap'


class TestNormalizationIndependentOfFlag:
    def test_unknown_category_normalizes_to_sponsor(self):
        assert normalize_segment_category('made_up_value') == 'sponsor'
        assert normalize_segment_category(None) == 'sponsor'

    def test_known_categories_pass_through_unnormalized(self):
        for cat in SEGMENT_CATEGORIES:
            assert normalize_segment_category(cat) == cat

    def test_merge_seam_normalizes_out_of_enum_value_regardless_of_flag(self):
        det = AdDetector()
        ads = [{'start': 0.0, 'end': 10.0, 'confidence': 0.9,
                'reason': 'test', 'category': 'not_a_real_category'}]
        merged = det._merge_detection_results(ads)
        assert merged[0]['category'] == 'sponsor'
