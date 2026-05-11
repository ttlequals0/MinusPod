"""JSONL append/fsync storage for benchmark calls + episode results."""
from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path
from typing import Iterator


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
    tmp = path.with_suffix(".tmp")
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644)
    try:
        os.write(fd, body.encode())
        os.fsync(fd)
    finally:
        os.close(fd)
    os.replace(tmp, path)
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
