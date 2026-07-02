"""Tests for cue-candidate merging (within-episode recurrence + cross-episode).

merge_cue_candidates combines recurring sounds (ad-break stings that repeat within
the episode) with cross-episode intro/outro segments (head/tail audio shared across
sibling episodes), tagging each with a kind and a capture-type hint.
"""
from audio_analysis.cue_candidates import merge_cue_candidates
from config import AUDIO_CUE_TYPE_SHOW_INTRO, AUDIO_CUE_TYPE_SHOW_OUTRO


class TestMergeCueCandidates:
    def test_recurring_tagged_with_kind_and_count(self):
        out = merge_cue_candidates([{'start': 400.0, 'end': 404.0, 'count': 4}], [])
        assert out == [{
            'start': 400.0, 'end': 404.0, 'kind': 'recurring',
            'count': 4, 'suggestedType': None,
            'adBoundaryHits': None, 'boundaryAffinity': None, 'affinitySource': None,
        }]

    def test_intro_tagged_with_suggested_type(self):
        xe = [{'start': 2.0, 'end': 9.0, 'kind': 'intro', 'episodeMatches': 4}]
        out = merge_cue_candidates([], xe)
        assert out == [{
            'start': 2.0, 'end': 9.0, 'kind': 'intro',
            'episodeMatches': 4, 'suggestedType': AUDIO_CUE_TYPE_SHOW_INTRO,
        }]

    def test_outro_tagged_with_suggested_type(self):
        xe = [{'start': 1700.0, 'end': 1718.0, 'kind': 'outro', 'episodeMatches': 3}]
        out = merge_cue_candidates([], xe)
        assert out[0]['kind'] == 'outro'
        assert out[0]['suggestedType'] == AUDIO_CUE_TYPE_SHOW_OUTRO
        assert out[0]['episodeMatches'] == 3

    def test_cross_episode_ranks_before_recurring(self):
        recurring = [{'start': 900.0, 'end': 903.0, 'count': 5}]
        xe = [
            {'start': 2.0, 'end': 9.0, 'kind': 'intro', 'episodeMatches': 4},
            {'start': 1700.0, 'end': 1715.0, 'kind': 'outro', 'episodeMatches': 3},
        ]
        out = merge_cue_candidates(recurring, xe)
        assert [c['kind'] for c in out] == ['intro', 'outro', 'recurring']

    def test_recurring_order_preserved(self):
        # discover_recurring_spots already returns descending recurrence order;
        # the merge keeps it.
        recurring = [
            {'start': 900.0, 'end': 903.0, 'count': 7},
            {'start': 500.0, 'end': 503.0, 'count': 2},
        ]
        out = merge_cue_candidates(recurring, [])
        assert [c['count'] for c in out] == [7, 2]

    def test_recurring_overlapping_cross_episode_is_dropped(self):
        # A sting that both recurs within the episode and is an intro is one sound;
        # keep the typed intro, drop the duplicate recurring hit.
        recurring = [{'start': 3.0, 'end': 9.0, 'count': 4}]
        xe = [{'start': 2.0, 'end': 9.5, 'kind': 'intro', 'episodeMatches': 3}]
        out = merge_cue_candidates(recurring, xe)
        assert [c['kind'] for c in out] == ['intro']

    def test_recurring_not_overlapping_is_kept(self):
        recurring = [{'start': 900.0, 'end': 903.0, 'count': 4}]
        xe = [{'start': 2.0, 'end': 9.0, 'kind': 'intro', 'episodeMatches': 3}]
        out = merge_cue_candidates(recurring, xe)
        assert [c['kind'] for c in out] == ['intro', 'recurring']

    def test_candidate_overlapping_active_template_is_dropped(self):
        # The user already captured this cue; don't re-suggest it.
        recurring = [{'start': 400.0, 'end': 404.0, 'count': 4}]
        xe = [{'start': 2.0, 'end': 9.0, 'kind': 'intro', 'episodeMatches': 3}]
        out = merge_cue_candidates(recurring, xe, templated_spans=[(1.0, 10.0), (399.0, 405.0)])
        assert out == []

    def test_only_new_candidates_survive_template_dedup(self):
        recurring = [{'start': 400.0, 'end': 404.0, 'count': 4}]
        xe = [{'start': 2.0, 'end': 9.0, 'kind': 'intro', 'episodeMatches': 3}]
        out = merge_cue_candidates(recurring, xe, templated_spans=[(1.0, 10.0)])
        assert [c['kind'] for c in out] == ['recurring']

    def test_overlapping_cross_episode_candidates_deduped(self):
        # A long shared segment can yield several near-duplicate runs; keep one.
        xe = [
            {'start': 2.0, 'end': 12.0, 'kind': 'intro', 'episodeMatches': 3},
            {'start': 5.0, 'end': 15.0, 'kind': 'intro', 'episodeMatches': 2},
        ]
        out = merge_cue_candidates([], xe)
        assert len(out) == 1
        assert out[0]['start'] == 2.0

    def test_empty_inputs_give_empty_list(self):
        assert merge_cue_candidates([], []) == []

    def test_merge_passthrough_affinity_fields(self):
        """merge_cue_candidates must pass through suggestedType, adBoundaryHits,
        boundaryAffinity, affinitySource from recurring candidates."""
        recurring = [{
            'start': 400.0, 'end': 404.0, 'count': 4,
            'suggestedType': 'ad_break_boundary',
            'adBoundaryHits': 3,
            'boundaryAffinity': 0.75,
            'affinitySource': 'episode',
        }]
        out = merge_cue_candidates(recurring, [])
        assert out[0]['suggestedType'] == 'ad_break_boundary'
        assert out[0]['adBoundaryHits'] == 3
        assert out[0]['boundaryAffinity'] == 0.75
        assert out[0]['affinitySource'] == 'episode'

    def test_merge_passthrough_none_affinity_fields(self):
        """Null affinity fields must also pass through (no ad history case)."""
        recurring = [{
            'start': 400.0, 'end': 404.0, 'count': 4,
            'suggestedType': None,
            'adBoundaryHits': None,
            'boundaryAffinity': None,
            'affinitySource': None,
        }]
        out = merge_cue_candidates(recurring, [])
        assert out[0]['suggestedType'] is None
        assert out[0]['adBoundaryHits'] is None
