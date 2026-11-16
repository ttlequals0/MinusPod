"""SharedSingleton forwards attribute writes and deletes to the live target."""
import sys
import types

from tests.app_bootstrap import bootstrap

bootstrap('shared_singleton_')

from main_app.pipeline.shared import SharedSingleton


class _Target:
    def value(self):
        return 'class'


def test_delete_removes_the_override_from_the_target(monkeypatch):
    target = _Target()
    monkeypatch.setitem(sys.modules, 'main_app.processing', types.SimpleNamespace(db=target))
    proxy = SharedSingleton('db')
    proxy.value = lambda: 'override'
    assert target.value() == 'override'
    del proxy.value
    assert target.value() == 'class'


def test_monkeypatch_through_the_proxy_restores_the_target(monkeypatch):
    target = _Target()
    monkeypatch.setitem(sys.modules, 'main_app.processing', types.SimpleNamespace(db=target))
    proxy = SharedSingleton('db')
    with monkeypatch.context() as patch:
        patch.setattr(proxy, 'value', lambda: 'override')
        assert target.value() == 'override'
    assert target.value() == 'class'
