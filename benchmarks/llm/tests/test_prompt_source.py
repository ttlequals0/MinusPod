"""Snapshot system-prompt resolution, hash decoupling, report note, dump-prompt."""
from __future__ import annotations

import hashlib

import pytest
from typer.testing import CliRunner

from benchmark import cli, parsing, report
from benchmark.runner import precompute_prompt_hashes


def test_resolve_live_default(monkeypatch):
    monkeypatch.setattr(parsing, "get_static_system_prompt", lambda: "LIVE PROMPT")
    text, label = parsing.resolve_system_prompt(None)
    assert text == "LIVE PROMPT"
    sha8 = hashlib.sha256(b"LIVE PROMPT").hexdigest()[:8]
    assert label == f"live (sha256:{sha8})"


def test_resolve_snapshot_reads_verbatim(tmp_path):
    p = tmp_path / "2026-06-02.txt"
    body = "FROZEN PROMPT\nwith sponsors baked in\n"
    p.write_text(body)
    text, label = parsing.resolve_system_prompt(p)
    assert text == body
    sha8 = hashlib.sha256(body.encode()).hexdigest()[:8]
    assert label == f"snapshot:2026-06-02.txt (sha256:{sha8})"


def test_resolve_snapshot_label_omits_path(tmp_path):
    sub = tmp_path / "deep" / "nested"
    sub.mkdir(parents=True)
    p = sub / "frozen.txt"
    p.write_text("x")
    _, label = parsing.resolve_system_prompt(p)
    assert "snapshot:frozen.txt" in label
    assert str(sub) not in label


def test_resolve_missing_file_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        parsing.resolve_system_prompt(tmp_path / "nope.txt")


def test_resolve_empty_file_raises(tmp_path):
    p = tmp_path / "empty.txt"
    p.write_text("   \n")
    with pytest.raises(ValueError):
        parsing.resolve_system_prompt(p)


def test_snapshot_changes_hashes(minimal_cfg, make_episode):
    """Different system prompt -> different prompt hashes, so snapshot and live
    rows never collide in dedup (the whole point of decoupling)."""
    eps = [make_episode("ep-1", n_windows=2)]
    live = precompute_prompt_hashes(minimal_cfg, eps, system_prompt="LIVE")
    snap = precompute_prompt_hashes(minimal_cfg, eps, system_prompt="FROZEN")
    assert live.keys() == snap.keys()
    assert all(live[k] != snap[k] for k in live)


def test_run_metadata_includes_prompt_source(pricing_snapshot):
    calls = [{"error": None, "total_cost_usd_at_runtime": 0.0}]
    out = report._render_run_metadata(
        calls, pricing_snapshot=pricing_snapshot,
        prompt_source="snapshot:2026-06-02.txt (sha256:a1b2c3d4)",
    )
    assert "- System prompt: snapshot:2026-06-02.txt (sha256:a1b2c3d4)" in out


def test_run_metadata_defaults_to_live(pricing_snapshot):
    calls = [{"error": None, "total_cost_usd_at_runtime": 0.0}]
    out = report._render_run_metadata(calls, pricing_snapshot=pricing_snapshot)
    assert "- System prompt: live" in out


def test_dump_prompt_writes_file(tmp_path):
    runner = CliRunner()
    out = tmp_path / "snap" / "prompt.txt"
    result = runner.invoke(cli.app, ["dump-prompt", str(out)])
    assert result.exit_code == 0, result.output
    assert out.is_file()
    assert out.read_text().strip()
    assert "wrote prompt snapshot" in result.output


def test_run_dry_run_rejects_missing_snapshot(tmp_path):
    from tests.test_cli import write_minimal_config
    cfg = write_minimal_config(tmp_path)
    runner = CliRunner()
    result = runner.invoke(
        cli.app,
        ["run", "--config", str(cfg), "--dry-run", "--snapshot", str(tmp_path / "nope.txt")],
    )
    assert result.exit_code == 1
    assert "snapshot prompt not found" in (result.stderr or result.output)
