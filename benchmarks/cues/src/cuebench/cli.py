"""Typer CLI for the MinusPod cue-template eval harness."""
from __future__ import annotations

import json
import logging
import sys
from pathlib import Path
from typing import List, Optional

import typer

from audio_analysis.cue_features import deserialize_mfcc

from . import cross_episode_eval as xep_mod
from . import feeds as feeds_mod
from . import report as report_mod
from . import scan_eval as scan_eval_mod
from . import sweep as sweep_mod
from . import templates as tpl_mod

app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    help="Offline cue-template eval harness for MinusPod.",
)


def _setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )


def _load_templates(template_paths: List[Path]) -> list:
    rows = tpl_mod.load_templates(template_paths)
    if not rows:
        typer.echo("error: no usable templates loaded", err=True)
        raise typer.Exit(1)
    return rows


def _resolve_audio(
    audio: Optional[List[Path]],
    rss: Optional[str],
    max_episodes: int,
) -> List[Path]:
    if not audio and not rss:
        typer.echo(
            "error: provide --audio FILE... or --rss URL", err=True
        )
        raise typer.Exit(1)
    try:
        if rss:
            return feeds_mod.fetch(rss, max_episodes=max_episodes, audio_files=audio)
        return feeds_mod.fetch("", max_episodes=max_episodes, audio_files=audio)
    except Exception as e:
        typer.echo(f"error resolving audio: {e}", err=True)
        raise typer.Exit(1)


@app.command()
def load_template(
    source: Path = typer.Argument(..., help="Export zip or directory (cue.flac + template.json)"),
) -> None:
    """Validate and display a cue template export."""
    _setup_logging()
    try:
        row = tpl_mod.load_template(source)
    except Exception as e:
        typer.echo(f"error: {e}", err=True)
        raise typer.Exit(1)
    typer.echo(f"label:     {row['label']}")
    typer.echo(f"cue_type:  {row['cue_type']}")
    typer.echo(f"duration:  {row['duration_s']:.3f} s")
    typer.echo(f"n_coeffs:  {row['n_coeffs']}")
    typer.echo(f"mfcc_frames: {_mfcc_frame_count(row)}")
    typer.echo("ok")


@app.command()
def sweep(
    template: List[Path] = typer.Option(
        ..., "--template", help="Template export zip or dir (repeatable)"
    ),
    audio: Optional[List[Path]] = typer.Option(
        None, "--audio", help="Local audio file (repeatable; bypasses RSS)"
    ),
    rss: Optional[str] = typer.Option(
        None, "--rss", help="Podcast RSS URL to fetch episodes from"
    ),
    max_episodes: int = typer.Option(5, "--max-episodes", help="Max episodes to download"),
    formant_ab: bool = typer.Option(
        False, "--formant-ab", help="Run 0 dB vs 12 dB formant attenuation side by side"
    ),
    confirm: bool = typer.Option(
        False, "--confirm",
        help="Re-run detect at suggested threshold and report true match counts"
    ),
    output_dir: Optional[Path] = typer.Option(
        None, "--output-dir", help="Directory for report.md and report.json (default: results/)"
    ),
) -> None:
    """Sweep templates across episodes and write results/report.md + report.json."""
    _setup_logging()
    rows = _load_templates(list(template))
    paths = _resolve_audio(list(audio) if audio else None, rss, max_episodes)
    typer.echo(
        f"sweep: {len(rows)} template(s), {len(paths)} episode(s)"
        f"{' + formant A/B' if formant_ab else ''}"
        f"{' + confirm' if confirm else ''}"
    )
    result = sweep_mod.run(rows, paths, formant_ab=formant_ab, confirm=confirm)
    md_path, json_path = report_mod.write(result, output_dir=output_dir)
    typer.echo(f"report: {md_path}")
    typer.echo(f"json:   {json_path}")
    _print_summary(result)


@app.command()
def scan(
    template: List[Path] = typer.Option(
        ..., "--template", help="Template export zip or dir (repeatable)"
    ),
    audio: Optional[List[Path]] = typer.Option(
        None, "--audio", help="Local audio file (repeatable; bypasses RSS)"
    ),
    rss: Optional[str] = typer.Option(
        None, "--rss", help="Podcast RSS URL to fetch episodes from"
    ),
    max_episodes: int = typer.Option(5, "--max-episodes"),
    output_dir: Optional[Path] = typer.Option(None, "--output-dir"),
    cross_episode: bool = typer.Option(False, "--cross-episode", help="Run cross-episode intro/outro discovery"),
    intro_max: float = typer.Option(60.0, "--intro-max", help="Max intro duration (seconds)"),
    outro_max: float = typer.Option(60.0, "--outro-max", help="Max outro duration (seconds)"),
) -> None:
    """Run Chromaprint discovery eval against sweep ground truth."""
    _setup_logging()
    rows = _load_templates(list(template))
    paths = _resolve_audio(list(audio) if audio else None, rss, max_episodes)
    sweep_result = sweep_mod.run(rows, paths)
    template_durations = {str(r["id"]): r["duration_s"] for r in rows}
    scan_result = scan_eval_mod.run(paths, sweep_result["per_template"], template_durations)
    xep_result = (
        xep_mod.run(paths, intro_max_duration=intro_max, outro_max_duration=outro_max)
        if cross_episode
        else None
    )
    md_path, json_path = report_mod.write(
        sweep_result, scan_result=scan_result, xep_result=xep_result, output_dir=output_dir
    )
    typer.echo(f"report: {md_path}")
    typer.echo(f"json:   {json_path}")
    if not scan_result.get("available"):
        typer.echo(f"scan skipped: {scan_result.get('skip_reason')}")
    if xep_result is not None and xep_result.get("skip_reason"):
        typer.echo(f"cross-episode skipped: {xep_result.get('skip_reason')}")


@app.command()
def fetch(
    rss: str = typer.Option(..., "--rss", help="Podcast RSS URL to fetch episodes from"),
    max_episodes: int = typer.Option(5, "--max-episodes", help="Max episodes to download"),
) -> None:
    """Pre-download episodes into the cache without running a sweep."""
    _setup_logging()
    try:
        paths = feeds_mod.fetch(rss, max_episodes=max_episodes, audio_files=None)
    except Exception as e:
        typer.echo(f"error fetching episodes: {e}", err=True)
        raise typer.Exit(1)

    for path in paths:
        typer.echo(str(path))

    cache_dir = paths[0].parent if paths else feeds_mod.cache_dir_for(rss)
    typer.echo(f"{len(paths)} episodes in cache {cache_dir}")


@app.command()
def report(
    output_dir: Optional[Path] = typer.Option(None, "--output-dir"),
) -> None:
    """Re-render report from existing results/report.json."""
    json_path = (
        Path(output_dir) / "report.json"
        if output_dir
        else Path(__file__).resolve().parents[2] / "results" / "report.json"
    )
    if not json_path.exists():
        typer.echo(f"error: {json_path} not found -- run sweep first", err=True)
        raise typer.Exit(1)
    payload = json.loads(json_path.read_text())
    md_path, _ = report_mod.write(
        payload["sweep"],
        scan_result=payload.get("scan_eval"),
        xep_result=payload.get("cross_episode"),
        output_dir=output_dir,
    )
    typer.echo(f"report written: {md_path}")


# -- helpers --

def _mfcc_frame_count(row: dict) -> int:
    mfcc = deserialize_mfcc(row["mfcc_blob"], row["n_coeffs"])
    return mfcc.shape[0]


def _print_summary(result: dict) -> None:
    scores = result.get("scores", [])
    typer.echo(f"total matches at floor: {len(scores)}")
    for tid_str, info in result.get("per_template", {}).items():
        label = info.get("label", tid_str)
        sug = info.get("suggestion", {})
        suggested = sug.get("suggested")
        conf = sug.get("confidence", "low")
        peak = info.get("peak_score", 0.0)
        typer.echo(
            f"  [{label}] matches={len(info.get('scores', []))}"
            f" peak={peak:.3f}"
            f" suggested={suggested} ({conf})"
        )


if __name__ == "__main__":
    app()
