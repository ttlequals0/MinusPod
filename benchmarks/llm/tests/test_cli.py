"""CLI smoke tests via typer's testing harness."""
from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from benchmark import cli


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
