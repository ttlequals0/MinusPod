"""Accuracy + JSON compliance metrics for benchmark calls."""
from __future__ import annotations

import math
import statistics
from dataclasses import dataclass, field


EXACT_METHOD_SCORES: dict[str, float] = {
    "json_array_direct": 1.0,
    "json_object_segments_key": 0.85,
    "json_object_single_ad": 0.7,
    "json_object_no_ads": 1.0,
    "markdown_code_block": 0.6,
    "regex_json_array": 0.4,
    "bracket_fallback": 0.2,
}

PREFIX_METHOD_SCORES: tuple[tuple[str, float], ...] = (
    ("json_object_window_", 0.85),
    ("json_object_", 0.85),
)


@dataclass
class Match:
    pred_index: int
    truth_index: int
    iou: float


@dataclass
class AccuracyResult:
    iou_threshold: float
    true_positives: int
    false_positives: int
    false_negatives: int
    matches: list[Match]

    @property
    def precision(self) -> float:
        denom = self.true_positives + self.false_positives
        return self.true_positives / denom if denom else 0.0

    @property
    def recall(self) -> float:
        denom = self.true_positives + self.false_negatives
        return self.true_positives / denom if denom else 0.0

    @property
    def f1(self) -> float:
        p, r = self.precision, self.recall
        return 2 * p * r / (p + r) if (p + r) > 0 else 0.0


@dataclass
class BoundaryError:
    start_mae: float
    end_mae: float


@dataclass
class NoAdResult:
    false_positive_count: int
    hallucinated_window_fraction: float
    passed: bool


def iou(a: tuple[float, float], b: tuple[float, float]) -> float:
    overlap = max(0.0, min(a[1], b[1]) - max(a[0], b[0]))
    if overlap == 0:
        return 0.0
    union = max(a[1], b[1]) - min(a[0], b[0])
    return overlap / union if union > 0 else 0.0


def match_predictions(
    predictions: list[tuple[float, float]],
    truths: list[tuple[float, float]],
    *,
    threshold: float,
) -> AccuracyResult:
    pairs: list[tuple[float, int, int]] = []
    for pi, p in enumerate(predictions):
        for ti, t in enumerate(truths):
            score = iou(p, t)
            if score >= threshold:
                pairs.append((score, pi, ti))

    pairs.sort(key=lambda x: x[0], reverse=True)
    used_pred: set[int] = set()
    used_truth: set[int] = set()
    matches: list[Match] = []
    for score, pi, ti in pairs:
        if pi in used_pred or ti in used_truth:
            continue
        used_pred.add(pi)
        used_truth.add(ti)
        matches.append(Match(pred_index=pi, truth_index=ti, iou=score))

    tp = len(matches)
    fp = len(predictions) - tp
    fn = len(truths) - tp
    return AccuracyResult(iou_threshold=threshold, true_positives=tp, false_positives=fp, false_negatives=fn, matches=matches)


def boundary_error(
    predictions: list[tuple[float, float]],
    truths: list[tuple[float, float]],
    matches: list[Match],
) -> BoundaryError | None:
    if not matches:
        return None
    starts = [abs(predictions[m.pred_index][0] - truths[m.truth_index][0]) for m in matches]
    ends = [abs(predictions[m.pred_index][1] - truths[m.truth_index][1]) for m in matches]
    return BoundaryError(start_mae=statistics.fmean(starts), end_mae=statistics.fmean(ends))


def no_ad_score(per_window_predictions: list[list[tuple[float, float]]]) -> NoAdResult:
    fp_count = sum(len(w) for w in per_window_predictions)
    non_empty = sum(1 for w in per_window_predictions if w)
    fraction = non_empty / len(per_window_predictions) if per_window_predictions else 0.0
    return NoAdResult(false_positive_count=fp_count, hallucinated_window_fraction=fraction, passed=(fp_count == 0))


def compliance_score(extraction_method: str | None) -> float:
    if extraction_method is None:
        return 0.0
    if extraction_method in EXACT_METHOD_SCORES:
        return EXACT_METHOD_SCORES[extraction_method]
    for prefix, score in PREFIX_METHOD_SCORES:
        if extraction_method.startswith(prefix):
            return score
    return 0.5


@dataclass
class SchemaViolations:
    missing_required: int = 0
    wrong_type: int = 0
    extra_keys: int = 0
    out_of_range: int = 0
    extra_key_names: list[str] = field(default_factory=list)


REQUIRED_AD_KEYS = ("start", "end")
KNOWN_OPTIONAL_KEYS = (
    "confidence", "reason", "advertiser", "description",
    "continues_from_previous", "continues_in_next",
    "start_time", "end_time", "text",
)


def schema_audit(parsed_ads: list[dict]) -> SchemaViolations:
    v = SchemaViolations()
    extras: set[str] = set()
    for ad in parsed_ads:
        for req in REQUIRED_AD_KEYS:
            if req not in ad and f"{req}_time" not in ad:
                v.missing_required += 1
        for key, val in ad.items():
            if key in REQUIRED_AD_KEYS or key.endswith("_time"):
                if not isinstance(val, (int, float)):
                    v.wrong_type += 1
                elif val < 0:
                    v.out_of_range += 1
            elif key == "confidence":
                if not isinstance(val, (int, float)) or not 0 <= val <= 1:
                    v.wrong_type += 1
            elif key not in KNOWN_OPTIONAL_KEYS:
                v.extra_keys += 1
                extras.add(key)
    v.extra_key_names = sorted(extras)
    return v


def trial_stdev(values: list[float]) -> float:
    if len(values) < 2:
        return 0.0
    return statistics.stdev(values)
