"""benchmark.toml loader and validation."""
from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class MinusPodConfig:
    base_url: str
    password_env: str
    session_cache_path: Path


@dataclass(frozen=True)
class ProviderConfig:
    name: str
    client: str
    api_key_env: str
    base_url: str | None = None


@dataclass(frozen=True)
class ModelConfig:
    id: str
    provider: str
    deprecated: bool = False


def _default_max_tokens() -> int:
    """Default the benchmark's max_tokens to production's detection_max_tokens tunable."""
    try:
        from config import get_stage_tunable  # type: ignore[import-not-found]
        return int(get_stage_tunable('detection_max_tokens'))
    except Exception:
        return 4096


@dataclass(frozen=True)
class RunConfig:
    trials: int = 5
    temperature: float = 0.0
    timeout_seconds: int = 180
    max_retries: int = 3
    response_format: str = "json_object"
    max_tokens: int = 4096  # actual default resolved at parse time via _default_max_tokens()
    max_concurrent_calls: int = 8
    max_concurrent_per_provider: int = 4
    # Cap on a provider rate-limit pause (e.g. the Claude wrapper's five-hour
    # account reset) before the call is recorded as an error instead; default
    # 6h covers that window with margin.
    max_rate_limit_pause_seconds: int = 21600


@dataclass(frozen=True)
class CorpusConfig:
    path: Path


@dataclass(frozen=True)
class BenchmarkConfig:
    minuspod: MinusPodConfig
    providers: dict[str, ProviderConfig]
    models: list[ModelConfig]
    run: RunConfig
    corpus: CorpusConfig

    def model_provider(self, model_id: str) -> ProviderConfig:
        for m in self.models:
            if m.id == model_id:
                return self.providers[m.provider]
        raise KeyError(f"Model {model_id!r} not configured")


class ConfigError(ValueError):
    pass


def load(path: str | Path = "benchmark.toml") -> BenchmarkConfig:
    p = Path(path)
    if not p.is_file():
        raise ConfigError(f"benchmark.toml not found at {p}; copy benchmark.toml.example")
    with p.open("rb") as f:
        raw = tomllib.load(f)
    return _parse(raw, base_dir=p.parent)


def _parse(raw: dict[str, Any], base_dir: Path) -> BenchmarkConfig:
    if "minuspod" not in raw:
        raise ConfigError("Missing [minuspod] section")
    mp = raw["minuspod"]
    minuspod = MinusPodConfig(
        base_url=_require(mp, "base_url", "minuspod"),
        password_env=_require(mp, "password_env", "minuspod"),
        session_cache_path=Path(mp.get("session_cache_path", "~/.cache/minuspod-benchmark/session.json")).expanduser(),
    )

    providers_raw = raw.get("providers", {})
    if not providers_raw:
        raise ConfigError("At least one [providers.*] section required")
    providers: dict[str, ProviderConfig] = {}
    for name, body in providers_raw.items():
        client = _require(body, "client", f"providers.{name}")
        if client not in ("anthropic", "openai_compatible"):
            raise ConfigError(f"providers.{name}.client must be 'anthropic' or 'openai_compatible', got {client!r}")
        providers[name] = ProviderConfig(
            name=name,
            client=client,
            api_key_env=_require(body, "api_key_env", f"providers.{name}"),
            base_url=body.get("base_url"),
        )

    models_raw = raw.get("models", [])
    if not models_raw:
        raise ConfigError("At least one [[models]] entry required")
    models: list[ModelConfig] = []
    for i, body in enumerate(models_raw):
        model_id = _require(body, "id", f"models[{i}]")
        provider = _require(body, "provider", f"models[{i}]")
        if provider not in providers:
            raise ConfigError(f"models[{i}].provider {provider!r} not declared in [providers.*]")
        models.append(ModelConfig(id=model_id, provider=provider, deprecated=bool(body.get("deprecated", False))))

    run_raw = raw.get("run", {})
    run = RunConfig(
        trials=int(run_raw.get("trials", 5)),
        temperature=float(run_raw.get("temperature", 0.0)),
        timeout_seconds=int(run_raw.get("timeout_seconds", 180)),
        max_retries=int(run_raw.get("max_retries", 3)),
        response_format=str(run_raw.get("response_format", "json_object")),
        max_tokens=int(run_raw.get("max_tokens", _default_max_tokens())),
        max_concurrent_calls=int(run_raw.get("max_concurrent_calls", 8)),
        max_concurrent_per_provider=int(run_raw.get("max_concurrent_per_provider", 4)),
        max_rate_limit_pause_seconds=int(run_raw.get("max_rate_limit_pause_seconds", 21600)),
    )

    corpus_raw = raw.get("corpus", {})
    corpus_path = Path(corpus_raw.get("path", "data/corpus"))
    if not corpus_path.is_absolute():
        corpus_path = (base_dir / corpus_path).resolve()
    corpus = CorpusConfig(path=corpus_path)

    return BenchmarkConfig(minuspod=minuspod, providers=providers, models=models, run=run, corpus=corpus)


def _require(d: dict[str, Any], key: str, ctx: str) -> Any:
    if key not in d:
        raise ConfigError(f"Missing {ctx}.{key}")
    return d[key]


def secret(env_var: str) -> str:
    val = os.environ.get(env_var)
    if not val:
        raise ConfigError(f"Environment variable {env_var} not set")
    return val
