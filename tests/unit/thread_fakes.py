"""Shared test stand-in for threading.Thread that runs the target inline."""


class SyncThread:
    def __init__(self, target=None, args=(), daemon=None):
        self._target = target
        self._args = args

    def start(self):
        self._target(*self._args)
