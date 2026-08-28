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
