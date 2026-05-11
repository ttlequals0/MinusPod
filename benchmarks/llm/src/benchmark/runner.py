"""Async fan-out runner: dispatches LLM calls and writes calls.jsonl."""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from utils.time import utc_now_iso

from . import llm, parsing, pricing
from .config import BenchmarkConfig
from .corpus import Episode
from .metrics import compliance_score, schema_audit
from .storage import (
    SCHEMA_VERSION,
    append_jsonl,
    hash_prompt,
    read_jsonl,
    sanitize_error,
    scan_calls,
    write_prompt,
    write_response,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class WorkUnit:
    model_id: str
    provider_name: str
    episode_id: str
    trial: int
    window_index: int


@dataclass
class RunPaths:
    calls_jsonl: Path
    episode_results_jsonl: Path
    responses_dir: Path
    prompts_dir: Path

    @classmethod
    def for_root(cls, results_root: Path) -> "RunPaths":
        raw = results_root / "raw"
        return cls(
            calls_jsonl=raw / "calls.jsonl",
            episode_results_jsonl=raw / "episode_results.jsonl",
            responses_dir=raw / "responses",
            prompts_dir=raw / "prompts",
        )


@dataclass
class RunStats:
    total_units: int = 0
    skipped: int = 0
    completed: int = 0
    errored: int = 0


def build_work_list(
    cfg: BenchmarkConfig,
    episodes: list[Episode],
    *,
    completed: set[tuple[str, str, int, int, str]],
    prompt_hashes: dict[tuple[str, str, int, int], str],
    include_errored: bool = False,
    error_keys: set[tuple[str, str, int, int, str]] | None = None,
) -> tuple[list[WorkUnit], int]:
    """Return (units_to_run, count_skipped).

    A WorkUnit is included if its (model, episode, trial, window_index, prompt_hash)
    is not in ``completed``, OR is in ``completed`` but only via an errored record
    and ``include_errored`` is True.
    """
    skipped = 0
    units: list[WorkUnit] = []
    for model in cfg.models:
        if model.deprecated:
            continue
        for ep in episodes:
            for trial in range(cfg.run.trials):
                for w in ep.windows:
                    ph = prompt_hashes.get((model.id, ep.ep_id, trial, w.index), "")
                    key = (model.id, ep.ep_id, trial, w.index, ph)
                    if key in completed:
                        if include_errored and error_keys and key in error_keys:
                            units.append(WorkUnit(model.id, model.provider, ep.ep_id, trial, w.index))
                        else:
                            skipped += 1
                            continue
                    else:
                        units.append(WorkUnit(model.id, model.provider, ep.ep_id, trial, w.index))
    return units, skipped


def precompute_prompt_hashes(
    cfg: BenchmarkConfig,
    episodes: list[Episode],
    *,
    system_prompt: str,
) -> dict[tuple[str, str, int, int], str]:
    """User prompt is identical across (model, trial); cache once per (episode, window).

    Hash still varies per model (model id is part of the hash) and we expand to
    every (model, episode, trial, window_index) tuple in the result so callers
    have a flat lookup.
    """
    user_prompts: dict[tuple[str, int], str] = {
        (ep.ep_id, w.index): _build_user_prompt(ep, w, total_windows=len(ep.windows))
        for ep in episodes for w in ep.windows
    }
    active_models = [m for m in cfg.models if not m.deprecated]
    hash_by_model_window: dict[tuple[str, str, int], str] = {
        (model.id, ep_id, w_idx): hash_prompt(
            system_prompt=system_prompt,
            user_prompt=user_prompts[(ep_id, w_idx)],
            model=model.id,
            temperature=cfg.run.temperature,
        )
        for model in active_models
        for (ep_id, w_idx) in user_prompts
    }
    return {
        (model_id, ep_id, trial, w_idx): h
        for (model_id, ep_id, w_idx), h in hash_by_model_window.items()
        for trial in range(cfg.run.trials)
    }


def _build_user_prompt(episode: Episode, window, *, total_windows: int) -> str:
    description = (episode.metadata.description or "").strip()
    description_section = f"\n\nEpisode description: {description}\n" if description else ""
    return parsing.format_window_prompt(
        podcast_name=episode.metadata.podcast_name,
        episode_title=episode.metadata.title,
        description_section=description_section,
        transcript_lines=window.transcript_lines,
        window_index=window.index,
        total_windows=total_windows,
        window_start=window.start,
        window_end=window.end,
    )


async def run(
    cfg: BenchmarkConfig,
    episodes: list[Episode],
    *,
    paths: RunPaths,
    pricing_snapshot: pricing.PricingSnapshot,
    include_errored: bool = False,
) -> RunStats:
    system_prompt = parsing.get_static_system_prompt()
    prompt_hashes = precompute_prompt_hashes(cfg, episodes, system_prompt=system_prompt)

    completed, err_keys = scan_calls(paths.calls_jsonl)
    units, skipped = build_work_list(
        cfg, episodes,
        completed=completed,
        prompt_hashes=prompt_hashes,
        include_errored=include_errored,
        error_keys=err_keys if include_errored else None,
    )

    stats = RunStats(total_units=len(units) + skipped, skipped=skipped)

    if not units:
        return stats

    global_sema = asyncio.Semaphore(cfg.run.max_concurrent_calls)
    per_provider_sema = {name: asyncio.Semaphore(cfg.run.max_concurrent_per_provider) for name in cfg.providers}
    episodes_by_id = {ep.ep_id: ep for ep in episodes}

    async def execute(unit: WorkUnit) -> None:
        nonlocal stats
        provider_cfg = cfg.providers[unit.provider_name]
        episode = episodes_by_id[unit.episode_id]
        window = episode.windows[unit.window_index]
        user_prompt = _build_user_prompt(episode, window, total_windows=len(episode.windows))
        ph = prompt_hashes[(unit.model_id, unit.episode_id, unit.trial, unit.window_index)]

        async with global_sema, per_provider_sema[unit.provider_name]:
            call_id = _call_id(unit, ph)
            t0 = time.perf_counter()
            error_payload: dict | None = None
            response_text = ""
            input_tokens = output_tokens = 0
            json_format_used = "n/a"
            underlying_provider = unit.provider_name
            parsed_ads: list[dict] = []
            extraction_method: str | None = None
            comp = 0.0
            in_cost = out_cost = total_cost = 0.0
            response_path: Path | None = None

            try:
                resp = await llm.call_with_retry(
                    provider=provider_cfg,
                    model_id=unit.model_id,
                    system_prompt=system_prompt,
                    user_prompt=user_prompt,
                    temperature=cfg.run.temperature,
                    max_tokens=cfg.run.max_tokens,
                    timeout=cfg.run.timeout_seconds,
                    response_format=cfg.run.response_format,
                    max_retries=cfg.run.max_retries,
                )
                response_text = resp.text
                input_tokens = resp.input_tokens
                output_tokens = resp.output_tokens
                json_format_used = resp.json_format_used
                underlying_provider = resp.underlying_provider
            except Exception as e:
                error_payload = sanitize_error(e)

            elapsed_ms = int((time.perf_counter() - t0) * 1000)
            try:
                parsed_ads, extraction_method = _parse_response(response_text)
                comp = compliance_score(extraction_method)
                cost_lookup = pricing_snapshot.lookup(unit.model_id)
                if cost_lookup is not None:
                    in_cost, out_cost, total_cost = pricing.cost_usd(
                        cost_lookup, input_tokens=input_tokens, output_tokens=output_tokens
                    )
                response_path = write_response(paths.responses_dir, call_id, response_text)
                write_prompt(paths.prompts_dir, ph, user_prompt)
            except Exception as post_e:
                if error_payload is None:
                    error_payload = sanitize_error(post_e)
                else:
                    logger.exception("post-LLM error after LLM also errored: %s", post_e)
            violations = schema_audit(parsed_ads)

            record = {
                "schema_version": SCHEMA_VERSION,
                "call_id": call_id,
                "timestamp": utc_now_iso(),
                "model": unit.model_id,
                "provider_config": unit.provider_name,
                "underlying_provider": underlying_provider,
                "episode_id": unit.episode_id,
                "trial": unit.trial,
                "window_index": unit.window_index,
                "temperature": cfg.run.temperature,
                "prompt_hash": ph,
                "response_time_ms": elapsed_ms,
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "input_cost_usd_at_runtime": round(in_cost, 6),
                "output_cost_usd_at_runtime": round(out_cost, 6),
                "total_cost_usd_at_runtime": round(total_cost, 6),
                "pricing_snapshot_id_at_runtime": pricing_snapshot.captured_at,
                "json_format_used": json_format_used,
                "response_path": str(response_path.relative_to(paths.calls_jsonl.parent)) if response_path else None,
                "prompt_path": f"prompts/{ph}.txt",
                "extraction_method": extraction_method,
                "compliance_score": comp,
                "parsed_ads": parsed_ads,
                "schema_violations": asdict(violations),
                "windows_stale": False,
                "error": error_payload,
            }
            try:
                append_jsonl(paths.calls_jsonl, record)
            except Exception as write_e:
                logger.exception("failed to append calls.jsonl record %s: %s", call_id, write_e)
                return

            if error_payload:
                stats.errored += 1
            else:
                stats.completed += 1

    await asyncio.gather(*(execute(u) for u in units), return_exceptions=False)

    derive_episode_results(cfg, episodes, paths=paths)
    return stats


def derive_episode_results(cfg: BenchmarkConfig, episodes: list[Episode], *, paths: RunPaths) -> None:
    """Recompute episode_results.jsonl from calls.jsonl. Idempotent."""
    if paths.episode_results_jsonl.exists():
        paths.episode_results_jsonl.unlink()

    by_trial: dict[tuple[str, str, int], list[dict]] = {}
    for rec in read_jsonl(paths.calls_jsonl):
        if rec.get("error"):
            continue
        key = (rec["model"], rec["episode_id"], rec["trial"])
        by_trial.setdefault(key, []).append(rec)

    episodes_by_id = {ep.ep_id: ep for ep in episodes}
    for (model, episode_id, trial), records in by_trial.items():
        episode = episodes_by_id.get(episode_id)
        if episode is None:
            continue
        if len(records) < len(episode.windows):
            continue
        records.sort(key=lambda r: r["window_index"])
        all_ads: list[dict] = []
        for r in records:
            for ad in r.get("parsed_ads") or []:
                all_ads.append(ad)
        deduped = parsing.deduplicate_window_ads(all_ads)
        append_jsonl(paths.episode_results_jsonl, {
            "schema_version": SCHEMA_VERSION,
            "model": model,
            "episode_id": episode_id,
            "trial": trial,
            "window_count": len(records),
            "merged_ads": deduped,
            "total_input_tokens": sum(r.get("input_tokens", 0) for r in records),
            "total_output_tokens": sum(r.get("output_tokens", 0) for r in records),
            "total_response_time_ms": sum(r.get("response_time_ms", 0) for r in records),
        })


def _parse_response(text: str) -> tuple[list[dict], str | None]:
    if not text:
        return [], None
    _, method = parsing.extract_json_ads_array(text)
    parsed = parsing.parse_ads_from_response(text) or []
    return list(parsed), method


def _call_id(unit: WorkUnit, prompt_hash: str) -> str:
    safe_model = unit.model_id.replace("/", "_").replace(":", "_")
    short_hash = prompt_hash.split(":", 1)[-1][:12]
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{safe_model}_{unit.episode_id}_t{unit.trial}_w{unit.window_index}_{short_hash}_{ts}"
