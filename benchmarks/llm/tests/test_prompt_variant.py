"""Threading prompt_variant through the CLI, runner, and call records.

Mirrors tests/test_addressing_mode.py's fixture style. No benchmark calls here.
"""
from __future__ import annotations

import asyncio

from typer.testing import CliRunner

from benchmark import cli, corpus, report as report_mod, runner, variants
from benchmark.llm import LLMResponse
from benchmark.pricing import PricingSnapshot
from benchmark.storage import append_call, read_calls, read_jsonl

from tests.test_addressing_mode import CALL_TEMPLATE, SEGMENTS
from tests.test_cli import write_minimal_config


# --- detection variant unchanged by default ----------------------------------

def test_detection_default_matches_explicit_kwarg_and_pre_change_behavior(make_episode, minimal_cfg):
    ep = make_episode()
    default_prompt = runner._build_user_prompt(ep, ep.windows[0], total_windows=1)
    explicit_prompt = runner._build_user_prompt(
        ep, ep.windows[0], total_windows=1, prompt_variant="detection",
    )
    assert default_prompt == explicit_prompt

    default_hashes = runner.precompute_prompt_hashes(minimal_cfg, [ep], system_prompt="S")
    explicit_hashes = runner.precompute_prompt_hashes(
        minimal_cfg, [ep], system_prompt="S", prompt_variant="detection",
    )
    assert default_hashes == explicit_hashes


# --- segmentation variant: user prompt + hash differ -------------------------

def test_segmentation_prompt_and_hash_differ_from_detection(tmp_path, write_corpus_episode, minimal_cfg):
    ep_dir = write_corpus_episode(tmp_path)
    ep = corpus.load_episode(ep_dir)

    detection_hashes = runner.precompute_prompt_hashes(minimal_cfg, [ep], system_prompt="S")
    segmentation_hashes = runner.precompute_prompt_hashes(
        minimal_cfg, [ep], system_prompt="S", prompt_variant="segmentation",
    )
    assert detection_hashes[("m1", ep.ep_id, 0, 0)] != segmentation_hashes[("m1", ep.ep_id, 0, 0)]


def test_segmentation_prompt_id_mode_header_says_indexes(tmp_path, write_corpus_episode):
    ep_dir = write_corpus_episode(tmp_path)
    ep = corpus.load_episode(ep_dir)
    prompt = runner._build_user_prompt(
        ep, ep.windows[0], total_windows=1,
        addressing_mode="segment_ids", prompt_variant="segmentation",
        id_segments=corpus.stamp_id_windows(ep)[0],
    )
    assert "Transcript with indexes:" in prompt


# --- reconstruct_user_prompt: legacy default ---------------------------------

def test_reconstruct_user_prompt_missing_field_defaults_to_detection(tmp_path, write_corpus_episode):
    ep_dir = write_corpus_episode(tmp_path)
    ep = corpus.load_episode(ep_dir)
    rebuilt = runner.reconstruct_user_prompt({"episode_id": ep.ep_id, "window_index": 0}, corpus_dir=tmp_path)
    assert rebuilt == runner._build_user_prompt(ep, ep.windows[0], total_windows=1, prompt_variant="detection")


# --- run() end-to-end: record field + derive_episode_results keying ---------

def test_run_writes_prompt_variant_on_record(tmp_path, minimal_cfg, make_episode, pricing_snapshot, monkeypatch):
    async def fake_call(**kwargs):
        return LLMResponse(
            text='[{"start_time": 0.0, "end_time": 30.0}]',
            input_tokens=100, output_tokens=10,
            json_format_used="native", underlying_provider="openrouter", stop_reason="stop",
        )

    monkeypatch.setattr(runner.llm, "call_with_retry", fake_call)
    ep = make_episode(n_windows=1)
    paths = runner.RunPaths.for_root(tmp_path)
    asyncio.run(runner.run(
        minimal_cfg, [ep], paths=paths, pricing_snapshot=pricing_snapshot, system_prompt="S",
        prompt_variant="segmentation",
    ))
    records = list(read_calls(paths.raw))
    assert records
    assert all(r["prompt_variant"] == "segmentation" for r in records)


def test_derive_episode_results_keeps_rows_for_two_variants(tmp_path, minimal_cfg, make_episode):
    ep = make_episode(n_windows=1)
    paths = runner.RunPaths.for_root(tmp_path)
    base = {
        "schema_version": 2, "model": "m1", "episode_id": ep.ep_id, "trial": 0,
        "window_index": 0, "input_tokens": 10, "output_tokens": 5, "response_time_ms": 1,
        "parsed_ads": [], "error": None,
    }
    append_call(paths.raw, {**base, "addressing_mode": "timestamps", "prompt_variant": "detection"})
    append_call(paths.raw, {**base, "addressing_mode": "timestamps", "prompt_variant": "segmentation"})

    runner.derive_episode_results(minimal_cfg, [ep], paths=paths)
    results = list(read_jsonl(paths.episode_results_jsonl))
    assert len(results) == 2
    variants_seen = {r["prompt_variant"] for r in results}
    assert variants_seen == {"detection", "segmentation"}
    for r in results:
        assert r["addressing_mode"] == "timestamps"


def test_derive_episode_results_legacy_record_defaults(tmp_path, minimal_cfg, make_episode):
    ep = make_episode(n_windows=1)
    paths = runner.RunPaths.for_root(tmp_path)
    append_call(paths.raw, {
        "schema_version": 2, "model": "m1", "episode_id": ep.ep_id, "trial": 0,
        "window_index": 0, "input_tokens": 10, "output_tokens": 5, "response_time_ms": 1,
        "parsed_ads": [], "error": None,
    })  # no addressing_mode or prompt_variant key, as every call before this feature existed

    runner.derive_episode_results(minimal_cfg, [ep], paths=paths)
    results = list(read_jsonl(paths.episode_results_jsonl))
    assert len(results) == 1
    assert results[0]["addressing_mode"] == "timestamps"
    assert results[0]["prompt_variant"] == "detection"


# --- CLI: --model / --episode filters ----------------------------------------

def test_run_dry_run_model_filter_narrows_work_list(tmp_path, monkeypatch, write_corpus_episode):
    cli_runner = CliRunner()
    cfg_path = write_minimal_config(tmp_path)
    corpus_dir = tmp_path / "data" / "corpus"
    monkeypatch.chdir(tmp_path)
    write_corpus_episode(corpus_dir, "ep-a")
    write_corpus_episode(corpus_dir, "ep-b")

    result = cli_runner.invoke(cli.app, ["run", "--config", str(cfg_path), "--dry-run"])
    assert result.exit_code == 0
    assert "10 calls would execute" in result.stdout  # 1 model x 2 episodes x 1 window x 5 trials

    result = cli_runner.invoke(
        cli.app, ["run", "--config", str(cfg_path), "--dry-run", "--episode", "ep-a"],
    )
    assert result.exit_code == 0
    assert "5 calls would execute" in result.stdout  # filtered to 1 episode


def test_run_unknown_model_filter_exits_2(tmp_path, monkeypatch, write_corpus_episode):
    cli_runner = CliRunner()
    cfg_path = write_minimal_config(tmp_path)
    corpus_dir = tmp_path / "data" / "corpus"
    monkeypatch.chdir(tmp_path)
    write_corpus_episode(corpus_dir, "ep-a")

    result = cli_runner.invoke(
        cli.app, ["run", "--config", str(cfg_path), "--dry-run", "--model", "bogus-model"],
    )
    assert result.exit_code == 2
    assert "bogus-model" in result.output


def test_run_unknown_episode_filter_exits_2(tmp_path, monkeypatch, write_corpus_episode):
    cli_runner = CliRunner()
    cfg_path = write_minimal_config(tmp_path)
    corpus_dir = tmp_path / "data" / "corpus"
    monkeypatch.chdir(tmp_path)
    write_corpus_episode(corpus_dir, "ep-a")

    result = cli_runner.invoke(
        cli.app, ["run", "--config", str(cfg_path), "--dry-run", "--episode", "ep-bogus"],
    )
    assert result.exit_code == 2
    assert "ep-bogus" in result.output


def test_run_episode_filter_keeps_other_episodes_in_regenerated_report(
    tmp_path, monkeypatch, write_corpus_episode, pricing_snapshot,
):
    """A filtered `run` must only narrow which calls execute, not the corpus
    the end-of-run report is regenerated from: ep-b's prior-run row must
    survive a run filtered to ep-a alone."""
    monkeypatch.setattr(cli, "_root", lambda: tmp_path)
    monkeypatch.setattr(cli.pricing, "latest_snapshot", lambda _dir: pricing_snapshot)
    cli_runner = CliRunner()
    cfg_path = write_minimal_config(tmp_path)
    corpus_dir = tmp_path / "data" / "corpus"
    write_corpus_episode(corpus_dir, "ep-a")
    write_corpus_episode(corpus_dir, "ep-b")

    paths = runner.RunPaths.for_root(tmp_path / "results")
    append_call(paths.raw, {
        **CALL_TEMPLATE, "call_id": "cb1", "episode_id": "ep-b",
        "addressing_mode": "timestamps", "prompt_variant": "detection",
        "parsed_ads": [{"start": 0.0, "end": 30.0}],
    })

    async def fake_call(**kwargs):
        return LLMResponse(
            text='[{"start_time": 0.0, "end_time": 30.0}]',
            input_tokens=100, output_tokens=10,
            json_format_used="native", underlying_provider="openrouter", stop_reason="stop",
        )

    monkeypatch.setattr(cli.runner_mod.llm, "call_with_retry", fake_call)

    result = cli_runner.invoke(
        cli.app, ["run", "--config", str(cfg_path), "--episode", "ep-a"],
    )
    assert result.exit_code == 0, result.output

    report_text = (tmp_path / "results" / "report.md").read_text()
    assert "ep-a" in report_text
    assert "ep-b" in report_text


# --- CLI: segmentation + --snapshot rejected ---------------------------------

def test_run_segmentation_with_snapshot_exits_2(tmp_path, monkeypatch, write_corpus_episode):
    cli_runner = CliRunner()
    cfg_path = write_minimal_config(tmp_path)
    corpus_dir = tmp_path / "data" / "corpus"
    monkeypatch.chdir(tmp_path)
    write_corpus_episode(corpus_dir, "ep-a")
    snapshot_path = tmp_path / "snap.txt"
    snapshot_path.write_text("frozen prompt")

    result = cli_runner.invoke(
        cli.app,
        ["run", "--config", str(cfg_path), "--dry-run", "--prompt-variant", "segmentation", "--snapshot", str(snapshot_path)],
    )
    assert result.exit_code == 2
    assert "segmentation variant uses its frozen prompt" in result.output


def test_show_prompt_segmentation_record_with_snapshot_exits_2(tmp_path, monkeypatch, write_corpus_episode):
    monkeypatch.setattr(cli, "_root", lambda: tmp_path)
    cfg_path = write_minimal_config(tmp_path)
    write_corpus_episode(tmp_path / "data" / "corpus")

    paths = runner.RunPaths.for_root(tmp_path / "results")
    append_call(paths.raw, {
        **CALL_TEMPLATE, "call_id": "cseg1",
        "prompt_variant": "segmentation", "addressing_mode": "timestamps",
    })
    snapshot_path = tmp_path / "snap.txt"
    snapshot_path.write_text("frozen prompt")

    cli_runner = CliRunner()
    result = cli_runner.invoke(
        cli.app,
        ["show-prompt", "cseg1", "--config", str(cfg_path), "--snapshot", str(snapshot_path)],
    )
    assert result.exit_code == 2
    assert "segmentation variant uses its frozen prompt" in result.output


def test_dump_prompt_segmentation_writes_frozen_prompt(tmp_path):
    cli_runner = CliRunner()
    out = tmp_path / "seg_prompt.txt"
    result = cli_runner.invoke(cli.app, ["dump-prompt", str(out), "--prompt-variant", "segmentation"])
    assert result.exit_code == 0, result.output
    assert out.read_text() == variants.segmentation_system_prompt()


# --- report isolation by prompt variant --------------------------------------

def test_report_isolates_prompt_variants(tmp_path, minimal_cfg, pricing_snapshot, write_corpus_episode):
    ep_dir = write_corpus_episode(tmp_path / "corpus", segments=SEGMENTS)
    ep = corpus.load_episode(ep_dir)
    append_call(tmp_path, {
        **CALL_TEMPLATE, "call_id": "c1", "episode_id": ep.ep_id,
        "prompt_variant": "detection",
        "parsed_ads": [{"start_time": 0.0, "end_time": 30.0}],
    })
    append_call(tmp_path, {
        **CALL_TEMPLATE, "call_id": "c2", "episode_id": ep.ep_id,
        "model": "m-seg-only", "prompt_variant": "segmentation",
        "parsed_ads": [{"start": 0.0, "end": 30.0}],
    })

    out_det = tmp_path / "report_det.md"
    report_mod.render(
        cfg=minimal_cfg, episodes=[ep], raw_dir=tmp_path,
        pricing_snapshot=pricing_snapshot, output_path=out_det, assets_dir=tmp_path / "assets_det",
    )
    text_det = out_det.read_text()
    assert "`m1`" in text_det
    assert "`m-seg-only`" not in text_det
    assert "prompt variant:" not in text_det.splitlines()[0]

    out_seg = tmp_path / "report_seg.md"
    report_mod.render(
        cfg=minimal_cfg, episodes=[ep], raw_dir=tmp_path,
        pricing_snapshot=pricing_snapshot, output_path=out_seg, assets_dir=tmp_path / "assets_seg",
        prompt_variant="segmentation",
    )
    text_seg = out_seg.read_text()
    assert "(prompt variant: segmentation)" in text_seg.splitlines()[0]
    assert "`m-seg-only`" in text_seg
    assert "`m1`" not in text_seg


def test_report_historical_record_without_prompt_variant_counts_as_detection(
    tmp_path, minimal_cfg, pricing_snapshot, write_corpus_episode,
):
    ep_dir = write_corpus_episode(tmp_path / "corpus", segments=SEGMENTS)
    ep = corpus.load_episode(ep_dir)
    append_call(tmp_path, {
        **CALL_TEMPLATE, "call_id": "c1", "episode_id": ep.ep_id,
        "parsed_ads": [{"start_time": 0.0, "end_time": 30.0}],
    })  # no prompt_variant key, as every call before this feature existed

    out_det = tmp_path / "report.md"
    report_mod.render(
        cfg=minimal_cfg, episodes=[ep], raw_dir=tmp_path,
        pricing_snapshot=pricing_snapshot, output_path=out_det, assets_dir=tmp_path / "assets",
    )
    assert "`m1`" in out_det.read_text()

    out_seg = tmp_path / "report_seg.md"
    report_mod.render(
        cfg=minimal_cfg, episodes=[ep], raw_dir=tmp_path,
        pricing_snapshot=pricing_snapshot, output_path=out_seg, assets_dir=tmp_path / "assets_seg",
        prompt_variant="segmentation",
    )
    assert "No benchmark data yet" in out_seg.read_text()


# --- report_paths: default cell unchanged, other cells suffixed -------------

def test_report_paths_default_cell_is_unchanged(tmp_path):
    report_md, assets_dir = report_mod.report_paths(tmp_path, "detection", "timestamps")
    assert report_md == tmp_path / "report.md"
    assert assets_dir == tmp_path / "report_assets"


def test_report_paths_nondefault_cell_is_suffixed(tmp_path):
    report_md, assets_dir = report_mod.report_paths(tmp_path, "segmentation", "segment_ids")
    assert report_md == tmp_path / "report-segmentation-segment_ids.md"
    assert assets_dir == tmp_path / "report_assets-segmentation-segment_ids"


def test_nondefault_cell_chart_links_point_at_own_assets_dir(
    tmp_path, minimal_cfg, pricing_snapshot, write_corpus_episode,
):
    ep_dir = write_corpus_episode(tmp_path / "corpus", segments=SEGMENTS)
    ep = corpus.load_episode(ep_dir)
    append_call(tmp_path, {
        **CALL_TEMPLATE, "call_id": "c1", "episode_id": ep.ep_id,
        "prompt_variant": "segmentation", "addressing_mode": "segment_ids",
        "parsed_ads": [{"start": 0.0, "end": 30.0}],
    })
    report_md, assets_dir = report_mod.report_paths(tmp_path, "segmentation", "segment_ids")
    report_mod.render(
        cfg=minimal_cfg, episodes=[ep], raw_dir=tmp_path,
        pricing_snapshot=pricing_snapshot, output_path=report_md, assets_dir=assets_dir,
        prompt_variant="segmentation", addressing_mode="segment_ids",
    )
    text = report_md.read_text()
    assert "(report_assets-segmentation-segment_ids/pareto.svg)" in text
    assert (assets_dir / "pareto.svg").is_file()


# --- cli.report computes the suffixed paths for a non-default cell ----------

def test_cli_report_computes_suffixed_paths_for_nondefault_cell(tmp_path, monkeypatch, write_corpus_episode):
    cli_runner = CliRunner()
    cfg_path = write_minimal_config(tmp_path)
    corpus_dir = tmp_path / "data" / "corpus"
    monkeypatch.chdir(tmp_path)
    write_corpus_episode(corpus_dir, "ep-a")

    captured = {}
    monkeypatch.setattr(cli.report_mod, "render", lambda **kw: captured.update(kw))
    monkeypatch.setattr(cli.pricing, "latest_snapshot", lambda _dir: PricingSnapshot(captured_at="x", entries=[]))

    result = cli_runner.invoke(
        cli.app,
        ["report", "--config", str(cfg_path), "--prompt-variant", "segmentation", "--addressing-mode", "segment_ids"],
    )
    assert result.exit_code == 0, result.output
    # cli._root() is the installed package location, not cwd; it is unaffected
    # by monkeypatch.chdir, same as every other file path cli.py writes.
    results_dir = cli._root() / "results"
    assert captured["output_path"] == results_dir / "report-segmentation-segment_ids.md"
    assert captured["assets_dir"] == results_dir / "report_assets-segmentation-segment_ids"
    assert captured["prompt_variant"] == "segmentation"
    assert captured["addressing_mode"] == "segment_ids"
