from __future__ import annotations

import pytest
from types import SimpleNamespace

from benchmark import jev
from benchmark.truth_parser import Ad


def seg(sid: int, start: float, end: float, text: str = "words") -> dict:
    return {"sid": sid, "start": start, "end": end, "text": text}


def contiguous(n: int, *, length: float = 10.0, start: float = 0.0) -> list[dict]:
    return [seg(i, start + i * length, start + (i + 1) * length) for i in range(n)]


# Run recovery is tested against fixed thresholds, not the tuned defaults:
# ENTER/STAY are fitted to corpus data and are expected to move.
ENTER, STAY = 0.60, 0.40


def spans(segments, probabilities, **kw):
    kw.setdefault('enter', ENTER)
    kw.setdefault('stay', STAY)
    return jev.spans_from_probabilities(segments, probabilities, **kw)


class TestSpansFromProbabilities:
    def test_empty_when_nothing_clears_enter(self):
        segs = contiguous(5)
        probs = {i: 0.5 for i in range(5)}
        assert spans(segs, probs) == []

    def test_single_run_spans_segment_edges(self):
        segs = contiguous(5)
        probs = {0: 0.0, 1: 0.9, 2: 0.9, 3: 0.0, 4: 0.0}
        (ad,) = spans(segs, probs)
        assert (ad["start"], ad["end"]) == (10.0, 30.0)
        assert (ad["start_id"], ad["end_id"]) == (1, 2)

    def test_boundaries_are_never_interpolated(self):
        segs = [seg(0, 0.0, 7.3), seg(1, 7.3, 21.9), seg(2, 21.9, 30.0)]
        probs = {0: 0.0, 1: 0.95, 2: 0.0}
        (ad,) = spans(segs, probs)
        assert ad["start"] == 7.3
        assert ad["end"] == 21.9

    def test_hysteresis_bridges_a_weak_middle_segment(self):
        segs = contiguous(5)
        probs = {0: 0.0, 1: 0.9, 2: 0.45, 3: 0.9, 4: 0.0}
        recovered = spans(segs, probs)
        assert len(recovered) == 1
        assert (recovered[0]["start"], recovered[0]["end"]) == (10.0, 40.0)

    def test_run_of_only_weak_segments_is_not_an_ad(self):
        segs = contiguous(4)
        probs = {i: 0.45 for i in range(4)}
        assert spans(segs, probs) == []

    def test_confidence_is_the_run_maximum(self):
        segs = contiguous(3)
        probs = {0: 0.62, 1: 0.97, 2: 0.55}
        (ad,) = spans(segs, probs)
        assert ad["confidence"] == pytest.approx(0.97)

    def test_run_breaks_across_a_wide_silence_gap(self):
        # Adjacent in the list, 40s apart in time: two breaks, not one.
        segs = [seg(0, 0.0, 30.0), seg(1, 70.0, 100.0)]
        probs = {0: 0.9, 1: 0.9}
        recovered = spans(segs, probs)
        assert [(s["start"], s["end"]) for s in recovered] == [(0.0, 30.0), (70.0, 100.0)]

    def test_run_survives_a_narrow_silence_gap(self):
        segs = [seg(0, 0.0, 30.0), seg(1, 35.0, 60.0)]
        probs = {0: 0.9, 1: 0.9}
        (ad,) = spans(segs, probs)
        assert (ad["start"], ad["end"]) == (0.0, 60.0)

    def test_missing_probability_raises(self):
        segs = contiguous(3)
        with pytest.raises(ValueError):
            spans(segs, {1: 0.9})

    def test_unsorted_input_is_ordered_by_time(self):
        segs = list(reversed(contiguous(4)))
        probs = {0: 0.1, 1: 0.9, 2: 0.9, 3: 0.1}
        (ad,) = spans(segs, probs)
        assert (ad["start"], ad["end"]) == (10.0, 30.0)

    def test_bridges_a_low_scoring_speech_dip(self):
        # A cross-promo playing a clip of the show it advertises: the clip
        # reads as conversation and scores low, mid-break.
        segs = contiguous(5, length=20.0)
        probs = {0: 0.98, 1: 0.98, 2: 0.05, 3: 0.98, 4: 0.98}
        (ad,) = spans(segs, probs, enter=0.95, bridge=30.0)
        assert (ad["start"], ad["end"]) == (0.0, 100.0)

    def test_does_not_bridge_empty_silence(self):
        # No segment in the gap means silence, which is what separates two
        # breaks. Bridging it would undo the split.
        segs = [seg(0, 0.0, 30.0), seg(1, 55.0, 85.0)]
        probs = {0: 0.98, 1: 0.98}
        recovered = spans(segs, probs, enter=0.95, bridge=60.0, max_gap=10.0)
        assert len(recovered) == 2

    def test_dip_longer_than_the_bridge_still_splits(self):
        segs = contiguous(5, length=20.0)
        probs = {0: 0.98, 1: 0.98, 2: 0.05, 3: 0.98, 4: 0.98}
        recovered = spans(segs, probs, enter=0.95, bridge=10.0)
        assert len(recovered) == 2

    def test_bridge_off_by_default_in_helper(self):
        segs = contiguous(5, length=20.0)
        probs = {0: 0.98, 1: 0.98, 2: 0.05, 3: 0.98, 4: 0.98}
        assert len(spans(segs, probs, enter=0.95, bridge=0.0)) == 2

    def test_bridge_needs_a_confident_run_on_both_sides(self):
        # The trailing segments never reach enter, so there is no second run
        # to join and the bridge has nothing to do.
        segs = contiguous(4, length=20.0)
        probs = {0: 0.98, 1: 0.05, 2: 0.50, 3: 0.50}
        (ad,) = spans(segs, probs, enter=0.95, bridge=30.0)
        assert ad["end"] == 20.0

    def test_stay_above_enter_is_rejected(self):
        with pytest.raises(ValueError):
            jev.spans_from_probabilities(contiguous(2), {}, enter=0.4, stay=0.8)


class TestContainedSegments:
    """Chunked transcription emits a segment wholly inside its predecessor at"""

    def test_span_end_is_the_furthest_end_not_the_last_member(self):
        segs = [seg(0, 0.0, 10.0), seg(1, 10.0, 40.0), seg(2, 10.1, 16.0)]
        probs = {0: 0.0, 1: 0.98, 2: 0.98}
        (ad,) = spans(segs, probs, enter=0.95)
        assert ad["end"] == 40.0
        assert ad["end_id"] == 1

    def test_gap_is_measured_from_the_furthest_end_reached(self):
        # Without a running frontier the gap to sid 3 is measured from sid 2's
        # end (16.0), reading as 24s and splitting a run that is contiguous.
        segs = [seg(0, 0.0, 40.0), seg(1, 0.1, 16.0), seg(2, 40.0, 70.0)]
        probs = {0: 0.98, 1: 0.98, 2: 0.98}
        recovered = spans(segs, probs, enter=0.95, max_gap=20.0)
        assert len(recovered) == 1
        assert (recovered[0]["start"], recovered[0]["end"]) == (0.0, 70.0)

    def test_bridge_wall_is_measured_from_the_furthest_end_reached(self):
        # The contained sid 1 must not make the wall to the second run look
        # wider than it is and block a bridge that should fire.
        segs = [seg(0, 0.0, 40.0), seg(1, 0.1, 16.0),
                seg(2, 50.0, 60.0), seg(3, 60.0, 90.0)]
        probs = {0: 0.98, 1: 0.98, 2: 0.05, 3: 0.98}
        recovered = spans(segs, probs, enter=0.95, max_gap=5.0, bridge=25.0)
        assert len(recovered) == 1
        assert recovered[0]["end"] == 90.0

    def test_containment_does_not_invent_a_detection(self):
        segs = [seg(0, 0.0, 40.0), seg(1, 0.1, 16.0)]
        probs = {0: 0.10, 1: 0.10}
        assert spans(segs, probs, enter=0.95) == []


class TestOracleProbabilities:
    def test_overlap_marks_any_intersection(self):
        segs = [seg(0, 0.0, 20.0), seg(1, 20.0, 40.0)]
        probs = jev.oracle_probabilities(segs, [Ad(18.0, 25.0, "x")], policy="overlap")
        assert probs == {0: 1.0, 1: 1.0}

    def test_majority_requires_more_than_half_the_segment(self):
        segs = [seg(0, 0.0, 20.0), seg(1, 20.0, 40.0)]
        probs = jev.oracle_probabilities(segs, [Ad(18.0, 35.0, "x")], policy="majority")
        assert probs == {0: 0.0, 1: 1.0}

    def test_no_truth_ads_marks_nothing(self):
        probs = jev.oracle_probabilities(contiguous(3), [], policy="overlap")
        assert set(probs.values()) == {0.0}

    def test_unknown_policy_is_rejected(self):
        with pytest.raises(ValueError):
            jev.oracle_probabilities(contiguous(1), [], policy="nonsense")


class TestPayload:
    def test_criteria_is_not_repeated_per_question(self):
        payload = jev.build_payload(contiguous(3))
        assert all("criteria" not in q for q in payload["questions"].values())
        assert "guidance" in payload["state"]

    def test_instructions_name_the_line_id(self):
        payload = jev.build_payload([seg(42, 0.0, 1.0)])
        assert "L0042" in payload["questions"]["s42"]["instructions"]

    def test_state_lines_carry_ids(self):
        state = jev.build_state([seg(7, 0.0, 1.0, "hello there")])
        assert state == "L0007| hello there"

    def test_uid_makes_a_repeat_a_distinct_draw(self):
        a = jev.build_payload(contiguous(2), uid="pass-1")
        b = jev.build_payload(contiguous(2), uid="pass-2")
        assert a["state"]["uid"] != b["state"]["uid"]
        assert a["questions"] == b["questions"]

    def test_no_uid_key_when_not_requested(self):
        assert "uid" not in jev.build_payload(contiguous(2))["state"]

    def test_metadata_lands_in_state(self):
        meta = SimpleNamespace(
            podcast_name="Crime Junkie", title="MISSING: Cole",
            description="A man vanishes in rural North Carolina.")
        state = jev.build_payload(contiguous(2), metadata=meta)["state"]
        assert state["podcast"] == "Crime Junkie"
        assert state["episode_title"] == "MISSING: Cole"
        assert "North Carolina" in state["episode_description"]

    def test_empty_description_is_omitted(self):
        meta = SimpleNamespace(podcast_name="S", title="T", description="")
        state = jev.build_payload(contiguous(2), metadata=meta)["state"]
        assert "episode_description" not in state

    def test_full_guidance_restores_the_dropped_rules(self):
        assert len(jev.GUIDANCE_FULL) > len(jev.GUIDANCE)
        for rule in ("tagline", "Acast", "dead air", "Patreon"):
            assert rule in jev.GUIDANCE_FULL


class TestConfirmPolicy:
    def test_accepts_a_clear_ad(self):
        assert jev.ConfirmPolicy().accepts(
            {"is_ad": 0.98, "promotional_language": 0.98})

    def test_rejects_below_is_ad(self):
        assert not jev.ConfirmPolicy().accepts(
            {"is_ad": 0.1, "promotional_language": 0.98})

    def test_rejects_without_promotional_language(self):
        assert not jev.ConfirmPolicy().accepts(
            {"is_ad": 0.98, "promotional_language": 0.05})

    def test_either_exclusion_signal_rejects(self):
        base = {"is_ad": 0.98, "promotional_language": 0.98}
        assert not jev.ConfirmPolicy().accepts({**base, "guest_own_work": 0.9})
        assert not jev.ConfirmPolicy().accepts({**base, "host_organic": 0.9})

    def test_missing_signals_read_as_zero(self):
        assert not jev.ConfirmPolicy().accepts({})

    def test_bounds_are_independent(self):
        answers = {"is_ad": 0.6, "promotional_language": 0.98}
        assert jev.ConfirmPolicy(min_is_ad=0.5).accepts(answers)
        assert not jev.ConfirmPolicy(min_is_ad=0.7).accepts(answers)


class TestConfirmSpans:
    def test_span_members_are_selected_by_time_not_id(self):
        segs = contiguous(6)
        # A merged span carries a stale end_id; time must still bound it.
        ad = {"start": 10.0, "end": 40.0, "start_id": 1, "end_id": 1}
        seen = {}

        def source(payload):
            seen["span"] = payload["state"]["span"]
            return {"is_ad": 0.99, "promotional_language": 0.99}

        kept = jev.confirm_spans([ad], segs, source, policy=jev.ConfirmPolicy())
        assert len(kept) == 1
        assert seen["span"].split().count("words") == 3

    def test_rejected_span_is_dropped(self):
        segs = contiguous(3)
        ad = {"start": 0.0, "end": 10.0, "start_id": 0, "end_id": 0}
        kept = jev.confirm_spans(
            [ad], segs, lambda p: {"is_ad": 0.01}, policy=jev.ConfirmPolicy())
        assert kept == []

    def test_answers_are_attached_to_survivors(self):
        segs = contiguous(3)
        ad = {"start": 0.0, "end": 10.0, "start_id": 0, "end_id": 0}
        answers = {"is_ad": 0.99, "promotional_language": 0.99}
        (kept,) = jev.confirm_spans(
            [ad], segs, lambda p: answers, policy=jev.ConfirmPolicy())
        assert kept["confirm"] == answers

    def test_payload_carries_surrounding_context(self):
        segs = contiguous(6)
        payload = jev.build_confirm_payload([segs[2]], segs)
        assert payload["state"]["content_before"]
        assert payload["state"]["content_after"]
        assert set(payload["questions"]) == set(jev.CONFIRM_QUESTIONS)

    def test_span_at_episode_start_has_empty_before(self):
        segs = contiguous(4)
        payload = jev.build_confirm_payload([segs[0]], segs)
        assert payload["state"]["content_before"] == ""


class TestAggregatePasses:
    def test_mean_across_passes(self):
        a = jev.WindowResult({0: 1.0, 1: 0.0})
        b = jev.WindowResult({0: 0.0, 1: 0.0})
        assert jev.aggregate_passes([a, b]).probabilities == {0: 0.5, 1: 0.0}

    def test_a_contested_segment_falls_below_enter(self):
        # One pass says yes, one says no: the mean must not open a run.
        agreed = jev.aggregate_passes(
            [jev.WindowResult({0: 0.98}), jev.WindowResult({0: 0.02})])
        assert agreed.probabilities[0] < jev.ENTER_THRESHOLD

    def test_agreement_survives_averaging(self):
        agreed = jev.aggregate_passes(
            [jev.WindowResult({0: 0.98}), jev.WindowResult({0: 0.98})])
        assert agreed.probabilities[0] >= jev.ENTER_THRESHOLD

    def test_missing_segment_counts_as_zero(self):
        out = jev.aggregate_passes(
            [jev.WindowResult({0: 1.0}), jev.WindowResult({})])
        assert out.probabilities == {0: 0.5}

    def test_tokens_sum_across_passes(self):
        out = jev.aggregate_passes([
            jev.WindowResult({}, input_tokens=100),
            jev.WindowResult({}, input_tokens=150),
        ])
        assert out.input_tokens == 250

    def test_empty_is_not_an_error(self):
        assert jev.aggregate_passes([]).probabilities == {}

    def test_identical_draws_average_to_themselves(self):
        # Jev is near-deterministic here, so this is the common case: adding
        # passes changes nothing and only multiplies the bill.
        draw = jev.WindowResult({0: 0.97, 1: 0.03})
        assert jev.aggregate_passes([draw] * 5).probabilities == draw.probabilities


class TestProbabilityCache:
    def test_round_trips_through_disk(self, tmp_path):
        segs = contiguous(2)
        cache = jev.ProbabilityCache(tmp_path / "c.json")
        key = jev.payload_key(segs)
        cache._data[key] = {"probabilities": {"0": 0.9}, "input_tokens": 5}
        cache.save()

        reloaded = jev.ProbabilityCache(tmp_path / "c.json")
        result = reloaded.get_or_call(segs, api_key=None)
        assert result.probabilities == {0: 0.9}
        assert reloaded.hits == 1

    def test_miss_without_a_key_raises_rather_than_scoring_zeros(self, tmp_path):
        cache = jev.ProbabilityCache(tmp_path / "c.json")
        with pytest.raises(KeyError):
            cache.get_or_call(contiguous(2), api_key=None)

    def test_changing_the_guidance_invalidates_the_key(self):
        segs = contiguous(2)
        a = jev.hash_payload(jev.build_payload(segs, guidance="one rule"))
        b = jev.hash_payload(jev.build_payload(segs, guidance="another rule"))
        assert a != b

    def test_adding_metadata_invalidates_the_key(self):
        segs = contiguous(2)
        meta = SimpleNamespace(
            podcast_name="Show", title="Ep 1", description="About a thing.")
        bare = jev.hash_payload(jev.build_payload(segs))
        rich = jev.hash_payload(jev.build_payload(segs, metadata=meta))
        assert bare != rich

    def test_distinct_uids_are_distinct_draws(self):
        segs = contiguous(2)
        assert jev.payload_key(segs, uid="pass-0") != jev.payload_key(segs, uid="pass-1")

    def test_missing_file_starts_empty(self, tmp_path):
        assert jev.ProbabilityCache(tmp_path / "absent.json")._data == {}


class TestCrossValidation:
    @staticmethod
    def _scorer(table):
        def score_fn(ep_id, enter, stay):
            s = jev.EpisodeScore(ep_id=ep_id, is_no_ad=False)
            s.f05 = table[(ep_id, enter, stay)]
            return s
        return score_fn

    def test_mean_f05_ignores_no_ad_episodes(self):
        a = jev.EpisodeScore(ep_id="a", is_no_ad=False)
        a.f05 = 0.8
        b = jev.EpisodeScore(ep_id="b", is_no_ad=True)
        b.f05 = 0.0
        assert jev.mean_f05([a, b]) == pytest.approx(0.8)

    def test_mean_f05_of_nothing_is_zero(self):
        assert jev.mean_f05([]) == 0.0

    def test_tune_picks_the_grid_maximum(self):
        grid = [(0.9, 0.4), (0.95, 0.4)]
        table = {("a", 0.9, 0.4): 0.5, ("a", 0.95, 0.4): 0.9}
        enter, stay, f05 = jev.tune_thresholds(
            ["a"], self._scorer(table), grid=grid)
        assert (enter, stay, f05) == (0.95, 0.4, 0.9)

    def test_every_episode_is_held_out_exactly_once(self):
        grid = [(0.9, 0.4)]
        ids = ["a", "b", "c", "d"]
        table = {(e, 0.9, 0.4): 0.5 for e in ids}
        folds = jev.cross_validate(ids, self._scorer(table), fold_size=2,
                                   grid=grid, fixed=(0.9, 0.4))
        assert [f.held_out for f in folds] == [("a", "b"), ("c", "d")]

    def test_ragged_final_fold_is_kept(self):
        grid = [(0.9, 0.4)]
        ids = ["a", "b", "c"]
        table = {(e, 0.9, 0.4): 0.5 for e in ids}
        folds = jev.cross_validate(ids, self._scorer(table), fold_size=2,
                                   grid=grid, fixed=(0.9, 0.4))
        assert folds[-1].held_out == ("c",)

    def test_tuning_never_sees_the_held_out_episodes(self):
        # 'b' scores well at the second setting, 'a' at the first. Holding out
        # 'b' must select on 'a' alone, so the fold reports b's weaker score.
        grid = [(0.9, 0.4), (0.95, 0.4)]
        table = {
            ("a", 0.9, 0.4): 0.9, ("a", 0.95, 0.4): 0.1,
            ("b", 0.9, 0.4): 0.2, ("b", 0.95, 0.4): 1.0,
        }
        (fold,) = jev.cross_validate(
            ["a", "b"], self._scorer(table), fold_size=1, grid=grid,
            fixed=(0.9, 0.4))[1:]
        assert fold.held_out == ("b",)
        assert (fold.enter, fold.stay) == (0.9, 0.4)
        assert fold.test_f05 == pytest.approx(0.2)

    def test_fixed_column_uses_the_shipped_defaults(self):
        grid = [(0.9, 0.4), (0.95, 0.4)]
        table = {
            ("a", 0.9, 0.4): 0.9, ("a", 0.95, 0.4): 0.1,
            ("b", 0.9, 0.4): 0.2, ("b", 0.95, 0.4): 1.0,
        }
        (fold,) = jev.cross_validate(
            ["a", "b"], self._scorer(table), fold_size=1, grid=grid,
            fixed=(0.95, 0.4))[1:]
        assert fold.fixed_f05 == pytest.approx(1.0)


class TestParseResponse:
    def test_reads_noul_probabilities_and_usage(self):
        body = {
            "answers": {
                "s0": {"type": "noul", "noul": 0.91},
                "s1": {"type": "noul", "noul": 0.02},
            },
            "usage": {"input_tokens": 1234, "output_tokens": 0},
        }
        result = jev.parse_response(body)
        assert result.probabilities == {0: 0.91, 1: 0.02}
        assert result.input_tokens == 1234

    def test_ignores_answers_outside_the_segment_namespace(self):
        body = {"answers": {"s3": {"noul": 0.5}, "summary": {"choice": "x"}}}
        assert jev.parse_response(body).probabilities == {3: 0.5}

    def test_empty_body_is_not_an_error(self):
        assert jev.parse_response({}).probabilities == {}
