"""Typer CLI for the MinusPod LLM benchmark."""
from __future__ import annotations

import asyncio
import dataclasses
import hashlib
import logging
import shutil
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

import typer
from dotenv import load_dotenv

# Load benchmarks/llm/.env so MINUSPOD_PASSWORD and provider API keys are available
# regardless of where the user invokes `benchmark` from. Shell-exported vars still win.
load_dotenv(dotenv_path=Path(__file__).resolve().parents[2] / ".env", override=False)

from . import auth, capture as capture_mod, corpus as corpus_mod, migrate as migrate_mod, parsing, pricing, report as report_mod, runner as runner_mod, variants
from .report import compare as compare_mod
from .config import BenchmarkConfig, load as load_config
from .runner import build_work_list, precompute_prompt_hashes
from .storage import (
    StorageError,
    append_call,
    call_shard,
    calls_dir,
    find_call,
    hash_prompt,
    read_calls,
    read_jsonl,
    read_response,
    scan_calls,
)

app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    help="Offline LLM ad-detection benchmark for MinusPod.",
)

ADDRESSING_MODES = variants.ADDRESSING_MODES


def _validate_addressing_mode(mode: str) -> None:
    if mode not in ADDRESSING_MODES:
        typer.echo(f"error: --addressing-mode must be one of {ADDRESSING_MODES}, got {mode!r}", err=True)
        raise typer.Exit(2)


def _validate_prompt_variant(name: str) -> None:
    try:
        variants.validate_variant(name)
    except ValueError as e:
        typer.echo(f"error: {e}", err=True)
        raise typer.Exit(2) from e


def _require_known(values: list[str], known: set[str], flag: str) -> set[str]:
    """Exit 2 with an "unknown value(s)" error if values has anything outside known."""
    values_set = set(values)
    unknown = sorted(values_set - known)
    if unknown:
        typer.echo(f"error: unknown {flag} value(s): {', '.join(unknown)}", err=True)
        raise typer.Exit(2)
    return values_set


def _with_id_mode_section(system_prompt: str, addressing_mode: str) -> str:
    """Append SEGMENT_ID_SYSTEM_SECTION after the live/snapshot prompt is
    resolved, so a frozen snapshot file stays mode-agnostic."""
    if addressing_mode == "segment_ids":
        return system_prompt + parsing.SEGMENT_ID_SYSTEM_SECTION
    return system_prompt


def _setup_logging() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")


def _load(config_path: Path) -> BenchmarkConfig:
    try:
        return load_config(config_path)
    except Exception as e:
        typer.echo(f"error loading {config_path}: {e}", err=True)
        raise typer.Exit(1) from e


def _root() -> Path:
    return Path(__file__).resolve().parents[2]


def _resolve_prompt(snapshot: Path | None) -> tuple[str, str]:
    try:
        return parsing.resolve_system_prompt(snapshot)
    except (FileNotFoundError, ValueError) as e:
        typer.echo(f"error: {e}", err=True)
        raise typer.Exit(1) from e


def _resolve_prompt_for_variant(prompt_variant: str, snapshot: Path | None, addressing_mode: str) -> tuple[str, str]:
    """Segmentation uses its own frozen prompt and is mode-agnostic; --snapshot
    doesn't apply to it. Detection keeps the existing live/snapshot + id-mode path."""
    if prompt_variant == "segmentation":
        if snapshot is not None:
            typer.echo("error: segmentation variant uses its frozen prompt", err=True)
            raise typer.Exit(2)
        system_prompt = variants.segmentation_system_prompt()
        sha8 = hashlib.sha256(system_prompt.encode()).hexdigest()[:8]
        return system_prompt, f"segmentation-v1.txt (sha256:{sha8})"
    system_prompt, prompt_source = _resolve_prompt(snapshot)
    return _with_id_mode_section(system_prompt, addressing_mode), prompt_source


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
        typer.echo("regenerate-windows requires --force (invalidates prior call records for this episode).")
        raise typer.Exit(2)
    n = capture_mod.regenerate_windows(ep_id, corpus_dir=cfg.corpus.path)
    typer.echo(f"regenerated {n} windows for {ep_id}")


@app.command("list-episodes")
def list_episodes_cmd(
    podcast_slug: str | None = typer.Option(None, "--podcast-slug"),
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
    snapshot: Path | None = typer.Option(
        None, "--snapshot",
        help="Frozen system-prompt file to use instead of the live prompt (decouples the corpus from SEED_SPONSORS edits).",
    ),
    addressing_mode: str = typer.Option(
        "timestamps", "--addressing-mode",
        help="Prompt addressing scheme: 'timestamps' (default) or 'segment_ids' "
        "(experimental; transcript lines are numbered [id] instead of timestamped, "
        "and the model reports start_id/end_id). Lets an A/B run be a single command "
        "per side: `benchmark run` then `benchmark run --addressing-mode segment_ids`.",
    ),
    prompt_variant: str = typer.Option(
        variants.DEFAULT_VARIANT, "--prompt-variant",
        help="System prompt variant: 'detection' (default, live/snapshot prompt) or "
        "'segmentation' (frozen prompt; --snapshot not allowed with it).",
    ),
    model: list[str] = typer.Option(
        [], "--model", help="Only run this model id (exact match); repeatable. Unknown ids exit 2.",
    ),
    episode: list[str] = typer.Option(
        [], "--episode", help="Only run this episode directory name (exact match); repeatable. Unknown names exit 2.",
    ),
) -> None:
    """Auto-fill all gaps in the call records, then regenerate report."""
    _setup_logging()
    _validate_addressing_mode(addressing_mode)
    _validate_prompt_variant(prompt_variant)
    cfg = _load(config_path)
    system_prompt, prompt_source = _resolve_prompt_for_variant(prompt_variant, snapshot, addressing_mode)
    episodes = [corpus_mod.load_episode(cfg.corpus.path / e) for e in corpus_mod.list_episodes(cfg.corpus.path)]
    if not episodes:
        typer.echo("no corpus episodes; run `benchmark capture` first", err=True)
        raise typer.Exit(1)

    # --model/--episode narrow only the work list the runner executes; cfg and
    # episodes stay the full corpus so the report render and derive_episode_results
    # below don't drop other episodes or lose track of deprecated models.
    run_cfg = cfg
    run_episodes = episodes

    if model:
        model_set = _require_known(model, {m.id for m in cfg.models}, "--model")
        run_cfg = dataclasses.replace(cfg, models=[m for m in cfg.models if m.id in model_set])

    if episode:
        episode_set = _require_known(episode, {e.ep_id for e in episodes}, "--episode")
        run_episodes = [e for e in episodes if e.ep_id in episode_set]

    paths = runner_mod.RunPaths.for_root(_root() / "results")
    snapshots_dir = _root() / "data" / "pricing_snapshots"
    snap = pricing.latest_snapshot(snapshots_dir) or pricing.fetch_current()
    if pricing.latest_snapshot(snapshots_dir) is None:
        pricing.write_snapshot(snap, snapshots_dir)

    if force:
        typer.echo("WARNING: --force will reset existing calls; abort if unintended.")
        legacy = paths.raw / "calls.jsonl"
        if legacy.exists():
            legacy.unlink()
        if paths.calls_dir.exists():
            shutil.rmtree(paths.calls_dir)

    if dry_run:
        units, skipped = _preview(
            run_cfg, run_episodes, paths=paths, system_prompt=system_prompt,
            include_errored=retry_errors, addressing_mode=addressing_mode, prompt_variant=prompt_variant,
        )
        typer.echo(f"dry-run: {len(units)} calls would execute, {skipped} skipped (already done)")
        raise typer.Exit(0)

    stats = asyncio.run(runner_mod.run(
        run_cfg, run_episodes, paths=paths, pricing_snapshot=snap, system_prompt=system_prompt,
        include_errored=retry_errors, addressing_mode=addressing_mode, prompt_variant=prompt_variant,
        all_episodes=episodes,
    ))
    typer.echo(f"run complete: total={stats.total_units} skipped={stats.skipped} completed={stats.completed} errored={stats.errored}")

    if stats.errored and no_report_on_failure:
        typer.echo("skipping report regen (--no-report-on-failure)")
        return

    output, assets = report_mod.report_paths(_root() / "results", prompt_variant, addressing_mode)
    report_mod.render(
        cfg=cfg,
        episodes=episodes,
        raw_dir=paths.raw,
        pricing_snapshot=snap,
        output_path=output,
        assets_dir=assets,
        prompt_source=prompt_source,
        addressing_mode=addressing_mode,
        prompt_variant=prompt_variant,
    )
    typer.echo(f"report written: {output}")


@app.command()
def report(
    config_path: Path = typer.Option(Path("benchmark.toml"), "--config"),
    snapshot: Path | None = typer.Option(
        None, "--snapshot",
        help="Label the report with this prompt file; pass the same snapshot used for `run` so the footer matches the stored calls.",
    ),
    addressing_mode: str = typer.Option(
        "timestamps", "--addressing-mode",
        help="Regenerate the report from only the call records recorded under this "
        "addressing mode (records without the field are 'timestamps'). A report never "
        "mixes modes; run this twice to get both sides of an A/B.",
    ),
    prompt_variant: str = typer.Option(
        variants.DEFAULT_VARIANT, "--prompt-variant",
        help="System prompt variant the report's prompt_source label describes: "
        "'detection' (default, live/snapshot prompt) or 'segmentation' (frozen prompt).",
    ),
) -> None:
    """Regenerate results/report.md from the existing call records."""
    _setup_logging()
    _validate_addressing_mode(addressing_mode)
    _validate_prompt_variant(prompt_variant)
    cfg = _load(config_path)
    _, prompt_source = _resolve_prompt_for_variant(prompt_variant, snapshot, addressing_mode)
    episodes = [corpus_mod.load_episode(cfg.corpus.path / e) for e in corpus_mod.list_episodes(cfg.corpus.path)]
    paths = runner_mod.RunPaths.for_root(_root() / "results")
    snap = pricing.latest_snapshot(_root() / "data" / "pricing_snapshots") or pricing.fetch_current()
    output, assets = report_mod.report_paths(_root() / "results", prompt_variant, addressing_mode)
    report_mod.render(
        cfg=cfg,
        episodes=episodes,
        raw_dir=paths.raw,
        pricing_snapshot=snap,
        output_path=output,
        assets_dir=assets,
        prompt_source=prompt_source,
        addressing_mode=addressing_mode,
        prompt_variant=prompt_variant,
    )
    typer.echo(f"report written: {output}")


@app.command()
def compare(
    config_path: Path = typer.Option(Path("benchmark.toml"), "--config"),
) -> None:
    """Compare all (prompt variant, addressing mode) cells side by side in results/comparison.md."""
    _setup_logging()
    cfg = _load(config_path)
    episodes = [corpus_mod.load_episode(cfg.corpus.path / e) for e in corpus_mod.list_episodes(cfg.corpus.path)]
    paths = runner_mod.RunPaths.for_root(_root() / "results")
    snap = pricing.latest_snapshot(_root() / "data" / "pricing_snapshots") or pricing.fetch_current()
    output = _root() / "results" / "comparison.md"
    compare_mod.render(
        cfg=cfg,
        episodes=episodes,
        raw_dir=paths.raw,
        pricing_snapshot=snap,
        output_path=output,
    )
    typer.echo(f"comparison written: {output}")


@app.command()
def dump_prompt(
    output: Path = typer.Argument(..., help="File to write the current live system prompt to"),
    prompt_variant: str = typer.Option(
        variants.DEFAULT_VARIANT, "--prompt-variant",
        help="'detection' (default, dumps the live prompt) or 'segmentation' (dumps the frozen segmentation prompt).",
    ),
) -> None:
    """Freeze the current system prompt to a file for use with `run --snapshot`."""
    _validate_prompt_variant(prompt_variant)
    text = (
        variants.segmentation_system_prompt() if prompt_variant == "segmentation"
        else parsing.get_static_system_prompt()
    )
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


@dataclasses.dataclass
class _MigrateCallsResult:
    legacy_rows: int
    appended: int
    skipped: int
    total_shard_rows: int


def _migrate_calls(raw_dir: Path) -> _MigrateCallsResult:
    """Fold legacy calls.jsonl rows into their per-model call shards.

    Idempotent: a call_id already present in its shard is skipped rather than
    appended again, so a rerun after an interrupted prior pass is safe.
    """
    legacy_rows = list(read_jsonl(raw_dir / "calls.jsonl"))

    by_model: dict[str, list[dict]] = defaultdict(list)
    for rec in legacy_rows:
        by_model[rec["model"]].append(rec)

    appended = skipped = 0
    for model, recs in by_model.items():
        shard = call_shard(raw_dir, model)
        existing_ids = {r.get("call_id") for r in read_jsonl(shard)}
        for rec in recs:
            if rec.get("call_id") in existing_ids:
                skipped += 1
                continue
            append_call(raw_dir, rec)
            existing_ids.add(rec.get("call_id"))
            appended += 1

    all_ids = [
        r.get("call_id")
        for shard in sorted(calls_dir(raw_dir).glob("*.jsonl"))
        for r in read_jsonl(shard)
    ]
    duplicates = [cid for cid, n in Counter(all_ids).items() if n > 1]
    if duplicates:
        raise StorageError(f"{len(duplicates)} call_id(s) duplicated across shards; aborting before delete")

    return _MigrateCallsResult(
        legacy_rows=len(legacy_rows), appended=appended, skipped=skipped, total_shard_rows=len(all_ids),
    )


@app.command("migrate-calls")
def migrate_calls_cmd() -> None:
    """One-time migration of results/raw/calls.jsonl into per-model shards (schema v3).

    Idempotent (skips call_ids already present in their shard); verifies no
    call_id is duplicated across shards before deleting calls.jsonl.
    """
    _setup_logging()
    paths = runner_mod.RunPaths.for_root(_root() / "results")
    legacy = paths.raw / "calls.jsonl"
    if not legacy.is_file():
        typer.echo(f"no {legacy} to migrate", err=True)
        raise typer.Exit(1)
    try:
        result = _migrate_calls(paths.raw)
    except StorageError as e:
        typer.echo(f"error: {e}", err=True)
        raise typer.Exit(1) from e
    legacy.unlink()
    typer.echo(f"legacy rows: {result.legacy_rows}")
    typer.echo(f"appended to shards: {result.appended}")
    typer.echo(f"already present (skipped): {result.skipped}")
    typer.echo(f"total rows across shards: {result.total_shard_rows}")


def _find_call_or_exit(paths: runner_mod.RunPaths, call_id: str) -> dict:
    rec = find_call(paths.raw, call_id)
    if rec is None:
        typer.echo(f"call_id not found under {paths.calls_dir}: {call_id}", err=True)
        raise typer.Exit(1)
    return rec


@app.command("show-prompt")
def show_prompt_cmd(
    call_id: str = typer.Argument(..., help="call_id from the call records"),
    config_path: Path = typer.Option(Path("benchmark.toml"), "--config"),
    snapshot: Path | None = typer.Option(
        None, "--snapshot",
        help="System-prompt file the run used; needed for prompt_hash verification when the run was not on the live prompt.",
    ),
    addressing_mode: str | None = typer.Option(
        None, "--addressing-mode",
        help="Must match the call record's stored addressing_mode if given; omit to trust "
        "the record (records without the field are 'timestamps').",
    ),
    prompt_variant: str | None = typer.Option(
        None, "--prompt-variant",
        help="Must match the call record's stored prompt_variant if given; omit to trust "
        "the record (records without the field are 'detection').",
    ),
) -> None:
    """Reconstruct the exact user prompt for a call from the corpus and verify it against prompt_hash.

    Prompts are not stored on disk (schema v2); this rebuilds them
    deterministically from windows.json + metadata and proves fidelity by
    recomputing the hash recorded at call time. The addressing mode and prompt
    variant used are the ones stored on the call record, not a global default.
    """
    cfg = _load(config_path)
    paths = runner_mod.RunPaths.for_root(_root() / "results")
    rec = _find_call_or_exit(paths, call_id)
    record_mode = rec.get("addressing_mode", "timestamps")
    record_variant = rec.get("prompt_variant", "detection")
    if addressing_mode is not None:
        _validate_addressing_mode(addressing_mode)
        if addressing_mode != record_mode:
            typer.echo(
                f"error: --addressing-mode {addressing_mode} does not match this call's "
                f"stored addressing_mode {record_mode}", err=True,
            )
            raise typer.Exit(1)
    if prompt_variant is not None:
        _validate_prompt_variant(prompt_variant)
        if prompt_variant != record_variant:
            typer.echo(
                f"error: --prompt-variant {prompt_variant} does not match this call's "
                f"stored prompt_variant {record_variant}", err=True,
            )
            raise typer.Exit(1)
    try:
        user_prompt = runner_mod.reconstruct_user_prompt(rec, corpus_dir=cfg.corpus.path)
    except Exception as e:
        typer.echo(f"error reconstructing prompt: {e}", err=True)
        raise typer.Exit(1) from e
    system_prompt, prompt_source = _resolve_prompt_for_variant(record_variant, snapshot, record_mode)
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
    call_id: str = typer.Argument(..., help="call_id from the call records"),
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


@app.command("rotate-raw")
def rotate_raw_cmd(
    keep: bool = typer.Option(False, "--keep", help="Copy instead of move, leaving results/raw in place."),
) -> None:
    """Move results/raw to results/archive/<date>/raw/ so the next sweep starts clean.

    Call records are append-only (legacy calls.jsonl or calls/ shards), so
    without rotation they accumulate every campaign ever run. That is unbounded
    growth and a correctness hazard: the report dedups per work unit without
    consulting prompt_hash, so a partially-completed sweep silently blends its
    rows with the previous campaign's.
    """
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    src = _root() / "results" / "raw"
    has_calls = (src / "calls.jsonl").is_file() or any((src / "calls").glob("*.jsonl"))
    if not src.is_dir() or not has_calls:
        typer.echo("no results/raw to rotate", err=True)
        raise typer.Exit(1)
    dst = _root() / "results" / "archive" / today / "raw"
    if dst.exists():
        typer.echo(f"{dst} already exists; refusing to overwrite", err=True)
        raise typer.Exit(1)
    dst.parent.mkdir(parents=True, exist_ok=True)
    size_mb = sum(f.stat().st_size for f in src.rglob("*") if f.is_file()) / 1048576
    if keep:
        shutil.copytree(src, dst)
    else:
        shutil.move(str(src), str(dst))
        (_root() / "results" / "raw" / "responses").mkdir(parents=True, exist_ok=True)
        (_root() / "results" / "raw" / "calls").mkdir(parents=True, exist_ok=True)
    typer.echo(f"rotated {size_mb:.0f} MB to {dst}" + (" (original kept)" if keep else ""))


def _preview(cfg, episodes, *, paths, system_prompt, include_errored=False, addressing_mode="timestamps", prompt_variant="detection"):
    hashes = precompute_prompt_hashes(
        cfg, episodes, system_prompt=system_prompt, addressing_mode=addressing_mode, prompt_variant=prompt_variant,
    )
    completed, err_keys = scan_calls(read_calls(paths.raw))
    units, skipped = build_work_list(
        cfg, episodes, completed=completed, prompt_hashes=hashes,
        include_errored=include_errored, error_keys=err_keys,
    )
    return units, skipped


if __name__ == "__main__":
    app()
