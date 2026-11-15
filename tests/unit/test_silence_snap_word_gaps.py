"""Word-gap mode of silence snap: transcript boundary, then the real pause (cases 1, 3, 4)."""
import json
import pathlib

from tests.app_bootstrap import bootstrap

bootstrap('silence_snap_word_gaps_test_')

import pytest

from ad_detector import silence_boundary_snap as snap
from config import CUE_MATCH_STAGE
from main_app.pipeline import cuts

FIXTURES = pathlib.Path(__file__).resolve().parents[1] / 'fixtures' / 'cut_edges'


def _case(name):
    return json.loads((FIXTURES / name).read_text())


def _reader(case):
    profile = case['energy'][0]
    t0, frames = profile['t0'], profile['rms_dbfs']

    def read(a, b):
        i, j = round((a - t0) / 0.01), round((b - t0) / 0.01)
        return frames[i:j] if 0 <= i < j <= len(frames) else None
    return read


def _flat(pauses, level=-30.0, quiet=-60.0, lo=0.0, hi=400.0):
    """Reader: speech level everywhere except the quiet (start, end) spans."""
    def read(a, b):
        a, b = max(a, lo), min(b, hi)
        out = []
        for i in range(round((b - a) / 0.01)):
            t = a + i * 0.01
            out.append(quiet if any(p <= t < q for p, q in pauses) else level)
        return out
    return read


def _edge(edge, side, words, reader):
    return snap._snap_word_edge(edge, side, words, energy_reader=reader)


def _words(*items):
    return [{'word': w, 'start': s, 'end': e} for w, s, e in items]


def _segments(words):
    return [{'start': words[0]['start'], 'end': words[-1]['end'], 'text': ' '.join(
        w['word'] for w in words), 'words': words}]


def _marker_edges(marker, segments, reader, *, prior_end=None, next_start=None, duration=4000.0,
                  spans=None):
    """The word-gap mode on one marker: {start, end, edge_snap, edge_pause_s}, or None if nothing moved."""
    ad = dict(marker)
    neighbours = [{'start': prior_end - 30.0, 'end': prior_end}] if prior_end is not None else []
    if next_start is not None:
        neighbours.append({'start': next_start, 'end': next_start + 30.0})
    snap.snap_ad_boundaries_to_silence([ad], spans or [], 2.0, 0.3, word_segments=segments,
                                       energy_reader=reader, neighbours=neighbours, duration=duration)
    if 'edge_snap' not in ad:
        return None
    return {k: ad[k] for k in ('start', 'end', 'edge_snap', 'edge_pause_s')}


def _snap(case, marker=None, **kw):
    return _marker_edges(marker or case['marker'], case['segments'], _reader(case), **kw)


def test_case4_start_snaps_to_pause_end():
    case = _case('case4_example_cleanslate_sun.json')
    lo, hi = case['expected']['expected_start_tolerance']
    update = _snap(case)
    assert lo <= update['start'] <= hi
    assert update['end'] == pytest.approx(case['expected']['expected_end'], abs=0.05)
    assert update['edge_snap']['start']['acoustic'] is True
    assert update['edge_pause_s']['start'] >= 0.5


def test_case1_splice_anchored_start_untouched():
    case = _case('case1_acme_health_leadin.json')
    marker = {**case['marker'], 'start': 2419.13, 'edge_splice_anchor': ['start']}
    update = _snap(case, marker)
    assert update is None or update['start'] == 2419.13


def test_case3_splice_end_untouched():
    case = _case('case3_example_opal_tail_splice.json')
    marker = {**case['marker'], 'end': 1442.93, 'edge_splice_anchor': ['end']}
    update = _snap(case, marker)
    assert update is None or (update['end'] == 1442.93
                              and update['start'] == pytest.approx(1369.5, abs=0.05))


def test_sentence_boundary_preferred_within_1_5s():
    words = _words(('one', 99.0, 99.4), ('two.', 99.5, 100.0), ('three', 100.9, 101.2),
                   ('four', 101.3, 101.8), ('five', 101.9, 102.4))
    # Word gaps lie at 99.5 and 101.3 (the edge is 100.2, inside nothing); the sentence start wins.
    assert snap._transcript_candidate(100.2, 'start', words) == pytest.approx(100.9)
    assert snap._transcript_candidate(100.2, 'end', words) == pytest.approx(100.0)


def test_word_gap_within_0_6s():
    words = _words(('one', 99.0, 99.4), ('two', 99.5, 100.0), ('three', 100.6, 101.2))
    assert snap._transcript_candidate(100.45, 'start', words) == pytest.approx(100.6)
    assert snap._transcript_candidate(105.0, 'start', words) is None


def test_no_pause_moves_only_to_a_near_word_gap():
    words = _words(('one', 99.0, 99.4), ('two.', 99.5, 100.6), ('three', 100.9, 101.2))
    # Edge inside "two.": no pause in the audio, so the nearest word gap within 0.6 s.
    assert _edge(100.4, 'start', words, _flat([])) == (pytest.approx(100.9), None, False)
    update = _marker_edges({'start': 100.4, 'end': 160.0}, _segments(words), _flat([]))
    assert update['start'] == pytest.approx(100.9)
    assert update['edge_snap']['start']['acoustic'] is False


def test_no_pause_edge_in_word_gap_stays():
    # Probe from the review: the start sits in the gap after "text."; no audio evidence, no move.
    words = _words(('end.', 99.0, 99.3), ('Ad', 100.5, 100.8), ('text.', 101.0, 101.3),
                   ('more', 103.0, 103.3))
    assert _edge(101.45, 'start', words, _flat([])) == (101.45, None, False)
    assert _marker_edges({'start': 101.45, 'end': 130.0}, _segments(words), _flat([])) is None


def test_no_pause_never_crosses_a_sentence():
    words = _words(('one.', 99.0, 99.4), ('two', 100.5, 100.9), ('three', 101.0, 101.8))
    # Edge inside "three" (101.4): the near gap is 101.0, and the sentence start 100.5 is farther.
    assert _edge(101.4, 'start', words, _flat([]))[0] == pytest.approx(101.0)
    assert _edge(100.0, 'start', words, _flat([]))[0] == 100.0


def test_pause_placement_per_side():
    words = _words(('one', 99.0, 99.4), ('two.', 99.5, 100.0), ('three', 100.9, 101.2),
                   ('four.', 101.3, 101.8), ('five', 102.6, 103.0))
    reader = _flat([(100.1, 100.85), (101.85, 102.55)])
    start, pause, _ = _edge(100.95, 'start', words, reader)
    assert start == pytest.approx(100.85 - 0.03) and pause == pytest.approx(0.75, abs=0.02)
    end, _, _ = _edge(101.6, 'end', words, reader)
    assert end == pytest.approx(101.85 + 0.03)


def test_dai_core_edge_untouched():
    case = _case('case4_example_cleanslate_sun.json')
    marker = {**case['marker'], 'dai_core_spans': [{'start': 1860.37, 'end': 1932.54}]}
    update = _snap(case, marker)
    assert update is None or update['start'] == 1860.37


def test_user_confirmed_edge_untouched():
    case = _case('case4_example_cleanslate_sun.json')
    marker = {**case['marker'], 'validation': {'user_confirmed': True}}
    update = _snap(case, marker)
    assert update is None or update['start'] == 1860.37


def test_cue_cut_marker_edges_untouched():
    case = _case('case4_example_cleanslate_sun.json')
    assert _snap(case, {**case['marker'], 'detection_stage': CUE_MATCH_STAGE}) is None


def test_fingerprint_measured_edge_untouched_and_an_unmeasured_one_still_moves():
    case = _case('case4_example_cleanslate_sun.json')
    member = {'start': 1860.37, 'end': 1932.54, 'stage': 'fingerprint',
              'fingerprint_match_start': 1860.37, 'fingerprint_match_end': 1932.54}
    measured = {**case['marker'], 'merged_member_spans': case['marker']['merged_member_spans'] + [member]}
    assert _snap(case, measured) is None
    later = {**member, 'fingerprint_match_start': 1866.0}
    unmeasured = {**case['marker'], 'merged_member_spans': case['marker']['merged_member_spans'] + [later]}
    lo, hi = case['expected']['expected_start_tolerance']
    assert lo <= _snap(case, unmeasured)['start'] <= hi


def test_short_ask_edge_stays_within_cap():
    words = _words(('one.', 99.0, 99.4), ('two', 100.1, 100.5), ('three', 100.6, 101.0))
    reader = _flat([(100.3, 100.7)])
    marker = {'start': 99.9, 'end': 120.0, 'short_ask': True, 'short_ask_origin': [98.5, 120.0]}
    # The pause would put the start 2.2 s from where the detector placed it: over the 1.5 s cap.
    assert _marker_edges(marker, _segments(words), reader, duration=4000.0) is None
    near = {**marker, 'short_ask_origin': [99.5, 120.0]}
    update = _marker_edges(near, _segments(words), reader, duration=4000.0)
    assert update is not None and 99.9 < update['start'] <= 99.5 + 1.5


def test_floor_respected():
    words = _words(('one.', 99.0, 99.4), ('two', 100.1, 100.5), ('three', 100.6, 101.0))
    reader = _flat([(100.15, 100.55)])
    # 9.9 s is already under the 10 s floor and 10.2 s would drop under it: both refuse to shrink.
    for end in (109.7, 110.0):
        assert _marker_edges({'start': 99.8, 'end': end}, _segments(words), reader) is None
    roomy = _marker_edges({'start': 99.8, 'end': 112.0}, _segments(words), reader)
    assert roomy['start'] == pytest.approx(100.55 - 0.03)


def test_never_crosses_neighbour_marker():
    words = _words(('one.', 99.0, 99.4), ('two', 100.6, 101.2), ('three', 101.3, 101.8))
    reader = _flat([(99.45, 100.55)])
    marker = {'start': 100.9, 'end': 160.0}
    free = _marker_edges(marker, _segments(words), reader, duration=4000.0)
    assert free['start'] == pytest.approx(100.55 - 0.03)
    # A marker ending at 100.6 bars the move: the edge stays where it was.
    assert _marker_edges(marker, _segments(words), reader, prior_end=100.6) is None


def test_idempotent():
    case = _case('case4_example_cleanslate_sun.json')
    first = _snap(case)
    again = _snap(case, {**case['marker'], **first})
    assert again['start'] == pytest.approx(first['start'])
    assert again['end'] == pytest.approx(first['end'])


def test_cuts_helper_updates_the_master_and_returns_new_cuts(monkeypatch):
    case = _case('case4_example_cleanslate_sun.json')
    saved = []
    monkeypatch.setattr(cuts, 'resolve_precise_cut_edges', lambda db: True)
    monkeypatch.setattr(cuts.storage, 'save_combined_ads', lambda *a: saved.append(a))
    master = dict(case['marker'])
    out = cuts._snap_cut_edges_to_words(
        'slug', 'ep', [master], [master], case['segments'], None, 4000.0,
        energy_reader=_reader(case))
    assert out[0] is not master and out[0]['start'] > 1860.6
    assert master['start'] == out[0]['start'] and master['edge_pause_s']['start'] >= 0.5
    assert saved


def _analyzer_spans(case, noise_db=-50.0, min_s=0.3):
    """What silencedetect at the silence-snap defaults reports over the fixture profile."""
    profile = case['energy'][0]
    t0, frames = profile['t0'], profile['rms_dbfs']
    spans, run = [], None
    for i, level in enumerate([*frames, 0.0]):
        if level <= noise_db and run is None:
            run = i
        elif level > noise_db and run is not None:
            if (i - run) * 0.01 >= min_s:
                start, end = t0 + run * 0.01, t0 + i * 0.01
                spans.append({'start': start, 'end': end, 'duration': end - start})
            run = None
    return spans


def test_case4_analyzer_spans_place_the_start_without_reading_audio():
    case = _case('case4_example_cleanslate_sun.json')
    lo, hi = case['expected']['expected_start_tolerance']

    def no_read(a, b):
        raise AssertionError('the energy reader is only a fallback')
    update = _marker_edges(case['marker'], case['segments'], no_read, spans=_analyzer_spans(case))
    assert lo <= update['start'] <= hi
    assert update['edge_snap']['start']['acoustic'] is True


def test_spans_shorter_than_min_silence_are_not_pauses():
    words = _words(('one.', 99.0, 99.4), ('two', 100.1, 100.5), ('three', 100.6, 101.0))
    spans = [{'start': 100.0, 'end': 100.1, 'duration': 0.1}]
    update = _marker_edges({'start': 100.3, 'end': 160.0}, _segments(words), _flat([]), spans=spans)
    # No pause qualifies and the audio has none; the edge inside "two" goes to the near word gap.
    assert update['edge_snap']['start']['acoustic'] is False


@pytest.mark.parametrize('key', ['cue_snap', 'silence_snap'])
def test_edge_placed_by_cue_or_silence_snap_is_skipped(key):
    case = _case('case4_example_cleanslate_sun.json')
    marker = {**case['marker'], key: {'start': {'snap_point': 1860.37}}}
    update = _snap(case, marker)
    assert update is None or update['start'] == 1860.37


def test_merge_gap_guard_keeps_a_neighbour_one_second_away():
    words = _words(('one.', 99.0, 99.4), ('two', 100.6, 101.2), ('three', 101.3, 101.8))
    reader = _flat([(99.45, 100.55)])
    marker = {'start': 100.9, 'end': 160.0}
    # The pause would bring the start 0.6 s from a marker ending at 99.9 (was 1.0 s): refused.
    assert _marker_edges(marker, _segments(words), reader, prior_end=99.9) is None
    assert _marker_edges(marker, _segments(words), reader, prior_end=99.0)['start'] == pytest.approx(100.52)


def test_without_word_segments_nothing_word_related_is_recorded():
    ads = [{'start': 100.4, 'end': 160.2}]
    snap.snap_ad_boundaries_to_silence(ads, [{'start': 99.2, 'end': 99.8, 'duration': 0.6}], 2.0, 0.3)
    assert 'edge_snap' not in ads[0] and 'edge_pause_s' not in ads[0]


def test_an_edge_with_no_span_near_it_reads_the_audio():
    case = _case('case4_example_cleanslate_sun.json')
    lo, hi = case['expected']['expected_start_tolerance']
    far = [{'start': 10.0, 'end': 10.5, 'duration': 0.5}]
    update = _marker_edges(case['marker'], case['segments'], _reader(case), spans=far)
    assert lo <= update['start'] <= hi and update['edge_pause_s']['start'] >= 0.5


def test_a_span_out_of_reach_still_lets_the_audio_place_the_edge():
    words = _words(('one.', 99.0, 99.4), ('two', 100.1, 100.5), ('three', 100.6, 101.0))
    far = [{'start': 98.75, 'end': 98.85, 'duration': 0.1}]  # inside the read window, beyond reach
    edge, pause, acoustic = snap._snap_word_edge(100.3, 'start', words, spans=far,
                                                 energy_reader=_flat([(99.45, 100.05)]))
    assert acoustic is True and abs(edge - 100.02) < 0.011 and pause > 0.5
