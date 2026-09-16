"""Per-stage reasoning effort for review calls."""
from dataclasses import dataclass
from unittest.mock import MagicMock, patch

import pytest

from tests.app_bootstrap import bootstrap

bootstrap('reviewer_reasoning_test_')

import config
from ad_reviewer import AdReviewer
from config import PROVIDER_ANTHROPIC, PROVIDER_OPENAI_COMPATIBLE
from database import Database
from llm_client import _clear_provider_cache


@pytest.fixture
def anthropic_route(preserve_setting):
    """Both reasoning keys and the provider the route falls back to live in the
    shared session settings, so another module's value would decide these
    assertions."""
    for key in ('llm_provider', 'reviewer_reasoning_level',
                'reviewer_reasoning_budget'):
        preserve_setting(key)
    db = Database()
    db.clear_setting('reviewer_reasoning_level')
    db.clear_setting('reviewer_reasoning_budget')
    db.set_setting('llm_provider', PROVIDER_ANTHROPIC)
    _clear_provider_cache()
    yield db
    _clear_provider_cache()


@dataclass
class _LLMResp:
    content: str
    model: str = 'test-model'


def test_reviewer_reasoning_is_unset_by_default(anthropic_route):
    """An effort the operator never asked for costs a rejected request and a
    retry per reviewed ad on a model that takes no reasoning parameter."""
    assert config.get_stage_tunable('reviewer_reasoning_level') is None
    # Anthropic reads the budget instead, and None there means no extended
    # thinking at all: already lower than the 1024-token floor.
    assert config.get_stage_tunable('reviewer_reasoning_budget') is None


def test_the_review_call_passes_the_effort_resolved_for_its_route(anthropic_route):
    """End to end: the reviewer's own route decides which key is read. This
    route is Anthropic, so the budget reaches the call and the level set
    alongside it does not."""
    db = MagicMock()
    db.get_setting.side_effect = {
        'review_prompt': 'review', 'resurrect_prompt': 'resurrect',
    }.get
    db.get_connection.return_value = MagicMock()
    reviewer = AdReviewer(db=db, llm_client=MagicMock(), sponsor_service=None)
    settings = {'reviewer_reasoning_budget': '2048',
                'reviewer_reasoning_level': 'high'}

    with patch('ad_reviewer.resolve_stage_tunables',
               side_effect=lambda prefix, provider=None: config.resolve_stage_tunables(
                   prefix, settings=settings, provider=provider)), \
            patch('ad_reviewer.call_llm_for_window',
                  return_value=(_LLMResp('[]'), None)) as call:
        reviewer.review(
            accepted_ads=[{'start': 120.0, 'end': 180.0, 'confidence': 0.9}],
            resurrection_eligible=[],
            segments=[{'start': 120.0, 'end': 180.0, 'text': 'sponsor pitch'}],
            episode_meta={'slug': 'test-pod', 'episode_id': 'ep1',
                          'podcast_id': 'p1', 'podcast_name': 'Test Podcast',
                          'episode_title': 'Test Episode'},
            pass_num=1, pass_model='claude-test',
        )

    assert call.call_args.kwargs['reasoning_effort'] == 2048


class TestTheRouteDecidesWhichReasoningKeyIsRead:
    """The global provider is not the stage's provider once a stage is routed
    to its own slot, and the two keys are not interchangeable: a token budget
    sent as an effort level is a rejected request."""

    SETTINGS = {
        'reviewer_reasoning_level': 'high',
        'reviewer_reasoning_budget': '2048',
    }

    def test_a_stage_on_anthropic_reads_the_budget(self):
        _tokens, _temp, reasoning = config.resolve_stage_tunables(
            'reviewer', settings=self.SETTINGS, provider=PROVIDER_ANTHROPIC)

        assert reasoning == 2048

    def test_a_stage_routed_off_anthropic_reads_the_level(self):
        _tokens, _temp, reasoning = config.resolve_stage_tunables(
            'reviewer', settings=self.SETTINGS,
            provider=PROVIDER_OPENAI_COMPATIBLE)

        assert reasoning == 'high'

    def test_no_route_falls_back_to_the_global_provider(self):
        with patch('llm_client.get_effective_provider',
                   return_value=PROVIDER_ANTHROPIC):
            _tokens, _temp, reasoning = config.resolve_stage_tunables(
                'reviewer', settings=self.SETTINGS)

        assert reasoning == 2048
