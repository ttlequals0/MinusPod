"""Typer CLI for the MinusPod LLM benchmark."""
from __future__ import annotations

import asyncio
import logging
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import typer

from . import auth, capture as capture_mod, corpus as corpus_mod, parsing, pricing, report as report_mod, runner as runner_mod
from .config import BenchmarkConfig, load as load_config
from .runner import build_work_list, precompute_prompt_hashes
from .storage import scan_calls

app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    help="Offline LLM ad-detection benchmark for MinusPod.",
)


def _setup_logging() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")


def _load(config_path: Path) -> BenchmarkConfig:
    try:
        return load_config(config_path)
    except Exception as e:
        typer.echo(f"error loading {config_path}: {e}", err=True)
        raise typer.Exit(1)


def _root() -> Path:
    return Path(__file__).resolve().parents[2]


@app.command()
def capture(
    episode_url: str = typer.Option(..., "--episode-url", help="MinusPod UI URL of the episode to capture"),
    config_path: Path = typer.Option(Path("benchmark.toml"), "--config", help="Path to benchmark.toml"),
) -> None:
    """Pull an episode from MinusPod into data/candidates/."""
    _setup_logging()
    cfg = _load(config_path)
    session = auth.acquire(cfg.minuspod)
    candidates_dir = _root() / "data" / "candidates"
    corpus_dir = cfg.corpus.path

    candidate_dir = capture_mod.capture(
        base_url=cfg.minuspod.base_url,
        episode_url=episode_url,
        session=session,
        candidates_dir=candidates_dir,
        corpus_dir=corpus_dir,
    )
    typer.echo(f"captured: {candidate_dir}")
    typer.echo("Edit truth.txt under that directory, then run: benchmark verify <ep-id>")


@app.command()
def verify(
    ep_id: str = typer.Argument(..., help="Episode id (the directory name under data/candidates/)"),
    config_path: Path = typer.Option(Path("benchmark.toml"), "--config", help="Path to benchmark.toml"),
) -> None:
    """Validate a candidate, precompute windows, and promote to data/corpus/."""
    _setup_logging()
    cfg = _load(config_path)
    candidates_dir = _root() / "data" / "candidates"
    corpus_dir = cfg.corpus.path

    target = capture_mod.verify(ep_id, candidates_dir=candidates_dir, corpus_dir=corpus_dir)
    typer.echo(f"verified and promoted to corpus: {target}")


@app.command("regenerate-windows")
def regenerate_windows_cmd(
    ep_id: str = typer.Argument(...),
    force: bool = typer.Option(False, "--force"),
    config_path: Path = typer.Option(Path("benchmark.toml"), "--config"),
) -> None:
    """Recompute windows.json for a corpus episode."""
    _setup_logging()
    cfg = _load(config_path)
    if not force:
        typer.echo("regenerate-windows requires --force (invalidates prior calls.jsonl entries for this episode).")
        raise typer.Exit(2)
    n = capture_mod.regenerate_windows(ep_id, corpus_dir=cfg.corpus.path)
    typer.echo(f"regenerated {n} windows for {ep_id}")


@app.command("list-episodes")
def list_episodes_cmd(
    podcast_slug: Optional[str] = typer.Option(None, "--podcast-slug"),
    config_path: Path = typer.Option(Path("benchmark.toml"), "--config"),
) -> None:
    """List corpus episodes."""
    cfg = _load(config_path)
    episodes = corpus_mod.list_episodes(cfg.corpus.path)
    if podcast_slug:
        episodes = [e for e in episodes if e.startswith(f"ep-{podcast_slug}-")]
    for e in episodes:
        typer.echo(e)


@app.command()
def validate(
    config_path: Path = typer.Option(Path("benchmark.toml"), "--config"),
) -> None:
    """Validate config + corpus integrity."""
    _setup_logging()
    cfg = _load(config_path)
    typer.echo(f"config OK: {len(cfg.providers)} providers, {len(cfg.models)} models")
    episodes = corpus_mod.list_episodes(cfg.corpus.path)
    failures: list[str] = []
    for ep_id in episodes:
        try:
            corpus_mod.load_episode(cfg.corpus.path / ep_id)
        except Exception as e:
            failures.append(f"  {ep_id}: {e}")
    typer.echo(f"corpus episodes: {len(episodes)}; failures: {len(failures)}")
    for f in failures:
        typer.echo(f, err=True)
    if failures:
        raise typer.Exit(1)


@app.command("refresh-pricing")
def refresh_pricing_cmd(
    config_path: Path = typer.Option(Path("benchmark.toml"), "--config"),
) -> None:
    """Fetch a new pricing snapshot via MinusPod's pricing_fetcher."""
    _setup_logging()
    _load(config_path)
    snap = pricing.fetch_current()
    snapshots_dir = _root() / "data" / "pricing_snapshots"
    path = pricing.write_snapshot(snap, snapshots_dir)
    typer.echo(f"wrote pricing snapshot: {path} ({len(snap.entries)} models)")


@app.command()
def run(
    config_path: Path = typer.Option(Path("benchmark.toml"), "--config"),
    retry_errors: bool = typer.Option(False, "--retry-errors"),
    force: bool = typer.Option(False, "--force"),
    dry_run: bool = typer.Option(False, "--dry-run"),
    no_report_on_failure: bool = typer.Option(False, "--no-report-on-failure"),
) -> None:
    """Auto-fill all gaps in calls.jsonl, then regenerate report."""
    _setup_logging()
    cfg = _load(config_path)
    episodes = [corpus_mod.load_episode(cfg.corpus.path / e) for e in corpus_mod.list_episodes(cfg.corpus.path)]
    if not episodes:
        typer.echo("no corpus episodes; run `benchmark capture` first", err=True)
        raise typer.Exit(1)

    paths = runner_mod.RunPaths.for_root(_root() / "results")
    snapshots_dir = _root() / "data" / "pricing_snapshots"
    snap = pricing.latest_snapshot(snapshots_dir) or pricing.fetch_current()
    if pricing.latest_snapshot(snapshots_dir) is None:
        pricing.write_snapshot(snap, snapshots_dir)

    if force:
        typer.echo("WARNING: --force will reset existing calls; abort if unintended.")
        if paths.calls_jsonl.exists():
            paths.calls_jsonl.unlink()

    if dry_run:
        units, skipped = _preview(cfg, episodes, paths=paths)
        typer.echo(f"dry-run: {len(units)} calls would execute, {skipped} skipped (already done)")
        raise typer.Exit(0)

    stats = asyncio.run(runner_mod.run(cfg, episodes, paths=paths, pricing_snapshot=snap, include_errored=retry_errors))
    typer.echo(f"run complete: total={stats.total_units} skipped={stats.skipped} completed={stats.completed} errored={stats.errored}")

    if stats.errored and no_report_on_failure:
        typer.echo("skipping report regen (--no-report-on-failure)")
        return

    output = _root() / "results" / "report.md"
    assets = _root() / "results" / "report_assets"
    report_mod.render(
        cfg=cfg,
        episodes=episodes,
        calls_path=paths.calls_jsonl,
        episode_results_path=paths.episode_results_jsonl,
        pricing_snapshot=snap,
        output_path=output,
        assets_dir=assets,
    )
    typer.echo(f"report written: {output}")


@app.command()
def report(
    config_path: Path = typer.Option(Path("benchmark.toml"), "--config"),
) -> None:
    """Regenerate results/report.md from existing calls.jsonl."""
    _setup_logging()
    cfg = _load(config_path)
    episodes = [corpus_mod.load_episode(cfg.corpus.path / e) for e in corpus_mod.list_episodes(cfg.corpus.path)]
    paths = runner_mod.RunPaths.for_root(_root() / "results")
    snap = pricing.latest_snapshot(_root() / "data" / "pricing_snapshots") or pricing.fetch_current()
    output = _root() / "results" / "report.md"
    assets = _root() / "results" / "report_assets"
    report_mod.render(
        cfg=cfg,
        episodes=episodes,
        calls_path=paths.calls_jsonl,
        episode_results_path=paths.episode_results_jsonl,
        pricing_snapshot=snap,
        output_path=output,
        assets_dir=assets,
    )
    typer.echo(f"report written: {output}")


@app.command()
def archive() -> None:
    """Snapshot results/report.md + assets to results/archive/<date>/."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    src_report = _root() / "results" / "report.md"
    src_assets = _root() / "results" / "report_assets"
    dst_dir = _root() / "results" / "archive" / today
    if not src_report.is_file():
        typer.echo("no results/report.md to archive", err=True)
        raise typer.Exit(1)
    dst_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy(src_report, dst_dir / "report.md")
    if src_assets.is_dir():
        dst_assets = dst_dir / "report_assets"
        if dst_assets.exists():
            shutil.rmtree(dst_assets)
        shutil.copytree(src_assets, dst_assets)
    typer.echo(f"archived to {dst_dir}")


def _preview(cfg, episodes, *, paths):
    system_prompt = parsing.get_static_system_prompt()
    hashes = precompute_prompt_hashes(cfg, episodes, system_prompt=system_prompt)
    completed, _ = scan_calls(paths.calls_jsonl)
    units, skipped = build_work_list(cfg, episodes, completed=completed, prompt_hashes=hashes)
    return units, skipped


if __name__ == "__main__":
    app()
