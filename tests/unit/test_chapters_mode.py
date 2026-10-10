"""Unit tests for the per-feed chapters mode (issue #560).

'auto' (default) preserves publisher-embedded chapters when enough of them
survive the cut: it probes the PROCESSED file, which the ffmpeg cut step has
already remapped onto the cut timeline (audio_processor.py), instead of
generating new ones with the chapter LLM. 'generate' keeps the pre-#560
behavior unconditionally; 'off' skips the chapter step entirely.
"""
from datetime import datetime, timedelta, timezone
import json
import shutil
from unittest.mock import MagicMock, patch

import pytest

from tests.app_bootstrap import bootstrap
from tests.unit.test_id3_chapter_preservation import _long_fixture

_test_data_dir = bootstrap('chapters_mode_test_')

import chapters_generator
from ad_chapters import ID3_CHAPTER_SOURCE_KEY
from config import (
    CHAPTERS_MODE_AUTO,
    CHAPTERS_MODE_GENERATE,
    CHAPTERS_MODE_OFF,
    resolve_chapters_mode,
)
from llm_client import ProviderAccountChangedError, ProviderRateLimitedError
from id3_chapters import ChapterTagError
from id3_chapters import chapter_frames, read_tag
from audio_processor import AudioProcessor
from storage import Storage
from utils.audio import get_audio_duration
from cancel import ProcessingCancelled, ProcessingOwnershipLost
from main_app import processing
import rate_limit_hold
from rate_limit_hold import hold_message
from utils.time import utc_now_iso

MARKED_SPONSOR = [{'start': 900.0, 'end': 960.0, 'action_applied': 'mark',
                 'category': 'sponsor', 'confidence': 0.95, 'was_cut': False}]


# ---------- resolve_chapters_mode ----------

def test_missing_row_resolves_auto():
    assert resolve_chapters_mode({}) == CHAPTERS_MODE_AUTO
    assert resolve_chapters_mode(None) == CHAPTERS_MODE_AUTO


def test_null_column_resolves_auto():
    assert resolve_chapters_mode({'chapters_mode': None}) == CHAPTERS_MODE_AUTO


def test_explicit_values_pass_through():
    assert resolve_chapters_mode({'chapters_mode': 'generate'}) == CHAPTERS_MODE_GENERATE
    assert resolve_chapters_mode({'chapters_mode': 'off'}) == CHAPTERS_MODE_OFF
    assert resolve_chapters_mode({'chapters_mode': 'auto'}) == CHAPTERS_MODE_AUTO


def test_invalid_value_falls_back_to_auto():
    assert resolve_chapters_mode({'chapters_mode': 'bogus'}) == CHAPTERS_MODE_AUTO


def test_null_feed_mode_uses_global_mode():
    db = MagicMock()
    db.get_setting.return_value = CHAPTERS_MODE_OFF
    assert resolve_chapters_mode({'chapters_mode': None}, db=db) == CHAPTERS_MODE_OFF
    assert resolve_chapters_mode({'chapters_mode': CHAPTERS_MODE_GENERATE}, db=db) == CHAPTERS_MODE_GENERATE


# ---------- _generate_assets chapter block ----------

def _db(chapters_mode=None, chapters_enabled=None, upstream_chapters_url=None):
    db = MagicMock()

    def get_setting(key):
        if key == 'chapters_enabled':
            return chapters_enabled
        if key == 'vtt_transcripts_enabled':
            return 'false'
        return None

    db.get_setting.side_effect = get_setting
    db.get_podcast_by_slug.return_value = {'chapters_mode': chapters_mode}
    db.get_episode.return_value = {'upstream_chapters_url': upstream_chapters_url}
    return db


def _run(monkeypatch, db, publisher_chapters, generator_chapters=None, podcast_row=None,
         original_duration=None, fetch_return=None, run_stats=None, markers=None,
         generator_error=None, generator_setup_error=None):
    """Invoke the real _generate_assets with all IO seams mocked, returning
    (storage_mock, probe_mock, generator_class_mock, embed_mock, fetch_mock)."""
    storage_mock = MagicMock()
    probe_mock = MagicMock(return_value=publisher_chapters)
    embed_mock = MagicMock()
    fetch_mock = MagicMock(return_value=fetch_return)
    transcript_gen_class = MagicMock()
    transcript_gen_class.return_value.compute_final_segments.return_value = []
    transcript_gen_class.return_value.generate_text.return_value = None

    generator_class = MagicMock()
    generator_class.side_effect = generator_setup_error
    generator_class.return_value.generate_chapters.side_effect = generator_error
    generator_class.return_value.generate_chapters.return_value = (
        generator_chapters if generator_chapters is not None
        else {'chapters': [{'startTime': 0, 'title': 'Generated'}]}
    )
    generator_class.return_value.chapters_degraded = False

    monkeypatch.setattr(processing, 'db', db)
    monkeypatch.setattr(processing, 'storage', storage_mock)
    monkeypatch.setattr(processing, 'probe_chapters', probe_mock)
    monkeypatch.setattr(processing, 'embed_chapters', embed_mock)
    monkeypatch.setattr(processing, 'fetch_upstream_chapters', fetch_mock)
    monkeypatch.setattr(processing, 'get_replacement_duration', lambda: 2.0)
    monkeypatch.setattr(rate_limit_hold, 'get_llm_usage_url', lambda _db: '')
    monkeypatch.setattr('transcript_generator.TranscriptGenerator', transcript_gen_class)
    monkeypatch.setattr(chapters_generator, 'ChaptersGenerator', generator_class)

    processing._generate_assets(
        'testslug', 'ep1', segments=[], all_cuts=[], episode_description='desc',
        podcast_name='Pod', episode_title='Title', regenerate_chapters=True,
        audio_path='/tmp/fake-processed.mp3', audio_duration=100.0,
        podcast_row=podcast_row, original_duration=original_duration,
        run_stats=run_stats, markers=markers,
    )
    return storage_mock, probe_mock, generator_class, embed_mock, fetch_mock


def test_auto_preserves_publisher_chapters_no_llm_no_embed(monkeypatch):
    publisher = [
        {'start': 0.0, 'end': 30.0, 'title': 'Intro'},
        {'start': 100.4, 'end': 200.0, 'title': ''},
    ]
    db = _db(chapters_mode='auto')
    storage_mock, probe_mock, generator_class, embed_mock, fetch_mock = _run(monkeypatch, db, publisher)

    probe_mock.assert_called_once_with('/tmp/fake-processed.mp3')
    generator_class.return_value.generate_chapters.assert_not_called()
    embed_mock.assert_not_called()
    fetch_mock.assert_not_called()
    db.save_processing_assets.assert_called_once_with(
        'testslug', 'ep1',
        {'final_segments': [], 'chapters': {'version': '1.2.0', 'chapters': [
            # startTime floors at 1, not 0 (some podcast apps require it).
            {'startTime': 1, 'title': 'Intro'},
            {'startTime': 100, 'title': 'Chapter 2'},
        ]}, 'applied_cuts': []},
    )


def test_auto_with_zero_publisher_chapters_falls_back_to_generate(monkeypatch):
    db = _db(chapters_mode='auto')
    storage_mock, probe_mock, generator_class, embed_mock, fetch_mock = _run(monkeypatch, db, [])

    generator_class.return_value.generate_chapters.assert_called_once()
    assert 'chapters' in db.save_processing_assets.call_args.args[2]


def test_generator_chapters_degraded_flag_propagates_to_run_stats(monkeypatch):
    """chapters_gen.chapters_degraded (set when topic detection fails or
    yields an unusable result) must reach run_stats so it survives into
    processing_stats_json, per the wiring in _generate_assets."""
    db = _db(chapters_mode='generate')
    storage_mock = MagicMock()
    transcript_gen_class = MagicMock()
    transcript_gen_class.return_value.compute_final_segments.return_value = []
    transcript_gen_class.return_value.generate_text.return_value = None

    generator_class = MagicMock()
    generator_class.return_value.generate_chapters.return_value = {
        'chapters': [{'startTime': 1, 'title': 'Introduction'}]
    }
    generator_class.return_value.chapters_degraded = True
    generator_class.return_value.chapters_degradation_reason = 'chapter topic detection failed'

    monkeypatch.setattr(processing, 'db', db)
    monkeypatch.setattr(processing, 'storage', storage_mock)
    monkeypatch.setattr(processing, 'probe_chapters', MagicMock(return_value=[]))
    monkeypatch.setattr(processing, 'embed_chapters', MagicMock())
    monkeypatch.setattr(processing, 'fetch_upstream_chapters', MagicMock(return_value=None))
    monkeypatch.setattr(processing, 'get_replacement_duration', lambda: 2.0)
    monkeypatch.setattr('transcript_generator.TranscriptGenerator', transcript_gen_class)
    monkeypatch.setattr(chapters_generator, 'ChaptersGenerator', generator_class)

    run_stats = {}
    processing._generate_assets(
        'testslug', 'ep1', segments=[], all_cuts=[], episode_description='desc',
        podcast_name='Pod', episode_title='Title', regenerate_chapters=True,
        audio_path='/tmp/fake-processed.mp3', audio_duration=100.0,
        run_stats=run_stats,
    )

    assert run_stats['chapters_degraded'] is True
    assert run_stats['chapters_degraded_reason'] == 'chapter topic detection failed'


RATE_LIMIT_ERROR = ProviderRateLimitedError('resets in 900s', retry_after_seconds=900.0)


def test_provider_rate_limit_holds_the_queue_and_still_publishes(monkeypatch):
    """A 429 in the chapter step must record the hold (#696) and let the run
    finish: the audio is already cut, so it publishes ad chapters only."""
    db = _db(chapters_mode='generate')
    run_stats = {}
    with patch.object(rate_limit_hold, 'fire_queue_held_event') as fire:
        storage_mock, _, _, _, _ = _run(monkeypatch, db, [], run_stats=run_stats,
                                        markers=MARKED_SPONSOR,
                                        generator_error=RATE_LIMIT_ERROR)

    # A fresh pause alerts once, the same rule the failure handler follows.
    assert fire.call_count == 1
    assert fire.call_args.kwargs['slug'] == 'testslug'
    assert fire.call_args.kwargs['podcast_name'] == 'Pod'
    held_until = dict(c.args for c in db.set_setting.call_args_list)['rate_limit_hold_until']
    assert held_until > utc_now_iso()
    assert run_stats['chapters_degraded'] is True
    assert run_stats['chapters_degraded_reason'] == hold_message(held_until, RATE_LIMIT_ERROR)
    # Ad chapters still publish: the cut audio must not go out without them.
    saved = db.save_processing_assets.call_args.args[2]['chapters']['chapters']
    # The resume entry falls past audio_duration, so only the ad entry lands.
    assert [ch['startTime'] for ch in saved] == [900]


def test_provider_rate_limit_under_an_active_hold_does_not_alert_again(monkeypatch):
    """A 429 landing inside an existing pause extends it at most; one pause,
    one alert."""
    active_until = (datetime.now(timezone.utc) + timedelta(hours=2)).strftime(
        '%Y-%m-%dT%H:%M:%SZ')
    db = _db(chapters_mode='generate')
    db.get_setting.side_effect = lambda key: {
        'chapters_enabled': None,
        'vtt_transcripts_enabled': 'false',
        'rate_limit_hold_until': active_until,
    }.get(key)

    run_stats = {}
    with patch.object(rate_limit_hold, 'fire_queue_held_event') as fire:
        _run(monkeypatch, db, [], run_stats=run_stats, markers=MARKED_SPONSOR,
             generator_error=RATE_LIMIT_ERROR)

    fire.assert_not_called()
    assert run_stats['chapters_degraded_reason'] == hold_message(active_until, RATE_LIMIT_ERROR)


@pytest.mark.parametrize('error', [
    ProviderAccountChangedError('account changed'),
    ProcessingCancelled(),
    ProcessingOwnershipLost(),
])
def test_chapter_run_control_errors_escape_generate_assets(monkeypatch, error):
    with pytest.raises(type(error)):
        _run(monkeypatch, _db(chapters_mode='generate'), [], generator_error=error)


def test_chapter_setup_failure_is_recorded_as_degraded(monkeypatch):
    run_stats = {}
    storage, _, generator, _, _ = _run(
        monkeypatch, _db(chapters_mode='generate'), [], run_stats=run_stats,
        generator_setup_error=RuntimeError('setup failed'))

    assert run_stats['chapters_degraded'] is True
    assert run_stats['chapters_degraded_reason'] == (
        'Chapter generation failed. Check Settings > AI Models and try again.')
    assert 'chapters' not in processing.db.save_processing_assets.call_args.args[2]
    generator.assert_called_once()


def test_auto_with_one_publisher_chapter_falls_back_to_generate(monkeypatch):
    db = _db(chapters_mode='auto')
    publisher = [{'start': 0.0, 'end': 30.0, 'title': 'Only One'}]
    storage_mock, probe_mock, generator_class, embed_mock, fetch_mock = _run(monkeypatch, db, publisher)

    generator_class.return_value.generate_chapters.assert_called_once()
    assert 'chapters' in db.save_processing_assets.call_args.args[2]


def test_auto_retains_one_rich_chapter_without_generation(monkeypatch):
    db = _db(chapters_mode='auto')
    chapter = {'start': 0.125, 'end': 0.625, 'title': 'Intro', '_id3_id': '6669727374',
               '_id3_end': 0.625, 'url': 'https://example.com/chapter'}
    _, _, generator, embed, _ = _run(monkeypatch, db, [chapter])
    generator.assert_not_called()
    embed.assert_not_called()
    published = db.save_processing_assets.call_args.args[2]
    assert published['chapters']['chapters'] == [
        {'startTime': 0.125, 'title': 'Intro', '_id3_id': '6669727374',
         '_id3_end': 0.625, 'url': 'https://example.com/chapter'}]
    assert published['applied_cuts'] == []


def test_source_tag_failure_publishes_no_asset_fields(monkeypatch):
    db = _db(chapters_mode='auto')
    storage = MagicMock()
    monkeypatch.setattr(processing, 'db', db)
    monkeypatch.setattr(processing, 'storage', storage)
    monkeypatch.setattr(processing, 'probe_chapters', MagicMock(side_effect=ChapterTagError('Unsupported chapter')))
    with pytest.raises(ChapterTagError, match='Unsupported chapter'):
        processing._generate_assets('testslug', 'ep1', [], [], '', 'Pod', 'Title',
                                    audio_path='/tmp/fake-processed.mp3', audio_duration=100)
    db.save_processing_assets.assert_not_called()
    storage.save_transcript_vtt.assert_not_called()
    storage.save_chapters_json.assert_not_called()


def _real_asset_storage(monkeypatch, temp_db):
    storage = object.__new__(Storage)
    storage._initialized = False
    Storage.__init__(storage, str(temp_db.data_dir))
    storage.db = temp_db
    monkeypatch.setattr(processing, 'db', temp_db)
    monkeypatch.setattr(processing, 'storage', storage)
    monkeypatch.setattr(processing, 'resolve_ad_chapter_config', lambda *args: None)
    temp_db.set_setting('chapters_enabled', 'true')
    temp_db.set_setting('vtt_transcripts_enabled', 'true')
    return storage


@pytest.fixture
def chapter_episode(temp_db, mock_episode):
    episode_id = 'a1b2c3d4e5f6'
    temp_db.get_connection().execute('UPDATE episodes SET episode_id = ? WHERE id = ?',
                                     (episode_id, mock_episode['id']))
    temp_db.get_connection().commit()
    return {**mock_episode, 'episode_id': episode_id}


@pytest.mark.skipif(not shutil.which('ffmpeg'), reason='ffmpeg is unavailable')
def test_recut_restores_previously_removed_chapter_artifacts_without_drift(monkeypatch, tmp_path, temp_db, chapter_episode):
    storage = _real_asset_storage(monkeypatch, temp_db)
    source, _ = _long_fixture(tmp_path, 4)
    slug, ep_id = chapter_episode['slug'], chapter_episode['episode_id']
    prior = {'version': '1.2.0', 'chapters': [
        {'startTime': 0.125, 'title': 'Intro', '_id3_id': b'first'.hex(), '_id3_end': 20},
        {'startTime': 21, 'title': 'Intro', '_id3_id': b'last'.hex(), '_id3_end': 31},
    ]}
    temp_db.save_processing_assets(slug, ep_id, {
        'chapters': prior, 'applied_cuts': [{'start': 20, 'end': 30, 'replacement_duration': 1}],
    })
    staging = tmp_path / 'recut.mp3'
    for previous_cuts in ([{'start': 20, 'end': 30, 'replacement_duration': 1}], []):
        shutil.copyfile(source, staging)
        processing._generate_assets(slug, ep_id, [], [], '', 'Pod', 'Episode',
                                    regenerate_chapters=False, audio_path=staging,
                                    audio_duration=get_audio_duration(str(staging)),
                                    previous_cuts=previous_cuts, original_duration=60)
        chapters = json.loads(temp_db.get_episode(slug, ep_id)['chapters_json'])['chapters']
        assert [ch['_id3_id'] for ch in chapters] == [b'first'.hex(), b'ad'.hex(), b'last'.hex()]
        assert [ch['startTime'] for ch in chapters] == [0.125, 21, 30]
        assert [ch['_id3_end'] for ch in chapters] == [20, 29, 40]
        assert chapters[0]['url'] == 'https://example.com/chapter'
        name = chapters[0]['img'].rsplit('/', 1)[1]
        assert storage.get_chapter_image(slug, ep_id, name) is not None
        assert set(chapter_frames(read_tag(staging))[0]) == {b'first', b'ad', b'last'}
        assert temp_db.get_applied_cuts(slug, ep_id) == []


@pytest.mark.skipif(not shutil.which('ffmpeg'), reason='ffmpeg is unavailable')
def test_failed_rich_recut_preserves_prior_audio_and_all_published_fields(monkeypatch, tmp_path, temp_db, chapter_episode):
    storage = _real_asset_storage(monkeypatch, temp_db)
    source, _ = _long_fixture(tmp_path, 3)
    slug, ep_id = chapter_episode['slug'], chapter_episode['episode_id']
    processing._generate_assets(slug, ep_id, [], [], '', 'Pod', 'Episode', audio_path=source,
                                audio_duration=60, podcast_row={'chapters_mode': 'auto'})
    conn = temp_db.get_connection()
    before = tuple(conn.execute('SELECT * FROM episode_details WHERE episode_id = ?', (chapter_episode['id'],)).fetchone())
    original_audio = source.read_bytes()
    chapter = json.loads(temp_db.get_episode(slug, ep_id)['chapters_json'])['chapters'][0]
    image = storage.get_chapter_image(slug, ep_id, chapter['img'].rsplit('/', 1)[1])
    staging = tmp_path / 'recut.mp3'
    shutil.copyfile(source, staging)
    save_image = MagicMock(wraps=storage.save_chapter_image)
    monkeypatch.setattr(storage, 'save_chapter_image', save_image)
    monkeypatch.setattr(processing, 'embed_chapters', lambda *args, **kwargs: False)
    with pytest.raises(ChapterTagError, match='could not be embedded'):
        processing._generate_assets(slug, ep_id, [], [], '', 'Pod', 'Episode',
                                    regenerate_chapters=False, audio_path=staging,
                                    audio_duration=60, previous_cuts=[], original_duration=60)
    assert tuple(conn.execute('SELECT * FROM episode_details WHERE episode_id = ?', (chapter_episode['id'],)).fetchone()) == before
    assert source.read_bytes() == original_audio
    assert staging.read_bytes() == original_audio
    save_image.assert_not_called()
    assert storage.get_chapter_image(slug, ep_id, chapter['img'].rsplit('/', 1)[1]) == image


@pytest.mark.skipif(not shutil.which('ffmpeg'), reason='ffmpeg is unavailable')
def test_recut_clears_artifacts_when_all_publisher_chapters_are_cut(monkeypatch, tmp_path, temp_db, chapter_episode):
    storage = _real_asset_storage(monkeypatch, temp_db)
    source, _ = _long_fixture(tmp_path, 4)
    slug, ep_id = chapter_episode['slug'], chapter_episode['episode_id']
    processing._generate_assets(slug, ep_id, [], [], '', 'Pod', 'Episode', audio_path=source,
                                audio_duration=60, podcast_row={'chapters_mode': 'auto'})
    output = tmp_path / 'all-cut.mp3'
    cuts = AudioProcessor().remove_ads(str(source), [{'start': 0, 'end': 60}], str(output))
    assert cuts
    processing._generate_assets(slug, ep_id, [], cuts, '', 'Pod', 'Episode',
                                regenerate_chapters=False, audio_path=output,
                                audio_duration=get_audio_duration(str(output)),
                                previous_cuts=[], original_duration=60)
    empty = json.loads(temp_db.get_episode(slug, ep_id)['chapters_json'])
    assert empty['chapters'] == []
    assert empty[ID3_CHAPTER_SOURCE_KEY] == 'id3'
    assert chapter_frames(read_tag(output))[0] == {}
    assert temp_db.get_applied_cuts(slug, ep_id) == [
        {key: cut[key] for key in ('start', 'end', 'replacement_duration')} for cut in cuts]
    processing._generate_assets(slug, ep_id, [], [], '', 'Pod', 'Episode',
                                regenerate_chapters=False, audio_path=source,
                                audio_duration=60, previous_cuts=cuts, original_duration=60)
    restored = json.loads(temp_db.get_episode(slug, ep_id)['chapters_json'])
    assert [ch['_id3_id'] for ch in restored['chapters']] == [b'first'.hex(), b'ad'.hex(), b'last'.hex()]
    assert restored['chapters'][0]['url'] == 'https://example.com/chapter'
    assert storage.get_chapter_image(slug, ep_id, restored['chapters'][0]['img'].rsplit('/', 1)[1]) is not None


@pytest.mark.skipif(not shutil.which('ffmpeg'), reason='ffmpeg is unavailable')
def test_recut_preserves_generated_choice_even_when_current_mode_is_auto(monkeypatch, tmp_path, temp_db, chapter_episode):
    _real_asset_storage(monkeypatch, temp_db)
    source, _ = _long_fixture(tmp_path, 4)
    slug, ep_id = chapter_episode['slug'], chapter_episode['episode_id']
    chosen = {'version': '1.2.0', 'chapters': [{'startTime': 5, 'title': 'Chosen topic'}]}
    temp_db.save_processing_assets(slug, ep_id, {'chapters': chosen, 'applied_cuts': []})
    processing._generate_assets(slug, ep_id, [], [], '', 'Pod', 'Episode',
                                regenerate_chapters=False, audio_path=source,
                                audio_duration=60, previous_cuts=[], original_duration=60,
                                podcast_row={'chapters_mode': 'auto'})
    assert json.loads(temp_db.get_episode(slug, ep_id)['chapters_json']) == chosen
    assert set(chapter_frames(read_tag(source))[0]) == {b'ch0'}


def test_unknown_empty_chapter_set_does_not_imply_publisher_choice(monkeypatch):
    pending = {}
    monkeypatch.setattr(processing.storage, 'get_chapters_json',
                        lambda *args: {'version': '1.2.0', 'chapters': []})
    probe = MagicMock()
    monkeypatch.setattr(processing, 'probe_chapters', probe)
    processing._remap_stored_chapters('testslug', 'ep1', [], 1, [], 60,
                                     audio_path='/tmp/fake-processed.mp3', audio_duration=60,
                                     pending_assets=pending)
    probe.assert_not_called()
    assert pending == {}


def test_auto_probe_failure_skips_chapter_step_without_generating(monkeypatch):
    # probe_chapters returns None (not []) on a transient ffprobe failure,
    # distinct from "definitively no chapters" (embedded_chapters.py). This
    # must NOT fall through to generate+embed, which would overwrite the ID3
    # frames the cut step already wrote correctly (issue #500's failure mode).
    db = _db(chapters_mode='auto')
    storage_mock, probe_mock, generator_class, embed_mock, fetch_mock = _run(monkeypatch, db, None)

    probe_mock.assert_called_once_with('/tmp/fake-processed.mp3')
    generator_class.return_value.generate_chapters.assert_not_called()
    assert 'chapters' not in db.save_processing_assets.call_args.args[2]
    embed_mock.assert_not_called()


def test_mode_off_skips_generator_and_save(monkeypatch):
    db = _db(chapters_mode='off')
    publisher = [
        {'start': 0.0, 'end': 30.0, 'title': 'Intro'},
        {'start': 100.0, 'end': 200.0, 'title': 'Body'},
    ]
    storage_mock, probe_mock, generator_class, embed_mock, fetch_mock = _run(monkeypatch, db, publisher)

    generator_class.return_value.generate_chapters.assert_not_called()
    assert 'chapters' not in db.save_processing_assets.call_args.args[2]
    probe_mock.assert_not_called()


def test_mode_generate_runs_generator_regardless_of_publisher_chapters(monkeypatch):
    db = _db(chapters_mode='generate')
    publisher = [
        {'start': 0.0, 'end': 30.0, 'title': 'Intro'},
        {'start': 100.0, 'end': 200.0, 'title': 'Body'},
    ]
    storage_mock, probe_mock, generator_class, embed_mock, fetch_mock = _run(monkeypatch, db, publisher)

    probe_mock.assert_not_called()
    fetch_mock.assert_not_called()
    generator_class.return_value.generate_chapters.assert_called_once()
    assert 'chapters' in db.save_processing_assets.call_args.args[2]
    embed_mock.assert_called_once()


def test_global_chapters_enabled_false_unchanged(monkeypatch):
    db = _db(chapters_mode='auto', chapters_enabled='false')
    publisher = [
        {'start': 0.0, 'end': 30.0, 'title': 'Intro'},
        {'start': 100.0, 'end': 200.0, 'title': 'Body'},
    ]
    storage_mock, probe_mock, generator_class, embed_mock, fetch_mock = _run(monkeypatch, db, publisher)

    probe_mock.assert_not_called()
    generator_class.return_value.generate_chapters.assert_not_called()
    assert 'chapters' not in db.save_processing_assets.call_args.args[2]


def test_passed_in_podcast_row_skips_refetch(monkeypatch):
    # get_podcast_by_slug would resolve 'auto' if it were consulted; passing
    # podcast_row explicitly must both win and avoid the extra DB call.
    db = _db(chapters_mode='auto')
    storage_mock, probe_mock, generator_class, embed_mock, fetch_mock = _run(
        monkeypatch, db, [], podcast_row={'chapters_mode': 'off'})

    db.get_podcast_by_slug.assert_not_called()
    generator_class.return_value.generate_chapters.assert_not_called()
    assert 'chapters' not in db.save_processing_assets.call_args.args[2]


# ---------- Upstream podcast:chapters JSON fetch (issue #560 follow-up) ----------
#
# Auto mode tries this source only after the embedded probe comes up short
# (fewer than MIN_PRESERVED_CHAPTERS survivors) and the episode row carries
# upstream_chapters_url. all_cuts=[] in this harness, so
# _remap_chapters_for_recut's previous_cuts=[]/new_cuts=[] projection is an
# identity map: a fetched chapter's startTime survives unchanged as long as
# it is not a degenerate sliver against its neighbor or original_duration.

def test_auto_fetches_upstream_when_embedded_short_and_url_present(monkeypatch):
    db = _db(chapters_mode='auto', upstream_chapters_url='https://pub.example.com/ch.json')
    db.get_podcast_by_slug.return_value['download_user_agent_override'] = 'Feed/2.0'
    fetched = [
        {'startTime': 5, 'title': 'Cold Open'},
        {'startTime': 50, 'img': 'https://cdn.example.com/2.jpg',
         'url': 'https://example.com/chapter2'},
    ]
    storage_mock, probe_mock, generator_class, embed_mock, fetch_mock = _run(
        monkeypatch, db, publisher_chapters=[], original_duration=100.0,
        fetch_return=fetched)

    fetch_mock.assert_called_once_with('https://pub.example.com/ch.json', user_agent='Feed/2.0')
    generator_class.return_value.generate_chapters.assert_not_called()
    db.save_processing_assets.assert_called_once_with(
        'testslug', 'ep1',
        {'final_segments': [], 'chapters': {'version': '1.2.0', 'chapters': [
            {'startTime': 5, 'title': 'Cold Open'},
            {'startTime': 50, 'img': 'https://cdn.example.com/2.jpg',
             'url': 'https://example.com/chapter2', 'title': 'Chapter 2'},
        ]}, 'applied_cuts': []},
    )
    embed_mock.assert_called_once_with(
        '/tmp/fake-processed.mp3',
        [
            {'startTime': 5, 'title': 'Cold Open'},
            {'startTime': 50, 'img': 'https://cdn.example.com/2.jpg',
             'url': 'https://example.com/chapter2', 'title': 'Chapter 2'},
        ],
        duration=100.0,
    )


def test_fetch_failure_falls_through_to_generator_not_a_skipped_run(monkeypatch):
    # None means "unknown" (network/parse/shape failure), not "no chapters".
    # Unlike a probe failure, this must NOT skip the run: a bad remote file
    # must not block chapters outright.
    db = _db(chapters_mode='auto', upstream_chapters_url='https://pub.example.com/ch.json')
    db.get_podcast_by_slug.return_value['download_user_agent_override'] = 'Feed/2.0'
    storage_mock, probe_mock, generator_class, embed_mock, fetch_mock = _run(
        monkeypatch, db, publisher_chapters=[], original_duration=100.0,
        fetch_return=None)

    fetch_mock.assert_called_once_with('https://pub.example.com/ch.json', user_agent='Feed/2.0')
    generator_class.return_value.generate_chapters.assert_called_once()
    assert 'chapters' in db.save_processing_assets.call_args.args[2]


def test_fetched_chapters_below_threshold_after_remap_falls_to_generator(monkeypatch):
    db = _db(chapters_mode='auto', upstream_chapters_url='https://pub.example.com/ch.json')
    fetched = [{'startTime': 5, 'title': 'Only One'}]
    storage_mock, probe_mock, generator_class, embed_mock, fetch_mock = _run(
        monkeypatch, db, publisher_chapters=[], original_duration=100.0,
        fetch_return=fetched)

    fetch_mock.assert_called_once()
    generator_class.return_value.generate_chapters.assert_called_once()
    assert 'chapters' in db.save_processing_assets.call_args.args[2]


# ---------- Segment-marker hints (ad-break boundary hints) ----------

def test_markers_reach_generator_as_segment_markers_kwarg(monkeypatch):
    """_generate_assets threads its markers argument through to
    ChaptersGenerator.generate_chapters as segment_markers: only reachable
    on the AI-generation branch (mode 'generate' here, no publisher/upstream
    chapters to preserve)."""
    db = _db(chapters_mode='generate')
    markers = [{'start': 10.0, 'end': 20.0, 'action_applied': 'remove', 'category': 'sponsor'}]
    storage_mock, probe_mock, generator_class, embed_mock, fetch_mock = _run(
        monkeypatch, db, publisher_chapters=[], markers=markers)

    generator_class.return_value.generate_chapters.assert_called_once()
    kwargs = generator_class.return_value.generate_chapters.call_args.kwargs
    assert kwargs['segment_markers'] is markers


def test_markers_not_passed_when_publisher_chapters_preserved(monkeypatch):
    """Auto mode preserving embedded publisher chapters never calls the
    generator at all, so markers can never reach a hints prompt on that
    path regardless of what is passed in."""
    db = _db(chapters_mode='auto')
    publisher = [
        {'start': 0.0, 'end': 30.0, 'title': 'Intro'},
        {'start': 100.0, 'end': 200.0, 'title': 'Body'},
    ]
    markers = [{'start': 10.0, 'end': 20.0, 'action_applied': 'remove', 'category': 'sponsor'}]
    storage_mock, probe_mock, generator_class, embed_mock, fetch_mock = _run(
        monkeypatch, db, publisher, markers=markers)

    generator_class.return_value.generate_chapters.assert_not_called()


def test_markers_not_passed_when_upstream_json_preserved(monkeypatch):
    """Same guarantee for the upstream podcast:chapters JSON preserve path."""
    db = _db(chapters_mode='auto', upstream_chapters_url='https://pub.example.com/ch.json')
    fetched = [
        {'startTime': 5, 'title': 'Cold Open'},
        {'startTime': 50, 'title': 'Segment Two'},
    ]
    markers = [{'start': 10.0, 'end': 20.0, 'action_applied': 'remove', 'category': 'sponsor'}]
    storage_mock, probe_mock, generator_class, embed_mock, fetch_mock = _run(
        monkeypatch, db, publisher_chapters=[], original_duration=100.0,
        fetch_return=fetched, markers=markers)

    fetch_mock.assert_called_once()
    generator_class.return_value.generate_chapters.assert_not_called()
