"""Proxies for the app singletons that pipeline stages use.

main_app creates the singletons; stage modules read them through these proxies.
Each proxy resolves through main_app.processing because tests rebind them there
(monkeypatch.setattr(processing, 'db', fake)).
"""
import sys


class SharedSingleton:
    def __init__(self, name):
        self._name = name

    def _target(self):
        return getattr(sys.modules['main_app.processing'], self._name)

    def __getattr__(self, attr):
        return getattr(self._target(), attr)

    def __bool__(self):
        return bool(self._target())

    def __setattr__(self, attr, value):
        if attr == '_name':
            object.__setattr__(self, attr, value)
            return
        setattr(self._target(), attr, value)


db = SharedSingleton('db')
storage = SharedSingleton('storage')
transcriber = SharedSingleton('transcriber')
ad_detector = SharedSingleton('ad_detector')
audio_processor = SharedSingleton('audio_processor')
audio_analyzer = SharedSingleton('audio_analyzer')
sponsor_service = SharedSingleton('sponsor_service')
status_service = SharedSingleton('status_service')
pattern_service = SharedSingleton('pattern_service')
