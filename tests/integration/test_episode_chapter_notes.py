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
    with patch('api.episodes.threading', SimpleNamespace(Thread=SyncThread)), \
         patch('api.episodes.ChaptersGenerator') as generator, \
         patch('api.episodes.embed_chapters', return_value=False), \
         patch('main_app.processing._refresh_rss_for_slug') as refresh:
        generator.return_value.generate_chapters.return_value = CHAPTERS
        resp = app_client.post(f'/api/v1/feeds/{SLUG}/episodes/{EPISODE_ID}/regenerate-chapters',
                               headers=headers)
    assert resp.status_code == 202, resp.data
    refresh.assert_called_once_with(SLUG, EPISODE_ID)
