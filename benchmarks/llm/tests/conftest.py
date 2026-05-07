"""Shared fixtures for benchmark tests."""
from __future__ import annotations

import pytest

from benchmark.config import BenchmarkConfig, CorpusConfig, MinusPodConfig, ModelConfig, ProviderConfig, RunConfig
from benchmark.corpus import Episode, EpisodeMetadata, Window
from benchmark.pricing import ModelPrice, PricingSnapshot
from benchmark.truth_parser import Ad, Truth


@pytest.fixture
def minimal_cfg() -> BenchmarkConfig:
    return BenchmarkConfig(
        minuspod=MinusPodConfig(base_url="x", password_env="P", session_cache_path=None),  # type: ignore[arg-type]
        providers={
            "openrouter": ProviderConfig(name="openrouter", client="openai_compatible", api_key_env="K", base_url="https://x"),
        },
        models=[
            ModelConfig(id="m1", provider="openrouter"),
            ModelConfig(id="m-old", provider="openrouter", deprecated=True),
        ],
        run=RunConfig(trials=2, max_concurrent_calls=2, max_concurrent_per_provider=1),
        corpus=CorpusConfig(path=None),  # type: ignore[arg-type]
    )


def _episode(ep_id: str, *, n_windows: int, no_ad: bool, duration: float = 600.0) -> Episode:
    metadata = EpisodeMetadata(
        ep_id=ep_id,
        podcast_slug="t",
        podcast_name="T",
        episode_id="e",
        title="Title",
        duration=duration,
        segments_hash="sha256:test",
        description="desc",
    )
    windows = [
        Window(
            index=i,
            start=i * (duration / max(n_windows, 1)),
            end=(i + 1) * (duration / max(n_windows, 1)),
            transcript_lines=[f"[{i*100}.0s - {i*100+5}.0s] line {i}"],
        )
        for i in range(n_windows)
    ]
    truth = (
        Truth(ads=[], is_no_ad_episode=True) if no_ad
        else Truth(ads=[Ad(start=0, end=10, text="x")], is_no_ad_episode=False)
    )
    return Episode(ep_id=ep_id, metadata=metadata, segments=[], truth=truth, windows=windows)


@pytest.fixture
def make_episode():
    def _factory(ep_id: str = "ep-001", *, n_windows: int = 2, no_ad: bool = False, duration: float = 600.0) -> Episode:
        return _episode(ep_id, n_windows=n_windows, no_ad=no_ad, duration=duration)
    return _factory


@pytest.fixture
def pricing_snapshot() -> PricingSnapshot:
    return PricingSnapshot(
        captured_at="2026-05-07T05:00:00Z",
        entries=[
            ModelPrice(match_key="m1", raw_model_id="m1", input_cost_per_mtok=3.0, output_cost_per_mtok=15.0),
            ModelPrice(match_key="anthropic/claude-sonnet-4.6", raw_model_id="anthropic/claude-sonnet-4.6", input_cost_per_mtok=3.0, output_cost_per_mtok=15.0),
        ],
    )
