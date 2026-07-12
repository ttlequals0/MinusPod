"""Runner unit tests focusing on the synchronous building blocks.

Async fan-out integration is exercised via the runner's actual use against
real provider APIs, which is out of scope for the offline unit suite.
"""
from __future__ import annotations

import asyncio

import pytest

from benchmark import corpus, runner
from benchmark.llm import LLMResponse
from benchmark.storage import read_jsonl


def test_build_user_prompt_uses_minuspod_format(make_episode):
    ep = make_episode()
    prompt = runner._build_user_prompt(ep, ep.windows[0], total_windows=len(ep.windows))
    assert "T" in prompt
    assert "Title" in prompt
    assert "line 0" in prompt


def test_precompute_prompt_hashes_one_per_combo(minimal_cfg, make_episode):
    ep = make_episode(n_windows=3)
    hashes = runner.precompute_prompt_hashes(minimal_cfg, [ep], system_prompt="S")
    # 1 active model * 1 episode * 2 trials * 3 windows = 6 (deprecated skipped)
    assert len(hashes) == 6
    assert all(model_id == "m1" for (model_id, _, _, _) in hashes)


def test_precompute_prompt_hashes_identical_across_trials_for_same_model(minimal_cfg, make_episode):
    ep = make_episode(n_windows=2)
    hashes = runner.precompute_prompt_hashes(minimal_cfg, [ep], system_prompt="S")
    assert hashes[("m1", "ep-001", 0, 0)] == hashes[("m1", "ep-001", 1, 0)]
    assert ("m-old", "ep-001", 0, 0) not in hashes


def test_build_work_list_skips_deprecated_models(minimal_cfg, make_episode):
    ep = make_episode(n_windows=1)
    hashes = runner.precompute_prompt_hashes(minimal_cfg, [ep], system_prompt="S")
    units, skipped = runner.build_work_list(minimal_cfg, [ep], completed=set(), prompt_hashes=hashes)
    assert skipped == 0
    model_ids = {u.model_id for u in units}
    assert model_ids == {"m1"}


def test_build_work_list_skips_completed(minimal_cfg, make_episode):
    ep = make_episode(n_windows=2)
    hashes = runner.precompute_prompt_hashes(minimal_cfg, [ep], system_prompt="S")
    completed = {("m1", "ep-001", 0, 0, hashes[("m1", "ep-001", 0, 0)])}
    units, skipped = runner.build_work_list(minimal_cfg, [ep], completed=completed, prompt_hashes=hashes)
    assert skipped == 1
    assert len(units) == 3


def test_build_work_list_includes_errored_when_requested(minimal_cfg, make_episode):
    ep = make_episode(n_windows=1)
    hashes = runner.precompute_prompt_hashes(minimal_cfg, [ep], system_prompt="S")
    h = hashes[("m1", "ep-001", 0, 0)]
    completed = {("m1", "ep-001", 0, 0, h)}
    err_keys = {("m1", "ep-001", 0, 0, h)}
    units, skipped = runner.build_work_list(
        minimal_cfg, [ep],
        completed=completed,
        prompt_hashes=hashes,
        include_errored=True,
        error_keys=err_keys,
    )
    # 1 trial in minimal_cfg's 2 trials only the errored one is retried; the
    # other trial isn't in completed so it's also queued. Total: 2 units.
    assert len(units) == 2


def test_call_id_is_deterministic_shape():
    unit = runner.WorkUnit(model_id="anthropic/claude-sonnet-4.6", provider_name="openrouter", episode_id="ep-001", trial=0, window_index=2)
    cid = runner._call_id(unit, "sha256:abcdef0123456789")
    assert "anthropic_claude-sonnet-4.6" in cid
    assert "ep-001" in cid
    assert "_t0_w2" in cid
    assert "abcdef012345" in cid


def test_run_writes_v2_records_and_response_shards(tmp_path, minimal_cfg, make_episode, pricing_snapshot, monkeypatch):
    """The execute path writes schema v2: response bodies appended to a
    per-model JSONL shard, response_path pointing at the shard, and no
    prompt_path (prompts are reconstructed on demand)."""
    async def fake_call(**kwargs):
        return LLMResponse(
            text='[{"start_time": 0.0, "end_time": 30.0}]',
            input_tokens=100,
            output_tokens=10,
            json_format_used="native",
            underlying_provider="openrouter",
            stop_reason="stop",
        )

    monkeypatch.setattr(runner.llm, "call_with_retry", fake_call)
    ep = make_episode(n_windows=1)
    paths = runner.RunPaths.for_root(tmp_path)
    stats = asyncio.run(runner.run(
        minimal_cfg, [ep], paths=paths, pricing_snapshot=pricing_snapshot, system_prompt="S",
    ))

    assert stats.completed == 2  # 1 model x 1 window x 2 trials
    records = list(read_jsonl(paths.calls_jsonl))
    assert len(records) == 2
    for rec in records:
        assert rec["schema_version"] == 2
        assert rec["response_path"] == "responses/m1.jsonl"
        assert "prompt_path" not in rec

    shard = paths.responses_dir / "m1.jsonl"
    bodies = list(read_jsonl(shard))
    assert {b["call_id"] for b in bodies} == {r["call_id"] for r in records}
    assert all(b["body"] == '[{"start_time": 0.0, "end_time": 30.0}]' for b in bodies)
    assert not paths.prompts_dir.exists()


def test_reconstruct_user_prompt_matches_runtime_prompt(tmp_path, write_corpus_episode):
    """show-prompt rebuilds the exact user prompt the runner sent, from the
    committed corpus alone."""
    ep_id = write_corpus_episode(tmp_path).name
    rebuilt = runner.reconstruct_user_prompt(
        {"episode_id": ep_id, "window_index": 0}, corpus_dir=tmp_path,
    )
    ep = corpus.load_episode(tmp_path / ep_id)
    assert rebuilt == runner._build_user_prompt(ep, ep.windows[0], total_windows=1)
    assert "BetterHelp" in rebuilt


def test_reconstruct_user_prompt_rejects_stale_window_index(tmp_path, write_corpus_episode):
    ep_id = write_corpus_episode(tmp_path).name
    with pytest.raises(ValueError, match="window_index"):
        runner.reconstruct_user_prompt(
            {"episode_id": ep_id, "window_index": 5}, corpus_dir=tmp_path,
        )


def test_violations_dict_round_trip():
    from dataclasses import asdict
    from benchmark.metrics import schema_audit
    v = schema_audit([{"start": 0, "end": 10, "extra1": "x"}])
    d = asdict(v)
    assert d["extra_keys"] == 1
    assert d["extra_key_names"] == ["extra1"]


def test_parse_response_empty_text():
    parsed, method = runner._parse_response("")
    assert parsed == []
    assert method is None


def test_parse_response_valid_array():
    text = '[{"start_time": 10.0, "end_time": 30.0, "confidence": 0.95, "reason": "test ad"}]'
    parsed, method = runner._parse_response(text)
    assert method is not None
    assert isinstance(parsed, list)
