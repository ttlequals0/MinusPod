"""A play request deferred for a busy worker must reach the real work queue,
not only the status file the UI reads, which nothing drains."""
from unittest.mock import patch

import pytest

from tests.app_bootstrap import bootstrap

_test_data_dir = bootstrap('jit_queue_test_')

from main_app import app  # noqa: E402
from utils.constants import REPROCESS_SOURCE_JIT  # noqa: E402

EP = 'a1b2c3d4e5f6'
SLUG = 'example-podcast'
LOOKUP = ({'id': EP, 'url': 'https://example.com/ep.mp3', 'title': 'Episode One',
           'description': 'desc', 'artwork_url': None,
           'published': '2026-07-22T04:12:25Z'}, 'Example Podcast')


@pytest.fixture
def client():
    app.config['TESTING'] = True
    with app.test_client() as c:
        yield c


def _busy(mock_db):
    """An episode that exists, is unprocessed, and has no rendered audio."""
    mock_db.get_episode.return_value = {
        'episode_id': EP, 'status': 'discovered',
        'original_url': 'https://example.com/ep.mp3',
    }


@patch('main_app.processing.start_background_processing',
       return_value=(False, 'queue_busy:other:ep'))
@patch('main_app.routes._lookup_episode', return_value=LOOKUP)
@patch('main_app.routes.status_service')
@patch('main_app.routes.db')
@patch('main_app.routes.get_feed_map',
       return_value={SLUG: {'in': 'https://example.com/f.xml', 'out': SLUG}})
def test_queue_busy_writes_a_real_work_queue_row(
    _feed_map, mock_db, mock_status, _lookup, _start, client,
):
    _busy(mock_db)
    mock_status.get_queue_position.return_value = 1

    resp = client.get(f'/episodes/{SLUG}/{EP}.mp3')

    assert resp.status_code == 503
    # The display queue alone left this episode stranded.
    mock_status.queue_episode.assert_called_once()
    mock_db.upsert_episode_for_processing.assert_called_once()
    args = mock_db.upsert_episode_for_processing.call_args.args
    assert args[0] == SLUG
    assert args[1] == EP


@patch('main_app.processing.start_background_processing',
       return_value=(False, 'queue_busy:other:ep'))
@patch('main_app.routes._lookup_episode', return_value=LOOKUP)
@patch('main_app.routes.status_service')
@patch('main_app.routes.db')
@patch('main_app.routes.get_feed_map',
       return_value={SLUG: {'in': 'https://example.com/f.xml', 'out': SLUG}})
def test_queue_busy_marks_the_play_as_user_requested(
    _feed_map, mock_db, mock_status, _lookup, _start, client,
):
    """Without this the drainer's auto-process gate drops the row on feeds
    with auto-processing turned off."""
    _busy(mock_db)
    mock_status.get_queue_position.return_value = 1

    client.get(f'/episodes/{SLUG}/{EP}.mp3')

    stamped = [c for c in mock_db.upsert_episode.call_args_list
               if c.kwargs.get('reprocess_requested_at')]
    assert stamped, 'a play request must be marked user-requested'


@patch('main_app.processing.start_background_processing',
       return_value=(False, 'queue_busy:other:ep'))
@patch('main_app.routes._lookup_episode', return_value=LOOKUP)
@patch('main_app.routes.status_service')
@patch('main_app.routes.db')
@patch('main_app.routes.get_feed_map',
       return_value={SLUG: {'in': 'https://example.com/f.xml', 'out': SLUG}})
def test_queue_busy_records_jit_provenance(
    _feed_map, mock_db, mock_status, _lookup, _start, client,
):
    """The stamp gets the play past the auto-process gate; the provenance
    marker keeps it distinguishable from a human reprocess."""
    _busy(mock_db)
    mock_status.get_queue_position.return_value = 1

    client.get(f'/episodes/{SLUG}/{EP}.mp3')

    stamped = [c for c in mock_db.upsert_episode.call_args_list
               if c.kwargs.get('reprocess_requested_at')]
    assert stamped
    assert stamped[0].kwargs['reprocess_source'] == REPROCESS_SOURCE_JIT
