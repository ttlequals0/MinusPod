"""Divider planning for a merged multi-sponsor marker (issue #563).

Candidates come from AD_TRANSITION_PHRASES matches in the span's transcript,
mapped back to the start of the transcript segment each match falls in.
"""

from tests.app_bootstrap import bootstrap

_test_data_dir = bootstrap('split_planning_test_')

from config import MIN_AD_DURATION  # noqa: E402
from split_planning import build_split_candidates, build_split_pieces  # noqa: E402
from utils.text import extract_timed_spans_in_range  # noqa: E402


def _vtt(*rows):
    """rows: (start_seconds, end_seconds, text)."""
    def ts(v):
        return f"{int(v // 3600):02d}:{int(v % 3600 // 60):02d}:{v % 60:06.3f}"
    return '\n'.join(f"[{ts(a)} --> {ts(b)}] {t}" for a, b, t in rows)


# Three back-to-back reads: a sponsor, then a second sponsor introduced by a
# transition phrase, then a third. 30s apart so every piece clears the floor.
THREE_ADS = _vtt(
    (100.0, 130.0, 'Today you can save at Acme dot com with code SAVE.'),
    (130.0, 160.0, 'This episode is brought to you by Beta Corp, the easy way to file.'),
    (160.0, 190.0, 'And our thanks to Gamma Industries for supporting the show.'),
)


def _spans(vtt, start, end):
    return extract_timed_spans_in_range(vtt, start, end)


class TestCandidates:
    def test_finds_the_transition_between_two_reads(self):
        spans = _spans(THREE_ADS, 100.0, 190.0)
        times = [c['time'] for c in build_split_candidates(spans, 100.0, 190.0)]
        assert 130.0 in times

    def test_candidate_reports_the_phrase_that_produced_it(self):
        spans = _spans(THREE_ADS, 100.0, 190.0)
        found = [c for c in build_split_candidates(spans, 100.0, 190.0)
                 if c['time'] == 130.0]
        assert found and 'brought to you by' in found[0]['phrase']

    def test_candidates_are_sorted_by_time(self):
        spans = _spans(THREE_ADS, 100.0, 190.0)
        times = [c['time'] for c in build_split_candidates(spans, 100.0, 190.0)]
        assert times == sorted(times)

    def test_no_transcript_yields_no_candidates(self):
        assert build_split_candidates([], 100.0, 190.0) == []

    def test_span_without_a_transition_phrase_yields_no_candidates(self):
        vtt = _vtt((0.0, 60.0, 'Just ordinary conversation about the weather.'))
        assert build_split_candidates(_spans(vtt, 0.0, 60.0), 0.0, 60.0) == []

    def test_candidate_too_close_to_the_start_is_dropped(self):
        """A phrase opening the block marks the block itself, not a divider."""
        vtt = _vtt(
            (0.0, 3.0, 'This episode is brought to you by Acme.'),
            (3.0, 90.0, 'Acme makes the thing you need, at Acme dot com.'),
        )
        times = [c['time'] for c in build_split_candidates(_spans(vtt, 0.0, 90.0), 0.0, 90.0)]
        assert 0.0 not in times

    def test_candidate_too_close_to_the_end_is_dropped(self):
        vtt = _vtt(
            (0.0, 88.0, 'A long first read for Acme, at Acme dot com.'),
            (88.0, 90.0, 'This episode is brought to you by Beta Corp.'),
        )
        times = [c['time'] for c in build_split_candidates(_spans(vtt, 0.0, 90.0), 0.0, 90.0)]
        assert 88.0 not in times

    def test_every_candidate_leaves_room_for_a_real_ad(self):
        spans = _spans(THREE_ADS, 100.0, 190.0)
        for c in build_split_candidates(spans, 100.0, 190.0):
            assert c['time'] - 100.0 >= MIN_AD_DURATION
            assert 190.0 - c['time'] >= MIN_AD_DURATION

    def test_near_duplicate_candidates_collapse(self):
        """Two phrases inside one short window describe one boundary."""
        vtt = _vtt(
            (0.0, 40.0, 'Opening content for the show goes here at length.'),
            (40.0, 42.0, 'This episode is brought to you by Acme.'),
            (42.0, 44.0, 'Sponsored by Acme, the best in the business.'),
            (44.0, 120.0, 'Acme really does make a fine product, at Acme dot com.'),
        )
        times = [c['time'] for c in build_split_candidates(_spans(vtt, 0.0, 120.0), 0.0, 120.0)]
        assert len(times) == len(set(times))
        for a, b in zip(times, times[1:]):
            assert b - a >= MIN_AD_DURATION


class TestPieces:
    def test_no_dividers_gives_one_piece_covering_the_span(self):
        spans = _spans(THREE_ADS, 100.0, 190.0)
        pieces = build_split_pieces(spans, 100.0, 190.0, [])
        assert len(pieces) == 1
        assert (pieces[0]['start'], pieces[0]['end']) == (100.0, 190.0)

    def test_one_divider_gives_two_adjacent_pieces(self):
        spans = _spans(THREE_ADS, 100.0, 190.0)
        pieces = build_split_pieces(spans, 100.0, 190.0, [130.0])
        assert [(p['start'], p['end']) for p in pieces] == [(100.0, 130.0), (130.0, 190.0)]

    def test_two_dividers_give_three_pieces_in_order(self):
        spans = _spans(THREE_ADS, 100.0, 190.0)
        pieces = build_split_pieces(spans, 100.0, 190.0, [160.0, 130.0])
        assert [(p['start'], p['end']) for p in pieces] == [
            (100.0, 130.0), (130.0, 160.0), (160.0, 190.0)]

    def test_each_piece_carries_its_own_text(self):
        spans = _spans(THREE_ADS, 100.0, 190.0)
        pieces = build_split_pieces(spans, 100.0, 190.0, [130.0])
        assert 'Acme' in pieces[0]['text']
        assert 'Beta Corp' in pieces[1]['text']

    def test_boundaries_outside_the_span_are_ignored(self):
        spans = _spans(THREE_ADS, 100.0, 190.0)
        pieces = build_split_pieces(spans, 100.0, 190.0, [50.0, 130.0, 900.0])
        assert [(p['start'], p['end']) for p in pieces] == [(100.0, 130.0), (130.0, 190.0)]

    def test_sponsor_is_guessed_per_piece_or_left_unset(self):
        spans = _spans(THREE_ADS, 100.0, 190.0)
        for piece in build_split_pieces(spans, 100.0, 190.0, [130.0]):
            assert piece['sponsor'] is None or isinstance(piece['sponsor'], str)

    def test_empty_transcript_still_produces_the_piece_geometry(self):
        """No transcript is not a reason to refuse a manual split."""
        pieces = build_split_pieces([], 100.0, 190.0, [130.0])
        assert [(p['start'], p['end']) for p in pieces] == [(100.0, 130.0), (130.0, 190.0)]
        assert all(p['text'] == '' for p in pieces)
