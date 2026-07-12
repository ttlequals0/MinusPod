"""migrate-raw: v1 per-call .txt layout -> v2 per-model JSONL shards."""
from __future__ import annotations

from pathlib import Path

from benchmark import corpus, runner
from benchmark.migrate import migrate
from benchmark.runner import RunPaths
from benchmark.storage import append_jsonl, hash_prompt, read_jsonl, read_response


def _v1_record(**overrides) -> dict:
    rec = {
        "schema_version": 1,
        "call_id": "m1_ep-t-e_t0_w0_abcdef012345_20260101T000000Z",
        "model": "m1",
        "episode_id": "ep-t-e",
        "trial": 0,
        "window_index": 0,
        "temperature": 0.0,
        "prompt_hash": "sha256:abc",
        "response_path": "responses/m1_ep-t-e_t0_w0_abcdef012345_20260101T000000Z.txt",
        "prompt_path": "prompts/sha256:abc.txt",
        "error": None,
    }
    rec.update(overrides)
    return rec


def _v1_layout(tmp_path: Path, records: list[dict], responses: dict[str, str], prompts: dict[str, str]) -> RunPaths:
    paths = RunPaths.for_root(tmp_path / "results")
    for rec in records:
        append_jsonl(paths.calls_jsonl, rec)
    paths.responses_dir.mkdir(parents=True, exist_ok=True)
    for call_id, body in responses.items():
        (paths.responses_dir / f"{call_id}.txt").write_text(body)
    paths.prompts_dir.mkdir(parents=True, exist_ok=True)
    for prompt_hash, body in prompts.items():
        (paths.prompts_dir / f"{prompt_hash}.txt").write_text(body)
    return paths


def test_migrate_happy_path(tmp_path, write_corpus_episode):
    corpus_dir = tmp_path / "corpus"
    ep_id = write_corpus_episode(corpus_dir, ep_id="ep-t-e").name
    ep = corpus.load_episode(corpus_dir / ep_id)
    user_prompt = runner._build_user_prompt(ep, ep.windows[0], total_windows=1)
    ph = hash_prompt(system_prompt="S", user_prompt=user_prompt, model="m1", temperature=0.0)

    c1 = "m1_ep-t-e_t0_w0_abcdef012345_20260101T000000Z"
    c2 = "openai_gpt-4_ep-t-e_t0_w0_abcdef012345_20260101T000001Z"
    paths = _v1_layout(
        tmp_path,
        records=[
            _v1_record(call_id=c1, prompt_hash=ph, prompt_path=f"prompts/{ph}.txt",
                       response_path=f"responses/{c1}.txt"),
            _v1_record(call_id=c2, model="openai/gpt-4", prompt_hash=ph,
                       prompt_path=f"prompts/{ph}.txt", response_path=f"responses/{c2}.txt"),
        ],
        responses={c1: "body one", c2: "body two"},
        prompts={ph: user_prompt},
    )

    result = migrate(paths, corpus_dir=corpus_dir)

    assert result.responses_migrated == 2
    assert result.responses_kept == 0
    assert result.prompts_deleted == 1
    assert result.prompts_kept == []
    assert result.records_rewritten == 2
    assert result.backup_path is not None and result.backup_path.is_file()

    assert read_response(paths.responses_dir, "m1", c1) == "body one"
    assert read_response(paths.responses_dir, "openai/gpt-4", c2) == "body two"
    assert not list(paths.responses_dir.glob("*.txt"))
    assert not paths.prompts_dir.exists()

    records = list(read_jsonl(paths.calls_jsonl))
    assert [r["schema_version"] for r in records] == [2, 2]
    assert records[0]["response_path"] == "responses/m1.jsonl"
    assert records[1]["response_path"] == "responses/openai_gpt-4.jsonl"
    assert all("prompt_path" not in r for r in records)
    # Untouched fields survive the rewrite.
    assert records[0]["prompt_hash"] == ph


def test_migrate_keeps_unverifiable_prompt_files(tmp_path, write_corpus_episode):
    corpus_dir = tmp_path / "corpus"
    ep_id = write_corpus_episode(corpus_dir, ep_id="ep-t-e").name
    c1 = "m1_ep-t-e_t0_w0_abcdef012345_20260101T000000Z"
    paths = _v1_layout(
        tmp_path,
        records=[_v1_record(call_id=c1, prompt_hash="sha256:stale")],
        responses={c1: "body"},
        prompts={
            "sha256:stale": "text that does not reconstruct",
            "sha256:orphan": "hash referenced by no record",
        },
    )

    result = migrate(paths, corpus_dir=corpus_dir)

    assert result.prompts_deleted == 0
    assert sorted(result.prompts_kept) == ["sha256:orphan", "sha256:stale"]
    assert (paths.prompts_dir / "sha256:stale.txt").read_text() == "text that does not reconstruct"
    assert (paths.prompts_dir / "sha256:orphan.txt").is_file()


def test_migrate_orphan_response_uses_filename_model(tmp_path, write_corpus_episode):
    corpus_dir = tmp_path / "corpus"
    write_corpus_episode(corpus_dir, ep_id="ep-t-e")
    orphan = "anthropic_claude-x_ep-gone-123_t0_w4_deadbeef0123_20260101T000000Z"
    paths = _v1_layout(tmp_path, records=[], responses={orphan: "orphan body"}, prompts={})

    result = migrate(paths, corpus_dir=corpus_dir)

    assert result.responses_migrated == 1
    assert result.responses_orphaned == 1
    rows = list(read_jsonl(paths.responses_dir / "anthropic_claude-x.jsonl"))
    assert rows == [{"call_id": orphan, "body": "orphan body"}]
    assert not list(paths.responses_dir.glob("*.txt"))


def test_migrate_is_idempotent(tmp_path, write_corpus_episode):
    corpus_dir = tmp_path / "corpus"
    ep_id = write_corpus_episode(corpus_dir, ep_id="ep-t-e").name
    ep = corpus.load_episode(corpus_dir / ep_id)
    user_prompt = runner._build_user_prompt(ep, ep.windows[0], total_windows=1)
    ph = hash_prompt(system_prompt="S", user_prompt=user_prompt, model="m1", temperature=0.0)
    c1 = "m1_ep-t-e_t0_w0_abcdef012345_20260101T000000Z"
    paths = _v1_layout(
        tmp_path,
        records=[_v1_record(call_id=c1, prompt_hash=ph, prompt_path=f"prompts/{ph}.txt")],
        responses={c1: "body one"},
        prompts={ph: user_prompt},
    )

    first = migrate(paths, corpus_dir=corpus_dir)
    second = migrate(paths, corpus_dir=corpus_dir)

    assert first.responses_migrated == 1
    assert second.responses_migrated == 0
    assert second.records_rewritten == 0
    assert second.backup_path is None
    rows = list(read_jsonl(paths.responses_dir / "m1.jsonl"))
    assert rows == [{"call_id": c1, "body": "body one"}]


def test_migrate_partial_rerun_skips_already_sharded_call(tmp_path, write_corpus_episode):
    """A crash between shard append and .txt delete must not duplicate lines."""
    corpus_dir = tmp_path / "corpus"
    write_corpus_episode(corpus_dir, ep_id="ep-t-e")
    c1 = "m1_ep-t-e_t0_w0_abcdef012345_20260101T000000Z"
    paths = _v1_layout(tmp_path, records=[_v1_record(call_id=c1)], responses={c1: "body one"}, prompts={})
    # Simulate a prior interrupted run that already sharded this call.
    append_jsonl(paths.responses_dir / "m1.jsonl", {"call_id": c1, "body": "body one"})

    result = migrate(paths, corpus_dir=corpus_dir)

    assert result.responses_migrated == 0
    rows = list(read_jsonl(paths.responses_dir / "m1.jsonl"))
    assert rows == [{"call_id": c1, "body": "body one"}]
    assert not list(paths.responses_dir.glob("*.txt"))


def test_migrate_preserves_null_response_path_on_errored_records(tmp_path, write_corpus_episode):
    corpus_dir = tmp_path / "corpus"
    write_corpus_episode(corpus_dir, ep_id="ep-t-e")
    paths = _v1_layout(
        tmp_path,
        records=[_v1_record(response_path=None, error={"type": "Boom", "message": "x"})],
        responses={},
        prompts={},
    )

    migrate(paths, corpus_dir=corpus_dir)

    records = list(read_jsonl(paths.calls_jsonl))
    assert records[0]["response_path"] is None
    assert records[0]["schema_version"] == 2


def test_migrate_keeps_txt_when_shard_body_differs(tmp_path, write_corpus_episode):
    """If the shard already holds a different body for this call_id, the .txt
    is the on-disk truth we must not destroy, and the record must keep
    pointing at it rather than at the mismatching shard."""
    corpus_dir = tmp_path / "corpus"
    write_corpus_episode(corpus_dir, ep_id="ep-t-e")
    c1 = "m1_ep-t-e_t0_w0_abcdef012345_20260101T000000Z"
    paths = _v1_layout(tmp_path, records=[_v1_record(call_id=c1)], responses={c1: "real body"}, prompts={})
    append_jsonl(paths.responses_dir / "m1.jsonl", {"call_id": c1, "body": "corrupt body"})

    result = migrate(paths, corpus_dir=corpus_dir)

    assert result.responses_kept == 1
    assert (paths.responses_dir / f"{c1}.txt").read_text() == "real body"
    records = list(read_jsonl(paths.calls_jsonl))
    assert records[0]["response_path"] == f"responses/{c1}.txt"


def test_migrate_never_downgrades_newer_schema_records(tmp_path, write_corpus_episode):
    """Re-running migrate-raw after a future schema bump must not stamp
    newer records back down to v2."""
    corpus_dir = tmp_path / "corpus"
    write_corpus_episode(corpus_dir, ep_id="ep-t-e")
    v3 = {
        "schema_version": 3,
        "call_id": "m1_ep-t-e_t0_w0_abcdef012345_20260101T000000Z",
        "model": "m1",
        "episode_id": "ep-t-e",
        "trial": 0,
        "window_index": 0,
        "temperature": 0.0,
        "prompt_hash": "sha256:abc",
        "response_path": "responses/m1.jsonl",
        "error": None,
    }
    paths = _v1_layout(tmp_path, records=[v3], responses={}, prompts={})

    result = migrate(paths, corpus_dir=corpus_dir)

    assert result.records_rewritten == 0
    records = list(read_jsonl(paths.calls_jsonl))
    assert records[0]["schema_version"] == 3


def test_migrate_tolerates_record_missing_window_index(tmp_path, write_corpus_episode):
    """A malformed record must not abort the whole migration; the prompt it
    references is kept instead."""
    corpus_dir = tmp_path / "corpus"
    write_corpus_episode(corpus_dir, ep_id="ep-t-e")
    rec = _v1_record(prompt_hash="sha256:noidx")
    del rec["window_index"]
    c1 = rec["call_id"]
    paths = _v1_layout(
        tmp_path,
        records=[rec],
        responses={c1: "body"},
        prompts={"sha256:noidx": "some prompt"},
    )

    result = migrate(paths, corpus_dir=corpus_dir)

    assert result.responses_migrated == 1
    assert result.prompts_kept == ["sha256:noidx"]


def test_migrate_orphan_prefers_known_model_prefix(tmp_path, write_corpus_episode):
    """An orphaned .txt whose sanitized model contains '_ep-' must land in the
    shard of the known model with the longest matching prefix, not be split at
    the first '_ep-'."""
    corpus_dir = tmp_path / "corpus"
    write_corpus_episode(corpus_dir, ep_id="ep-t-e")
    known = "m1_ep-t-e_t0_w0_abcdef012345_20260101T000000Z"
    orphan = "weird_m_ep-x_ep-t-e_t0_w9_deadbeef0123_20260101T000000Z"
    paths = _v1_layout(
        tmp_path,
        records=[
            _v1_record(call_id=known),
            _v1_record(call_id="other", model="weird/m_ep-x"),
        ],
        responses={orphan: "orphan body"},
        prompts={},
    )

    result = migrate(paths, corpus_dir=corpus_dir)

    assert result.responses_orphaned == 1
    rows = list(read_jsonl(paths.responses_dir / "weird_m_ep-x.jsonl"))
    assert rows == [{"call_id": orphan, "body": "orphan body"}]
