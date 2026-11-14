"""Thread-safe set with a fixed upper bound."""
import threading
from collections import OrderedDict
from collections.abc import Hashable


class BoundedSet:
    """Set that evicts its least recently added entry past maxsize (bounds long-lived dedup sets)."""

    def __init__(self, maxsize: int) -> None:
        self._maxsize = maxsize
        self._order: OrderedDict[Hashable, None] = OrderedDict()
        self._lock = threading.Lock()

    def __contains__(self, key: Hashable) -> bool:
        with self._lock:
            return key in self._order

    def add(self, key: Hashable) -> bool:
        """Add key and return True, or refresh it and return False when already present."""
        with self._lock:
            if key in self._order:
                self._order.move_to_end(key)
                return False
            self._order[key] = None
            while len(self._order) > self._maxsize:
                self._order.popitem(last=False)
            return True

    def clear(self) -> None:
        with self._lock:
            self._order.clear()

    def __len__(self) -> int:
        with self._lock:
            return len(self._order)
