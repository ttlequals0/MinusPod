"""Pass-through mode (#521) and the feed website link.

Pass-through feeds serve episodes untouched; the website link is the channel-level RSS <link> captured at refresh.
"""
import os
import sys
import tempfile

os.environ.setdefault('MINUSPOD_DATA_DIR', tempfile.mkdtemp(prefix='passthrough_test_'))
os.environ.setdefault('SECRET_KEY', 'test-secret')

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))

from unittest.mock import MagicMock, patch

import main_app.feeds as feeds_mod
import main_app.processing as processing
from main_app.pipeline import common, transcribe, failure, recut
from main_app.pipeline import finalize as finalize_stage
from main_app.pipeline.transcribe import CDN_REFUSED_PREFIX


def _run_passthrough(download_result='/tmp/pt.mp3', duration=3305.7,
                     codec='mp3', codec_seq=None, retained=False, podcast=None,
                     episode_data=None, availability=(True, None), real_import=False):
    # codec_seq lets a test return a different codec on each probe call (the
    # pass-through path retries once when the first probe yields None); a plain
    # codec value is returned on every call.
    codec_effect = list(codec_seq) if codec_seq is not None else (lambda *a, **k: codec)
    with patch.object(processing, 'db') as db, \
         patch.object(processing, 'transcriber') as transcriber, \
         patch.object(processing, 'audio_processor') as audio_processor, \
         patch.object(processing, 'storage') as storage, \
         patch.object(processing, 'status_service'), \
         patch.object(recut, 'get_audio_codec', side_effect=codec_effect), \
         patch.object(recut.passthrough_assets, 'import_upstream_assets',
                      **({'wraps': recut.passthrough_assets.import_upstream_assets}
                         if real_import else
                         {'return_value': {'upstream_chapters': 3,
                                           'upstream_transcript': 'timed'}})) as importer, \
         patch.object(finalize_stage, '_finalize_episode') as finalize, \
         patch.object(common, '_copy_retained_original_to_temp',
                      return_value='/tmp/retained.mp3') as copy_retained, \
         patch.object(processing.shutil, 'move') as move, \
         patch.object(processing.os, 'unlink'), \
         patch.object(processing.os.path, 'exists', return_value=retained):
        transcriber.check_audio_availability.side_effect = (
            availability if callable(availability) else (lambda *a, **k: availability))
        transcriber.download_audio.return_value = download_result
        audio_processor.get_audio_duration.return_value = duration
        storage.get_episode_path.return_value = '/data/pt/episodes/ep1.mp3'
        result = recut._passthrough_episode(
            'pt-feed', 'ep1', 'https://example.com/ep1.mp3', 'Episode One',
            'PT Feed', None, None, None, start_time=0.0,
            episode_data=episode_data or {'processed_version': 0}, podcast=podcast)
    _run_passthrough.importer = importer
    return result, transcriber, finalize, move, db, copy_retained


class TestPassthroughEpisode:
    def test_downloads_and_finalizes_untouched(self):
        result, transcriber, finalize, move, db, _ = _run_passthrough(
            podcast={'download_user_agent_override': 'Feed/2.0'})

        assert result is True
        transcriber.check_audio_availability.assert_called_once_with(
            'https://example.com/ep1.mp3', user_agent='Feed/2.0')
        transcriber.download_audio.assert_called_once_with(
            'https://example.com/ep1.mp3', user_agent='Feed/2.0')
        transcriber.transcribe_chunked.assert_not_called()
        move.assert_called_once_with('/tmp/pt.mp3', '/data/pt/episodes/ep1.mp3')
        # Assets from any earlier interrupted run must not be served next
        # to the untouched audio.
        db.clear_episode_ad_data.assert_called_once_with('pt-feed', 'ep1')
        kwargs = finalize.call_args.kwargs
        assert kwargs['pass1_cut_count'] == 0
        assert kwargs['verification_count'] == 0
        assert kwargs['original_duration'] == 3305.7
        assert kwargs['new_duration'] == 3305.7
        assert kwargs['run_stats'] == {'mode': 'passthrough',
                                       'downloaded_duration': 3305.7,
                                       'upstream_chapters': 3,
                                       'upstream_transcript': 'timed'}
        _run_passthrough.importer.assert_called_once()
        assert _run_passthrough.importer.call_args.args[:2] == ('pt-feed', 'ep1')

    def test_settled_user_agent_reaches_upstream_fetches(self):
        episode = {'processed_version': 0,
                   'upstream_chapters_url': 'https://example.com/ch.json',
                   'upstream_transcript_url': 'https://example.com/t.vtt',
                   'upstream_transcript_type': 'text/vtt'}

        def availability(url, user_agent=None):
            return (True, None) if user_agent == 'Feed/9' else (False, CDN_REFUSED_PREFIX)
        with patch('main_app.pipeline.transcribe.feed_user_agent', return_value='Feed/9'), \
             patch('main_app.pipeline.passthrough_assets.feed_user_agent', return_value='Feed/9'), \
             patch.object(recut.passthrough_assets, 'fetch_upstream_chapters',
                          return_value=[]) as chapters, \
             patch.object(recut.passthrough_assets, 'fetch_upstream_transcript',
                          return_value=None) as transcript:
            result, transcriber, *_ = _run_passthrough(
                podcast={'download_user_agent_override': 'Refused/1'},
                episode_data=episode, availability=availability, real_import=True)

        assert result is True
        transcriber.download_audio.assert_called_once_with(
            'https://example.com/ep1.mp3', user_agent='Feed/9')
        assert _run_passthrough.importer.call_args.kwargs['outcome'] == {
            'download_user_agent': 'Refused/1', 'fallback_user_agent': 'Feed/9'}
        chapters.assert_called_once_with(
            'https://example.com/ch.json', user_agent='Feed/9', fallback_user_agent='Feed/9')
        transcript.assert_called_once_with(
            'https://example.com/t.vtt', 'text/vtt', user_agent='Feed/9',
            fallback_user_agent='Feed/9')

    def test_retained_original_reused_instead_of_download(self):
        result, transcriber, finalize, move, _, copy_retained = \
            _run_passthrough(retained=True)

        assert result is True
        copy_retained.assert_called_once()
        transcriber.download_audio.assert_not_called()
        move.assert_called_once_with('/tmp/retained.mp3',
                                     '/data/pt/episodes/ep1.mp3')

    def test_non_mp3_enclosure_is_converted(self):
        with patch.object(processing, 'AudioProcessor'), \
             patch.object(recut, 'AudioProcessor') as ap_cls:
            ap_cls.return_value.convert_to_mp3.return_value = '/tmp/pt.converted.mp3'
            result, _, finalize, move, _, _ = _run_passthrough(codec='aac')

        assert result is True
        ap_cls.return_value.convert_to_mp3.assert_called_once_with('/tmp/pt.mp3')
        move.assert_called_once_with('/tmp/pt.converted.mp3',
                                     '/data/pt/episodes/ep1.mp3')

    def test_none_codec_is_converted(self):
        # ffprobe could not determine the codec (returns None). The output path is forced to .mp3, so the
        # bytes must be converted rather than moved through raw, or a non-mp3 enclosure is served as audio/mpeg.
        with patch.object(processing, 'AudioProcessor'), \
             patch.object(recut, 'AudioProcessor') as ap_cls:
            ap_cls.return_value.convert_to_mp3.return_value = '/tmp/pt.converted.mp3'
            result, _, _, move, _, _ = _run_passthrough(codec=None)

        assert result is True
        ap_cls.return_value.convert_to_mp3.assert_called_once_with('/tmp/pt.mp3')
        move.assert_called_once_with('/tmp/pt.converted.mp3',
                                     '/data/pt/episodes/ep1.mp3')

    def test_transient_codec_miss_passes_through_untouched(self):
        # First probe returns None (transient ffprobe miss), retry sees 'mp3'.
        # The file must be served untouched, not re-encoded.
        with patch.object(processing, 'AudioProcessor'), \
             patch.object(recut, 'AudioProcessor') as ap_cls:
            result, _, _, move, _, _ = _run_passthrough(codec_seq=[None, 'mp3'])

        assert result is True
        ap_cls.return_value.convert_to_mp3.assert_not_called()
        move.assert_called_once_with('/tmp/pt.mp3', '/data/pt/episodes/ep1.mp3')

    def test_unplayable_download_fails_loudly(self):
        with patch.object(failure, '_handle_processing_failure') as fail:
            result, _, finalize, _, _, _ = _run_passthrough(duration=None)

        assert result is False
        finalize.assert_not_called()
        fail.assert_called_once()

    def test_download_failure_goes_to_failure_handler(self):
        with patch.object(processing, 'db'), \
             patch.object(processing, 'transcriber') as transcriber, \
             patch.object(processing, 'status_service'), \
             patch.object(processing, 'storage'), \
             patch.object(processing.os.path, 'exists', return_value=False), \
             patch.object(failure, '_handle_processing_failure') as fail:
            transcriber.check_audio_availability.return_value = (True, None)
            transcriber.download_audio.return_value = None
            result = recut._passthrough_episode(
                'pt-feed', 'ep1', 'https://example.com/ep1.mp3', 'Episode One',
                'PT Feed', None, None, None, start_time=0.0, episode_data=None)

        assert result is False
        fail.assert_called_once()


class TestProcessEpisodeBranch:
    def test_passthrough_flag_routes_to_passthrough(self):
        with patch.object(processing, 'db') as db, \
             patch.object(recut, '_passthrough_episode') as pt, \
             patch.object(processing, 'start_episode_token_tracking'):
            db.get_episode.return_value = {}
            db.get_podcast_by_slug.return_value = {'id': 1,
                                                   'passthrough_enabled': 1}
            db.get_podcast_row.return_value = db.get_podcast_by_slug.return_value
            pt.return_value = True
            result = processing.process_episode(
                'pt-feed', 'ep1', 'https://example.com/ep1.mp3')

        assert result is True
        pt.assert_called_once()

    def test_flag_off_does_not_route_to_passthrough(self):
        with patch.object(processing, 'db') as db, \
             patch.object(recut, '_passthrough_episode') as pt, \
             patch.object(processing, 'start_episode_token_tracking'), \
             patch.object(transcribe, '_download_and_transcribe',
                          side_effect=RuntimeError('stop here')), \
             patch.object(failure, '_handle_processing_failure'), \
         patch.object(processing, 'status_service'):
            db.get_episode.return_value = {}
            db.get_podcast_by_slug.return_value = {'id': 1,
                                                   'passthrough_enabled': None}
            db.get_podcast_row.return_value = db.get_podcast_by_slug.return_value
            processing.process_episode(
                'pt-feed', 'ep1', 'https://example.com/ep1.mp3')

        pt.assert_not_called()


class TestWebsiteLinkCapture:
    @patch('main_app.feeds.pattern_service')
    @patch('main_app.feeds.status_service')
    @patch('main_app.feeds.storage')
    @patch('main_app.feeds.rss_parser')
    @patch('main_app.feeds.db')
    def _refresh_with_link(self, link, db, rss_parser, storage,
                           status_service, pattern_service):
        db.get_podcast_by_slug.return_value = {
            'id': 1, 'etag': None, 'last_modified_header': None,
            'artwork_cached': True,
        }
        db.get_podcast_row.return_value = db.get_podcast_by_slug.return_value
        db.bulk_upsert_discovered_episodes.return_value = 0
        db.is_auto_process_enabled_for_podcast.return_value = False
        rss_parser.fetch_feed_conditional.return_value = (b'<rss/>', None, None)
        parsed = MagicMock()
        parsed.feed = {'title': 'Show', 'description': '', 'link': link}
        parsed.entries = [1]
        parsed.bozo = False
        rss_parser.parse_feed.return_value = parsed
        rss_parser.find_channel_element.return_value = None
        rss_parser.resolve_channel_fields.return_value = {
            'title': 'Show', 'description': '', 'link': link, 'language': 'en',
            'author': '', 'categories': []}
        rss_parser.extract_podcast_artwork_url.return_value = None
        rss_parser.extract_podping_declaration.return_value = {
            'uses_podping': None, 'hive_accounts': []}
        rss_parser.extract_episodes.return_value = []
        with patch('main_app.feeds._build_and_save_served_rss'):
            feeds_mod.refresh_rss_feed('show', 'https://example.com/f.xml',
                                       force=True)
        # The metadata update is the update_podcast call carrying title.
        for call in db.update_podcast.call_args_list:
            if 'website_url' in call.kwargs:
                return call.kwargs['website_url']
        raise AssertionError('metadata update_podcast call not found')

    def test_channel_link_is_captured(self):
        assert self._refresh_with_link('https://www.example.com/') == \
            'https://www.example.com/'

    def test_non_http_link_is_dropped(self):
        assert self._refresh_with_link('javascript:alert(1)') is None

    def test_missing_link_stored_as_none(self):
        assert self._refresh_with_link('') is None
