"""Render Markdown reports from calls.jsonl and the benchmark corpus."""
from __future__ import annotations

import logging

from pathlib import Path

from .. import pricing
from ..corpus import Episode
from ..storage import read_calls
from ..variants import record_cell
from .aggregate import (
    _aggregate,
    _dedup_last_write_wins,
    campaign_mixing,
    _json_format_summary,
)
from .charts import (
    _render_accuracy_latency,
    _render_agreement_chart,
    _render_alignment_chart,
    _render_boundary_chart,
    _render_calibration_chart,
    _render_cost_split_chart,
    _render_compliance,
    _render_detection_bucket_chart,
    _render_episode_heatmap,
    _render_latency_tail_chart,
    _render_pareto,
    _render_parser_stress_chart,
    _render_precision_recall_chart,
    _render_token_efficiency_chart,
    _render_trial_variance_chart,
)
from .sections import (
    _build_toc,
    _render_accuracy_breakdown,
    _render_boundary_accuracy,
    _render_calibration_table,
    _render_charts_section,
    _render_cost_breakdown,
    _render_cross_model_agreement,
    _render_deprecated,
    _render_detection_buckets,
    _render_failures,
    _render_how_to_read,
    _render_latency_tail,
    _render_methodology,
    _render_parser_stress,
    _render_per_episode_detail,
    _render_per_model_detail,
    _render_quick_comparison,
    _render_run_metadata,
    _render_tldr,
    _render_token_efficiency,
    _render_transcript_source,
    _render_trial_variance,
)



logger = logging.getLogger(__name__)

def report_paths(results_dir: Path, prompt_variant: str, addressing_mode: str) -> tuple[Path, Path]:
    """Per-(variant, mode) cell output paths; the default cell keeps the
    pre-existing report.md path, other cells get a suffixed path so they
    don't clobber each other's charts."""
    if prompt_variant == "detection" and addressing_mode == "timestamps":
        return results_dir / "report.md", results_dir / "report_assets"
    suffix = f"{prompt_variant}-{addressing_mode}"
    return results_dir / f"report-{suffix}.md", results_dir / f"report_assets-{suffix}"


def render(
    *,
    cfg,
    episodes: list[Episode],
    raw_dir: Path,
    pricing_snapshot: pricing.PricingSnapshot,
    output_path: Path,
    assets_dir: Path,
    prompt_source: str = "live",
    addressing_mode: str = "timestamps",
    prompt_variant: str = "detection",
) -> None:
    """Render results/report.md from the call records under raw_dir.

    ``addressing_mode`` isolates the report to one addressing scheme: calls
    are filtered to records whose ``addressing_mode`` field (missing on every
    call written before this field existed, which defaults to 'timestamps')
    matches. ``prompt_variant`` does the same for the ``prompt_variant`` field
    (missing records default to 'detection'). A store holding more than one
    (variant, mode) cell never blends them into one set of numbers; render
    once per cell to see each side of an A/B.
    """
    title_suffixes = []
    if prompt_variant != "detection":
        title_suffixes.append(f"prompt variant: {prompt_variant}")
    if addressing_mode != "timestamps":
        title_suffixes.append(f"addressing mode: {addressing_mode}")
    title = "# MinusPod LLM Benchmark Report"
    if title_suffixes:
        title += " (" + ", ".join(title_suffixes) + ")"

    all_calls = list(read_calls(raw_dir))
    raw_calls = [r for r in all_calls if record_cell(r) == (prompt_variant, addressing_mode)]
    if not raw_calls:
        run_hint = "benchmark run"
        if prompt_variant != "detection":
            run_hint += f" --prompt-variant {prompt_variant}"
        if addressing_mode != "timestamps":
            run_hint += f" --addressing-mode {addressing_mode}"
        mode_notes = []
        if prompt_variant != "detection":
            mode_notes.append(f"prompt variant '{prompt_variant}'")
        if addressing_mode != "timestamps":
            mode_notes.append(f"addressing mode '{addressing_mode}'")
        mode_note = f" for {' and '.join(mode_notes)}" if mode_notes else ""
        output_path.write_text(f"{title}\n\nNo benchmark data yet{mode_note}. Run `{run_hint}` first.\n")
        return
    mixed = campaign_mixing(raw_calls)
    if mixed:
        logger.warning(
            "call records hold more than one campaign: %d work units across %d models carry "
            "two prompt hashes. Dedup keeps the last row per unit regardless of prompt, so "
            "any unit not re-run this campaign still shows the older result. Run "
            "`benchmark rotate-raw` between campaigns.",
            sum(mixed.values()), len(mixed),
        )
    calls = _dedup_last_write_wins(raw_calls)

    by_model, extras = _aggregate(calls, episodes, pricing_snapshot=pricing_snapshot)
    deprecated_ids = {m.id for m in cfg.models if m.deprecated}
    active = {mid: s for mid, s in by_model.items() if mid not in deprecated_ids}
    deprecated = {mid: s for mid, s in by_model.items() if mid in deprecated_ids}

    extras_active = extras.without(deprecated_ids)
    calls_active = calls if not deprecated_ids else [r for r in calls if r["model"] not in deprecated_ids]

    stale = sum(1 for r in calls_active if r.get("windows_stale"))
    if stale:
        logger.warning(
            "%d scored call(s) are marked windows_stale: they ran against "
            "windows that changed afterward. Re-run those units before "
            "trusting the affected models' numbers.", stale,
        )

    assets_dir_name = assets_dir.name
    sections = [
        _render_how_to_read(episodes),
        _render_tldr(active, episodes),
        _render_charts_section(active, assets_dir_name),
        _render_failures(calls_active),
        _render_accuracy_breakdown(active),
        _render_boundary_accuracy(active),
        _render_calibration_table(extras_active.calibration, assets_dir_name),
        _render_latency_tail(active),
        _render_token_efficiency(active),
        _render_cost_breakdown(active),
        _render_trial_variance(active),
        _render_cross_model_agreement(extras_active.agreement, active, episodes),
        _render_detection_buckets(extras_active.detection_buckets),
        _render_quick_comparison(active, episodes),
        "---",
        "## Detailed Results",
        _render_per_model_detail(active),
        _render_per_episode_detail(active, episodes),
        _render_parser_stress(active),
    ]
    if deprecated:
        sections.append(_render_deprecated(deprecated))
    sections += [
        _render_methodology(cfg, episodes, calls_active, pricing_snapshot=pricing_snapshot),
        _render_transcript_source(),
        _render_run_metadata(
            calls, pricing_snapshot=pricing_snapshot, raw_calls=raw_calls,
            prompt_source=prompt_source, addressing_mode=addressing_mode,
            prompt_variant=prompt_variant,
        ),
    ]

    body = "\n\n".join(s for s in sections if s) + "\n"
    toc = _build_toc(body)
    output_path.write_text(title + "\n\n" + toc + "\n\n" + body)

    assets_dir.mkdir(parents=True, exist_ok=True)
    _render_pareto(active, assets_dir / "pareto.svg")
    _render_accuracy_latency(active, assets_dir / "accuracy_latency.svg")
    _render_cost_split_chart(active, assets_dir / "cost_split.svg")
    _render_compliance(active, assets_dir / "compliance.svg")
    _render_episode_heatmap(active, episodes, assets_dir / "episodes.svg")
    _render_calibration_chart(extras_active.calibration, assets_dir / "calibration.svg")
    _render_latency_tail_chart(active, assets_dir / "latency_tail.svg")
    _render_agreement_chart(extras_active.agreement, len(active), assets_dir / "agreement.svg")
    _render_alignment_chart(extras_active.agreement, len(active), assets_dir / "alignment.svg")
    _render_precision_recall_chart(active, assets_dir / "precision_recall.svg")
    _render_boundary_chart(active, assets_dir / "boundary.svg")
    _render_token_efficiency_chart(active, assets_dir / "token_efficiency.svg")
    _render_trial_variance_chart(active, assets_dir / "trial_variance.svg")
    _render_detection_bucket_chart(
        extras_active.detection_buckets, "length",
        ["short (<30s)", "medium (30-90s)", "long (>=90s)"],
        "Detection rate by ad length (rows sorted by overall detection rate, descending)",
        assets_dir / "detection_by_length.svg",
    )
    _render_detection_bucket_chart(
        extras_active.detection_buckets, "position",
        ["pre-roll (<10%)", "mid-roll (10-90%)", "post-roll (>90%)"],
        "Detection rate by ad position (rows sorted by overall detection rate, descending)",
        assets_dir / "detection_by_position.svg",
    )
    _render_parser_stress_chart(active, assets_dir / "parser_stress.svg")

