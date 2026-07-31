"""A 304 must still get the <podcast:podping> declaration read once.

A 304 carries no body, so the declaration cannot be parsed from it. Feeds whose
RSS rarely changes would otherwise never have the tag ingested, leaving per-feed
hiveAccount authorization inert (#579).
"""
import os
import sys
import tempfile
from unittest.mock import MagicMock, patch

os.environ.setdefault('MINUSPOD_DATA_DIR', tempfile.mkdtemp(prefix='podping_304_test_'))
os.environ.setdefault('SECRET_KEY', 'test-secret')

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))

import main_app.feeds as feeds_mod

DECLARATION = {'uses_podping': True, 'hive_accounts': ['podping.aaa']}


def _refresh(podcast_row, slug):
    """Drive refresh_rss_feed against a 304, returning (db, rss_parser).

    Each test uses its own slug: refresh_rss_feed coalesces repeat attempts on
    the same slug within 30s.
    """
    db = MagicMock()
    rss_parser = MagicMock()
    storage = MagicMock()

    db.get_podcast_by_slug.return_value = podcast_row
    db.get_episodes.return_value = ([], 1)  # one discovered episode
    db.get_processed_episodes_for_feed.return_value = []
    db.bulk_upsert_discovered_episodes.return_value = 0
    db.is_auto_process_enabled_for_podcast.return_value = False
    storage.get_rss.return_value = '<rss/>'

    # First call is the conditional GET returning 304, second is the forced
    # full fetch that actually carries a body.
    rss_parser.fetch_feed_conditional.side_effect = [
        (None, 'etag-1', None),
        (b'<rss/>', 'etag-1', None),
    ]
    parsed = MagicMock()
    parsed.feed = {'title': 'Show', 'description': '', 'link': 'https://example.com'}
    parsed.entries = [1]
    parsed.bozo = False
    rss_parser.parse_feed.return_value = parsed
    rss_parser.extract_podcast_artwork_url.return_value = None
    rss_parser.extract_episodes.return_value = []
    rss_parser.extract_podping_declaration.return_value = DECLARATION

    with patch.object(feeds_mod, 'db', db), \
         patch.object(feeds_mod, 'rss_parser', rss_parser), \
         patch.object(feeds_mod, 'storage', storage), \
         patch.object(feeds_mod, 'status_service', MagicMock()), \
         patch.object(feeds_mod, 'pattern_service', MagicMock()), \
         patch('main_app.feeds._build_and_save_served_rss'):
        feeds_mod.refresh_rss_feed(slug, 'https://example.com/f.xml')
    return db, rss_parser


def _metadata_kwargs(db):
    for call in db.update_podcast.call_args_list:
        if 'podping_uses' in call.kwargs:
            return call.kwargs
    return None


def test_304_forces_one_full_fetch_when_never_read():
    db, rss_parser = _refresh({
        'id': 1, 'etag': 'etag-1', 'last_modified_header': None,
        'artwork_cached': True, 'podping_checked_at': None,
    }, 'never-read-feed')
    assert rss_parser.fetch_feed_conditional.call_count == 2
    kwargs = _metadata_kwargs(db)
    assert kwargs is not None, 'declaration was never written'
    assert kwargs['podping_uses'] == 1
    assert kwargs['podping_hive_accounts'] == '["podping.aaa"]'
    assert kwargs['podping_checked_at']


def test_304_does_not_refetch_once_already_read():
    db, rss_parser = _refresh({
        'id': 1, 'etag': 'etag-1', 'last_modified_header': None,
        'artwork_cached': True, 'podping_checked_at': '2026-07-26T00:00:00Z',
        'channel_metadata_at': '2026-07-26T00:00:00Z',
    }, 'already-read-feed')
    # Only the conditional GET: no forced second fetch.
    assert rss_parser.fetch_feed_conditional.call_count == 1
    assert _metadata_kwargs(db) is None


def test_a_tagless_feed_is_still_marked_as_read():
    """podping_uses stays NULL for a feed with no tag, but checked_at is set,
    so the next 304 does not force another fetch."""
    from database.podcasts import podping_declaration_columns
    cols = podping_declaration_columns(None, [])
    assert cols['podping_uses'] is None
    assert cols['podping_hive_accounts'] is None
    assert cols['podping_checked_at']
