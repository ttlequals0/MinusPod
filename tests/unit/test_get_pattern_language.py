"""Tests for utils.language.get_pattern_language resolution order.

Per-feed language_override > global whisper_language; 'auto' collapses to None at either level."""

import os
import pathlib
import sys

import pytest

sys.path.insert(0, os.path.join(pathlib.Path(__file__).parent, '..', '..', 'src'))

from database import Database
from utils.language import get_pattern_language


@pytest.fixture
def db(tmp_path):
    Database._instance = None
    if hasattr(Database, '_initialized'):
        Database._initialized = False
    inst = Database(data_dir=str(tmp_path))
    yield inst
    Database._instance = None


def test_returns_none_when_db_missing():
    assert get_pattern_language(None) is None


def test_falls_back_to_global_setting(db):
    db.set_setting('whisper_language', 'en', is_default=True)
    assert get_pattern_language(db) == 'en'


def test_global_auto_returns_none(db):
    db.set_setting('whisper_language', 'auto', is_default=False)
    assert get_pattern_language(db) is None


def test_slug_override_wins_over_global(db):
    db.set_setting('whisper_language', 'en', is_default=False)
    db.create_podcast('show-a', 'https://example.com/feed.xml', title='Show A')
    db.update_podcast('show-a', language_override='de')
    assert get_pattern_language(db, slug='show-a') == 'de'


def test_slug_override_auto_returns_none_not_global(db):
    db.set_setting('whisper_language', 'en', is_default=False)
    db.create_podcast('show-b', 'https://example.com/feed.xml', title='Show B')
    db.update_podcast('show-b', language_override='auto')
    assert get_pattern_language(db, slug='show-b') is None


def test_empty_slug_override_falls_back_to_global(db):
    db.set_setting('whisper_language', 'fr', is_default=False)
    db.create_podcast('show-c', 'https://example.com/feed.xml', title='Show C')
    # Default after create, no override set
    assert get_pattern_language(db, slug='show-c') == 'fr'


def test_unknown_slug_falls_back_to_global(db):
    db.set_setting('whisper_language', 'en', is_default=False)
    assert get_pattern_language(db, slug='does-not-exist') == 'en'


class _FakeDb:
    """Minimal stand-in: only the two lookups get_pattern_language uses."""

    def __init__(self, setting=None, override=None):
        self._setting = setting
        self._override = override

    def get_setting(self, key):
        return self._setting if key == 'whisper_language' else None

    def get_podcast_row(self, slug):
        return {'language_override': self._override}


def test_env_auto_without_db_setting_returns_none(monkeypatch):
    monkeypatch.setenv('WHISPER_LANGUAGE', 'auto')
    assert get_pattern_language(_FakeDb(), slug='show') is None


def test_env_language_without_db_setting_is_used(monkeypatch):
    monkeypatch.setenv('WHISPER_LANGUAGE', 'sv')
    assert get_pattern_language(_FakeDb(), slug='show') == 'sv'


def test_db_setting_beats_env(monkeypatch):
    monkeypatch.setenv('WHISPER_LANGUAGE', 'sv')
    assert get_pattern_language(_FakeDb(setting='de'), slug='show') == 'de'


def test_feed_override_beats_db_setting_and_env(monkeypatch):
    monkeypatch.setenv('WHISPER_LANGUAGE', 'sv')
    assert get_pattern_language(_FakeDb(setting='de', override='fr'), slug='show') == 'fr'


def test_nothing_set_defaults_to_en(monkeypatch):
    monkeypatch.delenv('WHISPER_LANGUAGE', raising=False)
    assert get_pattern_language(_FakeDb(), slug='show') == 'en'


def test_blank_env_defaults_to_en(monkeypatch):
    monkeypatch.setenv('WHISPER_LANGUAGE', '  ')
    assert get_pattern_language(_FakeDb(), slug='show') == 'en'
