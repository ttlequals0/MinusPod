"""Feed builder helpers whose app singletons resolve through main_app at call time, so they import without it."""
import importlib


class AppSingleton:
    """Forwards attribute access to the singleton of that name on main_app."""

    def __init__(self, name):
        object.__setattr__(self, '_name', name)

    def _target(self):
        return getattr(importlib.import_module('main_app'), self._name)

    def __getattr__(self, attr):
        return getattr(self._target(), attr)

    def __setattr__(self, attr, value):
        setattr(self._target(), attr, value)

    def __bool__(self):
        return bool(self._target())


db = AppSingleton('db')
storage = AppSingleton('storage')
rss_parser = AppSingleton('rss_parser')


def feed_auth_enabled(db) -> bool:
    return db.get_setting_bool('feed_auth_enabled', False)


def active_feed_key(db):
    """The enforced key, or None when auth is disabled or no key is stored.

    URL emitters use this so keyless serving resumes when the feature is disabled while the
    stored key is retained for re-enable.
    """
    if not feed_auth_enabled(db):
        return None
    return db.get_setting('feed_auth_key') or None
