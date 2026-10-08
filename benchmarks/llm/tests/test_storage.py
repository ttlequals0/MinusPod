
import pytest

from benchmark.storage import (
    StorageError,
    append_call,
    append_jsonl,
    append_response,
    call_shard,
    dedup_index,
    errored_keys,
    find_call,
    hash_prompt,
    read_calls,
    read_jsonl,
    read_response,
    safe_model_id,
    sanitize_error,
)


def test_find_call_returns_last_match(tmp_path):
    p = tmp_path / "calls.jsonl"
    append_jsonl(p, {"call_id": "c1", "trial": 0})
    append_jsonl(p, {"call_id": "c2", "trial": 0})
    append_jsonl(p, {"call_id": "c1", "trial": 1})
    assert find_call(p, "c1") == {"call_id": "c1", "trial": 1}
    assert find_call(p, "absent") is None


def test_find_call_raises_storage_error_on_corrupt_matching_line(tmp_path):
    """A torn line (crash mid-append) must surface as a StorageError with the
    file path, matching read_jsonl, not a raw JSONDecodeError traceback."""
    p = tmp_path / "calls.jsonl"
    p.write_text('{"call_id": "c1", "trial": 0}\n{"call_id": "c2", "tr\n')
    with pytest.raises(StorageError, match="invalid JSON"):
        find_call(p, "c2")


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


def test_safe_model_id_sanitizes_separators():
    assert safe_model_id("openai/gpt-4:mini") == "openai_gpt-4_mini"
    assert safe_model_id("m1") == "m1"


def test_append_response_shards_by_model(tmp_path):
    p1 = append_response(tmp_path, "openai/gpt-4", "call-1", "body one")
    p2 = append_response(tmp_path, "openai/gpt-4", "call-2", "body two")
    p3 = append_response(tmp_path, "anthropic:claude", "call-3", "body three")
    assert p1 == p2 == tmp_path / "openai_gpt-4.jsonl"
    assert p3 == tmp_path / "anthropic_claude.jsonl"
    assert list(read_jsonl(p1)) == [
        {"call_id": "call-1", "body": "body one"},
        {"call_id": "call-2", "body": "body two"},
    ]


def test_read_response_returns_last_write(tmp_path):
    append_response(tmp_path, "m", "c1", "first")
    append_response(tmp_path, "m", "c2", "other")
    append_response(tmp_path, "m", "c1", "second")
    assert read_response(tmp_path, "m", "c1") == "second"
    assert read_response(tmp_path, "m", "missing") is None
    assert read_response(tmp_path, "absent-model", "c1") is None


def test_append_call_shards_by_model(tmp_path):
    p1 = append_call(tmp_path, {"call_id": "c1", "model": "openai/gpt-4"})
    p2 = append_call(tmp_path, {"call_id": "c2", "model": "openai/gpt-4"})
    p3 = append_call(tmp_path, {"call_id": "c3", "model": "anthropic:claude"})
    assert p1 == p2 == call_shard(tmp_path, "openai/gpt-4") == tmp_path / "calls" / "openai_gpt-4.jsonl"
    assert p3 == tmp_path / "calls" / "anthropic_claude.jsonl"
    assert [r["call_id"] for r in read_jsonl(p1)] == ["c1", "c2"]


def test_read_calls_orders_by_shard_name_then_within_file(tmp_path):
    append_call(tmp_path, {"call_id": "b1", "model": "model-b"})
    append_call(tmp_path, {"call_id": "a1", "model": "model-a"})
    append_call(tmp_path, {"call_id": "a2", "model": "model-a"})
    assert [r["call_id"] for r in read_calls(tmp_path)] == ["a1", "a2", "b1"]


def test_read_calls_yields_legacy_file_rows_first_and_warns(tmp_path, caplog):
    append_jsonl(tmp_path / "calls.jsonl", {"call_id": "legacy1", "model": "m"})
    append_call(tmp_path, {"call_id": "shard1", "model": "m"})
    with caplog.at_level("WARNING"):
        rows = list(read_calls(tmp_path))
    assert [r["call_id"] for r in rows] == ["legacy1", "shard1"]
    assert sum("migrate-calls" in m for m in caplog.messages) == 1


def test_read_calls_missing_raw_dir_yields_nothing(tmp_path):
    assert list(read_calls(tmp_path / "absent")) == []


def test_find_call_scans_raw_dir_across_shards(tmp_path):
    append_call(tmp_path, {"call_id": "c1", "model": "m1"})
    append_call(tmp_path, {"call_id": "c2", "model": "m2"})
    assert find_call(tmp_path, "c2") == {"call_id": "c2", "model": "m2"}
    assert find_call(tmp_path, "absent") is None


def test_find_call_accepts_records_iterable():
    records = [{"call_id": "c1", "trial": 0}, {"call_id": "c1", "trial": 1}]
    assert find_call(records, "c1") == {"call_id": "c1", "trial": 1}


def test_dedup_index_built_from_calls():
    records = [
        {"model": "m1", "episode_id": "e1", "trial": 0, "window_index": 0, "prompt_hash": "h1"},
        {"model": "m1", "episode_id": "e1", "trial": 0, "window_index": 1, "prompt_hash": "h2"},
        {"model": "m2", "episode_id": "e1", "trial": 0, "window_index": 0, "prompt_hash": "h3"},
    ]
    idx = dedup_index(records)
    assert ("m1", "e1", 0, 0, "h1") in idx
    assert ("m1", "e1", 0, 1, "h2") in idx
    assert len(idx) == 3


def test_dedup_index_accepts_raw_dir(tmp_path):
    append_call(tmp_path, {"model": "m1", "episode_id": "e1", "trial": 0, "window_index": 0, "prompt_hash": "h1"})
    idx = dedup_index(tmp_path)
    assert idx == {("m1", "e1", 0, 0, "h1")}


def test_errored_keys_filters_only_errors():
    records = [
        {"model": "m", "episode_id": "e", "trial": 0, "window_index": 0, "prompt_hash": "h", "error": None},
        {"model": "m", "episode_id": "e", "trial": 0, "window_index": 1, "prompt_hash": "h2", "error": {"type": "X"}},
    ]
    err = errored_keys(records)
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


def _row(**kw):
    base = {"model": "m", "episode_id": "ep", "trial": 0,
            "window_index": 0, "prompt_hash": "h", "error": None}
    return {**base, **kw}


def test_errored_keys_discharged_by_a_later_success():
    """Call shards are append-only, so a successful retry must clear the
    earlier failure. Otherwise every --retry-errors pass redoes work already
    recovered."""
    records = [_row(error={"message": "boom"})]
    assert errored_keys(records) == {("m", "ep", 0, 0, "h")}

    records.append(_row())
    assert errored_keys(records) == set()


def test_errored_keys_reinstated_when_the_retry_also_fails():
    records = [_row(error={"message": "boom"}), _row(), _row(error={"message": "boom again"})]
    assert errored_keys(records) == {("m", "ep", 0, 0, "h")}


def test_errored_keys_are_per_key():
    """A success on one window must not discharge a different window's failure."""
    records = [_row(window_index=0, error={"message": "boom"}), _row(window_index=1)]
    assert errored_keys(records) == {("m", "ep", 0, 0, "h")}
