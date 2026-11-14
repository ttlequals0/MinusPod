"""The feed builders import alone, in a fresh interpreter, without loading main_app."""
import os
import subprocess
import sys
from pathlib import Path

import pytest

from tests.app_bootstrap import bootstrap

bootstrap('feed_builder_import_order_test_')

SRC = str(Path(__file__).resolve().parents[2] / 'src')
SNIPPET = (
    "import importlib, sys\n"
    "importlib.import_module(sys.argv[1])\n"
    "print(','.join(sorted(m for m in sys.modules if m == 'main_app' or m.startswith('main_app.'))))\n"
)


@pytest.mark.parametrize('module', ['local_feed_builder', 'recents_feed', 'feed_build_support', 'lookup_cache'])
def test_the_module_imports_alone_without_main_app(module):
    env = {**os.environ, 'PYTHONPATH': SRC}
    result = subprocess.run([sys.executable, '-c', SNIPPET, module], env=env, capture_output=True, text=True,
                            timeout=120)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == ''


def test_the_app_handles_resolve_to_main_app_at_call_time():
    import feed_build_support
    import main_app
    assert feed_build_support.db._target() is main_app.db
    assert feed_build_support.storage._target() is main_app.storage
    assert feed_build_support.rss_parser._target() is main_app.rss_parser


def test_assigning_a_module_handle_replaces_it():
    import local_feed_builder
    original = local_feed_builder.db
    try:
        local_feed_builder.db = object()
        assert local_feed_builder.db is not original
    finally:
        local_feed_builder.db = original


def test_the_cache_and_auth_helpers_keep_their_old_import_paths():
    import feed_build_support
    import lookup_cache
    from main_app import cache, feed_auth, shared_state
    assert cache.TTLCache is lookup_cache.TTLCache
    assert shared_state.episode_lookup_cache is lookup_cache.episode_lookup_cache
    assert shared_state.invalidate_episode_lookup_cache is lookup_cache.invalidate_episode_lookup_cache
    assert feed_auth.active_feed_key is feed_build_support.active_feed_key
    assert feed_auth.feed_auth_enabled is feed_build_support.feed_auth_enabled
