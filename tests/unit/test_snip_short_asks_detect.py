"""snip_short_asks: detection prompt, schema, parser, window stamp and reviewer prompts."""
import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from tests.app_bootstrap import bootstrap

bootstrap('snip_short_asks_detect_test_')

from ad_detector import AdDetector
from ad_detector.prompts import (
    AD_QUOTE_ANCHOR_SECTION, SEGMENT_ID_SYSTEM_SECTION, ad_detection_json_schema,
    parse_ads_from_response, resolve_segment_id_ads,
)
from ad_reviewer import AdReviewer
from utils.constants import (
    SHORT_ASKS_PROMPT_SECTION, SHORT_ASKS_REVIEW_SECTION, SHOW_SEGMENTS_PROMPT_SECTION,
)


class _Db:
    def __init__(self, short_asks=False, show_segments=False, system_prompt=None):
        self.short_asks = short_asks
        self.show_segments = show_segments
        self.system_prompt = system_prompt

    def get_setting(self, key):
        return self.system_prompt if key == 'system_prompt' else None

    def resolve_feed_bool(self, slug, column, setting_key, podcast=None):
        assert column == setting_key
        return {'snip_short_asks': self.short_asks,
                'detect_show_segments': self.show_segments}[column]


def _detector(**kw):
    det = AdDetector()
    det.db = _Db(**kw)
    det.sponsor_service = None
    return det


class TestSystemPrompt:
    def test_off_is_the_upstream_composition(self):
        det = _detector()
        expected = f"{det.get_system_prompt()}{AD_QUOTE_ANCHOR_SECTION}"
        assert det._build_detection_system_prompt('feed') == expected
        assert SHORT_ASKS_PROMPT_SECTION not in expected

    def test_off_with_show_segments_and_ids_is_unchanged(self):
        det = _detector(show_segments=True)
        expected = (f"{det.get_system_prompt()}\n\n{SHOW_SEGMENTS_PROMPT_SECTION}"
                    f"{SEGMENT_ID_SYSTEM_SECTION}{AD_QUOTE_ANCHOR_SECTION}")
        assert det._build_detection_system_prompt('feed', 'segment_ids') == expected

    def test_on_appends_after_show_segments(self):
        det = _detector(short_asks=True, show_segments=True)
        prompt = det._build_detection_system_prompt('feed')
        assert prompt.index(SHOW_SEGMENTS_PROMPT_SECTION) < prompt.index(SHORT_ASKS_PROMPT_SECTION)
        assert prompt.endswith(AD_QUOTE_ANCHOR_SECTION)

    def test_on_applies_over_a_custom_system_prompt(self):
        det = _detector(short_asks=True, system_prompt='My own detection prompt.')
        prompt = det._build_detection_system_prompt('feed')
        assert prompt.startswith('My own detection prompt.')
        assert SHORT_ASKS_PROMPT_SECTION in prompt

    def test_db_without_the_resolver_is_off(self):
        det = AdDetector()
        det.db = SimpleNamespace(get_setting=lambda key: None)
        assert SHORT_ASKS_PROMPT_SECTION not in det._build_detection_system_prompt('feed')


class TestSchema:
    def test_default_schema_has_no_short_ask(self):
        for mode in ('timestamps', 'segment_ids'):
            props = ad_detection_json_schema(mode)['properties']['ads']['items']['properties']
            assert 'short_ask' not in props

    def test_opted_in_schema_adds_an_optional_boolean(self):
        for mode in ('timestamps', 'segment_ids'):
            item = ad_detection_json_schema(mode, short_ask=True)['properties']['ads']['items']
            assert item['properties']['short_ask'] == {'type': 'boolean'}
            assert 'short_ask' not in item['required']
        assert 'short_ask' not in ad_detection_json_schema('timestamps')['properties'][
            'ads']['items']['properties']


class TestParser:
    @staticmethod
    def _parse(flag):
        item = {'start': 10.0, 'end': 16.0, 'category': 'interaction', 'confidence': 0.9,
                'reason': 'Host asks listeners to subscribe', 'end_text': 'subscribe.'}
        if flag is not None:
            item['short_ask'] = flag
        return parse_ads_from_response(json.dumps([item]), 'feed', 'ep')

    def test_literal_true_is_copied(self):
        assert self._parse(True)[0]['short_ask'] is True

    @pytest.mark.parametrize('flag', [False, 'true', 1, None])
    def test_anything_else_is_not(self, flag):
        assert 'short_ask' not in self._parse(flag)[0]

    def test_id_mode_copies_the_flag(self):
        segs = [{'sid': 1, 'start': 10.0, 'end': 13.0, 'text': 'If you like the show'},
                {'sid': 2, 'start': 13.0, 'end': 16.0, 'text': 'please subscribe.'}]
        ad = {'start_id': 1, 'end_id': 2, 'category': 'interaction', 'confidence': 0.9,
              'reason': 'Host asks listeners to subscribe', 'short_ask': True}
        assert resolve_segment_id_ads([ad], segs, 'feed', 'ep')[0]['short_ask'] is True


def _window(system_prompt, ads):
    segs = [{'start': 0.0, 'end': 100.0, 'text': 'Story.'}]
    detector = AdDetector(api_key='test-key')
    response = SimpleNamespace(content=json.dumps(ads), usage={})
    with patch.object(detector, '_call_llm_for_window', return_value=(response, None)):
        return detector._process_single_window(
            window_idx=0, window={'start': 0.0, 'end': 100.0, 'segments': segs},
            total_windows=1, model='m', system_prompt=system_prompt,
            description_section='', podcast_name='P', episode_title='E',
            audio_enforcer=None, audio_analysis=None, llm_timeout=30, max_retries=1,
            slug='feed', episode_id='ep', pass_name='pass1',
            window_label_prefix='Window', validate_timestamps=False).ads


def _ask(**over):
    ad = {'start': 40.0, 'end': 46.0, 'category': 'interaction', 'confidence': 0.9,
          'reason': 'Host asks listeners to subscribe', 'end_text': 'subscribe.',
          'short_ask': True}
    ad.update(over)
    return ad


class TestWindowStamp:
    def test_on_keeps_flag_and_stamps_the_llm_edges(self):
        ad = _window(f"x\n{SHORT_ASKS_PROMPT_SECTION}", [_ask()])[0]
        assert ad['short_ask'] is True
        assert ad['short_ask_origin'] == [40.0, 46.0]

    def test_off_drops_the_flag(self):
        ad = _window('x', [_ask()])[0]
        assert 'short_ask' not in ad and 'short_ask_origin' not in ad

    def test_category_outside_the_set_drops_the_flag(self):
        ad = _window(SHORT_ASKS_PROMPT_SECTION, [_ask(category='cross_promo')])[0]
        assert 'short_ask' not in ad

    def test_longer_than_forty_seconds_drops_the_flag(self):
        ad = _window(SHORT_ASKS_PROMPT_SECTION, [_ask(start=10.0, end=60.0)])[0]
        assert 'short_ask' not in ad


class TestReviewPrompts:
    @staticmethod
    def _reviewer():
        db = MagicMock()
        db.get_setting.return_value = None
        return AdReviewer(db=db, llm_client=MagicMock())

    def test_off_renders_identically(self):
        r = self._reviewer()
        assert r._render_review_prompt(60, '') == r._render_review_prompt(60, '', short_asks=False)
        assert r._render_resurrect_prompt('') == r._render_resurrect_prompt('', short_asks=False)
        assert SHORT_ASKS_REVIEW_SECTION not in r._render_review_prompt(60, '')
        assert SHORT_ASKS_REVIEW_SECTION not in r._render_resurrect_prompt('')

    def test_on_appends_the_section_to_both(self):
        r = self._reviewer()
        review = r._render_review_prompt(60, '', short_asks=True)
        resurrect = r._render_resurrect_prompt('', short_asks=True)
        assert review == f"{r._render_review_prompt(60, '')}\n\n{SHORT_ASKS_REVIEW_SECTION}"
        assert resurrect == f"{r._render_resurrect_prompt('')}\n\n{SHORT_ASKS_REVIEW_SECTION}"

    def test_on_goes_before_the_operator_override(self):
        r = self._reviewer()
        r.db.get_setting.side_effect = lambda k: 'OVERRIDE TEXT' if k == 'review_prompt_override' else None
        prompt = r._render_review_prompt(60, '', short_asks=True)
        assert prompt.index(SHORT_ASKS_REVIEW_SECTION) < prompt.index('OVERRIDE TEXT')


class TestReviewMeta:
    @staticmethod
    def _meta(resolver):
        from main_app.pipeline import review
        def resolve_feed_bool(slug, column, setting_key, podcast=None):
            assert column == setting_key == 'snip_short_asks'
            return resolver(slug)
        with patch.object(review, 'db', SimpleNamespace(resolve_feed_bool=resolve_feed_bool)):
            return review._build_episode_meta(
                'feed', 'ep', 1, 'P', 'E', '', '')

    def test_meta_carries_the_resolved_flag(self):
        assert self._meta(lambda slug: True)['snip_short_asks'] is True
        assert self._meta(lambda slug: False)['snip_short_asks'] is False

    def test_a_failing_resolver_is_off(self):
        def boom(slug):
            raise RuntimeError('db down')
        assert self._meta(boom)['snip_short_asks'] is False


class TestPodcastWants:
    """AdDetector._podcast_wants: the one per-feed bool lookup, off when it cannot be read."""

    def test_reads_the_resolver(self):
        det = _detector(short_asks=True)
        assert det._podcast_wants('feed', 'snip_short_asks', 'snip_short_asks') is True
        assert _detector()._podcast_wants('feed', 'snip_short_asks', 'snip_short_asks') is False

    def test_empty_slug_or_no_db_is_off(self):
        det = _detector(short_asks=True)
        assert det._podcast_wants('', 'snip_short_asks', 'snip_short_asks') is False
        det.db = None
        assert det._podcast_wants('feed', 'snip_short_asks', 'snip_short_asks') is False

    def test_a_failing_resolver_is_off(self):
        det = _detector()
        det.db.resolve_feed_bool = MagicMock(side_effect=RuntimeError('db down'))
        assert det._podcast_wants('feed', 'snip_short_asks', 'snip_short_asks') is False
