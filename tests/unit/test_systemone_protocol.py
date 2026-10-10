import math
import os
import subprocess
import sys

import pytest

from systemone.protocol import (
    JevReviewValidationError,
    RequestLimitError,
    _review_answers,
    build_payload,
    jev_ask,
    jev_review_questions,
    parse_response,
)


ANSWER_BODY = {
    "answers": {"s1": {"noul": 0.98}, "s2": {"noul": 0.02}},
    "usage": {"input_tokens": 1000, "output_tokens": 5, "cost": 0.25},
}
REVIEW_QUESTIONS = {
    "evidence": {"type": "noul"},
    "choice": {"type": "choice", "criteria": {"unknown": "none", "word": "word"}},
}
REVIEW_BODY = {
    "answers": {
        "evidence": {"noul": 0.96},
        "choice": {"choice": "word", "confidence": 0.94,
                    "probabilities": {"unknown": 0.1, "word": 0.9}},
    },
    "usage": {"input_tokens": 3, "output_tokens": 2},
}


def test_payload_preserves_source_question_ids_and_full_shared_state():
    payload = build_payload(
        [{"sid": 8, "text": " first "}, {"sid": 9, "text": "second"}],
        model="jev-latest",
        uid="window-1",
        caller_context="episode cues",
    )
    assert payload["model"] == "jev-latest"
    assert payload["state"]["transcript"] == "L0008| first\nL0009| second"
    assert list(payload["questions"]) == ["s8", "s9"]
    assert "L0008" in payload["questions"]["s8"]["instructions"]
    assert payload["state"]["caller_context"] == "episode cues"
    assert payload["state"]["uid"] == "window-1"


def test_detection_validation_requires_every_answer_and_finite_probability():
    assert parse_response(ANSWER_BODY, {"s1", "s2"})["probabilities"] == {"s1": 0.98, "s2": 0.02}
    for value in (True, -0.01, 1.01, math.nan, math.inf, "0.5"):
        body = {"answers": {"s1": {"noul": value}}, "usage": {}}
        with pytest.raises(JevReviewValidationError, match="evidence_probability"):
            parse_response(body, {"s1"})
    with pytest.raises(JevReviewValidationError, match="answers_keys"):
        parse_response({"answers": {"s1": {"noul": 0.9}}}, {"s1", "s2"})


def test_missing_usage_stays_unknown_and_reported_cost_is_preserved():
    missing = parse_response({"answers": {"s1": {"noul": 0.9}}}, {"s1"})
    assert missing["input_tokens"] is None
    assert missing["output_tokens"] is None
    reported = parse_response(
        {"answers": {"s1": {"noul": 0.9}}, "usage": {"input_tokens": 7, "cost": 0.125}},
        {"s1"},
    )
    assert reported["input_tokens"] == 7
    assert reported["output_tokens"] is None
    assert reported["cost"] == 0.125


def test_review_validation_checks_complete_choice_distribution():
    parsed = _review_answers(REVIEW_BODY, REVIEW_QUESTIONS)
    assert parsed["answers"]["choice"]["choice"] == "word"
    for probabilities in (
        {"unknown": 0.2, "word": 0.2},
        {"unknown": 0.0, "word": math.inf},
        {"unknown": 0.0},
    ):
        body = {**REVIEW_BODY, "answers": {**REVIEW_BODY["answers"], "choice": {
            **REVIEW_BODY["answers"]["choice"], "probabilities": probabilities}}}
        with pytest.raises(JevReviewValidationError):
            _review_answers(body, REVIEW_QUESTIONS)


def test_detection_splits_questions_in_order_with_identical_state_and_validates_each_chunk():
    segments = [{"sid": sid, "text": f"line {sid}"} for sid in range(1, 6)]
    seen = []

    def fetcher(payload, **kwargs):
        seen.append((payload, kwargs))
        return {
            "answers": {key: {"noul": 0.98 if key in {"s2", "s3"} else 0.02}
                        for key in payload["questions"]},
            "usage": {"input_tokens": 10, "output_tokens": 2},
        }

    result = jev_ask(
        segments, model="jev-latest", fetcher=fetcher, max_questions=2,
        window_label="window-a", deadline_at=123.0,
    )
    assert [list(payload["questions"]) for payload, _ in seen] == [["s1", "s2"], ["s3", "s4"], ["s5"]]
    assert all(payload["state"] == seen[0][0]["state"] for payload, _ in seen)
    assert all(kwargs["request_kind"] == "detection" for _, kwargs in seen)
    assert all(kwargs["deadline_at"] == 123.0 for _, kwargs in seen)
    assert all(callable(kwargs["validate"]) for _, kwargs in seen)
    assert result["usage"] == {"input_tokens": 30, "output_tokens": 6, "cost": None}
    assert result["spans"] == [{"start_id": 2, "end_id": 3, "confidence": 0.98}]


def test_review_splits_questions_without_splitting_choice_options():
    questions = {
        "first": {"type": "noul"},
        "second": {"type": "noul"},
        "third": {"type": "choice", "criteria": {"no": "no", "yes": "yes"}},
    }
    seen = []

    def fetcher(payload, **kwargs):
        seen.append(payload)
        answers = {}
        for key, question in payload["questions"].items():
            answers[key] = ({"noul": 0.8} if question["type"] == "noul" else {
                "choice": "yes", "confidence": 0.9, "probabilities": {"no": 0.1, "yes": 0.9}})
        return {"answers": answers}

    result = jev_review_questions(
        state={"transcript": "unchanged"}, questions=questions, model="jev-latest",
        fetcher=fetcher, max_questions=2,
    )
    assert [list(payload["questions"]) for payload in seen] == [["first", "second"], ["third"]]
    assert all(payload["state"] == {"transcript": "unchanged"} for payload in seen)
    assert result["answers"]["third"]["choice"] == "yes"
    assert result["usage"] == {"input_tokens": None, "output_tokens": None, "cost": None}


def test_state_and_one_question_over_byte_limit_fails_before_dispatch():
    called = False

    def fetcher(*_args, **_kwargs):
        nonlocal called
        called = True
        raise AssertionError("oversized request was dispatched")

    with pytest.raises(RequestLimitError, match="state and one question"):
        jev_ask([{"sid": 1, "text": "x" * 1000}], model="jev-latest", fetcher=fetcher,
                max_request_bytes=300)
    assert called is False


def test_truncated_reply_is_terminal_coverage_failure():
    def fetcher(payload, **_kwargs):
        return {"answers": {key: {"noul": 0.9} for key in payload["questions"]}, "truncated": True}

    with pytest.raises(JevReviewValidationError, match="truncated_response"):
        jev_ask([{"sid": 1, "text": "line"}], model="jev-latest", fetcher=fetcher)


@pytest.mark.parametrize(
    "order",
    [
        "import systemone.adapter; import ad_detector; import llm_client",
        "import llm_client; import ad_detector; import systemone.adapter",
    ],
)
def test_cold_import_orders_do_not_create_cycles_or_require_runtime_setup(order):
    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join(filter(None, ["src", env.get("PYTHONPATH", "")]))
    subprocess.run([sys.executable, "-c", order], check=True, env=env, timeout=20)
