"""Review and rate links appended to served descriptions."""
import os
from unittest.mock import patch

import pytest

from tests.app_bootstrap import bootstrap

bootstrap('review_links_test_')

from config import resolve_toggle  # noqa: E402
from review_links import append_review_links, format_review_links  # noqa: E402
from utils.episode_paths import episode_ui_url  # noqa: E402


@pytest.fixture
def ui_base():
    with patch.dict(os.environ, {'UI_BASE_URL': 'https://app.example.com'}):
        yield


def test_episode_ui_url_prefers_ui_base_url_and_strips_trailing_slash():
    with patch.dict(os.environ, {'BASE_URL': 'https://feed.example.com/',
                                'UI_BASE_URL': 'https://app.example.com/'}):
        assert episode_ui_url('show', 'ep1') == 'https://app.example.com/ui/feeds/show/episodes/ep1'
    with patch.dict(os.environ, {'BASE_URL': 'https://feed.example.com/pods'}):
        os.environ.pop('UI_BASE_URL', None)
        assert episode_ui_url('show', 'ep1') == 'https://feed.example.com/pods/ui/feeds/show/episodes/ep1'


def test_block_has_review_and_rate_links(ui_base):
    block = format_review_links('show', 'ep1')
    url = 'https://app.example.com/ui/feeds/show/episodes/ep1'
    assert block == (f'<p><a href="{url}">Review in MinusPod</a> | '
                     f'<a href="{url}?rate=5">Rate 5 stars</a></p>')


def test_url_is_html_escaped():
    with patch.dict(os.environ, {'UI_BASE_URL': 'https://app.example.com/a?x=1&y="2"'}):
        block = format_review_links('show', 'ep1')
    assert '&amp;y=&quot;2&quot;' in block
    assert '"2"' not in block


def test_append_joins_with_blank_line(ui_base):
    out = append_review_links('<p>Notes</p>', 'show', 'ep1')
    assert out.startswith('<p>Notes</p>\n\n<p><a href=')
    assert append_review_links(None, 'show', 'ep1').startswith('<p><a href=')


@pytest.mark.parametrize('override,default,expected', [
    ('on', False, True), ('off', True, False), (None, True, True), ('', False, False), ('bogus', True, True),
])
def test_resolve_toggle_override_wins_else_default(override, default, expected):
    assert resolve_toggle(override, default) is expected
