"""Tests for scripts/import_calls.py: idempotency, variant stamping, and all-or-nothing writes."""
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


def test_stamps_prompt_variant_on_imported_rows(tmp_path):
    src = _make_source(tmp_path)
    dest = tmp_path / "dest_raw"

    rows_added, shard_counts = import_calls.import_calls(src, "segmentation", dest_raw=dest)

    assert rows_added == 2
    assert shard_counts == {"deepseek_deepseek-v4-flash.jsonl": 1, "gemma4_e4b.jsonl": 1}

    rows = list(read_calls(dest))
    assert len(rows) == 2
    assert all(row["prompt_variant"] == "segmentation" for row in rows)


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

    assert list(read_calls(dest))[0]["prompt_variant"] == "segmentation"


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


def test_unknown_variant_is_rejected_before_writing(tmp_path):
    src = _make_source(tmp_path)
    dest = tmp_path / "dest_raw"
    with pytest.raises(ValueError, match="unknown prompt variant"):
        import_calls.import_calls(src, "segmentaton", dest_raw=dest)
    assert list(read_calls(dest)) == []


def test_missing_source_shard_writes_no_call_rows(tmp_path):
    src = _make_source(tmp_path)
    (src / "responses" / "gemma4_e4b.jsonl").unlink()
    dest = tmp_path / "dest_raw"
    with pytest.raises(FileNotFoundError):
        import_calls.import_calls(src, "segmentation", dest_raw=dest)
    assert list(read_calls(dest)) == []
    # Nothing was committed, so the rerun after restoring the shard imports everything.
    _write_jsonl(src / "responses" / "gemma4_e4b.jsonl", [{"call_id": "m2_ep1_t0_w0_def", "body": "[]"}])
    rows_added, _ = import_calls.import_calls(src, "segmentation", dest_raw=dest)
    assert rows_added == 2


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
