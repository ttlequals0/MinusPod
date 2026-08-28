"""Local feeds (feed_type='local', source_url='local://<slug>') must be inert
on every upstream-dependent path: refresh, podping feed-map, differential
fetch, artwork refresh, the refresh API, and OPML original-mode export.
"""
from unittest.mock import MagicMock, patch

import pytest

from tests.app_bootstrap import bootstrap

_test_data_dir = bootstrap('local_feed_guards_test_',
                           passphrase='local-feed-guards-test-passphrase')


def _local(slug='arc'):
    return {'id': 1, 'slug': slug, 'source_url': f'local://{slug}',
            'feed_type': 'local', 'title': 'Archive'}


def test_refresh_single_feed_skips_local():
    from main_app import feeds
    with patch.object(feeds, 'db') as db:
        db.get_podcast_by_slug.return_value = _local()
        assert feeds.refresh_single_feed('arc') is False


def test_refresh_all_feeds_excludes_local():
    from main_app import feeds
    with patch.object(feeds, 'get_feed_map') as gfm, \
         patch.object(feeds, 'db') as db, \
         patch.object(feeds, 'refresh_rss_feed') as rrf:
        gfm.return_value = {'arc': {'in': 'local://arc', 'out': '/arc'},
                            'sub': {'in': 'https://x/feed.xml', 'out': '/sub'}}
        db.get_podcast_by_slug.side_effect = lambda s: (
            _local() if s == 'arc' else {'slug': 'sub', 'feed_type': 'subscribed'})
        feeds.refresh_all_feeds()
        called_slugs = {c.args[0] for c in rrf.call_args_list}
        assert 'arc' not in called_slugs
        assert 'sub' in called_slugs


def test_refresh_feed_artwork_skips_local():
    from main_app import feeds
    local = _local()
    local['artwork_url'] = 'https://example.com/cover.png'
    with patch.object(feeds, 'storage') as storage, \
         patch.object(feeds, 'db') as db, \
         patch.object(feeds, 'rss_parser') as rss_parser:
        assert feeds.refresh_feed_artwork('arc', podcast=local) is False
        storage.download_artwork.assert_not_called()
        db.get_podcast_by_slug.assert_not_called()
        rss_parser.fetch_feed.assert_not_called()


def test_podping_feed_map_skips_local():
    from podping_listener import PodpingListener

    db = MagicMock()
    db.get_podcast_feed_urls.return_value = [
        {'slug': 'arc', 'source_url': 'local://arc', 'feed_type': 'local'},
        {'slug': 'sub', 'source_url': 'https://x/feed.xml', 'feed_type': 'subscribed'},
    ]
    db.get_all_podping_declarations.return_value = {}

    listener = PodpingListener(db=db, refresh=MagicMock(), sleep=lambda s: None)
    listener._refresh_feed_map()

    assert 'arc' not in listener.feed_map.values()
    assert 'sub' in listener.feed_map.values()


def test_differential_fetch_skips_local():
    """The caller already has the podcast row; the guard must use the passed
    `podcast` kwarg rather than refetching it per episode."""
    from main_app import processing
    with patch.object(processing, 'db') as db:
        result = processing._run_differential_fetch(
            'arc', 'ep1', 'https://example.com/ep1.mp3', '/tmp/ep1.mp3', 1,
            podcast=_local())
    assert result is None
    db.get_podcast_by_slug.assert_not_called()


def test_differential_fetch_runs_when_podcast_missing_or_subscribed():
    """None (no row available) and a non-local row must both behave as
    before: the guard does not skip the stage."""
    from main_app import processing
    with patch.object(processing, 'resolve_differential_fetch_setting',
                      return_value=False) as resolve, \
         patch.object(processing, 'db'):
        assert processing._run_differential_fetch(
            'arc', 'ep1', 'https://example.com/ep1.mp3', '/tmp/ep1.mp3', 1) is None
        assert processing._run_differential_fetch(
            'sub', 'ep1', 'https://example.com/ep1.mp3', '/tmp/ep1.mp3', 1,
            podcast={'slug': 'sub', 'feed_type': 'subscribed'}) is None
    # Both calls reached the gate (proving neither was short-circuited as
    # local); the gate itself turned them off via resolve_differential_fetch_setting.
    assert resolve.call_count == 2


def test_opml_original_mode_skips_local():
    from utils.opml import build_opml_xml
    xml = build_opml_xml([_local(), {'slug': 'sub', 'title': 'Sub',
                                     'source_url': 'https://x/feed.xml',
                                     'feed_type': 'subscribed'}],
                         'original', 'https://pods.example.com')
    assert 'local://' not in xml
    assert 'https://x/feed.xml' in xml


def test_opml_modified_mode_includes_local():
    from utils.opml import build_opml_xml
    xml = build_opml_xml([_local()], 'modified', 'https://pods.example.com')
    assert 'https://pods.example.com/arc' in xml


@pytest.fixture
def client():
    from main_app import app
    app.config['TESTING'] = True
    with app.test_client() as c:
        yield c


def test_api_refresh_feed_rejects_local(client):
    from database import Database
    db = Database()
    db.create_podcast('arc', 'local://arc', 'Archive', feed_type='local')

    r = client.post('/api/v1/feeds/arc/refresh')

    assert r.status_code == 400
    assert 'upstream' in r.get_json()['error'].lower()
