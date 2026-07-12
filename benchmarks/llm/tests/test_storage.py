
import pytest

from benchmark.storage import (
    StorageError,
    append_jsonl,
    append_response,
    dedup_index,
    errored_keys,
    find_call,
    hash_prompt,
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
