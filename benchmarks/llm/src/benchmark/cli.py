"""Typer CLI for the MinusPod LLM benchmark."""
from __future__ import annotations

import asyncio
import logging
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import typer
from dotenv import load_dotenv

# Load benchmarks/llm/.env so MINUSPOD_PASSWORD and provider API keys are available
# regardless of where the user invokes `benchmark` from. Shell-exported vars still win.
load_dotenv(dotenv_path=Path(__file__).resolve().parents[2] / ".env", override=False)

from . import auth, capture as capture_mod, corpus as corpus_mod, migrate as migrate_mod, parsing, pricing, report as report_mod, runner as runner_mod
from .config import BenchmarkConfig, load as load_config
from .runner import build_work_list, precompute_prompt_hashes
from .storage import find_call, hash_prompt, read_response, scan_calls

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


def _resolve_prompt(snapshot: Optional[Path]) -> tuple[str, str]:
    try:
        return parsing.resolve_system_prompt(snapshot)
    except (FileNotFoundError, ValueError) as e:
        typer.echo(f"error: {e}", err=True)
        raise typer.Exit(1)


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
    snapshot: Optional[Path] = typer.Option(
        None, "--snapshot",
        help="Frozen system-prompt file to use instead of the live prompt (decouples the corpus from SEED_SPONSORS edits).",
    ),
) -> None:
    """Auto-fill all gaps in calls.jsonl, then regenerate report."""
    _setup_logging()
    cfg = _load(config_path)
    system_prompt, prompt_source = _resolve_prompt(snapshot)
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
        units, skipped = _preview(cfg, episodes, paths=paths, system_prompt=system_prompt)
        typer.echo(f"dry-run: {len(units)} calls would execute, {skipped} skipped (already done)")
        raise typer.Exit(0)

    stats = asyncio.run(runner_mod.run(cfg, episodes, paths=paths, pricing_snapshot=snap, system_prompt=system_prompt, include_errored=retry_errors))
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
        prompt_source=prompt_source,
    )
    typer.echo(f"report written: {output}")


@app.command()
def report(
    config_path: Path = typer.Option(Path("benchmark.toml"), "--config"),
    snapshot: Optional[Path] = typer.Option(
        None, "--snapshot",
        help="Label the report with this prompt file; pass the same snapshot used for `run` so the footer matches the stored calls.",
    ),
) -> None:
    """Regenerate results/report.md from existing calls.jsonl."""
    _setup_logging()
    cfg = _load(config_path)
    _, prompt_source = _resolve_prompt(snapshot)
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
        prompt_source=prompt_source,
    )
    typer.echo(f"report written: {output}")


@app.command()
def dump_prompt(
    output: Path = typer.Argument(..., help="File to write the current live system prompt to"),
) -> None:
    """Freeze the current live system prompt to a file for use with `run --snapshot`."""
    text = parsing.get_static_system_prompt()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(text)
    typer.echo(f"wrote prompt snapshot: {output} ({len(text)} chars)")


@app.command("migrate-raw")
def migrate_raw_cmd(
    config_path: Path = typer.Option(Path("benchmark.toml"), "--config"),
) -> None:
    """One-time migration of results/raw from v1 (per-call .txt) to v2 (per-model JSONL shards).

    Verifies before every delete; safe to re-run if interrupted.
    """
    _setup_logging()
    cfg = _load(config_path)
    paths = runner_mod.RunPaths.for_root(_root() / "results")
    result = migrate_mod.migrate(paths, corpus_dir=cfg.corpus.path)
    typer.echo(f"responses migrated to shards: {result.responses_migrated} ({result.responses_orphaned} without a calls.jsonl record)")
    typer.echo(f"response .txt kept (shard body mismatch): {result.responses_kept}")
    typer.echo(f"prompt files verified against corpus and deleted: {result.prompts_deleted}")
    typer.echo(f"calls.jsonl records rewritten to schema v2: {result.records_rewritten}")
    if result.backup_path:
        typer.echo(f"calls.jsonl backup: {result.backup_path}")
    if result.prompts_kept:
        typer.echo(
            f"WARNING: {len(result.prompts_kept)} prompt file(s) did not reconstruct "
            "byte-exact from the corpus and were kept in results/raw/prompts/",
            err=True,
        )


def _find_call_or_exit(paths: runner_mod.RunPaths, call_id: str) -> dict:
    rec = find_call(paths.calls_jsonl, call_id)
    if rec is None:
        typer.echo(f"call_id not found in {paths.calls_jsonl}: {call_id}", err=True)
        raise typer.Exit(1)
    return rec


@app.command("show-prompt")
def show_prompt_cmd(
    call_id: str = typer.Argument(..., help="call_id from calls.jsonl"),
    config_path: Path = typer.Option(Path("benchmark.toml"), "--config"),
    snapshot: Optional[Path] = typer.Option(
        None, "--snapshot",
        help="System-prompt file the run used; needed for prompt_hash verification when the run was not on the live prompt.",
    ),
) -> None:
    """Reconstruct the exact user prompt for a call from the corpus and verify it against prompt_hash.

    Prompts are not stored on disk (schema v2); this rebuilds them
    deterministically from windows.json + metadata and proves fidelity by
    recomputing the hash recorded at call time.
    """
    cfg = _load(config_path)
    paths = runner_mod.RunPaths.for_root(_root() / "results")
    rec = _find_call_or_exit(paths, call_id)
    try:
        user_prompt = runner_mod.reconstruct_user_prompt(rec, corpus_dir=cfg.corpus.path)
    except Exception as e:
        typer.echo(f"error reconstructing prompt: {e}", err=True)
        raise typer.Exit(1)
    system_prompt, prompt_source = _resolve_prompt(snapshot)
    recomputed = hash_prompt(
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        model=rec["model"],
        temperature=float(rec["temperature"]),
    )
    typer.echo(user_prompt)
    if recomputed == rec["prompt_hash"]:
        typer.echo(f"prompt_hash verified ({recomputed}, system prompt: {prompt_source})", err=True)
    else:
        typer.echo(
            f"prompt_hash MISMATCH: stored={rec['prompt_hash']} recomputed={recomputed} "
            f"(system prompt: {prompt_source}). The system prompt or windows.json "
            "changed since this call ran; retry with the --snapshot the run used.",
            err=True,
        )
        raise typer.Exit(3)


@app.command("show-response")
def show_response_cmd(
    call_id: str = typer.Argument(..., help="call_id from calls.jsonl"),
) -> None:
    """Print the raw LLM response body for a call from its per-model shard."""
    paths = runner_mod.RunPaths.for_root(_root() / "results")
    rec = _find_call_or_exit(paths, call_id)
    body = read_response(paths.responses_dir, rec["model"], call_id)
    if body is None:
        typer.echo(f"no response body for {call_id} in {paths.responses_dir}", err=True)
        raise typer.Exit(1)
    typer.echo(body)


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


def _preview(cfg, episodes, *, paths, system_prompt):
    hashes = precompute_prompt_hashes(cfg, episodes, system_prompt=system_prompt)
    completed, _ = scan_calls(paths.calls_jsonl)
    units, skipped = build_work_list(cfg, episodes, completed=completed, prompt_hashes=hashes)
    return units, skipped


if __name__ == "__main__":
    app()
