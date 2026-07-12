"""Render Markdown report from calls.jsonl + episode_results.jsonl + corpus."""
from __future__ import annotations

from pathlib import Path

from .. import pricing
from ..corpus import Episode
from ..storage import read_jsonl
from .aggregate import (
    _aggregate,
    _dedup_last_write_wins,
    _json_format_summary,
)
from .charts import (
    _render_agreement_chart,
    _render_alignment_chart,
    _render_boundary_chart,
    _render_calibration_chart,
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


def render(
    *,
    cfg,
    episodes: list[Episode],
    calls_path: Path,
    episode_results_path: Path,
    pricing_snapshot: pricing.PricingSnapshot,
    output_path: Path,
    assets_dir: Path,
    prompt_source: str = "live",
) -> None:
    raw_calls = list(read_jsonl(calls_path))
    if not raw_calls:
        output_path.write_text("# MinusPod LLM Benchmark Report\n\nNo benchmark data yet. Run `benchmark run` first.\n")
        return
    calls = _dedup_last_write_wins(raw_calls)

    by_model, extras = _aggregate(calls, episodes, pricing_snapshot=pricing_snapshot)
    deprecated_ids = {m.id for m in cfg.models if m.deprecated}
    active = {mid: s for mid, s in by_model.items() if mid not in deprecated_ids}
    deprecated = {mid: s for mid, s in by_model.items() if mid in deprecated_ids}

    extras_active = extras.without(deprecated_ids)
    calls_active = calls if not deprecated_ids else [r for r in calls if r["model"] not in deprecated_ids]

    sections = [
        _render_how_to_read(),
        _render_tldr(active, episodes),
        _render_charts_section(),
        _render_failures(calls_active),
        _render_accuracy_breakdown(active),
        _render_boundary_accuracy(active),
        _render_calibration_table(extras_active.calibration),
        _render_latency_tail(active),
        _render_token_efficiency(active),
        _render_trial_variance(active),
        _render_cross_model_agreement(extras_active.agreement, active),
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
        _render_methodology(cfg, episodes, pricing_snapshot=pricing_snapshot),
        _render_transcript_source(),
        _render_run_metadata(calls, pricing_snapshot=pricing_snapshot, raw_calls=raw_calls, prompt_source=prompt_source),
    ]

    body = "\n\n".join(s for s in sections if s) + "\n"
    toc = _build_toc(body)
    output_path.write_text("# MinusPod LLM Benchmark Report\n\n" + toc + "\n\n" + body)

    assets_dir.mkdir(parents=True, exist_ok=True)
    _render_pareto(active, assets_dir / "pareto.svg")
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

