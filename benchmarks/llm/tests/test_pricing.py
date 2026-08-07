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


def test_batch_variant_does_not_overwrite_standard_price(monkeypatch):
    """`anthropic/claude-opus-5` and `...:batch` share a match key because
    normalize_model_key strips punctuation. Batch is 50% of standard, so a
    variant winning the collision silently halves the model's reported cost."""
    from benchmark import pricing

    standard = {"match_key": "claudeopus5", "raw_model_id": "anthropic/claude-opus-5",
                "input_cost_per_mtok": 5.0, "output_cost_per_mtok": 25.0}
    batch = {"match_key": "claudeopus5", "raw_model_id": "anthropic/claude-opus-5:batch",
             "input_cost_per_mtok": 2.5, "output_cost_per_mtok": 12.5}

    for order in ([standard, batch], [batch, standard]):
        monkeypatch.setattr(pricing, "fetch_litellm_pricing", lambda o=order: list(o))
        monkeypatch.setattr(pricing, "fetch_openrouter_pricing", lambda: [])
        snap = pricing.fetch_current()
        got = snap.lookup("anthropic/claude-opus-5")
        assert got.input_cost_per_mtok == 5.0, f"variant won with order {order[0]['raw_model_id']}"
        assert got.output_cost_per_mtok == 25.0


def test_bedrock_version_suffix_is_not_treated_as_a_variant():
    """`...-v2:0` is a real Bedrock model id, not a pricing variant."""
    from benchmark.pricing import _is_pricing_variant
    assert _is_pricing_variant("anthropic.claude-3-5-sonnet-20241022-v2:0") is False
    assert _is_pricing_variant("anthropic/claude-opus-5:batch") is True
    assert _is_pricing_variant("nvidia/nemotron-3-ultra:free") is True
    assert _is_pricing_variant("anthropic/claude-opus-5") is False


def test_variant_still_used_when_no_standard_entry_exists(monkeypatch):
    """A variant is better than no price at all."""
    from benchmark import pricing
    only_batch = [{"match_key": "somemodel", "raw_model_id": "vendor/some-model:batch",
                   "input_cost_per_mtok": 1.0, "output_cost_per_mtok": 2.0}]
    monkeypatch.setattr(pricing, "fetch_litellm_pricing", lambda: list(only_batch))
    monkeypatch.setattr(pricing, "fetch_openrouter_pricing", lambda: [])
    assert pricing.fetch_current().lookup("vendor/some-model").input_cost_per_mtok == 1.0
