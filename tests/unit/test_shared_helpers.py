"""Small shared helpers: LIKE escaping, subscriber token parsing, retention clamp."""
from unittest.mock import MagicMock

import pytest

from config import (
    DOWNLOAD_HISTORY_RETENTION_DAYS_MAX, DOWNLOAD_HISTORY_RETENTION_DAYS_MIN,
    resolve_download_history_retention_days,
)
from database.feed_subscribers import parse_subscriber_token
from utils.text import escape_like


def test_escape_like_neutralises_wildcards_and_backslash():
    assert escape_like(r'50%_off\x') == r'50\%\_off\\x'


def test_parse_subscriber_token_splits_a_valid_token():
    assert parse_subscriber_token('a' * 16 + '.' + 'b' * 64) == ('a' * 16, 'b' * 64)


@pytest.mark.parametrize('token', [None, 'nodot', 'a' * 16 + '.' + 'b' * 63,
                                   'g' * 16 + '.' + 'b' * 64])
def test_parse_subscriber_token_rejects_malformed(token):
    assert parse_subscriber_token(token) is None


@pytest.mark.parametrize('stored, expected', [
    (0, DOWNLOAD_HISTORY_RETENTION_DAYS_MIN),
    (99999, DOWNLOAD_HISTORY_RETENTION_DAYS_MAX),
    (30, 30),
])
def test_retention_days_are_clamped(stored, expected):
    db = MagicMock(get_setting_int=MagicMock(return_value=stored))
    assert resolve_download_history_retention_days(db) == expected
