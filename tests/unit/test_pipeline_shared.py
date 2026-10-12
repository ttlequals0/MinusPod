"""SharedSingleton forwards to main_app.processing and follows a rebind."""
import sys
import types

from main_app.pipeline.shared import SharedSingleton


def test_shared_singleton_forwards_and_follows_rebind(monkeypatch):
    fake_processing = types.SimpleNamespace(db=types.SimpleNamespace(get_setting=lambda k: 'one'))
    monkeypatch.setitem(sys.modules, 'main_app.processing', fake_processing)
    proxy = SharedSingleton('db')
    assert proxy.get_setting('x') == 'one'
    fake_processing.db = types.SimpleNamespace(get_setting=lambda k: 'two')
    assert proxy.get_setting('x') == 'two'
    assert bool(proxy) is True
    fake_processing.db = None
    assert bool(proxy) is False
