"""precise_cut_edges: a cut edge follows the waveform (relative pauses, quiet frames, fade room, crossfades)."""
import math
import shutil
import struct
import wave
from pathlib import Path

from tests.app_bootstrap import bootstrap

bootstrap('waveform_cut_edges_test_')

import pytest

import audio_processor
import config
from ad_detector import silence_boundary_snap as snap
from audio_processor import AudioProcessor
from utils.time import adjust_timestamp

ROOT = Path(__file__).resolve().parents[2]


def _words(*items):
    return [{'word': w, 'start': s, 'end': e} for w, s, e in items]


def _segments(words):
    return [{'start': words[0]['start'], 'end': words[-1]['end'],
             'text': ' '.join(w['word'] for w in words), 'words': words}]


def _profile(level=-25.0, dips=(), lo=0.0, hi=400.0):
    """Reader: `level` everywhere except (start, end, dB) dips."""
    def read(a, b):
        a, b = max(a, lo), min(b, hi)
        out = []
        for i in range(round((b - a) / 0.01)):
            t = a + i * 0.01 + 0.005
            out.append(next((db for p, q, db in dips if p <= t < q), level))
        return out
    return read


# --- (a) relative pause threshold ----------------------------------------------------------

def test_a_dip_15_db_under_speech_is_a_pause_at_the_default_drop():
    reader = _profile(dips=[(100.10, 100.40, -41.0)])  # 16 dB under the -25 dB speech
    frames = reader(99.0, 101.0)
    assert snap._find_pauses(frames, 99.0, drop_db=20.0) == []
    pauses = snap._find_pauses(frames, 99.0, drop_db=15.0)
    assert len(pauses) == 1 and pauses[0][0] == pytest.approx(100.10, abs=0.011)


def test_drop_passes_through_the_word_snap():
    words = _words(('one', 99.0, 99.9), ('two', 100.5, 101.0), ('three', 101.1, 101.6))
    reader = _profile(dips=[(99.95, 100.45, -41.0)])
    strict = snap._snap_word_edge(100.7, 'start', words, energy_reader=reader, drop_db=20.0)
    loose = snap._snap_word_edge(100.7, 'start', words, energy_reader=reader, drop_db=15.0)
    # 20 dB: no pause, only the quietest-gap-frame fallback; 15 dB: the whole pause.
    assert strict[1] == pytest.approx(snap.QUIET_FRAME_PAUSE_S)
    assert loose[2] is True and loose[1] == pytest.approx(0.5, abs=0.011)
    assert loose[0] == pytest.approx(100.45 - snap.PAUSE_MARGIN_S, abs=0.011)


# --- (b) quietest frame of an inter-word gap -----------------------------------------------

def test_no_pause_edge_goes_to_the_quietest_gap_frame():
    # Continuous speech over a music bed: no run long enough to be a pause, but the gap
    # between "two" and "three" dips 12 dB for 20 ms.
    words = _words(('one', 99.0, 99.6), ('two', 99.6, 100.30), ('three', 100.34, 101.0),
                   ('four', 101.0, 101.5))
    reader = _profile(dips=[(100.30, 100.32, -37.0)])
    edge, pause, acoustic = snap._snap_word_edge(100.6, 'start', words, energy_reader=reader)
    assert acoustic is True
    assert 100.30 <= edge <= 100.34
    assert pause == pytest.approx(snap.QUIET_FRAME_PAUSE_S)


def test_quiet_frame_never_inside_a_word():
    words = _words(('one', 99.0, 99.6), ('two', 99.6, 100.30), ('three', 100.34, 101.0))
    # The deepest dip is inside "three"; only the gap frame may be used.
    reader = _profile(dips=[(100.50, 100.52, -55.0), (100.30, 100.32, -37.0)])
    edge, _pause, acoustic = snap._snap_word_edge(100.6, 'start', words, energy_reader=reader)
    assert acoustic is True and 100.30 <= edge <= 100.34


def test_quiet_frame_needs_a_real_drop():
    # A gap frame only 2 dB under the speech is no better than the transcript: old fallback.
    words = _words(('one', 99.0, 99.6), ('two', 99.6, 100.30), ('three', 100.34, 101.0))
    reader = _profile(dips=[(100.30, 100.32, -27.0)])
    edge, pause, acoustic = snap._snap_word_edge(100.6, 'start', words, energy_reader=reader)
    assert acoustic is False and pause is None


def test_quiet_frame_stays_in_reach():
    words = _words(('one', 97.0, 98.0), ('two', 98.04, 100.9), ('three', 100.94, 101.0),
                   ('four', 101.0, 103.0))
    # The only quiet gap frame is 2.4 s before the edge: out of reach, nothing moves acoustically.
    reader = _profile(dips=[(98.0, 98.04, -55.0)])
    _edge, _pause, acoustic = snap._snap_word_edge(100.4, 'start', words, energy_reader=reader)
    assert acoustic is False


def test_quiet_frame_records_its_mode_on_the_marker():
    words = _words(('one', 99.0, 99.6), ('two', 99.6, 100.30), ('three', 100.34, 101.0),
                   ('four', 101.0, 101.5))
    ad = {'start': 100.6, 'end': 160.0}
    snap.snap_ad_boundaries_to_silence(
        [ad], [], 2.0, 0.3, word_segments=_segments(words),
        energy_reader=_profile(dips=[(100.30, 100.32, -37.0)]), neighbours=[], duration=4000.0)
    assert 100.30 <= ad['start'] <= 100.34
    assert ad['edge_snap']['start'] == {'acoustic': True, 'moved_s': pytest.approx(ad['start'] - 100.6),
                                        'mode': 'quiet_frame'}
    assert ad['edge_pause_s']['start'] == pytest.approx(snap.QUIET_FRAME_PAUSE_S)


def _even_words(first_sentence_end=30):
    """Words every 0.3 s (0.25 s word, 0.05 s gap: too short for a pause); word N ends a sentence."""
    words = []
    for i in range(70):
        start = round(i * 0.3, 3)
        words.append({'word': 'end.' if i == first_sentence_end else f'w{i}', 'start': start,
                      'end': round(start + 0.25, 3)})
    return words


def _word_reader(words, dips=()):
    """Speech -20 dB inside words, -40 dB in the gaps, except (start, end, dB) dips."""
    def read(a, b):
        out = []
        for k in range(round((b - a) / 0.01)):
            c = a + (k + 0.5) * 0.01
            level = -20.0 if any(w['start'] <= c <= w['end'] for w in words) else -40.0
            out.append(next((db for p, q, db in dips if p < c < q), level))
        return out
    return read


def test_quiet_frame_stays_in_the_transcript_gap_not_a_quieter_one_inside_the_ad():
    # Sentence ends 9.25, the ad's first word starts 9.30. The gap two ad words in is 8 dB
    # quieter; the edge must still stay in the 9.25-9.30 gap, keeping no ad word.
    words = _even_words()
    reader = _word_reader(words, dips=[(9.85, 9.90, -48.0)])
    edge, _pause, acoustic = snap._snap_word_edge(9.30, 'start', words, energy_reader=reader)
    assert acoustic is True and 9.25 <= edge <= 9.30


def test_start_quiet_frame_never_cuts_kept_speech():
    words = _even_words()
    reader = _word_reader(words, dips=[(8.95, 9.00, -55.0)])  # quieter gap before "end."
    edge, _pause, _ = snap._snap_word_edge(9.30, 'start', words, energy_reader=reader)
    assert 9.25 <= edge <= 9.30


def test_end_quiet_frame_never_keeps_ad_speech_nor_cuts_kept_speech():
    # Ad ends with "end." (9.00-9.25); kept speech starts 9.30.
    words = _even_words()
    for dips in ([(9.55, 9.60, -55.0)], [(8.95, 9.00, -55.0)]):
        edge, _pause, _ = snap._snap_word_edge(
            9.25, 'end', words, energy_reader=_word_reader(words, dips=dips))
        assert 9.25 <= edge <= 9.30, dips


def test_quiet_frame_on_the_marker_stays_on_the_sentence_boundary():
    words = _even_words()
    ad = {'start': 9.31, 'end': 40.0}
    snap.snap_ad_boundaries_to_silence(
        [ad], [], 1.5, 0.3, word_segments=_segments(words),
        energy_reader=_word_reader(words, dips=[(9.85, 9.90, -48.0)]), neighbours=[], duration=100)
    assert 9.25 <= ad['start'] <= 9.30


def test_quiet_frame_never_jumps_words_to_a_sentence_boundary():
    # The edge sits in w34, 0.9 s after a quieter sentence gap; moving there would cut three kept words.
    words = _even_words()
    reader = _word_reader(words, dips=[(9.25, 9.30, -55.0)])
    edge, _pause, acoustic = snap._snap_word_edge(10.21, 'start', words, energy_reader=reader)
    assert acoustic is True and 10.15 <= edge <= 10.20


def test_no_quiet_frame_when_the_transcript_gap_has_no_frame():
    # Words abut at the boundary (no gap frame): never cross a word to a quieter gap.
    words = _words(('one.', 99.0, 100.0), ('two', 100.0, 100.3), ('three', 100.34, 101.0))
    reader = _profile(dips=[(100.30, 100.34, -55.0)])
    edge, pause, acoustic = snap._snap_word_edge(100.0, 'start', words, energy_reader=reader)
    assert acoustic is False and pause is None and edge == 100.0


# --- word guard on pauses only the 15 dB threshold finds ------------------------------------

_SOFT = _words(('end.', 99.0, 100.0), ('and', 100.05, 100.2), ('that', 100.2, 100.5),
               ('more', 100.55, 101.5))


def test_soft_pause_start_move_never_keeps_an_ad_word():
    # A dip 16 dB under the speech over "and that": a pause at 15 dB, not at 20 dB. Moving the
    # start edge to its end would keep "and" and half of "that".
    reader = _profile(dips=[(100.10, 100.40, -41.0)])
    edge, _pause, _ = snap._snap_word_edge(100.02, 'start', _SOFT, energy_reader=reader, drop_db=15.0)
    assert 100.0 <= edge <= 100.05


def test_soft_pause_end_move_never_lands_inside_a_word():
    reader = _profile(dips=[(100.10, 100.40, -41.0)])
    edge, _pause, _ = snap._snap_word_edge(100.52, 'end', _SOFT, energy_reader=reader, drop_db=15.0)
    assert 100.5 <= edge <= 100.55


def test_soft_pause_guard_falls_back_to_the_gap_quiet_frame():
    reader = _profile(dips=[(100.10, 100.40, -41.0), (100.03, 100.05, -36.0)])
    edge, pause, acoustic = snap._snap_word_edge(100.02, 'start', _SOFT, energy_reader=reader,
                                                 drop_db=15.0)
    assert acoustic is True and 100.0 <= edge <= 100.05
    assert pause == pytest.approx(snap.QUIET_FRAME_PAUSE_S)


def test_deep_pause_moves_like_prod_even_across_words():
    # The same dip deep enough for the 20 dB rule: prod's drift correction stays as it was.
    reader = _profile(dips=[(100.10, 100.40, -50.0)])
    start, _p, acoustic = snap._snap_word_edge(100.02, 'start', _SOFT, energy_reader=reader,
                                               drop_db=15.0)
    end, _p2, _ = snap._snap_word_edge(100.52, 'end', _SOFT, energy_reader=reader, drop_db=15.0)
    assert acoustic is True
    assert start == pytest.approx(100.40 - snap.PAUSE_MARGIN_S, abs=0.011)
    assert end == pytest.approx(100.10 + snap.PAUSE_MARGIN_S, abs=0.011)


# --- near-digital silence beats the transcript ----------------------------------------------

# A DAI splice: the word times run ~150 ms late, so "profit" really ends in the 20 ms of digital
# silence at 100.15, while the transcript says "they" is already speaking there.
_SPLICE = _words(('how', 99.36, 99.47), ('much', 99.47, 99.68), ('profit', 99.68, 100.0),
                 ('they', 100.0, 100.21), ("'re", 100.21, 100.37), ('generating', 100.37, 100.91))


def _splice_reader(silence=((100.15, 100.17, -73.0),)):
    return _profile(level=-19.0, dips=list(silence))


def test_start_edge_snaps_into_near_digital_silence_despite_word_times():
    ad = {'start': 100.0, 'end': 160.0}
    snap.snap_ad_boundaries_to_silence(
        [ad], [], 2.0, 0.3, word_segments=_segments(_SPLICE), energy_reader=_splice_reader(),
        neighbours=[], duration=4000.0)
    assert 100.15 <= ad['start'] <= 100.17
    assert ad['edge_snap']['start']['mode'] == 'digital_silence'


def test_end_edge_snaps_into_near_digital_silence():
    edge, _pause, acoustic = snap._snap_word_edge(100.37, 'end', _SPLICE, energy_reader=_splice_reader())
    assert acoustic is True and 100.15 <= edge <= 100.17


def test_one_silent_frame_is_not_digital_silence():
    # 10 ms under -60 dBFS is a dip, not a splice: the word guard still holds.
    edge, _pause, _ = snap._snap_word_edge(100.0, 'start', _SPLICE,
                                           energy_reader=_splice_reader(((100.15, 100.16, -73.0),)))
    assert not 100.1 < edge < 100.2


def test_digital_silence_out_of_reach_is_ignored():
    edge, _pause, _ = snap._snap_word_edge(
        100.0, 'start', _SPLICE, energy_reader=_splice_reader(((100.55, 100.60, -90.0),)))
    assert edge < 100.5


def test_edge_already_in_digital_silence_stays():
    # A jingle-bounded outro edge sitting in a long silent run: the digital rule leaves it alone.
    words = _words(('bye.', 99.0, 99.8), ('next', 102.0, 102.5))
    reader = _profile(level=-20.0, dips=[(99.85, 101.5, -100.0)])
    edge, _pause, _ = snap._snap_word_edge(100.5, 'start', words, energy_reader=reader)
    assert edge == 100.5


def test_a_20_db_pause_still_wins_over_a_nearer_digital_silence():
    # An outro edge with a deep pause in reach (where prod puts it) and a short silent run nearer:
    # prod's pause move stays exactly as it was.
    words = _words(('bye.', 99.0, 99.5), ('next', 103.0, 103.5))
    reader = _profile(level=-20.0, dips=[(99.70, 99.73, -65.0), (99.90, 100.90, -100.0)])
    edge, _pause, acoustic = snap._snap_word_edge(99.6, 'start', words, energy_reader=reader)
    assert acoustic is True and edge == pytest.approx(100.90 - snap.PAUSE_MARGIN_S, abs=0.011)


def test_cue_snapped_and_locked_edges_never_move_to_digital_silence():
    for extra in ({'cue_snap': {'start': {'cue': 'jingle'}}}, {'reviewer_locked_start': 100.0},
                  {'validation': {'user_confirmed': True}}):
        ad = {'start': 100.0, 'end': 160.0, **extra}
        snap.snap_ad_boundaries_to_silence(
            [ad], [], 2.0, 0.3, word_segments=_segments(_SPLICE), energy_reader=_splice_reader(),
            neighbours=[], duration=4000.0)
        assert ad['start'] == 100.0, extra


# --- edge inside a pause but on its speech side ----------------------------------------------

def test_start_edge_hugging_the_kept_speech_moves_into_the_pause():
    words = _words(('one.', 99.0, 100.0), ('two', 100.8, 101.3))
    reader = _profile(dips=[(100.0, 100.8, -70.0)])
    edge, room, acoustic = snap._snap_word_edge(100.02, 'start', words, energy_reader=reader)
    assert acoustic is True
    assert edge == pytest.approx(100.0 + snap.FADE_ROOM_S, abs=0.011)
    assert room == pytest.approx(snap.FADE_ROOM_S, abs=0.011)


def test_end_edge_hugging_the_kept_speech_moves_into_the_pause():
    words = _words(('one.', 99.0, 100.0), ('two', 100.8, 101.3))
    reader = _profile(dips=[(100.0, 100.8, -70.0)])
    edge, room, _ = snap._snap_word_edge(100.79, 'end', words, energy_reader=reader)
    assert edge == pytest.approx(100.8 - snap.FADE_ROOM_S, abs=0.011)
    assert room == pytest.approx(snap.FADE_ROOM_S, abs=0.011)


def test_fade_room_move_never_passes_a_word():
    # The audio is quiet to 100.8 but the transcript puts "two" at 100.1 (start) / "one." to
    # 100.7 (end): the fade-room move stops at the word, never keeping or cutting part of it.
    reader = _profile(dips=[(100.0, 100.8, -70.0)])
    start_words = _words(('one.', 99.0, 100.0), ('two', 100.1, 101.3))
    edge, _room, _ = snap._snap_word_edge(100.02, 'start', start_words, energy_reader=reader)
    assert 100.02 <= edge <= 100.1
    end_words = _words(('one.', 99.0, 100.7), ('two', 100.8, 101.3))
    edge, _room, _ = snap._snap_word_edge(100.79, 'end', end_words, energy_reader=reader)
    assert 100.7 <= edge <= 100.79


def test_fade_room_move_skips_an_edge_inside_a_word():
    reader = _profile(dips=[(100.0, 100.8, -70.0)])
    words = _words(('one.', 99.0, 100.05), ('two', 100.6, 101.3))
    edge, _room, _ = snap._snap_word_edge(100.02, 'start', words, energy_reader=reader)
    assert edge == 100.02


def test_edge_with_fade_room_in_a_pause_stays():
    words = _words(('one.', 99.0, 100.0), ('two', 100.8, 101.3))
    reader = _profile(dips=[(100.0, 100.8, -70.0)])
    edge, room, _ = snap._snap_word_edge(100.4, 'start', words, energy_reader=reader)
    assert edge == 100.4 and room == pytest.approx(0.4, abs=0.011)


def test_locked_edge_inside_a_pause_is_not_moved_for_fade_room():
    words = _words(('one.', 99.0, 100.0), ('two', 100.8, 101.3), ('x', 150.0, 151.0))
    for side, edge, ad in (('start', 100.02, {'start': 100.02, 'end': 150.0, 'reviewer_locked_start': 100.02}),
                           ('end', 100.79, {'start': 40.0, 'end': 100.79, 'reviewer_locked_end': 100.79})):
        snap.snap_ad_boundaries_to_silence(
            [ad], [], 2.0, 0.3, word_segments=_segments(words),
            energy_reader=_profile(dips=[(100.0, 100.8, -70.0)]), neighbours=[], duration=4000.0)
        assert ad[side] == edge
        assert side not in (ad.get('edge_snap') or {})


def test_locked_edge_gets_no_quiet_frame():
    words = _words(('one', 99.0, 99.6), ('two', 99.6, 100.30), ('three', 100.34, 101.0),
                   ('four', 101.0, 101.5), ('five', 150.0, 151.0))
    ad = {'start': 100.6, 'end': 150.0, 'reviewer_locked_start': 100.6}
    snap.snap_ad_boundaries_to_silence(
        [ad], [], 2.0, 0.3, word_segments=_segments(words),
        energy_reader=_profile(dips=[(100.30, 100.32, -37.0)]), neighbours=[], duration=4000.0)
    # The pre-existing word-gap fallback only: not acoustic, no quiet-frame mode.
    record = (ad.get('edge_snap') or {}).get('start') or {}
    assert record.get('mode') != 'quiet_frame' and not record.get('acoustic')


def test_unlocked_edge_still_gets_fade_room():
    words = _words(('one.', 99.0, 100.0), ('two', 100.8, 101.3), ('x', 150.0, 151.0))
    ad = {'start': 100.02, 'end': 150.0}
    snap.snap_ad_boundaries_to_silence(
        [ad], [], 2.0, 0.3, word_segments=_segments(words),
        energy_reader=_profile(dips=[(100.0, 100.8, -70.0)]), neighbours=[], duration=4000.0)
    assert ad['start'] == pytest.approx(100.0 + snap.FADE_ROOM_S, abs=0.011)


def test_short_pause_room_is_the_whole_pause():
    words = _words(('one.', 99.0, 100.0), ('two', 100.15, 101.3))
    reader = _profile(dips=[(100.0, 100.15, -70.0)])
    edge, room, _ = snap._snap_word_edge(100.01, 'start', words, energy_reader=reader)
    assert edge == pytest.approx(100.15 - snap.PAUSE_MARGIN_S, abs=0.011)
    assert room == pytest.approx(0.15 - snap.PAUSE_MARGIN_S, abs=0.011)


# --- (c) crossfade ---------------------------------------------------------------------------

@pytest.fixture
def source(tmp_path):
    if not shutil.which('ffmpeg'):
        pytest.skip('ffmpeg is unavailable')
    path = tmp_path / 'source.mp3'
    path.write_bytes((ROOT / 'assets/replace.mp3').read_bytes() * 90)
    return path


def _render(source, tmp_path, monkeypatch, ads, **kwargs):
    recorded = []
    original = audio_processor.tracked_run

    def record(command, **kw):
        recorded.append(command)
        return original(command, **kw)

    monkeypatch.setattr(audio_processor, 'tracked_run', record)
    processor = AudioProcessor(**kwargs)
    out = tmp_path / 'out.mp3'
    assert processor.remove_ads(str(source), ads, str(out)) is not None
    command = next(c for c in recorded if '-filter_complex' in c)
    return command[command.index('-filter_complex') + 1], processor.get_audio_duration(str(out))


def test_join_without_a_clip_is_an_equal_power_crossfade(source, tmp_path, monkeypatch):
    plain, plain_len = _render(source, tmp_path, monkeypatch, [{'start': 20, 'end': 30}],
                               replacement_sound_enabled=False)
    graph, length = _render(source, tmp_path, monkeypatch, [{'start': 20, 'end': 30}],
                            replacement_sound_enabled=False, edge_crossfade_s=0.04)
    assert 'acrossfade' not in plain
    assert 'acrossfade=d=0.040:c1=qsin:c2=qsin' in graph
    # Length-neutral: each side reaches half the crossfade into the cut.
    assert length == pytest.approx(plain_len, abs=0.03)


def test_crossfade_off_by_default(source, tmp_path, monkeypatch):
    graph, _ = _render(source, tmp_path, monkeypatch,
                       [{'start': 20, 'end': 30}, {'start': 50, 'end': 61}],
                       replacement_sound_enabled=False)
    assert 'acrossfade' not in graph and 'curve=' not in graph


def test_crossfade_on_several_joins(source, tmp_path, monkeypatch):
    graph, _ = _render(source, tmp_path, monkeypatch,
                       [{'start': 20, 'end': 30}, {'start': 50, 'end': 61}],
                       replacement_sound_enabled=False, edge_crossfade_s=0.04)
    assert graph.count('acrossfade') == 2


def test_clip_joins_use_equal_power_fades(source, tmp_path, monkeypatch):
    ad = {'start': 20, 'end': 30, 'edge_pause_s': {'start': 0.05, 'end': 0.3}}
    graph, _ = _render(source, tmp_path, monkeypatch, [ad], edge_crossfade_s=0.04)
    content = [b for b in graph.split(';') if b.startswith('[0:a]')]
    assert 'afade=t=out' in content[0] and 'curve=qsin' in content[0].split('afade=t=out')[1]
    assert 'afade=t=in:d=0.3:curve=qsin' in content[1]


def test_crossfade_tunable_and_resolver():
    class Db:
        def __init__(self, on):
            self.on = on

        def get_setting(self, key):
            return {'precise_cut_edges': 'true' if self.on else 'false'}.get(key)

    assert config.resolve_edge_crossfade_s(Db(False)) == 0.0
    assert config.resolve_edge_crossfade_s(Db(True)) == pytest.approx(0.04)


def _burst_wav(path, bursts, seconds=120.0, rate=16000):
    """Mono wav: quiet noise floor, a 1 kHz burst of 50 ms at each time in `bursts`."""
    import random
    rng = random.Random(1)
    frames = []
    for i in range(int(rate * seconds)):
        t = i / rate
        value = rng.randint(-30, 30)
        if any(b <= t < b + 0.05 for b in bursts):
            value = int(15000 * math.sin(2 * math.pi * 1000 * t))
        frames.append(struct.pack('<h', value))
    with wave.open(str(path), 'wb') as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b''.join(frames))


def _burst_onset(path):
    import subprocess
    raw = subprocess.run(['ffmpeg', '-v', 'error', '-i', str(path), '-ac', '1', '-ar', '16000',
                          '-f', 's16le', '-'], capture_output=True, check=True).stdout
    samples = struct.unpack(f'<{len(raw) // 2}h', raw)
    return next(i for i, v in enumerate(samples) if abs(v) > 5000) / 16000


def test_timeline_maps_agree_with_the_audio_after_many_crossfades(tmp_path):
    if not shutil.which('ffmpeg'):
        pytest.skip('ffmpeg is unavailable')
    src = tmp_path / 'bursts.wav'
    _burst_wav(src, [110.0])
    ads = [{'start': s, 'end': s + 10.0} for s in (10.0, 25.0, 40.0, 55.0, 70.0)]
    out = tmp_path / 'out.mp3'
    applied = AudioProcessor(replacement_sound_enabled=False, edge_crossfade_s=0.04).remove_ads(
        str(src), [dict(a) for a in ads], str(out))
    assert applied is not None and len(applied) == 5
    mapped = adjust_timestamp(110.0, applied, 0.0)
    assert mapped == pytest.approx(60.0, abs=0.001)
    # Five crossfaded joins: the burst lands where every timeline map puts it (no 5 x 40 ms drift).
    assert _burst_onset(out) == pytest.approx(mapped, abs=0.015)
    assert AudioProcessor().get_audio_duration(str(out)) == pytest.approx(120.0 - 50.0, abs=0.06)
