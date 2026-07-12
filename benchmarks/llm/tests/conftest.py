"""Shared fixtures for benchmark tests."""
from __future__ import annotations

import json

import pytest

from benchmark import corpus
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


_DEFAULT_SEGMENTS = [
    {"start": 0.0, "end": 5.0, "text": "This episode is brought to you by BetterHelp"},
    {"start": 5.0, "end": 30.0, "text": "BetterHelp is online therapy that fits"},
    {"start": 30.0, "end": 60.0, "text": "Now back to the show"},
    {"start": 60.0, "end": 100.0, "text": "Welcome back everyone"},
]

_DEFAULT_TRUTH = """
start: 0
end: 30
text: This episode is brought to you by BetterHelp BetterHelp is online therapy
"""


@pytest.fixture
def write_corpus_episode():
    """Write a loadable single-window corpus episode to disk; returns its directory.

    Windows carry transcript_lines derived from the segments, so prompts
    reconstructed from the written episode contain the segment text.
    """
    def _write(root, ep_id="ep-test-001", segments=None, truth_text=None):
        segments = segments or _DEFAULT_SEGMENTS
        ep_dir = root / ep_id
        ep_dir.mkdir(parents=True)
        (ep_dir / "segments.json").write_text(json.dumps(segments))
        corpus.write_metadata(ep_dir, EpisodeMetadata(
            ep_id=ep_id, podcast_slug="test-show", podcast_name="Test Show",
            episode_id="abc123", title="Test Episode",
            duration=float(segments[-1]["end"]),
            segments_hash=corpus.hash_segments(segments),
        ))
        (ep_dir / "truth.txt").write_text(truth_text or _DEFAULT_TRUTH)
        corpus.write_windows(ep_dir, corpus.compute_windows(segments))
        return ep_dir

    return _write
