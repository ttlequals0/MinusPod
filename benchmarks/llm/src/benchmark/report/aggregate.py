"""Aggregate calls.jsonl into per-model stats; tiering and CI statistics."""
from __future__ import annotations

import math
import statistics
from collections import defaultdict
from dataclasses import dataclass, field

from .. import metrics, parsing, pricing
from ..corpus import Episode


DEFAULT_IOU_THRESHOLD = 0.5


# Confidence bins used by the calibration heatmap. (lo, hi) half-open, plus a
# parallel label list. Keep these aligned.
CALIBRATION_BINS: tuple[tuple[float, float], ...] = (
    (0.0, 0.7), (0.7, 0.9), (0.9, 0.95), (0.95, 0.99), (0.99, 1.001),
)


CALIBRATION_BIN_LABELS = ("0.00-0.70", "0.70-0.90", "0.90-0.95", "0.95-0.99", "0.99+")


# Trial-to-trial F1 stdev thresholds: <STABLE green, <WOBBLY orange, else red.
F1_STDEV_STABLE = 0.02


F1_STDEV_WOBBLY = 0.05


@dataclass
class ModelEpisodeStats:
    model: str
    episode_id: str
    trial_f1s: list[float] = field(default_factory=list)
    trial_f05s: list[float] = field(default_factory=list)
    trial_precisions: list[float] = field(default_factory=list)
    trial_recalls: list[float] = field(default_factory=list)
    trial_tps: list[int] = field(default_factory=list)
    trial_fps: list[int] = field(default_factory=list)
    trial_fns: list[int] = field(default_factory=list)
    trial_start_maes: list[float] = field(default_factory=list)
    trial_end_maes: list[float] = field(default_factory=list)
    trial_costs: list[float] = field(default_factory=list)
    trial_response_times: list[int] = field(default_factory=list)
    no_ad_passes: list[bool] = field(default_factory=list)
    no_ad_fp_counts: list[int] = field(default_factory=list)


@dataclass
class ModelStats:
    model: str
    f1_per_episode: dict[str, float] = field(default_factory=dict)
    f05_per_episode: dict[str, float] = field(default_factory=dict)
    f1_stdev_per_episode: dict[str, float] = field(default_factory=dict)
    precision_per_episode: dict[str, float] = field(default_factory=dict)
    recall_per_episode: dict[str, float] = field(default_factory=dict)
    tp_total: int = 0
    fp_total: int = 0
    fn_total: int = 0
    boundary_start_mae: float | None = None
    boundary_end_mae: float | None = None
    no_ad_pass: dict[str, bool] = field(default_factory=dict)
    no_ad_fp_count: dict[str, int] = field(default_factory=dict)
    total_episode_cost: float = 0.0
    p50_call_latency_ms: float = 0.0
    p90_call_latency_ms: float = 0.0
    p95_call_latency_ms: float = 0.0
    p99_call_latency_ms: float = 0.0
    max_call_latency_ms: float = 0.0
    json_compliance_mean: float = 0.0
    # JSON request-mode telemetry. `json_format_used` is logged per call as
    # "native" (the provider accepted response_format=json_object) or
    # "prompt_injection" (provider rejected it and we fell back to instructing
    # JSON in the prompt). Surfacing this helps explain compliance variance:
    # a low score on a model that needed prompt_injection is a different
    # signal than the same score with native support.
    json_format_native_pct: float = 0.0
    json_format_total: int = 0
    json_format_primary: str = "n/a"
    parse_failure_rate: float = 0.0
    extraction_method_counts: dict[str, int] = field(default_factory=dict)
    schema_violations_total: int = 0
    extra_key_names: set[str] = field(default_factory=set)
    output_tokens_total: int = 0
    detected_ads_total: int = 0
    # Verbosity / truncation telemetry. Useful for spotting models that
    # don't follow "respond with valid JSON only" and emit chatty `reason`
    # fields (phi-4, some Gemini variants). `call_count` is the denominator
    # for the three percentages computed at render time.
    call_count: int = 0
    truncated_count: int = 0
    over_1024_count: int = 0
    salvaged_count: int = 0
    avg_f1: float = 0.0
    avg_f05: float = 0.0
    avg_precision: float = 0.0
    avg_recall: float = 0.0
    mean_f1_stdev: float = 0.0

    @property
    def cost_per_tp(self) -> float | None:
        return self.total_episode_cost / self.tp_total if self.tp_total > 0 else None

    @property
    def tokens_per_detected_ad(self) -> float | None:
        return self.output_tokens_total / self.detected_ads_total if self.detected_ads_total > 0 else None


# If >=95% of calls used one mode we call that the model's primary, otherwise
# `mixed`. The threshold matches existing "near-perfect compliance" framing in
# the report; anything below it is worth surfacing as fallback noise.
_JSON_FORMAT_PRIMARY_THRESHOLD = 0.95


def _json_format_summary(counts: dict[str, int]) -> tuple[str, float]:
    total = sum(counts.values())
    if total == 0:
        return "n/a", 0.0
    native = counts.get("native", 0)
    native_pct = native / total
    if native_pct >= _JSON_FORMAT_PRIMARY_THRESHOLD:
        return "native", native_pct
    if (counts.get("prompt_injection", 0) / total) >= _JSON_FORMAT_PRIMARY_THRESHOLD:
        return "prompt-inject", native_pct
    return "mixed", native_pct


def _dedup_last_write_wins(calls: list[dict]) -> list[dict]:
    """`calls.jsonl` is append-only. A `benchmark run --retry-errors` that
    successfully retries a previously-failed tuple appends a new row alongside
    the original error row. The report should reflect the final state, not the
    historical errors, so dedup per (model, episode_id, trial, window_index)
    and keep the last row encountered.
    """
    by_key: dict[tuple, dict] = {}
    for rec in calls:
        key = (rec.get("model"), rec.get("episode_id"), rec.get("trial"), rec.get("window_index"))
        by_key[key] = rec
    return list(by_key.values())


@dataclass
class _Extras:
    """Side data computed during aggregation that doesn't belong on ModelStats."""
    calibration: dict[str, list[tuple[float, bool]]]    # model -> [(confidence, is_tp), ...]
    agreement: dict[tuple[str, int], dict[str, int]]    # (episode, window_idx) -> {model: n_predicted_ads}
    detection_buckets: dict[str, dict[str, dict[str, list[bool]]]]

    def without(self, model_ids: set[str]) -> "_Extras":
        if not model_ids:
            return self
        return _Extras(
            calibration={mid: v for mid, v in self.calibration.items() if mid not in model_ids},
            agreement={
                key: {m: v for m, v in per_model.items() if m not in model_ids}
                for key, per_model in self.agreement.items()
            },
            detection_buckets={mid: v for mid, v in self.detection_buckets.items() if mid not in model_ids},
        )
    # detection_buckets[model][bucket_kind][bucket_label] -> list of bool (was each truth-ad in this bucket detected?)


def _aggregate(
    calls: list[dict],
    episodes: list[Episode],
    *,
    pricing_snapshot: pricing.PricingSnapshot,
) -> tuple[dict[str, ModelStats], _Extras]:
    truths_by_episode = {ep.ep_id: ep for ep in episodes}
    me: dict[tuple[str, str], ModelEpisodeStats] = {}
    response_times_per_model: dict[str, list[int]] = defaultdict(list)
    method_counts: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    compliance_values_per_model: dict[str, list[float]] = defaultdict(list)
    parse_failures_per_model: dict[str, int] = defaultdict(int)
    parse_total_per_model: dict[str, int] = defaultdict(int)
    schema_totals_per_model: dict[str, int] = defaultdict(int)
    extra_keys_per_model: dict[str, set[str]] = defaultdict(set)

    output_tokens_per_model: dict[str, int] = defaultdict(int)
    detected_ads_per_model: dict[str, int] = defaultdict(int)
    call_count_per_model: dict[str, int] = defaultdict(int)
    truncated_per_model: dict[str, int] = defaultdict(int)
    over_1024_per_model: dict[str, int] = defaultdict(int)
    json_format_counts_per_model: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    calibration: dict[str, list[tuple[float, bool]]] = defaultdict(list)
    agreement: dict[tuple[str, int], dict[str, int]] = defaultdict(lambda: defaultdict(int))
    detection_buckets: dict[str, dict[str, dict[str, list[bool]]]] = defaultdict(
        lambda: defaultdict(lambda: defaultdict(list))
    )

    by_trial: dict[tuple[str, str, int], list[dict]] = defaultdict(list)
    for rec in calls:
        if rec.get("error"):
            continue
        by_trial[(rec["model"], rec["episode_id"], rec["trial"])].append(rec)
        response_times_per_model[rec["model"]].append(int(rec.get("response_time_ms", 0)))
        method = rec.get("extraction_method")
        if method is None:
            parse_failures_per_model[rec["model"]] += 1
        method_counts[rec["model"]][method or "parse_failure"] += 1
        parse_total_per_model[rec["model"]] += 1
        compliance_values_per_model[rec["model"]].append(float(rec.get("compliance_score", 0.0)))
        sv = rec.get("schema_violations") or {}
        schema_totals_per_model[rec["model"]] += int(sv.get("missing_required", 0)) + int(sv.get("wrong_type", 0)) + int(sv.get("extra_keys", 0)) + int(sv.get("out_of_range", 0))
        for k in sv.get("extra_key_names") or []:
            extra_keys_per_model[rec["model"]].add(k)
        out_toks = int(rec.get("output_tokens", 0))
        output_tokens_per_model[rec["model"]] += out_toks
        call_count_per_model[rec["model"]] += 1
        if rec.get("truncated"):
            truncated_per_model[rec["model"]] += 1
        # over_1024 has an explicit flag on new records and falls back to a
        # direct comparison on output_tokens for historical records collected
        # before the flag existed. Truncated has no fallback: stop_reason
        # was never captured pre-flag, so we accept 0% on legacy data.
        if rec.get("over_1024_tokens") or out_toks > 1024:
            over_1024_per_model[rec["model"]] += 1
        # "n/a" is the runner's placeholder when the LLM call errored before a
        # format could be selected; skip those so the percent reflects only
        # successful calls.
        json_format = rec.get("json_format_used")
        if json_format and json_format != "n/a":
            json_format_counts_per_model[rec["model"]][json_format] += 1
        ads = rec.get("parsed_ads") or []
        detected_ads_per_model[rec["model"]] += len(ads)
        # Cross-model agreement: count predictions per (episode, window) per model
        agreement[(rec["episode_id"], int(rec.get("window_index", 0)))][rec["model"]] += len(ads)

    for (model, ep_id, trial), records in by_trial.items():
        ep = truths_by_episode.get(ep_id)
        if ep is None:
            continue
        if len(records) < len(ep.windows):
            continue
        records.sort(key=lambda r: r["window_index"])
        per_window_ads: list[list[dict]] = [list(r.get("parsed_ads") or []) for r in records]
        per_window_pred: list[list[tuple[float, float]]] = [
            [(_start(ad), _end(ad)) for ad in w] for w in per_window_ads
        ]
        # Deduplicate ads that span the 180s window overlap before scoring: an
        # ad emitted by two adjacent windows would otherwise be counted as a TP
        # plus a duplicate FP, systematically depressing precision/F1. This
        # matches the production pipeline (derive_episode_results), which runs
        # the same dedup (benchmark-1). The production dedup keys on 'start'/
        # 'end'; benchmark ads use 'start_time'/'end_time', so normalize into
        # copies first (without mutating the per-window records).
        norm_ads: list[dict] = []
        for w in per_window_ads:
            for ad in w:
                a = dict(ad)
                a['start'] = _start(ad)
                a['end'] = _end(ad)
                norm_ads.append(a)
        flat_ads: list[dict] = parsing.deduplicate_window_ads(norm_ads)
        flat_preds: list[tuple[float, float]] = [(_start(ad), _end(ad)) for ad in flat_ads]

        stats = me.setdefault((model, ep_id), ModelEpisodeStats(model=model, episode_id=ep_id))
        if ep.truth.is_no_ad_episode:
            res = metrics.no_ad_score(per_window_pred)
            stats.no_ad_passes.append(res.passed)
            stats.no_ad_fp_counts.append(res.false_positive_count)
            # On a no-ad episode every detection is a false positive -> calibration is_tp=False
            for ad in flat_ads:
                conf = ad.get("confidence")
                if isinstance(conf, (int, float)):
                    calibration[model].append((float(conf), False))
        else:
            truth_ranges = [(ad.start, ad.end) for ad in ep.truth.ads]
            r = metrics.match_predictions(flat_preds, truth_ranges, threshold=DEFAULT_IOU_THRESHOLD)
            stats.trial_f1s.append(r.f1)
            stats.trial_f05s.append(r.fbeta(0.5))
            stats.trial_precisions.append(r.precision)
            stats.trial_recalls.append(r.recall)
            stats.trial_tps.append(r.true_positives)
            stats.trial_fps.append(r.false_positives)
            stats.trial_fns.append(r.false_negatives)
            be = metrics.boundary_error(flat_preds, truth_ranges, r.matches)
            if be is not None:
                stats.trial_start_maes.append(be.start_mae)
                stats.trial_end_maes.append(be.end_mae)
            # Calibration: for each prediction, was it a TP (matched a truth) or FP?
            matched_pred_idxs = {m.pred_index for m in r.matches}
            for i, ad in enumerate(flat_ads):
                conf = ad.get("confidence")
                if isinstance(conf, (int, float)):
                    calibration[model].append((float(conf), i in matched_pred_idxs))
            # Detection-by-bucket: for each truth ad, was it detected (i.e. matched at IoU>=0.5)?
            duration = float(ep.metadata.duration) if getattr(ep.metadata, "duration", None) else 0.0
            matched_truth_idxs = {m.truth_index for m in r.matches}
            for ti, ad in enumerate(ep.truth.ads):
                hit = ti in matched_truth_idxs
                detection_buckets[model]["length"][_length_bucket(ad.end - ad.start)].append(hit)
                detection_buckets[model]["position"][_position_bucket(ad.start, duration)].append(hit)
        stats.trial_costs.append(_recompute_total_cost(records, pricing_snapshot))
        stats.trial_response_times.append(sum(r.get("response_time_ms", 0) for r in records))

    me_by_model: dict[str, list[tuple[str, ModelEpisodeStats]]] = defaultdict(list)
    for (m, ep_id), s in me.items():
        me_by_model[m].append((ep_id, s))

    out: dict[str, ModelStats] = {}
    # Sorted so downstream tables render in a stable order: set iteration
    # varies per process under hash randomization, and stable sorts preserve
    # this order for rows that tie on the sort key.
    models_seen: set[str] = set(me_by_model) | set(response_times_per_model)
    for model in sorted(models_seen):
        ms = ModelStats(model=model)
        all_start_maes: list[float] = []
        all_end_maes: list[float] = []
        for ep_id, s in me_by_model[model]:
            if s.trial_f1s:
                ms.f1_per_episode[ep_id] = statistics.fmean(s.trial_f1s)
                ms.f1_stdev_per_episode[ep_id] = metrics.trial_stdev(s.trial_f1s)
            if s.trial_f05s:
                ms.f05_per_episode[ep_id] = statistics.fmean(s.trial_f05s)
            if s.trial_precisions:
                ms.precision_per_episode[ep_id] = statistics.fmean(s.trial_precisions)
            if s.trial_recalls:
                ms.recall_per_episode[ep_id] = statistics.fmean(s.trial_recalls)
            ms.tp_total += sum(s.trial_tps)
            ms.fp_total += sum(s.trial_fps)
            ms.fn_total += sum(s.trial_fns)
            all_start_maes.extend(s.trial_start_maes)
            all_end_maes.extend(s.trial_end_maes)
            if s.no_ad_passes:
                ms.no_ad_pass[ep_id] = all(s.no_ad_passes)
                ms.no_ad_fp_count[ep_id] = max(s.no_ad_fp_counts) if s.no_ad_fp_counts else 0
            if s.trial_costs:
                ms.total_episode_cost += statistics.fmean(s.trial_costs)
        if all_start_maes:
            ms.boundary_start_mae = statistics.fmean(all_start_maes)
            ms.boundary_end_mae = statistics.fmean(all_end_maes)
        rts = sorted(response_times_per_model[model])
        if rts:
            ms.p50_call_latency_ms = _percentile(rts, 50)
            ms.p90_call_latency_ms = _percentile(rts, 90)
            ms.p95_call_latency_ms = _percentile(rts, 95)
            ms.p99_call_latency_ms = _percentile(rts, 99)
            ms.max_call_latency_ms = float(max(rts))
        comps = compliance_values_per_model[model]
        ms.json_compliance_mean = statistics.fmean(comps) if comps else 0.0
        if parse_total_per_model[model]:
            ms.parse_failure_rate = parse_failures_per_model[model] / parse_total_per_model[model]
        ms.extraction_method_counts = dict(method_counts[model])
        ms.schema_violations_total = schema_totals_per_model[model]
        ms.extra_key_names = extra_keys_per_model[model]
        ms.output_tokens_total = output_tokens_per_model[model]
        ms.detected_ads_total = detected_ads_per_model[model]
        ms.call_count = call_count_per_model[model]
        ms.truncated_count = truncated_per_model[model]
        ms.over_1024_count = over_1024_per_model[model]
        ms.salvaged_count = method_counts[model].get("json_object_single_ad_truncated", 0)
        f1s = list(ms.f1_per_episode.values())
        ms.avg_f1 = statistics.fmean(f1s) if f1s else 0.0
        f05s = list(ms.f05_per_episode.values())
        ms.avg_f05 = statistics.fmean(f05s) if f05s else 0.0
        ms.avg_precision = statistics.fmean(ms.precision_per_episode.values()) if ms.precision_per_episode else 0.0
        ms.avg_recall = statistics.fmean(ms.recall_per_episode.values()) if ms.recall_per_episode else 0.0
        stdevs = list(ms.f1_stdev_per_episode.values())
        ms.mean_f1_stdev = statistics.fmean(stdevs) if stdevs else 0.0
        counts = json_format_counts_per_model[model]
        ms.json_format_total = sum(counts.values())
        ms.json_format_primary, ms.json_format_native_pct = _json_format_summary(counts)
        out[model] = ms
    extras = _Extras(
        calibration=dict(calibration),
        agreement=dict(agreement),
        detection_buckets={k: {b: dict(buckets) for b, buckets in v.items()} for k, v in detection_buckets.items()},
    )
    return out, extras


def _recompute_total_cost(records: list[dict], snap: pricing.PricingSnapshot) -> float:
    if not records:
        return 0.0
    price = snap.lookup(records[0]["model"])
    if price is None:
        return 0.0
    total = 0.0
    for r in records:
        _, _, cost = pricing.cost_usd(price, input_tokens=int(r.get("input_tokens", 0)), output_tokens=int(r.get("output_tokens", 0)))
        total += cost
    return total


def _start(ad: dict) -> float:
    return float(ad.get("start", ad.get("start_time", 0.0)))


def _end(ad: dict) -> float:
    return float(ad.get("end", ad.get("end_time", 0.0)))


def _length_bucket(seconds: float) -> str:
    if seconds < 30:
        return "short (<30s)"
    if seconds < 90:
        return "medium (30-90s)"
    return "long (>=90s)"


def _position_bucket(start: float, duration: float) -> str:
    if duration <= 0:
        return "unknown"
    rel = start / duration
    if rel < 0.10:
        return "pre-roll (<10%)"
    if rel > 0.90:
        return "post-roll (>90%)"
    return "mid-roll (10-90%)"


def _percentile(sorted_values: list[int], p: int) -> float:
    if not sorted_values:
        return 0.0
    k = (p / 100) * (len(sorted_values) - 1)
    lo = int(k)
    hi = min(lo + 1, len(sorted_values) - 1)
    return sorted_values[lo] + (sorted_values[hi] - sorted_values[lo]) * (k - lo)


# 95% t critical values by degrees of freedom, two-sided and one-sided.
# Beyond df=30, fall back to the normal-approximation z (1.960 / 1.645).
_T_CRIT = {
    "two": {
        1: 12.706, 2: 4.303, 3: 3.182, 4: 2.776, 5: 2.571, 6: 2.447, 7: 2.365,
        8: 2.306, 9: 2.262, 10: 2.228, 11: 2.201, 12: 2.179, 13: 2.160, 14: 2.145,
        15: 2.131, 16: 2.120, 17: 2.110, 18: 2.101, 19: 2.093, 20: 2.086, 21: 2.080,
        22: 2.074, 23: 2.069, 24: 2.064, 25: 2.060, 26: 2.056, 27: 2.052, 28: 2.048,
        29: 2.045, 30: 2.042,
    },
    "one": {
        1: 6.314, 2: 2.920, 3: 2.353, 4: 2.132, 5: 2.015, 6: 1.943, 7: 1.895,
        8: 1.860, 9: 1.833, 10: 1.812, 11: 1.796, 12: 1.782, 13: 1.771, 14: 1.761,
        15: 1.753, 16: 1.746, 17: 1.740, 18: 1.734, 19: 1.729, 20: 1.725, 21: 1.721,
        22: 1.717, 23: 1.714, 24: 1.711, 25: 1.708, 26: 1.706, 27: 1.703, 28: 1.701,
        29: 1.699, 30: 1.697,
    },
}


def _t_crit(df: int, *, two_sided: bool) -> float:
    table = _T_CRIT["two" if two_sided else "one"]
    return table.get(df, 1.960 if two_sided else 1.645)


def _ci_half_width(values: list[float]) -> float:
    """95% CI half-width of the mean across episodes (t-based). 0.0 if < 2 points."""
    n = len(values)
    if n < 2:
        return 0.0
    return _t_crit(n - 1, two_sided=True) * metrics.trial_stdev(values) / math.sqrt(n)


def _sig_worse(leader: dict[str, float], model: dict[str, float]) -> bool:
    """Paired one-sided t-test (95%): is `model` significantly worse than
    `leader` across the episodes they share? Models are scored on the same
    episodes, so the per-episode difference cancels shared episode difficulty
    and is far less noisy than each model's own spread. Two models that trade
    wins across episodes (mean difference near zero) are NOT separated; a model
    that is consistently below the leader is. Ties / < 2 shared episodes -> not
    worse (same tier)."""
    eps = [e for e in leader if e in model]
    diffs = [leader[e] - model[e] for e in eps]
    n = len(diffs)
    if n < 2:
        return False
    mean_d = statistics.fmean(diffs)
    if mean_d <= 0:
        return False
    sd = statistics.stdev(diffs)
    if sd == 0:
        return True  # strictly worse on every shared episode
    t = mean_d / (sd / math.sqrt(n))
    return t > _t_crit(n - 1, two_sided=False)


def _tier_label(index: int) -> str:
    """0->A, 25->Z, 26->AA, 27->AB ... spreadsheet-style so the label never
    overflows past 'Z' into punctuation (a literal '|' would break the table)."""
    label = ""
    index += 1
    while index > 0:
        index, rem = divmod(index - 1, 26)
        label = chr(ord("A") + rem) + label
    return label


def _assign_tiers(ranked: list[ModelStats]) -> list[str]:
    """ranked sorted by avg_f05 descending. A model joins the current tier
    unless it is significantly worse (paired, per-episode) than that tier's
    leader, in which case it opens a new tier and becomes its leader."""
    tiers: list[str] = []
    leader: dict[str, float] | None = None
    idx = -1
    for s in ranked:
        if leader is None or _sig_worse(leader, s.f05_per_episode):
            idx += 1
            leader = s.f05_per_episode
        tiers.append(_tier_label(idx))
    return tiers


def _avg_f1(stats: ModelStats) -> float:
    return stats.avg_f1


def _per_model_alignment(
    agreement: dict[tuple[str, int], dict[str, int]],
    n_models: int,
) -> list[dict]:
    """For each (episode, window): majority = >half of active models voted yes.
    For each model: count its alignment vs that majority into 4 buckets.
    Returns list of {model, with_yes, with_no, broke_yes, broke_no, alignment}.
    """
    # Pre-resolve majority per window
    window_majority: dict[tuple[str, int], bool] = {}
    for key, per_model in agreement.items():
        n_yes = sum(1 for v in per_model.values() if v > 0)
        window_majority[key] = n_yes > n_models / 2
    # Tally per model
    models = {m for per_model in agreement.values() for m in per_model.keys()}
    out: list[dict] = []
    for model in sorted(models):
        wy = wn = by = bn = 0
        for key, per_model in agreement.items():
            voted_yes = per_model.get(model, 0) > 0
            maj_yes = window_majority[key]
            if voted_yes and maj_yes: wy += 1
            elif not voted_yes and not maj_yes: wn += 1
            elif voted_yes and not maj_yes: by += 1
            else: bn += 1
        total = wy + wn + by + bn
        alignment = (wy + wn) / total if total else 0
        out.append({"model": model, "with_yes": wy, "with_no": wn,
                    "broke_yes": by, "broke_no": bn, "alignment": alignment})
    return out

