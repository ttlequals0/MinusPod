"""Reuse MinusPod's pricing_fetcher for both runtime cost tracking and snapshots.

Pricing source of truth is MinusPod's ``src/pricing_fetcher.py`` (LiteLLM-backed).
The benchmark snapshots the fetched table at run time so report regeneration can
recompute costs at consistent prices regardless of when calls were made.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ModelPrice:
    match_key: str
    raw_model_id: str
    input_cost_per_mtok: float
    output_cost_per_mtok: float


@dataclass
class PricingSnapshot:
    captured_at: str
    entries: list[ModelPrice]
    _index: dict[str, ModelPrice] = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        self._index = {e.match_key: e for e in self.entries}

    def lookup(self, model_id: str) -> ModelPrice | None:
        from config import normalize_model_key

        return self._index.get(normalize_model_key(model_id))


def fetch_current() -> PricingSnapshot:
    from pricing_fetcher import fetch_litellm_pricing

    raw = fetch_litellm_pricing()
    entries = [
        ModelPrice(
            match_key=item["match_key"],
            raw_model_id=item.get("raw_model_id", ""),
            input_cost_per_mtok=float(item.get("input_cost_per_mtok", 0.0)),
            output_cost_per_mtok=float(item.get("output_cost_per_mtok", 0.0)),
        )
        for item in raw
    ]
    return PricingSnapshot(captured_at=_utc_now_microseconds(), entries=entries)


def write_snapshot(snapshot: PricingSnapshot, snapshots_dir: Path) -> Path:
    snapshots_dir.mkdir(parents=True, exist_ok=True)
    filename = snapshot.captured_at.replace(":", "").replace("-", "").replace(".", "_").rstrip("Z") + ".json"
    path = snapshots_dir / filename
    payload = {
        "captured_at": snapshot.captured_at,
        "entries": [
            {
                "match_key": e.match_key,
                "raw_model_id": e.raw_model_id,
                "input_cost_per_mtok": e.input_cost_per_mtok,
                "output_cost_per_mtok": e.output_cost_per_mtok,
            }
            for e in snapshot.entries
        ],
    }
    path.write_text(json.dumps(payload, indent=2))
    return path


def load_snapshot(path: Path) -> PricingSnapshot:
    data = json.loads(path.read_text())
    return PricingSnapshot(
        captured_at=data["captured_at"],
        entries=[ModelPrice(**e) for e in data["entries"]],
    )


def latest_snapshot(snapshots_dir: Path) -> PricingSnapshot | None:
    if not snapshots_dir.is_dir():
        return None
    files = sorted(snapshots_dir.glob("*.json"))
    return load_snapshot(files[-1]) if files else None


def cost_usd(price: ModelPrice, *, input_tokens: int, output_tokens: int) -> tuple[float, float, float]:
    in_cost = (input_tokens / 1_000_000) * price.input_cost_per_mtok
    out_cost = (output_tokens / 1_000_000) * price.output_cost_per_mtok
    return in_cost, out_cost, in_cost + out_cost


def _utc_now_microseconds() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
