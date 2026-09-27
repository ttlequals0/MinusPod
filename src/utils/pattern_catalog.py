"""Per-run reuse of the active ad-pattern catalog read."""
from contextlib import contextmanager
from contextvars import ContextVar

_SCOPE: ContextVar[dict | None] = ContextVar('pattern_catalog_scope', default=None)


@contextmanager
def pattern_catalog_scope():
    """Reuse one active-pattern read for every match inside the block."""
    token = _SCOPE.set({})
    try:
        yield
    finally:
        _SCOPE.reset(token)


def scoped_catalog(key, load):
    """The rows cached under key in the current scope, else a fresh load."""
    scope = _SCOPE.get()
    if scope is None:
        return load()
    if key not in scope:
        scope[key] = load()
    return scope[key]


def invalidate_pattern_catalog_scope():
    """Drop the current scope's cached catalog after a pattern write."""
    scope = _SCOPE.get()
    if scope:
        scope.clear()
