"""Accuracy + JSON compliance metrics for benchmark calls."""
from __future__ import annotations

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
    # Synthetic method for a successful ID-addressing-mode parse (runner
    # ._parse_id_response). parse_id_ads_from_response does not expose the
    # underlying JSON-wrapping detail extract_json_ads_array would have
    # reported, so this scores every clean id-contract response the same
    # as the cleanest timestamp-mode parse rather than re-parsing the text
    # a second time just to recover that detail.
    "segment_id_direct": 1.0,
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

    def fbeta(self, beta: float) -> float:
        """F-beta. beta<1 weights precision over recall (beta=0.5 -> precision 2x)."""
        p, r = self.precision, self.recall
        b2 = beta * beta
        denom = b2 * p + r
        return (1 + b2) * p * r / denom if denom > 0 else 0.0


@dataclass
class BoundaryError:
    start_mae: float
    end_mae: float
    # Mean signed error, predicted minus truth. Negative start / positive end
    # means the cut extends beyond the ad into surrounding content.
    start_bias: float
    end_bias: float


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


CANONICAL_GAP_SECONDS = 15.0


def canonicalize_spans(
    spans: list[tuple[float, float]], *, gap: float = CANONICAL_GAP_SECONDS
) -> list[tuple[float, float]]:
    """Merge spans separated by less than `gap` seconds.

    One span = one contiguous ad break, the detection prompt's merge rule.
    Applied to predictions and truths alike before matching.
    """
    if not spans:
        return []
    ordered = sorted(spans)
    out = [list(ordered[0])]
    for s, e in ordered[1:]:
        if s - out[-1][1] < gap:
            out[-1][1] = max(out[-1][1], e)
        else:
            out.append([s, e])
    return [(s, e) for s, e in out]


def canonicalize_ads(
    ads: list[dict], *, gap: float = CANONICAL_GAP_SECONDS
) -> list[dict]:
    """Dict-level merge on start/end so ads stay aligned with their spans."""
    if not ads:
        return []
    ordered = sorted((dict(a) for a in ads),
                     key=lambda a: (a["start"], a["end"]))
    out = [ordered[0]]
    for a in ordered[1:]:
        cur = out[-1]
        if a["start"] - cur["end"] < gap:
            cur["end"] = max(cur["end"], a["end"])
            ca, cb = cur.get("confidence"), a.get("confidence")
            if isinstance(cb, (int, float)) and (
                    not isinstance(ca, (int, float)) or cb > ca):
                cur["confidence"] = cb
        else:
            out.append(a)
    return out


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
    start_deltas = [predictions[m.pred_index][0] - truths[m.truth_index][0] for m in matches]
    end_deltas = [predictions[m.pred_index][1] - truths[m.truth_index][1] for m in matches]
    return BoundaryError(
        start_mae=statistics.fmean(abs(d) for d in start_deltas),
        end_mae=statistics.fmean(abs(d) for d in end_deltas),
        start_bias=statistics.fmean(start_deltas),
        end_bias=statistics.fmean(end_deltas),
    )


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
    # Ads carrying a usable segment category, and ads carrying none. Counted
    # separately from missing_required: an ad with no category is still a
    # usable detection, it just stays uncategorized.
    category_present: int = 0
    category_missing: int = 0


REQUIRED_AD_KEYS = ("start", "end")


def _production_known_keys() -> tuple[str, ...]:
    """Mirror the production parser's known-field set so the benchmark's
    schema_audit doesn't penalize models for emitting fields the live app accepts.
    """
    extras: set[str] = set()
    try:
        from utils.constants import STRUCTURAL_FIELDS, SPONSOR_PRIORITY_FIELDS  # type: ignore[import-not-found]
        extras |= {k.lower() for k in STRUCTURAL_FIELDS}
        extras |= {k.lower() for k in SPONSOR_PRIORITY_FIELDS}
    except Exception:
        pass
    return tuple(extras)


KNOWN_OPTIONAL_KEYS = (
    "confidence", "reason", "advertiser", "description",
    "continues_from_previous", "continues_in_next",
    "start_time", "end_time", "text",
    # The prompt asks for this one, so emitting it is compliance, not an
    # extra key. It was scored as a violation before, which penalized the
    # models that did what they were told.
    "category",
) + _production_known_keys()


def _fallback_categories() -> tuple[str, ...]:
    """Production's vocabulary, or a copy of it when config is not importable."""
    try:
        from config import SEGMENT_CATEGORIES  # type: ignore[import-not-found]
        return tuple(SEGMENT_CATEGORIES)
    except Exception:
        return ("sponsor", "cross_promo", "self_promo", "interaction",
                "intro", "outro", "recap")


def _fallback_resolve_category(ad: dict):
    value = ad.get("category")
    if isinstance(value, str) and value.strip().lower() in _fallback_categories():
        return value.strip().lower()
    return None


# Resolved once at import: which resolver scored the run. Without this the
# compliance figures differ silently between an environment that can import the
# app and one that cannot.
try:
    from ad_detector.prompts import resolve_ad_category as _resolve_category  # type: ignore[import-not-found]
    CATEGORY_RESOLVER = "production"
except Exception:
    _resolve_category = _fallback_resolve_category
    CATEGORY_RESOLVER = "fallback"


def schema_audit(parsed_ads: list[dict]) -> SchemaViolations:
    v = SchemaViolations()
    extras: set[str] = set()
    for ad in parsed_ads:
        if _resolve_category(ad):
            v.category_present += 1
        else:
            v.category_missing += 1
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
