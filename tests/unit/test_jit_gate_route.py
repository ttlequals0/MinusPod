"""The operator-configured agent gate on the just-in-time serve path."""
from unittest.mock import patch

from tests.app_bootstrap import bootstrap

_test_data_dir = bootstrap('jit_gate_route_test_', reset_storage=True)
from main_app import app, db  # noqa: E402

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


def test_blocked_agent_falls_through_for_local_feed():
    """A local episode's original_url is the unreachable local:// sentinel
    (no upstream to redirect to), so the blocked-agent gate must not fire
    for local feeds -- it falls through to normal JIT handling instead."""
    slug = 'jit-gate-local-test'
    db.create_podcast(slug, f'local://{slug}', 'Archive', feed_type='local')
    _set_blocked('["BadBot"]')
    local_ep = {'url': f'local://{slug}', 'title': 'Ep 1',
                'description': '', 'artwork_url': None, 'published': None}
    feed_map = {slug: {'in': f'local://{slug}', 'out': slug}}
    try:
        with patch('main_app.routes.get_feed_map', return_value=feed_map), \
             patch('main_app.routes._lookup_episode', return_value=(local_ep, 'Archive')), \
             patch('main_app.processing.start_background_processing',
                   return_value=(True, None)) as start:
            with app.test_client() as c:
                resp = c.get(f'/episodes/{slug}/{EPISODE_ID}.mp3',
                             headers={'User-Agent': 'BadBot/1.0'})
        assert resp.status_code == 503
        assert resp.headers.get('Location') != local_ep['url']
        start.assert_called_once()
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
