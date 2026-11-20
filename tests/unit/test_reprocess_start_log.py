"""The reprocess start line names the mode once."""
import logging
from unittest.mock import patch

import pytest

from tests.app_bootstrap import bootstrap

bootstrap('reprocess_start_log_test_')
from main_app import app, db  # noqa: E402

EPISODE_ID = 'a1b2c3d4e5f6'


@pytest.fixture
def slug(request):
    name = f"reprocess-log-{request.node.callspec.params['mode']}"
    db.create_podcast(name, 'https://example.com/feed.xml', title='Reprocess Log')
    db.upsert_episode(name, EPISODE_ID, title='Ep', status='processed',
                      original_url='https://example.com/ep.mp3')
    return name


@pytest.mark.parametrize('mode,expected', [
    ('reprocess', 'Starting reprocess (async)'),
    ('full', 'Starting full reprocess (async)'),
    ('llm', 'Starting llm reprocess (async)'),
])
def test_start_line_does_not_repeat_the_word(slug, caplog, mode, expected):
    caplog.set_level(logging.INFO)
    with patch('main_app.processing.start_background_processing', return_value=(True, None)), \
         patch('api.episodes.runs.reset_episode_for_reprocess'), \
         patch('api.episodes.runs.REPROCESS_MODE_NEEDS_TRANSCRIPT', {mode: False}):
        with app.test_client() as c:
            c.post(f'/api/v1/feeds/{slug}/episodes/{EPISODE_ID}/reprocess', json={'mode': mode})
    lines = [r.getMessage() for r in caplog.records if 'reprocess (async)' in r.getMessage()]
    assert lines == [f'[{slug}:{EPISODE_ID}] {expected}']
