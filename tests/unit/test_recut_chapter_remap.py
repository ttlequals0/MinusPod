"""Unit tests for the recut chapter remap (served chapters JSON, AI-free).

The recut path re-cuts the retained ORIGINAL audio, so applied_cuts are in
original-episode coordinates while the stored chapters JSON sits on the
previous processed timeline. The remap goes previous-processed -> original
(inverse of the previous cut adjustment) -> recut timeline.
"""
import os
import sys
import tempfile

import pytest

# Bind a temp data dir via env (Storage reads MINUSPOD_DATA_DIR natively) so
# importing main_app does not mkdir /app/data (same pattern as
# test_recut_ad_list.py).
_test_data_dir = tempfile.mkdtemp(prefix='recut_chapter_test_')
os.environ.setdefault('SECRET_KEY', 'test-secret')
os.environ.setdefault('MINUSPOD_DATA_DIR', _test_data_dir)

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))

from main_app import processing
from utils.time import adjust_timestamp

BEEP = 2.0


@pytest.fixture(autouse=True)
def _isolate_db():
    import database
    database.Database._instance = None
    database.Database.__init__.__defaults__ = (_test_data_dir,)
    database.Database.__new__.__defaults__ = (_test_data_dir,)
    yield


# ---------- _unadjust_timestamp ----------

def test_unadjust_is_inverse_of_adjust_outside_cuts():
    cuts = [{'start': 100.0, 'end': 200.0}, {'start': 500.0, 'end': 560.0}]
    for t in (0.0, 50.0, 250.0, 499.0, 600.0, 1000.0):
        p = adjust_timestamp(t, cuts, BEEP)
        assert processing._unadjust_timestamp(p, cuts, BEEP) == pytest.approx(t)


def test_unadjust_inside_beep_maps_to_cut_start():
    cuts = [{'start': 100.0, 'end': 200.0}]
    # Beep occupies [100, 102) on the processed timeline (BEEP=2).
    assert processing._unadjust_timestamp(101.0, cuts, BEEP) == 100.0
    # End of the beep is where content resumes: original 200.
    assert processing._unadjust_timestamp(102.0, cuts, BEEP) == pytest.approx(200.0)


def test_unadjust_no_cuts_identity():
    assert processing._unadjust_timestamp(123.4, [], BEEP) == 123.4


# ---------- _remap_chapters_for_recut ----------

def test_chapter_before_cut_unchanged_and_after_cut_shifted():
    # No previous cuts: chapters are effectively in original coordinates.
    chapters = [{'startTime': 10, 'title': 'Intro'},
                {'startTime': 400, 'title': 'Topic'}]
    new_cuts = [{'start': 100.0, 'end': 200.0}]
    out = processing._remap_chapters_for_recut(
        chapters, [], new_cuts, BEEP, 1000.0, 902.0)
    # After the cut: shifted by (cut length - beep) = 98s.
    assert out == [{'startTime': 10, 'title': 'Intro'},
                   {'startTime': 302, 'title': 'Topic'}]


def test_chapter_inside_cut_dropped_folds_into_predecessor():
    chapters = [{'startTime': 10, 'title': 'Intro'},
                {'startTime': 120, 'title': 'Swallowed'},
                {'startTime': 180, 'title': 'PartlyCut'},
                {'startTime': 300, 'title': 'After'}]
    new_cuts = [{'start': 100.0, 'end': 200.0}]
    out = processing._remap_chapters_for_recut(
        chapters, [], new_cuts, BEEP, 1000.0, 902.0)
    titles = [ch['title'] for ch in out]
    # 'Swallowed' spans [120, 180], entirely inside the cut -> dropped.
    assert 'Swallowed' not in titles
    # 'PartlyCut' spans [180, 300]: survives, start snaps to the beep start.
    assert {'startTime': 100, 'title': 'PartlyCut'} in out
    assert {'startTime': 202, 'title': 'After'} in out


def test_remap_through_previous_cuts_composition():
    # Previous run cut [100, 200] (beep 2), so previous-processed = orig - 98
    # after 200. Stored chapters at previous-processed 50 and 302
    # (originals 50 and 400).
    chapters = [{'startTime': 50, 'title': 'A'}, {'startTime': 302, 'title': 'B'},
                {'startTime': 700, 'title': 'C'}]  # orig 798
    previous_cuts = [{'start': 100.0, 'end': 200.0}]
    new_cuts = [{'start': 100.0, 'end': 200.0}, {'start': 500.0, 'end': 600.0}]
    out = processing._remap_chapters_for_recut(
        chapters, previous_cuts, new_cuts, BEEP, 1000.0, 804.0)
    # A: orig 50 -> before both cuts, unchanged. B: orig 400 -> only first cut
    # before it, still 302. C: orig 798 -> both cuts before it: 798 - 98 - 98.
    assert out == [{'startTime': 50, 'title': 'A'},
                   {'startTime': 302, 'title': 'B'},
                   {'startTime': 602, 'title': 'C'}]


def test_sliver_chapter_dropped():
    # Chapter A lands < MIN_CHAPTER_SECONDS (1.0s) before its successor on
    # the new timeline -> dropped, mirroring embedded remap policy.
    chapters = [{'startTime': 100, 'title': 'A'}, {'startTime': 150, 'title': 'B'}]
    new_cuts = [{'start': 100.2, 'end': 149.5}]
    out = processing._remap_chapters_for_recut(
        chapters, [], new_cuts, 0.0, 1000.0, 950.7)
    # A -> 100.0; B -> 150 - 49.3 = 100.7; gap 0.7 < 1.0 -> A dropped.
    assert [ch['title'] for ch in out] == ['B']


def test_extra_keys_preserved():
    chapters = [{'startTime': 10, 'title': 'Intro', 'url': 'https://x.example/a'},
                {'startTime': 400, 'title': 'Topic', 'img': 'https://x.example/i.png'}]
    new_cuts = [{'start': 100.0, 'end': 200.0}]
    out = processing._remap_chapters_for_recut(
        chapters, [], new_cuts, BEEP, 1000.0, 902.0)
    assert out[0]['url'] == 'https://x.example/a'
    assert out[1]['img'] == 'https://x.example/i.png'


def test_no_cuts_at_all_is_identity_apart_from_int_floor():
    chapters = [{'startTime': 10, 'title': 'A'}, {'startTime': 500, 'title': 'B'}]
    out = processing._remap_chapters_for_recut(chapters, [], [], BEEP, 1000.0, 1000.0)
    assert out == chapters


# ---------- _generate_assets seam ----------

def _stub_assets_io(monkeypatch, counters, embed_ok=True):
    import chapters_generator
    monkeypatch.setattr(
        chapters_generator.ChaptersGenerator, 'generate_chapters',
        lambda self, *a, **k: counters.__setitem__(
            'generated', counters.get('generated', 0) + 1) or {'chapters': []})
    monkeypatch.setattr(processing.db, 'get_setting', lambda k: 'true')
    monkeypatch.setattr(processing.storage, 'save_final_segments', lambda *a, **k: None)
    monkeypatch.setattr(processing.storage, 'save_transcript_vtt', lambda *a, **k: None)
    monkeypatch.setattr(processing.db, 'save_episode_details', lambda *a, **k: None)
    def _save_both(s, e, chapters, cuts):
        # Chapters + applied cuts persist through ONE atomic storage call.
        counters['saved'] = chapters
        counters['applied_saved'] = cuts
    monkeypatch.setattr(
        processing.storage, 'save_chapters_and_applied_cuts', _save_both)
    # Default: no probe (callers pass audio_duration). Overridden where the
    # duration=None consistency path is exercised.
    monkeypatch.setattr(processing, 'get_audio_duration', lambda p: None)

    def _embed(path, chapters, duration=None):
        counters['embedded'] = {'path': path, 'chapters': chapters,
                                'duration': duration}
        return embed_ok
    monkeypatch.setattr(processing, 'embed_chapters', _embed)
    # Beep is 2.0s in these tests regardless of the real replacement file.
    monkeypatch.setattr(processing, 'get_replacement_duration', lambda: BEEP)


SEGMENTS = [{'start': 0.0, 'end': 30.0, 'text': 'hello world'}]


def test_generate_assets_recut_remaps_saves_and_embeds(monkeypatch):
    counters = {}
    _stub_assets_io(monkeypatch, counters)
    stored = {'version': '1.2.0',
              'chapters': [{'startTime': 10, 'title': 'Intro'},
                           {'startTime': 302, 'title': 'Topic'}]}
    monkeypatch.setattr(processing.storage, 'get_chapters_json',
                        lambda s, e: stored)
    previous_cuts = [{'start': 100.0, 'end': 200.0}]
    new_cuts = [{'start': 100.0, 'end': 200.0}, {'start': 500.0, 'end': 600.0}]
    processing._generate_assets(
        'slug', 'ep', SEGMENTS, new_cuts, '', 'Pod', 'Title',
        regenerate_chapters=False,
        audio_path='/data/slug/episodes/ep-v2.mp3', audio_duration=804.0,
        previous_cuts=previous_cuts, original_duration=1000.0)
    assert counters.get('generated', 0) == 0, "recut must stay AI-free"
    saved = counters['saved']
    assert saved['version'] == '1.2.0'
    # Topic (orig 400) sits before the new cut: unchanged at 302.
    assert saved['chapters'] == [{'startTime': 10, 'title': 'Intro'},
                                 {'startTime': 302, 'title': 'Topic'}]
    # Remapped set replaces the ffmpeg-remapped source chapters in the MP3.
    assert counters['embedded'] == {'path': '/data/slug/episodes/ep-v2.mp3',
                                    'chapters': saved['chapters'],
                                    'duration': 804.0}
    # The recut's own applied cuts become the authoritative list for the next
    # recut (persisted as {start,end} pairs).
    assert counters['applied_saved'] == new_cuts


def test_generate_assets_recut_shifts_chapters_after_new_cut(monkeypatch):
    counters = {}
    _stub_assets_io(monkeypatch, counters)
    stored = {'version': '1.2.0',
              'chapters': [{'startTime': 10, 'title': 'Intro'},
                           {'startTime': 700, 'title': 'Late'}]}
    monkeypatch.setattr(processing.storage, 'get_chapters_json',
                        lambda s, e: stored)
    previous_cuts = [{'start': 100.0, 'end': 200.0}]
    new_cuts = [{'start': 100.0, 'end': 200.0}, {'start': 500.0, 'end': 600.0}]
    processing._generate_assets(
        'slug', 'ep', SEGMENTS, new_cuts, '', 'Pod', 'Title',
        regenerate_chapters=False,
        audio_path='/x.mp3', audio_duration=804.0,
        previous_cuts=previous_cuts, original_duration=1000.0)
    # Late: previous-processed 700 -> orig 798 -> minus 2x(98) = 602.
    assert counters['saved']['chapters'][1] == {'startTime': 602, 'title': 'Late'}


def test_generate_assets_recut_no_stored_chapters_noop(monkeypatch):
    counters = {}
    _stub_assets_io(monkeypatch, counters)
    monkeypatch.setattr(processing.storage, 'get_chapters_json', lambda s, e: None)
    processing._generate_assets(
        'slug', 'ep', SEGMENTS, [{'start': 100.0, 'end': 200.0}], '', 'Pod',
        'Title', regenerate_chapters=False,
        previous_cuts=[], original_duration=1000.0)
    assert 'saved' not in counters
    assert 'embedded' not in counters
    assert counters.get('generated', 0) == 0


def test_generate_assets_recut_remap_error_keeps_old_json(monkeypatch):
    counters = {}
    _stub_assets_io(monkeypatch, counters)

    def boom(s, e):
        raise RuntimeError("db exploded")

    monkeypatch.setattr(processing.storage, 'get_chapters_json', boom)
    # Must not raise: the recut proceeds and the old JSON stays.
    processing._generate_assets(
        'slug', 'ep', SEGMENTS, [{'start': 100.0, 'end': 200.0}], '', 'Pod',
        'Title', regenerate_chapters=False,
        previous_cuts=[], original_duration=1000.0)
    assert 'saved' not in counters
    assert 'embedded' not in counters


def test_generate_assets_regenerate_true_unchanged(monkeypatch):
    # regenerate_chapters=True must keep calling the generator and never touch
    # the remap path.
    counters = {}
    _stub_assets_io(monkeypatch, counters)

    def fail_if_called(s, e):
        raise AssertionError("remap path must not run when regenerating")

    monkeypatch.setattr(processing.storage, 'get_chapters_json', fail_if_called)
    processing._generate_assets('slug', 'ep', SEGMENTS, [], '', 'Pod', 'Title')
    assert counters.get('generated', 0) == 1


# ---------- fallback: no persisted applied cuts (pre-deploy episodes) ----------

def test_generate_assets_recut_no_persisted_cuts_leaves_chapters_untouched(monkeypatch):
    # previous_cuts=None models an episode rendered before applied_cuts_json
    # was persisted. The remap must be a no-op: no reconstruction, no remap, no
    # save, no embed -- the served chapters JSON stays byte-identical.
    counters = {}
    _stub_assets_io(monkeypatch, counters)

    def unexpected_read(s, e):
        raise AssertionError("fallback must not even read chapters JSON")

    monkeypatch.setattr(processing.storage, 'get_chapters_json', unexpected_read)
    new_cuts = [{'start': 100.0, 'end': 200.0}, {'start': 500.0, 'end': 600.0}]
    # Must not raise.
    processing._generate_assets(
        'slug', 'ep', SEGMENTS, new_cuts, '', 'Pod', 'Title',
        regenerate_chapters=False,
        audio_path='/x.mp3', audio_duration=804.0,
        previous_cuts=None, original_duration=1000.0)
    assert 'saved' not in counters, "chapters JSON must be left untouched"
    assert 'embedded' not in counters
    assert 'applied_saved' not in counters, \
        "stale chapters must not gain an authoritative cut list"


def test_remap_stored_chapters_none_cuts_is_noop_directly(monkeypatch):
    # Unit-level pin of the fallback in _remap_stored_chapters itself.
    counters = {}
    _stub_assets_io(monkeypatch, counters)
    monkeypatch.setattr(
        processing.storage, 'get_chapters_json',
        lambda s, e: (_ for _ in ()).throw(AssertionError("must not read")))
    processing._remap_stored_chapters(
        'slug', 'ep', [{'start': 100.0, 'end': 200.0}], BEEP,
        None, 1000.0, audio_path='/x.mp3', audio_duration=804.0)
    assert counters == {}


# ---------- bug C: embed failure must not leave new JSON with stale ID3 ----------

def test_generate_assets_recut_embed_failure_keeps_old_json(monkeypatch):
    # embed_chapters returns False (ffmpeg failed). Because the embed runs
    # BEFORE the JSON save, the served JSON and embedded ID3 both stay old --
    # the exact #523 mismatch (new JSON, stale ID3) is now impossible.
    counters = {}
    _stub_assets_io(monkeypatch, counters, embed_ok=False)
    stored = {'version': '1.2.0',
              'chapters': [{'startTime': 10, 'title': 'Intro'},
                           {'startTime': 302, 'title': 'Topic'}]}
    monkeypatch.setattr(processing.storage, 'get_chapters_json',
                        lambda s, e: stored)
    previous_cuts = [{'start': 100.0, 'end': 200.0}]
    new_cuts = [{'start': 100.0, 'end': 200.0}, {'start': 500.0, 'end': 600.0}]
    processing._generate_assets(
        'slug', 'ep', SEGMENTS, new_cuts, '', 'Pod', 'Title',
        regenerate_chapters=False,
        audio_path='/x.mp3', audio_duration=804.0,
        previous_cuts=previous_cuts, original_duration=1000.0)
    assert 'embedded' in counters, "embed is attempted first"
    assert 'saved' not in counters, "JSON must not be saved after embed fails"
    assert 'applied_saved' not in counters, \
        "authoritative cuts must not advance when the render set was not saved"


# ---------- duration=None: JSON filter and embed must share one duration ----------

def test_generate_assets_recut_duration_none_uses_probed_value(monkeypatch):
    # audio_duration=None: _remap_stored_chapters probes the file once and
    # feeds the SAME duration to both the sliver filter and the embed, so the
    # served and embedded chapter sets trim against the same tail bound.
    counters = {}
    _stub_assets_io(monkeypatch, counters)
    monkeypatch.setattr(processing, 'get_audio_duration', lambda p: 804.0)
    stored = {'version': '1.2.0',
              'chapters': [{'startTime': 10, 'title': 'Intro'},
                           {'startTime': 700, 'title': 'Late'}]}
    monkeypatch.setattr(processing.storage, 'get_chapters_json',
                        lambda s, e: stored)
    previous_cuts = [{'start': 100.0, 'end': 200.0}]
    new_cuts = [{'start': 100.0, 'end': 200.0}, {'start': 500.0, 'end': 600.0}]
    processing._generate_assets(
        'slug', 'ep', SEGMENTS, new_cuts, '', 'Pod', 'Title',
        regenerate_chapters=False,
        audio_path='/x.mp3', audio_duration=None,
        previous_cuts=previous_cuts, original_duration=1000.0)
    # Probed 804.0 flows into the embed rather than a re-probe or a computed
    # fallback that could disagree with the JSON filter.
    assert counters['embedded']['duration'] == 804.0
    # Late still remaps (804.0 tail leaves room): consistent survivor set.
    assert counters['saved']['chapters'][1] == {'startTime': 602, 'title': 'Late'}
