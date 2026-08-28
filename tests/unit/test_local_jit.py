"""JIT processing must reuse a retained local original instead of
re-downloading it, and must never dereference the ``local://<episode_id>``
sentinel that stands in for a local feed's (nonexistent) upstream URL.

Covers:
- _download_and_transcribe's fresh-episode branch reuses a retained
  original when present (no download attempt), for any feed.
- _download_and_transcribe raises instead of downloading when the feed is
  local and the retained original is missing.
- _lookup_episode skips rss_parser.fetch_feed entirely for local feeds.
- The HEAD path for an unprocessed local episode reports on whatever audio
  is held locally instead of proxying a (nonexistent) upstream.
"""
from unittest.mock import MagicMock, patch

import pytest

from tests.app_bootstrap import bootstrap

_test_data_dir = bootstrap('local_jit_test_', reset_storage=True)

import main_app.processing as processing  # noqa: E402
import main_app.routes as routes_mod  # noqa: E402
from main_app import app, db, storage  # noqa: E402


def _local(slug):
    return {'id': 1, 'slug': slug, 'source_url': f'local://{slug}',
            'feed_type': 'local', 'title': 'Archive'}


def _seg(start, end, text):
    return {'start': start, 'end': end, 'text': text}


# ---------------------------------------------------------------------
# _download_and_transcribe: fresh-episode branch reuse / missing-original
# ---------------------------------------------------------------------

def test_fresh_episode_reuses_retained_original_no_download(tmp_path):
    original = tmp_path / 'orig.mp3'
    original.write_bytes(b'mp3-bytes')
    segments = [_seg(0.0, 10.0, 'hi')]

    mock_storage = MagicMock()
    mock_storage.get_transcript.return_value = None
    mock_storage.get_original_path.return_value = str(original)
    mock_t = MagicMock()
    mock_t.transcribe_chunked.return_value = list(segments)
    mock_t.segments_to_text.return_value = 'joined'
    mock_sponsor = MagicMock()
    mock_sponsor.apply_transcript_corrections.side_effect = lambda t: t

    with patch.object(processing, 'storage', mock_storage), \
         patch.object(processing, 'transcriber', mock_t), \
         patch.object(processing, 'sponsor_service', mock_sponsor), \
         patch.object(processing, 'status_service', MagicMock()), \
         patch.object(processing, 'get_feed_language_override',
                      return_value=None), \
         patch.object(processing, '_retranscribe_tail_no_vad',
                      return_value=(segments, False)), \
         patch.object(processing, '_copy_retained_original_to_temp',
                      return_value='/tmp/reused.mp3') as copy_fn, \
         patch.object(processing, '_download_episode_audio') as download_fn:
        audio_path, out_segments = processing._download_and_transcribe(
            'show', 'ep1', 'http://example.com/e.mp3', 'Show')

    assert audio_path == '/tmp/reused.mp3'
    assert out_segments == segments
    copy_fn.assert_called_once_with(str(original))
    download_fn.assert_not_called()


def test_local_missing_original_raises_without_downloading():
    mock_storage = MagicMock()
    mock_storage.get_transcript.return_value = None
    mock_storage.get_original_path.return_value = None

    with patch.object(processing, 'storage', mock_storage), \
         patch.object(processing, '_download_episode_audio') as download_fn:
        with pytest.raises(Exception, match='original audio missing'):
            processing._download_and_transcribe(
                'arc', 's01e01', 'local://s01e01', 'Archive',
                podcast=_local('arc'))

    download_fn.assert_not_called()


def test_non_local_missing_original_still_downloads():
    """Sanity check: the raise is local-only. A subscribed feed with no
    retained original (the common case) still downloads as before."""
    mock_storage = MagicMock()
    mock_storage.get_transcript.return_value = None
    mock_storage.get_original_path.return_value = None
    mock_t = MagicMock()
    mock_t.transcribe_chunked.return_value = [_seg(0.0, 5.0, 'hi')]
    mock_t.segments_to_text.return_value = 'joined'
    mock_sponsor = MagicMock()
    mock_sponsor.apply_transcript_corrections.side_effect = lambda t: t

    with patch.object(processing, 'storage', mock_storage), \
         patch.object(processing, 'transcriber', mock_t), \
         patch.object(processing, 'sponsor_service', mock_sponsor), \
         patch.object(processing, 'status_service', MagicMock()), \
         patch.object(processing, 'get_feed_language_override',
                      return_value=None), \
         patch.object(processing, '_retranscribe_tail_no_vad',
                      return_value=([_seg(0.0, 5.0, 'hi')], False)), \
         patch.object(processing, '_download_episode_audio',
                      return_value='/tmp/dl.mp3') as download_fn:
        audio_path, _ = processing._download_and_transcribe(
            'sub', 'abc123def456', 'http://cdn.example.com/e.mp3', 'Show',
            podcast={'feed_type': 'subscribed'})

    assert audio_path == '/tmp/dl.mp3'
    download_fn.assert_called_once_with('http://cdn.example.com/e.mp3')


def test_skip_transcription_local_missing_original_raises_without_downloading():
    """cue_only+skip_transcription branch: same local://-sentinel guard as
    the fresh-episode branch."""
    mock_storage = MagicMock()
    mock_storage.get_original_path.return_value = None

    with patch.object(processing, 'storage', mock_storage), \
         patch.object(processing, '_download_episode_audio') as download_fn:
        with pytest.raises(Exception, match='original audio missing'):
            processing._download_and_transcribe(
                'arc', 's01e01', 'local://s01e01', 'Archive',
                skip_transcription=True, podcast=_local('arc'))

    download_fn.assert_not_called()


def test_existing_transcript_local_missing_original_raises_without_downloading():
    """Reprocess-with-existing-transcript branch: same guard. A local
    episode being reprocessed should still have its original (keep_original
    is forced on for local feeds), but if it was ever lost this must not
    fall through to downloading the local:// sentinel."""
    segments = [_seg(0.0, 10.0, 'hi')]
    mock_storage = MagicMock()
    mock_storage.get_transcript.return_value = 'existing transcript'
    mock_storage.get_original_path.return_value = None
    mock_db = MagicMock()
    mock_db.get_original_segments.return_value = list(segments)

    with patch.object(processing, 'storage', mock_storage), \
         patch.object(processing, 'db', mock_db), \
         patch.object(processing, '_download_episode_audio') as download_fn:
        with pytest.raises(Exception, match='original audio missing'):
            processing._download_and_transcribe(
                'arc', 's01e01', 'local://s01e01', 'Archive',
                podcast=_local('arc'))

    download_fn.assert_not_called()


# ---------------------------------------------------------------------
# _lookup_episode: local feeds skip rss_parser.fetch_feed
# ---------------------------------------------------------------------

def test_lookup_episode_local_skips_upstream_fetch():
    with patch.object(routes_mod, 'db') as mock_db, \
         patch.object(routes_mod, 'rss_parser') as mock_rss:
        mock_db.get_podcast_by_slug.return_value = _local('arc')
        mock_db.get_episode.return_value = {
            'original_url': 'local://s01e01',
            'title': 'Ep 1',
            'description': 'desc',
            'artwork_url': None,
            'published_at': None,
            'podcast_title': 'Archive',
        }
        ep_data, podcast_name = routes_mod._lookup_episode(
            'arc', 's01e01', {'arc': {'in': 'local://arc', 'out': '/arc'}})

    mock_rss.fetch_feed.assert_not_called()
    assert ep_data['url'] == 'local://s01e01'
    assert podcast_name == 'Archive'


# ---------------------------------------------------------------------
# HEAD requests for local, not-yet-processed episodes
# ---------------------------------------------------------------------

def test_head_local_episode_reports_retained_original_length():
    db.create_podcast('archead', 'local://archead', feed_type='local')
    db.upsert_episode('archead', 's01e01', original_url='local://s01e01',
                      status='discovered', title='Ep1')
    orig = storage.get_original_path('archead', 's01e01')
    orig.parent.mkdir(parents=True, exist_ok=True)
    orig.write_bytes(b'0123456789')

    with patch('main_app.routes.get_feed_map',
               return_value={'archead': {'in': 'local://archead',
                                          'out': '/archead'}}):
        with app.test_client() as c:
            resp = c.head('/episodes/archead/s01e01.mp3')

    assert resp.status_code == 200
    assert resp.headers['Content-Type'] == 'audio/mpeg'
    assert resp.headers['Content-Length'] == '10'


def test_head_local_episode_404s_when_nothing_retained():
    db.create_podcast('archead2', 'local://archead2', feed_type='local')
    db.upsert_episode('archead2', 's01e02', original_url='local://s01e02',
                      status='discovered', title='Ep2')

    with patch('main_app.routes.get_feed_map',
               return_value={'archead2': {'in': 'local://archead2',
                                           'out': '/archead2'}}):
        with app.test_client() as c:
            resp = c.head('/episodes/archead2/s01e02.mp3')

    assert resp.status_code == 404


# ---------------------------------------------------------------------
# GET serve_episode: unprocessed local episodes serve the retained
# original instead of blocking on the single ProcessingQueue slot.
# ---------------------------------------------------------------------

def _make_local_podcast_and_episode(slug, episode_id, status, **episode_kwargs):
    db.create_podcast(slug, f'local://{slug}', feed_type='local')
    db.upsert_episode(slug, episode_id, original_url=f'local://{episode_id}',
                      status=status, title='Ep1', **episode_kwargs)


def _write_original(slug, episode_id, data):
    path = storage.get_original_path(slug, episode_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def test_local_discovered_queue_busy_serves_original_and_still_queues():
    """(1) discovered + original present + queue busy -> 200 original,
    while the JIT/queue-stamp attempt still happens in the background."""
    slug, ep = 'locqbusy', 's01e01'
    _make_local_podcast_and_episode(slug, ep, status='discovered')
    _write_original(slug, ep, b'ORIGINAL-BYTES-0123456789')

    with patch('main_app.processing.start_background_processing',
               return_value=(False, 'queue_busy:other:ep')) as mock_start, \
         patch('main_app.routes.status_service') as mock_status, \
         patch('main_app.routes.get_feed_map',
               return_value={slug: {'in': f'local://{slug}', 'out': f'/{slug}'}}):
        mock_status.get_queue_position.return_value = 1
        with app.test_client() as c:
            resp = c.get(f'/episodes/{slug}/{ep}.mp3')

    assert resp.status_code == 200
    assert resp.data == b'ORIGINAL-BYTES-0123456789'
    assert resp.headers['Content-Type'] == 'audio/mpeg'
    mock_start.assert_called_once()
    mock_status.queue_episode.assert_called_once()


def test_local_processing_status_serves_original():
    """(2) status=processing + original present -> 200 original, no 503."""
    slug, ep = 'locproc', 's01e01'
    _make_local_podcast_and_episode(slug, ep, status='processing')
    _write_original(slug, ep, b'PROCESSING-BYTES')

    with patch('main_app.routes.get_feed_map',
               return_value={slug: {'in': f'local://{slug}', 'out': f'/{slug}'}}):
        with app.test_client() as c:
            resp = c.get(f'/episodes/{slug}/{ep}.mp3')

    assert resp.status_code == 200
    assert resp.data == b'PROCESSING-BYTES'


def test_local_failed_within_cooldown_serves_original():
    """(3) status=failed (fresh, within retry cooldown) + original present
    -> 200 original instead of the cooldown 503."""
    slug, ep = 'locfail', 's01e01'
    _make_local_podcast_and_episode(slug, ep, status='failed', retry_count=1)
    _write_original(slug, ep, b'FAILED-BYTES')

    with patch('main_app.routes.get_feed_map',
               return_value={slug: {'in': f'local://{slug}', 'out': f'/{slug}'}}):
        with app.test_client() as c:
            resp = c.get(f'/episodes/{slug}/{ep}.mp3')

    assert resp.status_code == 200
    assert resp.data == b'FAILED-BYTES'


def test_local_permanently_failed_serves_original():
    """status=permanently_failed + original present -> 200 original instead
    of the 410 Gone (explicitly called out by the controller ruling)."""
    slug, ep = 'locperm', 's01e01'
    _make_local_podcast_and_episode(slug, ep, status='permanently_failed')
    _write_original(slug, ep, b'PERM-FAILED-BYTES')

    with patch('main_app.routes.get_feed_map',
               return_value={slug: {'in': f'local://{slug}', 'out': f'/{slug}'}}):
        with app.test_client() as c:
            resp = c.get(f'/episodes/{slug}/{ep}.mp3')

    assert resp.status_code == 200
    assert resp.data == b'PERM-FAILED-BYTES'


def test_local_original_missing_falls_back_to_current_behavior():
    """(4) No retained original -> unchanged current behavior (503 queued
    response), even though the feed is local."""
    slug, ep = 'locmissing', 's01e01'
    _make_local_podcast_and_episode(slug, ep, status='discovered')

    with patch('main_app.processing.start_background_processing',
               return_value=(False, 'queue_busy:other:ep')) as mock_start, \
         patch('main_app.routes.status_service') as mock_status, \
         patch('main_app.routes.get_feed_map',
               return_value={slug: {'in': f'local://{slug}', 'out': f'/{slug}'}}):
        mock_status.get_queue_position.return_value = 1
        with app.test_client() as c:
            resp = c.get(f'/episodes/{slug}/{ep}.mp3')

    assert resp.status_code == 503
    mock_start.assert_called_once()


def test_subscribed_feed_queue_busy_still_503():
    """(5) Regression guard: a subscribed feed's byte-for-byte queue-busy
    behavior must be untouched by the local-original branch."""
    slug, ep = 'subqueue', 'a1b2c3d4e5f6'
    db.create_podcast(slug, 'https://example.com/sub/feed.xml', feed_type='subscribed')
    db.upsert_episode(slug, ep, original_url='https://example.com/sub/ep9.mp3',
                      status='discovered', title='Ep9')
    lookup = ({'id': ep, 'url': 'https://example.com/sub/ep9.mp3', 'title': 'Ep9',
               'description': None, 'artwork_url': None, 'published': None}, 'Sub Show')

    with patch('main_app.processing.start_background_processing',
               return_value=(False, 'queue_busy:other:ep')), \
         patch('main_app.routes._lookup_episode', return_value=lookup), \
         patch('main_app.routes.status_service') as mock_status, \
         patch('main_app.routes.get_feed_map',
               return_value={slug: {'in': 'https://example.com/sub/feed.xml',
                                     'out': f'/{slug}'}}):
        mock_status.get_queue_position.return_value = 1
        with app.test_client() as c:
            resp = c.get(f'/episodes/{slug}/{ep}.mp3')

    assert resp.status_code == 503


def test_local_range_request_on_original_returns_206():
    """(6) A Range request against the retained original gets a proper
    206 Partial Content response (conditional=True support)."""
    slug, ep = 'locrange', 's01e01'
    _make_local_podcast_and_episode(slug, ep, status='processing')
    _write_original(slug, ep, b'0123456789ABCDEFGHIJ')

    with patch('main_app.routes.get_feed_map',
               return_value={slug: {'in': f'local://{slug}', 'out': f'/{slug}'}}):
        with app.test_client() as c:
            resp = c.get(f'/episodes/{slug}/{ep}.mp3',
                         headers={'Range': 'bytes=0-4'})

    assert resp.status_code == 206
    assert resp.data == b'01234'
    assert resp.headers['Content-Range'] == 'bytes 0-4/20'


def test_local_processed_serves_processed_file_fast_path_untouched():
    """(7) PROCESSED status still serves the processed file, not the
    original -- the hot-path fast return is untouched."""
    slug, ep = 'locprocessed', 's01e01'
    # processed_version is only honored on UPDATE, not on the initial
    # INSERT (see database/episodes.py upsert_episode) -- create discovered
    # first, then transition to processed.
    _make_local_podcast_and_episode(slug, ep, status='discovered')
    db.upsert_episode(slug, ep, status='processed', processed_version=1)
    _write_original(slug, ep, b'ORIGINAL-SHOULD-NOT-BE-SERVED')
    processed_path = storage.get_episode_path(slug, ep, version=1)
    processed_path.parent.mkdir(parents=True, exist_ok=True)
    processed_path.write_bytes(b'PROCESSED-BYTES')

    with patch('main_app.routes.get_feed_map',
               return_value={slug: {'in': f'local://{slug}', 'out': f'/{slug}'}}):
        with app.test_client() as c:
            resp = c.get(f'/episodes/{slug}/{ep}.mp3')

    assert resp.status_code == 200
    assert resp.data == b'PROCESSED-BYTES'
