import json
from pathlib import Path

import pytest

from benchmark.storage import (
    StorageError,
    append_jsonl,
    dedup_index,
    errored_keys,
    hash_prompt,
    read_jsonl,
    sanitize_error,
    write_prompt,
    write_response,
)


def test_append_and_read_round_trip(tmp_path):
    p = tmp_path / "calls.jsonl"
    append_jsonl(p, {"a": 1})
    append_jsonl(p, {"b": 2})
    rows = list(read_jsonl(p))
    assert rows == [{"a": 1}, {"b": 2}]


def test_append_creates_parent_dir(tmp_path):
    p = tmp_path / "deep" / "nest" / "calls.jsonl"
    append_jsonl(p, {"x": 1})
    assert p.is_file()


def test_read_jsonl_missing_file_yields_nothing(tmp_path):
    assert list(read_jsonl(tmp_path / "absent.jsonl")) == []


def test_read_jsonl_corrupt_line(tmp_path):
    p = tmp_path / "calls.jsonl"
    p.write_text('{"ok": true}\n{not json\n')
    rows: list[dict] = []
    with pytest.raises(StorageError, match="invalid JSON"):
        for r in read_jsonl(p):
            rows.append(r)


def test_hash_prompt_deterministic():
    h1 = hash_prompt(system_prompt="sys", user_prompt="user", model="m", temperature=0.0)
    h2 = hash_prompt(system_prompt="sys", user_prompt="user", model="m", temperature=0.0)
    assert h1 == h2
    assert h1.startswith("sha256:")


def test_hash_prompt_changes_with_each_field():
    base = dict(system_prompt="s", user_prompt="u", model="m", temperature=0.0)
    h_base = hash_prompt(**base)
    assert h_base != hash_prompt(**{**base, "system_prompt": "s2"})
    assert h_base != hash_prompt(**{**base, "user_prompt": "u2"})
    assert h_base != hash_prompt(**{**base, "model": "m2"})
    assert h_base != hash_prompt(**{**base, "temperature": 0.7})


def test_write_response(tmp_path):
    p = write_response(tmp_path, "call-001", "hello world")
    assert p.read_text() == "hello world"
    assert p.name == "call-001.txt"


def test_write_prompt_idempotent(tmp_path):
    p1 = write_prompt(tmp_path, "sha256:abc", "prompt body")
    p2 = write_prompt(tmp_path, "sha256:abc", "prompt body")
    assert p1 == p2
    assert p1.read_text() == "prompt body"


def test_dedup_index_built_from_calls(tmp_path):
    p = tmp_path / "calls.jsonl"
    for rec in [
        {"model": "m1", "episode_id": "e1", "trial": 0, "window_index": 0, "prompt_hash": "h1"},
        {"model": "m1", "episode_id": "e1", "trial": 0, "window_index": 1, "prompt_hash": "h2"},
        {"model": "m2", "episode_id": "e1", "trial": 0, "window_index": 0, "prompt_hash": "h3"},
    ]:
        append_jsonl(p, rec)
    idx = dedup_index(p)
    assert ("m1", "e1", 0, 0, "h1") in idx
    assert ("m1", "e1", 0, 1, "h2") in idx
    assert len(idx) == 3


def test_errored_keys_filters_only_errors(tmp_path):
    p = tmp_path / "calls.jsonl"
    append_jsonl(p, {"model": "m", "episode_id": "e", "trial": 0, "window_index": 0, "prompt_hash": "h", "error": None})
    append_jsonl(p, {"model": "m", "episode_id": "e", "trial": 0, "window_index": 1, "prompt_hash": "h2", "error": {"type": "X"}})
    err = errored_keys(p)
    assert err == {("m", "e", 0, 1, "h2")}


def test_sanitize_error_redacts_keys():
    class MyErr(RuntimeError):
        pass
    err = MyErr("Failed: Authorization=Bearer abc.def secret thing")
    out = sanitize_error(err)
    assert out["type"] == "MyErr"
    assert "abc.def" not in out["message"]
    assert "<redacted>" in out["message"]


def test_sanitize_error_redacts_openai_style_key():
    err = RuntimeError("got error with sk-1234567890abcdef in body")
    out = sanitize_error(err)
    assert "sk-1234567890abcdef" not in out["message"]
    assert "<redacted-key>" in out["message"]


def test_sanitize_error_truncates_long_messages():
    err = RuntimeError("x" * 2000)
    out = sanitize_error(err)
    assert len(out["message"]) <= 500
