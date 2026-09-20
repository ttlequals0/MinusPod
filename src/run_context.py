"""Per-run context: which episode a thread is working on.

Runs are threads. Pool workers (chunk uploads, detection windows, reviewer
ads) are bound to the run of the thread that submitted them, so per-run
state (token totals, run log) is looked up by thread, not by process.
"""
import copy
import threading
import time
from collections import defaultdict
from contextlib import contextmanager

from utils.url import url_has_userinfo

_lock = threading.Lock()
_by_thread: dict[int, 'RunContext'] = {}


class TokenAccumulator:
    def __init__(self):
        self._lock = threading.Lock()
        self.active = False
        self.input_tokens = 0
        self.output_tokens = 0
        self.cost = 0.0
        self._last_totals = {'input_tokens': 0, 'output_tokens': 0, 'cost': 0.0}

    def start(self):
        with self._lock:
            self.active = True
            self.input_tokens = 0
            self.output_tokens = 0
            self.cost = 0.0
            self._last_totals = {
                'input_tokens': 0, 'output_tokens': 0, 'cost': 0.0,
            }

    def add(self, input_tokens: int, output_tokens: int, cost: float) -> None:
        with self._lock:
            if not self.active:
                return
            self.input_tokens += input_tokens
            self.output_tokens += output_tokens
            self.cost += cost

    def is_active(self) -> bool:
        with self._lock:
            return self.active

    def collect_and_reset(self) -> dict:
        with self._lock:
            totals = {'input_tokens': self.input_tokens,
                      'output_tokens': self.output_tokens, 'cost': self.cost}
            if self.active:
                self._last_totals = dict(totals)
            self.active = False
            self.input_tokens = 0
            self.output_tokens = 0
            self.cost = 0.0
        return totals

    def last_totals(self) -> dict:
        with self._lock:
            return dict(self._last_totals)


class RunTiming:
    """Thread-safe wall-clock timing for one processing run."""

    def __init__(self, clock=None):
        self._lock = threading.Lock()
        self._elapsed = defaultdict(float)
        self._active = defaultdict(list)
        self._clock = clock or time.monotonic

    def add(self, name: str, seconds: float) -> None:
        if seconds < 0:
            return
        with self._lock:
            self._elapsed[name] += seconds

    @contextmanager
    def measure(self, name: str):
        started = self._clock()
        with self._lock:
            self._active[name].append(started)
        try:
            yield
        finally:
            finished = self._clock()
            with self._lock:
                starts = self._active[name]
                if started in starts:
                    starts.remove(started)
                self._elapsed[name] += finished - started
                if not starts:
                    del self._active[name]

    def snapshot(self) -> dict[str, float]:
        with self._lock:
            result = dict(self._elapsed)
            if not self._active:
                return result
            now = self._clock()
            for name, starts in self._active.items():
                result[name] = result.get(name, 0.0) + sum(
                    max(0.0, now - started) for started in starts)
            return result


_FORBIDDEN_ROUTE_KEYS = {'api_key', 'apikey', 'authorization', 'headers', 'secret', 'token'}

# Ledger attempt the calling thread is dispatching under, so a compatibility
# retry inside a provider adapter can be counted against it.
_dispatch = threading.local()


def begin_dispatch(attempt_id: str) -> None:
    """Mark `attempt_id` as the calling thread's active ledger attempt."""
    _dispatch.attempt_id = attempt_id


def end_dispatch() -> None:
    """Clear the calling thread's active ledger attempt."""
    _dispatch.attempt_id = None


def current_dispatch_attempt() -> str | None:
    """The calling thread's active ledger attempt, or None outside a dispatch."""
    return getattr(_dispatch, 'attempt_id', None)


class RunContext:
    def __init__(self, slug: str, episode_id: str, run_id: str | None = None):
        self.slug = slug
        self.episode_id = episode_id
        self.key = f"{slug}:{episode_id}"
        self.run_id = run_id
        self.recorder = None
        self.tokens = TokenAccumulator()
        self.timing = RunTiming()
        self.timing.add('ffmpeg', 0.0)
        self.route_snapshot = None
        self._thinking_notices = {}
        self._thinking_notice_lock = threading.Lock()

    def set_route_snapshot(self, snapshot: dict) -> None:
        """Store the non-secret per-phase route for this run. Rejects credential
        keys and any base_url that embeds userinfo credentials."""
        for phase in snapshot.values():
            if not isinstance(phase, dict):
                continue
            if _FORBIDDEN_ROUTE_KEYS & {k.lower() for k in phase}:
                raise ValueError("route snapshot must not contain credentials")
            base_url = phase.get('base_url')
            if url_has_userinfo(base_url):
                raise ValueError("route snapshot base_url must not embed credentials")
        self.route_snapshot = copy.deepcopy(snapshot)

    def add_thinking_notice(self, run_id: str, notice: dict) -> bool:
        """Add one notice to this run, deduplicated across worker threads."""
        if not run_id or run_id != self.run_id:
            return False
        key = (
            notice['pass'], notice['provider'], notice['model'],
            str(notice['requested']),
        )
        with self._thinking_notice_lock:
            current = self._thinking_notices.get(key)
            if current is None:
                self._thinking_notices[key] = copy.deepcopy(notice)
            elif current['compatibility'] == 'incompatible':
                current['compatibility'] = notice['compatibility']
            elif (notice['compatibility'] != 'incompatible'
                  and current['compatibility'] != notice['compatibility']):
                current['compatibility'] = 'incompatible'
        return True

    def thinking_notices(self, run_id: str) -> list[dict]:
        """Return this run's notices only when the run ID still matches."""
        if not run_id or run_id != self.run_id:
            return []
        with self._thinking_notice_lock:
            return copy.deepcopy(list(self._thinking_notices.values()))


def begin(slug: str, episode_id: str, run_id: str | None = None) -> RunContext:
    ctx = RunContext(slug, episode_id, run_id=run_id)
    with _lock:
        _by_thread[threading.get_ident()] = ctx
    return ctx


def end(ctx: RunContext) -> None:
    with _lock:
        if _by_thread.get(threading.get_ident()) is ctx:
            del _by_thread[threading.get_ident()]


def current() -> RunContext | None:
    with _lock:
        return _by_thread.get(threading.get_ident())


def record_ffmpeg_elapsed(seconds: float) -> None:
    """Add one ffmpeg subprocess's wall time to the current run."""
    ctx = current()
    if ctx is not None:
        ctx.timing.add('ffmpeg', seconds)


def route_for_phase(phase: str) -> dict | None:
    """This thread's run route for `phase` ({provider_key, configured_model}),
    or None outside a run or before the snapshot is resolved."""
    ctx = current()
    if ctx is None or not ctx.route_snapshot:
        return None
    return ctx.route_snapshot.get(phase)


def run_in_worker_thread(fn):
    """Bind a pool task to the submitting thread's run. Call on the
    submitting thread: exe.submit(run_in_worker_thread(fn), *args)."""
    ctx = current()

    def bound(*args, **kwargs):
        if ctx is None:
            return fn(*args, **kwargs)
        ident = threading.get_ident()
        with _lock:
            _by_thread[ident] = ctx
        try:
            if ctx.recorder is not None:
                ctx.recorder.register_thread()
            return fn(*args, **kwargs)
        finally:
            if ctx.recorder is not None:
                ctx.recorder.unregister_thread()
            with _lock:
                if _by_thread.get(ident) is ctx:
                    del _by_thread[ident]
    return bound
