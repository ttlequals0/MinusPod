"""Typer CLI for the MinusPod LLM benchmark."""
from __future__ import annotations

import asyncio
import logging
import shutil
from datetime import datetime, timezone
from pathlib import Path

import typer
from dotenv import load_dotenv

# Load benchmarks/llm/.env so MINUSPOD_PASSWORD and provider API keys are available
# regardless of where the user invokes `benchmark` from. Shell-exported vars still win.
load_dotenv(dotenv_path=Path(__file__).resolve().parents[2] / ".env", override=False)

from . import auth, capture as capture_mod, corpus as corpus_mod, jev, migrate as migrate_mod, parsing, pricing, report as report_mod, runner as runner_mod
from .config import BenchmarkConfig, load as load_config
from .runner import build_work_list, precompute_prompt_hashes
from .storage import find_call, hash_prompt, read_response, scan_calls

app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    help="Offline LLM ad-detection benchmark for MinusPod.",
)

ADDRESSING_MODES = ("timestamps", "segment_ids")


def _validate_addressing_mode(mode: str) -> None:
    if mode not in ADDRESSING_MODES:
        typer.echo(f"error: --addressing-mode must be one of {ADDRESSING_MODES}, got {mode!r}", err=True)
        raise typer.Exit(2)


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
) -> None:
    """Auto-fill all gaps in calls.jsonl, then regenerate report."""
    _setup_logging()
    _validate_addressing_mode(addressing_mode)
    cfg = _load(config_path)
    system_prompt, prompt_source = _resolve_prompt(snapshot)
    system_prompt = _with_id_mode_section(system_prompt, addressing_mode)
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
        units, skipped = _preview(
            cfg, episodes, paths=paths, system_prompt=system_prompt,
            include_errored=retry_errors, addressing_mode=addressing_mode,
        )
        typer.echo(f"dry-run: {len(units)} calls would execute, {skipped} skipped (already done)")
        raise typer.Exit(0)

    stats = asyncio.run(runner_mod.run(
        cfg, episodes, paths=paths, pricing_snapshot=snap, system_prompt=system_prompt,
        include_errored=retry_errors, addressing_mode=addressing_mode,
    ))
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
        pricing_snapshot=snap,
        output_path=output,
        assets_dir=assets,
        prompt_source=prompt_source,
        addressing_mode=addressing_mode,
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
        help="Regenerate the report from only the calls.jsonl rows recorded under this "
        "addressing mode (records without the field are 'timestamps'). A report never "
        "mixes modes; run this twice to get both sides of an A/B.",
    ),
) -> None:
    """Regenerate results/report.md from existing calls.jsonl."""
    _setup_logging()
    _validate_addressing_mode(addressing_mode)
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
        pricing_snapshot=snap,
        output_path=output,
        assets_dir=assets,
        prompt_source=prompt_source,
        addressing_mode=addressing_mode,
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
    snapshot: Path | None = typer.Option(
        None, "--snapshot",
        help="System-prompt file the run used; needed for prompt_hash verification when the run was not on the live prompt.",
    ),
    addressing_mode: str | None = typer.Option(
        None, "--addressing-mode",
        help="Must match the call record's stored addressing_mode if given; omit to trust "
        "the record (records without the field are 'timestamps').",
    ),
) -> None:
    """Reconstruct the exact user prompt for a call from the corpus and verify it against prompt_hash.

    Prompts are not stored on disk (schema v2); this rebuilds them
    deterministically from windows.json + metadata and proves fidelity by
    recomputing the hash recorded at call time. The addressing mode used is
    the one stored on the call record, not a global default.
    """
    cfg = _load(config_path)
    paths = runner_mod.RunPaths.for_root(_root() / "results")
    rec = _find_call_or_exit(paths, call_id)
    record_mode = rec.get("addressing_mode", "timestamps")
    if addressing_mode is not None:
        _validate_addressing_mode(addressing_mode)
        if addressing_mode != record_mode:
            typer.echo(
                f"error: --addressing-mode {addressing_mode} does not match this call's "
                f"stored addressing_mode {record_mode}", err=True,
            )
            raise typer.Exit(1)
    try:
        user_prompt = runner_mod.reconstruct_user_prompt(rec, corpus_dir=cfg.corpus.path)
    except Exception as e:
        typer.echo(f"error reconstructing prompt: {e}", err=True)
        raise typer.Exit(1) from e
    system_prompt, prompt_source = _resolve_prompt(snapshot)
    system_prompt = _with_id_mode_section(system_prompt, record_mode)
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


@app.command("rotate-raw")
def rotate_raw_cmd(
    keep: bool = typer.Option(False, "--keep", help="Copy instead of move, leaving results/raw in place."),
) -> None:
    """Move results/raw to results/archive/<date>/raw/ so the next sweep starts clean.

    calls.jsonl is append-only, so without rotation it accumulates every campaign
    ever run. That is unbounded growth and a correctness hazard: the report dedups
    per work unit without consulting prompt_hash, so a partially-completed sweep
    silently blends its rows with the previous campaign's.
    """
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    src = _root() / "results" / "raw"
    if not src.is_dir() or not (src / "calls.jsonl").is_file():
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
    typer.echo(f"rotated {size_mb:.0f} MB to {dst}" + (" (original kept)" if keep else ""))


@app.command("jev-spike")
def jev_spike_cmd(
    oracle: str = typer.Option(
        "overlap", "--oracle",
        help="Score a perfect judge instead of calling the API: "
             "'overlap', 'majority', or 'off' for live calls."),
    enter: float = typer.Option(jev.ENTER_THRESHOLD, "--enter"),
    stay: float = typer.Option(jev.STAY_THRESHOLD, "--stay"),
    passes: int = typer.Option(
        1, "--passes",
        help="Independent draws per window, averaged. Only meaningful live."),
    confirm: bool = typer.Option(
        False, "--confirm",
        help="Pass B: treat Pass A as a recall-first candidate generator and "
             "confirm each span with a second span-level request."),
    guidance: str = typer.Option(
        "full", "--guidance",
        help="'basic' (one paragraph) or 'full' (MinusPod's whole rulebook)."),
    metadata: bool = typer.Option(
        True, "--metadata/--no-metadata",
        help="Include podcast name, episode title, and synopsis in state."),
    corpus_dir: Path | None = typer.Option(None, "--corpus-dir"),
) -> None:
    """Evaluate per-segment Jev answers or an offline overlap oracle."""
    _setup_logging()
    root = corpus_dir or (_root() / "data" / "corpus")
    ep_ids = corpus_mod.list_episodes(root)
    if not ep_ids:
        typer.echo(f"no corpus episodes under {root}", err=True)
        raise typer.Exit(1)

    if guidance not in ("basic", "full"):
        typer.echo("--guidance must be 'basic' or 'full'", err=True)
        raise typer.Exit(2)
    guidance_text = jev.GUIDANCE if guidance == "basic" else jev.GUIDANCE_FULL
    episode_metadata = (lambda ep: ep.metadata) if metadata else (lambda ep: None)

    cache = None
    api_key = None
    if oracle == "off":
        cache = jev.ProbabilityCache(_root() / "results" / "raw" / "jev_cache.json")
        api_key = jev.api_key_from_env()

    scores: list[jev.EpisodeScore] = []
    est_tokens = 0
    for ep_id in ep_ids:
        episode = corpus_mod.load_episode(root / ep_id)
        windows = jev.episode_windows(episode)
        est_tokens += jev.estimate_input_tokens(windows)

        if oracle == "off":
            def source(segs, _cache=cache, _key=api_key, _n=passes,
                       _g=guidance_text, _m=episode_metadata(episode)):
                return jev.aggregate_passes([
                    _cache.get_or_call(
                        segs, api_key=_key, guidance=_g, metadata=_m,
                        uid=None if _n == 1 else f"pass-{i}")
                    for i in range(_n)
                ])
        else:
            def source(segs, _policy=oracle, _ep=episode):
                return jev.WindowResult(
                    probabilities=jev.oracle_probabilities(
                        segs, _ep.truth.ads, policy=_policy))

        confirm_fn = None
        if confirm:
            if cache is None:
                typer.echo("--confirm needs live probabilities; drop --oracle.", err=True)
                raise typer.Exit(2)

            def confirm_fn(ads, all_segs, _cache=cache, _key=api_key):
                return jev.confirm_spans(
                    ads, all_segs,
                    lambda payload: _cache.nouls(
                        payload, api_key=_key)["probabilities"],
                    policy=jev.ConfirmPolicy())

        try:
            scores.append(jev.score_episode(
                episode, windows, source, enter=enter, stay=stay,
                confirm=confirm_fn))
        except KeyError as e:
            if cache:
                cache.save()
            typer.echo(f"{e}\nSet TYPESAFE_API_KEY to populate the cache.", err=True)
            raise typer.Exit(1) from e

    if cache:
        cache.save()
        typer.echo(f"cache: {cache.hits} hit, {cache.misses} fetched "
                   f"-> {cache.path.relative_to(_root())}")

    _echo_jev_table(scores, est_tokens=est_tokens, oracle=oracle,
                    variant=f"guidance={guidance} metadata={metadata}")


@app.command("jev-cv")
def jev_cv_cmd(
    fold_size: int = typer.Option(2, "--fold-size"),
    guidance: str = typer.Option("full", "--guidance"),
    metadata: bool = typer.Option(True, "--metadata/--no-metadata"),
    corpus_dir: Path | None = typer.Option(None, "--corpus-dir"),
) -> None:
    """Cross-validate detection thresholds from cached answers."""
    _setup_logging()
    if fold_size < 1:
        raise typer.BadParameter("fold-size must be positive")
    root = corpus_dir or (_root() / "data" / "corpus")
    cache = jev.ProbabilityCache(_root() / "results" / "raw" / "jev_cache.json")
    guidance_text = jev.GUIDANCE if guidance == "basic" else jev.GUIDANCE_FULL

    episodes, windows = {}, {}
    for ep_id in corpus_mod.list_episodes(root):
        ep = corpus_mod.load_episode(root / ep_id)
        episodes[ep_id] = ep
        windows[ep_id] = jev.episode_windows(ep)

    def score_fn(ep_id, enter, stay):
        ep = episodes[ep_id]
        meta = ep.metadata if metadata else None
        return jev.score_episode(
            ep, windows[ep_id],
            lambda s: cache.get_or_call(
                s, api_key=None, guidance=guidance_text, metadata=meta),
            enter=enter, stay=stay)

    ad_ids = [e for e in episodes if not episodes[e].truth.is_no_ad_episode]
    try:
        folds = jev.cross_validate(ad_ids, score_fn, fold_size=fold_size)
    except KeyError as e:
        typer.echo(f"{e}\nRun `benchmark jev-spike --oracle off` first.", err=True)
        raise typer.Exit(1) from e

    typer.echo(f"\n{len(folds)} folds of {fold_size}, guidance={guidance} "
               f"metadata={metadata}")
    typer.echo(f"{'held out':44}{'enter':>6}{'stay':>6}{'train':>8}"
               f"{'test':>8}{'fixed':>8}")
    for f in folds:
        held = ", ".join(h.replace("ep-", "")[:18] for h in f.held_out)
        typer.echo(f"{held[:44]:44}{f.enter:6.2f}{f.stay:6.2f}"
                   f"{f.train_f05:8.3f}{f.test_f05:8.3f}{f.fixed_f05:8.3f}")

    n = len(folds)
    if not n:
        typer.echo("no ad-bearing corpus episodes available", err=True)
        raise typer.Exit(1)
    mean = lambda k: sum(getattr(f, k) for f in folds) / n  # noqa: E731
    typer.echo(f"\n{'MEAN':44}{'':12}{mean('train_f05'):8.3f}"
               f"{mean('test_f05'):8.3f}{mean('fixed_f05'):8.3f}")
    typer.echo(f"\nin-sample minus held-out: {mean('train_f05') - mean('test_f05'):+.3f}"
               "   (the optimism in a tuned-on-everything number)")
    typer.echo(f"per-fold tuning vs shipped defaults on the same episodes: "
               f"{mean('test_f05') - mean('fixed_f05'):+.3f}")


@app.command("combined-report")
def combined_report_cmd(
    config_path: Path = typer.Option(Path("benchmark.toml"), "--config"),
    snapshot: Path | None = typer.Option(
        None, "--snapshot", help="Same prompt snapshot label as `benchmark report`."),
    addressing_mode: str = typer.Option("timestamps", "--addressing-mode"),
    output: Path | None = typer.Option(None, "--output"),
    jev_passes: int = typer.Option(
        1, "--jev-passes",
        help="Repeats the jev cache was filled with; must match the jev-spike run."),
) -> None:
    """Full report-style Markdown with Jev rows merged into every table."""
    _setup_logging()
    cfg = _load(config_path)
    _, prompt_source = _resolve_prompt(snapshot)
    episodes = [corpus_mod.load_episode(cfg.corpus.path / e)
                for e in corpus_mod.list_episodes(cfg.corpus.path)]
    paths = runner_mod.RunPaths.for_root(_root() / "results")
    snap = pricing.latest_snapshot(_root() / "data" / "pricing_snapshots")
    if snap is None:
        typer.echo("combined-report needs a cached pricing snapshot", err=True)
        raise typer.Exit(1)
    out = output or (_root() / "results" / "report-combined.md")
    report_mod.render(
        cfg=cfg, episodes=episodes, calls_path=paths.calls_jsonl,
        pricing_snapshot=snap,
        output_path=out, assets_dir=_root() / "results" / "report_assets",
        prompt_source=prompt_source, addressing_mode=addressing_mode, include_jev=True,
        jev_passes=jev_passes)
    typer.echo(f"combined report written: {out}")


def _echo_jev_table(scores, *, est_tokens: int, oracle: str,
                    variant: str = "") -> None:
    ad_eps = [s for s in scores if not s.is_no_ad]
    typer.echo(f"\nmode: {'oracle=' + oracle if oracle != 'off' else 'live jev'}   "
               f"episodes: {len(scores)}   {variant}")
    typer.echo(f"{'episode':38}{'F1':>7}{'F0.5':>7}{'prec':>7}{'rec':>7}"
               f"{'startMAE':>10}{'endMAE':>9}")
    for s in sorted(ad_eps, key=lambda s: s.f1):
        smae = f"{s.start_mae:.1f}" if s.start_mae is not None else "-"
        emae = f"{s.end_mae:.1f}" if s.end_mae is not None else "-"
        typer.echo(f"{s.ep_id[:38]:38}{s.f1:7.3f}{s.f05:7.3f}"
                   f"{s.precision:7.3f}{s.recall:7.3f}{smae:>10}{emae:>9}")

    if ad_eps:
        mean = lambda xs: sum(xs) / len(xs)
        typer.echo(f"\n{'MEAN':38}{mean([s.f1 for s in ad_eps]):7.3f}"
                   f"{mean([s.f05 for s in ad_eps]):7.3f}"
                   f"{mean([s.precision for s in ad_eps]):7.3f}"
                   f"{mean([s.recall for s in ad_eps]):7.3f}")

    for s in scores:
        if s.is_no_ad:
            verdict = "PASS" if s.no_ad_passed else f"FAIL ({s.no_ad_fps} FP)"
            typer.echo(f"no-ad control {s.ep_id[:30]:32} {verdict}")

    cost = est_tokens * jev.INPUT_COST_PER_MTOK / 1e6
    typer.echo(f"\nestimated input tokens/pass (all episodes): {est_tokens:,}")
    typer.echo(f"estimated cost/pass (all episodes): ${cost:.4f}")



def _preview(cfg, episodes, *, paths, system_prompt, include_errored=False, addressing_mode="timestamps"):
    hashes = precompute_prompt_hashes(cfg, episodes, system_prompt=system_prompt, addressing_mode=addressing_mode)
    completed, err_keys = scan_calls(paths.calls_jsonl)
    units, skipped = build_work_list(
        cfg, episodes, completed=completed, prompt_hashes=hashes,
        include_errored=include_errored, error_keys=err_keys,
    )
    return units, skipped


if __name__ == "__main__":
    app()
