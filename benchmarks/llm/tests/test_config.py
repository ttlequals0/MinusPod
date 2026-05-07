import pytest
from pathlib import Path

from benchmark import config


def write(tmp_path: Path, body: str) -> Path:
    p = tmp_path / "benchmark.toml"
    p.write_text(body)
    return p


def test_minimal_valid_config(tmp_path):
    p = write(tmp_path, """
[minuspod]
base_url = "https://example.com"
password_env = "PASSWORD"

[providers.openrouter]
client = "openai_compatible"
api_key_env = "OPENROUTER_API_KEY"
base_url = "https://openrouter.ai/api/v1"

[[models]]
id = "anthropic/claude-sonnet-4.6"
provider = "openrouter"

[corpus]
path = "data/corpus"
""")
    cfg = config.load(p)
    assert cfg.minuspod.base_url == "https://example.com"
    assert cfg.providers["openrouter"].client == "openai_compatible"
    assert len(cfg.models) == 1
    assert cfg.models[0].id == "anthropic/claude-sonnet-4.6"
    assert cfg.run.trials == 5
    assert cfg.run.max_concurrent_calls == 8


def test_corpus_path_resolves_relative_to_config(tmp_path):
    p = write(tmp_path, """
[minuspod]
base_url = "x"
password_env = "P"
[providers.openrouter]
client = "openai_compatible"
api_key_env = "K"
[[models]]
id = "m"
provider = "openrouter"
[corpus]
path = "my/corpus"
""")
    cfg = config.load(p)
    assert cfg.corpus.path == (tmp_path / "my" / "corpus").resolve()


def test_missing_file(tmp_path):
    with pytest.raises(config.ConfigError, match="not found"):
        config.load(tmp_path / "nope.toml")


def test_missing_minuspod_section(tmp_path):
    p = write(tmp_path, "")
    with pytest.raises(config.ConfigError, match="\\[minuspod\\]"):
        config.load(p)


def test_invalid_provider_client(tmp_path):
    p = write(tmp_path, """
[minuspod]
base_url = "x"
password_env = "P"
[providers.bad]
client = "frobnitz"
api_key_env = "K"
[[models]]
id = "m"
provider = "bad"
""")
    with pytest.raises(config.ConfigError, match="frobnitz"):
        config.load(p)


def test_model_references_undeclared_provider(tmp_path):
    p = write(tmp_path, """
[minuspod]
base_url = "x"
password_env = "P"
[providers.openrouter]
client = "openai_compatible"
api_key_env = "K"
[[models]]
id = "m"
provider = "ghost"
""")
    with pytest.raises(config.ConfigError, match="ghost"):
        config.load(p)


def test_no_models(tmp_path):
    p = write(tmp_path, """
[minuspod]
base_url = "x"
password_env = "P"
[providers.openrouter]
client = "openai_compatible"
api_key_env = "K"
""")
    with pytest.raises(config.ConfigError, match="models"):
        config.load(p)


def test_deprecated_model_flag(tmp_path):
    p = write(tmp_path, """
[minuspod]
base_url = "x"
password_env = "P"
[providers.openrouter]
client = "openai_compatible"
api_key_env = "K"
[[models]]
id = "old"
provider = "openrouter"
deprecated = true
[[models]]
id = "new"
provider = "openrouter"
""")
    cfg = config.load(p)
    assert cfg.models[0].deprecated is True
    assert cfg.models[1].deprecated is False


def test_secret_missing(monkeypatch):
    monkeypatch.delenv("NOT_SET_VAR", raising=False)
    with pytest.raises(config.ConfigError, match="NOT_SET_VAR"):
        config.secret("NOT_SET_VAR")


def test_secret_present(monkeypatch):
    monkeypatch.setenv("HAS_VALUE", "abc")
    assert config.secret("HAS_VALUE") == "abc"


def test_model_provider_lookup(tmp_path):
    p = write(tmp_path, """
[minuspod]
base_url = "x"
password_env = "P"
[providers.together]
client = "openai_compatible"
api_key_env = "K"
[providers.openrouter]
client = "openai_compatible"
api_key_env = "K2"
[[models]]
id = "m1"
provider = "together"
[[models]]
id = "m2"
provider = "openrouter"
""")
    cfg = config.load(p)
    assert cfg.model_provider("m1").name == "together"
    assert cfg.model_provider("m2").name == "openrouter"
    with pytest.raises(KeyError):
        cfg.model_provider("missing")
