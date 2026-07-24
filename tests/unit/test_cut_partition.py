"""Tests for the segment-category cut partition (issue #565 Task 5): the
remove-vs-beep split for the markers that reach the final cut list.

'keep' is fully owned by Task 4's _partition_keep_ads and never reaches
this code under normal flow; this task only distinguishes remove from
beep on the markers that DO get cut, reusing the same per-episode
segment_actions map Task 4 already resolves.

Under test:
- main_app.processing._partition_cut_actions (pure partition helper)
- main_app.processing.process_episode integration: the audio-processor
  'beep' flag reaches AudioProcessor.process_episode, and action_applied
  is persisted on saved markers without leaking that transient flag
- audio_processor.AudioProcessor.remove_ads real-ffmpeg behavior: a
  'remove' span shortens the episode (today's fixed-length beep clip,
  unchanged), a 'beep' span pads that same clip with silence to the
  span's own length so total duration is preserved, and an empty cut
  list (a fully-kept episode) leaves the audio byte-identical
"""
import filecmp
import shutil
import subprocess
from contextlib import ExitStack
from unittest.mock import patch

import pytest

from tests.app_bootstrap import bootstrap

_test_data_dir = bootstrap(
    'cut_partition_test_', passphrase='cut-partition-test-passphrase')

import main_app.processing as processing
from audio_processor import AudioProcessor
from config import SEGMENT_CATEGORIES, DEFAULT_SEGMENT_ACTION

ALL_REMOVE = {cat: DEFAULT_SEGMENT_ACTION for cat in SEGMENT_CATEGORIES}


class TestPartitionCutActions:
    """Direct unit tests of the partition helper -- no ffmpeg, no pipeline."""

    def test_remove_and_beep_resolved_per_category(self):
        actions = dict(ALL_REMOVE, cross_promo='beep')
        sponsor_ad = {'start': 0.0, 'end': 10.0, 'category': 'sponsor'}
        promo_ad = {'start': 20.0, 'end': 30.0, 'category': 'cross_promo'}

        processing._partition_cut_actions([sponsor_ad, promo_ad], actions)

        assert sponsor_ad['action_applied'] == 'remove'
        assert promo_ad['action_applied'] == 'beep'

    def test_missing_category_resolves_as_sponsor(self):
        # Heuristic pre/post-roll and VAD-gap ads carry no 'category' key.
        actions = dict(ALL_REMOVE, sponsor='beep')
        heuristic_ad = {'start': 5.0, 'end': 15.0}

        processing._partition_cut_actions([heuristic_ad], actions)

        assert heuristic_ad['action_applied'] == 'beep'

    def test_unknown_category_value_falls_back_to_sponsor(self):
        actions = dict(ALL_REMOVE, sponsor='beep')
        ad = {'start': 0.0, 'end': 10.0, 'category': 'not-a-real-category'}

        processing._partition_cut_actions([ad], actions)

        assert ad['action_applied'] == 'beep'

    def test_keep_resolved_action_falls_back_to_remove(self):
        # Only reachable via a category-less marker added after Task 4's
        # keep partition ran, with sponsor itself set to keep; the segment
        # is already in the cut list this late, so it still cuts.
        actions = dict(ALL_REMOVE, sponsor='keep')
        ad = {'start': 0.0, 'end': 10.0, 'category': 'sponsor'}

        processing._partition_cut_actions([ad], actions)

        assert ad['action_applied'] == 'remove'

    def test_garbage_action_value_falls_back_to_default(self):
        actions = dict(ALL_REMOVE, sponsor='not-a-real-action')
        ad = {'start': 0.0, 'end': 10.0, 'category': 'sponsor'}

        processing._partition_cut_actions([ad], actions)

        assert ad['action_applied'] == DEFAULT_SEGMENT_ACTION

    def test_does_not_stamp_beep_key_on_the_marker(self):
        actions = dict(ALL_REMOVE, sponsor='beep')
        ad = {'start': 0.0, 'end': 10.0, 'category': 'sponsor'}

        processing._partition_cut_actions([ad], actions)

        assert ad['action_applied'] == 'beep'
        assert 'beep' not in ad

    def test_returns_same_list_object(self):
        ads = [{'start': 0.0, 'end': 10.0, 'category': 'sponsor'}]
        result = processing._partition_cut_actions(ads, ALL_REMOVE)
        assert result is ads


SEGMENTS = [{'start': 0.0, 'end': 5.0, 'text': 'hello'},
            {'start': 5.0, 'end': 10.0, 'text': 'world'}]


def _sponsor_ad():
    return {'start': 10.0, 'end': 20.0, 'category': 'sponsor',
           'confidence': 0.95, 'detection_stage': 'llm'}


def _cross_promo_ad():
    return {'start': 30.0, 'end': 40.0, 'category': 'cross_promo',
           'confidence': 0.95, 'detection_stage': 'llm'}


def _run_pipeline(first_pass_ads, segment_actions):
    """Drive process_episode's full pass-1 flow with every stage but the
    cut partition itself mocked out (mirrors test_keep_bypass.py's
    harness). Returns the recorded mocks so tests can inspect what the
    audio processor and storage were actually called with.
    """
    podcast_row = {'id': 1, 'slug': 'cut-feed', 'description': None,
                   'tags': None, 'dai_platform': None,
                   'passthrough_enabled': None, 'skip_ad_detection': None,
                   'detection_mode': None}

    def _fake_refine_and_validate(slug, episode_id, all_ads, *a, **k):
        for ad in all_ads:
            ad['was_cut'] = True
        return list(all_ads), list(all_ads)

    def _fake_run_ad_reviewer(slug, episode_id, podcast_id, ads_to_remove,
                              all_ads_with_validation, *a, **k):
        return ads_to_remove, all_ads_with_validation

    def _pass_through_ads(slug, episode_id, ads_to_remove, *a, **k):
        return ads_to_remove

    with ExitStack() as stack:
        p = lambda *a, **k: stack.enter_context(patch.object(*a, **k))
        db = p(processing, 'db')
        p(processing, 'status_service')
        storage = p(processing, 'storage')
        audio_processor_mod = p(processing, 'audio_processor')
        p(processing, 'start_episode_token_tracking')
        p(processing, 'get_available_memory_gb', return_value=None)
        p(processing, 'get_min_cut_confidence', return_value=0.8)
        p(processing, '_download_and_transcribe',
          return_value=('/tmp/cutpart.mp3', SEGMENTS))
        p(processing, '_run_differential_fetch', return_value=None)
        p(processing, '_run_audio_analysis', return_value=None)
        p(processing, 'load_positional_prior', return_value=None)
        p(processing, '_detect_ads_first_pass',
          return_value=(first_pass_ads, len(first_pass_ads), {}))
        p(processing, '_refine_and_validate',
          side_effect=_fake_refine_and_validate)
        p(processing, '_run_ad_reviewer', side_effect=_fake_run_ad_reviewer)
        p(processing, '_snap_terminal_starts', side_effect=_pass_through_ads)
        p(processing, '_complete_cut_tails', side_effect=_pass_through_ads)
        local_ap_cls = p(processing, 'AudioProcessor')
        p(processing, '_run_verification_pass',
          return_value=(0, [], [], [], '/tmp/cutpart-cut.mp3', 0, True, 0))
        p(processing, '_generate_assets')
        p(processing, '_finalize_episode')
        p(processing.shutil, 'move')
        p(processing.os, 'unlink')
        p(processing.os.path, 'exists', return_value=False)

        db.get_episode.return_value = {}
        db.get_podcast_by_slug.return_value = podcast_row
        db.get_setting.return_value = 'false'
        db.get_all_settings.return_value = {}
        db.resolve_segment_actions.return_value = segment_actions
        audio_processor_mod.get_audio_duration.return_value = 100.0
        local_ap = local_ap_cls.return_value
        local_ap.process_episode.side_effect = (
            lambda audio_path, segs: ('/tmp/cutpart-cut.mp3', list(segs)))
        local_ap.get_audio_duration.return_value = 100.0
        storage.get_episode_path.return_value = '/tmp/cutpart-final.mp3'

        result = processing.process_episode(
            'cut-feed', 'ep1', 'https://example.com/ep1.mp3')

    return {'result': result, 'db': db, 'storage': storage,
            'local_ap': local_ap}


class TestProcessEpisodePartitionIntegration:
    def test_beep_flag_reaches_audio_processor_and_markers_stamped(self):
        sponsor = _sponsor_ad()
        promo = _cross_promo_ad()
        segment_actions = dict(ALL_REMOVE, cross_promo='beep')

        m = _run_pipeline([sponsor, promo], segment_actions)

        assert m['result'] is True
        audio_segments = m['local_ap'].process_episode.call_args.args[1]
        by_span = {(s['start'], s['end']): s for s in audio_segments}
        assert by_span[(sponsor['start'], sponsor['end'])]['beep'] is False
        assert by_span[(promo['start'], promo['end'])]['beep'] is True

        # Persisted markers carry action_applied, never the transient
        # audio-processor-only 'beep' flag.
        saved = m['storage'].save_combined_ads.call_args.args[2]
        saved_by_span = {(a['start'], a['end']): a for a in saved}
        sponsor_saved = saved_by_span[(sponsor['start'], sponsor['end'])]
        promo_saved = saved_by_span[(promo['start'], promo['end'])]
        assert sponsor_saved['action_applied'] == 'remove'
        assert promo_saved['action_applied'] == 'beep'
        assert 'beep' not in sponsor_saved
        assert 'beep' not in promo_saved

    def test_all_remove_regression(self):
        sponsor = _sponsor_ad()
        promo = _cross_promo_ad()

        m = _run_pipeline([sponsor, promo], dict(ALL_REMOVE))

        assert m['result'] is True
        audio_segments = m['local_ap'].process_episode.call_args.args[1]
        assert all(s['beep'] is False for s in audio_segments)
        saved = m['storage'].save_combined_ads.call_args.args[2]
        assert all(a['action_applied'] == 'remove' for a in saved)


@pytest.mark.skipif(shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
                    reason="ffmpeg/ffprobe not available")
class TestAudioProcessorCutPartition:
    """Real-ffmpeg round trip: remove shortens, beep preserves duration,
    an empty (fully-kept) cut list leaves the file untouched.
    """

    def _make_source(self, tmp_path, duration=60):
        src = tmp_path / "in.mp3"
        subprocess.run(
            ["ffmpeg", "-y", "-f", "lavfi", "-i",
             f"sine=frequency=440:duration={duration}",
             "-acodec", "libmp3lame", "-ab", "64k", str(src)],
            check=True, capture_output=True,
        )
        return src

    def _make_beep_asset(self, tmp_path, duration=2):
        beep = tmp_path / "beep.mp3"
        subprocess.run(
            ["ffmpeg", "-y", "-f", "lavfi", "-i",
             f"sine=frequency=880:duration={duration}",
             "-acodec", "libmp3lame", "-ab", "64k", str(beep)],
            check=True, capture_output=True,
        )
        return beep

    def test_remove_span_shortens_duration(self, tmp_path):
        src = self._make_source(tmp_path, duration=60)
        beep = self._make_beep_asset(tmp_path)
        out = tmp_path / "out.mp3"
        proc = AudioProcessor(replace_audio_path=str(beep), bitrate="64k")

        applied = proc.remove_ads(str(src), [{'start': 10.0, 'end': 25.0}], str(out))

        assert applied and len(applied) == 1
        beep_len = proc.get_beep_duration()
        new_duration = proc.get_audio_duration(str(out))
        assert new_duration == pytest.approx(60.0 - 15.0 + beep_len, abs=1.0)
        assert new_duration < 55.0

    def test_beep_span_preserves_duration(self, tmp_path):
        src = self._make_source(tmp_path, duration=60)
        beep = self._make_beep_asset(tmp_path)
        out = tmp_path / "out.mp3"
        proc = AudioProcessor(replace_audio_path=str(beep), bitrate="64k")

        applied = proc.remove_ads(
            str(src), [{'start': 10.0, 'end': 25.0, 'beep': True}], str(out))

        assert applied and len(applied) == 1
        new_duration = proc.get_audio_duration(str(out))
        assert new_duration == pytest.approx(60.0, abs=1.0)

    def test_kept_span_leaves_audio_intact(self, tmp_path):
        # A kept marker never reaches ads_to_remove (Task 4); an empty cut
        # list is what remove_ads sees for a fully-kept episode.
        src = self._make_source(tmp_path, duration=30)
        beep = self._make_beep_asset(tmp_path)
        out = tmp_path / "out.mp3"
        proc = AudioProcessor(replace_audio_path=str(beep), bitrate="64k")

        applied = proc.remove_ads(str(src), [], str(out))

        assert applied == []
        assert filecmp.cmp(str(src), str(out), shallow=False)

    def test_mixed_remove_and_beep_spans_in_one_episode(self, tmp_path):
        src = self._make_source(tmp_path, duration=120)
        beep = self._make_beep_asset(tmp_path)
        out = tmp_path / "out.mp3"
        proc = AudioProcessor(replace_audio_path=str(beep), bitrate="64k")

        applied = proc.remove_ads(str(src), [
            {'start': 10.0, 'end': 25.0},                 # remove: shortens
            {'start': 50.0, 'end': 65.0, 'beep': True},    # beep: preserves
        ], str(out))

        assert applied and len(applied) == 2
        beep_len = proc.get_beep_duration()
        new_duration = proc.get_audio_duration(str(out))
        # Only the remove span shrinks; the beep span backfills its own
        # 15s length with 2s of tone plus 13s of silence.
        expected = 120.0 - (15.0 + 15.0) + (beep_len + 15.0)
        assert new_duration == pytest.approx(expected, abs=1.0)
