"""Chapter trim match skips transcription and detection for one episode (issue 1060)."""
import os
import tempfile
from unittest.mock import MagicMock, patch

import pytest

os.environ.setdefault('MINUSPOD_DATA_DIR', tempfile.mkdtemp(prefix='chapter_skip_test_'))
os.environ.setdefault('SECRET_KEY', 'test-secret')

from main_app.pipeline import detect, publish, signals, transcribe  # noqa: E402
from main_app.pipeline import recut as recut_stage  # noqa: E402
from tests.unit.pipeline_run_support import _row, _run_pipeline  # noqa: E402

SEGMENTS = [{'start': 0.0, 'end': 5.0, 'text': 'hello'}]
ID3_CHAPTERS = [{'start': 1.0, 'title': 'Opening'}, {'start': 1200.0, 'title': 'Listener Donations'},
                {'start': 1500.0, 'title': 'Main Topic'}]
JSON_CHAPTERS = [{'startTime': 1, 'title': 'Opening'}, {'startTime': 1200, 'title': 'Listener Donations'},
                 {'startTime': 1500, 'title': 'Main Topic'}]
PODCAST = {'description_trim_pattern': '*Donations*', 'description_trim_mode': 'to_next',
           'skip_transcription': 1}


def _transcribe(id3=None, fetched=None, url='https://example.com/ch.json', podcast=PODCAST,
                chapter_trim_skip=True, force=False, actions=None):
    """Run the real download stage with the chapter sources and Whisper stubbed."""
    outcome = {}
    fetch = MagicMock(return_value=fetched)
    with patch.object(transcribe, 'db') as tdb, patch.object(transcribe, 'storage') as tstorage, \
         patch.object(transcribe, 'transcriber') as whisper, \
         patch.object(transcribe, '_resolve_audio_path', return_value='/tmp/a.mp3'), \
         patch.object(transcribe, '_apply_transcript_corrections'), \
         patch.object(transcribe, '_repair_transcript', side_effect=lambda *a, **k: (a[3], [], [])), \
         patch.object(transcribe, 'get_feed_language_override', return_value=None), \
         patch.object(publish, 'db') as pdb, patch.object(publish, 'probe_chapters', return_value=id3), \
         patch.object(publish, 'fetch_upstream_chapters', fetch), \
         patch.object(publish, 'get_audio_duration', return_value=3200.0):
        tdb.get_episode.return_value = {'upstream_chapters_url': url}
        tdb.get_original_segments.return_value = None
        tstorage.get_transcript.return_value = None
        pdb.resolve_segment_actions.return_value = actions or {'outro': 'remove'}
        pdb.resolve_keep_original_audio.return_value = False
        whisper.transcribe_chunked.return_value = SEGMENTS
        whisper.segments_to_text.return_value = 'hello'
        path, segments = transcribe._download_and_transcribe(
            's', 'e', 'https://example.com/e.mp3', podcast=podcast, outcome=outcome,
            chapter_trim_skip=chapter_trim_skip, force_transcription=force)
    return path, segments, outcome, whisper, fetch, tdb


def test_id3_chapter_match_skips_whisper_with_original_timeline_marker():
    path, segments, outcome, whisper, fetch, _ = _transcribe(id3=ID3_CHAPTERS)
    whisper.transcribe_chunked.assert_not_called()
    fetch.assert_not_called()
    assert (path, segments) == ('/tmp/a.mp3', [])
    marker = outcome['chapter_trim_marker']
    assert (marker['start'], marker['end']) == (1200.0, 1500.0)
    assert marker['detection_stage'] == 'description_chapter' and marker['source'] == 'upstream_chapter'


def test_json_chapter_match_uses_to_end_span_and_ignores_unretained_original():
    podcast = {**PODCAST, 'description_trim_mode': 'to_end'}
    _, segments, outcome, whisper, fetch, _ = _transcribe(id3=[], fetched=JSON_CHAPTERS, podcast=podcast)
    whisper.transcribe_chunked.assert_not_called()
    fetch.assert_called_once()
    assert segments == []
    assert (outcome['chapter_trim_marker']['start'], outcome['chapter_trim_marker']['end']) == (1200.0, 3200.0)


@pytest.mark.parametrize('kwargs', [
    {'id3': []},
    {'id3': None},
    {'id3': [], 'url': None},
    {'id3': [], 'fetched': None},
    {'id3': [], 'fetched': [{'startTime': 1, 'title': 'Only one'}]},
    {'id3': [{'start': 1.0, 'title': 'Opening'}, {'start': 1500.0, 'title': 'Main Topic'}]},
    {'id3': ID3_CHAPTERS, 'chapter_trim_skip': False},
    {'id3': ID3_CHAPTERS, 'podcast': {**PODCAST, 'description_trim_pattern': None}},
])
def test_no_chapters_or_no_title_match_transcribes_as_usual(kwargs):
    _, segments, outcome, whisper, _, _ = _transcribe(**kwargs)
    whisper.transcribe_chunked.assert_called_once()
    assert segments == SEGMENTS
    assert 'chapter_trim_marker' not in outcome


@pytest.mark.parametrize('action', ['keep', 'mark'])
def test_non_cutting_outro_action_transcribes_as_usual(action):
    _, segments, outcome, whisper, _, _ = _transcribe(id3=ID3_CHAPTERS, actions={'outro': action})
    whisper.transcribe_chunked.assert_called_once()
    assert segments == SEGMENTS
    assert 'chapter_trim_marker' not in outcome


def test_json_fetch_failure_means_normal_processing():
    _, segments, outcome, whisper, _, _ = _transcribe(id3=[], fetched=None)
    whisper.transcribe_chunked.assert_called_once()
    assert 'chapter_trim_marker' not in outcome


def test_forced_rerun_clears_stale_details_on_match():
    *_, tdb = _transcribe(id3=ID3_CHAPTERS, force=True)
    tdb.clear_episode_details.assert_called_once_with('s', 'e')


def test_saved_transcript_is_reused_without_a_chapter_check():
    with patch.object(publish, 'pre_cut_chapter_trim_marker') as probe, \
         patch.object(transcribe, 'db') as tdb, patch.object(transcribe, 'storage') as tstorage, \
         patch.object(transcribe, '_resolve_audio_path', return_value='/tmp/a.mp3'), \
         patch.object(transcribe, '_repair_transcript', side_effect=lambda *a, **k: (a[3], [], [])), \
         patch.object(transcribe, 'get_feed_language_override', return_value=None):
        tdb.get_episode.return_value = {}
        tdb.get_original_segments.return_value = SEGMENTS
        tdb.get_repair_holes.return_value = []
        tstorage.get_transcript.return_value = 'hello'
        _, segments = transcribe._download_and_transcribe(
            's', 'e', 'https://example.com/e.mp3', podcast=PODCAST, chapter_trim_skip=True)
    probe.assert_not_called()
    assert segments == SEGMENTS


def _marker_effect(marker):
    def effect(*args, **kwargs):
        kwargs['outcome']['chapter_trim_marker'] = marker
        return '/tmp/mode.mp3', []
    return effect


MARKER = {'start': 1200.0, 'end': 1500.0, 'category': 'outro', 'confidence': 1.0,
          'detection_stage': 'description_chapter', 'sponsor': None,
          'reason': 'Chapter: Listener Donations', 'source': 'upstream_chapter',
          'trim_mode': 'to_next'}


def _feed_row(mode=None, **extra):
    return {**_row(mode=mode), 'description_trim_pattern': '*Donations*',
            'skip_transcription': 1, **extra}


def test_matched_episode_runs_without_llm_phases_and_cue_only_extras():
    with patch.object(recut_stage, '_recut_episode') as recut, \
         patch.object(signals, '_run_transcript_diff') as transcript_diff:
        m = _run_pipeline(_feed_row(), enable_ad_review=True, download_effect=_marker_effect(MARKER))
    assert m['result'] is True
    recut.assert_not_called()
    assert m['dat'].call_args.kwargs['chapter_trim_skip'] is True
    for stage in (m['detect'], m['analyze'], m['diff_fetch'], transcript_diff):
        stage.assert_not_called()
    assert m['refine'].call_args.args[2] == []
    refine = m['refine'].call_args.kwargs
    assert refine['chapter_marker'] == MARKER
    assert refine['apply_heuristic_rolls'] is False and refine['cue_only_safety'] is None
    m['reviewer'].assert_not_called()
    assert m['verify'].call_args.kwargs['skip_verification'] is True
    m['db'].reserve_provider_spend.assert_not_called()
    stats = m['finalize'].call_args.kwargs['run_stats']
    assert stats['transcription_skipped'] is True and stats['verification_skipped'] is True
    assert stats['audio_analysis_skipped'] is True
    assert 'cue_only' not in stats


def test_unmatched_episode_is_a_normal_run():
    m = _run_pipeline(_feed_row(), enable_ad_review=True)
    assert m['detect'].call_args.kwargs['skip_llm'] is False
    assert m['refine'].call_args.kwargs['chapter_marker'] is None
    assert m['refine'].call_args.kwargs['apply_heuristic_rolls'] is True
    m['reviewer'].assert_called_once()
    m['db'].reserve_provider_spend.assert_called_once()


@pytest.mark.parametrize('row,expected', [
    (_feed_row(), True),
    (_feed_row(mode='keep_content'), True),
    (_feed_row(description_trim_pattern=None), False),
    (_feed_row(skip_transcription=0), False),
    (_feed_row(skip_ad_detection=1), False),
    (_feed_row(mode='cue_only'), False),
])
def test_chapter_trim_skip_is_offered_only_for_eligible_feeds(row, expected):
    m = _run_pipeline(row)
    assert m['dat'].call_args.kwargs['chapter_trim_skip'] is expected


def test_cue_only_skip_transcription_is_unchanged():
    m = _run_pipeline(_feed_row(mode='cue_only'))
    assert m['dat'].call_args.kwargs['skip_transcription'] is True
    assert m['detect'].call_args.kwargs['force_create_from_pairs'] is True
    assert m['refine'].call_args.kwargs['chapter_marker'] is None


def test_marker_is_force_accepted_before_the_cut_and_never_recut_or_duplicated():
    with patch.object(detect, '_validate_repeat_twins', return_value=([], [], 0, 0)), \
         patch.object(detect, 'storage'), patch.object(detect, 'db'):
        to_remove, validated = detect._refine_and_validate(
            's', 'e', [], [], '/tmp/a.mp3', 'desc', 3200.0, 0.8, 'Pod',
            corrections=([], []), apply_heuristic_rolls=False, chapter_marker=dict(MARKER))
    cut = to_remove[0]
    assert (cut['start'], cut['end']) == (1200.0, 1500.0) and cut['was_cut'] is True
    assert validated[0]['validation']['decision'] == 'ACCEPT'
    assert publish.pending_chapter_trim(validated) is False
    # The post-cut asset step sees the same chapter again and must not queue a second marker.
    with patch.object(publish, 'db', MagicMock(resolve_keep_original_audio=lambda *a: True)):
        assert publish._queue_chapter_trim(
            's', 'e', JSON_CHAPTERS, PODCAST, [], 0.0, 3000.0, 3200.0, validated,
            'upstream_chapter', original_timeline=True) is False
    assert len(validated) == 1
