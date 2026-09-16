"""Divider planning for a merged multi-sponsor marker (issue #563).

Candidates come from AD_TRANSITION_PHRASES matches in the span's transcript,
the member spans a merge recorded, a clean handoff from one brand to another,
and measured cut times, each mapped back to a transcript segment start.
"""

import re

from tests.app_bootstrap import bootstrap

_test_data_dir = bootstrap('split_planning_test_')

from config import MIN_AD_DURATION  # noqa: E402
from split_planning import (  # noqa: E402
    build_split_candidates, build_split_pieces, marker_split_sources)
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
        for a, b in zip(times, times[1:], strict=False):  # pairwise adjacent walk
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
        assert 'Beta Corp' not in pieces[0]['text']
        assert 'Acme' not in pieces[1]['text']

    def test_a_span_touching_a_boundary_lands_in_one_piece_only(self):
        spans = _spans(THREE_ADS, 100.0, 190.0)
        pieces = build_split_pieces(spans, 100.0, 190.0, [130.0, 160.0])
        assert 'Beta Corp' not in pieces[0]['text']
        assert 'Gamma Industries' not in pieces[1]['text']
        assert 'Acme' not in pieces[1]['text']
        assert 'Beta Corp' not in pieces[2]['text']

    def test_sponsor_guess_does_not_leak_from_a_neighbouring_piece(self):
        spans = _spans(THREE_ADS, 100.0, 190.0)
        pieces = build_split_pieces(spans, 100.0, 190.0, [130.0, 160.0])
        # Piece one has no URL or transition phrase, so no guess at all.
        assert [p['sponsor'] for p in pieces] == [
            None, 'Beta Corp', 'Gamma Industries']

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


# Two back-to-back reads with no handoff phrase between them: the shape that
# produced no candidates at all, so a merged multi-brand block was never split.
TWO_BRANDS_NO_PHRASE = _vtt(
    (100.0, 130.0, 'Acme makes the thing you need. Acme is worth a look.'),
    (130.0, 160.0, 'Beta Corp files your taxes fast. Beta Corp is easy.'),
)


class TestBrandCandidates:
    def test_a_brand_handoff_is_a_divider_labeled_with_the_incoming_brand(self):
        spans = _spans(TWO_BRANDS_NO_PHRASE, 100.0, 160.0)

        found = build_split_candidates(spans, 100.0, 160.0,
                                       brands=['Acme', 'Beta Corp'])

        assert [(c['time'], c['phrase']) for c in found] == [(130.0, 'Beta Corp')]
        # One brand is one read, however many times it is named.
        assert build_split_candidates(spans, 100.0, 160.0, brands=['Acme']) == []

    def test_interleaved_brands_are_not_a_handoff(self):
        """Two brands talked about together are one read, not two."""
        vtt = _vtt(
            (0.0, 40.0, 'Acme and Beta Corp both make it.'),
            (40.0, 90.0, 'Beta Corp buys from Acme every year.'),
        )
        assert build_split_candidates(_spans(vtt, 0.0, 90.0), 0.0, 90.0,
                                      brands=['Acme', 'Beta Corp']) == []

    def test_a_brand_inside_a_longer_word_is_not_a_mention(self):
        vtt = _vtt(
            (0.0, 40.0, 'Acme is the one we use every single day here.'),
            (40.0, 90.0, 'Betacorporation was never mentioned by name.'),
        )
        assert build_split_candidates(_spans(vtt, 0.0, 90.0), 0.0, 90.0,
                                      brands=['Acme', 'Beta Corp']) == []

    def test_aliases_count_as_the_same_brand(self):
        vtt = _vtt(
            (0.0, 40.0, 'Acme is the one we use. Acme Co ships it fast.'),
            (40.0, 90.0, 'Nothing else is advertised in this stretch at all.'),
        )
        rows = [{'name': 'Acme', 'aliases': '["Acme Co"]'}]
        assert build_split_candidates(_spans(vtt, 0.0, 90.0), 0.0, 90.0,
                                      brands=rows) == []


class TestCompiledMatchersAreReused:
    """SponsorService already holds one compiled matcher per registry brand;
    split planning rebuilt them for every span it planned."""

    def test_a_supplied_matcher_is_used_instead_of_a_fresh_one(self):
        vtt = _vtt(
            (0.0, 40.0, 'Acme is the one we use every single day here.'),
            (40.0, 90.0, 'Their rival ships it faster for half the price.'),
        )
        spans = _spans(vtt, 0.0, 90.0)
        # Deliberately matches a word the brand name does not: the candidate
        # can only come from the supplied pattern.
        compiled = {'Beta Corp': re.compile(r'rival', re.IGNORECASE)}

        found = build_split_candidates(spans, 0.0, 90.0,
                                       brands=['Acme', 'Beta Corp'],
                                       compiled=compiled)

        assert [(c['time'], c['phrase']) for c in found] == [(40.0, 'Beta Corp')]


class TestMemberAndCutCandidates:
    MEMBERS = [{'start': 100.0, 'end': 130.0, 'sponsor': 'Acme'},
               {'start': 130.0, 'end': 190.0, 'sponsor': 'Beta Corp'}]

    def test_a_member_boundary_is_a_divider_named_after_its_sponsor(self):
        found = build_split_candidates([], 100.0, 190.0, members=self.MEMBERS)

        # The first member's start opens the block; only 130.0 is interior.
        assert [(c['time'], c['phrase']) for c in found] == [(130.0, 'Beta Corp')]

    def test_a_member_without_a_sponsor_still_proposes_a_divider(self):
        members = [{'start': 100.0, 'end': 130.0}, {'start': 130.0, 'end': 190.0}]
        found = build_split_candidates([], 100.0, 190.0, members=members)
        assert [c['time'] for c in found] == [130.0]

    def test_a_measured_cut_is_a_divider(self):
        found = build_split_candidates([], 100.0, 190.0, cuts=[145.0])
        assert [(c['time'], c['phrase']) for c in found] == [(145.0, 'measured cut')]

    def test_sources_agreeing_on_one_boundary_collapse_to_one_divider(self):
        spans = _spans(THREE_ADS, 100.0, 190.0)
        found = build_split_candidates(
            spans, 100.0, 190.0,
            members=[{'start': 100.0, 'end': 131.0},
                     {'start': 131.0, 'end': 190.0}],
            cuts=[129.0])
        assert len([c for c in found if 125.0 <= c['time'] <= 135.0]) == 1

    def test_candidates_from_every_source_stay_sorted(self):
        spans = _spans(THREE_ADS, 100.0, 190.0)
        times = [c['time'] for c in build_split_candidates(
            spans, 100.0, 190.0, members=self.MEMBERS, cuts=[175.0])]
        assert times == sorted(times)


class TestMarkerSplitSources:
    def test_member_spans_and_sponsors_are_read_from_the_marker(self):
        members, cuts = marker_split_sources({'merged_member_spans': [
            {'start': 130.0, 'end': 190.0, 'sponsor': 'Beta Corp'},
            {'start': 100.0, 'end': 130.0, 'sponsor': 'Acme'},
        ]})
        assert [m['start'] for m in members] == [100.0, 130.0]
        assert [m['sponsor'] for m in members] == ['Acme', 'Beta Corp']
        assert cuts == []

    def test_malformed_member_entries_are_dropped(self):
        members, _ = marker_split_sources({'merged_member_spans': [
            'not a dict', {'start': 'x', 'end': 1.0}, {'start': 5.0, 'end': 5.0},
            {'start': 100.0, 'end': 130.0},
        ]})
        assert [(m['start'], m['end']) for m in members] == [(100.0, 130.0)]

    def test_gaps_between_dai_core_spans_become_cuts(self):
        _, cuts = marker_split_sources({'dai_core_spans': [
            {'start': 100.0, 'end': 128.0}, {'start': 131.0, 'end': 190.0},
        ]})
        assert cuts == [131.0]

    def test_a_marker_with_no_merge_record_yields_nothing(self):
        assert marker_split_sources({}) == ([], [])


class TestPiecesNamedByBrand:
    def test_a_piece_is_credited_only_when_it_names_one_brand(self):
        spans = _spans(TWO_BRANDS_NO_PHRASE, 100.0, 160.0)
        brands = ['Acme', 'Beta Corp']

        split = build_split_pieces(spans, 100.0, 160.0, [130.0], brands=brands)
        whole = build_split_pieces(spans, 100.0, 160.0, [], brands=brands)

        assert [p['sponsor'] for p in split] == ['Acme', 'Beta Corp']
        # Two brands in one piece is ambiguous: the generic extractor answers.
        assert whole[0]['sponsor'] is None
