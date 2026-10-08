"""Import call records + response-shard lines from another run's raw export."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT / "src"))
sys.path.insert(0, str(REPO_ROOT / "benchmarks" / "llm" / "src"))

from benchmark.storage import append_call, dump_line, read_calls, read_jsonl, safe_model_id  # noqa: E402

DEST_RAW = Path(__file__).resolve().parents[1] / "results" / "raw"


def _load_call_ids(path: Path) -> set[str]:
    return {rec["call_id"] for rec in read_jsonl(path)}


def _insert_prompt_variant(row: dict, variant: str) -> dict:
    out = {}
    for key, value in row.items():
        if key == "prompt_variant":
            continue  # the stamped value below always wins over a source row's own
        out[key] = value
        if key == "addressing_mode":
            out["prompt_variant"] = variant
    return out


def import_calls(from_raw: Path, variant: str, dest_raw: Path = DEST_RAW) -> tuple[int, dict[str, int]]:
    """Append new rows/shard lines from from_raw into dest_raw; returns (rows appended, {shard_filename: lines appended}).

    Reads from_raw via read_calls, which handles both a legacy flat
    calls.jsonl (the #801-style checkout) and calls/ shards transparently.
    """
    existing_ids = {rec["call_id"] for rec in read_calls(dest_raw)}

    new_rows: list[dict] = []
    new_ids_by_model: dict[str, set[str]] = {}
    for row in read_calls(from_raw):
        call_id = row["call_id"]
        if call_id in existing_ids:
            continue
        if "addressing_mode" not in row:
            raise ValueError(f"{call_id}: row has no addressing_mode field")
        new_rows.append(_insert_prompt_variant(row, variant))
        new_ids_by_model.setdefault(row["model"], set()).add(call_id)

    for row in new_rows:
        append_call(dest_raw, row)

    shard_counts = _import_shards(from_raw, dest_raw, new_ids_by_model)
    return len(new_rows), shard_counts


def _import_shards(from_raw: Path, dest_raw: Path, new_ids_by_model: dict[str, set[str]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for model, call_ids in new_ids_by_model.items():
        shard_name = f"{safe_model_id(model)}.jsonl"
        src_shard = from_raw / "responses" / shard_name
        if not src_shard.is_file():
            raise FileNotFoundError(f"missing source shard for model {model!r}: {src_shard}")
        dest_shard = dest_raw / "responses" / shard_name
        existing_shard_ids = _load_call_ids(dest_shard)

        appended_rows: list[dict] = []
        for rec in read_jsonl(src_shard):
            cid = rec["call_id"]
            if cid in call_ids and cid not in existing_shard_ids:
                appended_rows.append(rec)

        if appended_rows:
            dest_shard.parent.mkdir(parents=True, exist_ok=True)
            with dest_shard.open("a", encoding="utf-8") as f:
                for rec in appended_rows:
                    f.write(dump_line(rec))
        counts[shard_name] = len(appended_rows)
    return counts


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--from-raw", required=True, type=Path, help="source results/raw dir to import from")
    parser.add_argument("--variant", required=True, help="prompt_variant value to stamp on imported rows")
    args = parser.parse_args()

    rows_added, shard_counts = import_calls(args.from_raw, args.variant)
    print(f"calls/: {rows_added} row(s) appended")
    for shard, count in sorted(shard_counts.items()):
        print(f"responses/{shard}: {count} line(s) appended")


if __name__ == "__main__":
    main()
