"""Detection parity tests derived from MinusPodJev test_openai.py."""

import json
from typing import Any

import pytest

from tests.app_bootstrap import bootstrap
bootstrap("systemone_adapter_detection_")

from ad_detector.prompts import format_window_prompt
from community_export import brand_match_candidates
from config import SEGMENT_CATEGORIES
from sponsor_service import SponsorService
from systemone.adapter import SystemOneSettings, parse_transcript, run_chat_completion


TS_LINES = [
    "[0.0s - 6.0s] Welcome back to the show.",
    "[6.0s - 12.0s] Today we talk about hiking trips.",
    "[12.0s - 18.0s] It was a great trip in the mountains.",
    "[18.0s - 24.0s] This episode is sponsored by BetterHelp.",
    "[24.0s - 30.0s] BetterHelp offers online therapy at betterhelp.com slash show.",
    "[30.0s - 36.0s] Use code SHOW for ten percent off your first month today.",
    "[36.0s - 42.0s] Anyway, back to the trip we were on.",
    "[42.0s - 48.0s] We hiked for hours and it was tiring.",
]


def _fake(ad_sids, *, category_index=0):
    ad_sids = set(ad_sids)

    def fetcher(payload: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
        answers = {}
        for key, question in payload["questions"].items():
            if key.startswith("s"):
                answers[key] = {"noul": 0.98 if int(key[1:]) in ad_sids else 0.02}
            elif question.get("type") == "choice":
                options = list(question["criteria"])
                choice = options[category_index]
                answers[key] = {
                    "choice": choice,
                    "confidence": 0.97,
                    "probabilities": {option: (0.97 if option == choice else 0.03 / (len(options) - 1))
                                      for option in options},
                }
        return {"answers": answers, "usage": {"input_tokens": 1000, "output_tokens": 5}}

    return fetcher


def _sponsor_service(db, name=None):
    if name and db.get_known_sponsor_by_name(name) is None:
        db.create_known_sponsor(name=name)
    return SponsorService(db)


def _sponsor_lookup(service):
    return lambda: [{"name": row["name"], "candidates": brand_match_candidates(row)}
                    for row in service.get_sponsors()]


@pytest.mark.parametrize("addressing_mode", ["timestamps", "segment_ids"])
@pytest.mark.parametrize("category", SEGMENT_CATEGORIES)
def test_current_window_prompt_detection_and_category_parity(temp_db, addressing_mode, category):
    if temp_db.get_known_sponsor_by_name("BetterHelp") is None:
        temp_db.create_known_sponsor(name="BetterHelp")
    service = SponsorService(temp_db)
    if addressing_mode == "timestamps":
        lines = TS_LINES
        detected = {3, 4, 5}
    else:
        lines = [f"[{index + 10}] {line.split('] ', 1)[1]}" for index, line in enumerate(TS_LINES)]
        detected = {13, 14, 15}
    prompt = format_window_prompt(
        "Example Podcast", "Episode", "", lines, 0, 1, 0.0, 600.0,
        addressing_mode=addressing_mode,
    )
    parsed, mode = parse_transcript(prompt)
    assert mode == addressing_mode
    assert len(parsed) == len(TS_LINES)

    category_index = list(SEGMENT_CATEGORIES).index(category)
    settings = SystemOneSettings(
        model="jev-latest", category_pass=True, category_context=2,
    )
    category_payloads = []

    def fetcher(payload, **kwargs):
        if any(q.get("type") == "choice" for q in payload["questions"].values()):
            category_payloads.append(payload)
        return _fake(detected, category_index=category_index)(payload, **kwargs)

    result = run_chat_completion(
        messages=[{"role": "system", "content": "Keep the episode verification policy."},
                  {"role": "user", "content": prompt}],
        request_model="jev-latest", settings=settings, fetcher=fetcher,
        sponsor_lookup=_sponsor_lookup(service),
    )
    ad = json.loads(result["choices"][0]["message"]["content"])["ads"][0]
    assert ad["category"] == category
    assert ad["confidence"] == 0.98
    assert ad["sponsor_name"] == "BetterHelp"
    assert len(category_payloads) == 1
    state = category_payloads[0]["state"]
    assert "Keep the episode verification policy." in state["guidance"]
    if addressing_mode == "timestamps":
        assert state["focus"] == "L0003-L0005"
        assert "L0001| Today we talk about hiking trips." in state["transcript"]
        assert "L0006| Anyway, back to the trip we were on." in state["transcript"]
    else:
        assert "L0013| This episode is sponsored by BetterHelp." in state["transcript"]


def test_nonempty_unrecognized_prompt_fails_closed():
    with pytest.raises(ValueError, match="could not parse"):
        run_chat_completion(
            messages=[{"role": "user", "content": "Transcript with unrecognized content"}],
            request_model="jev-latest", settings=SystemOneSettings(model="jev-latest"),
            fetcher=_fake(set()), sponsor_lookup=lambda: (),
        )


def test_category_pass_off_uses_default_category_and_one_dispatch(temp_db):
    prompt = format_window_prompt("Example Podcast", "Episode", "", TS_LINES, 0, 1, 0.0, 600.0)
    result = run_chat_completion(
        messages=[{"role": "user", "content": prompt}], request_model="jev-latest",
        settings=SystemOneSettings(model="jev-latest", category_pass=False),
        fetcher=_fake({3, 4, 5}), sponsor_lookup=_sponsor_lookup(_sponsor_service(temp_db, "BetterHelp")),
    )
    ad = json.loads(result["choices"][0]["message"]["content"])["ads"][0]
    assert ad["category"] == "sponsor"
    assert result["usage"]["prompt_tokens"] == 1000


def test_large_custom_system_policy_is_forwarded_without_truncation(temp_db):
    suffix = "KEEP_THIS_TAIL_RULE"
    policy = f"{'p' * 200_001}\n{suffix}"
    calls = []

    def fetcher(payload, **kwargs):
        calls.append(payload)
        return _fake({0})(payload, **kwargs)

    run_chat_completion(
        messages=[{"role": "system", "content": policy},
                  {"role": "user", "content": "[0.0s - 1.0s] sponsored by BetterHelp"}],
        request_model="jev-latest",
        settings=SystemOneSettings(model="jev-latest", category_pass=False),
        fetcher=fetcher, sponsor_lookup=_sponsor_lookup(_sponsor_service(temp_db, "BetterHelp")),
    )
    assert policy in calls[0]["state"]["guidance"]
    assert suffix in calls[0]["state"]["guidance"]


def test_unmatched_sponsor_stays_unlabeled_with_grounded_reason(temp_db):
    prompt = format_window_prompt(
        "Example Podcast", "Episode", "",
        ["[0.0s - 10.0s] Sponsored by Unknown Brand, visit example.com today."],
        0, 1, 0.0, 600.0,
    )
    result = run_chat_completion(
        messages=[{"role": "user", "content": prompt}], request_model="jev-latest",
        settings=SystemOneSettings(model="jev-latest", category_pass=False),
        fetcher=_fake({0}), sponsor_lookup=_sponsor_lookup(SponsorService(temp_db)),
    )
    ad = json.loads(result["choices"][0]["message"]["content"])["ads"][0]
    assert "sponsor_name" not in ad
    assert ad["reason"].startswith("Based on transcript:")


def test_empty_transcript_prompt_keeps_existing_empty_ads_response():
    result = run_chat_completion(
        messages=[{"role": "user", "content": ""}],
        request_model="jev-latest", settings=SystemOneSettings(model="jev-latest"),
        fetcher=_fake(set()), sponsor_lookup=lambda: (),
    )
    assert json.loads(result["choices"][0]["message"]["content"]) == {"ads": []}
    assert result["usage"] == {"prompt_tokens": None, "completion_tokens": None, "total_tokens": None}
