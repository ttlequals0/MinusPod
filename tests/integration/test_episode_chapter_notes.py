"""The episode API description carries the chapter list when enabled (#720)."""
import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from api import get_database
from tests.unit.thread_fakes import SyncThread

SLUG = 'chapter-notes-slug'
EPISODE_ID = 'abcdef012345'
CHAPTERS = {'version': '1.2.0', 'chapters': [{'startTime': 0, 'title': 'Intro'},
                                             {'startTime': 65, 'title': 'Main'}]}


@pytest.fixture
def seeded(app_client):
    db = get_database()
    db.set_setting('chapters_in_notes', 'false', is_default=True)
    db.create_podcast(SLUG, 'https://example.com/feed.xml', 'Chapter Notes')
    db.upsert_episode(slug=SLUG, episode_id=EPISODE_ID, original_url='https://example.com/ep.mp3',
                      title='Ep', description='<p>Notes</p>', status='processed')
    db.save_episode_details(SLUG, EPISODE_ID, chapters_json=json.dumps(CHAPTERS))
    yield db
    db.set_setting('chapters_in_notes', 'false', is_default=True)
    db.delete_podcast(SLUG)


def test_detail_carries_no_block_by_default(app_client, seeded):
    body = app_client.get(f'/api/v1/feeds/{SLUG}/episodes/{EPISODE_ID}').get_json()
    assert body['description'] == '<p>Notes</p>'
    assert body['chapterNotes'] == ''


def test_detail_carries_the_block_beside_the_description_when_enabled(app_client, seeded):
    """Separate field: the local-episode editor round-trips description."""
    seeded.set_setting('chapters_in_notes', 'true', is_default=False)
    body = app_client.get(f'/api/v1/feeds/{SLUG}/episodes/{EPISODE_ID}').get_json()
    assert body['description'] == '<p>Notes</p>'
    assert body['chapterNotes'] == '<p>Chapters</p><p>00:00 Intro<br>01:05 Main</p>'


def test_feed_override_off_wins_over_global_on(app_client, seeded):
    seeded.set_setting('chapters_in_notes', 'true', is_default=False)
    seeded.update_podcast(SLUG, chapters_in_notes='off')
    body = app_client.get(f'/api/v1/feeds/{SLUG}/episodes/{EPISODE_ID}').get_json()
    assert body['chapterNotes'] == ''


def _authed(client):
    with client.session_transaction() as sess:
        sess['authenticated'] = True
    client.get('/api/v1/auth/status')
    cookie = client.get_cookie('minuspod_csrf')
    return {'X-CSRF-Token': cookie.value} if cookie else {}


def test_regenerating_chapters_force_refreshes_the_served_feed(app_client, seeded):
    """Same seam a finished run uses, so a listed chapter block updates."""
    from storage import Storage
    Storage().save_transcript_vtt(SLUG, EPISODE_ID, 'WEBVTT\n\n00:00.000 --> 00:01.000\nHi\n')
    headers = _authed(app_client)
    with patch('api.episodes.chapters.threading', SimpleNamespace(Thread=SyncThread)), \
         patch('api.episodes.chapters.ChaptersGenerator') as generator, \
         patch('api.episodes.chapters.embed_chapters', return_value=False), \
         patch('main_app.pipeline.common._refresh_rss_for_slug') as refresh:
        generator.return_value.generate_chapters.return_value = CHAPTERS
        resp = app_client.post(f'/api/v1/feeds/{SLUG}/episodes/{EPISODE_ID}/regenerate-chapters',
                               headers=headers)
    assert resp.status_code == 202, resp.data
    refresh.assert_called_once_with(SLUG, EPISODE_ID)


def test_regenerating_chapters_passes_the_feed_show_notes_switch(app_client, seeded):
    from storage import Storage
    Storage().save_transcript_vtt(SLUG, EPISODE_ID, 'WEBVTT\n\n00:00.000 --> 00:01.000\nHi\n')
    seeded.update_podcast(SLUG, show_notes_chapters=0)
    headers = _authed(app_client)
    with patch('api.episodes.chapters.threading', SimpleNamespace(Thread=SyncThread)), \
         patch('api.episodes.chapters.ChaptersGenerator') as generator, \
         patch('api.episodes.chapters.embed_chapters', return_value=False), \
         patch('main_app.pipeline.common._refresh_rss_for_slug'):
        generator.return_value.generate_chapters.return_value = CHAPTERS
        generator.return_value.chapters_degraded = False
        resp = app_client.post(f'/api/v1/feeds/{SLUG}/episodes/{EPISODE_ID}/regenerate-chapters',
                               headers=headers)
    assert resp.status_code == 202, resp.data
    assert generator.return_value.generate_chapters.call_args.kwargs['show_notes_chapters'] is False


def _regenerate_switch(app_client, seeded, durations, cuts=None):
    from storage import Storage
    Storage().save_transcript_vtt(SLUG, EPISODE_ID, 'WEBVTT\n\n00:00.000 --> 00:01.000\nHi\n')
    seeded.upsert_episode(slug=SLUG, episode_id=EPISODE_ID, **durations)
    if cuts is not None:
        seeded.save_applied_cuts(SLUG, EPISODE_ID, cuts)
    headers = _authed(app_client)
    with patch('api.episodes.chapters.threading', SimpleNamespace(Thread=SyncThread)), \
         patch('api.episodes.chapters.ChaptersGenerator') as generator, \
         patch('api.episodes.chapters.embed_chapters', return_value=False), \
         patch('main_app.pipeline.common._refresh_rss_for_slug'):
        generator.return_value.generate_chapters.return_value = CHAPTERS
        generator.return_value.chapters_degraded = False
        resp = app_client.post(f'/api/v1/feeds/{SLUG}/episodes/{EPISODE_ID}/regenerate-chapters',
                               headers=headers)
    assert resp.status_code == 202, resp.data
    return generator.return_value.generate_chapters.call_args.kwargs['show_notes_chapters']


def test_regeneration_skips_show_notes_when_audio_was_cut_without_a_stored_cut_list(app_client, seeded):
    assert _regenerate_switch(
        app_client, seeded, {'original_duration': 3000.0, 'new_duration': 2700.0}) is False


def test_regeneration_keeps_show_notes_when_audio_was_not_shortened(app_client, seeded):
    assert _regenerate_switch(
        app_client, seeded, {'original_duration': 3000.0, 'new_duration': 3000.0}) is True


def test_regeneration_keeps_show_notes_when_the_cut_list_is_stored(app_client, seeded):
    assert _regenerate_switch(
        app_client, seeded, {'original_duration': 3000.0, 'new_duration': 2700.0},
        cuts=[{'start': 100.0, 'end': 400.0}]) is True
