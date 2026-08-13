"""CLI smoke tests via typer's testing harness."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from benchmark import cli
from benchmark import runner as runner_mod


def write_minimal_config(tmp_path: Path) -> Path:
    p = tmp_path / "benchmark.toml"
    p.write_text("""
[minuspod]
base_url = "x"
password_env = "P"

[providers.openrouter]
client = "openai_compatible"
api_key_env = "K"
base_url = "https://x"

[[models]]
id = "m1"
provider = "openrouter"

[corpus]
path = "data/corpus"
""")
    return p


def test_help_runs():
    runner = CliRunner()
    result = runner.invoke(cli.app, ["--help"])
    assert result.exit_code == 0
    assert "capture" in result.stdout
    assert "verify" in result.stdout
    assert "run" in result.stdout
    assert "report" in result.stdout


def test_help_lists_audit_commands():
    runner = CliRunner()
    result = runner.invoke(cli.app, ["--help"])
    assert result.exit_code == 0
    assert "show-prompt" in result.stdout
    assert "show-response" in result.stdout
    assert "migrate-raw" in result.stdout


def test_validate_with_missing_config(tmp_path):
    runner = CliRunner()
    result = runner.invoke(cli.app, ["validate", "--config", str(tmp_path / "missing.toml")])
    assert result.exit_code == 1
    assert "not found" in result.stderr or "not found" in result.output


def test_validate_with_valid_config_no_corpus(tmp_path):
    runner = CliRunner()
    cfg = write_minimal_config(tmp_path)
    result = runner.invoke(cli.app, ["validate", "--config", str(cfg)])
    assert result.exit_code == 0
    assert "config OK" in result.stdout


def test_list_episodes_empty(tmp_path):
    runner = CliRunner()
    cfg = write_minimal_config(tmp_path)
    result = runner.invoke(cli.app, ["list-episodes", "--config", str(cfg)])
    assert result.exit_code == 0
    assert result.stdout.strip() == ""


def test_regenerate_windows_requires_force(tmp_path):
    runner = CliRunner()
    cfg = write_minimal_config(tmp_path)
    result = runner.invoke(cli.app, ["regenerate-windows", "ep-x", "--config", str(cfg)])
    assert result.exit_code == 2
    assert "force" in result.stdout or "force" in result.output


def _call_record(model: str, ep_id: str, trial: int, window: int, prompt_hash: str, *, error: bool) -> dict:
    return {
        "model": model,
        "episode_id": ep_id,
        "trial": trial,
        "window_index": window,
        "prompt_hash": prompt_hash,
        "error": {"type": "LLMNonRetryableError", "message": "404"} if error else None,
    }


def test_preview_honors_include_errored(tmp_path, minimal_cfg, make_episode):
    """--dry-run --retry-errors must count errored units, not report them skipped."""
    ep = make_episode(n_windows=1)
    hashes = runner_mod.precompute_prompt_hashes(minimal_cfg, [ep], system_prompt="S")
    calls = tmp_path / "calls.jsonl"
    with calls.open("w") as f:
        for trial in (0, 1):
            h = hashes[("m1", ep.ep_id, trial, 0)]
            f.write(json.dumps(_call_record("m1", ep.ep_id, trial, 0, h, error=trial == 0)) + "\n")

    paths = runner_mod.RunPaths(
        calls_jsonl=calls,
        episode_results_jsonl=tmp_path / "episode_results.jsonl",
        responses_dir=tmp_path / "responses",
        prompts_dir=tmp_path / "prompts",
    )

    units, skipped = cli._preview(minimal_cfg, [ep], paths=paths, system_prompt="S")
    assert (len(units), skipped) == (0, 2)

    units, skipped = cli._preview(minimal_cfg, [ep], paths=paths, system_prompt="S", include_errored=True)
    assert (len(units), skipped) == (1, 1)
