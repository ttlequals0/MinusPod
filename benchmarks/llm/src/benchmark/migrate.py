"""One-time migration of results/raw from schema v1 to v2.

v1: one .txt per call under responses/, one .txt per prompt_hash under prompts/.
v2: per-model JSONL shards under responses/; prompts reconstructed on demand.

Every destructive step verifies first: a response .txt is deleted only after
its body reads back byte-exact from the shard, a prompt .txt only after it
reconstructs byte-exact from the corpus, and calls.jsonl is rewritten via
tmp+rename with a timestamped backup left behind. Safe to re-run after an
interruption.
"""
from __future__ import annotations

import os
import shutil
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from .corpus import CorpusError
from .runner import RunPaths, reconstruct_user_prompt
from .storage import append_response, dump_line, read_jsonl, response_shard, safe_model_id

TARGET_SCHEMA_VERSION = 2


@dataclass
class MigrationReport:
    responses_migrated: int = 0
    responses_orphaned: int = 0
    responses_kept: int = 0
    prompts_deleted: int = 0
    prompts_kept: list[str] = field(default_factory=list)
    records_rewritten: int = 0
    backup_path: Path | None = None


def migrate(paths: RunPaths, *, corpus_dir: Path) -> MigrationReport:
    report = MigrationReport()
    records = list(read_jsonl(paths.calls_jsonl))
    model_by_call = {
        rec["call_id"]: rec["model"]
        for rec in records
        if rec.get("call_id") and rec.get("model")
    }

    kept_call_ids = _migrate_responses(paths, model_by_call, report)
    _migrate_prompts(paths, records, corpus_dir, report)
    _rewrite_calls(paths, records, report, kept_call_ids)
    return report


def _orphan_model(stem: str, known_models: list[str]) -> str:
    """Recover the model for a v1 .txt with no calls.jsonl record.

    v1 call_id filename format, frozen: <safe_model>_<ep_id>_..., and ep_id
    always starts with "ep-". A sanitized model id may itself contain "_ep-",
    so prefer the longest known-model prefix before falling back to the split.
    """
    for model in sorted(known_models, key=len, reverse=True):
        if stem.startswith(safe_model_id(model) + "_ep-"):
            return model
    return stem.split("_ep-", 1)[0]


def _migrate_responses(paths: RunPaths, model_by_call: dict[str, str], report: MigrationReport) -> set[str]:
    """Fold v1 .txt bodies into per-model shards. Returns the call_ids whose
    .txt was kept because the shard held a different body for it."""
    if not paths.responses_dir.is_dir():
        return set()
    txt_files = sorted(paths.responses_dir.glob("*.txt"))
    if not txt_files:
        return set()

    known_models = list(set(model_by_call.values()))
    model_by_txt: dict[Path, str] = {}
    for txt in txt_files:
        model = model_by_call.get(txt.stem)
        if model is None:
            report.responses_orphaned += 1
            model = _orphan_model(txt.stem, known_models)
        model_by_txt[txt] = model

    shard_index: dict[Path, set[str]] = {
        shard: {r.get("call_id") for r in read_jsonl(shard)}
        for shard in paths.responses_dir.glob("*.jsonl")
    }
    for txt, model in model_by_txt.items():
        call_id = txt.stem
        shard = response_shard(paths.responses_dir, model)
        if call_id in shard_index.get(shard, set()):
            continue
        append_response(paths.responses_dir, model, call_id, txt.read_text(encoding="utf-8"))
        shard_index.setdefault(shard, set()).add(call_id)
        report.responses_migrated += 1

    # Delete a .txt only after its body reads back byte-exact from its shard.
    # Verify shard by shard so memory stays bounded by one shard, not the
    # whole response set.
    kept_call_ids: set[str] = set()
    txts_by_shard: dict[Path, list[Path]] = {}
    for txt, model in model_by_txt.items():
        txts_by_shard.setdefault(response_shard(paths.responses_dir, model), []).append(txt)
    for shard, txts in txts_by_shard.items():
        bodies = {r.get("call_id"): r.get("body") for r in read_jsonl(shard)}
        for txt in txts:
            if bodies.get(txt.stem) == txt.read_text(encoding="utf-8"):
                txt.unlink()
            else:
                report.responses_kept += 1
                kept_call_ids.add(txt.stem)
    return kept_call_ids


def _migrate_prompts(paths: RunPaths, records: list[dict], corpus_dir: Path, report: MigrationReport) -> None:
    if not paths.prompts_dir.is_dir():
        return

    window_by_hash: dict[str, tuple[str, int]] = {}
    for rec in records:
        ph = rec.get("prompt_hash")
        if (
            ph
            and ph not in window_by_hash
            and rec.get("episode_id")
            and rec.get("window_index") is not None
        ):
            window_by_hash[ph] = (rec["episode_id"], int(rec["window_index"]))

    rebuilt_cache: dict[tuple[str, int], str | None] = {}
    for pf in sorted(paths.prompts_dir.glob("*.txt")):
        target = window_by_hash.get(pf.stem)
        rebuilt = _reconstruct(target, corpus_dir, rebuilt_cache) if target else None
        if rebuilt is not None and rebuilt == pf.read_text(encoding="utf-8"):
            pf.unlink()
            report.prompts_deleted += 1
        else:
            report.prompts_kept.append(pf.stem)

    if all(p.name == ".gitkeep" for p in paths.prompts_dir.iterdir()):
        for p in paths.prompts_dir.iterdir():
            p.unlink()
        paths.prompts_dir.rmdir()


def _reconstruct(
    target: tuple[str, int],
    corpus_dir: Path,
    cache: dict[tuple[str, int], str | None],
) -> str | None:
    """Cached per (episode, window): many prompt hashes (one per model) map to
    the same user prompt, and reconstruction loads the episode from disk."""
    if target not in cache:
        ep_id, window_index = target
        try:
            cache[target] = reconstruct_user_prompt(
                {"episode_id": ep_id, "window_index": window_index}, corpus_dir=corpus_dir,
            )
        except (CorpusError, ValueError):
            # Episode gone from the corpus or windows regenerated since the
            # run; the stored prompt file is kept.
            cache[target] = None
    return cache[target]


def _rewrite_calls(paths: RunPaths, records: list[dict], report: MigrationReport, kept_call_ids: set[str]) -> None:
    rewritten: list[dict] = []
    changed = 0
    for rec in records:
        # Never touch records already at (or past) the target: a re-run after
        # a future schema bump must not downgrade them.
        if int(rec.get("schema_version", 1)) >= TARGET_SCHEMA_VERSION:
            rewritten.append(rec)
            continue
        new = dict(rec)
        new["schema_version"] = TARGET_SCHEMA_VERSION
        new.pop("prompt_path", None)
        # A kept .txt (shard body mismatch) stays the record's authoritative
        # body; only verified-in-shard records point at the shard.
        if new.get("response_path") and new.get("call_id") not in kept_call_ids:
            shard = response_shard(paths.responses_dir, new["model"])
            new["response_path"] = str(shard.relative_to(paths.calls_jsonl.parent))
        if new != rec:
            changed += 1
        rewritten.append(new)
    if not changed:
        return

    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup_path = paths.calls_jsonl.with_name(f"calls.jsonl.bak-{ts}")
    shutil.copy2(paths.calls_jsonl, backup_path)

    tmp = paths.calls_jsonl.with_suffix(".jsonl.tmp")
    tmp.write_text("".join(dump_line(rec) for rec in rewritten), encoding="utf-8")
    os.replace(tmp, paths.calls_jsonl)

    report.records_rewritten = changed
    report.backup_path = backup_path
