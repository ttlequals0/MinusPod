"""SVG chart renderers (matplotlib, Agg backend) for the benchmark report."""
from __future__ import annotations

import statistics
from pathlib import Path

from ..corpus import Episode
from .aggregate import (
    CALIBRATION_BIN_LABELS,
    CALIBRATION_BINS,
    F1_STDEV_STABLE,
    F1_STDEV_WOBBLY,
    ModelStats,
    _avg_f1,
    _per_model_alignment,
)


def _plt():
    """Agg backend with deterministic SVG element ids: a fixed hashsalt keeps
    matplotlib's generated ids stable across runs, so regenerating unchanged
    data produces byte-identical committed assets. Pair with _save_svg, which
    suppresses the embedded creation date."""
    import matplotlib
    matplotlib.use("Agg")
    matplotlib.rcParams["svg.hashsalt"] = "minuspod-benchmark"
    import matplotlib.pyplot as plt
    return plt


def _save_svg(fig, path: Path) -> None:
    fig.savefig(path, format="svg", bbox_inches="tight", metadata={"Date": None})


_CATEGORICAL_CMAPS = ("tab20", "tab20b", "tab20c")  # 20 colors each = 60 total


def _distinct_colors(n: int) -> list[tuple[float, ...]]:
    """Return n visually distinct RGBA tuples, never repeating.

    Concatenating the tab20 family gives 60 categorical colors with good
    perceptual contrast. Past that, fall back to evenly-spaced hsv samples
    so each model still gets a unique color, accepting weaker contrast."""
    plt = _plt()
    palette: list[tuple[float, ...]] = []
    for name in _CATEGORICAL_CMAPS:
        palette.extend(plt.get_cmap(name).colors)
    if n <= len(palette):
        return palette[:n]
    cmap = plt.get_cmap("hsv")
    return [cmap(i / n) for i in range(n)]


def _render_pareto(stats: dict[str, ModelStats], path: Path) -> None:
    """Distinct color per model, legend rendered as a real matplotlib legend
    below the plot so each model's color sits next to its name."""
    plt = _plt()

    points = [(s, _avg_f1(s)) for s in stats.values()]
    points = [(s, f1) for s, f1 in points if not (f1 == 0 and s.total_episode_cost == 0)]
    points.sort(key=lambda t: (-t[1], t[0].total_episode_cost))  # rank by F1 desc, then cost asc

    colors = _distinct_colors(len(points))
    fig, ax = plt.subplots(figsize=(11, 9))
    for i, (s, f1) in enumerate(points):
        ax.scatter(
            s.total_episode_cost, f1,
            s=180, color=colors[i],
            edgecolors="black", linewidths=0.7, zorder=3,
            label=f"{s.model}  (F1 {f1:.3f}, ${s.total_episode_cost:.4f}/ep)",
        )

    ax.set_xlabel("Cost per episode (USD), lower is better", fontsize=10)
    ax.set_ylabel("F1 score (accuracy, 0-1), higher is better", fontsize=10)
    ax.set_title("Cost vs F1 by model", fontsize=12, fontweight="bold")
    ax.grid(True, alpha=0.3)

    ncol = 2 if len(points) > 6 else 1
    legend = fig.legend(
        loc="lower center",
        bbox_to_anchor=(0.5, 0.02),
        ncol=ncol,
        fontsize=9,
        frameon=True,
        edgecolor="lightgray",
        columnspacing=2.0,
        handletextpad=0.7,
        borderpad=0.8,
    )
    legend.get_frame().set_alpha(0.95)

    # Reserve enough bottom space for the legend; 0.45 fits ~7-row 2-column legend
    rows = (len(points) + ncol - 1) // ncol
    bottom = min(0.55, 0.10 + 0.038 * rows)
    fig.subplots_adjust(left=0.10, right=0.96, top=0.93, bottom=bottom)
    _save_svg(fig, path)
    plt.close(fig)


def _render_compliance(stats: dict[str, ModelStats], path: Path) -> None:
    """Horizontal bar chart of JSON-array compliance, sorted descending."""
    plt = _plt()

    rows = sorted(stats.values(), key=lambda s: s.json_compliance_mean)
    if not rows:
        return
    labels = [s.model for s in rows]
    values = [s.json_compliance_mean for s in rows]
    colors = ["#2ca02c" if v >= 0.95 else "#f0a020" if v >= 0.7 else "#d62728" for v in values]

    fig, ax = plt.subplots(figsize=(10, max(4, 0.45 * len(rows))))
    bars = ax.barh(labels, values, color=colors, edgecolor="black", linewidth=0.4)
    ax.set_xlim(0, 1.05)
    ax.set_xlabel("JSON schema compliance (0 to 1, higher is better)", fontsize=10)
    ax.set_title("How often each model returned the requested JSON shape cleanly", fontsize=11, fontweight="bold")
    ax.axvline(0.95, color="gray", linestyle=":", linewidth=0.8, alpha=0.7)
    ax.grid(True, axis="x", alpha=0.3)
    for bar, v in zip(bars, values):
        ax.text(v + 0.01, bar.get_y() + bar.get_height() / 2, f"{v:.2f}",
                va="center", fontsize=8)
    fig.tight_layout()
    _save_svg(fig, path)
    plt.close(fig)


def _render_episode_heatmap(stats: dict[str, ModelStats], episodes: list[Episode], path: Path) -> None:
    """Heatmap of F1 across (model, episode). Skips the no-ad episode."""
    plt = _plt()
    import numpy as np

    ad_episodes = [ep for ep in episodes if not ep.truth.is_no_ad_episode]
    if not ad_episodes or not stats:
        return
    # Sort models by avg F1 desc so the best are at the top
    models_sorted = sorted(stats.values(), key=lambda s: _avg_f1(s), reverse=True)

    matrix = np.zeros((len(models_sorted), len(ad_episodes)))
    for i, s in enumerate(models_sorted):
        for j, ep in enumerate(ad_episodes):
            matrix[i, j] = s.f1_per_episode.get(ep.ep_id, 0.0)

    fig, ax = plt.subplots(figsize=(max(8, 1.5 * len(ad_episodes)), max(4, 0.4 * len(models_sorted))))
    im = ax.imshow(matrix, cmap="RdYlGn", vmin=0, vmax=1, aspect="auto")
    ax.set_xticks(range(len(ad_episodes)))
    # Use podcast slug if title would be too long
    ax.set_xticklabels([ep.metadata.podcast_slug for ep in ad_episodes], rotation=30, ha="right", fontsize=9)
    ax.set_yticks(range(len(models_sorted)))
    ax.set_yticklabels([s.model for s in models_sorted], fontsize=9)
    for i in range(len(models_sorted)):
        for j in range(len(ad_episodes)):
            v = matrix[i, j]
            ax.text(j, i, f"{v:.2f}", ha="center", va="center",
                    fontsize=8, color="black" if v > 0.4 else "white")
    ax.set_title("F1 score by model and episode (no-ad episode excluded)", fontsize=11, fontweight="bold")
    fig.colorbar(im, ax=ax, label="F1 score (0 to 1)", shrink=0.6)
    fig.tight_layout()
    _save_svg(fig, path)
    plt.close(fig)


# ---------------------------------------------------------------------------
# Extended-analysis render functions (Section A items added after first run)
# ---------------------------------------------------------------------------


def _render_calibration_chart(
    calibration: dict[str, list[tuple[float, bool]]],
    path: Path,
) -> None:
    """Calibration heatmap: one row per model, one column per confidence bin,
    cell color = calibration error (actual hit rate minus bin midpoint), cell
    text = actual hit rate plus sample size. Replaces the prior line-overlay
    chart, which crowded near-identical points at the high-confidence end and
    rendered the x-axis labels unreadable.

    Diverging colormap centered on 0:
      green  -> well-calibrated (actual close to expected)
      red    -> overconfident   (actual << expected)
      blue   -> underconfident  (actual >> expected)
    """
    plt = _plt()
    import numpy as np

    bins = CALIBRATION_BINS
    bin_labels = CALIBRATION_BIN_LABELS
    bin_midpoints = [(lo + min(hi, 1.0)) / 2 for lo, hi in bins]

    # Build the (model, bin) matrices: actual hit rate, sample count, calibration error.
    model_rows = []
    for model in sorted(calibration):
        pairs = calibration[model]
        if len(pairs) < 5:
            continue
        row_actual = [float("nan")] * len(bins)
        row_n = [0] * len(bins)
        row_err = [float("nan")] * len(bins)
        for j, (lo, hi) in enumerate(bins):
            ins = [t for conf, t in pairs if lo <= conf < hi]
            if not ins:
                continue
            actual = sum(ins) / len(ins)
            row_actual[j] = actual
            row_n[j] = len(ins)
            row_err[j] = actual - bin_midpoints[j]
        # Sort key: largest dominant-bin sample size, used to put high-volume models near the top.
        dominant_n = max(row_n) if row_n else 0
        model_rows.append((model, row_actual, row_n, row_err, dominant_n))

    if not model_rows:
        return

    # Sort by overall mean calibration error (negative -> overconfident at top)
    def mean_err(r):
        errs = [e for e in r[3] if not np.isnan(e)]
        return sum(errs) / len(errs) if errs else 0
    model_rows.sort(key=mean_err)

    n_models = len(model_rows)
    fig, ax = plt.subplots(figsize=(max(9, 1.6 * len(bins)), max(5, 0.40 * n_models)))
    matrix = np.array([r[3] for r in model_rows], dtype=float)
    masked = np.ma.masked_invalid(matrix)
    im = ax.imshow(masked, cmap="RdYlGn", vmin=-0.6, vmax=0.6, aspect="auto")
    cmap = im.get_cmap().copy()
    cmap.set_bad(color="#eeeeee")
    im.set_cmap(cmap)

    ax.set_xticks(range(len(bin_labels)))
    ax.set_xticklabels(bin_labels, fontsize=9)
    ax.set_yticks(range(n_models))
    ax.set_yticklabels([r[0] for r in model_rows], fontsize=9)
    ax.set_xlabel("Self-reported confidence bin", fontsize=10)

    # Annotate each cell: actual hit rate + (n=sample size). Blank cells (NaN) stay empty.
    for i, (_, row_actual, row_n, row_err, _) in enumerate(model_rows):
        for j in range(len(bins)):
            if np.isnan(row_actual[j]):
                continue
            color = "black" if abs(row_err[j]) < 0.4 else "white"
            ax.text(j, i, f"{row_actual[j]:.2f}\n(n={row_n[j]})",
                    ha="center", va="center", fontsize=7, color=color)

    ax.set_title(
        "Confidence calibration (cell text = actual hit rate, n = sample size)\n"
        "Red = overconfident   Green = well-calibrated   Blue = underconfident",
        fontsize=10, fontweight="bold",
    )
    fig.colorbar(im, ax=ax, label="actual hit rate minus bin midpoint", shrink=0.7)
    fig.tight_layout()
    _save_svg(fig, path)
    plt.close(fig)


def _render_latency_tail_chart(stats: dict[str, ModelStats], path: Path) -> None:
    """Bar chart of p50/p90/p99/max per model on a log scale. Visually surfaces
    which models have well-behaved tails vs which have multi-minute outliers."""
    plt = _plt()
    import numpy as np

    rows = sorted(stats.values(), key=lambda s: s.p50_call_latency_ms)
    if not rows:
        return
    labels = [s.model for s in rows]
    p50s = [s.p50_call_latency_ms / 1000 for s in rows]
    p90s = [s.p90_call_latency_ms / 1000 for s in rows]
    p99s = [s.p99_call_latency_ms / 1000 for s in rows]
    maxes = [s.max_call_latency_ms / 1000 for s in rows]

    y = np.arange(len(rows))
    height = 0.2
    fig, ax = plt.subplots(figsize=(11, max(5, 0.55 * len(rows))))
    ax.barh(y - 1.5 * height, p50s, height, label="p50", color="#2ca02c", edgecolor="black", linewidth=0.3)
    ax.barh(y - 0.5 * height, p90s, height, label="p90", color="#1f77b4", edgecolor="black", linewidth=0.3)
    ax.barh(y + 0.5 * height, p99s, height, label="p99", color="#f0a020", edgecolor="black", linewidth=0.3)
    ax.barh(y + 1.5 * height, maxes, height, label="max", color="#d62728", edgecolor="black", linewidth=0.3)
    ax.set_yticks(y)
    ax.set_yticklabels(labels, fontsize=9)
    ax.set_xscale("log")
    ax.set_xlabel("Seconds (log scale), lower is better", fontsize=10)
    ax.set_title("Latency percentiles per model", fontsize=11, fontweight="bold")
    ax.grid(True, axis="x", alpha=0.3, which="both")
    ax.legend(loc="lower right", fontsize=9)
    fig.tight_layout()
    _save_svg(fig, path)
    plt.close(fig)


def _render_agreement_chart(
    agreement: dict[tuple[str, int], dict[str, int]],
    n_models: int,
    path: Path,
) -> None:
    """Histogram of how many models flagged each (episode, window). Tall bars
    at the extremes (0-of-N or N-of-N) mean the field broadly agrees on those
    windows; bars in the middle are contested cases worth inspecting."""
    plt = _plt()
    import numpy as np

    if not agreement:
        return
    counts = np.zeros(n_models + 1, dtype=int)
    for per_model in agreement.values():
        n_voted = sum(1 for v in per_model.values() if v > 0)
        counts[n_voted] += 1
    total = counts.sum()
    if total == 0:
        return
    xs = np.arange(n_models + 1)
    # Color gradient: low agreement = red, mid = yellow, high = green
    colors = ["#d62728" if i < n_models * 0.25 else "#f0a020" if i < n_models * 0.75 else "#2ca02c" for i in xs]

    fig, ax = plt.subplots(figsize=(11, 5.5))
    bars = ax.bar(xs, counts, color=colors, edgecolor="black", linewidth=0.4)
    for bar, c in zip(bars, counts):
        if c == 0:
            continue
        ax.text(bar.get_x() + bar.get_width() / 2, c + max(counts) * 0.01,
                f"{c}\n({c * 100 / total:.0f}%)",
                ha="center", va="bottom", fontsize=8)
    ax.set_xlabel(f"Models predicting at least one ad (out of {n_models})", fontsize=10)
    ax.set_ylabel("Window count", fontsize=10)
    ax.set_title(
        "Cross-model agreement per window\n"
        "Left = nobody flags (clear non-ad), right = everyone agrees (clear ad), middle = contested",
        fontsize=11, fontweight="bold",
    )
    ax.set_xticks(xs)
    ax.set_xticklabels([str(i) for i in xs], fontsize=8)
    ax.set_ylim(0, max(counts) * 1.15)
    ax.grid(True, axis="y", alpha=0.3)
    fig.tight_layout()
    _save_svg(fig, path)
    plt.close(fig)


def _render_alignment_chart(
    agreement: dict[tuple[str, int], dict[str, int]],
    n_models: int,
    path: Path,
) -> None:
    """Per-model stacked bars of agreement-with-majority. Each model has a
    horizontal bar split into 4 segments: with-yes / with-no / broke-yes /
    broke-no. Sorted by alignment so highest-consensus models are at top."""
    plt = _plt()
    import numpy as np

    rows = _per_model_alignment(agreement, n_models)
    if not rows:
        return
    rows.sort(key=lambda r: r["alignment"])
    labels = [r["model"] for r in rows]
    wy = np.array([r["with_yes"] for r in rows])
    wn = np.array([r["with_no"] for r in rows])
    by_arr = np.array([r["broke_yes"] for r in rows])
    bn = np.array([r["broke_no"] for r in rows])
    y = np.arange(len(rows))

    fig, ax = plt.subplots(figsize=(12, max(5, 0.40 * len(rows))))
    ax.barh(y, wy, color="#2ca02c", edgecolor="black", linewidth=0.3, label="with-yes (matches majority yes)")
    ax.barh(y, wn, left=wy, color="#1f77b4", edgecolor="black", linewidth=0.3, label="with-no (matches majority no)")
    ax.barh(y, by_arr, left=wy + wn, color="#f0a020", edgecolor="black", linewidth=0.3, label="broke-yes (likely false positive)")
    ax.barh(y, bn, left=wy + wn + by_arr, color="#d62728", edgecolor="black", linewidth=0.3, label="broke-no (likely miss)")
    for i, r in enumerate(rows):
        total = r["with_yes"] + r["with_no"] + r["broke_yes"] + r["broke_no"]
        ax.text(total + 1, i, f"{r['alignment'] * 100:.0f}%", va="center", fontsize=8)
    ax.set_yticks(y)
    ax.set_yticklabels(labels, fontsize=9)
    ax.set_xlabel(f"Windows (of {sum(wy + wn + by_arr + bn) // max(len(rows), 1)} total)", fontsize=10)
    ax.set_title(
        "Per-model alignment with majority vote (right-edge label = alignment rate)\n"
        "Green + blue = matches consensus   |   Orange = likely false positive   |   Red = likely missed real ad",
        fontsize=11, fontweight="bold",
    )
    ax.legend(loc="lower right", fontsize=8, framealpha=0.95)
    ax.grid(True, axis="x", alpha=0.3)
    fig.tight_layout()
    _save_svg(fig, path)
    plt.close(fig)


def _render_precision_recall_chart(stats: dict[str, ModelStats], path: Path) -> None:
    """Scatter of precision vs recall per model, with F1 isocurves for reference.
    Top-right = perfect; top-left = cautious (high precision, low recall);
    bottom-right = greedy (high recall, low precision); bottom-left = bad both
    ways."""
    plt = _plt()
    import numpy as np

    points = []
    for s in stats.values():
        if not s.precision_per_episode or not s.recall_per_episode:
            continue
        p = statistics.fmean(s.precision_per_episode.values())
        r = statistics.fmean(s.recall_per_episode.values())
        if p == 0 and r == 0:
            continue
        points.append((s, p, r))
    if not points:
        return
    points.sort(key=lambda t: -(2 * t[1] * t[2] / (t[1] + t[2]) if (t[1] + t[2]) > 0 else 0))  # F1 desc

    colors = _distinct_colors(len(points))
    fig, ax = plt.subplots(figsize=(11, 9))

    # F1 isocurves: for each target F1, plot the curve precision*recall*2 / (p+r) = F1
    # Equivalent: r = (F1 * p) / (2p - F1) for p > F1/2
    for f1_iso in [0.2, 0.4, 0.6, 0.8]:
        ps = np.linspace(f1_iso / 2 + 0.001, 1.0, 200)
        rs = (f1_iso * ps) / (2 * ps - f1_iso)
        rs = np.clip(rs, 0, 1)
        ax.plot(rs, ps, "--", color="gray", linewidth=0.6, alpha=0.5)
        # Label at top-right end of each curve
        ax.text(rs[-1] + 0.005, ps[-1] - 0.015, f"F1={f1_iso}",
                fontsize=7, color="gray", alpha=0.7)

    for i, (s, p, r) in enumerate(points):
        f1 = 2 * p * r / (p + r) if (p + r) > 0 else 0
        ax.scatter(r, p, s=180, color=colors[i],
                   edgecolors="black", linewidths=0.7, zorder=3,
                   label=f"{s.model}  (P {p:.2f}, R {r:.2f}, F1 {f1:.2f})")

    ax.set_xlabel("Recall: of the real ads, what fraction the model found", fontsize=10)
    ax.set_ylabel("Precision: of the model's flags, what fraction were real ads", fontsize=10)
    ax.set_title(
        "Precision vs Recall per model (dashed lines are F1 isocurves)\n"
        "Top-right = ideal   |   Top-left = cautious   |   Bottom-right = greedy   |   Bottom-left = poor",
        fontsize=11, fontweight="bold",
    )
    ax.set_xlim(-0.02, 1.02)
    ax.set_ylim(-0.02, 1.02)
    ax.grid(True, alpha=0.3)

    ncol = 2 if len(points) > 6 else 1
    fig.legend(loc="lower center", bbox_to_anchor=(0.5, 0.02), ncol=ncol,
               fontsize=8, frameon=True, edgecolor="lightgray")
    rows = (len(points) + ncol - 1) // ncol
    bottom = min(0.55, 0.10 + 0.035 * rows)
    fig.subplots_adjust(left=0.10, right=0.96, top=0.90, bottom=bottom)
    _save_svg(fig, path)
    plt.close(fig)


def _render_boundary_chart(stats: dict[str, ModelStats], path: Path) -> None:
    """Stacked horizontal bars of start MAE and end MAE per model. Sorted by
    total error so the cleanest boundaries appear at the top. Models with
    skewed bars (one side much larger than the other) consistently overshoot
    on one boundary. Worth knowing if you cut audio downstream."""
    plt = _plt()
    import numpy as np

    rows = [s for s in stats.values() if s.boundary_start_mae is not None]
    if not rows:
        return
    rows.sort(key=lambda s: (s.boundary_start_mae or 0) + (s.boundary_end_mae or 0))
    labels = [s.model for s in rows]
    starts = [s.boundary_start_mae or 0 for s in rows]
    ends = [s.boundary_end_mae or 0 for s in rows]
    y = np.arange(len(rows))

    fig, ax = plt.subplots(figsize=(11, max(5, 0.40 * len(rows))))
    ax.barh(y, starts, color="#1f77b4", edgecolor="black", linewidth=0.3, label="start MAE")
    ax.barh(y, ends, left=starts, color="#ff7f0e", edgecolor="black", linewidth=0.3, label="end MAE")
    for i, (s_v, e_v) in enumerate(zip(starts, ends)):
        ax.text(s_v + e_v + 0.3, i, f"{s_v + e_v:.1f}s total",
                va="center", fontsize=8)
    ax.set_yticks(y)
    ax.set_yticklabels(labels, fontsize=9)
    ax.set_xlabel("Boundary error in seconds (lower is better)", fontsize=10)
    ax.set_title("Boundary accuracy per model (matched ads only, IoU >= 0.5)", fontsize=11, fontweight="bold")
    ax.legend(loc="lower right", fontsize=9)
    ax.grid(True, axis="x", alpha=0.3)
    fig.tight_layout()
    _save_svg(fig, path)
    plt.close(fig)


def _render_token_efficiency_chart(stats: dict[str, ModelStats], path: Path) -> None:
    """Scatter of tokens-per-detected-ad vs F1. Upper-left is the efficient
    zone (high F1 with few output tokens). Right side is verbose reasoning
    models; the question is whether they buy more F1 with the extra tokens
    or just burn output budget."""
    plt = _plt()

    points = [(s, _avg_f1(s)) for s in stats.values()
              if s.tokens_per_detected_ad is not None and s.detected_ads_total > 0]
    if not points:
        return
    points.sort(key=lambda t: -t[1])

    colors = _distinct_colors(len(points))
    fig, ax = plt.subplots(figsize=(11, 8))
    for i, (s, f1) in enumerate(points):
        ax.scatter(s.tokens_per_detected_ad, f1, s=180, color=colors[i],
                   edgecolors="black", linewidths=0.7, zorder=3,
                   label=f"{s.model}  (F1 {f1:.2f}, {s.tokens_per_detected_ad:.0f} tok/ad)")
    ax.set_xscale("log")
    ax.set_xlabel("Output tokens per detected ad (log scale, lower is more concise)", fontsize=10)
    ax.set_ylabel("F1 score (higher is better)", fontsize=10)
    ax.set_title(
        "Token efficiency vs accuracy: does verbose output buy more F1?\n"
        "Upper-left = efficient (high F1, few tokens)   |   Lower-right = burning tokens for no gain",
        fontsize=11, fontweight="bold",
    )
    ax.set_ylim(-0.02, 1.02)
    ax.grid(True, alpha=0.3, which="both")

    ncol = 2 if len(points) > 6 else 1
    fig.legend(loc="lower center", bbox_to_anchor=(0.5, 0.02), ncol=ncol,
               fontsize=8, frameon=True, edgecolor="lightgray")
    rows = (len(points) + ncol - 1) // ncol
    bottom = min(0.55, 0.10 + 0.035 * rows)
    fig.subplots_adjust(left=0.10, right=0.96, top=0.92, bottom=bottom)
    _save_svg(fig, path)
    plt.close(fig)


def _render_trial_variance_chart(stats: dict[str, ModelStats], path: Path) -> None:
    """Horizontal bars of mean F1 stdev across episodes per model. Color
    threshold at 0.05: below = stable at temp=0, above = wobbly."""
    plt = _plt()

    rows = [(s, statistics.fmean(s.f1_stdev_per_episode.values()))
            for s in stats.values() if s.f1_stdev_per_episode]
    if not rows:
        return
    rows.sort(key=lambda t: t[1])
    labels = [r[0].model for r in rows]
    values = [r[1] for r in rows]
    colors = ["#2ca02c" if v < F1_STDEV_STABLE else "#f0a020" if v < F1_STDEV_WOBBLY else "#d62728" for v in values]

    fig, ax = plt.subplots(figsize=(11, max(5, 0.40 * len(rows))))
    bars = ax.barh(labels, values, color=colors, edgecolor="black", linewidth=0.4)
    for bar, v in zip(bars, values):
        ax.text(v + max(values) * 0.01, bar.get_y() + bar.get_height() / 2,
                f"{v:.4f}", va="center", fontsize=8)
    ax.axvline(F1_STDEV_STABLE, color="gray", linestyle=":", linewidth=0.8, alpha=0.7)
    ax.axvline(F1_STDEV_WOBBLY, color="gray", linestyle=":", linewidth=0.8, alpha=0.7)
    ax.set_xlabel("Mean F1 stdev across episodes (lower is more deterministic at temp=0)", fontsize=10)
    ax.set_title("Trial-to-trial F1 variance per model", fontsize=11, fontweight="bold")
    ax.grid(True, axis="x", alpha=0.3)
    fig.tight_layout()
    _save_svg(fig, path)
    plt.close(fig)


def _render_detection_bucket_chart(
    detection_buckets: dict[str, dict[str, dict[str, list[bool]]]],
    bucket_kind: str,
    bucket_order: list[str],
    title: str,
    path: Path,
) -> None:
    """Heatmap of detection rate per (model, bucket) for a given bucket kind
    ('length' or 'position'). Cell text shows rate + sample size."""
    plt = _plt()
    import numpy as np

    if not detection_buckets:
        return
    # Sort models by overall detection rate (sum of hits / total) for that bucket kind, desc
    def overall_rate(model):
        all_hits = []
        for label in bucket_order:
            all_hits.extend(detection_buckets[model].get(bucket_kind, {}).get(label, []))
        return sum(all_hits) / len(all_hits) if all_hits else 0
    models_sorted = sorted(detection_buckets, key=overall_rate, reverse=True)
    if not models_sorted:
        return

    matrix = np.full((len(models_sorted), len(bucket_order)), np.nan)
    sizes = [[0] * len(bucket_order) for _ in models_sorted]
    for i, model in enumerate(models_sorted):
        buckets = detection_buckets[model].get(bucket_kind, {})
        for j, label in enumerate(bucket_order):
            hits = buckets.get(label, [])
            if hits:
                matrix[i, j] = sum(hits) / len(hits)
                sizes[i][j] = len(hits)

    fig, ax = plt.subplots(figsize=(max(7, 1.7 * len(bucket_order)), max(5, 0.40 * len(models_sorted))))
    masked = np.ma.masked_invalid(matrix)
    im = ax.imshow(masked, cmap="RdYlGn", vmin=0, vmax=1, aspect="auto")
    cmap = im.get_cmap().copy(); cmap.set_bad(color="#eeeeee"); im.set_cmap(cmap)
    ax.set_xticks(range(len(bucket_order)))
    ax.set_xticklabels(bucket_order, rotation=20, ha="right", fontsize=9)
    ax.set_yticks(range(len(models_sorted)))
    ax.set_yticklabels(models_sorted, fontsize=9)
    for i in range(len(models_sorted)):
        for j in range(len(bucket_order)):
            v = matrix[i, j]
            if np.isnan(v):
                continue
            color = "black" if 0.3 < v < 0.7 else "white"
            ax.text(j, i, f"{v:.2f}\n(n={sizes[i][j]})", ha="center", va="center",
                    fontsize=7, color=color)
    ax.set_title(title, fontsize=11, fontweight="bold")
    fig.colorbar(im, ax=ax, label="detection rate", shrink=0.7)
    fig.tight_layout()
    _save_svg(fig, path)
    plt.close(fig)


def _render_parser_stress_chart(stats: dict[str, ModelStats], path: Path) -> None:
    """Heatmap of extraction-method usage per model. Rows = models, columns =
    methods sorted by total usage (most common first), cell = call count."""
    plt = _plt()
    import numpy as np

    methods_global: dict[str, int] = {}
    for s in stats.values():
        for m, n in s.extraction_method_counts.items():
            methods_global[m] = methods_global.get(m, 0) + n
    methods = sorted(methods_global, key=lambda m: -methods_global[m])
    if not methods:
        return
    models_sorted = sorted(
        stats.values(),
        key=lambda s: -(s.extraction_method_counts.get("json_array_direct", 0)
                        / max(sum(s.extraction_method_counts.values()), 1)),
    )

    matrix = np.zeros((len(models_sorted), len(methods)), dtype=int)
    for i, s in enumerate(models_sorted):
        for j, m in enumerate(methods):
            matrix[i, j] = s.extraction_method_counts.get(m, 0)

    fig, ax = plt.subplots(figsize=(max(10, 1.4 * len(methods)), max(5, 0.40 * len(models_sorted))))
    im = ax.imshow(matrix, cmap="YlOrRd", aspect="auto")
    ax.set_xticks(range(len(methods)))
    ax.set_xticklabels(methods, rotation=25, ha="right", fontsize=8)
    ax.set_yticks(range(len(models_sorted)))
    ax.set_yticklabels([s.model for s in models_sorted], fontsize=9)
    max_v = matrix.max() if matrix.size else 1
    for i in range(len(models_sorted)):
        for j in range(len(methods)):
            v = matrix[i, j]
            if v == 0:
                continue
            color = "white" if v > max_v * 0.6 else "black"
            ax.text(j, i, str(v), ha="center", va="center", fontsize=7, color=color)
    ax.set_title(
        "Extraction-method usage per model (cell = call count)\n"
        "Models at top use the clean json_array_direct path most often",
        fontsize=11, fontweight="bold",
    )
    fig.colorbar(im, ax=ax, label="call count", shrink=0.7)
    fig.tight_layout()
    _save_svg(fig, path)
    plt.close(fig)

