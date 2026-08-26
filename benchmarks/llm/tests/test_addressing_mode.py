"""Segment-ID addressing mode: prompt build, sid stamping, response
parse/resolve, id-contract-miss fallback counting, and report isolation.

MinusPod 2.91.0 shipped 'segment_ids' as an alternative to the default
'timestamps' addressing mode (transcript lines numbered [id] instead of
timestamped, model reports start_id/end_id instead of seconds). These tests
pin the harness side of that: no benchmark calls are made here.
"""
from __future__ import annotations

import pytest

from benchmark import corpus, parsing, report as report_mod, runner
from benchmark.corpus import CorpusError, Window
from benchmark.storage import append_jsonl


SEGMENTS = [
    {"start": 0.0, "end": 5.0, "text": "This episode is brought to you by BetterHelp"},
    {"start": 5.0, "end": 30.0, "text": "BetterHelp is online therapy that fits"},
    {"start": 30.0, "end": 60.0, "text": "Now back to the show"},
    {"start": 60.0, "end": 100.0, "text": "Welcome back everyone"},
]


CALL_TEMPLATE = {
    "schema_version": 2,
    "model": "m1",
    "provider_config": "openrouter",
    "underlying_provider": "OpenRouter",
    "episode_id": "ep-test-001",
    "trial": 0,
    "window_index": 0,
    "temperature": 0.0,
    "prompt_hash": "sha256:abc",
    "response_time_ms": 1500,
    "input_tokens": 1000,
    "output_tokens": 100,
    "total_cost_usd_at_runtime": 0.005,
    "json_format_used": "native",
    "extraction_method": "json_array_direct",
    "compliance_score": 1.0,
    "schema_violations": {"missing_required": 0, "wrong_type": 0, "extra_keys": 0, "out_of_range": 0, "extra_key_names": []},
    "windows_stale": False,
    "error": None,
}


def _load(tmp_path, write_corpus_episode):
    ep_dir = write_corpus_episode(tmp_path, segments=SEGMENTS)
    return corpus.load_episode(ep_dir)


# --- sid stamping consistency with stored window boundaries -----------------

def test_stamp_id_windows_matches_stored_boundaries(tmp_path, write_corpus_episode):
    ep = _load(tmp_path, write_corpus_episode)
    id_windows = corpus.stamp_id_windows(ep)
    assert len(id_windows) == len(ep.windows) == 1
    assert [seg["sid"] for seg in id_windows[0]] == [0, 1, 2, 3]
    # sid is stamped onto episode.segments itself (global index), matching
    # production's convention of stamping before create_windows.
    assert [seg["sid"] for seg in ep.segments] == [0, 1, 2, 3]


def test_stamp_id_windows_rejects_boundaries_stale_vs_windows_json(tmp_path, write_corpus_episode):
    """A stored windows.json that no longer matches what create_windows
    produces (e.g. window_size/overlap tunables drifted since capture) must
    fail loudly and point at regenerate-windows, not silently mis-stamp ids."""
    ep = _load(tmp_path, write_corpus_episode)
    ep.windows[0] = Window(index=0, start=0.0, end=50.0, transcript_lines=ep.windows[0].transcript_lines)
    with pytest.raises(CorpusError, match="regenerate-windows"):
        corpus.stamp_id_windows(ep)


# --- ID prompt build ---------------------------------------------------------

def test_id_mode_transcript_lines_are_sid_bracketed(tmp_path, write_corpus_episode):
    ep = _load(tmp_path, write_corpus_episode)
    id_segments = corpus.stamp_id_windows(ep)[0]
    prompt = runner._build_user_prompt(
        ep, ep.windows[0], total_windows=1,
        addressing_mode="segment_ids", id_segments=id_segments,
    )
    assert "[0] This episode is brought to you by BetterHelp" in prompt
    assert "[1] BetterHelp is online therapy that fits" in prompt
    assert "[3] Welcome back everyone" in prompt
    # No timestamp brackets leak into id-mode transcript lines.
    assert "0.0s" not in prompt


def test_id_mode_system_section_and_hash_differ_from_timestamps(tmp_path, write_corpus_episode, minimal_cfg):
    ep = _load(tmp_path, write_corpus_episode)
    base_system_prompt = "SYSTEM"
    id_system_prompt = base_system_prompt + parsing.SEGMENT_ID_SYSTEM_SECTION
    assert "ADDRESSING MODE: SEGMENT IDS" in id_system_prompt

    ts_hashes = runner.precompute_prompt_hashes(minimal_cfg, [ep], system_prompt=base_system_prompt)
    id_hashes = runner.precompute_prompt_hashes(
        minimal_cfg, [ep], system_prompt=id_system_prompt, addressing_mode="segment_ids",
    )
    assert ts_hashes[("m1", ep.ep_id, 0, 0)] != id_hashes[("m1", ep.ep_id, 0, 0)]


def test_timestamps_mode_prompt_and_hash_unchanged_by_default(tmp_path, write_corpus_episode, minimal_cfg):
    """No behavior change in timestamps mode: default calls (no addressing_mode
    kwarg) must byte-match calls that explicitly pass addressing_mode='timestamps'."""
    ep = _load(tmp_path, write_corpus_episode)
    default_prompt = runner._build_user_prompt(ep, ep.windows[0], total_windows=1)
    explicit_prompt = runner._build_user_prompt(
        ep, ep.windows[0], total_windows=1, addressing_mode="timestamps", id_segments=None,
    )
    assert default_prompt == explicit_prompt

    default_hashes = runner.precompute_prompt_hashes(minimal_cfg, [ep], system_prompt="S")
    explicit_hashes = runner.precompute_prompt_hashes(
        minimal_cfg, [ep], system_prompt="S", addressing_mode="timestamps",
    )
    assert default_hashes == explicit_hashes


# --- ID response parse + resolve to seconds; fallback counting --------------

def test_id_response_parse_and_resolve_to_expected_seconds(tmp_path, write_corpus_episode):
    ep = _load(tmp_path, write_corpus_episode)
    id_segments = corpus.stamp_id_windows(ep)[0]
    response = '[{"start_id": 0, "end_id": 1, "confidence": 0.9, "reason": "BetterHelp ad"}]'
    ads, method, id_contract_miss = runner._parse_id_response(response, id_segments)
    assert id_contract_miss is False
    assert method is not None
    assert len(ads) == 1
    assert ads[0]["start"] == 0.0
    assert ads[0]["end"] == 30.0


def test_id_response_falls_back_to_timestamp_parse_and_counts_miss(tmp_path, write_corpus_episode):
    ep = _load(tmp_path, write_corpus_episode)
    id_segments = corpus.stamp_id_windows(ep)[0]
    response = '[{"start_time": 0.0, "end_time": 30.0, "confidence": 0.9, "reason": "BetterHelp ad"}]'
    ads, method, id_contract_miss = runner._parse_id_response(response, id_segments)
    assert id_contract_miss is True
    assert len(ads) == 1
    assert ads[0]["start"] == 0.0
    assert ads[0]["end"] == 30.0


def test_id_response_empty_text(tmp_path, write_corpus_episode):
    ep = _load(tmp_path, write_corpus_episode)
    id_segments = corpus.stamp_id_windows(ep)[0]
    ads, method, id_contract_miss = runner._parse_id_response("", id_segments)
    assert (ads, method, id_contract_miss) == ([], None, False)


# --- historical-record mode default -----------------------------------------

def test_reconstruct_user_prompt_missing_field_defaults_to_timestamps(tmp_path, write_corpus_episode):
    ep_dir = write_corpus_episode(tmp_path, segments=SEGMENTS)
    ep = corpus.load_episode(ep_dir)
    rebuilt = runner.reconstruct_user_prompt({"episode_id": ep.ep_id, "window_index": 0}, corpus_dir=tmp_path)
    assert rebuilt == runner._build_user_prompt(ep, ep.windows[0], total_windows=1)
    assert "[0.0s - 5.0s]" in rebuilt


def test_reconstruct_user_prompt_uses_record_segment_id_mode(tmp_path, write_corpus_episode):
    ep_dir = write_corpus_episode(tmp_path, segments=SEGMENTS)
    ep = corpus.load_episode(ep_dir)
    rebuilt = runner.reconstruct_user_prompt(
        {"episode_id": ep.ep_id, "window_index": 0, "addressing_mode": "segment_ids"}, corpus_dir=tmp_path,
    )
    assert "[0] This episode is brought to you by BetterHelp" in rebuilt


# --- report isolation ---------------------------------------------------------

def test_report_isolates_addressing_modes(tmp_path, minimal_cfg, pricing_snapshot, write_corpus_episode):
    ep_dir = write_corpus_episode(tmp_path / "corpus", segments=SEGMENTS)
    ep = corpus.load_episode(ep_dir)
    calls_path = tmp_path / "calls.jsonl"
    append_jsonl(calls_path, {
        **CALL_TEMPLATE, "call_id": "c1", "episode_id": ep.ep_id,
        "addressing_mode": "timestamps",
        "parsed_ads": [{"start_time": 0.0, "end_time": 30.0}],
    })
    append_jsonl(calls_path, {
        **CALL_TEMPLATE, "call_id": "c2", "episode_id": ep.ep_id,
        "model": "m-id-only", "addressing_mode": "segment_ids",
        "parsed_ads": [{"start": 0.0, "end": 30.0}],
    })

    out_ts = tmp_path / "report_ts.md"
    report_mod.render(
        cfg=minimal_cfg, episodes=[ep], calls_path=calls_path, episode_results_path=tmp_path / "ep.jsonl",
        pricing_snapshot=pricing_snapshot, output_path=out_ts, assets_dir=tmp_path / "assets_ts",
    )
    text_ts = out_ts.read_text()
    assert "`m1`" in text_ts
    assert "`m-id-only`" not in text_ts
    assert "addressing mode:" not in text_ts.splitlines()[0]

    out_id = tmp_path / "report_id.md"
    report_mod.render(
        cfg=minimal_cfg, episodes=[ep], calls_path=calls_path, episode_results_path=tmp_path / "ep.jsonl",
        pricing_snapshot=pricing_snapshot, output_path=out_id, assets_dir=tmp_path / "assets_id",
        addressing_mode="segment_ids",
    )
    text_id = out_id.read_text()
    assert "(addressing mode: segment_ids)" in text_id.splitlines()[0]
    assert "`m-id-only`" in text_id
    assert "`m1`" not in text_id


def test_report_historical_record_without_field_counts_as_timestamps(tmp_path, minimal_cfg, pricing_snapshot, write_corpus_episode):
    ep_dir = write_corpus_episode(tmp_path / "corpus", segments=SEGMENTS)
    ep = corpus.load_episode(ep_dir)
    calls_path = tmp_path / "calls.jsonl"
    append_jsonl(calls_path, {
        **CALL_TEMPLATE, "call_id": "c1", "episode_id": ep.ep_id,
        "parsed_ads": [{"start_time": 0.0, "end_time": 30.0}],
    })  # no addressing_mode key at all, as every call before this feature existed

    out_default = tmp_path / "report.md"
    report_mod.render(
        cfg=minimal_cfg, episodes=[ep], calls_path=calls_path, episode_results_path=tmp_path / "ep.jsonl",
        pricing_snapshot=pricing_snapshot, output_path=out_default, assets_dir=tmp_path / "assets",
    )
    assert "`m1`" in out_default.read_text()

    out_id = tmp_path / "report_id.md"
    report_mod.render(
        cfg=minimal_cfg, episodes=[ep], calls_path=calls_path, episode_results_path=tmp_path / "ep.jsonl",
        pricing_snapshot=pricing_snapshot, output_path=out_id, assets_dir=tmp_path / "assets_id",
        addressing_mode="segment_ids",
    )
    assert "No benchmark data yet" in out_id.read_text()


def test_id_contract_miss_surfaced_in_per_model_detail(tmp_path, minimal_cfg, pricing_snapshot, write_corpus_episode):
    ep_dir = write_corpus_episode(tmp_path / "corpus", segments=SEGMENTS)
    ep = corpus.load_episode(ep_dir)
    calls_path = tmp_path / "calls.jsonl"
    append_jsonl(calls_path, {
        **CALL_TEMPLATE, "call_id": "c1", "episode_id": ep.ep_id,
        "addressing_mode": "segment_ids", "id_contract_miss": True,
        "parsed_ads": [{"start_time": 0.0, "end_time": 30.0}],
    })
    out = tmp_path / "report.md"
    report_mod.render(
        cfg=minimal_cfg, episodes=[ep], calls_path=calls_path, episode_results_path=tmp_path / "ep.jsonl",
        pricing_snapshot=pricing_snapshot, output_path=out, assets_dir=tmp_path / "assets",
        addressing_mode="segment_ids",
    )
    assert "ID-contract misses (fell back to timestamp parse): 1" in out.read_text()
