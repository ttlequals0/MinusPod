"""JSONL append/fsync storage for benchmark calls + episode results."""
from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Iterator


SCHEMA_VERSION = 1


class StorageError(RuntimeError):
    pass


def append_jsonl(path: Path, record: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(record, separators=(",", ":")) + "\n"
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
    try:
        os.write(fd, line.encode())
        os.fsync(fd)
    finally:
        os.close(fd)


def read_jsonl(path: Path) -> Iterator[dict]:
    if not path.is_file():
        return
    with path.open() as f:
        for lineno, raw in enumerate(f, start=1):
            raw = raw.strip()
            if not raw:
                continue
            try:
                yield json.loads(raw)
            except json.JSONDecodeError as e:
                raise StorageError(f"{path}:{lineno}: invalid JSON: {e}") from e


def write_response(responses_dir: Path, call_id: str, body: str) -> Path:
    responses_dir.mkdir(parents=True, exist_ok=True)
    path = responses_dir / f"{call_id}.txt"
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644)
    try:
        os.write(fd, body.encode())
        os.fsync(fd)
    finally:
        os.close(fd)
    return path


def write_prompt(prompts_dir: Path, prompt_hash: str, body: str) -> Path:
    prompts_dir.mkdir(parents=True, exist_ok=True)
    path = prompts_dir / f"{prompt_hash}.txt"
    if path.exists():
        return path
    tmp = path.with_suffix(".tmp")
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644)
    try:
        os.write(fd, body.encode())
        os.fsync(fd)
    finally:
        os.close(fd)
    os.replace(tmp, path)
    return path


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


def dedup_index(calls_path: Path) -> set[tuple[str, str, int, int, str]]:
    """Return set of completed (model, episode, trial, window_index, prompt_hash) tuples.

    Used to skip already-completed work on resume. Records with non-null
    ``error`` count as 'attempted' and are skipped by default; callers wanting
    to re-run errored records should filter accordingly.
    """
    seen: set[tuple[str, str, int, int, str]] = set()
    for rec in read_jsonl(calls_path):
        seen.add((
            rec["model"],
            rec["episode_id"],
            int(rec["trial"]),
            int(rec["window_index"]),
            rec["prompt_hash"],
        ))
    return seen


def errored_keys(calls_path: Path) -> set[tuple[str, str, int, int, str]]:
    out: set[tuple[str, str, int, int, str]] = set()
    for rec in read_jsonl(calls_path):
        if rec.get("error"):
            out.add((rec["model"], rec["episode_id"], int(rec["trial"]), int(rec["window_index"]), rec["prompt_hash"]))
    return out


def sanitize_error(exc: BaseException) -> dict:
    """Strip secrets from exception data before persisting."""
    import re

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
