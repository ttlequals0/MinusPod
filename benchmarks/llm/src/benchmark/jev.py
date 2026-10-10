# Copyright (c) 2025 Samir Jafferali. MIT license: LICENSE.systemone.
"""Jev per-segment evaluation, cached threshold sweeps, and confirmation."""
from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import dataclass, field
from collections.abc import Callable, Iterable, Sequence
from pathlib import Path

import requests

from . import metrics
from .corpus import Episode, stamp_id_windows
from .truth_parser import Ad
from .report.aggregate import DEFAULT_IOU_THRESHOLD, STRICT_IOU_THRESHOLD, ModelStats, _percentile

API_URL = "https://api.typesafe.ai/v1/systemone"
DEFAULT_MODEL = "jev-latest"

# $ per million input tokens (docs.typesafe.ai/models). Output is not billed.
INPUT_COST_PER_MTOK = 0.042

# Hysteresis keeps weak segments within a confident run.
ENTER_THRESHOLD = 0.95
STAY_THRESHOLD = 0.40
MAX_RUN_GAP_SECONDS = 30.0
BRIDGE_SECONDS = 30.0


def line_id(sid: int) -> str:
    return f"L{sid:04d}"


# Shared guidance avoids repeating the definition in every segment question.
GUIDANCE = (
    "Each line of `transcript` is one segment of a podcast episode, prefixed "
    "with its line id. A line is ADVERTISING when it is a sponsor read, a "
    "produced ad spot, a dynamically inserted ad, a hosting-platform pre-roll "
    "or post-roll, a cross-promotion for another show, or a produced segment "
    "asking listeners to subscribe, rate, or follow. Signs of advertising: a "
    "sponsor or brand name, a URL, a promo code, a product pitch, a call to "
    "action, or concentrated marketing copy that is tonally separate from the "
    "conversation. A line is EDITORIAL CONTENT when it is the host or a guest "
    "discussing the episode's subject. A guest talking about their own book or "
    "project, and the host mentioning their own show or Patreon in passing "
    "during conversation, are editorial content, not advertising."
)

# Restore the complete rules from MinusPod's DEFAULT_SYSTEM_PROMPT.
GUIDANCE_FULL = GUIDANCE + (
    "\n\nADVERTISING ALSO INCLUDES:\n"
    "- Short brand tagline spots, roughly 15 to 45 seconds, that carry no "
    "promo code and no URL. They use concentrated marketing copy (\"bringing "
    "you the latest\", \"where innovation lands first\", \"level up your "
    "game\"), are usually voiced by someone other than the host, and feel "
    "tonally separate from the surrounding conversation. A typical shape is "
    "brand name, tagline, product category pitch, brand name again. These are "
    "advertising even though they lack the usual markers.\n"
    "- Hosting-platform insertions such as \"Acast powers the world's best "
    "podcasts\", \"Hosted on Acast\", \"Spotify for Podcasters\", or "
    "\"iHeartRadio\". These usually bookend the episode.\n"
    "- Produced segments promoting a different show, whether inserted by the "
    "network or the platform, even with no promo code.\n"
    "- Network promos: short produced spots advertising other shows.\n"
    "\nTHE DECIDING DISTINCTION:\n"
    "If the HOST says \"check out my other show\" mid-conversation, that is "
    "editorial content. If a PRODUCED SEGMENT, in a different voice or a "
    "different acoustic, promotes another show or the platform itself, that "
    "is advertising.\n"
    "\nNOT ADVERTISING:\n"
    "- Silence, pauses, or dead air. These are ordinary production gaps.\n"
    "- Topic transitions where the host simply changes subject.\n"
    "- A guest discussing their own work, book, or project in the course of "
    "the interview.\n"
    "- The host mentioning their own other shows, social media, or Patreon as "
    "part of conversation.\n"
    "- Brand names that come up in passing during genuine discussion. A line "
    "must carry promotional intent, not merely name a company."
)

NOUL_INSTRUCTIONS = "Line {lid} of `transcript` is advertising, not editorial content."


def build_state(segments: Sequence[dict]) -> str:
    """Window transcript as ID-prefixed lines, the semantic_find shape."""
    return "\n".join(
        f"{line_id(seg['sid'])}| {seg.get('text', '').strip()}" for seg in segments
    )


def build_questions(segments: Sequence[dict]) -> dict[str, dict]:
    """One Noul per segment. Question keys are internal, so the line id is"""
    return {
        f"s{seg['sid']}": {
            "type": "noul",
            "instructions": NOUL_INSTRUCTIONS.format(lid=line_id(seg["sid"])),
        }
        for seg in segments
    }


def build_payload(segments: Sequence[dict], *, model: str = DEFAULT_MODEL,
                  uid: str | None = None, guidance: str = GUIDANCE,
                  metadata: object = None) -> dict:
    """One request covering a whole window."""
    state: dict[str, object] = {"guidance": guidance}
    if metadata is not None:
        state["podcast"] = metadata.podcast_name
        state["episode_title"] = metadata.title
        if metadata.description:
            state["episode_description"] = metadata.description
    state["transcript"] = build_state(segments)
    if uid is not None:
        state["uid"] = uid
    return {
        "state": state,
        "model": model,
        "questions": build_questions(segments),
    }


@dataclass
class WindowResult:
    probabilities: dict[int, float]
    input_tokens: int = 0
    output_tokens: int = 0
    elapsed_ms: int = 0


def parse_response(body: dict) -> WindowResult:
    answers = body.get("answers") or {}
    probs: dict[int, float] = {}
    for key, ans in answers.items():
        if not key.startswith("s"):
            continue
        value = ans.get("noul") if isinstance(ans, dict) else None
        if isinstance(value, (int, float)):
            probs[int(key[1:])] = float(value)
    usage = body.get("usage") or {}
    return WindowResult(
        probabilities=probs,
        input_tokens=int(usage.get("input_tokens") or 0),
        output_tokens=int(usage.get("output_tokens") or 0),
    )


def call_payload(payload: dict, *, api_key: str, timeout: float = 60.0) -> dict:
    resp = requests.post(
        API_URL,
        headers={"Authorization": f"Bearer {api_key}",
                 "Content-Type": "application/json"},
        json=payload,
        timeout=timeout,
    )
    resp.raise_for_status()
    return resp.json()


def hash_payload(payload: dict) -> str:
    """Cache key covering everything that would change the answer."""
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True).encode()).hexdigest()[:16]


def payload_key(segments: Sequence[dict], *, model: str = DEFAULT_MODEL,
                uid: str | None = None) -> str:
    return hash_payload(build_payload(segments, model=model, uid=uid))


class ProbabilityCache:
    """Disk cache of per-window probabilities."""

    def __init__(self, path: Path):
        self.path = path
        self._data: dict[str, dict] = {}
        if path.is_file():
            self._data = json.loads(path.read_text())
        self.hits = 0
        self.misses = 0

    def nouls(self, payload: dict, *, api_key: str | None) -> dict[str, float]:
        """Every Noul answer in the response, keyed as the question was."""
        key = hash_payload(payload)
        started = time.perf_counter()
        entry = self._data.get(key)
        if entry is None:
            if api_key is None:
                raise KeyError(
                    f"no cached answers for {key} and no API key to fetch them")
            body = call_payload(payload, api_key=api_key)
            usage = body.get("usage") or {}
            entry = {
                "probabilities": {
                    k: a["noul"] for k, a in (body.get("answers") or {}).items()
                    if isinstance(a, dict) and isinstance(a.get("noul"), (int, float))
                },
                "input_tokens": int(usage.get("input_tokens") or 0),
                "output_tokens": int(usage.get("output_tokens") or 0),
                "elapsed_ms": round((time.perf_counter() - started) * 1000),
            }
            self._data[key] = entry
            self.misses += 1
        else:
            self.hits += 1
        return entry

    def get_or_call(self, segments: Sequence[dict], *, api_key: str | None,
                    model: str = DEFAULT_MODEL, uid: str | None = None,
                    guidance: str = GUIDANCE,
                    metadata: object = None) -> WindowResult:
        entry = self.nouls(
            build_payload(segments, model=model, uid=uid,
                          guidance=guidance, metadata=metadata),
            api_key=api_key)
        return WindowResult(
            probabilities={
                int(k[1:]) if k.startswith("s") else int(k): v
                for k, v in entry["probabilities"].items()
            },
            input_tokens=entry.get("input_tokens", 0),
            output_tokens=entry.get("output_tokens", 0),
            elapsed_ms=int(entry.get("elapsed_ms") or 0),
        )

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self._data, indent=0, sort_keys=True))


# --- probability curve -> spans ------------------------------------------

def spans_from_probabilities(
    segments: Sequence[dict],
    probabilities: dict[int, float],
    *,
    enter: float = ENTER_THRESHOLD,
    stay: float = STAY_THRESHOLD,
    max_gap: float = MAX_RUN_GAP_SECONDS,
    bridge: float = BRIDGE_SECONDS,
) -> list[dict]:
    """Contiguous runs of ad-ish segments, as ad dicts."""
    if stay > enter:
        raise ValueError("stay threshold must not exceed enter threshold")

    ordered = sorted(segments, key=lambda s: s["start"])
    missing = [s["sid"] for s in ordered if s["sid"] not in probabilities]
    if missing:
        raise ValueError(f"{len(missing)} segment(s) returned no answer")
    above_stay = [probabilities.get(s["sid"], 0.0) >= stay for s in ordered]
    opens = [probabilities.get(s["sid"], 0.0) >= enter for s in ordered]

    ads: list[dict] = []
    runs: list[tuple[int, int]] = []
    i = 0
    while i < len(ordered):
        if not above_stay[i]:
            i += 1
            continue
        j = i
        # Contained segments must not shorten the current run's end.
        frontier = ordered[i]["end"]
        while (j + 1 < len(ordered) and above_stay[j + 1]
               and ordered[j + 1]["start"] - frontier <= max_gap):
            j += 1
            frontier = max(frontier, ordered[j]["end"])
        # A run of merely-above-stay segments with no confident member is not
        # an ad; it is the tail of an ordinary conversation.
        if any(opens[k] for k in range(i, j + 1)):
            runs.append((i, j))
        i = j + 1

    for lo, hi in _bridged(runs, ordered, bridge):
        members = ordered[lo:hi + 1]
        # Not members[-1]: a contained segment sorts last but ends earliest, and
        # taking its end truncates the span.
        last = max(members, key=lambda m: m["end"])
        ads.append({
            "start": members[0]["start"],
            "end": last["end"],
            "confidence": max(
                probabilities.get(m["sid"], 0.0) for m in members),
            "start_id": members[0]["sid"],
            "end_id": last["sid"],
        })
    return ads


def _bridged(runs: list[tuple[int, int]], ordered: Sequence[dict],
             bridge: float) -> list[tuple[int, int]]:
    """Join runs separated by a short stretch of low-scoring speech."""
    if not runs:
        return []
    out = [runs[0]]
    for lo, hi in runs[1:]:
        prev_lo, prev_hi = out[-1]
        between = ordered[prev_hi + 1:lo]
        spanned = sum(s["end"] - s["start"] for s in between)
        prev_end = max(s["end"] for s in ordered[prev_lo:prev_hi + 1])
        wall = ordered[lo]["start"] - prev_end
        if between and spanned <= bridge and wall <= bridge:
            out[-1] = (prev_lo, hi)
        else:
            out.append((lo, hi))
    return out


# --- oracle --------------------------------------------------------------

def oracle_probabilities(
    segments: Sequence[dict],
    truth_ads: Sequence[Ad],
    *,
    policy: str = "overlap",
) -> dict[int, float]:
    """Per-segment probabilities a perfect judge would return."""
    if policy not in ("overlap", "majority"):
        raise ValueError(f"unknown oracle policy: {policy}")

    probs: dict[int, float] = {}
    for seg in segments:
        duration = max(0.0, seg["end"] - seg["start"])
        covered = 0.0
        for ad in truth_ads:
            covered += max(
                0.0, min(seg["end"], ad.end) - max(seg["start"], ad.start))
        if policy == "overlap":
            hit = covered > 0
        else:
            hit = duration > 0 and covered / duration > 0.5
        probs[seg["sid"]] = 1.0 if hit else 0.0
    return probs


# --- episode scoring -----------------------------------------------------

# --- Pass B: span-level confirmation --------------------------------------

# Confirm assembled spans with context; isolated segments can omit sponsor cues.
CONFIRM_QUESTIONS = {
    "is_ad": (
        "Taken as a whole, `span` is an advertisement: a sponsor read, a "
        "produced ad spot, a dynamically inserted ad, a platform pre-roll or "
        "post-roll, or a cross-promotion for another show."
    ),
    "promotional_language": (
        "`span` contains explicit promotional language: a sponsor or brand "
        "name being promoted, a URL, a promo code, a product pitch, or a call "
        "to action."
    ),
    "produced_insert": (
        "`span` is a produced segment that is tonally separate from "
        "`content_before` and `content_after`, rather than a continuation of "
        "the same conversation."
    ),
    "guest_own_work": (
        "`span` is a guest discussing their own book, project, or work as "
        "part of the episode's interview."
    ),
    "host_organic": (
        "`span` is the host mentioning their own show, Patreon, merch, or "
        "social media in passing during conversation, rather than a produced "
        "promotional insert."
    ),
}

CONTEXT_SEGMENTS = 2


def _joined(segments: Sequence[dict]) -> str:
    return " ".join(s.get("text", "").strip() for s in segments)


def build_confirm_payload(span_segments: Sequence[dict],
                          all_segments: Sequence[dict], *,
                          model: str = DEFAULT_MODEL,
                          uid: str | None = None,
                          context: int = CONTEXT_SEGMENTS) -> dict:
    ordered = sorted(all_segments, key=lambda s: s["start"])
    sids = {s["sid"] for s in span_segments}
    idx = [i for i, s in enumerate(ordered) if s["sid"] in sids]
    lo, hi = (min(idx), max(idx)) if idx else (0, -1)

    state: dict[str, object] = {
        "guidance": GUIDANCE,
        "content_before": _joined(ordered[max(0, lo - context):lo]),
        "span": _joined(span_segments),
        "content_after": _joined(ordered[hi + 1:hi + 1 + context]),
    }
    if uid is not None:
        state["uid"] = uid
    return {
        "state": state,
        "model": model,
        "questions": {
            name: {"type": "noul", "instructions": text}
            for name, text in CONFIRM_QUESTIONS.items()
        },
    }


@dataclass(frozen=True)
class ConfirmPolicy:
    """Code owns the decision; the model only supplies the signals."""
    min_is_ad: float = 0.5
    min_promotional: float = 0.5
    max_exclusion: float = 0.5

    def accepts(self, answers: dict[str, float]) -> bool:
        get = lambda k: answers.get(k, 0.0)  # noqa: E731
        if get("is_ad") < self.min_is_ad:
            return False
        if get("promotional_language") < self.min_promotional:
            return False
        excluded = max(get("guest_own_work"), get("host_organic"))
        return excluded <= self.max_exclusion


def confirm_spans(ads: Sequence[dict], all_segments: Sequence[dict],
                  answer_source: Callable[[dict], dict[str, float]], *,
                  policy: ConfirmPolicy) -> list[dict]:
    """Drop candidate spans the confirmation questions reject."""
    kept = []
    for ad in ads:
        # By time, not by the id range: canonicalize_ads merges spans without
        # updating end_id, so a merged span's ids no longer bound it.
        members = [s for s in sorted(all_segments, key=lambda s: s["start"])
                   if s["start"] < ad["end"] and s["end"] > ad["start"]]
        if not members:
            continue
        answers = answer_source(
            build_confirm_payload(members, all_segments))
        if policy.accepts(answers):
            ad = dict(ad)
            ad["confirm"] = answers
            kept.append(ad)
    return kept


def aggregate_passes(results: Sequence[WindowResult]) -> WindowResult:
    """Mean probability per segment across independent passes."""
    if not results:
        return WindowResult(probabilities={})
    sids = {sid for r in results for sid in r.probabilities}
    return WindowResult(
        probabilities={
            sid: sum(r.probabilities.get(sid, 0.0) for r in results) / len(results)
            for sid in sids
        },
        input_tokens=sum(r.input_tokens for r in results),
        output_tokens=sum(r.output_tokens for r in results),
        elapsed_ms=sum(r.elapsed_ms for r in results),
    )


@dataclass
class EpisodeScore:
    ep_id: str
    is_no_ad: bool
    f1: float = 0.0
    f05: float = 0.0
    precision: float = 0.0
    recall: float = 0.0
    start_mae: float | None = None
    end_mae: float | None = None
    no_ad_passed: bool | None = None
    no_ad_fps: int = 0
    predictions: list[dict] = field(default_factory=list)
    input_tokens: int = 0


def score_episode(
    episode: Episode,
    windows: Sequence[Sequence[dict]],
    probability_source: Callable[[Sequence[dict]], WindowResult],
    *,
    enter: float = ENTER_THRESHOLD,
    stay: float = STAY_THRESHOLD,
    confirm: Callable[[Sequence[dict], Sequence[dict]], list[dict]] | None = None,
) -> EpisodeScore:
    """Run Pass A over every window, stitch, and score."""
    per_window_ads: list[list[dict]] = []
    input_tokens = 0
    for segs in windows:
        result = probability_source(segs)
        input_tokens += result.input_tokens
        per_window_ads.append(
            spans_from_probabilities(
                segs, result.probabilities, enter=enter, stay=stay))

    flat = [ad for window in per_window_ads for ad in window]
    flat = metrics.canonicalize_ads(flat)
    if confirm is not None:
        all_segments = [s for w in windows for s in w]
        flat = confirm(flat, all_segments)
        per_window_ads = [[a for a in flat]] if flat else [[]]
    preds = [(ad["start"], ad["end"]) for ad in flat]

    score = EpisodeScore(
        ep_id=episode.ep_id,
        is_no_ad=episode.truth.is_no_ad_episode,
        predictions=flat,
        input_tokens=input_tokens,
    )

    if episode.truth.is_no_ad_episode:
        per_window_spans = [[(a["start"], a["end"]) for a in w]
                            for w in per_window_ads]
        res = metrics.no_ad_score(per_window_spans)
        score.no_ad_passed = res.passed
        score.no_ad_fps = res.false_positive_count
        return score

    truth_ranges = metrics.canonicalize_spans(
        [(ad.start, ad.end) for ad in episode.truth.ads])
    result = metrics.match_predictions(
        preds, truth_ranges, threshold=metrics_iou_threshold())
    score.f1 = result.f1
    score.f05 = result.fbeta(0.5)
    score.precision = result.precision
    score.recall = result.recall
    boundary = metrics.boundary_error(preds, truth_ranges, result.matches)
    if boundary is not None:
        score.start_mae = boundary.start_mae
        score.end_mae = boundary.end_mae
    return score


# --- threshold generalization ---------------------------------------------

THRESHOLD_GRID = tuple(
    (enter, stay)
    for enter in (0.70, 0.80, 0.90, 0.95, 0.97, 0.98, 0.99)
    for stay in (0.30, 0.40, 0.50, 0.60, 0.70)
    if stay <= enter
)


def mean_f05(scores: Sequence[EpisodeScore]) -> float:
    ad = [s for s in scores if not s.is_no_ad]
    return sum(s.f05 for s in ad) / len(ad) if ad else 0.0


def tune_thresholds(episode_ids: Sequence[str],
                    score_fn: Callable[[str, float, float], EpisodeScore],
                    *, grid: Sequence[tuple[float, float]] = THRESHOLD_GRID
                    ) -> tuple[float, float, float]:
    """Pick the (enter, stay) maximizing mean F0.5 over ``episode_ids``."""
    best = (0.0, grid[0][0], grid[0][1])
    for enter, stay in grid:
        f05 = mean_f05([score_fn(ep, enter, stay) for ep in episode_ids])
        if f05 > best[0]:
            best = (f05, enter, stay)
    return best[1], best[2], best[0]


@dataclass
class Fold:
    held_out: tuple[str, ...]
    enter: float
    stay: float
    train_f05: float
    test_f05: float
    fixed_f05: float


def cross_validate(episode_ids: Sequence[str],
                   score_fn: Callable[[str, float, float], EpisodeScore],
                   *, fold_size: int = 2,
                   fixed: tuple[float, float] = (ENTER_THRESHOLD, STAY_THRESHOLD),
                   grid: Sequence[tuple[float, float]] = THRESHOLD_GRID
                   ) -> list[Fold]:
    """Tune on all but ``fold_size`` episodes, score those, repeat."""
    ids = list(episode_ids)
    folds = []
    for i in range(0, len(ids), fold_size):
        held = tuple(ids[i:i + fold_size])
        if not held:
            continue
        train = [e for e in ids if e not in held]
        enter, stay, train_f05 = tune_thresholds(train, score_fn, grid=grid)
        folds.append(Fold(
            held_out=held,
            enter=enter,
            stay=stay,
            train_f05=train_f05,
            test_f05=mean_f05([score_fn(e, enter, stay) for e in held]),
            fixed_f05=mean_f05([score_fn(e, *fixed) for e in held]),
        ))
    return folds


def metrics_iou_threshold() -> float:
    return DEFAULT_IOU_THRESHOLD


def episode_windows(episode: Episode) -> list[list[dict]]:
    return stamp_id_windows(episode)


def estimate_input_tokens(windows: Iterable[Sequence[dict]]) -> int:
    """Rough token count for a whole-episode pass: state plus questions."""
    total = 0
    for segs in windows:
        payload = build_payload(segs)
        total += len(json.dumps(payload)) // 4
    return total


def api_key_from_env() -> str | None:
    return os.environ.get("TYPESAFE_API_KEY")


# --- report integration ----------------------------------------------------

def merge_into_stats(stats: dict, episodes: Sequence[Episode], *,
                     cache_path: Path, enter: float = ENTER_THRESHOLD,
                     stay: float = STAY_THRESHOLD, passes: int = 1) -> None:
    """Fold the Jev rows into a report stats mapping."""

    cache = ProbabilityCache(cache_path)
    api_key = None

    live: list[EpisodeScore] = []
    ceiling: list[EpisodeScore] = []
    elapsed: list[int] = []
    trial_f1s: dict[str, list[float]] = {}
    uids = [None] if passes == 1 else [f"pass-{i}" for i in range(passes)]
    calls = 0

    def _answers(segs, episode, uid):
        return cache.get_or_call(segs, api_key=api_key, guidance=GUIDANCE_FULL,
                                 metadata=episode.metadata, uid=uid)

    for episode in episodes:
        windows = episode_windows(episode)
        calls += len(windows) * len(uids)

        def live_source(segs, _ep=episode):
            results = [_answers(segs, _ep, u) for u in uids]
            # Per call, not the aggregate's sum, or the percentiles describe a
            # whole repeat rather than one request.
            elapsed.extend(r.elapsed_ms for r in results)
            return aggregate_passes(results)

        def ceiling_source(segs, _ep=episode):
            return WindowResult(probabilities=oracle_probabilities(
                segs, _ep.truth.ads, policy="overlap"))

        live.append(score_episode(episode, windows, live_source, enter=enter, stay=stay))
        ceiling.append(score_episode(episode, windows, ceiling_source, enter=enter, stay=stay))

        # Scoring each repeat on its own is the only figure comparable to the
        # chat rows' trial stdev; the aggregated row is a different estimator.
        if len(uids) > 1 and not episode.truth.is_no_ad_episode:
            trial_f1s[episode.ep_id] = [
                score_episode(episode, windows,
                              lambda segs, _e=episode, _u=u: _answers(segs, _e, _u),
                              enter=enter, stay=stay).f1
                for u in uids]

    def _row(name: str, scores: list[EpisodeScore]) -> ModelStats:
        def mean(xs):
            return sum(xs) / len(xs) if xs else 0.0

        ms = ModelStats(model=name)
        ad = [s for s in scores if not s.is_no_ad]
        ms.f1_per_episode = {s.ep_id: s.f1 for s in ad}
        ms.f05_per_episode = {s.ep_id: s.f05 for s in ad}
        ms.precision_per_episode = {s.ep_id: s.precision for s in ad}
        ms.recall_per_episode = {s.ep_id: s.recall for s in ad}
        ms.avg_f1 = mean([s.f1 for s in ad])
        ms.avg_f05 = mean([s.f05 for s in ad])
        ms.avg_precision = mean([s.precision for s in ad])
        ms.avg_recall = mean([s.recall for s in ad])
        by_id = {e.ep_id: e for e in episodes}
        starts: list[float] = []
        ends: list[float] = []
        s_biases: list[float] = []
        e_biases: list[float] = []
        for s in ad:
            preds = [(a["start"], a["end"]) for a in s.predictions]
            truth = metrics.canonicalize_spans(
                [(a.start, a.end) for a in by_id[s.ep_id].truth.ads])
            res = metrics.match_predictions(preds, truth, threshold=metrics_iou_threshold())
            strict = metrics.match_predictions(preds, truth, threshold=STRICT_IOU_THRESHOLD)
            ms.f05_strict_per_episode[s.ep_id] = strict.fbeta(0.5)
            ms.tp_total += res.true_positives
            ms.fp_total += res.false_positives
            ms.fn_total += res.false_negatives
            be = metrics.boundary_error(preds, truth, res.matches)
            if be is not None:
                starts.append(be.start_mae)
                ends.append(be.end_mae)
                s_biases.append(be.start_bias)
                e_biases.append(be.end_bias)
        ms.avg_f05_strict = mean(ms.f05_strict_per_episode.values()) if ms.f05_strict_per_episode else None
        ms.boundary_start_mae = mean(starts) if starts else None
        ms.boundary_end_mae = mean(ends) if ends else None
        ms.boundary_start_bias = mean(s_biases) if s_biases else None
        ms.boundary_end_bias = mean(e_biases) if e_biases else None
        ms.detected_ads_total = sum(len(s.predictions) for s in scores)
        cost = sum(s.input_tokens for s in scores) * INPUT_COST_PER_MTOK / 1e6 / max(len(episodes), 1)
        ms.input_episode_cost = cost
        ms.total_episode_cost = cost
        ms.cost_episodes = len(episodes)
        ms.no_ad_pass = {s.ep_id: bool(s.no_ad_passed) for s in scores if s.is_no_ad}
        ms.no_ad_fp_count = {s.ep_id: s.no_ad_fps for s in scores if s.is_no_ad}
        ms.call_count = calls
        ms.attempted_count = calls
        ms.json_compliance_mean = 1.0
        ms.extraction_method_counts = {"noul_spans": calls}
        return ms

    stats["jev"] = _row("jev", live)
    stats["jev-ceiling"] = _row("jev-ceiling (oracle)", ceiling)
    if trial_f1s:
        stats["jev"].f1_stdev_per_episode = {
            ep_id: metrics.trial_stdev(f1s) for ep_id, f1s in trial_f1s.items()}
    # The ceiling row scores the same payloads; only the answers differ.
    ceil = stats["jev-ceiling"]
    ceil.total_episode_cost = stats["jev"].total_episode_cost
    ceil.input_episode_cost = stats["jev"].input_episode_cost
    timed = sorted(t for t in elapsed if t)
    if timed:
        stats["jev"].p50_call_latency_ms = _percentile(timed, 50)
        stats["jev"].p90_call_latency_ms = _percentile(timed, 90)
        stats["jev"].p95_call_latency_ms = _percentile(timed, 95)
        stats["jev"].max_call_latency_ms = float(max(timed))
        stats["jev-ceiling"].p50_call_latency_ms = stats["jev"].p50_call_latency_ms
        stats["jev-ceiling"].p95_call_latency_ms = stats["jev"].p95_call_latency_ms
