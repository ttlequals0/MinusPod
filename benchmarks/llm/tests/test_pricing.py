import json
from pathlib import Path

from benchmark.pricing import (
    ModelPrice,
    PricingSnapshot,
    cost_usd,
    latest_snapshot,
    load_snapshot,
    write_snapshot,
)


def make_snapshot():
    return PricingSnapshot(
        captured_at="2026-05-07T05:00:00Z",
        entries=[
            ModelPrice(
                match_key="anthropic/claude-sonnet-4.6",
                raw_model_id="anthropic/claude-sonnet-4.6",
                input_cost_per_mtok=3.0,
                output_cost_per_mtok=15.0,
            ),
            ModelPrice(
                match_key="openai/gpt-5.5",
                raw_model_id="openai/gpt-5.5",
                input_cost_per_mtok=5.0,
                output_cost_per_mtok=15.0,
            ),
        ],
    )


def test_cost_usd_basic():
    price = ModelPrice(
        match_key="x",
        raw_model_id="x",
        input_cost_per_mtok=3.0,
        output_cost_per_mtok=15.0,
    )
    in_cost, out_cost, total = cost_usd(price, input_tokens=1_000_000, output_tokens=100_000)
    assert in_cost == 3.0
    assert out_cost == 1.5
    assert total == 4.5


def test_cost_usd_zero_tokens():
    price = ModelPrice(match_key="x", raw_model_id="x", input_cost_per_mtok=10.0, output_cost_per_mtok=20.0)
    assert cost_usd(price, input_tokens=0, output_tokens=0) == (0.0, 0.0, 0.0)


def test_write_and_load_snapshot(tmp_path):
    snap = make_snapshot()
    path = write_snapshot(snap, tmp_path)
    assert path.is_file()
    loaded = load_snapshot(path)
    assert loaded.captured_at == snap.captured_at
    assert len(loaded.entries) == 2
    assert loaded.entries[0].input_cost_per_mtok == 3.0


def test_latest_snapshot_picks_newest(tmp_path):
    snap1 = make_snapshot()
    snap2 = PricingSnapshot(captured_at="2026-05-08T05:00:00Z", entries=snap1.entries)
    write_snapshot(snap1, tmp_path)
    write_snapshot(snap2, tmp_path)
    latest = latest_snapshot(tmp_path)
    assert latest is not None
    assert latest.captured_at == "2026-05-08T05:00:00Z"


def test_latest_snapshot_empty_dir(tmp_path):
    assert latest_snapshot(tmp_path) is None


def test_latest_snapshot_missing_dir():
    assert latest_snapshot(Path("/nonexistent/x/y")) is None
