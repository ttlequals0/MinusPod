"""The operator-configured agent gate on the just-in-time serve path."""
from unittest.mock import patch

import pytest

from tests.app_bootstrap import bootstrap

_test_data_dir = bootstrap('jit_gate_route_test_', reset_storage=True)
from main_app import app, db, storage  # noqa: E402

EP = {'url': 'https://cdn.example.com/ep1.mp3', 'title': 'Ep 1',
      'description': '', 'artwork_url': None, 'published': None}
EPISODE_ID = 'abc123def456'
FEED_MAP = {'test-feed': {'in': 'https://example.com/feed.xml', 'out': 'test-feed'}}


def _set_blocked(value):
    db.set_setting('jit_blocked_user_agents', value, is_default=False)


def test_blocked_agent_redirects_instead_of_processing():
    """A blocked crawler gets the origin audio and never queues work."""
    _set_blocked('["BadBot"]')
    with patch('main_app.routes.get_feed_map', return_value=FEED_MAP), \
         patch('main_app.routes._lookup_episode', return_value=(EP, 'Show')), \
         patch('main_app.processing.start_background_processing') as start:
        with app.test_client() as c:
            resp = c.get(f'/episodes/test-feed/{EPISODE_ID}.mp3',
                         headers={'User-Agent': 'BadBot/1.0'})
    assert resp.status_code == 302
    assert resp.headers['Location'] == EP['url']
    start.assert_not_called()


def test_unblocked_agent_still_triggers_processing():
    _set_blocked('["BadBot"]')
    with patch('main_app.routes.get_feed_map', return_value=FEED_MAP), \
         patch('main_app.routes._lookup_episode', return_value=(EP, 'Show')), \
         patch('main_app.processing.start_background_processing',
               return_value=(True, None)) as start:
        with app.test_client() as c:
            resp = c.get(f'/episodes/test-feed/{EPISODE_ID}.mp3',
                         headers={'User-Agent': 'Pocket Casts'})
    assert resp.status_code == 503
    start.assert_called_once()


@pytest.mark.parametrize('feed_type', ['local', 'recents'])
@pytest.mark.parametrize('retained', [True, False])
def test_blocked_agent_on_own_audio_feed_never_processes(feed_type, retained):
    """Local and recents feeds serve the retained original, else 503, never a run."""
    slug = f'jit-gate-{feed_type}-test'
    db.create_podcast(slug, f'{feed_type}://{slug}', 'Archive', feed_type=feed_type)
    _set_blocked('["BadBot"]')
    original = storage.get_original_path(slug, EPISODE_ID)
    if retained:
        original.parent.mkdir(parents=True, exist_ok=True)
        original.write_bytes(b'ID3original')
    feed_map = {slug: {'in': f'{feed_type}://{slug}', 'out': slug}}
    try:
        with patch('main_app.routes.get_feed_map', return_value=feed_map), \
             patch('main_app.processing.start_background_processing') as start:
            with app.test_client() as c:
                resp = c.get(f'/episodes/{slug}/{EPISODE_ID}.mp3',
                             headers={'User-Agent': 'BadBot/1.0'})
        assert resp.status_code == (200 if retained else 503)
        if retained:
            assert resp.data == b'ID3original'
        start.assert_not_called()
    finally:
        db.delete_podcast(slug)
        original.unlink(missing_ok=True)


def test_blocked_agent_on_permanently_failed_local_episode_gets_410():
    slug = 'jit-gate-local-failed-test'
    db.create_podcast(slug, f'local://{slug}', 'Archive', feed_type='local')
    _set_blocked('["BadBot"]')
    feed_map = {slug: {'in': f'local://{slug}', 'out': slug}}
    try:
        with patch('main_app.routes.get_feed_map', return_value=feed_map), \
             patch.object(db, 'get_episode', return_value={'status': 'permanently_failed'}), \
             patch('main_app.processing.start_background_processing') as start:
            with app.test_client() as c:
                resp = c.get(f'/episodes/{slug}/{EPISODE_ID}.mp3',
                             headers={'User-Agent': 'BadBot/1.0'})
        assert resp.status_code == 410
        start.assert_not_called()
    finally:
        db.delete_podcast(slug)


def test_empty_list_never_blocks():
    """Default state: an upgrade must change nothing."""
    _set_blocked('[]')
    with patch('main_app.routes.get_feed_map', return_value=FEED_MAP), \
         patch('main_app.routes._lookup_episode', return_value=(EP, 'Show')), \
         patch('main_app.processing.start_background_processing',
               return_value=(True, None)) as start:
        with app.test_client() as c:
            resp = c.get(f'/episodes/test-feed/{EPISODE_ID}.mp3',
                         headers={'User-Agent': 'BadBot/1.0'})
    assert resp.status_code == 503
    start.assert_called_once()


def test_blocked_agent_redirects_while_episode_is_processing():
    """A crawler hitting an in-flight episode gets the origin audio, not 503."""
    db.create_podcast('test-feed', 'https://example.com/feed.xml', 'Test Feed')
    db.upsert_episode('test-feed', EPISODE_ID, status='processing')
    _set_blocked('["BadBot"]')
    try:
        with patch('main_app.routes.get_feed_map', return_value=FEED_MAP), \
             patch('main_app.routes._lookup_episode', return_value=(EP, 'Show')):
            with app.test_client() as c:
                blocked = c.get(f'/episodes/test-feed/{EPISODE_ID}.mp3',
                                headers={'User-Agent': 'BadBot/1.0'})
                allowed = c.get(f'/episodes/test-feed/{EPISODE_ID}.mp3',
                                headers={'User-Agent': 'Pocket Casts'})
        assert blocked.status_code == 302
        assert blocked.headers['Location'] == EP['url']
        assert allowed.status_code == 503
    finally:
        db.upsert_episode('test-feed', EPISODE_ID, status='discovered')


@pytest.fixture
def youtube_feed():
    slug = 'jit-gate-yt-test'
    db.create_podcast(slug, 'youtube://playlist/PLabc123', 'YT Show', feed_type='youtube')
    db.update_podcast(slug, youtube_source_kind='playlist', youtube_source_id='PLabc123')
    _set_blocked('["BadBot"]')
    feed_map = {slug: {'in': 'youtube://playlist/PLabc123', 'out': slug}}
    with patch('main_app.routes.get_feed_map', return_value=feed_map):
        yield slug
    db.delete_podcast(slug)


def _get_yt(slug, agent):
    with app.test_client() as c:
        return c.get(f'/episodes/{slug}/{EPISODE_ID}.mp3', headers={'User-Agent': agent})


def test_blocked_agent_on_youtube_feed_redirects_to_source_page(youtube_feed):
    with patch('main_app.processing.start_background_processing') as start:
        resp = _get_yt(youtube_feed, 'BadBot/1.0')
    assert resp.status_code == 302
    assert resp.headers['Location'] == 'https://www.youtube.com/playlist?list=PLabc123'
    start.assert_not_called()


def test_blocked_agent_on_youtube_feed_without_source_gets_503(youtube_feed):
    db.update_podcast(youtube_feed, youtube_source_id=None)
    with patch('main_app.processing.start_background_processing') as start:
        resp = _get_yt(youtube_feed, 'BadBot/1.0')
    assert resp.status_code == 503
    start.assert_not_called()


def test_unblocked_agent_on_youtube_feed_keeps_existing_behavior(youtube_feed):
    db.set_setting('youtube_enabled', 'true', is_default=False)
    try:
        with patch('main_app.routes._lookup_episode', return_value=(EP, 'Show')), \
             patch('main_app.processing.start_background_processing',
                   return_value=(True, None)) as start:
            resp = _get_yt(youtube_feed, 'Pocket Casts')
    finally:
        db.set_setting('youtube_enabled', 'false', is_default=False)
    assert resp.status_code == 503
    assert 'Location' not in resp.headers
    start.assert_called_once()
