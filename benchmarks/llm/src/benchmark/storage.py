"""JSONL append/fsync storage for benchmark calls + episode results."""
from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path
from typing import Iterator


# v2: response bodies live in per-model JSONL shards (responses/<model>.jsonl)
# keyed by call_id; prompts are no longer stored (reconstructed from the corpus
# and verified against prompt_hash). v1 stored one .txt per call/prompt.
SCHEMA_VERSION = 2


class StorageError(RuntimeError):
    pass


def dump_line(record: dict) -> str:
    """The one place that owns the on-disk JSONL line format."""
    return json.dumps(record, separators=(",", ":")) + "\n"


def append_jsonl(path: Path, record: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = dump_line(record).encode()
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
    try:
        # os.write may write fewer bytes than asked (ENOSPC, signals); loop so
        # a short write cannot silently truncate a record.
        while data:
            data = data[os.write(fd, data):]
        os.fsync(fd)
    finally:
        os.close(fd)


def read_jsonl(path: Path) -> Iterator[dict]:
    if not path.is_file():
        return
    with path.open(encoding="utf-8") as f:
        for lineno, raw in enumerate(f, start=1):
            raw = raw.strip()
            if not raw:
                continue
            try:
                yield json.loads(raw)
            except json.JSONDecodeError as e:
                raise StorageError(f"{path}:{lineno}: invalid JSON: {e}") from e


def safe_model_id(model_id: str) -> str:
    return model_id.replace("/", "_").replace(":", "_")


def response_shard(responses_dir: Path, model_id: str) -> Path:
    return responses_dir / f"{safe_model_id(model_id)}.jsonl"


def append_response(responses_dir: Path, model_id: str, call_id: str, body: str) -> Path:
    path = response_shard(responses_dir, model_id)
    append_jsonl(path, {"call_id": call_id, "body": body})
    return path


def find_call(path: Path, call_id: str) -> dict | None:
    """Last matching record wins, matching the report's retry semantics.

    Substring pre-filter before json.loads: these files reach tens of MB and
    a single lookup should not pay a full-file JSON parse.
    """
    found: dict | None = None
    if not path.is_file():
        return None
    with path.open(encoding="utf-8") as f:
        for lineno, line in enumerate(f, start=1):
            if call_id not in line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError as e:
                raise StorageError(f"{path}:{lineno}: invalid JSON: {e}") from e
            if rec.get("call_id") == call_id:
                found = rec
    return found


def read_response(responses_dir: Path, model_id: str, call_id: str) -> str | None:
    rec = find_call(response_shard(responses_dir, model_id), call_id)
    return rec.get("body") if rec else None


def hash_prompt(*, system_prompt: str, user_prompt: str, model: str, temperature: float) -> str:
    h = hashlib.sha256()
    h.update(system_prompt.encode())
    h.update(b"\x00")
    h.update(user_prompt.encode())
    h.update(b"\x00")
    h.update(model.encode())
    h.update(b"\x00")
    h.update(f"{temperature}".encode())
    return "sha256:" + h.hexdigest()


CallKey = tuple[str, str, int, int, str]


def scan_calls(calls_path: Path) -> tuple[set[CallKey], set[CallKey]]:
    """Single-pass read of calls.jsonl. Returns (completed, errored).

    Errored is the subset of completed where ``error`` is populated, so callers
    that want to skip errored records use ``completed - errored`` and callers
    that want to retry only errored records use ``errored``.
    """
    completed: set[CallKey] = set()
    errored: set[CallKey] = set()
    for rec in read_jsonl(calls_path):
        key: CallKey = (
            rec["model"],
            rec["episode_id"],
            int(rec["trial"]),
            int(rec["window_index"]),
            rec["prompt_hash"],
        )
        completed.add(key)
        if rec.get("error"):
            errored.add(key)
    return completed, errored


def dedup_index(calls_path: Path) -> set[CallKey]:
    completed, _ = scan_calls(calls_path)
    return completed


def errored_keys(calls_path: Path) -> set[CallKey]:
    _, errored = scan_calls(calls_path)
    return errored


def sanitize_error(exc: BaseException) -> dict:
    """Strip secrets from exception data before persisting."""
    msg = str(exc)
    msg = re.sub(r"(Bearer|Token)\s+\S+", r"\1 <redacted>", msg, flags=re.IGNORECASE)
    msg = re.sub(
        r"(Authorization|api[_-]?key|api[_-]?secret|password)\s*[:=]\s*\S+",
        r"\1=<redacted>",
        msg,
        flags=re.IGNORECASE,
    )
    msg = re.sub(r"sk-[A-Za-z0-9]{8,}", "<redacted-key>", msg)
    return {"type": type(exc).__name__, "message": msg[:500]}
