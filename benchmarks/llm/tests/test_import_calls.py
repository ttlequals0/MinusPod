"""Tests for scripts/import_calls.py: idempotency and prompt_variant key placement."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

from benchmark.storage import StorageError, read_calls

SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "import_calls.py"
_spec = importlib.util.spec_from_file_location("import_calls", SCRIPT_PATH)
import_calls = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(import_calls)


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row) + "\n")


def _make_source(tmp_path: Path) -> Path:
    src = tmp_path / "src_raw"
    _write_jsonl(src / "calls.jsonl", [
        {
            "schema_version": 2, "call_id": "m1_ep1_t0_w0_abc", "model": "deepseek/deepseek-v4-flash",
            "addressing_mode": "segment_ids", "prompt_hash": "sha256:abc",
        },
        {
            "schema_version": 2, "call_id": "m2_ep1_t0_w0_def", "model": "gemma4:e4b",
            "addressing_mode": "segment_ids", "prompt_hash": "sha256:def",
        },
    ])
    _write_jsonl(src / "responses" / "deepseek_deepseek-v4-flash.jsonl", [
        {"call_id": "m1_ep1_t0_w0_abc", "body": "[]"},
    ])
    _write_jsonl(src / "responses" / "gemma4_e4b.jsonl", [
        {"call_id": "m2_ep1_t0_w0_def", "body": "[]"},
    ])
    return src


def test_inserts_prompt_variant_after_addressing_mode(tmp_path):
    src = _make_source(tmp_path)
    dest = tmp_path / "dest_raw"

    rows_added, shard_counts = import_calls.import_calls(src, "segmentation", dest_raw=dest)

    assert rows_added == 2
    assert shard_counts == {"deepseek_deepseek-v4-flash.jsonl": 1, "gemma4_e4b.jsonl": 1}

    rows = list(read_calls(dest))
    assert len(rows) == 2
    for row in rows:
        keys = list(row.keys())
        assert row["prompt_variant"] == "segmentation"
        assert keys.index("prompt_variant") == keys.index("addressing_mode") + 1


def test_existing_prompt_variant_is_overwritten_by_stamp(tmp_path):
    src = tmp_path / "src_raw"
    _write_jsonl(src / "calls.jsonl", [
        {
            "schema_version": 2, "call_id": "m1_ep1_t0_w0_abc", "model": "deepseek/deepseek-v4-flash",
            "addressing_mode": "segment_ids", "prompt_variant": "detection", "prompt_hash": "sha256:abc",
        },
    ])
    _write_jsonl(src / "responses" / "deepseek_deepseek-v4-flash.jsonl", [
        {"call_id": "m1_ep1_t0_w0_abc", "body": "[]"},
    ])
    dest = tmp_path / "dest_raw"

    rows_added, _ = import_calls.import_calls(src, "segmentation", dest_raw=dest)
    assert rows_added == 1

    row = list(read_calls(dest))[0]
    keys = list(row.keys())
    assert row["prompt_variant"] == "segmentation"
    assert keys.count("prompt_variant") == 1
    assert keys.index("prompt_variant") == keys.index("addressing_mode") + 1


def test_import_is_idempotent(tmp_path):
    src = _make_source(tmp_path)
    dest = tmp_path / "dest_raw"

    import_calls.import_calls(src, "segmentation", dest_raw=dest)
    rows_added_again, shard_counts_again = import_calls.import_calls(src, "segmentation", dest_raw=dest)

    assert rows_added_again == 0
    assert shard_counts_again == {}
    assert len(list(read_calls(dest))) == 2
    assert len((dest / "responses" / "deepseek_deepseek-v4-flash.jsonl").read_text().splitlines()) == 1
    assert len((dest / "responses" / "gemma4_e4b.jsonl").read_text().splitlines()) == 1


def test_missing_addressing_mode_raises(tmp_path):
    src = tmp_path / "src_raw"
    _write_jsonl(src / "calls.jsonl", [
        {"schema_version": 2, "call_id": "m1_ep1_t0_w0_abc", "model": "deepseek/deepseek-v4-flash"},
    ])
    dest = tmp_path / "dest_raw"

    with pytest.raises(ValueError, match="addressing_mode"):
        import_calls.import_calls(src, "segmentation", dest_raw=dest)


def test_malformed_json_line_names_path_and_lineno(tmp_path):
    src = tmp_path / "src_raw"
    calls_path = src / "calls.jsonl"
    calls_path.parent.mkdir(parents=True)
    calls_path.write_text('{"call_id": "ok", "model": "m1", "addressing_mode": "segment_ids"}\n{not json}\n')
    dest = tmp_path / "dest_raw"

    with pytest.raises(StorageError, match=rf"{calls_path}:2: invalid JSON"):
        import_calls.import_calls(src, "segmentation", dest_raw=dest)
