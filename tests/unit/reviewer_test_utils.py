"""Shared reviewer builders and LLM response stubs; import after bootstrap."""
from dataclasses import dataclass
from unittest.mock import MagicMock

from ad_reviewer import AdReviewer

REVIEW_PROMPTS = {'review_prompt': 'review', 'resurrect_prompt': 'resurrect'}


@dataclass
class _LLMResp:
    """Matches the LLMResponse dataclass shape (content is a string)."""
    content: str
    model: str = 'test-model'


def _resp(body: str) -> _LLMResp:
    return _LLMResp(content=body)


class InconclusiveError(Exception):
    status_code = 422
    body = {'error': {'code': 'jev_review_inconclusive',
                      'reason': 'missing_boundary_coverage',
                      'stage': 'boundary_coverage'}}


def _mock_episode_meta():
    return {
        'podcast_name': 'Test Podcast',
        'episode_title': 'Test Episode',
        'episode_description': 'desc',
        'podcast_description': 'pod desc',
        'slug': 'test-pod',
        'episode_id': 'ep1',
        'podcast_id': 'p1',
    }


def _meta():
    return {'podcast_name': 'Example Podcast', 'episode_title': 'Episode',
            'podcast_description': '', 'episode_description': '',
            'slug': 'example-podcast', 'episode_id': 'episode-1',
            'podcast_id': 1}


def _build_reviewer(db_settings=None, conn=None):
    db_settings = db_settings or {}
    db = MagicMock()
    db.get_setting.side_effect = lambda key: db_settings.get(key)
    db.get_connection.return_value = conn or MagicMock()
    return AdReviewer(db=db, llm_client=MagicMock(), sponsor_service=None)


def _reviewer(settings=None):
    """A reviewer with the default prompts plus any extra settings."""
    return _build_reviewer({**REVIEW_PROMPTS, **(settings or {})})
