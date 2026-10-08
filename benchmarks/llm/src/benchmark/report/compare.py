"""Cross-cell comparison: prompt variant x addressing mode, side by side.

One row per model across the four cells, reusing each per-cell report's own
aggregation; delta/p-value pair segmentation/segment_ids against
detection/timestamps on the episodes a model scored in both. Most columns
match the corresponding per-cell report exactly, but cost/ep here is always
divided by this cell's own episode count, while the per-cell report's
"Cost / episode" column prints the corpus-wide total cost unchanged.
"""
from __future__ import annotations

import statistics
from collections import defaultdict
from pathlib import Path

from .. import pricing
from ..corpus import Episode
from ..storage import read_calls
from ..variants import ADDRESSING_MODES, PROMPT_VARIANTS, record_cell
from .aggregate import ModelStats, _aggregate, _dedup_last_write_wins, _paired_t_pvalue

_BASELINE_CELL = ("detection", "timestamps")
_CANDIDATE_CELL = ("segmentation", "segment_ids")
_CELL_COLUMNS = ("F0.5", "precision", "recall", "F1", "cost/ep", "p50", "JSON compliance", "n episodes")


def _cell_label(variant: str, mode: str) -> str:
    return f"{variant}/{mode}"


def _episode_cost_counts_per_model(calls: list[dict]) -> dict[str, int]:
    """Episodes each model has a non-errored row for in this cell, so
    total_episode_cost (a sum across episodes) can be turned into dollars/episode."""
    ids: dict[str, set[str]] = defaultdict(set)
    for c in calls:
        if c.get("episode_id") and not c.get("error"):
            ids[c["model"]].add(c["episode_id"])
    return {m: len(v) for m, v in ids.items()}


def render(
    *,
    cfg,
    episodes: list[Episode],
    raw_dir: Path,
    pricing_snapshot: pricing.PricingSnapshot,
    output_path: Path,
) -> None:
    all_calls = list(read_calls(raw_dir))
    calls_by_cell: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for r in all_calls:
        calls_by_cell[record_cell(r)].append(r)
    deprecated_ids = {m.id for m in cfg.models if m.deprecated}
    cells: dict[tuple[str, str], dict[str, ModelStats]] = {}
    cell_episode_ids: dict[tuple[str, str], list[str]] = {}
    cell_cost_episode_counts: dict[tuple[str, str], dict[str, int]] = {}
    for variant in PROMPT_VARIANTS:
        for mode in ADDRESSING_MODES:
            raw = calls_by_cell.get((variant, mode), [])
            if not raw:
                continue
            calls = _dedup_last_write_wins(raw)
            by_model, _extras = _aggregate(calls, episodes, pricing_snapshot=pricing_snapshot)
            cells[(variant, mode)] = {mid: s for mid, s in by_model.items() if mid not in deprecated_ids}
            cell_episode_ids[(variant, mode)] = sorted({c["episode_id"] for c in calls if c.get("episode_id")})
            cell_cost_episode_counts[(variant, mode)] = _episode_cost_counts_per_model(calls)

    if not cells:
        output_path.write_text(
            "# MinusPod LLM Benchmark: Prompt Variant Comparison\n\n"
            "No benchmark data yet. Run `benchmark run` for at least two "
            "(prompt variant, addressing mode) cells first.\n"
        )
        return

    model_cell_count: dict[str, int] = {}
    for by_model in cells.values():
        for model in by_model:
            model_cell_count[model] = model_cell_count.get(model, 0) + 1
    models = sorted(m for m, n in model_cell_count.items() if n >= 2)

    cell_order = [(v, m) for v in PROMPT_VARIANTS for m in ADDRESSING_MODES if (v, m) in cells]

    lines = [
        "# MinusPod LLM Benchmark: Prompt Variant Comparison",
        "",
        "Detection scores every ad the model returned; segmentation keeps only "
        "sponsor, cross_promo, self_promo, and interaction segments, so the two "
        "variants are not scored against the same definition of 'ad'. Each "
        "cell's columns below come from that cell's own call records and "
        "match the corresponding per-cell report. One row per model that has "
        "rows in at least two cells; a model present in only one cell is "
        "omitted. The delta and p-value columns compare segmentation/segment_ids "
        "against detection/timestamps, paired on the episodes each model has "
        "scored in both of those two cells (not the full episode set in either "
        "cell's own columns). One exception: each cell's `cost/ep` here is this "
        "cell's total cost divided by this cell's own episode count, while the "
        "per-cell report's 'Cost / episode' column prints the corpus-wide total "
        "cost unchanged, so the two are not directly comparable.",
        "",
    ]

    header = ["Model"]
    for variant, mode in cell_order:
        label = _cell_label(variant, mode)
        header += [f"{label} {col}" for col in _CELL_COLUMNS]
    header += ["delta F0.5 (segmentation/segment_ids - detection/timestamps)", "p-value"]
    lines.append("| " + " | ".join(header) + " |")
    lines.append("|" + "|".join("---" for _ in header) + "|")

    for model in models:
        row: list[str] = []
        for variant, mode in cell_order:
            s = cells[(variant, mode)].get(model)
            if s is None:
                row += ["-"] * len(_CELL_COLUMNS)
                continue
            n_cost_episodes = cell_cost_episode_counts[(variant, mode)].get(model, 0)
            cost_per_ep = s.total_episode_cost / n_cost_episodes if n_cost_episodes else 0.0
            row += [
                f"{s.avg_f05:.3f}", f"{s.avg_precision:.3f}", f"{s.avg_recall:.3f}", f"{s.avg_f1:.3f}",
                f"${cost_per_ep:.4f}", f"{s.p50_call_latency_ms / 1000:.1f}s",
                f"{s.json_compliance_mean:.2f}", str(len(s.f05_per_episode)),
            ]
        delta_cell = "n/a"
        p_cell = "n/a"
        candidate = cells.get(_CANDIDATE_CELL, {}).get(model)
        baseline = cells.get(_BASELINE_CELL, {}).get(model)
        if candidate is not None and baseline is not None:
            shared = [e for e in candidate.f05_per_episode if e in baseline.f05_per_episode]
            if shared:
                cand_avg = statistics.fmean(candidate.f05_per_episode[e] for e in shared)
                base_avg = statistics.fmean(baseline.f05_per_episode[e] for e in shared)
                delta_cell = f"{cand_avg - base_avg:+.3f}"
            pvalue = _paired_t_pvalue(candidate.f05_per_episode, baseline.f05_per_episode)
            p_cell = f"{pvalue:.3f}" if pvalue is not None else "n/a"
        lines.append("| " + " | ".join([f"`{model}`"] + row + [delta_cell, p_cell]) + " |")

    lines += ["", "## Episode sets per cell", "", "| Cell | Episodes |", "|---|---|"]
    for variant, mode in cell_order:
        ids = cell_episode_ids[(variant, mode)]
        ep_list = ", ".join(f"`{i}`" for i in ids) if ids else "-"
        lines.append(f"| {_cell_label(variant, mode)} | {ep_list} |")

    output_path.write_text("\n".join(lines) + "\n")
