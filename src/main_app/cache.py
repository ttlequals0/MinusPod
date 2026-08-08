"""Thread-safe TTL cache for reducing database queries."""
import threading
import time


class TTLCache:
    """Simple thread-safe cache with time-to-live expiration."""

    def __init__(self, ttl_seconds: int = 30, max_size: int = 1024):
        self._cache = {}
        self._lock = threading.Lock()
        self._ttl = ttl_seconds
        self._max_size = max_size

    def get(self, key: str):
        """Get cached value if not expired, else return None."""
        with self._lock:
            if key in self._cache:
                value, expires = self._cache[key]
                if time.time() < expires:
                    return value
                del self._cache[key]
        return None

    def set(self, key: str, value):
        """Set cached value with TTL, evicting to stay under max_size."""
        with self._lock:
            now = time.time()
            if key not in self._cache and len(self._cache) >= self._max_size:
                self._evict_to_fit(now)
            self._cache[key] = (value, now + self._ttl)

    def _evict_to_fit(self, now):
        """Drop expired entries, then oldest ones, until under max_size."""
        expired = [k for k, (_, expires) in self._cache.items() if expires <= now]
        for k in expired:
            del self._cache[k]
        overage = len(self._cache) - self._max_size + 1
        if overage > 0:
            oldest = sorted(self._cache.items(), key=lambda kv: kv[1][1])[:overage]
            for k, _ in oldest:
                del self._cache[k]

    def invalidate(self, key: str = None):
        """Invalidate specific key or entire cache."""
        with self._lock:
            if key:
                self._cache.pop(key, None)
            else:
                self._cache.clear()
