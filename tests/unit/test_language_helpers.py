"""Tests for the shared language validation regex and per-feed override lookup.

LANGUAGE_CODE_RE must reject region/script subtags (e.g. 'pt-br', 'zh-hans')
because faster-whisper raises a fatal ValueError on them, which would make a
feed silently unprocessable. get_feed_language_override centralizes the
per-feed lookup so the transcription and pattern-stamping paths can't drift.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))

from database import Database
from utils.language import LANGUAGE_CODE_RE, get_feed_language_override


@pytest.fixture
def db(tmp_path):
    Database._instance = None
    if hasattr(Database, '_initialized'):
        Database._initialized = False
    inst = Database(data_dir=str(tmp_path))
    yield inst
    Database._instance = None


@pytest.mark.parametrize('code', ['en', 'de', 'pt', 'fi', 'zh', 'yue', 'haw'])
def test_regex_accepts_bare_codes(code):
    assert LANGUAGE_CODE_RE.match(code)


@pytest.mark.parametrize('code', ['pt-br', 'zh-hans', 'en-us', 'english', 'e', ''])
def test_regex_rejects_subtags_and_junk(code):
    assert LANGUAGE_CODE_RE.match(code) is None


def test_override_lookup_returns_value(db):
    db.create_podcast('show-a', 'https://example.com/feed.xml', title='Show A')
    db.update_podcast('show-a', language_override='de')
    assert get_feed_language_override(db, 'show-a') == 'de'


def test_override_lookup_none_without_override(db):
    db.create_podcast('show-b', 'https://example.com/feed.xml', title='Show B')
    assert get_feed_language_override(db, 'show-b') is None


def test_override_lookup_safe_for_unknown_and_missing(db):
    assert get_feed_language_override(db, 'does-not-exist') is None
    assert get_feed_language_override(db, None) is None
    assert get_feed_language_override(None, 'show-a') is None
