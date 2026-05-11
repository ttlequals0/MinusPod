"""Report renders cleanly from synthesized calls.jsonl."""
from __future__ import annotations

from benchmark import report
from benchmark.storage import append_jsonl


CALL_RECORD_TEMPLATE = {
    "schema_version": 1,
    "model": "m1",
    "provider_config": "openrouter",
    "underlying_provider": "OpenRouter",
    "episode_id": "ep-001",
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


def test_render_with_no_data(tmp_path, minimal_cfg, make_episode, pricing_snapshot):
    calls = tmp_path / "calls.jsonl"
    out = tmp_path / "report.md"
    report.render(
        cfg=minimal_cfg, episodes=[make_episode()],
        calls_path=calls, episode_results_path=tmp_path / "ep.jsonl",
        pricing_snapshot=pricing_snapshot,
        output_path=out, assets_dir=tmp_path / "assets",
    )
    assert "No benchmark data yet" in out.read_text()


def test_render_with_one_call(tmp_path, minimal_cfg, make_episode, pricing_snapshot):
    ep = make_episode(n_windows=1)
    calls = tmp_path / "calls.jsonl"
    append_jsonl(calls, {**CALL_RECORD_TEMPLATE, "call_id": "c1", "parsed_ads": [{"start_time": 0.0, "end_time": 30.0}]})
    out = tmp_path / "report.md"
    report.render(
        cfg=minimal_cfg, episodes=[ep],
        calls_path=calls, episode_results_path=tmp_path / "ep.jsonl",
        pricing_snapshot=pricing_snapshot,
        output_path=out, assets_dir=tmp_path / "assets",
    )
    text = out.read_text()
    assert "## TL;DR" in text
    assert "m1" in text
    assert "Per-Episode Detail" in text
    assert "Run Metadata" in text


def test_render_handles_no_ad_episode(tmp_path, minimal_cfg, make_episode, pricing_snapshot):
    ep = make_episode(n_windows=1, no_ad=True)
    calls = tmp_path / "calls.jsonl"
    append_jsonl(calls, {**CALL_RECORD_TEMPLATE, "call_id": "c2", "parsed_ads": []})
    out = tmp_path / "report.md"
    report.render(
        cfg=minimal_cfg, episodes=[ep],
        calls_path=calls, episode_results_path=tmp_path / "ep.jsonl",
        pricing_snapshot=pricing_snapshot,
        output_path=out, assets_dir=tmp_path / "assets",
    )
    text = out.read_text()
    assert "PASS" in text
    assert "no-ads" in text.lower() or "no-ad" in text.lower()
