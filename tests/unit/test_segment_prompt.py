"""Tests for segment category emission in the detection prompt (issue #565).

SHOW_SEGMENTS_PROMPT_SECTION is opt-in per podcast (detect_show_segments),
but merge-seam category normalization runs unconditionally.
"""
import json
import logging
from unittest.mock import patch, MagicMock

from ad_detector import AdDetector, WindowResult
from config import SEGMENT_CATEGORIES, DEFAULT_SEGMENT_ACTION, normalize_segment_category
from database import DEFAULT_VERIFICATION_PROMPT
from utils.constants import DEFAULT_SYSTEM_PROMPT, SHOW_SEGMENTS_PROMPT_SECTION
from ad_detector.prompts import (
    parse_ads_from_response, parse_category_repair_response,
    format_category_repair_prompt,
)
from llm_client import LLMClient, LLMResponse


class _FakeDb:
    """Minimal db stub: get_setting for no-override, get_podcast_by_slug
    for the detect_show_segments flag, resolve_segment_actions for the
    category-miss warning gate."""

    def __init__(self, detect_show_segments=False, system_prompt=None,
                 segment_actions=None):
        self._detect_show_segments = detect_show_segments
        self._system_prompt = system_prompt
        self._segment_actions = segment_actions

    def get_setting(self, key):
        if key == 'system_prompt':
            return self._system_prompt
        return None

    def get_podcast_by_slug(self, slug):
        if slug is None:
            return None
        return {'slug': slug, 'detect_show_segments': self._detect_show_segments}

    def resolve_detect_show_segments(self, slug):
        return self._detect_show_segments

    def resolve_segment_actions(self, slug):
        if self._segment_actions is not None:
            return self._segment_actions
        return {cat: DEFAULT_SEGMENT_ACTION for cat in SEGMENT_CATEGORIES}


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

    def test_category_required_wording_adjacent_to_schema_line(self):
        # Must sit next to the schema line, not only in the CATEGORY block
        # further down: that let a model skip the field while still
        # following the CATEGORY block's enum rules for everything else.
        schema_idx = DEFAULT_SYSTEM_PROMPT.index('Each ad segment:')
        required_idx = DEFAULT_SYSTEM_PROMPT.index(
            'is REQUIRED on every ad object')
        assert 0 < required_idx - schema_idx < 400
        assert 'is invalid' in DEFAULT_SYSTEM_PROMPT

    def test_verification_prompt_requires_category(self):
        schema_idx = DEFAULT_VERIFICATION_PROMPT.index('Each ad segment:')
        required_idx = DEFAULT_VERIFICATION_PROMPT.index(
            '"category" is REQUIRED on every object')
        assert 0 < required_idx - schema_idx < 500
        for category in SEGMENT_CATEGORIES:
            assert category in DEFAULT_VERIFICATION_PROMPT

    def test_non_sponsor_worked_example_present(self):
        # Both worked examples used to be "sponsor"; the model needs to see
        # a non-sponsor category filled in at least once.
        examples_section = DEFAULT_SYSTEM_PROMPT[
            DEFAULT_SYSTEM_PROMPT.index('EXAMPLE:'):]
        non_sponsor_cats = ('cross_promo', 'self_promo', 'interaction')
        assert any(f'"category": "{cat}"' in examples_section
                   for cat in non_sponsor_cats)


def test_verification_wires_category_actions_into_repair_and_dedup():
    action_map = dict(_all_remove_map(), self_promo='keep', outro='beep')
    detector = AdDetector(api_key='test-key')
    detector.db = _FakeDb(segment_actions=action_map)
    run_pass = MagicMock(return_value=([], [], 0, None, 0, 0, 0, 0, 0))

    with patch.object(detector, 'initialize_client'), \
         patch.object(detector, 'get_verification_prompt', return_value='verification'), \
         patch.object(detector, 'get_verification_model', return_value='model'), \
         patch.object(detector, '_build_known_pattern_hint', return_value=''), \
         patch.object(detector, '_run_detection_pass', run_pass):
        result = detector.run_verification_detection(
            _WARNING_SEGMENTS, podcast_name='Test', episode_title='Ep',
            slug='daily-tech-news-show', episode_id='ep1')

    assert result['status'] == 'success'
    kwargs = run_pass.call_args.kwargs
    assert kwargs['action_map'] == action_map
    assert kwargs['category_repair_enabled'] is True


class TestShowSegmentsSection:
    def test_section_defines_intro_outro_recap(self):
        for cat in ('intro', 'outro', 'recap'):
            assert cat in SHOW_SEGMENTS_PROMPT_SECTION

    def test_section_flags_cold_open_as_content(self):
        assert 'cold open' in SHOW_SEGMENTS_PROMPT_SECTION.lower()

    def test_section_states_category_is_required(self):
        # A detect_show_segments=true feed still got category-less LLM
        # responses, so the section repeats the requirement itself rather
        # than relying on the base prompt's block.
        assert 'REQUIRED' in SHOW_SEGMENTS_PROMPT_SECTION
        assert '"category"' in SHOW_SEGMENTS_PROMPT_SECTION

    def test_section_has_its_own_worked_example_with_category(self):
        assert ('"category": "intro"' in SHOW_SEGMENTS_PROMPT_SECTION
                or '"category": "outro"' in SHOW_SEGMENTS_PROMPT_SECTION)

    def test_section_unsure_rule_still_present(self):
        assert 'do not flag it' in SHOW_SEGMENTS_PROMPT_SECTION


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
        # Section is appended after override resolution, so an opted-in
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
        # Normalization runs at the merge seam regardless of the flag,
        # since intro/outro/recap are in SEGMENT_CATEGORIES either way.
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

    def test_merge_seam_drops_an_out_of_enum_value_regardless_of_flag(self):
        """Recording it as sponsor would make a real sponsor read
        indistinguishable from a value nothing recognized."""
        det = AdDetector()
        ads = [{'start': 0.0, 'end': 10.0, 'confidence': 0.9,
                'reason': 'test', 'category': 'not_a_real_category'}]
        merged = det._merge_detection_results(ads)
        assert merged[0].get('category') is None
        # Cutting is unchanged: action resolution still reads unset as sponsor.
        assert normalize_segment_category(merged[0].get('category')) == 'sponsor'


_WARNING_SEGMENTS = [
    {'start': 0.0, 'end': 500.0, 'text': 'first half of the episode'},
    {'start': 500.0, 'end': 1000.0, 'text': 'second half of the episode'},
]


def _all_remove_map():
    return {cat: DEFAULT_SEGMENT_ACTION for cat in SEGMENT_CATEGORIES}


def _fake_ad(start, end, category=None):
    ad = {'start': start, 'end': end, 'confidence': 0.9, 'reason': 'test ad',
          'end_text': 'x'}
    if category is not None:
        ad['category'] = category
    return ad


def _run_detect_ads(*, detect_show_segments, segment_actions, ads,
                     repair_side_effect=None):
    """Drive detect_ads() with one canned window of LLM ads, bypassing the
    real LLM call and the real category repair pass. Default repair_side_effect
    leaves ads untouched; tests exercising repair pass their own.
    """
    detector = AdDetector(api_key='test-key')
    detector.db = _FakeDb(detect_show_segments=detect_show_segments,
                          segment_actions=segment_actions)
    window_result = WindowResult(
        window_idx=0, window_start=0.0, window_end=1000.0,
        ads=ads, raw_response='raw', failed=False, last_error=None,
        transcript_excerpt='[0.0s - 1000.0s] some transcript text')
    run_windows = MagicMock(return_value=[window_result])
    repair_mock = MagicMock(
        side_effect=repair_side_effect if repair_side_effect is not None
        else lambda **kw: 0)
    with patch.object(detector, 'initialize_client'), \
         patch.object(detector, '_detect_foreign_language_ads', return_value=[]), \
         patch.object(detector, 'get_system_prompt', return_value='system'), \
         patch.object(detector, 'get_model', return_value='model'), \
         patch.object(detector, '_build_known_pattern_hint', return_value=''), \
         patch.object(detector, '_run_windows', run_windows), \
         patch.object(detector, '_repair_window_categories', repair_mock), \
         patch('ad_detector._resolve_parallel_windows', return_value=1), \
         patch('ad_detector.get_llm_timeout', return_value=60), \
         patch('ad_detector.get_llm_max_retries', return_value=1):
        result = detector.detect_ads(
            _WARNING_SEGMENTS, podcast_name='Test', episode_title='Ep',
            slug='daily-tech-news-show', episode_id='ep1')
    assert result['status'] == 'success'
    return result, repair_mock


def _category_miss_warnings(caplog):
    return [r for r in caplog.records if 'returned no category' in r.message]


class TestCategoryMissWarning:
    """Detector-side surfacing (companion to the prompt hardening above):
    when the feed's resolved settings actually care about category and the
    LLM still comes back category-less, log exactly one warning per run."""

    def test_warns_once_on_feed_with_keep_actions(self, caplog):
        action_map = dict(_all_remove_map(), self_promo='keep')
        ads = [
            _fake_ad(10.0, 40.0),
            _fake_ad(200.0, 240.0),
            _fake_ad(500.0, 540.0, category='sponsor'),
        ]
        with caplog.at_level(logging.WARNING, logger='podcast.claude'):
            _run_detect_ads(detect_show_segments=False,
                            segment_actions=action_map, ads=ads)

        warnings = _category_miss_warnings(caplog)
        assert len(warnings) == 1
        assert 'daily-tech-news-show' in warnings[0].message
        assert '2 of 3' in warnings[0].message

    def test_warns_once_when_show_segments_enabled(self, caplog):
        ads = [_fake_ad(10.0, 40.0)]
        with caplog.at_level(logging.WARNING, logger='podcast.claude'):
            _run_detect_ads(detect_show_segments=True,
                            segment_actions=_all_remove_map(), ads=ads)

        warnings = _category_miss_warnings(caplog)
        assert len(warnings) == 1
        assert '1 of 1' in warnings[0].message

    def test_no_warning_on_default_all_remove_feed_with_toggle_off(self, caplog):
        # Every category resolves to the same action here, so there's
        # nothing for a category miss to affect.
        ads = [_fake_ad(10.0, 40.0), _fake_ad(200.0, 240.0)]
        with caplog.at_level(logging.WARNING, logger='podcast.claude'):
            _run_detect_ads(detect_show_segments=False,
                            segment_actions=_all_remove_map(), ads=ads)

        assert _category_miss_warnings(caplog) == []

    def test_no_warning_when_all_markers_have_category(self, caplog):
        action_map = dict(_all_remove_map(), self_promo='keep')
        ads = [
            _fake_ad(10.0, 40.0, category='sponsor'),
            _fake_ad(200.0, 240.0, category='self_promo'),
        ]
        with caplog.at_level(logging.WARNING, logger='podcast.claude'):
            _run_detect_ads(detect_show_segments=False,
                            segment_actions=action_map, ads=ads)

        assert _category_miss_warnings(caplog) == []

    def test_warning_mentions_repair_when_it_resolved_some(self, caplog):
        action_map = dict(_all_remove_map(), self_promo='keep')
        ads = [
            _fake_ad(10.0, 40.0),
            _fake_ad(200.0, 240.0),
        ]

        def repair(*, ads, **kwargs):
            ads[0]['category'] = 'self_promo'
            return 1

        with caplog.at_level(logging.WARNING, logger='podcast.claude'):
            _run_detect_ads(detect_show_segments=False, segment_actions=action_map,
                            ads=ads, repair_side_effect=repair)

        warnings = _category_miss_warnings(caplog)
        assert len(warnings) == 1
        assert '1 of 2' in warnings[0].message
        assert 'repair pass resolved 1 of 2' in warnings[0].message

    def test_no_warning_when_repair_resolves_everything(self, caplog):
        action_map = dict(_all_remove_map(), self_promo='keep')
        ads = [_fake_ad(10.0, 40.0)]

        def repair(*, ads, **kwargs):
            ads[0]['category'] = 'self_promo'
            return 1

        with caplog.at_level(logging.WARNING, logger='podcast.claude'):
            _run_detect_ads(detect_show_segments=False, segment_actions=action_map,
                            ads=ads, repair_side_effect=repair)

        assert _category_miss_warnings(caplog) == []


class TestCategoryRepairPromptAndParsing:
    """Pure-function tests for the repair call's prompt/response helpers."""

    def test_format_prompt_includes_transcript_and_missing_indices(self):
        missing = [(0, _fake_ad(10.0, 40.0)), (2, _fake_ad(90.0, 95.0))]
        prompt = format_category_repair_prompt('[10.0s] hello world', missing)
        assert '[10.0s] hello world' in prompt
        assert '"index": 0' in prompt
        assert '"index": 2' in prompt

    def test_parse_bare_array_shape(self):
        resolved = parse_category_repair_response(
            json.dumps([{"index": 0, "category": "sponsor"},
                        {"index": 1, "category": "intro"}]))
        assert resolved == {0: 'sponsor', 1: 'intro'}

    def test_parse_wrapped_object_shape_from_tool_use_path(self):
        resolved = parse_category_repair_response(
            json.dumps({"categories": [{"index": 0, "category": "outro"}]}))
        assert resolved == {0: 'outro'}

    def test_parse_rejects_unknown_category(self):
        resolved = parse_category_repair_response(
            json.dumps([{"index": 0, "category": "advertisement"}]))
        assert resolved == {}

    def test_parse_rejects_bool_index(self):
        resolved = parse_category_repair_response(
            json.dumps([{"index": True, "category": "sponsor"}]))
        assert resolved == {}

    def test_parse_malformed_json_returns_empty_dict(self):
        assert parse_category_repair_response('not json at all') == {}

    def test_parse_non_list_non_dict_returns_empty_dict(self):
        assert parse_category_repair_response('42') == {}


class _FakeCategoryRepairClient(LLMClient):
    """Real LLMClient subclass (not a bare MagicMock), so the same
    set_usage_callback / _notify_usage cost-tracking path every other
    LLM call uses actually runs here too."""

    def __init__(self, content):
        super().__init__()
        self.content = content
        self.calls = []

    def messages_create(self, **kwargs):
        self.calls.append(kwargs)
        response = LLMResponse(
            content=self.content,
            model=kwargs.get('model', 'fake-model'),
            usage={'input_tokens': 42, 'output_tokens': 7},
        )
        self._notify_usage(response)
        return response

    def list_models(self, bypass_cache=False):
        return []

    def get_provider_name(self):
        return 'fake'


def _detect_ads_with_fake_client(*, detect_show_segments, segment_actions,
                                  ads, fake_client):
    """Same as _run_detect_ads, but wires a real (fake) LLMClient instead
    of mocking _repair_window_categories, so the repair path runs end to end."""
    detector = AdDetector(api_key='test-key')
    detector.db = _FakeDb(detect_show_segments=detect_show_segments,
                          segment_actions=segment_actions)
    detector._llm_client = fake_client
    window_result = WindowResult(
        window_idx=0, window_start=0.0, window_end=1000.0,
        ads=ads, raw_response='raw', failed=False, last_error=None,
        transcript_excerpt='[0.0s - 1000.0s] some transcript text')
    run_windows = MagicMock(return_value=[window_result])
    with patch.object(detector, 'initialize_client'), \
         patch.object(detector, '_detect_foreign_language_ads', return_value=[]), \
         patch.object(detector, 'get_system_prompt', return_value='system'), \
         patch.object(detector, 'get_model', return_value='model'), \
         patch.object(detector, '_build_known_pattern_hint', return_value=''), \
         patch.object(detector, '_run_windows', run_windows), \
         patch('ad_detector._resolve_parallel_windows', return_value=1), \
         patch('ad_detector.get_llm_timeout', return_value=60), \
         patch('ad_detector.get_llm_max_retries', return_value=1):
        result = detector.detect_ads(
            _WARNING_SEGMENTS, podcast_name='Test', episode_title='Ep',
            slug='daily-tech-news-show', episode_id='ep1')
    assert result['status'] == 'success'
    return result


class TestCategoryRepairEndToEnd:
    """Drives the real _repair_window_categories code path (unmocked) against
    a fake but real LLMClient subclass: gating, index-mapping, failure
    handling, and cost accounting are proven against production code."""

    def test_client_never_called_for_default_feed(self):
        # An all-remove, show-segments-off feed makes zero extra LLM calls,
        # even with every ad missing a category: repair must never fire.
        fake = _FakeCategoryRepairClient(content='[]')
        ads = [_fake_ad(10.0, 40.0), _fake_ad(200.0, 240.0)]
        _detect_ads_with_fake_client(
            detect_show_segments=False, segment_actions=_all_remove_map(),
            ads=ads, fake_client=fake)
        assert fake.calls == []

    def test_no_client_call_when_configured_feed_has_no_missing_categories(self):
        action_map = dict(_all_remove_map(), self_promo='keep')
        fake = _FakeCategoryRepairClient(content='[]')
        ads = [_fake_ad(10.0, 40.0, category='sponsor'),
               _fake_ad(200.0, 240.0, category='self_promo')]
        _detect_ads_with_fake_client(
            detect_show_segments=False, segment_actions=action_map,
            ads=ads, fake_client=fake)
        assert fake.calls == []

    def test_client_called_once_and_categories_applied_by_index(self):
        action_map = dict(_all_remove_map(), self_promo='keep')
        ads = [
            _fake_ad(10.0, 40.0),                        # index 0
            _fake_ad(200.0, 240.0, category='sponsor'),  # index 1
            _fake_ad(500.0, 540.0),                       # index 2
        ]
        response_json = json.dumps([
            {"index": 0, "category": "self_promo"},
            {"index": 2, "category": "interaction"},
        ])
        fake = _FakeCategoryRepairClient(content=response_json)
        usage_seen = {}
        fake.set_usage_callback(lambda model, usage: usage_seen.update(usage))

        _detect_ads_with_fake_client(
            detect_show_segments=False, segment_actions=action_map,
            ads=ads, fake_client=fake)

        assert len(fake.calls) == 1
        assert ads[0]['category'] == 'self_promo'
        assert ads[1]['category'] == 'sponsor'
        assert ads[2]['category'] == 'interaction'
        # Repair goes through the same messages_create -> _notify_usage ->
        # usage_callback chain as every other LLM call.
        assert usage_seen == {'input_tokens': 42, 'output_tokens': 7}

    def test_malformed_response_leaves_sponsor_default_and_does_not_raise(self):
        action_map = dict(_all_remove_map(), self_promo='keep')
        ads = [_fake_ad(10.0, 40.0), _fake_ad(200.0, 240.0)]
        fake = _FakeCategoryRepairClient(content='not valid json at all')

        result = _detect_ads_with_fake_client(
            detect_show_segments=False, segment_actions=action_map,
            ads=ads, fake_client=fake)

        assert result['status'] == 'success'
        assert len(fake.calls) == 1
        assert 'category' not in ads[0]
        assert 'category' not in ads[1]

    def test_partial_response_leaves_unresolved_ads_missing(self):
        action_map = dict(_all_remove_map(), self_promo='keep')
        ads = [_fake_ad(10.0, 40.0), _fake_ad(200.0, 240.0)]
        # Only index 0 is answered; index 1 is silently dropped by the model.
        fake = _FakeCategoryRepairClient(
            content=json.dumps([{"index": 0, "category": "self_promo"}]))

        _detect_ads_with_fake_client(
            detect_show_segments=False, segment_actions=action_map,
            ads=ads, fake_client=fake)

        assert ads[0]['category'] == 'self_promo'
        assert 'category' not in ads[1]

    def test_direct_call_returns_zero_and_makes_no_request_when_nothing_missing(self):
        # Short-circuits when every ad already has a category, independent
        # of the _run_detection_pass gate.
        detector = AdDetector(api_key='test-key')
        detector.db = _FakeDb(detect_show_segments=False,
                              segment_actions=_all_remove_map())
        fake = _FakeCategoryRepairClient(content='[]')
        detector._llm_client = fake
        ads = [_fake_ad(10.0, 40.0, category='sponsor')]

        repaired = detector._repair_window_categories(
            ads=ads, transcript_excerpt='', model='model',
            llm_timeout=60, max_retries=1, slug='s', episode_id='e',
            window_label='Window 1',
        )

        assert repaired == 0
        assert fake.calls == []


class TestCategoryRepairStructuredOutputSelection:
    """response_format selection: json_schema only when llm_capabilities says
    the provider proved it (Anthropic via tool-use); json_object otherwise,
    same fallback every other LLM call in this codebase already uses."""

    def _detector_with_fake_client(self, content='[]'):
        detector = AdDetector(api_key='test-key')
        detector.db = _FakeDb(detect_show_segments=False,
                              segment_actions=_all_remove_map())
        fake = _FakeCategoryRepairClient(content=content)
        detector._llm_client = fake
        return detector, fake

    def test_uses_json_schema_when_provider_supports_it(self):
        detector, fake = self._detector_with_fake_client()
        ads = [_fake_ad(10.0, 40.0)]
        with patch('ad_detector.supports_json_schema', return_value=True):
            detector._repair_window_categories(
                ads=ads, transcript_excerpt='', model='model',
                llm_timeout=60, max_retries=1, slug='s', episode_id='e',
                window_label='Window 1',
            )
        assert len(fake.calls) == 1
        assert fake.calls[0]['response_format']['type'] == 'json_schema'

    def test_falls_back_to_json_object_when_unsupported(self):
        detector, fake = self._detector_with_fake_client()
        ads = [_fake_ad(10.0, 40.0)]
        with patch('ad_detector.supports_json_schema', return_value=False):
            detector._repair_window_categories(
                ads=ads, transcript_excerpt='', model='model',
                llm_timeout=60, max_retries=1, slug='s', episode_id='e',
                window_label='Window 1',
            )
        assert len(fake.calls) == 1
        assert fake.calls[0]['response_format'] == {'type': 'json_object'}
