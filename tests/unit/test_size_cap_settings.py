"""Size caps resolve DB first (UI wins), env only as the seed, via the
shared config.get_env_backed_int read path."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))

import config
import rss_parser
import storage as storage_mod
import transcriber


def test_artwork_cap_prefers_db_setting(monkeypatch):
    monkeypatch.setenv('MINUSPOD_MAX_ARTWORK_BYTES', str(30 * 1024 * 1024))
    monkeypatch.setattr(config, '_db_setting', lambda key: str(10 * 1024 * 1024))
    assert storage_mod._max_artwork_bytes() == 10 * 1024 * 1024


def test_artwork_cap_clamps_db_value(monkeypatch):
    monkeypatch.setattr(config, '_db_setting', lambda key: '10')
    assert storage_mod._max_artwork_bytes() == 64 * 1024


def test_artwork_cap_env_seed_when_no_row(monkeypatch):
    monkeypatch.setenv('MINUSPOD_MAX_ARTWORK_BYTES', str(30 * 1024 * 1024))
    monkeypatch.setattr(config, '_db_setting', lambda key: None)
    assert storage_mod._max_artwork_bytes() == 30 * 1024 * 1024


def test_rss_cap_prefers_db_setting(monkeypatch):
    monkeypatch.setenv('MINUSPOD_MAX_RSS_BYTES', str(300 * 1024 * 1024))
    monkeypatch.setattr(config, '_db_setting', lambda key: str(50 * 1024 * 1024))
    assert rss_parser._max_rss_bytes() == 50 * 1024 * 1024


def test_rss_cap_floors_at_one_mib(monkeypatch):
    monkeypatch.setattr(config, '_db_setting', lambda key: '5')
    assert rss_parser._max_rss_bytes() == 1024 * 1024


def test_download_cap_prefers_db_setting(monkeypatch):
    monkeypatch.setenv('MAX_AUDIO_DOWNLOAD_MB', '900')
    monkeypatch.setattr(config, '_db_setting', lambda key: '250')
    assert transcriber._max_download_mb() == 250


def test_download_cap_clamps_nonpositive_db_value_to_floor(monkeypatch):
    monkeypatch.delenv('MAX_AUDIO_DOWNLOAD_MB', raising=False)
    monkeypatch.setattr(config, '_db_setting', lambda key: '0')
    assert transcriber._max_download_mb() == 1


def test_download_cap_malformed_db_value_uses_default(monkeypatch):
    monkeypatch.delenv('MAX_AUDIO_DOWNLOAD_MB', raising=False)
    monkeypatch.setattr(config, '_db_setting', lambda key: 'lots')
    assert transcriber._max_download_mb() == 500


def test_download_cap_honors_values_above_10gb(monkeypatch):
    # No hard ceiling: deployments deliberately above 10 GB keep working
    # (advisory warning only), matching the pre-2.50.0 env behavior.
    monkeypatch.setattr(config, '_db_setting', lambda key: '20480')
    assert transcriber._max_download_mb() == 20480
