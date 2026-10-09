"""Adapter parity tests adapted from MinusPodJev at 8ef6033459d8d32b3437db915b9175c2241c34f0."""

import json
from typing import Any
from unittest.mock import MagicMock

import httpx
import pytest

from tests.app_bootstrap import bootstrap
bootstrap("systemone_adapter_review_")

import systemone.adapter as adapter
from systemone.adapter import (
    ReviewInconclusiveError, ReviewUnavailableError, _assessment_speech,
    _boundary_candidates, _choice_state, _effective_category_actions,
    _neighbor_speech, _programme_checks, _range_boundary_support,
    _recover_review_segments, _review_prompt_parts, is_review_request,
    parse_candidate_bounds, parse_review_context, parse_review_segments,
    run_review,
)
from utils.llm_response import extract_json_ads_array
from ad_detector.prompts import format_window_prompt, get_static_system_prompt
from ad_reviewer import AdReviewer
from tests.unit.test_reviewer_policy_section import (
    SEGMENTS as POLICY_SEGMENTS,
    _ad as policy_ad,
    _meta as policy_meta,
)

_AD_KW = ("sponsor", "betterhelp", "promo code", "brought to you by", "acast")
_START_OPTIONS: dict[str, float] = {}
_WORD_START_OPTIONS: dict[str, float] = {}
_END_UNIT_OPTIONS: dict[str, list[dict[str, Any]] | float] = {}
_END_GROUP_OPTIONS: dict[str, list[float]] = {}
_WORD_END_OPTIONS: dict[str, float] = {}


@pytest.fixture
def jev_env():
    return None

def _upstream_status_error(
    status: int, retry_after: str = "7"
) -> httpx.HTTPStatusError:
    request = httpx.Request("POST", "https://api.example/v1/systemone")
    response = httpx.Response(
        status, request=request, headers={"Retry-After": retry_after}
    )
    return httpx.HTTPStatusError("upstream failure", request=request, response=response)


@pytest.fixture(autouse=True)
def capture_start_options(monkeypatch):
    original = adapter._unit_start_question
    original_word = adapter._word_start_question
    original_end = adapter._unit_end_question
    original_end_group = adapter._end_word_group_question
    original_end_word = adapter._word_end_question

    def capture(*args, **kwargs):
        question, options = original(*args, **kwargs)
        _START_OPTIONS.clear()
        _START_OPTIONS.update(options)
        return question, options

    monkeypatch.setattr(adapter, "_unit_start_question", capture)
    def capture_word(*args, **kwargs):
        question, options = original_word(*args, **kwargs)
        _WORD_START_OPTIONS.clear()
        _WORD_START_OPTIONS.update(options)
        return question, options

    monkeypatch.setattr(adapter, "_word_start_question", capture_word)

    def capture_end(*args, **kwargs):
        question, options = original_end(*args, **kwargs)
        _END_UNIT_OPTIONS.clear()
        _END_UNIT_OPTIONS.update(options)
        return question, options

    monkeypatch.setattr(adapter, "_unit_end_question", capture_end)

    def capture_end_group(*args, **kwargs):
        question, options = original_end_group(*args, **kwargs)
        _END_GROUP_OPTIONS.clear()
        _END_GROUP_OPTIONS.update(options)
        return question, options

    monkeypatch.setattr(adapter, "_end_word_group_question", capture_end_group)

    def capture_end_word(*args, **kwargs):
        question, options = original_end_word(*args, **kwargs)
        _WORD_END_OPTIONS.clear()
        _WORD_END_OPTIONS.update(options)
        return question, options

    monkeypatch.setattr(adapter, "_word_end_question", capture_end_word)
    yield
    _START_OPTIONS.clear()
    _WORD_START_OPTIONS.clear()
    _END_UNIT_OPTIONS.clear()
    _END_GROUP_OPTIONS.clear()
    _WORD_END_OPTIONS.clear()


def test_positive_word_edges_recover_only_whole_coarse_gaps():
    coarse = [{"sid": 0, "start": 60.0, "end": 66.0, "text": "coarse"}]
    words = {
        "start": [
            {"start": 66.1, "end": 70.0, "text": "first"},
            {"start": 66.1, "end": 70.0, "text": "first"},
            {"start": 70.0, "end": 70.0, "text": "zero"},
        ],
        "end": [{"start": 70.1, "end": 82.2, "text": "last"}],
    }

    recovered = _recover_review_segments(coarse, words)

    assert [(row["start"], row["end"], row["text"]) for row in recovered] == [
        (60.0, 66.0, "coarse"),
        (66.1, 70.0, "first"),
        (70.1, 82.2, "last"),
    ]


def test_word_recovery_excludes_words_overlapping_coarse_coverage():
    coarse = [{"sid": 0, "start": 60.0, "end": 66.0, "text": "coarse"}]
    words = {
        "start": [{"start": 65.9, "end": 70.0, "text": "partial"}],
        "end": [{"start": 61.0, "end": 62.0, "text": "covered"}],
    }

    assert _recover_review_segments(coarse, words) == [
        {"sid": 0, "start": 60.0, "end": 66.0, "text": "coarse"}
    ]


def test_word_only_candidate_recovery_reaches_jev(jev_env, tmp_path):
    prompt = build_review_prompt(
        66.1,
        82.16,
        [(60.0, 66.0, "context before")],
        [],
        [(83.0, 90.0, "context after")],
    ) + (
        "Boundary word timing, use these timestamps for corrections:\n"
        "Start edge:\n[66.1s-70.0s] This episode is sponsored\n"
        "End edge:\n[70.1s-82.2s] BetterHelp promo code\n"
    )
    calls = 0
    normal = make_text_fake()

    def fake(payload, **kwargs):
        nonlocal calls
        calls += 1
        return normal(payload, **kwargs)

    response = _run(prompt, fake, tmp_path)

    assert calls == 2
    assert json.loads(response["choices"][0]["message"]["content"])["ads"]


def test_boundary_candidates_use_supplied_words_up_to_sixty_seconds():
    segments = [{"start": 40.0, "end": 190.0, "text": "context"}]
    words = {
        "start": [
            {"start": value, "end": value + 0.1, "text": f"w{index}"}
            for index, value in enumerate((39.9, 40.0, 69.9, 100.0, 130.1, 159.9, 160.1))
        ],
        "end": [
            {"start": value - 0.1, "end": value, "text": f"w{index}"}
            for index, value in enumerate((59.9, 60.0, 60.1, 90.0, 120.0, 150.1, 180.0, 180.1))
        ],
    }

    starts, ends = _boundary_candidates(segments, words, (100.0, 120.0))

    assert 40.0 in starts and 159.9 in starts
    assert 39.9 not in starts and 160.1 not in starts
    assert 60.0 in ends and 180.0 in ends
    assert 59.9 not in ends and 180.1 not in ends
    assert len(starts) == len(set(starts)) and len(ends) == len(set(ends))


def test_boundary_candidates_cap_seconds_is_adjustable():
    segments = [{"start": 0.0, "end": 300.0, "text": "context"}]
    words = {
        "start": [{"start": 100.0, "end": 100.1, "text": "a"}],
        "end": [
            {"start": value - 0.1, "end": value, "text": f"w{index}"}
            for index, value in enumerate((195.0, 215.0))
        ],
    }

    _, default_ends = _boundary_candidates(segments, words, (100.0, 120.0))
    _, wide_ends = _boundary_candidates(segments, words, (100.0, 120.0), cap_seconds=90.0)

    assert 195.0 not in default_ends
    assert 195.0 in wide_ends and 215.0 not in wide_ends


def test_boundary_cap_setting_controls_offered_end_options(jev_env, tmp_path):
    prompt = build_review_prompt(
        100.0, 120.0,
        [(90.0, 100.0, "Editorial before.")],
        [(100.0, 110.0, "Editorial transition."),
         (110.0, 120.0, "Sponsor offer ends.")],
        [(120.0, 130.0, "Editorial return."),
         (130.0, 190.0, "Long editorial discussion."),
         (190.0, 195.0, "Closing remark.")],
    ) + (
        "Boundary word timing, use these timestamps for corrections:\n"
        "Start edge:\n[100.0s-101.0s] Editorial\n[110.0s-111.0s] Sponsor\n"
        "End edge:\n[119.0s-120.0s] ends.\n[129.0s-130.0s] return.\n[189.0s-190.0s] discussion.\n"
    )

    def offered(cap):
        seen: set[float] = set()
        normal = make_text_fake()

        def fake(payload, **kwargs):
            result = normal(payload, **kwargs)
            for options in (_WORD_END_OPTIONS.values(), _END_GROUP_OPTIONS.values()):
                for value in options:
                    seen.update(value if isinstance(value, list) else [value])
            for value in _END_UNIT_OPTIONS.values():
                if isinstance(value, list):
                    seen.update(float(word["end"]) for word in value)
                else:
                    seen.add(value)
            return result

        cache = tmp_path / f"cap{cap}"
        cache.mkdir()
        try:
            _run(prompt, fake, cache, refine_boundaries=True, review_boundary_cap_seconds=cap)
        except ReviewInconclusiveError:
            pass
        return seen

    assert 190.0 not in offered(60.0)
    assert 190.0 in offered(90.0)


def test_comparison_question_compares_two_eligible_intervals():
    question = adapter._comparison_question()["interval_comparison"]

    assert set(question["criteria"]) == {"adjusted", "original", "neither"}
    assert question["instructions"].startswith("Which eligible interval")


def test_boundary_candidates_keep_all_observed_word_edges_within_window():
    segments = [{"start": 90.0, "end": 140.0, "text": "Sponsor message."}]
    words = {
        "start": [
            {"start": 100.0 + index * 0.1, "end": 100.05 + index * 0.1, "text": "word"}
            for index in range(180)
        ],
        "end": [{"start": 119.0, "end": 120.0, "text": "sign-off."}],
    }

    starts, _ = _boundary_candidates(segments, words, (100.0, 120.0))

    assert len(starts) == 180
    assert starts[0] == 100.0
    assert starts[-1] == pytest.approx(117.9)


def test_boundary_option_overflow_abstains_without_truncation(jev_env, tmp_path):
    prompt = build_review_prompt(
        100.0, 120.0,
        [(90.0, 100.0, "Discussion ends.")],
        [(100.0, 120.0, "This episode is sponsored by Acme.")],
        [(120.0, 130.0, "Discussion resumes.")],
    ) + (
        "Boundary word timing, use these timestamps for corrections:\nStart edge:\n"
        + "".join(f"[{100.0 + index * 0.1:.2f}s-{100.05 + index * 0.1:.2f}s] word.\n" for index in range(255))
        + "End edge:\n[129.0s-130.0s] sponsored\n"
    )
    normal = make_text_fake()
    stages = []

    def fake(payload, **kwargs):
        stages.append(tuple(payload["questions"]))
        return normal(payload, **kwargs)

    with pytest.raises(ReviewInconclusiveError) as raised:
        _run(prompt, fake, tmp_path, refine_boundaries=True)

    assert raised.value.reason == "too_many_boundary_options"
    assert stages == [("s0", "s1", "s2"), ("evidence",)]


def test_word_transition_options_include_unpunctuated_return_after_many_words():
    segments = [{"start": 90.0, "end": 120.0, "text": "sponsor then editorial"}]
    words = {
        "start": [{"start": 100.0, "end": 100.1, "text": "Sponsor"}],
        "end": [
            {"start": 99.9 + index * 0.2, "end": 100.0 + index * 0.2, "text": f"word{index}"}
            for index in range(40)
        ],
    }

    _, ends = _boundary_candidates(segments, words, (100.0, 100.0))

    assert 104.8 in ends
    assert len(ends) <= 49


def test_choice_context_keeps_earlier_ad_lead_in_beyond_nearest_rows():
    rows = [
        {"start": 2800.0, "end": 2801.0, "text": "Break handoff."},
        {"start": 2802.0, "end": 2803.0, "text": "Ad lead-in begins."},
    ] + [
        {"start": 2804.0 + index * 1.5, "end": 2805.0 + index * 1.5, "text": f"Ad sentence {index}."}
        for index in range(10)
    ] + [{"start": 2820.0, "end": 2822.0, "text": "Brand mentioned."}]

    state = _choice_state(rows, (2820.0, 2840.0), "guidance", "")

    assert "Break handoff." in state["start_context"]
    assert "Ad lead-in begins." in state["start_context"]
    assert "Brand mentioned." in state["start_context"]
    assert "boundary_words" not in state


def test_neighbor_speech_separates_immediate_ad_tail_from_later_show_context():
    end_words = [
        {"start": 120.0 + index * 0.2, "end": 120.2 + index * 0.2, "text": word}
        for index, word in enumerate(
            "Now I am impressed with what they do acme .com all right back to the show".split()
        )
    ]
    adjacent, context = _neighbor_speech(end_words, 120.0, "after")

    assert adjacent == "Now I am impressed with what they do"
    assert context.startswith("acme .com all right back")


def test_word_edge_options_include_truncated_window_edges():
    segments = [{"start": 100.0, "end": 120.0, "text": "continuous sponsor speech"}]
    words = {
        "start": [{"start": 104.0, "end": 105.0, "text": "middle"},
                  {"start": 110.0, "end": 111.0, "text": "continues"}],
        "end": [{"start": 110.0, "end": 111.0, "text": "middle"},
                {"start": 115.0, "end": 116.0, "text": "continues"}],
    }

    starts, ends = _boundary_candidates(segments, words, (100.0, 120.0))

    assert 104.0 in starts
    assert 116.0 in ends


def test_partial_coarse_row_requires_complete_word_alignment():
    segments = [{"start": 100.0, "end": 110.0, "text": "This portion is sponsored by Acme"}]
    words = {"start": [{"start": 105.0, "end": 106.0, "text": "sponsored"}], "end": []}

    assert _assessment_speech(segments, words, (105.0, 110.0)) is None
    assert _assessment_speech(segments, words, (110.0, 120.0)) == ""


def test_boundary_inside_supplied_word_is_unsupported():
    segments = [
        {"start": 0.0, "end": 94.2, "text": "This episode is sponsored by Acme."},
        {"start": 94.2, "end": 95.0, "text": "Welcome back."},
    ]
    words = {
        "start": [{"start": 0.0, "end": 0.2, "text": "This"}],
        "end": [
            {"start": 93.9, "end": 94.2, "text": "Acme."},
            {"start": 94.42, "end": 94.98, "text": "Welcome"},
        ],
    }

    assert _range_boundary_support(segments, words, (0.0, 94.77)) == (True, False)
    assert _range_boundary_support(segments, words, (94.55, 95.0)) == (False, True)
    assert _range_boundary_support(segments, words, (0.0, 94.2)) == (True, True)
    assert _assessment_speech(segments, words, (0.0, 94.2)) == segments[0]["text"]
    assert 94.77 not in _boundary_candidates(segments, words, (0.0, 94.77))[1]
    assert 94.55 not in _boundary_candidates(segments, words, (94.55, 95.0))[0]


def test_boundary_at_coarse_gap_remains_supported():
    segments = [
        {"start": 0.0, "end": 94.3, "text": "This episode is sponsored by Acme."},
        {"start": 94.42, "end": 95.0, "text": "Welcome back."},
    ]
    words = {
        "start": [{"start": 0.0, "end": 0.2, "text": "This"}],
        "end": [
            {"start": 93.9, "end": 94.2, "text": "Acme."},
            {"start": 94.42, "end": 94.98, "text": "Welcome"},
        ],
    }

    assert _range_boundary_support(segments, words, (0.0, 94.3)) == (True, True)
    assert _assessment_speech(segments, words, (0.0, 94.3)) == segments[0]["text"]


def test_overlapping_words_leave_only_supported_rank_choices():
    segments = [{"start": 100.0, "end": 120.0, "text": "A sponsor offer."}]
    words = {
        "start": [
            {"start": 100.0, "end": 101.0, "text": "A"},
            {"start": 100.5, "end": 101.5, "text": "sponsor"},
            {"start": 102.0, "end": 103.0, "text": "offer"},
        ],
        "end": [
            {"start": 118.0, "end": 119.0, "text": "visit"},
            {"start": 118.5, "end": 119.5, "text": "Acme"},
            {"start": 119.5, "end": 120.0, "text": "today"},
        ],
    }

    starts, ends = _boundary_candidates(segments, words, (100.0, 120.0))
    assert starts == [100.0, 102.0]
    assert ends == [119.5, 120.0]


def test_word_clipped_original_can_adjust_to_aligned_end(jev_env, tmp_path):
    prompt = build_review_prompt(
        0.0, 94.8,
        [],
        [(0.0, 94.2, "This episode is sponsored by Acme."),
         (94.2, 95.0, "Welcome back.")],
        [(95.0, 100.0, "Now for the discussion.")],
    ) + (
        "Boundary word timing, use these timestamps for corrections:\n"
        "Start edge:\n[0.00s-0.20s] This\n"
        "End edge:\n[93.90s-94.20s] Acme.\n"
        "[94.42s-94.70s] Welcome\n[94.70s-94.98s] back.\n"
    )
    ranked = _select_pair_fake(0.0, 94.2)

    def fake(payload, **kwargs):
        if "boundary_end_word" in payload["questions"]:
            options = payload["questions"]["boundary_end_word"]["criteria"].values()
            assert any("At 94.20s," in option for option in options)
            assert not any("At 94.80s," in option for option in options)
        result = ranked(payload, **kwargs)
        if "interval_comparison" in payload["questions"]:
            assert "original" not in payload["questions"]["interval_comparison"]["criteria"]
            assert "Welcome back." in payload["state"]["excluded_end_speech"]
            _set_choice_answer(result, payload, "interval_comparison", "adjusted")
        return result

    response = _run(prompt, fake, tmp_path, refine_boundaries=True)
    ad = json.loads(response["choices"][0]["message"]["content"])["ads"][0]
    assert (ad["start"], ad["end"]) == (0.0, 94.2)


@pytest.mark.parametrize(
    "focused_score",
    [0.98, 0.5],
)
def test_zero_duration_word_supports_selected_endpoint_outside_coarse_context(
    jev_env, tmp_path, focused_score
):
    prompt = build_review_prompt(
        90.0,
        110.0,
        [(80.0, 89.9, "earlier context")],
        [(94.0, 100.0, "This episode is sponsored by BetterHelp")],
        [],
    ) + (
        "Boundary word timing, use these timestamps for corrections:\n"
        "Start edge:\n[93.9s-94.1s] sponsor\n"
        "End edge:\n[110.0s-110.0s] sponsor\n"
    )
    normal = make_text_fake()
    focused_payloads = []

    def fake(payload, **kwargs):
        result = normal(payload, **kwargs)
        _override_boundaries(result, payload, 93.9, 110.0)
        if "sponsor_read" in payload["questions"]:
            result["answers"]["sponsor_read"] = {"noul": focused_score}
        if "interval_comparison" in payload["questions"]:
            focused_payloads.append(payload)
            _set_choice_answer(
                result, payload, "interval_comparison",
                "adjusted", 0.98,
            )
        return result

    with pytest.raises(ReviewInconclusiveError) as raised:
        _run(prompt, fake, tmp_path, refine_boundaries=True)
    assert raised.value.reason == "insufficient_boundary_text"
    assert raised.value.stage == "boundary_coverage"
    assert raised.value.proposal["reason"] == "insufficient_boundary_text"
    assert raised.value.proposal["stage"] == "boundary_coverage"

    assert focused_payloads == []
    coarse = [{"start": 80.0, "end": 89.9}, {"start": 94.0, "end": 100.0}]
    words = {
        "start": [{"start": 93.9, "end": 94.1}],
        "end": [{"start": 110.0, "end": 110.0}],
    }
    assert _range_boundary_support(coarse, words, (93.9, 110.0)) == (True, True)
    assert _range_boundary_support(coarse, words, (93.9, 109.999)) == (True, False)


def _meaningful_pair_prompt() -> str:
    return build_review_prompt(
        100.0,
        120.0,
        [(90.0, 100.0, "Editorial before.")],
        [(100.0, 106.0, "Editorial transition."),
         (106.0, 115.0, "Sponsor offer ends."),
         (115.0, 120.0, "Sponsor continues.")],
        [(120.0, 130.0, "Editorial return.")],
    ) + (
        "Boundary word timing, use these timestamps for corrections:\n"
        "Start edge:\n[90.0s-91.0s] Editorial.\n[100.0s-101.0s] Editorial\n[106.0s-107.0s] Sponsor\n"
        "End edge:\n[114.0s-115.0s] ends.\n[119.0s-120.0s] continues.\n[129.0s-130.0s] Editorial.\n"
    )


def _unsupported_original_prompt() -> str:
    return build_review_prompt(
        90.0, 110.0,
        [(80.0, 89.9, "earlier context")],
        [(94.0, 100.0, "This episode is sponsored by BetterHelp")],
        [(100.0, 110.0, "context after"), (110.0, 120.0, "more context")],
    ) + (
        "Boundary word timing, use these timestamps for corrections:\n"
        "Start edge:\n"
        "[94.0s-94.5s] This\n[94.5s-95.0s] episode\n[95.0s-95.3s] is\n"
        "[95.3s-96.0s] sponsored\n[96.0s-96.2s] by\n[96.2s-100.0s] BetterHelp\n"
        "End edge:\n[96.2s-100.0s] BetterHelp\n[100.0s-101.0s] context\n"
    )


def _end_unit_option(target: float) -> str:
    for name, value in _END_UNIT_OPTIONS.items():
        if isinstance(value, float):
            if value == target:
                return name
        elif float(value[0]["start"]) <= target <= float(value[-1]["end"]):
            return name
    raise AssertionError(f"no end unit contains {target}")


def _boundary_option(key: str, target: float) -> str:
    if key == "boundary_start":
        selected = max(value for value in _START_OPTIONS.values() if value <= target)
        return next(name for name, value in _START_OPTIONS.items() if value == selected)
    if key == "boundary_start_word":
        return next(name for name, value in _WORD_START_OPTIONS.items() if value == target)
    if key == "boundary_end":
        return _end_unit_option(target)
    if key == "boundary_end_group":
        return next(name for name, values in _END_GROUP_OPTIONS.items() if target in values)
    return next(name for name, value in _WORD_END_OPTIONS.items() if value == target)


def _override_boundaries(
    result: dict[str, Any], payload: dict[str, Any], start: float, end: float,
) -> None:
    for key, question in payload["questions"].items():
        if key not in {"boundary_start", "boundary_start_word", "boundary_end", "boundary_end_group", "boundary_end_word"}:
            continue
        target = start if key.startswith("boundary_start") else end
        option = _boundary_option(key, target)
        result["answers"][key]["choice"] = option
        result["answers"][key]["probabilities"] = {
            name: 0.99 if name == option else 0.01 / (len(question["criteria"]) - 1)
            for name in question["criteria"]
        }


def _select_pair_fake(start: float, end: float):
    normal = make_text_fake()

    def fake(payload, **kwargs):
        result = normal(payload, **kwargs)
        _override_boundaries(result, payload, start, end)
        return result

    return fake


def _set_choice_answer(result: dict[str, Any], payload: dict[str, Any], question: str, choice: str, probability: float = 0.99):
    criteria = payload["questions"][question]["criteria"]
    result["answers"][question] = {
        "choice": choice,
        "confidence": probability,
        "probabilities": {
            key: probability if key == choice else (1.0 - probability) / (len(criteria) - 1)
            for key in criteria
        },
    }


def make_text_fake(*, keywords=_AD_KW, input_tokens=1200, output_tokens=6, raises=False):
    """Score ad keywords from state transcript text independently of sid assignment."""

    candidate: dict[str, float] = {}

    def fake(payload: dict[str, Any], **_kwargs: Any) -> dict[str, Any]:
        if raises:
            raise RuntimeError("upstream boom")
        candidate.update(payload["state"].get("candidate", {}))
        sid_text: dict[str, str] = {}
        if "assessment_speech" in payload["state"]:
            sid_text["s0"] = payload["state"]["assessment_speech"].lower()
        elif "transcript" in payload["state"]:
            for line in payload["state"]["transcript"].splitlines():
                tag, _, body = line.partition("| ")
                sid_text[f"s{int(tag[1:])}"] = body.lower()
        elif "speech" in payload["state"]:
            sid_text["s0"] = payload["state"]["speech"].lower()
        elif any(key.endswith("_speech") for key in payload["state"]):
            sid_text["s0"] = " ".join(
                str(value) for key, value in payload["state"].items() if key.endswith("_speech")
            ).lower()
        else:
            sid_text["s0"] = (
                payload["state"].get("start_context", "")
                + payload["state"].get("end_context", "")
            ).lower()
        answers: dict[str, Any] = {}
        for key in payload["questions"]:
            instructions = payload["questions"][key].get("instructions")
            if isinstance(instructions, dict) and "target_speech" in instructions:
                answers[key] = {"noul": 0.02}
                continue
            if key in {"unrelated_editorial", "before_continuation", "after_continuation"}:
                answers[key] = {"noul": 0.02}
                continue
            if key.startswith(("excluded_", "added_")) and key.endswith("_valid"):
                field = key.removesuffix("_valid") + "_speech"
                speech = payload["state"].get(field, "").lower()
                is_ad = any(kw in speech for kw in _AD_KW)
                answers[key] = {"noul": 0.98 if is_ad == key.startswith("added_") else 0.02}
                continue
            if payload["questions"][key].get("type") == "noul" and not (key.startswith("s") and key[1:].isdigit()):
                answers[key] = {"noul": 0.98 if any(kw in text for text in sid_text.values() for kw in _AD_KW) else 0.02}
                continue
            question = payload["questions"][key]
            criteria = question.get("criteria", {})
            if question.get("type") == "choice":
                if key == "end_run_category":
                    tail = payload["state"]["candidate_tail"].lower()
                    first_promo = min(
                        (tail.find(marker) for marker in ("sponsor", "tonight on", "promo code")
                         if marker in tail),
                        default=len(tail),
                    )
                    first_protected = min(
                        (tail.find(marker) for marker in ("welcome back", "editorial", "episode story")
                         if marker in tail),
                        default=len(tail),
                    )
                    option = (
                        "mixed" if first_protected < first_promo < len(tail) else
                        "protected" if first_protected < len(tail) else
                        "removable" if first_promo < len(tail) else "protected"
                    )
                elif key == "boundary_start":
                    option = next((name for name, value in _START_OPTIONS.items() if value == candidate.get("start")), next(name for name in criteria if name != "unknown"))
                elif key == "boundary_start_word":
                    option = next((name for name, value in _WORD_START_OPTIONS.items() if value == candidate.get("start")), "unknown")
                elif key == "boundary_end":
                    option = _end_unit_option(candidate["end"])
                elif key == "boundary_end_group":
                    option = next(
                        (name for name, values in _END_GROUP_OPTIONS.items() if candidate.get("end") in values),
                        "unknown",
                    )
                elif key == "boundary_end_word":
                    option = next(
                        (name for name, value in _WORD_END_OPTIONS.items() if value == candidate.get("end")),
                        "unknown",
                    )
                else:
                    option = next(name for name in criteria if name != "unknown")
                remainder = 0.01 / max(len(criteria) - 1, 1)
                answers[key] = {
                    "choice": option,
                    "confidence": 0.98,
                    "probabilities": {name: 0.99 if name == option else remainder for name in criteria},
                }
                continue
            if not (key.startswith("s") and key[1:].isdigit()):
                continue
            text = sid_text.get(key, "")
            hot = any(kw in text for kw in keywords)
            answers[key] = {"noul": 0.98 if hot else 0.02}
        return {
            "answers": answers,
            "usage": {"input_tokens": input_tokens, "output_tokens": output_tokens},
        }

    return fake


def _ts_lines(rows):
    return "\n".join(f"[{s:.1f}s-{e:.1f}s] {t}" for s, e, t in rows)


def build_review_prompt(cand_start, cand_end, before, ad, after, *, pool="accepted",
                        with_timestamps=True):
    """Mirror ad_reviewer._build_user_prompt output shape."""
    if pool == "resurrection":
        framing = (
            "This segment was rejected for low confidence by the validator. "
            "Decide whether it should be cut after all.\n"
            f"Original boundaries: {cand_start:.2f}s - {cand_end:.2f}s.\n"
        )
    else:
        framing = (
            "This is the candidate ad to review.\n"
            f"Original boundaries: {cand_start:.2f}s - {cand_end:.2f}s.\n"
        )
    render = _ts_lines if with_timestamps else (lambda rows: "\n".join(t for _, _, t in rows))
    return (
        "Podcast: My Podcast\n"
        "Episode: Ep 1\n"
        "\n"
        f"{framing}\n"
        "Transcript (60s before, the candidate ad, 60s after; all lines "
        "carry [start-end] second timestamps):\n"
        f"{render(before)}\n"
        f">>> CANDIDATE AD START [{cand_start:.1f}s] >>>\n"
        f"{render(ad)}\n"
        f"<<< CANDIDATE AD END [{cand_end:.1f}s] <<<\n"
        f"{render(after)}\n"
    )


def minuspod_review_verdict(content, original_start, original_end, *, pool="accepted", tol=0.1):
    """Faithful copy of ad_reviewer._review_single's decision over a parsed
    review response, driven by MinusPod's own extract_json_ads_array."""
    ads, method = extract_json_ads_array(content)
    assert ads is not None, "MinusPod could not parse the review response"
    if not ads:  # empty array -> reject (ad_reviewer.py:1246-1258)
        return "reject", None, None, method
    kept = ads[0]
    assert isinstance(kept, dict)
    if kept.get("is_ad") is False:  # structured whole-span reject (1281-1293)
        return "reject", None, None, method
    new_start = float(kept.get("start", original_start))
    new_end = float(kept.get("end", original_end))
    if pool == "resurrection":  # resurrection pool always resurrects (1330-1331)
        return "resurrect", new_start, new_end, method
    unchanged = abs(new_start - original_start) <= tol and abs(new_end - original_end) <= tol
    return ("confirmed" if unchanged else "adjust"), new_start, new_end, method


def _run(
    prompt,
    fake,
    tmp_path,
    *,
    refine_boundaries=False,
    review_request_id=None,
    enter=0.95,
    review_evidence_enter=None,
    review_choice_enter=None,
    review_boundary_cap_seconds=None,
    review_context_seconds=None,
):
    kwargs = {}
    if review_boundary_cap_seconds is not None:
        kwargs["review_boundary_cap_seconds"] = review_boundary_cap_seconds
    if review_context_seconds is not None:
        kwargs["review_context_seconds"] = review_context_seconds
    if fake is not None:
        kwargs["fetcher"] = fake
    return run_review(
        messages=[
            {"role": "system", "content": "review ads"},
            {"role": "user", "content": prompt},
        ],
        request_model="typesafe/jev",
        url="u",
        api_key="k",
        timeout=1.0,
        cache_path=str(tmp_path / "c.json"),
        model="jev-latest",
        enter=enter,
        stay=0.40,
        refine_boundaries=refine_boundaries,
        review_evidence_enter=review_evidence_enter,
        review_choice_enter=review_choice_enter,
        review_request_id=review_request_id,
        **kwargs,
    )


def test_refined_evidence_uses_only_candidate_speech(jev_env, tmp_path):
    evidence_state = {}
    evidence_question = {}
    normal = make_text_fake()

    def fake(payload, **kwargs):
        result = normal(payload, **kwargs)
        if "evidence" in payload["questions"]:
            evidence_state.update(payload["state"])
            evidence_question.update(payload["questions"]["evidence"])
            result["answers"]["evidence"] = {"noul": 0.01}
        return result

    with pytest.raises(ReviewInconclusiveError) as raised:
        _run(_meaningful_pair_prompt(), fake, tmp_path, refine_boundaries=True)

    assert raised.value.reason == "insufficient_evidence"
    assert evidence_state["candidate_interval_speech"] == (
        "Editorial transition.\nSponsor offer ends.\nSponsor continues."
    )
    assert "Editorial before." not in evidence_state["candidate_interval_speech"]
    assert "Editorial return." not in evidence_state["candidate_interval_speech"]
    assert "transcript" not in evidence_state
    assert "candidate_interval_speech" in evidence_question["instructions"]
    assert set(evidence_question["criteria"]) == {"true", "false"}


@pytest.mark.parametrize("refine_boundaries,include_end_words", [(False, True), (True, False)])
def test_unvalidated_review_paths_keep_full_context_evidence(
    jev_env, tmp_path, refine_boundaries, include_end_words,
):
    prompt = _meaningful_pair_prompt()
    if not include_end_words:
        prompt = prompt.split("End edge:\n", 1)[0] + "End edge:\n"
    evidence_state = {}
    evidence_question = {}
    normal = make_text_fake()

    def fake(payload, **kwargs):
        result = normal(payload, **kwargs)
        if "evidence" in payload["questions"]:
            evidence_state.update(payload["state"])
            evidence_question.update(payload["questions"]["evidence"])
            result["answers"]["evidence"] = {"noul": 0.01}
        return result

    with pytest.raises(ReviewInconclusiveError) as raised:
        _run(prompt, fake, tmp_path, refine_boundaries=refine_boundaries)

    assert raised.value.reason == "insufficient_evidence"
    assert "transcript" in evidence_state
    assert "candidate_interval_speech" not in evidence_state
    assert evidence_question == {
        "type": "noul",
        "instructions": "The candidate interval contains transcript-grounded advertising or promotional content covered by the supplied guidance, not merely editorial discussion or a brand mention.",
    }


def test_review_evidence_threshold_is_independent(jev_env, tmp_path):
    prompt = build_review_prompt(
        100.0,
        120.0,
        [(94.0, 100.0, "back to the topic")],
        [(100.0, 110.0, "This episode is sponsored by BetterHelp"),
         (110.0, 120.0, "Use promo code SHOW")],
        [(120.0, 126.0, "and we are back")],
    )
    with pytest.raises(ReviewInconclusiveError, match="sufficient advertising evidence"):
        _run(
            prompt,
            make_text_fake(),
            tmp_path,
            review_evidence_enter=0.99,
        )


def test_review_prefilter_uses_review_evidence_threshold_only(jev_env, tmp_path):
    rows = [
        (94.0, 100.0, "back to the topic"),
        (100.0, 110.0, "This episode is sponsored by BetterHelp"),
        (110.0, 120.0, "Use promo code SHOW"),
        (120.0, 126.0, "and we are back"),
    ]
    prompt = build_review_prompt(100.0, 120.0, rows[:1], rows[1:3], rows[3:])
    normal = make_text_fake()
    evidence_calls = 0

    def score_at_review_threshold(payload, **kwargs):
        nonlocal evidence_calls
        result = normal(payload, **kwargs)
        for name, answer in result["answers"].items():
            if name.startswith("s") and name[1:].isdigit() and answer["noul"] > 0.5:
                answer["noul"] = 0.93
        if "evidence" in result["answers"]:
            evidence_calls += 1
            result["answers"]["evidence"]["noul"] = 0.93
        return result

    reviewed = _run(
        prompt, score_at_review_threshold, tmp_path,
        enter=0.95, review_evidence_enter=0.85,
    )
    assert json.loads(reviewed["choices"][0]["message"]["content"])["ads"]
    assert evidence_calls == 1

    detection_prompt = format_window_prompt(
        "Pod", "Ep", "", [f"[{start:.1f}s-{end:.1f}s] {text}" for start, end, text in rows],
        0, 1, 0.0, 600.0,
    )
    detected = adapter.run_chat_completion(
        messages=[{"role": "user", "content": detection_prompt}],
        request_model="typesafe/jev",
        settings=adapter.SystemOneSettings(
            model="jev-latest",
            detection_enter=0.95,
            detection_stay=0.40,
            category_pass=False,
            category_context=2,
            default_category="sponsor",
            review_evidence_enter=0.85,
        ),
        fetcher=score_at_review_threshold,
        sponsor_lookup=lambda: (),
    )
    assert json.loads(detected["choices"][0]["message"]["content"])["ads"] == []
    assert evidence_calls == 1


def test_current_detection_prompt_builds_into_native_transcript_and_cue_context():
    rows = [
        "[0.0s - 6.0s] Welcome back to the show.",
        "[6.0s - 12.0s] Today we talk about hiking trips.",
        "[12.0s - 18.0s] It was a great trip in the mountains.",
        "[18.0s - 24.0s] This episode is sponsored by BetterHelp.",
        "[24.0s - 30.0s] BetterHelp offers online therapy at betterhelp.com slash show.",
        "[30.0s - 36.0s] Use code SHOW for ten percent off your first month today.",
        "[36.0s - 42.0s] Anyway, back to the trip we were on.",
        "[42.0s - 48.0s] We hiked for hours and it was tiring.",
    ]
    cue_context = "\n=== AUDIO SIGNALS ===\n[12.0s - 13.0s] cue"
    prompt = format_window_prompt(
        "Pod", "Ep", "[10.0s - 11.0s] show-note cue", rows, 0, 1, 0.0, 600.0,
        audio_context=cue_context,
    )
    segments, mode = adapter.parse_transcript(prompt)
    assert mode == "timestamps"
    assert len(segments) == 8
    assert segments[3]["text"] == "This episode is sponsored by BetterHelp."
    assert "=== AUDIO SIGNALS ===" in prompt


def test_current_review_and_resurrection_builders_preserve_policy_and_cue_context():
    db = MagicMock()
    db.get_setting.return_value = None
    reviewer = AdReviewer(db=db, sponsor_service=None)
    episode = policy_meta()
    ad = {**policy_ad(), "cue_pair": {
        "start": {"label": "a"}, "end": {"label": "b"},
    }}
    for pool in ("accepted", "resurrection"):
        prompt = reviewer._build_user_prompt(
            ad=ad, segments=POLICY_SEGMENTS, episode_meta=episode, pool=pool,
        )
        assert adapter.is_review_request(prompt, reviewer._render_review_prompt(60, ""))
        assert "Effective category actions: sponsor=remove, cross_promo=remove, self_promo=keep" in prompt
        assert "CUE-PAIR ORIGIN" in prompt
        if pool == "resurrection":
            assert "rejected for low confidence" in prompt
        caller_context, transcript = adapter._review_prompt_parts(prompt)
        parsed, words = adapter.parse_review_context(transcript)
        assert "Effective category actions: sponsor=remove, cross_promo=remove, self_promo=keep" in caller_context
        assert len(parsed) == 4
        assert words == {"start": [], "end": []}

def test_review_choice_threshold_is_independent(jev_env, tmp_path):
    prompt = build_review_prompt(
        100.0,
        120.0,
        [(94.0, 100.0, "back to the topic")],
        [(100.0, 110.0, "This episode is sponsored by BetterHelp"),
         (110.0, 120.0, "Use promo code SHOW")],
        [(120.0, 126.0, "and we are back")],
    )
    normal = make_text_fake()

    def fake(payload, **kwargs):
        result = normal(payload, **kwargs)
        if "interval_comparison" in payload["questions"]:
            choice = next(key for key in payload["questions"]["interval_comparison"]["criteria"] if key != "neither")
            _set_choice_answer(result, payload, "interval_comparison", choice, 0.98)
        return result

    with pytest.raises(ReviewInconclusiveError, match="a safe advertising interval"):
        _run(
            _with_word_edges(prompt),
            fake,
            tmp_path,
            refine_boundaries=True,
            review_choice_enter=0.99,
        )


def test_lower_evidence_threshold_replays_review_without_cache(jev_env, tmp_path):
    prompt = build_review_prompt(
        100.0,
        120.0,
        [(94.0, 100.0, "back to the topic")],
        [(100.0, 110.0, "This episode is sponsored by BetterHelp"),
         (110.0, 120.0, "Use promo code SHOW")],
        [(120.0, 126.0, "and we are back")],
    )
    calls = []
    base = make_text_fake()

    def fake(payload, **kwargs):
        calls.append(payload["questions"])
        body = base(payload, **kwargs)
        if "evidence" in body["answers"]:
            body["answers"]["evidence"]["noul"] = 0.86
        return body

    with pytest.raises(ReviewInconclusiveError, match="sufficient advertising evidence"):
        _run(prompt, fake, tmp_path, review_evidence_enter=0.95)
    assert len(calls) == 2

    response = _run(prompt, fake, tmp_path, review_evidence_enter=0.85)
    assert json.loads(response["choices"][0]["message"]["content"])["ads"]
    assert len(calls) == 4


def _with_word_edges(prompt, start=(99.5, 100.0, "This"), end=(119.5, 120.0, "BetterHelp")):
    return prompt + (
        "Boundary word timing, use these timestamps for corrections:\n"
        "Start edge:\n"
        f"[{start[0]:.1f}s-{start[1]:.1f}s] {start[2]}\n"
        "End edge:\n"
        f"[{end[0]:.1f}s-{end[1]:.1f}s] {end[2]}\n"
    )


def test_review_prompt_is_recognized():
    prompt = build_review_prompt(
        100.0, 120.0,
        [(94.0, 100.0, "so anyway back to the topic")],
        [(100.0, 110.0, "This episode is sponsored by BetterHelp"),
         (110.0, 120.0, "Use promo code SHOW at betterhelp.com")],
        [(120.0, 126.0, "and we are back")],
    )
    assert is_review_request(prompt) is True
    assert parse_candidate_bounds(prompt) == (100.0, 120.0)


def test_detection_window_is_not_a_review():
    lines = [
        "[0.0s - 6.0s] Welcome back to the show.",
        "[6.0s - 12.0s] This episode is sponsored by BetterHelp.",
    ]
    prompt = format_window_prompt("Pod", "Ep", "", lines, 0, 1, 0.0, 600.0)
    assert is_review_request(prompt) is False


def test_review_system_prompt_routes_without_markers():
    # No CANDIDATE markers in the user text; only the system prompt signals review.
    user = "[100.0s - 110.0s] This episode is sponsored by BetterHelp."
    assert ">>> CANDIDATE AD START [" not in user
    review_system = (
        "You are reviewing a candidate advertisement that has already been "
        "detected in a podcast episode."
    )
    resurrect_system = (
        "You are taking a second look at a segment that the validator already "
        "rejected for low confidence."
    )
    assert is_review_request(user, review_system) is True
    assert is_review_request(user, resurrect_system) is True
    assert is_review_request(user, review_system.upper()) is True  # case-insensitive
    assert is_review_request(user) is False  # no markers, no system signal


def test_detection_prompt_never_routes_to_review():
    lines = ["[0.0s - 6.0s] Welcome back to the show.", "[6.0s - 12.0s] Today we discuss hiking."]
    user = format_window_prompt("Pod", "Ep", "", lines, 0, 1, 0.0, 600.0)
    system = get_static_system_prompt()  # opens "Analyze this podcast transcript..."
    assert is_review_request(user, system) is False


def test_dedupe_overlapping_context_and_candidate_lines():
    # A straddling line rendered in both context and candidate is scored once.
    prompt = build_review_prompt(
        100.0, 120.0,
        [(96.0, 100.0, "let's take a quick break"),
         (100.0, 110.0, "This episode is sponsored by BetterHelp")],
        [(100.0, 110.0, "This episode is sponsored by BetterHelp"),
         (110.0, 120.0, "Use promo code SHOW at betterhelp.com")],
        [(120.0, 126.0, "and we are back")],
    )
    segs = parse_review_segments(prompt)
    starts = [s["start"] for s in segs]
    assert starts == sorted(starts)
    assert len(segs) == len({(s["start"], s["end"], s["text"]) for s in segs})
    assert [s["sid"] for s in segs] == list(range(len(segs)))


def test_confirm_keeps_candidate_with_jev_boundaries(jev_env, tmp_path):
    prompt = build_review_prompt(
        100.0, 120.0,
        [(94.0, 100.0, "so anyway back to the topic")],
        [(100.0, 110.0, "This episode is sponsored by BetterHelp"),
         (110.0, 120.0, "Use promo code SHOW at betterhelp.com")],
        [(120.0, 126.0, "and we are back to the conversation")],
    )
    resp = _run(prompt, make_text_fake(), tmp_path)
    content = resp["choices"][0]["message"]["content"]
    ad = json.loads(content)["ads"][0]
    assert ad["is_ad"] is True
    assert (ad["start"], ad["end"]) == (100.0, 120.0)  # Jev span edges == candidate

    verdict, start, end, method = minuspod_review_verdict(content, 100.0, 120.0)
    assert verdict == "confirmed"
    assert (start, end) == (100.0, 120.0)
    assert method == "json_object_ads_key"


def test_adjust_when_jev_span_extends_into_context(jev_env, tmp_path):
    # The ad actually starts at 94.0 (a context line reads promotional), so the
    # Jev span extends past the candidate start -> a boundary adjustment.
    prompt = build_review_prompt(
        100.0, 120.0,
        [(88.0, 94.0, "so anyway back to the topic"),
         (94.0, 100.0, "and now a word from our sponsor")],
        [(100.0, 110.0, "This episode is sponsored by BetterHelp"),
         (110.0, 120.0, "Use promo code SHOW at betterhelp.com")],
        [(120.0, 126.0, "and we are back to the conversation")],
    )
    resp = _run(prompt, make_text_fake(), tmp_path)
    content = resp["choices"][0]["message"]["content"]
    verdict, start, end, _ = minuspod_review_verdict(content, 100.0, 120.0)
    assert verdict == "adjust"
    assert start == 94.0 and end == 120.0


def test_reject_when_jev_finds_no_ad(jev_env, tmp_path):
    prompt = build_review_prompt(
        50.0, 70.0,
        [(45.0, 50.0, "Have you followed the antitrust case?")],
        [(50.0, 62.0, "The DOJ argued Apple's policies harm developers"),
         (62.0, 70.0, "They want the court to change the commission")],
        [(70.0, 74.0, "what is your take on the remedies?")],
    )
    resp = _run(prompt, make_text_fake(), tmp_path)
    content = resp["choices"][0]["message"]["content"]
    assert json.loads(content) == {"ads": []}
    verdict, start, end, _ = minuspod_review_verdict(content, 50.0, 70.0)
    assert verdict == "reject"


def test_resurrection_hit_resurrects_with_ad_signal(jev_env, tmp_path):
    prompt = build_review_prompt(
        666.7, 674.3,
        [(660.0, 666.7, "so that's our take on the case")],
        [(666.7, 674.3, "Hosted on Acast. See acast dot com slash privacy")],
        [(674.3, 680.0, "welcome back, today we talk about the launch")],
        pool="resurrection",
    )
    resp = _run(prompt, make_text_fake(), tmp_path)
    content = resp["choices"][0]["message"]["content"]
    verdict, start, end, _ = minuspod_review_verdict(content, 666.7, 674.3, pool="resurrection")
    assert verdict == "resurrect"
    assert json.loads(content)["ads"][0]["is_ad"] is True


def test_resurrection_miss_keeps_rejected(jev_env, tmp_path):
    prompt = build_review_prompt(
        200.0, 215.0,
        [(195.0, 200.0, "we've been talking about the privacy framework")],
        [(200.0, 215.0, "Apple says the framework gives users more control")],
        [(215.0, 220.0, "but critics argue Apple has too much power")],
        pool="resurrection",
    )
    resp = _run(prompt, make_text_fake(), tmp_path)
    content = resp["choices"][0]["message"]["content"]
    assert json.loads(content) == {"ads": []}
    verdict, *_ = minuspod_review_verdict(content, 200.0, 215.0, pool="resurrection")
    assert verdict == "reject"  # keep-rejected: no content cut


def test_ambiguous_overlapping_spans_are_unavailable(jev_env, tmp_path):
    prompt = build_review_prompt(
        100.0,
        200.0,
        [(94.0, 100.0, "context before")],
        [
            (100.0, 105.0, "This is sponsored by BetterHelp"),
            (105.0, 155.0, "The hosts return to their discussion"),
            (155.0, 200.0, "Use promo code SHOW at betterhelp.com"),
        ],
        [(200.0, 206.0, "context after")],
    )
    with pytest.raises(ReviewUnavailableError, match="unambiguous"):
        _run(prompt, make_text_fake(), tmp_path)


def test_high_score_without_advertising_cue_is_unavailable(jev_env, tmp_path):
    prompt = build_review_prompt(
        100.0,
        110.0,
        [(94.0, 100.0, "context before")],
        [(100.0, 110.0, "This unrelated editorial sentence has no promotion")],
        [(110.0, 116.0, "context after")],
    )
    with pytest.raises(ReviewUnavailableError, match="sufficient advertising evidence"):
        _run(prompt, make_text_fake(keywords=("unrelated",)), tmp_path)


def test_degrade_no_transcript_is_unavailable(jev_env, tmp_path):
    # Markers present (routes to review) but no [start-end] lines to score.
    prompt = build_review_prompt(
        100.0, 120.0,
        [(94.0, 100.0, "context before")],
        [(100.0, 120.0, "This episode is sponsored by BetterHelp")],
        [(120.0, 126.0, "context after")],
        with_timestamps=False,
    )
    assert is_review_request(prompt) is True
    assert parse_review_segments(prompt) == []
    with pytest.raises(ReviewUnavailableError, match="could not be parsed"):
        _run(prompt, make_text_fake(), tmp_path)


def test_degrade_no_transcript_resurrection_is_unavailable(jev_env, tmp_path):
    prompt = build_review_prompt(
        100.0, 120.0,
        [(94.0, 100.0, "context before")],
        [(100.0, 120.0, "This episode is sponsored by BetterHelp")],
        [(120.0, 126.0, "context after")],
        pool="resurrection",
        with_timestamps=False,
    )
    with pytest.raises(ReviewUnavailableError, match="could not be parsed"):
        _run(prompt, make_text_fake(), tmp_path)


def test_word_timing_is_not_mixed_into_coarse_review_segments():
    prompt = build_review_prompt(
        100.0, 120.0,
        [(94.0, 100.0, "context before")],
        [(100.0, 120.0, "This episode is sponsored by BetterHelp")],
        [(120.0, 126.0, "context after")],
    ) + (
        "Boundary word timing, use these timestamps for corrections:\n"
        "Start edge:\n[99.9s-100.0s] This\n"
        "End edge:\n[120.0s-120.0s] end\n"
    )
    segments, words = parse_review_context(prompt)
    assert all(segment["text"] not in {"This", "end"} for segment in segments)
    assert words["start"][0]["text"] == "This"
    assert words["end"][0]["end"] == 120.0


def test_rounded_zero_width_coarse_line_is_preserved_as_point_evidence():
    prompt = build_review_prompt(
        100.0, 120.0,
        [(94.0, 100.0, "context before")],
        [
            (100.0, 109.0, "This episode is sponsored by Acme"),
            (109.2, 109.2, "brief sponsor word"),
            (109.4, 120.0, "Visit example.com for details"),
        ],
        [(120.0, 126.0, "context after")],
    )

    segments, _ = parse_review_context(prompt)

    point = next(segment for segment in segments if segment["text"] == "brief sponsor word")
    assert (point["start"], point["end"]) == (109.2, 109.2)
    words = {"start": [], "end": []}
    assert _range_boundary_support(segments, words, (109.2, 109.2)) == (True, True)
    assert _range_boundary_support(segments, words, (109.1, 109.3)) == (False, False)
    assert not adapter._valid_pair(
        109.1, 109.3, (109.1, 109.3), (109.2, 109.2), 109.2, 109.2,
    )


def test_reversed_coarse_line_remains_invalid():
    prompt = build_review_prompt(
        100.0, 120.0,
        [(94.0, 100.0, "context before")],
        [(100.0, 120.0, "This episode is sponsored by Acme")],
        [(120.0, 126.0, "context after")],
    ).replace(
        "[100.0s-120.0s] This episode is sponsored by Acme",
        "[120.0s-100.0s] This episode is sponsored by Acme",
    )

    with pytest.raises(ValueError, match="timestamped review interval is invalid"):
        parse_review_context(prompt)


def test_review_word_edges_are_sorted_and_deduplicated():
    prompt = _meaningful_pair_prompt().replace(
        "[90.0s-91.0s] Editorial.\n[100.0s-101.0s] Editorial\n[106.0s-107.0s] Sponsor\n",
        "[106.0s-107.0s] Sponsor\n[100.0s-101.0s] Editorial\n"
        "[106.0s-107.0s] Sponsor\n[90.0s-91.0s] Editorial.\n",
    ).replace(
        "[114.0s-115.0s] ends.\n[119.0s-120.0s] continues.\n[129.0s-130.0s] Editorial.\n",
        "[129.0s-130.0s] Editorial.\n[119.0s-120.0s] continues.\n"
        "[114.0s-115.0s] ends.\n[119.0s-120.0s] continues.\n",
    )

    _, words = parse_review_context(prompt)

    assert [(word["start"], word["text"]) for word in words["start"]] == [
        (90.0, "Editorial."), (100.0, "Editorial"), (106.0, "Sponsor"),
    ]
    assert [(word["start"], word["text"]) for word in words["end"]] == [
        (114.0, "ends."), (119.0, "continues."), (129.0, "Editorial."),
    ]


def test_added_window_checks_cover_all_observed_speech():
    segments, words = parse_review_context(_meaningful_pair_prompt())
    checks = _programme_checks(segments, words, (90.0, 120.0), [("start", 90.0, 107.0)])
    added_targets = [
        question["instructions"]["target_speech"]
        for name, question in checks.items() if name.startswith("added_0_")
    ]

    assert "start_speech" in checks and "end_speech" in checks
    assert all(
        any(word["text"] in target for target in added_targets)
        for word in words["start"] if word["start"] < 107.0
    )


def test_review_caller_context_reaches_detection_and_all_review_stages(jev_env, tmp_path):
    prompt = _meaningful_pair_prompt().replace(
        "Podcast: My Podcast\nEpisode: Ep 1\n",
        "Podcast: My Podcast\n"
        "Episode: Ep 1\n"
        "Podcast description: sponsor history for the show\n"
        "AUDIO CUE EVIDENCE: labelled break cue at 99.5s-100.0s\n"
        "[101.0s-102.0s] Timestamped cue note, not spoken transcript\n",
    )
    payloads = []
    normal = _select_pair_fake(90.0, 115.0)

    def fake(payload, **kwargs):
        payloads.append(payload)
        return normal(payload, **kwargs)

    _run(prompt, fake, tmp_path, refine_boundaries=True)

    caller_context, transcript_text = _review_prompt_parts(prompt)
    assert len(payloads) == 12
    for payload in payloads:
        if (
            "sponsor_read" in payload["questions"]
            or "start_speech" in payload["questions"]
            or "end_unit_comparison" in payload["questions"]
            or "end_run_category" in payload["questions"]
            or "end_transition" in payload["questions"]
            or any(key.startswith("boundary_") for key in payload["questions"])
            or all(key.endswith(("_near", "_extended")) for key in payload["questions"])
        ):
            assert "caller_context" not in payload["state"]
        else:
            assert payload["state"]["caller_context"] == caller_context
        speech = payload["state"].get("assessment_speech", payload["state"].get("transcript", ""))
        assert "AUDIO CUE EVIDENCE" not in speech
        assert "Timestamped cue note" not in speech
        assert all(row["start"] != 101.0 for row in payload["state"].get("timeline", []))
    assert "[101.0s-102.0s]" in caller_context
    assert "Transcript (60s before, the candidate ad, 60s after; all lines carry [start-end] second timestamps):" not in caller_context
    assert "Boundary word timing, use these timestamps for corrections:" not in caller_context
    assert "[101.0s-102.0s]" not in transcript_text


def test_review_prompt_without_minuspod_transcript_heading_has_no_caller_context():
    prompt = "Podcast: Legacy\n[10.0s-12.0s] transcript line\n"

    context, transcript_text = _review_prompt_parts(prompt)

    assert context == ""
    assert transcript_text == prompt


def test_effective_category_actions_parse_partial_policy_and_beep():
    actions = _effective_category_actions(
        "Podcast: My Podcast\n"
        "Effective category actions: sponsor=remove, self_promo=keep, interaction=beep\n"
    )

    assert actions == {
        "sponsor": "remove", "self_promo": "keep", "interaction": "beep",
    }
    assert _effective_category_actions("Podcast: Legacy") == {}


def test_effective_category_actions_stops_at_header_line():
    actions = _effective_category_actions(
        "Effective category actions: sponsor=remove\n\n"
        "Evidence envelope: self_promo=keep\n"
    )

    assert actions == {"sponsor": "remove"}


def test_effective_category_actions_ignores_unknown_future_category():
    actions = _effective_category_actions(
        "Effective category actions: sponsor=remove, future_category=keep, self_promo=keep"
    )

    assert actions == {"sponsor": "remove", "self_promo": "keep"}


@pytest.mark.parametrize(
    "line",
    [
        "Effective category actions: sponsor=remove, sponsor=keep",
        "Effective category actions: sponsor=drop",
        "Effective category actions: sponsor",
        "Effective category actions:",
    ],
)
def test_effective_category_actions_reject_ambiguous_policy(line):
    with pytest.raises(ValueError):
        _effective_category_actions(line)


def test_duplicate_effective_category_action_headers_are_inconclusive(jev_env, tmp_path):
    prompt = _meaningful_pair_prompt().replace(
        "\nTranscript (60s before, the candidate ad, 60s after;",
        "\nEffective category actions: sponsor=remove\n"
        "Effective category actions: sponsor=remove\n\n"
        "Transcript (60s before, the candidate ad, 60s after;",
    )

    with pytest.raises(ReviewInconclusiveError) as raised:
        _run(prompt, _select_pair_fake(106.0, 120.0), tmp_path, refine_boundaries=True)

    assert raised.value.reason == "policy_conflict"
    assert raised.value.stage == "context"


@pytest.mark.parametrize(
    ("action", "held"),
    [("keep", True), ("remove", False), ("beep", False)],
)
def test_explicit_category_policy_reaches_rank_and_focused_guards(
    jev_env, tmp_path, action, held,
):
    prompt = _meaningful_pair_prompt().replace(
        "\nTranscript (60s before, the candidate ad, 60s after;",
        f"\nEffective category actions: sponsor=remove, future_category=remove, self_promo={action}\n\n"
        "Transcript (60s before, the candidate ad, 60s after;",
    )
    normal = _select_pair_fake(106.0, 120.0)
    observed = []

    def fake(payload, **kwargs):
        observed.append(payload)
        result = normal(payload, **kwargs)
        if "kept_category" in payload["questions"]:
            result["answers"]["kept_category"] = {"noul": 0.99 if action == "keep" else 0.01}
        return result

    if held:
        with pytest.raises(ReviewInconclusiveError) as raised:
            _run(prompt, fake, tmp_path, refine_boundaries=True)
        assert raised.value.proposal["reason"] == "programme_content_detected"
    else:
        _run(prompt, fake, tmp_path, refine_boundaries=True)

    policy = {"sponsor": "remove", "self_promo": action}
    relevant = [
        payload for payload in observed
        if (
            any(key.startswith("boundary_") for key in payload["questions"])
            or "sponsor_read" in payload["questions"]
            or "start_speech" in payload["questions"]
            or all(key.endswith(("_near", "_extended")) for key in payload["questions"])
        )
    ]
    assert relevant
    assert all(payload["state"]["category_actions"] == policy for payload in relevant)
    assert all(payload["state"]["candidate"] == {"start": 100.0, "end": 120.0}
               for payload in relevant if any(key.startswith("boundary_") for key in payload["questions"]))


@pytest.mark.parametrize("refine_boundaries", [False, True])
@pytest.mark.parametrize(("kept_score", "held"), [(0.99, True), (0.01, False)])
def test_keep_policy_is_checked_without_boundary_refinement(
    jev_env, tmp_path, refine_boundaries, kept_score, held,
):
    prompt = build_review_prompt(
        100.0, 120.0,
        [(90.0, 100.0, "Editorial before.")],
        [(100.0, 120.0, "Sponsor and self promotion.")],
        [(120.0, 130.0, "Editorial after.")],
    ).replace(
        "\nTranscript (60s before, the candidate ad, 60s after;",
        "\nEffective category actions: sponsor=remove, self_promo=keep\n\n"
        "Transcript (60s before, the candidate ad, 60s after;",
    )
    normal = make_text_fake()
    kept_requests = []

    def fake(payload, **kwargs):
        result = normal(payload, **kwargs)
        if "kept_category" in payload["questions"]:
            kept_requests.append(payload)
            result["answers"]["kept_category"] = {"noul": kept_score}
        return result

    if held:
        with pytest.raises(ReviewInconclusiveError) as raised:
            _run(prompt, fake, tmp_path, refine_boundaries=refine_boundaries)
        assert raised.value.reason == "programme_content_detected"
    else:
        _run(prompt, fake, tmp_path, refine_boundaries=refine_boundaries)

    assert len(kept_requests) == 1
    assert kept_requests[0]["state"]["candidate_interval_speech"] == "Sponsor and self promotion."
    instructions = kept_requests[0]["questions"]["kept_category"]["instructions"]
    assert instructions["keep_categories"] == ["self_promo"]
    assert "same podcast" in instructions["category_definitions"]["self_promo"]


def test_keep_policy_without_isolatable_candidate_speech_abstains(
    jev_env, tmp_path, monkeypatch,
):
    prompt = build_review_prompt(
        101.0, 119.0,
        [(90.0, 100.0, "Editorial before.")],
        [(100.0, 120.0, "Sponsor and self promotion.")],
        [(120.0, 130.0, "Editorial after.")],
    ).replace(
        "\nTranscript (60s before, the candidate ad, 60s after;",
        "\nEffective category actions: sponsor=remove, self_promo=keep\n\n"
        "Transcript (60s before, the candidate ad, 60s after;",
    )

    original = adapter._assessment_speech

    def missing_returned_speech(segments, word_edges, bounds):
        if bounds == (100.0, 120.0):
            return None
        return original(segments, word_edges, bounds)

    monkeypatch.setattr(adapter, "_assessment_speech", missing_returned_speech)
    with pytest.raises(ReviewInconclusiveError) as raised:
        _run(prompt, make_text_fake(), tmp_path, refine_boundaries=False)

    assert raised.value.reason == "insufficient_boundary_text"
    assert raised.value.stage == "boundary_coverage"


def test_keep_policy_checks_broader_detected_interval_without_refinement(
    jev_env, tmp_path,
):
    prompt = build_review_prompt(
        100.0, 110.0,
        [(90.0, 100.0, "Editorial before.")],
        [(100.0, 110.0, "Sponsor offer.")],
        [(110.0, 120.0, "Self promotion follows."),
         (120.0, 130.0, "Editorial after.")],
    ).replace(
        "\nTranscript (60s before, the candidate ad, 60s after;",
        "\nEffective category actions: sponsor=remove, self_promo=keep\n\n"
        "Transcript (60s before, the candidate ad, 60s after;",
    )
    normal = make_text_fake(keywords=("sponsor", "self promotion"))
    kept_requests = []

    def fake(payload, **kwargs):
        result = normal(payload, **kwargs)
        if "kept_category" in payload["questions"]:
            kept_requests.append(payload)
            result["answers"]["kept_category"] = {"noul": 0.99}
        return result

    with pytest.raises(ReviewInconclusiveError) as raised:
        _run(prompt, fake, tmp_path, refine_boundaries=False)

    assert raised.value.reason == "programme_content_detected"
    assert kept_requests[0]["state"]["candidate"] == {"start": 100.0, "end": 120.0}
    assert kept_requests[0]["state"]["candidate_interval_speech"] == (
        "Sponsor offer.\nSelf promotion follows."
    )


@pytest.mark.parametrize("refine_boundaries", [False, True])
def test_self_promo_keep_vetoes_same_show_access_without_policy_bias(
    jev_env, tmp_path, refine_boundaries,
):
    prompt = build_review_prompt(
        100.0, 120.0,
        [(90.0, 100.0, "Thanks for listening to My Podcast.")],
        [(100.0, 110.0, "Subscribe to My Podcast for early ad-free episodes."),
         (110.0, 120.0, "Sponsor Acme offers its independent service.")],
        [(120.0, 130.0, "The paid sponsor follows.")],
    ).replace(
        "\nTranscript (60s before, the candidate ad, 60s after;",
        "\nEffective category actions: sponsor=remove, self_promo=keep\n\n"
        "Transcript (60s before, the candidate ad, 60s after;",
    ) + (
        "Boundary word timing, use these timestamps for corrections:\n"
        "Start edge:\n[100.0s-101.0s] Subscribe\n"
        "End edge:\n[119.0s-120.0s] episodes.\n"
    )
    normal = _select_pair_fake(100.0, 120.0)
    relation_requests = []

    def fake(payload, **kwargs):
        result = normal(payload, **kwargs)
        relation_names = [
            name for name in payload["questions"]
            if name.startswith("same_show_access_")
        ]
        if relation_names:
            relation_requests.append(payload)
            for name in relation_names:
                result["answers"][name] = {"noul": 0.97}
        if "kept_category" in payload["questions"]:
            result["answers"]["kept_category"] = {"noul": 0.01}
        return result

    with pytest.raises(ReviewInconclusiveError) as raised:
        _run(
            prompt, fake, tmp_path,
            refine_boundaries=refine_boundaries,
            review_choice_enter=0.85,
        )

    assert raised.value.reason == "category_policy_unconfirmed"
    assert raised.value.stage == "focused_validation"
    assert raised.value.score == pytest.approx(0.97)
    assert relation_requests
    for request in relation_requests:
        assert request["state"]["podcast"] == "My Podcast"
        assert request["state"]["episode"] == "Ep 1"
        assert "Sponsor Acme" in request["state"]["candidate_interval_speech"]
        assert "category_actions" not in request["state"]
        assert "category_policy_rule" not in request["state"]


@pytest.mark.parametrize("action", ["remove", "beep"])
def test_self_promo_removal_actions_skip_same_show_access_guard(
    jev_env, tmp_path, action,
):
    prompt = build_review_prompt(
        100.0, 120.0,
        [(90.0, 100.0, "The episode ends.")],
        [(100.0, 120.0, "Subscribe to My Podcast for early ad-free episodes.")],
        [(120.0, 130.0, "The paid sponsor follows.")],
    ).replace(
        "\nTranscript (60s before, the candidate ad, 60s after;",
        f"\nEffective category actions: sponsor=remove, self_promo={action}\n\n"
        "Transcript (60s before, the candidate ad, 60s after;",
    )
    normal = make_text_fake(keywords=("subscribe",))
    seen_relation = False

    def fake(payload, **kwargs):
        nonlocal seen_relation
        seen_relation |= any(
            name.startswith("same_show_access_") for name in payload["questions"]
        )
        return normal(payload, **kwargs)

    _run(prompt, fake, tmp_path, refine_boundaries=False)
    assert seen_relation is False


def test_self_promo_keep_allows_independent_sponsor(
    jev_env, tmp_path,
):
    prompt = build_review_prompt(
        100.0, 120.0,
        [(90.0, 100.0, "The episode ends.")],
        [(100.0, 120.0, "Sponsor Acme offers its independent subscription service.")],
        [(120.0, 130.0, "The episode resumes.")],
    ).replace(
        "\nTranscript (60s before, the candidate ad, 60s after;",
        "\nEffective category actions: sponsor=remove, self_promo=keep\n\n"
        "Transcript (60s before, the candidate ad, 60s after;",
    )
    normal = make_text_fake(keywords=("acme",))
    relation_requests = []

    def fake(payload, **kwargs):
        result = normal(payload, **kwargs)
        relation_names = [
            name for name in payload["questions"]
            if name.startswith("same_show_access_")
        ]
        if relation_names:
            relation_requests.append(payload)
            for name in relation_names:
                result["answers"][name] = {"noul": 0.02}
        if "kept_category" in payload["questions"]:
            result["answers"]["kept_category"] = {"noul": 0.01}
        return result

    _run(prompt, fake, tmp_path, refine_boundaries=False)
    assert relation_requests


@pytest.mark.parametrize(
    ("selected", "expected"),
    [
        ((106.0, 120.0), (106.0, 120.0)),
        ((100.0, 115.0), (100.0, 120.0)),
        ((90.0, 120.0), (100.0, 120.0)),
        ((100.0, 130.0), (100.0, 120.0)),
        ((90.0, 115.0), (100.0, 120.0)),
    ],
)
def test_opt_in_refinement_applies_selected_meaningful_pair(jev_env, tmp_path, selected, expected):
    base = _select_pair_fake(*selected)

    def fake(payload, **kwargs):
        result = base(payload, **kwargs)
        if "interval_comparison" in payload["questions"]:
            choice = "adjusted" if expected == selected else "original"
            _set_choice_answer(result, payload, "interval_comparison", choice)
        return result

    response = _run(
        _meaningful_pair_prompt(),
        fake,
        tmp_path,
        refine_boundaries=True,
    )
    ad = json.loads(response["choices"][0]["message"]["content"])["ads"][0]
    assert (ad["start"], ad["end"]) == expected
    verdict, start, end, _ = minuspod_review_verdict(
        response["choices"][0]["message"]["content"], 100.0, 120.0
    )
    assert verdict == ("confirmed" if expected == (100.0, 120.0) else "adjust")
    assert (start, end) == expected


def test_start_alternative_preserves_provider_choice_when_probability_is_not_max(
    jev_env, tmp_path,
):
    base = _select_pair_fake(106.0, 120.0)
    alternative_requests = []

    def fake(payload, **kwargs):
        result = base(payload, **kwargs)
        if "boundary_start" in payload["questions"]:
            selected = _boundary_option("boundary_start", 106.0)
            alternative = _boundary_option("boundary_start", 100.0)
            criteria = payload["questions"]["boundary_start"]["criteria"]
            remainder = 0.1 / (len(criteria) - 2)
            result["answers"]["boundary_start"] = {
                "choice": selected,
                "confidence": 0.2,
                "probabilities": {
                    key: 0.2 if key == selected else 0.7 if key == alternative else remainder
                    for key in criteria
                },
            }
        if "boundary_start_alternative" in payload["questions"]:
            alternative_requests.append(payload)
            _set_choice_answer(
                result, payload, "boundary_start_alternative", "selected",
            )
        return result

    response = _run(_meaningful_pair_prompt(), fake, tmp_path, refine_boundaries=True)
    ad = json.loads(response["choices"][0]["message"]["content"])["ads"][0]

    assert (ad["start"], ad["end"]) == (106.0, 120.0)
    assert alternative_requests[0]["state"]["provider_selected"] == "selected"
    assert alternative_requests[0]["state"]["start_options"]["selected"]["start"] == 106.0
    assert alternative_requests[0]["state"]["start_options"]["earlier"]["start"] == 100.0
    assert alternative_requests[0]["state"]["added_speech_if_earlier"] == "Editorial transition."


@pytest.mark.parametrize(
    "added_speech,prefix_score,expected_start",
    [
        ("Back to the discussion.", 0.98, 106.0),
        ("Sponsor offer begins.", 0.02, 100.0),
    ],
)
def test_start_alternative_protects_episode_return_but_allows_ad_setup(
    jev_env, tmp_path, added_speech, prefix_score, expected_start,
):
    prompt = _meaningful_pair_prompt().replace("Editorial transition.", added_speech)
    select = _select_pair_fake(106.0, 120.0)
    prefix_requests = []

    def fake(payload, **kwargs):
        result = select(payload, **kwargs)
        questions = payload["questions"]
        if "boundary_start" in questions:
            selected = _boundary_option("boundary_start", 106.0)
            earlier = _boundary_option("boundary_start", 100.0)
            criteria = questions["boundary_start"]["criteria"]
            result["answers"]["boundary_start"] = {
                "choice": selected,
                "confidence": 0.2,
                "probabilities": {
                    key: 0.2 if key == selected else 0.7 if key == earlier
                    else 0.1 / (len(criteria) - 2)
                    for key in criteria
                },
            }
        if "boundary_start_alternative" in questions:
            _set_choice_answer(result, payload, "boundary_start_alternative", "earlier")
            prefix_requests.append(payload)
            result["answers"]["added_prefix_return"] = {"noul": prefix_score}
        if "interval_comparison" in questions:
            _set_choice_answer(result, payload, "interval_comparison", "adjusted")
        return result

    response = _run(prompt, fake, tmp_path, refine_boundaries=True)
    ad = json.loads(response["choices"][0]["message"]["content"])["ads"][0]

    assert len(prefix_requests) == 1
    assert prefix_requests[0]["state"]["added_speech_if_earlier"] == added_speech
    assert (ad["start"], ad["end"]) == (expected_start, 120.0)


def test_widened_start_programme_check_vetoes_episode_return(
    jev_env, tmp_path,
):
    prompt = _meaningful_pair_prompt().replace("Editorial before.", "Back to the discussion.")
    select = _select_pair_fake(90.0, 120.0)
    prefix_requests = []

    def fake(payload, **kwargs):
        result = select(payload, **kwargs)
        questions = payload["questions"]
        if "interval_comparison" in questions:
            _set_choice_answer(result, payload, "interval_comparison", "adjusted")
        if "added_prefix_return" in questions:
            prefix_requests.append(payload)
            result["answers"]["added_prefix_return"] = {"noul": 0.98}
        return result

    response = _run(prompt, fake, tmp_path, refine_boundaries=True)
    ad = json.loads(response["choices"][0]["message"]["content"])["ads"][0]

    assert len(prefix_requests) == 1
    assert prefix_requests[0]["state"]["added_speech_if_earlier"] == "Back to the discussion."
    assert "start_speech" in prefix_requests[0]["questions"]
    assert (ad["start"], ad["end"]) == (100.0, 120.0)


def test_unknown_start_does_not_trigger_alternative_choice(
    jev_env, tmp_path,
):
    base = _select_pair_fake(106.0, 120.0)
    stages = []

    def fake(payload, **kwargs):
        result = base(payload, **kwargs)
        stages.extend(payload["questions"])
        if "boundary_start" in payload["questions"]:
            first = _boundary_option("boundary_start", 106.0)
            second = _boundary_option("boundary_start", 100.0)
            criteria = payload["questions"]["boundary_start"]["criteria"]
            remainder = 0.05 / (len(criteria) - 3)
            result["answers"]["boundary_start"] = {
                "choice": "unknown",
                "confidence": 0.3,
                "probabilities": {
                    key: 0.3 if key == "unknown" else 0.4 if key == first
                    else 0.25 if key == second else remainder
                    for key in criteria
                },
            }
        return result

    response = _run(_meaningful_pair_prompt(), fake, tmp_path, refine_boundaries=True)
    ad = json.loads(response["choices"][0]["message"]["content"])["ads"][0]

    assert (ad["start"], ad["end"]) == (100.0, 120.0)
    assert "boundary_start_alternative" not in stages


def test_start_alternative_neither_is_inconclusive(jev_env, tmp_path):
    base = _select_pair_fake(106.0, 120.0)

    def fake(payload, **kwargs):
        result = base(payload, **kwargs)
        if "boundary_start_alternative" in payload["questions"]:
            _set_choice_answer(
                result, payload, "boundary_start_alternative", "neither",
            )
        return result

    with pytest.raises(ReviewInconclusiveError) as raised:
        _run(_meaningful_pair_prompt(), fake, tmp_path, refine_boundaries=True)

    assert raised.value.reason == "choice_inconclusive"
    assert raised.value.stage == "choice_rank"


def test_single_start_option_skips_alternative_choice(jev_env, tmp_path):
    prompt = build_review_prompt(
        100.0, 120.0,
        [(90.0, 100.0, "Editorial before.")],
        [(100.0, 120.0, "Sponsor offer and sign-off.")],
        [(120.0, 130.0, "Editorial after.")],
    ) + (
        "Boundary word timing, use these timestamps for corrections:\n"
        "Start edge:\n[100.0s-101.0s] Sponsor\n"
        "End edge:\n[119.0s-120.0s] sign-off.\n"
    )
    normal = make_text_fake()
    stages = []

    def fake(payload, **kwargs):
        stages.extend(payload["questions"])
        return normal(payload, **kwargs)

    _run(prompt, fake, tmp_path, refine_boundaries=True)

    assert "boundary_start_alternative" not in stages


def test_unsupported_selected_start_skips_alternative_choice(
    jev_env, tmp_path, monkeypatch,
):
    original = adapter._assessment_speech

    def missing_selected_speech(segments, word_edges, bounds):
        if bounds == (106.0, 120.0):
            return None
        return original(segments, word_edges, bounds)

    monkeypatch.setattr(adapter, "_assessment_speech", missing_selected_speech)
    normal = _select_pair_fake(106.0, 120.0)
    stages = []

    def fake(payload, **kwargs):
        stages.extend(payload["questions"])
        return normal(payload, **kwargs)

    response = _run(
        _meaningful_pair_prompt(), fake, tmp_path, refine_boundaries=True,
    )
    ad = json.loads(response["choices"][0]["message"]["content"])["ads"][0]

    assert "boundary_start_alternative" not in stages
    assert (ad["start"], ad["end"]) == (100.0, 120.0)


def test_refinement_centers_fine_context_on_distant_selected_end(jev_env, tmp_path):
    prompt = build_review_prompt(
        100.0, 180.5,
        [(90.0, 100.0, "Discussion ends.")],
        [(100.0, 144.0, "Acme sponsor offer and sign-off."),
         (144.0, 150.0, "Back to the discussion."),
         (150.0, 181.0, "The discussion continues.")],
        [(181.0, 190.0, "More discussion follows.")],
    ) + (
        "Boundary word timing, use these timestamps for corrections:\n"
        "Start edge:\n[100.0s-101.0s] Acme\n"
        "End edge:\n[143.0s-144.0s] sign-off.\n"
        "[144.0s-145.0s] Back\n[179.0s-180.0s] discussion.\n"
    )
    select = _select_pair_fake(100.0, 144.0)
    fine_contexts = []
    coarse_contexts = []

    def fake(payload, **kwargs):
        result = select(payload, **kwargs)
        if "boundary_end" in payload["questions"]:
            coarse_contexts.append(payload["state"]["end_context"])
        if "boundary_end_word" in payload["questions"]:
            fine_contexts.append(payload["state"]["end_context"])
        if "interval_comparison" in payload["questions"]:
            _set_choice_answer(result, payload, "interval_comparison", "adjusted")
        return result

    response = _run(prompt, fake, tmp_path, refine_boundaries=True)
    ad = json.loads(response["choices"][0]["message"]["content"])["ads"][0]

    assert len(coarse_contexts) == 1
    assert "Acme sponsor offer and sign-off" in coarse_contexts[0]
    assert "Back to the discussion" in coarse_contexts[0]
    assert "More discussion follows" in coarse_contexts[0]
    assert len(fine_contexts) == 1
    assert "Acme sponsor offer and sign-off" in fine_contexts[0]
    assert "Back to the discussion" in fine_contexts[0]
    assert "More discussion follows" in fine_contexts[0]
    assert (ad["start"], ad["end"]) == (100.0, 144.0)


def test_coarse_end_context_rejects_detector_edge_beyond_search_cap(jev_env, tmp_path):
    prompt = build_review_prompt(
        100.0, 180.0,
        [(90.0, 100.0, "Discussion ends.")],
        [(100.0, 140.0, "Sponsor opening."),
         (140.0, 180.0, "Candidate sponsor ending."),
         (180.0, 205.0, "Bridge sponsor speech."),
         (205.0, 245.0, "Later sponsor message.")],
        [(245.0, 270.0, "After the later message.")],
    ) + (
        "Boundary word timing, use these timestamps for corrections:\n"
        "Start edge:\n[100.0s-101.0s] Sponsor\n"
        "End edge:\n[179.0s-180.0s] ending.\n"
    )
    normal = _select_pair_fake(100.0, 180.0)
    end_contexts = []

    def fake(payload, **kwargs):
        result = normal(payload, **kwargs)
        if "boundary_end" in payload["questions"]:
            end_contexts.append(payload["state"]["end_context"])
        return result

    _run(prompt, fake, tmp_path, refine_boundaries=True)

    assert len(end_contexts) == 1
    assert "Candidate sponsor ending." in end_contexts[0]
    assert "After the later message." not in end_contexts[0]


def test_supported_expansion_does_not_require_partial_added_delta(jev_env, tmp_path):
    prompt = build_review_prompt(
        100.0, 119.5,
        [(94.0, 100.0, "Discussion ends.")],
        [(100.0, 118.0, "This episode is sponsored by Acme."),
         (118.0, 120.0, "Final sponsor sign-off.")],
        [(120.0, 126.0, "The interview resumes.")],
    ) + (
        "Boundary word timing, use these timestamps for corrections:\n"
        "Start edge:\n[100.0s-101.0s] This\n"
        "End edge:\n[119.0s-120.0s] sign-off.\n"
    )
    select = _select_pair_fake(100.0, 120.0)
    comparison_requests = []

    def fake(payload, **kwargs):
        result = select(payload, **kwargs)
        if "interval_comparison" in payload["questions"]:
            comparison_requests.append(payload)
        return result

    response = _run(prompt, fake, tmp_path, refine_boundaries=True)
    ad = json.loads(response["choices"][0]["message"]["content"])["ads"][0]

    assert (ad["start"], ad["end"]) == (100.0, 120.0)
    assert comparison_requests == []


def test_weak_edge_choice_can_reach_pair_comparison(jev_env, tmp_path):
    base = _select_pair_fake(106.0, 120.0)

    def fake(payload, **kwargs):
        result = base(payload, **kwargs)
        if "boundary_start" in payload["questions"]:
            answer = result["answers"]["boundary_start"]
            chosen = answer["choice"]
            alternatives = [key for key in answer["probabilities"] if key != chosen]
            answer["probabilities"] = {
                key: 0.4 if key == chosen else 0.35 if key == alternatives[0] else 0.25 / (len(alternatives) - 1)
                for key in answer["probabilities"]
            }
        return result

    response = _run(_meaningful_pair_prompt(), fake, tmp_path, refine_boundaries=True)
    ad = json.loads(response["choices"][0]["message"]["content"])["ads"][0]

    assert (ad["start"], ad["end"]) == (106.0, 120.0)


def test_weak_choice_cannot_confirm_original_with_same_ad_after_end(jev_env, tmp_path):
    prompt = build_review_prompt(
        100.0, 118.0,
        [(94.0, 100.0, "Discussion ends.")],
        [(100.0, 118.0, "This portion is sponsored by Acme."),
         (118.0, 120.0, "Use the Acme offer at acme.com.")],
        [(120.0, 130.0, "Back to the discussion.")],
    ) + (
        "Boundary word timing, use these timestamps for corrections:\n"
        "Start edge:\n[100.0s-101.0s] This\n"
        "End edge:\n[117.0s-118.0s] Acme.\n[118.0s-119.0s] Use\n"
        "[119.0s-120.0s] acme.com.\n"
    )
    normal = make_text_fake()

    def fake(payload, **kwargs):
        result = normal(payload, **kwargs)
        if "boundary_end" in payload["questions"]:
            answer = result["answers"]["boundary_end"]
            alternatives = [key for key in answer["probabilities"] if key != answer["choice"]]
            answer["probabilities"] = {
                key: 0.4 if key == answer["choice"] else 0.35 if key == alternatives[0] else 0.25 / (len(alternatives) - 1)
                for key in answer["probabilities"]
            }
        if "interval_comparison" in payload["questions"]:
            assert "Use" in payload["state"]["original_after_speech"]
            _set_choice_answer(result, payload, "interval_comparison", "neither")
        if "sponsor_read" in payload["questions"]:
            result["answers"]["sponsor_read"] = {"noul": 0.4}
        return result

    with pytest.raises(ReviewInconclusiveError, match="could not confirm a safe advertising interval"):
        _run(prompt, fake, tmp_path, refine_boundaries=True)


def test_separate_neighboring_ad_does_not_block_complete_cut(jev_env, tmp_path):
    prompt = build_review_prompt(
        100.0, 120.0,
        [(94.0, 100.0, "Discussion ends.")],
        [(100.0, 120.0, "Acme sponsor offer and sign-off.")],
        [(120.0, 130.0, "A separate Beacon sponsor offer.")],
    ) + (
        "Boundary word timing, use these timestamps for corrections:\n"
        "Start edge:\n[100.0s-101.0s] Acme\n"
        "End edge:\n[119.0s-120.0s] sign-off.\n[120.0s-121.0s] A\n"
        "[121.0s-122.0s] separate\n"
    )
    normal = make_text_fake()

    def fake(payload, **kwargs):
        result = normal(payload, **kwargs)
        if "after_continuation" in payload["questions"]:
            assert "same advertising message" in payload["questions"]["after_continuation"]["instructions"]
            assert "separate" in payload["state"]["after_edge_speech"]
            result["answers"]["after_continuation"] = {"noul": 0.02}
        return result

    response = _run(prompt, fake, tmp_path, refine_boundaries=True)
    ad = json.loads(response["choices"][0]["message"]["content"])["ads"][0]
    assert (ad["start"], ad["end"]) == (100.0, 120.0)


def test_unrelated_editorial_vetoes_ad_present_in_mixed_cut(jev_env, tmp_path):
    normal = make_text_fake()
    evidence_states = []

    def fake(payload, **kwargs):
        result = normal(payload, **kwargs)
        if "evidence" in payload["questions"]:
            evidence_states.append(payload["state"])
            result["answers"]["evidence"] = {"noul": 0.98}
        if "interval_comparison" in payload["questions"]:
            assert "Editorial transition" in payload["state"]["original_speech"]
            _set_choice_answer(result, payload, "interval_comparison", "neither")
        if "sponsor_read" in payload["questions"] and "Editorial transition" in payload["state"]["speech"]:
            result["answers"]["sponsor_read"] = {"noul": 0.2}
        return result

    with pytest.raises(ReviewInconclusiveError, match="could not confirm a safe advertising interval"):
        _run(_meaningful_pair_prompt(), fake, tmp_path, refine_boundaries=True)
    assert evidence_states[0]["candidate_interval_speech"].startswith("Editorial transition.")
    assert "transcript" not in evidence_states[0]


def test_sentence_interior_veto_isolates_show_island_inside_coarse_segment(
    jev_env, tmp_path,
):
    prompt = build_review_prompt(
        100.0, 130.0,
        [(94.0, 100.0, "Discussion ends.")],
        [(100.0, 130.0, "Acme sponsor offer. We are back. The interview resumes now. Acme sponsor sign-off.")],
        [(130.0, 136.0, "Discussion continues.")],
    ) + (
        "Boundary word timing, use these timestamps for corrections:\n"
        "Start edge:\n[100.0s-101.0s] Acme\n"
        "End edge:\n[129.0s-130.0s] sign-off.\n"
    )
    normal = make_text_fake()
    interior_targets = []

    def fake(payload, **kwargs):
        result = normal(payload, **kwargs)
        for name, question in payload["questions"].items():
            if name.startswith("interior_"):
                target = question["instructions"]["target_speech"]
                interior_targets.append(target)
                result["answers"][name] = {"noul": 0.9 if target.startswith("We are back") else 0.02}
        return result

    with pytest.raises(ReviewInconclusiveError) as raised:
        _run(prompt, fake, tmp_path, refine_boundaries=True)

    assert "We are back. The interview resumes now." in interior_targets
    assert raised.value.fallback["reason"] == "programme_content_detected"


def test_separate_sponsor_does_not_reclassify_independent_personal_story(
    jev_env, tmp_path,
):
    prompt = build_review_prompt(
        100.0, 120.0,
        [(94.0, 100.0, "Discussion continues.")],
        [(100.0, 110.0, "After my father died, I spent a year rebuilding our family home."),
         (110.0, 120.0, "This episode is sponsored by Acme. Visit acme.com.")],
        [(120.0, 126.0, "The interview resumes.")],
    ) + (
        "Boundary word timing, use these timestamps for corrections:\n"
        "Start edge:\n[100.0s-101.0s] After\n"
        "End edge:\n[119.0s-120.0s] acme.com.\n"
    )
    normal = make_text_fake()
    programme_states = []

    def fake(payload, **kwargs):
        result = normal(payload, **kwargs)
        if "start_speech" in payload["questions"]:
            programme_states.append(payload["state"])
            for name, question in payload["questions"].items():
                target = question["instructions"]["target_speech"]
                result["answers"][name] = {
                    "noul": 0.95 if "father died" in target else 0.02
                }
        return result

    with pytest.raises(ReviewInconclusiveError) as raised:
        _run(prompt, fake, tmp_path, refine_boundaries=True)

    assert raised.value.reason == "original_range_not_confirmed"
    assert raised.value.fallback["reason"] == "programme_content_detected"
    assert len(programme_states) == 1
    assert "father died" in programme_states[0]["candidate_interval_speech"]
    assert "sponsored by Acme" in programme_states[0]["candidate_interval_speech"]
    assert "not mere proximity" in programme_states[0]["evaluation_rule"]
    assert raised.value.fallback["score"] == 0.95


def test_rank_state_uses_nearby_context_without_duplicate_word_arrays(jev_env, tmp_path):
    normal = make_text_fake()

    def fake(payload, **kwargs):
        if "boundary_start" in payload["questions"]:
            state = payload["state"]
            assert "Editorial before" in state["start_context"]
            assert set(state) == {"candidate", "start_context"}
            assert state["candidate"] == {"start": 100.0, "end": 120.0}
            assert any(
                option.startswith("The sponsor message begins with:")
                for option in payload["questions"]["boundary_start"]["criteria"].values()
            )
            assert "transcript" not in state
            assert "boundary_words" not in state
            assert "timeline" not in state
        if "boundary_end" in payload["questions"]:
            assert "Sponsor continues" in payload["state"]["end_context"]
        return normal(payload, **kwargs)

    _run(_meaningful_pair_prompt(), fake, tmp_path, refine_boundaries=True)


def test_end_hierarchy_selects_an_utterance_before_an_exact_word():
    segments = [
        {"start": 100.0, "end": 104.0, "text": "Use code ACME."},
        {"start": 104.0, "end": 106.0, "text": "Welcome back."},
    ]
    words = [
        {"start": 100.0, "end": 101.0, "text": "Use"},
        {"start": 101.0, "end": 102.0, "text": "code"},
        {"start": 102.0, "end": 104.0, "text": "ACME."},
        {"start": 104.0, "end": 105.0, "text": "Welcome"},
        {"start": 105.0, "end": 106.0, "text": "back."},
    ]
    coarse, units = adapter._unit_end_question(
        segments, words, [101.0, 102.0, 104.0, 105.0, 106.0], 104.0,
    )

    sponsor_unit = next(value for value in units.values() if not isinstance(value, float) and value[-1]["text"] == "ACME.")
    fine, values = adapter._word_end_question(
        words, [101.0, 102.0, 104.0, 105.0, 106.0], sponsor_unit,
    )

    assert list(coarse["criteria"])[0] == "unknown"
    assert any("Use code ACME." in text for text in coarse["criteria"].values())
    assert any("following utterance: 'Welcome back.'" in text for text in coarse["criteria"].values())
    assert set(values.values()) == {101.0, 102.0, 104.0}
    assert any("At 104.00s, removed speech ends: 'Use code ACME.'" in text for text in fine["criteria"].values())
    assert any("kept speech begins: 'Welcome back.'" in text for text in fine["criteria"].values())


def test_end_word_options_allow_a_complete_closing_phrase_at_context_end():
    words = [
        {"start": 100.0, "end": 101.0, "text": "Visit"},
        {"start": 101.0, "end": 102.0, "text": "example"},
        {"start": 102.0, "end": 103.0, "text": ".com"},
    ]

    question, _ = adapter._word_end_question(words, [101.0, 102.0, 103.0], words)

    assert "complete URL" in question["instructions"]
    assert any(
        "removed speech ends: 'Visit example .com'" in option
        and "no later speech is present" in option
        for option in question["criteria"].values()
    )


def test_end_word_options_keep_truncated_closing_phrases_unknown():
    words = [
        {"start": 100.0, "end": 101.0, "text": "Visit"},
        {"start": 101.0, "end": 102.0, "text": "example"},
        {"start": 102.0, "end": 103.0, "text": "dot"},
    ]

    question, _ = adapter._word_end_question(words, [101.0, 102.0, 103.0], words)

    assert "Choose unknown when the supplied speech ends mid-URL or mid-phrase" in question["instructions"]
    assert any(
        "removed speech ends: 'Visit example dot'" in option
        and "no later speech is present" in option
        for option in question["criteria"].values()
    )


@pytest.mark.parametrize("programme_score", [0.02, 0.98])
def test_complete_terminal_cta_reaches_fine_end_and_still_checks_programme(
    jev_env, tmp_path, programme_score,
):
    prompt = build_review_prompt(
        100.0, 120.0,
        [(90.0, 100.0, "Editorial before.")],
        [(100.0, 110.0, "Sponsor offer."),
         (110.0, 120.0, "Visit example .com.")],
        [],
    ) + (
        "Boundary word timing, use these timestamps for corrections:\n"
        "Start edge:\n[100.0s-101.0s] Sponsor\n"
        "End edge:\n[110.0s-111.0s] Visit\n"
        "[111.0s-112.0s] example\n[112.0s-120.0s] .com.\n"
    )
    select = _select_pair_fake(100.0, 120.0)
    coarse = []
    fine = []
    programme = []
    closing = []

    def fake(payload, **kwargs):
        result = select(payload, **kwargs)
        questions = payload["questions"]
        if "boundary_end" in questions:
            coarse.append(payload)
        if "boundary_end_word" in questions:
            fine.append(payload)
        if "whole_speech" in questions:
            programme.append(payload)
            result["answers"]["whole_speech"] = {"noul": programme_score}
        if "terminal_closing" in questions:
            closing.append(payload)
            result["answers"]["terminal_closing"] = {"noul": 0.98}
        return result

    if programme_score >= 0.85:
        with pytest.raises(ReviewInconclusiveError) as raised:
            _run(prompt, fake, tmp_path, refine_boundaries=True)
        assert raised.value.reason == "original_range_not_confirmed"
        assert raised.value.fallback["reason"] == "programme_content_detected"
    else:
        response = _run(prompt, fake, tmp_path, refine_boundaries=True)
        ad = json.loads(response["choices"][0]["message"]["content"])["ads"][0]
        assert (ad["start"], ad["end"]) == (100.0, 120.0)

    assert len(coarse) == 1
    instructions = coarse[0]["questions"]["boundary_end"]["instructions"]
    assert "no later speech is supplied" in instructions
    assert "complete" in instructions and "mid-URL or mid-phrase" in instructions
    assert fine and programme
    assert bool(closing) == (programme_score < 0.85)
    if closing:
        assert closing[0]["state"]["closing_speech"].endswith(".com.")
        assert closing[0]["state"]["selected_boundary"] == 120.0
    assert "no later speech is present" in " ".join(
        fine[0]["questions"]["boundary_end_word"]["criteria"].values()
    )


def test_truncated_terminal_phrase_blocks_otherwise_accepted_boundary(
    jev_env, tmp_path,
):
    prompt = build_review_prompt(
        100.0, 120.0,
        [(90.0, 100.0, "Editorial before.")],
        [(100.0, 110.0, "Sponsor offer."),
         (110.0, 120.0, "Visit example dot")],
        [],
    ) + (
        "Boundary word timing, use these timestamps for corrections:\n"
        "Start edge:\n[100.0s-101.0s] Sponsor\n"
        "End edge:\n[110.0s-111.0s] Visit\n"
        "[111.0s-112.0s] example\n[112.0s-120.0s] dot\n"
    )
    select = _select_pair_fake(100.0, 120.0)
    stages = []

    def fake(payload, **kwargs):
        result = select(payload, **kwargs)
        questions = payload["questions"]
        stages.extend(questions)
        if "terminal_closing" in questions:
            assert payload["state"]["closing_speech"].endswith("example dot")
            result["answers"]["terminal_closing"] = {"noul": 0.02}
        return result

    with pytest.raises(ReviewInconclusiveError) as raised:
        _run(prompt, fake, tmp_path, refine_boundaries=True)

    assert raised.value.reason == "terminal_closing_unconfirmed"
    assert raised.value.stage == "focused_validation"
    assert "boundary_end" in stages
    assert "boundary_end_word" in stages
    assert "sponsor_read" in stages
    assert "terminal_closing" in stages


def test_terminal_closing_checks_last_word_before_padded_row_end(
    jev_env, tmp_path,
):
    prompt = build_review_prompt(
        100.0, 119.8,
        [(90.0, 100.0, "Editorial before.")],
        [(100.0, 120.0, "Sponsor offer. Visit example dot")],
        [],
    ) + (
        "Boundary word timing, use these timestamps for corrections:\n"
        "Start edge:\n[100.0s-101.0s] Sponsor\n"
        "End edge:\n[100.0s-101.0s] Sponsor\n"
        "[101.0s-102.0s] offer.\n[118.0s-118.5s] Visit\n"
        "[118.5s-119.0s] example\n[119.0s-119.8s] dot\n"
    )
    select = _select_pair_fake(100.0, 119.8)
    stages = []

    def fake(payload, **kwargs):
        result = select(payload, **kwargs)
        stages.extend(payload["questions"])
        if "terminal_closing" in payload["questions"]:
            result["answers"]["terminal_closing"] = {"noul": 0.02}
        return result

    with pytest.raises(ReviewInconclusiveError) as raised:
        _run(prompt, fake, tmp_path, refine_boundaries=True)

    assert raised.value.reason == "terminal_closing_unconfirmed"
    assert "terminal_closing" in stages


def _terminal_fallback_prompt() -> str:
    return build_review_prompt(
        100.0, 120.0,
        [(90.0, 100.0, "Editorial before.")],
        [(100.0, 106.0, "Editorial transition."),
         (106.0, 120.0, "Sponsor offer. Visit example dot")],
        [],
    ) + (
        "Boundary word timing, use these timestamps for corrections:\n"
        "Start edge:\n[100.0s-101.0s] Editorial\n[106.0s-107.0s] Sponsor\n"
        "End edge:\n[100.0s-101.0s] Editorial\n[106.0s-107.0s] Sponsor\n"
        "[117.0s-118.0s] Visit\n[118.0s-119.0s] example\n[119.0s-120.0s] dot\n"
    )


def _terminal_fallback_fake(original_score: float):
    select = _select_pair_fake(106.0, 120.0)
    closings = []

    def fake(payload, **kwargs):
        result = select(payload, **kwargs)
        if "interval_comparison" in payload["questions"]:
            _set_choice_answer(result, payload, "interval_comparison", "adjusted")
        if "terminal_closing" in payload["questions"]:
            speech = payload["state"]["closing_speech"]
            closings.append(speech)
            original = speech.startswith("Editorial")
            result["answers"]["terminal_closing"] = {"noul": original_score if original else 0.02}
        return result

    return fake, closings


def test_terminal_closing_falls_back_to_safe_original(jev_env, tmp_path):
    fake, closings = _terminal_fallback_fake(0.98)

    response = _run(_terminal_fallback_prompt(), fake, tmp_path, refine_boundaries=True)
    ad = json.loads(response["choices"][0]["message"]["content"])["ads"][0]

    assert (ad["start"], ad["end"]) == (100.0, 120.0)
    assert len(closings) == 2


def test_terminal_closing_abstains_when_original_also_fails(jev_env, tmp_path):
    fake, closings = _terminal_fallback_fake(0.02)

    with pytest.raises(ReviewInconclusiveError) as raised:
        _run(_terminal_fallback_prompt(), fake, tmp_path, refine_boundaries=True)

    error = raised.value
    assert error.reason == "terminal_closing_unconfirmed"
    assert error.stage == "focused_validation"
    assert len(closings) == 2
    for detail, bounds in ((error.proposal, (106.0, 120.0)), (error.fallback, (100.0, 120.0))):
        assert (detail["range_start"], detail["range_end"]) == bounds
        assert detail["score"] == pytest.approx(0.02)
        assert detail["threshold"] == pytest.approx(0.95)
        assert detail["cache_hit"] is False


def test_terminal_allowance_does_not_include_later_programme_speech(
    jev_env, tmp_path,
):
    prompt = build_review_prompt(
        100.0, 120.0,
        [(90.0, 100.0, "Editorial before.")],
        [(100.0, 110.0, "Sponsor offer."),
         (110.0, 120.0, "Visit example .com.")],
        [(120.0, 130.0, "Editorial return.")],
    ) + (
        "Boundary word timing, use these timestamps for corrections:\n"
        "Start edge:\n[100.0s-101.0s] Sponsor\n"
        "End edge:\n[110.0s-111.0s] Visit\n"
        "[111.0s-112.0s] example\n[112.0s-120.0s] .com.\n"
        "[120.0s-121.0s] Editorial\n[121.0s-130.0s] return.\n"
    )
    select = _select_pair_fake(100.0, 120.0)
    end_requests = []
    closing_requests = []

    def fake(payload, **kwargs):
        result = select(payload, **kwargs)
        if "boundary_end_word" in payload["questions"]:
            end_requests.append(payload)
        if "terminal_closing" in payload["questions"]:
            closing_requests.append(payload)
        return result

    response = _run(prompt, fake, tmp_path, refine_boundaries=True)
    ad = json.loads(response["choices"][0]["message"]["content"])["ads"][0]

    assert (ad["start"], ad["end"]) == (100.0, 120.0)
    assert end_requests
    assert not closing_requests
    assert any(
        "kept speech begins: 'Editorial return.'" in option
        for option in end_requests[0]["questions"]["boundary_end_word"]["criteria"].values()
    )


def test_terminal_cta_still_respects_kept_category(
    jev_env, tmp_path,
):
    prompt = build_review_prompt(
        100.0, 120.0,
        [(90.0, 100.0, "Editorial before.")],
        [(100.0, 110.0, "Sponsor offer and self promotion."),
         (110.0, 120.0, "Visit example .com.")],
        [],
    ).replace(
        "\nTranscript (60s before, the candidate ad, 60s after;",
        "\nEffective category actions: sponsor=remove, self_promo=keep\n\n"
        "Transcript (60s before, the candidate ad, 60s after;",
    ) + (
        "Boundary word timing, use these timestamps for corrections:\n"
        "Start edge:\n[100.0s-101.0s] Sponsor\n"
        "End edge:\n[110.0s-111.0s] Visit\n"
        "[111.0s-112.0s] example\n[112.0s-120.0s] .com.\n"
    )
    select = _select_pair_fake(100.0, 120.0)
    kept_requests = []

    def fake(payload, **kwargs):
        result = select(payload, **kwargs)
        if "kept_category" in payload["questions"]:
            kept_requests.append(payload)
            result["answers"]["kept_category"] = {"noul": 0.99}
        return result

    with pytest.raises(ReviewInconclusiveError) as raised:
        _run(prompt, fake, tmp_path, refine_boundaries=True)

    assert kept_requests
    assert raised.value.reason == "original_range_not_confirmed"
    assert raised.value.fallback["reason"] == "programme_content_detected"


def test_start_unit_options_include_neighboring_utterances():
    segments = [
        {"start": 90.0, "end": 92.0, "text": "Back after the break."},
        {"start": 92.0, "end": 94.0, "text": "I have a storage problem."},
        {"start": 94.0, "end": 96.0, "text": "Acme solves it."},
    ]
    words = [
        {"start": 90.0, "end": 92.0, "text": "Back after the break."},
        {"start": 92.0, "end": 94.0, "text": "I have a storage problem."},
        {"start": 94.0, "end": 96.0, "text": "Acme solves it."},
    ]

    question, values = adapter._unit_start_question(
        segments, words, [90.0, 92.0, 94.0], 92.0,
    )
    setup_option = next(key for key, value in values.items() if value == 92.0)

    assert "preceding utterance: 'Back after the break.'" in question["criteria"][setup_option]
    assert "following utterance: 'Acme solves it.'" in question["criteria"][setup_option]


def test_large_end_unit_groups_every_word_end_before_exact_selection():
    words = [
        {"start": float(index), "end": float(index + 1), "text": f"word{index}"}
        for index in range(29)
    ]
    values = [float(index + 1) for index in range(29)]

    question, groups = adapter._end_word_group_question(words, values)

    flattened = [value for group in groups.values() for value in group]
    assert flattened == values
    assert all(1 <= len(group) <= 8 for group in groups.values())
    assert len(groups) == 4
    assert "Every eligible word end appears in one group" in question["instructions"]
    assert any("following speech" in text for text in question["criteria"].values())


@pytest.mark.parametrize(
    ("pair_choice", "expected_end"),
    [("following", 123.0), ("selected", 120.0), ("unknown", None)],
)
def test_adjacent_end_unit_comparison_recovers_closing_url(
    jev_env, tmp_path, pair_choice, expected_end,
):
    prompt = build_review_prompt(
        100.0, 120.0,
        [(90.0, 100.0, "Editorial discussion ends.")],
        [(100.0, 115.0, "Acme sponsor offer."),
         (115.0, 120.0, "Use Acme.")],
        [(120.0, 126.0, "I trust Acme .com. Back to the show."),
         (126.0, 130.0, "Editorial discussion resumes.")],
    ) + (
        "Boundary word timing, use these timestamps for corrections:\n"
        "Start edge:\n[100.0s-101.0s] Acme\n"
        "End edge:\n[119.0s-120.0s] Acme.\n"
        "[120.0s-121.0s] I\n[121.0s-121.5s] trust\n"
        "[121.5s-122.0s] Acme\n[122.0s-123.0s] .com.\n"
        "[123.0s-124.0s] Back\n[124.0s-124.5s] to\n"
        "[124.5s-125.0s] the\n[125.0s-126.0s] show.\n"
    )
    normal = make_text_fake()
    compared = []

    def fake(payload, **kwargs):
        result = normal(payload, **kwargs)
        questions = payload["questions"]
        if "boundary_end" in questions:
            _set_choice_answer(
                result, payload, "boundary_end", _end_unit_option(120.0),
            )
        if "end_unit_comparison" in questions:
            compared.append(questions["end_unit_comparison"]["criteria"])
            _set_choice_answer(result, payload, "end_unit_comparison", pair_choice)
            if pair_choice == "selected":
                result["answers"]["end_unit_comparison"]["probabilities"] = {
                    "selected": 0.09, "following": 0.90, "unknown": 0.01,
                }
        if "boundary_end_word" in questions:
            word_end = expected_end if expected_end in _WORD_END_OPTIONS.values() else 120.0
            _set_choice_answer(
                result, payload, "boundary_end_word",
                _boundary_option("boundary_end_word", word_end),
            )
        if "interval_comparison" in questions:
            _set_choice_answer(result, payload, "interval_comparison", "adjusted")
        return result

    if pair_choice == "unknown":
        with pytest.raises(ReviewInconclusiveError) as error:
            _run(prompt, fake, tmp_path, refine_boundaries=True)
        assert error.value.reason == "choice_inconclusive"
        assert error.value.stage == "choice_rank"
    else:
        response = _run(prompt, fake, tmp_path, refine_boundaries=True)
        ad = json.loads(response["choices"][0]["message"]["content"])["ads"][0]
        assert (ad["start"], ad["end"]) == (100.0, expected_end)

    assert len(compared) == 1
    assert "Acme" in compared[0]["following"]


@pytest.mark.parametrize(
    ("tail_kind", "expected_end"),
    [("consecutive", 129.74), ("protected", 101.39),
     ("mixed", 101.39), ("forced_unsafe", 101.39)],
)
def test_end_transition_handles_adjacent_promo_and_programme_separator(
    jev_env, tmp_path, tail_kind, expected_end,
):
    middle = (
        [(102.12, 111.0, "The episode story resumes with the officer's morning.")]
        if tail_kind in {"mixed", "forced_unsafe"} else []
    )
    later = (
        (112.0, 129.74, "Tonight on WXYZ, the new season continues. The auditions continue tonight.")
        if tail_kind in {"mixed", "forced_unsafe"} else
        (102.12, 129.74, "The episode story resumes with the officer's morning.")
        if tail_kind == "protected" else
        (102.12, 129.74, "Tonight on WXYZ, the new season continues. The auditions continue tonight.")
    )
    prompt = build_review_prompt(
        50.1, 131.82,
        [(45.0, 50.1, "Okay, let's get into today's story.")],
        [(68.2, 95.86, "Sponsor Acme makes it easy to keep your coverages in one place."),
         (96.25, 101.39, "Acme membership eligibility and product restrictions apply."),
         *middle, later],
        [(135.84, 160.7, "Early in the morning, an officer woke to his alarm.")],
    ) + (
        "Boundary word timing, use these timestamps for corrections:\n"
        "Start edge:\n[68.2s-68.5s] Sponsor\n"
        "End edge:\n[96.25s-101.39s] Acme membership eligibility and product restrictions apply.\n"
        + (
            "[102.12s-111.0s] The episode story resumes with the officer's morning.\n"
            "[112.0s-129.74s] Tonight on WXYZ, the new season continues. The auditions continue tonight.\n"
            if tail_kind in {"mixed", "forced_unsafe"} else
            "[102.12s-129.74s] The episode story resumes with the officer's morning.\n"
            if tail_kind == "protected" else
            "[102.12s-129.74s] Tonight on WXYZ, the new season continues. The auditions continue tonight.\n"
        )
    )
    if tail_kind == "forced_unsafe":
        prompt = prompt.replace(
            "\nTranscript (60s before, the candidate ad, 60s after;",
            "\nEffective category actions: sponsor=remove, self_promo=keep\n\n"
            "Transcript (60s before, the candidate ad, 60s after;",
        )
    normal = make_text_fake()
    transitions = []
    safety = []
    categories = []

    def fake(payload, **kwargs):
        fake_payload = {**payload, "state": {
            **payload["state"], "candidate": {**payload["state"].get("candidate", {}), "end": 101.39},
        }}
        result = normal(fake_payload, **kwargs)
        questions = payload["questions"]
        end = (
            129.74 if "boundary_end_word" in questions
            and tail_kind in {"consecutive", "forced_unsafe"}
            and 129.74 in _WORD_END_OPTIONS.values() else 101.39
        )
        _override_boundaries(result, payload, 68.2, end)
        if "end_transition" in questions:
            transitions.append(payload)
            selected = _end_unit_option(129.74)
            _set_choice_answer(result, payload, "end_transition", selected)
        if "end_run_category" in questions:
            categories.append(payload)
            _set_choice_answer(
                result, payload, "end_run_category",
                "mixed" if tail_kind == "mixed" else
                "protected" if tail_kind == "protected" else "removable",
            )
        if "sponsor_read" in questions or "whole_speech" in questions:
            safety.append(payload)
        if tail_kind == "forced_unsafe":
            if "kept_category" in questions:
                result["answers"]["kept_category"] = {"noul": 0.02}
            for name in questions:
                if name.startswith("same_show_access_"):
                    result["answers"][name] = {"noul": 0.02}
        if tail_kind == "forced_unsafe" and "whole_speech" in questions:
            speech = payload["state"]["candidate_interval_speech"]
            if "episode story resumes" in speech:
                result["answers"]["whole_speech"] = {"noul": 0.98}
        return result

    response = _run(prompt, fake, tmp_path, refine_boundaries=True)
    ad = json.loads(response["choices"][0]["message"]["content"])["ads"][0]

    assert (ad["start"], ad["end"]) == (68.2, expected_end)
    assert len(categories) == 1
    assert len(transitions) == (0 if tail_kind in {"protected", "mixed"} else 1)
    if transitions:
        assert "Tonight on WXYZ" in transitions[0]["state"]["end_context"]
        assert "Early in the morning" in transitions[0]["state"]["end_context"]
    if tail_kind == "forced_unsafe":
        assert sum("sponsor_read" in entry["questions"] for entry in safety) == 2
        assert sum("whole_speech" in entry["questions"] for entry in safety) == 2
        assert sum("kept_category" in entry["questions"] for entry in safety) == 2
        assert not any("interval_comparison" in entry["questions"] for entry in safety)


def test_oversized_end_group_choice_keeps_supported_original(
    jev_env, tmp_path, monkeypatch,
):
    monkeypatch.setattr(adapter, "_END_WORD_GROUP_THRESHOLD", 0)

    def oversized_groups(words, values):
        return {
            "type": "choice",
            "instructions": "Choose a group.",
            "criteria": {"unknown": "Unknown.", **{f"group_{index}": "Option." for index in range(255)}},
        }, {f"group_{index}": [values[0]] for index in range(255)}

    monkeypatch.setattr(adapter, "_end_word_group_question", oversized_groups)
    normal = make_text_fake()
    group_calls = 0

    def fake(payload, **kwargs):
        nonlocal group_calls
        group_calls += int("boundary_end_group" in payload["questions"])
        return normal(payload, **kwargs)

    response = _run(_meaningful_pair_prompt(), fake, tmp_path, refine_boundaries=True)
    ad = json.loads(response["choices"][0]["message"]["content"])["ads"][0]

    assert group_calls == 0
    assert (ad["start"], ad["end"]) == (100.0, 120.0)


def test_end_tail_programme_veto_targets_only_the_last_four_words():
    segments = [{"start": 100.0, "end": 110.0, "text": "Use code ACME then welcome back now"}]
    words = [
        {"start": float(index + 100), "end": float(index + 101), "text": text}
        for index, text in enumerate("Use code ACME then welcome back now".split())
    ]
    edges = {"start": words, "end": words}

    checks = _programme_checks(segments, edges, (100.0, 107.0), [])

    assert checks["end_tail"]["instructions"]["target_speech"] == "then welcome back now"
    assert checks["end_boundary_speech"]["instructions"]["target_speech"] == "now"
    assert "target function, not mere proximity" in adapter._PROGRAMME_CONTEXT_RULE
    assert "character scene, or role-play" in adapter._PROGRAMME_CONTEXT_RULE
    assert "opening word" in checks["end_boundary_speech"]["instructions"]["question"]


def test_outside_completeness_checks_near_and_far_speech_separately():
    before = [
        {"start": float(index), "end": float(index + 1), "text": f"before{index}"}
        for index in range(16)
    ]
    after = [
        {"start": float(index + 20), "end": float(index + 21), "text": f"after{index}"}
        for index in range(16)
    ]

    questions = adapter._outside_sponsor_questions(
        {"start": before, "end": after}, (16.0, 20.0), "inside sponsor speech",
    )

    assert set(questions) == {"start_near", "start_extended", "end_near", "end_extended"}
    assert questions["start_near"]["instructions"]["target_speech"] == " ".join(f"before{index}" for index in range(8, 16))
    assert questions["start_extended"]["instructions"]["target_speech"] == " ".join(f"before{index}" for index in range(16))
    assert questions["end_near"]["instructions"]["target_speech"] == " ".join(f"after{index}" for index in range(8))
    assert questions["end_extended"]["instructions"]["target_speech"] == " ".join(f"after{index}" for index in range(16))


def test_incomplete_edge_reranks_once_with_side_specific_context(jev_env, tmp_path):
    select = _select_pair_fake(106.0, 120.0)
    start_rank_payloads = []

    def fake(payload, **kwargs):
        result = select(payload, **kwargs)
        if "boundary_start" in payload["questions"]:
            start_rank_payloads.append(payload)
        if "start_near" in payload["questions"]:
            inside = payload["questions"]["start_near"]["instructions"]["inside_candidate_speech"]
            score = 0.9 if inside.startswith("Sponsor offer") else 0.02
            for name in payload["questions"]:
                result["answers"][name] = {"noul": score if name.startswith("start_") else 0.02}
        return result

    _run(
        _meaningful_pair_prompt(), fake, tmp_path,
        refine_boundaries=True, review_choice_enter=0.85,
    )

    assert len(start_rank_payloads) == 2
    assert set(start_rank_payloads[0]["state"]) == {"candidate", "start_context"}
    assert set(start_rank_payloads[1]["state"]) == {
        "candidate", "start_context", "nearest_outside_speech", "farther_outside_context",
    }
    base = start_rank_payloads[0]["questions"]["boundary_start"]["instructions"]
    rerank = start_rank_payloads[1]["questions"]["boundary_start"]["instructions"]
    assert rerank.startswith(base)


def test_rank_upstream_error_logs_only_safe_metadata(jev_env, tmp_path, caplog):
    normal = make_text_fake()
    secret = "sensitive-prompt-and-credential"

    def fake(payload, **kwargs):
        if "boundary_start" in payload["questions"]:
            request = httpx.Request("POST", "https://api.example/v1/systemone")
            response = httpx.Response(
                400, request=request,
                json={"error": {"type": secret, "code": secret, "message": secret}},
            )
            raise httpx.HTTPStatusError("upstream failure", request=request, response=response)
        return normal(payload, **kwargs)

    with caplog.at_level("WARNING", logger="app.services.openai_adapter"):
        with pytest.raises(httpx.HTTPStatusError):
            _run(_meaningful_pair_prompt(), fake, tmp_path, refine_boundaries=True)

    assert "stage=choice_rank_start upstream_status=400" in caplog.text
    assert "state_bytes=" in caplog.text
    assert "criteria_counts=" in caplog.text
    assert "upstream_type=None upstream_code=None" in caplog.text
    assert secret not in caplog.text


def test_ambiguous_intro_fragment_cannot_be_trimmed(jev_env, tmp_path):
    prompt = build_review_prompt(
        100.0, 120.0,
        [(94.0, 100.0, "Editorial before.")],
        [(100.0, 106.0, "This portion of the show is"),
         (106.0, 120.0, "brought to you by Acme sponsor.")],
        [(120.0, 126.0, "Editorial return.")],
    ) + (
        "Boundary word timing, use these timestamps for corrections:\n"
        "Start edge:\n[100.0s-101.0s] This\n[106.0s-107.0s] brought\n"
        "End edge:\n[119.0s-120.0s] sponsor.\n[120.0s-121.0s] Editorial\n"
    )
    base = _select_pair_fake(106.0, 120.0)

    def fake(payload, **kwargs):
        result = base(payload, **kwargs)
        if "interval_comparison" in payload["questions"]:
            assert payload["state"]["excluded_start_speech"] == "This portion of the show is"
            _set_choice_answer(result, payload, "interval_comparison", "original")
        return result

    response = _run(prompt, fake, tmp_path, refine_boundaries=True)
    ad = json.loads(response["choices"][0]["message"]["content"])["ads"][0]

    assert (ad["start"], ad["end"]) == (100.0, 120.0)


def test_ad_only_cut_can_omit_ad_intro_when_original_is_unsafe(jev_env, tmp_path):
    prompt = build_review_prompt(
        100.0, 120.0,
        [(94.0, 100.0, "Editorial before.")],
        [(100.0, 106.0, "This sponsor message begins with Acme."),
         (106.0, 120.0, "Use the Acme offer at acme.com.")],
        [(120.0, 126.0, "Editorial return.")],
    ) + (
        "Boundary word timing, use these timestamps for corrections:\n"
        "Start edge:\n[100.0s-101.0s] This\n[101.0s-102.0s] sponsor\n"
        "[102.0s-103.0s] message\n[103.0s-104.0s] begins\n"
        "[104.0s-105.0s] with\n[105.0s-106.0s] Acme.\n"
        "[106.0s-107.0s] Use\n"
        "End edge:\n[119.0s-120.0s] acme.com.\n"
    )
    select = _select_pair_fake(105.0, 120.0)

    def fake(payload, **kwargs):
        result = select(payload, **kwargs)
        if "interval_comparison" in payload["questions"]:
            assert "sponsor message begins" in payload["state"]["excluded_start_speech"]
            _set_choice_answer(result, payload, "interval_comparison", "adjusted", 0.9)
        if "sponsor_read" in payload["questions"]:
            result["answers"]["sponsor_read"] = {
                "noul": 0.4 if "This sponsor message begins" in payload["state"]["speech"] else 0.96
            }
        return result

    response = _run(prompt, fake, tmp_path, refine_boundaries=True, review_choice_enter=0.85)
    ad = json.loads(response["choices"][0]["message"]["content"])["ads"][0]
    assert (ad["start"], ad["end"]) == (105.0, 120.0)


def test_outward_sponsor_tail_requires_edge_confirmation(jev_env, tmp_path):
    prompt = build_review_prompt(
        100.0, 118.0,
        [(94.0, 100.0, "Editorial before.")],
        [(100.0, 118.0, "This episode is sponsored by Acme."),
         (118.0, 120.0, "Sponsor website .com")],
        [(120.0, 130.0, "Editorial return.")],
    ) + (
        "Boundary word timing, use these timestamps for corrections:\n"
        "Start edge:\n[100.0s-101.0s] This\n[101.0s-102.0s] episode\n"
        "End edge:\n[117.0s-118.0s] Acme.\n[118.0s-119.0s] Sponsor\n"
        "[119.0s-120.0s] .com\n[120.0s-121.0s] Editorial\n"
    )
    base = _select_pair_fake(100.0, 120.0)

    def fake(payload, **kwargs):
        result = base(payload, **kwargs)
        if "interval_comparison" in payload["questions"]:
            assert payload["state"]["added_end_speech"] == "Sponsor website .com"
        return result

    response = _run(prompt, fake, tmp_path, refine_boundaries=True)
    ad = json.loads(response["choices"][0]["message"]["content"])["ads"][0]

    assert (ad["start"], ad["end"]) == (100.0, 120.0)


def test_missing_local_end_words_abstains_for_both_intervals(jev_env, tmp_path):
    prompt = build_review_prompt(
        100.0, 118.0,
        [(94.0, 100.0, "Editorial before.")],
        [(100.0, 118.0, "This episode is sponsored by Acme."),
         (118.0, 120.0, "Sponsor website .com")],
        [(120.0, 130.0, "Editorial return.")],
    ) + (
        "Boundary word timing, use these timestamps for corrections:\n"
        "Start edge:\n[100.0s-101.0s] This\n"
        "End edge:\n[120.0s-120.0s] edge\n"
    )

    with pytest.raises(ReviewInconclusiveError) as raised:
        _run(prompt, _select_pair_fake(100.0, 120.0), tmp_path, refine_boundaries=True)

    assert raised.value.reason == "insufficient_boundary_text"
    assert raised.value.stage == "boundary_coverage"
    assert raised.value.proposal["reason"] == "insufficient_boundary_text"
    assert raised.value.proposal["stage"] == "boundary_coverage"
    assert raised.value.fallback["reason"] == "insufficient_boundary_text"


def test_unknown_rank_cannot_confirm_without_original_comparison(jev_env, tmp_path):
    normal = make_text_fake()

    def fake(payload, **kwargs):
        result = normal(payload, **kwargs)
        if "boundary_start" in payload["questions"]:
            criteria = payload["questions"]["boundary_start"]["criteria"]
            result["answers"]["boundary_start"] = {
                "choice": "unknown",
                "confidence": 0.98,
                "probabilities": {key: 0.99 if key == "unknown" else 0.01 / (len(criteria) - 1) for key in criteria},
            }
        if "interval_comparison" in payload["questions"]:
            _set_choice_answer(result, payload, "interval_comparison", "neither")
        if "sponsor_read" in payload["questions"]:
            result["answers"]["sponsor_read"] = {"noul": 0.4}
        return result

    with pytest.raises(ReviewInconclusiveError, match="could not confirm a safe advertising interval"):
        _run(_meaningful_pair_prompt(), fake, tmp_path, refine_boundaries=True)


@pytest.mark.parametrize(
    "proposed_safety, original_safety, preference, preference_score, expected",
    [
        (0.96, 0.96, "neither", 0.54, None),
        (0.96, 0.3, "neither", 0.9, None),
        (0.3, 0.96, "neither", 0.9, None),
        (0.96, 0.3, "original", 0.9, (106.0, 120.0)),
        (0.3, 0.96, "adjusted", 0.9, (100.0, 120.0)),
        (0.96, 0.96, "adjusted", 0.54, (106.0, 120.0)),
        (0.3, 0.3, "adjusted", 0.99, None),
    ],
)
def test_interval_safety_is_independent_of_relative_preference(
    jev_env, tmp_path, proposed_safety, original_safety, preference, preference_score, expected
):
    select = _select_pair_fake(106.0, 120.0)

    def fake(payload, **kwargs):
        result = select(payload, **kwargs)
        if "interval_comparison" in payload["questions"]:
            _set_choice_answer(result, payload, "interval_comparison", preference, preference_score)
        if "sponsor_read" in payload["questions"]:
            score = original_safety if "Editorial transition" in payload["state"]["speech"] else proposed_safety
            result["answers"]["sponsor_read"] = {"noul": score}
        return result

    if expected is None:
        with pytest.raises(ReviewInconclusiveError) as raised:
            _run(_meaningful_pair_prompt(), fake, tmp_path, refine_boundaries=True, review_choice_enter=0.85)
        if preference == "neither":
            assert raised.value.reason == "neither_complete"
            assert raised.value.stage == "interval_comparison"
            assert raised.value.score == preference_score
        else:
            assert raised.value.reason == "ad_content_unconfirmed"
            assert raised.value.threshold == 0.85
            assert raised.value.proposal["reason"] == "proposed_range_not_confirmed"
            assert raised.value.proposal["score"] == proposed_safety
            assert raised.value.proposal["threshold"] == 0.85
            assert raised.value.fallback["reason"] == "original_range_not_confirmed"
            assert raised.value.fallback["score"] == original_safety
            assert raised.value.fallback["threshold"] == 0.85
    else:
        response = _run(_meaningful_pair_prompt(), fake, tmp_path, refine_boundaries=True, review_choice_enter=0.85)
        ad = json.loads(response["choices"][0]["message"]["content"])["ads"][0]
        assert (ad["start"], ad["end"]) == expected


@pytest.mark.parametrize(
    ("selected_start", "outer_label", "outer_text"),
    [(106.0, "original", "Editorial transition."),
     (90.0, "proposed", "Editorial before.")],
)
@pytest.mark.parametrize("veto_signal", ["programme", "policy"])
def test_nested_protected_veto_blocks_containing_interval(
    jev_env, tmp_path, selected_start, outer_label, outer_text, veto_signal,
):
    select = _select_pair_fake(selected_start, 120.0)
    observed = {}
    prompt = _meaningful_pair_prompt()
    if veto_signal == "policy":
        prompt = prompt.replace(
            "\nTranscript (60s before, the candidate ad, 60s after;",
            "\nEffective category actions: sponsor=remove, self_promo=keep\n\n"
            "Transcript (60s before, the candidate ad, 60s after;",
        )

    def fake(payload, **kwargs):
        result = select(payload, **kwargs)
        questions = payload["questions"]
        if "interval_comparison" in questions:
            _set_choice_answer(
                result, payload, "interval_comparison",
                "original" if outer_label == "original" else "adjusted",
            )
        if "sponsor_read" in questions:
            result["answers"]["sponsor_read"] = {"noul": 0.98}
        if "kept_category" in questions:
            result["answers"]["kept_category"] = {"noul": 0.02}
        if "whole_speech" in questions and veto_signal == "programme":
            speech = payload["state"]["candidate_interval_speech"]
            score = 0.84 if outer_text in speech else 0.86
            observed["outer" if outer_text in speech else "inner"] = score
            result["answers"]["whole_speech"] = {"noul": score}
        if veto_signal == "policy" and any(name.startswith("same_show_access_") for name in questions):
            speech = payload["state"]["candidate_interval_speech"]
            score = 0.84 if outer_text in speech else 0.86
            observed["outer" if outer_text in speech else "inner"] = score
            for name in questions:
                result["answers"][name] = {"noul": score}
        return result

    with pytest.raises(ReviewInconclusiveError) as raised:
        _run(prompt, fake, tmp_path, refine_boundaries=True)

    assert observed == {"inner": 0.86, "outer": 0.84}
    reason = "programme_content_detected" if veto_signal == "programme" else "category_policy_unconfirmed"
    assert raised.value.proposal["reason"] == reason
    assert raised.value.fallback["reason"] == reason
    assert raised.value.proposal["score"] == pytest.approx(0.86)
    assert raised.value.fallback["score"] == pytest.approx(0.86)


def test_partial_overlap_does_not_transfer_programme_veto(jev_env, tmp_path):
    select = _select_pair_fake(106.0, 130.0)
    observed = {}

    def fake(payload, **kwargs):
        result = select(payload, **kwargs)
        questions = payload["questions"]
        if "interval_comparison" in questions:
            _set_choice_answer(result, payload, "interval_comparison", "adjusted")
        if "sponsor_read" in questions:
            result["answers"]["sponsor_read"] = {"noul": 0.98}
        if "whole_speech" in questions:
            original = "Editorial transition." in payload["state"]["candidate_interval_speech"]
            score = 0.86 if original else 0.84
            observed["original" if original else "proposed"] = score
            result["answers"]["whole_speech"] = {"noul": score}
        return result

    prompt = _meaningful_pair_prompt().replace(
        "[120.0s-130.0s] Editorial return.",
        "[120.0s-130.0s] Editorial return.\n[130.0s-140.0s] Editorial after.",
    )
    response = _run(prompt, fake, tmp_path, refine_boundaries=True)
    ad = json.loads(response["choices"][0]["message"]["content"])["ads"][0]

    assert observed == {"original": 0.86, "proposed": 0.84}
    assert (ad["start"], ad["end"]) == (106.0, 130.0)


def test_outside_continuation_does_not_veto_containing_interval(jev_env, tmp_path):
    select = _select_pair_fake(106.0, 120.0)
    outside_scores = {}

    def fake(payload, **kwargs):
        result = select(payload, **kwargs)
        questions = payload["questions"]
        if "interval_comparison" in questions:
            _set_choice_answer(result, payload, "interval_comparison", "adjusted")
        for name, question in questions.items():
            if name.endswith(("_near", "_extended")):
                inside = question["instructions"]["inside_candidate_speech"]
                score = 0.98 if name.startswith("start_") and inside.startswith("Sponsor offer") else 0.02
                outside_scores[(inside, name)] = score
                result["answers"][name] = {"noul": score}
        return result

    response = _run(_meaningful_pair_prompt(), fake, tmp_path, refine_boundaries=True)
    ad = json.loads(response["choices"][0]["message"]["content"])["ads"][0]

    assert any(score == 0.98 for score in outside_scores.values())
    assert (ad["start"], ad["end"]) == (100.0, 120.0)


@pytest.mark.parametrize("proposed_programme_score, approved", [(0.1, True), (0.85, False), (0.9, False)])
def test_promotion_requires_local_programme_clearance(
    jev_env, tmp_path, proposed_programme_score, approved
):
    select = _select_pair_fake(106.0, 120.0)
    stages = []

    def fake(payload, **kwargs):
        result = select(payload, **kwargs)
        questions = payload["questions"]
        if "interval_comparison" in questions:
            _set_choice_answer(result, payload, "interval_comparison", "adjusted")
            assert set(questions) == {"interval_comparison"}
        if "sponsor_read" in questions:
            stages.append("promotion")
            assert set(payload["state"]) == {"guidance", "speech"}
            result["answers"]["sponsor_read"] = {"noul": 0.98}
        if "start_speech" in questions:
            stages.append("programme")
            assert set(payload["state"]) == {"guidance", "candidate_interval_speech", "evaluation_rule"}
            assert "Sponsor offer ends" in payload["state"]["candidate_interval_speech"]
            assert "Judge only target_speech" in payload["state"]["evaluation_rule"]
            target = questions["start_speech"]["instructions"]["target_speech"]
            result["answers"]["start_speech"] = {
                "noul": 0.9 if "Editorial" in target else proposed_programme_score
            }
        return result

    if approved:
        response = _run(_meaningful_pair_prompt(), fake, tmp_path, refine_boundaries=True)
        ad = json.loads(response["choices"][0]["message"]["content"])["ads"][0]
        assert (ad["start"], ad["end"]) == (106.0, 120.0)
    else:
        with pytest.raises(ReviewInconclusiveError) as raised:
            _run(_meaningful_pair_prompt(), fake, tmp_path, refine_boundaries=True)
        assert raised.value.reason == "original_range_not_confirmed"
        assert raised.value.threshold == 0.85
        assert raised.value.proposal["reason"] == "programme_content_detected"
        assert raised.value.proposal["score"] == proposed_programme_score
        assert raised.value.proposal["threshold"] == 0.85
        assert raised.value.fallback["reason"] == "programme_content_detected"
        assert raised.value.fallback["score"] == 0.9
    assert stages == ["promotion", "programme", "promotion", "programme"]


def test_comparison_neither_rejects_safe_alternatives(jev_env, tmp_path):
    select = _select_pair_fake(106.0, 120.0)
    states = {}

    def fake(payload, **kwargs):
        result = select(payload, **kwargs)
        for key in ("evidence", "boundary_start", "interval_comparison"):
            if key in payload["questions"]:
                states[key] = payload["state"]
        if "interval_comparison" in payload["questions"]:
            _set_choice_answer(result, payload, "interval_comparison", "neither")
        if "sponsor_read" in payload["questions"]:
            result["answers"]["sponsor_read"] = {"noul": 0.4}
        return result

    with pytest.raises(ReviewInconclusiveError, match="rejected every eligible advertising interval") as raised:
        _run(_meaningful_pair_prompt(), fake, tmp_path, refine_boundaries=True)

    assert raised.value.reason == "neither_complete"
    assert raised.value.stage == "interval_comparison"
    assert raised.value.score == pytest.approx(0.99)
    assert raised.value.proposal is None
    assert raised.value.fallback is None
    assert states["evidence"]["candidate"] == {"start": 100.0, "end": 120.0}
    assert set(states["boundary_start"]) == {"candidate", "start_context"}
    assert "assessment_range" not in states["evidence"]
    assert "assessment_range" not in states["boundary_start"]
    assert states["interval_comparison"]["proposed_speech"] == "Sponsor offer ends.\nSponsor continues."
    assert states["interval_comparison"]["original_speech"] == "Editorial transition.\nSponsor offer ends.\nSponsor continues."
    assert "timeline" not in states["interval_comparison"]
    assert "transcript" not in states["interval_comparison"]


def test_refinement_logs_effective_context_and_precise_thresholds(jev_env, tmp_path, caplog):
    import logging

    prompt = build_review_prompt(
        100.0,
        120.0,
        [(94.0, 100.0, "context before")],
        [(100.0, 120.0, "This episode is sponsored by BetterHelp")],
        [(120.0, 126.0, "context after")],
    )
    with caplog.at_level(logging.INFO, logger="systemone.adapter"):
        _run(
            _with_word_edges(prompt, start=(100.0, 101.0, "This"), end=(119.0, 120.0, "BetterHelp")),
            make_text_fake(),
            tmp_path,
            refine_boundaries=True,
            review_request_id="request-123",
            enter=0.9496,
        )

    messages = [record.getMessage() for record in caplog.records]
    assert any("request_id=request-123 stage=context" in message and "start_word_count=1" in message for message in messages)
    assert any("stage=evidence score=0.98 threshold=0.9496" in message for message in messages)
    assert any("stage=interval_comparison" in message and "threshold=0.9496" in message for message in messages)
    assert all("BetterHelp" not in message for message in messages)


def test_review_upstream_failure_is_unavailable(jev_env, tmp_path):
    prompt = build_review_prompt(
        100.0, 120.0,
        [(94.0, 100.0, "context before")],
        [(100.0, 110.0, "This episode is sponsored by BetterHelp"),
         (110.0, 120.0, "Use promo code SHOW at betterhelp.com")],
        [(120.0, 126.0, "context after")],
    )
    with pytest.raises(ReviewUnavailableError, match="System One review request failed"):
        _run(prompt, make_text_fake(raises=True), tmp_path)
