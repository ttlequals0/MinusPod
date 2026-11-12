"""Chapters with the LLM provider set to none."""
import logging
from unittest.mock import MagicMock, patch

import pytest

from tests.app_bootstrap import bootstrap, authenticate_test_client

bootstrap('llm_optional_chapters_test_')

import llm_client  # noqa: E402
from config import (  # noqa: E402
    CHAPTERS_MODE_AUTO, CHAPTERS_MODE_GENERATE, CHAPTERS_MODE_OFF, resolve_chapters_mode,
)
from api.episodes import chapters as chapters_routes  # noqa: E402
from tests.integration.openapi_conformance import assert_response_conforms  # noqa: E402
from tests.unit.test_chapters_mode import MARKED_SPONSOR, _db, _run  # noqa: E402

PUBLISHER = [
    {'start': 0.0, 'end': 30.0, 'title': 'Intro'},
    {'start': 100.4, 'end': 200.0, 'title': 'Main'},
]


@pytest.fixture
def provider_none(monkeypatch):
    monkeypatch.setattr(llm_client, 'get_effective_provider', lambda: 'none')


@pytest.fixture
def provider_anthropic(monkeypatch):
    monkeypatch.setattr(llm_client, 'get_effective_provider', lambda: 'anthropic')


def test_generate_behaves_like_auto_under_none(provider_none):
    assert resolve_chapters_mode({'chapters_mode': CHAPTERS_MODE_GENERATE}) == CHAPTERS_MODE_AUTO
    db = MagicMock()
    db.get_setting.return_value = CHAPTERS_MODE_GENERATE
    assert resolve_chapters_mode({'chapters_mode': None}, db=db) == CHAPTERS_MODE_AUTO


def test_off_and_auto_are_unchanged_under_none(provider_none):
    assert resolve_chapters_mode({'chapters_mode': CHAPTERS_MODE_OFF}) == CHAPTERS_MODE_OFF
    assert resolve_chapters_mode({'chapters_mode': CHAPTERS_MODE_AUTO}) == CHAPTERS_MODE_AUTO


def test_generate_stays_generate_with_a_provider(provider_anthropic):
    assert resolve_chapters_mode({'chapters_mode': CHAPTERS_MODE_GENERATE}) == CHAPTERS_MODE_GENERATE


def test_chapter_step_skips_generation_but_keeps_ad_chapters_under_none(monkeypatch, provider_none):
    db = _db(chapters_mode='auto')
    run_stats = {}
    _, _, generator_class, embed_mock, _ = _run(
        monkeypatch, db, [], run_stats=run_stats, markers=MARKED_SPONSOR)
    generator_class.assert_not_called()
    saved = db.save_processing_assets.call_args.args[2]['chapters']['chapters']
    assert [ch['startTime'] for ch in saved] == [900]
    embed_mock.assert_called_once()
    assert 'chapters_degraded' not in run_stats
    assert 'chapters_degraded_reason' not in run_stats


def test_chapter_step_with_nothing_to_publish_is_quiet_under_none(monkeypatch, provider_none):
    db = _db(chapters_mode='auto')
    run_stats = {}
    _, _, generator_class, embed_mock, _ = _run(monkeypatch, db, [], run_stats=run_stats)
    generator_class.assert_not_called()
    embed_mock.assert_not_called()
    assert 'chapters' not in db.save_processing_assets.call_args.args[2]
    assert run_stats == {}


def test_publisher_chapters_are_kept_under_none(monkeypatch, provider_none):
    db = _db(chapters_mode='auto')
    _, _, generator_class, _, _ = _run(monkeypatch, db, PUBLISHER)
    generator_class.assert_not_called()
    saved = db.save_processing_assets.call_args.args[2]['chapters']['chapters']
    assert [ch['title'] for ch in saved] == ['Intro', 'Main']


def test_generate_mode_feed_keeps_publisher_chapters_under_none(monkeypatch, provider_none):
    db = _db(chapters_mode='generate')
    _, probe_mock, generator_class, _, _ = _run(monkeypatch, db, PUBLISHER)
    probe_mock.assert_called_once()
    generator_class.assert_not_called()
    saved = db.save_processing_assets.call_args.args[2]['chapters']['chapters']
    assert [ch['title'] for ch in saved] == ['Intro', 'Main']


def test_upstream_json_chapters_are_still_fetched_under_none(monkeypatch, provider_none):
    db = _db(chapters_mode='auto', upstream_chapters_url='https://example.com/chapters.json')
    fetched = [{'startTime': 5, 'title': 'One'}, {'startTime': 60, 'title': 'Two'}]
    _, _, generator_class, _, fetch_mock = _run(
        monkeypatch, db, [], original_duration=100.0, fetch_return=fetched)
    fetch_mock.assert_called_once()
    generator_class.assert_not_called()
    saved = db.save_processing_assets.call_args.args[2]['chapters']['chapters']
    assert [ch['title'] for ch in saved] == ['One', 'Two']


def test_generate_mode_with_a_provider_still_generates(monkeypatch, provider_anthropic):
    db = _db(chapters_mode='generate')
    run_stats = {}
    _, probe_mock, generator_class, _, _ = _run(monkeypatch, db, PUBLISHER, run_stats=run_stats)
    probe_mock.assert_not_called()
    generator_class.return_value.generate_chapters.assert_called_once()


def test_failed_generation_with_a_provider_still_marks_chapters_degraded(monkeypatch, provider_anthropic):
    db = _db(chapters_mode='auto')
    run_stats = {}
    _run(monkeypatch, db, [], run_stats=run_stats, generator_setup_error=RuntimeError('boom'))
    assert run_stats['chapters_degraded'] is True


@pytest.fixture
def chapters_feed(app_client):
    from api import get_database
    db = get_database()
    db.create_podcast('chapters-none-feed', 'https://example.com/feed.xml', 'Chapters None')
    db.upsert_episode('chapters-none-feed', 'bb11cc22dd33', title='Episode', status='processed',
                      original_url='https://example.com/e.mp3')
    token = authenticate_test_client(app_client)
    yield app_client, token
    db.delete_podcast('chapters-none-feed')


URL = '/api/v1/feeds/chapters-none-feed/episodes/bb11cc22dd33/regenerate-chapters'


def test_regenerate_chapters_returns_409_under_none(chapters_feed, provider_none):
    client, token = chapters_feed
    with patch.object(chapters_routes.threading, 'Thread') as thread:
        response = client.post(URL, headers=token)
    assert response.status_code == 409
    body = response.get_json()
    assert body['code'] == 'llm_disabled'
    assert body['status'] == 409
    thread.assert_not_called()
    assert_response_conforms(
        'post', '/feeds/{slug}/episodes/{episode_id}/regenerate-chapters', response, status=409)


def test_regenerate_chapters_without_a_transcript_is_still_400_with_a_provider(chapters_feed, provider_anthropic):
    client, token = chapters_feed
    response = client.post(URL, headers=token)
    assert response.status_code == 400
    assert 'code' not in response.get_json()


def test_regenerate_chapters_unknown_episode_is_404_under_none(chapters_feed, provider_none):
    client, token = chapters_feed
    response = client.post(URL.replace('bb11cc22dd33', 'ffffffffffff'), headers=token)
    assert response.status_code == 404


def test_regen_thread_that_loses_the_provider_is_a_quiet_noop(chapters_feed, provider_anthropic, caplog):
    from api import get_database
    db = get_database()
    stamp = db.claim_chapters_regen('chapters-none-feed', 'bb11cc22dd33')
    assert stamp

    def refuse(*args, **kwargs):
        raise llm_client.LLMDisabledError('none')
    with patch.object(chapters_routes, '_regenerate_chapters', refuse), caplog.at_level(logging.DEBUG):
        chapters_routes._regenerate_chapters_job('chapters-none-feed', 'bb11cc22dd33', stamp)
    episode = db.get_episode('chapters-none-feed', 'bb11cc22dd33')
    assert not episode['chapters_regen_error'] and not episode['chapters_regen_started_at']
    rows, _ = db.get_processing_history(podcast_slug='chapters-none-feed')
    assert rows == []
    assert [r for r in caplog.records if r.levelno >= logging.WARNING] == []
