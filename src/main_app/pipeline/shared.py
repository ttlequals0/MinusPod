"""App singletons for pipeline stages, resolved through main_app.processing at call time.

Stage modules read db, storage and the service objects through these proxies so a
test that rebinds processing.db reaches every stage, as it did when the stages
were one module.
"""
import sys


class SharedSingleton:
    __slots__ = ('_name',)

    def __init__(self, name):
        self._name = name

    def _target(self):
        return getattr(sys.modules['main_app.processing'], self._name)

    def __getattr__(self, attr):
        return getattr(self._target(), attr)

    def __bool__(self):
        return bool(self._target())

    def __repr__(self):
        return f'<shared {self._name}>'


db = SharedSingleton('db')
storage = SharedSingleton('storage')
transcriber = SharedSingleton('transcriber')
ad_detector = SharedSingleton('ad_detector')
audio_processor = SharedSingleton('audio_processor')
audio_analyzer = SharedSingleton('audio_analyzer')
sponsor_service = SharedSingleton('sponsor_service')
status_service = SharedSingleton('status_service')
pattern_service = SharedSingleton('pattern_service')
shutdown_event = SharedSingleton('shutdown_event')
